"""数据合同验收。"""

from __future__ import annotations

import collections
import json
import sqlite3
from typing import Any
import zlib

from . import relations
from .constants import CHUNKER_VERSION
from .runtime import active
from .util import utc_now

# 抽查多少篇原文来核对语言。全量解压太慢，而"要错就整批错"，抽样足够。
LOCALE_SAMPLE_SIZE = 300


def fetched_locales(connection: sqlite3.Connection) -> collections.Counter:
    """服务器实际回了哪些语言版本，按篇数统计。

    数据集里的 `language` 是指令不是事实：站点没有你要的语言时，多半不报错，
    只不声不响回默认语言。不对一遍，就会得到一个标着德语的英文库。
    """
    read_locale = getattr(active().source, "document_locale", None)
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

    官方链接是通用的，任何文档站都有；成员表只有认得它的来源适配器才会产出；
    其余由领域知识包声明自己会推出哪几类。
    """
    from .members import supported as members_supported

    kinds = ["official_link"]
    if members_supported():
        kinds.append("page_member_table")
    kinds.extend(active().hook("DERIVED_EVIDENCE_KINDS", ()))
    return kinds


def link_coverage_observation(connection: sqlite3.Connection) -> dict[str, Any]:
    """来源清单有没有漏掉别的页面正在链接的目录。

    刻意**不算作合同违反**：官方站点地图列不列某个目录是站点自己的事，
    把它判成失败会让一个本来健康的库无缘无故变红。但它必须被说出来——
    "别的页面链过去、清单里却没有"是来源范围划漏的唯一早期信号，
    不报出来就只能等到用户查不到东西时再从头排查。
    """
    gaps = relations.link_target_gaps(connection)
    areas = "；".join(
        f"{item['area']}（{item['links']} 条链接）"
        for item in gaps["top_uncovered_areas"]
    )
    return {
        "name": "inventory_link_coverage",
        "pending_targets": gaps["pending_targets"],
        "missing_targets": gaps["missing_targets"],
        "uncovered_areas": gaps["uncovered_areas"],
        "detail": (
            f"{gaps['pending_targets']:,} 条链接指向清单里已有、但正文尚未抓取的页面"
            "（用 get 或 ask 就能补上）。"
            + (
                f"另有 {gaps['missing_targets']:,} 条指向清单里根本没有的页面，"
                f"集中在 {gaps['uncovered_areas']} 个完全没被枚举的目录：{areas}。"
                "这类要改来源适配器的枚举范围，抓多少次都不会有。"
                if gaps["uncovered_areas"]
                else "没有发现整目录缺失的情况。"
            )
        ),
    }


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

    feeds_total, feeds_ok = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(status='success'), 0) FROM sitemaps"
    ).fetchone()
    add(
        "inventory_feeds_complete",
        feeds_total - feeds_ok,
        f"全部清单入口必须成功读取（共 {feeds_total}，成功 {feeds_ok}）",
    )
    # 只统计"不合格的行"是不够的：一个刚建好的空库一行都没有，于是一行都不
    # 不合格，全部通过、退出码 0。空 ≠ 合格，得单独确认它确实有东西。
    page_total = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    add(
        "inventory_not_empty",
        0 if feeds_ok and page_total else 1,
        "数据集至少要有一个成功的清单入口和一个页面；"
        f"当前成功入口 {feeds_ok}、页面 {page_total:,}。"
        "为 0 说明还没跑过 `crawl --discovery-only`，或者跑到一半失败了",
    )
    counts = {
        row["category"]: row["count"]
        for row in connection.execute(
            "SELECT category, COUNT(*) AS count FROM pages GROUP BY category"
        )
    }
    # 配置声明了一个分类，却一页都没枚举到，几乎总是分类规则写错了——
    # 这种错不会报异常，只会让整整一类文档静静地缺席。
    required = [
        key
        for key in active().dataset.categories
        if key not in active().dataset.optional_categories
    ]
    empty = [key for key in required if not counts.get(key)]
    add(
        "declared_categories_have_pages",
        len(empty),
        "配置声明的每个分类都要枚举到页面："
        + (
            "、".join(f"{key} 为 0" for key in empty)
            + "（确实可能为空的分类请写进 optional_categories）"
            if empty
            else "、".join(f"{key}×{counts.get(key, 0):,}" for key in required)
            or "（没有声明任何分类）"
        ),
    )
    add(
        "page_inventory_metadata",
        connection.execute(
            """
            SELECT COUNT(*) FROM pages
            WHERE url IS NULL OR path IS NULL OR category IS NULL
               OR doc_version IS NULL OR locale IS NULL
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
        language = active().language
        wrong = sum(n for code, n in locales.items() if code != language.lower())
        add(
            "fetched_language_matches_declaration",
            wrong,
            f"抓回来的正文应当都是声明的 {language}"
            + (
                "，实际抽到：" + "、".join(f"{c}×{n}" for c, n in locales.most_common())
                if wrong
                else ""
            ),
        )
    failed = sum(1 for check in checks if check["status"] == "fail")
    report = {
        "phase": phase,
        "status": "pass" if failed == 0 else "fail",
        "checked_at": utc_now(),
        "checks": checks,
    }
    if phase == "content":
        # 观察项不参与 pass/fail：它们是"值得知道"，不是"违反了合同"。
        report["observations"] = [link_coverage_observation(connection)]
    return report
