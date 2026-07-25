"""SQLite 连接、表结构与轻量迁移。"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import urllib.parse

from .config import (
    CHUNKER_VERSION,
    DB_PATH,
    DOC_PREFIX,
    LANGUAGE,
    SITEMAP_INDEX_URL,
    VERSION,
)
from .util import utc_now


def connect_db(path: Path = DB_PATH) -> sqlite3.Connection:
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
    add_column_if_missing(connection, "pages", "ue_version", "TEXT")
    add_column_if_missing(connection, "pages", "locale", "TEXT")
    add_column_if_missing(connection, "pages", "route_depth", "INTEGER")
    add_column_if_missing(connection, "pages", "parent_path", "TEXT")
    add_column_if_missing(connection, "pages", "discovered_at", "TEXT")
    add_column_if_missing(connection, "pages", "last_seen_at", "TEXT")
    add_column_if_missing(connection, "pages", "deleted_at", "TEXT")
    # 按需抓取靠它定位页面：没抓过的页面只有 path，没有标题。
    add_column_if_missing(connection, "pages", "normalized_slug", "TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_slug ON pages(normalized_slug)"
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
    # 加列的那一刻，库里已有的块必然都是加列之前的规则产出的，统一标 v1。
    # 只在真正加列时回填一次：之后写入都自带版本号，再回填反而会覆盖真相。
    if add_column_if_missing(connection, "chunks", "parser_version", "TEXT"):
        connection.execute(
            "UPDATE chunks SET parser_version='v1' WHERE parser_version IS NULL"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_parser ON chunks(parser_version)"
    )
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
                title,
                heading_path,
                content_text,
                source_url UNINDEXED,
                category UNINDEXED,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('fts', 'fts5')"
        )
    except sqlite3.OperationalError:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sections_fts (
                rowid INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                heading_path TEXT NOT NULL,
                content_text TEXT NOT NULL,
                source_url TEXT NOT NULL,
                category TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('fts', 'fallback')"
        )
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title,
                heading_path,
                context_prefix,
                content_text,
                source_url UNINDEXED,
                category UNINDEXED,
                knowledge_type UNINDEXED,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('chunk_fts', 'fts5')"
        )
    except sqlite3.OperationalError:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks_fts (
                rowid INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                heading_path TEXT NOT NULL,
                context_prefix TEXT NOT NULL,
                content_text TEXT NOT NULL,
                source_url TEXT NOT NULL,
                category TEXT NOT NULL,
                knowledge_type TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('chunk_fts', 'fallback')"
        )
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
        [
            ("ue_version", VERSION),
            ("language", LANGUAGE),
            ("source", "Epic Developer Community"),
            ("sitemap_index", SITEMAP_INDEX_URL),
            ("schema_version", "2"),
        ],
    )
    backfill_page_metadata(connection)
    backfill_page_slugs(connection)
    connection.commit()


def add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> bool:
    """加列；返回 True 表示这次真的加了（可据此做一次性回填）。"""
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column in columns:
        return False
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def route_metadata(path: str) -> tuple[int, str | None]:
    relative = path[len(DOC_PREFIX) :] if path.lower().startswith(DOC_PREFIX.lower()) else path.strip("/")
    segments = [segment for segment in relative.split("/") if segment]
    parent = "/".join(path.rstrip("/").split("/")[:-1]) or None
    return len(segments), parent


def backfill_page_metadata(connection: sqlite3.Connection) -> None:
    rows = list(
        connection.execute(
            """
            SELECT id, path FROM pages
            WHERE ue_version IS NULL OR locale IS NULL OR route_depth IS NULL
               OR discovered_at IS NULL OR last_seen_at IS NULL
            """
        )
    )
    if not rows:
        return
    now = utc_now()
    values = []
    for row in rows:
        depth, parent = route_metadata(row["path"])
        values.append((VERSION, LANGUAGE, depth, parent, now, now, row["id"]))
    connection.executemany(
        """
        UPDATE pages SET
            ue_version=COALESCE(ue_version, ?),
            locale=COALESCE(locale, ?),
            route_depth=COALESCE(route_depth, ?),
            parent_path=COALESCE(parent_path, ?),
            discovered_at=COALESCE(discovered_at, ?),
            last_seen_at=COALESCE(last_seen_at, ?)
        WHERE id=?
        """,
        values,
    )


def page_slug(path: str) -> str:
    """URL 最后一段，标准化后用于精确定位。

    `/…/UKismetSystemLibrary/K2_SetTimer` → `k2settimer`，
    而用户问的 `K2_SetTimer` 标准化后也是 `k2settimer`——两边能对上。
    """
    from .chunking import normalize_name

    tail = urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1])
    return normalize_name(tail)


def backfill_page_slugs(connection: sqlite3.Connection) -> None:
    rows = list(
        connection.execute(
            "SELECT id, path FROM pages WHERE normalized_slug IS NULL LIMIT 400000"
        )
    )
    if not rows:
        return
    connection.executemany(
        "UPDATE pages SET normalized_slug=? WHERE id=?",
        [(page_slug(row["path"]), row["id"]) for row in rows],
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
