---
id: BUG-005
title: "`related` 用空数组同时表示多种失败状态"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: related
labels: [related, diagnostics, cli, mcp, api-contract]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
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

## 调查记录

### 2026-07-26：cppreference 与 Blender 小样

新增跨数据集证据：

```powershell
python -m docatlas related "std::integral"
python -m docatlas related "std::basic_string_view"
python -m docatlas related "Mesh to Curve Node"
python -m docatlas related "Curve to Mesh Node"
python -m docatlas related "Mesh to Points Node"
python -m docatlas related "Points to Vertices Node"
```

以上查询均在约 0.16–0.18 秒、退出码 0 后只返回 `[]`。在线对应页面存在；Blender
`stats` 显示清单完整但目标正文仍为 pending，因此这些空数组至少混合了“实体尚未
抓取”和“名称没有命中实体”。

正向对照：Blender 的 Capture Attribute、Store Named Attribute、Named Attribute
已抓取后，`related` 会返回实体、正反向 `official_link` 和 `confidence: 1.0`。
另有已抓实体会返回实体对象加空 `relations`。这进一步确认调用方需要区分不同空
状态，而不是把所有情况压成裸数组。

## 验证

```powershell
python -m docatlas related "Set Field Of View"    # 清单里有、未抓
python -m docatlas related "Nanite Virtualized Geometry"  # 实体在、无关系
python -m docatlas related "zzzznotarealthing"    # 哪儿都没有
```

三种情况现在给三种 `status`，退出码也不同（`ok` → 0，其余 → 1）：

```json
{"subject": "Set Field Of View", "status": "entity_not_found",
 "entities": [], "next_steps": ["本地没有正文，但全站清单里有 5 个页面对得上：", "…"],
 "lookup": {"pending_pages": [{"path": "…/BlueprintAPI/Camera/SetFieldOfView",
                               "matched_by": "exact_slug"}], "crawled_pages": []}}
```

正向对照未受影响：已覆盖实体仍返回 `status: ok` 与正反向关系、`evidence_kind`、
`confidence`。

回归测试：`RelatedContractTests` 3 个用例分别钉死三种状态，
`InventoryCandidateTests.test_describe_lookup_gives_a_different_answer_for_each_state`
钉死"三种没有必须给三种下一步"。

## 解决记录

**根因**：返回类型本身就丢信息。裸 `[]` 没有位置放"为什么空"。

**改动**：`related` 的返回从数组换成对象：

| 字段 | 含义 |
|---|---|
| `status` | `ok` / `entity_found_but_no_relations` / `entity_not_found` |
| `entities` | 原来的数组，语义不变 |
| `next_steps` | 可直接执行的下一步（字符串数组） |
| `lookup` | 非 `ok` 时给出清单诊断：`pending_pages` / `crawled_pages` |

实现只有一份：`context.related_payload()`。CLI 的 `related` 和 MCP 的
`docatlas_related` 都调它，不存在两边语义不一致的可能。

**没有保留旧的数组格式。** 议题的"可能方向"提过"保持数组兼容 + 额外字段"，
但那会同时存在两套契约，正是本轮要清掉的那类债。这个项目还没有外部调用方，
一次换干净比长期维护两种形状便宜得多；`SKILL.md` 里同步写了状态表，
AI 侧的读法也一起更新了。

**顺带修掉的**：实体查找原来用 `WHERE e.normalized_name=? OR a.normalized_alias=?`
跨两张表 OR，同样会让 SQLite 放弃索引，已改为 `UNION`。

## 外部关联

- GitHub Issue：
- 修复 PR：
