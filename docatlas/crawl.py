"""The body-fetching main loop, and local reprocessing."""

from __future__ import annotations

import concurrent.futures
from collections import deque
import sqlite3
import time
from typing import Any
import zlib

from .constants import CHUNKER_VERSION
from .runtime import active, bind
from .net import REQUEST_LIMITER
from .util import log
from .documents import fetch_document, transform_document
from .store import store_document_result

# The dataset decides which category is fetched first; unset means no preference.

def _category_order() -> str:
    """Compile the priority table into a SQL CASE. Unset means fetch by id."""
    if not CATEGORY_PRIORITY:
        return "0"
    branches = " ".join(
        f"WHEN '{name}' THEN {rank}" for name, rank in CATEGORY_PRIORITY.items()
    )
    return f"CASE category {branches} ELSE {max(CATEGORY_PRIORITY.values()) + 1} END"


def sample_quota(
    connection: sqlite3.Connection,
    sample_per_category: int,
    *,
    category: str | None = None,
) -> dict[str, int]:
    """How many pages each category still needs to reach N.

    "At most N per category" is a per-category cap, not a global quota: when a
    category holds only 9 pages, the missing 11 must not be redistributed to other
    categories, which would push those past N and stop the sample being a sample.
    Pages already fetched count against that category's allowance, so repeat runs
    do not keep growing.
    """
    quota: dict[str, int] = {}
    for key in active().dataset.query_categories:
        if category and key != category:
            continue
        available, done = connection.execute(
            """
            SELECT
                COALESCE(SUM(status IN ('pending', 'failed') AND attempts < 8), 0),
                COALESCE(SUM(status IN ('success', 'redirect')), 0)
            FROM pages WHERE category=?
            """,
            (key,),
        ).fetchone()
        remaining = min(sample_per_category - done, available)
        if remaining > 0:
            quota[key] = remaining
    return quota


def select_page_batch(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    refresh: bool,
    sample_per_category: int,
    category: str | None = None,
) -> list[sqlite3.Row]:
    status_clause = "1=1" if refresh else "status IN ('pending', 'failed')"
    if sample_per_category:
        rows: list[sqlite3.Row] = []
        for key, remaining in sample_quota(
            connection, sample_per_category, category=category
        ).items():
            rows.extend(
                connection.execute(
                    f"""
                    SELECT id, url, path, category FROM pages
                    WHERE category=? AND {status_clause} AND attempts < 8
                    ORDER BY CASE status WHEN 'failed' THEN 1 ELSE 0 END, id
                    LIMIT ?
                    """,
                    (key, remaining),
                )
            )
        return rows
    category_clause = " AND category=?" if category else ""
    category_params: tuple[Any, ...] = (category,) if category else ()
    return list(
        connection.execute(
            f"""
            SELECT id, url, path, category FROM pages
            WHERE {status_clause} AND attempts < 8{category_clause}
            ORDER BY {_category_order()}, id
            LIMIT ?
            """,
            (*category_params, batch_size),
        )
    )


def crawl_documents(
    connection: sqlite3.Connection,
    *,
    workers: int,
    delay: float,
    max_pages: int,
    sample_per_category: int,
    refresh: bool,
    category: str | None = None,
) -> None:
    if refresh:
        log("Refresh mode: successful pages will be re-read too")
        connection.execute(
            """
            UPDATE pages
            SET status='pending'
            WHERE status IN ('success', 'redirect')
            """
        )
        connection.commit()
        refresh = False
    count_sql = (
        "SELECT COUNT(*) FROM pages"
        if refresh
        else "SELECT COUNT(*) FROM pages "
             "WHERE status IN ('pending', 'failed') AND attempts < 8"
    )
    if category:
        count_sql += " AND category=?" if not refresh else " WHERE category=?"
    total_target = connection.execute(
        count_sql, (category,) if category else ()
    ).fetchone()[0]
    if max_pages:
        total_target = min(total_target, max_pages)
    if sample_per_category:
        # Per category, not N x number of categories: a category short of N does
        # not pass its shortfall to another.
        quota = sample_quota(connection, sample_per_category, category=category)
        total_target = min(total_target, sum(quota.values()))
        log(
            "Sampling targets: "
            + ", ".join(f"{key} {count}" for key, count in quota.items())
            + f"; total {total_target:,}"
        )
    scope = f", {category} only" if category else ""
    log(f"Fetching bodies: target {total_target:,} page(s), {workers} workers{scope}")
    processed = 0
    succeeded = 0
    failed = 0
    started_at = time.monotonic()
    has_hard_limit = bool(max_pages or sample_per_category)

    # Take a large batch of candidate pages into an in-memory queue. Each take
    # sorts the whole table, and 64 rows cost the same as 4000, so take rarely.
    queue_size = max(workers * 250, 2000)
    # Submission window: keep a few pages queued per thread so no thread idles
    # waiting on the slowest page.
    window = max(workers * 4, 16)
    # Buffer this many results before writing. Committing per page floods the WAL.
    commit_every = max(workers * 20, 100)
    commit_interval = 5.0

    dispatched: set[int] = set()
    pending: deque[sqlite3.Row] = deque()
    exhausted = False
    uncommitted = 0
    last_commit_at = time.monotonic()
    last_refill_at = 0.0

    def refill() -> None:
        nonlocal exhausted, last_refill_at
        if exhausted or len(pending) >= window:
            return
        # The queue bottoms out repeatedly near the end; cap how often it is
        # refilled so the batch query does not spin.
        if pending and time.monotonic() - last_refill_at < 2.0:
            return
        last_refill_at = time.monotonic()
        rows = select_page_batch(
            connection,
            batch_size=queue_size,
            refresh=refresh,
            sample_per_category=sample_per_category,
            category=category,
        )
        if not rows:
            exhausted = True
            return
        # Commits are batched, so pages just fetched but not yet written still
        # appear here; `dispatched` dedupes them. Only when the database truly
        # returns nothing is the crawl done.
        pending.extend(row for row in rows if row["id"] not in dispatched)

    def flush(force: bool = False) -> None:
        nonlocal uncommitted, last_commit_at
        if not uncommitted:
            return
        now = time.monotonic()
        if force or uncommitted >= commit_every or now - last_commit_at >= commit_interval:
            connection.commit()
            uncommitted = 0
            last_commit_at = now

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        in_flight: dict[concurrent.futures.Future[dict[str, Any]], sqlite3.Row] = {}
        while True:
            if not (has_hard_limit and processed + len(in_flight) >= total_target):
                refill()
            while pending and len(in_flight) < window:
                if has_hard_limit and processed + len(in_flight) >= total_target:
                    break
                row = pending.popleft()
                dispatched.add(row["id"])
                in_flight[executor.submit(bind(fetch_document), row, delay)] = row
            if not in_flight:
                break
            done, _ = concurrent.futures.wait(
                in_flight, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                row = in_flight.pop(future)
                result = future.result()
                store_document_result(connection, result, row["category"])
                uncommitted += 1
                processed += 1
                if result["ok"]:
                    succeeded += 1
                else:
                    failed += 1
                if processed % 100 == 0 or processed == total_target:
                    elapsed = max(time.monotonic() - started_at, 0.001)
                    rate = processed / elapsed
                    remaining = (total_target - processed) / rate if rate else 0
                    limiter = REQUEST_LIMITER.snapshot()
                    cooling = "; throttle cooldown" if REQUEST_LIMITER.cooling_down else ""
                    log(
                        f"Bodies {processed:,}/{total_target:,}; ok {succeeded:,}; "
                        f"failed {failed:,}; {rate:.1f} pages/s; "
                        f"adaptive rate {limiter['rate']} req/s "
                        f"({limiter['throttle_events']} backoffs); "
                        f"~{remaining/60:.1f} min left{cooling}"
                    )
            flush()
            if sample_per_category and not pending and not in_flight:
                break
    flush(force=True)
    log(
        f"Body stage done: processed {processed:,}; ok {succeeded:,}; failed {failed:,}"
    )


def reprocess_stored_documents(
    connection: sqlite3.Connection, *, limit: int, force: bool = False
) -> int:
    # Take only the page list first (cheap) and load each archived body by id.
    # Loading them together reads tens of thousands of compressed bodies into
    # memory at once, which stalls outright on a large library.
    # By default only pages not yet processed by the current rules: an
    # interruption then resumes instead of redoing everything. --force redoes all.
    outdated = "" if force else " AND COALESCE(parser_version,'') != ?"
    sql = (
        "SELECT id, url, path, category FROM pages "
        f"WHERE status='success'{outdated} ORDER BY id"
    )
    params: tuple[Any, ...] = () if force else (CHUNKER_VERSION,)
    if limit:
        sql += " LIMIT ?"
        params += (limit,)
    pages = list(connection.execute(sql, params))
    total = len(pages)
    if not total:
        log(f"No pages need reprocessing (all at {CHUNKER_VERSION})")
    else:
        log(
            f"Reprocessing {total:,} page(s) from stored bodies -> {CHUNKER_VERSION}, offline"
        )
    processed = 0
    skipped = 0
    reader = connection.cursor()
    for page in pages:
        raw = reader.execute(
            "SELECT raw_json FROM raw_documents WHERE page_id=? "
            "ORDER BY id DESC LIMIT 1",
            (page["id"],),
        ).fetchone()
        if raw is None:
            skipped += 1
            continue
        result = transform_document(page, zlib.decompress(raw["raw_json"]))
        store_document_result(connection, result, page["category"])
        processed += 1
        # Batched commits as in fetching: per-page commits are pure waste at
        # tens of thousands of pages.
        if processed % 200 == 0:
            connection.commit()
        if processed % 500 == 0 or processed == total:
            log(f"Reprocessed {processed:,}/{total:,}")
    connection.commit()
    if skipped:
        log(f"Skipped {skipped:,} page(s) with no archived body")
    from .relations import build_cross_index  # circular import: pulled in here

    build_cross_index(connection)
    return processed
