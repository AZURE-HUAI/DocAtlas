"""清单覆盖缺口：把范围内正文引用到、清单里却没有的页面收进来。

来源清单是站点自己给的（站点地图、`searchindex.js`、目录页），而站点给的
那份清单和"读懂这批内容需要哪些页"从来不是一回事：

* Blender 数据集只枚举 `render/shader_nodes/` 和 `modeling/geometry_nodes/`，
  但节点页正文一直在链 `modeling/modifiers/geometry_nodes`、
  `interface/controls/nodes/groups`——学节点绕不开的基础页，一页都没有。
* Unreal 的站点地图没有列 55 个目录，而已抓正文往那里指了 397 次。

这两件事形状一样，所以处理它的机制也只该有一套。判据不是"我觉得这些页重要"，
而是**范围内的正文自己指过去了**：一篇我们决定收录的文档说"细节见那一页"，
那一页就是这批内容的一部分。这是站点自己写的事实，不是猜测。

边界也从这里来，一共两层：

* **只走一跳。** 收进来的页面处于 `pending`，它们自己的链接不会继续展开。
  想再往外一层就再跑一次，那是一个明确的决定，不是无声的雪球。
* **只收范围内页面引用的目标。** 起点必须是已经抓过正文的页。

收进来的页面归哪一类，分两步问，各有各的负责人：

    1. 问适配器（`categorize_path`）：这条路径在本站属于哪一类？
       Unreal 的 `/API/…` 是 C++ API，Blender 的 `render/shader_nodes/…`
       是着色器节点——这是站点布局，只有适配器知道。
    2. 适配器认不出来，说明它落在数据集声明的目录之外。收不收由**数据集**
       表态（`[inventory] referenced_category`），没表态就不收。

核心因此不认识任何一个具体目录，也不去猜。曾经试过"跟着相邻目录的分类走"
这种零配置的猜法，在真实数据上两边都翻车：Blender 的
`/render/eevee/material_settings` 会被判成着色器节点（`/render/` 底下确实
只有 shader_nodes），Unreal 的一篇 Android 入门教程会被判成 C++ API
（文档根底下 C++ API 页最多）。猜得看起来像，恰恰最难发现是错的。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import resolve_link_targets, route_metadata
from .runtime import active
from .util import utc_now


# 改了"哪条链接算站内文档"的规则就 +1：已存链接会整批重判一次，
# 否则新规则只对以后抓的页面生效，同一个库里两套判断。
LINK_TARGET_VERSION = "2"


def reclassify_links(connection: sqlite3.Connection) -> int:
    """对已存链接重跑一遍"这是不是站内文档"，不联网。

    判断规则收在适配器里，规则一改，之前抓的页面就带着旧结论。重抓一遍代价
    太大而且完全没必要——`page_links.target_url` 原样存着，重判即可。
    """
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='link_targets'"
    ).fetchone()
    if stored and stored[0] == LINK_TARGET_VERSION:
        return 0
    normalize = getattr(active().source, "normalize_link_target", None)
    if normalize is None:
        return 0  # 这个来源不认站内链接，没有可重判的东西
    dataset = active().dataset
    changed = 0
    for row in connection.execute("SELECT id, target_url, target_path FROM page_links"):
        target_path = normalize(dataset, row["target_url"])
        if target_path == row["target_path"]:
            continue
        # 目标路径变了，之前解析出来的页面 id 就是旧结论——必须一并清掉，
        # 否则这条链接会一直指着重判之前认定的那一页。清空之后由下面的
        # resolve_link_targets 按新路径重新解析。
        connection.execute(
            "UPDATE page_links SET target_path=?, evidence_kind=?,"
            " target_page_id=NULL WHERE id=?",
            (target_path, "official_link" if target_path else "external_link", row["id"]),
        )
        changed += 1
    resolve_link_targets(connection)
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('link_targets', ?)",
        (LINK_TARGET_VERSION,),
    )
    connection.commit()
    return changed


def referenced_category() -> str:
    """整个目录都没被枚举过的引用目标归哪一类。空字符串表示不收。"""
    return str(active().dataset.inventory_option("referenced_category", "") or "")


def path_category(path: str) -> str | None:
    """适配器认不认得这条路径属于本数据集的哪一类。没有这个能力就返回 None。"""
    workspace = active()
    classify = getattr(workspace.source, "categorize_path", None)
    return classify(workspace.dataset, path) if classify else None


def linked_targets(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """已抓正文链到了本站文档，而那一页不在清单里。按被引用次数排。"""
    return list(
        connection.execute(
            """
            SELECT l.target_path AS path,
                   MIN(l.target_url) AS url,
                   COUNT(*) AS links
            FROM page_links l
            JOIN pages source ON source.id=l.from_page_id
            WHERE l.target_path IS NOT NULL
              AND l.target_page_id IS NULL
              AND source.status='success'
            GROUP BY l.target_path
            ORDER BY links DESC, path
            """
        )
    )


def admit_linked_targets(
    connection: sqlite3.Connection,
    *,
    limit: int | None = None,
    min_links: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把被引用的清单外页面收进清单，状态 `pending`。

    收进来的页面 `sitemap_url` 留空——它们不是任何清单入口列出来的，
    这一点必须在库里看得出来，否则下次重新枚举时会以为站点地图变了。

    地址一律由适配器按路径重新拼（`canonical_url`），**不用链接里那一串**。
    正文里的链接常带着自己的包袱：Unreal 的链接会带 `application_version=5.5`
    （5.8 的库里存一条 5.5 的地址，引用给用户就是错的），Blender 的会带
    `#term-Alpha-Channel` 这样的片段。路径已经规范化过了，地址就该跟着它重算。
    """
    fallback = referenced_category()
    workspace = active()
    now = utc_now()
    admitted: list[dict[str, Any]] = []
    skipped_no_area = 0
    rows = [row for row in linked_targets(connection) if row["links"] >= min_links]
    for row in rows:
        if limit is not None and len(admitted) >= limit:
            break
        category = path_category(row["path"]) or fallback
        if not category:
            # 适配器不认得，数据集也没说要收——这不是漏网，是范围之外。
            skipped_no_area += 1
            continue
        depth, parent = route_metadata(row["path"])
        if not dry_run:
            connection.execute(
                """
                INSERT INTO pages(
                    url, path, category, doc_version, locale, route_depth,
                    parent_path, discovered_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO NOTHING
                """,
                (
                    workspace.source.canonical_url(workspace.dataset, row["path"]),
                    row["path"],
                    category,
                    workspace.version,
                    workspace.language,
                    depth,
                    parent,
                    now,
                    now,
                ),
            )
        admitted.append(
            {"path": row["path"], "category": category, "links": row["links"]}
        )
    if not dry_run and admitted:
        # 新页面进来之后，指向它们的链接才解析得出目标 id。
        connection.execute(
            """
            UPDATE page_links
            SET target_page_id=(
                SELECT p.id FROM pages p WHERE p.path=page_links.target_path
            )
            WHERE target_path IS NOT NULL AND target_page_id IS NULL
            """
        )
        connection.commit()
    by_category: dict[str, int] = {}
    for item in admitted:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    return {
        "candidates": len(rows),
        "admitted": len(admitted),
        "by_category": by_category,
        "outside_scope": skipped_no_area,
        "referenced_category": fallback,
        "top_admitted": admitted[:10],
        "dry_run": dry_run,
    }
