# 架构评估报告

> 评估对象：`C:\Users\HUAI\Desktop\UE5文档\`（计划改名 DocAtlas）
> 评估时间：2026-07-25
> 依据：实际代码 5,717 行、实际数据库 739 MB、实际目录与配置文件。
> **本报告不含任何代码改动。** 唯一已执行的写操作是建立 Git 仓库（见第 0 节）。

---

## 0. 已执行：Git 部署

```
UE5文档/
├─ .git/           240 KB
├─ .gitignore      挡住全部数据
├─ .gitattributes  统一换行符
└─ (36 个源码与文档文件已纳入版本控制)
```

提交 `b9a6170`，36 个文件。**没有把数据放进 Git**：

| 排除项 | 体积 | 为什么排除 |
|---|---:|---|
| `ue58_docs.sqlite3` | 739 MB | 二进制、每次运行都变，Git 会为每次改动存一份完整拷贝 |
| `site_inventory.jsonl` | 124 MB | 可由数据库重新导出 |
| `manifest.jsonl` | 92 MB | 同上，且已改为按需生成 |
| `exports/` `assets/` | — | 抓取产物 |
| `*.log` `report.json` `ROUTER.md` | — | 每次运行都重写 |
| `_scratch/` | 3.2 MB | 早期调试残留 |

**Git 保护的是"怎么做"，不是"做出来的东西"。** 数据的保护手段是文件复制备份，两者不能互相替代——这一点在第 13 节的迁移方案里会再强调一次。

---

## 1. 现有项目的实际分析

### 1.1 物理布局

```
UE5文档/
├─ ue.ps1                       转发器：找最新版本目录并转发参数
└─ 5.8.0/                       ← 版本数据目录，但程序也住在这里
   ├─ ue_kb/                    程序本体，20 个模块 5,717 行
   ├─ tests/                    44 个离线测试
   ├─ ue58_docs.py              兼容外壳
   ├─ ue.ps1 status.ps1 start-background.ps1
   │  background-runner.ps1 run-full.ps1      运行脚本
   ├─ README/ARCHITECTURE/DATA_CONTRACT/AI_ROUTING.md
   ├─ ue58_docs.sqlite3         739 MB
   ├─ site_inventory.jsonl      124 MB (+ .sha256 + _summary.json)
   ├─ manifest.jsonl            92 MB
   ├─ exports/ assets/ _scratch/
   └─ ROUTER.md report.json *.log background-state.json
```

### 1.2 代码分层（现状是好的）

`ue_kb/` 已经是严格的单向分层，下层不依赖上层：

```
config → util → net → db → {discover, htmlmd} → chunking → documents
       → store → {crawl, assets, ondemand} → crossindex → search → context
       → {export, reports, validate} → cli
```

这个分层**本身没有问题，不需要重做**。问题不在层次，在于每一层里混着三种不同性质的知识（见第 4 节）。

### 1.3 数据库实际状态（实测）

| 项目 | 数量 |
|---|---:|
| 页面清单（全站，已冻结） | 199,883 |
| 已抓正文成功 | 10,760 |
| 逻辑小节 sections | 41,946 |
| 知识块 chunks | 48,099 |
| 实体 entities | 10,760 |
| 实体别名 | 43,073 |
| 交叉关系 relations | 10,400 |
| 页面链接 page_links | 45,025 |
| 原始 JSON 存档 raw_documents | 10,781 |

按分类：

| 分类 | 清单页数 | 已抓 | 覆盖率 |
|---|---:|---:|---:|
| 教程与功能文档 guides | 1,866 | 1,844 + 21 重定向 + 1 失败 | 100% |
| Epic 社区文档 | 754 | 754 | 100% |
| 节点参考 node_reference | 1,098 | 1,098 | 100% |
| 蓝图 API | 56,895 | 7,055 | 12.4% |
| C++ API | 139,269 | 8 | ~0% |
| Python API | **1** | 1 | 100% |

> **需要更正一个此前的说法**：Python API 不是"100% 完成"。Epic 的 python sitemap 里**总共只列了 1 个页面**（`python-1.xml` 只有 524 字节）。真实的 Python API 文档在官方站点上没有通过 sitemap 暴露，所以本地库实际上**没有 Python API 内容**。这是站点侧的限制，不是抓取的问题，但结论必须说清楚。

### 1.4 数据库体积构成（实测 dbstat）

| 对象 | 体积 |
|---|---:|
| `sections` 表 | 142.4 MB |
| `chunks` 表 | 123.8 MB |
| `pages` 表 | 122.1 MB |
| `chunks_fts`（内容 + 索引） | 108.4 MB |
| `sections_fts`（内容 + 索引） | 81.7 MB |
| `pages` 的两个唯一索引 | 52.2 MB |
| `raw_documents`（压缩原文） | 24.2 MB |
| `page_links` | 17.8 MB |
| 其余 | ~66 MB |

**关键发现：`sections` 和 `chunks` 存的是同一批文字。** 41,946 个小节中，40,097 个（95.6%）只切出 1 个知识块——也就是说这个块的正文与小节正文完全相同。两张表加两套全文索引 = **456 MB，占整库 62%，内容重复度约 96%**。

### 1.5 抓取与恢复能力（现状很好）

已具备且经过实战验证：

- **断点续传**：进度全在 `pages.status`，随时中断随时继续。
- **限流 ≠ 失败**：`store.py:16-21` 把 429/403/502/503/504 判为"服务器现在不想理我们"，留在 `pending` 且**不消耗重试次数**。这个设计避免了"一次限流风暴永久判死几千页"。
- **自适应限速**：`net.GlobalRateLimiter` 用 AIMD 自己找速率上限，并且"一次限流事件只降一档"。
- **原始层可离线重加工**：`raw_documents` 存了全部 10,781 份原始 JSON（zlib 压缩后仅 24.2 MB）。`reprocess` 命令完全不联网即可重切全部知识块。**这是整个项目最有价值的设计决策。**
- **清单冻结**：`site_inventory.jsonl` + sha256，`metadata.inventory_status='complete'`。`crawl --skip-discovery` 会检查这个标志，未冻结拒绝启动正文阶段（`cli.py:62-66`）。
- **按需抓取**：靠 `pages.normalized_slug`（URL 最后一段标准化）定位，`ask` 命中不到时自动补抓。

### 1.6 一个必须如实汇报的中断

上一轮会话启动的全量 `reprocess` **已经停止**，停在 2,000 / 10,760 页（会话中断时进程被终止）。

我做了验证：用宽松正则 `</?[a-z][a-z0-9]*(\s[^>]*)?>` 扫全部 48,099 个知识块，**HTML 残留命中数为 0**。原因是 `reprocess` 按页面 id 升序处理，而受旧规则影响的页面正是最早抓取的低 id 页——中断点恰好在受影响范围之后。

其他健康检查同样干净：

- 超过 900 token 的块：0
- 成功页面缺原始存档：0
- 成功页面缺主实体：0
- `normalized_slug` 为空：0
- 44 个离线测试：全部通过

**结论：这次中断没有留下损坏。但它暴露了一个真实的风险**——我是靠"扫一遍全表"才敢下这个结论，数据本身**无法自证**是用哪一版规则切的。这直接引出第 3 节的首要问题。

---

## 2. 目前架构是否合理

### 2.1 合理的部分（不应改动）

| 设计 | 判断依据 |
|---|---|
| 三层数据（原始 / 知识 / 路由） | `reprocess` 已多次证明：切分规则怎么改都不用重抓 |
| `ue_kb/` 单向分层 | 20 个模块职责清晰，改一处能推断影响面 |
| 限流不算失败 | 直接决定了大规模抓取能不能跑完 |
| 清单先行 + 按需抓取 | 199,883 页清单已冻结，"用到再抓"因此成立且精确 |
| 关系带证据与置信度 | 不把"名字一样"冒充成"官方对应"，这是知识库的诚实底线 |
| 上下文硬预算 | `context.py` 是保护 AI 上下文的唯一闸门，实现正确 |
| `UE_KB_HOME` 环境变量 | **分离代码与数据所需的机制已经存在并在用**（测试就靠它） |

### 2.2 不合理的核心：程序住在数据目录里

**结论：不合理。应当分离。**

判断依据不是"不好看"，是四条可验证的后果：

**(1) 目录名 `5.8.0` 是数据坐标，代码却被钉在这个坐标上。**

`config.py:21-23`：

```python
DATA_DIR = Path(os.environ.get("UE_KB_HOME") or Path(__file__).resolve().parent.parent)
```

`Path(__file__).parent.parent` 明确地把"代码在哪"当成"数据在哪"。要加 UE 5.9 只有两条路：

- 复制一份 `ue_kb/` 到 `5.9.0/` —— 代码从此分叉，修一个 bug 要修 N 次，这是最典型的维护灾难；
- 做软链接 —— Windows 上需要管理员权限，且用户完全无法理解发生了什么。

**(2) Git 边界被迫画在数据之上。**

这不是假设，是我刚才建仓时的实际遭遇：代码在 `5.8.0/`，`.gitignore` 必须挡住同目录下的 739 MB。加一个 5.9.0 之后，规则要么复制一遍，要么改成通配符——而需要通配符本身就说明代码和数据不该同处一室。

**(3) 测试要靠环境变量把自己"推开"。**

`tests/` 用 `UE_KB_HOME` 指向临时目录。这个机制是对的，但它是在"默认会读到隔壁那个 739 MB 真库"的前提下打的补丁。代码搬出去后，**默认无库**才是自然状态，补丁就不必要了。

**(4) 备份语义混乱。**

用户想备份"我抓的东西"时，会连代码一起复制；想同步代码时，会撞上 739 MB。两件东西的生命周期、变更频率、备份方式完全不同，却装在同一个文件夹里。

### 2.3 但是——不要现在就大改

`5.8.0/` 里的东西是**能跑的、验证过的、有 10,760 页真实成果的**。分离必须是**搬移 + 转发**，不是重写。第 13 节给出的方案里，每一步都可以单独验证、单独回滚。

---

## 3. 现有架构的主要问题

按严重程度排序。

### 问题 1（最严重）：没有解析器版本，加工数据无法自证

`chunks` 表有 `content_hash`、`created_at`、`updated_at`，但**没有一列说明"这个块是用哪一版切分规则切出来的"**。

后果非常具体：第 1.6 节那次中断，如果 HTML 残留没有恰好集中在低 id 页面，现在库里就是新旧规则混杂，而**没有任何办法把它们区分开**——只能整库重切 10,760 页（虽然不用重抓，但白跑 40 分钟）。

规则以后一定还会改（第 5 节就要改一次）。**这是唯一一个"越早加越好"的字段。**

### 问题 2（严重）：知识块过碎，超过一半没有独立使用价值

设计目标是每块约 550 token、硬上限 900。实测：

| token 区间 | 块数 | 占比 |
|---|---:|---:|
| < 50 | **26,254** | **54.6%** |
| 50–200 | 10,433 | 21.7% |
| 201–550 | 8,485 | 17.6% |
| > 550 | 2,927 | 6.1% |

平均 143 token，只有 6% 达到目标大小。实际样例：

```
[  6t] SmoothBoneWeights           → 正文："SmoothBoneWeights (v1)"
[  8t] SmoothStep Vector Sampler   → 正文："SmoothStep Vector Sampler (v1)"
[ 24t] Inputs                      → 正文："Name Description Permitted Types Default Value --- --- ---"
```

第三个尤其说明问题：检索命中一个叫 `Inputs` 的块，AI 拿到的是**一张空表头**。

**根因在 `chunking.py:223-254`。** 切块是在 **section 内部**做的。section 由 Markdown 标题切开，而 API 页面的 `Information` / `Inputs` / `Outputs` 各自是一个标题，各自只有几行，于是各产出一个碎块。`chunk_section()` 只会**把大 section 切小，从不把小 section 合并**。

这正好是用户第 7 点要避免的两种极端之一——不是"按固定字数切碎"，而是"按标题切碎"，结果一样。

### 问题 3（严重）：`sections` 与 `chunks` 96% 重复，占掉 62% 的库体积

见 1.4 节。`sections` 是 `chunks` 的前身，`chunks` 上线后它只剩两个真实用途：

- `search.py:312` `_legacy_section_search()` —— 只在 `chunks` 表为空（刚建库）时触发；
- `page_links.from_section_id` 外键 —— 记录链接出现在哪一节。

但它仍然维护着一整套 `sections_fts` 全文索引（81.7 MB），每次写入都要同步更新。**这套索引从来没有在正常检索路径上被用过。**

### 问题 4（中等）：缺三个来源字段

对照用户第 6 点的清单，已有的部分很完整：

✅ 文档来源 / 版本 / 语言 / 文档类型 / 原始 URL / 页面 ID / 分片 ID / 页面标题 / 标题层级路径 / 原始正文 / 加工内容 / 原始与加工的对应关系 / 抓取时间 / 更新时间 / 内容哈希

缺三个：

| 缺失 | 现状 | 影响 |
|---|---|---|
| **解析器版本** | 完全没有 | 见问题 1 |
| **规范化 URL** | `pages.url` 已带 `?application_version=5.8&lang=en-US`，原始与规范是同一个值 | 单站点无害；多站点后必须分开，否则无法跨源去重 |
| **前后分片关系** | 只有 `chunk_index` + `section_id`，没有 prev/next | `ask` 无法返回"相邻章节"（用户第 7 点明确要求） |
| **产品** | 隐含在数据库文件名里，无字段 | 多产品后必须显式 |

### 问题 5（中等）：站点特有逻辑散落各处，没有收在一起

详见第 4 节。要点：`config.py` 已经收了一部分（这是好的），但**还有一批直接写死在各模块里、绕过了 config**。

### 问题 6（轻微）：多处文案与命名钉死在 UE 5.8

- 数据库文件名 `ue58_docs.sqlite3`
- `reports.py:97` ROUTER.md 里写死具体页面 slug `unreal-engine-5-8-documentation`
- `reports.py:135` 文案里写死 `ue58_docs.sqlite3`
- `ue.ps1` 的 `-Category` 参数把六个 UE 分类写进 `ValidateSet`
- 全部 Markdown 文档标题都是 "UE 5.8"

这些不影响运行，但会在加第二个来源时集中爆发。

### 问题 7（轻微）：`_scratch/` 与运行脚本重叠

`_scratch/` 里 18 个调试残留（3.2 MB，含 1.8 MB 的 `main.js`），且里面还有两个中文名的 PowerShell 脚本（`搜索UE5文档.ps1`、`查看抓取进度.ps1`），与外面的 `ue.ps1` / `status.ps1` 功能重叠。已在 `.gitignore` 中排除，但目录里仍然存在，会让人困惑哪个才是入口。

---

## 4. 哪些逻辑被写死了

分四类。**这个分类直接决定了第 9 节的责任划分。**

### A 类：已收敛在 `config.py`（改动容易，现状可接受）

| 位置 | 内容 |
|---|---|
| `config.py:10` | `VERSION = "5.8"` |
| `config.py:11` | `LANGUAGE = "en-US"` |
| `config.py:13` | `SITEMAP_INDEX_URL` |
| `config.py:14-16` | `DOCUMENT_API_URL` |
| `config.py:17` | `DOC_PREFIX = "/documentation/unreal-engine/"` |
| `config.py:25` | `DB_PATH = DATA_DIR / "ue58_docs.sqlite3"` |
| `config.py:29-36` | `CATEGORY_PATTERNS` 六类 sitemap URL 片段 |
| `config.py:38-45` | `CATEGORY_LABELS` |
| `config.py:75-82` | `ENTITY_TYPES` 分类 → 实体类型 |

### B 类：绕过 config，直接写死在模块里（**这是真正的问题**）

| 位置 | 内容 | 性质 |
|---|---|---|
| `discover.py:40` | `f"https://dev.epicgames.com{quoted_path}"` | 主机名 |
| `discover.py:41` | `?application_version={VERSION}&lang={LANGUAGE}` | Epic 特有 query 约定 |
| `discover.py:52` | `^/documentation/[a-z]{2}-[a-z]{2}/` locale 前缀剥离 | Epic 特有 URL 规则 |
| `discover.py:58` | `path.lower() == "/documentation/unreal-engine"` 排除首页 | 单页特例 |
| `documents.py:47` | `parsed.netloc != "dev.epicgames.com"` | 主机白名单 |
| `documents.py:50` | 同 `discover.py:52` 的 locale 剥离（**逻辑重复**） | Epic 特有 |
| `htmlmd.py:89-91` | `urljoin("https://dev.epicgames.com/documentation/", src)` | 图片相对路径基准 |
| `chunking.py:389-391` | `source_url.startswith("https://dev.epicgames.com/documentation/")` 决定质量分 1.0 还是 0.7 | **算法里嵌了主机名** |
| `reports.py:97-98` | ROUTER.md 官方入口写死 `unreal-engine-5-8-documentation` | 文案 |

### C 类：Epic 站点数据格式假设（属于"文档解析器"）

| 位置 | 假设 |
|---|---|
| `documents.py:22-30` | 存在 `document.json?path=&lang=&application_version=` 这个接口 |
| `documents.py:33-42` | `document["applications"][].version` 表示适用版本 |
| `documents.py:169` | `document["redirect_url"]` 表示页面搬家 |
| `documents.py:177` | `document["blocks"]` 是顶层内容数组 |
| `documents.py:181-188` | `title` / `seo_title` / `description` / `seo_description` 字段名 |
| `htmlmd.py:267` | `block.settings.is_hidden` |
| `htmlmd.py:269` | `block.content_html` |
| `htmlmd.py:274` | `block.type == "document_list"` 及其 `items[].document_url` |
| `htmlmd.py:219-228` | `collect_strings` 跳过的字段名全是 Epic 后端字段（`hash_id`/`storage_key`/`revision_hash_id`/`has_live_revision`…） |
| `net.py` | Epic 特有的"302 + 无 Location + body 含 redirect_url" |

### D 类：Unreal 领域知识（**不应通用化，应作为可插拔规则包**）

| 位置 | 内容 |
|---|---|
| `documents.py:107-113` | `cpp_api` 路径切段取 module / owner_type |
| `documents.py:125-130` | `K2_` 前缀剥离并生成别名 |
| `documents.py:131-139` | `^[UAFIET][A-Z]` Unreal 类型前缀剥离 |
| `documents.py:232-243` | `ScriptName=` / `DisplayName=` 元数据正则 |
| `chunking.py:17-21` | `humanize_cpp_identifier` 驼峰拆词 |
| `crossindex.py` 全文 | blueprint_node ↔ cpp_symbol 对应、DisplayName 元数据证据 |
| `search.py:79-81` | `_IDENTIFIER_RE` 含 `^[UAFIETS][A-Z]` |
| `search.py:66-74, 239-245` | `cpp_api` 分类降权、大段 C++ 成员目录降权 |
| `config.py:66-74` | `KNOWLEDGE_TYPE_RULES` 英文标题正则（Inputs/Parameters/Returns/Remarks…） |

> **重要判断：D 类不应该被"配置化"。** 把"K2_ 前缀要剥离"写成 TOML 配置，只会得到一个既难读又难测的迷你 DSL。这些是真正的领域知识，写成 Python 函数更清楚、更好测、更好改。它们只需要**能被换掉**，不需要被**参数化**。

---

## 5. 建议的设计原则

1. **代码归代码，数据归数据。** 版本目录只装数据，一份代码服务所有版本。
2. **原始层神圣不可侵犯。** 任何加工规则的改动都必须能从 `raw_documents` 离线重放，永不重抓。
3. **加工产物必须自证出身。** 每个知识块必须能回答"我是谁用哪一版规则、从哪个原始文档、什么时候切出来的"。
4. **新来源靠加模块，不靠改核心。** 加 Unity 不应该动 `search.py` 一行。
5. **领域知识用代码表达，不用配置表达。** 配置管"是什么"（URL、版本、分类），代码管"怎么理解"（K2_ 前缀、蓝图↔C++）。
6. **不为第二个来源提前设计第三个来源。** 抽象接口在有两个真实实现之前必然设计错。
7. **每一步迁移都要能单独验证、单独回滚。**
8. **上下文预算是硬约束，永远不放松。**

---

## 6. 建议的系统边界

```
┌─────────────────────────────────────────────────────────┐
│ 通用核心 core/                                           │
│   不知道 Epic 是谁，不知道 UE 是什么。                     │
│   HTTP / 限速 / 重试 / SQLite / 分块 / 检索 / 上下文预算 / │
│   断点续传 / 覆盖率 / 验收 / 导出 / CLI 骨架               │
└──────────────────┬──────────────────────────────────────┘
                   │ 调用（按名字动态加载）
┌──────────────────▼──────────────────────────────────────┐
│ 来源适配器 sources/                                       │
│   知道"这个站点怎么列页、怎么取一页、返回什么格式"           │
│   epic_ue.py / unity.py / blender.py …                   │
│   ← 吸收第 4 节的 A + B + C 类                            │
└──────────────────┬──────────────────────────────────────┘
                   │ 可选挂载
┌──────────────────▼──────────────────────────────────────┐
│ 领域知识包 knowledge/                                     │
│   知道"这个产品的符号命名习惯与交叉关系"                    │
│   unreal.py（K2_ / UAFIET / DisplayName / 蓝图↔C++）      │
│   ← 吸收第 4 节的 D 类                                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 数据集配置 sources/*.toml                                 │
│   "我要抓哪个站点的哪个产品的哪个版本的哪个语言"            │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 数据 data/<source>/<product>/<version>/                  │
│   一个版本一个 SQLite，各自独立                            │
└─────────────────────────────────────────────────────────┘
```

**三层的判据很简单**：

- 换一个站点要改的 → 适配器
- 换一个产品（但站点结构相同，如 UE→Unity Docs 都是 sitemap+HTML）要改的 → 领域包
- 什么都不用改的 → 核心

---

## 7. 建议的根目录与数据目录设计

```
DocAtlas/                             ← Git 仓库根 = 代码根
├─ .git/  .gitignore  .gitattributes
├─ da.ps1                             用户唯一入口
├─ README.md  docs/
├─ docatlas/
│  ├─ core/          config util net db chunking store search context
│  │                 crawl ondemand export reports validate cli
│  ├─ sources/       epic_ue.py  (以后 unity.py blender.py)
│  └─ knowledge/     unreal.py   (可选)
├─ sources/                           数据集配置（不是代码）
│  ├─ epic-ue-5.8.toml
│  └─ epic-ue-5.9.toml                （以后，只有 version 一行不同）
├─ tests/
└─ data/                              ← .gitignore 完全排除
   └─ epic-ue/
      ├─ 5.8/
      │  ├─ knowledge.sqlite3         ← 由 ue58_docs.sqlite3 改名迁入
      │  ├─ inventory.jsonl (+ .sha256)
      │  ├─ assets/  exports/
      │  └─ report.json  ROUTER.md  *.log
      └─ 5.9/
```

### 数据放仓库内还是仓库外？

**建议放仓库内的 `data/`，用 `.gitignore` 排除。**

| 方案 | 优 | 劣 |
|---|---|---|
| 仓库内 `data/` ✅ | 整个项目一个文件夹，复制即备份；用户心智负担最低 | 需要 `.gitignore` 纪律（已经有了） |
| 仓库外 `~/DocAtlas/` | Git 完全看不见 | 用户要记两个位置；备份容易漏 |
| 纯环境变量 | 最灵活 | 对非技术用户是纯负担 |

考虑到用户是非技术背景，**"一个文件夹装下全部"的价值高于洁癖**。同时保留 `DOCATLAS_HOME` 环境变量覆盖（沿用今天 `UE_KB_HOME` 的机制），测试仍然靠它隔离。

### 一个版本一个 SQLite，不要合并成大库

理由都是具体的：

- 删除或备份一个版本 = 删一个文件夹，不需要 SQL
- 一个库损坏不波及其他
- 199,883 页已经让 `pages` 表 122 MB；四个 UE 版本 + Unity + Blender 合成一个库会到数 GB，FTS 重建和 WAL 会很痛
- 跨库查询：先用"依次查各库再合并排序"（简单、够用），确有需要再上 SQLite `ATTACH`（上限 10 个库）

---

## 8. 哪些程序不应放在版本数据目录

**全部程序都不应该。** 具体清单：

| 现在的位置 | 应去 | 原因 |
|---|---|---|
| `5.8.0/ue_kb/**` | `docatlas/` | 一份代码服务所有版本 |
| `5.8.0/tests/` | `tests/` | 测试属于代码 |
| `5.8.0/ue58_docs.py` | 保留为转发外壳 | 用户与 Skill 的肌肉记忆不能断 |
| `5.8.0/ue.ps1` | 保留为转发外壳 | 同上 |
| `5.8.0/status.ps1` `start-background.ps1` `background-runner.ps1` `run-full.ps1` | `docatlas/scripts/` | 运行脚本属于代码 |
| `5.8.0/README/ARCHITECTURE/DATA_CONTRACT/AI_ROUTING.md` | `docs/` | 文档描述代码，不描述某个版本的数据 |
| `5.8.0/_scratch/` | 删除或移出仓库 | 调试残留 |

**应当留在版本数据目录的**：

`knowledge.sqlite3`、`inventory.jsonl(+.sha256, +_summary.json)`、`assets/`、`exports/`、`report.json`、`ROUTER.md`、`*.log`、`background-state.json`。

判据一句话：**"重装一次程序会消失的东西"归代码；"重装程序也必须留下的东西"归数据。**

---

## 9. 责任划分

### 通用核心 `docatlas/core/`

| 模块 | 现在对应 | 变化 |
|---|---|---|
| `net` | `net.py` | 无变化，已经完全通用 |
| `db` | `db.py` | 表结构加字段（第 11 节）；移除 UE 专有默认值 |
| `chunking` | `chunking.py` | 拿掉 `humanize_cpp_identifier` 与主机名判断；**加跨 section 合并** |
| `store` | `store.py` | 无实质变化 |
| `crawl` `ondemand` | 同名 | 分类优先级表改为从配置读 |
| `search` `context` | 同名 | 权重表改为可由领域包覆盖 |
| `export` `reports` `validate` | 同名 | 文案去 UE 化 |
| `cli` | `cli.py` | 加 `--source` / `--product` / `--version` |

### 来源适配器 `docatlas/sources/epic_ue.py`

一个模块，实现四个函数即可（**不需要抽象基类**）：

```python
def list_pages(config) -> Iterable[PageRecord]:      # ← discover.py 全部
def page_request_url(config, path) -> str:           # ← documents.document_api_url
def parse_document(config, path, body) -> Document:  # ← documents.transform_document 的解析部分
def canonical_url(config, path) -> str:              # ← discover.canonical_source_url
```

吸收第 4 节的 A + B + C 三类全部内容。核心用 `importlib.import_module(f"docatlas.sources.{name}")` 加载，**不到 20 行，不需要注册表、不需要插件框架**。

### 领域知识包 `docatlas/knowledge/unreal.py`

吸收第 4 节 D 类：

```python
def entity_aliases(title, path, category) -> set[tuple[str, str]]   # K2_ / UAFIET / 驼峰
def extract_metadata_aliases(text) -> set[tuple[str, str]]          # ScriptName / DisplayName
def build_relations(connection) -> int                              # 蓝图 ↔ C++
SEARCH_WEIGHTS: dict                                                # 分类与知识类型权重
```

**可选挂载**：没有领域包的来源（比如 Blender 文档）就不挂，核心照常工作。

### 数据集配置 `sources/epic-ue-5.8.toml`

```toml
[dataset]
source   = "epic-ue"
product  = "unreal-engine"
version  = "5.8"
language = "en-US"

[adapter]
module = "epic_ue"
knowledge = "unreal"                  # 可选

[site]
host           = "dev.epicgames.com"
sitemap_index  = "https://dev.epicgames.com/documentation/sitemap.xml"
document_api   = "https://dev.epicgames.com/community/api/documentation/document.json"
doc_prefix     = "/documentation/unreal-engine/"

[categories]
guides          = { pattern = "/unreal_engine/external/",                      label = "教程与功能文档", entity_type = "guide",         priority = 1 }
community_docs  = { pattern = "/unreal_engine/epic_developer_community/",      label = "社区文档",       entity_type = "document",      priority = 2 }
blueprint_api   = { pattern = "/unreal_engine/ue_blueprint_api_external/",     label = "蓝图 API",       entity_type = "blueprint_node",priority = 3 }
node_reference  = { pattern = "/unreal_engine/ue_noderef_api_external/",       label = "节点参考",       entity_type = "editor_node",   priority = 4 }
python_api      = { pattern = "/unreal_engine/ue_python_api_external/",        label = "Python API",     entity_type = "python_api",    priority = 5 }
cpp_api         = { pattern = "/unreal_engine/ue_cpp_api_external/",           label = "C++ API",        entity_type = "cpp_symbol",    priority = 6 }
```

**加 UE 5.9 = 复制这个文件，改 `version` 一行。** 这就是整个设计的验收标准。

---

## 10. 配置与数据模型设计

### 配置解析优先级

```
命令行参数  >  环境变量 DOCATLAS_HOME  >  数据集 TOML  >  核心默认值
```

### 数据模型变更（全部是加列，不改已有列语义）

`db.py` 已有的 `add_column_if_missing()` 机制可以直接用，**现有数据库无损升级**。

**`pages` 表新增：**

| 列 | 类型 | 说明 |
|---|---|---|
| `source` | TEXT | `epic-ue` |
| `product` | TEXT | `unreal-engine` |
| `canonical_url` | TEXT | 去掉 query 参数的规范化 URL，与 `url` 分开 |

（`ue_version` 已有，语义上应改称 `version`，但**不要改名**——加一个视图或就沿用旧名，避免为了整洁而冒险。）

**`chunks` 表新增（最重要）：**

| 列 | 类型 | 说明 |
|---|---|---|
| `parser_version` | TEXT | **解决问题 1**。写入时填当前规则版本，如 `"chunk-v2"` |
| `prev_chunk_id` | INTEGER | **解决问题 4**。相邻块指针 |
| `next_chunk_id` | INTEGER | 同上 |
| `embedding` | BLOB | 预留，现在恒为 NULL，**不实现** |

**`metadata` 表新增键：**`source` / `product` / `dataset_id` / `parser_version`。

### 来源信息完整性对照（用户第 6 点验收表）

| 要求 | 现在 | 变更后 |
|---|---|---|
| 文档来源 | metadata.source | `pages.source` ✅ |
| 产品/文档集 | 隐含 | `pages.product` ✅ |
| 版本 | `pages.ue_version` ✅ | 保持 |
| 语言 | `pages.locale` ✅ | 保持 |
| 文档类型 | `pages.category` + `document_type` ✅ | 保持 |
| 原始 URL | `pages.url` ✅ | 保持 |
| 规范化 URL | ❌ 与原始同值 | `pages.canonical_url` ✅ |
| 页面识别码 | `pages.id` ✅ | 保持 |
| 分片识别码 | `chunks.id` ✅ | 保持 |
| 页面标题 | `pages.title` ✅ | 保持 |
| 完整标题层级 | `chunks.heading_path` ✅ | 保持 |
| 原始正文 | `raw_documents.raw_json` ✅ | 保持 |
| 加工后内容 | `chunks.content_md` ✅ | 保持 |
| 原始↔加工对应 | `chunks.page_id → raw_documents.page_id` ✅ | 保持 |
| 前后分片关系 | ❌ | `prev_chunk_id` / `next_chunk_id` ✅ |
| 抓取时间 | `pages.fetched_at` ✅ | 保持 |
| 更新时间 | `chunks.updated_at` ✅ | 保持 |
| 内容哈希 | `chunks.content_hash` ✅ | 保持 |
| 解析器版本 | ❌ | `chunks.parser_version` ✅ |

---

## 11. 知识分片格式与索引设计

### 分块策略修正（解决问题 2）

现在：`markdown_units()` → 在 section 内累积到 2200 字符 → 切块。小 section 直接单独成块。

建议改为**三段式**：

```
1. 切分  按标题层级切成 section（保持现状，这一步是对的）
2. 合并  连续的小 section，在满足以下全部条件时合并成一个块：
           - 累计 < target(2200 字符)
           - 同一父标题下
           - knowledge_type 相邻兼容（signature+parameters+returns 可合，
             但 examples 与 navigation 不与其他合并）
3. 切分  合并后仍超过 max(3200 字符) 的，按现有逻辑切小（表格保表头、
           代码保围栏、列表按项——这部分现有实现是好的，保留）
```

预期效果：碎块（<50 token）比例从 **54.6% 降到 15% 以下**，块数从 48,099 降到约 15,000–20,000，平均 token 从 143 升到 350–450。

### 关于"上下文重叠"—— 我建议不做

用户第 7 点要求"必要的上下文重叠"。**我不同意，理由如下：**

- 现在每个块已经带 `context_prefix`（`UE 5.8 | 蓝图 API | 页面名 | 标题层级 | 知识类型`）和完整 `heading_path`，**上下文信息不缺**，缺的是相邻正文。
- 字符级重叠会让 `content_hash` 失去唯一性，直接破坏 `context.py` 的去重逻辑（同一段文字出现在两个块里，哈希不同，去重失效，**反而浪费 AI 上下文**）。
- 重叠会让 FTS 索引膨胀 20–30%，同一段文字被检索命中两次。

**替代方案**：`prev_chunk_id` / `next_chunk_id` 指针 + `ask` 加 `--with-neighbors` 选项。要相邻内容时显式取，不要默认塞。这样既满足"必要时能拿到相邻章节"，又不污染索引和去重。

### 索引设计

- **保留** `chunks_fts`（FTS5，unicode61）——现有五档回退检索依赖它，工作良好。
- **停止维护** `sections_fts`（省 81.7 MB 和一半写入开销）。`sections` 表本身保留（作为章节骨架与 `page_links` 锚点），只是不再建全文索引。`_legacy_section_search()` 兜底路径改为直接报"知识块尚未生成"。
- **`entity_aliases`** 是精确符号检索的核心（43,073 条），保持不变。
- **`embedding` 列预留但不实现**（理由见第 15 节）。

---

## 12. 搜索、总路由与 AI 统一接入

### 检索能力对照（用户第 10 点要求）

| 要求 | 现状 | 需要做什么 |
|---|---|---|
| 指定产品 | ❌ | 加 `--product`（配置就位后自然可用） |
| 指定版本 | ❌ 隐含在库文件 | 加 `--version`（选择哪个库） |
| 指定语言 | ❌ | 加 `--lang` |
| 指定文档类型 | ✅ `--category` | 保持 |
| 关键字全文搜索 | ✅ FTS5 五档回退 | 保持 |
| 精确符号/类/函数/节点名 | ✅ 实体 + 43,073 别名 | 保持（这是现有系统最强的部分） |
| 返回分片 + 上下文 + 来源 | ✅ `ask` | 保持 |
| 返回相邻章节 | ❌ | prev/next 指针 + `--with-neighbors` |
| 返回相关文档 | ✅ 一跳关系指针 | 保持 |
| 语意搜索 | ❌ | **暂不实现**，只预留字段 |

### AI 接入方式选择

用户列了 6 种。**我建议只做 2 种：**

| 方案 | 判断 | 理由 |
|---|---|---|
| **CLI + Skill 文件** | ✅ **保留并改进** | 已在用、已验证。改进点：加 `--source/--product/--version`；Skill 文件从写死 UE 5.8 改为"先列出有哪些数据集，再选" |
| **MCP Server** | ✅ **值得做，排在架构迁移之后** | Claude Code 靠 Skill 调 CLI 已经能用，但 MCP 让 **任何** 客户端（Cursor、Cline、Claude Desktop）直接可用，不必为每家写一遍 Skill。而且 MCP 只是在现有 `ask`/`search`/`get` 外面包一层，**核心逻辑一行不改** |
| 统一搜索指令 | ⏸ 已被 CLI 覆盖 | `ask` 就是它 |
| 本地 HTTP API | ❌ **不做** | MCP 已覆盖同样场景，多一个要维护的端口、进程和安全边界 |
| Python SDK | ❌ **不做** | `from docatlas import ask` 本来就能用，不需要专门"设计"一层 |
| AI 可读说明 | ✅ 已有 | `AI_ROUTING.md` + Skill，随迁移更新即可 |

**MCP 建议暴露 4 个工具**（少即是多）：

```
docatlas_list_datasets()                              有哪些来源/产品/版本
docatlas_ask(query, dataset?, category?, budget?)     主入口，返回已裁剪 Markdown
docatlas_search(query, dataset?, category?, limit?)   只要目录
docatlas_show(chunk_id, with_neighbors?)              展开一条 + 可选相邻
```

---

## 13. 现有 UE 5.8 数据的安全迁移方案

### 总结论：**一页都不用重抓。**

依据：`raw_documents` 存了全部 10,781 份原始 JSON（压缩后 24.2 MB），`reprocess` 已多次证明可完全离线重放；`site_inventory.jsonl` + sha256 冻结了 199,883 页清单。

### 数据分三类处置

| 数据 | 处置 | 需要重抓? |
|---|---|---|
| `pages` 清单 199,883 行 | **直接沿用**，加 `source`/`product`/`canonical_url` 三列并回填 | 否 |
| `raw_documents` 10,781 份 | **直接沿用**，一字不动 | 否 |
| `sections` 41,946 | **保留**，停止建 FTS | 否 |
| `chunks` 48,099 | **离线重切**（`reprocess`），补 `parser_version` / prev / next | 否 |
| `entities` 10,760 + 别名 43,073 | 随 reprocess 重建 | 否 |
| `relations` 10,400 | 随 `cross-index` 重建 | 否 |
| `page_links` 45,025 | **直接沿用** | 否 |
| `assets` 16,807 | **直接沿用** | 否 |
| `exports/` | 迁移后重新生成 | 否 |
| `manifest.jsonl` `site_inventory.jsonl` | 迁移后重新生成 | 否 |

### 分阶段执行

#### 阶段 0：立即可做，零风险

1. ✅ Git 已建（本次已完成）
2. **冷备份**：把 `ue58_docs.sqlite3` 完整复制一份到 `5.8.0/backup-2026-07-25/`（739 MB）。**在动任何数据之前必须做。**
3. **加 `parser_version` 列并回填现有 48,099 块为 `"v1"`**
   - 这是唯一"越早越好"的动作：越晚做，新旧规则混杂而不可区分的风险越大
   - 一句 `ALTER TABLE` + 一句 `UPDATE`，几秒完成，可回滚

**验证**：`validate --phase content` 全 pass；44 个测试全过。

#### 阶段 1：结构分离（纯搬移，逻辑零改动）

1. `git mv` 代码到 `docatlas/`，文档到 `docs/`，测试到 `tests/`
2. `config.py` 改为读 TOML；`DATA_DIR` 默认改为仓库内 `data/`
3. 数据目录 `5.8.0/` → `data/epic-ue/5.8/`；`ue58_docs.sqlite3` → `knowledge.sqlite3`
4. **保留转发外壳**：`5.8.0/ue.ps1` 与 `ue58_docs.py` 继续存在并转发，用户和 Skill 的用法一行都不用改

**验证（缺一不可）**：
- 44 个测试全过
- `validate --phase content` 全 pass
- **`ask "Nanite"` / `ask "Set Timer by Function Name"` / `ask "ACharacter"` 输出与迁移前逐字节相同**（迁移前先存三份基线）

#### 阶段 2：适配器分层（改代码，不改数据）

1. B + C 类硬编码收进 `docatlas/sources/epic_ue.py`
2. D 类收进 `docatlas/knowledge/unreal.py`
3. `config.py` 的 A 类改为从 TOML 读

**验证**：
- 同阶段 1 三项
- **额外**：`reprocess --limit 100`，比对 100 页产出的 `content_hash` 与迁移前**完全一致**（证明重构没有改变任何加工行为）

#### 阶段 3：分块质量修正（改数据，不重抓）

1. 实现小 section 合并；加 prev/next 指针；`parser_version` 升至 `"v2"`
2. 全量 `reprocess`（离线，预计 30–45 分钟）
3. `cross-index` 重建关系
4. `export` 重新生成 Markdown 分片

**验证**：
- 碎块（<50 token）比例 **< 15%**（当前 54.6%）
- 平均 token **> 300**（当前 143）
- 超 900 token 块 = 0
- `parser_version='v1'` 的块数 = 0
- 抽 20 个已知查询，人工对比 `ask` 输出质量优于迁移前

**回滚方式**：阶段 0 的冷备份直接覆盖回去。

#### 阶段 4：多来源（等真的要加 Unity 时再做）

**不要在只有一个来源时"提前设计"适配器接口。** 第一个新来源就是对边界设计的真实检验；提前设计必然设计错。

---

## 14. 分阶段实施计划

| 阶段 | 内容 | 预计 | 风险 | 前置 |
|---|---|---|---|---|
| **0** | Git ✅ / 冷备份 / 加 `parser_version` 回填 v1 | 30 分钟 | 极低 | — |
| **1** | 目录分离 + TOML 配置 + 转发外壳 | 半天 | 低（纯搬移） | 0 |
| **2** | 适配器与领域包分层 | 1 天 | 中（改代码，靠 hash 比对兜底） | 1 |
| **3** | 分块合并 + prev/next + 全量 reprocess | 半天 + 45 分钟机器时间 | 中（改数据，靠冷备份兜底） | 2 |
| **4** | MCP Server | 半天 | 低（只包一层） | 2 |
| **5** | 加第二个来源（Unity / Blender） | 视站点而定 | — | 4 |

**建议节奏**：阶段 0 现在就做（30 分钟，纯收益）。阶段 1–2 一起做并一次验证。阶段 3 单独做单独验证。阶段 4 之后随时。阶段 5 等真有需求。

---

## 15. 风险、取舍与不建议现在做的事

### 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 迁移中数据库损坏 | 739 MB 成果 | 阶段 0 冷备份，**不做备份不开工** |
| 重构改变了加工行为而没察觉 | 检索质量悄悄下降 | 阶段 2 强制 100 页 `content_hash` 逐字比对 |
| 分块合并规则不当，块变太大 | 上下文浪费 | 硬上限 900 token 有测试守着，`validate` 也检查 |
| 转发外壳漏了某个入口，用户命令失效 | 体验断裂 | 阶段 1 验证清单包含 `ue.ps1` 全部 11 个动作 |
| Skill 文件路径写死 `C:\Users\HUAI\Desktop\UE5文档\5.8.0` | AI 查不到库 | 改名 DocAtlas 时**必须同步更新** `~/.claude/skills/ue5-docs/SKILL.md` |
| 过度抽象 | 维护成本反而上升 | 只做两层（适配器 + 领域包），不做插件框架 |

> ⚠️ **改名提醒**：`~/.claude/skills/ue5-docs/SKILL.md:11` 写死了 `C:\Users\HUAI\Desktop\UE5文档\5.8.0`。文件夹改名为 DocAtlas 的**同时**必须改这一行，否则 AI 会找不到知识库。

### 明确不建议现在做

| 不做 | 理由 |
|---|---|
| **语义 / 向量搜索** | 需要引入本地 embedding 模型（PyTorch 或 onnx），对非技术用户是巨大安装负担。现有"五档回退 + 43,073 条实体别名"已经解决了"名字对不上"这个语义搜索的主要用武之地。**只预留 `embedding` 列，零成本** |
| **插件注册表 / 抽象基类 / 依赖注入** | 两个来源不需要框架。`importlib.import_module()` 一行搞定 |
| **本地 HTTP API** | MCP 覆盖同样场景，多一个进程和安全边界 |
| **Python SDK** | `from docatlas import ask` 本来就能用 |
| **合并多版本到单一大库** | 见第 7 节 |
| **把 UE 领域逻辑配置化** | 会得到一个既难读又难测的迷你 DSL。它们需要"能被换掉"，不需要"被参数化" |
| **删除 `sections` 表** | 它还承担章节骨架和 `page_links` 锚点。只停掉它的 FTS 即可省 81.7 MB |
| **重新抓取任何页面** | `raw_documents` 让这件事永远没必要 |
| **补抓剩余 18.9 万页** | 用户已明确"用到再抓"。按需抓取已验证可用，全量抓取的边际价值远低于其时间与限流成本 |
| **上下文重叠（overlap）** | 见第 11 节，会破坏去重并膨胀索引。用 prev/next 指针替代 |

### 三个层次的清晰区分

**现在必须完成：**
1. 冷备份（阶段 0）
2. `chunks.parser_version` 加列并回填（阶段 0）
3. 代码与数据分离 + TOML 配置（阶段 1）
4. 站点适配器与领域包分层（阶段 2）
5. 分块合并修正（阶段 3）

**应预留能力、暂不实现：**
1. `chunks.embedding` 列（留空）
2. 跨库联合查询（先"依次查再合并"）
3. `knowledge/` 包的挂载点（目前只有 `unreal.py` 一个实现）
4. 第二个来源适配器（等真需求）

**完全没必要加入：**
1. 语义 / 向量检索
2. 插件框架、抽象基类、注册表
3. 本地 HTTP 服务
4. 独立 SDK 层
5. 上下文重叠
6. 多版本合库
7. 领域知识配置化

---

## 附：本次评估的实测数据

```
代码            5,717 行 / 20 模块 / 44 测试全过
数据库          739 MB（sections 142 + chunks 124 + pages 122
                       + chunks_fts 108 + sections_fts 82 + 索引 52
                       + raw 24 + links 18 + 其余 66）
页面清单        199,883（冻结，sha256 校验）
已抓正文        10,760 成功 / 21 重定向 / 1 失败
知识块          48,099（<50t 占 54.6%，>550t 占 6.1%，均值 143t）
小节            41,946（其中 40,097 = 95.6% 只切出 1 块）
实体            10,760 / 别名 43,073 / 关系 10,400
原始存档        10,781 份（压缩 24.2 MB）
HTML 残留       0（宽松正则全表扫描）
超限知识块      0
缺原始存档      0
缺主实体        0
```
