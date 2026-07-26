---
name: docatlas
description: 查询和维护本机 DocAtlas 官方文档知识库。当前默认库是《{{DATASET_NAME}}》，也可通过同一 MCP 查询其他已安装数据集。回答文档覆盖的问题时先查本地库；新建、升级、体检知识库时读取 WORKFLOWS.md。触发词：{{DATASET_TRIGGERS}}。
---

# DocAtlas

程序位置：`{{DOCATLAS_ROOT}}`。默认数据集：`{{DATASET_ID}}`（原文语言
{{DATASET_LANGUAGE}}）；本机数据集：{{DOCATLAS_DATASETS}}。

文档覆盖的问题先查 DocAtlas，再考虑联网或凭记忆回答。建库、升级、重加工或体检
先读 `WORKFLOWS.md`。

## AI 是中间层

AI 负责理解用户并把自然语言翻译成 MCP 可执行的请求：

1. 判断目标数据集、用户意图、官方术语、分类、版本和关系查询需求。
2. 将用户语言转换为数据集原文语言，保留代码符号、版本号和专有名词。
3. 先用 `dataset_id` 选择数据集；只有
   `docatlas_list_datasets.version_vocabulary` 声明支持内容版本时，才把版本意图
   传成 `version_target` 与 `version_mode`，不要让核心猜。
4. 结果弱、语言不匹配或候选不足时，改用更准确的官方术语重试。
5. 结果给出安全的 `next_steps` 时继续执行，直到得到答案或明确的覆盖边界。
6. 用用户的语言解释结果，并保留原始文档出处。

DocAtlas 核心负责确定性检索、排序、关系和证据；开放式理解与翻译留在 AI 层。

## 查询入口

优先使用 MCP；没有 MCP 才用命令行。

本机提供：{{DOCATLAS_MCP_TOOLS}}。

| 需要 | MCP | 命令行 |
|---|---|---|
| 答案材料 | `docatlas_ask` | `python -m docatlas ask` |
| 标题目录 | `docatlas_search` | `python -m docatlas search` |
| 展开一条 | `docatlas_show` | `python -m docatlas show` |
| 知识关系 | `docatlas_related` | `python -m docatlas related` |
| 数据集与能力 | `docatlas_list_datasets` | `python -m docatlas paths` |

一个 MCP 服务器服务所有数据集。每次调用可传 `dataset_id`；不确定查哪个时先调用
`docatlas_list_datasets`。

默认用 `docatlas_ask`，简单问题 `token_budget=1500`，一般问题 3000，需要通读
时 6000。知道分类就传 `category`；只用本地内容时传 `no_fetch=true`。默认返回
Markdown，需要稳定字段时才用 `format="json"`。

版本意图：

- `strict`：用户明确限定目标版本。
- `migration`：追问旧功能后来被什么替代；旧版本证据应保留。
- `compare`：比较版本，不排除内容。
- `any`：不限定版本。

数据集版本或快照日期只标识**选中的资料集**，由 `dataset_id` 保证隔离；它不等于
正文的可过滤版本。若 `version_vocabulary` 为空，不传 `version_target`，也不要把
`dataset_supports_versions=false` 判为故障。迁移证据不在当前资料集时，如实说明
覆盖边界；有旧版数据集才切换 `dataset_id` 查询，不能让核心或 AI 补写缺失史料。

## 关系与证据

`related` 用于回答“属于什么、对应什么、作用于什么”。每条关系都带方向、证据、
置信度和出处。

- `confidence=1.0`：可按官方事实转述。
- `confidence<1.0`：必须说明是推断或候选。

自动生成但没有官方独立实体的内容可以作为检索别名，不能伪造成官方关系。

`related` 返回 `entity_found_but_no_relations` 且 `next_steps` 列出同一清单内的
pending 路径时，AI 应自动完成一次有上限的闭环：

1. 用相同 `dataset_id` 调用 `docatlas_ask`，查询返回的精确 path 或官方页面名，
   保持有限 `fetch_limit`。
2. 补抓成功后，用原实体重试一次 `docatlas_related`。
3. `target_outside_inventory`、站外目标、弱候选或抓取失败时停止，并说明实际边界；
   不得无限重试或放宽来源范围。

这一步属于 AI 编排，不要求用户复制路径，也不让只读的 `related` 隐式联网写库。

## 查不到时

读取返回的 `status` 和 `next_steps`；安全且可执行的步骤由 AI 继续完成：

| 状态 | 含义 |
|---|---|
| `pages_not_fetched` | 清单有页面，正文尚未抓取 |
| `candidates_too_weak` | 有候选，但不足以安全补抓 |
| `language_mismatch` | 查询语言与数据集原文语言不同 |
| `no_match` / `entity_not_found` | 当前查询没有命中 |
| `entity_found_but_no_relations` | 实体存在，但没有关系 |
| `target_outside_inventory` | 官方目标存在，但来源清单未覆盖 |
| `knowledge_id_not_found` | K 编号无效或已过期 |

清单没有候选时只能说“当前数据集未收录或未找到”，不能据此断言官网没有该页面。
不要用记忆补写缺失内容。

## 引用与上下文

回答必须带 DocAtlas 返回的原出处 URL，不要自己拼。默认顺序是：
`ask` → 必要时 `show` → 必要时 `related`。不要直接读取数据库、清单或导出分片，
也不要无上限扩大查询结果。

命令行切换数据集：

```powershell
$env:DOCATLAS_DATASET='<dataset_id>'; python -m docatlas ask "<官方术语>" --token-budget 3000
```
