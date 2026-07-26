---
id: BUG-011
title: "Blender 数据集清单遗漏节点工作流所需的跨目录基础页"
type: bug
status: in_progress
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

### 2026-07-26：改为通用检测后在真实库上的实测

`blender-manual-5.2` 已按测试要求删除，来源适配器也不在仓库里，所以议题
"可能方向"的前两条（给 Blender 适配器加分类规则或附加 feed）现在无从下手。
落地的是第三条——它本来就是唯一与具体站点无关的那条。

在 `epic-ue-5.8` 上实测，同一类缺口确实存在：

```text
21,545 条链接指向清单里已有、但正文尚未抓取的页面（get / ask 就能补上）
   397 条链接指向清单里根本没有的页面
    55 个目录被别的页面链接到，而清单在该目录下一页都没有
```

最集中的几个：`/API/Runtime/Engine/GameFramework/AActor`（42 条）、
`/API/Plugins/OnlineServicesInterface/Online/ILobbies`（26 条）、
`/API/Plugins/OnlineServicesInterface/Online`（25 条）。

## 解决记录

**通用部分已解决，Blender 数据集部分无法验证。**

议题真正的伤害不是"少了几页"，而是**诊断说错了方向**：
`related "Geometry Nodes Modifier"` 报 `entity_not_found` 并断言"全站清单
没有相符页面"，用户于是一遍遍改查询词，而真正该改的是来源适配器的枚举范围。

**改动（与站点无关，任何数据集都生效）**：

- `ondemand.linked_but_unlisted()`：已抓页面的正文链接指向某一页，而这一页
  不在清单里——按名字能对上时，`inventory_lookup` 把它单独列出来。
- `related` 新增状态 `target_outside_inventory`，`next_steps` 直接给官方地址，
  并说明"要入库得扩大来源适配器的枚举范围，重抓多少次都不会有"。
- `relations.link_target_gaps()` / `validate` 的 `inventory_link_coverage`
  观察项：把"目标还没抓"和"目标不在清单"分开统计，并按目录归组。

**判据为什么是"整个目录一页都没有"**：零星几条对不上是正常噪音（改版、
拼错、非文档页），按条数设阈值只会误报。"某个目录被别的页面链接到，而清单
在这个目录下一页都没有"才是范围划漏的确定信号——Blender 的
`/modeling/modifiers/`、`/interface/controls/nodes/` 正是这个形状。

**为什么不判成 `fail`**：官方站点地图列不列某个目录是站点自己的事。真实库
实测有 55 个这样的目录，判成失败会让一个本来健康的库无缘无故变红，也会诱使
人把检查删掉。所以放进 `observations`，报数不拦路。

**仍未解决**：Blender 数据集本身的枚举范围。需要重新建立
`blender-manual-5.2` 与对应适配器后，按议题"验证"一节的四条命令重跑；
届时上面这套诊断会直接指出该补哪些目录。

## 外部关联

- GitHub Issue：
- 修复 PR：
