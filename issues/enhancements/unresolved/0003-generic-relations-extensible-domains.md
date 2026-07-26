---
id: ENH-003
title: "关系能力通用化并允许领域独立扩展"
type: enhancement
status: in_progress
lifecycle: unresolved
priority: low
area: architecture
labels: [relations, architecture, extensibility, knowledge-pack]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-004]
---

# 背景

DocAtlas 的关系能力用于把散落在不同页面中的相关知识连接起来。搜索解决“找到一个
知识点”，关系继续回答“它属于什么、对应什么、作用于什么、证据来自哪里”。

当前同时存在两类关系：

- 相对通用的关系，例如官方页面链接、页面归属和引用关系。
- Unreal 专属关系，例如蓝图节点对应 C++ API、`Target is` 指向的类型、
  `DisplayName` 元数据，以及未来可能补充的 `BlueprintReadWrite` 属性访问器关系。

相关实现目前分布在：

- `docatlas/crossindex.py`
- `docatlas/knowledge/unreal.py`

这个分层已经体现出“通用能力 + 领域知识”的雏形。未来接入 Unity、Blender 或
其他文档库时，关系能力的边界可能需要进一步明确。

## 目标

让关系的基础表达和查询能力可以服务不同文档领域，同时保留 Unreal 等领域按自身
知识独立扩展的空间；暂不预设必须采用插件系统或某个固定接口。

## 值得讨论的问题

`related`、关系存储、证据、方向和置信度并不只属于 Unreal；其他技术文档同样会有
接口对应、类型归属、别名、继承、生成关系和跨语言映射。

但“为什么两个实体有关”往往依赖具体领域：

- Unreal 知道蓝图、C++、`K2_`、`DisplayName` 和 `BlueprintReadWrite`。
- Unity 可能需要理解 Inspector、组件、C# API 和生命周期函数。
- Blender 可能需要理解界面操作、Operator 与 Python API。

可以讨论是否让关系的存储、查询和统一结果结构保持通用，同时允许每个领域通过
独立知识包补充自己的识别规则、关系类型和证据解释。

## 一种可能方向

以下仅用于帮助讨论，不要求照此实现：

```text
通用关系能力
├─ 实体与关系的存储
├─ 关系方向
├─ 证据来源
├─ 置信度
├─ 增量更新
└─ related / ask 的统一查询结果

领域扩展
├─ Unreal 规则
├─ Unity 规则
├─ Blender 规则
└─ 其他产品规则
```

通用层可以只负责“如何保存、查询和表达关系”，领域层负责“根据什么规则建立关系”。
领域扩展是否需要正式插件接口、沿用当前 Python 知识包约定，还是只保留少量回调，
应由真实扩展需求决定，暂时不必提前建设复杂框架。

## 可供评估的维度

- 哪些关系真正跨领域通用，哪些只是名称相似但语义不同？
- 通用层是否需要认识具体关系类型？
- 领域关系是否需要命名空间？
- 证据和置信度能否继续使用统一合同？
- 没有领域知识包时，是否仍能获得官方链接和页面层级等基础关系？
- 领域包缺失或版本不兼容时，应降级运行还是报错？
- 全量 `cross-index` 与增量 `link_pages` 如何保持一致？
- CLI、MCP、`ask` 和 `related` 是否共享关系状态与错误语义？
- 如何验证领域扩展不会生成大量错误关系或清除其他领域关系？
- 新版本数据集能否复用同一领域包，同时允许版本差异覆盖？

## 验证思路

与其先确定抽象形式，可以先用真实案例验证边界：

- Unreal：蓝图节点 ↔ C++ API。
- Unreal：`BlueprintReadWrite` 属性 ↔ 自动生成的 Getter/Setter。
- 通用：页面中的官方链接和父子归属。
- 第二个非 Unreal 数据集：选少量明确关系，观察现有接口哪里无法复用。

如果第二个领域只需实现少量函数就能接入，当前分层可能已经足够；如果必须修改
大量核心代码，才说明需要更正式的扩展合同。

## 非目标

本议题暂不要求：

- 立即设计完整插件系统。
- 预先统一所有产品的关系类型。
- 确定最终接口、类名或目录结构。
- 为尚未接入的产品编写推测性抽象。
- 一次性重构现有 Unreal 规则。

建议保留一个判断原则：

> “连接、存储、查询和解释关系结果”可以尽量通用；
> “判断两个实体为什么有关”应允许由具体领域独立扩展。

## 验证

本轮没有引入插件系统，而是按议题"验证思路"说的，用真实案例去撞边界。
撞出来的结论有测试守着（`GenericRelationLayerTests`）：

- 没挂领域知识包时，`RELATION_LABELS` / `EVIDENCE_LABELS` 仍给出通用标签，
  `expected_evidence_kinds()` 退回 `['official_link']`，
  `query_names()` 正常工作——**基础关系与检索完全可用**。
- `dataset.knowledge_hook(None, …)` 取任何能力都安静地拿默认值（原有用例）。

本轮实际新增的领域扩展点：`query_aliases(query) -> list[str]`
（BUG-004 / BUG-008 需要）。它是一次真实的接入检验：

| 问题 | 答案 |
|---|---|
| 加一个领域规则要改多少核心代码？ | 核心加 6 行（`search.query_names()` 里取钩子、去重） |
| 领域包要实现什么？ | 一个普通函数，`knowledge/unreal.py` 里 15 行 |
| 没实现会怎样？ | `knowledge_hook` 返回 `None`，检索照常 |

回归测试：128 用例全过。

## 解决记录

**本议题保持 `discussion`，不关闭。** 它的非目标写得很清楚：不要求现在设计完整
插件系统、不要求预先统一关系类型、不要求为尚未接入的产品写推测性抽象。
本轮没有出现需要突破这条边界的真实需求，因此**不做结构性改动是有意的决定**，
不是遗漏。

**本轮为它积累的证据**：

1. 新增 `query_aliases` 钩子时，核心只加了 6 行、领域包 15 行，
   `knowledge_hook()`（`getattr` + 默认值）这套约定够用。议题里的判断标准是
   "如果第二个领域只需实现少量函数就能接入，当前分层可能已经足够"——
   这一次的答案是足够。
2. 反向也验证了：`text.qualifier_tail()`（限定名取末段）**不该**进领域包，
   因为 C++、Python、Java、C# 都有限定名。判断依据是"这条规则依赖某个产品的
   行话吗"，而不是"它现在被谁用到"。
3. `official_link` 关系、页面层级、证据与置信度这套表达，在没有知识包时完整可用，
   已有测试钉死。

**留给以后的触发条件**（满足任意一条再重新讨论）：

- 出现第二个需要**自定义关系类型**的领域包（不只是别名规则）；
- 领域关系类型开始互相撞名，需要命名空间；
- 领域包需要在 `cross-index` 之外的时机参与增量更新。

在那之前，判断原则不变：**连接、存储、查询、解释关系尽量通用；
"为什么两个实体有关"允许领域独立扩展。**

### 2026-07-26：cppreference 与 Blender 的真实关系合同验证

本轮满足了议题原先提出的“第二个非 Unreal 数据集”触发条件，而且同时覆盖两个
领域。两者均未配置领域 `knowledge` 包，只使用通用官方链接关系：

- cppreference 的 `std::integral ↔ std::is_integral` 与
  `std::make_unique ↔ std::unique_ptr` 完成按需增量关系验证。
- Blender Geometry 的 Named / Capture Attribute 完成完整索引验证，Sample Index
  完成按需增量验证。
- Blender Shader 的 Bump / Normal Map / Displacement 完成完整索引验证。

至少 42 条返回记录使用同一组字段：
`relation_type`、`direction`、`evidence_kind`、`confidence`、`note`、
`evidence_url`。所有通用关系均为 `official_link`、置信度 1.0，方向和证据 URL
在线核对正确，没有出现实体、方向、证据或置信度表达错误。

适配器准备阶段也暴露了标准合同的价值：cppreference 与 Blender 原文使用相对链接；
在来源层规范化成绝对固定版本 URL 前，内容能检索但关系数为 0，
`relation_evidence_coverage` 会失败。规范化后，两库无需修改关系核心即可产出关系。

另一个边界是 inventory 覆盖：Shader Group 页链接到 Interface Node Groups，但
目标页不在 Blender 数据集清单，所以实体存在却无关系。这应归因于来源清单范围
（`BUG-011`），不能误判为通用关系或领域扩展失败。

结论更新：现有通用关系模型已经跨领域复用成功；下一步更值得讨论的是如何通过 MCP
以类型化、中立合同公开这些字段，以及如何标准化适配器与 inventory 的能力声明，
详见 `ENH-006`。

### 2026-07-26：UE 关系工具的实体粒度缺口

通过已开启的 UE 5.8 MCP 直接调用 `docatlas_related`：

- `Set Timer by Function Name` 能返回蓝图节点 ↔
  `UKismetSystemLibrary::K2_SetTimer` 的 `blueprint_cpp_api` 关系，以及节点 →
  `UKismetSystemLibrary` 的 `targets_type` 关系；关系方向、证据、置信度和出处完整。
- `TargetArmLength` 已存在于 `USpringArmComponent` 的 C++ 属性表，普通搜索可命中；
  但 `Set Target Arm Length` 和 `TargetArmLength` 都返回 `entity_not_found`。

这说明现有关系系统已经能连接“有独立页面的蓝图节点和 C++ 符号”，但尚未把类页面
表格中的属性提升为独立实体，也没有显式建立
`BlueprintReadWrite property ↔ generated Getter/Setter node` 关系。查询别名解决的
只是“搜得到属性所在页面”，不能替代关系图中的实体和边。

后续实现这一关系时，UE 领域包负责识别 `BlueprintReadWrite` 及自动访问器命名规则；
通用核心仍只负责存储和查询实体、方向、证据与置信度，MCP 按 `ENH-006` 的中立合同
原样公开。

### 2026-07-26：触发条件满足，正式合同已落地

上一轮给自己留的三条重开条件，这一轮中了第三条——**领域包需要在
`cross-index` 之外的时机参与增量更新**，而且是以 bug 的形式暴露的：
`build_relations`（全量）和 `link_pages`（增量）是两个各写一遍的函数，
后者只补了三种领域关系里的一种。于是"全都跑过一遍"的库和"边用边补"的库
内容不一样，而且没有任何报错。

同时 `ENH-006` 提出了硬性验收：**新增一个符合接口的数据集，不修改 MCP
server 和通用关系核心，就能建出并查到该数据集的关系**。旧写法满足不了——
领域包拿到的是裸 `sqlite3.Connection`，自己写 SQL 往 `relations` 表插。
"接一个新数据集"实际等于"先学会我们的表结构"，那不叫接口。

**落地的合同**（`docatlas/relations.py`）：

```python
def relation_rules(graph):
    for source, target, name in graph.name_matches("ui_node", "api_symbol"):
        yield RelationCandidate(source, target, "node_api", "exact_name", 0.9)
```

`graph` 提供四个只读原语，实体只能从它们拿到：`entities()` 遍历、`find()`
按名字/别名解析、`name_matches()` 成批按名字或别名对起来、`texts()` 遍历
正文。**领域包不写 SQL、不认识表结构、不需要知道 entity id、也不知道
origin。**

通用核心负责：解析实体、验证目标存在、挡撞名（一个名字对上超过 8 个就整组
丢弃）、拒自环、夹置信度到 [0,1]、去重、存储、全量/增量、失败诊断。
`relations.origin` 记录关系是谁建的，全量重建按 origin 清理——领域包因此
不必再维护一张"我会产出哪些 evidence_kind"的清单，漏写一项就会留下一条
永远删不掉的死关系。

同一个 `relation_rules` 现在同时服务全量和增量（`graph` 自带范围），
上面那个全量/增量不一致的 bug 从结构上消失了。

**接入成本的实测**（这正是议题定的判断标准）：

| 问题 | 旧写法 | 新合同 |
|---|---|---|
| `knowledge/unreal.py` 关系部分行数 | 249 行，含 6 段手写 SQL | 116 行，0 段 SQL |
| 新领域包要写什么 | 学表结构 + 写 INSERT + 自己维护清理清单 | 一个生成器函数 |
| 核心要改吗 | 增量路径要单独再写一遍 | 不改 |

**等价性验证**：在真实库上只读跑新规则，与旧 SQL 产出的关系逐条对照——
**57 条，0 漏 0 多，置信度逐条一致**，耗时 0.27 秒。行为完全没变，
变的只是谁来写。

**新领域包接入验证**：测试里有一个假产品的领域包 `ToyDomain`，只实现
`relation_rules`，不碰核心也不碰 MCP，建出的关系带正确的
`relation_type` / `evidence_kind` / `confidence` / `note` / `origin`。
这就是议题问的"如果第二个领域只需实现少量函数就能接入，当前分层可能已经
足够"——现在是**一个函数**。

**仍未做**：`BlueprintReadWrite 属性 ↔ 自动生成的 Getter/Setter` 关系。
这需要把类页面表格里的属性提升为独立实体（实体抽取层的事，不是关系层），
与本轮的合同工作正交。新合同不阻碍它：等属性变成实体之后，UE 包加一条
`relation_rules` 分支即可，核心不用动。

## 外部关联

- GitHub Issue：
- 修复 PR：
