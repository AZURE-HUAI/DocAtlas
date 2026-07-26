---
id: BUG-010
title: "含 Markdown 链接的标题会生成不可跳转的来源锚点"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: chunking
labels: [chunking, source-url, citation, markdown]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
related: []
---

# 问题

HTML 标题转换成含链接的 Markdown 标题后，切块锚点把链接目标 URL 和版本说明一起
拼进 fragment，生成官方页面中不存在的来源地址。正文内容可读，但引用不能跳回
对应小节。

## 环境

- 数据集：`cppreference-2026-07-26` 临时小样
- 版本：2026-07-26 快照
- 入口：CLI `show`

## 复现

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas show K1337
```

该块标题来自 cppreference Algorithms 页的：

```markdown
### [Constrained algorithms](https://en.cppreference.com/cpp/algorithm/ranges) (since C++20)
```

## 实际结果

返回的来源为：

```text
https://en.cppreference.com/cpp/algorithm#constrainedalgorithmshttpsencppreferencecomcppalgorithmrangessincec20
```

`https://en.cppreference.com/cpp/algorithm` 本身 HTTP 200，但在线 HTML 中不存在该
fragment ID。测试用正则核对结果为 `EXISTS_IN_CURRENT_HTML=False`。

## 期望结果

来源 URL 应使用原页面真实小节锚点，或至少生成不包含 Markdown 链接目标的稳定标题
slug，并验证它能跳到相应小节。

## 可能方向

生成锚点前先把 Markdown 标题转换为可见纯文本，不能把链接 URL 当成标题文字。
若来源适配器能提供原始 HTML `id`，优先保留原站锚点会更可靠。

## 临时绕行

删除 fragment 后打开页面，再在页内搜索 “Constrained algorithms”。

## 调查记录

- 原始页面和目标 ranges 链接都可访问，问题只在 DocAtlas 生成的 fragment。
- 临时数据集已清理，K ID 不再存在；上面保留了完整实测输出和来源页面。

## 验证

`HeadingAnchorTests` 直接钉死议题里那个真实标题：

```python
text.heading_anchor(
    "[Constrained algorithms](https://en.cppreference.com/cpp/algorithm/ranges) (since C++20)"
)
# 修复前：constrainedalgorithmshttpsencppreferencecomcppalgorithmrangessincec20
# 修复后：constrainedalgorithmssincec20
```

断言里明确要求结果中不出现 `http` 和 `cppreference`。另外覆盖：
含行内代码的标题（`` `TArray` Members `` → `tarraymembers`）、纯文本标题、
只有链接没有可见文字的标题（降级为 `content`）、同名标题仍然互不重复。

真实库现状核对：`epic-ue-5.8` 共 11,500 个带 fragment 的知识块，
其中 fragment 里混进了链接地址的有 **18 个**（Epic 的标题极少带链接，
所以影响面小；cppreference 那种站点会大面积踩到）。这 18 条在
`reprocess` 到 `v4` 之后一并重算。

回归测试：128 用例全过。

## 解决记录

**根因**：`heading_anchor()` 把整个 Markdown 标题当成标题文字。它只做
"去掉所有非字母数字"，于是链接的 URL 被原样拼进了 fragment，造出一个官方页面
里根本不存在的地址。正文没问题，坏的只有引用能不能跳回那一节。

**改动**：`docatlas/text.py` 新增 `heading_visible_text()`——先把 Markdown 链接
还原成可见文字（方括号里的文字留下，圆括号里的地址丢掉），再去掉行内代码等标记，
然后才生成锚点。`heading_anchor()` 走这条路。

`text.py` 是适配器和知识包都能用的纯字符串层，规则放这里可以被所有来源共享，
不需要每个适配器自己防一遍。

`CHUNKER_VERSION` 已因 BUG-009 升到 `v4`，本修复搭同一次 `reprocess` 生效，
不额外增加一次全库重算。

**没有做的事**：没有改用连字符风格的 slug（`constrained-algorithms`）。
各家文档站生成锚点的规则并不一致，在没有实测证据的情况下换一种写法，只是把
一种猜测换成另一种猜测，还要付一次全库重算。议题的"期望结果"允许"至少生成不
包含链接目标的稳定标题 slug"，本次做到的正是这一条。
若将来要追求 fragment 与官方页面严格一致，正确的做法是让来源适配器把原站
HTML 的 `id` 传出来（议题"可能方向"里也提到了），那是一个独立的、可验证的改动。

## 外部关联

- GitHub Issue：
- 修复 PR：
