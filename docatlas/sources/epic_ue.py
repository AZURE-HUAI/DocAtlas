"""Epic Developer Community 文档站适配器。

这里集中了"这个站点长什么样"的全部知识：域名、站点地图布局、
document.json 接口的返回格式、URL 里的语言前缀规则。
核心代码不再知道 Epic 是谁——换成别的站点只需要另写一个这样的模块。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from ..constants import IMAGE_EXTENSIONS, URL_RE
from ..htmlmd import collect_strings, html_to_markdown


# URL 里的语言前缀，例如 /documentation/zh-cn/… → /documentation/…
_LOCALE_PREFIX_RE = re.compile(r"^/documentation/[a-z]{2}-[a-z]{2}/", re.I)


def _doc_prefix(dataset) -> str:
    return dataset.option("doc_prefix", "/")


def _base_url(dataset) -> str:
    return dataset.option("base_url", "").rstrip("/")


# ── 1. 这个站点有哪些页面 ────────────────────────────────────────

def sitemap_index_url(dataset) -> str:
    return dataset.option("sitemap_index", "")


def categorize_sitemap(dataset, url: str) -> str | None:
    """站点地图 URL 命中哪个分类片段就归哪一类；都不中就是我们不要的。"""
    for category, pattern in dataset.categories.items():
        if pattern in url:
            return category
    return None


# 站点地图之外的页面属于哪一类，看地址第一段。这是 Epic 的地址布局，
# 不是这一轮发现的那几个目录——下面三条是清单里**仅有的**三个一级目录，
# 各自纯度都是 100%：`API/` 139,268 页、`BlueprintAPI/` 56,894 页、
# `node-reference/` 1,097 页。文档根下的扁平页在地址上分不出教程还是社区
# 文档，所以不猜——那种页面留给数据集自己表态。
_PATH_CATEGORIES = {
    "api": "cpp_api",
    "blueprintapi": "blueprint_api",
    "node-reference": "node_reference",
}


def categorize_path(dataset, path: str) -> str | None:
    """站内文档路径属于哪个分类；分不出来就返回 None，绝不猜。"""
    prefix = _doc_prefix(dataset)
    if not path.casefold().startswith(prefix.casefold()):
        return None
    segments = [s for s in path[len(prefix):].split("/") if s]
    if len(segments) < 2:
        return None  # 根下的扁平页，地址里没有分类信息
    return _PATH_CATEGORIES.get(segments[0].casefold())


def normalize_location(dataset, location: str) -> tuple[str, str] | None:
    """站点地图里的一条 URL → (标准路径, 正式地址)；不要的返回 None。

    Epic 把各语言版本都列在同一份站点地图里，这里只留目标语言，
    并把 /documentation/zh-cn/… 这种语言前缀去掉，让同一篇文档只有一条路径。
    """
    parsed = urllib.parse.urlsplit(html.unescape(location))
    query = urllib.parse.parse_qs(parsed.query)
    languages = query.get("lang", [])
    if languages and languages[0] not in {dataset.language, ""}:
        return None
    path = urllib.parse.unquote(parsed.path)
    locale_prefix = _LOCALE_PREFIX_RE.match(path)
    if locale_prefix:
        path = "/documentation/" + path[locale_prefix.end():]
    prefix = _doc_prefix(dataset)
    if not path.lower().startswith(prefix.lower()):
        return None
    path = path.rstrip("/")
    if path.lower() in {p.lower() for p in dataset.option("skip_paths", [])}:
        return None
    return path, canonical_url(dataset, path)


# ── 2/4. 地址怎么拼 ──────────────────────────────────────────────

def canonical_url(dataset, path: str) -> str:
    """给人看、给引用用的正式地址。"""
    quoted_path = urllib.parse.quote(path, safe="/:@-._~")
    return (
        f"{_base_url(dataset)}{quoted_path}"
        f"?application_version={dataset.version}&lang={dataset.language}"
    )


def document_request_url(dataset, path: str) -> str:
    """真正去要内容的地址。Epic 的正文在一个 JSON 接口里，不是页面 HTML。"""
    query = urllib.parse.urlencode(
        {
            "path": path,
            "lang": dataset.language,
            "application_version": dataset.version,
        }
    )
    return f"{dataset.option('document_api', '')}?{query}"


def normalize_link_target(dataset, target_url: str) -> str | None:
    """正文里的链接指向本站文档时给出它的路径，指向站外则返回 None。"""
    parsed = urllib.parse.urlsplit(html.unescape(target_url))
    host = urllib.parse.urlsplit(_base_url(dataset)).netloc.lower()
    if parsed.netloc and parsed.netloc.lower() != host:
        return None
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    locale_match = _LOCALE_PREFIX_RE.match(path)
    if locale_match:
        path = "/documentation/" + path[locale_match.end():]
    if not path.lower().startswith(_doc_prefix(dataset).lower()):
        return None
    return path


def is_official_url(dataset, url: str) -> bool:
    """出处是不是官方文档地址——用于给内容质量打分。"""
    return url.startswith(f"{_base_url(dataset)}/documentation/")


def asset_base_url(dataset) -> str:
    return dataset.option("asset_base", f"{_base_url(dataset)}/documentation/")


# ── 3. 拿回来的东西怎么解析 ──────────────────────────────────────

def document_locale(payload: dict[str, Any]) -> str | None:
    """服务器实际给的是哪个语言版本。字段名各站不同，所以由适配器认。

    数据集里的 `language` 是**指令**（去要哪一版），不是事实。站点没有那个
    语言时多半不会报错，只会不声不响回默认语言——于是你得到一个标着德语的
    英文库，AI 还被告知去查德语词。所以要拿这个回声跟声明对一遍。
    """
    locale = payload.get("locale")
    return str(locale) if locale else None

def _document_list_to_markdown(block: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in block.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "Untitled").strip()
        url = item.get("document_url")
        description = str(item.get("description") or "").strip()
        line = f"- [{title}]({url})" if url else f"- {title}"
        if description:
            line += f" — {description}"
        lines.append(line)
    return "\n".join(lines)


def _render_blocks(
    blocks: list[Any], asset_base: str
) -> tuple[str, set[str], set[str]]:
    """Epic 把正文拆成一串 block，逐个转成 Markdown 再拼起来。"""
    rendered: list[str] = []
    assets: set[str] = set()
    block_types: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "unknown")
        block_types.add(block_type)
        if block.get("settings", {}).get("is_hidden") is True:
            continue
        if isinstance(block.get("content_html"), str):
            markdown, found_assets = html_to_markdown(
                block["content_html"], asset_base
            )
            if markdown:
                rendered.append(markdown)
            assets.update(found_assets)
        elif block_type == "document_list":
            markdown = _document_list_to_markdown(block)
            if markdown:
                rendered.append(markdown)
        else:
            fallback = collect_strings(block, asset_base=asset_base)
            if fallback:
                rendered.append("\n\n".join(fallback))

        for url in URL_RE.findall(json.dumps(block, ensure_ascii=False)):
            cleaned = html.unescape(url).rstrip(".,;")
            suffix = Path(urllib.parse.urlsplit(cleaned).path).suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                assets.add(cleaned)
    return "\n\n".join(rendered).strip(), assets, block_types


def _version_is_supported(dataset, document: dict[str, Any]) -> bool:
    """文档没标版本就当通用；标了版本就必须包含当前版本。"""
    applications = document.get("applications") or []
    if isinstance(applications, dict):
        applications = [applications]
    versions = {
        str(application.get("version"))
        for application in applications
        if isinstance(application, dict) and application.get("version") is not None
    }
    return not versions or dataset.version in versions


def parse_document(dataset, path: str, body: bytes) -> dict[str, Any]:
    """原始响应 → 与站点无关的中间结构，交给核心去切块和落库。

    返回 kind='redirect' 表示这一页搬走了；kind='document' 表示有正文。
    """
    document = json.loads(body.decode("utf-8"))
    if document.get("redirect_url"):
        return {"kind": "redirect", "redirect_url": document["redirect_url"]}

    markdown, assets, block_types = _render_blocks(
        document.get("blocks") or [], asset_base_url(dataset)
    )
    title = str(
        document.get("title")
        or document.get("seo_title")
        or Path(path).name
        or "Untitled"
    ).strip()
    description = str(
        document.get("description") or document.get("seo_description") or ""
    ).strip()
    return {
        "kind": "document",
        "title": title,
        "description": description,
        "markdown": markdown,
        "assets": assets,
        "block_types": block_types,
        "document_type": document.get("document_type"),
        "source_type": document.get("source"),
        "updated_at": document.get("updated_at"),
        "version_supported": int(_version_is_supported(dataset, document)),
    }


# ── 5. 类型页的成员表 ───────────────────────────────────────────
#
# Epic 的 C++ API 页把类的属性和方法排成 Markdown 表格，第一列固定叫 Name。
# 这些成员**大多没有自己的页面**——`TargetArmLength` 只存在于
# `USpringArmComponent` 这一页的 Variables 表里。认出这些表是站点知识，
# 所以在适配器里；"BlueprintReadWrite 意味着什么"是领域知识，在知识包里。

# 二级标题 → 这一节列的是哪种成员。故意不收 Constructors / Destructors：
# 构造函数与类同名，提升成实体只会让同一个名字查出两条。
_MEMBER_SECTIONS = {
    "Variables": "cpp_property",
    "Deprecated Variables": "cpp_property",
    "Constants": "cpp_constant",
    "Functions": "cpp_function",
    "Overloads": "cpp_function",
    "Operators": "cpp_function",
}

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _member_name(cell: str, entity_type: str) -> str:
    """从 Name 单元格里取出成员本名。

    属性那一列就是名字；函数那一列是整条签名（`void SetFoo ( float New )`），
    名字是左括号之前的最后一个标识符——前面那些是返回类型和 `virtual`。
    """
    if entity_type != "cpp_function":
        name = cell.strip("`")
        return name if _IDENTIFIER_RE.fullmatch(name) else ""
    tokens = _IDENTIFIER_RE.findall(cell.split("(")[0])
    return tokens[-1] if tokens else ""


def page_members(
    dataset,
    *,
    category: str,
    title: str,
    path: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """这一页的成员表里列了哪些成员。

    带链接的那一行直接跳过：官方给它出了独立页面，那一页本身就是实体，
    再提升一次就成了同一个东西的两份记录。这也正好把范围收敛到真正缺的
    那一批——没有页面、因此在库里根本不存在的成员。
    """
    if category != "cpp_api":
        return []
    members: list[dict[str, Any]] = []
    for section in sections:
        levels = str(section.get("heading_path") or "").split(" > ")
        entity_type = _MEMBER_SECTIONS.get(levels[1]) if len(levels) > 1 else None
        if not entity_type:
            continue
        lines = str(section.get("body_md") or "").split("\n")
        header = _table_cells(lines[0]) if lines else None
        if not header or header[0] != "Name":
            continue
        # 说明符列的表头是一个指向官方说明的链接，各类成员指向的锚点不同，
        # 所以认"最后一列的表头里提到 Unreal Specifiers"，不认具体地址。
        specifier_column = len(header) - 1 if "Unreal Specifiers" in header[-1] else None
        for line in lines[2:]:
            cells = _table_cells(line)
            if not cells or len(cells) != len(header):
                continue
            if "](" in cells[0]:
                continue
            name = _member_name(cells[0], entity_type)
            if not name:
                continue
            specifiers = cells[specifier_column] if specifier_column is not None else ""
            members.append(
                {
                    "name": name,
                    "entity_type": entity_type,
                    "signature": cells[0],
                    "source_url": section.get("source_anchor") or "",
                    "attributes": {
                        "member_section": levels[1],
                        "summary": cells[1] if len(cells) > 1 else "",
                        # 原样存着，不在这里解释。怎么读它是领域知识包的事。
                        "unreal_specifiers": specifiers,
                    },
                }
            )
    return members


def entity_placement(
    dataset, category: str, segments: list[str]
) -> tuple[str | None, str | None]:
    """从 URL 路径推断这个符号属于哪个模块、挂在哪个类型下。

    靠的是 Epic 的路径布局（/api/<域>/<模块>/<类>/<成员>），换个站点就不成立，
    所以放在适配器里而不是核心。
    """
    module = None
    owner_type = None
    if category == "cpp_api" and segments and segments[0].casefold() == "api":
        if len(segments) >= 3:
            module = segments[2]
        if len(segments) >= 2:
            owner_type = segments[-2]
    elif category in {"blueprint_api", "node_reference"} and len(segments) >= 2:
        owner_type = segments[-2]
    return module, owner_type
