---
id: ENH-008
title: "版本意图的跨层结构化合同"
type: enhancement
status: resolved
lifecycle: resolved
priority: high
area: search
labels: [versioning, search, mcp, contract, multi-dataset]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-002, ENH-006]
---

# 背景

`BUG-002` 封存时把"版本语义"整体判为**有意未做**，理由是两个复现案例要求
**相反**的行为，任何排序规则都会修好一个、弄坏另一个：

- C++20 那条：希望压低只在 C++23 / C++26 存在的内容。
- Blender 那条：正确答案是 `Sample Index`，而它之所以正确，恰恰**因为它讲的是
  Blender 3.4 以前的旧行为**。按"优先当前版本"去排，正确答案会被压下去。

这个判断在当时是对的，但它成立的前提是"由 DocAtlas 自己猜用户想要哪个版本"。
只要核心必须猜，两个案例就永远不可能同时满足。

真正的缺口不是排序公式，而是**用户的版本意图没有一条能穿过各层的通道**：

| 层 | 缺口 |
|---|---|
| 版本适用信息 | 完全没有。`pages.doc_version` 是建库时统一回填的整库版本号，块级零信息 |
| 查询携带版本意图 | 没有通道。`ask()` 只接 query / budget / category |
| MCP 传递并回传版本条件 | 无 |

`datasets/cppreference-2026-07-26.toml` 里躺着一个 `cxx_learning_baseline =
"C++20"`，代码里一处都没有引用——上一轮想做版本感知留下的死配置。

## 目标

- 版本意图由上层 AI 判断，以结构化条件传入；核心执行，不推断。
- 版本适用信息必须来自文档**自己写的**内容，可核对，不是猜测。
- 不写死任何方向：不能"永远优先新版本"，也不能"永远优先当前版本"。
- 没有版本信息时绝不筛内容。
- 筛过必须说出来，并说清楚为什么。

## 可能方向

（原议题记录的参考方向，最终采用见"解决记录"。）

- 在页面级或知识块级保存版本适用信息。
- 由数据集或领域适配层提供可验证的版本适用信息。
- 检索层按结构化条件过滤、降权或保留。

## 待讨论问题

- 页面级元信息是否够用，还是必须到知识块级？
- 谁来提供版本的先后顺序？

## 非目标

- 不要求 DocAtlas 从问句里推断用户的版本意图，那是 AI/Skill 的职责。
- 不在本议题中调整与版本无关的排序，那属于 `BUG-002` 的历史范围。

## 验证思路

固定两条要求相反的回归查询（一条严格限定、一条迁移追溯），先记录改动前的
结果，再要求两条**同时**改善，且不带版本条件时结果逐条不变。

## 验证

### 固定回归查询（改动前先记录）

| 案例 | 查询 | 改前 |
|---|---|---|
| A 严格限定 | cppreference：`how do I mark a function as always assumed true` | 首位 `C++ attribute: assume (since C++23)`，次位 `Annotations (since C++26)` |
| B 迁移追溯 | Blender：`In Blender 5.2, what replaced the old Transfer Attribute node?` | 迁移证据 K177 排在**第 6** |

### 改后

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas ask "how do I mark a function as always assumed true" `
  --token-budget 1500 --no-fetch --version-target "C++20" --json
```

| 参数 | 结果 |
|---|---|
| 不带版本条件 | K1110 / K1071 / K1108 / K1253——**与改前逐条相同** |
| `--version-target C++20` | 排除 2 条，C++11 就有的 `Attribute specifier sequence` 升到首位 |
| `--version-mode compare` | 排除 0 条，与不限定时相同，每条带 `applies_to` |

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas ask "In Blender 5.2, what replaced the old Transfer Attribute node?" `
  --token-budget 1500 --no-fetch --version-mode migration --version-target 5.2 --json
```

| 参数 | 结果 |
|---|---|
| 不带版本条件 | 与改前逐条相同，K177 不在前 5 |
| `--version-mode migration` | **K177 从第 6 升到第 1**，带 `mentions 3.4` |
| `--version-target 5.2`（严格） | 排除 **0** 条——散文提及不是适用范围，严格模式碰不到 Blender |

两个案例的行为相反，来自**同一份证据**、**同一套规则**。

### MCP 端到端（一个连接，三个库）

`docatlas_list_datasets` 回传各库的 `version_vocabulary`：cppreference 是
"C++ 标准版本"，Blender 是"Blender 版本号"，`epic-ue-5.8` 明确不支持。
连续切三个库各查一次，`dataset.dataset_id` 逐条与请求一致，没有串库。
`version_intent` 原样回传（mode / target / excluded / 逐条 explanation）。
非法 `version_mode` 返回 `isError` 与可选值，不是静默忽略。

### 回归与验收

- 单元测试 185 → **198 全过**（新增 13 条）。
- `validate --phase content`：三个数据集各 14 项全 pass。
- 三个真实库计数：UE 199,883 页 / 25,487 块 / 17,830 关系，
  cppreference 6,957 / 631 / 189，均与改动前一致；Blender 因本轮补抓
  `Sample Index` 三页，块 92→96、关系 49→52。
- UE 库开库 0.21 秒，`chunk_versions` **0 条**——没有声明版本词汇的数据集
  不为这个功能付任何代价。

## 解决记录

**改的是"版本意图从哪来"，不是排序公式。** `BUG-002` 的结论没有被推翻：
让核心自己猜版本偏好，那两个案例确实无法同时满足。这里成立的原因是意图
**从外面传进来**，核心只执行。

### 分了四层，每层只干一件事

| 层 | 职责 | 改了什么 |
|---|---|---|
| 来源适配器 | 认出**本站**怎么写版本，并给出可比较的排序键 | `cppreference.py`、`blender_manual.py` 各加 `version_marks` / `version_sort_key` / `VERSION_VOCABULARY` |
| 通用核心 | 存标记、比大小、按意图筛，不认识任何产品 | 新增 `docatlas/versions.py`；`chunk_versions` 表 |
| 查询合同 | 携带"目标版本 + 意图类型" | `answer()` / `build_context_pack()` 加 `version_intent` |
| 入口 | 接收并回传条件 | CLI `--version-target` / `--version-mode`；MCP 同名两参数 + 结果回传 |

排序键必须由适配器给，这不是洁癖：**C++98 比 C++11 早，可数字上 98 > 11**，
任何通用的"版本号比大小"都会排反。两位年份按世纪还原（`>=90` 归 19xx），
以后出 C++29、C++32 都不用改代码。

### 三种证据强度，用途完全不同

    since     "从 X 版本才有"。硬证据，只有它能排除内容。
    until     "到 X 版本为止"。只报告，不裁决。
    mentions  正文提到某个版本。软证据，只在迁移追溯时加分。

`until` 为什么不能排除，是被真实数据否掉的：cppreference 的
`Algorithms library > Modifying sequence operations` 一块里同时有
`(until C++11)` 和 `(since C++11)`——那是同一张表相邻两行的脚注。按
"到 C++11 为止"排除，会把 C++20 里明明存在的整组 swap 操作删掉。

### 最要紧的一条：标记写在哪，决定它管多大范围

第一版规则是"块里最早的 `since` 晚于目标就排除"。**这条规则是错的**，
拿真实数据一验就露馅：

| 标记位置 | 块数 | 实际情况 |
|---|---|---|
| 标题里 | 6 | 6 条全对（Fold operations、`[[assume]]`、Annotations 确实是 C++23/26 才有） |
| 仅正文里 | 19 | 约 16 条是误杀 |

误杀的都是很早就有的内容：`std::all_of`（C++11）、`std::binary_search`、
实参依赖查找、`std::optional` 的成员函数表——`std::optional` 那张表只因为
有一行 `begin (C++26)`，整块就会被藏起来。

所以规则改成：**只有写在标题里的 `since` 能排除内容**。标题里的标记限定
整段，正文里的只限定那一行。`chunk_versions.scope` 记这件事。

两种错误的代价不对称：多给了新版本内容，用户看得见也看得懂；少给了本该有的
内容，用户完全无从发现。所以宁可漏放宽，不可误藏。已知的漏放宽有
`std::hive::reserve`、`std::erase_if(std::flat_map)`——页面标题不带标记，
因此不会被排除。这是有意接受的代价。

### Blender 只产出软证据，是如实反映来源

Blender 手册**没有**机器可读的版本标注约定，版本只在句子里出现：

> This recreates the behavior of the Transfer Attribute node from Blender
> versions before 3.4.

这条线索真实、也确实有用（它就是迁移问题的答案），但它是散文不是标注。所以
Blender 适配器只产出 `mentions`，永远不参与排除。96 块里只提取到 1 条，
**零误报**——正是那条迁移证据。

### 数据兼容

`chunk_versions` 是新表，三个已有库开库时自动建，不需要重建。已抓正文的
标记由 `versions.backfill()` 本地重算，**不联网**；`metadata.version_marks`
记规则版本，规则一变整批重来。数据集没声明版本词汇时整个跳过——UE 那个
25,487 块的库一次都不会被扫。

### 顺带清掉的死配置

`cxx_learning_baseline` 从 cppreference 配置里删掉了。学习基线是**用户的
意图**，属于每次提问，不该固化在数据集配置里——同一个库里既有人学 C++20
也有人学 C++23。

## 外部关联

- GitHub Issue：
- 实现 PR：
