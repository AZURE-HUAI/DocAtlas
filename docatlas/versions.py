"""版本适用范围与版本意图。

这里回答两个**不同**的问题，混在一起就一定会写死某种偏好：

  * **这段内容适用于哪些版本**——文档自己写着的事实。由领域层（来源适配器
    或知识包）从正文里认出来，因为"版本怎么写"是每个站自己的排版约定：
    cppreference 写 `(since C++20)`，Blender 写"versions before 3.4"。
  * **用户想要哪些版本**——这是意图，核心猜不出来。必须由上层 AI 判断之后，
    以结构化条件传进来。

核心只做通用的那一半：存事实、比大小、按意图筛。它不知道 C++20 比 C++17 新，
也不知道 Blender 5.2 是什么——**排序键由领域层给出**。这不是洁癖：C++98 比
C++11 早，可数字上 98 > 11，任何通用的"版本号比大小"规则都会把它排反。

## 三种证据，强度不同，用途不同

    since     "从 X 版本才有"。硬证据，**只有它能用来排除**。
    until     "到 X 版本为止"。存下来供报告和比较，但不参与排除（原因见下）。
    mentions  正文提到了某个版本。软证据，只在"迁移追溯"时加分。

`until` 为什么不排除：真实数据里一个知识块能同时带好几行不同的标记。
cppreference 的 `Algorithms library > Modifying sequence operations` 一块里
既有 `(until C++11)` 又有 `(since C++11)`——那是同一张表相邻两行的脚注。
按"到 C++11 为止"去排除，会把 C++20 里明明存在的整组 swap 操作删掉。
所以 `until` 只报告，不裁决。

## 标记写在哪里，决定它管多大范围

同样是 `(since C++26)`，写在标题里和写在正文里完全不是一回事：

    heading   `Annotations (since C++26)`、`C++ attribute: assume (since C++23)`
              ——限定整个小节，这一段在更早的版本里根本不存在。
    body      `std::optional` 成员函数表里的一行 `begin (C++26)`
              ——只限定那一行，整张表在 C++17 就有了。

所以**只有标题里的 `since` 能排除内容**。这不是保守，是实测：cppreference
小样里标题限定的 6 块全部确实是版本限定；只在正文出现标记的 19 块中，
`std::all_of`、`std::binary_search`、实参依赖查找、`std::optional` 成员表
等十余块都是很早就有的内容，按正文标记排除等于把用户要的答案藏起来。

两种错误的代价不对称：多给了新版本的内容，用户看得见也看得懂；少给了本该有的
内容，用户完全无从发现。所以宁可漏放宽，不可误藏。

## 四种意图，同一份证据可以有相反的处理

这正是不能写死"优先新版本"或"优先当前版本"的原因：

    strict     严格限定：排除"目标版本里还不存在"的内容。
    migration  迁移追溯：带版本标记的内容反而**加分**——"什么时候变的"
               就是用户要问的答案。
    compare    版本比较：一条都不删，把标记原样带出去，由上层组织。
    any        不限定：与没有这个功能时的行为完全一致。

## 没有信息时永远不筛

数据集没有声明版本词汇、或某一块没有任何标记时，一律按"适用"处理。宁可多给，
也不能因为"我不知道"就把官方内容藏起来——那种错误在结果里完全看不出来。
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable

from .runtime import active

# 意图。`any` 等同于不传。
STRICT = "strict"
MIGRATION = "migration"
COMPARE = "compare"
ANY = "any"
MODES = (STRICT, MIGRATION, COMPARE, ANY)

# 证据种类。
SINCE = "since"
UNTIL = "until"
MENTIONS = "mentions"
MARK_KINDS = (SINCE, UNTIL, MENTIONS)

# 标记的作用范围。标题里的管整段，正文里的只管那一行——只有前者够硬到能排除。
HEADING = "heading"
BODY = "body"

# 提取规则的版本。领域层改了写法就 +1，已有库会整批重算——
# 否则同一个库里两套规则产出的标记并存，筛出来的结果没法解释。
MARKS_VERSION = "1"

# 迁移追溯时的加分。带任何版本标记就说明"这里讲的是版本之间的差异"，
# 而标记的版本与用户所在版本不同时，那基本就是迁移证据本身。
MIGRATION_MARKED_BONUS = 8.0
MIGRATION_OTHER_VERSION_BONUS = 10.0


@dataclass(frozen=True)
class Mark:
    """一条版本适用信息。`key` 是领域层给的可比较键，核心只拿它比大小。"""

    kind: str
    label: str
    key: str
    scope: str = BODY

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "version": self.label, "scope": self.scope}


@dataclass(frozen=True)
class Intent:
    """用户的版本意图。由上层 AI 判断后传进来，核心不推断。"""

    mode: str
    target: str = ""
    target_key: str = ""

    @property
    def excludes(self) -> bool:
        """这次会不会真的排除内容。没有可比较的目标版本就只能不排除。"""
        return self.mode == STRICT and bool(self.target_key)


def vocabulary() -> str:
    """这个数据集的版本说的是什么，用于把诊断写成人话。"""
    return str(active().extension("VERSION_VOCABULARY", "") or "")


def supported() -> bool:
    """这个数据集能不能提供可验证的版本适用信息。"""
    return callable(active().extension("version_marks"))


def sort_key(label: str) -> str:
    """把版本标签变成可比较的键；领域层不认识这个标签就返回空串。"""
    maker = active().extension("version_sort_key")
    if not maker or not label:
        return ""
    return str(maker(label) or "")


def marks_in(heading: str, body: str) -> list[Mark]:
    """认出一个知识块的版本标记，并记住每条是写在标题还是正文里。

    领域层只需要回答"这段文字里有哪些版本标记"；标记管多大范围是核心的
    记账，不该让每个适配器各自实现一遍。同一条标记两处都出现时算标题——
    强的那个说了算。
    """
    reader = active().extension("version_marks")
    if not reader:
        return []
    found: dict[tuple[str, str], Mark] = {}
    for scope, text in ((BODY, body), (HEADING, heading)):
        for kind, label in reader(text or ""):
            if kind not in MARK_KINDS or not label:
                continue
            if key := sort_key(label):
                found[(kind, label)] = Mark(
                    kind=kind, label=label, key=key, scope=scope
                )
    return sorted(found.values(), key=lambda mark: (mark.kind, mark.key))


def parse_intent(mode: str | None, target: str | None) -> Intent | None:
    """把上层传来的版本条件变成结构化意图。

    看不懂的模式要报错，不能默默忽略——静静地不筛，比明确拒绝危险得多：
    调用方会以为限定生效了。
    """
    mode = (mode or "").strip().casefold()
    target = (target or "").strip()
    if not mode and not target:
        return None
    if not mode:
        # 只给了目标版本，最常见的意思就是"限定在这个版本"。
        mode = STRICT
    if mode not in MODES:
        raise ValueError(
            f"看不懂的版本意图 {mode!r}。可选：{'、'.join(MODES)}。"
            f"strict=严格限定该版本，migration=追溯版本之间的变化，"
            f"compare=保留全部版本供比较，any=不限定。"
        )
    if mode == ANY:
        return None
    return Intent(mode=mode, target=target, target_key=sort_key(target))


# ---------------------------------------------------------------------------
# 存取
# ---------------------------------------------------------------------------


def store_marks(
    connection: sqlite3.Connection, chunk_id: int, heading: str, body: str
) -> int:
    """记下一个知识块的版本适用信息。返回记了几条。"""
    marks = marks_in(heading, body)
    if not marks:
        return 0
    connection.executemany(
        "INSERT OR REPLACE INTO chunk_versions(chunk_id, kind, label, sort_key, scope)"
        " VALUES(?, ?, ?, ?, ?)",
        [
            (chunk_id, mark.kind, mark.label, mark.key, mark.scope)
            for mark in marks
        ],
    )
    return len(marks)


def load_marks(
    connection: sqlite3.Connection, chunk_ids: Iterable[int]
) -> dict[int, list[Mark]]:
    """批量取版本标记。没标记的块不会出现在结果里。"""
    ids = list(chunk_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    found: dict[int, list[Mark]] = {}
    for row in connection.execute(
        f"SELECT chunk_id, kind, label, sort_key, scope FROM chunk_versions"
        f" WHERE chunk_id IN ({placeholders}) ORDER BY kind, sort_key",
        ids,
    ):
        found.setdefault(row["chunk_id"], []).append(
            Mark(
                kind=row["kind"],
                label=row["label"],
                key=row["sort_key"],
                scope=row["scope"],
            )
        )
    return found


def backfill(connection: sqlite3.Connection) -> int:
    """给已经抓好的知识块补上版本标记，不需要重新联网。

    只在提取规则变了、或这个库还没做过时整批重算。数据集不声明版本词汇时
    直接跳过——UE 那种二十多万页的库不该为一个用不上的功能扫一遍全表。
    """
    if not supported():
        return 0
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='version_marks'"
    ).fetchone()
    if stored and stored[0] == MARKS_VERSION:
        return 0
    connection.execute("DELETE FROM chunk_versions")
    written = 0
    for row in connection.execute(
        "SELECT c.id, p.title, c.heading_path, c.content_text"
        " FROM chunks c JOIN pages p ON p.id=c.page_id"
    ):
        written += store_marks(
            connection,
            row["id"],
            f"{row['title']}\n{row['heading_path']}",
            row["content_text"],
        )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('version_marks', ?)",
        (MARKS_VERSION,),
    )
    connection.commit()
    return written


# ---------------------------------------------------------------------------
# 按意图筛选
# ---------------------------------------------------------------------------


def _excluded_by(marks: list[Mark], target_key: str) -> str:
    """这一块该不该因为版本被排除；该排除就返回那个版本的标签。

    两道闸门，都是实测定下来的：

    1. **只看标题里的 `since`**。正文里的标记只限定它所在的那一行，拿它排除
       整块会误伤（`std::optional` 的成员表因为一行 `begin (C++26)` 被整块
       藏起来）。
    2. **只看最早的那一个**。标题上若干标记里最早的 since 都还晚于目标版本，
       才说明这一段在目标版本里确实不存在。
    """
    since = [
        mark for mark in marks if mark.kind == SINCE and mark.scope == HEADING
    ]
    if not since:
        return ""
    earliest = min(since, key=lambda mark: mark.key)
    return earliest.label if earliest.key > target_key else ""


def _migration_bonus(marks: list[Mark], target_key: str) -> float:
    """迁移追溯时这一块该加多少分。

    带版本标记 = 这里讲的是"版本之间有差异"；标记的版本和用户所在版本不同 =
    这基本就是"以前是怎样、现在换成什么"本身。
    """
    if not marks:
        return 0.0
    bonus = MIGRATION_MARKED_BONUS
    if target_key and any(mark.key != target_key for mark in marks):
        bonus += MIGRATION_OTHER_VERSION_BONUS
    return bonus


def apply(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    intent: Intent | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按版本意图处理检索结果，并如实说明做了什么。

    返回 `(留下的结果, 报告)`。报告一定要能回答"为什么这条在/不在"——
    悄悄少几条结果，是比排错更难发现的问题。
    """
    if intent is None:
        return rows, {}
    report: dict[str, Any] = {
        "mode": intent.mode,
        "target": intent.target,
        "dataset_supports_versions": supported(),
        "excluded": 0,
        "excluded_examples": [],
    }
    if vocab := vocabulary():
        report["vocabulary"] = vocab
    if not supported():
        report["note"] = (
            "这个库没有声明版本词汇，无法核对版本适用范围，因此本次没有按版本"
            "筛选，全部结果照常返回。"
        )
        return rows, report
    if intent.target and not intent.target_key:
        report["note"] = (
            f"这个库不认识版本 {intent.target!r}，无法比较，因此本次没有按版本"
            "筛选。用该库自己的版本写法再试一次。"
        )
        return rows, report

    marks_by_chunk = load_marks(connection, [row["id"] for row in rows])
    kept: list[dict[str, Any]] = []
    for row in rows:
        marks = marks_by_chunk.get(row["id"], [])
        if marks:
            row["applies_to"] = [mark.as_dict() for mark in marks]
        if intent.excludes and (label := _excluded_by(marks, intent.target_key)):
            report["excluded"] += 1
            if len(report["excluded_examples"]) < 5:
                report["excluded_examples"].append(
                    {
                        "knowledge_id": f"K{row['id']}",
                        "title": row["page_title"],
                        "reason": f"{label} 才有，{intent.target} 里还没有",
                    }
                )
            continue
        if intent.mode == MIGRATION:
            row["score"] = round(
                row["score"] + _migration_bonus(marks, intent.target_key), 2
            )
        kept.append(row)
    if intent.mode == MIGRATION:
        kept.sort(key=lambda item: -item["score"])
    return kept, report


def describe(report: dict[str, Any]) -> list[str]:
    """把版本处理结果写成人和 AI 都能照着做的说明。"""
    if not report:
        return []
    if note := report.get("note"):
        return [note]
    mode = report["mode"]
    target = report.get("target") or "（未指定）"
    if mode == STRICT:
        if not report["excluded"]:
            return [
                f"已按 {target} 核对版本适用范围，没有结果因为版本被排除。"
            ]
        lines = [
            f"已按 {target} 严格限定：{report['excluded']} 条内容因为在 {target} "
            f"里还不存在而被排除。"
        ]
        lines.extend(
            f"  排除：{item['title']}（{item['reason']}）"
            for item in report["excluded_examples"]
        )
        lines.append(
            "如果你想看这些更新版本里的做法，把版本意图改成 compare 再问一次。"
        )
        return lines
    if mode == MIGRATION:
        return [
            "按迁移追溯排序：明确写着版本差异的内容已经提前——"
            "「什么时候改的、以前是什么」正是这类问题的答案。"
        ]
    return ["已保留全部版本的内容，每条都带着自己的适用范围，供按版本对照。"]
