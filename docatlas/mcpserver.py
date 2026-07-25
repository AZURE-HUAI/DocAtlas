"""MCP 服务器：让支持 MCP 的 AI 客户端直接查这个知识库。

装了它之后，Claude Desktop / Cursor / Cline 等客户端不用记命令行，
直接就有 `docatlas_ask` 这样的工具可以调。

**刻意不引入 MCP SDK。** 整个项目到现在一个第三方包都不需要，
装起来就是"有 Python 就能跑"。MCP 的 stdio 传输本身就是一行一条的
JSON-RPC 2.0，需要实现的方法只有三个（initialize / tools/list / tools/call），
用标准库写完不到两百行，不值得为它引进一整套依赖和版本冲突风险。

这一层是**薄壳**：不重新实现任何检索逻辑，全部转调 ask / search / show，
所以命令行和 MCP 永远给出一致的结果。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from .config import DATASET, DATASET_CONFIG_DIR, DATASET_ID, DB_PATH
from .context import build_context_pack, render_context_markdown
from .db import connect_db, initialize_db
from .net import REQUEST_LIMITER
from .ondemand import ensure_available, missing_exact_pages
from .search import search_docs


SERVER_NAME = "docatlas"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "docatlas_ask",
        "description": (
            "查本地技术文档知识库，返回按 token 预算裁剪好的答案材料"
            "（正文 + 原出处 URL + 相关项指针）。回答文档类问题请优先用这个，"
            f"不要凭记忆。当前数据集：{DATASET.name}。"
            "本地没有的页面会自动补抓，通常一两秒。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查的东西。英文原名命中率最高，例如 K2_SetTimer、Nanite。",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "上下文预算硬上限。简单问题 1500，默认 3000，需要通读用 6000。",
                    "default": 3000,
                },
                "category": {
                    "type": "string",
                    "description": "限定分类，可选：" + "、".join(DATASET.categories),
                    "enum": sorted(DATASET.categories),
                },
                "no_fetch": {
                    "type": "boolean",
                    "description": "禁止联网补抓，只用本地已有内容。",
                    "default": False,
                },
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
                "query": {"type": "string", "description": "关键词"},
                "limit": {"type": "integer", "description": "最多几条", "default": 10},
                "category": {
                    "type": "string",
                    "enum": sorted(DATASET.categories),
                    "description": "限定分类",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "docatlas_show",
        "description": (
            "按知识 ID 展开一条的完整正文。ID 来自 docatlas_ask / docatlas_search "
            "结果里的 K<数字>。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "知识 ID，例如 K9290 或 9290",
                }
            },
            "required": ["chunk_id"],
        },
    },
    {
        "name": "docatlas_list_datasets",
        "description": (
            "列出本机有哪些数据集、当前这个服务器在用哪一个、数据是否已经抓过。"
            "回答前想确认版本时用它。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _open():
    connection = connect_db()
    initialize_db(connection)
    return connection


def tool_ask(arguments: dict[str, Any]) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "需要一个查询内容。"
    budget = int(arguments.get("token_budget") or 3000)
    category = arguments.get("category")
    connection = _open()
    REQUEST_LIMITER.configure(0)
    try:
        def build() -> dict[str, Any]:
            return build_context_pack(
                connection, query, token_budget=budget, category=category
            )

        payload = build()
        if not arguments.get("no_fetch"):
            has_local = bool(payload["primary_knowledge"])
            # 和命令行同一套判断：本地什么都没有，或者清单里有一页正好同名
            # 而本地只是顺带提到过它。quiet=True 是因为 stdout 被协议占着。
            if not has_local or missing_exact_pages(connection, query, category):
                fetched = ensure_available(
                    connection,
                    query,
                    limit=int(arguments.get("fetch_limit") or 5),
                    category=category,
                    quiet=True,
                    exact_only=has_local,
                )
                if fetched["succeeded"]:
                    payload = build()
                    payload["on_demand_fetch"] = fetched
        return render_context_markdown(payload)
    finally:
        connection.close()


def tool_search(arguments: dict[str, Any]) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "需要一个查询内容。"
    connection = _open()
    try:
        rows = search_docs(
            connection,
            query,
            limit=int(arguments.get("limit") or 10),
            category=arguments.get("category"),
        )
        if not rows:
            return "没有找到结果。英文原文库建议优先使用英文关键词。"
        lines = []
        for index, row in enumerate(rows, 1):
            label = DATASET.category_labels.get(row["category"], row["category"])
            lines.append(
                f"[{index}] K{row['id']} | {row['page_title']} — {row['heading_path']}\n"
                f"    分类：{label}　类型：{row['knowledge_type']}　"
                f"匹配：{row['match_stage']}　得分：{row['score']}\n"
                f"    {row['snippet']}\n"
                f"    DOC 原出处：{row['source_url']}"
            )
        return "\n".join(lines)
    finally:
        connection.close()


def tool_show(arguments: dict[str, Any]) -> str:
    raw_id = str(arguments.get("chunk_id") or "").strip()
    digits = raw_id[1:] if raw_id[:1].casefold() == "k" else raw_id
    if not digits.isdigit():
        return f"看不懂的知识 ID：{raw_id!r}（应该长这样：K9290）"
    connection = _open()
    try:
        row = connection.execute(
            "SELECT content_md FROM chunks WHERE id=?", (int(digits),)
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT content_md FROM sections WHERE id=?", (int(digits),)
            ).fetchone()
        return row["content_md"] if row else f"没有找到知识 ID {raw_id}。"
    finally:
        connection.close()


def tool_list_datasets(_arguments: dict[str, Any]) -> str:
    available = sorted(path.stem for path in DATASET_CONFIG_DIR.glob("*.toml"))
    lines = [
        f"当前数据集：{DATASET_ID}（{DATASET.name}，版本 {DATASET.version}）",
        f"数据库：{DB_PATH}{'' if DB_PATH.exists() else '（还没有数据）'}",
        "",
        "本机配置了这些数据集：" + ("、".join(available) or "（一个都没有）"),
        "",
        "一个 MCP 服务器只服务一个数据集。要同时查多个版本，就在客户端里配多个"
        "服务器条目，各自设不同的 DOCATLAS_DATASET 环境变量。",
    ]
    return "\n".join(lines)


HANDLERS = {
    "docatlas_ask": tool_ask,
    "docatlas_search": tool_search,
    "docatlas_show": tool_show,
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
            text = handler(params.get("arguments") or {})
        except Exception:  # 工具出错要作为结果回去，不能把整个连接搞崩
            return _result(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": "查询失败：\n" + traceback.format_exc()}
                    ],
                    "isError": True,
                },
            )
        return _result(request_id, {"content": [{"type": "text", "text": text}]})

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
    print(
        f"[docatlas] MCP 服务器已启动，数据集 {DATASET_ID}",
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
