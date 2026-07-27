---
id: BUG-015
title: "分类过滤会静默排除引用闭包收录的页面（referenced 分类未接入 category 白名单）"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: categories
labels: [blender, categories, retrieval]
reported_at: 2026-07-27
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-011]
---

# 问题

数据集通过 `[inventory].referenced_category`（一跳引用闭包，BUG-011 引入的机制）收录的
页面会被赋予一个真实存在的分类（本次实测是 `blender-manual-5.2` 的 `referenced`，
标签"节点页引用到的手册页"），但这个分类没有被并入 `Dataset.categories`。这导致两个
连带问题：

1. `docatlas_list_datasets` 上报的分类列表里看不到这个分类（`blender-manual-5.2`
   只报 `geometry_nodes`、`node_editors`、`shader_nodes` 三个，实际上还有第四个）。
2. 用 `docatlas_ask`/`docatlas_search` 传 `category` 参数过滤时，凡是主题最相关的答案
   恰好落在这个"隐藏分类"里，就会被过滤掉，首位命中变成分类内相关性较低的其他页面；
   主动传 `category="referenced"` 则会被直接拒绝（"没有分类 referenced"）。

`SKILL.md` 明确建议 AI"知道分类就传 category"，而 AI 不可能知道一个连
`docatlas_list_datasets` 都不上报的分类，所以这个问题在正常使用中会被系统性触发，
不是边缘情况。

## 复现

两个独立只读测试方向（Geometry Nodes、Shader/Texture Nodes）各自用不同查询独立
复现了同一根因。

**Geometry Nodes 方向**（K184 Geometry Nodes Modifier 页面，属于 `referenced` 分类）：

```
docatlas_ask(query="Geometry Nodes modifier", dataset_id="blender-manual-5.2",
             category="geometry_nodes", token_budget=1500)
→ 首位命中错误：K228 Fields（7 条知识块里没有一条是 Geometry Nodes Modifier 本身）

docatlas_ask(query="Geometry Nodes modifier", dataset_id="blender-manual-5.2",
             token_budget=1500)  # 不传 category
→ 首位命中修正：K184 Geometry Nodes Modifier（分类标签"节点页引用到的手册页"）
```

**Shader Nodes 方向**（Node Groups 页面同样属于 `referenced` 分类）：

```
docatlas_ask(query="Node Groups", dataset_id="blender-manual-5.2", category="node_editors")
→ 首位命中降级为编辑器说明页（K210/K211），而非 Node Groups 本身

docatlas_ask(query="Node Groups", dataset_id="blender-manual-5.2")  # 不传 category
→ 首位命中修正：K189/K188 Node Groups

docatlas_search(query="Node Groups", dataset_id="blender-manual-5.2", category="referenced")
→ 直接报错："没有分类 referenced，可选：geometry_nodes、node_editors、shader_nodes"
```

**主智能体复核**（2026-07-27，走完整 MCP 调用，两种传参对照）：

```
docatlas_list_datasets(dataset_id="blender-manual-5.2", format="json")
→ categories: {"geometry_nodes":"Geometry Nodes","node_editors":"节点编辑器",
                "shader_nodes":"Shader / Texture Nodes"}
   （不含 referenced，尽管该分类有真实内容且知识块的 category 字段值确为 referenced）
```

## 期望结果

- `docatlas_list_datasets` 应完整上报数据集实际使用的全部分类，包括通过
  `inventory.referenced_category` 声明的分类。
- `category` 参数应接受这个分类值作为合法过滤条件；至少，分类过滤不应该丢失掉
  数据集里相关性最高的内容。
- AI 按 `SKILL.md`"知道分类就传 category"的建议行事时，不应该因此丢失最相关的答案。

## 根因定位（闸门 3）

`docatlas/dataset.py` 的 `load_dataset()`（约 88-112 行）构造 `Dataset.categories`
时只读取 TOML 的 `[categories]` 表（第 97 行 `categories=raw.get("categories") or {}`），
没有把 `inventory.referenced_category`（第 111 行单独读进 `inventory` 字段）声明的
分类名并入。这个字段本来就不可能有固定路径前缀——它是通过一跳引用闭包动态收录的，
配置里从一开始就写不进 `[categories]`。

这个不完整的 `Dataset.categories` 又被两处下游当作"合法分类的全集"使用：

- `docatlas/mcpserver.py:270-274` 的 `_check_category()`：`category` 参数只要不在
  `workspace.dataset.categories` 里就报错拒绝。
- `docatlas/mcpserver.py:558`：`docatlas_list_datasets` 构造数据集报告时，分类列表
  直接遍历 `sorted(workspace.dataset.categories)`。

要修，需要让这两处依据的"分类全集"也包含 `inventory.referenced_category` 声明的分类
——最直接的位置是 `load_dataset()` 构造 `categories` 字段时，把
`inventory.referenced_category`（若声明了）并入这个 dict（用 `category_labels`
里已有的标签作为显示名）。

## 复现强度 / 四道闸门结论

- **闸门1（规范调用复跑）**：通过。全部经 MCP、显式 `dataset_id="blender-manual-5.2"`、
  官方术语（"Geometry Nodes modifier"、"Node Groups"）、合理预算（1500/3000）复跑，
  加/不加 `category` 结果稳定一错一对，可重复。
- **闸门2（内容是否在库）**：通过。不传 category 时 `docatlas_ask` 能完整返回
  K184/K185（Geometry Nodes Modifier）与 K188/K189（Node Groups）正文，且经在线核对
  与 5.2 官网逐句一致；`docatlas_search`（json）确认这些知识块的 `category` 字段值
  确为 `referenced`。
- **闸门3（能否说出改哪一层）**：通过，见上"根因定位"一节，具体到文件与行号。
- **闸门4（最终输出层复现）**：通过。全部通过完整 `docatlas_ask`/`docatlas_search`
  MCP 调用复现，不是只测到内部函数或候选定位。

两个独立测试方向分别用不同查询独立复现同一根因，互相印证；主智能体用 MCP 直连
第三次复核，结论一致。

## 影响范围

目前只有 `blender-manual-5.2` 声明了 `inventory.referenced_category`，实测受影响的
只有这一个数据集；但这是 `WORKFLOWS.md` 描述的通用机制（"配
`[inventory].referenced_category`，一跳引用闭包自动收"），任何数据集用到这个功能
都会遇到同样的问题，不是 Blender 专属缺陷。

## 验证

**修复前**（实测）：库里 `referenced` 分类有 29 页（3 页已抓正文），而
`dataset.categories` 只有 3 个分类，`search --category referenced` 被 argparse
拒掉，`sample_quota` 也不给它配额。

**修复后**（实测，走真实命令行）：

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas search "Node Groups" --category referenced --limit 3
#  [1] K189 Node Groups > Usage   分类：节点页引用到的手册页   得分 129.0
#  [2] K188 Node Groups           分类：节点页引用到的手册页   得分 128.4
```

`cppreference-2026-07-26` 没声明 `inventory.referenced_category`，
`query_categories` 与 `categories` 完全一致，未受影响。

回归测试见 `ReferencedCategoryTests`（5 条），逐条做过变异检验：把
`query_categories` 改回只返回 `categories`，测试立刻变红。

## 解决记录

现象属实，根因也在 `dataset.py`，但**没有按报告建议的做法改**。

### 为什么不能并进 `Dataset.categories`

报告建议把 `inventory.referenced_category` 并进 `categories` 这个 dict。查了
一遍谁在用它，四处都是按**路径前缀**做匹配：

| 位置 | 拿 `categories` 干什么 |
|---|---|
| `sources/blender_manual.py` | `docname.startswith(prefix)` 判页面属于哪一类 |
| `sources/roblox_creator.py` | `category in dataset.categories` 认路径 |
| `crawl.py sample_quota` | 逐类分配抽样配额 |
| `validate.py` | 声明了的分类必须枚举到页面 |

`referenced` 按定义没有前缀——它收的正是"散落在声明目录之外、被正文引用到"
的页面。硬并进去要给一个值：给标签，永远匹配不上；给空串，
`path.startswith("")` **恒真**，整个库会被判成这一类。

### 改成一个派生属性，把两件事分开

```python
Dataset.categories        # 分类 → 路径前缀，枚举规则
Dataset.query_categories  # 页面可能带的分类全集，含引用闭包那一类
```

判断"这条路径属于哪一类"用前者，判断"这个分类值合不合法"用后者。

### 顺带查出同一个 bug 的另外两张脸

报告只测到 MCP 一侧。同样拿枚举规则当分类全集用的还有：

- **命令行**：`--category` 的 `choices` 来自 `CATEGORY_PATTERNS`，`referenced`
  被 argparse 直接拒掉。
- **抽样配额**：`sample_quota` 只遍历 `categories`，于是 blender 库里 26 页
  pending 的引用页**永远抽不到样**，`crawl --sample-per-category` 跑多少次
  都不会碰它们。

`CATEGORY_PATTERNS` 的全部使用者（命令行选项、导出、报表）要的都是"页面可能
带的分类"，没有一个要路径前缀——名字和取值都是错的，改名为 `CATEGORY_IDS`
并取 `query_categories`。

### 关于"传了 category 反而更差"

这一半不是检索缺陷：分类过滤的语义就是排除别的分类。真正的缺陷是这个分类
**不可见也不可用**——`docatlas_list_datasets` 不报它，传进来还被当拼错拒掉，
AI 因此不可能选对。分类可见之后，`SKILL.md` 那句"知道分类就传 category"也
一并改了措辞（不确定就别传，宁可不过滤）。

## 外部关联

- GitHub Issue：
- 修复 PR：
