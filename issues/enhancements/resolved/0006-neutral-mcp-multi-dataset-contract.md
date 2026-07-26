---
id: ENH-006
title: "为 MCP 提供中立的多数据集路由与结构化交换合同"
type: enhancement
status: resolved
lifecycle: resolved
priority: high
area: integrations
labels: [mcp, multi-dataset, contract, relations, extensibility]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [ENH-002, ENH-003, BUG-009, ENH-009]
---

# 目标

一个领域中立的 MCP 连接发现并查询所有数据集；新数据集无需复制工具或修改核心。

## 验证

- 每个工具可传 `dataset_id`，`docatlas_list_datasets` 返回能力与分类。
- JSON 响应带版本化结构；Markdown 仍是默认的简洁输出。
- 关系统一返回类型、方向、证据、置信度、出处、状态和下一步。
- UE、cppreference、Blender、Roblox 在一个连接中连续查询不串库。
- Roblox 接入只新增适配器、配置和测试，没有修改 MCP、关系核心或数据库结构。
- 231 项测试及四个数据集的 inventory/content 验收通过。

## 解决记录

MCP 已与具体产品解耦。通用层负责数据集路由和统一合同；来源适配器提供站点数据；
领域包只补充产品专属关系规则。建库和全量抓取仍保留在 CLI。
