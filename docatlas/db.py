"""SQLite 连接、表结构与轻量迁移。"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import urllib.parse

from .runtime import active
from .util import utc_now


def inventory_index_url() -> str:
    """清单入口的总地址，纯粹是溯源信息，没有任何代码依赖它。

    只有站点地图型来源才有"一个总入口"这个概念。用分页 API、目录页或静态
    索引列页的来源（`inventory_feeds` / `read_feed`）压根没有总入口——那种
    来源不实现 `sitemap_index_url`，这里就留空，而不是让开库直接崩掉。
    """
    workspace = active()
    index_url = getattr(workspace.source, "sitemap_index_url", None)
    return index_url(workspace.dataset) if index_url else ""


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    # 默认值不能写在签名里：那是导入时求值，MCP 切了数据集也还是老路径。
    path = path or active().db_path
    # 第一次用的时候数据目录还不存在，不建的话 sqlite 只会甩一句
    # "unable to open database file"，看不出该干嘛。
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
    # 按需抓取靠它定位页面：没抓过的页面只有 path，没有标题。
    # 关系是谁建的：核心记 'core'，领域知识包记包名。全量重建按它清理，
    # 领域包因此不必再自己维护一张"我会产出哪些 evidence_kind"的清单——
    # 那张清单漏写一项，就会留下一条永远删不掉的死关系。
    if add_column_if_missing(connection, "relations", "origin", "TEXT"):
        connection.execute(
            "UPDATE relations SET origin=CASE WHEN evidence_kind='official_link'"
            " THEN 'core' ELSE ? END WHERE origin IS NULL",
            (active().dataset.knowledge or "core",),
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_relations_origin ON relations(origin)"
    )
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
    # 相邻块指针。刻意不做"内容重叠"：重叠会让 content_hash 失去唯一性，
    # 直接破坏上下文包的去重，还会把全文索引撑大。要上下文就顺着指针去取。
    add_column_if_missing(connection, "chunks", "prev_chunk_id", "INTEGER")
    add_column_if_missing(connection, "chunks", "next_chunk_id", "INTEGER")
    # 页面级的加工版本。有了它，重切可以只挑没做过的页，中途断掉能接着跑；
    # 也能覆盖"这一页加工完一个知识块都没有"的情况（纯导航页就是这样）。
    if add_column_if_missing(connection, "pages", "parser_version", "TEXT"):
        connection.execute(
            "UPDATE pages SET parser_version='v1' WHERE status='success'"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_parser ON pages(parser_version)"
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
    dataset = active().dataset
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
        [
            ("dataset", dataset.id),
            ("product", dataset.product),
            ("doc_version", dataset.version),
            ("language", dataset.language),
            ("source", dataset.name),
            ("source_adapter", dataset.source),
            ("knowledge_pack", dataset.knowledge or ""),
            ("inventory_index", inventory_index_url()),
            ("schema_version", "3"),
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


def rename_column_if_present(
    connection: sqlite3.Connection, table: str, old: str, new: str
) -> bool:
    """老库改名用；SQLite 的 RENAME COLUMN 只改元数据，不重写数据。"""
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if old not in columns or new in columns:
        return False
    connection.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    return True


def migrate_metadata_key(connection: sqlite3.Connection, old: str, new: str) -> None:
    """老库改名用；`metadata.key` 是主键，改名不能靠 UPDATE 就完事——如果两个
    key 都在（旧库跑过一次 initialize 之后新 key 已经写入），旧的那行就是
    死数据，删掉；如果只有旧 key，直接把它改名。"""
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
    """老库改名用。`tags` 表 `UNIQUE(name, tag_type)`，同一个 name 在旧库里可能
    已经同时有 old 和 new 两个 tag_type（新代码跑过一次之后）。逐个改名会撞
    UNIQUE 约束，所以撞了就把引用旧 tag 的 chunk_tags 转投新 tag，再删旧
    tag；没撞就直接改名。"""
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


def backfill_page_metadata(connection: sqlite3.Connection) -> None:
    rows = list(
        connection.execute(
            """
            SELECT id, path FROM pages
            WHERE doc_version IS NULL OR locale IS NULL OR route_depth IS NULL
               OR discovered_at IS NULL OR last_seen_at IS NULL
            """
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


# 静态站点会把实现细节写进地址（`fields.html`、`index.php`）。用户说的是
# 页面名，不会带这一截，所以定位前先去掉。只认这几种确定是"文件类型"的后缀，
# 免得把 `UObject.Tick` 这种名字里的点当成扩展名切掉。
PAGE_EXTENSIONS = frozenset(
    {"html", "htm", "xhtml", "shtml", "php", "asp", "aspx", "jsp", "md", "txt"}
)
# 改了 page_slug 的规则就 +1：已有库里的 slug 会被整批重算，
# 否则新规则只对以后发现的页面生效，同一个库里两套 slug 并存。
SLUG_VERSION = "2"


def page_slug(path: str) -> str:
    """URL 最后一段，标准化后用于精确定位。

    `/…/UKismetSystemLibrary/K2_SetTimer` → `k2settimer`，
    `/modeling/geometry_nodes/fields.html` → `fields`，
    而用户问的 `K2_SetTimer` / `Fields` 标准化后正好是同一串。
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
