"""页面清单枚举：把全站有哪些页面写进 pages 表。

"这个站点有哪些页面"由来源适配器回答，这里只负责并发、落库、状态和进度。

清单来源分成两半，适配器可以只换其中一半：

    inventory_feeds(dataset)      -> [(清单入口地址, 分类或 None)]
    read_feed(dataset, url)       -> [(分类或 None, 页面地址)]

默认实现就是 sitemap：入口 = 站点地图索引里被 `categorize_sitemap` 认领的
子地图；读取 = 解析 XML 里的 `<loc>`。站点如果用的是分页 API、目录页或者
Sphinx 的 `searchindex.js`，适配器换掉这两个函数就行——路径规范化、分类、
版本语言元数据、写库、失败诊断和 inventory 验收全都照旧复用。翻页、限流和
重试属于"这个站怎么列页"，所以归适配器；并发和落库归这里。

`sitemaps` 表存的就是这些"清单入口"，表名是历史原因，语义是通用的。
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from .runtime import active, bind
from .util import log, utc_now
from .net import fetch_bytes
from .db import route_metadata


def xml_locations(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    return [
        node.text.strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "loc" and node.text
    ]


def _sitemap_feeds(dataset) -> list[tuple[str, str | None]]:
    source = active().source
    index_body, _, _ = fetch_bytes(source.sitemap_index_url(dataset))
    return [
        (url, category)
        for url in xml_locations(index_body)
        if (category := source.categorize_sitemap(dataset, url)) is not None
    ]


def _read_sitemap(dataset, url: str) -> Iterable[tuple[str | None, str]]:
    body, _, _ = fetch_bytes(url, timeout=120, retries=6)
    return [(None, location) for location in xml_locations(body)]


def inventory_feeds() -> list[tuple[str, str | None]]:
    workspace = active()
    hook = getattr(workspace.source, "inventory_feeds", None) or _sitemap_feeds
    return list(hook(workspace.dataset))


def read_feed(url: str) -> Iterable[tuple[str | None, str]]:
    workspace = active()
    hook = getattr(workspace.source, "read_feed", None) or _read_sitemap
    return hook(workspace.dataset, url)


def discover_inventory(
    connection: sqlite3.Connection,
    *,
    workers: int,
    refresh: bool,
) -> int:
    workspace = active()
    log(f"读取 {workspace.name} 的页面清单入口…")
    selected = inventory_feeds()
    connection.executemany(
        """
        INSERT INTO sitemaps(url, category, status)
        VALUES(?, ?, 'pending')
        ON CONFLICT(url) DO UPDATE SET category=excluded.category
        """,
        [(url, category or "") for url, category in selected],
    )
    if refresh:
        connection.execute(
            "UPDATE sitemaps SET status='pending', error=NULL WHERE 1=1"
        )
    connection.commit()
    log(f"已找到 {len(selected):,} 个清单入口")

    pending = list(
        connection.execute(
            "SELECT url, category FROM sitemaps WHERE status!='success' "
            "ORDER BY category, url"
        )
    )

    def download(row: sqlite3.Row) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                "url": row["url"],
                "category": row["category"],
                "entries": list(read_feed(row["url"])),
            }
        except Exception as exc:  # worker boundary
            return {
                "ok": False,
                "url": row["url"],
                "category": row["category"],
                "error": f"{type(exc).__name__}: {exc}",
            }

    discovered_pages = 0
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(bind(download), pending):
            completed += 1
            if result["ok"]:
                page_rows: list[tuple[Any, ...]] = []
                for entry_category, location in result["entries"]:
                    # 分类优先看条目自己说的，其次才是入口所属的：一个入口列出
                    # 多个分类（分页 API 常见）和一个入口一类（sitemap）都能表达。
                    category = entry_category or result["category"]
                    normalized = workspace.source.normalize_location(
                        workspace.dataset, location
                    )
                    if not category or not normalized:
                        continue
                    path, source_url = normalized
                    route_depth, parent_path = route_metadata(path)
                    observed_at = utc_now()
                    page_rows.append(
                        (
                            source_url,
                            path,
                            category,
                            result["url"],
                            workspace.version,
                            workspace.language,
                            route_depth,
                            parent_path,
                            observed_at,
                            observed_at,
                        )
                    )
                connection.executemany(
                    """
                    INSERT INTO pages(
                        url, path, category, sitemap_url, doc_version, locale,
                        route_depth, parent_path, discovered_at, last_seen_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        category=excluded.category,
                        sitemap_url=excluded.sitemap_url,
                        doc_version=excluded.doc_version,
                        locale=excluded.locale,
                        route_depth=excluded.route_depth,
                        parent_path=excluded.parent_path,
                        last_seen_at=excluded.last_seen_at,
                        deleted_at=NULL
                    """,
                    page_rows,
                )
                connection.execute(
                    """
                    UPDATE sitemaps
                    SET status='success', url_count=?, error=NULL, fetched_at=?
                    WHERE url=?
                    """,
                    (len(page_rows), utc_now(), result["url"]),
                )
                discovered_pages += len(page_rows)
            else:
                connection.execute(
                    """
                    UPDATE sitemaps
                    SET status='failed', error=?, fetched_at=?
                    WHERE url=?
                    """,
                    (result["error"], utc_now(), result["url"]),
                )
            connection.commit()
            if completed % 20 == 0 or completed == len(pending):
                log(
                    f"清单入口 {completed:,}/{len(pending):,}；"
                    f"本轮列出页面 {discovered_pages:,}"
                )
    connection.commit()
    total_pages = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    failed_feeds = connection.execute(
        "SELECT COUNT(*) FROM sitemaps WHERE status='failed'"
    ).fetchone()[0]
    log(f"去重后页面总数：{total_pages:,}；失败清单入口：{failed_feeds:,}")
    return total_pages
