---
id: BUG-018
title: "每页最多 2 块的硬上限，会在预算充足时用不相关的跨页内容顶替同页更优候选"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: context
labels: [cppreference, context-assembly, budget]
reported_at: 2026-07-27
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-014]
---

# 问题

查询关于 coroutines（协程）的问题时，正确页面 `cpp/language/coroutines` 本身
就有至少 8-9 条明显相关的知识块（`co_await` 语义、`suspend_always`、执行
模型、`co_yield` 示例等），但最终答案里这一页最多只出现 2 条，预算剩下的部分
被同一次查询里评分低得多、主题完全不相关的其他页面（C++ 缩略语词表、编译器
厂商兼容性列表等）填掉——即使这些内容和"协程"没有任何实质关联。

这不是没找到内容：`docatlas_search` 能证明同一页面还有一堆评分 30-56 的
更贴切候选，`docatlas_ask` 却没有优先选中它们，而是转去用不相关内容凑满
预算。

## 复现

```
docatlas_ask(query="co_yield coroutines promise_type", dataset_id="cppreference-2026-07-26",
             version_target="C++20", version_mode="strict", token_budget=3000, no_fetch=true)
→ 7 条知识块，约用 2,160/3,000 token（预算还剩约 840）。来自
  cpp/language/coroutines 页的只有 K1464（co_yield 示例）、K1451（概述）2 条；
  其余 5 条是 K741（C++20 版本总览）、K1024（C++ language 导航页）、
  K1040（Acronyms 缩略语词表——与协程毫无关系）、K742（C++20 新特性列表）、
  K1019（编译器厂商兼容性列表——与协程毫无关系）。

docatlas_search(query="co_await coroutine_handle suspend_always awaiter",
                 dataset_id="cppreference-2026-07-26", category="language", limit=8)
→ 8 条结果，评分 30.5-56.0，**全部**来自同一个 cpp/language/coroutines 页
  （K1462/K1461/K1451/K1460/K1459/K1466/K1458/K1453），没有一条进入上面
  `ask` 的最终答案。
```

用两种不同措辞独立复现同一模式：预算有余量时，宁可去别的页面找评分更低、
主题不相关的内容，也不多用同一页面里评分明显更高、主题高度相关的候选。

## 根因定位（闸门 3）

`docatlas/context.py:46`：

```python
MAX_CHUNKS_PER_PAGE = 2
```

`_select_primary()`（约 52-75 行）逐个候选块填充预算时：

```python
if per_page.get(page_id, 0) >= MAX_CHUNKS_PER_PAGE:
    continue
```

不管预算还剩多少、这一页排在后面的候选分数比别的页面候选高多少，同一页最多
只能有 2 条进入 `primary_knowledge`。一旦达到这个硬上限，算法直接跳过本页
后续所有候选（包括分数明显更高的 `co_await` 相关内容），转而去候选池里找
下一个"来自不同页面"的项——而这次查询的候选池里，下一批不同页面的候选
（Acronyms、编译器厂商列表）本身评分就低，只是因为"来自另一页"而不受这个
上限约束，所以被选中顶替。

这个上限对"一个类的成员列表页动辄上百个方法"这类场景（`retrieval_policy`
里 `"large_cpp_member_indexes": "ranked down"` 暗示的正是这类顾虑）是合理
的防御；但对 Coroutines 这种概念性参考页——`co_await`/`co_yield`/执行模型
本来就是同一个连贯主题在同一页里的不同小节——2 条硬上限会人为切断本可以
在预算内完整呈现的单页深度内容，转而用不相关的跨页内容凑数，是更差的结果。

## 复现强度 / 四道闸门结论

- **闸门1（规范调用复跑）**：通过。两种不同措辞的完整 MCP 调用（显式
  `dataset_id`、合理预算 3000、官方术语）独立复现同一模式。
- **闸门2（内容是否在库里）**：通过。`docatlas_search` 直接证明同页有
  8 条评分 30.5-56.0 的候选存在于库中，没有一条进入最终答案；不是覆盖
  范围问题。
- **闸门3（能否说出改哪一层）**：通过，见上——`docatlas/context.py:46`
  的 `MAX_CHUNKS_PER_PAGE` 常量与 `_select_primary()`（约 63-64 行）的
  每页硬上限逻辑，应该在预算允许、且同页候选分数明显高于跨页候选时放宽，
  而不是无条件截断。
- **闸门4（最终输出层复现）**：通过。经完整 `docatlas_ask` 复现，且用
  `docatlas_search` 独立证实了"更优候选存在但未被选中"这一关键前提，不是
  只测到内部函数。

## 验证

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas ask "co_yield coroutines promise_type" --token-budget 3000 --no-fetch
```

| | 修复前 | 修复后 |
|---|---|---|
| 来自 Coroutines 页 | 2 条（K1464、K1451） | **4 条**（+K1466、K1463） |
| 厂商兼容性列表 K1019 | 在答案里 | 已挤出 |
| 用量 | 2,160 / 3,000 | 2,180 / 3,000 |

**小预算不退化**（这是改动最容易出错的地方）：预算 1500 时仍是 2 条协程内容，
与修复前完全一致。

回归测试见 `EndToEndTests` 新增的三条（预算够用时同页更优内容不被跳过 / 一页
仍然吃不掉整个预算 / 预算小的时候保底那几块照给）。

第一版测试**没能通过变异检验**——它只断言了上界，把上限改回写死 2 照样满足，
等于根本没测这个修复。重写成直接对 `_select_primary` 构造 BUG-018 的原始形状
（一页 5 条高分候选 + 三页各 1 条低分候选）并断言正向行为之后才杀得掉。
连同解开 BUG-014 耦合那一处，本次 11 条变异全部被杀死。

## 解决记录

四条里唯一一条**现象和根因报告都说对了**的。先量了一遍候选池确认机制，不是
打分算错：

```text
K1464 56.5 / K1451 40.6 / K1462 37.4 / K1466 36.2 / K1463 36.1
K1465 35.7 / K1461 35.4 / K1457 34.3 / K1456 33.5   ← 全是 Coroutines 页
K741  33.2 / K1024 32.4 / K1040 32.0（缩略语词表）/ K1019 24.1（厂商列表）
```

上限卡在 2 之后，**7 条分数更高的同页内容被跳过**，让位给 24.1～33.2 的填充
内容。

### 改成限预算占比，而不是限块数

要防的是"一篇长文吃掉整个预算"，那本来就是**预算占比**的事。写成块数，预算
一变大就反过来伤人：预算 6000 说的正是"我要通读这一页"，却仍然只给 2 块。

```python
MIN_CHUNKS_PER_PAGE = 2     # 保底
MAX_PAGE_BUDGET_RATIO = 0.6 # 超过保底之后，按占比继续给
```

保底那 2 块不能省：预算小的时候按占比算装不下第二块，那会比原来更差。

`retrieval_policy` 里上报的字段跟着改成 `min_chunks_per_page` 和
`max_page_budget_ratio`——继续报 `max_chunks_per_page: 2` 就是在说假话。

### 顺带解开一处隐藏耦合

改完跑测试，`UrlFragmentTests` 两条立刻红了。查下去发现 BUG-014 的保证
（地址里带 `#小节` 时答案就到这一节为止）一直是**靠"每页最多 2 块"顺手截断**
实现的：`build_context_pack` 把整页其余内容也接在小节后面当上下文，指望预算和
上限把它裁掉。同页限额一放宽，整页内容就又回来了——正是 BUG-014 反对的
"静默退回页面概览"。

限定条件应该由它自己那一层写死，不该寄生在一个不相干的常量上。改成命中小节时
`candidates = section`，与同页限额彻底解耦。

### 没有一并处理的

答案里仍然会出现 `Acronyms`（32.0 分）。导航/索引页的首块被 `classify` 判成
`summary`，在概念提问档拿到 +8，因此普遍偏高。这是打分层的事，和本议题的
硬上限无关，没有在这次改动里动它。

## 外部关联

- GitHub Issue：
- 修复 PR：
