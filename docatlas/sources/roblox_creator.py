"""Roblox Creator Hub 来源适配器。

这个站点已经为机器阅读做好了准备，所以这里几乎不用做解析：

* 三份索引是纯文本清单，每行 `- [标题](/路径.md): 描述`：

      /docs/llms.txt                    全站（教程、Studio、Luau、Open Cloud 指南）
      /docs/reference/engine/llms.txt   Engine API（类 / 数据类型 / 枚举 / 库 / 全局）
      /docs/cloud/reference 那一份       Open Cloud API

  刻意**不用** `/docs/llms-full.txt`：那是把整站正文拼成一个文件，
  下载它等于绕过按需抓取，也拿不到逐页的出处。

* 单页正文直接给 Markdown：页面地址后面加 `.md` 就是。开头一段 YAML
  frontmatter 带着标题、摘要、继承链和生成时间。

**Engine API 和 Open Cloud API 是两套完全不同的东西**，官方索引开篇就在强调
这一点：Engine API 是实验体验内部用 Luau 调的对象和服务
（`game:GetService()`），Open Cloud 是外部服务器用 `x-api-key` 调的 HTTP 接口。
两者在地址上就是分开的（`/docs/reference/engine/` 对 `/docs/cloud/`），所以
这里按路径分类，不靠关键词——`Assets` 这个词两边都有，靠词去认必然混。

站点没有产品版本号，文档持续更新。所以数据集用**快照日期**当版本
（`roblox-creator-2026-07-26`），索引文件第一行的
`<!-- Last updated: … -->` 是它的溯源依据。
"""

from __future__ import annotations

import html
from pathlib import Path
import re
from typing import Any
import urllib.parse

from ..htmlmd import plain_text
from ..net import fetch_bytes


# 索引里每一行的形状：`- [标题](/docs/…md): 一句话描述`（描述可以没有）。
_ENTRY_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>/docs/[^)]+)\)")

# 索引开头还有一段"入口一览"，那里的地址是**裸写**的，不是 Markdown 链接：
#
#     - Full content (single file): /docs/llms-full.txt
#     - Deprecated API inventory: /docs/reference/engine/deprecated.md
#
# 只收 `.md`：那是页面格式，而 `.txt` 是索引本身（包括那份不该下载的全文）。
# 少了这一条，官方明确列为入口的 deprecated 清单永远进不了清单。
_BARE_ENTRY_RE = re.compile(r"^- [^:\[]+:\s*(?P<path>/docs/\S+\.md)\s*$")

# 正文开头的 YAML frontmatter。只取几个确定存在的字段，不做通用 YAML 解析。
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

# 页面路径 → 分类。顺序有意义：`/docs/en-us/cloud…` 要在 `studio_guides`
# 之前判掉，否则 Open Cloud 的指南会被算成 Studio 教程。
#
# `en-us/cloud/` 末尾那个斜杠是要紧的：`en-us/cloud-services/` 讲的是
# **实验内部**的 DataStoreService、MemoryStoreService、HttpService，
# 要用 `game:GetService()` 拿——那是 Engine 那一侧的东西，不是 Open Cloud。
# 少一个斜杠，这一整批就会被归进外部 HTTP 接口，正是官方索引开篇警告的那种混淆。
_PATH_RULES = (
    ("reference/engine/", "engine_api"),
    ("cloud/", "open_cloud"),
    ("en-us/cloud/", "open_cloud"),
    ("en-us/luau/", "luau"),
    ("en-us/luau", "luau"),
    ("en-us/scripting/", "luau"),
    ("en-us/", "studio_guides"),
)


# 正文里的链接会**省掉语言段**：索引写 `/docs/en-us/studio/setup`，
# 而 `studio/setup.md` 正文里链的是 `/docs/studio/setup`。两个地址都返回同一页，
# 而且那一页自己在 frontmatter 里写明了正规写法（`url: /docs/en-us/studio/setup`）。
# 不补回去的话，同一页会有两条路径，链接永远对不上清单。
#
# 有两处例外，它们本来就没有语言段——都是自动生成的 API 参考，
# 官方子索引里就是这么列的：
_LOCALE_FREE_PREFIXES = ("reference/engine/", "cloud/reference/")


def _base_url(dataset) -> str:
    return dataset.option("base_url", "https://create.roblox.com").rstrip("/")


def _canonical_path(dataset, path: str) -> str:
    """把地址收敛成站点自己认的那一种写法。

    两个方向都要走。索引里同一个 Engine 类会同时以
    `/docs/reference/engine/classes/DataStore` 和
    `/docs/en-us/reference/engine/classes/DataStore` 出现——不收敛的话，
    同一页会在清单里占两条，还会因为路径不同被判成两个分类。
    """
    prefix = _doc_prefix(dataset)
    if not path.startswith(prefix):
        return path
    locale = dataset.language
    relative = path[len(prefix):].removeprefix(f"{locale}/")
    if relative == locale:
        relative = ""
    # 自动生成的 API 参考本来就没有语言段；其余页面一律带。
    if relative.startswith(_LOCALE_FREE_PREFIXES):
        return f"{prefix}{relative}"
    return f"{prefix}{locale}/{relative}" if relative else f"{prefix}{locale}"


def _doc_prefix(dataset) -> str:
    return dataset.option("doc_prefix", "/docs/")


# ── 1. 这个站点有哪些页面 ────────────────────────────────────────

def inventory_feeds(dataset) -> list[tuple[str, str | None]]:
    """三份索引各读一遍。分类由每个条目自己的路径决定，所以入口不带分类。"""
    return [(f"{_base_url(dataset)}{path}", None) for path in dataset.option("indexes", [])]


def read_feed(dataset, url: str) -> list[tuple[str | None, str]]:
    body, _, _ = fetch_bytes(url, timeout=120, retries=6)
    text = body.decode("utf-8", errors="replace")
    entries: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        match = _ENTRY_RE.match(stripped) or _BARE_ENTRY_RE.match(stripped)
        if not match:
            continue
        normalized = normalize_location(dataset, match.group("path"))
        if not normalized or normalized[0] in seen:
            continue
        seen.add(normalized[0])
        entries.append((categorize_path(dataset, normalized[0]), normalized[1]))
    return entries


def index_updated_at(dataset) -> str | None:
    """索引自己声明的生成时间。站点没有版本号，这是唯一的时间锚点。"""
    url = f"{_base_url(dataset)}{dataset.option('indexes', ['/docs/llms.txt'])[0]}"
    body, _, _ = fetch_bytes(url, timeout=120, retries=3)
    match = re.search(r"Last updated:\s*([0-9TZ:\-]+)", body[:400].decode("utf-8", "replace"))
    return match.group(1) if match else None


def categorize_path(dataset, path: str) -> str | None:
    """站内文档路径属于哪一类。按地址分，不按关键词——见模块说明。"""
    prefix = _doc_prefix(dataset)
    if not path.startswith(prefix):
        return None
    relative = path[len(prefix):]
    for marker, category in _PATH_RULES:
        if relative.startswith(marker):
            return category if category in dataset.categories else None
    return None


def normalize_location(dataset, location: str) -> tuple[str, str] | None:
    """索引里的一条地址 → (标准路径, 正式地址)；不是本站文档就返回 None。"""
    parsed = urllib.parse.urlsplit(html.unescape(location))
    host = urllib.parse.urlsplit(_base_url(dataset)).netloc.casefold()
    if parsed.netloc and parsed.netloc.casefold() != host:
        return None
    path = _canonical_path(
        dataset, urllib.parse.unquote(parsed.path).removesuffix(".md").rstrip("/")
    )
    if not path.startswith(_doc_prefix(dataset)):
        return None
    if categorize_path(dataset, path) is None:
        return None
    return path, canonical_url(dataset, path)


# ── 2/4. 地址怎么拼 ──────────────────────────────────────────────

def canonical_url(dataset, path: str) -> str:
    """给人看、给引用用的地址。"""
    return f"{_base_url(dataset)}{urllib.parse.quote(path, safe='/:@-._~')}"


def document_request_url(dataset, path: str) -> str:
    """真正去要正文的地址：页面地址后面加 `.md` 就是 Markdown 原文。"""
    return f"{canonical_url(dataset, path)}.md"


def normalize_link_target(dataset, target_url: str) -> str | None:
    """正文里这条链接指向本站的哪一页；不是本站文档页就返回 None。

    刻意**不**要求分类命中：一条指向 `/docs/…` 的链接就是官方文档链接，
    收不收录那个目录是数据集的范围决定，不是链接的性质（BUG-011 / BUG-013）。
    """
    parsed = urllib.parse.urlsplit(html.unescape(target_url))
    host = urllib.parse.urlsplit(_base_url(dataset)).netloc.casefold()
    if parsed.netloc and parsed.netloc.casefold() != host:
        return None
    path = _canonical_path(
        dataset, urllib.parse.unquote(parsed.path).removesuffix(".md").rstrip("/")
    )
    return path if path.startswith(_doc_prefix(dataset)) else None


def is_official_url(dataset, url: str) -> bool:
    return url.startswith(f"{_base_url(dataset)}{_doc_prefix(dataset)}")


def asset_base_url(dataset) -> str:
    return _base_url(dataset) + "/"


# ── 3. 拿回来的东西怎么解析 ──────────────────────────────────────

def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """切出开头的 YAML frontmatter。

    只认"顶格的 `键: 值`"这一种形状——这几份文档里需要的字段全是这个形状，
    为此引一个 YAML 解析器不划算。列表值（`inherits:` 下面那几行）留给正文，
    它们在 Markdown 里照样看得见。
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and not key.startswith((" ", "\t", "-")):
            fields[key.strip()] = value.strip().strip('"')
    return fields, text[match.end():]


def parse_document(dataset, path: str, body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    fields, markdown = _frontmatter(text)
    # 少数生成页（deprecated 清单就是）没有 frontmatter 标题，标题写在正文的
    # 一级标题里。退回路径末段只会得到 "deprecated" 这种查不到的名字。
    heading = re.search(r"^#\s+(.+)$", markdown, re.M)
    title = (
        fields.get("title")
        or fields.get("name")
        or (heading.group(1).strip() if heading else "")
        or Path(path).name
    )
    description = fields.get("summary") or fields.get("description") or ""
    if not description:
        # 没有摘要字段时，用正文里第一句引用块（`> …`）或第一个自然段。
        description = next(
            (
                plain_text(line.lstrip("> ").strip())
                for line in markdown.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "|", "-", "*"))
            ),
            "",
        )
    return {
        "kind": "document",
        "title": title.strip(),
        "description": description.strip(),
        "markdown": markdown.strip(),
        "assets": set(),
        "block_types": {"markdown"},
        # `type: class` / `type: feature` 是站点自己标的，原样传下去。
        "document_type": fields.get("type"),
        "source_type": "roblox_creator_hub",
        "updated_at": fields.get("last_updated"),
        # 站点没有产品版本，也就无所谓"这一页支持不支持当前版本"。
        "version_supported": 1,
    }


def entity_placement(
    dataset, category: str, segments: list[str]
) -> tuple[str | None, str | None]:
    """从路径推断这个符号属于哪个模块、挂在哪个类型下。

    `reference/engine/classes/Part` → 模块 `classes`，归属 `classes`；
    `en-us/luau/booleans` → 模块 `luau`。倒数第二段就是它所在的那一组。
    """
    owner = segments[-2] if len(segments) > 1 else None
    return owner, owner
