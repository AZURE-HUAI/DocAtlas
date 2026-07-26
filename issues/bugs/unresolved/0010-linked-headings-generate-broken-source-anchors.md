---
id: BUG-010
title: "含 Markdown 链接的标题会生成不可跳转的来源锚点"
type: bug
status: open
lifecycle: unresolved
priority: medium
area: chunking
labels: [chunking, source-url, citation, markdown]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
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

覆盖纯文本标题、含行内代码标题、含 Markdown 链接标题和重复标题，逐一核对生成
fragment 在官方页面中存在或按既定降级策略处理。

## 解决记录


## 外部关联

- GitHub Issue：
- 修复 PR：
