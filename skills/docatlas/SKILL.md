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

核心负责确定性检索、排序、关系和证据；开放式理解与翻译留在 AI 层。AI 要做的：

1. 判断目标数据集、用户意图、官方术语、分类、版本和关系查询需求。
2. 把用户语言转成数据集原文语言，保留代码符号、版本号和专有名词。
3. 先用 `dataset_id` 选库；只有 `docatlas_list_datasets.version_vocabulary`
   声明支持内容版本时才传版本意图，不要让核心猜。
4. 结果弱、语言不匹配或候选不足时，换更准确的官方术语重试。
5. `next_steps` 给出安全可执行的步骤时继续执行，直到得到答案或明确的覆盖边界。
6. 用用户的语言解释结果，并保留原始文档出处。

## 查询入口

优先使用 MCP；没有 MCP 才用命令行。本机提供：{{DOCATLAS_MCP_TOOLS}}。

| 需要 | MCP | 命令行 |
|---|---|---|
| 答案材料 | `docatlas_ask` | `python -m docatlas ask` |
| 标题目录 | `docatlas_search` | `python -m docatlas search` |
| 展开一条 | `docatlas_show` | `python -m docatlas show` |
| 知识关系 | `docatlas_related` | `python -m docatlas related` |
| 数据集与能力 | `docatlas_list_datasets` | `python -m docatlas paths`（只列 id 和路径，不含能力与建库进度） |

一个服务器服务所有数据集，每次调用可传 `dataset_id`；不确定查哪个先调
`docatlas_list_datasets`。默认用 `docatlas_ask`：`token_budget` 简单问题 1500、
一般 3000、需要通读 6000（预算越大，同一页给得越深，不是拿别的页面来凑）；
只用本地内容传 `no_fetch=true`；默认返回 Markdown，需要稳定字段才用
`format="json"`。

查询词用官方术语本身，**不要为了"更准"去补通用词**。补 `node`、`function`、`class`
这类满库都是的词，只会让长页面也凑齐关键词跟你抢位置；真正缩小范围的是更完整的
官方名字（`std::ranges::sort` 而不是 `sort` 加几个修饰词）。结果不对时换官方术语
重试，而不是往上堆词。

`category` 是**过滤**，不是提示：传了就等于声明"其他分类一律不要"。分类值只能
来自 `docatlas_list_datasets`，不能照着目录名猜。拿不准就别传——不过滤最多是
结果多几条，滤错了最相关的那一页会直接消失，而返回里看不出它被滤掉了。传了之
后首位不对，先去掉 `category` 重查一次再下结论。

### 已知确切页面时，直接传地址

`query` 接受官方 URL 或清单内路径，直接定位到那一页；带 `#小节` 时继续限定到该
小节。这是最强的定位方式——用于 `next_steps` 给出了路径、或用户贴了官方链接。

```text
docatlas_ask(query="https://cppreference.com/cpp/language/coroutines")
docatlas_ask(query="/render/shader_nodes/index")
docatlas_ask(query="https://create.roblox.com/docs/ui/on-screen-containers#screen-insets")
```

限定符同样有效：`std::ranges::sort` 命中 `ranges` 那一页，不会退回 `std::sort`。
传了 `#小节` 必须读回 `fragment_intent`：`matched=false` 表示本页没有这一节
（多半官方改过标题），此时给的是整页内容，应按页内实际标题重查，不得当成该小节
的答案转述。

### 版本意图

`version_mode` 四种：`strict` 用户明确限定目标版本；`migration` 追问旧功能后来
被什么替代，旧版本证据应保留；`compare` 比较版本，不排除内容；`any` 不限定。

数据集版本或快照日期只标识**选中的资料集**，由 `dataset_id` 保证隔离，不等于正文
的可过滤版本。`version_vocabulary` 为空时不传 `version_target`，也不要把
`dataset_supports_versions=false` 判为故障。迁移证据不在当前资料集时如实说明覆盖
边界；只有确实存在旧版数据集才切 `dataset_id`，不能让核心或 AI 补写缺失史料。

## 关系与证据

`related` 回答"属于什么、对应什么、作用于什么"，每条关系都带方向、证据、置信度
和出处。`confidence=1.0` 可按官方事实转述；`<1.0` 必须说明是推断或候选。自动
生成但没有官方独立实体的内容只能做检索别名，不能伪造成官方关系。

返回 `entity_found_but_no_relations` 且 `next_steps` 列出同一清单内的 pending
路径时，AI 自己完成一次有上限的闭环：用同一 `dataset_id` 调 `docatlas_ask`、把
那条路径原样传给 `query`（保持有限 `fetch_limit`）→ 补抓成功后用原实体重试
`docatlas_related`。遇到 `target_outside_inventory`、站外目标、弱候选或抓取失败
就停下并说明实际边界，不得无限重试或放宽来源范围。

这属于 AI 编排：不要求用户复制路径，也不让只读的 `related` 隐式联网写库。

## 查不到时

读 `status` 和 `next_steps`，安全且可执行的步骤由 AI 继续完成：

| 状态 | 含义 |
|---|---|
| `pages_not_fetched` | 清单有页面，正文尚未抓取 |
| `candidates_too_weak` | 有候选，但不足以安全补抓 |
| `language_mismatch` | 查询语言与数据集原文语言不同 |
| `no_match` / `entity_not_found` | 当前查询没有命中 |
| `entity_found_but_no_relations` | 实体存在，但没有关系 |
| `target_outside_inventory` | 官方目标存在，但来源清单未覆盖 |
| `knowledge_id_not_found` | K 编号无效或已过期 |

**"本数据集没有"不等于"官方没有"。** 清单范围由数据集声明的目录决定，DocAtlas
没有联网核对过官网。空结果只能说成"当前数据集未收录或未找到"；用户坚持官网确实
有那一页时，那是收录范围问题，改查询词无效，见 `WORKFLOWS.md` 流程 B。不要用
记忆补写缺失内容。

## 引用与上下文

回答必须带 DocAtlas 返回的原出处 URL，不要自己拼。默认顺序 `ask` → 必要时
`show` → 必要时 `related`。不要直接读取数据库、清单或导出分片，也不要无上限扩大
查询结果。

命令行切换数据集：

```powershell
$env:DOCATLAS_DATASET='<dataset_id>'; python -m docatlas ask "<官方术语>" --token-budget 3000
```
