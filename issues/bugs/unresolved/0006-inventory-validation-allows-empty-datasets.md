---
id: BUG-006
title: "inventory 验收会把空数据集和空分类判为通过"
type: bug
status: in_progress
lifecycle: unresolved
priority: high
area: validation
labels: [validate, inventory, dataset, diagnostics]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-007]
---

# 问题

`validate --phase inventory` 只统计不合格行和失败清单，没有确认数据集至少包含一份
成功清单、一个页面，也没有确认配置声明的每个必需分类非空。因此全新空数据库会
返回 `status: pass` 和退出码 0。

## 环境

- 数据集：使用已有配置创建的全新空数据根
- 版本：任意
- 入口：CLI

## 复现

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
$env:DOCATLAS_HOME='C:\Users\HUAI\Desktop\DocAtlas\data\docatlas-stress-empty-validate'
python -m docatlas validate --phase inventory
```

原测试配置和隔离数据根已按测试要求清理。相同行为也可用任一现有数据集配置和新的
空 `DOCATLAS_HOME` 复现。

## 实际结果

2026-07-26 实测耗时约 0.5 秒，退出码 0：

```json
{
  "phase": "inventory",
  "status": "pass",
  "checks": [
    {"name": "sitemaps_complete", "status": "pass", "failures": 0},
    {"name": "page_inventory_metadata", "status": "pass", "failures": 0},
    {"name": "unique_page_paths", "status": "pass", "failures": 0}
  ]
}
```

紧接着 `python -m docatlas paths` 显示数据库刚刚建立，但页面清单尚未执行。

同一批建库测试还出现过较弱的变体：cppreference 首次分类规则错误时，
`compiler_support` 为 0，页面总数 6,956，inventory 验收仍然通过。修正规则后该
分类实际有 9 页。

## 期望结果

- 没有成功清单或页面时，inventory 阶段应失败，并说明尚未执行或完成发现。
- 配置声明的必需分类为空时，应失败或至少产生明确、可配置的验收诊断。
- 调用方不能把“没有不合格行”误解为“存在且完整的数据”。

## 可能方向

可考虑分别检查成功清单数、页面总数和每个配置分类的页面数。分类是否允许为空可以
由数据集配置表达，避免把确实可选的分类一律判错。

## 临时绕行

在 `validate` 之外人工核对 discovery 输出中的 `page_count`、`sitemap_count` 和
每个分类计数，不能只看退出码。

## 调查记录

- 该行为在一次外键导致 discovery 中断后的空库上首次发现。
- 随后换全新隔离数据根独立复现，排除了失败事务残留的影响。
- 测试结束前已删除隔离数据根，没有保留临时数据集或产品代码改动。

## 验证

`InventoryValidationTests` 覆盖议题要求的三个场景，全部离线可重复：

| 场景 | 期望 | 实测 |
|---|---|---|
| 全新空库 | fail | `inventory_not_empty` = fail，整体 `status: fail` |
| 某个声明分类为 0 页 | fail | `declared_categories_have_pages` = fail，`requirement` 里点名是哪一类 |
| 正常非空清单 | pass | 整体 `status: pass` |
| 分类写进 `optional_categories` | pass | 该检查恢复 pass |

真实库对照（`epic-ue-5.8`，6 个分类全部非空）：

```powershell
python -m docatlas validate --phase inventory
# inventory_feeds_complete / inventory_not_empty /
# declared_categories_have_pages / page_inventory_metadata /
# unique_page_paths 全部 pass
```

回归测试：128 用例全过。

## 解决记录

**根因**：所有检查都是"数不合格的行"。空库里一行都没有，于是一行都不不合格，
`failures: 0` → `status: pass` → 退出码 0。**"没有不合格的行"和"有合格的数据"
是两件事**，原来的合同只表达了前者。

**改动**（`docatlas/validate.py`）新增两项，都在 `inventory` 阶段：

- `inventory_not_empty`：成功清单入口数 > 0 **且** 页面数 > 0，
  否则 fail，并在 `requirement` 里写出当前的两个数字和最可能的原因。
- `declared_categories_have_pages`：数据集声明的每个分类都要枚举到页面。
  确实可能为空的分类写进新增的 `optional_categories`（`docatlas/dataset.py`），
  **由配置显式声明，而不是把检查放宽**。

同时把 `sitemaps_complete` 改名为 `inventory_feeds_complete`，并在
`requirement` 里带上总数和成功数——配合 ENH-004，清单入口已经不一定是 sitemap。

**为什么不做成警告**：议题里那个较弱的变体（cppreference 分类规则写错，
`compiler_support` 为 0 而 6,956 页整体通过）说明这类错误只会以"少了一整类
文档"的形式表现出来，用户不会自己发现。它必须是红的。

## 外部关联

- GitHub Issue：
- 修复 PR：
