"""Template source adapter — copy this file to support a new documentation site.

It describes a small invented site under `example.invalid` — the TLD reserved
for examples — and implements every interface the core looks for. Nothing here is special-cased in the core:
what makes a site work is exactly these functions.

The test suite runs against this adapter, so the template is exercised on every
commit and cannot quietly rot.

To add a real site, copy this file to `docatlas/sources/<yoursite>.py`, write a
`datasets/<yourdataset>.toml` naming it under `source =`, and replace the four
answers below. The core needs no change.

## The four questions an adapter answers

    1. Which pages exist?     sitemap_index_url / categorize_sitemap /
                              normalize_location, or inventory_feeds + read_feed
    2. Where is a body?       document_request_url
    3. How is it parsed?      parse_document
    4. What is the real URL?  canonical_url

Everything else — rate limiting, retries, storage, chunking, retrieval, context
budgeting — is the core's job and no concern of an adapter.

## Two enumeration styles

This template uses **sitemaps**, the common case: one index lists many sitemap
files, each holding page URLs. A site without sitemaps implements
`inventory_feeds` (which lists to read) and `read_feed` (how to read one)
instead, and the core treats both the same. Implement one style or the other,
never both.

## Everything below `is_official_url` is optional

Leave a function out and the core simply does without that capability. Nothing
crashes, and nothing has to be registered anywhere.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from ..htmlmd import html_to_markdown, lead_sentence

# Settings come from `[source_options]` in the dataset toml, never from
# constants here: the same adapter has to serve every version of the site, and a
# new version must be a config change rather than a code change.


def _base_url(dataset) -> str:
    return dataset.option("base_url", "https://example.invalid").rstrip("/")


def _doc_prefix(dataset) -> str:
    """Path prefix every document of this site sits under."""
    return dataset.option("doc_prefix", "/docs/")


# ---------------------------------------------------------------------------
# 1. Which pages exist
# ---------------------------------------------------------------------------


def sitemap_index_url(dataset) -> str:
    """The one sitemap index. Feed-style sources omit this function entirely."""
    return dataset.option("sitemap_index", f"{_base_url(dataset)}/sitemap.xml")


def categorize_sitemap(dataset, url: str) -> str | None:
    """Which category a sitemap file holds; None skips the file.

    Matching on the path fragments declared in `[categories]` keeps the mapping
    in the dataset rather than in code.
    """
    for category, pattern in dataset.categories.items():
        if pattern in url:
            return category
    return None


def categorize_path(dataset, path: str) -> str | None:
    """Category of a path that was never in a sitemap; None when undecidable.

    The core reaches for this when a body links to a page the inventory does not
    hold. Returning None is a perfectly good answer and means "the dataset
    decides" — guessing here silently files pages under the wrong category.
    """
    prefix = _doc_prefix(dataset)
    if not path.casefold().startswith(prefix.casefold()):
        return None
    segments = [segment for segment in path[len(prefix):].split("/") if segment]
    if len(segments) < 2:
        return None  # a flat page at the root carries no category in its URL
    first = segments[0].casefold()
    return first if first in dataset.categories else None


def normalize_location(dataset, location: str) -> tuple[str, str] | None:
    """A URL found in a sitemap -> (canonical path, canonical URL).

    None drops the entry. Two jobs here, and both matter:

    * **Reject what is not ours.** Other hosts, and other languages.
    * **Collapse variants.** One document must end up with exactly one path, or
      it is stored twice and cited inconsistently. This site prefixes
      translations (`/docs/de/...`), so that segment comes off.
    """
    parsed = urllib.parse.urlsplit(html.unescape(location))
    if parsed.netloc and parsed.netloc.casefold() != urllib.parse.urlsplit(
        _base_url(dataset)
    ).netloc.casefold():
        return None
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    path = _strip_locale(dataset, path)
    if not path.casefold().startswith(_doc_prefix(dataset).casefold()):
        return None
    return path, canonical_url(dataset, path)


_LOCALE_SEGMENT_RE = re.compile(r"^([a-z]{2}(?:-[a-z]{2})?)/", re.I)


def _strip_locale(dataset, path: str) -> str:
    prefix = _doc_prefix(dataset)
    if not path.casefold().startswith(prefix.casefold()):
        return path
    rest = path[len(prefix):]
    match = _LOCALE_SEGMENT_RE.match(rest)
    return prefix + rest[match.end():] if match else path


# ---------------------------------------------------------------------------
# 2. How URLs are built
# ---------------------------------------------------------------------------


def canonical_url(dataset, path: str) -> str:
    """The URL people see in a citation. Pin the version here.

    Without a version in the address, a citation drifts to whatever the site
    currently serves, and no longer says what the library actually holds.
    """
    quoted = urllib.parse.quote(path, safe="/:@-._~")
    return f"{_base_url(dataset)}{quoted}?version={dataset.version}"


def document_request_url(dataset, path: str) -> str:
    """The URL actually fetched, which need not be the canonical one.

    This site serves bodies as JSON from an API, so pages are never scraped.
    A site with no API returns `canonical_url(dataset, path)` here and parses
    HTML in `parse_document`.
    """
    query = urllib.parse.urlencode(
        {"path": path, "lang": dataset.language, "version": dataset.version}
    )
    return f"{dataset.option('document_api', _base_url(dataset) + '/api/doc')}?{query}"


def normalize_link_target(dataset, target_url: str) -> str | None:
    """Path for a link found in a body; None when it points off-site.

    This is what turns links into relations, so it has to accept the same
    variant spellings `normalize_location` collapses. Miss one and a link that
    does resolve is recorded as pointing nowhere.
    """
    normalized = normalize_location(dataset, target_url)
    return normalized[0] if normalized else None


def is_official_url(dataset, url: str) -> bool:
    """Whether a URL is official documentation of this site.

    Deliberately separate from "does this belong to this dataset": a dataset may
    collect one section of a site, while the whole site is still official. Fusing
    the two makes in-scope pages look unofficial, which costs them ranking.
    """
    parsed = urllib.parse.urlsplit(url)
    host = urllib.parse.urlsplit(_base_url(dataset)).netloc.casefold()
    return parsed.netloc.casefold() == host and parsed.path.startswith("/docs/")


def asset_base_url(dataset) -> str:
    """Base for resolving relative image paths in a body."""
    return dataset.option("asset_base", f"{_base_url(dataset)}/docs/")


# ---------------------------------------------------------------------------
# 3. How a response is parsed
# ---------------------------------------------------------------------------


def document_locale(payload: dict[str, Any]) -> str | None:
    """Which language the server actually returned. Optional.

    A dataset's `language` is an instruction, not a fact. Asked for a language
    it does not have, a site usually does not fail — it quietly serves its
    default, leaving a library labelled with a language it is not written in and
    an agent told to query in that language. Echoing the real one lets the core
    catch that.
    """
    locale = payload.get("locale")
    return str(locale) if locale else None


def _render_blocks(blocks: list[Any]) -> tuple[str, set[str], set[str]]:
    """Body blocks -> Markdown, plus the assets and block types seen.

    Reporting block types matters: an unrecognised type shows up in the health
    check instead of being silently dropped.
    """
    rendered: list[str] = []
    assets: set[str] = set()
    block_types: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "unknown")
        block_types.add(block_type)
        if isinstance(block.get("html"), str):
            markdown, found = html_to_markdown(block["html"])
            assets.update(found)
        elif isinstance(block.get("code"), str):
            language = str(block.get("language") or "")
            markdown = f"```{language}\n{block['code']}\n```"
        else:
            markdown = str(block.get("text") or "").strip()
        if markdown:
            rendered.append(markdown)
        if isinstance(block.get("image"), str):
            assets.add(block["image"])
    return "\n\n".join(rendered).strip(), assets, block_types


def parse_document(dataset, path: str, body: bytes) -> dict[str, Any]:
    """Raw response -> the site-independent structure the core stores.

    `kind="redirect"` says the page moved; `kind="document"` says there is a
    body. Reporting a redirect rather than following it is what lets the core
    tell "this page was withdrawn" apart from "your query missed".
    """
    document = json.loads(body.decode("utf-8"))
    if document.get("redirect_url"):
        return {"kind": "redirect", "redirect_url": document["redirect_url"]}

    markdown, assets, block_types = _render_blocks(document.get("blocks") or [])
    description = str(document.get("description") or "").strip()
    return {
        "kind": "document",
        "title": str(document.get("title") or Path(path).name or "Untitled").strip(),
        # Where a site states no summary, take the opening sentence of the body.
        # Use the shared helper rather than writing the rule again: what counts
        # as a quotable opening line is core behaviour, not site knowledge.
        "description": description or lead_sentence(markdown),
        "markdown": markdown,
        "assets": assets,
        "block_types": block_types,
        "document_type": document.get("document_type"),
        "source_type": document.get("source"),
        "updated_at": document.get("updated_at"),
        # 0 means "this body does not cover the requested version"; the core then
        # records the page without letting it answer as if it did.
        "version_supported": int(
            not document.get("versions")
            or dataset.version in document.get("versions", [])
        ),
    }


# ---------------------------------------------------------------------------
# 4. Where an entity sits (optional)
# ---------------------------------------------------------------------------


def entity_placement(
    dataset, category: str, segments: list[str]
) -> tuple[str | None, str | None]:
    """From a path, infer the module a symbol lives in and the type owning it.

    Path layout is site knowledge, which is why the core cannot do this. Here
    the layout is `/docs/<category>/<module>/<type>/<member>`.
    """
    module = segments[1] if len(segments) > 1 else None
    owner_type = segments[-2] if len(segments) > 2 else None
    return module, owner_type


# ---------------------------------------------------------------------------
# 5. Members listed on a type page (optional)
# ---------------------------------------------------------------------------
#
# Reference pages often table a type's properties and methods, and most of those
# members have **no page of their own**. Without this, they cannot be searched
# for by name at all. Recognising the tables is site knowledge and belongs here;
# what the columns imply about a product is domain knowledge and belongs in a
# knowledge pack under `docatlas/knowledge/`.

# Section heading -> what kind of member that table lists. Constructors are
# deliberately absent: one shares its type's name, so promoting it would only
# return the same name twice.
_MEMBER_SECTIONS = {
    "Properties": "property",
    "Methods": "method",
}

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def page_members(
    dataset,
    *,
    category: str,
    title: str,
    path: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Members tabled on this page, as [{name, entity_type, attributes}].

    A member whose Name cell is a link has its own page and is skipped: that
    page is already the entity, and promoting it here would duplicate it.
    """
    if category not in dataset.api_categories:
        return []
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in sections:
        entity_type = _MEMBER_SECTIONS.get(str(section.get("title") or "").strip())
        if not entity_type:
            continue
        for line in str(section.get("body_md") or "").splitlines():
            cells = _table_cells(line)
            if not cells:
                continue
            name_cell = cells[0]
            if "](" in name_cell:  # linked: it has a page of its own
                continue
            match = _IDENTIFIER_RE.search(name_cell.replace("`", ""))
            if not match or match.group() in seen:
                continue
            seen.add(match.group())
            members.append(
                {
                    "name": match.group(),
                    "entity_type": entity_type,
                    # Verbatim extras a knowledge pack can judge by. The core
                    # only stores them.
                    "attributes": {"notes": cells[-1]} if len(cells) > 1 else {},
                }
            )
    return members


def _table_cells(line: str) -> list[str] | None:
    """Cells of one Markdown table row; None when the line is not a data row."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if not any(cells) or all(set(cell) <= set("-: ") for cell in cells):
        return None  # the separator row under a header
    return cells


# ---------------------------------------------------------------------------
# 6. Version applicability (optional)
# ---------------------------------------------------------------------------
#
# How a version is written is each site's own typographic convention, so
# recognising it belongs here. The core only compares the sort keys handed back
# and knows no versioning scheme of its own — see `docatlas/versions.py`.

VERSION_VOCABULARY = "product releases (v1.0 / v2.0 / v2.1 ...)"

_MARK_RE = re.compile(r"\(\s*(since|until|removed in)?\s*v(\d+)\.(\d+)\s*\)", re.I)


def version_sort_key(label: str) -> str:
    """`v2.10` -> `00002.00010`, so string comparison orders versions correctly.

    Zero-padding is the point: compared as text, "v2.10" sorts before "v2.9".
    Whatever scheme a site uses, this function has to turn it into keys that
    compare in release order.
    """
    match = re.search(r"v(\d+)\.(\d+)", label or "", re.I)
    if not match:
        return ""
    return f"{int(match.group(1)):05d}.{int(match.group(2)):05d}"


def version_marks(text: str) -> list[tuple[str, str]]:
    """Version marks in a body, as [(kind, label)].

    The core knows three kinds: `since`, `until` and `mentions`. Only `since`
    may exclude content, so mapping a keyword to the wrong kind quietly hides
    documentation.
    """
    marks: list[tuple[str, str]] = []
    for match in _MARK_RE.finditer(text or ""):
        keyword = re.sub(r"\s+", " ", (match.group(1) or "").strip().casefold())
        kind = "until" if keyword in {"until", "removed in"} else "since"
        marks.append((kind, f"v{match.group(2)}.{match.group(3)}"))
    return marks
