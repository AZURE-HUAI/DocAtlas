"""命令行入口。"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from .config import (
    CATEGORY_LABELS,
    CATEGORY_PATTERNS,
    DATA_DIR,
    DATA_ROOT,
    DATASET,
    DATASET_ID,
    DB_PATH,
    LANGUAGE,
    REPO_ROOT,
)
from .runtime import bind
from .util import log, set_log_file
from .net import REQUEST_LIMITER
from .db import connect_db, initialize_db
from .discover import discover_inventory
from .documents import fetch_document
from .store import store_document_result
from .crawl import crawl_documents, reprocess_stored_documents
from .assets import download_assets
from .relations import build_cross_index, link_new_pages
from .search import search_docs
from .export import export_markdown
from .reports import write_reports, write_site_inventory
from .context import (
    answer,
    build_context_pack,
    describe_lookup,
    exact_page_hint,
    related_payload,
    render_context_markdown,
)
from .ondemand import (
    DEFAULT_FETCH_LIMIT,
    fetch_now,
    find_uncrawled_candidates,
    inventory_lookup,
)
from .validate import validate_contract


def require_inventory(connection: sqlite3.Connection) -> None:
    """库是空的就直接说清楚下一步做什么，而不是给一个空结果让人猜。"""
    if connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]:
        return
    raise SystemExit(
        f"数据集 {DATASET_ID} 还没有页面清单（数据目录：{DATA_DIR}）。\n"
        "先枚举一次全站清单，只读站点地图、不抓正文：\n"
        "    python -m docatlas crawl --discovery-only\n"
        "枚举完就能直接查了——查到本地没有的页面会当场补抓，"
        "不用先把全站下载下来。"
    )


def print_stats(stats: dict[str, Any]) -> None:
    print(json.dumps(stats, ensure_ascii=False, indent=2))




def command_crawl(args: argparse.Namespace) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    set_log_file(args.log_file)
    REQUEST_LIMITER.configure(args.requests_per_second)
    connection = connect_db()
    initialize_db(connection)
    if not args.skip_discovery:
        discover_inventory(
            connection,
            workers=args.sitemap_workers,
            refresh=args.refresh_sitemaps,
        )
    else:
        existing = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        if not existing:
            raise SystemExit("数据库还没有页面清单，不能使用 --skip-discovery")
        inventory_row = connection.execute(
            "SELECT value FROM metadata WHERE key='inventory_status'"
        ).fetchone()
        if not inventory_row or inventory_row[0] != "complete":
            raise SystemExit(
                "完整页面清单尚未冻结为 complete，拒绝启动正文阶段。"
                "请先运行 inventory 并确认失败站点地图为 0。"
            )
    if args.discovery_only:
        inventory = write_site_inventory(connection)
        print(json.dumps(inventory, ensure_ascii=False, indent=2))
        stats = write_reports(connection)
        print_stats(stats)
        connection.close()
        return 0
    if not args.skip_discovery:
        inventory = write_site_inventory(connection)
        if inventory["status"] != "complete":
            raise SystemExit(
                "页面清单仍不完整，正文阶段未启动。"
                "请稍后重跑 discover 阶段补齐失败站点地图。"
            )
    crawl_documents(
        connection,
        workers=args.workers,
        delay=args.delay,
        max_pages=args.max_pages,
        sample_per_category=args.sample_per_category,
        refresh=args.refresh_pages,
        category=args.category,
    )
    relation_stats = build_cross_index(connection)
    log(
        "交叉索引："
        f"实体 {relation_stats['entities']:,}；"
        f"关系 {relation_stats['relations']:,}"
    )
    if args.download_assets:
        download_assets(
            connection,
            workers=args.asset_workers,
            max_assets=args.max_assets,
        )
    stats = write_reports(connection, manifest=True)
    if args.export:
        export_markdown(connection, shard_mb=args.shard_mb)
        stats = write_reports(connection, manifest=True)
    print_stats(stats)
    connection.close()
    return 0


def command_assets(args: argparse.Namespace) -> int:
    REQUEST_LIMITER.configure(args.requests_per_second)
    connection = connect_db()
    initialize_db(connection)
    download_assets(
        connection, workers=args.workers, max_assets=args.max_assets
    )
    print_stats(write_reports(connection))
    connection.close()
    return 0


def command_reprocess(args: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    processed = reprocess_stored_documents(
        connection, limit=args.limit, force=args.force
    )
    stats = write_reports(connection)
    print(
        json.dumps(
            {"reprocessed_pages": processed, "stats": stats},
            ensure_ascii=False,
            indent=2,
        )
    )
    connection.close()
    return 0


def command_fetch_pages(args: argparse.Namespace) -> int:
    REQUEST_LIMITER.configure(args.requests_per_second)
    connection = connect_db()
    initialize_db(connection)
    inventory_row = connection.execute(
        "SELECT value FROM metadata WHERE key='inventory_status'"
    ).fetchone()
    if not inventory_row or inventory_row[0] != "complete":
        raise SystemExit("页面清单未达到 complete，拒绝抓取定向正文。")
    placeholders = ",".join("?" for _ in args.page_ids)
    rows = list(
        connection.execute(
            f"""
            SELECT id, url, path, category FROM pages
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            args.page_ids,
        )
    )
    if len(rows) != len(set(args.page_ids)):
        found = {row["id"] for row in rows}
        missing = sorted(set(args.page_ids) - found)
        raise SystemExit(f"找不到页面 ID：{missing}")
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.workers
    ) as executor:
        future_to_row = {
            executor.submit(bind(fetch_document), row, 0.05): row for row in rows
        }
        for future in concurrent.futures.as_completed(future_to_row):
            row = future_to_row[future]
            result = future.result()
            store_document_result(connection, result, row["category"])
            connection.commit()
            log(
                f"定向页面 {row['id']}："
                f"{'成功' if result['ok'] else result['error']}"
            )
    graph = build_cross_index(connection)
    print(json.dumps(graph, ensure_ascii=False, indent=2))
    connection.close()
    return 0


def command_export(args: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    export_markdown(connection, shard_mb=args.shard_mb)
    print_stats(write_reports(connection))
    connection.close()
    return 0


def command_search(args: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    rows = search_docs(
        connection, args.query, limit=args.limit, category=args.category
    )
    hint = exact_page_hint(connection, args.query, args.category)
    if args.json:
        # 空结果和非空结果用同一种形状：调用方不该为了"这次有没有命中"
        # 写两套解析。空的时候多给一个 lookup 说明是哪一种"没有"。
        payload: dict[str, Any] = {"results": [dict(row) for row in rows]}
        if hint:
            payload["exact_page_pending"] = True
        if not rows:
            payload["lookup"] = inventory_lookup(
                connection, args.query, category=args.category
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        connection.close()
        return 0 if rows else 1
    if not rows:
        for line in describe_lookup(
            inventory_lookup(connection, args.query, category=args.category)
        ):
            print(line)
        connection.close()
        return 1
    for index, row in enumerate(rows, 1):
        label = CATEGORY_LABELS.get(row["category"], row["category"])
        print(
            f"\n[{index}] 知识 ID K{row['id']} | "
            f"{row['page_title']} — {row['heading_path']}"
        )
        print(f"分类：{label}　匹配：{row['match_stage']}　得分：{row['score']}")
        print(f"知识类型：{row['knowledge_type']}")
        print(f"摘要：{row['snippet']}")
        print(f"DOC 原出处：{row['source_url']}")
    for line in hint:
        print(f"\n{line}" if line.startswith("提示") else line)
    connection.close()
    return 0


def command_show(args: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    raw_id = str(args.section_id)
    numeric_id = int(raw_id[1:] if raw_id[:1].casefold() == "k" else raw_id)
    row = connection.execute(
        """
        SELECT c.id, p.title AS page_title, p.category, c.heading_path,
               c.content_md, c.source_anchor AS source_url,
               c.knowledge_type, c.context_prefix
        FROM chunks c JOIN pages p ON p.id=c.page_id
        WHERE c.id=?
        """,
        (numeric_id,),
    ).fetchone()
    if not row:
        row = connection.execute(
        """
        SELECT s.id, p.title AS page_title, p.category, s.heading_path,
               s.content_md, s.source_url, s.knowledge_type,
               '' AS context_prefix
        FROM sections s JOIN pages p ON p.id=s.page_id
        WHERE s.id=?
        """,
            (numeric_id,),
        ).fetchone()
    if not row:
        print(f"没有找到知识 ID {args.section_id}。")
        connection.close()
        return 1
    if args.json:
        print(json.dumps(dict(row), ensure_ascii=False, indent=2))
    else:
        print(row["content_md"])
    connection.close()
    return 0


def command_context(args: argparse.Namespace) -> int:
    """机器可读的上下文包（JSON）。给程序用。"""
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    payload = build_context_pack(
        connection,
        args.query,
        token_budget=args.token_budget,
        category=args.category,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    connection.close()
    return 0


def command_ask(args: argparse.Namespace) -> int:
    """一条命令拿到"直接能读的答案材料"。AI 和人都用这个。

    本地没有时会自动补抓那几页——不需要提前把全站抓下来。
    """
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    REQUEST_LIMITER.configure(0)
    payload = answer(
        connection,
        args.query,
        token_budget=args.token_budget,
        category=args.category,
        allow_fetch=not args.no_fetch,
        fetch_limit=args.fetch_limit,
        quiet=args.json,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_context_markdown(payload))
    connection.close()
    return 0 if payload["primary_knowledge"] else 1


def command_get(args: argparse.Namespace) -> int:
    """按需抓取：只把用得上的那几页取回本地。"""
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    REQUEST_LIMITER.configure(0)
    candidates = find_uncrawled_candidates(
        connection, args.query, limit=args.limit, category=args.category
    )
    if not candidates:
        lookup = inventory_lookup(connection, args.query, category=args.category)
        if lookup["crawled_pages"]:
            print(f'这一页本地已经有了，直接查即可：ask "{args.query}"')
            for page in lookup["crawled_pages"]:
                print(f"  {page['path']}")
        else:
            for line in describe_lookup(lookup):
                print(line)
        connection.close()
        return 1
    print(f"准备抓取 {len(candidates)} 页：")
    for row in candidates:
        label = CATEGORY_LABELS.get(row["category"], row["category"])
        print(f"  [{label}] {row['path']}")
    outcome = fetch_now(connection, candidates)
    outcome["relations"] = link_new_pages(
        connection, [page["page_id"] for page in outcome["pages"]]
    )
    print(
        f"\n完成：成功 {outcome['succeeded']}；失败 {outcome['failed']}；"
        f"新建关系 {outcome['relations']}"
    )
    for page in outcome["pages"]:
        print(f"  ✓ {page['title'] or page['path']}")
    if outcome["succeeded"]:
        print(f'\n现在可以查了：ask "{args.query}"')
    connection.close()
    return 0 if outcome["succeeded"] else 1


def command_cross_index(_: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    print(json.dumps(build_cross_index(connection), ensure_ascii=False, indent=2))
    connection.close()
    return 0


def command_related(args: argparse.Namespace) -> int:
    """一跳交叉关系。实现在 context.related_payload，MCP 用的是同一个。"""
    connection = connect_db()
    initialize_db(connection)
    require_inventory(connection)
    result = related_payload(connection, str(args.subject).strip())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    connection.close()
    return 0 if result["status"] == "ok" else 1


def command_inventory(_: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    print(
        json.dumps(
            write_site_inventory(connection),
            ensure_ascii=False,
            indent=2,
        )
    )
    connection.close()
    return 0


def command_validate(args: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    payload = validate_contract(connection, args.phase)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    connection.close()
    return 0 if payload["status"] == "pass" else 1


def command_stats(args: argparse.Namespace) -> int:
    connection = connect_db()
    initialize_db(connection)
    print_stats(write_reports(connection, manifest=getattr(args, "manifest", False)))
    connection.close()
    return 0


def command_mcp(_: argparse.Namespace) -> int:
    """以 MCP 服务器的方式跑，给支持 MCP 的 AI 客户端用。"""
    from .mcpserver import serve  # 只有真要跑 MCP 时才加载

    return serve()


def command_paths(_: argparse.Namespace) -> int:
    """告诉调用方数据在哪。PowerShell 脚本靠它定位日志和数据库，
    这样路径规则只在 config.py 写一次，别处不再各写一遍。"""
    print(
        json.dumps(
            {
                "dataset": DATASET_ID,
                # 原文是什么语言由数据集说了算，不是程序的假设——安装技能时
                # 要把它填进说明里，好让 AI 知道该用哪种语言的词去查。
                "language": LANGUAGE,
                "repo_root": str(REPO_ROOT),
                "data_root": str(DATA_ROOT),
                "data_dir": str(DATA_DIR),
                "database": str(DB_PATH),
                "exists": DB_PATH.exists(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def skill_substitutions() -> dict[str, str]:
    """技能文档里的占位符 → 当前数据集的实际内容。

    技能文档是给 AI 看的操作手册，措辞必须是通用的——"当前装的是什么库"
    由数据集填，不能写死。写死了就等于假定所有人装的都是同一份文档。
    """
    from .mcpserver import TOOLS
    from .runtime import available_dataset_ids
    from .validate import expected_evidence_kinds

    return {
        # 工具名从 MCP 服务器自己那里取：技能手册永远不会写出一个不存在的工具。
        "DOCATLAS_MCP_TOOLS": "、".join(f"`{tool['name']}`" for tool in TOOLS),
        "DOCATLAS_DATASETS": "、".join(f"`{key}`" for key in available_dataset_ids()),
        "DOCATLAS_ROOT": str(REPO_ROOT),
        "DATASET_ID": DATASET_ID,
        "DATASET_NAME": DATASET.name,
        "DATASET_LANGUAGE": LANGUAGE,
        "DATASET_CATEGORIES": "、".join(
            CATEGORY_LABELS.get(key, key) for key in DATASET.categories
        ),
        "DATASET_CATEGORY_IDS": " / ".join(f"`{key}`" for key in DATASET.categories),
        "DATASET_TRIGGERS": "、".join(DATASET.skill_triggers) or "（未配置）",
        "DATASET_EVIDENCE_KINDS": "、".join(
            f"`{kind}`" for kind in expected_evidence_kinds()
        ),
    }


def command_render_skill(args: argparse.Namespace) -> int:
    """把技能模板按当前数据集填好，打到标准输出，安装脚本负责落地。

    填充放在 Python 这边：只有它认识数据集。安装脚本就只管把文件放对地方，
    换个平台重写一个安装脚本也不用把这些知识再抄一遍。
    """
    template = Path(args.template)
    if not template.exists():
        print(f"找不到模板：{template}", file=sys.stderr)
        return 1
    text = template.read_text(encoding="utf-8")
    for name, value in skill_substitutions().items():
        text = text.replace("{{" + name + "}}", value)
    if leftover := sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", text))):
        print(f"{template.name} 里有没人认识的占位符：{'、'.join(leftover)}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"DocAtlas 本地文档知识库（当前数据集：{DATASET_ID}）"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl = subparsers.add_parser("crawl", help="发现并抓取官方文档")
    crawl.add_argument("--workers", type=int, default=8, help="正文并发数")
    crawl.add_argument(
        "--sitemap-workers", type=int, default=2, help="站点地图并发数"
    )
    crawl.add_argument(
        "--asset-workers", type=int, default=8, help="图片并发数"
    )
    crawl.add_argument(
        "--delay", type=float, default=0.0, help="每次成功请求后的礼貌延迟（秒）"
    )
    crawl.add_argument(
        "--requests-per-second",
        type=float,
        default=0.0,
        help="所有线程合计的最大请求速率；0 表示不主动限速（服务器回 429/403 时仍会自动退让）",
    )
    crawl.add_argument(
        "--max-pages", type=int, default=0, help="本轮最多抓取页数；0 表示全部"
    )
    crawl.add_argument(
        "--sample-per-category",
        type=int,
        default=0,
        help="每类只抓取 N 页，用于验收",
    )
    crawl.add_argument(
        "--skip-discovery", action="store_true", help="跳过站点地图发现"
    )
    crawl.add_argument(
        "--discovery-only",
        action="store_true",
        help="只完成全站页面清单，不抓正文",
    )
    crawl.add_argument(
        "--refresh-sitemaps", action="store_true", help="重新读取成功的站点地图"
    )
    crawl.add_argument(
        "--refresh-pages", action="store_true", help="重新读取成功的页面"
    )
    crawl.add_argument(
        "--download-assets", action="store_true", help="下载正文引用图片"
    )
    crawl.add_argument(
        "--max-assets", type=int, default=0, help="本轮最多下载图片数"
    )
    crawl.add_argument(
        "--export", action="store_true", help="抓取后生成 Markdown 分片"
    )
    crawl.add_argument(
        "--shard-mb", type=int, default=8, help="Markdown 分片目标大小"
    )
    crawl.add_argument(
        "--category",
        choices=list(CATEGORY_PATTERNS),
        help="只抓这一类，例如只补齐 node_reference",
    )
    crawl.add_argument(
        "--log-file", help="另存 UTF-8 进度日志"
    )
    crawl.set_defaults(func=command_crawl)

    assets = subparsers.add_parser("assets", help="补下正文引用图片")
    assets.add_argument("--workers", type=int, default=8)
    assets.add_argument("--requests-per-second", type=float, default=0.0)
    assets.add_argument("--max-assets", type=int, default=0)
    assets.set_defaults(func=command_assets)

    reprocess = subparsers.add_parser(
        "reprocess", help="使用本地原始 JSON 重新拆分知识点，不访问网络"
    )
    reprocess.add_argument("--limit", type=int, default=0)
    reprocess.add_argument(
        "--force",
        action="store_true",
        help="连已经是当前切分版本的页面也重做（默认只补没做过的，可断点续传）",
    )
    reprocess.set_defaults(func=command_reprocess)

    fetch_pages = subparsers.add_parser(
        "fetch-pages", help="按页面 ID 定向抓取正文，用于样本验收"
    )
    fetch_pages.add_argument("page_ids", type=int, nargs="+")
    fetch_pages.add_argument("--workers", type=int, default=2)
    fetch_pages.add_argument("--requests-per-second", type=float, default=0.0)
    fetch_pages.set_defaults(func=command_fetch_pages)

    export = subparsers.add_parser("export", help="生成 AI 友好的 Markdown 分片")
    export.add_argument("--shard-mb", type=int, default=8)
    export.set_defaults(func=command_export)

    search = subparsers.add_parser("search", help="全文查询总路由")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--category", choices=list(CATEGORY_PATTERNS))
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    show = subparsers.add_parser("show", help="按知识 ID 读取完整小节")
    show.add_argument("section_id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=command_show)

    ask = subparsers.add_parser(
        "ask",
        help="推荐入口：按 token 预算直接给出可读的答案材料（Markdown）",
    )
    ask.add_argument("query")
    ask.add_argument(
        "--token-budget", type=int, default=3000, help="上下文预算，默认 3000"
    )
    ask.add_argument("--category", choices=list(CATEGORY_PATTERNS))
    ask.add_argument("--json", action="store_true", help="改输出结构化 JSON")
    ask.add_argument(
        "--no-fetch",
        action="store_true",
        help="本地没有也不联网补抓，只用已有内容回答",
    )
    ask.add_argument(
        "--fetch-limit",
        type=int,
        default=DEFAULT_FETCH_LIMIT,
        help=f"自动补抓时最多抓几页，默认 {DEFAULT_FETCH_LIMIT}",
    )
    ask.set_defaults(func=command_ask)

    get = subparsers.add_parser(
        "get", help="按需抓取：只把用得上的那几页取回本地"
    )
    get.add_argument("query", help="页面名称，如 K2_SetTimer、Nanite")
    get.add_argument("--limit", type=int, default=DEFAULT_FETCH_LIMIT)
    get.add_argument("--category", choices=list(CATEGORY_PATTERNS))
    get.set_defaults(func=command_get)

    context_parser = subparsers.add_parser(
        "context", help="同 ask，但固定输出 JSON（给程序调用）"
    )
    context_parser.add_argument("query")
    context_parser.add_argument("--token-budget", type=int, default=3000)
    context_parser.add_argument("--category", choices=list(CATEGORY_PATTERNS))
    context_parser.set_defaults(func=command_context)

    cross_index = subparsers.add_parser(
        "cross-index", help="解析官方链接并建立实体交叉关系"
    )
    cross_index.set_defaults(func=command_cross_index)

    related = subparsers.add_parser(
        "related", help="查询知识点或实体的一跳交叉关系"
    )
    related.add_argument("subject", help="知识 ID（如 K123）或实体名称")
    related.set_defaults(func=command_related)

    inventory = subparsers.add_parser(
        "inventory", help="冻结并校验全站页面清单"
    )
    inventory.set_defaults(func=command_inventory)

    validate = subparsers.add_parser(
        "validate", help="按数据合同验证目录或正文阶段"
    )
    validate.add_argument(
        "--phase", choices=("inventory", "content"), default="inventory"
    )
    validate.set_defaults(func=command_validate)

    stats = subparsers.add_parser("stats", help="输出覆盖率和失败统计")
    stats.add_argument(
        "--manifest",
        action="store_true",
        help="同时重写逐页清单 manifest.jsonl（近 100 MB，默认不写）",
    )
    stats.set_defaults(func=command_stats)

    paths = subparsers.add_parser("paths", help="打印数据集与数据目录的实际位置")
    paths.set_defaults(func=command_paths)

    render = subparsers.add_parser(
        "render-skill", help="把技能模板按当前数据集填好并打印（给安装脚本用）"
    )
    render.add_argument("template", help="模板文件路径")
    render.set_defaults(func=command_render_skill)

    mcp = subparsers.add_parser(
        "mcp", help="以 MCP 服务器方式运行（给 Claude Desktop / Cursor 等客户端用）"
    )
    mcp.set_defaults(func=command_mcp)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        log("收到中止信号；已提交的批次可在下次运行时继续")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
