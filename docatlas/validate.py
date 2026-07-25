"""数据合同验收。"""

from __future__ import annotations

import sqlite3
from typing import Any

from .util import utc_now


def validate_contract(
    connection: sqlite3.Connection, phase: str
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, failures: int, requirement: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "pass" if failures == 0 else "fail",
                "failures": failures,
                "requirement": requirement,
            }
        )

    add(
        "sitemaps_complete",
        connection.execute(
            "SELECT COUNT(*) FROM sitemaps WHERE status!='success'"
        ).fetchone()[0],
        "全部 UE 子站点地图必须成功",
    )
    add(
        "page_inventory_metadata",
        connection.execute(
            """
            SELECT COUNT(*) FROM pages
            WHERE url IS NULL OR path IS NULL OR category IS NULL
               OR ue_version IS NULL OR locale IS NULL
               OR route_depth IS NULL OR sitemap_url IS NULL
            """
        ).fetchone()[0],
        "页面必须具有路径、类型、版本、语言和来源站点地图",
    )
    duplicate_paths = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT path FROM pages GROUP BY path HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    add("unique_page_paths", duplicate_paths, "规范化页面路径必须唯一")
    if phase == "content":
        add(
            "successful_page_raw_revision",
            connection.execute(
                """
                SELECT COUNT(*) FROM pages p
                WHERE p.status='success'
                  AND NOT EXISTS (
                      SELECT 1 FROM raw_documents r WHERE r.page_id=p.id
                  )
                """
            ).fetchone()[0],
            "每个成功页面必须有原始 JSON 修订",
        )
        add(
            "successful_page_entity",
            connection.execute(
                """
                SELECT COUNT(*) FROM pages p
                WHERE p.status='success'
                  AND NOT EXISTS (
                      SELECT 1 FROM entities e WHERE e.page_id=p.id
                  )
                """
            ).fetchone()[0],
            "每个成功页面必须有主实体",
        )
        add(
            "chunk_required_fields",
            connection.execute(
                """
                SELECT COUNT(*) FROM chunks
                WHERE trim(title)='' OR trim(heading_path)=''
                   OR trim(content_text)='' OR trim(source_url)=''
                   OR trim(source_anchor)='' OR trim(knowledge_type)=''
                   OR token_estimate <= 0
                """
            ).fetchone()[0],
            "知识块的标题、层级、正文、类型、来源和 token 必须完整",
        )
        add(
            "chunk_size_limit",
            connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE token_estimate > 900"
            ).fetchone()[0],
            "知识块估算不得超过 900 tokens",
        )
        add(
            "relation_evidence",
            connection.execute(
                """
                SELECT COUNT(*) FROM relations
                WHERE trim(evidence_kind)='' OR trim(source_url)=''
                   OR confidence < 0 OR confidence > 1
                """
            ).fetchone()[0],
            "关系必须有证据、来源和合法置信度",
        )
    failed = sum(1 for check in checks if check["status"] == "fail")
    return {
        "phase": phase,
        "status": "pass" if failed == 0 else "fail",
        "checked_at": utc_now(),
        "checks": checks,
    }
