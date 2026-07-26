"""页面成员实体。

一页文档不一定只讲一个东西。类型页会用表格列出它的属性和方法，而这些成员
**大多没有自己的页面**——Unreal 的 `TargetArmLength`、`CrouchedHalfHeight`
只存在于 `USpringArmComponent` / `UCharacterMovementComponent` 的成员表里。
以前"一页 = 一个实体"，于是这些名字在库里根本不存在：搜得到正文，
`related` 却只会回 `entity_not_found`。

这里做的事只有一件：**把成员表里的成员提升为实体**。怎么认出成员表是站点
知识，归来源适配器（`page_members`）；成员在领域里还能叫什么名字是领域知识，
归知识包（`member_aliases`）；这个文件只负责规范化、定身份、去重和落库形状。

三条不变量，后面所有代码都靠它们成立：

* **成员实体永远和它的所有者在同一页。** 于是 `DELETE FROM entities
  WHERE page_id=?` 重新加工一页时会连成员一起删干净，不需要额外的清理代码。
* **身份带所有者**：`qualified_name` 是 `USpringArmComponent::ClientLoc`。
  `ACharacter` 和 `UCharacterMovementComponent` 各有一个 `ClientLoc`，
  30 个这样的同名属性不能串成一个。
* **有自己页面的成员不在这里出现。** 成员表里带链接的那一行说明官方给它出了
  页面，那一页本身就是实体，再提升一次就是同一个东西存两份。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .runtime import active
from .text import normalize_name


# 改了成员识别规则就 +1：已抓页面会整批重算一次，
# 否则新规则只对以后抓的页面生效，同一个库里两套成员。
MEMBERS_VERSION = "1"


def supported() -> bool:
    """当前数据集的来源适配器认不认成员表。"""
    return active().extension("page_members") is not None


def collect(
    *,
    category: str,
    title: str,
    path: str,
    source_url: str,
    sections: list[dict[str, Any]],
    module: str | None,
) -> list[dict[str, Any]]:
    """把适配器认出的成员规范化成实体描述。

    适配器只报告事实（叫什么、是哪一类、签名、摘要、原样的修饰符），
    起限定名、定别名、去重都在这里——这样接一个新站点只要会读它自己的表格，
    不必知道实体长什么样。
    """
    reader = active().extension("page_members")
    if reader is None:
        return []
    found = reader(
        active().dataset,
        category=category,
        title=title,
        path=path,
        sections=sections,
    )
    aliases_of = active().hook("member_aliases")
    version = active().version
    descriptors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for member in found or []:
        name = str(member.get("name") or "").strip()
        entity_type = str(member.get("entity_type") or "").strip()
        normalized = normalize_name(name)
        if not name or not entity_type or not normalized:
            continue
        # 同一页上同名同类的成员只留第一条：重载和被覆盖的虚函数会在
        # 好几个小节里重复出现，它们是同一个成员的不同签名。
        if (entity_type, normalized) in seen:
            continue
        seen.add((entity_type, normalized))
        qualified_name = f"{title}::{name}"
        attributes = {
            "member_of": title,
            "category": category,
            "path": path,
            **(member.get("attributes") or {}),
        }
        member_aliases = {(name, "member_name"), (qualified_name, "qualified_name")}
        if aliases_of:
            member_aliases |= aliases_of(
                name=name,
                entity_type=entity_type,
                owner=title,
                attributes=attributes,
            ) or set()
        descriptors.append(
            {
                "entity_type": entity_type,
                "canonical_name": name,
                "normalized_name": normalized,
                "qualified_name": qualified_name,
                "module": module,
                "owner_type": title,
                "signature": member.get("signature") or None,
                "source_url": member.get("source_url") or source_url,
                "version": version,
                "attributes_json": json.dumps(attributes, ensure_ascii=False),
                "aliases": sorted(member_aliases),
            }
        )
    return descriptors


def backfill(connection: sqlite3.Connection) -> int:
    """对已抓正文重跑一遍成员识别。纯本地计算，不联网。

    加了成员能力（或改了识别规则）的时候，库里已有的页面不该为此重抓一遍——
    小节正文都还在，重新读一次表格就够了。适配器不认成员表的数据集直接跳过，
    一条 SQL 都不会发。
    """
    if not supported():
        return 0
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='page_members'"
    ).fetchone()
    if stored and stored[0] == MEMBERS_VERSION:
        return 0

    # 重算前先清掉上一轮的成员，否则改名后的旧成员会永远留在库里。
    connection.execute("DELETE FROM entities WHERE member_of_id IS NOT NULL")
    created = 0
    owners = list(
        connection.execute(
            """
            SELECT e.id AS owner_id, e.module, p.id AS page_id, p.path,
                   p.category, p.title, p.url
            FROM entities e JOIN pages p ON p.id=e.page_id
            WHERE e.member_of_id IS NULL AND p.status='success'
            """
        )
    )
    from .store import store_members

    for owner in owners:
        sections = [
            {
                "heading_path": row["heading_path"],
                "body_md": row["body_md"] or "",
                "knowledge_type": row["knowledge_type"],
                "source_anchor": row["source_anchor"] or owner["url"],
            }
            for row in connection.execute(
                "SELECT heading_path, body_md, knowledge_type, source_anchor"
                " FROM sections WHERE page_id=? ORDER BY position",
                (owner["page_id"],),
            )
        ]
        if not sections:
            continue
        members = collect(
            category=owner["category"],
            title=owner["title"] or "",
            path=owner["path"],
            source_url=owner["url"],
            sections=sections,
            module=owner["module"],
        )
        created += store_members(
            connection, owner["page_id"], owner["owner_id"], members
        )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('page_members', ?)",
        (MEMBERS_VERSION,),
    )
    connection.commit()
    return created
