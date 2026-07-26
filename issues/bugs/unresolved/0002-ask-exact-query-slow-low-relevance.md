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

## 外部关联

- GitHub Issue：
- 修复 PR：
