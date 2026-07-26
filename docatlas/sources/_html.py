"""Small HTML helpers shared by static documentation source adapters."""

from __future__ import annotations

import html
from html.parser import HTMLParser
import re
import urllib.parse


_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_ACTIVE_CONTENT_RE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.I | re.S,
)
_URL_ATTRIBUTE_RE = re.compile(
    r"\b(href|src)=(['\"])(.*?)\2",
    re.I | re.S,
)


class _ElementExtractor(HTMLParser):
    def __init__(
        self,
        *,
        tag: str | None,
        element_id: str | None,
        class_name: str | None,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.tag = tag.casefold() if tag else None
        self.element_id = element_id
        self.class_name = class_name
        self.depth = 0
        self.found = False
        self.output: list[str] = []

    def _matches(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        return (
            (self.tag is None or tag.casefold() == self.tag)
            and (self.element_id is None or values.get("id") == self.element_id)
            and (self.class_name is None or self.class_name in classes)
        )

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if not self.found:
            if self._matches(tag, attrs):
                self.found = True
                self.depth = 1
            return
        if not self.depth:
            return
        self.output.append(self.get_starttag_text())
        if tag.casefold() not in _VOID_TAGS:
            self.depth += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self.found and self.depth:
            self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if not self.found or not self.depth:
            return
        self.depth -= 1
        if self.depth:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.found and self.depth:
            self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.found and self.depth:
            self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.found and self.depth:
            self.output.append(f"&#{name};")

    def html(self) -> str:
        return "".join(self.output)


class _ElementFilter(HTMLParser):
    def __init__(
        self,
        *,
        class_names: set[str],
        element_ids: set[str],
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.class_names = class_names
        self.element_ids = element_ids
        self.skip_depth = 0
        self.output: list[str] = []

    def _should_skip(self, attrs: list[tuple[str, str | None]]) -> bool:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        return bool(
            classes.intersection(self.class_names)
            or values.get("id") in self.element_ids
        )

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        is_void = tag.casefold() in _VOID_TAGS
        if self.skip_depth:
            if not is_void:
                self.skip_depth += 1
            return
        if self._should_skip(attrs):
            if not is_void:
                self.skip_depth = 1
            return
        self.output.append(self.get_starttag_text())

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if not self.skip_depth and not self._should_skip(attrs):
            self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.skip_depth:
            self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.skip_depth:
            self.output.append(f"&#{name};")

    def html(self) -> str:
        return "".join(self.output)


def extract_element_html(
    value: str,
    *,
    tag: str | None = None,
    element_id: str | None = None,
    class_name: str | None = None,
) -> str:
    parser = _ElementExtractor(
        tag=tag,
        element_id=element_id,
        class_name=class_name,
    )
    parser.feed(value)
    parser.close()
    return parser.html()


def remove_elements_html(
    value: str,
    *,
    class_names: set[str] | None = None,
    element_ids: set[str] | None = None,
) -> str:
    parser = _ElementFilter(
        class_names=class_names or set(),
        element_ids=element_ids or set(),
    )
    parser.feed(value)
    parser.close()
    return parser.html()


def clean_active_content(value: str) -> str:
    return _ACTIVE_CONTENT_RE.sub("", value)


def absolutize_html_urls(value: str, page_url: str) -> str:
    def replace(match: re.Match[str]) -> str:
        attribute, quote, raw_url = match.groups()
        decoded = html.unescape(raw_url.strip())
        if not decoded or decoded.lower().startswith(
            ("data:", "javascript:", "mailto:")
        ):
            return match.group(0)
        absolute = urllib.parse.urljoin(page_url, decoded)
        return f"{attribute}={quote}{html.escape(absolute, quote=True)}{quote}"

    return _URL_ATTRIBUTE_RE.sub(replace, value)
