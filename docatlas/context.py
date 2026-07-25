"""受 token 预算约束的 AI 上下文包。

这一层存在的唯一理由是**保护上下文**。检索能找到几十条相关内容，但把它们
全塞给 AI 只会让真正的答案被淹没。所以这里做四件事：

1. **硬预算**：累计 token 超过预算就停，绝不"最后一条超一点没关系"。
2. **同页限额**：一个页面最多贡献 2 块，避免一篇长文吃掉整个预算。
3. **去重**：内容哈希相同的块只保留一份（Epic 文档大量复制粘贴）。
4. **一跳关系只给指针**：相关的蓝图/C++ 对应项只给名称、依据、置信度和
   一条可展开的命令，不直接把正文塞进来——需要时再展开。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .chunking import normalize_name
from .config import CATEGORY_LABELS, KNOWLEDGE, LANGUAGE, VERSION
from .dataset import knowledge_hook
from .search import search_chunks

MAX_CHUNKS_PER_PAGE = 2
PRIMARY_BUDGET_RATIO = 0.8
# 低于这个置信度的关系不进上下文：宁可不给，也不能让 AI 把猜测当官方对应。
MIN_RELATION_CONFIDENCE = 0.8

# 通用关系的中文说法。领域知识包可以补自己那几种（蓝图↔C++ 之类）。
RELATION_LABELS = {
    "belongs_to": "所属",
    "parameter_type": "参数类型",
    "return_type": "返回值类型",
    "signature_reference": "签名引用",
    "example_reference": "示例引用",
    "official_reference": "官方相关文档",
    **knowledge_hook(KNOWLEDGE, "RELATION_LABELS", {}),
}

EVIDENCE_LABELS = {
    "official_link": "官方文档链接",
    **knowledge_hook(KNOWLEDGE, "EVIDENCE_LABELS", {}),
}

# 相关项的排序：领域特有的关系（有实锤证据的对应）排在通用关系前面。
_RELATION_PRIORITY = {
    **knowledge_hook(KNOWLEDGE, "RELATION_PRIORITY", {}),
    "parameter_type": 3,
    "return_type": 4,
    "belongs_to": 7,
}
_PRIORITY_BRANCHES = " ".join(
    f"WHEN '{name}' THEN {rank}"
    for name, rank in sorted(_RELATION_PRIORITY.items(), key=lambda kv: kv[1])
)
_RELATION_PRIORITY_SQL = (
    f"CASE r.relation_type {_PRIORITY_BRANCHES} ELSE 8 END"
    if _PRIORITY_BRANCHES
    else "0"
)


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
            {_RELATION_PRIORITY_SQL},
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
) -> dict[str, Any]:
    candidates = search_chunks(connection, query, limit=60, category=category)

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

    return {
        "query": query,
        "ue_version": VERSION,
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


def render_context_markdown(pack: dict[str, Any]) -> str:
    """把上下文包渲染成给 AI 读的 Markdown。

    同样的内容，Markdown 比 JSON 省 30%~40% token——JSON 的引号、转义和字段名
    全都要花钱，而且 AI 读起来还更费劲。
    """
    lines: list[str] = [
        f"# UE {pack['ue_version']} 文档检索：{pack['query']}",
        "",
        f"预算 {pack['token_budget']:,} tokens，本次约用 {pack['estimated_tokens']:,}。"
        f"共 {len(pack['primary_knowledge'])} 条知识块。",
        "",
    ]
    if not pack["primary_knowledge"]:
        lines.append(
            f"本地库中没有命中。可以换成原文语言（{LANGUAGE}）的写法再试，"
            "或确认该页面是否已抓取。"
        )
        return "\n".join(lines)

    for index, item in enumerate(pack["primary_knowledge"], 1):
        label = CATEGORY_LABELS.get(item["category"], item["category"])
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
            kind = RELATION_LABELS.get(
                relation["relation_type"], relation["relation_type"]
            )
            evidence = EVIDENCE_LABELS.get(
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
