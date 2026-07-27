"""检索层。

一次查询按"从最准到最广"分五档执行，然后把各档结果合并排序：

| 档位 | 做法 | 适合的提问 |
|---|---|---|
| A 实体 | 名称/别名精确命中 | `Set Timer by Function Name`、`AActor` |
| B 短语 | FTS 完整短语 | `virtual shadow maps` |
| C 全含 | FTS 所有关键词都出现 | `nanite tessellation displacement` |
| D 任含 | FTS 任一关键词，BM25 排序 | `how do I make an object glow` |
| E 前缀 | 最后一个词按前缀匹配 | 拼了一半 / 记不全名字 |

排序不只看 BM25：签名、概要、参数、返回值这些"直接能回答问题"的知识类型
会被提前，庞大的 C++ 成员目录会被压后。这样 AI 拿到的前几条就是有用的。
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from .chunking import normalize_name
from .db import chunk_fts_mode, fts_mode
from .runtime import active
from .text import qualifier_segments, qualifier_suffixes, qualifier_tail

# 只在"任含"档剔除，避免 how/what 这类词淹没真正的关键词。
STOPWORDS = frozenset(
    """
    a an and are as at be by can do does for from get got have how i if in into is
    it its me my of on or should so than that the their them then there these this
    to use used using was what when where which who why will with you your
    """.split()
)

# 同一个词，问法不同想要的东西完全不同：
#   "K2_SetTimer"  → 想要签名、参数、返回值
#   "Nanite"       → 想要这是什么、怎么用，而不是某个节点的 Outputs 表
# 所以按查询的"形状"切换排序偏好。
API_TYPE_BONUS = {
    "signature": 9.0,
    "summary": 8.0,
    "parameters": 7.0,
    "returns": 6.5,
    "overview": 6.0,
    "remarks": 4.0,
    "examples": 4.0,
    "references": 1.0,
    "details": 0.5,
    "navigation": -3.0,
}

CONCEPT_TYPE_BONUS = {
    "overview": 10.0,
    "summary": 8.0,
    "examples": 6.0,
    "remarks": 5.0,
    "details": 2.0,
    "signature": 2.0,
    "parameters": 1.0,
    "returns": 0.5,
    "references": 0.5,
    "navigation": -4.0,
}

# 各档位的起评分。小节回退档不在这里：它建库初期才走，结果不参与打分排序。
STAGE_BASE = {
    "entity": 100.0,
    "phrase": 70.0,
    "all_terms": 40.0,
    "any_term": 40.0,
    "prefix": 20.0,
}

# `all_terms` 和 `any_term` 查的是同一组词，只有布尔运算符不同，所以同一行在两档
# 里的 bm25 完全相等——两档的分数落在同一把尺子上，可以直接比大小。因此它们**共用
# 起评分**，由 bm25 分出高下。
#
# 从前 `all_terms` 比 `any_term` 高 20 分，等于宣称"凑齐了所有词"一定比"只中了
# 部分词"更相关。这在长文档面前不成立：几万字的版本说明里随便都能凑齐三个常见词，
# 而真正讲这件事的那一节可能不含其中某个词。实测 `random stream nodes` 的正确页面
# 落到第 8 位，前 7 位全是噪音；同样的形状在 cppreference 和 Roblox 上也各复现一次
# ——bm25 早就把顺序排对了，只是它的**分值**被丢掉、只用了档内名次（BUG-020）。
COMPARABLE_STAGES = frozenset({"all_terms", "any_term"})

# 归一化 bm25 能拿到的分数区间。要压得住"整页标题全中"的 12 分加成，否则常见词
# 拼出来的弱匹配又会靠标题字面重合翻上来。
RELEVANCE_WEIGHT = 30.0


def query_profile(query: str, *, entity_hit: bool) -> str:
    """`api`（找具体符号）还是 `concept`（问这是什么、怎么做）。"""
    if entity_hit:
        return "api"
    identifier_re = active().identifier_re
    return "api" if any(identifier_re.search(t) for t in tokenize(query)) else "concept"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.+#:]+|[一-鿿]+")

CHUNK_COLUMNS = """
    c.id, c.page_id, p.title AS page_title, c.heading_path,
    c.content_md, c.context_prefix, c.source_anchor AS source_url,
    p.category, c.knowledge_type, c.token_estimate, c.quality_score,
    c.content_hash
"""

# 全文索引四列的权重：标题 > 小节路径 > 上下文前缀 > 正文。排序和打分必须用同一
# 个式子，各写一遍迟早分岔。
_BM25 = "bm25(chunks_fts, 2.5, 1.8, 1.5, 1.0)"


def tokenize(query: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(query) if token]


def knowledge_id(value: str) -> int | None:
    """`K9290` / `9290` → 9290；不是知识编号则返回 None。

    命令行、MCP 和关系查询原本各写了一遍"去掉开头的 K 再转整数"，其中命令行
    那份直接 `int()`，输入不是数字时抛的是 ValueError 堆栈而不是一句人话。
    编号长什么样是检索层的事，所以规则收在这里，三处共用。
    """
    text = value.strip()
    digits = text[1:] if text[:1].casefold() == "k" else text
    return int(digits) if digits.isdigit() else None


def _quote(token: str) -> str:
    return '"' + token.replace('"', "") + '"'


def quote_fts_query(query: str) -> str:
    """所有关键词都必须出现。小节回退档用它。"""
    return " AND ".join(_quote(token) for token in tokenize(query))


def fts_expressions(query: str) -> list[tuple[str, str]]:
    """返回 [(档位, FTS 表达式)]，从精确到宽松。"""
    tokens = tokenize(query)
    if not tokens:
        return []
    expressions: list[tuple[str, str]] = []
    if len(tokens) > 1:
        expressions.append(("phrase", _quote(" ".join(tokens))))
    expressions.append(("all_terms", " AND ".join(_quote(t) for t in tokens)))
    meaningful = [t for t in tokens if t.casefold() not in STOPWORDS] or tokens
    if len(meaningful) > 1:
        expressions.append(("any_term", " OR ".join(_quote(t) for t in meaningful)))
    last = meaningful[-1]
    if len(last) >= 3 and last.isascii():
        terms = [_quote(t) for t in meaningful[:-1]]
        terms.append(_quote(last)[:-1] + '*"')
        expressions.append(("prefix", " OR ".join(terms)))
    return expressions


def query_names(query: str) -> list[str]:
    """这条查询该按哪几个名字去对实体表。

    第一个永远是查询本身；接着是限定名的后缀写法（`std::views::transform`
    对得上官方页面 `std::ranges::views::transform`）；末段单独放在最后一档，
    它最宽也最容易撞名。再往后是领域知识包补的说法（Unreal 的 `K2_` 前缀、
    属性访问器脱 Get/Set 之类）。核心不知道这些规则，只负责按顺序试。
    """
    names = [query, *qualifier_suffixes(query), qualifier_tail(query)]
    expand = active().hook("query_aliases")
    if expand:
        names.extend(alias for alias in expand(query) if alias)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        normalized = normalize_name(name)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _entity_hits(
    connection: sqlite3.Connection, query: str, category: str | None, limit: int
) -> list[sqlite3.Row]:
    """名称或别名精确命中的知识块。

    刻意拆成两条 UNION 而不是 `名称=? OR 别名=?`：跨两张表的 OR 会让
    SQLite 放弃索引去全表扫，两条分支各自都能走索引。
    """
    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for normalized in query_names(query):
        for row in connection.execute(
            f"""
            SELECT {CHUNK_COLUMNS}
            FROM (
                SELECT id AS entity_id FROM entities WHERE normalized_name=?
                UNION
                SELECT entity_id FROM entity_aliases WHERE normalized_alias=?
            ) hit
            JOIN knowledge_entities ke ON ke.entity_id=hit.entity_id
            JOIN chunks c ON c.id=ke.chunk_id
            JOIN pages p ON p.id=c.page_id
            WHERE (? IS NULL OR p.category=?)
            LIMIT ?
            """,
            (normalized, normalized, category, category, limit),
        ):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows[:limit]


def _fts_hits(
    connection: sqlite3.Connection,
    expression: str,
    category: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    # CROSS JOIN 是有意的：它锁死连接顺序，逼 SQLite 从全文索引出发。
    # 不锁的话，只要带上 `--category`，优化器就改从 pages 的分类索引出发，
    # 于是每一个候选块都要单独跑一次全文匹配——实测 0.05 秒变 44 秒。
    sql = f"""
        SELECT {CHUNK_COLUMNS}, {_BM25} AS bm25_score
        FROM chunks_fts
        CROSS JOIN chunks c ON c.id=chunks_fts.rowid
        CROSS JOIN pages p ON p.id=c.page_id
        WHERE chunks_fts MATCH ?
    """
    params: list[Any] = [expression]
    if category:
        sql += " AND p.category=?"
        params.append(category)
    sql += " ORDER BY bm25_score LIMIT ?"
    params.append(limit)
    try:
        return list(connection.execute(sql, params))
    except sqlite3.OperationalError:
        # 用户输入里可能带 FTS 语法字符；宁可这一档没结果，也不要整体报错。
        return []


def _like_hits(
    connection: sqlite3.Connection, query: str, category: str | None, limit: int
) -> list[sqlite3.Row]:
    pattern = f"%{query}%"
    sql = f"""
        SELECT {CHUNK_COLUMNS}
        FROM chunks c JOIN pages p ON p.id=c.page_id
        WHERE (c.content_text LIKE ? OR p.title LIKE ? OR c.heading_path LIKE ?)
    """
    params: list[Any] = [pattern, pattern, pattern]
    if category:
        sql += " AND p.category=?"
        params.append(category)
    sql += " ORDER BY p.title, c.chunk_index LIMIT ?"
    params.append(limit)
    return list(connection.execute(sql, params))


@dataclasses.dataclass
class _Scoring:
    """一次查询里所有档位共用的打分上下文。

    这些值按查询算一次就够了，但打分要对每一行调用；分开存着，省得把六七个
    参数在函数之间传来传去，也方便后面往里加新的排序信号。
    """

    workspace: Any
    terms: set[str]
    normalized_query: str
    # 查询里末段之前的那几段（`std::views::transform` → std、views）。
    qualifiers: tuple[str, ...] = ()
    profile: str = "api"


def stage_relevance(
    batches: Sequence[tuple[str, Sequence[sqlite3.Row]]],
) -> list[list[float] | None]:
    """逐档给出归一化到 0..1 的 bm25 相关度；同尺的几档共用一个基准。

    基准必须**跨档**取。只按本档取的话，每一档的第一名都是 1.0，宽松档里的最佳
    匹配和精确档里的勉强匹配就一样高——那正是 BUG-020 的成因。不同尺的档（`phrase`
    是整串短语、`prefix` 有词干展开）返回 None，由档内名次兜底。
    """
    best = min(
        (
            row["bm25_score"]
            for stage, rows in batches
            if stage in COMPARABLE_STAGES
            for row in rows
        ),
        default=0.0,
    )
    return [
        [row["bm25_score"] / best for row in rows]
        if stage in COMPARABLE_STAGES and best
        else None
        for stage, rows in batches
    ]


def _score(
    row: sqlite3.Row,
    stage: str,
    rank: int,
    ctx: _Scoring,
    relevance: float | None = None,
) -> float:
    # 档内名次只是同档的平局裁决，跨档没有可比性——`all_terms` 的第 4 名未必
    # 比 `any_term` 的第 1 名更相关。所以能拿到归一化 bm25 时一律用它。
    if relevance is None:
        score = STAGE_BASE[stage] - min(rank, 40) * 0.4
    else:
        score = STAGE_BASE[stage] + relevance * RELEVANCE_WEIGHT
    if ctx.profile == "concept":
        score += CONCEPT_TYPE_BONUS.get(row["knowledge_type"], 0.0)
        score += ctx.workspace.concept_category_bonus.get(row["category"], 0.0)
    else:
        score += API_TYPE_BONUS.get(row["knowledge_type"], 0.0)
    score += (row["quality_score"] or 0.0) * 2.0

    title = (row["page_title"] or "").casefold()
    if ctx.terms:
        heading = (row["heading_path"] or "").casefold()
        score += sum(1 for t in ctx.terms if t in title) / len(ctx.terms) * 6.0
        score += sum(1 for t in ctx.terms if t in heading) / len(ctx.terms) * 3.0
    # 用户打出来的限定符是位置信息，不是可有可无的装饰。同为 entity 档时，
    # 名字里带着 `views` 的那一页才是他要的——少了这一档，
    # `std::views::transform` 和 `std::sort` 一样，只能退到末段去撞名，而
    # 这个库里叫 transform 的有八个（BUG-017）。
    if ctx.qualifiers:
        where = normalize_name(f"{row['page_title'] or ''}{row['source_url'] or ''}")
        matched = sum(1 for segment in ctx.qualifiers if segment in where)
        score += matched / len(ctx.qualifiers) * 8.0

    # 标题本身就是（或以之开头）用户问的东西，几乎一定是对的那一页。
    normalized_title = normalize_name(row["page_title"] or "")
    if ctx.normalized_query and normalized_title:
        if normalized_title == ctx.normalized_query:
            score += 12.0
        elif normalized_title.startswith(ctx.normalized_query):
            score += 6.0

    # 大段的成员罗列回答不了"这是什么、怎么做"，但很占 token，所以压后。
    #
    # 只在概念提问时压。问一个具体符号时，答案往往**就在**那张成员表里——
    # 官方不给属性单独出页面，`TargetArmLength` 这类名字只记在所属类的成员
    # 表中，一律压后就等于把唯一的官方定义压掉。
    if (
        ctx.profile == "concept"
        and row["category"] in ctx.workspace.dataset.verbose_categories
        and row["knowledge_type"] in {"details", "navigation"}
        and (row["token_estimate"] or 0) > 300
    ):
        score -= 8.0
    return score


def _snippet(content_md: str, terms: set[str], width: int = 260) -> str:
    body = re.sub(r"^#{1,6}\s+.*$", "", content_md or "", flags=re.M)
    body = re.sub(r"^>\s*DOC 原出处.*$", "", body, flags=re.M)
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""
    lowered = body.casefold()
    positions = [lowered.find(term) for term in terms]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions) - 60) if positions else 0
    excerpt = body[start : start + width]
    prefix = "… " if start else ""
    suffix = "…" if len(body) > start + width else ""
    return prefix + excerpt + suffix


def search_chunks(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """返回按相关度排序的知识块，每条带 `score` 与 `match_stage`。"""
    if not connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]:
        return _legacy_section_search(connection, query, limit=limit, category=category)

    terms = {t.casefold() for t in tokenize(query) if t.casefold() not in STOPWORDS}
    pool = max(limit * 4, 40)
    scored: dict[int, dict[str, Any]] = {}
    ctx = _Scoring(
        workspace=active(),
        terms=terms,
        normalized_query=normalize_name(query),
        qualifiers=tuple(
            normalized
            for segment in qualifier_segments(query)
            if (normalized := normalize_name(segment))
        ),
    )

    def absorb(
        rows: list[sqlite3.Row], stage: str, relevance: list[float] | None = None
    ) -> None:
        for rank, row in enumerate(rows):
            score = _score(
                row, stage, rank, ctx, None if relevance is None else relevance[rank]
            )
            existing = scored.get(row["id"])
            if existing and existing["score"] >= score:
                continue
            item = dict(row)
            item["score"] = round(score, 2)
            item["match_stage"] = stage
            item["query_profile"] = ctx.profile
            scored[row["id"]] = item

    entity_rows = _entity_hits(connection, query, category, pool)
    ctx.profile = query_profile(query, entity_hit=bool(entity_rows))
    absorb(entity_rows, "entity")
    if chunk_fts_mode(connection) == "fts5":
        # 共用同一把尺子的那两档要先收齐再打分：归一化的基准是两档合起来的最佳
        # bm25，边收边打分就只能拿本档的基准，等于又回到"本档第一名必得满分"。
        batches: list[tuple[str, list[sqlite3.Row]]] = []
        collected = set(scored)
        for stage, expression in fts_expressions(query):
            # 高精度档已经够用就不再下探，避免宽松档冲淡结果。
            if len(collected) >= pool and stage in {"any_term", "prefix"}:
                break
            rows = _fts_hits(connection, expression, category, pool)
            collected.update(row["id"] for row in rows)
            batches.append((stage, rows))
        for (stage, rows), relevance in zip(batches, stage_relevance(batches)):
            absorb(rows, stage, relevance)
    elif not scored:
        absorb(_like_hits(connection, query, category, pool), "all_terms")

    ranked = sorted(scored.values(), key=lambda item: -item["score"])[:limit]
    for item in ranked:
        item["snippet"] = _snippet(item["content_md"], terms)
    return ranked


def chunk_or_section(
    connection: sqlite3.Connection, numeric_id: int
) -> sqlite3.Row | None:
    """按编号读一条知识块；找不到再退回小节表。

    刚建完库、还没切分知识块的时候，编号指的是小节（见 `_legacy_section_search`），
    所以两张表都要查。命令行和 MCP 的 `show` 用的是同一份查询——两边的 K 编号
    必须指向同一条内容，各写一份 SQL 迟早会分岔。
    """
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
    if row:
        return row
    return connection.execute(
        """
        SELECT s.id, p.title AS page_title, p.category, s.heading_path,
               s.content_md, s.source_url, s.knowledge_type,
               '' AS context_prefix
        FROM sections s JOIN pages p ON p.id=s.page_id
        WHERE s.id=?
        """,
        (numeric_id,),
    ).fetchone()


def page_chunks(
    connection: sqlite3.Connection, page_ids: list[int], *, limit: int
) -> list[dict[str, Any]]:
    """整页读取：不检索，直接按顺序取这几页的知识块。

    用在"查询已经把页面指出来了"的场合（贴了官方地址或清单路径）。那时再让
    全文检索去撞是本末倒置——地址里的 `documentation`、`language` 这类词会把
    总目录页顶到第一位，而用户明明已经说清楚要哪一页了。

    先给能直接回答问题的类型（概要、签名、概述），其余按页内顺序。
    """
    if not page_ids:
        return []
    placeholders = ",".join("?" for _ in page_ids)
    rows = connection.execute(
        f"""
        SELECT {CHUNK_COLUMNS}
        FROM chunks c JOIN pages p ON p.id=c.page_id
        WHERE c.page_id IN ({placeholders})
        ORDER BY CASE c.knowledge_type
                     WHEN 'summary' THEN 1 WHEN 'signature' THEN 2
                     WHEN 'overview' THEN 3 ELSE 4 END,
                 c.page_id, c.chunk_index
        LIMIT ?
        """,
        (*page_ids, limit),
    )
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(rows):
        item = dict(row)
        item["score"] = round(STAGE_BASE["entity"] - rank, 2)
        item["match_stage"] = "named_page"
        item["query_profile"] = "api"
        item["snippet"] = _snippet(item["content_md"], set())
        items.append(item)
    return items


def _legacy_section_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category: str | None,
) -> list[dict[str, Any]]:
    """知识块还没生成时（刚建库）退回到小节级检索。"""
    columns = """
        s.id, s.page_id, p.title AS page_title, s.heading_path,
        s.content_md, '' AS context_prefix, s.source_url,
        p.category, s.knowledge_type, s.token_estimate,
        s.quality_score, s.content_hash
    """
    if fts_mode(connection) == "fts5":
        expression = quote_fts_query(query)
        if not expression:
            return []
        # CROSS JOIN 的理由同 `_fts_hits`：不锁连接顺序，带分类的查询会退化成
        # 逐行跑全文匹配。
        sql = f"""
            SELECT {columns}
            FROM sections_fts
            CROSS JOIN sections s ON s.id=sections_fts.rowid
            CROSS JOIN pages p ON p.id=s.page_id
            WHERE sections_fts MATCH ?
        """
        params: list[Any] = [expression]
        if category:
            sql += " AND p.category=?"
            params.append(category)
        sql += " ORDER BY bm25(sections_fts, 2.0, 1.5, 1.0) LIMIT ?"
        params.append(limit)
        rows = list(connection.execute(sql, params))
    else:
        pattern = f"%{query}%"
        sql = f"""
            SELECT {columns}
            FROM sections s JOIN pages p ON p.id=s.page_id
            WHERE (s.content_text LIKE ? OR p.title LIKE ? OR s.heading_path LIKE ?)
        """
        params = [pattern, pattern, pattern]
        if category:
            sql += " AND p.category=?"
            params.append(category)
        sql += " ORDER BY p.title, s.position LIMIT ?"
        params.append(limit)
        rows = list(connection.execute(sql, params))
    terms = {t.casefold() for t in tokenize(query)}
    results = []
    for row in rows:
        item = dict(row)
        item["score"] = 0.0
        item["match_stage"] = "section_fallback"
        item["snippet"] = _snippet(item["content_md"], terms)
        results.append(item)
    return results
