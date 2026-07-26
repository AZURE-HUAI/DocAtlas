---
id: ENH-009
title: "用陌生领域验收分层架构：Roblox Creator Hub"
type: enhancement
status: resolved
lifecycle: resolved
priority: high
area: sources
labels: [multi-dataset, contract, source-adapter, roblox, acceptance]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [ENH-006, BUG-013]
---

# 背景

三个数据集都是先有代码、后有数据集，谁也说不清"通用"到底成立到什么程度——
写核心的时候就知道要接哪几个站，边界很容易在不知不觉中被磨成"刚好够用"。

所以拿一个**没参与过设计**的领域来验：Roblox Creator Hub。它和现有三个都不像
——不是 sitemap，不是 Sphinx，不是 MediaWiki，而是为机器阅读准备的
`llms.txt` 索引 + 单页 Markdown；没有产品版本号；而且有两套**必须不能混**的
API 体系。

## 目标

判据只有一条：接第五个领域时，只需要新增来源适配器、数据集配置和可选知识包，
不需要改 MCP、查询核心、关系核心和数据库结构。

Roblox 是这一轮的样本，不是新的硬编码目标。

## 非目标

- 不下载全站正文（`/docs/llms-full.txt` 明确不用）。
- 第一轮不写 Roblox 领域知识包——要先验证没有知识包时通用能力是否够用。

## 验证

### 最终判据：接入到底动了哪些文件

```text
新增  datasets/roblox-creator-2026-07-26.toml
新增  docatlas/sources/roblox_creator.py
新增  tests/test_source_adapters.py 里的 RobloxCreatorAdapterTests
```

**没有**改动 MCP 工具与协议、通用查询核心、通用关系核心、数据库结构，
也没有碰另外三个适配器和 Unreal 知识包。`git diff --stat` 只有测试文件一行。

### 来源侦查（2026-07-26 实测）

| 项目 | 事实 |
|---|---|
| 索引更新时间 | `<!-- Last updated: 2026-07-24T23:56:32Z -->`（三份索引各自声明） |
| 语言 | `en-us`，写在地址里 |
| 页面数量 | 全站索引 2,308 条链接；Engine API 1,260 条；Open Cloud 108 条 |
| 单页 Markdown | 页面地址加 `.md`，开头是 YAML frontmatter |
| 产品版本 | **没有**，文档持续更新 → 用快照日期 `roblox-creator-2026-07-26` |
| 失效页面 | 干净的 404，无重定向绕圈 |
| 限流 | 3 req/s 抓 88 页，0 次失败 |
| Engine / Open Cloud 边界 | 地址上就是分开的，官方索引开篇专门警告不要混 |

### 清单与小样

```text
枚举 2,371 页    engine_api 1,247 / studio_guides 903 / open_cloud 159 / luau 42
正文  88 页      四个分类各 20 页小样 + 7 页按需补抓的链接目标 + deprecated 清单
```

### 关系（阶段 A：没有知识包）

```text
official_reference / official_link / origin=core   8 条
missing_targets 0    uncovered_areas 0
增量（link_new_pages）与全量（rebuild）结果逐条一致
```

没有领域知识包，通用的官方链接关系照常建得出来——这是本轮要验的那一条。

### 真实问题（每一条都先由 AI 转成官方术语再交给 MCP）

| 问题 | 结果 |
|---|---|
| Studio 安装与测试 | `Roblox Studio setup`（studio_guides） |
| Luau 语法 | 命中 `scripting/events/*` 的 metatable 段落 |
| Engine 类的继承（`category=engine_api`） | `Instance`（engine_api） |
| 外部服务器调 API 的限流（`category=open_cloud`） | `Rate limits and throttling`（open_cloud） |
| **易混淆**：同一个词 "data store" 两侧都有 | `engine_api` 只回 Engine 页，`open_cloud` 只回 Open Cloud 页，互不越界 |
| deprecated / 迁移 | `Deprecated Roblox Engine APIs`（577 条废弃成员的官方清单） |
| 跨页面关系 | `related "Data stores"` → `Data and memory stores`，带 `official_link` 出处 |
| 中文口语提问 | 明确回"库是 en-us、查询是汉字，必然对不上"，并要求换官方写法；换成英文后命中 |
| 冷查询按需补抓 | `ask "DataStoreService"` 当场抓 2 页并作答 |
| 不存在的目标 | `entity_not_found`，且如实说明清单里也没有 |

### 四库同时在线

一个 MCP 连接、连续 8 次调用轮着切四个库，`dataset.dataset_id` 逐条与请求
一致，**没有串库**。同一个词在四个库里各自命中本领域的内容：

```text
"Instance" → UE: Material Instance Editor UI | C++: alignof operator
             Blender: Instance on Points Node | Roblox: Instance（engine_api）
```

### 回归

- 单元测试 225 → **231 全过**。
- 四个数据集各自 `validate --phase inventory` 6 项、`--phase content` 15 项，
  **全 pass**。
- UE / cppreference / Blender 三个库的页/块/实体/关系计数与接入 Roblox 之前
  逐条相同。

## 解决记录

**通用合同验收通过。** 接入过程中一次都没有需要改通用层——三处真实缺陷全部
在适配器内部解决，而且每一处都是**站点特有**的知识，本来就该在那里。

### 缺陷一：正文链接省掉了语言段

索引写 `/docs/en-us/studio/setup`，而正文里链的是 `/docs/studio/setup`。
两个地址都返回同一页，页面自己在 frontmatter 里写明了正规写法
（`url: /docs/en-us/studio/setup`）。不收敛的话，链接永远对不上清单——
`missing_targets` 会一直显示"清单外目标"，而它们其实都在清单里。

### 缺陷二：同一个 Engine 类在索引里有两种写法

`/docs/reference/engine/classes/DataStore` 和
`/docs/en-us/reference/engine/classes/DataStore` 同时出现在官方索引里。
不收敛的后果不只是重复：带语言段的那一份会因为路径形状被判成
`studio_guides`。实测**枚举总数从 3,630 降到 2,371——1,260 页是重复**，
占三分之一，而且全部分类错误。

两条合并成一个规则：路径先脱掉语言段，若剩下的部分属于自动生成的 API 参考
（`reference/engine/`、`cloud/reference/`）就保持不带，否则一律补回去。
双向收敛，不是单向补全。

### 缺陷三：少一个斜杠，Engine 服务被判成 Open Cloud

`en-us/cloud` 这个前缀会把 `en-us/cloud-services/` 一起吃掉。而
`cloud-services` 下面是 **DataStoreService、MemoryStoreService、
HttpService**——实验内部用 `game:GetService()` 拿的 Engine 服务，
恰恰不是外部 HTTP 的 Open Cloud。这正是官方索引开篇警告的那种混淆，
一个斜杠之差。

这三条也说明了为什么分类必须按地址而不按关键词：`Assets`、`Analytics`、
`data store` 这些词两套体系里都有，靠词去认必然混。

### 一处刻意的取舍

官方把 deprecated 清单列在索引开头的"入口一览"里，那一段的地址是**裸写**的，
不是 Markdown 链接，所以第一版解析器读不到它。补的规则是"裸地址只收 `.md`"
——`.md` 是页面格式，`.txt` 是索引本身（包括那份明确不该下载的
`llms-full.txt`）。判据来自站点自己的格式约定，不是把那个地址写死。

### 阶段 B（Roblox 知识包）没有做

第一轮的真实问题里，没有一条是通用能力答不上、非要领域规则不可的。继承链、
服务归属、deprecated↔替代项这些关系确实存在（`inherits:` 就写在 frontmatter
里），但那属于"能做"而不是"现在需要"。等真有问题被卡住再做，那时才知道
规则该长什么样。

数据库保留，供下一轮复现。

## 外部关联

- GitHub Issue：
- 实现 PR：
