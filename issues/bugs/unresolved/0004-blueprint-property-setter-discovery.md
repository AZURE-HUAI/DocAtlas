---
id: BUG-004
title: "蓝图属性 Setter 难以按节点显示名检索和关联"
type: bug
status: open
lifecycle: unresolved
priority: medium
area: unreal-knowledge
labels: [search, related, unreal, blueprint, aliases]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-005, ENH-003]
---

# 问题

用户按蓝图界面中的 Setter 名称搜索时，难以发现对应的
`BlueprintReadWrite` C++ 属性，关系层也无法表达二者的生成关系。

## 复现

```powershell
python -m docatlas search "SetTargetArmLength" --limit 20
python -m docatlas search "TargetArmLength" --limit 20
python -m docatlas related "TargetArmLength"
python -m docatlas related "Set Target Arm Length"
```

## 实际结果

- `SetTargetArmLength` 没有搜索结果。
- `TargetArmLength` 只能从 C++ 教程中找到使用示例。
- 两条 `related` 查询分别约 286 毫秒和 283 毫秒返回 `[]`。
- 当前无法表达 `BlueprintReadWrite` 属性与自动生成的蓝图 Getter/Setter
  之间的关系。

Epic 官方 API 将 `USpringArmComponent::TargetArmLength` 标记为
`BlueprintReadWrite`，因此它可以在蓝图中以属性 Setter 的形式使用。

## 期望结果

- 用户使用蓝图界面名称、紧凑代码名或原始属性名时，都有合理路径发现同一属性。
- 没有独立节点页面时，至少返回对应属性的官方 API 定义，并说明蓝图节点来自
  可读写属性。
- 查询结果能够解释 C++ 属性与蓝图自动访问器之间的联系及其证据。

## 可能方向

一种参考方向是根据官方 `BlueprintReadWrite` 元数据补充检索别名或生成关系，
关系可以类似 `blueprint_property_accessor`。也可以选择不创建虚拟蓝图实体，
而是在属性结果中动态解释访问器。

应先确认官方文档实际提供了哪些元数据和独立页面，再决定使用实体、别名、关系
还是查询时展开；关系名称和具体实现均未确定。

## 调查记录

不确定官方是否为此类自动生成 Setter 提供独立页面。缺口可能位于实体模型、
自动别名或 Unreal 知识包，而不一定只是搜索排序。
