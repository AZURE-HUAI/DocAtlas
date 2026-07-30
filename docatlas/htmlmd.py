"""HTML -> Markdown. A generic utility that knows no particular site.

Image URLs are often relative, and turning them absolute needs a base. The base
comes from the source adapter; no domain is hardcoded here.
"""

from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Any
import urllib.parse

from .constants import (
    CODE_FENCE_RE,
    HEADING_RE,
    MARKDOWN_LINK_RE,
    MARKDOWN_MARKUP_RE,
    WHITESPACE_RE,
)


def _holds_blocks(cell: str) -> bool:
    """Does this cell hold block-level content (a heading, a code block)?

    A Markdown table cell can only hold inline content, yet sites routinely use
    `<table>` as a layout container: a site may wrap an entire versioned section
    in a table whose single cell holds a full `<h3>` plus paragraphs and a
    `<pre>`. Flattening that into one row loses the heading, the paragraphs and
    the code block together, and the whole passage ends up filed under the
    previous section.

    Only headings and code fences count: those genuinely cannot be expressed
    inside a table, so losing them is real damage. Several plain paragraphs
    squashed into one row is merely ugly, does not change the meaning, and is not
    worth reshaping the table for.
    """
    return any(
        HEADING_RE.match(line) or CODE_FENCE_RE.match(line)
        for line in cell.splitlines()
    )


class HTMLToMarkdown(HTMLParser):
    """Small, dependency-free HTML → Markdown converter."""

    def __init__(self, asset_base: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.asset_base = asset_base
        self.output: list[str] = []
        self.href_stack: list[str | None] = []
        self.in_pre = False
        self.in_code = False
        self.list_stack: list[dict[str, Any]] = []
        self.table_row: list[str] | None = None
        self.table_cell: list[str] | None = None
        self.block_cells: list[str] = []
        self.table_header = False
        self.table_header_written = False
        self.assets: set[str] = set()

    def append(self, value: str) -> None:
        if self.table_cell is not None:
            self.table_cell.append(value)
        else:
            self.output.append(value)

    def newline(self, count: int = 1) -> None:
        self.append("\n" * count)

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attrs_dict = dict(attrs)
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.newline(2)
            self.append("#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "figure"}:
            self.newline(2)
        elif tag == "br":
            self.newline()
        elif tag in {"strong", "b"}:
            self.append("**")
        elif tag in {"em", "i"}:
            self.append("*")
        elif tag == "blockquote":
            self.newline(2)
            self.append("> ")
        elif tag == "pre":
            self.in_pre = True
            self.newline(2)
            self.append("```\n")
        elif tag == "code" and not self.in_pre:
            self.in_code = True
            self.append("`")
        elif tag in {"ul", "ol"}:
            self.list_stack.append({"tag": tag, "count": 0})
            self.newline()
        elif tag == "li":
            self.newline()
            indent = "  " * max(0, len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1]["tag"] == "ol":
                self.list_stack[-1]["count"] += 1
                marker = f"{self.list_stack[-1]['count']}. "
            else:
                marker = "- "
            self.append(indent + marker)
        elif tag == "a":
            href = attrs_dict.get("href")
            self.href_stack.append(href)
            self.append("[")
        elif tag == "img":
            src = attrs_dict.get("src") or attrs_dict.get("data-src")
            alt = attrs_dict.get("alt") or "image"
            if src:
                absolute = (
                    urllib.parse.urljoin(self.asset_base, src)
                    if self.asset_base
                    else src
                )
                self.assets.add(absolute)
                self.append(f"![{alt}]({absolute})")
        elif tag == "table":
            self.newline(2)
            self.table_header_written = False
        elif tag == "tr":
            self.table_row = []
        elif tag in {"th", "td"}:
            self.table_cell = []
            self.table_header = tag == "th"

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self.newline(2)
        elif tag in {"div", "section", "article", "figure"}:
            self.newline()
        elif tag in {"strong", "b"}:
            self.append("**")
        elif tag in {"em", "i"}:
            self.append("*")
        elif tag == "pre":
            self.append("\n```\n\n")
            self.in_pre = False
        elif tag == "code" and not self.in_pre:
            self.append("`")
            self.in_code = False
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            self.newline()
        elif tag == "a":
            href = self.href_stack.pop() if self.href_stack else None
            self.append(f"]({href})" if href else "]")
        elif tag in {"th", "td"}:
            cell = "".join(self.table_cell or [])
            if self.table_row is not None:
                if _holds_blocks(cell):
                    self.block_cells.append(cell)
                else:
                    flat = WHITESPACE_RE.sub(" ", cell.replace("\n", " ")).strip()
                    self.table_row.append(flat.replace("|", "\\|"))
            self.table_cell = None
        elif tag == "tr":
            # A cell holding a heading or code block means this is not a data
            # table but a layout container. Those cannot be expressed in a
            # Markdown table, so flattening would amount to deleting them.
            for block in self.block_cells:
                self.append("\n\n" + block.strip() + "\n\n")
            if self.table_row:
                self.append("| " + " | ".join(self.table_row) + " |\n")
                if not self.table_header_written:
                    self.append("| " + " | ".join("---" for _ in self.table_row) + " |\n")
                    self.table_header_written = True
            self.block_cells = []
            self.table_row = None
        elif tag == "table":
            self.newline(2)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self.in_pre:
            self.append(data)
            return
        if self.table_row is not None and self.table_cell is None:
            return
        normalized = re.sub(r"\s+", " ", data)
        self.append(normalized)

    def markdown(self) -> str:
        value = "".join(self.output)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        value = re.sub(r" +([,.;:!?])", r"\1", value)
        lines = [
            line.strip() if line.lstrip().startswith("|") else line.rstrip()
            for line in value.splitlines()
        ]
        return "\n".join(lines).strip()


def html_to_markdown(value: str, asset_base: str = "") -> tuple[str, set[str]]:
    parser = HTMLToMarkdown(asset_base)
    parser.feed(value)
    parser.close()
    return parser.markdown(), parser.assets


_HTML_TAG_RE = re.compile(
    r"</?(?:p|br|hr|strong|b|em|i|u|ul|ol|li|dl|dt|dd|h[1-6]|a|code|pre|table"
    r"|thead|tbody|tr|td|th|blockquote|div|span|img|figure|figcaption|section)"
    r"\b[^>]*>",
    re.I,
)


def looks_like_html(text: str) -> bool:
    return bool(_HTML_TAG_RE.search(text))


def maybe_html_to_markdown(text: str, asset_base: str = "") -> str:
    """Convert only when it really looks like HTML, so a `<` in ordinary text is
    not treated as a tag.
    """
    if not looks_like_html(text):
        return text
    markdown, _assets = html_to_markdown(text, asset_base)
    return markdown or text


def collect_strings(
    value: Any,
    *,
    key: str = "",
    seen: set[str] | None = None,
    asset_base: str = "",
) -> list[str]:
    """Conservative fallback for uncommon structured blocks."""
    if seen is None:
        seen = set()
    result: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key in {
                "settings",
                "id",
                "hash_id",
                "document_hash_id",
                "revision_hash_id",
                "storage_key",
                "thumbnail_storage_key",
                "status",
                "has_live_revision",
            }:
                continue
            result.extend(
                    collect_strings(
                        child, key=child_key, seen=seen, asset_base=asset_base
                    )
                )
    elif isinstance(value, list):
        for child in value:
            result.extend(
                collect_strings(child, key=key, seen=seen, asset_base=asset_base)
            )
    elif isinstance(value, str):
        text = value.strip()
        if not text or text in seen:
            return result
        seen.add(text)
        if key.endswith("_html"):
            md, _ = html_to_markdown(text, asset_base)
            if md:
                result.append(md)
        elif key.endswith("_url"):
            return result
        elif key in {"title", "name"}:
            result.append(f"### {text}")
        elif key in {"description", "content", "text", "caption"}:
            # These fields often mix in inline HTML (<strong>, <a>, ...). Left
            # unconverted, the tags travel into the body and the full-text index,
            # which is both unreadable and polluting to search.
            result.append(maybe_html_to_markdown(text, asset_base))
        elif key in {"code", "language"}:
            # Code is kept verbatim: `<` and `>` in a C++ template are not HTML.
            result.append(text)
    return result


def plain_text(markdown: str) -> str:
    value = MARKDOWN_LINK_RE.sub(r"\1", markdown)
    # Fallback for any tag that slipped through: strip whole tags before stripping
    # Markdown markers, or MARKDOWN_MARKUP_RE eats the `>` and leaves fragments
    # like `<strong` to enter the full-text index.
    value = _HTML_TAG_RE.sub(" ", value)
    value = MARKDOWN_MARKUP_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


# A line holding only an image, optionally wrapped in a link:
# `![alt](src)`, `[![alt](src)](href)`.
_IMAGE_ONLY_LINE_RE = re.compile(
    r"^\[?\s*!\[[^\]]*\]\([^)]*\)\s*\]?(?:\([^)]*\))?$"
)
# A list bullet, not `**bold**`: only a star followed by whitespace counts.
_STAR_BULLET_RE = re.compile(r"^\*\s")

# Minimum length for a summary sentence. Below this it is a label, not a sentence:
# a node page may hold nothing but a diagram plus parameter names and badges such
# as `Image`, `EEVEE Only` or `Brightness`, and picking any of those as "what this
# page is about" misleads.
#
# Counted in **characters**, not words: word counts need spaces to split on, and a
# whole sentence in Chinese, Japanese or Korean contains none, so a word count
# would judge every normal sentence in those languages to be a label. A dataset
# declares its own language; nothing is assumed here.
MIN_LEAD_CHARS = 16


def lead_sentence(markdown: str) -> str:
    """The first sentence-like line of a body, for use as the page summary.

    Skips what cannot answer "what is this page about": headings (that is the
    page's own name), table rows, list items (usually a table of contents or
    navigation), image-only lines, and labels shorter than `MIN_LEAD_CHARS`. An
    image-only line matters because a page opening with a diagram would otherwise
    yield the flattened image markup as its summary. When nothing qualifies,
    returns an empty string: better no summary than passing off something
    meaningless, since the page's name is already in its title.

    Deliberately does **not** skip block quotes: some sites write their opening
    paragraph inside `> ...`, and `plain_text` strips the marker to leave exactly
    the wanted sentence. Deliberately treats only "star plus space" as a list
    item, too: a sentence starting `**Bold**` is prose, not a list, and excluding
    it would discard perfectly good opening lines.
    """
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "-")):
            continue
        if _STAR_BULLET_RE.match(stripped) or _IMAGE_ONLY_LINE_RE.match(stripped):
            continue
        if len(text := plain_text(stripped)) >= MIN_LEAD_CHARS:
            return text
    return ""
