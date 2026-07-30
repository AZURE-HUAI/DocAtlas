"""Page member entities.

A document page does not always describe only one thing. Type pages list their
properties and methods in tables, and most of those members **have no page of
their own**: a property may exist only inside the member table of the type that
owns it. Treat one page as exactly one entity and those names do not exist in the
library at all — the body text is searchable, but `related` can only answer
`entity_not_found`.

This module does one thing: **promote the members of a member table into
entities**. Recognizing a member table is site knowledge and belongs to the
source adapter (`page_members`); what a member may also be called within its
domain is domain knowledge and belongs to the knowledge pack (`member_aliases`).
This file only handles normalization, identity, deduplication and storage shape.

Three invariants everything below relies on:

* **A member entity always lives on the same page as its owner.** So
  `DELETE FROM entities WHERE page_id=?` when reprocessing a page removes its
  members along with it, requiring no extra cleanup code.
* **Identity includes the owner**: `qualified_name` is `Owner::member`. Two
  unrelated types may each declare a member of the same name, and those must
  not collapse into one entity.
* **Members that have their own page never appear here.** A linked row in a
  member table means the site published a page for it, and that page is already
  the entity; promoting it again would store one thing twice.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .runtime import active
from .text import normalize_name


# Bump on any change to member recognition rules: already-fetched pages are then
# recomputed in bulk. Otherwise new rules apply only to future pages and one
# library ends up holding two generations of members.
MEMBERS_VERSION = "1"


def supported() -> bool:
    """Whether the current dataset's source adapter recognizes member tables."""
    return active().extension("page_members") is not None


def collect(
    *,
    category: str,
    title: str,
    path: str,
    source_url: str,
    sections: list[dict[str, Any]],
    module: str | None,
) -> list[dict[str, Any]]:
    """Normalize adapter-reported members into entity descriptions.

    The adapter reports facts only (name, kind, signature, summary, modifiers as
    written); qualified names, aliases and deduplication happen here. That way
    supporting a new site needs only the ability to read its own tables, without
    knowing what an entity looks like.
    """
    reader = active().extension("page_members")
    if reader is None:
        return []
    found = reader(
        active().dataset,
        category=category,
        title=title,
        path=path,
        sections=sections,
    )
    aliases_of = active().hook("member_aliases")
    version = active().version
    descriptors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for member in found or []:
        name = str(member.get("name") or "").strip()
        entity_type = str(member.get("entity_type") or "").strip()
        normalized = normalize_name(name)
        if not name or not entity_type or not normalized:
            continue
        # Keep only the first same-name, same-kind member on a page: overloads
        # and overridden virtuals recur across several sections, and those are
        # different signatures of one member.
        if (entity_type, normalized) in seen:
            continue
        seen.add((entity_type, normalized))
        qualified_name = f"{title}::{name}"
        attributes = {
            "member_of": title,
            "category": category,
            "path": path,
            **(member.get("attributes") or {}),
        }
        member_aliases = {(name, "member_name"), (qualified_name, "qualified_name")}
        if aliases_of:
            member_aliases |= aliases_of(
                name=name,
                entity_type=entity_type,
                owner=title,
                attributes=attributes,
            ) or set()
        descriptors.append(
            {
                "entity_type": entity_type,
                "canonical_name": name,
                "normalized_name": normalized,
                "qualified_name": qualified_name,
                "module": module,
                "owner_type": title,
                "signature": member.get("signature") or None,
                "source_url": member.get("source_url") or source_url,
                "version": version,
                "attributes_json": json.dumps(attributes, ensure_ascii=False),
                "aliases": sorted(member_aliases),
            }
        )
    return descriptors


def backfill(connection: sqlite3.Connection) -> int:
    """Re-run member recognition over already-fetched bodies. Local, no network.

    When member support is added, or its rules change, pages already in the
    library should not be refetched for it: the section bodies are still there, so
    rereading the tables is enough. Datasets whose adapter does not recognize
    member tables are skipped without issuing a single SQL statement.
    """
    if not supported():
        return 0
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='page_members'"
    ).fetchone()
    if stored and stored[0] == MEMBERS_VERSION:
        return 0

    # Clear the previous pass's members first, or renamed ones linger forever.
    connection.execute("DELETE FROM entities WHERE member_of_id IS NOT NULL")
    created = 0
    owners = list(
        connection.execute(
            """
            SELECT e.id AS owner_id, e.module, p.id AS page_id, p.path,
                   p.category, p.title, p.url
            FROM entities e JOIN pages p ON p.id=e.page_id
            WHERE e.member_of_id IS NULL AND p.status='success'
            """
        )
    )
    from .store import store_members

    for owner in owners:
        sections = [
            {
                "heading_path": row["heading_path"],
                "body_md": row["body_md"] or "",
                "knowledge_type": row["knowledge_type"],
                "source_anchor": row["source_anchor"] or owner["url"],
            }
            for row in connection.execute(
                "SELECT heading_path, body_md, knowledge_type, source_anchor"
                " FROM sections WHERE page_id=? ORDER BY position",
                (owner["page_id"],),
            )
        ]
        if not sections:
            continue
        members = collect(
            category=owner["category"],
            title=owner["title"] or "",
            path=owner["path"],
            source_url=owner["url"],
            sections=sections,
            module=owner["module"],
        )
        created += store_members(
            connection, owner["page_id"], owner["owner_id"], members
        )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('page_members', ?)",
        (MEMBERS_VERSION,),
    )
    connection.commit()
    return created
