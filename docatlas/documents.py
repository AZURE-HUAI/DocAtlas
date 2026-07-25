"""单页文档的抓取与结构化转换。

流程是固定的，跟站点无关：
    要内容 → 解析成标题/正文 → 切小节 → 切知识块 → 认实体 → 挖链接

其中"怎么要、怎么解析"交给来源适配器，"这个符号还能叫什么名字"交给
领域知识包。这个文件本身不认识任何具体网站或引擎。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from typing import Any
import urllib.error
import urllib.parse

from .config import DATASET, ENTITY_TYPES, KNOWLEDGE, MARKDOWN_TARGET_RE, SOURCE, VERSION
from .dataset import knowledge_hook
from .net import fetch_bytes
from .htmlmd import plain_text
from .chunking import chunk_sections, normalize_name, split_sections
from .text import humanize_cpp_identifier  # noqa: F401  （测试与外部调用方在用）


# 小节的知识类型 → 它里面的链接算哪种关系。
LINK_KIND_BY_KNOWLEDGE_TYPE = {
    "navigation": "hierarchy",
    "parameters": "parameter_type",
    "returns": "return_type",
    "signature": "signature_reference",
    "examples": "example_reference",
    "references": "official_reference",
}


def document_api_url(path: str) -> str:
    return SOURCE.document_request_url(DATASET, path)


def normalize_target_path(target_url: str) -> str | None:
    return SOURCE.normalize_link_target(DATASET, target_url)


def extract_page_links(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for section in sections:
        for match in MARKDOWN_TARGET_RE.finditer(section["body_md"]):
            anchor_text = plain_text(match.group(1))
            target_url = html.unescape(match.group(2))
            key = (section["position"], target_url, anchor_text)
            if key in seen:
                continue
            seen.add(key)
            target_path = normalize_target_path(target_url)
            links.append(
                {
                    "section_position": section["position"],
                    "target_url": target_url,
                    "target_path": target_path,
                    "anchor_text": anchor_text,
                    "link_kind": LINK_KIND_BY_KNOWLEDGE_TYPE.get(
                        section["knowledge_type"], "reference"
                    ),
                    "evidence_kind": (
                        "official_link" if target_path else "external_link"
                    ),
                    "source_url": section["source_anchor"],
                }
            )
    return links


def entity_descriptor(
    *,
    title: str,
    path: str,
    category: str,
    source_url: str,
    source_type: str | None,
    document_type: str | None,
) -> dict[str, Any]:
    """一页文档 = 一个知识实体。这里给它起名字、找别名、认归属。

    通用别名（标题、路径末段、限定名）在这里生成；
    领域特有的别名（Unreal 的 K2_ 脱前缀之类）由知识包补。
    """
    prefix = DATASET.option("doc_prefix", "/")
    relative = (
        path[len(prefix):]
        if path.lower().startswith(prefix.lower())
        else path.strip("/")
    )
    segments = [
        urllib.parse.unquote(segment) for segment in relative.split("/") if segment
    ]
    entity_type = ENTITY_TYPES.get(category, "document")
    module, owner_type = SOURCE.entity_placement(DATASET, category, segments)
    slug = segments[-1] if segments else title
    qualified_name = "::".join(segments[1:]) if len(segments) > 1 else title
    aliases = {
        (title, "display_name"),
        (slug, "route_slug"),
        (qualified_name, "qualified_name"),
    }
    extra_aliases = knowledge_hook(KNOWLEDGE, "extra_entity_aliases")
    if extra_aliases:
        aliases |= extra_aliases(title=title, category=category, segments=segments)
    compact_title = re.sub(r"[^A-Za-z0-9_]+", "", title)
    if compact_title:
        aliases.add((compact_title, "compact_name"))
    attributes = {
        "category": category,
        "path": path,
        "source_type": source_type,
        "document_type": document_type,
    }
    return {
        "entity_type": entity_type,
        "canonical_name": title,
        "normalized_name": normalize_name(title),
        "qualified_name": qualified_name,
        "module": module,
        "owner_type": owner_type,
        "signature": None,
        "source_url": source_url,
        "version": VERSION,
        "attributes_json": json.dumps(attributes, ensure_ascii=False),
        "aliases": sorted(aliases),
    }


def transform_document(row: sqlite3.Row, body: bytes) -> dict[str, Any]:
    page_id = row["id"]
    path = row["path"]
    source_url = row["url"]
    category = row["category"]

    parsed = SOURCE.parse_document(DATASET, path, body)
    if parsed["kind"] == "redirect":
        return {
            "ok": True,
            "id": page_id,
            "status": "redirect",
            "redirect_url": parsed["redirect_url"],
            "raw": body,
        }

    title = parsed["title"]
    description = parsed["description"]
    markdown = parsed["markdown"]
    document_type = parsed["document_type"]
    source_type = parsed["source_type"]
    version_supported = parsed["version_supported"]

    sections = split_sections(
        title=title,
        description=description,
        markdown=markdown,
        source_url=source_url,
        category=category,
    )
    if not version_supported:
        # 文档明说不支持当前版本，内容还留着，但可信度打对折。
        for section in sections:
            section["quality_score"] *= 0.5
    chunks = chunk_sections(
        sections,
        page_title=title,
        category=category,
        document_type=document_type,
    )
    entity = entity_descriptor(
        title=title,
        path=path,
        category=category,
        source_url=source_url,
        source_type=source_type,
        document_type=document_type,
    )
    document_aliases = knowledge_hook(KNOWLEDGE, "document_aliases")
    if document_aliases:
        extra = document_aliases(
            category=category,
            title=title,
            description=description,
            markdown=markdown,
            sections=sections,
            plain_text=plain_text,
        )
        if extra:
            entity["aliases"] = sorted(set(entity["aliases"]) | extra)
    return {
        "ok": True,
        "id": page_id,
        "status": "success",
        "title": title,
        "description": description,
        "source_type": source_type,
        "document_type": document_type,
        "version_supported": version_supported,
        "updated_at": parsed["updated_at"],
        "content_hash": hashlib.sha256(body).hexdigest(),
        "raw": body,
        "sections": sections,
        "chunks": chunks,
        "page_links": extract_page_links(sections),
        "entity": entity,
        "assets": sorted(parsed["assets"]),
        "block_types": sorted(parsed["block_types"]),
    }


def fetch_document(row: sqlite3.Row, delay: float) -> dict[str, Any]:
    page_id = row["id"]
    path = row["path"]
    try:
        body, _, _ = fetch_bytes(
            document_api_url(path), timeout=120, retries=6, delay=delay
        )
        return transform_document(row, body)
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "id": page_id,
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:  # worker boundary
        return {
            "ok": False,
            "id": page_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
