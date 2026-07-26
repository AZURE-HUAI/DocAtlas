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

from ..relations import RelationCandidate
from ..text import humanize_cpp_identifier, normalize_name


# UHT 给蓝图暴露的 C++ 函数加的前缀。
K2_PREFIX = "K2_"
# Unreal 的类型前缀：UObject 派生 U、Actor 派生 A、结构体 F、接口 I、枚举 E、模板 T。
UNREAL_TYPE_PREFIX_RE = re.compile(r"^[UAFIET][A-Z]")

# 查询长得像 Unreal 标识符时，检索要偏向签名 / 参数而不是概念介绍。
# 比通用规则多认一个"类型前缀 + 大驼峰"的形状，例如 AActor、FVector。
IDENTIFIER_PATTERN = r"::|_[A-Za-z]|[a-z][A-Z]|^[UAFIETS][A-Z][A-Za-z]+$"

# 本包会产出的证据类型。`validate` 用它检查"某类关系被整类做没"——
# 关系的清理不看这张表，看 relations.origin，所以漏写一项不会留下死关系。
DERIVED_EVIDENCE_KINDS = (
    "exact_normalized_name",
    "document_statement",
    "unreal_display_name_metadata",
    "unreal_property_specifier",
)

RELATION_LABELS = {
    "blueprint_cpp_api": "对应 C++ API",
    "blueprint_cpp_candidate": "候选 C++ API（需核对签名）",
    "node_api_candidate": "候选 API（需核对签名）",
    "targets_type": "Target 类型",
    "blueprint_getter": "蓝图 Getter",
    "blueprint_setter": "蓝图 Setter",
}

EVIDENCE_LABELS = {
    "unreal_display_name_metadata": "Unreal DisplayName 元数据",
    "unreal_property_specifier": "UPROPERTY 说明符",
    "document_statement": "文档正文声明",
    "exact_normalized_name": "名称标准化后一致",
}

# 上下文包里同一个知识块的相关项按这个顺序排；数字越小越靠前。
RELATION_PRIORITY = {
    "blueprint_cpp_api": 1,
    "blueprint_getter": 1,
    "blueprint_setter": 1,
    "targets_type": 2,
    "blueprint_cpp_candidate": 5,
    "node_api_candidate": 6,
}


# 蓝图里 `BlueprintReadWrite` 属性会自动生成 `Set X` / `Get X` 节点，
# 但官方**不给这些访问器单独出页面**——属性只记在所属类的 API 页里。
# 所以用户按访问器名字搜时，要顺带按属性本名再找一次。
ACCESSOR_PREFIX_RE = re.compile(r"^(?:set|get)[\s_]*(?=[A-Za-z])", re.I)


def query_aliases(query: str) -> list[str]:
    """这条查询在 Unreal 里还可能叫什么。

    只补"同一个东西的另一种叫法"，不做联想：多给一个名字就多一批候选页，
    宁可漏也不要把不相干的页面拉进来。
    """
    subject = query.strip()
    aliases: list[str] = []
    stripped = ACCESSOR_PREFIX_RE.sub("", subject)
    if stripped and stripped != subject:
        aliases.append(stripped)
        aliases.append(humanize_cpp_identifier(stripped))
    # `K2_SetTimer` 的页面地址带前缀，用户敲的多半不带。只对看起来像单个
    # 标识符的查询做这件事——整句话前面加 K2_ 是没有意义的。
    if subject and " " not in subject:
        if subject.startswith(K2_PREFIX):
            aliases.append(subject[len(K2_PREFIX):])
        else:
            aliases.append(f"{K2_PREFIX}{subject}")
    return aliases


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


# ---------------------------------------------------------------------------
# 成员实体：类页面上的属性和方法在领域里还能叫什么名字。
# ---------------------------------------------------------------------------

# UPROPERTY 的蓝图暴露级别。ReadWrite 有 Get 也有 Set，ReadOnly 只有 Get，
# 两个都没有的属性在蓝图里根本看不见——名字不该被造出来。
_BLUEPRINT_READ_WRITE = "BlueprintReadWrite"
_BLUEPRINT_READ_ONLY = "BlueprintReadOnly"

# `BlueprintGetter=GetFoo` / `BlueprintSetter=SetFoo`：属性显式指定了访问器。
ACCESSOR_SPECIFIER_RE = re.compile(
    r"\bBlueprint(Getter|Setter)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)"
)


def member_aliases(
    *, name: str, entity_type: str, owner: str, attributes: dict
) -> set[tuple[str, str]]:
    """一个类成员在蓝图和编辑器里还会叫什么。

    最要紧的一条：`BlueprintReadWrite` 的属性会在蓝图里自动生成
    `Get X` / `Set X` 两个节点，而官方**不给这些节点单独出页面**。
    用户看到的是节点名，库里存的是属性名，中间这一层只能由别名补上。

    注意这里只造名字，不造关系——自动生成的访问器没有独立实体，
    硬编一条指向不存在东西的关系，等于把猜测写成事实。
    """
    aliases: set[tuple[str, str]] = {
        (humanize_cpp_identifier(name), "cpp_humanized_name")
    }
    specifiers = str(attributes.get("unreal_specifiers") or "")

    if entity_type == "cpp_property":
        readable = _BLUEPRINT_READ_ONLY in specifiers or _BLUEPRINT_READ_WRITE in specifiers
        writable = _BLUEPRINT_READ_WRITE in specifiers
        # 布尔属性的匈牙利前缀不出现在蓝图节点名里：bDoCollisionTest 显示成
        # "Do Collision Test"。
        display_base = name[1:] if re.fullmatch(r"b[A-Z]\w*", name) else name
        humanized = humanize_cpp_identifier(display_base)
        if readable:
            aliases.add((f"Get {humanized}", "blueprint_getter_node"))
        if writable:
            aliases.add((f"Set {humanized}", "blueprint_setter_node"))
        if display_base != name:
            aliases.add((display_base, "unreal_prefix_stripped"))
            aliases.add((humanized, "unreal_prefix_stripped_humanized"))

    if entity_type == "cpp_function" and name.startswith(K2_PREFIX):
        base = name[len(K2_PREFIX):]
        if base:
            aliases.add((base, "k2_base_name"))
            aliases.add((humanize_cpp_identifier(base), "k2_humanized_name"))

    # `Meta=(DisplayName="On End Crouch")` 就是蓝图里显示的名字，和 C++ 名
    # 常常对不上（K2_OnEndCrouch → On End Crouch）。
    for match in re.finditer(r'\bDisplayName\s*=\s*"([^"]+)"', specifiers):
        alias = match.group(1).strip()
        if alias:
            aliases.add((alias, "unreal_display_name"))
    return aliases


# ---------------------------------------------------------------------------
# 关系规则：凭什么说两个实体有关。
#
# 找候选、验证目标、挡撞名、去重、存储、全量/增量更新都归通用核心
# （`docatlas/relations.py`）。这一段只回答"为什么有关"，所以一行 SQL 也没有，
# 也不认识 entity id、表结构和 origin。换成 Unity 或 Blender 时，
# 整个文件不加载，通用的官方链接关系照样建得出来。
# ---------------------------------------------------------------------------

# `Target is` 之后先粗抓一段，具体到哪个词结束由 _resolve_target 定。
TARGET_IS_PATTERN = re.compile(r"\bTarget is ([A-Za-z][A-Za-z0-9_ ]{2,80})")

# 目标类型名最长几个词（`Ability System Blueprint Library` 是 4 个）。
# 给到 8 是留余量，再长基本就是抓进了后面的正文。
MAX_TARGET_WORDS = 8


def relation_rules(graph):
    """Unreal 的四种关系证据，按硬度从高到低排。

    每条规则的 `relation_type` + `evidence_kind` 两两不同，而去重是按
    （起点、终点、关系类型、证据类型）来的，所以它们不会互相覆盖——同一对
    实体可以既有 `blueprint_cpp_api` 又有 `targets_type`，各带各的证据。
    这里的顺序只是给人读的：先写最硬的那条。
    """
    yield from _display_name_metadata(graph)
    yield from _property_accessors(graph)
    yield from _target_type_statements(graph)
    yield from _same_name_candidates(graph)


def _property_accessors(graph):
    """`BlueprintSetter=SetCrouchedHalfHeight` 指名道姓说了访问器是谁。

    只连**同一个类页面上**的那个函数。不限定所有者的话，
    `ACharacter` 和 `UCharacterMovementComponent` 各有一个 `ClientLoc`，
    同名属性会互相串到对方的访问器上去。

    没有显式指定访问器的 `BlueprintReadWrite` 属性不在这里出现：那种访问器
    是引擎自动生成的，官方没有对应文档实体，能给的只有别名
    （见 `member_aliases`），不是关系。
    """
    for prop in graph.entities("cpp_property"):
        specifiers = str(prop.attributes.get("unreal_specifiers") or "")
        for role, accessor_name in ACCESSOR_SPECIFIER_RE.findall(specifiers):
            for accessor in graph.find(accessor_name, entity_type="cpp_function"):
                if accessor.page_id != prop.page_id:
                    continue
                yield RelationCandidate(
                    source=prop,
                    target=accessor,
                    relation_type=f"blueprint_{role.lower()}",
                    evidence_kind="unreal_property_specifier",
                    confidence=1.0,
                    evidence_url=prop.source_url,
                    note=(
                        f"{prop.owner_type} 的成员表里，属性 {prop.name} 的 UPROPERTY "
                        f"说明符写着 Blueprint{role}={accessor_name}"
                    ),
                )


def _display_name_metadata(graph):
    """C++ 文档里的 `DisplayName="X"` 就是蓝图里那个节点的名字。

    这是置信度 1.0 的对应：不是猜的，是官方文档自己写的。
    """
    for node, symbol, display_name in graph.name_matches(
        "blueprint_node",
        "cpp_symbol",
        source_alias="display_name",
        target_alias="unreal_display_name",
    ):
        yield RelationCandidate(
            source=node,
            target=symbol,
            relation_type="blueprint_cpp_api",
            evidence_kind="unreal_display_name_metadata",
            confidence=1.0,
            evidence_url=symbol.source_url,
            note=(
                f'C++ 文档的 Unreal 元数据声明 DisplayName="{display_name}"；'
                "与蓝图节点显示名完全一致"
            ),
        )


def _target_type_statements(graph):
    """蓝图文档正文写着 "Target is Actor"，说明这个节点作用在 AActor 上。"""
    for node, body in graph.texts("blueprint_node", containing="Target is "):
        match = TARGET_IS_PATTERN.search(body)
        if not match:
            continue
        type_name, targets = _resolve_target(graph, match.group(1))
        for target in targets:
            yield RelationCandidate(
                source=node,
                target=target,
                relation_type="targets_type",
                evidence_kind="document_statement",
                confidence=0.92,
                note=f"文档正文声明 Target is {type_name}",
            )


def _resolve_target(graph, tail: str):
    """从 `Target is` 后面那串词里认出目标类型名。

    难点是名字到哪儿结束。正文里紧跟着的就是下一段内容，中间**没有标点**：

        ...are blocked Target is Ability System Component Inputs Type Name...

    所以边界只能靠已知实体来定：从长到短试，第一个对得上的就是它。
    从长到短是必须的——"Actor Component" 得赢过 "Actor"。
    """
    words = tail.split()[:MAX_TARGET_WORDS]
    for size in range(len(words), 1, -1):
        candidate = " ".join(words[:size])
        if targets := graph.find(candidate, entity_type="cpp_symbol"):
            return candidate, targets
    return "", []


def _same_name_candidates(graph):
    """名字标准化后完全一致的蓝图 / 编辑器节点 ↔ API 符号。

    只是"候选"：同名不等于同一个东西，所以置信度压在 0.82~0.9，并在备注里
    写清楚需要 AI 核对签名。
    """
    pairs = [
        ("blueprint_node", ("cpp_symbol",), "blueprint_cpp_candidate"),
        (
            "editor_node",
            ("blueprint_node", "cpp_symbol", "python_api"),
            "node_api_candidate",
        ),
    ]
    for from_type, to_types, relation_type in pairs:
        for source, target, _ in graph.name_matches(from_type, to_types):
            owner_matches = bool(
                source.owner_type
                and target.owner_type
                and normalize_name(source.owner_type) == normalize_name(target.owner_type)
            )
            yield RelationCandidate(
                source=source,
                target=target,
                relation_type=relation_type,
                evidence_kind="exact_normalized_name",
                confidence=0.9 if owner_matches else 0.82,
                note=(
                    "名称与所有者类型均一致"
                    if owner_matches
                    else "显示名称标准化后完全一致；需要 AI 核对签名"
                ),
            )
