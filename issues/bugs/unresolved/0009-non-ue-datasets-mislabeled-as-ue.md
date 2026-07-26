---
id: BUG-009
title: "非 Unreal 数据集仍被标记为 UE 和 ue_version"
type: bug
status: open
lifecycle: unresolved
priority: medium
area: dataset
labels: [dataset, versioning, context, reports, multi-dataset]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [ENH-003, ENH-004]
---

# 问题

通用查询、上下文和报告层仍把版本写成 Unreal 专属的 `UE <version>` 和
`ue_version`。接入 cppreference 与 Blender 后，返回内容会把完全不同的产品误标为
UE，影响版本识别和下游结构化调用。

## 环境

- 数据集：`cppreference-2026-07-26`、`blender-manual-5.2`
- 版本：2026-07-26 快照、Blender 5.2
- 入口：CLI `ask --json`、`stats`

## 复现

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas ask "Principled BSDF" --token-budget 1600
python -m docatlas ask "Principled BSDF" --token-budget 1600 --json
python -m docatlas stats
```

## 实际结果

- Blender Shader 的 21/21 次 `ask`、Geometry 的 20/20 次 `ask` 都以
  `# UE 5.2 文档检索` 开头。
- cppreference 查询以 `# UE 2026-07-26 文档检索` 开头。
- JSON 使用 `"ue_version": "5.2"` 或 `"ue_version": "2026-07-26"`。
- 知识块 `context_prefix` 同样以 `UE 5.2` / `UE 2026-07-26` 开头。
- `stats` 和 inventory 报告也使用 `ue_version` 字段。

代码只读核对显示通用路径中仍有这些固定名称：

```text
docatlas/chunking.py   f"UE {VERSION}"
docatlas/context.py    "ue_version": VERSION
docatlas/context.py    f"# UE {pack['ue_version']} 文档检索"
docatlas/reports.py    "ue_version": VERSION
```

## 期望结果

展示文本使用数据集的产品名/名称和版本；结构化字段使用领域中立的版本字段，或在
保持兼容时同时提供明确的通用字段。Unreal 数据集仍可显示 UE，但不应强加给其他
来源。

## 可能方向

展示名称可以来自 `Dataset.name` / `product` / `version`。结构化字段的迁移需要考虑
CLI、MCP 和现有调用方兼容性，可在弃用期保留旧字段并增加通用字段。

## 临时绕行

调用方必须结合 `python -m docatlas paths` 的 `dataset` 字段解释版本，忽略输出中的
`UE` 和 `ue_version` 命名。

## 调查记录

- 错误在两个非 Unreal 来源、三个独立学习方向中全部复现。
- 页面正文和来源 URL 本身仍指向正确的 cppreference / Blender 5.2 页面。
- 临时数据集已清理，本议题只保留实测证据。

## 验证

至少使用一个 Unreal 和两个非 Unreal 数据集核对 Markdown 标题、JSON、
context prefix、stats 与 inventory 字段。

## 解决记录


## 外部关联

- GitHub Issue：
- 修复 PR：
