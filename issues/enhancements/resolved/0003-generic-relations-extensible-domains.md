---
id: ENH-003
title: "关系能力通用化并允许领域独立扩展"
type: enhancement
status: resolved
lifecycle: resolved
priority: low
area: architecture
labels: [relations, architecture, extensibility, knowledge-pack]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-004, BUG-012, ENH-006, ENH-009]
---

## 目标

关系存储、查询、证据和更新归通用核心；产品专属的“为什么有关”由独立领域包提供。

## 验证

- `relation_rules(graph)` 同时服务全量与增量更新；领域包不写 SQL。
- UE 旧关系与新合同逐条对照：57 条，0 漏、0 多。
- cppreference、Blender 和 Roblox 未提供领域包时，通用官方链接关系仍可用。
- 新领域只需新增适配器、配置和可选知识包，无需修改关系核心。

## 解决记录

通用核心现负责实体解析、目标验证、去重、存储、诊断和统一查询；领域包只生成
带类型、证据和置信度的候选。类页面成员也已能成为独立实体。

UE 自动生成且没有官方独立实体的 Get/Set 节点只作为检索别名；官方明确声明的
访问器才建立真实关系，避免伪造证据。
