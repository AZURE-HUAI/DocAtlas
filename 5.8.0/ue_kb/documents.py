"""单页文档的抓取与结构化转换。"""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .config import DOCUMENT_API_URL, DOC_PREFIX, ENTITY_TYPES, LANGUAGE, MARKDOWN_TARGET_RE, VERSION
from .net import fetch_bytes
from .htmlmd import plain_text, render_blocks
from .chunking import chunk_section, humanize_cpp_identifier, normalize_name, split_sections


def document_api_url(path: str) -> str:
    query = urllib.parse.urlencode(
        {
            "path": path,
            "lang": LANGUAGE,
            "application_version": VERSION,
        }
    )
    return f"{DOCUMENT_API_URL}?{query}"


def version_is_supported(document: dict[str, Any]) -> bool:
    applications = document.get("applications") or []
    if isinstance(applications, dict):
        applications = [applications]
    versions = {
        str(application.get("version"))
        for application in applications
        if isinstance(application, dict) and application.get("version") is not None
    }
    return not versions or VERSION in versions


def normalize_target_path(target_url: str) -> str | None:
    parsed = urllib.parse.urlsplit(html.unescape(target_url))
    if parsed.netloc and parsed.netloc.lower() != "dev.epicgames.com":
        return None
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    locale_match = re.match(r"^/documentation/[a-z]{2}-[a-z]{2}/", path, re.I)
    if locale_match:
        path = "/documentation/" + path[locale_match.end() :]
    if not path.lower().startswith(DOC_PREFIX.lower()):
        return None
    return path


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
                    "link_kind": {
                        "navigation": "hierarchy",
                        "parameters": "parameter_type",
                        "returns": "return_type",
                        "signature": "signature_reference",
                        "examples": "example_reference",
                        "references": "official_reference",
                    }.get(section["knowledge_type"], "reference"),
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
    relative = path[len(DOC_PREFIX) :] if path.lower().startswith(DOC_PREFIX.lower()) else path.strip("/")
    segments = [urllib.parse.unquote(segment) for segment in relative.split("/") if segment]
    entity_type = ENTITY_TYPES.get(category, "document")
    module = None
    owner_type = None
    if category == "cpp_api" and segments and segments[0].casefold() == "api":
        if len(segments) >= 3:
            module = segments[2]
        if len(segments) >= 2:
            owner_type = segments[-2]
    elif category in {"blueprint_api", "node_reference"} and len(segments) >= 2:
        owner_type = segments[-2]
    slug = segments[-1] if segments else title
    qualified_name = "::".join(segments[1:]) if len(segments) > 1 else title
    aliases = {
        (title, "display_name"),
        (slug, "route_slug"),
        (qualified_name, "qualified_name"),
    }
    if category == "cpp_api":
        symbol_name = title.split("::")[-1]
        aliases.add((symbol_name, "cpp_symbol_name"))
        aliases.add((humanize_cpp_identifier(symbol_name), "cpp_humanized_name"))
        if symbol_name.startswith("K2_") and len(symbol_name) > 3:
            k2_base_name = symbol_name[3:]
            aliases.add((k2_base_name, "k2_base_name"))
            aliases.add(
                (humanize_cpp_identifier(k2_base_name), "k2_humanized_name")
            )
        if "::" not in title and re.match(r"^[UAFIET][A-Z]", symbol_name):
            unreal_base_name = symbol_name[1:]
            aliases.add((unreal_base_name, "unreal_prefix_stripped"))
            aliases.add(
                (
                    humanize_cpp_identifier(unreal_base_name),
                    "unreal_prefix_stripped_humanized",
                )
            )
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
    document = json.loads(body.decode("utf-8"))
    if document.get("redirect_url"):
        return {
            "ok": True,
            "id": page_id,
            "status": "redirect",
            "redirect_url": document["redirect_url"],
            "raw": body,
        }
    markdown, assets, block_types = render_blocks(document.get("blocks") or [])
    title = str(
        document.get("title")
        or document.get("seo_title")
        or Path(path).name
        or "Untitled"
    ).strip()
    description = str(
        document.get("description")
        or document.get("seo_description")
        or ""
    ).strip()
    sections = split_sections(
        title=title,
        description=description,
        markdown=markdown,
        source_url=source_url,
        category=row["category"],
    )
    version_supported = int(version_is_supported(document))
    if not version_supported:
        for section in sections:
            section["quality_score"] *= 0.5
    document_type = document.get("document_type")
    chunks = [
        chunk
        for section in sections
        for chunk in chunk_section(
            section,
            page_title=title,
            category=row["category"],
            document_type=document_type,
        )
    ]
    source_type = document.get("source")
    entity = entity_descriptor(
        title=title,
        path=path,
        category=row["category"],
        source_url=source_url,
        source_type=source_type,
        document_type=document_type,
    )
    if row["category"] == "cpp_api":
        is_member_page = "::" in title
        metadata_text = (
            "\n".join([description, markdown])
            if is_member_page
            else "\n".join(
                plain_text(section["body_md"])
                for section in sections
                if section["knowledge_type"] == "signature"
            )
        )
        metadata_aliases = set(entity["aliases"])
        metadata_fields = [("ScriptName", "unreal_script_name")]
        if is_member_page:
            metadata_fields.append(("DisplayName", "unreal_display_name"))
        for metadata_name, alias_type in metadata_fields:
            for match in re.finditer(
                rf"\b{metadata_name}\s*=\s*[\"“]([^\"”]+)[\"”]",
                metadata_text,
            ):
                alias = match.group(1).strip()
                if alias:
                    metadata_aliases.add((alias, alias_type))
        entity["aliases"] = sorted(metadata_aliases)
    return {
        "ok": True,
        "id": page_id,
        "status": "success",
        "title": title,
        "description": description,
        "source_type": source_type,
        "document_type": document_type,
        "version_supported": version_supported,
        "updated_at": document.get("updated_at"),
        "content_hash": hashlib.sha256(body).hexdigest(),
        "raw": body,
        "sections": sections,
        "chunks": chunks,
        "page_links": extract_page_links(sections),
        "entity": entity,
        "assets": sorted(assets),
        "block_types": sorted(block_types),
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
