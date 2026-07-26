---
id: BUG-002
title: "精确 `ask` 查询曾出现性能与排序问题"
type: bug
status: closed
lifecycle: resolved
priority: high
area: search
labels: [ask, performance, ranking, blueprint-api]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-001, BUG-003, ENH-005, ENH-007, ENH-008]
---

# 问题

本议题最初把核心检索性能、精确页面排序、自然问句理解和版本意图混在一起记录。
复核后的责任边界是：

- 页面已经本地化，输入精确官方标题或符号仍异常缓慢、排错或返回损坏内容，属于
  DocAtlas 核心。
- 理解自然问句、提炼官方术语、识别迁移意图、按版本组织多个结果和自动改写重试，
  属于 AI/Skill 中间层。

原始核心性能和面包屑噪音已有实现与验证记录。本轮没有复现出独立的“精确官方标题
已本地化但仍排错”故障，因此不能再用自然问句首位命中率维持该 Bug 为未解决。

## 复现

```powershell
python -m docatlas ask "Blueprint Camera zoom Set Field Of View FOV" --token-budget 2500 --category blueprint_api --fetch-limit 3
```

## 实际结果

- 命令成功返回，但实测耗时约 39.2 秒。
- 返回 10 条知识块。
- 靠前结果包括 OpenCV Camera View Info、ICVFX Camera Depth Of Field、
  Geometry Script Render Capture Camera 和 Live Link Camera。
- 没有优先返回查询中明确写出的 `Set Field Of View` 蓝图节点。

这说明性能问题不只发生在宽泛、6000 token 的概览查询上。

## 期望结果

- 明确输入的精确节点名能够优先于只共享部分词语的周边内容。
- 小范围、已限定分类的查询在合理时间内完成。
- 发生明显延迟时，开发者能够判断耗时来自哪个阶段。

## 可能方向

- 评估精确标题、规范化名称和完整短语是否需要更强的排序信号。
- 对比补抓、候选召回、关系扩展和上下文裁剪各阶段耗时。
- 用一组真实蓝图节点查询验证调整是否改善首位命中，同时避免伤害概念查询。

这些方向不预设具体排序公式或性能指标，应以回归查询集的结果为准。

## 临时绕行

先用 `search` 搜索更短的正式名称，再对明确的知识 ID 使用 `show`。

## 调查记录

### 2026-07-26：cppreference 与 Blender 小样

> 责任归属修正：本节大量使用普通学习问法，适合作为 AI/Skill 查询规划证据，
> 不能整体当作 DocAtlas 精确排序缺陷。

在两个临时非 Unreal 数据集上又完成 36 轮真实学习测试。性能大多已不是主要矛盾：
全部 CLI 命令中位约 0.2–0.24 秒，但首位相关性仍低。

- C++ 12 个首轮 `ask` 正确首位 0/12。`std::unique` 被 `unique_ptr` 结果占据，
  `std::ranges::sort` 被语言导航和 `[[likely]]` 内容压过；已抓页面中的公共导航
  模板大量进入知识块并主导 any-term 排序。
- Blender Shader 正确首位 1/12。明确查询 Principled BSDF 时，Principled Hair
  BSDF 多轮排在普通 Principled BSDF 之前；Geometry Nodes 与 Legacy Texture
  页面也会压过 Shader Nodes。
- Blender Geometry 正确首位 4/12，其中只有 1 轮完整、干净地回答主要问题。

这些结果与 BUG-008 的 pending 补抓缺口有关但不完全相同：即使目标页已经显式抓取，
Principled Hair 与公共导航块仍能排在更精确正文之前，因此保留为本议题的新增排序
证据。在线目标页均按对应版本核对可访问。

### 2026-07-26：固定版本重建后的版本语义复现

> 责任归属修正：判断“限定当前版本”与“从旧版本迁移”的不同意图，需要 AI 综合
> 问句和多页内容；本节转交 ENH-007，不再作为核心排序 Bug 的未解决条件。

重新建立 `cppreference-2026-07-26` 与 `blender-manual-5.2` 小样并完成 36 轮后，
主智能体确认普通本地调用约 0.2 秒，主要残余问题仍是相关性和版本语义：

- 精确 `std::optional` 已能在 221 ms 内正确首位命中标准页面，说明单个精确实体
  的基础路径可用。
- 查询
  `In strict C++20, should I return std::optional when a map lookup may find no value?`
  在 199 ms 内返回，但首位是 C++23 的
  `std::flat_map<...>::contains`，`std::optional` 只排第二，实验版 optional 也混在
  前四位。固定 C++20 学习语境没有约束候选版本。
- Blender 5.2 查询
  `In Blender 5.2, what replaced the old Transfer Attribute node?`
  在 204 ms 内返回，首位为当前版复数 `Transfer Attributes Node`。固定版本官网的
  `Sample Index Node` 页面明确写着它重现的是 Blender 3.4 以前旧单数
  `Transfer Attribute` 的行为；新复数节点不是该版本迁移问题的答案。
- C++ 轮次还稳定复现：C++20 虚析构问题被 C++26 reflection
  `std::meta::has_virtual_destructor` 压制；map/optional 问题被 experimental
  optional 或 C++23 ranges 条目压制。

这些结果证明当前缺口不是冷抓网络耗时，而是目标已本地化之后，查询里的版本/标准
限定没有可靠进入排序和上下文组织。

### 2026-07-26：重建数据集后的纯排序对照

> 责任归属修正：该查询是自然问句；精确对照 `Reference initialization` 能正确
> 首位命中。因此它证明 AI 应先落成官方术语并重试，不证明精确核心检索仍坏。

三位学习者在 C++20、Blender 着色器节点、Blender 几何节点各完成 12 轮，共
36 轮。正确主题首位命中 13/36（C++ 3/12、着色器 5/12、几何节点 5/12）。
全部 MCP 调用成功，常见延迟只有几十毫秒，在线目标页也都有效，因此这批错排不能
归因于超时或官网失效。

主智能体用一个已经本地化、完全不触发补抓的样例把排序问题单独复现出来：

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas ask 'How does reference initialization work in C++20?' --category language --token-budget 1500 --json
```

- 查询约 219 ms，`fetch.requested = 0`。
- 首条错误地落到 `Aggregate initialization`。
- 正确的 `Reference initialization` 只排在第 4、5 条。
- 对照查询 `Reference initialization` 能把正确页面排到第一。

这说明自然句式与多个初始化主题共享词语时，泛化页面仍可能压过已经在库中的目标
主题；它不再属于 BUG-008 已处理的 pending 候选定位范围。

## 验证

```powershell
python -m docatlas ask "Blueprint Camera zoom Set Field Of View FOV" --token-budget 2500 --category blueprint_api --fetch-limit 3
```

| | 修复前 | 修复后 |
|---|---|---|
| 耗时 | 39.2 秒 | **0.49 秒** |
| 首位结果 | OpenCV Camera View Info | **Set Field Of View** |
| 目标节点是否出现 | 完全没有 | 第 1 条 |

前 6 条依次是 Set Field Of View、Make Open CVCamera View Info、Set First Person
Field Of View、Get Effective Field Of View、Set Enable First Person Field Of
View、Set Use Field Of View for LOD——查询里明确写出的那一个排到了首位，
其余相关节点紧随其后。

回归测试：128 用例全过，其中 `QualifierAndAliasTests`、`InventoryCandidateTests`
覆盖名称扩展与候选定位。

## 解决记录

**根因有两个，都不是排序公式的问题。**

1. **慢**：和 BUG-001 同一个根因（`--category` 让全文索引不再当外层循环）。
   修复后 39.2 秒 → 0.49 秒。
2. **不准**：目标页 `/BlueprintAPI/Camera/SetFieldOfView` 当时**根本不在本地**，
   状态是 `pending`。原来的 `ask` 只在"本地一条结果都没有"或"整条查询规范化后
   与某个 slug 完全一致"时才补抓；这条查询两个条件都不满足，于是拿一堆沾边的
   Camera 页面凑了个答案。排序再怎么调，也排不出一条不存在的记录。

**改动**：
- `docatlas/search.py`：`CROSS JOIN` 修性能（同 BUG-001）。
- `docatlas/context.py` 新增 `answer()`，把"要不要补抓"的判断从
  "本地有没有结果"改成 **"本地有没有一条结果的页面标题就是用户问的名字"**
  （`_has_exact_local_hit`）。弱相关的本地块不再能挡住真正的目标页。
- 候选定位改成三档（见 BUG-008），"Set Field Of View" 这样带空格的官方名
  能命中 `exact_slug`。

排序权重一个都没动。首位命中的改善来自"目标页现在真的在库里了"，
而不是把某类结果人为提权——后者会伤到概念查询。

### 2026-07-26：排序噪音的真正来源是面包屑

**第一个假设是错的，先记下来。** 议题写的是"公共导航模板大量进入知识块并
主导 any-term 排序"，于是先按"同一份内容重复出现在很多页"去查，打算按
`content_hash` 跨页重复度降权。实测：25,565 个块对应 25,559 个不同的
`content_hash`，**每一份内容都只属于一页**，没有任何跨页重复。这条路根本
不存在，改了等于没改。

真正的机制是**面包屑**：

| 实测项 | 数值 |
|---|---|
| 知识块总数 | 25,565 |
| 正文以 `Navigation` 面包屑开头 | 6,854（26.8%） |
| 其中被判成 `navigation` 类型的 | **1** |

小节层面分类是对的（`Navigation` 小节确实标着 `navigation`），但小页面的
若干小节会被合并成一块，合并后整块按"名副其实"的类型算，于是标成
`parameters` / `returns`，导航正文跟着进了全文索引。一条面包屑带进去的是
整站的目录名和两三条 URL，FTS 分词后每一页都含有 `blueprintapi`、`camera`
这类词——`Blueprint Camera zoom Set Field Of View FOV` 于是命中该目录下的
每一页，命中的全是面包屑，正文一个词都没对上。实测该查询前 8 条**全部**落在
`any_term` 档，正是议题描述的症状。

**改动**：`chunking.strip_breadcrumbs()` 只删"两个以上链接用 `>`/`›`/`»`
串成的一整行"。正文里的行内链接、孤零零一个链接都不动；紧跟在面包屑后面的
`Target is X` 也留着——那是 `targets_type` 关系唯一的证据来源，一起删掉会让
一整类关系归零。`CHUNKER_VERSION` v4 → v5。

**真实库验证（10,766 页已全部重加工到 v5）**：

中途按 `parser_version` 分组做过一次对照，得出"45% → 0.02%"——**那个数字是
错的，别信**。重加工按页面 id 顺序推进，当时 v5 里几乎全是没有面包屑的教程页，
带面包屑的蓝图 API 页还没轮到，两组根本不是同一批页面。教训还是那一条：
对照组要是同一批输入，否则量到的是抽样偏差。

同一批小节分别按新旧规则算，才是公平的前后对照（抽样 4,000 个 `blueprint_api`
小节）：

| | 数值 |
|---|---|
| 含面包屑的小节 | 1,396 / 4,000（34%） |
| 进全文索引的字符数 | 503,102 → 423,012（**少 15%**） |
| 全库 `content_text` 含 `BlueprintAPI/` 的块 | **0**（改前每个带面包屑的块都有） |
| 全库 `Target is` 证据块 | 1,414（`targets_type` 关系的来源，完好） |

目录名噪音清零了，索引也小了一圈。但要说清楚：**议题里那条复现查询的排序
并没有肉眼可见的变化**——`Set Field Of View` 上一轮就已经排在首位，其余几条
（`Set First Person Field Of View`、`Get Effective Field Of View`…）本来就是
靠自己的标题命中的，本来就该在。这次拿掉的是"整个 `/Camera/` 目录下每一页都
因为面包屑而参与匹配"这件事，它降低的是噪音基数，不是把某一条顶上去。

全量验收：`validate --phase content` 14 项全 pass（含 `chunk_parser_version`
与 `relation_evidence_coverage`）；关系 17,830 条，其中领域关系 57 条与重加工
前逐条一致。

### 2026-07-26：版本语义**有意未做**，不是遗漏

议题的两个复现案例要求**相反**的行为，任何"优先当前版本"的排序规则都会
修好一个、弄坏另一个：

- C++20 那条：希望压低提到 C++23 / C++26 的页面。
- Blender 那条：正确答案是 `Sample Index`，而它之所以正确，**恰恰因为它
  讲的是 Blender 3.4 以前的旧行为**。按"优先 5.2、压低其他版本"去排，
  正确答案会被压下去。

用户问的是"从旧版本迁移过来该用什么"，此时提到旧版本的页面是答案而不是噪音；
只看版本号无法把这两种情形分开。加上 `cppreference-2026-07-26` 与
`blender-manual-5.2` 两个数据集已按测试要求删除，**任何排序改动都无法验证**。

因此这一半保持未解决。凭猜改排序的代价，这个项目已经付过一次：BUG-001/002
第一轮的结论就是"排序再怎么调，也排不出一条不存在的记录"。

重新开工的前提：重建那两个小样数据集，并先固定一组回归查询（至少覆盖
"限定当前版本"和"从旧版本迁移"两类问法），再谈改排序。

## 2026-07-26 责任边界修正与封存

- 原性能问题已有 39.2 秒 → 0.49 秒的验证结果。
- 原面包屑污染已清除，并通过全量内容与关系验收。
- 本轮精确官方标题控制查询能够正确首位命中。
- 剩余失败集中在自然问法、同义表达、跨语言和版本意图，应由 AI/Skill 负责查询
  翻译、拆解、重试与答案组织，分别由 ENH-005、ENH-007 跟踪。

因此本议题按范围混杂、剩余部分已被上层议题接替而封存为 `closed`。若以后出现
“目标页已本地化，输入精确官方标题仍稳定排错或异常缓慢”的新证据，应另行建立范围
单一的核心检索 Bug，而不是把自然语言失败重新塞回本档案。

## 外部关联

- GitHub Issue：
- 修复 PR：
