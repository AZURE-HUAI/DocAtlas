"""回答层：`ask` 和 `related` 的唯一实现。

命令行和 MCP 都调这里，所以两个入口不可能给出不一样的答案——以前它们各写
一套"要不要补抓"的判断，然后慢慢分了岔。

这一层的核心职责是**保护上下文**。检索能找到几十条相关内容，但把它们全塞给
AI 只会让真正的答案被淹没。所以上下文包做四件事：

1. **硬预算**：累计 token 超过预算就停，绝不"最后一条超一点没关系"。
2. **同页限额**：一个页面最多贡献 2 块，避免一篇长文吃掉整个预算。
3. **去重**：内容哈希相同的块只保留一份（文档站大量复制粘贴）。
4. **一跳关系只给指针**：相关项只给名称、依据、置信度和一条可展开的命令，
   不直接把正文塞进来——需要时再展开。

另外两件事同样属于"回答"，所以也在这里：

- `answer()` 决定要不要去清单里补抓。判断依据不是"本地有没有结果"，而是
  **"有没有一条结果的页面标题就是用户问的名字"**——否则几条沾边的本地块
  会一直把真正的目标页挡在门外。
- 查不到时的**诊断**（`describe_lookup` / `exact_page_hint`）。空结果本身
  不含信息量：官方没有这一页、清单里有但没抓、名字写得对不上，三件事的
  下一步完全不同，必须说清楚是哪一种。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .chunking import normalize_name
from .ondemand import (
    DEFAULT_FETCH_LIMIT,
    ensure_available,
    inventory_lookup,
    missing_exact_pages,
)
from .relations import page_link_status
from .runtime import active
from .search import query_names, search_chunks
from .text import SCRIPT_NAMES, script_mismatch, script_of_language
from . import versions

MAX_CHUNKS_PER_PAGE = 2
PRIMARY_BUDGET_RATIO = 0.8
# 低于这个置信度的关系不进上下文：宁可不给，也不能让 AI 把猜测当官方对应。
MIN_RELATION_CONFIDENCE = 0.8


def _select_primary(
    candidates: list[dict[str, Any]], budget: int
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    per_page: dict[int, int] = {}
    seen_hashes: set[str] = set()
    used = 0
    for row in candidates:
        tokens = int(row["token_estimate"] or 1)
        page_id = row["page_id"]
        content_hash = row.get("content_hash")
        if per_page.get(page_id, 0) >= MAX_CHUNKS_PER_PAGE:
            continue
        if content_hash and content_hash in seen_hashes:
            continue
        if used + tokens > budget:
            continue
        selected.append(row)
        per_page[page_id] = per_page.get(page_id, 0) + 1
        if content_hash:
            seen_hashes.add(content_hash)
        used += tokens
    if not selected and candidates:
        # 最相关的一条本身就超预算：截断，而不是整个放弃或整块塞进去。
        head = dict(candidates[0])
        keep_chars = max(200, budget * 4)
        if len(head["content_md"]) > keep_chars:
            head["content_md"] = head["content_md"][:keep_chars] + "\n\n…（已按预算截断）"
            head["truncated"] = True
        head["token_estimate"] = min(int(head["token_estimate"] or 1), budget)
        selected.append(head)
        used = head["token_estimate"]
    return selected, used


def _one_hop_relations(
    connection: sqlite3.Connection, chunk_ids: list[int], limit: int
) -> list[dict[str, Any]]:
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    # 主内容自己的实体不算"相关"，否则会看到"X 相关的是 X"。
    own_entity_ids = {
        row["entity_id"]
        for row in connection.execute(
            f"SELECT DISTINCT entity_id FROM knowledge_entities "
            f"WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
    }
    rows = connection.execute(
        f"""
        SELECT DISTINCT
            related.id            AS entity_id,
            related.canonical_name,
            related.entity_type,
            related.qualified_name,
            related.source_url,
            p.route_depth,
            r.relation_type,
            r.evidence_kind,
            r.confidence,
            r.note
        FROM knowledge_entities ke
        JOIN relations r
            ON r.from_entity_id=ke.entity_id OR r.to_entity_id=ke.entity_id
        JOIN entities related
            ON related.id=CASE
                WHEN r.from_entity_id=ke.entity_id THEN r.to_entity_id
                ELSE r.from_entity_id
            END
        JOIN pages p ON p.id=related.page_id
        WHERE ke.chunk_id IN ({placeholders})
          AND r.confidence >= ?
        ORDER BY
            {active().relation_priority_sql},
            r.confidence DESC,
            p.route_depth DESC,
            related.canonical_name
        LIMIT ?
        """,
        (*chunk_ids, MIN_RELATION_CONFIDENCE, limit * 4),
    )
    seen: set[int] = set(own_entity_ids)
    relations: list[dict[str, Any]] = []
    for row in rows:
        if row["entity_id"] in seen:
            continue
        # route_depth <= 1 是 /API、/BlueprintAPI 这种总目录页，指过去没有意义。
        if (row["route_depth"] or 9) <= 1:
            continue
        seen.add(row["entity_id"])
        item = dict(row)
        best = connection.execute(
            """
            SELECT c.id FROM chunks c
            JOIN entities e ON e.page_id=c.page_id
            WHERE e.id=?
              AND c.knowledge_type IN ('summary', 'signature', 'overview')
            ORDER BY CASE c.knowledge_type
                WHEN 'signature' THEN 1 WHEN 'summary' THEN 2 ELSE 3 END,
                c.chunk_index
            LIMIT 1
            """,
            (row["entity_id"],),
        ).fetchone()
        item["expand_chunk_id"] = best["id"] if best else None
        relations.append(item)
        if len(relations) >= limit:
            break
    return relations


def build_context_pack(
    connection: sqlite3.Connection,
    query: str,
    *,
    token_budget: int,
    category: str | None,
    version_intent: versions.Intent | None = None,
) -> dict[str, Any]:
    candidates = search_chunks(connection, query, limit=60, category=category)
    # 版本意图在裁剪预算之前生效：先决定"哪些内容对这个版本算数"，再决定
    # "预算内放得下哪几条"。反过来的话，被排除的内容已经占掉了名额。
    candidates, version_report = versions.apply(
        connection, candidates, version_intent
    )

    normalized = normalize_name(query)
    exact_page_ids = {
        row["page_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT e.page_id
            FROM entities e
            JOIN pages p ON p.id=e.page_id
            LEFT JOIN entity_aliases a ON a.entity_id=e.id
            WHERE (e.normalized_name=? OR a.normalized_alias=?)
              AND (? IS NULL OR p.category=?)
            """,
            (normalized, normalized, category, category),
        )
    }
    # 名字精确命中时，只看这个实体自己的页面——不要把同目录的兄弟函数一起带进来。
    scoped = [row for row in candidates if row["page_id"] in exact_page_ids]
    if scoped:
        candidates = scoped

    primary_budget = max(1, int(token_budget * PRIMARY_BUDGET_RATIO))
    primary, used = _select_primary(candidates, primary_budget)
    relations = _one_hop_relations(
        connection, [row["id"] for row in primary], limit=8
    )

    dataset = active().dataset
    pack = {
        "query": query,
        "dataset": dataset.id,
        "product": dataset.product,
        "version": dataset.version,
        "token_budget": token_budget,
        "estimated_tokens": used,
        "primary_knowledge": primary,
        "one_hop_relations": relations,
        "retrieval_policy": {
            "max_chunks_per_page": MAX_CHUNKS_PER_PAGE,
            "primary_budget_ratio": PRIMARY_BUDGET_RATIO,
            "graph_depth": 1,
            "min_relation_confidence": MIN_RELATION_CONFIDENCE,
            "relations_are_pointers_only": True,
            "large_cpp_member_indexes": "ranked down",
            "exact_entity_scope": bool(scoped),
        },
    }
    if version_report:
        pack["version_intent"] = version_report
    return pack


def _has_exact_local_hit(pack: dict[str, Any], query: str) -> bool:
    """本地结果里有没有一条"页面标题就是用户问的那个名字"。

    有，才说明真的找到了；只是若干页面顺带提到过这个词，不算。这条判断决定
    要不要去清单里补抓——否则一堆弱相关的本地块会把真正的目标页挡在门外。
    """
    wanted = set(query_names(query))
    return any(
        normalize_name(item["page_title"] or "") in wanted
        for item in pack["primary_knowledge"]
    )


def answer(
    connection: sqlite3.Connection,
    query: str,
    *,
    token_budget: int,
    category: str | None,
    allow_fetch: bool = True,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    quiet: bool = False,
    version_intent: versions.Intent | None = None,
) -> dict[str, Any]:
    """`ask` 的唯一实现：命令行和 MCP 都走这里，两边结果不可能不一致。"""

    def build() -> dict[str, Any]:
        return build_context_pack(
            connection,
            query,
            token_budget=token_budget,
            category=category,
            version_intent=version_intent,
        )

    pack = build()
    if allow_fetch:
        exact_local = _has_exact_local_hit(pack, query)
        needs_fetch = not exact_local or missing_exact_pages(
            connection, query, category
        )
        if needs_fetch:
            # 已经有确切命中时只补同名的那一页，不顺带把名字相近的一起拉进来。
            fetched = ensure_available(
                connection,
                query,
                limit=fetch_limit,
                category=category,
                quiet=quiet,
                exact_only=exact_local,
            )
            if fetched["succeeded"]:
                pack = build()
            pack["on_demand_fetch"] = fetched
    if not pack["primary_knowledge"]:
        pack["lookup"] = inventory_lookup(connection, query, category=category)
    return pack


KNOWLEDGE_ID_RE = re.compile(r"[Kk]?\d+")


def _subject_entities(
    connection: sqlite3.Connection, subject: str
) -> list[sqlite3.Row]:
    if KNOWLEDGE_ID_RE.fullmatch(subject):
        chunk_id = int(subject[1:] if subject[:1].casefold() == "k" else subject)
        return list(
            connection.execute(
                """
                SELECT e.* FROM knowledge_entities ke
                JOIN entities e ON e.id=ke.entity_id
                WHERE ke.chunk_id=?
                ORDER BY ke.confidence DESC
                """,
                (chunk_id,),
            )
        )
    # 跨两张表的 OR 会让 SQLite 弃用索引；拆成 UNION，两边各走各的索引。
    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for normalized in query_names(subject):
        for row in connection.execute(
            """
            SELECT * FROM entities WHERE id IN (
                SELECT id FROM entities WHERE normalized_name=?
                UNION
                SELECT entity_id FROM entity_aliases WHERE normalized_alias=?
            )
            ORDER BY entity_type, canonical_name
            LIMIT 20
            """,
            (normalized, normalized),
        ):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        if rows:
            break
    return rows


def related_payload(
    connection: sqlite3.Connection, subject: str
) -> dict[str, Any]:
    """一跳交叉关系，带明确状态。

    以前这里返回裸数组：没匹配到实体、实体存在但没关系、页面在清单里还没抓，
    三件事都是一个 `[]`，调用方根本没法判断下一步该改写查询、补抓、还是
    重建关系。所以状态必须写出来。
    """
    entities = []
    for entity in _subject_entities(connection, subject):
        relations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    r.relation_type,
                    r.evidence_kind,
                    r.confidence,
                    r.source_url AS evidence_url,
                    r.note,
                    CASE WHEN r.from_entity_id=? THEN 'outgoing' ELSE 'incoming' END
                        AS direction,
                    related.id AS related_entity_id,
                    related.canonical_name AS related_name,
                    related.entity_type AS related_type,
                    related.qualified_name AS related_qualified_name,
                    related.source_url AS related_source_url
                FROM relations r
                JOIN entities related ON related.id=CASE
                    WHEN r.from_entity_id=? THEN r.to_entity_id
                    ELSE r.from_entity_id
                END
                WHERE r.from_entity_id=? OR r.to_entity_id=?
                ORDER BY r.confidence DESC, r.relation_type, related.canonical_name
                """,
                (entity["id"], entity["id"], entity["id"], entity["id"]),
            )
        ]
        entities.append(
            {
                "entity": {
                    "id": entity["id"],
                    "page_id": entity["page_id"],
                    "name": entity["canonical_name"],
                    "type": entity["entity_type"],
                    "qualified_name": entity["qualified_name"],
                    "module": entity["module"],
                    "owner_type": entity["owner_type"],
                    "source_url": entity["source_url"],
                    "version": entity["version"],
                },
                "relations": relations,
            }
        )
    if not entities:
        # K 编号是知识块 ID，不是页面名字——查不到时那是"编号不存在"，
        # 跟清单里有没有这一页毫无关系，不能套用 inventory_lookup 的诊断。
        status = (
            "knowledge_id_not_found"
            if KNOWLEDGE_ID_RE.fullmatch(subject)
            else "entity_not_found"
        )
    elif any(item["relations"] for item in entities):
        status = "ok"
    else:
        status = "entity_found_but_no_relations"
    result: dict[str, Any] = {
        "subject": subject,
        "status": status,
        "entities": entities,
        "next_steps": [],
    }
    if status == "entity_not_found":
        result["lookup"] = inventory_lookup(connection, subject)
        # "官方没有这一页"和"我们的来源没枚举到这一页"要分开：前者到此为止，
        # 后者是我们自己的覆盖范围问题，改查询词永远解决不了。
        if result["lookup"].get("linked_targets"):
            result["status"] = status = "target_outside_inventory"
        result["next_steps"] = describe_lookup(result["lookup"])
    elif status == "knowledge_id_not_found":
        result["next_steps"] = [
            f"{subject} 不是本地存在的知识块编号。K 编号只能从 search / ask 的"
            "结果里读到，不能凭空猜或复用旧结果——库重跑过一次编号就会变。"
            '先用 python -m docatlas search "<关键词>" 拿到当前有效的 K 编号。',
        ]
    elif status == "entity_found_but_no_relations":
        # 笼统说一句"没有关系"没法照着做。它指向的页面还没抓、还是那些页面
        # 压根不在清单里，下一步完全不同，所以直接把目标状态查出来说。
        targets = page_link_status(
            connection, [item["entity"]["page_id"] for item in entities]
        )
        result["link_targets"] = targets
        steps: list[str] = []
        if targets["pending"]:
            steps.append(
                f"这一页链向 {len(targets['pending'])} 个清单里已有、但正文还没抓的页面。"
                "取回来关系就会出现："
            )
            steps.extend(f'  python -m docatlas get "{t["path"]}"' for t in targets["pending"])
        if targets["missing"]:
            steps.append(
                f"另有 {len(targets['missing'])} 个链接目标不在全站清单里"
                "——官方有，是来源适配器没有枚举到，抓多少次都不会有："
            )
            steps.extend(f"  {t['url']}" for t in targets["missing"])
        if not steps:
            steps = [
                "这个实体在库里，它指向的页面也都抓过了，只是这一页确实不指向别处。",
                "换个更具体的名字查，或者用 search 看看它出现在哪些页面里。",
            ]
        result["next_steps"] = steps
    return result


def describe_lookup(lookup: dict[str, Any]) -> list[str]:
    """把"清单知道什么"翻译成人和 AI 都能照着做的下一步。

    空结果本身不含信息量：官方没有这一页、清单里有但没抓、名字写得对不上，
    三件事的下一步完全不同，必须说清楚是哪一种。
    """
    workspace = active()
    pending = lookup["pending_pages"]
    crawled = lookup["crawled_pages"]
    if pending:
        lines = [f"本地没有正文，但全站清单里有 {len(pending)} 个页面对得上："]
        for page in pending:
            label = workspace.category_labels.get(page["category"], page["category"])
            lines.append(f"  [{label}] {page['path']}")
        lines.append(f'取回来再查：python -m docatlas get "{lookup["query"]}"')
        return lines
    if crawled:
        return [
            f"有 {len(crawled)} 个同名页面已经抓过了，但没有知识块命中这次的查询词。",
            f'换个说法再试，或直接读那一页：python -m docatlas ask "{lookup["query"]}"',
        ]
    if weak := lookup.get("weak_candidates"):
        # "线索不够所以没敢抓"和"确实没有这一页"是两回事。把候选摆出来，
        # 让人自己决定要不要取，而不是一句"官方没有"把路堵死。
        lines = [
            f"没有把握到底该取哪一页，所以这次没有自动补抓。清单里这几页沾边：",
        ]
        for page in weak:
            label = workspace.category_labels.get(page["category"], page["category"])
            lines.append(f"  [{label}] {page['path']}")
        lines.append(
            "看着对就直接取："
            f'python -m docatlas get "{lookup["query"]}"；'
            "或者换成页面的官方名字再查一次，那样就能自动补抓了。"
        )
        return lines
    if linked := lookup.get("linked_targets"):
        # 已抓页面链过去，清单里却没有——那不是"官方没有"，是我们没枚举到。
        # 说成"官方确实没有这一页"会让人一直去改查询词，白费力气。
        lines = [
            f"本地已有的页面里，有正文链接指向「{lookup['query']}」，"
            "但这一页不在全站清单里——官方有，是我们的来源没有枚举到它。",
        ]
        lines.extend(f"  {item['url']}" for item in linked)
        lines.append(
            "先直接打开上面的官方地址；要让它进库，得扩大来源适配器的枚举范围"
            "（见 WORKFLOWS.md 流程 B），重抓多少次都不会有。"
        )
        return lines
    if foreign := script_mismatch(lookup["query"], workspace.language):
        # 用另一套文字提问，在单语库里必然一无所获——这不是"官方没有这一页"，
        # 而是"这个库里没有这种文字的正文"。两句话指向完全不同的下一步。
        return [
            f"这个库的正文是 {workspace.language}"
            f"（{SCRIPT_NAMES.get(script_of_language(workspace.language), '')}），"
            f"而这条查询是用{SCRIPT_NAMES.get(foreign, foreign)}写的，"
            "所以必然一条都对不上——不是官方没有这一页。",
            f"把关键词换成原文（{workspace.language}）里的官方写法再查一次，"
            "比如页面标题、函数名、菜单项的原词。专有名词和代码符号一般不翻译，"
            "原样写就行。",
        ]
    return [
        "没有找到结果，全站清单里也没有对得上的页面。",
        f"原文语言是 {workspace.language}，换成原文里的官方写法往往能命中；"
        "还是没有，就说明官方文档确实没有这一页。",
    ]


def exact_page_hint(
    connection: sqlite3.Connection, query: str, category: str | None = None
) -> list[str]:
    """有结果，但清单里还躺着一页正好叫这个名字——必须说出来。

    否则用户看到的是一串沾边的页面，完全不知道真正对得上的那一页就在清单里、
    只差一条命令。"找不到"和"还没取回来"是两回事。
    """
    missing = missing_exact_pages(connection, query, category)
    if not missing:
        return []
    return [
        f"提示：全站清单里还有 {missing} 个页面的名字与「{query}」完全一致，"
        "但正文尚未抓取，所以不在上面的结果里。",
        f'取回来：python -m docatlas get "{query}"（或直接用 ask，它会自动补抓）',
    ]


def _render_empty(pack: dict[str, Any]) -> list[str]:
    """没命中时，把"清单知道什么"说清楚，而不是丢一句"没找到"。"""
    workspace = active()
    lookup = pack.get("lookup") or {}
    pending = lookup.get("pending_pages") or []
    if not pending:
        # 诊断已经统一在 describe_lookup 里：异语言、线索不够、来源没枚举到、
        # 确实没有——四种"没有"各有各的下一步，这里不该再写一份会走岔的副本。
        return ["本地库里没有命中。", "", *describe_lookup(lookup or {
            "query": pack["query"], "pending_pages": [], "crawled_pages": [],
        })]
    lines = [
        f"本地还没有这一页的正文，但全站清单里有 {len(pending)} 个页面对得上：",
        "",
    ]
    for page in pending:
        label = workspace.category_labels.get(page["category"], page["category"])
        lines.append(f"- [{label}] {page['path']}")
    lines.extend(
        [
            "",
            "本次没有补抓（可能用了 --no-fetch，或抓取失败）。取回来再问一次即可：",
            "",
            f'    python -m docatlas get "{pack["query"]}"',
        ]
    )
    return lines


def render_context_markdown(pack: dict[str, Any]) -> str:
    """把上下文包渲染成给 AI 读的 Markdown。

    同样的内容，Markdown 比 JSON 省 30%~40% token——JSON 的引号、转义和字段名
    全都要花钱，而且 AI 读起来还更费劲。
    """
    workspace = active()
    lines: list[str] = [
        f"# 文档检索：{pack['query']}",
        "",
        f"来源：《{workspace.name}》（{pack['product']} {pack['version']}）。"
        f"预算 {pack['token_budget']:,} tokens，本次约用 {pack['estimated_tokens']:,}，"
        f"共 {len(pack['primary_knowledge'])} 条知识块。",
        "",
    ]
    # 按版本筛过就必须说出来。悄悄少几条，比排错更难被发现。
    if lines_about_version := versions.describe(pack.get("version_intent") or {}):
        lines.extend([*lines_about_version, ""])
    if not pack["primary_knowledge"]:
        lines.extend(_render_empty(pack))
        return "\n".join(lines)

    for index, item in enumerate(pack["primary_knowledge"], 1):
        label = workspace.category_labels.get(item["category"], item["category"])
        lines.append(
            f"## {index}. {item['heading_path']}　"
            f"[{label} · {item['knowledge_type']} · K{item['id']}]"
        )
        lines.append("")
        lines.append(item["content_md"].strip())
        lines.append("")

    if pack["one_hop_relations"]:
        lines.append("---")
        lines.append("")
        lines.append("## 交叉关系（只给指针，正文未展开）")
        lines.append("")
        for relation in pack["one_hop_relations"]:
            kind = workspace.relation_labels.get(
                relation["relation_type"], relation["relation_type"]
            )
            evidence = workspace.evidence_labels.get(
                relation["evidence_kind"], relation["evidence_kind"]
            )
            expand = (
                f"　展开：`show K{relation['expand_chunk_id']}`"
                if relation["expand_chunk_id"]
                else ""
            )
            lines.append(
                f"- **{kind}**：{relation['canonical_name']} "
                f"（{relation['entity_type']}）　依据：{evidence}　"
                f"置信度 {relation['confidence']:.2f}{expand}"
            )
            lines.append(f"  {relation['source_url']}")
        lines.append("")
    return "\n".join(lines)
