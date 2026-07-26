---
id: ENH-005
title: "AI/Skill 将用户语言转换为数据集语言后查询"
type: enhancement
status: resolved
lifecycle: resolved
priority: medium
area: skill
labels: [multilingual, skill, query-rewrite, ai-layer]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-008, ENH-007]
---

# 目标

AI 读取数据集语言，把用户问题转换成原文官方术语后查询；最终仍用用户语言回答。

## 验证

- 核心能返回独立的 `language_mismatch` 状态。
- Skill 明确要求保留代码符号、版本号和专有名词，并在弱结果时改写重试。
- 查询改写不修改官方正文和出处。

## 解决记录

职责已固定：AI 是用户与 MCP 之间的理解和翻译层；DocAtlas 核心不实现开放式机器
翻译，只提供数据集语言、状态、结果和证据。
