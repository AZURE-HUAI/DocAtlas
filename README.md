# DocAtlas

把官方技术文档完整抓到本地，切成小知识块，建好全文索引和交叉关系，
让**任何 AI 智能体**都能又快又准地查——而且每一条都带原出处 URL。

不是网页收藏夹，也不是全文搜索引擎。它解决的是一个很具体的问题：

> AI 回答技术问题时，要么凭记忆瞎编，要么把整页文档塞进上下文然后没预算干正事。
> DocAtlas 让它拿到**刚好够用的那几段**，并且每段都能追溯到官方页面。

当前内置一个数据集：**Unreal Engine 5.8 官方文档**（教程、蓝图 API、C++ API、
Python API、节点参考、社区文档）。加别的版本或别的产品见[第五节](#五加一个版本或一个新站点)。

---

## 零、从零开始（第一次用）

只要有 Python 3.11+ 就行，**不需要装任何第三方包**。程序放哪都行，不用装进 Python
环境；数据可以跟程序放一起，也可以单独挪到别的盘。

```powershell
git clone <这个仓库> DocAtlas
cd DocAtlas
python install.py                            # 装技能 + 注册 MCP（详见第六节）
python -m docatlas crawl --discovery-only    # 第一步：枚举全站页面清单
```

第一步只读官方站点地图、不抓正文，几十分钟，产出一份冻结的清单
（UE 5.8 是 199,883 页）。**做完这一步就能用了**——

```powershell
.\docatlas.ps1 ask "Nanite"
```

因为清单里已经记着每一页在哪，`ask` 查到本地没有会当场把那一页抓回来。
**不需要先把二十万页全下载下来。**

想要离线可用（出差、断网、或者就是想要全量），再跑：

```powershell
.\docatlas.ps1 start      # 抓正文，随时可中断，下次自动续传
```

---

## 一、你只需要记这几条命令

```powershell
.\docatlas.ps1                                     # 交互式搜索，什么都不用记
.\docatlas.ps1 ask   "Nanite"                      # 直接给出整理好的答案材料（最常用）
.\docatlas.ps1 get   "ACharacter"                  # 只把指定的页面抓到本地
.\docatlas.ps1 find  "Set Timer"                   # 只列标题和出处，不展开正文
.\docatlas.ps1 show  K9290                         # 展开某一条知识的完整内容
.\docatlas.ps1 links "Set Timer by Function Name"  # 看蓝图 ↔ C++ ↔ 类型的对应关系
.\docatlas.ps1 where                               # 数据到底存在哪个目录
```

不用 PowerShell 的话，等价写法是 `python -m docatlas ask "Nanite"`。

### 不用一次性全抓：用到哪页抓哪页

全站 199,883 页的**清单**已经抓完并冻结，所以即使某页正文还没取，
系统也知道它存在、在哪个分类、URL 是什么。于是：

**`ask` 发现本地没有会自动去官网取那一页**，通常一两秒，你不用管。

```powershell
.\docatlas.ps1 ask "GetCharacterMovement"
# → 本地还没有这一页，正在按需抓取 1 页（C++ API）…
# → 然后直接给出答案
```

定位分三档，从最确定到最宽松，所以怎么称呼都能对上：

| 你说 | 对上的页面 | 靠什么 |
|---|---|---|
| `K2_SetTimer` | `…/UKismetSystemLibrary/K2_SetTimer` | 末段完全一致 |
| `Set Timer by Function Name` | `…/Time/SetTimerbyFunctionName` | 同上（忽略空格大小写） |
| `Fields` | `…/geometry_nodes/fields.html` | 同上（`.html` 不算名字的一部分） |
| `std::from_chars` | `…/utility/from_chars` | 同上（限定名只取末段） |
| `Nanite` | `…/nanite-virtualized-geometry-…` | 末段里含有这个词 |
| `Wave Texture Node` | `…/shader_nodes/textures/wave.html` | 每个实词都出现在路径里 |

最后一档要求**全部**实词命中，所以"怎么让物体发光"这类概念提问不会误触发补抓。

想提前备好一批页面（比如要连着查一个类的成员），用 `get`。
不想联网（离线、或只想看本地有什么）：`python -m docatlas ask "Nanite" --no-fetch`。

### 查不到时会告诉你是哪一种"没有"

空结果本身没有信息量，所以 `ask` / `find` / `links` 都会说清楚：

| 情况 | 会看到 | 下一步 |
|---|---|---|
| 清单里有这一页，正文还没取 | 列出对得上的页面路径 | 照提示 `get`，或直接用 `ask`（自动补抓） |
| 页面已经在本地，只是词没对上 | "同名页面已经抓过了" | 换个说法 |
| 清单里也没有 | "全站清单里也没有对得上的页面" | 官方确实没有这一页 |

`find` 还有一种情况：结果不空，但清单里躺着一个**名字完全一致**的页面没抓——
它会在末尾单独提示，免得你拿一堆沾边的结果凑答案。

### `ask` 和 `find` 该用哪个？

- **`ask`**：你要答案。按 token 预算挑出最相关的几块正文、去重、裁剪，
  再附上交叉关系的指针。**AI 默认用这个。**
- **`find`**：你要目录。只给标题、类型、匹配方式、得分、出处，不给正文。

```powershell
.\docatlas.ps1 ask "Lumen reflections" -TokenBudget 1500        # 问题简单，少给点
.\docatlas.ps1 ask "Gameplay Ability System" -TokenBudget 6000  # 需要通读，多给点
.\docatlas.ps1 find "Nanite" -Category cpp_api                  # 只在 C++ API 里找
```

搜索支持"从精确到宽松"五档自动回退，所以下面这些写法都能命中：

| 你输入 | 命中方式 |
|---|---|
| `Set Timer by Function Name` | 实体名精确命中 |
| `K2_SetTimer` | C++ 符号别名命中 |
| `virtual shadow maps` | 完整短语命中 |
| `how do I set a timer in blueprint` | 关键词 + BM25 |
| `nanit` | 前缀命中 |

---

## 二、抓取

```powershell
.\docatlas.ps1 status      # 看一眼当前覆盖率
.\docatlas.ps1 watch       # 实时滚动进度（Ctrl+C 退出，不影响后台抓取）
.\docatlas.ps1 start       # 开始 / 继续抓取（随时可中断，下次自动续传）
.\docatlas.ps1 stop        # 停止抓取
.\docatlas.ps1 check       # 数据质量验收
```

官网对文档接口有限流（会回 HTTP 429）。抓取器**自己会找速率**：
连续成功就慢慢加速，被拒绝就降一档并全局冷却，无需手工调参。

- 被限流的页面**不算失败**，会留在待抓队列，也不消耗重试次数。
- 任何时候 Ctrl+C 或 `stop` 都安全，进度全在数据库里。

### 改了加工规则怎么办：不用重抓

原始响应全部按内容哈希压缩存档，所以切分规则怎么改都能**离线重放**：

```powershell
python -m docatlas reprocess                 # 用本地原文重切，不联网
python -m docatlas validate --phase content  # 数据合同验收
```

每一页和每一个知识块都记着自己是哪版规则产出的（`parser_version`），
所以 `reprocess` 默认**只做还没升级的页**——中途断掉再跑就是续传。
要全部重做加 `--force`。

### 只补做某个阶段

```powershell
python -m docatlas assets       # 只补下图片
python -m docatlas export       # 只重新生成 Markdown 分片
python -m docatlas cross-index  # 只重建交叉关系
python -m docatlas stats        # 覆盖率
```

---

## 三、数据长什么样

分三层，从原始到可用：

| 层 | 内容 | 为什么要有 |
|---|---|---|
| **原始层** | 官网返回的原始响应，按内容哈希压缩存档 | 以后想换切分方式，不用重抓 |
| **知识层** | 按标题层级切块并合并小段，标注 `summary` / `signature` / `parameters` / `returns` / `remarks` / `examples` / `overview` / `details` / `references` | 长文不整篇喂给 AI |
| **路由层** | 全文索引 + 实体别名 + 交叉关系 + 上下文包 | 让检索又准又省 token |

每个知识块都带：版本、分类、页面标题、完整标题层级、知识类型、Markdown 正文、
纯文本、token 估算、**原出处 URL**、页面锚点、内容哈希、质量分、切分规则版本、
以及前后相邻块的指针。目标约 550 token，硬上限 900 token。

> **为什么不做"上下文重叠"**：重叠会让 `content_hash` 失去唯一性，直接破坏
> 上下文包的去重（同一段文字出现在两块里、哈希不同、去重失效），还会把全文
> 索引撑大 20~30%。需要上下文时顺着 `prev_chunk_id` / `next_chunk_id` 去取即可。

### 交叉关系的可信度

关系不是拍脑袋连的，每条都记录证据和置信度：

| 证据类型 | 置信度 | 含义 |
|---|---|---|
| `official_link` | 1.0 | 页面里真实存在的链接 |
| `unreal_display_name_metadata` | 1.0 | C++ 侧 `DisplayName` 与蓝图节点名完全一致 |
| `document_statement` | 0.92 | 正文明确写了 `Target is X` |
| `exact_normalized_name` | 0.82~0.9 | 只是名字一致，**属候选，需核对签名** |

置信度低于 1.0 的会明确标成"候选"，不会冒充官方对应关系。

---

## 四、目录结构

**代码和数据分开住**，这是整个设计的地基：加一个版本不需要复制一份程序。

```
DocAtlas/
├─ docatlas.ps1          ← 你的唯一入口
├─ docatlas/             ← 程序本体
│  ├─ *.py               核心：抓取、切块、检索、上下文预算（不认识任何具体网站）
│  ├─ sources/           来源适配器：一个模块懂一个文档站
│  └─ knowledge/         领域知识包：一个模块懂一个技术领域的行话
├─ datasets/*.toml       ← 数据集配置（网址、分类、检索权重）
├─ scripts/              ← PowerShell 辅助脚本
├─ tests/                ← 离线回归测试，不联网
└─ data/                 ← 全部数据（Git 不管这里）
   └─ epic-ue-5.8/
      ├─ knowledge.sqlite3      全部结构化数据
      ├─ exports/               整本 Markdown 分片（给人翻，AI 不要整篇读）
      ├─ assets/                正文引用的图片
      ├─ ROUTER.md              自动生成的总路由与覆盖率
      └─ site_inventory.jsonl   冻结的全站页面清单（带 sha256）
```

数据想放别的盘：设环境变量 `DOCATLAS_HOME`。
想用别的数据集：设 `DOCATLAS_DATASET`。

---

## 五、加一个版本，或一个新站点

### 加 UE 5.9：复制一个配置文件

```powershell
copy datasets\epic-ue-5.8.toml datasets\epic-ue-5.9.toml
# 编辑新文件：id 和 version 改成 5.9
$env:DOCATLAS_DATASET = 'epic-ue-5.9'
.\docatlas.ps1 start
```

**程序代码一行都不用改**，5.9 的数据会存进 `data/epic-ue-5.9/`，
和 5.8 完全隔离——删掉一个不影响另一个。

### 加 Unity / Blender：写一个适配器

新建 `docatlas/sources/你的站点.py`，实现四件事：

1. 这个站点有哪些页面（`sitemap_index_url` / `categorize_sitemap` / `normalize_location`）
2. 某一页的内容去哪里要（`document_request_url`）
3. 拿回来怎么解析（`parse_document`）
4. 正式引用地址长什么样（`canonical_url`）

限速、重试、落库、切块、检索、上下文预算全都不用管，那些是核心的事。
照着 [`docatlas/sources/epic_ue.py`](docatlas/sources/epic_ue.py) 抄一份改就行。

如果那个领域有自己的"行话"（比如 Unreal 的 `K2_` 前缀、蓝图和 C++ 的对应关系），
再写一个 `docatlas/knowledge/你的领域.py`。这一层是可选的，不写也能跑。

---

## 六、AI 怎么用

两种接法，Skill 和 MCP，`install.py` 一次都装上：

```bash
python install.py                      # 装技能 + 注册 MCP，装完自己验一遍
python install.py --data-dir D:/Docs   # 顺便把数据挪到别的盘
python install.py --print              # 只打印 MCP 配置片段，不动任何文件
```

安装器守两条纪律：

1. **先验证，再写配置。** 注册之前先从一个陌生目录把 MCP 服务器真起一次、走完
   握手，通不过就什么都不写——一次失败留下半截配置，比没装还难查。
2. **不代改你的现成配置。** Claude Code 交给官方的 `claude mcp add`；Codex 只在
   没有该条目时追加；其余客户端只打印片段让你粘贴。

### Skill（Claude Code / Codex）

装到 `~/.claude/skills/` 和 `~/.codex/skills/`，只装到真实存在的客户端目录。
写出的文件一律是 **UTF-8 无 BOM**——Codex 读到 BOM 就认不出 `SKILL.md`。装完会
自己验一遍（文件非空、无 BOM、占位符都填掉了、frontmatter 完好）。

装完之后任何会话里问到相关问题，AI 会自动先查本地库，而不是凭记忆或联网。

技能文件里必须写明程序在哪，安装时会把**仓库的实际位置**填进去——所以以后移动或
改名项目目录，重跑一次就行，不用手工改任何文件。

### MCP 服务器（Claude Code / Claude Desktop / Cursor / Cline …）

不认识的客户端就手工填这一段（把路径换成你自己的）：

```json
{
  "mcpServers": {
    "docatlas": {
      "command": "C:/你的/python.exe",
      "args": ["C:/你的路径/DocAtlas/mcp_server.py"]
    }
  }
}
```

**别写 `cwd`。** DocAtlas 不是装进 Python 环境的包，就是个普通文件夹，所以
`python -m docatlas mcp` 只有在仓库目录下才找得到它；而各家客户端的 stdio 配置
不一定支持 `cwd`——Claude Code 就只认 `command` / `args` / `env`，写了会静默失效，
只留下一句 `No module named docatlas`（BUG-021）。用仓库根的 `mcp_server.py` 当
入口没有这个问题：Python 会把脚本所在目录放进 `sys.path`，从任何工作目录启动都成立，
不需要 cwd，不需要 PYTHONPATH，也不需要 pip install。

`command` 建议写 Python 的**绝对路径**：客户端起子进程时的 PATH 不一定和你的终端
一样，写 `python` 可能解析到另一个解释器。

提供五个工具：`docatlas_ask`（默认用这个）、`docatlas_search`、
`docatlas_show`、`docatlas_related`、`docatlas_list_datasets`。
它们和命令行调用的是同一套检索代码，结果一致——有测试钉着这一点。

想同时查多个版本，就配多个条目，各自加 `"env": {"DOCATLAS_DATASET": "epic-ue-5.9"}`。

> MCP 服务器是**手写**的，没有引入 MCP SDK——整个项目到现在一个第三方包都不需要，
> 有 Python 就能跑。

### 两种接法共同遵守的三条纪律

1. 默认走 `ask` 并带 token 预算，不要无脑加大结果数量。
2. 绝不直接读 `exports/`、`manifest.jsonl`、`knowledge.sqlite3`——一个文件就能吃光上下文。
3. 回答必须附带原出处 URL；查不到就如实说"本地还没抓到"。

---

## 七、开发

```powershell
python -m unittest discover -s tests -v      # 128 个用例，离线，不碰真实数据库
```

改代码前请先看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 三层分工与各模块职责
- [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md) —— 数据结构与字段约定
- [docs/AI_ROUTING.md](docs/AI_ROUTING.md) —— AI 检索策略与上下文预算规则
- [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) —— 从单一知识库演进到多来源的完整评估

---

## 八、问题反馈与协作

- 报告错误或提出增强：使用 GitHub 的 **New issue** 页面。
- 查看当前问题、长期证据和解决归档：[`issues/README.md`](issues/README.md)。
- 了解分支、测试、PR 和封存要求：[`CONTRIBUTING.md`](CONTRIBUTING.md)。

GitHub Issues 负责在线讨论、分工和开关状态；仓库中的 `issues/` 保存复现证据、
设计背景、验证结果和历史决策。两者的同步规则见
[`issues/WORKFLOW.md`](issues/WORKFLOW.md)。
