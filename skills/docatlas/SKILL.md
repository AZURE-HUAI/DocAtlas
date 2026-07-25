---
name: docatlas
description: 查询本地 DocAtlas 技术文档知识库（当前数据集：Unreal Engine 5.8 官方文档——教程、蓝图 API、C++ API、Python API、节点参考、社区文档，全部带原出处 URL）。任何涉及 UE / Unreal Engine / 虚幻引擎的问题都应先用它，而不是联网搜索或凭记忆回答——包括：某个蓝图节点怎么用、某个 C++ 类/函数的参数与返回值、蓝图节点对应哪个 C++ API、某个功能（Nanite、Lumen、GAS、Niagara、Chaos、Sequencer…）怎么配置、某个 UPROPERTY/UFUNCTION 说明符含义、报错信息里出现的 UE 符号。触发词包括：UE5、UE 5.8、虚幻、Unreal、蓝图、Blueprint、AActor/UObject 等 UE 类型名、K2_ 开头的函数名。
---

# DocAtlas 本地文档知识库

用户在本机建了一份官方文档的完整离线知识库。**回答相关问题必须先查它**，
不要凭记忆，也不要联网——本地库有确切的版本、确切的原文和确切的出处 URL。

程序位置：`{{DOCATLAS_ROOT}}`

所有命令都在这个目录下执行。数据存在别处，`python -m docatlas paths` 会告诉你在哪
（一般不需要关心）。

## 怎么查（按这个顺序）

### 第一步：`ask` —— 默认就用这个

```bash
python -m docatlas ask "Set Timer by Function Name" --token-budget 3000
```

它直接返回**已经整理好、已经按预算裁剪过**的 Markdown：命中的知识块正文 +
每块的原出处 + 一跳交叉关系的指针。绝大多数问题一条命令就够。

用户怎么问是用户的事，**查什么词是你的事**。这个数据集的原文语言是
`{{DATASET_LANGUAGE}}`。用户用别的语言提问时，一般先把他的说法落成原文里的
官方写法再查——例如原文为英文时，用户问"定时器"，你查 `Set Timer`。

不是硬规矩：专有名词、报错原文、代码符号直接原样查往往更准；用户母语的词
碰上译文页或社区内容也可能命中。命中不好就换个说法再试一次。

**本地没有的页面它会自动补抓。** 全站 199,883 页的清单早就抓完并冻结了，
所以即使某页正文还没取，也知道它存在、在哪、URL 是什么——`ask` 会当场把
那一页取回来再回答，通常一两秒。所以：

- 不要因为"可能还没抓到"就不查，**直接查就行**。
- 不要自己去联网找官方文档，`ask` 已经会取了。

参数：
- `--token-budget N`：上下文预算，默认 3000。问题简单用 1500，需要通读用 6000。
  **这是保护上下文的主要手段，不要省略。**
- `--category`：限定 `guides` / `blueprint_api` / `cpp_api` / `python_api` /
  `node_reference` / `community_docs`。知道要查哪一类时加上，结果更干净。
- `--no-fetch`：禁止联网补抓，只用已有内容。离线时或只想看本地覆盖率时用。
- `--fetch-limit N`：补抓时最多取几页，默认 5。

### 需要显式抓某一页时：`get`

```bash
python -m docatlas get "ACharacter"
python -m docatlas get "UCharacterMovementComponent" --limit 3
```

用在"要连着查一个类的一批成员"这种场景——先 `get` 把页面备齐，后面的 `ask`
就都是本地命中了。日常问答不需要，`ask` 会自己处理。

### 第二步：`search` —— 只要目录，不要正文

```bash
python -m docatlas search "nanite tessellation" --limit 10
```

返回标题、知识类型、匹配档位、得分、原出处，**不返回正文**。用在：
- 不确定该看哪一条，想先扫一眼
- 想确认某个东西在不在库里

### 第三步：`show K<id>` —— 精确展开一条

```bash
python -m docatlas show K9290
```

`ask` 和 `search` 的结果里每条都带 `K<数字>` 编号，用它展开完整正文。

### 第四步：`related` —— 交叉关系

```bash
python -m docatlas related "Set Timer by Function Name"
```

返回 JSON：这个蓝图节点对应哪个 C++ API、Target 是什么类型、属于哪个模块，
**每条关系都带证据类型和置信度**。

## 上下文纪律（重要）

这个库有 20 万页。以下做法会瞬间吃光上下文，**不要做**：

- ❌ 直接读 `exports/` 里的 Markdown 分片（单个分片 8MB）
- ❌ 用 Read 打开 `knowledge.sqlite3`、`manifest.jsonl`、`site_inventory.jsonl`
- ❌ 用 `grep` 扫数据目录
- ❌ 不带 `--token-budget` 就无脑加大 `--limit`

正确做法：**`ask` 拿主料 → 不够再 `show` 精确补一条 → 还不够才提高预算重问**。

## 引用规则

每条知识块末尾都有 `> DOC 原出处：<url>`。回答用户时**必须带上这个 URL**，
让用户能回到官方页面核对。不要自己拼 URL。

## 关系的可信度怎么读

`related` / `ask` 给出的每条关系都有 `evidence_kind` 和 `confidence`：

| 证据 | 置信度 | 含义 |
|---|---|---|
| `official_link` | 1.0 | 官方页面里真实存在的链接 |
| `unreal_display_name_metadata` | 1.0 | C++ 侧 `DisplayName` 元数据与蓝图节点名完全一致 |
| `document_statement` | 0.92 | 正文明确写了 `Target is X` |
| `exact_normalized_name` | 0.82~0.9 | 只是名字标准化后一致，**属于候选，需核对签名** |

置信度 < 1.0 的关系，转述时要说明是"推断"或"候选"，不能说成官方对应。

## 查不到怎么办

`ask` 已经会自动补抓，所以"查不到"通常意味着**这个东西在官方文档里确实不存在**，
或者名字写得和官方对不上。

处理顺序：

1. 换官方写法再试一次。用户说的是口语名，官方可能是别的：
   `角色移动组件` → `UCharacterMovementComponent`；`定时器` → `Set Timer`。
2. 用 `search` 扫一眼有没有近似的：`python -m docatlas search "movement" --limit 10`
3. 还是没有，就**如实说官方文档里没有这一页**，不要用记忆编一个答案。

只有在明确要看本地覆盖率时才跑：

```bash
python -m docatlas stats
```

## 版本纪律

答案要限定在当前数据集的版本内。`python -m docatlas paths` 会告诉你是哪个
（例如 `epic-ue-5.8` 就是 UE 5.8）。不要把其他版本的行为混进来。

如果用户装了多个版本的数据集，切换方式是设环境变量再跑：

```bash
DOCATLAS_DATASET=epic-ue-5.9 python -m docatlas ask "Nanite"
```

## 给用户的简易入口

用户自己不用记命令，可以直接跑：

```powershell
.\docatlas.ps1              # 交互式搜索
.\docatlas.ps1 ask "Nanite"
.\docatlas.ps1 status       # 抓取进度
```
