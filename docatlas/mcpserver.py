"""MCP 服务器：让支持 MCP 的 AI 客户端直接查这个知识库。

装了它之后，Claude Desktop / Cursor / Cline 等客户端不用记命令行，
直接就有 `docatlas_ask` 这样的工具可以调。

**刻意不引入 MCP SDK。** 整个项目到现在一个第三方包都不需要，
装起来就是"有 Python 就能跑"。MCP 的 stdio 传输本身就是一行一条的
JSON-RPC 2.0，需要实现的方法只有三个（initialize / tools/list / tools/call），
用标准库写完不到两百行，不值得为它引进一整套依赖和版本冲突风险。

这一层是**薄壳**：不重新实现任何检索逻辑，全部转调 ask / search / related，
所以命令行和 MCP 永远给出一致的结果。

**一个服务器服务本机所有数据集。** 每个工具都接一个可选的 `dataset_id`，
不给就用启动时那个。以前是一个进程锁死一个库，要查第二个库得在客户端里
再配一条 server 记录——实测的结果是没人配，跨库的查询全部退回了命令行。

工具的入参出参是**领域中立**的：这里不认识 Unreal，也不认识蓝图。分类、
关系类型、证据类型都由数据集自己声明，通过 `docatlas_list_datasets` 发现，
不写进协议。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import runtime
from .context import (
    answer,
    describe_lookup,
    exact_page_hint,
    related_payload,
    render_context_markdown,
)
from .db import connect_db, initialize_db
from .net import REQUEST_LIMITER
from .ondemand import DEFAULT_FETCH_LIMIT, inventory_lookup
from .search import search_docs
from .text import script_mismatch
from . import versions


SERVER_NAME = "docatlas"
SERVER_VERSION = "2.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

# 结构化结果的合同版本。字段含义变了就 +1，调用方据此判断能不能照旧解析。
CONTRACT_VERSION = "1"


class ToolError(Exception):
    """调用方能改的错（数据集不存在、分类拼错、库还没建）。

    和程序 bug 分开：这类要把可执行的下一步说清楚，不该甩一段堆栈。
    """


def _dataset_catalogue() -> list[tuple[str, str]]:
    """本机有哪些数据集，(id, 名字)。坏掉的配置跳过，不能让它拖垮整个服务器。"""
    catalogue = []
    for dataset_id in runtime.available_dataset_ids():
        try:
            catalogue.append((dataset_id, runtime.workspace(dataset_id).name))
        except BaseException:  # noqa: BLE001  配置错误会抛 SystemExit
            catalogue.append((dataset_id, "（配置读不出来）"))
    return catalogue


def _dataset_hint() -> str:
    listing = "；".join(f"{key}（{name}）" for key, name in _dataset_catalogue())
    return f"要查哪个文档库，不填就用默认的那个。本机有：{listing or '（一个都没有）'}"


_DATASET_PROPERTY = {"type": "string", "description": _dataset_hint()}
_FORMAT_PROPERTY = {
    "type": "string",
    "enum": ["markdown", "json"],
    "default": "markdown",
    "description": (
        "markdown 是给人和 AI 读的，同样内容比 JSON 省三四成 token，"
        "默认用它。要稳定的字段来解析才用 json。"
    ),
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "docatlas_ask",
        "description": (
            "查本地技术文档知识库，返回按 token 预算裁剪好的答案材料"
            "（正文 + 原出处 URL + 相关项指针）。回答文档类问题请优先用这个，"
            "不要凭记忆。本地没有的页面会自动补抓，通常一两秒。"
            "有哪些库、每个库覆盖什么，用 docatlas_list_datasets 看。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "要查的东西。用该数据集原文语言里的官方写法通常最准；"
                        "专有名词、报错原文、代码符号原样查往往更准；"
                        "命中不好就换个说法再试一次。"
                    ),
                },
                "dataset_id": _DATASET_PROPERTY,
                "token_budget": {
                    "type": "integer",
                    "description": "上下文预算硬上限。简单问题 1500，默认 3000，需要通读用 6000。",
                    "default": 3000,
                },
                "category": {
                    "type": "string",
                    "description": (
                        "限定分类，可选。每个数据集的分类不一样，"
                        "用 docatlas_list_datasets 查；写错会返回该库的合法取值。"
                    ),
                },
                "no_fetch": {
                    "type": "boolean",
                    "description": "禁止联网补抓，只用本地已有内容。",
                    "default": False,
                },
                "fetch_limit": {
                    "type": "integer",
                    "description": "需要补抓时最多取几页。默认 5，够用；调大只会更慢。",
                    "default": DEFAULT_FETCH_LIMIT,
                },
                "version_target": {
                    "type": "string",
                    "description": (
                        "用户认定的目标版本，用该库自己的写法（C++20、3.4）。"
                        "版本意图由你判断后填进来，DocAtlas 不会从问句里猜。"
                        "该库支持哪种版本写法见 docatlas_list_datasets 的 "
                        "version_vocabulary。"
                    ),
                },
                "version_mode": {
                    "type": "string",
                    "enum": list(versions.MODES),
                    "description": (
                        "strict=用户明确限定该版本，排除那一版里还不存在的内容；"
                        "migration=用户在追问「以前是什么、后来换成了谁」，"
                        "写明版本差异的内容会被提前，旧版本内容是答案而不是噪音；"
                        "compare=要对照多个版本，一条都不删并标出各自适用范围；"
                        "any=不限定。只给 version_target 时按 strict 处理。"
                    ),
                },
                "format": _FORMAT_PROPERTY,
            },
            "required": ["query"],
        },
    },
    {
        "name": "docatlas_search",
        "description": (
            "只列标题、知识类型、匹配方式、得分和出处，不返回正文。"
            "用在'不确定该看哪条，先扫一眼目录'的时候。要答案请用 docatlas_ask。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词。"},
                "dataset_id": _DATASET_PROPERTY,
                "limit": {"type": "integer", "description": "最多几条", "default": 10},
                "category": {"type": "string", "description": "限定分类"},
                "format": _FORMAT_PROPERTY,
            },
            "required": ["query"],
        },
    },
    {
        "name": "docatlas_show",
        "description": (
            "按知识 ID 展开一条的完整正文。ID 来自 docatlas_ask / docatlas_search "
            "结果里的 K<数字>。编号只在同一个库、同一次建库内有效。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "知识 ID，例如 K9290 或 9290",
                },
                "dataset_id": _DATASET_PROPERTY,
            },
            "required": ["chunk_id"],
        },
    },
    {
        "name": "docatlas_related",
        "description": (
            "查一个名称或知识 ID 的交叉关系：它属于什么、对应哪个接口、"
            "作用在什么类型上。每条都带方向、证据类型、置信度、备注和出处 URL。"
            "先用 docatlas_ask 拿正文，再用这个把相关的东西串起来。"
            "查不到时会明确区分：没有这个实体、编号不存在、实体存在但没有关系、"
            "关系目标还没抓、关系目标不在清单里。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "实体名称，或 docatlas_ask 结果里的 K<数字>",
                },
                "dataset_id": _DATASET_PROPERTY,
                "format": _FORMAT_PROPERTY,
            },
            "required": ["subject"],
        },
    },
    {
        "name": "docatlas_list_datasets",
        "description": (
            "列出本机所有文档库，以及每个库的产品、版本、语言、分类、"
            "会产出哪些关系类型和证据类型、数据建到什么程度。"
            "回答前想确认查哪个库、库里有没有这类内容时用它。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": "只看某一个库；不填就全列。",
                },
                "format": _FORMAT_PROPERTY,
            },
        },
    },
]


def _workspace(arguments: dict[str, Any]) -> runtime.Workspace:
    """按 dataset_id 选库；不给就用启动时那个。"""
    dataset_id = str(arguments.get("dataset_id") or "").strip()
    if not dataset_id:
        return runtime.active()
    try:
        return runtime.workspace(dataset_id)
    except SystemExit as exc:
        # load_dataset 用 SystemExit 报配置错——那是命令行的活法。放进服务器
        # 里不接住的话，一个拼错的 dataset_id 会让整个 MCP 进程退出：
        # SystemExit 继承 BaseException，普通的 except Exception 抓不到它。
        raise ToolError(str(exc)) from exc


def _open(workspace: runtime.Workspace):
    """打开这个库；还没建过就直接说清楚下一步，不要造一个空库出来。"""
    if not workspace.db_path.exists():
        raise ToolError(
            f"数据集 {workspace.id}（{workspace.name}）还没有数据。\n"
            f"先枚举一次全站清单（只读站点地图、不抓正文）：\n"
            f"    $env:DOCATLAS_DATASET='{workspace.id}'\n"
            f"    python -m docatlas crawl --discovery-only"
        )
    connection = connect_db(workspace.db_path)
    initialize_db(connection)
    return connection


def _check_category(workspace: runtime.Workspace, category: str | None) -> None:
    if category and category not in workspace.dataset.categories:
        raise ToolError(
            f"{workspace.id} 没有分类 {category!r}。"
            f"可选：{'、'.join(sorted(workspace.dataset.categories)) or '（没有声明分类）'}"
        )


def _identity(workspace: runtime.Workspace) -> dict[str, Any]:
    return {
        "dataset_id": workspace.id,
        "name": workspace.name,
        "product": workspace.dataset.product,
        "version": workspace.version,
        "language": workspace.language,
    }


def tool_ask(arguments: dict[str, Any]) -> Any:
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ToolError("需要一个查询内容。")
    workspace = _workspace(arguments)
    category = arguments.get("category")
    _check_category(workspace, category)
    with runtime.use(workspace):
        # 版本意图要在这个库的上下文里解析：什么算合法版本写法由数据集说了算。
        try:
            version_intent = versions.parse_intent(
                arguments.get("version_mode"), arguments.get("version_target")
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        connection = _open(workspace)
        REQUEST_LIMITER.configure(0)
        try:
            # 和命令行调的是同一个 answer()，两边不可能给出不一样的答案。
            # quiet=True 是因为 stdout 被协议占着，一个字都不能往那儿打。
            pack = answer(
                connection,
                query,
                token_budget=int(arguments.get("token_budget") or 3000),
                category=category,
                allow_fetch=not arguments.get("no_fetch"),
                fetch_limit=int(arguments.get("fetch_limit") or DEFAULT_FETCH_LIMIT),
                quiet=True,
                version_intent=version_intent,
            )
            if arguments.get("format") != "json":
                return render_context_markdown(pack)
            return _structured_ask(workspace, pack)
        finally:
            connection.close()


def _structured_ask(
    workspace: runtime.Workspace, pack: dict[str, Any]
) -> dict[str, Any]:
    lookup = pack.get("lookup") or {}
    # 四种"没有"，四种下一步。压成一个 no_match，调用方就只能靠猜。
    if pack["primary_knowledge"]:
        status = "ok"
    elif lookup.get("pending_pages"):
        status = "pages_not_fetched"
    elif lookup.get("weak_candidates"):
        status = "candidates_too_weak"
    elif lookup.get("linked_targets"):
        status = "target_outside_inventory"
    elif script_mismatch(pack["query"], workspace.language):
        status = "language_mismatch"
    else:
        status = "no_match"
    result = {
        "contract_version": CONTRACT_VERSION,
        "dataset": _identity(workspace),
        "query": pack["query"],
        "status": status,
        "token_budget": pack["token_budget"],
        "estimated_tokens": pack["estimated_tokens"],
        "knowledge": [
            {
                "knowledge_id": f"K{item['id']}",
                "title": item["page_title"],
                "heading_path": item["heading_path"],
                "category": item["category"],
                "knowledge_type": item["knowledge_type"],
                "tokens": item["token_estimate"],
                "source_url": item["source_url"],
                "content_md": item["content_md"],
                # 只在文档真的写了适用版本时才有这个字段。没有 ≠ 适用于所有
                # 版本，只是这一段没写——不要替它下结论。
                **(
                    {"applies_to": item["applies_to"]}
                    if item.get("applies_to")
                    else {}
                ),
            }
            for item in pack["primary_knowledge"]
        ],
        "relations": [
            {
                "relation_type": item["relation_type"],
                "related_name": item["canonical_name"],
                "related_type": item["entity_type"],
                "evidence_kind": item["evidence_kind"],
                "confidence": item["confidence"],
                "note": item["note"],
                "evidence_url": item["source_url"],
                "expand_knowledge_id": (
                    f"K{item['expand_chunk_id']}" if item["expand_chunk_id"] else None
                ),
            }
            for item in pack["one_hop_relations"]
        ],
    }
    if fetch := pack.get("on_demand_fetch"):
        result["fetch"] = {
            "requested": fetch["requested"],
            "succeeded": fetch["succeeded"],
            "failed": fetch["failed"],
        }
    # 把版本条件原样回给调用方：它据此才能判断"没看到某条内容"是被版本挡了、
    # 还是本来就没有。悄悄筛过而不说，是最难排查的一种错。
    if applied := pack.get("version_intent"):
        result["version_intent"] = {
            **applied,
            "explanation": versions.describe(applied),
        }
    if status != "ok":
        result["next_steps"] = describe_lookup(lookup) if lookup else []
    return result


def tool_search(arguments: dict[str, Any]) -> Any:
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ToolError("需要一个查询内容。")
    workspace = _workspace(arguments)
    category = arguments.get("category")
    _check_category(workspace, category)
    with runtime.use(workspace):
        connection = _open(workspace)
        try:
            rows = search_docs(
                connection,
                query,
                limit=int(arguments.get("limit") or 10),
                category=category,
            )
            if arguments.get("format") == "json":
                lookup = (
                    inventory_lookup(connection, query, category=category)
                    if not rows
                    else {}
                )
                return {
                    "contract_version": CONTRACT_VERSION,
                    "dataset": _identity(workspace),
                    "query": query,
                    "status": "ok" if rows else "no_match",
                    "results": [
                        {
                            "knowledge_id": f"K{row['id']}",
                            "title": row["page_title"],
                            "heading_path": row["heading_path"],
                            "category": row["category"],
                            "knowledge_type": row["knowledge_type"],
                            "match_stage": row["match_stage"],
                            "score": row["score"],
                            "snippet": row["snippet"],
                            "source_url": row["source_url"],
                        }
                        for row in rows
                    ],
                    "next_steps": describe_lookup(lookup) if lookup else [],
                }
            if not rows:
                return "\n".join(
                    describe_lookup(
                        inventory_lookup(connection, query, category=category)
                    )
                )
            labels = workspace.category_labels
            lines = []
            for index, row in enumerate(rows, 1):
                label = labels.get(row["category"], row["category"])
                lines.append(
                    f"[{index}] K{row['id']} | {row['page_title']} — {row['heading_path']}\n"
                    f"    分类：{label}　类型：{row['knowledge_type']}　"
                    f"匹配：{row['match_stage']}　得分：{row['score']}\n"
                    f"    {row['snippet']}\n"
                    f"    DOC 原出处：{row['source_url']}"
                )
            lines.extend(exact_page_hint(connection, query, category))
            return "\n".join(lines)
        finally:
            connection.close()


def tool_show(arguments: dict[str, Any]) -> Any:
    raw_id = str(arguments.get("chunk_id") or "").strip()
    digits = raw_id[1:] if raw_id[:1].casefold() == "k" else raw_id
    if not digits.isdigit():
        raise ToolError(f"看不懂的知识 ID：{raw_id!r}（应该长这样：K9290）")
    workspace = _workspace(arguments)
    with runtime.use(workspace):
        connection = _open(workspace)
        try:
            row = connection.execute(
                "SELECT content_md FROM chunks WHERE id=?", (int(digits),)
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT content_md FROM sections WHERE id=?", (int(digits),)
                ).fetchone()
            if row is None:
                raise ToolError(
                    f"{workspace.id} 里没有知识 ID {raw_id}。编号只在同一个库、"
                    "同一次建库内有效——库重跑过一次就会变，别复用旧结果里的编号。"
                    "先用 docatlas_search 拿一个当前有效的。"
                )
            return row["content_md"]
        finally:
            connection.close()


def tool_related(arguments: dict[str, Any]) -> Any:
    subject = str(arguments.get("subject") or "").strip()
    if not subject:
        raise ToolError("需要一个实体名称或知识 ID。")
    workspace = _workspace(arguments)
    with runtime.use(workspace):
        connection = _open(workspace)
        try:
            result = related_payload(connection, subject)
            if arguments.get("format") == "json":
                return {
                    "contract_version": CONTRACT_VERSION,
                    "dataset": _identity(workspace),
                    **result,
                }
            return _render_related(workspace, result)
        finally:
            connection.close()


def _render_related(workspace: runtime.Workspace, result: dict[str, Any]) -> str:
    lines = [f"状态：{result['status']}"]
    # 即使没有关系也要把找到的实体列出来——"没找到这个东西"和
    # "找到了但它没连着别的东西"对调用方是两件事。
    for item in result["entities"]:
        entity = item["entity"]
        lines.append("")
        lines.append(f"## {entity['name']}（{entity['type']}）")
        lines.append(f"   {entity['source_url']}")
        for relation in item["relations"]:
            kind = workspace.relation_labels.get(
                relation["relation_type"], relation["relation_type"]
            )
            evidence = workspace.evidence_labels.get(
                relation["evidence_kind"], relation["evidence_kind"]
            )
            lines.append(
                f"   - {relation['relation_type']}（{kind}）"
                f" → {relation['related_name']}"
                f"（{relation['related_type']}，{relation['direction']}）"
                f"　依据：{evidence}"
                f"　置信度 {relation['confidence']:.2f}"
            )
            lines.append(f"     出处：{relation['evidence_url']}")
            if relation["note"]:
                lines.append(f"     备注：{relation['note']}")
    if result["next_steps"]:
        lines.extend(["", *result["next_steps"]])
    return "\n".join(lines)


def _dataset_report(workspace: runtime.Workspace) -> dict[str, Any]:
    """一个库的身份、能力和建库进度。

    "能力"是数据集自己声明的，不是这里写死的：分类来自配置，关系类型和证据
    类型来自领域知识包。没挂知识包也有通用的官方链接关系。
    """
    report: dict[str, Any] = {
        **_identity(workspace),
        "categories": {
            key: workspace.category_labels.get(key, key)
            for key in sorted(workspace.dataset.categories)
        },
        "knowledge_pack": workspace.dataset.knowledge,
        "source_adapter": workspace.dataset.source,
        "relation_types": sorted(workspace.relation_labels),
        "evidence_kinds": sorted(
            {"official_link", *workspace.hook("DERIVED_EVIDENCE_KINDS", ())}
        ),
        "triggers": list(workspace.dataset.skill_triggers),
    }
    # 能不能按版本限定、该用什么写法，必须能被发现，否则调用方只能瞎猜一个
    # version_target 然后得到"这个库不认识这个版本"。
    with runtime.use(workspace):
        if versions.supported():
            report["version_vocabulary"] = versions.vocabulary() or workspace.version
            report["version_modes"] = list(versions.MODES)
    if not workspace.db_path.exists():
        report["state"] = "not_built"
        return report
    connection = connect_db(workspace.db_path)
    try:
        counted = dict(
            connection.execute(
                "SELECT"
                " (SELECT COUNT(*) FROM pages) AS pages,"
                " (SELECT COUNT(*) FROM pages WHERE status='success') AS fetched,"
                " (SELECT COUNT(*) FROM chunks) AS knowledge,"
                " (SELECT COUNT(*) FROM relations) AS relations"
            ).fetchone()
        )
    except Exception:  # noqa: BLE001  半成品的库不该让整份清单查不出来
        report["state"] = "unreadable"
        return report
    finally:
        connection.close()
    report["state"] = "ready" if counted["knowledge"] else "inventory_only"
    report["counts"] = counted
    return report


def tool_list_datasets(arguments: dict[str, Any]) -> Any:
    only = str(arguments.get("dataset_id") or "").strip()
    ids = [only] if only else runtime.available_dataset_ids()
    reports = []
    for dataset_id in ids:
        try:
            reports.append(_dataset_report(runtime.workspace(dataset_id)))
        except SystemExit as exc:
            if only:
                raise ToolError(str(exc)) from exc
            reports.append({"dataset_id": dataset_id, "state": "broken_config"})
    if arguments.get("format") == "json":
        return {
            "contract_version": CONTRACT_VERSION,
            "default_dataset_id": runtime.active().id,
            "datasets": reports,
        }
    default = runtime.active().id
    lines = [f"默认数据集：{default}（不填 dataset_id 时用它）", ""]
    for report in reports:
        marker = "→" if report["dataset_id"] == default else " "
        state = {
            "ready": "可以查",
            "inventory_only": "只有清单，正文尚未抓取",
            "not_built": "还没有数据",
            "unreadable": "数据库读不出来",
            "broken_config": "配置有问题",
        }.get(report.get("state", ""), report.get("state", ""))
        lines.append(
            f"{marker} {report['dataset_id']}"
            + (
                f" — {report.get('name')}（{report.get('product')} "
                f"{report.get('version')}，原文 {report.get('language')}）"
                if report.get("name")
                else ""
            )
        )
        lines.append(f"    状态：{state}")
        if counts := report.get("counts"):
            lines.append(
                f"    清单 {counts['pages']:,} 页，已抓 {counts['fetched']:,} 页，"
                f"知识块 {counts['knowledge']:,}，关系 {counts['relations']:,}"
            )
        if categories := report.get("categories"):
            lines.append("    分类：" + "、".join(categories))
        if report.get("relation_types"):
            lines.append("    关系类型：" + "、".join(report["relation_types"]))
        lines.append("")
    return "\n".join(lines).rstrip()


HANDLERS = {
    "docatlas_ask": tool_ask,
    "docatlas_search": tool_search,
    "docatlas_show": tool_show,
    "docatlas_related": tool_related,
    "docatlas_list_datasets": tool_list_datasets,
}


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    """工具返回值 → MCP 结果。

    字符串直接当文本回；结构化结果同时给 `structuredContent` 和一份 JSON 文本，
    因为按协议要求，不认识 `structuredContent` 的客户端要能从文本里读到同样
    的内容。默认返回 Markdown 正是为了不让每次查询都付这份 JSON 的 token。
    """
    if isinstance(value, str):
        return {"content": [{"type": "text", "text": value}], "isError": is_error}
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": value,
        "isError": is_error,
    }


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """返回要回给客户端的响应；通知类消息返回 None。"""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        # 回声客户端声明的协议版本：我们用到的三个方法各版本都稳定，
        # 硬报一个版本反而会让老客户端直接拒绝握手。
        requested = (message.get("params") or {}).get("protocolVersion")
        return _result(
            request_id,
            {
                "protocolVersion": requested or DEFAULT_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"没有这个工具：{name}")
        try:
            value = handler(params.get("arguments") or {})
        except ToolError as exc:
            # 调用方能改的错：把话说清楚就行，不要甩堆栈。
            return _result(request_id, _tool_result(str(exc), is_error=True))
        except Exception:  # 程序 bug 也要作为结果回去，不能把整个连接搞崩
            return _result(
                request_id,
                _tool_result("查询失败：\n" + traceback.format_exc(), is_error=True),
            )
        return _result(request_id, _tool_result(value))

    if method == "ping":
        return _result(request_id, {})

    if request_id is None:
        return None
    return _error(request_id, -32601, f"不支持的方法：{method}")


def serve(stdin=None, stdout=None) -> int:
    """在 stdin/stdout 上跑 MCP。

    协议独占 stdout，所以任何日志都必须走 stderr——往 stdout 打一个字
    都会让客户端解析失败。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    catalogue = "、".join(key for key, _ in _dataset_catalogue())
    print(
        f"[docatlas] MCP 服务器已启动。默认数据集 {runtime.active().id}；"
        f"可查：{catalogue}",
        file=sys.stderr,
        flush=True,
    )
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0
