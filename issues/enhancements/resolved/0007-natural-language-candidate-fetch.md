---
id: ENH-007
title: "AI/Skill 将自然语言问题落成官方术语后查询"
type: enhancement
status: resolved
lifecycle: resolved
priority: low
area: skill
labels: [skill, query-rewrite, ai-layer, natural-language, multi-entity, context-composition]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-008, ENH-005, ENH-010]
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

## 重新打开记录

### 2026-07-26：多意图真实问题仍被压成一次宽泛查询

四方向 48 轮真实使用测试中，MCP 没有崩溃或超时，但首位正确只有 21/48。
主要失败形状不是单个术语翻译错，而是 AI 把多个可独立查询的实体继续塞进同一个
`ask`。这会让已抓的宽泛页面盖过真正目标，也无法组织完整的跨页答案。

Roblox 综合轮的原问题是：

```text
我在 Roblox Studio 做了一个大家一起捡金币的小游戏，怎样让每个人看到自己的分数、
两个人同时测试手机画面，并在确认权限后把游戏公开给朋友玩？
```

直接把中文交给 `studio_guides` 后首位是 `Roblox Studio setup`；改成一串英文术语
后也只部分命中 Projects、Scripting 和 PlayerGui。主智能体把它拆成单页官方术语
后，以下查询均能按需抓取正确页面：

- `Explorer`
- `Terrain Editor`
- `Studio testing modes`
- `Pivot Tools`
- `Score points`

Geometry Nodes 的道路、围栏、Position/Index/Normal、Transform/Curve conversion
也出现同一形状：单个官方标题可查，复合任务查询却由高频词页面占据首位。

这说明原 Skill 的原则是对的，但“少量官方术语”还不够可执行。AI 需要先产生一个
有边界的查询计划：一个目标页面或实体对应一次查询，再按用户任务顺序组织结果；
不能只把自然语言换成一长串英文关键词。

## 重新验证要求

- 固定使用上面的中文 Roblox 综合问题，不把中文直接传给 MCP。
- AI 明确列出目标数据集、分类、快照版本和至少四个原子查询。
- 每个原子查询只包含一个主要官方页面或实体；需要关系时另行调用 `related`。
- 最终答案同时覆盖计分、双客户端测试、设备模拟和发布权限，并保留各自官方 URL。
- 不查询 `open_cloud`，也不把宽泛相关页当成已回答。

## 二次解决记录

### 2026-07-26：按既有 AI 中间层边界关闭

复核确认，上述失败来自测试智能体没有执行已经写入 Skill 的中间层职责：

- 把中文原问题直接交给英文数据集；
- 把多个独立实体压成一次宽泛 `ask`；
- 在结果不足时没有逐个使用官方术语重试。

主智能体按现有规则拆成 `Explorer`、`Terrain Editor`、`Studio testing modes`、
`Pivot Tools` 和 `Score points` 后，五个目标均正常触发按需抓取并返回正式来源。
因此没有证据表明 DocAtlas 核心或 Skill 缺少这项能力，也不需要让核心承担自然语言
规划。

本议题恢复为 `resolved`。重新打开记录继续保留，用作测试执行约束：后续测试必须
实际使用 AI 中间层，不能把未执行中间层职责产生的结果记成产品增强。
