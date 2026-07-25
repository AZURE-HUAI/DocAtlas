---
id: ENH-002
title: "Skill 与 MCP 形成明确的组合入口"
type: enhancement
status: open
lifecycle: unresolved
priority: medium
area: integrations
labels: [skill, mcp, cli, contract]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-005]
---

# 背景

当前分层方向是：Skill 告诉 AI 何时查询、如何控制上下文和引用出处；MCP 将查询
能力包装成结构化工具；两者最终共用同一套核心检索逻辑。

但 `SKILL.md` 只指导 AI 执行 `python -m docatlas ...`，没有说明 MCP 存在时是否
应优先调用 `docatlas_*` 工具。因此客户端同时安装 Skill 和 MCP 时，AI 仍可能
绕过 MCP；目前更像两种可替换入口，而不是明确组合。

## 当前缺口

- Skill 包含 `get`、`related`、`stats` 等操作，MCP 目前只提供 `ask`、`search`、
  `show` 和 `list_datasets`。
- MCP 的 `tool_ask()` 实际读取 `fetch_limit`，但工具 `inputSchema` 没有公布该参数。
- Skill 与 MCP 只有独立测试，没有验证“有 MCP 时优先使用、没有时回退 CLI”
  的组合约定。

## 目标

让 Skill、MCP 和 CLI 的职责、优先级和回退行为对 AI 与开发者都清晰可验证。

## 可能方向

- Skill 在 MCP 可用时优先使用结构化工具，否则回退 CLI。
- 查询类能力逐步通过 MCP 对齐；长时间或高影响的维护操作继续由
  `WORKFLOWS.md` 和 CLI 承担。
- 补齐必要参数定义和组合契约测试。

也可以选择保持两种独立入口，或只让部分查询优先走 MCP。以上只是参考方向；
关键是让可用能力、差异和回退行为可以被用户与测试观察，不预先限定组合方式。

## 验证思路

- 在 MCP 可用时验证 Skill 选择约定的结构化工具，并覆盖至少一条参数传递测试。
- 在 MCP 不可用时验证同一查询能够回退 CLI，且关键结果语义保持一致。
- 对照 MCP `inputSchema` 与实际读取参数，确保公开合同不存在未声明参数。

## 非目标

- 不要求 MCP 成为唯一入口。
- 不要求把建库、全量抓取等长时间维护操作全部迁入 MCP。
- 不在本议题中预先确定每个命令必须属于哪一种接入方式。
