---
id: BUG-008
title: "`ask` 无法按官方页面名补抓 route slug 不完全相同的 pending 页面"
type: bug
status: in_progress
lifecycle: unresolved
priority: high
area: on-demand
labels: [ask, on-demand-fetch, inventory, ranking, multi-dataset]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-002, BUG-003]
---

# 问题

页面已经在冻结清单中但正文为 pending 时，`ask` 只在整条查询规范化后与 URL 最后
一段完全一致时强制补抓。普通官方页面名、带命名空间的 C++ 符号，以及 URL 含
`.html` 的站点都很难满足这个条件；只要已有小样能提供弱相关块，查询会快速返回
错误页面，不抓真正目标页。

## 环境

- 数据集：`cppreference-2026-07-26`、`blender-manual-5.2` 临时小样
- 版本：C++20 学习基线；Blender 5.2
- 入口：CLI

## 复现

Blender 的 `Fields` 页面已在 695 页清单中，路径为
`/modeling/geometry_nodes/fields.html`：

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas ask "Fields" --category geometry_nodes --token-budget 1600 --json
python -m docatlas get "fields" --limit 1
python -m docatlas ask "Fields" --category geometry_nodes --token-budget 1600 --json
```

`.html` 路径的精确对照：

```powershell
python -m docatlas get "wave" --limit 1
python -m docatlas get "wavehtml" --limit 2
```

## 实际结果

`Fields`：

- 补抓前 `ask` 226 ms、退出码 0，首三位为 Capture Attribute、Transfer
  Attributes、Capture Attribute Inputs，没有 Fields。
- `get "fields"` 1.367 秒明确从 pending 清单抓到
  `/modeling/geometry_nodes/fields.html`，成功并新建 6 条关系。
- 补抓后同一 `ask` 176 ms，Fields 立即成为首位。

`Wave Texture Node`：

- 官方完整名称 `ask` 首位为 Image Texture，随后是 Noise 与 White Noise。
- `get "wave"` 因短词不做包含匹配而报告清单里没有页面。
- `get "wavehtml"` 1.734 秒从同一清单抓到 Shader 与 Geometry 两张 Wave 页面。

36 轮压力测试中的同类结果：

- C++ 12 个首轮 `ask` 正确首位 0/12；`std::from_chars`、RAII、`std::ranges::sort`、
  `std::jthread` 等在线精确页均存在且可访问。
- Blender Shader 正确首位 1/12；Geometry 正确首位 4/12。
- 相关查询通常在约 0.2–0.5 秒返回已有弱相关小样，表明并非网络补抓超时。

## 期望结果

- 冻结清单中存在明显对应的 pending 页面时，官方页面名和常见限定符写法能够触发
  候选补抓。
- `.html` 等站点实现细节不应成为用户必须输入的页面名组成部分。
- 若候选存在但不够确定，结果应说明没有补抓的原因和可执行的下一步，而不是把弱
  相关本地块当作完整回答。

## 可能方向

可以评估基于标题线索、路径多段、去扩展名 slug、命名空间剥离和短语覆盖率的候选
生成。补抓策略仍应有严格页数上限，避免把自然问题变成宽泛爬取。

## 临时绕行

先把问题改写成 URL 最后一段的精确 slug；对于 HTML 文档，本次测试甚至需要附加
`html`，例如 `wavehtml`。显式 `get` 后再运行原始 `ask`。

## 调查记录

- Blender `stats` 在模拟学习者测试前后保持 58 个 success，证明大量精确名称查询
  没有触发补抓。
- 主智能体使用 `get` 正向证明目标页已在 pending 清单，不是 inventory 真缺页。
- 临时数据集和来源适配器已按测试要求删除。

## 验证

`InventoryCandidateTests` 用议题里的四种真实形状建了一份小清单，逐条验证：

| 查询 | 清单里的路径 | 命中档位 | 修复前 |
|---|---|---|---|
| `Fields` | `/modeling/geometry_nodes/fields.html` | `exact_slug` | 要输入 `fieldshtml` 才行 |
| `std::from_chars` | `/cpp/utility/from_chars` | `exact_slug` | 不命中 |
| `Wave Texture Node` | `/render/shader_nodes/textures/wave.html` | `path_covers_query` | 不命中 |
| `Set Field Of View` | `/…/BlueprintAPI/Camera/SetFieldOfView` | `exact_slug` | 只在无本地结果时才触发 |
| `how do I make an object glow` | —— | 无候选 | —— |

最后一行是反向保证：概念提问**不能**触发一堆补抓。

真实库端到端（`epic-ue-5.8`）：

```powershell
python -m docatlas ask "Set Field Of View" --token-budget 1500
# 2.72 秒；日志"本地还没有这一页，正在按需抓取 5 页（蓝图 API）…"；首条即目标页
python -m docatlas ask "Blueprint Camera zoom Set Field Of View FOV" --token-budget 2500 --category blueprint_api --fetch-limit 3
# 0.49 秒；首位 Set Field Of View（修复前该页完全不出现）
```

`PageSlugTests` 另外钉死"扩展名不算名字，但名字里的点要留着"：
`fields.html` → `fields`，而 `UObject.Tick` → `uobjecttick`、`release-5.8` → `release58`。

回归测试：128 用例全过。

## 解决记录

**根因有三个，叠在一起才造成"查得到弱相关、查不到目标页"。**

1. **slug 里带着站点实现细节**。`page_slug()` 直接取 URL 末段，
   `fields.html` → `fieldshtml`，用户不可能这么打字。
2. **只认整条查询的规范化结果**。`std::from_chars` → `stdfromchars`，
   而页面地址里只有 `from_chars`；多词官方标题（`Wave Texture Node`）
   与多段路径（`shader_nodes/textures/wave`）之间也没有任何桥。
3. **有弱相关本地块就不补抓**。原判断是"本地有没有结果"，
   于是小样里几条沾边的块直接把真正的目标页挡在门外。

**改动**：

- `db.page_slug()` 去掉文档类扩展名（固定白名单 `html/htm/php/md/…`，
  避免把 `UObject.Tick` 这种名字里的点当扩展名切掉）。改了规则就得重算已有
  数据，所以加了 `SLUG_VERSION` 标记：版本一变，`backfill_page_slugs()`
  整批重算——否则同一个库里会并存两套 slug。
- `text.qualifier_tail()`（核心、与产品无关）+ `knowledge/unreal.py::query_aliases()`
  （领域）共同产出候选名，由 `search.query_names()` 按顺序去试。
- `ondemand._candidate_queries()` 把定位整理成三档：`exact_slug` →
  `slug_contains` → `path_covers_query`。第三档要求查询里**每个实词**都出现在
  路径中且实词 ≥ 2 个，所以概念提问不会误触发，命中了那一页也确实值得取。
- `context.answer()` 把补抓条件从"本地没有结果"改成
  **"本地没有一条结果的页面标题就是所问的名字"**（`_has_exact_local_hit`）。

**代价（有意接受）**：本地有结果但没有确切命中时，`ask` 现在会联网补抓，
所以这类查询从 ~0.2 秒变成 ~1–3 秒。这正是议题要求的取舍——
拿弱相关小样当完整回答的代价更大。需要纯离线时用 `--no-fetch`，
补抓页数仍受 `--fetch-limit`（默认 5）硬约束。

## 外部关联

- GitHub Issue：
- 修复 PR：
