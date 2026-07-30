---
name: docatlas
description: 查询和维护本机 DocAtlas 官方文档知识库。一个 MCP 服务所有已安装数据集，安装时记录的默认数据集是《{{DATASET_NAME}}》。回答文档覆盖的问题时先查本地库；新建、升级、体检知识库时读取 WORKFLOWS.md。触发词：{{DATASET_TRIGGERS}}。
---

# DocAtlas

本地官方文档知识库。已抓取的正文会切块建索引，按官方术语检索，每条结果都带
原始出处地址。

**每个库的完成度不一样**：有的只建好了页面清单、正文尚未抓取，有的抓了一部分。
`docatlas_list_datasets` 会给出每个库的实际进度；查询返回 `pages_not_fetched`
时说明清单里有这一页但正文还没取回来，按 `next_steps` 处理。

程序位置：`{{DOCATLAS_ROOT}}`。

## 什么时候用

- 问题落在已安装的文档库覆盖范围内 → **先查 DocAtlas**。
- 需要官方原文、准确参数、版本差异或页面之间的关系。
- 要新建、升级、扩大或体检知识库 → 读 `WORKFLOWS.md`。

DocAtlas 查不到时，可以另行联网查证或说明自己不确定，但**不能把凭印象写出来的
内容当作官方文档转述**（见[查不到时](#查不到时)）。

## 能做什么

| 需要 | MCP 工具 | 命令行 |
|---|---|---|
| 取答案材料 | `docatlas_ask` | `python -m docatlas ask` |
| 列标题目录 | `docatlas_search` | `python -m docatlas search` |
| 展开某一条 | `docatlas_show` | `python -m docatlas show` |
| 查实体关系 | `docatlas_related` | `python -m docatlas related` |
| 列数据集与能力 | `docatlas_list_datasets` | `python -m docatlas paths`（只有 id 和路径） |

**优先用 MCP**，没有 MCP 时才用命令行。本机可用：{{DOCATLAS_MCP_TOOLS}}。

## 选数据集

一个 MCP 服务所有数据集，每次调用都可以传 `dataset_id` 指定查哪个库。

- **不确定查哪个 → 先调 `docatlas_list_datasets`**，以返回结果为准。它会给出
  每个库的 id、语言、分类、能力和建库进度。
- 不要假设某个库一定存在，也不要把安装时记录的默认库当成唯一选择。
- 下面这行是**安装当时**的快照：已配置 {{DOCATLAS_DATASETS}}，默认数据集
  `{{DATASET_ID}}`，原文语言 {{DATASET_LANGUAGE}}。安装之后增删过数据集它就过期了，
  以 `docatlas_list_datasets` 的实时返回为准。

命令行切换数据集：

```powershell
$env:DOCATLAS_DATASET='<dataset_id>'; python -m docatlas ask "<官方术语>"
```

## 工具用法

### docatlas_ask

默认入口，返回可直接引用的答案材料。

- `token_budget`：简单问题 1500，一般 3000，需要通读某一页用 6000。预算越大
  同一页给得越深。
- `no_fetch=true`：只用本地已有内容，不联网补抓。
- `format="json"`：需要稳定字段时用；默认返回 Markdown。

### docatlas_search

按标题和关键词列目录，用来确认"库里到底有没有这个名字"。

每条结果带 `match_stage`，表示这一条是怎么命中的：`entity` 表示库里有一页正好
叫这个名字，其他档都是关键词匹配到的邻居。**同名概念要靠它区分**——`entity`
那条才是官方同名页，不要和后面几条并列转述。`docatlas_ask` 不返回这个字段。

### docatlas_show

按知识 ID（`K` 加数字）展开完整内容。ID 来自 `ask` 或 `search` 的返回。

### docatlas_related

查实体之间的关系，每条带方向、证据、置信度和出处。

### docatlas_list_datasets

列出本机所有数据集及其能力。不确定选哪个库、不确定有哪些分类、不确定是否支持
版本过滤时，都先调它。

## 查询注意事项

**用官方术语本身，不要补通用词。** 补 `node`、`function`、`class` 这类满库都是
的词，只会让不相关的长页面也凑齐关键词。真正缩小范围的是更完整的官方名字。结果
不对时换更准确的术语重试，而不是往上堆词。

**`category` 是过滤，不是提示。** 传了就等于声明"其他分类一律不要"。分类值只能
来自 `docatlas_list_datasets`，不要照目录名猜。拿不准就别传——不过滤最多是结果
多几条，滤错了最相关的那一页会直接消失，而且返回里看不出它被滤掉了。

**已知确切页面时直接传地址。** `query` 接受官方 URL 或库内路径，带 `#小节` 时
继续限定到该小节：

```text
docatlas_ask(query="https://docs.example.com/guide/widgets")
docatlas_ask(query="/guide/widgets#configuration")
```

传了 `#小节` 必须读回 `fragment_intent`：`matched=false` 表示本页没有这一节，
此时返回的是整页内容，应按页内实际标题重查，**不得当成该小节的答案转述**。

**版本意图**：只有 `docatlas_list_datasets` 声明支持内容版本时才传。
`version_mode` 四种——`strict` 限定目标版本，`migration` 追问旧功能被什么替代
（保留旧版本证据），`compare` 比较版本不排除内容，`any` 不限定。数据集自身的
版本号只标识选中的资料集，不等于正文可过滤的版本。

## 关系查询

`confidence=1.0` 可按官方事实转述；**`<1.0` 必须说明是推断或候选**，不能当成
官方结论。自动生成、没有官方独立页面的目标只能作为检索别名。

返回 `entity_found_but_no_relations` 且 `next_steps` 给出同库内的待抓路径时，可
用同一 `dataset_id` 调 `docatlas_ask` 把该路径原样传给 `query` 补抓，成功后重试
`docatlas_related`。遇到站外目标、弱候选或抓取失败就停下并说明边界，不要反复
重试或放宽来源范围。

## 查不到时

先读 `status` 和 `next_steps`，其中安全且可执行的步骤可以直接继续执行。

| 状态 | 含义 |
|---|---|
| `pages_not_fetched` | 清单里有，正文尚未抓取 |
| `candidates_too_weak` | 有候选，但不足以安全补抓 |
| `language_mismatch` | 查询语言与该库原文语言不同 |
| `no_match` / `entity_not_found` | 当前查询没有命中 |
| `entity_found_but_no_relations` | 实体存在，但没有关系 |
| `target_outside_inventory` | 官方目标存在，但收录范围未覆盖 |
| `knowledge_id_not_found` | 知识 ID 无效或已过期 |

**"当前数据集没有" ≠ "官方没有"。** 收录范围由数据集配置决定，DocAtlas 不会
联网核对官网。空结果只能说成"当前数据集未收录或未找到"。用户确认官网确实有那
一页时，属于收录范围问题，改查询词无效——见 `WORKFLOWS.md`。

**任何情况下都不要用记忆补写官方内容。**

## 引用要求

- 回答必须带 DocAtlas 返回的原出处 URL，**不要自己拼地址**。
- 默认顺序：`ask` → 需要时 `show` → 需要时 `related`。
- 不要直接读数据库、清单文件或导出分片。
- 用用户的语言解释结果，保留代码符号、版本号和专有名词的原文写法。
