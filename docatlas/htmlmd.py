"""HTML → Markdown。通用工具，不认识任何具体站点。

图片地址往往是相对路径，拼成绝对地址需要一个基准；基准由来源适配器给，
这里不写死任何域名。
"""

from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Any
import urllib.parse

from .constants import MARKDOWN_LINK_RE, MARKDOWN_MARKUP_RE, WHITESPACE_RE


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
            cell = WHITESPACE_RE.sub(" ", cell.replace("\n", " ")).strip()
            if self.table_row is not None:
                self.table_row.append(cell.replace("|", "\\|"))
            self.table_cell = None
        elif tag == "tr":
            if self.table_row:
                self.append("| " + " | ".join(self.table_row) + " |\n")
                if not self.table_header_written:
                    self.append("| " + " | ".join("---" for _ in self.table_row) + " |\n")
                    self.table_header_written = True
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
    """只在确实像 HTML 时才转换，避免把普通文本里的 `<` 当标签处理。"""
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
            # 这些字段里经常混着行内 HTML（<strong>、<a> 等）。不转换的话
            # 标签会一路带进正文和全文索引，既难读又污染检索。
            result.append(maybe_html_to_markdown(text, asset_base))
        elif key in {"code", "language"}:
            # 代码原样保留：C++ 模板里的 < > 不是 HTML。
            result.append(text)
    return result


def plain_text(markdown: str) -> str:
    value = MARKDOWN_LINK_RE.sub(r"\1", markdown)
    # 兜底：万一还有漏网的 HTML 标签，先整段去掉再去 Markdown 记号。
    # 否则 MARKDOWN_MARKUP_RE 会把 `>` 吃掉、留下 `<strong` 这种碎片进全文索引。
    value = _HTML_TAG_RE.sub(" ", value)
    value = MARKDOWN_MARKUP_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()
