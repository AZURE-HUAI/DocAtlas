"""按需抓取：用到哪一页才去取哪一页。

全站清单（199,883 页）在正文抓取之前就已经冻结了，所以即使一页还没取正文，
我们**依然知道它存在、在哪个分类、URL 是什么**。这就是按需抓取能成立的前提：
不用漫无目的地爬，可以直接命中目标那一页。

匹配靠 URL 最后一段（`normalized_slug`）：

    /…/UKismetSystemLibrary/K2_SetTimer   → k2settimer
    用户问 "K2_SetTimer"                   → k2settimer
    用户问 "Set Timer by Function Name"    → settimerbyfunctionname
    /…/Time/SetTimerbyFunctionName        → settimerbyfunctionname

所以用户怎么称呼都能对上，不需要先知道官方 URL。
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from typing import Any

from .chunking import normalize_name
from .config import CATEGORY_LABELS, DATASET, KNOWLEDGE
from .crossindex import RELATION_TYPE_BY_LINK_KIND
from .dataset import knowledge_hook
from .db import page_slug
from .documents import fetch_document
from .store import store_document_result
from .util import log

# 一次按需抓取最多取几页。目的是"补上缺的那一页"，不是顺手爬一片。
DEFAULT_FETCH_LIMIT = 5
MAX_FETCH_LIMIT = 40

# 同名候选谁优先，由数据集配置说了算（没配就一视同仁）。
CATEGORY_PRIORITY = DATASET.category_priority


def _priority_case(column: str = "category") -> str:
    """把优先级表编译成一段 SQL CASE，省得同一份顺序在字典和 SQL 里各写一遍。"""
    if not CATEGORY_PRIORITY:
        return "0"
    branches = " ".join(
        f"WHEN '{name}' THEN {rank}" for name, rank in CATEGORY_PRIORITY.items()
    )
    return f"CASE {column} {branches} ELSE {max(CATEGORY_PRIORITY.values()) + 1} END"


def find_uncrawled_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category: str | None = None,
    exact_only: bool = False,
) -> list[sqlite3.Row]:
    """在清单里找出"很可能是用户要的、但还没抓正文"的页面。"""
    normalized = normalize_name(query)
    if not normalized:
        return []

    category_clause = " AND category=?" if category else ""
    category_params: tuple[Any, ...] = (category,) if category else ()
    order = f"ORDER BY {_priority_case()}, route_depth, id"
    pending = "status IN ('pending', 'failed') AND attempts < 8"

    # 第一档：slug 完全一致。绝大多数"我要查某个节点/某个函数"都命中这里。
    rows = list(
        connection.execute(
            f"SELECT id, url, path, category FROM pages "
            f"WHERE normalized_slug=? AND {pending}{category_clause} {order} LIMIT ?",
            (normalized, *category_params, limit),
        )
    )
    if exact_only or len(rows) >= limit:
        return rows

    # 第二档：slug 包含查询词（例如问 "Nanite" 命中 nanite-virtualized-geometry…）。
    # 短词不做包含匹配，否则会捞回一大堆不相干的页面。
    if len(normalized) >= 5:
        seen = {row["id"] for row in rows}
        for row in connection.execute(
            f"SELECT id, url, path, category FROM pages "
            f"WHERE normalized_slug LIKE ? AND {pending}{category_clause} "
            f"{order} LIMIT ?",
            (f"%{normalized}%", *category_params, limit * 3),
        ):
            if row["id"] in seen:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def missing_exact_pages(
    connection: sqlite3.Connection, query: str, category: str | None = None
) -> int:
    """清单里有一页正好叫这个名字，但还没抓——这种情况一定要补抓。

    否则会出现这种事：问 `GetCharacterMovement`，本地只有某篇教程的代码示例
    顺带提到了它，于是就用那段代码来回答，而真正的 API 页面明明就在清单里。
    """
    normalized = normalize_name(query)
    if not normalized:
        return 0
    sql = """
        SELECT COUNT(*) FROM pages
        WHERE normalized_slug=?
          AND status IN ('pending', 'failed') AND attempts < 8
    """
    params: list[Any] = [normalized]
    if category:
        sql += " AND category=?"
        params.append(category)
    return connection.execute(sql, params).fetchone()[0]


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
            executor.submit(fetch_document, row, 0.0): row for row in rows
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


def link_new_pages(connection: sqlite3.Connection, page_ids: list[int]) -> int:
    """只为刚抓到的页面补建交叉关系。

    全量 `build_cross_index` 要扫全表，二十万页时要跑好几分钟——按需抓取
    显然不能每次都付这个代价。这里只处理这几页涉及的官方链接。
    """
    if not page_ids:
        return 0
    placeholders = ",".join("?" for _ in page_ids)
    connection.execute(
        f"""
        UPDATE page_links
        SET target_page_id=(
            SELECT p.id FROM pages p WHERE p.path=page_links.target_path
        )
        WHERE target_path IS NOT NULL
          AND (from_page_id IN ({placeholders}) OR target_page_id IS NULL)
        """,
        page_ids,
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
          AND source_entity.id != target_entity.id
          AND (
              page_links.from_page_id IN ({placeholders})
              OR page_links.target_page_id IN ({placeholders})
          )
        """,
        (*page_ids, *page_ids),
    ):
        relation_type = RELATION_TYPE_BY_LINK_KIND.get(
            row["link_kind"], "official_reference"
        )
        now = _now()
        connection.execute(
            """
            INSERT OR IGNORE INTO relations(
                from_entity_id, to_entity_id, relation_type, evidence_kind,
                confidence, evidence_chunk_id, source_url, created_at, updated_at
            ) VALUES(?, ?, ?, 'official_link', 1.0, ?, ?, ?, ?)
            """,
            (
                row["from_id"],
                row["to_id"],
                relation_type,
                row["chunk_id"],
                row["source_url"],
                now,
                now,
            ),
        )
        created += 1

    # 领域特有的关系（Unreal 的蓝图↔C++ 对应之类），只针对新页面所属实体。
    link_pages = knowledge_hook(KNOWLEDGE, "link_pages")
    if link_pages:
        created += link_pages(connection, page_ids, _now())
    connection.commit()
    return created


def _now() -> str:
    from .util import utc_now

    return utc_now()


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
        labels = ", ".join(
            sorted({CATEGORY_LABELS.get(r["category"], r["category"]) for r in candidates})
        )
        log(f"本地还没有这一页，正在按需抓取 {len(candidates)} 页（{labels}）…")
    outcome = fetch_now(connection, candidates, quiet=quiet)
    outcome["relations"] = link_new_pages(
        connection, [page["page_id"] for page in outcome["pages"]]
    )
    return outcome
