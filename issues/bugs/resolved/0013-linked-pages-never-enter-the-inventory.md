---
id: BUG-013
title: "范围内正文引用到的页面永远进不了清单"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: sources
labels: [inventory, coverage, source-adapter, unreal, blender]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-011]
---

# 问题

站点给的那份清单（站点地图、`searchindex.js`、目录页）和"读懂这批内容需要
哪些页"从来不是一回事。已抓正文一直在链清单外的页面，而那些页面
**用任何方式都补不进来**：`get` 和 `ask` 只在清单里找，重复执行多少次都没用。

同一个形状在两个数据集上都成立：

```text
epic-ue-5.8       397 条链接指向清单里根本没有的页面，集中在 55 个未覆盖目录
blender-manual-5.2  0 条（诊断本身是坏的，见下）
```

Blender 那个 0 是假的。它的适配器把"这是不是官方文档链接"和"这一页在不在
我们的收录范围"当成了同一个问题，于是指向 `interface/controls/nodes/groups`
的链接被记成**站外链接**——缺口在库里完全看不见。

## 环境

- 数据集：`epic-ue-5.8`、`blender-manual-5.2`
- 入口：CLI 与 MCP 一致

## 复现

```powershell
$env:DOCATLAS_DATASET='epic-ue-5.8'
python -m docatlas validate --phase content   # inventory_link_coverage 观察项
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas related "Geometry Nodes Modifier"
```

## 期望结果

- 被范围内正文引用、清单里却没有的页面能进入清单，并可按需抓取。
- 收进来的范围有边界，不会变成"整站正文下载"。
- 不靠手工维护页面名单；判据来自站点自己写下的引用。
- 新页面的版本、语言、分类和来源地址都要正确。

## 可能方向

（原议题记录的参考方向，最终采用见"解决记录"。）

- 链接引用检测、目录覆盖检测、固定版本与站内 URL 判断。
- 数据集范围策略与有边界的链接闭包。

## 验证

### 前后对照

| 指标 | `epic-ue-5.8` 改前 | 改后 | `blender-manual-5.2` 改前 | 改后 |
|---|---|---|---|---|
| `missing_targets` | 397 | **42** | 0（诊断是坏的） | 11 |
| `uncovered_areas` | 55 | **1** | 0（同上） | 1 |
| 清单页面数 | 199,883 | 200,207（+324） | 618 | 647（+29） |
| 收进来后判错分类的 | —— | **0** | —— | **0** |
| 高引用目标 | `AActor` 42 条链接、在清单外 | 已收录，抓回后 `related "AActor"` = `ok` | `interface/controls/nodes/groups` 不可见 | 已收录并抓回 |

Blender 的 11 / 1 不是"没修干净"：收录 29 页并抓回 4 页之后，**新一轮**引用
浮现出来（`/editors/geometry_node`、`/interface/controls/nodes/parts` 等）。
闭包一次只走一跳，这正是设计要的行为。

### 收不进来的那一批（有意）

UE 剩下 18 个目标全是文档根下的扁平页，地址里分不出是教程还是社区文档。
其中两条是 `unreal-engine-5-6-documentation` 和 `unreal-engine-5-6-release-notes`
——**5.6 的页面**。一律收进来会往 5.8 的库里掺别的版本，所以宁可留在报告里。

### 地址必须重算，不能沿用链接里那一串

第一版直接把 `page_links.target_url` 当页面地址存了，实测全错：

```text
UE   324/324 条带着 ?application_version=5.5   ← 5.8 的库里存了 5.5 的引用地址
Blender 12/29 条带着 #term-Alpha-Channel 之类的片段
```

改成由适配器按路径重拼（`canonical_url`）之后，336 条全部修正，
版本 / 语言与数据集声明**逐条一致**（实测不一致数 0）。

### 回归与验收

- 单元测试 213 → **225 全过**（新增 12 条）。
- 三个数据集 `validate --phase inventory` 各 6 项、`--phase content` 各 15 项，
  全 pass。
- `cppreference-2026-07-26` 页/块/实体/关系四项计数与改动前逐条相同：
  它的正文没有指向清单外的链接，机制对它完全无感。

## 解决记录

**一套机制，两个数据集共用。** BUG-011 里 Blender 的缺页和 UE 的 55 个未覆盖
目录形状完全一样，所以没有写两份。

### 先修一个更靠前的错误

Blender 适配器的 `normalize_link_target` 直接调 `normalize_location`，
而后者要求路径命中 `[categories]` 声明的目录。**"是不是官方文档链接"是链接
的性质，"在不在收录范围"是我们的决定**，混在一起的后果是缺口彻底隐身。
拆开之后，同一个库立刻显出 27 个缺口。顺带按 `.html` 后缀挡掉了
`_images/*.png` 这类素材——它们抓回来只会是一堆没有正文的死页。

### 判据：范围内的正文自己指过去了

不是"我觉得这几页重要"。一篇我们决定收录的文档说"细节见那一页"，那一页就
是这批内容的一部分——这是站点写下的事实。边界有两层：

* **只走一跳。** 收进来的页面是 `pending`，它们自己的链接不会跟着展开。
  再往外一层是一次明确的决定，不是无声的雪球。
* **起点必须已抓过正文。** 没读过的页说了什么，我们并不知道。

`--min-links` 默认 1，这是数据逼出来的：BUG-011 点名的
`/modeling/modifiers/geometry_nodes` 只被引用 **1 次**，设成 2 就正好漏掉它。

### 分类：问适配器，不猜

| 谁 | 回答什么 |
|---|---|
| 来源适配器 `categorize_path` | 这条路径在本站属于哪一类 |
| 数据集 `[inventory] referenced_category` | 适配器认不出的，收不收、归哪一类 |
| 通用核心 | 以上两个都没有答案就不收 |

中途试过一个不需要任何配置的猜法——"跟着相邻目录已有页面的分类走"。
在真实数据上两边都翻车：

```text
Blender  /render/eevee/material_settings   → 判成 shader_nodes
         （/render/ 底下确实只有 shader_nodes，但 EEVEE 材质设置不是着色器节点）
UE       /documentation/unreal-engine/set-up-android-sdk-…  → 判成 cpp_api
         （文档根底下 C++ API 页最多，而根本身不是"一个目录"）
```

猜得看起来像，恰恰最难发现是错的，所以删掉了。适配器里那三行
（`API/`→cpp_api、`BlueprintAPI/`→blueprint_api、`node-reference/`→node_reference）
不是这一轮发现的目录，而是清单里**仅有的三个一级目录**，纯度各 100%。

### 数据兼容

新增的是一个数据集配置项和一个可选适配器函数，**没有改数据库结构**。
已存链接由 `coverage.reclassify_links()` 按新规则重判一次，本地计算不联网，
`metadata.link_targets` 记规则版本。收进来的页面 `sitemap_url` 留空——
它们不是任何清单入口列出来的，这一点必须在库里看得出来。

`validate` 的 `page_inventory_metadata` 原本要求"每一页都必须有来源站点地图"，
现在拆成两条：通用元数据（路径、分类、版本、语言）仍是硬要求；来路单独check，
允许"被正文引用进来"这第二种。写死"必须有站点地图"等于禁止第二种来路存在。

## 外部关联

- GitHub Issue：
- 修复 PR：
