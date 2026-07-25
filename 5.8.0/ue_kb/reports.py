"""统计、总路由与全站清单。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .config import CATEGORY_LABELS, CATEGORY_PATTERNS, DB_PATH, LANGUAGE, SCRIPT_DIR, VERSION
from .util import utc_now
from .discover import canonical_source_url


def database_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    page_counts = {
        row["status"]: row["count"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM pages GROUP BY status"
        )
    }
    categories: dict[str, dict[str, int]] = {}
    for row in connection.execute(
        """
        SELECT category, status, COUNT(*) AS count
        FROM pages GROUP BY category, status ORDER BY category, status
        """
    ):
        categories.setdefault(row["category"], {})[row["status"]] = row["count"]
    asset_counts = {
        row["status"]: row["count"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM assets GROUP BY status"
        )
    }
    return {
        "generated_at": utc_now(),
        "ue_version": VERSION,
        "language": LANGUAGE,
        "pages_total": sum(page_counts.values()),
        "pages": page_counts,
        "sections": connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0],
        "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "relations": connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
        "raw_revisions": connection.execute(
            "SELECT COUNT(*) FROM raw_documents"
        ).fetchone()[0],
        "categories": categories,
        "sitemaps_failed": connection.execute(
            "SELECT COUNT(*) FROM sitemaps WHERE status='failed'"
        ).fetchone()[0],
        "version_mismatches": connection.execute(
            "SELECT COUNT(*) FROM pages WHERE status='success' AND version_supported=0"
        ).fetchone()[0],
        "assets": asset_counts,
        "database_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }


def write_manifest(connection: sqlite3.Connection) -> Path:
    """导出逐页机器可读清单。

    这是一个近 100 MB 的全量导出文件，只在真正需要时生成——以前它挂在
    `write_reports` 里，导致每次看一眼进度都要重写 100 MB。
    """
    manifest_path = SCRIPT_DIR / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as manifest:
        for row in connection.execute(
            """
            SELECT id, title, description, url, path, category, source_type,
                   document_type, updated_at, status, section_count,
                   version_supported, error
            FROM pages ORDER BY category, path
            """
        ):
            manifest.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return manifest_path


def write_reports(
    connection: sqlite3.Connection, *, manifest: bool = False
) -> dict[str, Any]:
    stats = database_stats(connection)
    report_path = SCRIPT_DIR / "report.json"
    report_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if manifest:
        write_manifest(connection)

    router_lines = [
        f"# Unreal Engine {VERSION} 本地文档总路由",
        "",
        f"- 官方入口：[{canonical_source_url('/documentation/unreal-engine/unreal-engine-5-8-documentation')}]"
        f"({canonical_source_url('/documentation/unreal-engine/unreal-engine-5-8-documentation')})",
        f"- 文档语言：{LANGUAGE}",
        f"- 页面总数：{stats['pages_total']:,}",
        f"- 成功页面：{stats['pages'].get('success', 0):,}",
        f"- 逻辑小节：{stats['sections']:,}",
        f"- 检索知识块：{stats['chunks']:,}",
        f"- 交叉实体：{stats['entities']:,}",
        f"- 交叉关系：{stats['relations']:,}",
        f"- 失败页面：{stats['pages'].get('failed', 0):,}",
        f"- 重定向页面：{stats['pages'].get('redirect', 0):,}",
        f"- 失败站点地图：{stats['sitemaps_failed']:,}",
        "",
        "## 分类路由",
        "",
        "| 分类 | 已发现 | 成功 | 失败 |",
        "|---|---:|---:|---:|",
    ]
    for category in CATEGORY_PATTERNS:
        values = stats["categories"].get(category, {})
        total = sum(values.values())
        router_lines.append(
            f"| {CATEGORY_LABELS[category]} (`{category}`) | {total:,} | "
            f"{values.get('success', 0):,} | {values.get('failed', 0):,} |"
        )
    router_lines.extend(
        [
            "",
            "## 怎么查",
            "",
            "```powershell",
            ".\\ue.ps1                                  # 交互式搜索",
            '.\\ue.ps1 ask   "Nanite virtualized geometry"',
            '.\\ue.ps1 find  "Gameplay Ability System" -Limit 20',
            '.\\ue.ps1 links "Set Timer by Function Name"',
            "```",
            "",
            "`ask` 会按 token 预算返回整理好的知识块和 Epic DOC 原出处，是 AI 的默认入口。"
            "结构化总索引位于 `ue58_docs.sqlite3`；逐页清单需要时用 "
            "`python ue58_docs.py stats --manifest` 生成到 `manifest.jsonl`；"
            "整本 Markdown 位于 `exports/`（体积大，AI 不要整篇读）。",
            "",
            "## 数据保证",
            "",
            "- 每个知识小节都单独保存 `source_url`。",
            "- 每个检索块末尾都重复写入 `DOC 原出处`。",
            "- 原始 JSON 按内容哈希追加保存，可追溯到 Epic 返回的历史结构。",
            "- 交叉关系保存证据类型与置信度，不把候选映射冒充官方声明。",
            "- 重跑采集器默认只补抓未完成或失败项目，不会重复成功页面。",
            "",
            f"最后生成：{stats['generated_at']}",
            "",
        ]
    )
    (SCRIPT_DIR / "ROUTER.md").write_text(
        "\n".join(router_lines), encoding="utf-8"
    )
    return stats


def write_site_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    inventory_path = SCRIPT_DIR / "site_inventory.jsonl"
    digest = hashlib.sha256()
    total = 0
    categories: dict[str, int] = {}
    with inventory_path.open("w", encoding="utf-8", newline="\n") as output:
        for row in connection.execute(
            """
            SELECT id, url, path, category, sitemap_url, ue_version, locale,
                   route_depth, parent_path, discovered_at, last_seen_at
            FROM pages
            WHERE deleted_at IS NULL
            ORDER BY category, path
            """
        ):
            line = json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
            encoded = (line + "\n").encode("utf-8")
            output.write(line + "\n")
            digest.update(encoded)
            total += 1
            categories[row["category"]] = categories.get(row["category"], 0) + 1
    failed_sitemaps = connection.execute(
        "SELECT COUNT(*) FROM sitemaps WHERE status!='success'"
    ).fetchone()[0]
    inventory_hash = digest.hexdigest()
    status = "complete" if failed_sitemaps == 0 else "incomplete"
    summary = {
        "status": status,
        "generated_at": utc_now(),
        "ue_version": VERSION,
        "language": LANGUAGE,
        "sitemap_count": connection.execute(
            "SELECT COUNT(*) FROM sitemaps"
        ).fetchone()[0],
        "failed_sitemaps": failed_sitemaps,
        "page_count": total,
        "categories": categories,
        "sha256": inventory_hash,
        "inventory_file": inventory_path.name,
    }
    (SCRIPT_DIR / "site_inventory_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (SCRIPT_DIR / "site_inventory.sha256").write_text(
        f"{inventory_hash}  {inventory_path.name}\n",
        encoding="ascii",
    )
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
        [
            ("inventory_status", status),
            ("inventory_hash", inventory_hash),
            ("inventory_page_count", str(total)),
            ("inventory_generated_at", summary["generated_at"]),
        ],
    )
    connection.commit()
    return summary
