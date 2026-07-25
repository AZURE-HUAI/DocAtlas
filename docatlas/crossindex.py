"""蓝图 / C++ / 类型交叉关系的构建。"""

from __future__ import annotations

import re
import sqlite3

from .util import utc_now
from .chunking import normalize_name


def build_cross_index(connection: sqlite3.Connection) -> dict[str, int]:
    now = utc_now()
    connection.execute(
        """
        DELETE FROM relations
        WHERE evidence_kind IN (
            'exact_normalized_name',
            'document_statement',
            'unreal_display_name_metadata'
        )
        """
    )
    connection.execute(
        """
        UPDATE page_links
        SET target_page_id=(
            SELECT p.id FROM pages p WHERE p.path=page_links.target_path
        )
        WHERE target_path IS NOT NULL
        """
    )
    official_links = list(
        connection.execute(
            """
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
            """
        )
    )
    for row in official_links:
        relation_type = {
            "hierarchy": "belongs_to",
            "parameter_type": "parameter_type",
            "return_type": "return_type",
            "signature_reference": "signature_reference",
            "example_reference": "example_reference",
            "official_reference": "official_reference",
        }.get(row["link_kind"], "official_reference")
        connection.execute(
            """
            INSERT OR IGNORE INTO relations(
                from_entity_id, to_entity_id, relation_type, evidence_kind,
                confidence, evidence_chunk_id, source_url,
                created_at, updated_at
            ) VALUES(?, ?, ?, 'official_link', 1.0, ?, ?, ?, ?)
            """,
            (
                row["from_id"],
                row["to_id"],
                relation_type,
                row["chunk_id"],
                row["source_url"],
                now,
                now,
            ),
        )

    candidate_rows = list(
        connection.execute(
            """
            SELECT
                source.id AS from_id,
                target.id AS to_id,
                source.entity_type AS from_type,
                target.entity_type AS to_type,
                source.owner_type AS source_owner,
                target.owner_type AS target_owner,
                source.source_url
            FROM entities source
            JOIN entities target
                ON target.normalized_name=source.normalized_name
               AND target.id != source.id
            WHERE length(source.normalized_name) >= 6
              AND (
                (source.entity_type='blueprint_node'
                    AND target.entity_type='cpp_symbol')
                OR
                (source.entity_type='editor_node'
                    AND target.entity_type IN (
                        'blueprint_node', 'cpp_symbol', 'python_api'
                    ))
              )
            ORDER BY source.id, target.id
            """
        )
    )
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in candidate_rows:
        grouped.setdefault(row["from_id"], []).append(row)
    for rows in grouped.values():
        if len(rows) > 8:
            continue
        for row in rows:
            relation_type = (
                "blueprint_cpp_candidate"
                if row["from_type"] == "blueprint_node"
                else "node_api_candidate"
            )
            owner_matches = (
                row["source_owner"]
                and row["target_owner"]
                and normalize_name(row["source_owner"])
                == normalize_name(row["target_owner"])
            )
            confidence = 0.9 if owner_matches else 0.82
            connection.execute(
                """
                INSERT OR IGNORE INTO relations(
                    from_entity_id, to_entity_id, relation_type,
                    evidence_kind, confidence, source_url, note,
                    created_at, updated_at
                ) VALUES(?, ?, ?, 'exact_normalized_name', ?, ?, ?, ?, ?)
                """,
                (
                    row["from_id"],
                    row["to_id"],
                    relation_type,
                    confidence,
                    row["source_url"],
                    (
                        "名称与所有者类型均一致"
                        if owner_matches
                        else "显示名称标准化后完全一致；需要 AI 核对签名"
                    ),
                    now,
                    now,
                ),
            )

    display_name_rows = list(
        connection.execute(
            """
            SELECT DISTINCT
                blueprint.id AS from_id,
                cpp.id AS to_id,
                blueprint.source_url AS blueprint_url,
                cpp.source_url AS evidence_url,
                source_alias.alias AS blueprint_name,
                target_alias.alias AS metadata_display_name
            FROM entities blueprint
            JOIN entity_aliases source_alias
                ON source_alias.entity_id=blueprint.id
               AND source_alias.alias_type='display_name'
            JOIN entity_aliases target_alias
                ON target_alias.normalized_alias=source_alias.normalized_alias
               AND target_alias.alias_type='unreal_display_name'
            JOIN entities cpp
                ON cpp.id=target_alias.entity_id
               AND cpp.entity_type='cpp_symbol'
            WHERE blueprint.entity_type='blueprint_node'
              AND length(source_alias.normalized_alias) >= 6
            ORDER BY blueprint.id, cpp.id
            """
        )
    )
    for row in display_name_rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO relations(
                from_entity_id, to_entity_id, relation_type,
                evidence_kind, confidence, source_url, note,
                created_at, updated_at
            ) VALUES(
                ?, ?, 'blueprint_cpp_api',
                'unreal_display_name_metadata', 1.0, ?, ?, ?, ?
            )
            """,
            (
                row["from_id"],
                row["to_id"],
                row["evidence_url"],
                (
                    f'C++ 文档的 Unreal 元数据声明 DisplayName="'
                    f'{row["metadata_display_name"]}"；与蓝图节点显示名完全一致'
                ),
                now,
                now,
            ),
        )

    target_rows = list(
        connection.execute(
            """
            SELECT DISTINCT
                e.id AS from_id,
                e.source_url,
                c.content_text
            FROM entities e
            JOIN chunks c ON c.page_id=e.page_id
            WHERE e.entity_type='blueprint_node'
              AND c.content_text LIKE '%Target is %'
            """
        )
    )
    for row in target_rows:
        match = re.search(
            r"\bTarget is ([A-Za-z][A-Za-z0-9_ ]{2,80}?)(?:[.!]|\s{2,}|$)",
            row["content_text"],
        )
        if not match:
            continue
        target_name = match.group(1).strip()
        normalized_target = normalize_name(target_name)
        targets = list(
            connection.execute(
                """
                SELECT DISTINCT e.id FROM entity_aliases a
                JOIN entities e ON e.id=a.entity_id
                WHERE a.normalized_alias=?
                  AND e.entity_type='cpp_symbol'
                LIMIT 8
                """,
                (normalized_target,),
            )
        )
        for target in targets:
            connection.execute(
                """
                INSERT OR IGNORE INTO relations(
                    from_entity_id, to_entity_id, relation_type,
                    evidence_kind, confidence, source_url, note,
                    created_at, updated_at
                ) VALUES(?, ?, 'targets_type', 'document_statement',
                          0.92, ?, ?, ?, ?)
                """,
                (
                    row["from_id"],
                    target["id"],
                    row["source_url"],
                    f"文档正文声明 Target is {target_name}",
                    now,
                    now,
                ),
            )
    connection.commit()
    return {
        "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "aliases": connection.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0],
        "page_links": connection.execute("SELECT COUNT(*) FROM page_links").fetchone()[0],
        "relations": connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
    }
