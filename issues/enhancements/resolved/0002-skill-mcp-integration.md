---
id: ENH-002
title: "Skill 与 MCP 形成明确的组合入口"
type: enhancement
status: resolved
lifecycle: resolved
priority: medium
area: integrations
labels: [skill, mcp, cli, contract]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
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

## 验证

契约测试（`SkillMcpContractTests`，全部离线）：

| 用例 | 守住什么 |
|---|---|
| `test_every_argument_the_handlers_read_is_declared` | 反射每个 handler 源码里的 `arguments.get("…")`，必须全部出现在该工具的 `inputSchema.properties` 里 |
| `test_skill_lists_the_tools_that_actually_exist` | `SKILL.md` 里的工具清单由 `mcpserver.TOOLS` 生成，不可能写出不存在的工具 |
| `test_skill_tells_the_ai_to_prefer_mcp` | `SKILL.md` 必须写明 MCP 优先 |
| `test_cli_and_mcp_answer_through_the_same_function` | 两边的 `ask` 必须都调 `context.answer()` |

原有的 `test_documented_commands_all_exist` 继续守 CLI 一侧。

握手与工具列表实测：5 个工具（`docatlas_ask` / `docatlas_search` /
`docatlas_show` / `docatlas_related` / `docatlas_list_datasets`）全部列出，
每个都有 handler（`test_every_advertised_tool_has_a_handler`）。

回归测试：128 用例全过。

### 2026-07-26：多数据集真实使用边界

本议题现有验证证明“有 MCP 时优先、没有时回退 CLI”，但本轮进一步遇到一种中间
状态：**MCP 存在，却服务错误的数据集**。

- 当前 MCP 固定服务 `epic-ue-5.8`。
- cppreference 与 Blender 两个目标数据集已在本机建立并通过小样合同，但
  `docatlas_list_datasets` 只列出当前 server 的配置，查询工具没有
  `dataset_id` 参数。
- 三个方向因此不能把“有 MCP 就优先”机械理解为可用，必须先核对数据集，再将
  181 次有效查询全部回退 CLI。

这说明组合约定还需要加入“能力与目标数据集匹配”这一层；更完整的中立路由和
结构化响应合同由 `ENH-006` 单独讨论。

## 解决记录

**缺口有三条，逐条处理：**

1. **能力不对齐**——MCP 少了 `related`。已补 `docatlas_related`，
   返回和 CLI 同一套结构化状态（BUG-005）。`get` 不单独开工具：
   `docatlas_ask` 已经会自动补抓，再开一个只会制造"该用哪个"的犹豫。
   `stats` 和建库类命令**有意留在 CLI**——它们要跑几分钟到几小时，
   不适合放进一次工具调用里，`WORKFLOWS.md` 负责这一半。
2. **公开合同缺参数**——`tool_ask()` 一直在读 `fetch_limit`，
   `inputSchema` 里却没有它，等于没有任何客户端知道可以传。已补，
   并用上表第一条用例把这类漏洞钉死，以后加参数忘了公开会直接测试失败。
3. **组合约定没写下来**——`SKILL.md` 新增"有 MCP 就用 MCP，没有才用命令行"
   一节：能力对照表、判断方式（工具列表里有没有 `docatlas_` 开头的工具）、
   参数对应关系（`--token-budget` ↔ `token_budget` …），
   以及"长时间、会改数据的操作只有 CLI"这条分界。
   表里的工具名由 `DOCATLAS_MCP_TOOLS` 占位符从 `mcpserver.TOOLS` 生成。

**顺带消掉的重复实现**：`tool_ask()` 原本自己写了一遍"要不要补抓"的判断，
和 CLI 的那份并行演化。现在两边都调 `context.answer()`，
`docatlas_search` / `docatlas_related` 也共用 `describe_lookup()` 等函数——
议题说的"两者最终共用同一套核心检索逻辑"从口头约定变成了有测试守着的事实。

## 外部关联

- GitHub Issue：
- 修复 PR：
