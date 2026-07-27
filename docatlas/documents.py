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

from .constants import MARKDOWN_TARGET_RE
from .members import collect as collect_members
from .runtime import active
from .net import fetch_bytes
from .htmlmd import plain_text
from .chunking import chunk_sections, normalize_name, split_sections
from .text import qualifier_suffixes
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
    workspace = active()
    return workspace.source.document_request_url(workspace.dataset, path)


def normalize_target_path(target_url: str) -> str | None:
    workspace = active()
    return workspace.source.normalize_link_target(workspace.dataset, target_url)


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
    workspace = active()
    prefix = workspace.doc_prefix
    relative = (
        path[len(prefix):]
        if path.lower().startswith(prefix.lower())
        else path.strip("/")
    )
    segments = [
        urllib.parse.unquote(segment) for segment in relative.split("/") if segment
    ]
    entity_type = workspace.dataset.entity_types.get(category, "document")
    module, owner_type = workspace.source.entity_placement(
        workspace.dataset, category, segments
    )
    slug = segments[-1] if segments else title
    qualified_name = "::".join(segments[1:]) if len(segments) > 1 else title
    aliases = {
        (title, "display_name"),
        (slug, "route_slug"),
        (qualified_name, "qualified_name"),
    }
    # 用 extension 而不是 hook：一个站点怎么给页面起标题，和"怎么解析这个站的
    # 页面"是同一类知识，属于来源适配器；知识包仍然优先。
    extra_aliases = workspace.extension("extra_entity_aliases")
    if extra_aliases:
        aliases |= extra_aliases(title=title, category=category, segments=segments)
    compact_title = re.sub(r"[^A-Za-z0-9_]+", "", title)
    if compact_title:
        aliases.add((compact_title, "compact_name"))
    # 限定名的后缀指着同一个东西，也得登记，否则两边永远碰不上头：用户写
    # `std::views::transform`（标准里 `std::views` 就是 `std::ranges::views`），
    # 官方页面叫 `std::ranges::views::transform`，两个归一化后并不相等，只能
    # 退到末段 `transform`——而这个库里叫 transform 的有八个（BUG-017）。
    aliases |= {
        (suffix, "qualifier_suffix")
        for name, _kind in list(aliases)
        for suffix in qualifier_suffixes(name)
    }
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
        "version": workspace.version,
        "attributes_json": json.dumps(attributes, ensure_ascii=False),
        "aliases": sorted(aliases),
    }


def transform_document(row: sqlite3.Row, body: bytes) -> dict[str, Any]:
    page_id = row["id"]
    path = row["path"]
    source_url = row["url"]
    category = row["category"]

    workspace = active()
    parsed = workspace.source.parse_document(workspace.dataset, path, body)
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
    document_aliases = active().hook("document_aliases")
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
    # 类型页的成员表里还藏着一批实体（属性、方法），它们大多没有自己的页面。
    # 认表格是站点知识，所以由适配器出；这里只是把它们一起交出去。
    members = collect_members(
        category=category,
        title=title,
        path=path,
        source_url=source_url,
        sections=sections,
        module=entity["module"],
    )
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
        "members": members,
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
