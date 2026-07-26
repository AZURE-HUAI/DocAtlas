---
id: BUG-008
title: "`ask` 无法按官方页面名补抓 route slug 不完全相同的 pending 页面"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: on-demand
labels: [ask, on-demand-fetch, inventory, ranking, multi-dataset]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-002, BUG-003, ENH-007]
---

# 问题

页面已在清单中但正文未抓取时，官方标题、限定符和带扩展名路径不能稳定触发补抓；
弱相关本地结果会掩盖真正目标页。

## 复现

典型失败包括 `Fields`、`std::from_chars`、`Wave Texture Node`、
`Set Field Of View` 和 `duration_cast milliseconds`。

## 验证

- 支持 `exact_slug`、`token_exact_slug`、`slug_contains` 和
  `path_covers_query` 四档安全候选。
- `.html` 等文档扩展名不再算页面名，限定符和常见分隔符可正确匹配。
- 精确目标未本地化时会补抓；不安全候选只报告 `candidates_too_weak`。
- 宽泛概念问句不会触发大范围抓取。

## 解决记录

本议题范围内的候选定位和诊断已完成并验证，状态为 `resolved`。自然语言问题由
AI/Skill 先转换成官方术语，见 `ENH-007`。
