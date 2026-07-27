---
id: BUG-022
title: "页面摘要被插回仍然包含原句的正文，同一句话连着出现两遍"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: chunking
labels: [chunking, adapters, data-quality]
reported_at: 2026-07-28
resolved_at: 2026-07-28
github_issue: null
fix_pr: null
related: [BUG-023]
---

# 问题

用户反馈：cppreference 的 C++20 页，简介那段话出现了两次。

真实库里确认，`/cpp/20` 的 0 号小节正文就是：

```text
C++20 is a major version after C++17, featuring major features (concepts,
modules, coroutines, and ranges) and other language and library features.
The standard was published in December 2020.
C++20 is a major version after C++17, featuring major features (concepts,
modules, coroutines, and ranges) and other language and library features.
The standard was published in December 2020.
```

影响的不只是观感：这一段既进全文索引也进上下文包，等于用双倍预算说同一件事，
AI 拿到的有效信息少了一截。

## 复现

```bash
DOCATLAS_DATASET=cppreference-2026-07-26 python -m docatlas ask "https://cppreference.com/cpp/20" --token-budget 600
```

第一条知识块里同一句话紧挨着出现两遍。

## 期望结果

同一句话只出现一次。

## 根因定位

**报告说的"加工程序提取摘要后又插进正文"是对的，但成因在适配器和切分程序的
接缝上，不在任何一方内部。**

适配器的 `description` 多数就是正文第一句——cppreference 和 Blender 都直接取
markdown 里第一条不以 `#` / `|` / `-` 开头的行：

```python
description = next(
    (plain_text(line) for line in markdown.splitlines()
     if line.strip() and not line.lstrip().startswith(("#", "|", "-"))), "")
```

而 `chunking.split_sections` 拿到这个 `description` 后无条件插到 0 号小节最
前面，唯一的去重判断是"摘要和**标题**是不是同一个"——从没跟**正文**比过。于是
摘要来自正文的那些站点，每一页都重一遍。

摘要本身不能一律不要：Epic 的摘要来自页面 JSON、Roblox 的来自 front matter，
正文里根本没这句话。真实库里 Roblox 有 102/106 页的 0 号小节除了摘要没有别的
内容，去掉就等于把这一页的开场白删了。所以判断必须是条件式的。

## 解决记录

`docatlas/chunking.py` 新增 `description_repeats_lead()`，只有正文开头没说过这
句话时才把摘要插进去。

放在切分层而不是各个适配器里：四个适配器有三个都会撞上，逐个打补丁等于把同一
条规则抄三遍，而"要不要把摘要并进正文"本来就是切分程序的职责。

三个判断细节，每一条都对应一种真实形状：

1. **比"包含"，不比"相等"。** 摘要常常是正文首句的截断版，正文那句还带着
   `**加粗**` 和后半截（`…a light-weight physics simulation solution` 之后还
   接着 `, built from the ground up…`）。
2. **比拍平之后的文本。** Blender 有一批页面的摘要就是首图的 alt 文字，而正文里
   那一行还裹着完整的图片 Markdown 语法，不拍平根本对不上。
3. **只看第一个标题之前的那几行。** 重复只可能发生在这里，摘要正是插在它们前
   面的。这同时保证摘要被跳过时正文一定不空——匹配得上就说明这段正文里本来就
   有这句话。

第一版写成"正文**开头就是**摘要"，被 `/cpp/compiler_support` 打回：那一页开头
是一张"本页可能滞后"的提示表格，正文首句排在表格后面，而适配器挑摘要时正好跳过
了表格行。改成"这段开场白里有没有这句话"才覆盖住。

切分规则变了，`CHUNKER_VERSION` v6 → v7。

## 验证

**全量重跑真实原始页面**（四个数据集，`epic-ue-5.8` 随机抽 600 页）：

| 数据集 | 抽查 | 摘要不再插入 | 其中确认是重复 | 正文因此变空 |
|---|---|---|---|---|
| cppreference-2026-07-26 | 193 | 61 | 61 | **0** |
| blender-manual-5.2 | 91 | 29 | 25 | **0** |
| epic-ue-5.8 | 600 | 5 | 5 | **0** |
| roblox-creator-2026-07-26 | 107 | 0 | 0 | **0** |

Roblox 一页没动是关键的反向证据：它的摘要全部来自 front matter，正文里确实没有
这句话，规则正确地没有去碰。Blender 那 29 与 25 的差额是首图 alt 那一类，同样是
真重复，只是拍平口径不同没被计数器认出来。

**重加工后的真实库**：`reprocess --force` 后 cppreference 全库
「0 号小节里摘要出现两次以上」的页数 = **0**（修复前 61），`/cpp/20` 的开场白
只剩一句。`validate --phase content` 通过。

回归测试见 `PageSummaryTests`（5 条：三种重复形状 + 两条反向控制组）。把
`description_repeats_lead` 还原成恒假，三条断言新行为的用例全红，两条反向控制组
全绿——测的确实是这次的改动。

## 外部关联

- GitHub Issue：
- 修复 PR：
