---
id: BUG-014
title: "精确 URL 的 fragment 没有限定到对应小节"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: retrieval
labels: [ask, exact-url, fragment, retrieval, roblox]
reported_at: 2026-07-27
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-008, BUG-010]
---

# 问题

`docatlas_ask` 已能把精确官方 URL 限定到被指名页面，但 URL 带 fragment 时没有继续
限定到对应小节。目标小节正文已经在本地库中，最终回答仍只返回页面概览和上级参数块，
导致用户给出最精确的官方地址后反而拿不到该地址指向的答案。

## 复现

数据集：`roblox-creator-2026-07-26`

```text
docatlas_ask(
  dataset_id="roblox-creator-2026-07-26",
  category="studio_guides",
  query="https://create.roblox.com/docs/ui/on-screen-containers#screen-insets",
  token_budget=3000,
  no_fetch=true,
  format="json"
)
```

原查询复查和本地规范化 fragment 对照均稳定复现：

```text
https://create.roblox.com/docs/ui/on-screen-containers#screen-insets
https://create.roblox.com/docs/en-us/ui/on-screen-containers#screeninsets
```

## 实际结果

- `status=ok`
- `estimated_tokens=51`
- 只返回 K2725 页面概览和 K2728 `Container properties`
- 没有返回同页已经存在的 K2729/K2730 `Screen insets`

同一数据集改用精确官方术语：

```text
docatlas_search(query="ScreenInsets CoreUISafeInsets", ...)
docatlas_ask(query="ScreenInsets CoreUISafeInsets", ...)
```

会立即返回 K2729/K2730，`estimated_tokens=469`。这证明目标正文已经抓取和解析，
不是 `pages_not_fetched`、来源缺失、网络失败或 token 预算不足。

Roblox 当前官方 HTML 中，`Screen insets` 小节链接的实际 `href` 是
`#screen-insets`；该小节正文明确包含 `ScreenInsets` 与
`CoreUISafeInsets`。在线来源：

```text
https://create.roblox.com/docs/ui/on-screen-containers#screen-insets
```

## 期望结果

精确 URL 带有效 fragment 时，最终回答应优先返回该 fragment 对应的小节块，并保持
结果只来自被指名页面。若来源 fragment 与本地块 fragment 的规范化形式不同，应由
来源适配或小节匹配层确定性映射；找不到对应小节时应明确诊断，而不是静默退回页面
概览。

## 验证

修复后用新 MCP 进程重复上述两个 URL 查询：

1. 首位知识块是 `Screen insets`；
2. 返回正文包含 `ScreenInsets` 和 `CoreUISafeInsets`；
3. 所有知识块都来自被指名页面；
4. `estimated_tokens` 不再只有概览的 51 tokens；
5. 不带 fragment 的同页 URL 仍保持现有页面级精确合同；
6. `BUG-008` 的精确页面与 `BUG-010` 的来源锚点回归继续通过。

## 解决记录

`BUG-008` 只做到"地址限定到页面"，`#小节` 在 `normalize_link_target` 里连同
query 一起被 `urlsplit` 丢掉了——地址里最细的那一层指认从来没被读过。

### 两条真实数据决定了做法

**一、两边锚点写法不同。** 官方 href 是 `#screen-insets`，库里存的是
`#screeninsets`（`text.heading_anchor` 把标题拍平生成的）。两边都按"只留小写
字母和数字"拍平就对得上——纯字符串规则，不需要任何站点知识，四个库通用。

**二、只按锚点匹配会漏掉半节内容。** 这是查库才看出来的：

```text
K2729  anchor=screeninsets       heading_path=… > Container properties > Screen insets
K2730  anchor=coreuisafeinsets   heading_path=… > Container properties > Screen insets
```

`CoreUISafeInsets` 有**自己的**锚点，却挂在 `Screen insets` 底下。只认锚点就
只给 K2729，把用户点进这一节真正要读的 K2730 留在外面。所以定成两步：

- **锚点认出是哪一节**（`_chunk_anchor` == fragment）
- **`heading_path` 划定这一节到哪儿为止**（等于它、或以它开头）

### 改动

- `ondemand.target_fragment()`：从查询里的地址取出 fragment 并拍平。
- `context._fragment_section()`：上面那两步，返回这一节的块和小节名。
- `build_context_pack`：指名到小节时整页读回来自己挑（`search.page_chunks`），
  这一节排前面、本页其余内容跟在后面当上下文。全文检索在这一步已经没有意义
  ——用户把话说到最细了，再让它去撞只会把无关小节排前面。
- `fragment_intent` 原样回给调用方，走 `version_intent` 同一个道理：**限定
  条件用了就得说出来**。认不出那一节时明确写"没有对应的小节，下面是整页"，
  不静默退回页面概览——静默退回时用户看到的是个像模像样的答案，完全不知道
  自己指的那一节根本没被用上。

### 验证（新 MCP 进程，`no_fetch=true`，逐条对应本议题"验证"一节）

| 判据 | 改前 | 改后 |
|---|---|---|
| 1 首位知识块 | K2725 页面概览 | **K2729 `Screen insets`** |
| 2 正文含 `ScreenInsets` / `CoreUISafeInsets` | 都没有 | **都有**（K2729 + K2730） |
| 3 全部来自被指名页面 | 是 | 是（来源页面数 = 1） |
| 4 `estimated_tokens` | 51 | **469** |
| 5 同页不带 fragment | 页面级 | **不变**：51 tokens、无 `fragment_intent` |
| 6 BUG-008 / BUG-010 回归 | —— | 全过 |

官方 href 形式和本地规范化形式两条 URL 结果逐条相同：

```text
https://create.roblox.com/docs/ui/on-screen-containers#screen-insets
https://create.roblox.com/docs/en-us/ui/on-screen-containers#screeninsets
  → 都是 matched=true, section="Screen insets", tokens=469
```

认不出小节时（`#no-such-section`）：`matched=false`，如实说明并给整页内容。

### 回归

单元测试 247 → **253 全过**，新增 `UrlFragmentTests` 六条（锚点写法映射、
小节限定、子块跟随、认不出如实报告、匹配结果回传、无 fragment 的反向控制组）。
四个数据集 `validate` 的 inventory 7 项、content 16 项仍全 pass。

## 外部关联

- GitHub Issue：
- 修复 PR：
