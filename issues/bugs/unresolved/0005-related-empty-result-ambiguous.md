---
id: BUG-005
title: "`related` 用空数组同时表示多种失败状态"
type: bug
status: open
lifecycle: unresolved
priority: medium
area: related
labels: [related, diagnostics, cli, mcp, api-contract]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-003, BUG-004, ENH-002]
---

# 问题

`related` 使用裸 `[]` 表示多种不同状态，调用方无法知道下一步该做什么。

## 复现

```powershell
python -m docatlas related "Set Field Of View"
python -m docatlas related "TargetArmLength"
python -m docatlas related "Set Target Arm Length"
```

## 实际结果

三条查询都只返回 `[]`，无法区分：

- 没有匹配到实体；
- 实体存在，但没有任何关系；
- 页面存在于冻结清单，但正文或实体尚未入库；
- 查询名称没有命中别名；
- 关系索引尚未生成或覆盖该类实体。

## 正向对照

```powershell
python -m docatlas related "Set Timer by Function Name"
```

约 283 毫秒返回两个实体及其正反向关系，包含 `official_link`、
`blueprint_cpp_api` 和 `targets_type`。这证明已覆盖实体的查询速度、证据和方向
输出正常，问题集中在空结果的诊断契约与覆盖缺口。

## 期望结果

调用方能够区分“没找到实体”和“找到实体但没有关系”，并获得足够信息决定是否
改写查询、补抓页面或重建关系。

下面的 JSON 只用于说明可能表达的语义，不规定字段名或最终返回结构：

```json
{
  "status": "entity_not_found",
  "entities": [],
  "suggestions": ["FieldOfView", "UCameraComponent::SetFieldOfView"]
}
```

实体存在但没有关系时，也可以考虑保留实体信息：

```json
{
  "status": "entity_found_but_no_relations",
  "entities": [{"name": "TargetArmLength"}]
}
```

## 可能方向

- 返回结构化状态。
- 保持现有数组兼容，同时通过诊断模式、退出码或额外字段补充原因。
- 在没有精确实体时给出相近候选或下一步建议。
- 评估 CLI、MCP 和内部 Python API 哪些语义需要一致，哪些可以按入口呈现。

具体返回格式应结合兼容性和调用方需求决定，示例中的状态名不是既定接口。
