---
id: BUG-001
title: "`ask` 查询宽泛版本概览时超时且没有进度输出"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: query
labels: [ask, performance, diagnostics]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
related: [BUG-002]
---

# 问题

在 `epic-ue-5.8` 数据集上查询 UE 5.8 的整体更新概览时，`ask` 连续两次超过
60 秒，最终被外部超时终止，期间没有返回正文或进度信息。

## 复现

第一次查询会自动补抓，并且误将发行说明限定在 `guides`：

```powershell
python -m docatlas ask "Unreal Engine 5.8 release notes what's new major new features changes" --token-budget 6000 --category guides
```

第二次改用正确的 `community_docs` 分类，并通过 `--no-fetch` 排除网络补抓影响，
仍然复现：

```powershell
python -m docatlas ask "Summarize the most important new features and workflow changes in Unreal Engine 5.8 Release Notes across Rendering, Animation, MetaHuman, Worldbuilding, PCG, Gameplay, UI, Audio, Developer and Platform" --token-budget 6000 --category community_docs --no-fetch
```

## 实际结果

- 两次查询都超过 60 秒并被外部终止。
- 运行期间没有正文、阶段或进度输出。
- `--no-fetch` 没有消除问题。

## 对照

- `python -m docatlas search "Unreal Engine 5.8 Release Notes" --limit 20`
  约 0.5 秒返回。
- 连续执行多条 `python -m docatlas show K<id>`，每组约 2–3 秒返回。
- 数据和发行说明知识块本身可读取，问题集中在宽泛 `ask` 查询路径。

## 期望结果

- 用户能够在合理时间内得到结果，或明确知道查询仍在进行、停在哪个阶段。
- 分类选错、没有命中和真正的长时间计算能够被区分。
- `--no-fetch` 场景的性能问题能够被定位，而不是只表现为外部超时。

## 可能方向

下面仅供排查和设计时参考：

- 在无候选或分类不匹配时更早结束。
- 为较长查询提供阶段性状态、耗时记录或内部诊断。
- 评估是否需要可配置的超时、取消或部分结果。

具体采用哪种方式，应结合延迟实际发生的阶段和 CLI/MCP 的共同需求决定。

## 临时绕行

先用 `search` 定位发行说明知识块，再用 `show` 分段读取和整理。

## 调查记录

目前只确认上述现象，尚未定位具体代码原因。

## 验证

在真实的 `epic-ue-5.8` 库（199,883 页清单、25,552 知识块）上重跑议题里的两条命令：

```powershell
python -m docatlas ask "Summarize the most important new features and workflow changes in Unreal Engine 5.8 Release Notes across Rendering, Animation, MetaHuman, Worldbuilding, PCG, Gameplay, UI, Audio, Developer and Platform" --token-budget 6000 --category community_docs --no-fetch
```

- 修复前：两次都超过 60 秒被外部超时终止。
- 修复后：**0.58 秒**，退出码 0，返回 26,899 字的正文。

分阶段计时（`_fts_hits` 单档）：修复前 `phrase` 74.5 秒、`all_terms` 81.8 秒、
`any_term` 72.3 秒、`prefix` 58.8 秒；修复后同样四档合计 0.1 秒以内。

`EXPLAIN QUERY PLAN` 对照（同一条 `MATCH`，只差一个 `--category`）：

```text
不带分类  SCAN chunks_fts VIRTUAL TABLE INDEX 0:M7      → 0.01 秒
带分类    SEARCH p USING INDEX idx_pages_category        → 44.00 秒
          SEARCH c USING INDEX idx_chunks_page
          SCAN chunks_fts VIRTUAL TABLE INDEX 0:=M7
加 CROSS JOIN 后  SCAN chunks_fts VIRTUAL TABLE INDEX 0:M7 → 0.05 秒
```

回归测试：`python -m unittest discover -s tests` → 128 用例全过。

## 解决记录

**根因**：不是网络，也不是候选量，是 SQLite 的连接顺序被优化器改了。

`_fts_hits` 原来写成 `FROM chunks_fts JOIN chunks JOIN pages WHERE MATCH ? AND
p.category=?`。不带 `--category` 时优化器从全文索引出发（`INDEX 0:M7`），一次
索引查询就出结果；一带上 `--category`，它改从 `idx_pages_category` 出发，于是
`chunks_fts MATCH` 退化成 `INDEX 0:=M7`——**对每一个候选块单独跑一次全文匹配**。
`community_docs` 有 754 页、`blueprint_api` 有 7,055 页，几千次全文查询就是那
60 秒。全程没有任何异常，所以 `--no-fetch` 当然也去不掉。

**改动**：`docatlas/search.py` 的两处全文查询（`_fts_hits`、
`_legacy_section_search`）改用 `CROSS JOIN`——在 SQLite 里它的语义就是"不许重排
这两张表的顺序"，强制全文索引当外层循环。同时把 `_entity_hits` 里
`名称=? OR 别名=?` 这种跨两张表的 OR 拆成 `UNION` 两条分支（同样是会让优化器
整个放弃索引的写法）。

**没有做的事**：没有加超时、没有加进度输出、没有加取消机制。议题的"可能方向"
提过这些，但延迟的来源是一条本该是毫秒级的查询，加超时只会把一个 bug 变成一个
"功能"。查询恢复到亚秒之后，这些机制都没有存在的理由。

**留下的护栏**：`docs/ARCHITECTURE.md` 新增一节写明"检索的连接顺序不能交给
优化器"，把 0.05 秒 vs 44 秒的实测写进去——这类退化不报错，只能靠知道它存在。

## 外部关联

- GitHub Issue：
- 修复 PR：
