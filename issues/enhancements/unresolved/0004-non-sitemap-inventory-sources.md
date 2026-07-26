---
id: ENH-004
title: "来源适配器支持非 sitemap 的页面清单"
type: enhancement
status: discussion
lifecycle: unresolved
priority: medium
area: sources
labels: [sources, discovery, sitemap, extensibility]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [ENH-003, BUG-006]
---

# 背景

现有 discovery 核心固定读取 sitemap index，再下载 XML 子 sitemap。此次真实接入的
两个官方文档源都能提供完整清单，但入口不是该形态：

- cppreference：MediaWiki `allpages` API，需使用 continuation 分页；
- Blender Manual 5.2：Sphinx `searchindex.js`，其中 `docnames` 是完整页面名。

为了完成小样测试，主智能体临时在 `discover.py` 增加了约 110 行“适配器直接返回
`(category, location)`”的分支。测试结束后已按要求全部撤销。两个来源适配器本身无法
只靠现有配置和既定接口完成 discovery。

## 目标

来源适配器能以受控、可验收的方式提供非 sitemap 页面清单，同时继续复用通用的
路径规范化、分类、版本/语言元数据、数据库写入、状态和 inventory 验收。

## 可能方向

- 提供小型的可选 discovery hook，由适配器迭代返回页面位置和分类。
- 把 sitemap、分页 API、目录页和静态搜索索引视为几种清单 provider，共享同一
  规范化与落库路径。
- 保留失败来源、页数、分类计数和更新时间等诊断，不让自定义来源绕过数据合同。

以上只是测试中验证过的接口需求，不预先规定最终抽象或模块名称。

## 待讨论问题

- continuation、重试、限流和断点应由核心还是适配器负责到什么程度？
- 如何表达一个清单入口对应多个分类，以及分类为空是否允许？
- 非 XML 清单如何参与 `sitemaps_complete` 等现有验收命名和状态？
- 自定义 discovery 是否需要刷新与删除已下线页面的统一语义？

## 非目标

- 不要求为所有网页编写通用爬虫。
- 不要求本议题直接内置 cppreference 或 Blender 适配器。
- 不绕过先 discovery-only、inventory validate、每类小样的建库纪律。

## 验证思路

用三种来源做合同测试：现有 XML sitemap、分页 JSON API、Sphinx
`searchindex.js`。三者应得到稳定分类计数、失败诊断和可重复清单哈希；空清单应与
BUG-006 的验收要求保持一致。

## 验证


## 解决记录


## 外部关联

- GitHub Issue：
- 实现 PR：
