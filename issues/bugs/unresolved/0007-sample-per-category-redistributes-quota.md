---
id: BUG-007
title: "`sample-per-category` 会把分类缺额补抓到其他分类"
type: bug
status: in_progress
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

`SampleQuotaTests` 用议题要求的规模（9 / 20 / 100）验证：

```python
quota = crawl.sample_quota(connection, 20)
# {'guides': 20, 'blueprint_api': 20, 'cpp_api': 9}      合计 49，不是 60
rows = crawl.select_page_batch(connection, batch_size=999,
                               refresh=False, sample_per_category=20)
Counter(row["category"] for row in rows)
# {'guides': 20, 'blueprint_api': 20, 'cpp_api': 9}
```

重复运行语义：某类已成功 20 页后再跑同一条命令 →
`sample_quota` 返回 `{}`，`select_page_batch` 返回 `[]`，不会继续扩大。

单分类运行：`sample_quota(connection, 5, category="cpp_api")` → `{'cpp_api': 5}`，
其它分类不受影响。

抓取主循环的日志现在会先把逐类目标打出来：

```text
抽样目标：guides 20、blueprint_api 20、cpp_api 9，合计 49
```

回归测试：128 用例全过。

## 解决记录

**根因**：目标数和停止条件用的是全局公式 `sample_per_category × 分类数`，
而 `select_page_batch` 每一轮又对每个分类各取 N 条。某类只有 9 页时，全局目标
仍是 60，主循环就继续从别的分类补足总数，于是 `language` 抓到了 31 页。

**改动**（`docatlas/crawl.py`）新增 `sample_quota()`，逐类计算"还差几页"：

```python
remaining = min(sample_per_category - 已成功页数, 可抓页数)
```

- 主循环的 `total_target` 改成 `sum(quota.values())`——上例是 49 而不是 60。
- `select_page_batch` 按每类的 `remaining` 取，而不是一律取 N。
- 已成功的页面计入该类额度，所以**重复运行不会继续扩大已达上限的分类**
  （议题验证要求的第二条）。

**顺带修掉的两处**：`select_page_batch` 原来在 `for category in CATEGORY_PATTERNS`
里覆盖了同名的函数参数（抽样分支因此静默忽略 `--category`）；
非抽样分支的排序里写死了 `guides/community_docs/blueprint_api/…` 这串 Unreal
分类名——核心不该认识它们，已改为读数据集的 `category_priority`。

## 外部关联

- GitHub Issue：
- 修复 PR：
