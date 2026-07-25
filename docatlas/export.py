"""Markdown 分片导出。"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any

from .config import CATEGORY_LABELS, CATEGORY_PATTERNS, DATA_DIR, EXPORT_DIR, URL_RE, VERSION
from .util import log


def replace_remote_assets(
    connection: sqlite3.Connection, markdown: str, export_file: Path
) -> str:
    urls = set(URL_RE.findall(markdown))
    if not urls:
        return markdown
    mapping: dict[str, str] = {}
    for url in urls:
        row = connection.execute(
            "SELECT local_path FROM assets WHERE url=? AND status='success'",
            (url,),
        ).fetchone()
        if row:
            absolute_asset = DATA_DIR / row["local_path"]
            mapping[url] = os.path.relpath(
                absolute_asset, export_file.parent
            ).replace("\\", "/")
    for remote, local in mapping.items():
        markdown = markdown.replace(remote, local)
    return markdown


def export_markdown(
    connection: sqlite3.Connection, *, shard_mb: int
) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    max_bytes = shard_mb * 1024 * 1024
    for category in CATEGORY_PATTERNS:
        category_dir = EXPORT_DIR / category
        category_dir.mkdir(parents=True, exist_ok=True)
        rows = connection.execute(
            """
            SELECT p.id, p.title, p.description, p.url, p.updated_at,
                   c.chunk_index AS position, c.content_md,
                   c.context_prefix, c.knowledge_type, c.id AS knowledge_id
            FROM pages p
            JOIN chunks c ON c.page_id=p.id
            WHERE p.status='success' AND p.category=?
            ORDER BY p.id, c.section_id, c.chunk_index
            """,
            (category,),
        )
        shard_number = 1
        output_file: Any = None
        output_path: Path | None = None
        current_size = 0
        current_page_id: int | None = None
        try:
            for row in rows:
                if output_file is None or current_size >= max_bytes:
                    if output_file:
                        output_file.close()
                    output_path = category_dir / f"part-{shard_number:04d}.md"
                    output_file = output_path.open("w", encoding="utf-8", newline="\n")
                    output_file.write(
                        f"# UE {VERSION} {CATEGORY_LABELS[category]} — "
                        f"分片 {shard_number}\n\n"
                    )
                    current_size = output_file.tell()
                    shard_number += 1
                    current_page_id = None
                pieces: list[str] = []
                if row["id"] != current_page_id:
                    pieces.append(
                        "\n\n---\n\n"
                        f"# {row['title']}\n\n"
                        f"- UE 版本：{VERSION}\n"
                        f"- 分类：{CATEGORY_LABELS[category]}\n"
                        f"- 更新时间：{row['updated_at'] or '未知'}\n"
                        f"- DOC 原出处：[{row['url']}]({row['url']})\n"
                    )
                    if row["description"]:
                        pieces.append(f"\n{row['description']}\n")
                    current_page_id = row["id"]
                pieces.append(
                    "\n\n"
                    f"> 知识 ID：K{row['knowledge_id']}  \n"
                    f"> 知识类型：{row['knowledge_type']}  \n"
                    f"> 检索上下文：{row['context_prefix']}\n\n"
                    + row["content_md"]
                    + "\n"
                )
                text = "".join(pieces)
                text = replace_remote_assets(connection, text, output_path)
                output_file.write(text)
                current_size += len(text.encode("utf-8"))
        finally:
            if output_file:
                output_file.close()
        log(f"已导出 {CATEGORY_LABELS[category]}：{max(0, shard_number - 1)} 个分片")
