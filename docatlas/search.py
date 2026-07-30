"""The retrieval layer.

A query runs in five stages from most precise to broadest, and the results are
then merged and ranked:

| Stage | How | Suits |
|---|---|---|
| A entity | exact name/alias hit | `Set Timer by Function Name`, `AActor` |
| B phrase | full FTS phrase | `virtual shadow maps` |
| C all | every FTS keyword present | `nanite tessellation displacement` |
| D any | any FTS keyword, BM25 ranked | `how do I make an object glow` |
| E prefix | last word matched as a prefix | half-typed / half-remembered names |

Ranking is not BM25 alone: knowledge types that answer a question directly —
signature, summary, parameters, return value — move up, while huge C++ member
listings move down, so the first few results an AI receives are the useful ones.
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

# Removed only in the "any" stage, so words like how/what cannot drown out the
# real keywords.
STOPWORDS = frozenset(
    """
    a an and are as at be by can do does for from get got have how i if in into is
    it its me my of on or should so than that the their them then there these this
    to use used using was what when where which who why will with you your
    """.split()
)

# The same subject wants different things depending on how it is asked:
#   an exact symbol name -> wants the signature, parameters, return value
#   a bare concept word  -> wants what it is and how to use it, not some
#                           reference page's parameter table
# So ranking preference switches on the *shape* of the query.
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

# Base score per stage. The section fallback stage is absent: it only runs early
# in a library's life, and its results take no part in scored ranking.
STAGE_BASE = {
    "entity": 100.0,
    "phrase": 70.0,
    "all_terms": 40.0,
    "any_term": 40.0,
    "prefix": 20.0,
}

# `all_terms` and `any_term` query the same set of words and differ only in the
# boolean operator, so one row's bm25 is identical in both stages. Their scores
# therefore sit on the same scale and can be compared directly, which is why they
# **share a base score** and let bm25 decide between them.
#
# Giving `all_terms` a higher base would assert that "matched every word" is
# always more relevant than "matched some". That does not hold for long documents:
# a release-notes page of tens of thousands of words accumulates three common
# terms by accident, while the section actually about the subject may omit one of
# them. Measured across three datasets, the correct page fell well down the list
# with noise above it — bm25 had ordered them correctly all along, and only its
# *magnitude* was being discarded in favour of within-stage rank.
COMPARABLE_STAGES = frozenset({"all_terms", "any_term"})

# Score range normalized bm25 can reach. It has to outweigh the 12-point bonus
# for "whole page title matched", or a weak match assembled from common words
# climbs back up on literal title overlap alone.
RELEVANCE_WEIGHT = 30.0


def query_profile(query: str, *, entity_hit: bool) -> str:
    """`api` (looking for a specific symbol) or `concept` (what is it / how do I)."""
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

# Weights for the four full-text columns: title > section path > context prefix >
# body. Ranking and scoring must use the same expression; written twice they would
# eventually diverge.
_BM25 = "bm25(chunks_fts, 2.5, 1.8, 1.5, 1.0)"


def tokenize(query: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(query) if token]


def knowledge_id(value: str) -> int | None:
    """`K9290` / `9290` -> 9290; None when it is not a knowledge id.

    What an id looks like is the retrieval layer's business, so the rule lives
    here and is shared by the CLI, MCP and relation queries rather than
    reimplemented in each — one of those did a bare `int()` and raised a
    ValueError traceback instead of a readable message on non-numeric input.
    """
    text = value.strip()
    digits = text[1:] if text[:1].casefold() == "k" else text
    return int(digits) if digits.isdigit() else None


def _quote(token: str) -> str:
    return '"' + token.replace('"', "") + '"'


def quote_fts_query(query: str) -> str:
    """Every keyword must appear. Used by the section fallback stage."""
    return " AND ".join(_quote(token) for token in tokenize(query))


def fts_expressions(query: str) -> list[tuple[str, str]]:
    """Returns [(stage, FTS expression)], from precise to loose."""
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
    """Which names this query should be matched against the entity table.

    The query itself always comes first, then suffix spellings of a qualified name
    (`b::c` matches an official page named `a::b::c`). The bare tail goes last on
    its own, being the broadest and the most collision-prone. After that come
    spellings supplied by the knowledge pack, such as stripping a domain's naming
    prefix or an accessor's Get/Set. The core knows none of those rules and only
    tries the names in order.
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
    """Chunks hit exactly by name or alias.

    Deliberately two UNIONed branches rather than `name=? OR alias=?`: an OR
    across two tables makes SQLite abandon its indexes for a full scan, whereas
    each branch on its own can use one.
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
    # The CROSS JOIN is deliberate: it pins the join order, forcing SQLite to
    # start from the full-text index. Unpinned, adding `--category` makes the
    # optimizer start from the pages category index instead, so every candidate
    # chunk runs its own full-text match — measured at 0.05 seconds versus 44.
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
        # User input may contain FTS syntax characters; better this stage returns
        # nothing than the whole query erroring out.
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
    """Scoring context shared by every stage of one query.

    These values are computed once per query but needed for every row. Keeping
    them together avoids passing six or seven parameters between functions and
    makes room for new ranking signals later.
    """

    workspace: Any
    terms: set[str]
    normalized_query: str
    # Segments before the tail in the query (`std::views::transform` -> std, views).
    qualifiers: tuple[str, ...] = ()
    profile: str = "api"


def stage_relevance(
    batches: Sequence[tuple[str, Sequence[sqlite3.Row]]],
) -> list[list[float] | None]:
    """Per-stage bm25 relevance normalized to 0..1; same-scale stages share a base.

    The baseline has to be taken **across** stages. Taken within one, every
    stage's top row scores 1.0, making the best match in a loose stage equal to a
    barely-qualifying one in a precise stage. Stages on a different scale
    (`phrase` is a whole phrase, `prefix` expands stems) return None and fall back
    to within-stage rank.
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
    # Within-stage rank only breaks ties inside a stage and does not compare
    # across them: 4th in `all_terms` is not necessarily more relevant than 1st in
    # `any_term`. So normalized bm25 is used whenever it is available.
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
    # A qualifier the user typed is location information, not optional decoration.
    # Among entity-stage hits, the page whose name carries that qualifier is the
    # one they meant. Without this stage a fully qualified name behaves like a
    # bare one and can only fall back to colliding on its tail, which in a large
    # library may be shared by a dozen unrelated pages.
    if ctx.qualifiers:
        where = normalize_name(f"{row['page_title'] or ''}{row['source_url'] or ''}")
        matched = sum(1 for segment in ctx.qualifiers if segment in where)
        score += matched / len(ctx.qualifiers) * 8.0

    # The title is, or begins with, exactly what was asked: almost certainly the
    # right page.
    normalized_title = normalize_name(row["page_title"] or "")
    if ctx.normalized_query and normalized_title:
        if normalized_title == ctx.normalized_query:
            score += 12.0
        elif normalized_title.startswith(ctx.normalized_query):
            score += 6.0

    # Long member listings cannot answer "what is this / how do I" and cost many
    # tokens, so they move down.
    #
    # Only for concept questions, though. Asking about a specific symbol, the
    # answer is often **inside** that member table: many sites give properties no
    # page of their own, so such a name is recorded only in its owning type's
    # member table, and demoting unconditionally would demote the only official
    # definition there is.
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
    body = re.sub(r"^>\s*DOC source.*$", "", body, flags=re.M)
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
    """Chunks ordered by relevance, each carrying `score` and `match_stage`."""
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
        # The two stages sharing one scale must be collected before scoring: the
        # normalization baseline is the best bm25 across both, and scoring as they
        # arrive would use only this stage's baseline, which again means "top row
        # of this stage always scores full marks".
        batches: list[tuple[str, list[sqlite3.Row]]] = []
        collected = set(scored)
        for stage, expression in fts_expressions(query):
            # Stop descending once the precise stages suffice, so loose stages do
            # not dilute the result.
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
    """Read one chunk by id, falling back to the sections table.

    Just after a library is built, before chunks are split, an id refers to a
    section (see `_legacy_section_search`), so both tables are queried. The CLI
    and MCP `show` share this one query: a K id must point at the same content on
    both sides, and two copies of the SQL would eventually diverge.
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
    """Whole-page read: no retrieval, just this page's chunks in order.

    Used when the query already names the page (an official URL or inventory path
    was supplied). Letting full-text search compete then gets it backwards — words
    in the URL such as `documentation` or `language` push the top-level index page
    to first place, when the user already said exactly which page they wanted.

    Types that answer a question directly (summary, signature, overview) come
    first; the rest follow in page order.
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
    """Fall back to section-level retrieval before chunks exist (fresh library)."""
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
        # Same reason for CROSS JOIN as in `_fts_hits`: unpinned join order makes
        # a category-filtered query degrade into a per-row full-text match.
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
