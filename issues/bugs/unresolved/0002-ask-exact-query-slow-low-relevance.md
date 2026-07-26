---
id: BUG-002
title: "精确 `ask` 查询仍然偏慢，且结果相关性较低"
type: bug
status: in_progress
lifecycle: unresolved
priority: high
area: search
labels: [ask, performance, ranking, blueprint-api]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-001, BUG-003]
---

# 问题

较小预算、限定 `blueprint_api` 分类的精确查询仍有明显延迟和排序噪声。

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

真实库重加工验证（按 `parser_version` 分组对照）：

| | v4（旧规则） | v5（新规则） |
|---|---|---|
| 以 `Navigation` 开头的块 | 6,852 / 15,072（45%） | **2 / 10,493（0.02%）** |
| `Target is` 证据是否还在 | 在 | **在** |

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

## 外部关联

- GitHub Issue：
- 修复 PR：
