---
id: BUG-007
title: "`sample-per-category` 会把分类缺额补抓到其他分类"
type: bug
status: open
lifecycle: unresolved
priority: medium
area: crawl
labels: [crawl, sampling, dataset, validation]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-006]
---

# 问题

`crawl --sample-per-category N` 的帮助文字和建库流程都表示“每类只抓取 N 页”，
但当某个分类不足 N 页时，主循环仍按 `N × 分类数` 作为全局目标，并从其他分类
继续补足总数，导致部分分类超过 N。

## 环境

- 数据集：`cppreference-2026-07-26` 临时小样
- 版本：2026-07-26 快照
- 入口：CLI

## 复现

清单分类计数：

```text
language 228
standard_library 6719
compiler_support 9
```

运行：

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas crawl --skip-discovery --sample-per-category 20 --workers 4
```

## 实际结果

- 命令报告目标 60 页并成功抓取 60 页。
- 最终分布为：

```text
language 31
standard_library 20
compiler_support 9
```

`language` 超过请求的每类 20 页上限。对照 Blender 两个分类都至少有 20 页时，
同一参数得到正确的 20/20 分布。

## 期望结果

每个分类最多抓取 N 页；某分类不足 N 页时不应把缺额转移到其他分类。上述清单的
目标总数应为 `20 + 20 + 9 = 49`。

## 可能方向

抓取目标和停止条件可以依据各分类实际可选页数分别计算，而不是只使用
`sample_per_category * len(categories)` 的全局上限。具体实现应同时覆盖失败重试和
已抓页面存在时的续跑语义。

## 临时绕行

逐分类运行 `crawl --category <name> --max-pages N`，并在每次后核对分类计数。

## 调查记录

- 问题只在某个分类页面数小于样本上限时暴露。
- 临时 cppreference 数据集已按测试要求清理；没有保留适配器或数据。

## 验证

修复后用分类规模分别为 9、20、100 的清单请求每类 20 页，确认结果为
9/20/20，且重复运行不会继续扩大已达到上限的分类。

## 解决记录


## 外部关联

- GitHub Issue：
- 修复 PR：
