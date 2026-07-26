---
id: ENH-005
title: "为异语言查询提供召回适配或可执行诊断"
type: enhancement
status: discussion
lifecycle: unresolved
priority: medium
area: search
labels: [multilingual, search, diagnostics, cli]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-008]
---

# 背景

数据集明确声明英文时，中文自然问题常直接返回空结果和退出码 1；同义英文控制查询
立即命中正确页面。英文库不具备中文正文不是 Bug，但真实用户很难从当前空结果判断
应把问题改写成什么，也得不到可执行的下一步。

本轮固定版本测试中：

- Shader 中文“渐变条/把灰度花纹重新映射成颜色”的多种问法与中文 `search`
  稳定为空；英文 `Color Ramp Node Mix Color Node Noise Texture` 立即命中目标页。
- Geometry 中文“把小方块撒到网格表面并朝向法线”主智能体复现为
  206 ms、退出码 1、0 结果；同义英文控制查询 208 ms、退出码 0，返回 17 个知识块，
  前两位包括 Mesh Sample Nodes 与 Distribute Points on Faces。
- C++ 中文 variant/optional 问题会召回无关 C 文档；即使英文自然改写仍受排序问题
  影响，但精确英文符号至少能进入相关候选。

# 目标

- 当查询语言与数据集声明语言明显不一致时，提供可理解、可执行的下一步。
- 在风险可控时，允许用官方术语或轻量查询改写提高跨语言召回。
- 明确区分“异语言未命中”“inventory 没有页面”“页面 pending”和“本地排序未采用”
  等状态。

# 可能方向

- 检测查询语言与 `Dataset.language` 的明显差异，在空结果中建议官方语言关键词。
- 允许可审计的查询改写层，只改检索词，不伪造或翻译官方正文。
- 对常见领域术语维护小而明确的别名表；或由上层 Skill 在查询前生成原文术语。
- 即使不做跨语言召回，也让 CLI/MCP 的空结果包含可直接执行的英文重试建议。

# 待讨论问题

- 查询改写应属于核心检索、领域知识包还是上层 Skill？
- 如何避免错误翻译专有名词后补抓无关页面？
- 对非拉丁文字的语言检测需要做到什么精度才值得维护？

# 非目标

- 不要求把英文官方正文自动翻译成中文。
- 不把指定来源本身缺少中文版本判定为产品错误。
- 不在本议题中调整英文查询的排名；该问题由 `BUG-002` 跟踪。

# 验证思路

使用本轮三条固定控制组：

1. Blender Shader：中文 Color Ramp 自然问法 / 英文官方节点名。
2. Blender Geometry：中文散布并朝向法线 / 英文 Distribute + Align 问法。
3. cppreference：中文 variant/optional / 英文精确标准符号。

验证中文查询能命中目标，或至少返回明确的语言不匹配诊断与可执行下一步；同时确认
英文原查询的结果与耗时不退化。

## 验证

## 解决记录

## 外部关联

- GitHub Issue：
- 实现 PR：
