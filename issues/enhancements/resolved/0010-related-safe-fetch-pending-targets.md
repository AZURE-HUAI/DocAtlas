---
id: ENH-010
title: "`related` 安全补抓 pending 官方目标并增量建立关系"
type: enhancement
status: closed
lifecycle: resolved
priority: medium
area: relations
labels: [relations, on-demand-fetch, official-link, incremental, diagnostics]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [ENH-003, BUG-008, BUG-013]
---

# 目标

当 `related` 已确定实体存在，并识别出同一数据集清单内尚未抓取的官方链接目标时，
提供一个有数量上限、可审计的按需闭环：补抓安全目标、运行增量关系更新，再返回
新关系。默认诊断仍应保持只读语义，不得无边界抓取或越过数据集范围。

# 实测证据

四方向关系测试共 8 轮，通过 5 轮。已建立的关系实体、方向、证据和置信度错误数
为 0；主要缺口是 pending 目标需要调用方手工拼出另一条 `ask`。

Shader 的 SHADER-R10 验证了底层能力本身可用：

1. `related(K172)` 返回 `entity_found_but_no_relations`，并列出
   `Texture Coordinate Node` 为 pending 目标。
2. 调用方手工用精确路径 `ask`，成功抓取 1 页。
3. 再次 `related(K172)` 得到 Image Texture → Texture Coordinate 的
   `parameter_type`，`evidence_kind=official_link`、`confidence=1.0`。
4. 对目标实体查询还能看到同一关系的 incoming 方向。

C++ `filter_view`、Geometry `Instance on Points` 和 Roblox `RemoteEvent` 轮次则停在
pending 或 `entity_found_but_no_relations`。诊断没有说错，但用户必须自己理解路径、
选择目标并重复调用，关系功能才可能完成。

# 必要边界

- 只允许补抓 `related` 本次返回、且已在同一数据集 inventory 中的目标。
- 必须沿用当前数据集的版本、语言和官方 URL 规则。
- 数量有明确上限；候选不安全时只返回诊断，不抓取。
- `target_outside_inventory`、站外链接和缺少正式实体的别名不得进入闭环。
- 新关系仍需真实双方实体、官方证据、合法方向和置信度；不能因补抓而降低标准。
- 抓取或增量更新失败时保留原状态，并返回实际失败阶段与下一步。

## 验证

- 使用 SHADER-R10 的冷库前置状态，一次受限操作完成 pending → fetch → relation，
  结果与现有手工三步流程逐字段一致。
- C++、Geometry、Roblox 各验证一个 pending 目标；不能建立关系时给出确定原因。
- 验证完整索引关系与增量关系去重，重复执行不增加重复边。
- `evidence_kind=official_link`、`confidence=1.0`、实体、方向和 `evidence_url`
  与原文一致。
- inventory 外目标、弱候选和超过上限的请求均不联网。

## 解决记录

2026-07-26 审查后决定不在 `related` 内实现自动补抓，状态为 `closed`。

- 底层能力没有缺失：返回结果已经列出同一清单内的 pending 路径，并给出精确
  `python -m docatlas get "<path>"` 下一步；抓取后增量关系能够正确建立。
- Codex/Skill 的 AI 中间层本来就负责读结构化结果并调用下一步工具，因此用户不需要
  自己理解路径或手工拼命令。把同一编排再写进 `related` 是重复能力。
- `related` 当前是只读诊断。让它隐式联网、写库和建立关系会引入意外副作用及失败
  状态，却没有修复错误答案；需要自动闭环时由 AI 显式调用 `get`，过程更透明。

若将来存在“返回的路径无法被 `get` 抓取”或“抓取后关系仍不建立”的稳定复现，应
分别登记为抓取或关系 bug；不能以方便少一次调用为由重开本增强。

### 后续说明修订

复盘确认原 Skill 只要求“读取 `next_steps`”，没有明确要求 AI 继续执行，测试流也
只检查步骤是否正确。现已补成明确闭环：安全 pending 由 AI 使用同一 `dataset_id`
和有限 `fetch_limit` 调用 `docatlas_ask` 补抓，再重试一次 `related`；越界目标、
弱候选或失败则停止并说明。这样补齐使用说明，不改变 `related` 的只读语义。

# 外部关联

- GitHub Issue：
- 实现 PR：
