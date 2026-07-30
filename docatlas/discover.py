"""Page inventory enumeration: record which pages a site has in the pages table.

"Which pages does this site have" is answered by the source adapter; this module
only handles concurrency, storage, status and progress.

Inventory sources come in two halves, and an adapter may replace just one:

    inventory_feeds(dataset)      -> [(feed URL, category or None)]
    read_feed(dataset, url)       -> [(category or None, page URL)]

The default implementation is sitemaps: feeds are the sub-sitemaps claimed by
`categorize_sitemap` from the sitemap index, and reading parses `<loc>` out of
the XML. For a site using a paginated API, index pages or Sphinx's
`searchindex.js`, an adapter replaces those two functions and everything else is
reused: path normalization, categorization, version and language metadata,
storage, failure diagnostics and inventory validation. Pagination, rate limiting
and retries are part of "how this site lists pages" and belong to the adapter;
concurrency and storage belong here.

The `sitemaps` table holds these feeds. Its name is historical; the meaning is
generic.
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
    log(f"Reading inventory feeds for {workspace.name}...")
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
    log(f"Found {len(selected):,} inventory feed(s)")

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
                    # An entry's own category wins over its feed's, which covers
                    # both one feed listing many categories (common for
                    # paginated APIs) and one feed per category (sitemaps).
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
                    f"Feeds {completed:,}/{len(pending):,}; "
                    f"pages listed this pass {discovered_pages:,}"
                )
    connection.commit()
    total_pages = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    failed_feeds = connection.execute(
        "SELECT COUNT(*) FROM sitemaps WHERE status='failed'"
    ).fetchone()[0]
    log(f"Pages after dedupe: {total_pages:,}; failed feeds: {failed_feeds:,}")
    return total_pages
