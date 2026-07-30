"""Fetching and structuring a single document page.

The pipeline is fixed and site-independent:
    request content -> parse into title/body -> split sections -> split chunks
    -> identify entities -> extract links

"How to request, how to parse" is delegated to the source adapter, and "what else
this symbol can be called" to the knowledge pack. This file knows no particular
site or engine.
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
from .text import humanize_cpp_identifier  # noqa: F401  (used by tests/callers)


# Section knowledge type -> which kind of relation its links count as.
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
    """One document page = one knowledge entity: name it, alias it, place it.

    Generic aliases (title, last path segment, qualified name) are produced here;
    domain-specific ones, such as stripping a domain's naming prefix, come from
    the knowledge pack.
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
    # An extension rather than a hook: how a site titles its pages is the same
    # kind of knowledge as how to parse that site, so it belongs to the source
    # adapter. The knowledge pack still takes precedence.
    extra_aliases = workspace.extension("extra_entity_aliases")
    if extra_aliases:
        aliases |= extra_aliases(title=title, category=category, segments=segments)
    compact_title = re.sub(r"[^A-Za-z0-9_]+", "", title)
    if compact_title:
        aliases.add((compact_title, "compact_name"))
    # Suffixes of a qualified name point at the same thing and must be registered
    # too, or the two sides never meet: a user writes `std::views::transform`
    # (in the standard, `std::views` *is* `std::ranges::views`) while the official
    # page is `std::ranges::views::transform`. Those are unequal once normalized,
    # leaving only the bare tail `transform` — and eight things here are called
    # transform.
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
        # The page states it does not support the current version. Keep the
        # content, but halve its confidence.
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
    # A type page's member table hides further entities (properties, methods),
    # most without a page of their own. Recognizing the table is site knowledge
    # and comes from the adapter; here they are just passed along.
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
