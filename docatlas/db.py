"""SQLite connection, schema, and in-place migrations."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import urllib.parse

from .runtime import active
from .util import utc_now

# Schema version. New tables, columns and indexes all migrate in place in
# initialize_db; this number is recorded in the database purely so an
# unfamiliar file can be identified as belonging to a given generation.
SCHEMA_VERSION = "3"


def inventory_index_url() -> str:
    """Address of the inventory entry point: provenance only, nothing reads it.

    Only sitemap-style sources have the notion of "one entry point". Sources
    built on a paginated API, directory pages or static index listings
    (`inventory_feeds` / `read_feed`) have none at all; they do not implement
    `sitemap_index_url`, so this stays empty rather than crashing on open.
    """
    workspace = active()
    index_url = getattr(workspace.source, "sitemap_index_url", None)
    return index_url(workspace.dataset) if index_url else ""


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    # Not a default in the signature: that evaluates at import time, so a
    # dataset switch would still open the old path.
    path = path or active().db_path
    # On first use the data directory does not exist yet; without this,
    # sqlite only says "unable to open database file", which tells the
    # user nothing about what to do.
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=120)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA busy_timeout=120000")
    return connection


def initialize_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sitemaps (
            url TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            url_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            sitemap_url TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT,
            description TEXT,
            source_type TEXT,
            document_type TEXT,
            version_supported INTEGER,
            updated_at TEXT,
            fetched_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            redirect_url TEXT,
            content_hash TEXT,
            section_count INTEGER NOT NULL DEFAULT 0,
            raw_json BLOB,
            FOREIGN KEY (sitemap_url) REFERENCES sitemaps(url)
        );

        CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
        CREATE INDEX IF NOT EXISTS idx_pages_category ON pages(category);

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY,
            page_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            heading_level INTEGER NOT NULL,
            heading_path TEXT NOT NULL,
            title TEXT NOT NULL,
            content_md TEXT NOT NULL,
            content_text TEXT NOT NULL,
            source_url TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            UNIQUE(page_id, position),
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_sections_page ON sections(page_id);

        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            page_id INTEGER,
            local_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            content_type TEXT,
            bytes INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            fetched_at TEXT,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS raw_documents (
            id INTEGER PRIMARY KEY,
            page_id INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source_url TEXT NOT NULL,
            raw_json BLOB NOT NULL,
            UNIQUE(page_id, content_hash),
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            section_id INTEGER NOT NULL,
            page_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            knowledge_type TEXT NOT NULL,
            title TEXT NOT NULL,
            heading_path TEXT NOT NULL,
            context_prefix TEXT NOT NULL,
            content_md TEXT NOT NULL,
            content_text TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_anchor TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            quality_score REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE(section_id, chunk_index),
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(knowledge_type);
        CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash);

        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            page_id INTEGER NOT NULL,
            entity_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            qualified_name TEXT,
            module TEXT,
            owner_type TEXT,
            signature TEXT,
            source_url TEXT NOT NULL,
            version TEXT NOT NULL,
            attributes_json TEXT NOT NULL DEFAULT '{}',
            -- Whether this entity is a member documented on another entity's
            -- page. NULL = it is what the page itself is about. Members always
            -- live on the owner's page, so deleting by page_id clears them too.
            member_of_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE(page_id, entity_type, normalized_name),
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_entities_normalized
            ON entities(normalized_name, entity_type);
        CREATE INDEX IF NOT EXISTS idx_entities_page ON entities(page_id);

        CREATE TABLE IF NOT EXISTS entity_aliases (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE(entity_id, normalized_alias, alias_type),
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_aliases_normalized
            ON entity_aliases(normalized_alias);

        CREATE TABLE IF NOT EXISTS knowledge_entities (
            chunk_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            confidence REAL NOT NULL,
            PRIMARY KEY(chunk_id, entity_id, role),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS page_links (
            id INTEGER PRIMARY KEY,
            from_page_id INTEGER NOT NULL,
            from_section_id INTEGER,
            target_url TEXT NOT NULL,
            target_path TEXT,
            target_page_id INTEGER,
            anchor_text TEXT NOT NULL,
            link_kind TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            source_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(from_page_id, from_section_id, target_url, anchor_text),
            FOREIGN KEY (from_page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY (from_section_id) REFERENCES sections(id) ON DELETE CASCADE,
            FOREIGN KEY (target_page_id) REFERENCES pages(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_page_links_target_path
            ON page_links(target_path);

        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY,
            from_entity_id INTEGER NOT NULL,
            to_entity_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_chunk_id INTEGER,
            source_url TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE(from_entity_id, to_entity_id, relation_type, evidence_kind),
            FOREIGN KEY (from_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (to_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (evidence_chunk_id) REFERENCES chunks(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_entity_id);
        CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_entity_id);

        -- Which versions a chunk applies to. The marks come from the domain
        -- layer reading the prose, so they are facts the documentation states
        -- rather than inferences. The core only stores and compares them and
        -- knows no versioning scheme; the sort key comes from the domain layer.
        -- `scope` records where a mark sat: heading qualifies the section, body
        -- only its own line, and only heading is hard enough to exclude on.
        -- See the module docstring of versions.py.
        CREATE TABLE IF NOT EXISTS chunk_versions (
            chunk_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            sort_key TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'body',
            PRIMARY KEY(chunk_id, kind, label),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chunk_versions_chunk
            ON chunk_versions(chunk_id);

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            UNIQUE(name, tag_type)
        );

        CREATE TABLE IF NOT EXISTS chunk_tags (
            chunk_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY(chunk_id, tag_id),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
        """
    )
    rename_column_if_present(connection, "pages", "ue_version", "doc_version")
    migrate_metadata_key(connection, "ue_version", "doc_version")
    migrate_metadata_key(connection, "sitemap_index", "inventory_index")
    migrate_tag_type(connection, "ue_version", "doc_version")
    add_column_if_missing(connection, "pages", "doc_version", "TEXT")
    add_column_if_missing(connection, "pages", "locale", "TEXT")
    add_column_if_missing(connection, "pages", "route_depth", "INTEGER")
    add_column_if_missing(connection, "pages", "parent_path", "TEXT")
    add_column_if_missing(connection, "pages", "discovered_at", "TEXT")
    add_column_if_missing(connection, "pages", "last_seen_at", "TEXT")
    add_column_if_missing(connection, "pages", "deleted_at", "TEXT")
    # Who created a relation: 'core' for the core, otherwise the knowledge
    # pack's name. A full rebuild cleans up by this column, so a pack no
    # longer has to keep its own list of "evidence kinds I produce" — one
    # missing entry there leaves a dead relation nothing can ever delete.
    if add_column_if_missing(connection, "relations", "origin", "TEXT"):
        connection.execute(
            "UPDATE relations SET origin=CASE WHEN evidence_kind='official_link'"
            " THEN 'core' ELSE ? END WHERE origin IS NULL",
            (active().dataset.knowledge or "core",),
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_relations_origin ON relations(origin)"
    )
    # In a database gaining the scope column, every existing mark was computed
    # without scopes and must be recomputed: old rows all read as body, and
    # since only heading can exclude, skipping the rebuild would silently
    # disable version restriction. Clearing the stamp makes backfill redo it.
    if add_column_if_missing(
        connection, "chunk_versions", "scope", "TEXT NOT NULL DEFAULT 'body'"
    ):
        connection.execute("DELETE FROM metadata WHERE key='version_marks'")
    # When an old database gains the member column, its existing entities are
    # all one-per-page, so a NULL member_of_id is already the truth about them.
    # Members are recomputed by the backfill below from bodies already stored.
    if add_column_if_missing(connection, "entities", "member_of_id", "INTEGER"):
        connection.execute("DELETE FROM metadata WHERE key='page_members'")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_entities_member_of"
        " ON entities(member_of_id)"
    )
    add_column_if_missing(connection, "pages", "normalized_slug", "TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_slug ON pages(normalized_slug)"
    )
    # Partial index covering only pages still missing derived metadata — see
    # backfill_page_metadata.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_metadata_pending ON pages(id)"
        f" WHERE {PENDING_METADATA_CONDITION}"
    )
    add_column_if_missing(
        connection, "sections", "knowledge_type", "TEXT NOT NULL DEFAULT 'overview'"
    )
    add_column_if_missing(connection, "sections", "source_anchor", "TEXT")
    add_column_if_missing(connection, "sections", "body_md", "TEXT")
    add_column_if_missing(connection, "sections", "content_hash", "TEXT")
    add_column_if_missing(
        connection, "sections", "quality_score", "REAL NOT NULL DEFAULT 1.0"
    )
    # At the moment the column appears, every chunk already stored was produced
    # by the rules that predate it, so they are all v1. Backfilled once, only
    # when the column is actually added: later writes carry their own version,
    # and backfilling again would overwrite the truth.
    if add_column_if_missing(connection, "chunks", "parser_version", "TEXT"):
        connection.execute(
            "UPDATE chunks SET parser_version='v1' WHERE parser_version IS NULL"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_parser ON chunks(parser_version)"
    )
    # Neighbour pointers. Overlapping content is deliberately avoided: overlap
    # destroys the uniqueness of content_hash, which breaks deduplication in
    # context bundles, and inflates the full-text index. Follow the pointers
    # instead when more context is wanted.
    add_column_if_missing(connection, "chunks", "prev_chunk_id", "INTEGER")
    add_column_if_missing(connection, "chunks", "next_chunk_id", "INTEGER")
    # Per-page processing version. It lets a rechunk pick only pages not yet
    # done and resume after an interruption, and it also covers pages that
    # produced no chunk at all — pure navigation pages do exactly that.
    if add_column_if_missing(connection, "pages", "parser_version", "TEXT"):
        connection.execute(
            "UPDATE pages SET parser_version='v1' WHERE status='success'"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_parser ON pages(parser_version)"
    )
    fts_modes = {
        key: _create_fts(connection, table, columns)
        for key, table, columns in FTS_TABLES
    }
    write_metadata(connection, fts_modes)
    backfill_page_metadata(connection)
    backfill_page_slugs(connection)
    # Applicability is computed locally by running the domain layer's rules
    # over bodies already stored — no network. Datasets that declare no
    # version vocabulary are skipped, so a library of a few hundred thousand
    # pages is never scanned for a feature it cannot use.
    from .versions import backfill as backfill_chunk_versions

    backfill_chunk_versions(connection)
    # Member entities likewise: the section bodies of fetched pages are all
    # here, so the tables are simply reread — nothing is fetched again.
    from .members import backfill as backfill_page_members

    backfill_page_members(connection)
    # When the rule for "does this link point at in-scope documentation"
    # changes, links already stored must be reclassified, or one database
    # holds two verdicts and the inventory gap figures drift.
    from .coverage import reclassify_links

    reclassify_links(connection)
    connection.commit()


# Full-text tables. `UNINDEXED` columns ride along without being matched;
# per fts5 syntax the marker follows the column name, and on the plain-table
# fallback every column becomes TEXT.
# (metadata key recording the mode, table name, column definitions)
FTS_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "fts",
        "sections_fts",
        ("title", "heading_path", "content_text", "source_url UNINDEXED",
         "category UNINDEXED"),
    ),
    (
        "chunk_fts",
        "chunks_fts",
        ("title", "heading_path", "context_prefix", "content_text",
         "source_url UNINDEXED", "category UNINDEXED",
         "knowledge_type UNINDEXED"),
    ),
)


def _create_fts(
    connection: sqlite3.Connection, table: str, columns: tuple[str, ...]
) -> str:
    """Create the full-text table; returns the mode used (`fts5`/`fallback`).

    fts5 is a compile-time option and not every SQLite shipped with Python
    has it. Without it, a plain table is created and the search layer falls
    back to LIKE — weaker, but the machine still works.
    """
    try:
        connection.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5("
            + ", ".join(columns)
            + ", tokenize='unicode61 remove_diacritics 2')"
        )
        return "fts5"
    except sqlite3.OperationalError:
        plain = ", ".join(
            f"{column.split()[0]} TEXT NOT NULL" for column in columns
        )
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"(rowid INTEGER PRIMARY KEY, {plain})"
        )
        return "fallback"


def write_metadata(
    connection: sqlite3.Connection, fts_modes: dict[str, str] | None = None
) -> None:
    """Record the dataset identity, writing only when something changed.

    These values barely move once a library exists, yet `initialize_db` runs
    on every open, queries included. An unconditional `INSERT OR REPLACE` of
    nine rows would mean **taking a write lock on every query**: a read-only
    file could not be queried at all, and querying while crawling would make
    the two processes wait on each other. Reading first costs one SELECT and
    keeps the read path genuinely read-only.
    """
    dataset = active().dataset
    wanted = {
        "dataset": dataset.id,
        "product": dataset.product,
        "doc_version": dataset.version,
        "language": dataset.language,
        "source": dataset.name,
        "source_adapter": dataset.source,
        "knowledge_pack": dataset.knowledge or "",
        "schema_version": SCHEMA_VERSION,
        **(fts_modes or {}),
    }
    stored = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM metadata")
    }
    # The entry point has to be asked of the source adapter and may be
    # expensive; once stored, never asked again.
    if "inventory_index" not in stored:
        wanted["inventory_index"] = inventory_index_url()
    changed = [(key, value) for key, value in wanted.items() if stored.get(key) != value]
    if changed:
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)", changed
        )


def resolve_link_targets(connection: sqlite3.Connection) -> None:
    """Resolve link target paths to page ids.

    Only rows not yet resolved. Recomputing a resolved row could only ever
    produce the same answer: `pages.path` is UNIQUE and no code rewrites it
    after insert, and when a page is deleted the foreign key's `ON DELETE SET
    NULL` clears the column by itself.

    Not a micro-optimisation. In a large library 43,430 of 43,472 path-bearing
    links were already resolved, and every on-demand fetch rewrote them to
    their existing values — 0.086 seconds measured, growing linearly with
    crawl progress. With this condition it is 0.020 seconds, and flat.
    """
    connection.execute(
        """
        UPDATE page_links
        SET target_page_id=(
            SELECT p.id FROM pages p WHERE p.path=page_links.target_path
        )
        WHERE target_path IS NOT NULL AND target_page_id IS NULL
        """
    )


def add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> bool:
    """Add a column; True means it was actually added, which is the signal
    for a one-off backfill."""
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column in columns:
        return False
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def rename_column_if_present(
    connection: sqlite3.Connection, table: str, old: str, new: str
) -> bool:
    """Rename for older databases; SQLite's RENAME COLUMN only touches
    metadata and does not rewrite data."""
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if old not in columns or new in columns:
        return False
    connection.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    return True


def migrate_metadata_key(connection: sqlite3.Connection, old: str, new: str) -> None:
    """Rename for older databases. `metadata.key` is the primary key, so a
    plain UPDATE is not enough: when both keys exist (the new one was written
    the first time the newer code ran) the old row is dead data and is
    deleted; when only the old key exists it is renamed."""
    old_row = connection.execute(
        "SELECT value FROM metadata WHERE key=?", (old,)
    ).fetchone()
    if old_row is None:
        return
    new_exists = (
        connection.execute("SELECT 1 FROM metadata WHERE key=?", (new,)).fetchone()
        is not None
    )
    if new_exists:
        connection.execute("DELETE FROM metadata WHERE key=?", (old,))
    else:
        connection.execute(
            "UPDATE metadata SET key=? WHERE key=?", (new, old)
        )


def migrate_tag_type(connection: sqlite3.Connection, old: str, new: str) -> None:
    """Rename for older databases. `tags` is `UNIQUE(name, tag_type)`, and one
    name may already carry both the old and the new tag_type once the newer
    code has run. Renaming row by row would violate the constraint, so on a
    collision the chunk_tags pointing at the old tag are moved to the new one
    and the old tag is deleted; without a collision it is simply renamed."""
    old_tags = list(
        connection.execute("SELECT id, name FROM tags WHERE tag_type=?", (old,))
    )
    for row in old_tags:
        old_id, name = row["id"], row["name"]
        new_row = connection.execute(
            "SELECT id FROM tags WHERE name=? AND tag_type=?", (name, new)
        ).fetchone()
        if new_row is None:
            connection.execute(
                "UPDATE tags SET tag_type=? WHERE id=?", (new, old_id)
            )
            continue
        new_id = new_row["id"]
        connection.execute(
            "INSERT OR IGNORE INTO chunk_tags(chunk_id, tag_id) "
            "SELECT chunk_id, ? FROM chunk_tags WHERE tag_id=?",
            (new_id, old_id),
        )
        connection.execute("DELETE FROM chunk_tags WHERE tag_id=?", (old_id,))
        connection.execute("DELETE FROM tags WHERE id=?", (old_id,))


def route_metadata(path: str) -> tuple[int, str | None]:
    doc_prefix = active().doc_prefix
    relative = path[len(doc_prefix) :] if path.lower().startswith(doc_prefix.lower()) else path.strip("/")
    segments = [segment for segment in relative.split("/") if segment]
    parent = "/".join(path.rstrip("/").split("/")[:-1]) or None
    return len(segments), parent


# Which pages still lack derived metadata. A constant because the partial
# index below must match the query **verbatim** for SQLite to use it — two
# separate copies would silently fall back to a full scan the moment one of
# them was edited.
PENDING_METADATA_CONDITION = (
    "doc_version IS NULL OR locale IS NULL OR route_depth IS NULL"
    " OR discovered_at IS NULL OR last_seen_at IS NULL"
)


def backfill_page_metadata(connection: sqlite3.Connection) -> None:
    """Fill in pages missing derived metadata. Almost always none are, so
    this has to be cheap.

    It runs on **every** open, queries included. Without the partial index
    above it scans the whole pages table just to confirm "nothing to do" —
    0.105 seconds wasted per query on a library of 200k pages, about what
    `ask` itself costs. The partial index holds only rows genuinely missing a
    field, usually none, so the confirmation reads an empty index.
    """
    rows = list(
        connection.execute(
            f"SELECT id, path FROM pages WHERE {PENDING_METADATA_CONDITION}"
        )
    )
    if not rows:
        return
    now = utc_now()
    dataset = active().dataset
    values = []
    for row in rows:
        depth, parent = route_metadata(row["path"])
        values.append(
            (dataset.version, dataset.language, depth, parent, now, now, row["id"])
        )
    connection.executemany(
        """
        UPDATE pages SET
            doc_version=COALESCE(doc_version, ?),
            locale=COALESCE(locale, ?),
            route_depth=COALESCE(route_depth, ?),
            parent_path=COALESCE(parent_path, ?),
            discovered_at=COALESCE(discovered_at, ?),
            last_seen_at=COALESCE(last_seen_at, ?)
        WHERE id=?
        """,
        values,
    )


# Static sites write implementation detail into the address (`page.html`,
# `index.php`). Users say the page name without it, so it comes off before
# matching. Only suffixes that are unmistakably file types count, so that a
# dot inside a name such as `Type.Method` is not mistaken for an extension.
PAGE_EXTENSIONS = frozenset(
    {"html", "htm", "xhtml", "shtml", "php", "asp", "aspx", "jsp", "md", "txt"}
)
# Bump when the page_slug rules change: slugs in existing libraries are then
# recomputed in one pass. Otherwise the new rules would apply only to pages
# discovered later, leaving two kinds of slug in one database.
SLUG_VERSION = "2"


def page_slug(path: str) -> str:
    """The last URL segment, normalised for exact matching.

    `/…/<Owner>/<Some_Symbol>` → `somesymbol`, `/…/<group>/<topic>.html` →
    `topic` — and `Some_Symbol` or `Topic` as typed by the user normalises to
    exactly the same string.
    """
    from .text import normalize_name

    tail = urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1])
    stem, dot, extension = tail.rpartition(".")
    if dot and extension.casefold() in PAGE_EXTENSIONS:
        tail = stem
    return normalize_name(tail)


def backfill_page_slugs(connection: sqlite3.Connection) -> None:
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='slug_version'"
    ).fetchone()
    stale = not stored or stored[0] != SLUG_VERSION
    condition = "" if stale else " WHERE normalized_slug IS NULL"
    rows = list(connection.execute(f"SELECT id, path FROM pages{condition}"))
    if rows:
        connection.executemany(
            "UPDATE pages SET normalized_slug=? WHERE id=?",
            [(page_slug(row["path"]), row["id"]) for row in rows],
        )
    if stale:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('slug_version', ?)",
            (SLUG_VERSION,),
        )
    connection.commit()


def fts_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key='fts'"
    ).fetchone()
    return row[0] if row else "fallback"


def chunk_fts_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key='chunk_fts'"
    ).fetchone()
    return row[0] if row else "fallback"
