---
id: BUG-008
title: "`ask` 无法按官方页面名补抓 route slug 不完全相同的 pending 页面"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: on-demand
labels: [ask, on-demand-fetch, inventory, ranking, multi-dataset]
reported_at: 2026-07-26
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-002, BUG-003, ENH-007]
---

# 问题

页面已在清单中但正文未抓取时，官方标题、限定符和带扩展名路径不能稳定触发补抓；
弱相关本地结果会掩盖真正目标页。

## 复现

典型失败包括 `Fields`、`std::from_chars`、`Wave Texture Node`、
`Set Field Of View` 和 `duration_cast milliseconds`。

## 验证

- 支持 `exact_slug`、`token_exact_slug`、`slug_contains` 和
  `path_covers_query` 四档安全候选。
- `.html` 等文档扩展名不再算页面名，限定符和常见分隔符可正确匹配。
- 精确目标未本地化时会补抓；不安全候选只报告 `candidates_too_weak`。
- 宽泛概念问句不会触发大范围抓取。

## 解决记录

本议题范围内的候选定位和诊断已完成并验证，状态为 `resolved`。自然语言问题由
AI/Skill 先转换成官方术语，见 `ENH-007`。

## 重新打开记录

### 2026-07-26：cppreference 精确术语与 URL 回归

高强度真实使用测试中，测试智能体先完成原问题、自然改写、官方术语和精确 URL
四阶段复查；主智能体随后在同一热库中再次复现。数据集为
`cppreference-2026-07-26`，分类与版本条件均显式传入。

```text
docatlas_ask(
  dataset_id="cppreference-2026-07-26",
  category="language",
  query="co_await expression awaiter protocol await_ready await_suspend await_resume",
  version_target="C++20",
  version_mode="strict",
  token_budget=3000,
  format="json"
)
```

实际返回 `status=ok`，首位是 K1151 `requires expression (since C++20)`，
`fetch.requested=0`。改用精确 URL
`https://cppreference.com/cpp/language/coroutines`、`fetch_limit=1` 后仍以
`C++ language` 总页为首位，`fetch.requested=0`。总页正文已经明确链接
Coroutines，目标在线页实测存在且标题为 `Coroutines (C++20)`。

```text
docatlas_ask(
  dataset_id="cppreference-2026-07-26",
  category="standard_library",
  query="std::ranges::sort range overload Comp comp Proj proj pointer-to-member projection",
  version_target="C++20",
  version_mode="strict",
  token_budget=3000,
  format="json"
)
```

实际首位是 K873 `std::binary search`，`fetch.requested=0`。改用精确 URL
`https://cppreference.com/cpp/algorithm/ranges/sort` 后仍只返回 Algorithms
library 总页和普通算法页，`fetch.requested=0`；目标 URL 实测 HTTP 200，标题为
`std::ranges::sort - cppreference.com`。

同轮的 `std::span` 起初也失败，但主智能体复现时已经能够按精确符号补抓 5 页并
正确返回，因此不把它作为稳定复现。Coroutines 与 `ranges::sort` 在热库、官方
术语和精确 URL 三个条件下仍稳定失败，故重新打开本议题。

## 2026-07-26 保留资格审查

本议题继续保持 `open`，理由不是“答案排序不够理想”，而是同时满足以下三条：

- **会阻断真实任务：** 用户已经给出官方术语甚至精确 URL，系统仍不抓目标页，
  转而返回无关页面；这是可重复的错误结果，不是体验偏好。
- **属于 DocAtlas：** AI 中间层最多把自然语言改写成官方术语，不能再提供比精确
  URL 更强的提示。本例在完成这一步后仍失败，缺口位于确定性的清单候选匹配。
- **可由程序修复：** 目标页在线且属于既定数据集；现有代码已经有安全候选等级，
  可通过路径规范化、候选匹配和回归测试收敛，不需要人工补充答案或猜测语义。

验收只保留稳定复现的 Coroutines 与 `std::ranges::sort`；已经恢复的
`std::span` 不计入修复范围。

## 2026-07-27 解决记录

先把复现拆开逐条实测，结论是**三条里只有两条是程序的错**。

### 分诊（`cppreference-2026-07-26` 真实库，只读探针）

| 输入 | 改前 | 判定 |
|---|---|---|
| `Coroutines`（官方页名） | `exact_slug` 命中 `/cpp/language/coroutines` | **本来就是好的** |
| `https://…/cpp/language/coroutines` | 一个候选都没有 | 缺陷一 |
| `std::ranges::sort` | 头名是 `/cpp/algorithm/sort` | 缺陷二 |
| `co_await expression awaiter protocol …` | 命中 `/cpp/keyword/co_await` | **不是程序的错** |

原报告的两条命令都没试过官方页名本身。`Coroutines` 一直是通的——真正断掉的是
比页名**更精确**的那两种输入。最后一条是关键词堆，把自然语言落成官方术语是
AI 中间层的职责（`ENH-007`），核心不该去猜一句话里哪几个词是页面名。

### 缺陷一：最精确的输入被当成一串乱码

整条 URL 走的是普通名字规范化，`https://cppreference.com/cpp/language/coroutines`
变成 `httpscppreferencecomcpplanguagecoroutines`，跟任何 slug 都对不上。于是
"用户给了准确地址"这个最强线索，成了系统里最没用的输入。

`ondemand.target_paths()` 新增最高优先档 `exact_url`：查询里的 URL 交给来源
适配器的 `normalize_link_target` 判定归属和路径——核心不认识任何站点，也不去
猜别人的地址长什么样。四个适配器实测都能收敛自己的变体写法：

```text
epic-ue-5.8      https://dev.epicgames.com/documentation/en-us/unreal-engine/API/…/AActor
                 → /documentation/unreal-engine/API/…/AActor      （脱掉语言段）
cppreference     https://en.cppreference.com/w/cpp/algorithm/ranges/sort
                 → /cpp/algorithm/ranges/sort                     （脱掉 /w/ 前缀）
blender          https://docs.blender.org/manual/en/5.2/editors/shader_editor.html
                 → /editors/shader_editor
roblox           https://create.roblox.com/docs/reference/engine/classes/DataStoreService
                 → /docs/reference/engine/classes/DataStoreService
```

站外地址一律不认（`https://example.invalid/...` → 无候选）。

### 缺陷二：限定符只用来剥末段，前面几段整个丢掉

`std::ranges::sort` 和 `std::sort` 是两页，`ranges` 就是区分它们的那一段，而它
明明白白写在正确那一页的地址里。旧逻辑只取末段 `sort`，四个同名页只好靠
"路径浅的排前面"选，于是稳定选中错的那个。

`text.qualifier_segments()` 取出末段之前的几段，`ondemand._collect()` 在同一档
内按命中数**稳定排序**。只影响排序、不新增候选，所以不会扩大补抓范围。太短的
段（`std`）不参与——它在任何路径里都撞得到，拿它排序等于随机。

真实库 `--fetch-limit 1` 实测：

| 查询 | 改前头名 | 改后头名 |
|---|---|---|
| `std::ranges::sort` | `/cpp/algorithm/sort` | `/cpp/algorithm/ranges/sort`（标题 `std::ranges::sort`） |
| `std::sort`（反向控制组） | `/cpp/algorithm/sort` | `/cpp/algorithm/sort`，未被改坏 |
| `std::from_chars`（控制组） | `/cpp/utility/from_chars` | 不变 |

`ask "https://…/coroutines"` 当场抓回 `Coroutines (C++20)`，库从 126 页涨到 127。

### 缺陷三（本轮新发现）：系统给出的下一步命令自己跑不通

验收缺陷一时撞见的：`related` 的 `next_steps` 会打印

```text
python -m docatlas get "/render/shader_nodes/index"
```

而 `get` 按名字匹配只看得到末段 `index`，照着做只会得到"本数据集的清单里也没有
对得上的页面"。这和前两条是同一个病：**系统把精确标识符递给用户，自己却不认。**
所以 `target_paths()` 同时接受清单内路径——先问适配器（能收敛语言段之类的变体），
不认得就原样比对，那本来就是库里的写法。

### 回归

- 单元测试 231 → **241 全过**，新增 `QualifiedTargetTests`。
- 四个数据集 `validate` 的 inventory 7 项、content 16 项**全 pass**。
- 一个进程内交错切换四个库 8 次，`dataset.dataset_id` 逐条对齐，无串库。

### 明确不做的

关键词堆（`co_await expression awaiter protocol await_ready …`）不在修复范围。
让核心从一句话里猜哪几个词是页面名，正是本议题反对的"拿弱相关结果当答案"。
这一步属于 AI 中间层，`SKILL.md` 已写明；同时新增一条更省事的出路——知道确切
页面时直接把官方 URL 或路径当查询词传进来。
