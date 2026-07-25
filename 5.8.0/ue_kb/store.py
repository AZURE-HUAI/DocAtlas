"""把一页的解析结果写进数据库（小节、知识块、实体、链接、图片）。"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any
import zlib

from .config import VERSION
from .util import utc_now
from .chunking import normalize_name


# 这些错误说明"服务器现在不想理我们"，而不是"这一页有问题"。
THROTTLE_MARKERS = ("HTTP 429", "HTTP 403", "HTTP 503", "HTTP 502", "HTTP 504")


def _is_throttled(error: str) -> bool:
    return any(marker in error for marker in THROTTLE_MARKERS)


def store_document_result(
    connection: sqlite3.Connection,
    result: dict[str, Any],
    category: str,
) -> None:
    page_id = result["id"]
    stored_at = utc_now()
    if not result["ok"]:
        error = result["error"]
        if _is_throttled(error):
            # 被限流不是这一页的错：留在 pending，也不消耗重试次数，
            # 否则一次限流风暴就会把几千页永久判死。
            connection.execute(
                "UPDATE pages SET status='pending', error=?, fetched_at=? WHERE id=?",
                (error[:2000], stored_at, page_id),
            )
            return
        connection.execute(
            """
            UPDATE pages
            SET status='failed', attempts=attempts+1, error=?, fetched_at=?
            WHERE id=?
            """,
            (error[:2000], stored_at, page_id),
        )
        return
    if result["status"] == "redirect":
        content_hash = hashlib.sha256(result["raw"]).hexdigest()
        connection.execute(
            """
            INSERT OR IGNORE INTO raw_documents(
                page_id, content_hash, fetched_at, source_url, raw_json
            )
            SELECT id, ?, ?, url, ? FROM pages WHERE id=?
            """,
            (
                content_hash,
                stored_at,
                zlib.compress(result["raw"], level=6),
                page_id,
            ),
        )
        connection.execute(
            """
            UPDATE pages SET
                status='redirect',
                attempts=attempts+1,
                error=NULL,
                redirect_url=?,
                raw_json=NULL,
                fetched_at=?
            WHERE id=?
            """,
            (
                result["redirect_url"],
                stored_at,
                page_id,
            ),
        )
        return

    connection.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE page_id=?)",
        (page_id,),
    )
    connection.execute(
        "DELETE FROM sections_fts WHERE rowid IN (SELECT id FROM sections WHERE page_id=?)",
        (page_id,),
    )
    connection.execute("DELETE FROM page_links WHERE from_page_id=?", (page_id,))
    connection.execute("DELETE FROM entities WHERE page_id=?", (page_id,))
    connection.execute("DELETE FROM sections WHERE page_id=?", (page_id,))
    connection.execute(
        """
        INSERT OR IGNORE INTO raw_documents(
            page_id, content_hash, fetched_at, source_url, raw_json
        )
        SELECT id, ?, ?, url, ? FROM pages WHERE id=?
        """,
        (
            result["content_hash"],
            stored_at,
            zlib.compress(result["raw"], level=6),
            page_id,
        ),
    )
    connection.execute(
        """
        UPDATE pages SET
            status='success',
            title=?,
            description=?,
            source_type=?,
            document_type=?,
            version_supported=?,
            updated_at=?,
            fetched_at=?,
            attempts=attempts+1,
            error=NULL,
            redirect_url=NULL,
            content_hash=?,
            section_count=?,
            raw_json=NULL
        WHERE id=?
        """,
        (
            result["title"],
            result["description"],
            result["source_type"],
            result["document_type"],
            result["version_supported"],
            result["updated_at"],
            stored_at,
            result["content_hash"],
            len(result["sections"]),
            page_id,
        ),
    )
    section_ids: dict[int, int] = {}
    for section in result["sections"]:
        cursor = connection.execute(
            """
            INSERT INTO sections(
                page_id, position, heading_level, heading_path, title,
                content_md, content_text, source_url, token_estimate,
                knowledge_type, source_anchor, body_md, content_hash,
                quality_score
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                section["position"],
                section["heading_level"],
                section["heading_path"],
                section["title"],
                section["content_md"],
                section["content_text"],
                section["source_url"],
                section["token_estimate"],
                section["knowledge_type"],
                section["source_anchor"],
                section["body_md"],
                section["content_hash"],
                section["quality_score"],
            ),
        )
        section_id = cursor.lastrowid
        section_ids[section["position"]] = section_id
        connection.execute(
            """
            INSERT INTO sections_fts(
                rowid, title, heading_path, content_text, source_url, category
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                section_id,
                result["title"],
                section["heading_path"],
                section["content_text"],
                section["source_url"],
                category,
            ),
        )
    entity = result["entity"]
    entity_cursor = connection.execute(
        """
        INSERT INTO entities(
            page_id, entity_type, canonical_name, normalized_name,
            qualified_name, module, owner_type, signature, source_url,
            version, attributes_json, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            page_id,
            entity["entity_type"],
            entity["canonical_name"],
            entity["normalized_name"],
            entity["qualified_name"],
            entity["module"],
            entity["owner_type"],
            entity["signature"],
            entity["source_url"],
            entity["version"],
            entity["attributes_json"],
            stored_at,
            stored_at,
        ),
    )
    entity_id = entity_cursor.lastrowid
    connection.executemany(
        """
        INSERT OR IGNORE INTO entity_aliases(
            entity_id, alias, normalized_alias, alias_type, source
        ) VALUES(?, ?, ?, ?, 'document')
        """,
        [
            (entity_id, alias, normalize_name(alias), alias_type)
            for alias, alias_type in entity["aliases"]
            if alias and normalize_name(alias)
        ],
    )
    for chunk in result["chunks"]:
        section_id = section_ids[chunk["section_position"]]
        chunk_cursor = connection.execute(
            """
            INSERT INTO chunks(
                section_id, page_id, chunk_index, chunk_count,
                knowledge_type, title, heading_path, context_prefix,
                content_md, content_text, source_url, source_anchor,
                token_estimate, content_hash, quality_score,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section_id,
                page_id,
                chunk["chunk_index"],
                chunk["chunk_count"],
                chunk["knowledge_type"],
                chunk["title"],
                chunk["heading_path"],
                chunk["context_prefix"],
                chunk["content_md"],
                chunk["content_text"],
                chunk["source_url"],
                chunk["source_anchor"],
                chunk["token_estimate"],
                chunk["content_hash"],
                chunk["quality_score"],
                stored_at,
                stored_at,
            ),
        )
        chunk_id = chunk_cursor.lastrowid
        connection.execute(
            """
            INSERT INTO chunks_fts(
                rowid, title, heading_path, context_prefix, content_text,
                source_url, category, knowledge_type
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                result["title"],
                chunk["heading_path"],
                chunk["context_prefix"],
                chunk["content_text"],
                chunk["source_url"],
                category,
                chunk["knowledge_type"],
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO knowledge_entities(
                chunk_id, entity_id, role, confidence
            ) VALUES(?, ?, 'primary_subject', 1.0)
            """,
            (chunk_id, entity_id),
        )
        tag_values = {
            (category, "category"),
            (chunk["knowledge_type"], "knowledge_type"),
            (VERSION, "ue_version"),
        }
        if result["source_type"]:
            tag_values.add((str(result["source_type"]), "source_type"))
        if result["document_type"]:
            tag_values.add((str(result["document_type"]), "document_type"))
        if entity["module"]:
            tag_values.add((str(entity["module"]), "module"))
        for tag_name, tag_type in tag_values:
            connection.execute(
                "INSERT OR IGNORE INTO tags(name, tag_type) VALUES(?, ?)",
                (tag_name, tag_type),
            )
            tag_id = connection.execute(
                "SELECT id FROM tags WHERE name=? AND tag_type=?",
                (tag_name, tag_type),
            ).fetchone()[0]
            connection.execute(
                "INSERT OR IGNORE INTO chunk_tags(chunk_id, tag_id) VALUES(?, ?)",
                (chunk_id, tag_id),
            )
    for link in result["page_links"]:
        connection.execute(
            """
            INSERT OR IGNORE INTO page_links(
                from_page_id, from_section_id, target_url, target_path,
                anchor_text, link_kind, evidence_kind, source_url, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                section_ids.get(link["section_position"]),
                link["target_url"],
                link["target_path"],
                link["anchor_text"],
                link["link_kind"],
                link["evidence_kind"],
                link["source_url"],
                stored_at,
            ),
        )
    connection.executemany(
        """
        INSERT INTO assets(url, page_id)
        VALUES(?, ?)
        ON CONFLICT(url) DO NOTHING
        """,
        [(asset_url, page_id) for asset_url in result["assets"]],
    )
