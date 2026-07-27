---
id: BUG-020
title: "多打一个常见词就把正确页面挤出前十：档位起评分压过了 bm25 相关度"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: search
labels: [search, ranking, bm25]
reported_at: 2026-07-28
resolved_at: 2026-07-28
github_issue: null
fix_pr: null
related: [BUG-017, BUG-018]
---

# 问题

用户反馈：查 `Random Stream node`，结果里混进完全不相关的条目，只因为共享了
`stream` 这个词，得自己筛一遍才敢转述。

复现后发现比反馈的更严重：**多打一个到处都是的通用词，正确页面会被挤出前十**，
而 AI 只能看到返回的那几条，被挤掉的那一页它根本无从筛起。

## 复现

`epic-ue-5.8`，`search "random stream nodes"`，前 8 条：

```text
60.5 [all_terms] K220083  MetaSound Function Nodes Reference Guide
59.5 [all_terms] K214378  Unreal Engine 5.5 Release Notes
59.1 [all_terms] K216348  Unreal Engine 5.8 Release Notes
58.1 [all_terms] K217166  iOS
57.7 [all_terms] K217178  Windows
57.3 [all_terms] K217148  Android Settings
56.9 [all_terms] K219882  Platform Audio Settings
44.5 [any_term ] K227543  Random Streams        ← 正确答案，第 8
```

对照：去掉 `nodes` 一词查 `Random Stream`，前三名全是正确页面（93.5 / 93.1 /
72.7）。**多打一个词让结果变差了。**

同样的形状在另外两个数据集上各复现一次，所以不是 UE 独有：

| 数据集 | 查询 | 正确答案名次 | 首位是什么 |
|---|---|---|---|
| epic-ue-5.8 | `random stream nodes` | 8 | MetaSound Function Nodes |
| cppreference-2026-07-26 | `structured binding declaration` | >10 | Argument-dependent lookup |
| roblox-creator-2026-07-26 | `TweenService create tween` | >10 | Create a coin collection mechanic |

> 后两条查的页面在库里是 `pending`（清单有、正文未抓），没有知识块可排，
> 属于收录范围问题不是排序问题。真正的排序失败是第一条，以及下面规模化基准
> 里的那一批。

## 期望结果

排序按相关度走。用户多说一个词，最多让结果多几条，不该让最相关的那一页消失。

## 根因定位

**报告里的现象归因（"共享了 stream 这个词"）只说对了一半。** 共享词只是入场券，
真正决定名次的是档位起评分。

`docatlas/search.py` 的 `STAGE_BASE` 原本是：

```python
"all_terms": 50.0,   # 所有词都命中
"any_term":  30.0,   # 命中部分词
```

这 20 分的硬差距等于宣称"凑齐了所有词"一定比"只中了部分词"更相关。**这在长文档
面前不成立**：几万字的版本说明里随便都能凑齐三个常见词，而真正讲这件事的那一节
可能不含其中某个词。

更关键的是 **bm25 早就把顺序排对了，只是它的分值被丢掉了**。实测同一次查询各档的
原始 bm25（越负越好）：

```text
all_terms:  K227545 -13.72 | K219882 -11.60 | K219853 -9.67 | K214378 -8.32 | K216348 -6.36
any_term:   K227543 -16.89 | K227545 -13.72 | K227544 -12.78 | K219882 -11.60 | ...
```

全库最好的匹配是 `any_term` 档的 K227543（-16.89），而混进来的两条 Release Notes
只有 -8.32 / -6.36。`_fts_hits` 已经 `ORDER BY bm25(...)`，但 `_score` 只用了**档内
名次**（`- min(rank, 40) * 0.4`），分值本身被扔掉——于是"本档第一名"永远拿满起评分，
不论它到底有多好。

这和 [[BUG-017]]、[[BUG-018]] 是同一个家族：一个粗糙的常量压过了真实的证据强度。

## 解决记录

`docatlas/search.py`：

1. **`all_terms` 与 `any_term` 共用起评分（都是 40）。** 这两档查的是同一组词，
   只有布尔运算符不同，所以同一行在两档里的 bm25 完全相等——两档的分数落在同一
   把尺子上，可以直接比大小。"凑齐了几个词"这件事 bm25 本来就算进去了，不需要
   再额外给一次分。

2. **新增 `stage_relevance()`**：把同尺档的 bm25 归一化到 0..1，基准是这些档
   **合起来**的最佳值，再乘 `RELEVANCE_WEIGHT = 30.0` 计入得分。基准必须跨档取：
   只按本档取的话每一档的第一名都是 1.0，等于换个写法回到原来的 bug。

3. `phrase`（整串短语）和 `prefix`（词干展开）的 bm25 不同尺，不参与这次归一化，
   仍按档内名次兜底。

4. `_fts_hits` 把 bm25 作为 `bm25_score` 列一并返回；权重式子收成 `_BM25` 一个
   常量，排序和打分共用，不再各写一遍。

改动只动打分层，没有引入任何数据集专属的词表或规则。

### 试过但去掉了的

一并试了"`any_term` 档不再因候选够用而跳过"，理由是怕正确答案只差一个常见词而被
跳过。实测**收益为零**（规模化基准 88.4% → 88.1%，在噪声内），却让 20 万页库的
中位查询耗时从 72ms 涨到 105ms。已回退，跳档逻辑保持原样。

## 验证

**规模化基准**（`n=320`）：每个库随机抽 80 个已抓取页面，用「页面标题」和「页面
标题 + 该库自己最高频的通用词」两种查询各查一次，看这一页能不能排第一。通用词由
数据算出（该库标题里出现最多的非停用词），四个库同一套规则。

| 数据集 | 掺词 | 纯标题（前 → 后） | 掺词后（前 → 后） |
|---|---|---|---|
| epic-ue-5.8 | `set` | 100% → 100% | 60% → **80%** |
| cppreference-2026-07-26 | `library` | 95% → 95% | 85% → **94%** |
| blender-manual-5.2 | `node` | 91% → 91% | 80% → **85%** |
| roblox-creator-2026-07-26 | `roblox` | 98% → 98% | 92% → **95%** |
| **合计** | | 95.9% → 95.9% | **79.4% → 88.4%** |

四个库全部改善，纯标题查询一分不掉。

**性能**：20 万页库上 8 条查询各跑 3 遍，中位 72.6ms → 72.2ms，无退化。

**回归**：21 条定点查询（含 [[BUG-016]] [[BUG-017]] [[BUG-019]] 的原始用例）
首位命中 19/21 → 20/21，前三 20/21 → 21/21，无一退步。

**协议层**：新进程从陌生目录走 MCP stdin/stdout 调 `docatlas_search`，确认
`random stream nodes` 的首位是 `Random Streams`。

回归测试见 `RelevanceRankingTests`。夹具有个关键点：**必须先铺 20 页含通用词的
普通文档**。两三页的语料里任何词都是稀有词，IDF 是反的——第一版夹具就栽在这里，
bm25 反而偏向长页面，测试"通过"靠的是标题字面加成，等于没测到这次的修复。

变异检验 7/7 全部杀死：档位分改回 50/30、权重归零、`_score` 忽略相关度、`absorb`
不传相关度、归一化基准退回本档、把不同尺的档混进同一次归一化、`ORDER BY` 丢掉
bm25。其中最后一条第一版存活——它只在候选池溢出时才起作用，小夹具里所有行都进得来，
补了一条直接断言 `_fts_hits` 返回顺序的测试才杀掉。

## 外部关联

- GitHub Issue：
- 修复 PR：
