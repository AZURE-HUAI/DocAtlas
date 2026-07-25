"""交叉关系的构建。

通用的部分留在这里：文档里的官方链接指向另一篇文档，就是一条关系——
任何文档站都成立。领域特有的推断（蓝图节点对应哪个 C++ 函数之类）
交给领域知识包，没挂知识包就只有官方链接关系。
"""

from __future__ import annotations

import sqlite3

from .config import KNOWLEDGE
from .dataset import knowledge_hook
from .util import utc_now


# 官方链接的 link_kind → 关系类型。
RELATION_TYPE_BY_LINK_KIND = {
    "hierarchy": "belongs_to",
    "parameter_type": "parameter_type",
    "return_type": "return_type",
    "signature_reference": "signature_reference",
    "example_reference": "example_reference",
    "official_reference": "official_reference",
}


def build_cross_index(connection: sqlite3.Connection) -> dict[str, int]:
    now = utc_now()
    # 只清推导出来的关系；官方链接关系每轮都会重新 INSERT OR IGNORE。
    derived_kinds = tuple(
        knowledge_hook(KNOWLEDGE, "DERIVED_EVIDENCE_KINDS", ())
    )
    if derived_kinds:
        placeholders = ",".join("?" for _ in derived_kinds)
        connection.execute(
            f"DELETE FROM relations WHERE evidence_kind IN ({placeholders})",
            derived_kinds,
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
        relation_type = RELATION_TYPE_BY_LINK_KIND.get(
            row["link_kind"], "official_reference"
        )
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

    build_relations = knowledge_hook(KNOWLEDGE, "build_relations")
    if build_relations:
        build_relations(connection, now)

    connection.commit()
    return {
        "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "aliases": connection.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0],
        "page_links": connection.execute("SELECT COUNT(*) FROM page_links").fetchone()[0],
        "relations": connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
    }
