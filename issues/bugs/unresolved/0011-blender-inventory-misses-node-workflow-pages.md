---
id: BUG-011
title: "Blender 数据集清单遗漏节点工作流所需的跨目录基础页"
type: bug
status: open
lifecycle: unresolved
priority: high
area: sources
labels: [blender, inventory, source-adapter, relations]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-008]
---

# 问题

`blender-manual-5.2` 只枚举 `render/shader_nodes/` 与
`modeling/geometry_nodes/` 路径，遗漏了节点学习流程实际依赖的 Modifier、编辑器和
节点组接口页面。结果既会让基础问题被错误诊断为“全站清单里没有”，也会让已抓页面
指向这些基础页的官方链接无法形成关系。

## 环境

- 数据集：`blender-manual-5.2`
- 版本：Blender 5.2 LTS
- 语言：English
- 入口：CLI

## 复现

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas related "Geometry Nodes Modifier"
python -m docatlas search "Geometry Nodes Modifier" --category geometry_nodes --limit 10 --json
python -m docatlas related "Interface Node Groups"
```

在线对照：

```text
https://docs.blender.org/manual/en/5.2/modeling/modifiers/geometry_nodes.html
https://docs.blender.org/manual/en/5.2/interface/controls/nodes/groups.html
https://docs.blender.org/manual/en/5.2/editors/shader_editor.html
```

## 实际结果

主智能体于 2026-07-26 复现：

- `related "Geometry Nodes Modifier"` 用时 220 ms、退出码 1，返回
  `status=entity_not_found`、`pending_pages=[]`、`crawled_pages=[]`，并断言全站
  清单没有相符页面。
- `search "Geometry Nodes Modifier"` 用时 257 ms、退出码 0，首位是 `Fields`，
  只能从正文中偶然看到 modifier 字样。
- `related "Interface Node Groups"` 用时 171 ms、退出码 1，同样返回
  `entity_not_found`，清单内没有候选。
- 固定版本官网的 Geometry Nodes Modifier 页面实测 HTTP 200，标题为
  `Geometry Nodes Modifier - Blender 5.2 LTS Manual`。
- Shader `groups.html` 页面实测 HTTP 200，正文明确链接
  `../../interface/controls/nodes/groups.html`；目标页不在数据集清单，因此冷抓
  Shader Group 页后 `related K232` 只能得到
  `entity_found_but_no_relations`。这不是通用关系索引本身丢边，而是目标页没有
  被枚举。

Geometry 学习测试的 `GEOMETRY-R01` 也稳定复现：自然问题、关键词搜索与精确
`Geometry Nodes Modifier` 查询都没有取得官网目标页。

## 期望结果

- Blender 节点数据集应枚举完成 Shader/Geometry Nodes 基础工作流所必需的固定版本
  页面，即使这些页面位于编辑器、Modifier 或 Interface 目录。
- 清单中确实存在目标页时，查询不应误报“官方文档确实没有这一页”。
- 已抓页面中的固定版本官方链接若指向本测试范围所需页面，目标页应可被按需抓取，
  让完整索引与增量关系遵守同一合同。

## 可能方向

- 在 Blender 来源适配器中为少量跨目录基础页增加明确的分类规则或附加 feed。
- 以真实学习任务决定纳入范围，避免无边界地扩大成整站正文下载。
- 为“正文存在站内链接、但目标不在 inventory”增加验收或诊断，区分来源范围遗漏
  与目标尚未抓取。

这些方向只供调查参考，不预设最终分类设计。

## 临时绕行

直接访问固定版本官网基础页；当前数据集内没有可用的按需抓取绕行。

## 调查记录

- Blender 5.2 固定版本首页、Modifier、Shader Group 与 Interface Node Groups 页面
  均在线有效。
- 当前配置未挂领域 `knowledge` 包；本问题发生在通用页面清单和
  `official_link` 目标覆盖层，不属于领域关系缺失。
- 已有的完整索引与按需增量关系在目标页属于清单时均能产出
  `official_link`、`confidence=1.0`，因此没有把本问题归因于通用关系构建核心。

## 验证

修复后应重新运行：

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
python -m docatlas related "Geometry Nodes Modifier"
python -m docatlas related "Interface Node Groups"
```

并确认基础页进入正确分类、固定版本一致、官方链接目标可按需本地化。

## 解决记录

## 外部关联

- GitHub Issue：
- 修复 PR：
