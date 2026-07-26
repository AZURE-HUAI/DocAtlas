---
id: BUG-011
title: "Blender 数据集清单遗漏节点工作流所需的跨目录基础页"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: sources
labels: [blender, inventory, source-adapter, relations]
reported_at: 2026-07-26
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-008, BUG-013]
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

### 2026-07-26：重建 Blender 5.2 数据集后的复测

着色器学习流第 1、10 轮和几何节点学习流第 1 轮再次遇到跨目录基础页缺失。主流程
复现结果：

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas related 'Geometry Nodes Modifier' --json
python -m docatlas search 'Geometry Nodes Modifier' --json
```

- `related` 约 178 ms 返回 `entity_not_found`，`pending/crawled/linked` 都为空。
- `search` 约 177 ms，首条是只提到修改器的 `Attributes`。
- 固定版本官方页
  `https://docs.blender.org/manual/en/5.2/modeling/modifiers/geometry_nodes.html`
  可正常访问，标题为 `Geometry Nodes Modifier`。

因此当前通用诊断方向仍正确，而 Blender 适配器本身的跨目录枚举范围仍未完成。

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

### 2026-07-26（续）：`blender-manual-5.2` 恢复之后，枚举范围也解决了

数据集和适配器都回来了，于是上一段留下的那半件事可以做完。做的时候发现，
"Blender 缺页"和"UE 55 个未覆盖目录"根本是同一件事的两种表现，所以机制只
写了一套，记在 `BUG-013`。

先要修的是一个更靠前的错误：**Blender 适配器把"这是不是官方文档链接"和
"这一页在不在我们的收录范围"当成了同一个问题。**
`normalize_link_target` 直接调 `normalize_location`，而后者要求路径命中
`[categories]` 里声明的目录。结果是节点页链到 `interface/controls/nodes/groups`
时，这条链接被记成**站外链接**——于是"清单漏了这个目录"这件事在库里彻底
看不见：`missing_targets` 一直是 **0**。

拆开之后，同一个库里立刻显出 **27 个**被范围内正文引用、清单里却没有的手册页
（`_images/*.png` 这类素材按 `.html` 后缀挡掉了）。补抓 `Group` 两页之后
涨到 29，其中就有议题点名的两页：

```text
   2  /interface/controls/nodes/groups        ← 议题第 2 个目标
   1  /modeling/modifiers/geometry_nodes      ← 议题第 1 个目标
```

`/modeling/modifiers/geometry_nodes` 只被引用 **1 次**。这条数据直接决定了
`--min-links` 的默认值必须是 1：设成 2 就正好把议题的主目标漏掉。

收录之后重跑议题"复现"一节的命令：

| 查询 | 改前 | 改后 |
|---|---|---|
| `related "Geometry Nodes Modifier"` | `entity_not_found`，并断言"全站清单里也没有对得上的页面" | 清单里有，给出路径和 `get` 命令；抓回来后 `status=ok` |
| `related "Interface Node Groups"` | 同上 | 清单里有 `/interface/controls/nodes/groups`；该页官方标题是 `Node Groups`，按官方名查得到关系 |
| `related "Node Groups"`（抓取后） | —— | `ok`，与 `Geometry Nodes Modifier`、`Attributes`、`Group` 等**跨目录**页面成关系 |

议题期望的三条全部达成。`editors/shader_editor` 没有进来，因为目前已抓的
52 页正文里没有任何一页链到它——机制只认站点自己写下的引用，不替它补。
再抓几页 Shader Editor 相关的页面之后它会自己出现（第二轮已经出现了
`/editors/geometry_node`）。

### 2026-07-26：Shader Editor 真实学习流再次复现

本轮数据集已增长到 647 页、60 个已抓页面，inventory/content 验收均通过，但
Shader 学习流第一轮和主智能体复查仍无法取得 Shader Editor：

```text
docatlas_ask(
  dataset_id="blender-manual-5.2",
  category="shader_nodes",
  query="Shader Editor",
  version_target="5.2",
  version_mode="strict",
  token_budget=1500,
  fetch_limit=1,
  format="json"
)
```

- `ask` 返回 `status=ok`、`fetch.requested=0`，首位为 K143
  `RGB Curves Node > Examples`。
- 同条件 `search "Shader Editor"` 首位仍为 K143；前八名没有目标页。
- `related "Shader Editor"` 返回 `entity_not_found`，并声称“全站清单里也没有
  对得上的页面”“说明官方文档确实没有这一页”。
- 固定版本官网
  `https://docs.blender.org/manual/en/5.2/editors/shader_editor.html` 实测
  HTTP 200，标题为 `Shader Editor - Blender 5.2 LTS Manual`。

这不只是普通排序偏差：目标页仍未进入清单，且失败诊断把来源范围缺口错误表述为
官方文档不存在。测试方向明确要求覆盖 Shader Editor，因此重新打开本议题；原解决
历史和一跳引用闭包的边界全部保留。

## 2026-07-26 保留资格审查

本议题继续保持 `open`，但当前未解决范围收窄为 **Shader Editor 的清单覆盖和错误
诊断**：

- **会造成实际误导：** 学习流程第一步就无法取得 Shader Editor，且系统把“本数据集
  没收录”说成“官方文档确实没有”，不是无关紧要的边角差异。
- **属于既定范围：** 数据集配置把 `Shader Editor` 明确列为触发词，固定版本官方页
  也存在；因此它不是用户临时要求把 Blender 整站都收进来。
- **可由程序修复：** 来源适配器或数据集配置可以用有限的附加入口收录该页，也可以
  让诊断在没有全站证据时只说“当前清单未覆盖”。两种方案都不需要 AI 猜答案。

已经由一跳引用闭包解决的 Modifier、Node Groups 和通用缺页检测保留为历史记录，
不再作为本次未解决范围。修复不得以无限扩大到 Blender 全站为代价。

## 2026-07-27 解决记录

剩下的两件事分属两层，结论也不一样：**错误诊断是真 bug，清单覆盖不是。**

### 先测一件事：闭包到底够不够得到

把 126 个 shader 节点页全部抓下来数了一遍出站链接（只读探针，0 次失败）：

```text
指向 /editors/shader_editor 的链接：0 / 126 页
```

一条都没有。手册从不从节点页链回编辑器页——所以这不是"闭包漏了一跳"，是
**那条路根本不存在**，再抓多少页、等多少轮都不会出现。对照组：
`/modeling/geometry_nodes/introduction` **有**一条指向 `/editors/geometry_node`，
说明闭包机制本身是好的，只是几何节点那边写了、着色器那边没写。

结论：清单覆盖这一半**不是程序缺陷**，是数据集当初只声明了两个目录。没有任何
自动机制能替官网补上它从没写过的引用。

### 真 bug：系统替官网下了它没资格下的结论

`describe_lookup()` 的兜底话术是"还是没有，就说明官方文档确实没有这一页"。
DocAtlas 看得见的只有自己的清单，而清单范围 = 数据集声明的目录。伤害很实在：
用户照这句话会一遍遍改查询词，而真正该改的是收录范围，改查询词永远没结果。

改成只说得出口的那部分，并把边界一起报出来：

```text
没有找到结果，本数据集的清单里也没有对得上的页面。
这个库的收录范围是：Shader / Texture Nodes、Geometry Nodes、节点编辑器。
DocAtlas 只看得见自己的清单，不能据此断定官网没有这一页。
换成原文（en）里的官方写法再试一次；知道确切地址就直接把官方 URL 当查询词
传进来……确认官网有、而这里查不到，那是收录范围的问题（见 WORKFLOWS.md 流程 B）。
```

通用改动，四个库同时生效。`SKILL.md` 同步写明"本数据集没有 ≠ 官方没有"。

### 范围决定：一个分类可以声明多个目录

`[categories]` 原本一个分类只能写一个前缀，于是 Blender 只有两个极端选择：整个
`editors/` 都收（**201 页**，含视频序列器、摄影表、偏好设置），或者一页都不收。
适配器改为接受字符串或列表（只动 `blender_manual._category_for_docname`，核心
只遍历分类名，未受影响）：

```toml
node_editors = ["editors/shader_editor", "editors/geometry_node"]
```

**2 页**，正是议题点名、且闭包证明够不到的那两页。为什么不顺手收
`editors/texture_node/`（44 页）：贴图节点是另一套系统，不在本数据集名字
（Shader / Geometry Nodes）的范围里。

### 复测（真实库，议题"验证"一节的命令）

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas crawl --discovery-only --refresh-sitemaps
```

（`--refresh-sitemaps` 是必需的：已成功的清单入口不会重读，新目录进不来。
这一条已写进 `WORKFLOWS.md`。）

| 查询 | 改前 | 改后 |
|---|---|---|
| `ask "Shader Editor"` | `fetch.requested=0`，首位 K143 `RGB Curves Node > Examples` | 当场抓回 `/editors/shader_editor`，首位就是 `Shader Editor` |
| `related "Shader Editor"` | `entity_not_found` + "官方文档确实没有这一页" | `entity_found_but_no_relations` → 按 `next_steps` 补抓一页后 `status=ok` |
| 关系内容 | —— | `official_reference → Shader Nodes`，`confidence=1.0`，带官方链接出处 |

清单 647 → 649 页，已抓 60 → 67 页。`node_editors` 分类 2 页全部到齐。

### 回归

- 单元测试 231 → **241 全过**（含"一个分类可声明多个目录"和反向控制组：
  video_sequencer / dope_sheet / texture_node 仍在范围外）。
- 四个数据集 `validate` 的 inventory 7 项、content 16 项**全 pass**。
- 另外三个库的页/块/实体/关系计数不受影响。

## 外部关联

- GitHub Issue：
- 修复 PR：
