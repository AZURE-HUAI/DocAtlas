"""数据合同验收。"""

from __future__ import annotations

import collections
import json
import sqlite3
from typing import Any
import zlib

from .config import CHUNKER_VERSION, KNOWLEDGE, LANGUAGE, SOURCE
from .dataset import knowledge_hook
from .util import utc_now

# 抽查多少篇原文来核对语言。全量解压太慢，而"要错就整批错"，抽样足够。
LOCALE_SAMPLE_SIZE = 300


def fetched_locales(connection: sqlite3.Connection) -> collections.Counter:
    """服务器实际回了哪些语言版本，按篇数统计。

    数据集里的 `language` 是指令不是事实：站点没有你要的语言时，多半不报错，
    只不声不响回默认语言。不对一遍，就会得到一个标着德语的英文库。
    """
    read_locale = getattr(SOURCE, "document_locale", None)
    if read_locale is None:
        return collections.Counter()
    seen: collections.Counter = collections.Counter()
    for (blob,) in connection.execute(
        "SELECT raw_json FROM raw_documents WHERE raw_json IS NOT NULL"
        " ORDER BY RANDOM() LIMIT ?",
        (LOCALE_SAMPLE_SIZE,),
    ):
        try:
            payload = json.loads(zlib.decompress(blob))
        except (zlib.error, ValueError):
            continue  # 坏掉的存档由别的检查去管，这里只看语言
        locale = read_locale(payload)
        if locale:
            seen[locale.lower()] += 1
    return seen


def expected_evidence_kinds() -> list[str]:
    """这个数据集本应产出哪几类关系证据。

    官方链接是通用的，任何文档站都有；其余由领域知识包声明自己会推出哪几类。
    """
    kinds = ["official_link"]
    kinds.extend(knowledge_hook(KNOWLEDGE, "DERIVED_EVIDENCE_KINDS", ()))
    return kinds


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
        "全部子站点地图必须成功",
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
        add(
            "chunk_parser_version",
            connection.execute(
                "SELECT COUNT(*) FROM pages WHERE status='success'"
                " AND COALESCE(parser_version,'')!=?",
                (CHUNKER_VERSION,),
            ).fetchone()[0],
            f"每个成功页面都应已按当前切分规则（{CHUNKER_VERSION}）加工",
        )
        add(
            "chunk_neighbour_pointers",
            connection.execute(
                """
                SELECT COUNT(*) FROM chunks c
                WHERE c.next_chunk_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM chunks n
                      WHERE n.id=c.next_chunk_id AND n.page_id=c.page_id
                  )
                """
            ).fetchone()[0],
            "相邻块指针必须指向同一页里真实存在的块",
        )
        # 加工规则一改就可能把某类证据整类做没——只看"跑完没报错"发现不了，
        # 得盯着每类证据的产出量。曾经就因为丢掉一类小节，让一整类关系归零。
        expected = expected_evidence_kinds()
        missing = [
            kind
            for kind in expected
            if connection.execute(
                "SELECT COUNT(*) FROM relations WHERE evidence_kind=?", (kind,)
            ).fetchone()[0]
            == 0
        ]
        add(
            "relation_evidence_coverage",
            len(missing),
            "本应产出的每类关系证据都要真的有："
            + ("、".join(missing) + " 一条都没有" if missing else "、".join(expected)),
        )
        # 语言是**选的**不是猜的，所以没法自动填；但"选的有没有生效"能自动查。
        locales = fetched_locales(connection)
        wrong = sum(n for code, n in locales.items() if code != LANGUAGE.lower())
        add(
            "fetched_language_matches_declaration",
            wrong,
            f"抓回来的正文应当都是声明的 {LANGUAGE}"
            + (
                "，实际抽到：" + "、".join(f"{c}×{n}" for c, n in locales.most_common())
                if wrong
                else ""
            ),
        )
    failed = sum(1 for check in checks if check["status"] == "fail")
    return {
        "phase": phase,
        "status": "pass" if failed == 0 else "fail",
        "checked_at": utc_now(),
        "checks": checks,
    }
