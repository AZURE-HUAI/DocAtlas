"""通用关系核心。

关系回答的是"这个东西和什么有关、凭什么这么说"。**怎么找候选、怎么验证目标、
怎么挡撞名、怎么去重、怎么存、怎么增量更新、失败了怎么说清楚**——这些对任何
文档站都一样，归这里。只有"凭什么说这两个实体有关"才是领域知识，归
`knowledge/<名字>.py`。

领域包的合同只有一个函数：

    def relation_rules(graph):
        yield RelationCandidate(...)

`graph` 是只读视图，给四个查询原语（`entities` / `find` / `name_matches` /
`texts`）。候选用**实体对象**表达，实体只能从这四个原语里拿——所以目标一定
存在，不存在的目标在 `find()` 那一步就被记成诊断了。领域包不写 SQL、不认识
表结构、不需要知道 entity id，接一个新产品要改的只有这一个函数。

同一个函数同时服务全量重建和按需增量：`graph` 带上 `page_ids` 就只处理这几页。
以前这是两个函数（`build_relations` / `link_pages`），于是按需抓取只补了三种
领域关系里的一种，另外两种在增量路径上永远是空的。

关系的归属记在 `relations.origin`：核心产出的记 `core`，领域包产出的记包名。
全量重建按 origin 删——领域包不必再维护一张"我会产出哪些 evidence_kind"的
清单，漏写一项就会留下删不掉的死关系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
from typing import Any, Iterable, Iterator

from .runtime import active
from .util import utc_now

# 官方链接的 link_kind → 关系类型。任何文档站都成立：正文里链到另一篇文档，
# 就是一条关系。
RELATION_TYPE_BY_LINK_KIND = {
    "hierarchy": "belongs_to",
    "parameter_type": "parameter_type",
    "return_type": "return_type",
    "signature_reference": "signature_reference",
    "example_reference": "example_reference",
    "official_reference": "official_reference",
}

# 核心自己产出的关系记这个 origin。
CORE_ORIGIN = "core"

# 一个名字对上超过这么多实体，这名字就不算证据了——多半是 Get / Set 这类
# 烂大街的名字，给出去只会误导。
DEFAULT_MAX_AMBIGUITY = 8

# 名字短于这个长度不做匹配依据："Get"、"Add" 这种谁都叫。
DEFAULT_MIN_NAME_LENGTH = 6

_ENTITY_FIELDS = (
    "id",
    "page_id",
    "entity_type",
    "canonical_name",
    "normalized_name",
    "qualified_name",
    "owner_type",
    "module",
    "source_url",
)


def _columns(table: str, prefix: str = "") -> str:
    """`SELECT` 里的实体字段列表；`prefix` 用来给第二个实体的列改名避免撞名。"""
    return ", ".join(
        f"{table}.{name} AS {prefix}{name}" if prefix else f"{table}.{name}"
        for name in _ENTITY_FIELDS
    )


_ENTITY_COLUMNS = _columns("e")


@dataclass(frozen=True)
class Entity:
    """领域包看到的实体。只读，字段就是判断"是不是它"用得上的那些。"""

    id: int
    page_id: int
    entity_type: str
    name: str
    normalized_name: str
    qualified_name: str | None
    owner_type: str | None
    module: str | None
    source_url: str

    @classmethod
    def of(cls, row: sqlite3.Row, prefix: str = "") -> "Entity":
        return cls(
            id=row[f"{prefix}id"],
            page_id=row[f"{prefix}page_id"],
            entity_type=row[f"{prefix}entity_type"],
            name=row[f"{prefix}canonical_name"],
            normalized_name=row[f"{prefix}normalized_name"],
            qualified_name=row[f"{prefix}qualified_name"],
            owner_type=row[f"{prefix}owner_type"],
            module=row[f"{prefix}module"],
            source_url=row[f"{prefix}source_url"],
        )


@dataclass(frozen=True)
class RelationCandidate:
    """领域包声明的一条关系。

    `confidence` 是"有多确定"：官方文档白纸黑字写着的用 1.0，靠名字推出来的
    要压下去，并在 `note` 里写清楚凭什么——上层会把小于 1.0 的如实标成"推断"，
    写得含糊就等于让 AI 把猜测当官方对应转述出去。
    """

    source: Entity
    target: Entity
    relation_type: str
    evidence_kind: str
    confidence: float
    note: str = ""
    evidence_url: str = ""
    evidence_chunk_id: int | None = None


@dataclass
class RelationGraph:
    """领域包用来找候选的只读视图。

    `page_ids` 为 None 表示全量；给了就把查询限定在这几页（按需抓取补页之后
    只补这几页的关系，不重扫全库）。限定对关系的**任意一端**生效——新抓的
    页面既可能是关系的起点，也可能是别人早就想指过来的终点。
    """

    connection: sqlite3.Connection
    page_ids: list[int] | None = None
    # find() 没解析出实体的名字。这不是错误，是诊断：目标页多半还没抓，
    # 甚至压根不在清单里（见 BUG-011）。
    unresolved: list[str] = field(default_factory=list)

    def _scope(self, *columns: str) -> tuple[str, tuple[Any, ...]]:
        """把 page_ids 限定编译成 SQL 片段；不限定就返回空片段。"""
        if self.page_ids is None:
            return "", ()
        placeholders = ",".join("?" for _ in self.page_ids)
        clause = " OR ".join(f"{c} IN ({placeholders})" for c in columns)
        return f" AND ({clause})", tuple(self.page_ids) * len(columns)

    def entities(self, *entity_types: str) -> Iterator[Entity]:
        """遍历实体，可按类型过滤。"""
        where = ""
        params: tuple[Any, ...] = ()
        if entity_types:
            where = f" AND e.entity_type IN ({','.join('?' for _ in entity_types)})"
            params = entity_types
        scope, scope_params = self._scope("e.page_id")
        for row in self.connection.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM entities e WHERE 1=1{where}{scope}"
            " ORDER BY e.id",
            (*params, *scope_params),
        ):
            yield Entity.of(row)

    def find(
        self,
        name: str,
        *,
        entity_type: str | None = None,
        alias_type: str | None = None,
        limit: int = DEFAULT_MAX_AMBIGUITY,
    ) -> list[Entity]:
        """按名字或别名找实体。找不到会记进 `unresolved` 供诊断。

        故意不做 page_ids 限定：关系的终点可以是任何早就抓好的页面，
        限定了就会让增量更新只能连到同一批新页面上。
        """
        from .text import normalize_name

        normalized = normalize_name(name)
        if not normalized:
            return []
        # 参数顺序必须跟 SQL 文本里问号出现的顺序一致，所以边拼边攒。
        params: list[Any] = [normalized]
        alias_clause = ""
        if alias_type:
            alias_clause = " AND a.alias_type=?"
            params.append(alias_type)
        params.append(normalized)
        type_clause = ""
        if entity_type:
            type_clause = " AND e.entity_type=?"
            params.append(entity_type)
        params.append(limit + 1)
        rows = list(
            self.connection.execute(
                f"""
                SELECT {_ENTITY_COLUMNS} FROM entities e
                WHERE e.id IN (
                    SELECT entity_id FROM entity_aliases a
                    WHERE a.normalized_alias=?{alias_clause}
                    UNION
                    SELECT id FROM entities WHERE normalized_name=?
                ){type_clause}
                ORDER BY e.id LIMIT ?
                """,
                params,
            )
        )
        if not rows:
            self.unresolved.append(name)
            return []
        # 撞名太多就整组丢弃：这名字已经不能当证据了。
        return [] if len(rows) > limit else [Entity.of(row) for row in rows]

    def name_matches(
        self,
        from_type: str,
        to_type: str | tuple[str, ...],
        *,
        source_alias: str | None = None,
        target_alias: str | None = None,
        min_length: int = DEFAULT_MIN_NAME_LENGTH,
        max_ambiguity: int = DEFAULT_MAX_AMBIGUITY,
    ) -> Iterator[tuple[Entity, Entity, str]]:
        """成批地把两类实体按名字对起来，产出 `(起点, 终点, 对上的那个名字)`。

        这是结构性证据最常见的形状：蓝图节点 ↔ C++ 符号、组件 ↔ 类、
        操作 ↔ 脚本接口……都是"两边名字对得上"。给 `source_alias` /
        `target_alias` 就改按别名对（比如源用显示名、目标用元数据声明的名字）。

        一个起点对上超过 `max_ambiguity` 个终点就整组丢弃——这名字已经不能
        当证据了。`to_type` 给一组类型时，上限算的是这一组的总数，不是每类
        各算各的。
        """
        to_types = (to_type,) if isinstance(to_type, str) else tuple(to_type)
        params: list[Any] = []
        # 起点这边按本名还是按某种别名对。
        if source_alias:
            source_join = (
                "JOIN entity_aliases sa ON sa.entity_id=s.id AND sa.alias_type=?"
            )
            source_key = "sa.normalized_alias"
            source_label = "sa.alias"
            params.append(source_alias)
        else:
            source_join = ""
            source_key = "s.normalized_name"
            source_label = "s.canonical_name"
        # 终点这边同理。别名表必须先 JOIN 进来才能在 ON 里引用它。
        if target_alias:
            target_join = (
                f"JOIN entity_aliases ta"
                f" ON ta.normalized_alias={source_key} AND ta.alias_type=?"
                f" JOIN entities t ON t.id=ta.entity_id"
            )
            params.append(target_alias)
        else:
            target_join = f"JOIN entities t ON t.normalized_name={source_key}"
        scope, scope_params = self._scope("s.page_id", "t.page_id")
        params.append(from_type)
        params.extend(to_types)
        params.append(min_length)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT
                {_columns('s')},
                {_columns('t', 'target_')},
                {source_label} AS matched_name
            FROM entities s
            {source_join}
            {target_join}
            WHERE s.entity_type=?
              AND t.entity_type IN ({','.join('?' for _ in to_types)})
              AND t.id != s.id
              AND length({source_key}) >= ?{scope}
            ORDER BY s.id, t.id
            """,
            (*params, *scope_params),
        )
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["id"], []).append(row)
        for group in grouped.values():
            if len(group) > max_ambiguity:
                continue
            for row in group:
                yield (
                    Entity.of(row),
                    Entity.of(row, "target_"),
                    row["matched_name"],
                )

    def texts(
        self, *entity_types: str, containing: str | None = None
    ) -> Iterator[tuple[Entity, str]]:
        """遍历实体所在页面的正文，产出 `(实体, 正文)`。

        用于"文档自己写着两者有关"这类证据（Unreal 蓝图页的 `Target is X`）。
        `containing` 先在 SQL 里粗筛，精确边界由领域包自己定。
        """
        where = ""
        params: list[Any] = []
        if entity_types:
            where = f" AND e.entity_type IN ({','.join('?' for _ in entity_types)})"
            params.extend(entity_types)
        if containing:
            where += " AND c.content_text LIKE ?"
            params.append(f"%{containing}%")
        scope, scope_params = self._scope("e.page_id")
        for row in self.connection.execute(
            f"""
            SELECT DISTINCT {_ENTITY_COLUMNS}, c.content_text
            FROM entities e
            JOIN chunks c ON c.page_id=e.page_id
            WHERE 1=1{where}{scope}
            ORDER BY e.id
            """,
            (*params, *scope_params),
        ):
            yield Entity.of(row), row["content_text"]


def _write(
    connection: sqlite3.Connection,
    candidate: RelationCandidate,
    *,
    origin: str,
    now: str,
    replace: bool,
) -> bool:
    """存一条关系。返回 False 表示这条被挡下了。"""
    if candidate.source.id == candidate.target.id:
        return False  # 自己指自己不是关系
    if not candidate.relation_type or not candidate.evidence_kind:
        return False
    confidence = min(1.0, max(0.0, float(candidate.confidence)))
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    connection.execute(
        f"""
        {verb} INTO relations(
            from_entity_id, to_entity_id, relation_type, evidence_kind,
            confidence, evidence_chunk_id, source_url, note, origin,
            created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.source.id,
            candidate.target.id,
            candidate.relation_type,
            candidate.evidence_kind,
            confidence,
            candidate.evidence_chunk_id,
            candidate.evidence_url or candidate.source.source_url,
            candidate.note or None,
            origin,
            now,
            now,
        ),
    )
    return True


def _official_links(
    connection: sqlite3.Connection, now: str, page_ids: list[int] | None
) -> int:
    """正文里链到另一篇文档 = 一条关系。任何文档站都成立，所以归核心。"""
    scope = ""
    params: tuple[Any, ...] = ()
    if page_ids is not None:
        placeholders = ",".join("?" for _ in page_ids)
        scope = (
            f" AND (page_links.from_page_id IN ({placeholders})"
            f" OR page_links.target_page_id IN ({placeholders}))"
        )
        params = (*page_ids, *page_ids)
    # 先把链接的目标路径解析成页面 id：目标页可能是这一轮才抓回来的。
    connection.execute(
        """
        UPDATE page_links
        SET target_page_id=(
            SELECT p.id FROM pages p WHERE p.path=page_links.target_path
        )
        WHERE target_path IS NOT NULL
        """
    )
    created = 0
    for row in connection.execute(
        f"""
        SELECT
            source_entity.id AS from_id,
            target_entity.id AS to_id,
            page_links.link_kind,
            page_links.source_url,
            (
                SELECT c.id FROM chunks c
                WHERE c.section_id=page_links.from_section_id
                ORDER BY c.chunk_index LIMIT 1
            ) AS chunk_id
        FROM page_links
        JOIN entities source_entity
            ON source_entity.page_id=page_links.from_page_id
        JOIN entities target_entity
            ON target_entity.page_id=page_links.target_page_id
        WHERE page_links.evidence_kind='official_link'
          AND source_entity.id != target_entity.id{scope}
        """,
        params,
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO relations(
                from_entity_id, to_entity_id, relation_type, evidence_kind,
                confidence, evidence_chunk_id, source_url, origin,
                created_at, updated_at
            ) VALUES(?, ?, ?, 'official_link', 1.0, ?, ?, ?, ?, ?)
            """,
            (
                row["from_id"],
                row["to_id"],
                RELATION_TYPE_BY_LINK_KIND.get(row["link_kind"], "official_reference"),
                row["chunk_id"],
                row["source_url"],
                CORE_ORIGIN,
                now,
                now,
            ),
        )
        created += 1
    return created


def rebuild(
    connection: sqlite3.Connection, *, page_ids: list[int] | None = None
) -> dict[str, Any]:
    """建关系。`page_ids=None` 是全量重建，给了就只补这几页。

    全量会先按 origin 清掉领域包上一轮推导出来的关系（官方链接是抓来的事实，
    每轮 `INSERT OR IGNORE` 重放即可）；增量是纯追加，不删任何东西。
    """
    workspace = active()
    pack = workspace.dataset.knowledge or ""
    now = utc_now()
    full = page_ids is None

    if full and pack:
        connection.execute("DELETE FROM relations WHERE origin=?", (pack,))

    official = _official_links(connection, now, page_ids)

    graph = RelationGraph(connection, page_ids=page_ids)
    rules = workspace.hook("relation_rules")
    accepted = 0
    rejected = 0
    if rules:
        for candidate in rules(graph):
            if _write(connection, candidate, origin=pack, now=now, replace=full):
                accepted += 1
            else:
                rejected += 1

    connection.commit()
    return {
        "official_links": official,
        "domain_relations": accepted,
        "rejected": rejected,
        # 领域规则想连、但库里没有这个实体的名字。多半是目标页还没抓，
        # 也可能是来源清单压根没枚举到它（BUG-011）。
        "unresolved_targets": sorted(set(graph.unresolved))[:20],
        "scope": "full" if full else f"{len(page_ids or [])} pages",
    }


def link_target_gaps(
    connection: sqlite3.Connection, *, limit: int = 5
) -> dict[str, Any]:
    """正文链到了别的文档页，但那一页连不上——分清是哪一种连不上。

    两件事长得像，下一步完全相反：

    * **目标还没抓**：清单里有这一页，只是正文没取。`get` 一下就有了。
    * **目标不在清单**：来源适配器压根没枚举到它。抓多少次都没用，
      要改的是适配器的枚举范围。

    以前两种都表现为"这个实体没有关系"，于是排查方向经常整个跑偏。

    判断"来源范围漏了"用的是一个不会误伤的条件：**某个目录被别的页面链接
    到，而清单在这个目录下一页都没有**。零星几条对不上是正常噪音（改版、
    拼错、非文档页）；整个目录一页都没枚举到，那就是范围划漏了。
    """
    pending = connection.execute(
        "SELECT COUNT(*) FROM page_links l JOIN pages p ON p.id=l.target_page_id"
        " WHERE p.status NOT IN ('success', 'redirect')"
    ).fetchone()[0]
    missing_paths = [
        row[0]
        for row in connection.execute(
            "SELECT target_path FROM page_links"
            " WHERE target_path IS NOT NULL AND target_page_id IS NULL"
        )
    ]
    by_area: dict[str, int] = {}
    for path in missing_paths:
        area = "/".join(path.rstrip("/").split("/")[:-1])
        by_area[area] = by_area.get(area, 0) + 1
    uncovered = []
    for area, count in sorted(by_area.items(), key=lambda kv: -kv[1]):
        if not area:
            continue
        covered = connection.execute(
            "SELECT 1 FROM pages WHERE path LIKE ? LIMIT 1", (area + "/%",)
        ).fetchone()
        if covered is None:
            uncovered.append({"area": area, "links": count})
    return {
        "pending_targets": pending,
        "missing_targets": len(missing_paths),
        "uncovered_areas": len(uncovered),
        "top_uncovered_areas": uncovered[:limit],
    }


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "aliases": connection.execute(
            "SELECT COUNT(*) FROM entity_aliases"
        ).fetchone()[0],
        "page_links": connection.execute(
            "SELECT COUNT(*) FROM page_links"
        ).fetchone()[0],
        "relations": connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
    }


def build_cross_index(connection: sqlite3.Connection) -> dict[str, int]:
    """全量重建，命令行 `cross-index` 的入口。"""
    rebuild(connection)
    return counts(connection)


def link_new_pages(connection: sqlite3.Connection, page_ids: list[int]) -> int:
    """按需抓了几页之后只补这几页的关系。

    走的是和全量完全相同的规则——领域包只写一遍，两条路自动一致。以前增量
    是另一个函数，只补了三种领域关系里的一种。
    """
    if not page_ids:
        return 0
    outcome = rebuild(connection, page_ids=page_ids)
    return outcome["official_links"] + outcome["domain_relations"]
