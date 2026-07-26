---
id: ENH-006
title: "为 MCP 提供中立的多数据集路由与结构化交换合同"
type: enhancement
status: discussion
lifecycle: unresolved
priority: high
area: integrations
labels: [mcp, multi-dataset, contract, relations, extensibility]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [ENH-002, ENH-003, BUG-009]
---

# 背景

DocAtlas 的 MCP 实现已经是薄适配层，CLI 与 MCP 共用检索核心；工具名称也没有把
Unreal 写进协议名。但当前 MCP 进程在导入时固定一个 `DATASET`、`DATASET_ID` 和
`DB_PATH`，公开工具的分类 enum 也按该数据集一次性生成。

`docatlas_list_datasets` 能列出本机配置，却不能让 `ask/search/related` 选择
`dataset_id`。当前说明明确要求：每个数据集配置一个独立 MCP server entry。结果是
协议表面中立，实际调用仍绑定单一当前库。

本轮实测：

- 已开启的 MCP 只服务 `epic-ue-5.8`。
- cppreference、Blender Shader、Blender Geometry 三个方向均无法通过该 MCP
  查询，181 次有效测试调用全部回退 CLI。
- 同一套 CLI 核心对两个非 Unreal 数据集可用，说明阻塞点在 MCP 实例路由和公开
  合同，不在检索能力本身。
- MCP 的 `ask/search/related` 最终主要返回 Markdown/文本。核心内部已有
  `dataset/product/version`、知识块、来源、按需抓取状态和关系证据等结构化数据，
  但 MCP 调用方拿不到一套稳定、可校验的类型化响应。

# 目标

定义领域中立、可版本化的数据交换合同，使 UE、Unity、Blender、cppreference 或
其他文档来源只需满足同一适配边界，就能被 MCP 客户端发现和调用。

至少应让调用方能够稳定表达和读取：

- 数据集身份：`dataset_id`、产品、版本、语言、能力与分类。
- 查询请求：query、预算、分类、是否允许补抓和抓取上限。
- 查询结果：状态、知识块、来源 URL、版本、匹配与按需抓取诊断。
- 关系结果：实体、`relation_type`、`direction`、`evidence_kind`、
  `confidence`、`note`、`evidence_url`、状态与下一步。
- 领域扩展：在不破坏通用字段的前提下表达领域特有元数据。

# “关系关联”的具体含义

这里的关系关联特指 `docatlas_related` 所公开的**知识实体关系**，不是议题编号之间
的 `related` 字段，也不是在搜索结果末尾简单附几条相似页面。调用方用实体名称或
知识 K 编号查询后，应能回答：

- 这个实体属于哪个上级、对应哪个接口、作用于什么类型；
- 两端分别是什么实体类型，关系是传出还是传入；
- 这是一条官方明确关系，还是仅凭名称得到的候选；
- 证据来自哪个页面、可信度是多少，为什么建立这条关系；
- 没有结果时，是实体不存在、实体存在但没有关系、关系目标尚未入库，还是构建失败。

UE 5.8 MCP 的实际结果说明了“通用合同 + 领域扩展”的边界：

- 通用层关系：`belongs_to`、`official_reference` 等页面归属和官方链接关系。
- Unreal 领域关系：`blueprint_cpp_api`（蓝图节点对应 C++ API）、
  `blueprint_cpp_candidate` / `node_api_candidate`（需要核对签名的候选）以及
  `targets_type`（蓝图文档中的 `Target is X`）。
- 例如 `Set Timer by Function Name` 会关联
  `UKismetSystemLibrary::K2_SetTimer`，依据是 C++ 文档中的
  `DisplayName="Set Timer by Function Name"`，置信度 1.0；同时关联
  `UKismetSystemLibrary` 目标类型，依据是蓝图正文的 `Target is` 声明，
  置信度 0.92。

因此不能把 MCP 降成一个只转发领域关系结果的薄壳。DocAtlas 的通用 MCP 关系能力
应当拥有统一工具入口、实体与边模型、关系存储、去重、全量/增量更新、查询、证据、
置信度和失败状态；它本身不写死 Unreal 的 `K2_`、`DisplayName` 或 `Target is`
规则。Unreal、Unity、Blender 等数据集通过规定接口提供标准实体、关系线索和可选
领域规则，通用关系引擎负责把它们真正建立、保存并公开为可查询的联系。

# 复合工程的职责分层

这项能力不是单一工具参数改动，而是三个边界共同组成的复合工程：

```text
数据集来源与领域扩展
  └─ 标准实体、规范化标识、关系线索、领域识别规则
                    ↓ 数据集关系接口
通用关系核心
  └─ 建立关系、验证目标、去重、存储、全量/增量更新、状态诊断
                    ↓ 统一结构化合同
通用 MCP 工具
  └─ 发现数据集与能力、选择 dataset_id、查询 related、返回证据与下一步
```

各层职责应明确：

- **通用 MCP 工具层**：所有数据集使用同一组关系工具和请求/响应结构；负责数据集
  选择、能力发现、合同版本和稳定错误语义，不为 UE、Blender 等复制工具。
- **通用关系核心**：负责所有领域都需要的实体/关系生命周期。只要适配输出符合
  合同，就能建立真实关系，而不是只能展示适配器预先拼好的文本。
- **数据集接口**：提供数据集身份和 `relations` capability，并按标准交付稳定实体
  标识、实体类型、规范化来源 URL、关系候选、证据及增量变更；具体接口名称可以后定，
  但语义必须可做合同测试。
- **领域扩展**：只放“为什么有关”的专属知识。UE 识别 `DisplayName`、
  `BlueprintReadWrite` 和 `Target is`；其他领域实现自己的规则，但不能修改通用
  MCP 工具或关系数据库结构才能接入。

目标验收标准是：新增一个符合接口的数据集后，不修改 MCP server 和通用关系核心，
便能通过同一个 `related` 工具建立并查询该数据集的关系；如果数据集没有领域扩展，
仍应至少获得官方链接、页面层级等通用关系。

# 本轮关系能力证据

关系是本轮最能检验“协议是否真的通用”的能力。无领域 `knowledge` 包时，以下五个
非 Unreal 场景全部通过：

- cppreference：`std::integral ↔ std::is_integral` 按需增量关系。
- cppreference：`std::make_unique ↔ std::unique_ptr` 按需增量关系。
- Blender Geometry：Named / Capture Attribute 完整索引关系。
- Blender Geometry：Sample Index 按需增量关系。
- Blender Shader：Bump / Normal Map / Displacement 完整索引关系。

三个方向至少检查了 42 条返回关系记录；通用关系均为
`evidence_kind=official_link`、`confidence=1.0`，方向与证据 URL 在线核对无误。
这证明关系存储和查询不必为每个下游产品重写。

Shader Group 的空关系候选经主智能体复现后排除为关系核心 Bug：目标
Interface Node Groups 页面不在 Blender 数据集清单。这个反例同样说明统一合同应
把“目标不在 inventory”“目标 pending”“实体存在但没有关系”和“关系构建失败”
表达成不同状态。

UE 关系实测还发现“可检索”与“已建立关系”不能混为一谈：
`TargetArmLength` 已存在于 `USpringArmComponent` 的 C++ 属性表，普通搜索也能
命中；但用 `Set Target Arm Length` 或 `TargetArmLength` 调用 `docatlas_related`
仍返回 `entity_not_found`。这说明 `BlueprintReadWrite` 属性与自动生成的
Getter/Setter 节点尚未成为可查询的关系实体。该能力缺口归入 `ENH-003`，MCP 合同
则必须如实保留这种状态，不能把搜索命中包装成已经存在的关系。

# 可能方向

以下只描述可评估方案，不预设最终实现：

- 让同一 MCP server 能路由多个数据集，并在每次调用显式接受 `dataset_id`。
- 或保留单实例模式，但提供标准的服务发现、稳定别名和客户端路由合同，避免每接一库
  就手工复制一套工具配置。
- 为查询和关系提供类型化结构化结果，同时保留可选的人类可读 Markdown。
- 定义数据集关系能力接口和 `relations` capability，使通用关系核心能消费标准实体、
  关系候选、证据和增量变更，而不是调用某个产品专属实现。
- 让分类、领域关系类型和附加字段通过数据集 capabilities/extension namespace
  扩展，不把 UE、Blender 等领域枚举写进通用合同。
- 对来源适配器增加合同测试：规范化站内链接、版本、语言、来源 URL 和关系目标，
  防止每个新站重复处理相同边界。

# 待讨论问题

- 多数据集选择应是每次工具参数、server resource，还是由客户端路由多个 server？
- MCP 客户端会缓存 `tools/list` 多久，动态分类 enum 如何安全更新？
- 结构化结果如何版本化，并与当前文本返回兼容？
- 领域关系类型何时需要命名空间，何时使用通用 relation type 即可？
- 如何让长时间建库操作继续留在 CLI，而查询/关系合同保持一致？

# 非目标

- 不要求 MCP 取代 CLI 或承载全站抓取、重加工等长时间任务。
- 不要求把所有领域关系压成少数含义模糊的通用类型。
- 不要求为每一种未来工具提前建立复杂插件框架。
- 不在本议题中修复具体数据集的 inventory 覆盖问题。

# 验证思路

1. 在一个 MCP 连接中发现至少 UE 与一个非 Unreal 数据集，或验证标准客户端路由能
   无歧义选择两者。
2. 对同一查询比较 MCP 与 CLI 的结构化核心字段，确保语义一致。
3. 用完整索引和按需增量各验证一次通用 `official_link` 关系。
4. 用 UE 的 `blueprint_cpp_api` 和 `targets_type` 验证领域关系通过扩展命名空间
   返回，同时不污染通用合同。
5. 明确验证四种失败：数据集不存在、实体不存在、实体存在无关系、关系目标 pending。
6. 接入一个新的非 UE 数据集，只实现数据集关系接口，不修改 MCP server、通用关系
   存储和查询代码；验证完整建图、按需增量和 `related` 查询都能工作。

## 验证

## 解决记录

## 外部关联

- GitHub Issue：
- 实现 PR：
