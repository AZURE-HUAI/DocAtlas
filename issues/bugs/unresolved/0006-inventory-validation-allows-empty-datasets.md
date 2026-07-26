---
id: BUG-006
title: "inventory 验收会把空数据集和空分类判为通过"
type: bug
status: open
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

修复后至少覆盖：完全空库、某个必需分类为空、正常非空清单三个场景。

## 解决记录


## 外部关联

- GitHub Issue：
- 修复 PR：
