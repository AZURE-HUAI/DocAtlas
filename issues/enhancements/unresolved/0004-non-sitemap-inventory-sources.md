---
id: ENH-004
title: "来源适配器支持非 sitemap 的页面清单"
type: enhancement
status: in_progress
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

`InventoryFeedHookTests` 用一个只会分页的假适配器（两个入口、三条页面、
条目自带分类）验证：

```python
discover.SOURCE = FakeSource          # 只实现 inventory_feeds / read_feed
discover.discover_inventory(connection, workers=2, refresh=False)   # → 3
```

- 分类计数 `{'guides': 2, 'cpp_api': 1}`——**条目自己声明的分类赢过入口分类**，
  一个入口列出多个分类能表达得出来。
- 落库字段与 sitemap 路径完全一致：`doc_version` / `locale` / `route_depth` /
  `sitemap_url` 都非空，`inventory` 阶段的数据合同一项都没放松。
- 入口抛异常时（`read_feed` 抛 `TimeoutError`）：两个入口都记为 `failed`，
  `validate --phase inventory` 的 `inventory_feeds_complete` = fail，
  不会被静默吞掉。

配合 BUG-006，空清单在自定义来源上同样会被 `inventory_not_empty` 拦下。

回归测试：128 用例全过；真实 sitemap 路径（`epic-ue-5.8`）不受影响。

## 解决记录

**根因**：`discover.py` 把"清单是一份 XML sitemap"当成了核心事实，
而不是当成一种实现。所以 cppreference 的分页 API 和 Blender 的
`searchindex.js` 只能靠临时改核心才跑得通。

**改动**：把清单来源拆成两半，**默认实现就是 sitemap**：

```python
inventory_feeds(dataset)  -> [(清单入口地址, 分类或 None)]
read_feed(dataset, url)   -> [(分类或 None, 页面地址)]
```

适配器实现了就用它的，没实现就用内置的 sitemap 版本
（`_sitemap_feeds` / `_read_sitemap`）。**只有一条代码路径**，
不是"sitemap 分支 + 自定义分支"——后者迟早会分岔。

回答议题的"待讨论问题"：

- **翻页 / 重试 / 限流归谁**：归适配器。它们是"这个站怎么列页"的一部分，
  核心无从知道 continuation token 长什么样。并发、写库、进度、失败诊断归核心。
- **一个入口对应多个分类**：条目可以自带分类，优先于入口分类。
- **分类为空是否允许**：由 BUG-006 的 `declared_categories_have_pages` +
  `optional_categories` 统一回答，自定义来源不例外。
- **验收命名**：`sitemaps_complete` 已改名为 `inventory_feeds_complete`，
  状态语义不变。`sitemaps` 表名保留（避免无谓的 schema 变更），
  在 `discover.py` 顶部注明它存的是"清单入口"。

**非目标照旧**：没有内置 cppreference / Blender 适配器，没有写通用爬虫，
建库纪律（先 discovery-only → inventory validate → 每类小样）一条没松。
`WORKFLOWS.md` 的流程 B 已补上这两个函数的说明。

## 外部关联

- GitHub Issue：
- 实现 PR：
