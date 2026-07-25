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

import re
import sqlite3
from typing import Any

from .chunking import normalize_name
from .db import chunk_fts_mode, fts_mode

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

# 概念性提问优先看教程和社区文档，而不是 API 参考。
CONCEPT_CATEGORY_BONUS = {
    # 教程和社区文档都是"讲清楚一件事"的文档，不分高下，让其他信号来决定。
    "guides": 4.5,
    "community_docs": 4.5,
    "node_reference": 0.0,
    "blueprint_api": -2.0,
    "python_api": -2.0,
    "cpp_api": -3.0,
}

# 旧名称，保留给外部调用方。
KNOWLEDGE_TYPE_BONUS = API_TYPE_BONUS

_IDENTIFIER_RE = re.compile(
    r"::|_[A-Za-z]|[a-z][A-Z]|^[UAFIETS][A-Z][A-Za-z]+$"
)


def query_profile(query: str, *, entity_hit: bool) -> str:
    """`api`（找具体符号）还是 `concept`（问这是什么、怎么做）。"""
    if entity_hit:
        return "api"
    return "api" if any(_IDENTIFIER_RE.search(t) for t in tokenize(query)) else "concept"

STAGE_BASE = {
    "entity": 100.0,
    "phrase": 70.0,
    "all_terms": 50.0,
    "any_term": 30.0,
    "prefix": 20.0,
    "section_fallback": 10.0,
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.+#:]+|[一-鿿]+")

CHUNK_COLUMNS = """
    c.id, c.page_id, p.title AS page_title, c.heading_path,
    c.content_md, c.context_prefix, c.source_anchor AS source_url,
    p.category, c.knowledge_type, c.token_estimate, c.quality_score,
    c.content_hash
"""


def tokenize(query: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(query) if token]


def _quote(token: str) -> str:
    return '"' + token.replace('"', "") + '"'


def quote_fts_query(query: str) -> str:
    """所有关键词都必须出现（保留给旧调用方）。"""
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


def _entity_hits(
    connection: sqlite3.Connection, query: str, category: str | None, limit: int
) -> list[sqlite3.Row]:
    normalized = normalize_name(query)
    if not normalized:
        return []
    return list(
        connection.execute(
            f"""
            SELECT DISTINCT {CHUNK_COLUMNS}
            FROM entities e
            LEFT JOIN entity_aliases a ON a.entity_id=e.id
            JOIN knowledge_entities ke ON ke.entity_id=e.id
            JOIN chunks c ON c.id=ke.chunk_id
            JOIN pages p ON p.id=c.page_id
            WHERE (e.normalized_name=? OR a.normalized_alias=?)
              AND (? IS NULL OR p.category=?)
            LIMIT ?
            """,
            (normalized, normalized, category, category, limit),
        )
    )


def _fts_hits(
    connection: sqlite3.Connection,
    expression: str,
    category: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = f"""
        SELECT {CHUNK_COLUMNS}
        FROM chunks_fts
        JOIN chunks c ON c.id=chunks_fts.rowid
        JOIN pages p ON p.id=c.page_id
        WHERE chunks_fts MATCH ?
    """
    params: list[Any] = [expression]
    if category:
        sql += " AND p.category=?"
        params.append(category)
    sql += " ORDER BY bm25(chunks_fts, 2.5, 1.8, 1.5, 1.0) LIMIT ?"
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


def _score(
    row: sqlite3.Row,
    stage: str,
    rank: int,
    terms: set[str],
    profile: str,
    normalized_query: str,
) -> float:
    score = STAGE_BASE[stage] - min(rank, 40) * 0.4
    if profile == "concept":
        score += CONCEPT_TYPE_BONUS.get(row["knowledge_type"], 0.0)
        score += CONCEPT_CATEGORY_BONUS.get(row["category"], 0.0)
    else:
        score += API_TYPE_BONUS.get(row["knowledge_type"], 0.0)
    score += (row["quality_score"] or 0.0) * 2.0

    title = (row["page_title"] or "").casefold()
    if terms:
        heading = (row["heading_path"] or "").casefold()
        score += sum(1 for t in terms if t in title) / len(terms) * 6.0
        score += sum(1 for t in terms if t in heading) / len(terms) * 3.0
    # 标题本身就是（或以之开头）用户问的东西，几乎一定是对的那一页。
    normalized_title = normalize_name(row["page_title"] or "")
    if normalized_query and normalized_title:
        if normalized_title == normalized_query:
            score += 12.0
        elif normalized_title.startswith(normalized_query):
            score += 6.0

    # 大段的 C++ 成员罗列几乎从不直接回答问题，但很占 token。
    if (
        row["category"] == "cpp_api"
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
    normalized_query = normalize_name(query)
    pool = max(limit * 4, 40)
    scored: dict[int, dict[str, Any]] = {}
    profile = "api"

    def absorb(rows: list[sqlite3.Row], stage: str) -> None:
        for rank, row in enumerate(rows):
            score = _score(row, stage, rank, terms, profile, normalized_query)
            existing = scored.get(row["id"])
            if existing and existing["score"] >= score:
                continue
            item = dict(row)
            item["score"] = round(score, 2)
            item["match_stage"] = stage
            item["query_profile"] = profile
            scored[row["id"]] = item

    entity_rows = _entity_hits(connection, query, category, pool)
    profile = query_profile(query, entity_hit=bool(entity_rows))
    absorb(entity_rows, "entity")
    if chunk_fts_mode(connection) == "fts5":
        for stage, expression in fts_expressions(query):
            # 高精度档已经够用就不再下探，避免宽松档冲淡结果。
            if len(scored) >= pool and stage in {"any_term", "prefix"}:
                break
            absorb(_fts_hits(connection, expression, category, pool), stage)
    elif not scored:
        absorb(_like_hits(connection, query, category, pool), "all_terms")

    ranked = sorted(scored.values(), key=lambda item: -item["score"])[:limit]
    for item in ranked:
        item["snippet"] = _snippet(item["content_md"], terms)
    return ranked


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
        sql = f"""
            SELECT {columns}
            FROM sections_fts
            JOIN sections s ON s.id=sections_fts.rowid
            JOIN pages p ON p.id=s.page_id
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


def search_docs(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category: str | None,
) -> list[dict[str, Any]]:
    """旧名称，等价于 :func:`search_chunks`。"""
    return search_chunks(connection, query, limit=limit, category=category)
