# Unreal Engine 5.8 官方文档本地知识库

把 Epic 官方的 UE 5.8 文档（教程、蓝图 API、C++ API、Python API、节点参考、社区文档）
完整抓到本地，切成小知识块，建好全文索引和交叉关系，让 AI 能又快又准地查，
并且**每一条都带 Epic 原出处 URL**。

数据来自 Epic Developer Community 的公开站点地图，以及网站自身调用的结构化文档接口。

---

## 一、你只需要记这几条命令

全部在 `5.8.0` 目录下执行。

```powershell
.\ue.ps1                                     # 交互式搜索，什么都不用记
.\ue.ps1 ask   "Nanite"                      # 直接给出整理好的答案材料（最常用）
.\ue.ps1 get   "ACharacter"                  # 只把指定的页面抓到本地
.\ue.ps1 find  "Set Timer"                   # 只列标题和出处，不展开正文
.\ue.ps1 show  K9290                         # 展开某一条知识的完整内容
.\ue.ps1 links "Set Timer by Function Name"  # 看蓝图 ↔ C++ ↔ 类型的对应关系
```

### 不用一次性全抓：用到哪页抓哪页

全站 199,883 页的**清单**已经抓完并冻结了，所以即使某页正文还没取，
系统也知道它存在、在哪个分类、URL 是什么。于是：

**`ask` 发现本地没有会自动去 Epic 取那一页**，通常一两秒，你不用管。

```powershell
.\ue.ps1 ask "GetCharacterMovement"
# → 本地还没有这一页，正在按需抓取 1 页（C++ API）…
# → 然后直接给出答案
```

匹配靠 URL 最后一段，所以怎么称呼都能对上：

| 你说 | 对上的页面 |
|---|---|
| `K2_SetTimer` | `…/UKismetSystemLibrary/K2_SetTimer` |
| `Set Timer by Function Name` | `…/Time/SetTimerbyFunctionName` |
| `ACharacter` | `…/Engine/ACharacter` |

想提前备好一批页面（比如要连着查一个类的成员），用 `get`：

```powershell
.\ue.ps1 get "UCharacterMovementComponent"
```

不想联网（离线、或只想看本地有什么）：

```powershell
python ue58_docs.py ask "Nanite" --no-fetch
```

抓取相关：

```powershell
.\ue.ps1 status      # 看一眼当前覆盖率
.\ue.ps1 watch       # 实时滚动进度（Ctrl+C 退出，不影响后台抓取）
.\ue.ps1 start       # 开始 / 继续抓取（随时可中断，下次自动续传）
.\ue.ps1 stop        # 停止抓取
.\ue.ps1 check       # 数据质量验收
```

抓取分两步，**站点清单已经完成**（424 个站点地图、199,883 页已冻结），
现在跑的是第二步"抓正文"。进度行的含义：

```
正文 2,900/194,529；成功 2,899；失败 1；3.5 页/秒；自适应速率 9.0 请求/秒（退让 15 次）；预计剩余 900 分钟
     └ 已处理/总数     └ 成功    └ 失败  └ 实际速度  └ 当前请求速率   └ 被 Epic 拒绝后退让的次数
```

### `ask` 和 `find` 该用哪个？

- **`ask`**：你要答案。按 token 预算挑出最相关的几块正文、去重、裁剪，
  再附上交叉关系的指针。**AI 默认用这个。**
- **`find`**：你要目录。只给标题、类型、匹配方式、得分、出处，不给正文。
  适合"先看看库里有什么"。

```powershell
.\ue.ps1 ask "Lumen reflections" -TokenBudget 1500        # 问题简单，少给点
.\ue.ps1 ask "Gameplay Ability System" -TokenBudget 6000  # 需要通读，多给点
.\ue.ps1 find "Nanite" -Category cpp_api                  # 只在 C++ API 里找
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

## 二、AI 怎么用

已经装好一个 Claude Code 技能：`~/.claude/skills/ue5-docs/`。
任何会话里问到 UE 相关问题，AI 会自动先查这个本地库，而不是凭记忆或联网。

技能里写死了三条纪律，这是**保护上下文**的关键：

1. 默认走 `ask` 并带 `--token-budget`，不要无脑加大结果数量。
2. 绝不直接读 `exports/`、`manifest.jsonl`、`ue58_docs.sqlite3`——一个文件就能吃光上下文。
3. 回答必须附带 Epic 原出处 URL；查不到就如实说"本地还没抓到"。

---

## 三、数据长什么样

分三层，从原始到可用：

| 层 | 内容 | 为什么要有 |
|---|---|---|
| **原始层** | Epic 返回的原始 JSON，按内容哈希压缩存档 | 以后想换切分方式，不用重抓 |
| **知识层** | 按标题层级切成 `summary` / `signature` / `parameters` / `returns` / `remarks` / `examples` / `overview` / `details` / `navigation` / `references` | 长文不整篇喂给 AI |
| **路由层** | 全文索引 + 实体别名 + 交叉关系 + 上下文包 | 让检索又准又省 token |

每个知识块都带：UE 版本、分类、页面标题、完整标题层级、知识类型、Markdown 正文、
纯文本、token 估算、**Epic 原出处**、页面锚点、内容哈希、质量分。
目标约 550 token，硬上限 900 token。

### 交叉关系的可信度

关系不是拍脑袋连的，每条都记录证据和置信度：

| 证据类型 | 置信度 | 含义 |
|---|---|---|
| `official_link` | 1.0 | Epic 页面里真实存在的链接 |
| `unreal_display_name_metadata` | 1.0 | C++ 侧 `DisplayName` 与蓝图节点名完全一致 |
| `document_statement` | 0.92 | 正文明确写了 `Target is X` |
| `exact_normalized_name` | 0.82~0.9 | 只是名字一致，**属候选，需核对签名** |

置信度低于 1.0 的会明确标成"候选"，不会冒充官方对应关系。

---

## 四、目录结构

```
5.8.0/
├─ ue.ps1                    ← 你的唯一入口
├─ ue58_docs.py              ← 兼容外壳，等价于 python -m ue_kb
├─ ue_kb/                    ← 程序本体（分层，见 ARCHITECTURE.md）
├─ tests/                    ← 离线回归测试
├─ ue58_docs.sqlite3         ← 全部数据都在这里
├─ exports/                  ← 整本 Markdown 分片（给人翻，AI 不要整篇读）
├─ assets/                   ← 正文引用的图片
├─ ROUTER.md                 ← 自动生成的总路由与覆盖率
├─ report.json               ← 自动生成的统计
├─ site_inventory.jsonl      ← 冻结的全站页面清单（带 sha256）
└─ _scratch/                 ← 早期调试残留，确认不需要可以整个删掉
```

`manifest.jsonl`（逐页清单，约 90 MB）默认不再自动重写；需要时：

```powershell
python ue58_docs.py stats --manifest
```

---

## 五、抓取说明

Epic 对文档接口有限流（会回 HTTP 429）。抓取器**自己会找速率**：
连续成功就慢慢加速，被拒绝就降一档并全局冷却，无需手工调参。

进度日志里能直接看到：

```
正文 1,100/198,570；成功 1,085；失败 15；3.2 页/秒；自适应速率 9.29 请求/秒（退让 5 次）
```

- 被限流的页面**不算失败**，会留在待抓队列，也不消耗重试次数。
- 任何时候 Ctrl+C 或 `.\ue.ps1 stop` 都安全，进度全在数据库里。
- 想锁定固定速率：`python ue58_docs.py crawl --skip-discovery --requests-per-second 3`

### 抓完之后建议做一次收尾

正文抓取过程中修过一次正文清洗规则（Epic 有些字段里混着行内 HTML）。
最早抓下来的约 5,700 页是按旧规则切的，跑一次 `reprocess` 就能统一——
它**不联网**，直接用本地存档的原始 JSON 重新切分：

```powershell
python ue58_docs.py reprocess     # 重切知识块 + 重建交叉索引
python ue58_docs.py validate --phase content
```

这也正是"原始层"存在的意义：切分规则以后再怎么改，都不用重新抓一遍。

### 只补做某个阶段

```powershell
python ue58_docs.py assets                    # 只补下图片
python ue58_docs.py export                    # 只重新生成 Markdown 分片
python ue58_docs.py cross-index               # 只重建交叉关系
python ue58_docs.py reprocess                 # 用本地原文重切知识块，不联网
python ue58_docs.py stats                     # 覆盖率
python ue58_docs.py validate --phase content  # 数据合同验收
```

---

## 六、开发

```powershell
python -m unittest discover -s tests -v      # 22 个离线测试，不联网
```

改代码前请先看 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 七、相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) —— 代码分层与各模块职责
- [DATA_CONTRACT.md](DATA_CONTRACT.md) —— 数据结构与字段约定
- [AI_ROUTING.md](AI_ROUTING.md) —— AI 检索策略与上下文预算规则
- [ROUTER.md](ROUTER.md) —— 当前覆盖率（自动生成）

官方总入口：
[Unreal Engine 5.8 Documentation](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-5-8-documentation?application_version=5.8&lang=en-US)
