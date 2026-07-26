"""正文抓取主循环与本地重新加工。"""

from __future__ import annotations

import concurrent.futures
from collections import deque
import sqlite3
import time
from typing import Any
import zlib

from .config import CATEGORY_PATTERNS, CHUNKER_VERSION, DATASET
from .net import REQUEST_LIMITER
from .util import log
from .documents import fetch_document, transform_document
from .store import store_document_result

# 先抓哪一类由数据集说了算；没配就一视同仁。
CATEGORY_PRIORITY = DATASET.category_priority


def _category_order() -> str:
    """把优先级表编译成一段 SQL CASE。没配置时不排序，按 id 顺序抓。"""
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
    """每类还差几页才够 N。

    "每类最多 N 页"是逐类的上限，不是全局配额：某类只有 9 页时，缺的 11 页
    不该转到别的类去补——那会让别的类超过 N，抽样也就不成其为抽样了。
    已经抓成功的算进这一类的额度里，所以重复运行不会越抓越多。
    """
    quota: dict[str, int] = {}
    for key in CATEGORY_PATTERNS:
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
        # 逐类算，不是 N × 分类数：某类不足 N 页时，缺额不转给别的类。
        quota = sample_quota(connection, sample_per_category, category=category)
        total_target = min(total_target, sum(quota.values()))
        log(
            "抽样目标："
            + "、".join(f"{key} {count}" for key, count in quota.items())
            + f"，合计 {total_target:,}"
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
    connection: sqlite3.Connection, *, limit: int, force: bool = False
) -> int:
    # 先只取页面清单（很轻），原文一页一页按 id 单独取。
    # 原来是一条带相关子查询的大 JOIN，并且 list() 会把上万份压缩原文
    # 一次性读进内存——页数一多就直接卡死。
    # 默认只做还没按当前规则加工过的页：中途断掉再跑就是续传，
    # 而不是从头再来一遍上万页。--force 才全部重做。
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
        log(f"没有需要重新加工的页面（都已是 {CHUNKER_VERSION}）")
    else:
        log(
            f"使用已保存原文重新加工 {total:,} 页 → {CHUNKER_VERSION}，不访问网络"
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
