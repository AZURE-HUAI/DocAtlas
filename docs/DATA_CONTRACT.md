# DocAtlas 数据合同

版本：3  
适用范围：任何数据集。下文以内置的 `epic-ue-5.8`
（Epic Developer Community 的 Unreal Engine 5.8 英文官方文档）为例。

站点特有的部分（站点地图数量、接口形式、分类名）由来源适配器和
`datasets/*.toml` 决定；分层、字段和保证是所有数据集共同遵守的。

## 1. 处理阶段

### 阶段 A：页面目录（Inventory）

只读取**清单入口**，不抓正文。清单入口默认是站点地图的子地图；来源适配器
实现 `inventory_feeds` / `read_feed` 时也可以是分页 API、目录页或静态搜索索引，
后续处理完全一致。必须满足：

- 适配器认领的清单入口全部成功（`inventory_feeds_complete`；`epic-ue-5.8` 是 424 个）。
- **至少有一个成功入口和一个页面**（`inventory_not_empty`）。空库不算合格：
  没有不合格的行，不等于有数据。
- **配置声明的每个分类都枚举到了页面**（`declared_categories_have_pages`）。
  确实可能为空的分类要显式写进 `optional_categories`。
- 页面按规范化 `path` 去重。
- 每条页面记录包含 `url`、`path`、分类、版本、语言、父路径、路由深度和所属清单入口。
- 生成 `site_inventory.jsonl`、摘要和 SHA-256；失败入口为 0 时才标记 `complete`。

目录未达到 `complete` 时，不允许启动全量正文阶段。

抽样抓取（`--sample-per-category N`）是**每一类**的上限：某类只有 9 页就抓 9 页，
缺额不转给别的类；已成功的页面计入该类额度，重复运行不会继续扩大。

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
| `url` | 适配器给出的官方正式网址 |
| `path` | 规范化官方路由 |
| `title` | 官方标题 |
| `description` | 官方摘要 |
| `category` | `guides`、`blueprint_api`、`cpp_api` 等 |
| `source_type` | 官方标注的数据源类型 |
| `document_type` | landing、article、API 等官方类型 |
| `doc_version` | 数据集的版本（`epic-ue-5.8` 是 `5.8`；与产品无关的通用字段） |
| `locale` | 数据集的语言（`epic-ue-5.8` 是 `en-US`） |
| `parent_path` | 路由父级 |
| `route_depth` | 路由深度 |
| `sitemap_url` | 发现该页的清单入口（sitemap 或适配器提供的其它入口） |
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
- 小节锚点由标题的**可见文字**生成。标题里的 Markdown 链接目标只是给浏览器的
  地址，不是标题文字——拼进 fragment 会造出官方页面里不存在的来源地址。

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

全文检索的 SQL 必须让**全文索引当外层循环**（用 `CROSS JOIN` 锁死连接顺序）。
放任优化器重排时，只要带上 `--category`，它就会改从分类索引出发，于是每个候选
块都要单独跑一次全文匹配——实测同一条查询 0.05 秒变 44 秒。

### 查不到时必须说明是哪一种"没有"

`search` / `ask` / `related` 空结果时要区分并给出可执行的下一步：

| 情况 | 依据 | 下一步 |
|---|---|---|
| 清单里有对得上的页面，正文未抓 | `lookup.pending_pages` 非空 | `get` 补抓，或直接用 `ask`（自动补抓） |
| 同名页面已抓，只是查询词没命中 | `lookup.crawled_pages` 非空 | 换写法 |
| 清单里也没有 | 两者皆空 | 官方确实没有这一页 |

`related` 另外返回 `status`：`ok` / `entity_found_but_no_relations` /
`entity_not_found`，以及 `next_steps`。裸 `[]` 同时表示多种状态是不合格的契约。

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

### 按需抓取：清单里有就取得回来

页面在冻结清单里但正文为 pending 时，`ask` 会当场补抓。候选定位分三档：

| 档位 | 命中方式 | 例子 |
|---|---|---|
| `exact_slug` | 末段 slug 完全一致（去掉 `.html` 等文档扩展名、剥掉 `std::` 这类限定符） | `Fields` → `/…/fields.html` |
| `slug_contains` | slug 含有该词（词长 ≥ 5） | `Nanite` → `/…/nanite-virtualized-geometry` |
| `path_covers_query` | 查询里**每个**实词都出现在路径中（实词 ≥ 2 个） | `Wave Texture Node` → `/render/shader_nodes/textures/wave.html` |

覆盖档要求全部实词命中，所以概念性提问不会误触发补抓。本地已有结果但**没有
一条的页面标题就是所问的名字**时，同样允许补抓——否则弱相关的本地块会一直把
真正的目标页挡在门外。

## 7. 质量要求

- 全量目录：失败清单入口必须为 0，且页面数与各声明分类均不为 0。
- 正文：成功、失败、重定向分别统计，禁止静默丢弃。
- 版本：成功页面必须来自数据集声明版本的官方路由。
- 语言：抓回来的正文语言必须与数据集声明一致（`fetched_language_matches_declaration`）。
- 知识块：标题路径、正文、类型、版本、来源 URL 不得为空。
- 关系：必须有证据类型、置信度和来源。
- 所有加工步骤可重复运行且不产生重复数据。
