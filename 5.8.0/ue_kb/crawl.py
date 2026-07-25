"""正文抓取主循环与本地重新加工。"""

from __future__ import annotations

import concurrent.futures
from collections import deque
import sqlite3
import time
from typing import Any
import zlib

from .config import CATEGORY_PATTERNS
from .net import REQUEST_LIMITER
from .util import log
from .documents import fetch_document, transform_document
from .store import store_document_result


def select_page_batch(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    refresh: bool,
    sample_per_category: int,
    category: str | None = None,
) -> list[sqlite3.Row]:
    if sample_per_category:
        rows: list[sqlite3.Row] = []
        for category in CATEGORY_PATTERNS:
            status_clause = "1=1" if refresh else "status IN ('pending', 'failed')"
            rows.extend(
                connection.execute(
                    f"""
                    SELECT id, url, path, category FROM pages
                    WHERE category=? AND {status_clause}
                    ORDER BY CASE status WHEN 'failed' THEN 1 ELSE 0 END, id
                    LIMIT ?
                    """,
                    (category, sample_per_category),
                )
            )
        return rows
    status_clause = "1=1" if refresh else "status IN ('pending', 'failed')"
    category_clause = " AND category=?" if category else ""
    category_params: tuple[Any, ...] = (category,) if category else ()
    return list(
        connection.execute(
            f"""
            SELECT id, url, path, category FROM pages
            WHERE {status_clause} AND attempts < 8{category_clause}
            ORDER BY CASE category
                WHEN 'guides' THEN 1
                WHEN 'community_docs' THEN 2
                WHEN 'blueprint_api' THEN 3
                WHEN 'node_reference' THEN 4
                WHEN 'python_api' THEN 5
                WHEN 'cpp_api' THEN 6
                ELSE 9 END, id
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
        log("刷新模式：成功页面也会重新读取")
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
        total_target = min(
            total_target, sample_per_category * len(CATEGORY_PATTERNS)
        )
    scope = f"，仅 {category}" if category else ""
    log(f"开始抓取正文，目标 {total_target:,} 页，并发 {workers}{scope}")
    processed = 0
    succeeded = 0
    failed = 0
    started_at = time.monotonic()
    has_hard_limit = bool(max_pages or sample_per_category)

    # 一次取一大批候选页放进内存队列。取一次要排序全表，取 64 条和取 4000 条
    # 代价一样，所以宁可少取几次。
    queue_size = max(workers * 250, 2000)
    # 提交窗口：始终让每个线程手上排着几页，避免"等最慢的一页"造成空转。
    window = max(workers * 4, 16)
    # 攒够这么多结果再落盘。逐页 commit 会把 WAL 刷爆，是原来最大的浪费之一。
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
        # 收尾阶段队列会反复见底；限制重查频率，别让选批查询空转。
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
        # 提交是攒批做的，所以刚抓完还没落盘的页仍会出现在这里；靠 dispatched
        # 去重。只有数据库真的一条都不给了，才算抓完。
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
                in_flight[executor.submit(fetch_document, row, delay)] = row
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
                    cooling = "；限流冷却中" if REQUEST_LIMITER.cooling_down else ""
                    log(
                        f"正文 {processed:,}/{total_target:,}；成功 {succeeded:,}；"
                        f"失败 {failed:,}；{rate:.1f} 页/秒；"
                        f"自适应速率 {limiter['rate']} 请求/秒"
                        f"（退让 {limiter['throttle_events']} 次）；"
                        f"预计剩余 {remaining/60:.1f} 分钟{cooling}"
                    )
            flush()
            if sample_per_category and not pending and not in_flight:
                break
    flush(force=True)
    log(
        f"正文阶段结束：处理 {processed:,}；成功 {succeeded:,}；失败 {failed:,}"
    )


def reprocess_stored_documents(
    connection: sqlite3.Connection, *, limit: int
) -> int:
    # 先只取页面清单（很轻），原文一页一页按 id 单独取。
    # 原来是一条带相关子查询的大 JOIN，并且 list() 会把上万份压缩原文
    # 一次性读进内存——页数一多就直接卡死。
    sql = "SELECT id, url, path, category FROM pages WHERE status='success' ORDER BY id"
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    pages = list(connection.execute(sql, params))
    total = len(pages)
    log(f"使用已保存原文重新加工 {total:,} 页，不访问网络")
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
        # 和抓取一样攒批提交：逐页 commit 在上万页时是纯粹的浪费。
        if processed % 200 == 0:
            connection.commit()
        if processed % 500 == 0 or processed == total:
            log(f"重新加工 {processed:,}/{total:,}")
    connection.commit()
    if skipped:
        log(f"跳过 {skipped:,} 页（没有原文存档）")
    from .crossindex import build_cross_index  # 循环依赖：只在此处按需引入

    build_cross_index(connection)
    return processed
