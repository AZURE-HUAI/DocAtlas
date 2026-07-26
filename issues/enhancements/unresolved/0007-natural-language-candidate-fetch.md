---
id: ENH-007
title: "AI/Skill 将自然语言问题落成官方术语后查询"
type: enhancement
status: discussion
lifecycle: unresolved
priority: low
area: skill
labels: [skill, query-rewrite, ai-layer, natural-language]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-008, ENH-005]
---

# 背景

`BUG-008` 把按需补抓的候选定位从"整条查询规范化后与 URL 完全一致"扩展到四档
（`exact_slug` / `token_exact_slug` / `slug_contains` / `path_covers_query`），
解决了官方页面名、带命名空间的符号和 `.html` 路径这些"像一个名字"的查询。

候选定位要求查询里的实词能对上具体页面标题或路径，这是 DocAtlas 防止误抓大量
页面的安全边界。多词自然问句不满足这个条件时，不应该放宽核心抓取规则；应由
AI/Skill 理解用户意图，先提取一个或多个官方术语，再分别查询 DocAtlas。

普通用户不需要知道 route slug、页面标题或关键词组合。把自然表达翻译成检索计划，
正是 AI 作为中间层的职责，而不是 DocAtlas 核心缺陷。

# 目标

- AI/Skill 把用户问题拆成少量、明确的官方页面名或技术符号。
- 先用这些术语查询或补抓，再综合多个 DocAtlas 结果回答原问题。
- 弱相关时由 AI 自动换术语重试，不要求用户自己研究文档目录。
- 保持 DocAtlas 的严格补抓上限和确定性候选规则，避免自然问句拖回大范围页面。

# 可能方向

- 在 DocAtlas Skill 中加入“理解问题 → 生成官方术语 → 分项查询 → 核对来源 →
  汇总回答”的查询规划。
- AI 结合 `weak_candidates`、`target_outside_inventory` 和搜索结果决定如何重试。
- 核心继续只接收明确查询，不加入开放式意图推断或宽泛自动补抓。

# 待讨论问题

- AI/Skill 应记录多少查询改写过程，才能让失败结果容易复现？
- 误提取会不会把一个概念性问题错误地坐实成某个不相关页面，进而触发补抓？
- 是否需要像 `weak_candidates` 一样，先只报告候选、不抓取，观察真实查询后
  再决定要不要放开自动补抓？

# 非目标

- 不让 DocAtlas 核心直接理解任意自然问题或执行宽泛抓取。
- 不要求用户自己把问题改写成官方标题。
- 不在本议题中处理排序问题，那是 `BUG-002` 的范围。
- 跨语言转换由 `ENH-005` 记录；本议题只处理自然问题到检索计划的转换。

## 2026-07-26 补充调查

在 `blender-manual-5.2` 上做了 Image Texture 控制组：

1. 自然问句约 209 ms，首条错误落到 `RGB to BW`，没有触发补抓。
2. 换一种自然表达约 172 ms，首条变成 `Shader To RGB`，仍没有触发补抓。
3. 精确输入 `Image Texture Node` 约 1676 ms，触发 1 页补抓并成功，正确页面
   随即排到第一。

固定版本官方页
`https://docs.blender.org/manual/en/5.2/render/shader_nodes/textures/image.html`
可正常访问。页面可抓、官方标题也可定位，缺口只出现在自然问句没有生成足够强的
安全候选；应由 AI/Skill 把问题先落成 `Image Texture Node`，不需要重新打开
BUG-008 或放宽核心补抓规则。

### 从 BUG-002 重新归类的证据

三套学习流共 36 个自然问题，正确主题首位命中 13/36。这个数字衡量的是“未经 AI
查询规划，直接把用户原话交给检索层”的效果，不能直接解释为 23 次核心排序故障。

典型控制组：

- `How does reference initialization work in C++20?` 把正确页面排在第 4、5 条；
  AI 落成官方标题 `Reference initialization` 后，正确页面立即首位命中。
- “限定当前版本”与“从旧版本迁移”可能需要相反的版本取舍，不能靠一个固定排序
  权重解决；AI 应先识别问题意图，再选择并综合对应页面。
- `std::optional`、Blender 旧节点迁移等自然问题都应先拆成明确实体和版本条件，
  必要时分项查询，而不是要求 DocAtlas 从整句中完成推理。

这些证据现用于验证 AI/Skill 的术语提炼、版本意图识别和自动重试，不再作为
BUG-002 的未解决核心证据。

# 验证思路

用一组真实自然问句验证 AI/Skill 能否：

1. 生成少量可审计的官方术语；
2. 用这些术语查询并取得正确页面；
3. 在弱相关时换术语重试；
4. 最终按原问题组织答案并保留官方来源。

同时确认 DocAtlas 直接收到纯概念问句时仍不会触发宽泛补抓。

## 验证

完成后记录原问题、AI 生成的术语、每次 DocAtlas 查询及最终采用的来源。

## 解决记录

未解决时留空。完成或关闭后记录最终决策、实现范围和验证结论。

## 外部关联

- GitHub Issue：
- 实现 PR：
