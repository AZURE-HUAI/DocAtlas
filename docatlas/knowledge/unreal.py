"""Unreal 领域知识。

这里放的都是"只有懂 Unreal 的人才知道"的事：

  * 蓝图节点 `Set Timer by Function Name` 和 C++ 的 `K2_SetTimer` 是同一个东西，
    因为 UHT 会给蓝图暴露的函数加 `K2_` 前缀。
  * `AActor` 里的 `A` 是类型前缀（U/A/F/I/E/T），不是名字的一部分，
    所以用户搜 "Actor" 也该命中它。
  * C++ 文档里的 `DisplayName="…"` 元数据就是蓝图里显示的节点名——
    这是两边最可靠的对应证据。
  * 蓝图文档正文的 "Target is X" 说明这个节点作用在 X 类型上。

把这些写成配置会变成一套没法测也没法读的迷你语法，所以就是普通 Python 函数。
换成 Unity 或 Blender 时，这个文件整个不加载，核心照常工作。
"""

from __future__ import annotations

import re
import sqlite3

from ..text import humanize_cpp_identifier, normalize_name


# UHT 给蓝图暴露的 C++ 函数加的前缀。
K2_PREFIX = "K2_"
# Unreal 的类型前缀：UObject 派生 U、Actor 派生 A、结构体 F、接口 I、枚举 E、模板 T。
UNREAL_TYPE_PREFIX_RE = re.compile(r"^[UAFIET][A-Z]")

# 查询长得像 Unreal 标识符时，检索要偏向签名 / 参数而不是概念介绍。
# 比通用规则多认一个"类型前缀 + 大驼峰"的形状，例如 AActor、FVector。
IDENTIFIER_PATTERN = r"::|_[A-Za-z]|[a-z][A-Z]|^[UAFIETS][A-Z][A-Za-z]+$"

# 交叉索引重建时要先清掉的关系（都是本知识包推导出来的，不是抓来的事实）。
DERIVED_EVIDENCE_KINDS = (
    "exact_normalized_name",
    "document_statement",
    "unreal_display_name_metadata",
)

RELATION_LABELS = {
    "blueprint_cpp_api": "对应 C++ API",
    "blueprint_cpp_candidate": "候选 C++ API（需核对签名）",
    "node_api_candidate": "候选 API（需核对签名）",
    "targets_type": "Target 类型",
}

EVIDENCE_LABELS = {
    "unreal_display_name_metadata": "Unreal DisplayName 元数据",
    "document_statement": "文档正文声明",
    "exact_normalized_name": "名称标准化后一致",
}

# 上下文包里同一个知识块的相关项按这个顺序排；数字越小越靠前。
RELATION_PRIORITY = {
    "blueprint_cpp_api": 1,
    "targets_type": 2,
    "blueprint_cpp_candidate": 5,
    "node_api_candidate": 6,
}


def extra_entity_aliases(
    *, title: str, category: str, segments: list[str]
) -> set[tuple[str, str]]:
    """给一个符号补上"用户可能会这样叫它"的别名。

    有了这些别名，搜 `SetTimer`、`Set Timer`、`Actor` 都能找到
    `K2_SetTimer` / `AActor` 这些实际页面。
    """
    aliases: set[tuple[str, str]] = set()
    if category != "cpp_api":
        return aliases

    symbol_name = title.split("::")[-1]
    aliases.add((symbol_name, "cpp_symbol_name"))
    aliases.add((humanize_cpp_identifier(symbol_name), "cpp_humanized_name"))

    if symbol_name.startswith(K2_PREFIX) and len(symbol_name) > len(K2_PREFIX):
        k2_base_name = symbol_name[len(K2_PREFIX):]
        aliases.add((k2_base_name, "k2_base_name"))
        aliases.add((humanize_cpp_identifier(k2_base_name), "k2_humanized_name"))

    # 只对类页（标题里没有 ::）脱类型前缀：成员函数的首字母大写不是类型前缀。
    if "::" not in title and UNREAL_TYPE_PREFIX_RE.match(symbol_name):
        unreal_base_name = symbol_name[1:]
        aliases.add((unreal_base_name, "unreal_prefix_stripped"))
        aliases.add(
            (
                humanize_cpp_identifier(unreal_base_name),
                "unreal_prefix_stripped_humanized",
            )
        )
    return aliases


def document_aliases(
    *,
    category: str,
    title: str,
    description: str,
    markdown: str,
    sections: list[dict],
    plain_text,
) -> set[tuple[str, str]]:
    """从 C++ 文档正文里挖出 Unreal 元数据声明的名字。

    `DisplayName="Set Timer by Function Name"` 就是蓝图里看到的节点名，
    这是把蓝图节点和 C++ 函数对上的最硬证据。
    """
    if category != "cpp_api":
        return set()

    is_member_page = "::" in title
    metadata_text = (
        "\n".join([description, markdown])
        if is_member_page
        else "\n".join(
            plain_text(section["body_md"])
            for section in sections
            if section["knowledge_type"] == "signature"
        )
    )
    metadata_fields = [("ScriptName", "unreal_script_name")]
    if is_member_page:
        metadata_fields.append(("DisplayName", "unreal_display_name"))

    aliases: set[tuple[str, str]] = set()
    for metadata_name, alias_type in metadata_fields:
        for match in re.finditer(
            rf"\b{metadata_name}\s*=\s*[\"“]([^\"”]+)[\"”]", metadata_text
        ):
            alias = match.group(1).strip()
            if alias:
                aliases.add((alias, alias_type))
    return aliases


def build_relations(connection: sqlite3.Connection, now: str) -> None:
    """全量重建 Unreal 特有的交叉关系。核心负责通用的官方链接关系。"""
    _link_same_name_candidates(connection, now)
    _link_display_names(connection, now)
    _link_target_types(connection, now)


def link_pages(
    connection: sqlite3.Connection, page_ids: list[int], now: str
) -> int:
    """按需抓取补了几页之后，只给这几页补关系，不重建全库。"""
    if not page_ids:
        return 0
    return _link_display_names(connection, now, page_ids=page_ids)


def _link_same_name_candidates(connection: sqlite3.Connection, now: str) -> None:
    """名字标准化后完全一致的蓝图 / 编辑器节点 ↔ API 符号。

    只是"候选"：同名不等于同一个东西，所以置信度压在 0.82~0.9，
    并在备注里写清楚需要 AI 核对签名。一个名字对上 8 个以上就整组丢弃，
    那多半是 Get / Set 这类烂大街的名字，给出去只会误导。
    """
    candidate_rows = list(
        connection.execute(
            """
            SELECT
                source.id AS from_id,
                target.id AS to_id,
                source.entity_type AS from_type,
                target.entity_type AS to_type,
                source.owner_type AS source_owner,
                target.owner_type AS target_owner,
                source.source_url
            FROM entities source
            JOIN entities target
                ON target.normalized_name=source.normalized_name
               AND target.id != source.id
            WHERE length(source.normalized_name) >= 6
              AND (
                (source.entity_type='blueprint_node'
                    AND target.entity_type='cpp_symbol')
                OR
                (source.entity_type='editor_node'
                    AND target.entity_type IN (
                        'blueprint_node', 'cpp_symbol', 'python_api'
                    ))
              )
            ORDER BY source.id, target.id
            """
        )
    )
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in candidate_rows:
        grouped.setdefault(row["from_id"], []).append(row)
    for rows in grouped.values():
        if len(rows) > 8:
            continue
        for row in rows:
            relation_type = (
                "blueprint_cpp_candidate"
                if row["from_type"] == "blueprint_node"
                else "node_api_candidate"
            )
            owner_matches = (
                row["source_owner"]
                and row["target_owner"]
                and normalize_name(row["source_owner"])
                == normalize_name(row["target_owner"])
            )
            confidence = 0.9 if owner_matches else 0.82
            connection.execute(
                """
                INSERT OR IGNORE INTO relations(
                    from_entity_id, to_entity_id, relation_type,
                    evidence_kind, confidence, source_url, note,
                    created_at, updated_at
                ) VALUES(?, ?, ?, 'exact_normalized_name', ?, ?, ?, ?, ?)
                """,
                (
                    row["from_id"],
                    row["to_id"],
                    relation_type,
                    confidence,
                    row["source_url"],
                    (
                        "名称与所有者类型均一致"
                        if owner_matches
                        else "显示名称标准化后完全一致；需要 AI 核对签名"
                    ),
                    now,
                    now,
                ),
            )


def _link_display_names(
    connection: sqlite3.Connection,
    now: str,
    *,
    page_ids: list[int] | None = None,
) -> int:
    """蓝图节点 ↔ C++ 函数，证据是 C++ 文档里的 DisplayName 元数据。

    这是置信度 1.0 的对应：不是猜的，是官方文档自己写的。
    """
    scope = ""
    params: tuple = ()
    if page_ids is not None:
        placeholders = ",".join("?" for _ in page_ids)
        scope = (
            f" AND (blueprint.page_id IN ({placeholders})"
            f" OR cpp.page_id IN ({placeholders}))"
        )
        params = (*page_ids, *page_ids)

    rows = list(
        connection.execute(
            f"""
            SELECT DISTINCT
                blueprint.id AS from_id,
                cpp.id AS to_id,
                cpp.source_url AS evidence_url,
                target_alias.alias AS metadata_display_name
            FROM entities blueprint
            JOIN entity_aliases source_alias
                ON source_alias.entity_id=blueprint.id
               AND source_alias.alias_type='display_name'
            JOIN entity_aliases target_alias
                ON target_alias.normalized_alias=source_alias.normalized_alias
               AND target_alias.alias_type='unreal_display_name'
            JOIN entities cpp
                ON cpp.id=target_alias.entity_id
               AND cpp.entity_type='cpp_symbol'
            WHERE blueprint.entity_type='blueprint_node'
              AND length(source_alias.normalized_alias) >= 6{scope}
            ORDER BY blueprint.id, cpp.id
            """,
            params,
        )
    )
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO relations(
                from_entity_id, to_entity_id, relation_type,
                evidence_kind, confidence, source_url, note,
                created_at, updated_at
            ) VALUES(
                ?, ?, 'blueprint_cpp_api',
                'unreal_display_name_metadata', 1.0, ?, ?, ?, ?
            )
            """,
            (
                row["from_id"],
                row["to_id"],
                row["evidence_url"],
                (
                    f'C++ 文档的 Unreal 元数据声明 DisplayName="'
                    f'{row["metadata_display_name"]}"；与蓝图节点显示名完全一致'
                ),
                now,
                now,
            ),
        )
    return len(rows)


# `Target is` 之后先粗抓一段，具体到哪个词结束由 _resolve_target_entity 定。
TARGET_IS_PATTERN = re.compile(r"\bTarget is ([A-Za-z][A-Za-z0-9_ ]{2,80})")

# 目标类型名最长几个词（`Ability System Blueprint Library` 是 4 个）。
# 给到 8 是留余量，再长基本就是抓进了后面的正文。
MAX_TARGET_WORDS = 8


def _resolve_target_entity(
    connection: sqlite3.Connection, tail: str
) -> tuple[str, list[sqlite3.Row]]:
    """从 `Target is` 后面那串词里认出目标类型名。

    难点是名字到哪儿结束。正文里紧跟着的就是下一段内容，中间**没有标点**：

        ...are blocked Target is Ability System Component Inputs Type Name...

    所以边界只能靠别名表来定：从长到短试，第一个对得上已知实体的就是它。
    从长到短是必须的——"Actor Component" 得赢过 "Actor"。
    """
    words = tail.split()[:MAX_TARGET_WORDS]
    for size in range(len(words), 1, -1):
        candidate = " ".join(words[:size])
        targets = list(
            connection.execute(
                """
                SELECT DISTINCT e.id FROM entity_aliases a
                JOIN entities e ON e.id=a.entity_id
                WHERE a.normalized_alias=?
                  AND e.entity_type='cpp_symbol'
                LIMIT 8
                """,
                (normalize_name(candidate),),
            )
        )
        if targets:
            return candidate, targets
    return "", []


def _link_target_types(connection: sqlite3.Connection, now: str) -> None:
    """蓝图文档正文写着 "Target is Actor"，说明这个节点作用在 AActor 上。"""
    target_rows = list(
        connection.execute(
            """
            SELECT DISTINCT
                e.id AS from_id,
                e.source_url,
                c.content_text
            FROM entities e
            JOIN chunks c ON c.page_id=e.page_id
            WHERE e.entity_type='blueprint_node'
              AND c.content_text LIKE '%Target is %'
            """
        )
    )
    for row in target_rows:
        match = TARGET_IS_PATTERN.search(row["content_text"])
        if not match:
            continue
        target_name, targets = _resolve_target_entity(connection, match.group(1))
        for target in targets:
            connection.execute(
                """
                INSERT OR IGNORE INTO relations(
                    from_entity_id, to_entity_id, relation_type,
                    evidence_kind, confidence, source_url, note,
                    created_at, updated_at
                ) VALUES(?, ?, 'targets_type', 'document_statement',
                          0.92, ?, ?, ?, ?)
                """,
                (
                    row["from_id"],
                    target["id"],
                    row["source_url"],
                    f"文档正文声明 Target is {target_name}",
                    now,
                    now,
                ),
            )
