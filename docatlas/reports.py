"""Statistics, the router page, and the whole-site manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .config import (
    CATEGORY_LABELS,
    CATEGORY_IDS,
    DATA_DIR,
    DATASET,
    DB_PATH,
    LANGUAGE,
    SOURCE,
    VERSION,
)
from .util import utc_now


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
        "product": DATASET.product,
        "version": VERSION,
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
    """Export the machine-readable per-page manifest.

    A full export approaching 100 MB, generated only when actually wanted: hung
    off `write_reports`, it would rewrite 100 MB every time someone glanced at
    progress.
    """
    manifest_path = DATA_DIR / "manifest.jsonl"
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


def _home_url() -> str:
    """The dataset's home URL. With no home_path, omit the line; invent nothing."""
    home_path = DATASET.option("home_path")
    return SOURCE.canonical_url(DATASET, home_path) if home_path else ""


def write_reports(
    connection: sqlite3.Connection, *, manifest: bool = False
) -> dict[str, Any]:
    stats = database_stats(connection)
    report_path = DATA_DIR / "report.json"
    report_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if manifest:
        write_manifest(connection)

    router_lines = [
        f"# {DATASET.name} — local knowledge base router",
        "",
        *(
            [f"- Official entry point: [{home_url}]({home_url})"]
            if (home_url := _home_url())
            else []
        ),
        f"- Document language: {LANGUAGE}",
        f"- Pages total: {stats['pages_total']:,}",
        f"- Pages fetched: {stats['pages'].get('success', 0):,}",
        f"- Logical sections: {stats['sections']:,}",
        f"- Retrieval chunks: {stats['chunks']:,}",
        f"- Cross-referenced entities: {stats['entities']:,}",
        f"- Cross-referenced relations: {stats['relations']:,}",
        f"- Pages failed: {stats['pages'].get('failed', 0):,}",
        f"- Pages redirected: {stats['pages'].get('redirect', 0):,}",
        f"- Feeds failed: {stats['sitemaps_failed']:,}",
        "",
        "## Routing by category",
        "",
        "| Category | Discovered | Fetched | Failed |",
        "|---|---:|---:|---:|",
    ]
    for category in CATEGORY_IDS:
        values = stats["categories"].get(category, {})
        total = sum(values.values())
        router_lines.append(
            f"| {CATEGORY_LABELS[category]} (`{category}`) | {total:,} | "
            f"{values.get('success', 0):,} | {values.get('failed', 0):,} |"
        )
    router_lines.extend(
        [
            "",
            "## How to query",
            "",
            "```powershell",
            ".\\docatlas.ps1                    # interactive search",
            '.\\docatlas.ps1 ask "<what you want to know>"',
            'python -m docatlas search "<keywords>" --limit 20',
            'python -m docatlas related "<name or K id>"',
            "```",
            "",
            "`ask` returns assembled chunks and their sources within a token "
            "budget, and is the default entry point for an AI. The structured "
            "index lives in `knowledge.sqlite3`; generate the per-page manifest "
            "into `manifest.jsonl` with `python -m docatlas stats --manifest` "
            "when needed; the full Markdown sits in `exports/` (large, and not "
            "for an AI to read end to end).",
            "",
            "## Data guarantees",
            "",
            "- Every section stores its own `source_url`.",
            "- Every retrieval chunk repeats `DOC source` at its end.",
            "- Raw responses are appended by content hash, so the structures the "
            "site returned over time stay traceable.",
            "- Relations store an evidence kind and a confidence, so a candidate "
            "mapping is never passed off as an official statement.",
            "- Re-running the crawler fetches only unfinished or failed items by "
            "default and never repeats a successful page.",
            "",
            f"Generated: {stats['generated_at']}",
            "",
        ]
    )
    (DATA_DIR / "ROUTER.md").write_text(
        "\n".join(router_lines), encoding="utf-8"
    )
    return stats


def write_site_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    inventory_path = DATA_DIR / "site_inventory.jsonl"
    digest = hashlib.sha256()
    total = 0
    categories: dict[str, int] = {}
    with inventory_path.open("w", encoding="utf-8", newline="\n") as output:
        for row in connection.execute(
            """
            SELECT id, url, path, category, sitemap_url, doc_version, locale,
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
        "product": DATASET.product,
        "version": VERSION,
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
    (DATA_DIR / "site_inventory_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (DATA_DIR / "site_inventory.sha256").write_text(
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
