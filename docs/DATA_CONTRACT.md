# DocAtlas 数据合同

版本：3  
适用范围：任何数据集。下文以内置的 `epic-ue-5.8`
（Epic Developer Community 的 Unreal Engine 5.8 英文官方文档）为例。

站点特有的部分（站点地图数量、接口形式、分类名）由来源适配器和
`datasets/*.toml` 决定；分层、字段和保证是所有数据集共同遵守的。

## 1. 处理阶段

### 阶段 A：页面目录（Inventory）

只读取官方站点地图，不抓正文。必须满足：

- 适配器认领的子站点地图全部成功（`epic-ue-5.8` 是 424 个）。
- 页面按规范化 `path` 去重。
- 每条页面记录包含 `url`、`path`、分类、版本、语言、父路径、路由深度和所属站点地图。
- 生成 `site_inventory.jsonl`、摘要和 SHA-256；失败站点地图为 0 时才标记 `complete`。

目录未达到 `complete` 时，不允许启动全量正文阶段。

### 阶段 B：原始正文（Raw / Bronze）

- 使用适配器指定的取内容方式（`epic_ue` 用网页自身调用的 `document.json` 接口）。
- 原始响应以 zlib 压缩保存。
- 以 `page_id + content_hash` 追加留档；更新页面不会覆盖历史原文。
- 保存抓取时间、原始页面 URL 和内容哈希。

### 阶段 C：知识加工（Normalized / Silver）

层级固定为：

```text
页面 Page
└── 逻辑小节 Section
    └── 检索知识块 Chunk
```

页面负责版本、文档类型和来源；逻辑小节对应官方标题层级；知识块是 AI 实际检索单位。

### 阶段 D：AI 路由（Gold）

- 知识块全文索引。
- 实体、别名和交叉关系图。
- 按 token 预算生成上下文包。
- Markdown 分片、逐页清单、质量报告。

## 2. 页面字段

| 字段 | 含义 |
|---|---|
| `id` | 本地稳定页面 ID |
| `url` | 带 `application_version=5.8` 的 Epic DOC 原网址 |
| `path` | 规范化官方路由 |
| `title` | 官方标题 |
| `description` | 官方摘要 |
| `category` | `guides`、`blueprint_api`、`cpp_api` 等 |
| `source_type` | 官方标注的数据源类型 |
| `document_type` | landing、article、API 等官方类型 |
| `ue_version` | 数据集的版本（`epic-ue-5.8` 是 `5.8`） |
| `locale` | 数据集的语言（`epic-ue-5.8` 是 `en-US`） |
| `parent_path` | 路由父级 |
| `route_depth` | 路由深度 |
| `sitemap_url` | 发现该页的官方站点地图 |
| `updated_at` | 官方标注的文档更新时间 |
| `content_hash` | 原始响应哈希 |
| `parser_version` | 这一页由哪版切分规则加工的 |
| `status/error` | 抓取状态和可观察错误 |

## 3. 知识点类型

标题优先按官方结构识别：

- `summary`：API/节点摘要
- `overview`：概念或教程概览
- `signature`：声明、语法、Header、Include
- `parameters`：输入、参数、属性
- `returns`：输出、返回值、结果
- `remarks`：注意事项、限制、警告
- `examples`：示例、用法、步骤
- `navigation`：官方层级导航
- `references`：相关文档、前置知识
- `details`：无法可靠归入以上类型的正文

不根据正文臆造“注意事项”或“示例”；官方没有对应内容时，该类型可以不存在。

## 4. 分块规则

- 先按官方 H1–H6 标题拆逻辑小节。
- **同一父标题下相邻的小段落先合并**，再考虑切分：单独一个只有表头的
  `Inputs` 段落成不了一条能用的知识。合并时保留子标题，不抹掉结构。
- 每个知识块目标约 550 tokens，**硬上限 900 tokens**（`validate --phase content` 会强制检查）。
- 优先在段落边界切分。
- 切分后剩下的一小截并回上一块，不留孤块。
- 标题名和内容对不上的小节（如 Epic 蓝图页的 `Navigation`，里面装着节点描述
  和 `Target is X`）照常并入知识块，但不由它决定合并块的知识类型。
- Markdown 表格保持表头并按行拆分，不把单行参数拆开。
- 代码块尽量保持完整；超长代码才按行拆，并补回 fenced code 标记。
- **不做上下文重叠**：重叠会让 `content_hash` 失去唯一性，破坏上下文包的去重，
  还会把全文索引撑大。需要相邻内容时顺着 `prev_chunk_id` / `next_chunk_id` 取。
  每块只重复短小的 `context_prefix`。
- 每块记录 `parser_version`，改规则时能精确查出哪些还没升级。
- 每块保存页面标题、完整标题路径、知识类型、版本、分类和精确来源 URL。
- 每块末尾重复 `DOC 原出处`，避免脱离页面后丢失证据。

## 5. 交叉索引

### 实体类型

- `blueprint_node`
- `cpp_symbol`
- `python_api`
- `editor_node`
- `guide`
- `document`

实体保存规范名称、标准化名称、别名、限定名、模块、所有者类型和来源 URL。

### 关系与证据

| 关系 | 证据 | 默认置信度 |
|---|---|---:|
| `official_reference` | 官方正文明确链接 | 1.00 |
| `belongs_to` | 官方 Navigation 链接 | 1.00 |
| `parameter_type` / `return_type` | 参数或返回值小节中的官方类型链接 | 1.00 |
| `signature_reference` | 声明或签名中的官方链接 | 1.00 |
| `targets_type` | 正文明确写出 `Target is ...` | 0.92 |
| `blueprint_cpp_api` | C++ 文档的 Unreal `DisplayName` 元数据与蓝图节点显示名完全一致 | 1.00 |
| `blueprint_cpp_candidate` | 名称标准化完全一致 | 0.82–0.90 |
| `node_api_candidate` | 节点与 API 名称完全一致 | 0.82–0.90 |

候选关系必须标明它不是官方等价声明。名称过短、候选超过 8 个或匹配过于宽泛时不建关系，
避免生成看似丰富但错误的知识图谱。

## 6. 检索与 AI 上下文保护

### 检索：五档回退

一次查询从最精确到最宽松依次尝试，命中足够就不再下探，各档结果合并后统一排序：

| 档位 | 做法 |
|---|---|
| `entity` | 实体名或别名标准化后精确命中 |
| `phrase` | FTS 完整短语 |
| `all_terms` | FTS 所有关键词都出现 |
| `any_term` | FTS 任一关键词（剔除英文停用词），BM25 排序 |
| `prefix` | 最后一个关键词按前缀匹配 |

排序在 BM25 之外叠加：知识类型权重（`signature` / `summary` / `parameters` /
`returns` 优先，`navigation` 压后）、标题与标题路径命中率、质量分；
超过 300 tokens 的 C++ 成员罗列会被显著压低。

每条结果都带 `match_stage` 与 `score`，便于判断可信度。

### 上下文包：硬预算

默认预算 3000 tokens，规则如下（`docatlas/context.py`）：

1. 主结果使用 80% 预算；**累计超预算即停止，不允许最后一条越界**。
2. 同一页面最多取 2 个知识块，避免一篇长文占满上下文。
3. `content_hash` 相同的知识块只保留一份。
4. 查询精确命中实体时，只从该实体自身页面取正文，不带入同类的兄弟函数。
5. 一跳关系**只输出指针**（名称、实体类型、证据、置信度、可展开的知识 ID），
   不展开正文；置信度低于 0.80 的关系不进上下文。
6. 关系按用途排序：`blueprint_cpp_api` > `targets_type` > 参数/返回值类型 >
   候选关系 > `belongs_to`；`/API` 这类总目录页不作为关系目标。
7. 输出优先使用 Markdown 而非 JSON，同样内容可省约 30%~40% tokens。
8. 每个结果保留独立来源 URL；AI 不得只凭关系名称作结论。

调用方式：

```powershell
python -m docatlas ask "Set Timer" --token-budget 3000      # Markdown，AI 默认入口
python -m docatlas ask "Set Timer" --json                   # 结构化
python -m docatlas context "Set Timer" --token-budget 3000  # 等价于上一行
python -m docatlas related "Set Timer by Function Name"     # 全部关系与证据
```

## 7. 质量要求

- 全量目录：失败站点地图必须为 0。
- 正文：成功、失败、重定向分别统计，禁止静默丢弃。
- 版本：成功页面必须包含 UE 5.8 适用声明或通过官方 5.8 路由返回。
- 知识块：标题路径、正文、类型、版本、来源 URL 不得为空。
- 关系：必须有证据类型、置信度和来源。
- 所有加工步骤可重复运行且不产生重复数据。
