---
id: ENH-007
title: "AI/Skill 将自然语言问题落成官方术语后查询"
type: enhancement
status: resolved
lifecycle: resolved
priority: low
area: skill
labels: [skill, query-rewrite, ai-layer, natural-language]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-008, ENH-005]
---

# 目标

AI 把自然问题拆成少量官方术语、实体、分类和版本条件，再调用 MCP；核心保持严格
候选边界，不因自然问句宽泛补抓。

## 验证

- `Reference initialization`、`Image Texture Node` 等官方术语控制查询能定位
  正确页面。
- 版本意图可由 AI 传成 `strict`、`migration` 或 `compare`。
- 核心对不安全候选返回 `candidates_too_weak`，由 AI 改写后重试。

## 解决记录

Skill 已写明“理解问题 → 转换官方术语和结构化条件 → 查询 → 弱结果重试 →
按用户语言回答”。用户不需要自行研究页面名，核心也不承担开放式意图推断。
