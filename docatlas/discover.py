"""站点地图枚举：把 Epic 全站页面清单写进 pages 表。"""

from __future__ import annotations

import concurrent.futures
import html
import re
import sqlite3
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .config import CATEGORY_PATTERNS, DOC_PREFIX, LANGUAGE, SITEMAP_INDEX_URL, VERSION
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


def category_for_sitemap(url: str) -> str | None:
    for category, pattern in CATEGORY_PATTERNS.items():
        if pattern in url:
            return category
    return None


def canonical_source_url(path: str) -> str:
    quoted_path = urllib.parse.quote(path, safe="/:@-._~")
    return (
        f"https://dev.epicgames.com{quoted_path}"
        f"?application_version={VERSION}&lang={LANGUAGE}"
    )


def normalize_document_location(location: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlsplit(html.unescape(location))
    query = urllib.parse.parse_qs(parsed.query)
    languages = query.get("lang", [])
    if languages and languages[0] not in {LANGUAGE, ""}:
        return None
    path = urllib.parse.unquote(parsed.path)
    locale_prefix = re.match(r"^/documentation/[a-z]{2}-[a-z]{2}/", path, re.I)
    if locale_prefix:
        path = "/documentation/" + path[locale_prefix.end() :]
    if not path.lower().startswith(DOC_PREFIX.lower()):
        return None
    path = path.rstrip("/")
    if path.lower() == "/documentation/unreal-engine":
        return None
    return path, canonical_source_url(path)


def discover_sitemaps(
    connection: sqlite3.Connection,
    *,
    workers: int,
    refresh: bool,
) -> int:
    log("读取 Epic 官方文档站点地图索引…")
    index_body, _, _ = fetch_bytes(SITEMAP_INDEX_URL)
    all_sitemaps = xml_locations(index_body)
    selected = [
        (url, category)
        for url in all_sitemaps
        if (category := category_for_sitemap(url)) is not None
    ]
    connection.executemany(
        """
        INSERT INTO sitemaps(url, category, status)
        VALUES(?, ?, 'pending')
        ON CONFLICT(url) DO UPDATE SET category=excluded.category
        """,
        selected,
    )
    if refresh:
        connection.execute(
            "UPDATE sitemaps SET status='pending', error=NULL WHERE 1=1"
        )
    connection.commit()
    log(f"已找到 {len(selected):,} 个 UE 文档子站点地图")

    pending = list(
        connection.execute(
            """
            SELECT url, category FROM sitemaps
            WHERE status!='success'
            ORDER BY CASE category
                WHEN 'python_api' THEN 1
                WHEN 'node_reference' THEN 2
                WHEN 'cpp_api' THEN 3
                WHEN 'blueprint_api' THEN 4
                WHEN 'guides' THEN 5
                WHEN 'community_docs' THEN 6
                ELSE 9 END, url
            """
        )
    )

    def download_sitemap(row: sqlite3.Row) -> dict[str, Any]:
        try:
            body, _, _ = fetch_bytes(row["url"], timeout=120, retries=6)
            return {
                "ok": True,
                "url": row["url"],
                "category": row["category"],
                "locations": xml_locations(body),
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
        for result in executor.map(download_sitemap, pending):
            completed += 1
            if result["ok"]:
                page_rows: list[
                    tuple[
                        str,
                        str,
                        str,
                        str,
                        str,
                        str,
                        int,
                        str | None,
                        str,
                        str,
                    ]
                ] = []
                for location in result["locations"]:
                    normalized = normalize_document_location(location)
                    if normalized:
                        path, source_url = normalized
                        route_depth, parent_path = route_metadata(path)
                        observed_at = utc_now()
                        page_rows.append(
                            (
                                source_url,
                                path,
                                result["category"],
                                result["url"],
                                VERSION,
                                LANGUAGE,
                                route_depth,
                                parent_path,
                                observed_at,
                                observed_at,
                            )
                        )
                connection.executemany(
                    """
                    INSERT INTO pages(
                        url, path, category, sitemap_url, ue_version, locale,
                        route_depth, parent_path, discovered_at, last_seen_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        category=excluded.category,
                        sitemap_url=excluded.sitemap_url,
                        ue_version=excluded.ue_version,
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
                    f"站点地图 {completed:,}/{len(pending):,}；"
                    f"本轮列出英文页面 {discovered_pages:,}"
                )
    connection.commit()
    total_pages = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    failed_maps = connection.execute(
        "SELECT COUNT(*) FROM sitemaps WHERE status='failed'"
    ).fetchone()[0]
    log(f"去重后页面总数：{total_pages:,}；失败站点地图：{failed_maps:,}")
    return total_pages
