"""Inventory coverage gaps: collect pages that in-scope bodies reference but the
inventory does not list.

The source inventory is whatever the site itself provides (sitemap,
`searchindex.js`, index pages), and that list has never been the same thing as
"which pages are needed to understand this material":

* A dataset enumerating only the directories that hold its main subject still
  has bodies linking constantly to foundational pages kept elsewhere in the
  site — pages you cannot follow the material without, none of them present.
* A sitemap may omit whole directories that already-fetched bodies point into
  hundreds of times.

Both have the same shape, so there should be only one mechanism for them. The
criterion is not "these pages look important" but **an in-scope body pointed at
them**: when a document we chose to include says "see that page for details",
that page is part of this material. That is a fact the site wrote, not a guess.

The boundary comes from the same place, in two layers:

* **One hop only.** Collected pages are `pending`, and their own links are not
  expanded further. Going one layer out means running again, which is an explicit
  decision rather than a silent snowball.
* **Only targets referenced by in-scope pages.** The starting point must be a
  page whose body was already fetched.

Which category a collected page belongs to is asked in two steps, each with its
own owner:

    1. Ask the adapter (`categorize_path`): which category does this path belong
       to on this site? That is site layout, and only the adapter knows it.
    2. If the adapter cannot say, the path lies outside the directories the
       dataset declared. Whether to collect it is the **dataset's** call
       (`[inventory] referenced_category`); silence means do not collect.

The core therefore knows no specific directory and does not guess. Inferring a
category from neighbouring directories misclassifies on real data, and a wrong
guess that looks plausible is the hardest kind to notice.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import resolve_link_targets, route_metadata
from .runtime import active
from .util import utc_now


# Bump on any change to "which links count as in-site documents": stored links are
# then re-judged in bulk. Otherwise new rules apply only to future pages and one
# library holds two generations of judgements.
LINK_TARGET_VERSION = "2"


def reclassify_links(connection: sqlite3.Connection) -> int:
    """Re-judge "is this an in-site document" over stored links, offline.

    The rule lives in the adapter, so when it changes, previously fetched pages
    carry the old conclusion. Refetching would be expensive and pointless:
    `page_links.target_url` is stored verbatim, so re-judging is enough.
    """
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='link_targets'"
    ).fetchone()
    if stored and stored[0] == LINK_TARGET_VERSION:
        return 0
    normalize = getattr(active().source, "normalize_link_target", None)
    if normalize is None:
        return 0  # this source has no notion of in-site links; nothing to redo
    dataset = active().dataset
    changed = 0
    for row in connection.execute("SELECT id, target_url, target_path FROM page_links"):
        target_path = normalize(dataset, row["target_url"])
        if target_path == row["target_path"]:
            continue
        # A changed target path makes the previously resolved page id a stale
        # conclusion, so it must be cleared too, or the link keeps pointing at
        # the page chosen before re-judging. Once cleared, resolve_link_targets
        # below resolves it again from the new path.
        connection.execute(
            "UPDATE page_links SET target_path=?, evidence_kind=?,"
            " target_page_id=NULL WHERE id=?",
            (target_path, "official_link" if target_path else "external_link", row["id"]),
        )
        changed += 1
    resolve_link_targets(connection)
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('link_targets', ?)",
        (LINK_TARGET_VERSION,),
    )
    connection.commit()
    return changed


def referenced_category() -> str:
    """Category for referenced targets whose directory was never enumerated.

    An empty string means do not collect them.
    """
    return str(active().dataset.inventory_option("referenced_category", "") or "")


def path_category(path: str) -> str | None:
    """Which category the adapter assigns this path, or None if it cannot say."""
    workspace = active()
    classify = getattr(workspace.source, "categorize_path", None)
    return classify(workspace.dataset, path) if classify else None


def linked_targets(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """In-site targets linked from fetched bodies but absent from the inventory.

    Ordered by how often they are referenced.
    """
    return list(
        connection.execute(
            """
            SELECT l.target_path AS path,
                   MIN(l.target_url) AS url,
                   COUNT(*) AS links
            FROM page_links l
            JOIN pages source ON source.id=l.from_page_id
            WHERE l.target_path IS NOT NULL
              AND l.target_page_id IS NULL
              AND source.status='success'
            GROUP BY l.target_path
            ORDER BY links DESC, path
            """
        )
    )


def admit_linked_targets(
    connection: sqlite3.Connection,
    *,
    limit: int | None = None,
    min_links: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Collect referenced out-of-inventory pages, with status `pending`.

    Collected pages leave `sitemap_url` empty: they were not listed by any
    inventory feed, and that has to be visible in the database, or the next
    enumeration would conclude the sitemap had changed.

    URLs are always rebuilt by the adapter from the path (`canonical_url`) rather
    than taken from the link. Links in bodies carry their own baggage: a query
    string pinning a different version (storing a 5.5 URL in a 5.8 library would
    cite the wrong thing), or a fragment such as `#term-Alpha-Channel`. The path
    is already normalized, so the URL should be recomputed from it.
    """
    fallback = referenced_category()
    workspace = active()
    now = utc_now()
    admitted: list[dict[str, Any]] = []
    skipped_no_area = 0
    rows = [row for row in linked_targets(connection) if row["links"] >= min_links]
    for row in rows:
        if limit is not None and len(admitted) >= limit:
            break
        category = path_category(row["path"]) or fallback
        if not category:
            # Adapter cannot place it and the dataset did not ask for it: this is
            # out of scope, not an oversight.
            skipped_no_area += 1
            continue
        depth, parent = route_metadata(row["path"])
        if not dry_run:
            connection.execute(
                """
                INSERT INTO pages(
                    url, path, category, doc_version, locale, route_depth,
                    parent_path, discovered_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO NOTHING
                """,
                (
                    workspace.source.canonical_url(workspace.dataset, row["path"]),
                    row["path"],
                    category,
                    workspace.version,
                    workspace.language,
                    depth,
                    parent,
                    now,
                    now,
                ),
            )
        admitted.append(
            {"path": row["path"], "category": category, "links": row["links"]}
        )
    if not dry_run and admitted:
        # Only once the new pages exist can links to them resolve to a target id.
        connection.execute(
            """
            UPDATE page_links
            SET target_page_id=(
                SELECT p.id FROM pages p WHERE p.path=page_links.target_path
            )
            WHERE target_path IS NOT NULL AND target_page_id IS NULL
            """
        )
        connection.commit()
    by_category: dict[str, int] = {}
    for item in admitted:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    return {
        "candidates": len(rows),
        "admitted": len(admitted),
        "by_category": by_category,
        "outside_scope": skipped_no_area,
        "referenced_category": fallback,
        "top_admitted": admitted[:10],
        "dry_run": dry_run,
    }
