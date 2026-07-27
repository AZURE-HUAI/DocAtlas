---
id: BUG-019
title: "代码块里的注释被当成标题，代码被切碎且小节名是半句代码"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: chunking
labels: [chunking, headings, roblox]
reported_at: 2026-07-27
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-016]
---

# 问题

`docatlas/chunking.py` 的 `split_sections()` 逐行拿 `HEADING_RE` 判断标题，
**完全没有代码围栏跟踪**。于是围栏里以 `#` 开头的行——Python、bash、PowerShell
的注释——全部被当成一级标题：代码块从中间被切断，切出来的小节名是半句代码。

这一条不在本轮四份测试报告里。它是为了确认"放宽标题识别会不会误伤"而写脚本
全库扫描时撞见的，形状和 [[BUG-016]] 是同一类（逐行判断标题、不理会 Markdown
的块结构），所以在同一次改动里一起修了。

## 复现

`roblox-creator-2026-07-26` 的 `/docs/en-us/cloud/guides/configs`，库里实际
存着这些 `heading_path`：

```text
Experience configs > Optional: send previousDraftHash if you have an existing draft and want optimistic concurrency
Experience configs > Use the draftHash from the previous step
Experience configs > r = requests.get(f"{BASE}/full", headers=headers)
Experience configs > Publish the revert
```

后两条尤其明显：`r = requests.get(...)` 是 Python 代码的一行，不是任何标题。
它前面那行是 `# Get your published config` 这样的注释，被当成标题之后，`finish()`
在那里切了一刀，剩下的代码成了新小节的正文。

全库扫描结果（三个数据集）：

| 数据集 | 受影响页面 | 被误判成标题的行 |
|---|---|---|
| roblox-creator-2026-07-26 | 3 页 | 18 处 |
| cppreference-2026-07-26 | 0 | 0 |
| blender-manual-5.2 | 0 | 0 |

cppreference 与 blender 为 0，是因为它们的正文代码块里基本没有以 `#` 开头的
注释（C++ 用 `//`，Blender 手册少有代码）。换一个 Python / shell 内容多的站点，
这个数字会立刻上去——机制在通用核心里，对任何数据集都生效。

## 期望结果

代码块里的内容一律是内容，不是结构。围栏里的 `#` 注释不应该切断小节，更不该
拿来当小节名对外展示。

## 根因定位

`docatlas/chunking.py` 的 `split_sections()` 主循环原本是：

```python
for line in lines:
    match = HEADING_RE.match(line)
```

`HEADING_RE` 只看这一行长什么样，不知道自己在不在代码块里。

## 解决记录

新增 `fenced_line_numbers()`：先扫一遍，把成对围栏之间的行号收进一个集合，
主循环里这些行直接当正文。

**关键细节是落单的围栏不算数。** 实测三个 Roblox 页面的围栏标记是奇数个
（15、19、27），按朴素的"遇到围栏就翻转开关"处理，最后一个落单的围栏会让
它之后的**所有真标题**一起被吞掉——那比不处理更糟。所以只认配对的：先记住
开头，遇到下一个才把中间那段收进来；没等到配对的一律忽略。

放在 `chunking.py` 而不是 `constants.py`：它是切分逻辑，不是一条正则。

## 验证

`CHUNKER_VERSION` v5 → v6，四个数据集全部重加工。同一页面修复后：

```text
Experience configs > Experience configs > Create or update configs
Experience configs > Experience configs > Publish your changes
Experience configs > Experience configs > Get your published config
Experience configs > Experience configs > View history and roll back
Experience configs > Experience configs > Limitations
```

四条垃圾标题全部消失，代码块不再被切断。

回归测试见 `HeadingRecognitionTests` 的前两条。变异检验：让
`fenced_line_numbers` 返回空集，以及让落单围栏也当开头——两种改法各自都让测试
变红。

## 外部关联

- GitHub Issue：
- 修复 PR：
