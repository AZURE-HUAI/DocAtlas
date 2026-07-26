---
name: docatlas
description: 查询本机的 DocAtlas 离线文档知识库。当前装的是《{{DATASET_NAME}}》，含{{DATASET_CATEGORIES}}，每条都带原出处 URL。凡是这份文档覆盖得到的问题，都应先查它，再考虑联网搜索或凭记忆回答——本地库有确切的版本、确切的原文和确切的出处，记忆没有。适用于：某个功能怎么配置、某个接口的参数与返回值、某个符号是什么意思、报错信息里出现的名字、两个东西之间怎么对应。触发词：{{DATASET_TRIGGERS}}。此外，用户想**新建或维护知识库**时也用它——"加一个 X 的文档库""升到新版本""把某某站也收进来""重新加工一遍""体检一下"——建库流程写在同目录的 WORKFLOWS.md 里。
---

# DocAtlas 本地文档知识库

用户装了一份官方文档离线库。**回答相关问题先查它**，不要凭记忆、不要联网——
本地库有确切版本、确切原文和确切出处 URL。

当前库：《{{DATASET_NAME}}》，分类：{{DATASET_CATEGORIES}}。用户可能装了别的库，
`python -m docatlas paths` 告诉你现在生效的是哪个。

程序位置：`{{DOCATLAS_ROOT}}`，所有命令在这个目录下执行。

**建库、加版本、加站点、改加工规则要重来、做体检——先读 `WORKFLOWS.md`，
照那里的固定流程做，不要自己现编步骤。**

## 入口：有 MCP 就用 MCP

DocAtlas 提供两个入口，背后是同一套检索代码，结果一致：

| 能力 | MCP 工具（优先） | 命令行（回退） |
|---|---|---|
| 拿答案材料 | `docatlas_ask` | `python -m docatlas ask` |
| 只看目录 | `docatlas_search` | `python -m docatlas search` |
| 展开一条 | `docatlas_show` | `python -m docatlas show` |
| 交叉关系 | `docatlas_related` | `python -m docatlas related` |
| 看装了哪些库 | `docatlas_list_datasets` | `python -m docatlas paths` |

本机提供：{{DOCATLAS_MCP_TOOLS}}。有 `docatlas_` 开头的工具就用——不用起子进程、
不用管路径、参数是结构化的。没有才用命令行。

命令行参数与 MCP 参数一一对应：`--token-budget`→`token_budget`，
`--category`→`category`，`--no-fetch`→`no_fetch`，`--fetch-limit`→`fetch_limit`。

**一个 MCP 服务器服务本机所有库。** 每个工具都收一个可选的 `dataset_id`，
不填就用默认库（{{DATASET_ID}}）。本机有：{{DOCATLAS_DATASETS}}。不确定该查哪个、
或某个库覆盖了什么，先调 `docatlas_list_datasets`——它会给出每个库的产品、版本、
原文语言、分类、会产出哪些关系类型，以及数据建到什么程度。

命令行一个进程只服务一个库，换库靠环境变量（见"版本纪律"）。

默认返回 Markdown。要稳定字段来解析才传 `format="json"`——同样内容 JSON 要多花
三四成 token，平时不值当。

建库、抓全站、重新加工这类**长时间、会改数据**的操作只有命令行，见
`WORKFLOWS.md`——要跑几分钟到几小时，不适合放进一次工具调用。

## 怎么查

**默认用 `ask`：**

```bash
python -m docatlas ask "<要查的东西>" --token-budget 3000
```

返回整理好、按预算裁剪的正文 + 每块原出处 + 一跳交叉关系指针，绝大多数问题
一条命令够了。**本地没有的页面会自动补抓**——全站清单早就冻结了，即使某页
正文没取过也知道它存在、在哪，`ask` 会当场取回来再答，通常一两秒，不要因为
"可能还没抓到"就不查。

参数：
- `--token-budget N`：默认 3000。问题简单用 1500，需要通读用 6000。**别省略**，
  这是保护上下文的主要手段。
- `--category`：限定分类（{{DATASET_CATEGORY_IDS}}），知道就加，结果更干净。
- `--no-fetch`：不联网，只用本地已有内容。
- `--fetch-limit N`：补抓页数上限，默认 5。

查询词用数据集原文语言（{{DATASET_LANGUAGE}}）通常更准——用户问"定时器"，
查它的官方写法。不是硬规则：专有名词、报错原文、代码符号原样查往往更准；
命中不好就换个说法再试。

**其余命令按需要用：**

| 命令 | 什么时候用 |
|---|---|
| `get "<名字>" [--limit N]` | 要连着查一批相关页，先把正文备齐，后面 `ask` 就是本地命中 |
| `search "<词>" --limit N` | 只要目录不要正文——先扫一眼，或确认在不在库里 |
| `show K<id>` | 精确展开 `ask`/`search` 结果里的某一条（每条都带 `K<数字>` 编号） |
| `related "<名字或 K 编号>"` | 一跳交叉关系：属于哪个上级、对应哪个接口、作用在什么类型上 |

`related` 返回的 `status` 决定下一步，**不要把几种情况混为一谈**：

| `status` | 意思 | 怎么办 |
|---|---|---|
| `ok` | 有实体也有关系 | 读 `entities[].relations` |
| `entity_found_but_no_relations` | 实体在库，一条关系都没有 | 照 `next_steps`：它会直接列出这一页链向的目标各是什么状态 |
| `entity_not_found` | 查的是名字，没这个实体 | 看 `lookup.pending_pages`：清单有就补抓，没有就是官方确实没有 |
| `knowledge_id_not_found` | 查的是 K 编号，编号不存在 | 编号是猜的或过期的，用 `search` 重新拿一个有效编号 |
| `target_outside_inventory` | 已抓页面链接过去，但这一页不在清单里 | **官方有，是本地来源没枚举到**。照 `next_steps` 给的官方地址直接看；要入库得改来源适配器，重抓多少次都不会有 |

除 `ok` 外的每种状态都带一个 `next_steps` 数组，里面是可以直接执行的命令。

## 上下文纪律

这种库动辄十几万页，以下做法会瞬间吃光上下文，**不要做**：

- 直接读 `exports/` 里的 Markdown 分片、`knowledge.sqlite3`、`manifest.jsonl`、
  `site_inventory.jsonl`
- 用 `grep` 扫数据目录
- 不带 `--token-budget` 就无脑加大 `--limit`

正确做法：**`ask` 拿主料 → 不够再 `show` 精确补一条 → 还不够才提高预算重问**。

## 引用与可信度

每条知识块末尾都有 `> DOC 原出处：<url>`。回答用户时**必须带上这个 URL**，
不要自己拼。

`related` / `ask` 给出的每条关系都带 `evidence_kind`（凭什么这么说）、
`confidence`（有多确定）、`note`、`evidence_url`（出处，说不准时给用户核对）。
当前库会产出的证据类型：{{DATASET_EVIDENCE_KINDS}}。

- **`confidence` 等于 1.0** —— 官方页面白纸黑字有的（真实链接、元数据完全一致）。
  可以当事实转述。
- **`confidence` 小于 1.0** —— 程序**推断**出来的。转述时必须说明是"推断"
  或"候选"，**不能说成官方对应**——名字对得上不等于就是同一个东西。

## 查不到怎么办

**先看它告诉你哪一种"没有"**，五种的下一步完全不同，不要一律当成"官方没有"：

| 它说什么 | 意思 | 下一步 |
|---|---|---|
| 清单里有 N 个页面对得上 | 页面存在，只是正文没取 | 照给的 `get` 取回来；`ask` 会自己补抓 |
| 同名页面已抓过，但没有知识块命中 | 页面在本地，是查询词不对 | 换成页面里会出现的说法 |
| 没有把握该取哪一页 | 线索不够，没敢自动补抓 | 看它列出的沾边页面，对就 `get` 那一条 |
| 这条查询是用另一套文字写的 | 库里没有这种语言的正文 | 换成原文（{{DATASET_LANGUAGE}}）的官方写法重问 |
| 清单里也没有对得上的页面 | 这才是真的没有 | 如实说官方文档没有这一页 |

只有最后一种才可以说"官方文档确实没有这一页"。其余四种说成这样都是在骗人，
而且会让人往错误的方向反复试。任何一种都不要用记忆编答案。

只有明确要看本地覆盖率时才跑 `python -m docatlas stats`。

## 版本纪律

答案限定在当前数据集版本内（`python -m docatlas paths` 查看是哪个），不要把
其他版本的行为混进来。

用 MCP 时换库：传 `dataset_id`。用命令行时换库：

```powershell
$env:DOCATLAS_DATASET='<别的数据集 id>'; python -m docatlas ask "<问题>"
```

**一个库只装一个版本的正文。** 用户问的版本和当前库不是一个时，先说清楚这一点，
再回答——别拿 5.8 的文档去答 5.3 的问题还不吭声。

## 用户自己的入口

```powershell
.\docatlas.ps1                # 交互式搜索
.\docatlas.ps1 ask "<问题>"
.\docatlas.ps1 status         # 抓取进度
```
