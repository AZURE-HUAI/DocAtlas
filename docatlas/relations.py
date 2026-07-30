"""Generic relation core.

A relation answers "what is this connected to, and on what grounds".
**Finding candidates, verifying targets, guarding against name collisions,
deduplicating, storing, updating incrementally, and explaining failures**
are the same for any documentation site and live here. Only "why are these
two entities related" is domain knowledge, and that lives in
`knowledge/<name>.py`.

A knowledge pack's contract is a single function:

    def relation_rules(graph):
        yield RelationCandidate(...)

`graph` is a read-only view offering four query primitives (`entities` /
`find` / `name_matches` / `texts`). Candidates are expressed with **entity
objects**, and entities can only come from those primitives — so a target
always exists, and one that does not was already recorded as a diagnostic
inside `find()`. A pack writes no SQL, knows no tables and never sees an
entity id; supporting a new product means writing this one function.

The same function serves both a full rebuild and an incremental one: give
`graph` a `page_ids` list and only those pages are processed. Keeping it to
one function is what keeps the two paths in step — every domain relation an
incremental run can make is one a full rebuild makes too.

Ownership is recorded in `relations.origin`: `core` for the core, otherwise
the pack's name. A full rebuild deletes by origin, so a pack does not have
to keep its own list of "evidence kinds I produce" — one missing entry
there leaves a dead relation nothing can delete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import sqlite3
from typing import Any, Iterator

from .db import resolve_link_targets
from .runtime import active
from .util import utc_now

# link_kind of an official link → relation type. True of any documentation
# site: a link from one document to another is a relation.
RELATION_TYPE_BY_LINK_KIND = {
    "hierarchy": "belongs_to",
    "parameter_type": "parameter_type",
    "return_type": "return_type",
    "signature_reference": "signature_reference",
    "example_reference": "example_reference",
    "official_reference": "official_reference",
}

# The origin recorded for relations the core produces itself.
CORE_ORIGIN = "core"

# Past this many entities for one name, the name stops being evidence — it
# is almost certainly a ubiquitous verb like Get or Set, and handing it out
# only misleads.
DEFAULT_MAX_AMBIGUITY = 8

# Names shorter than this are not grounds for a match: everything is
# called "Get" or "Add".
DEFAULT_MIN_NAME_LENGTH = 6

_ENTITY_FIELDS = (
    "id",
    "page_id",
    "entity_type",
    "canonical_name",
    "normalized_name",
    "qualified_name",
    "owner_type",
    "module",
    "source_url",
    "member_of_id",
    "attributes_json",
)


def _columns(table: str, prefix: str = "") -> str:
    """Entity columns for a `SELECT`; `prefix` renames the second entity's
    columns so the two do not collide."""
    return ", ".join(
        f"{table}.{name} AS {prefix}{name}" if prefix else f"{table}.{name}"
        for name in _ENTITY_FIELDS
    )


_ENTITY_COLUMNS = _columns("e")


@dataclass(frozen=True)
class Entity:
    """An entity as a knowledge pack sees it. Read-only, carrying the fields
    needed to decide "is this the one"."""

    id: int
    page_id: int
    entity_type: str
    name: str
    normalized_name: str
    qualified_name: str | None
    owner_type: str | None
    module: str | None
    source_url: str
    # Not None means this is a member (property, method) documented on
    # another entity's page; the value is the owner's entity id.
    member_of_id: int | None = None
    # Verbatim facts recorded at crawl time for the pack to judge by, such as
    # the declaration modifiers a site prints beside a member.
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, row: sqlite3.Row, prefix: str = "") -> "Entity":
        raw = row[f"{prefix}attributes_json"]
        try:
            attributes = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            attributes = {}
        return cls(
            id=row[f"{prefix}id"],
            page_id=row[f"{prefix}page_id"],
            entity_type=row[f"{prefix}entity_type"],
            name=row[f"{prefix}canonical_name"],
            normalized_name=row[f"{prefix}normalized_name"],
            qualified_name=row[f"{prefix}qualified_name"],
            owner_type=row[f"{prefix}owner_type"],
            module=row[f"{prefix}module"],
            source_url=row[f"{prefix}source_url"],
            member_of_id=row[f"{prefix}member_of_id"],
            attributes=attributes if isinstance(attributes, dict) else {},
        )


@dataclass(frozen=True)
class RelationCandidate:
    """One relation declared by a knowledge pack.

    `confidence` is how certain it is: 1.0 for what the documentation states
    outright, lower for anything inferred from names, with `note` saying on
    what grounds. Anything below 1.0 is reported upstream as inferred, so a
    vague note lets an agent pass a guess on as an official correspondence.
    """

    source: Entity
    target: Entity
    relation_type: str
    evidence_kind: str
    confidence: float
    note: str = ""
    evidence_url: str = ""
    evidence_chunk_id: int | None = None


@dataclass
class RelationGraph:
    """The read-only view a knowledge pack finds candidates through.

    `page_ids` of None means everything; a list restricts queries to those
    pages, so a handful of newly fetched pages get their relations without
    rescanning the library. The restriction applies to **either end** — a
    new page can be the start of a relation, or the target something else
    has been trying to point at all along.
    """

    connection: sqlite3.Connection
    page_ids: list[int] | None = None
    # Names find() could not resolve to an entity. Not an error but a
    # diagnostic: the target page is probably unfetched, or was never
    # enumerated into the inventory at all.
    unresolved: list[str] = field(default_factory=list)

    def _scope(self, *columns: str) -> tuple[str, tuple[Any, ...]]:
        """Compile the page_ids restriction into a SQL fragment; empty when
        there is no restriction."""
        if self.page_ids is None:
            return "", ()
        placeholders = ",".join("?" for _ in self.page_ids)
        clause = " OR ".join(f"{c} IN ({placeholders})" for c in columns)
        return f" AND ({clause})", tuple(self.page_ids) * len(columns)

    def entities(self, *entity_types: str) -> Iterator[Entity]:
        """Walk the entities, optionally filtered by type."""
        where = ""
        params: tuple[Any, ...] = ()
        if entity_types:
            where = f" AND e.entity_type IN ({','.join('?' for _ in entity_types)})"
            params = entity_types
        scope, scope_params = self._scope("e.page_id")
        for row in self.connection.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM entities e WHERE 1=1{where}{scope}"
            " ORDER BY e.id",
            (*params, *scope_params),
        ):
            yield Entity.of(row)

    def find(
        self,
        name: str,
        *,
        entity_type: str | None = None,
        alias_type: str | None = None,
        limit: int = DEFAULT_MAX_AMBIGUITY,
    ) -> list[Entity]:
        """Find entities by name or alias. Misses go into `unresolved`.

        Deliberately not restricted by page_ids: the target of a relation can
        be any page fetched at any time, and restricting it would confine an
        incremental run to linking the new pages to each other.
        """
        from .text import normalize_name

        normalized = normalize_name(name)
        if not normalized:
            return []
        # Parameters must be gathered in the order the placeholders appear in
        # the SQL text, hence building both together.
        params: list[Any] = [normalized]
        alias_clause = ""
        if alias_type:
            alias_clause = " AND a.alias_type=?"
            params.append(alias_type)
        params.append(normalized)
        type_clause = ""
        if entity_type:
            type_clause = " AND e.entity_type=?"
            params.append(entity_type)
        params.append(limit + 1)
        rows = list(
            self.connection.execute(
                f"""
                SELECT {_ENTITY_COLUMNS} FROM entities e
                WHERE e.id IN (
                    SELECT entity_id FROM entity_aliases a
                    WHERE a.normalized_alias=?{alias_clause}
                    UNION
                    SELECT id FROM entities WHERE normalized_name=?
                ){type_clause}
                ORDER BY e.id LIMIT ?
                """,
                params,
            )
        )
        if not rows:
            self.unresolved.append(name)
            return []
        # Too many collisions and the whole group goes: the name has stopped
        # being evidence.
        return [] if len(rows) > limit else [Entity.of(row) for row in rows]

    def name_matches(
        self,
        from_type: str,
        to_type: str | tuple[str, ...],
        *,
        source_alias: str | None = None,
        target_alias: str | None = None,
        min_length: int = DEFAULT_MIN_NAME_LENGTH,
        max_ambiguity: int = DEFAULT_MAX_AMBIGUITY,
    ) -> Iterator[tuple[Entity, Entity, str]]:
        """Match two kinds of entity by name in bulk, yielding
        `(source, target, the name that matched)`.

        This is the commonest shape of structural evidence: a visual node and
        the symbol behind it, a component and its class, an operator and its
        scripting interface — all of them "the names line up". Pass
        `source_alias` / `target_alias` to match on aliases instead, say a
        display name on one side and a declared name on the other.

        A source matching more than `max_ambiguity` targets is dropped whole:
        the name has stopped being evidence. When `to_type` is a group of
        types the limit counts the group's total, not each type separately.
        """
        to_types = (to_type,) if isinstance(to_type, str) else tuple(to_type)
        params: list[Any] = []
        # Match the source on its own name, or on one kind of alias.
        if source_alias:
            source_join = (
                "JOIN entity_aliases sa ON sa.entity_id=s.id AND sa.alias_type=?"
            )
            source_key = "sa.normalized_alias"
            source_label = "sa.alias"
            params.append(source_alias)
        else:
            source_join = ""
            source_key = "s.normalized_name"
            source_label = "s.canonical_name"
        # Same for the target. The alias table has to be joined before it can
        # be referenced in the ON clause.
        if target_alias:
            target_join = (
                f"JOIN entity_aliases ta"
                f" ON ta.normalized_alias={source_key} AND ta.alias_type=?"
                f" JOIN entities t ON t.id=ta.entity_id"
            )
            params.append(target_alias)
        else:
            target_join = f"JOIN entities t ON t.normalized_name={source_key}"
        scope, scope_params = self._scope("s.page_id", "t.page_id")
        params.append(from_type)
        params.extend(to_types)
        params.append(min_length)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT
                {_columns('s')},
                {_columns('t', 'target_')},
                {source_label} AS matched_name
            FROM entities s
            {source_join}
            {target_join}
            WHERE s.entity_type=?
              AND t.entity_type IN ({','.join('?' for _ in to_types)})
              AND t.id != s.id
              AND length({source_key}) >= ?{scope}
            ORDER BY s.id, t.id
            """,
            (*params, *scope_params),
        )
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["id"], []).append(row)
        for group in grouped.values():
            if len(group) > max_ambiguity:
                continue
            for row in group:
                yield (
                    Entity.of(row),
                    Entity.of(row, "target_"),
                    row["matched_name"],
                )

    def texts(
        self, *entity_types: str, containing: str | None = None
    ) -> Iterator[tuple[Entity, str]]:
        """Walk the bodies of the pages entities live on, yielding
        `(entity, body)`.

        For evidence of the "the documentation says so itself" kind, where a
        page states its counterpart in prose. `containing` is a coarse SQL
        filter; the exact boundary is the pack's own business.
        """
        where = ""
        params: list[Any] = []
        if entity_types:
            where = f" AND e.entity_type IN ({','.join('?' for _ in entity_types)})"
            params.extend(entity_types)
        if containing:
            where += " AND c.content_text LIKE ?"
            params.append(f"%{containing}%")
        scope, scope_params = self._scope("e.page_id")
        for row in self.connection.execute(
            f"""
            SELECT DISTINCT {_ENTITY_COLUMNS}, c.content_text
            FROM entities e
            JOIN chunks c ON c.page_id=e.page_id
            WHERE 1=1{where}{scope}
            ORDER BY e.id
            """,
            (*params, *scope_params),
        ):
            yield Entity.of(row), row["content_text"]


def _write(
    connection: sqlite3.Connection,
    candidate: RelationCandidate,
    *,
    origin: str,
    now: str,
    replace: bool,
) -> bool:
    """Store one relation. False means it was rejected."""
    if candidate.source.id == candidate.target.id:
        return False  # pointing at itself is not a relation
    if not candidate.relation_type or not candidate.evidence_kind:
        return False
    confidence = min(1.0, max(0.0, float(candidate.confidence)))
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    connection.execute(
        f"""
        {verb} INTO relations(
            from_entity_id, to_entity_id, relation_type, evidence_kind,
            confidence, evidence_chunk_id, source_url, note, origin,
            created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.source.id,
            candidate.target.id,
            candidate.relation_type,
            candidate.evidence_kind,
            confidence,
            candidate.evidence_chunk_id,
            candidate.evidence_url or candidate.source.source_url,
            candidate.note or None,
            origin,
            now,
            now,
        ),
    )
    return True


def _official_links(
    connection: sqlite3.Connection, now: str, page_ids: list[int] | None
) -> int:
    """A link from one document to another is a relation. True of any
    documentation site, so it belongs to the core."""
    scope = ""
    params: tuple[Any, ...] = ()
    if page_ids is not None:
        placeholders = ",".join("?" for _ in page_ids)
        scope = (
            f" AND (page_links.from_page_id IN ({placeholders})"
            f" OR page_links.target_page_id IN ({placeholders}))"
        )
        params = (*page_ids, *page_ids)
    # Resolve target paths to page ids first: a target may have arrived in
    # this very round.
    resolve_link_targets(connection)
    created = 0
    for row in connection.execute(
        f"""
        SELECT
            source_entity.id AS from_id,
            target_entity.id AS to_id,
            page_links.link_kind,
            page_links.source_url,
            (
                SELECT c.id FROM chunks c
                WHERE c.section_id=page_links.from_section_id
                ORDER BY c.chunk_index LIMIT 1
            ) AS chunk_id
        FROM page_links
        JOIN entities source_entity
            ON source_entity.page_id=page_links.from_page_id
        JOIN entities target_entity
            ON target_entity.page_id=page_links.target_page_id
        WHERE page_links.evidence_kind='official_link'
          AND source_entity.id != target_entity.id
          -- The link is from **this page** to another, not one link per
          -- member on it. Without this guard, 60 members × 20 links become
          -- 1200 identical relations.
          AND source_entity.member_of_id IS NULL
          AND target_entity.member_of_id IS NULL{scope}
        """,
        params,
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO relations(
                from_entity_id, to_entity_id, relation_type, evidence_kind,
                confidence, evidence_chunk_id, source_url, origin,
                created_at, updated_at
            ) VALUES(?, ?, ?, 'official_link', 1.0, ?, ?, ?, ?, ?)
            """,
            (
                row["from_id"],
                row["to_id"],
                RELATION_TYPE_BY_LINK_KIND.get(row["link_kind"], "official_reference"),
                row["chunk_id"],
                row["source_url"],
                CORE_ORIGIN,
                now,
                now,
            ),
        )
        created += 1
    return created


def _member_links(
    connection: sqlite3.Connection, now: str, page_ids: list[int] | None
) -> int:
    """Listed in a member table = it belongs to what the page is about.

    Like an official link, a crawled fact rather than an inference: the page
    lists the member itself. True of any site with per-type pages, so it
    belongs to the core.
    """
    scope = ""
    params: tuple[Any, ...] = ()
    if page_ids is not None:
        placeholders = ",".join("?" for _ in page_ids)
        scope = f" AND m.page_id IN ({placeholders})"
        params = tuple(page_ids)
    created = 0
    for row in connection.execute(
        f"SELECT m.id, m.member_of_id, m.source_url FROM entities m"
        f" WHERE m.member_of_id IS NOT NULL{scope}",
        params,
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO relations(
                from_entity_id, to_entity_id, relation_type, evidence_kind,
                confidence, source_url, origin, created_at, updated_at
            ) VALUES(?, ?, 'belongs_to', 'page_member_table', 1.0, ?, ?, ?, ?)
            """,
            (row["id"], row["member_of_id"], row["source_url"], CORE_ORIGIN, now, now),
        )
        created += 1
    return created


def rebuild(
    connection: sqlite3.Connection, *, page_ids: list[int] | None = None
) -> dict[str, Any]:
    """Build relations. `page_ids=None` rebuilds everything, a list fills in
    only those pages.

    A full rebuild first deletes by origin what the pack inferred last round;
    official links are crawled facts and are simply replayed with `INSERT OR
    IGNORE`. An incremental run only appends, and deletes nothing.
    """
    workspace = active()
    pack = workspace.dataset.knowledge or ""
    now = utc_now()
    full = page_ids is None

    if full and pack:
        connection.execute("DELETE FROM relations WHERE origin=?", (pack,))

    official = _official_links(connection, now, page_ids)
    members = _member_links(connection, now, page_ids)

    graph = RelationGraph(connection, page_ids=page_ids)
    rules = workspace.hook("relation_rules")
    accepted = 0
    rejected = 0
    if rules:
        for candidate in rules(graph):
            if _write(connection, candidate, origin=pack, now=now, replace=full):
                accepted += 1
            else:
                rejected += 1

    connection.commit()
    return {
        "official_links": official,
        "member_links": members,
        "domain_relations": accepted,
        "rejected": rejected,
        # Names a domain rule wanted to link to but the library has no entity
        # for. Usually the target page is unfetched; sometimes the source
        # never enumerated it into the inventory at all.
        "unresolved_targets": sorted(set(graph.unresolved))[:20],
        "scope": "full" if full else f"{len(page_ids or [])} pages",
    }


def link_target_gaps(
    connection: sqlite3.Connection, *, limit: int = 5
) -> dict[str, Any]:
    """Links point at other documents that cannot be reached — and which
    kind of unreachable it is.

    The two look alike but the next step is the opposite:

    * **Target unfetched**: the inventory has the page, only its body is
      missing. One `get` and it is there.
    * **Target not in the inventory**: the source adapter never enumerated
      it. Fetching will never help; the adapter's enumeration is what has
      to change.

    Both used to surface as "this entity has no relations", which sends the
    investigation the wrong way.

    The test for "the source missed a region" cannot fire by accident: **an
    area other pages link into, where the inventory holds no page at all**.
    A few stray misses are ordinary noise (reorganisation, typos,
    non-document pages); a whole area with nothing enumerated is a gap in
    the source's scope.
    """
    pending = connection.execute(
        "SELECT COUNT(*) FROM page_links l JOIN pages p ON p.id=l.target_page_id"
        " WHERE p.status NOT IN ('success', 'redirect')"
    ).fetchone()[0]
    missing_paths = [
        row[0]
        for row in connection.execute(
            "SELECT target_path FROM page_links"
            " WHERE target_path IS NOT NULL AND target_page_id IS NULL"
        )
    ]
    by_area: dict[str, int] = {}
    for path in missing_paths:
        area = "/".join(path.rstrip("/").split("/")[:-1])
        by_area[area] = by_area.get(area, 0) + 1
    uncovered = []
    for area, count in sorted(by_area.items(), key=lambda kv: -kv[1]):
        if not area:
            continue
        covered = connection.execute(
            "SELECT 1 FROM pages WHERE path LIKE ? LIMIT 1", (area + "/%",)
        ).fetchone()
        if covered is None:
            uncovered.append({"area": area, "links": count})
    return {
        "pending_targets": pending,
        "missing_targets": len(missing_paths),
        "uncovered_areas": len(uncovered),
        "top_uncovered_areas": uncovered[:limit],
    }


def page_link_status(
    connection: sqlite3.Connection, page_ids: list[int], *, limit: int = 5
) -> dict[str, list[dict[str, str]]]:
    """The current state of everything these pages link to.

    "This entity has no relations" is too coarse. Whether the pages it
    points at are unfetched or absent from the inventory decides whether
    the next step is a fetch or a change to the source, so the two are
    reported apart.
    """
    if not page_ids:
        return {"pending": [], "missing": []}
    placeholders = ",".join("?" for _ in page_ids)
    pending = [
        {"path": row["path"], "url": row["url"]}
        for row in connection.execute(
            f"""
            SELECT DISTINCT p.path, p.url FROM page_links l
            JOIN pages p ON p.id=l.target_page_id
            WHERE l.from_page_id IN ({placeholders})
              AND p.status NOT IN ('success', 'redirect')
            LIMIT ?
            """,
            (*page_ids, limit),
        )
    ]
    missing = [
        {"path": row["target_path"], "url": row["target_url"]}
        for row in connection.execute(
            f"""
            SELECT DISTINCT target_path, target_url FROM page_links
            WHERE from_page_id IN ({placeholders})
              AND target_path IS NOT NULL AND target_page_id IS NULL
            LIMIT ?
            """,
            (*page_ids, limit),
        )
    ]
    return {"pending": pending, "missing": missing}


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "aliases": connection.execute(
            "SELECT COUNT(*) FROM entity_aliases"
        ).fetchone()[0],
        "page_links": connection.execute(
            "SELECT COUNT(*) FROM page_links"
        ).fetchone()[0],
        "relations": connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
    }


def build_cross_index(connection: sqlite3.Connection) -> dict[str, int]:
    """Full rebuild; the entry point behind the `cross-index` command."""
    rebuild(connection)
    return counts(connection)


def link_new_pages(connection: sqlite3.Connection, page_ids: list[int]) -> int:
    """Fill in relations for pages just fetched on demand.

    Runs the very same rules as a full rebuild, so a pack is written once
    and the two paths cannot drift apart.
    """
    if not page_ids:
        return 0
    outcome = rebuild(connection, page_ids=page_ids)
    return (
        outcome["official_links"]
        + outcome["member_links"]
        + outcome["domain_relations"]
    )
