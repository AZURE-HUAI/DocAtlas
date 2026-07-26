"""Fixed-version Blender Manual source adapter."""

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
    return dataset.option("base_url", "https://docs.blender.org").rstrip("/")


def _manual_prefix(dataset) -> str:
    return f"/manual/{dataset.language}/{dataset.version}/"


def _category_for_docname(dataset, docname: str) -> str | None:
    for category, prefix in dataset.categories.items():
        if docname.startswith(prefix):
            return category
    return None


def categorize_path(dataset, path: str) -> str | None:
    """手册路径属于数据集声明的哪个目录；都不在就返回 None。

    返回 None 不代表"这不是手册页"，只代表"不在我们声明的目录里"——
    要不要把这种页面收进来是数据集的范围策略，见 `[inventory]`。
    """
    return _category_for_docname(dataset, path.strip("/"))


def inventory_feeds(dataset) -> list[tuple[str, str | None]]:
    return [(dataset.option("search_index", ""), None)]


def _docnames(body: bytes) -> list[str]:
    text = body.decode("utf-8")
    match = re.search(r'"docnames":(\[.*?\]),"envversion"', text, re.S)
    if not match:
        raise ValueError("Blender searchindex.js does not contain docnames")
    return [str(value) for value in json.loads(match.group(1))]


def read_feed(dataset, url: str) -> list[tuple[str | None, str]]:
    body, _, _ = fetch_bytes(url, timeout=120, retries=6)
    entries: list[tuple[str | None, str]] = []
    for docname in _docnames(body):
        category = _category_for_docname(dataset, docname)
        if category:
            entries.append((category, canonical_url(dataset, f"/{docname}")))
    return entries


def normalize_location(dataset, location: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlsplit(html.unescape(location))
    expected_host = urllib.parse.urlsplit(_base_url(dataset)).netloc.casefold()
    if parsed.netloc and parsed.netloc.casefold() != expected_host:
        return None
    path = urllib.parse.unquote(parsed.path)
    prefix = _manual_prefix(dataset)
    if path.startswith(prefix):
        path = "/" + path[len(prefix):]
    elif parsed.netloc:
        return None
    path = path.removesuffix(".html").rstrip("/")
    docname = path.strip("/")
    if not _category_for_docname(dataset, docname):
        return None
    return path, canonical_url(dataset, path)


def canonical_url(dataset, path: str) -> str:
    docname = urllib.parse.quote(path.strip("/"), safe="/:@-._~")
    return f"{_base_url(dataset)}{_manual_prefix(dataset)}{docname}.html"


def document_request_url(dataset, path: str) -> str:
    return canonical_url(dataset, path)


def normalize_link_target(dataset, target_url: str) -> str | None:
    """正文里的这条链接指向本手册的哪一页；不是手册页就返回 None。

    和 `normalize_location` 差一个条件：**不看分类**。一条指向固定版本手册
    某一页的链接就是官方文档链接，我们收不收录那个目录是我们的范围决定，
    不是这条链接的性质。两者混为一谈的后果是：节点页链到
    `interface/controls/nodes/groups` 会被记成站外链接，于是"清单漏了这个
    目录"这件事在库里彻底看不见（BUG-011）。
    """
    parsed = urllib.parse.urlsplit(html.unescape(target_url))
    expected_host = urllib.parse.urlsplit(_base_url(dataset)).netloc.casefold()
    if parsed.netloc and parsed.netloc.casefold() != expected_host:
        return None
    path = urllib.parse.unquote(parsed.path)
    prefix = _manual_prefix(dataset)
    if path.startswith(prefix):
        path = "/" + path[len(prefix):]
    elif parsed.netloc:
        return None  # 别的版本或别的语言，不是这个数据集的页面
    # 手册页一律是 .html。`_images/…png`、`_downloads/…zip` 是素材不是文档，
    # 收进清单只会得到一堆抓不出正文的死页。
    if not path.endswith(".html"):
        return None
    return path.removesuffix(".html").rstrip("/")


def is_official_url(dataset, url: str) -> bool:
    return normalize_location(dataset, url) is not None


def _title(document_html: str, path: str) -> str:
    heading = extract_element_html(document_html, tag="h1")
    if heading:
        markdown, _ = html_to_markdown(heading)
        value = re.sub(r"\s*¶\s*$", "", plain_text(markdown)).strip()
        if value:
            return value
    return Path(path).name.replace("_", " ").title()


def parse_document(dataset, path: str, body: bytes) -> dict[str, Any]:
    document_html = body.decode("utf-8", errors="replace")
    page_url = canonical_url(dataset, path)
    article = extract_element_html(
        document_html, tag="article", element_id="furo-main-content"
    )
    article = remove_elements_html(article, class_names={"headerlink"})
    article = re.sub(r"<h1\b[^>]*>.*?</h1>", "", article, count=1, flags=re.I | re.S)
    article = clean_active_content(article)
    article = absolutize_html_urls(article, page_url)
    markdown, assets = html_to_markdown(article, page_url)
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
        "document_type": "manual",
        "source_type": "blender_manual",
        "updated_at": None,
        "version_supported": 1,
    }


def entity_placement(
    dataset, category: str, segments: list[str]
) -> tuple[str | None, str | None]:
    module = segments[-2] if len(segments) > 1 else category
    owner_type = category
    return module, owner_type


# ---------------------------------------------------------------------------
# 版本适用范围。
#
# **Blender 手册没有机器可读的版本标注约定**，这一点必须说清楚。它不像
# cppreference 那样给每段挂 `(since C++20)`；版本只在正文里当普通句子出现：
#
#     This recreates the behavior of the Transfer Attribute node
#     from Blender versions before 3.4.
#
# 这是真实存在、也确实有用的线索（它就是"旧节点换成了什么"这类问题的答案），
# 但它是散文，不是标注。所以这里**只产出 `mentions`**——软证据，只在迁移追溯
# 时用来加分，永远不参与排除。把散文当成硬适用范围去筛内容，会把手册里
# 顺口提一句版本号的正常页面整片删掉。
# ---------------------------------------------------------------------------

VERSION_VOCABULARY = "Blender 版本号（如 3.4、4.2、5.2）"

# 只在 Blender / version(s) 这类词附近的数字才算版本号。手册里 `0.5`、`2.0`
# 这样的参数取值遍地都是，不限定上下文会把它们全当成版本。
_VERSION_CONTEXT_RE = re.compile(
    r"\b(?:blender|versions?)\b[^.\n]{0,40}?(\d+\.\d+)",
    re.IGNORECASE,
)


def version_sort_key(label: str) -> str:
    """`3.4` → `0003.0004`，补零之后按字符串比就是按数字比。"""
    match = re.search(r"(\d+(?:\.\d+)*)", label or "")
    if not match:
        return ""
    return ".".join(f"{int(part):04d}" for part in match.group(1).split("."))


def version_marks(text: str) -> list[tuple[str, str]]:
    """从正文里认出被提到的 Blender 版本。只产出软证据 `mentions`。"""
    return [
        ("mentions", match.group(1))
        for match in _VERSION_CONTEXT_RE.finditer(text or "")
    ]
