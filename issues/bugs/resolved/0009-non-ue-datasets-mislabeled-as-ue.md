---
id: BUG-009
title: "非 Unreal 数据集仍被标记为 UE 和 ue_version"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: dataset
labels: [dataset, versioning, context, reports, multi-dataset]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
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

```powershell
python -m docatlas ask "Set Field Of View" --token-budget 1500
```

```text
# 文档检索：Set Field Of View

来源：《Unreal Engine 5.8 官方文档》（unreal-engine 5.8）。预算 1,500 tokens，…
```

标题不再以 `# UE <版本> 文档检索` 开头，产品名和版本都来自数据集配置。

结构化字段：

```powershell
python -m docatlas ask "…" --json   # dataset / product / version，无 ue_version
python -m docatlas stats            # product / version
python -m docatlas inventory        # product / version
```

数据库列：`pages.ue_version` → `pages.doc_version`。在 199,883 页的真实库上
实测整套迁移（含 slug 重算）**2.60 秒**，`ALTER TABLE … RENAME COLUMN`
只改元数据、不重写数据。

回归测试：`NeutralNamingTests` 三个用例——扫描 `docatlas/**.py` 确认再没有
`f"UE {` 或 `"ue_version"`（只放行 `rename_column_if_present` 那一行迁移）、
上下文包分别给出 `product` 与 `version`、知识块 `context_prefix` 以数据集的
产品名开头。128 用例全过。

## 解决记录

**根因**：通用路径里写死了一个具体产品的叫法。这与"路径不许写死"是同一类错误，
只是换了个字段。

**改动**：

| 位置 | 原来 | 现在 |
|---|---|---|
| `chunking.py` 的 `context_prefix` | `f"UE {VERSION}"` | `f"{DATASET.product} {VERSION}"` |
| `context.py` 上下文包 | `"ue_version"` | `dataset` / `product` / `version` |
| `context.py` Markdown 标题 | `# UE <版本> 文档检索：…` | `# 文档检索：…` + 一行"来源：《数据集名》（产品 版本）" |
| `reports.py` stats / inventory | `"ue_version"` | `product` / `version` |
| `pages` 表 | `ue_version` | `doc_version` |
| `metadata` 表 | `ue_version` | `doc_version` |

`context_prefix` 写在每一个知识块里、也进全文索引，所以
`constants.CHUNKER_VERSION` 从 `v3` 升到 `v4`，用 `reprocess` 就地重算
（只读本地原文，不联网，可断点续传）。

**关于改列名**：改名前的那份架构评估（`ARCHITECTURE_REVIEW.md`，内部文档，
未随仓库发布）曾记下"不要改 `ue_version` 的名字，
避免为了整洁而冒险"。本议题提供了新证据——在 cppreference 和 Blender 上它给出
的是**错误信息**，不只是不整洁——因此该结论已被推翻，推翻的理由和实测迁移耗时
一并写回了 `ARCHITECTURE_REVIEW.md`，不留"两个地方说法不一致"的坑。

**没有保留旧字段名。** 弃用期意味着同一份数据有两个名字，正是本轮要清掉的债；
这个项目目前没有外部结构化调用方，一次换干净成本最低。

## 外部关联

- GitHub Issue：
- 修复 PR：
