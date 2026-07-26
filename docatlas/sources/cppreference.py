"""cppreference.com C++ reference source adapter."""

from __future__ import annotations

import html
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from ..htmlmd import html_to_markdown, plain_text
from ..net import fetch_bytes
from ._html import (
    absolutize_html_urls,
    clean_active_content,
    extract_element_html,
    remove_elements_html,
)


def _base_url(dataset) -> str:
    return dataset.option("base_url", "https://cppreference.com").rstrip("/")


def _category_for_title(title: str) -> str | None:
    normalized = title.replace("_", " ").casefold()
    if not normalized.startswith("cpp/"):
        return None
    if normalized.startswith("cpp/compiler support"):
        return "compiler_support"
    if normalized.startswith("cpp/language"):
        return "language"
    return "standard_library"


def inventory_feeds(dataset) -> list[tuple[str, str | None]]:
    return [(dataset.option("inventory_api", ""), None)]


def read_feed(dataset, url: str) -> list[tuple[str | None, str]]:
    entries: list[tuple[str | None, str]] = []
    continuation: str | None = None
    while True:
        parameters = {
            "action": "query",
            "list": "allpages",
            "apnamespace": "0",
            "aplimit": "max",
            "apprefix": "cpp/",
            "format": "json",
        }
        if continuation:
            parameters["apcontinue"] = continuation
        separator = "&" if urllib.parse.urlsplit(url).query else "?"
        request_url = url + separator + urllib.parse.urlencode(parameters)
        body, _, _ = fetch_bytes(request_url, timeout=120, retries=6)
        payload = json.loads(body.decode("utf-8"))
        for page in payload.get("query", {}).get("allpages", []):
            title = str(page.get("title") or "")
            category = _category_for_title(title)
            if category:
                path = "/" + title.replace(" ", "_")
                entries.append((category, canonical_url(dataset, path)))
        continuation = (
            payload.get("continue", {}).get("apcontinue")
            if isinstance(payload.get("continue"), dict)
            else None
        )
        if not continuation:
            return entries


def normalize_location(dataset, location: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlsplit(html.unescape(location))
    allowed_hosts = {
        urllib.parse.urlsplit(_base_url(dataset)).netloc.casefold(),
        "en.cppreference.com",
        "www.cppreference.com",
    }
    if parsed.netloc and parsed.netloc.casefold() not in allowed_hosts:
        return None
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    if path.startswith("/w/"):
        path = path[2:]
    if not path.casefold().startswith("/cpp/"):
        return None
    return path, canonical_url(dataset, path)


def canonical_url(dataset, path: str) -> str:
    quoted = urllib.parse.quote(path, safe="/:@-._~")
    return f"{_base_url(dataset)}{quoted}"


def document_request_url(dataset, path: str) -> str:
    return canonical_url(dataset, path)


def normalize_link_target(dataset, target_url: str) -> str | None:
    normalized = normalize_location(dataset, target_url)
    return normalized[0] if normalized else None


def is_official_url(dataset, url: str) -> bool:
    normalized = normalize_location(dataset, url)
    return normalized is not None


def _title(document_html: str, path: str) -> str:
    heading = extract_element_html(
        document_html, tag="h1", element_id="firstHeading"
    )
    if heading:
        markdown, _ = html_to_markdown(heading)
        value = plain_text(markdown)
        if value:
            return value
    title_match = re.search(r"<title>(.*?)</title>", document_html, re.I | re.S)
    if title_match:
        value = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1)))
        return re.sub(r"\s*-\s*cppreference\.com\s*$", "", value).strip()
    return Path(path).name.replace("_", " ")


def parse_document(dataset, path: str, body: bytes) -> dict[str, Any]:
    document_html = body.decode("utf-8", errors="replace")
    page_url = canonical_url(dataset, path)
    content = extract_element_html(
        document_html, tag="div", class_name="mw-parser-output"
    )
    content = remove_elements_html(
        content,
        class_names={"mw-editsection", "noprint", "t-navbar", "toc"},
    )
    content = clean_active_content(content)
    content = absolutize_html_urls(content, page_url)
    markdown, assets = html_to_markdown(content, page_url)
    description = next(
        (
            plain_text(line)
            for line in markdown.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "|", "-"))
        ),
        "",
    )
    return {
        "kind": "document",
        "title": _title(document_html, path),
        "description": description,
        "markdown": markdown,
        "assets": assets,
        "block_types": {"html"},
        "document_type": "reference",
        "source_type": "cppreference",
        "updated_at": None,
        "version_supported": 1,
    }


def entity_placement(
    dataset, category: str, segments: list[str]
) -> tuple[str | None, str | None]:
    module = segments[1] if len(segments) > 1 else category
    owner_type = segments[-2] if len(segments) > 2 else None
    return module, owner_type


# ---------------------------------------------------------------------------
# 版本适用范围。
#
# cppreference 用一套固定的括号标记说明"这段从哪一版才有、到哪一版为止"：
#
#     Annotations (since C++26)
#     C++ attribute: carries dependency (since C++11)(removed in C++26)
#     Standard library header <optional (C++17)
#     Defined in header <algorithm (until C++11)
#
# 这是**这个站的排版约定**，不是通用真理，所以归这里而不是核心。核心只拿
# `sort_key` 去比大小，自己不知道 C++20 比 C++17 新。
# ---------------------------------------------------------------------------

VERSION_VOCABULARY = "C++ 标准版本（C++11 / C++17 / C++20 / C++23 …）"

# 括号里的关键词 → 证据种类。核心认得的三种是 since / until / mentions。
# `deprecated in` 有意不收：弃用不等于不存在，那一版里照样能用。
_MARK_KEYWORDS = {
    "since": "since",
    "until": "until",
    "removed in": "until",
}

_MARK_RE = re.compile(
    r"\(\s*(since|until|removed\s+in|deprecated\s+in)?\s*C\+\+(\d{2})\s*\)",
    re.IGNORECASE,
)


def version_sort_key(label: str) -> str:
    """`C++20` → `2020`，用于比大小。

    必须由这里给，不能让核心按数字通用处理：C++98 比 C++11 **早**，
    可数字上 98 > 11，任何通用规则都会排反。两位年份按世纪还原即可，
    不需要维护一张标准年份表，以后出 C++29、C++32 都不用改。
    """
    match = re.search(r"C\+\+\s*(\d{2})\b", label or "", re.IGNORECASE)
    if not match:
        return ""
    year = int(match.group(1))
    return str(1900 + year if year >= 90 else 2000 + year)


def version_marks(text: str) -> list[tuple[str, str]]:
    """从正文里认出版本标记，返回 [(种类, 标签)]。"""
    marks: list[tuple[str, str]] = []
    for match in _MARK_RE.finditer(text or ""):
        keyword = re.sub(r"\s+", " ", (match.group(1) or "").strip().casefold())
        if keyword == "deprecated in":
            continue
        # 不带关键词的裸 `(C++17)` 就是"这一版引入的"。
        kind = _MARK_KEYWORDS.get(keyword, "since")
        marks.append((kind, f"C++{match.group(2)}"))
    return marks
