"""按需抓取：用到哪一页才去取哪一页。

全站清单在正文抓取之前就已经冻结了，所以即使一页还没取正文，我们**依然知道
它存在、在哪个分类、URL 是什么**。这就是按需抓取能成立的前提：不用漫无目的
地爬，可以直接命中目标那一页。

定位分三档，从最确定到最宽松，任何一档够数就停：

    A 精确  slug 完全一致
            /…/UKismetSystemLibrary/K2_SetTimer  ← "K2_SetTimer"
            /…/geometry_nodes/fields.html        ← "Fields"（扩展名不算名字）
            /…/charconv/from_chars               ← "std::from_chars"（限定符剥掉）
    B 包含  slug 里含有这个词
            /…/nanite-virtualized-geometry-…     ← "Nanite"
    C 覆盖  查询里每个实词都出现在路径中
            /render/shader_nodes/textures/wave…  ← "Wave Texture Node"

C 档要求**全部**实词命中，所以"怎么让物体发光"这种概念提问不会误触发补抓；
真命中了，那一页也确实值得取。
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from typing import Any

from .chunking import normalize_name
from .db import page_slug
from .documents import fetch_document
from .relations import link_new_pages
from .runtime import active, bind
from .search import query_names, tokenize, STOPWORDS
from .text import qualifier_tail
from .store import store_document_result
from .util import log

# 一次按需抓取最多取几页。目的是"补上缺的那一页"，不是顺手爬一片。
DEFAULT_FETCH_LIMIT = 5
MAX_FETCH_LIMIT = 40

# 路径覆盖档至少要有这么多个实词，否则一个词就能扫回一大片。
MIN_COVERAGE_TOKENS = 2
# 太短的词（as、id、ue）做包含匹配会命中几万页，只在精确档用。
MIN_CONTAINS_CHARS = 5


def _priority_case(column: str = "category") -> str:
    """把优先级表编译成一段 SQL CASE，省得同一份顺序在字典和 SQL 里各写一遍。

    同名候选谁优先，由数据集配置说了算（没配就一视同仁）。
    """
    priority = active().category_priority
    if not priority:
        return "0"
    branches = " ".join(
        f"WHEN '{name}' THEN {rank}" for name, rank in priority.items()
    )
    return f"CASE {column} {branches} ELSE {max(priority.values()) + 1} END"


def coverage_tokens(query: str) -> list[str]:
    """路径覆盖档要求全部出现的那几个实词。"""
    tokens = []
    for token in tokenize(query):
        if token.casefold() in STOPWORDS:
            continue
        stripped = normalize_name(token)
        if len(stripped) >= 3:
            tokens.append(stripped)
    return tokens[:6]  # 再多就是整句话了，全部命中反而不可能


# 地址里的分隔符。比对之前要先去掉：用户敲的 `duration_cast` 规范化成
# `durationcast`，而路径里原样写着 `duration_cast`——不去掉下划线，
# 这两个字符串永远对不上，于是"清单里明明有"的页面报成"没有"。
_PATH_SEPARATORS = ("_", "-", ".", "~", "+", "%20", " ")


def _flattened_path(column: str = "path") -> str:
    """把路径拍平成和 `normalize_name` 一个口径，好跟规范化后的词直接比。"""
    expression = f"lower({column})"
    for separator in _PATH_SEPARATORS:
        expression = f"replace({expression}, '{separator}', '')"
    return expression


def identifier_tokens(query: str) -> list[str]:
    """查询里"一看就是个符号"的词：带 `::`、下划线或驼峰的那些。

    普通英文词不算。`milliseconds` 单独拿去对 slug 会扫回一大片，
    而 `duration_cast` 基本只可能是那一页——所以只有后者够格单独定位。
    这也是概念提问不会误触发补抓的原因：`how do I make an object glow`
    里一个符号形状的词都没有。
    """
    identifier_re = active().identifier_re
    found: list[str] = []
    for token in tokenize(query):
        if not identifier_re.search(token):
            continue
        for candidate in (token, qualifier_tail(token)):
            normalized = normalize_name(candidate)
            if len(normalized) >= MIN_CONTAINS_CHARS and normalized not in found:
                found.append(normalized)
    return found


def _candidate_queries(query: str) -> list[tuple[str, str, tuple[Any, ...]]]:
    """返回 [(档位, WHERE 片段, 参数)]，从最确定到最宽松。"""
    names = query_names(query)
    if not names:
        return []
    stages: list[tuple[str, str, tuple[Any, ...]]] = []
    placeholders = ",".join("?" for _ in names)
    stages.append(("exact_slug", f"normalized_slug IN ({placeholders})", tuple(names)))
    # 整条查询对不上，但里面某个符号正好就是一页的名字——`duration_cast
    # milliseconds` 里的 `duration_cast` 就是这样。只认符号形状的词，
    # 所以自然语言提问不会掉进来。
    if tokens := [name for name in identifier_tokens(query) if name not in names]:
        stages.append(
            (
                "token_exact_slug",
                f"normalized_slug IN ({','.join('?' for _ in tokens)})",
                tuple(tokens),
            )
        )
    for name in names:
        if len(name) >= MIN_CONTAINS_CHARS:
            stages.append(("slug_contains", "normalized_slug LIKE ?", (f"%{name}%",)))
    tokens = coverage_tokens(query)
    if len(tokens) >= MIN_COVERAGE_TOKENS:
        flattened = _flattened_path()
        stages.append(
            (
                "path_covers_query",
                " AND ".join(f"{flattened} LIKE ?" for _ in tokens),
                tuple(f"%{token}%" for token in tokens),
            )
        )
    return stages


def _collect(
    connection: sqlite3.Connection,
    stages: list[tuple[str, str, tuple[Any, ...]]],
    *,
    status_clause: str,
    limit: int,
    category: str | None,
) -> list[sqlite3.Row]:
    category_clause = " AND category=?" if category else ""
    category_params: tuple[Any, ...] = (category,) if category else ()
    order = f"ORDER BY {_priority_case()}, route_depth, id"
    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for stage, where, params in stages:
        for row in connection.execute(
            f"SELECT id, url, path, category, status, ? AS match_stage "
            f"FROM pages WHERE ({where}) AND {status_clause}{category_clause} "
            f"{order} LIMIT ?",
            (stage, *params, *category_params, limit * 3),
        ):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


_PENDING = "status IN ('pending', 'failed') AND attempts < 8"


def find_uncrawled_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category: str | None = None,
    exact_only: bool = False,
) -> list[sqlite3.Row]:
    """在清单里找出"很可能是用户要的、但还没抓正文"的页面。"""
    stages = _candidate_queries(query)
    if exact_only:
        stages = [stage for stage in stages if stage[0] == "exact_slug"]
    return _collect(
        connection, stages, status_clause=_PENDING, limit=limit, category=category
    )


def missing_exact_pages(
    connection: sqlite3.Connection, query: str, category: str | None = None
) -> int:
    """清单里有一页正好叫这个名字，但还没抓——这种情况一定要补抓。

    否则会出现这种事：问 `GetCharacterMovement`，本地只有某篇教程的代码示例
    顺带提到了它，于是就用那段代码来回答，而真正的 API 页面明明就在清单里。
    """
    names = query_names(query)
    if not names:
        return 0
    placeholders = ",".join("?" for _ in names)
    sql = (
        f"SELECT COUNT(*) FROM pages WHERE normalized_slug IN ({placeholders})"
        f" AND {_PENDING}"
    )
    params: list[Any] = list(names)
    if category:
        sql += " AND category=?"
        params.append(category)
    return connection.execute(sql, params).fetchone()[0]


def weak_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    """线索不够、不敢自动补抓，但清单里确实沾边的页面。

    "线索不足以安全补抓"和"清单里确实没有这一页"是两回事，以前都表现成同一句
    "官方文档确实没有这一页"。前者该把候选摆出来让人自己定，后者才是真没有。

    这里用的条件比补抓宽得多（任意一个实词出现在路径里就算），所以**只报告、
    不抓取**——照这个条件去抓，一个宽泛问题就能拖回一整片无关页面。
    """
    raw = tokenize(query)
    tokens = coverage_tokens(query)
    # 至少三个实词，才谈得上"差一个"。两个词里差一个就只剩一个词，
    # 那跟随便找个词去撞没有区别。
    if len(tokens) < MIN_COVERAGE_TOKENS + 1:
        return []
    # 虚词占了一半以上，这就是一句话，不是一个页面名。
    # "how do I make an object glow" 里 how/do/I/an 全是虚词，剩下的
    # make + object 能撞上一堆 MakeXxxObject 页面——那是噪音，不是候选。
    # 摆出来只会把人带偏，还不如老实说没找到。
    if len(tokens) * 2 < len(raw):
        return []
    # 补抓那一档要求**每个**实词都出现在路径里。这里放宽整整一个词：
    # 差一个词的页面往往就是用户要的（"camera field of view settings" 差的是
    # settings），但"差一个"还不足以替用户决定去抓，所以只报告不抓取。
    flattened = _flattened_path()
    matched = " + ".join(f"(CASE WHEN {flattened} LIKE ? THEN 1 ELSE 0 END)" for _ in tokens)
    sql = f"SELECT path, category FROM pages WHERE {_PENDING} AND ({matched}) >= ?"
    params: list[Any] = [f"%{token}%" for token in tokens]
    params.append(len(tokens) - 1)
    if category:
        sql += " AND category=?"
        params.append(category)
    # 不排序：这只是"要不要人工看一眼"的提示，而 ORDER BY 会逼着扫完全表再排。
    sql += " LIMIT ?"
    params.append(limit)
    return [
        {"path": row["path"], "category": row["category"]}
        for row in connection.execute(sql, params)
    ]


def linked_but_unlisted(
    connection: sqlite3.Connection, query: str, *, limit: int = 3
) -> list[dict[str, str]]:
    """别的页面链接过去、但全站清单里根本没有的目标页。

    这是"官方确实没有这一页"和"我们的来源没枚举到这一页"之间的分界线。
    答错方向的代价很实在：用户会一遍遍改查询词，而真正该改的是来源适配器的
    枚举范围——那一页在官网上好好地待着，只是我们从来没把它列进来。

    只在"什么都没找到"时才会走到这里，所以这个全表扫描不在常用路径上。
    """
    names = set(query_names(query))
    if not names:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in connection.execute(
        "SELECT DISTINCT target_path, target_url FROM page_links"
        " WHERE target_path IS NOT NULL AND target_page_id IS NULL"
    ):
        path = row["target_path"]
        if path in seen or page_slug(path) not in names:
            continue
        seen.add(path)
        found.append({"path": path, "url": row["target_url"]})
        if len(found) >= limit:
            break
    return found


def inventory_lookup(
    connection: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """清单对这个名字知道些什么。

    没有它，"查不到"就只能是一个空结果——调用方分不清是官方压根没这一页、
    这一页在清单里躺着只是正文还没取、还是我们的来源根本没枚举到它。
    三件事的下一步完全不同。
    """
    stages = _candidate_queries(query)
    pending = _collect(
        connection, stages, status_clause=_PENDING, limit=limit, category=category
    )
    crawled = _collect(
        connection,
        stages,
        status_clause="status IN ('success', 'redirect')",
        limit=limit,
        category=category,
    )
    return {
        "query": query,
        "pending_pages": [
            {
                "path": row["path"],
                "url": row["url"],
                "category": row["category"],
                "matched_by": row["match_stage"],
            }
            for row in pending
        ],
        "crawled_pages": [
            {"path": row["path"], "category": row["category"]} for row in crawled
        ],
        # 后两样只在什么都没找到时才算：它们回答的是"到底是哪一种没有"。
        "linked_targets": (
            linked_but_unlisted(connection, query) if not pending and not crawled else []
        ),
        "weak_candidates": (
            weak_candidates(connection, query, category=category)
            if not pending and not crawled
            else []
        ),
    }


def fetch_now(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    workers: int = 4,
    quiet: bool = False,
) -> dict[str, Any]:
    """立刻抓这几页并入库。返回成功/失败统计。"""
    if not rows:
        return {"requested": 0, "succeeded": 0, "failed": 0, "pages": []}

    succeeded = 0
    failed = 0
    fetched: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(workers, len(rows))
    ) as executor:
        future_to_row = {
            executor.submit(bind(fetch_document), row, 0.0): row for row in rows
        }
        for future in concurrent.futures.as_completed(future_to_row):
            row = future_to_row[future]
            result = future.result()
            store_document_result(connection, result, row["category"])
            if result["ok"]:
                succeeded += 1
                fetched.append(
                    {
                        "page_id": row["id"],
                        "path": row["path"],
                        "category": row["category"],
                        "title": result.get("title"),
                    }
                )
            else:
                failed += 1
                if not quiet:
                    log(f"按需抓取失败 {row['path']}：{result['error']}")
    connection.commit()
    return {
        "requested": len(rows),
        "succeeded": succeeded,
        "failed": failed,
        "pages": fetched,
    }


def ensure_available(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = DEFAULT_FETCH_LIMIT,
    category: str | None = None,
    quiet: bool = False,
    exact_only: bool = False,
) -> dict[str, Any]:
    """本地没有就现抓——`ask` 命中不到时自动走这条路。

    `exact_only=True` 用在"本地已经有些结果、但清单里还有一页正好同名"的情况：
    只补那一页，不顺带把一堆名字相近的页面拉进来。
    """
    limit = max(1, min(limit, MAX_FETCH_LIMIT))
    candidates = find_uncrawled_candidates(
        connection, query, limit=limit, category=category, exact_only=exact_only
    )
    if not candidates:
        return {"requested": 0, "succeeded": 0, "failed": 0, "pages": []}
    if not quiet:
        category_labels = active().category_labels
        labels = ", ".join(
            sorted(
                {category_labels.get(r["category"], r["category"]) for r in candidates}
            )
        )
        log(f"本地还没有这一页，正在按需抓取 {len(candidates)} 页（{labels}）…")
    outcome = fetch_now(connection, candidates, quiet=quiet)
    outcome["relations"] = link_new_pages(
        connection, [page["page_id"] for page in outcome["pages"]]
    )
    return outcome
