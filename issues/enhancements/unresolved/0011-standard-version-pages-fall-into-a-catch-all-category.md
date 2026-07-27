---
id: ENH-011
title: "横跨语言与标准库的页面被兜底分类吞掉（cppreference 标准版本总览页）"
type: enhancement
status: discussion
lifecycle: unresolved
priority: low
area: adapters
labels: [adapters, categories, cppreference]
reported_at: 2026-07-28
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-022]
---

# 目标

用户反馈：C++20 页被归为「C++ Standard Library」。

**这不是 Bug。** 那一页本来就横跨语言特性和标准库两边（`New language features`
和 `New library features` 各占一节），任何单选分类都不可能完全对。归到标准库
不算错答案，只是不够准。记在这里是因为成因清楚、改法也清楚。

## 现状

`docatlas/sources/cppreference.py` 的 `_category_for_title()` 是三选一，第三项
是兜底：

```python
if normalized.startswith("cpp/compiler support"):
    return "compiler_support"
if normalized.startswith("cpp/language"):
    return "language"
return "standard_library"          # ← 其余全归这里
```

于是 `standard_library` 的真实含义是"`/cpp/` 底下除语言和编译器支持之外的一切"，
标准版本总览页（`/cpp/11`、`/cpp/14`、`/cpp/17`、`/cpp/20`、`/cpp/23`、`/cpp/26`）
就落在这个兜底里。

影响只在标签和过滤上：AI 看到的分类名不准；想单独过滤"标准版本总览"这一类时
没有对应的 `category` 值。检索排序不受影响——那一页照样能被查到。

## 参考方向

给标准版本总览页一个自己的分类，例如 `standard_versions`。牵涉的地方：

- `sources/cppreference.py`：`_category_for_title()` 增加一条判断；
- `datasets/cppreference-2026-07-26.toml`：`[categories]`、`[category_labels]`、
  `[entity_types]`、`[category_priority]`、`[search.concept_category_bonus]`
  各补一行——这几张表都按分类名索引，漏一张就会在别处露出来（见 [[BUG-015]]）。

**改完必须重跑 `crawl --discovery-only --refresh-sitemaps`**：分类是清单阶段定的，
不重读清单入口，已有页面的分类不会变。

不建议做的：为"一页可以属于多个分类"改数据模型。收益只有标签更准一点，代价是
过滤、抽样、导出、`category_priority` 全都要跟着从"一对一"改成"一对多"。

## 验证

`/cpp/20`、`/cpp/23` 等页面的 `category` 落到新分类，`docatlas_list_datasets`
列得出这一类，按它过滤能且只能拿到标准版本总览页；`language` 和
`standard_library` 两类的页数不因此错位。

## 解决记录

未开始。

## 外部关联

- GitHub Issue：
- 实现 PR：
