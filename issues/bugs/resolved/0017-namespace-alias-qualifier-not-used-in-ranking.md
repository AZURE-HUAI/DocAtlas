---
id: BUG-017
title: "带命名空间限定符的查询找不到官方页面（并列标题未拆名，限定名后缀不参与匹配）"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: search
labels: [cppreference, ranking, qualifiers]
reported_at: 2026-07-27
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-008]
---

# 问题

查询 `std::views::transform`（C++20 惯用写法，`views` 是 `ranges::views`
的标准命名空间别名）时，真正对应的页面
`std::ranges::transform_view`（官方标题就是"std::ranges::views::transform,
std::ranges::transform_view"）既不在 `docatlas_ask` 的正文结果里，也不在
`docatlas_search` 前 8 名里，只以关系指针的形式出现；排到第一的反而是完全不带
`views`/`ranges` 限定的最朴素算法 `std::transform`。

这不是内容缺失——`transform_view` 已经在库里、已经被抓取，`docatlas_ask` 的
"交叉关系"里明确列出了它（`show K1585`）。是排序/实体匹配阶段没有用上用户
查询里的限定符信息。

## 复现

```
docatlas_ask(query="std::views::transform", dataset_id="cppreference-2026-07-26",
             category="standard_library", token_budget=1500, no_fetch=true)
→ 首位 K1569 std::transform（<algorithm> 头，最朴素的算法，名字里完全没有
  "views"）；transform_view 本身不在 4 条正文知识块里，只在末尾"交叉关系"
  列表中以指针形式出现（show K1585）。

docatlas_search(query="std::views::transform", dataset_id="cppreference-2026-07-26", limit=8)
→ 前 8 名全部 match_stage="entity"，分数 104.8-110，全部是别的"transform"
  （std::transform、std::ranges::transform、std::collate::transform、
  std::regex_traits::transform 等）；transform_view 完全不在前 8 名里。
```

## 根因定位（闸门 3）

`docatlas/search.py` 的 `query_names()`（约 140-157 行）只尝试两种归一化名字去
命中 `entities`/`entity_aliases` 表：查询原文，以及 `qualifier_tail(query)`——
后者（见 `docatlas/text.py:23-32`）只保留限定名的**最后一段**（`transform`），
中间的限定符（`views`）被整段丢弃，不参与后续任何一档匹配或打分。

`docatlas/text.py` 里其实已经有一个专门为**保留**限定符位置信息设计的函数
`qualifier_segments()`（35 行起），文档字符串明确写着这正是为了解决
"`std::ranges::sort` 和 `std::sort` 变成同一条查询"这类问题（BUG-008）。但
`qualifier_segments` 在整个仓库里唯一的调用点是 `docatlas/ondemand.py:148`——
只用在"按需抓取该取哪个 pending 页面"的场景，从未被 `docatlas/search.py` 的
实体匹配（`_entity_hits()`、`query_names()`）或打分（`_score()`）引用。

结果是：当查询的完整限定名（`std::views::transform`）不能精确命中任何一个
已登记的实体/别名全名时，系统直接退化到只用最后一段（`transform`）去查，
而 `transform` 在这个数据集里已经对应至少 8 个不同实体（算法、range
适配器、`std::optional::transform`、`std::collate::transform`、
`std::regex_traits::transform`……）。之后的打分（`_score()`，257 行起）没有
任何环节比较"候选实体的别名文本 vs 用户查询里的限定符段"，纯靠命中档位
（entity 档基础分 100）和 rank 衰减、分类加分区分，天然对哪个候选"更贴合
views 这个限定符"没有偏好。

**这不是 BUG-008 复发**：BUG-008 处理的是"按需抓取该取哪个 pending 页面"这条
路径，已经在 `ondemand.py` 修好；本议题是同一类限定符信息，在**已经入库、
需要排序挑一个**这条路径上，从未被接上。

## 复现强度 / 四道闸门结论

- **闸门1（规范调用复跑）**：通过。完整 MCP、显式 `dataset_id`、合理预算，
  `docatlas_ask`/`docatlas_search` 两种调用独立复现同一结果。
- **闸门2（内容是否在库里）**：通过。`transform_view`（K1585）已抓取入库，
  `docatlas_ask` 自己的"交叉关系"列表证明了这一点；不是覆盖范围问题。
- **闸门3（能否说出改哪一层）**：通过，见上——`docatlas/search.py` 的
  `query_names()`（140-157 行）应该在精确匹配失败后，用
  `qualifier_segments()` 而不是只用 `qualifier_tail()` 兜底，或者在
  `_score()` 里为"候选别名包含查询限定符段"增加打分信号。
- **闸门4（最终输出层复现）**：通过。经完整 `docatlas_ask`/`docatlas_search`
  复现，不是只测到内部函数。

## 验证

**修复前**：前 8 名全是别的 `transform`（`std::transform`、
`std::collate::transform`、`std::regex_traits::transform` 等），得分 104.8～110，
`transform_view` 一条都没进。

**修复后**（实测，真实命令行）：

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas search "std::views::transform" --limit 5
#  [1] K2645  得分 118.0  https://cppreference.com/cpp/ranges/transform_view
#  [2] K1933  得分 110.9  https://cppreference.com/cpp/algorithm/ranges/transform
#  [5] K2646  得分 110.1  https://cppreference.com/cpp/ranges/transform_view#datamembers
```

`std::transform` 已经掉出前 5。

**反向回归**（怕把不带限定符的查询搞坏），五条各自落在正确页面上：

| 查询 | 首位 |
|---|---|
| `std::transform` | `/cpp/algorithm/transform` |
| `std::sort` | `/cpp/algorithm/sort` |
| `std::ranges::sort` | `/cpp/algorithm/ranges/sort`（BUG-008 那条） |
| `std::optional::transform` | `/cpp/utility/optional/transform` |
| `std::vector` | `/cpp/container/vector` |

回归测试见 `QualifierMatchTests`（6 条）。变异检验：不生成后缀、后缀里带上
末段、并列标题不拆、查询不试后缀——四种改法各自都让测试变红。

## 解决记录

现象属实且稳定复现，**但报告给的修复位置改了也不会生效**。

### 为什么在 `_score()` 里加打分是白加

报告建议在 `_score()` 里为"候选别名包含查询限定符段"加分。实测候选池：

```text
entity 档命中 19 条，分布在 6 个页面 —— 没有一条来自 /cpp/ranges/transform_view
transform_view 只在 all_terms 档进来一条，K1588，52.5 分
entity 档的基础分是 100
```

正确的那一页压根不在 entity 档里，52.5 对 104～110，加多少分都翻不过来。

### 真根因一：并列标题被当成一个名字

这一页在库里登记的实体是：

```text
canonical_name  std::ranges::views::transform, std::ranges::transform view
normalized_name stdrangesviewstransformstdrangestransformview
```

cppreference 的标题会把同一个东西的几个官方写法用逗号并排列出来，这是
**两个**名字。整串当一个名字归一化，得到的这串东西没有任何用户会打出来，于是
这一页只能靠末段 `transform` 被找到——而这个库里叫 transform 的有八个。

拆名字属于"这个站怎么给页面起标题"，和"怎么解析这个站的页面"是同一类知识，
所以放在来源适配器（`sources/cppreference.py` 的 `extra_entity_aliases`），
核心不认识逗号约定。`entity_descriptor` 相应地从 `hook`（只问知识包）改成
`extension`（知识包优先，其次来源适配器）——这个分工本来就写在 `runtime.py`
的注释里，只是这一处没用上。

只拆带 `::` 的段：普通标题里的逗号是行文，"Lighting, Shadows and Reflections"
拆开只会拼出两个假名字。

### 真根因二：限定名的后缀不参与匹配

拆完仍然对不上。用户写的是 `std::views::transform`（标准里 `std::views` 就是
`std::ranges::views` 的别名），官方页面叫 `std::ranges::views::transform`，两个
归一化之后并不相等。

`text.py` 里本来就有 `qualifier_segments()`，文档字符串写明是为了解决这类问题
（BUG-008），但它唯一的调用点在 `ondemand.py`——只用在按需抓取那条路径上。

补了一个 `qualifier_suffixes()`：限定名逐段去掉开头，得到的后缀仍然指着同一个
东西。这是通用的命名规律（C++ 命名空间别名、Python 重导出、Java 包名简写），
所以放在 `text.py`，不属于任何站点。两侧都要用上才碰得到头：

- **索引侧**（`documents.py`）：别名一并登记后缀写法。
- **查询侧**（`search.py query_names`）：按"查询原文 → 后缀写法 → 末段"的顺序试。

刻意**不含只剩末段的那一档**——`transform` 正是歧义的来源，它已经由
`qualifier_tail` 单独当最后一档兜底。也刻意设了上限（默认 2 档），一个名字派生
出无穷多别名只会让索引白白变大。

### 最后才轮到打分

两边都进 entity 档之后需要分高下，这时报告说的那个信号才有用武之地：查询带
限定符时，页面标题/地址里能对上几段，就按比例加分（满分 8）。它是**平局裁决**，
不是主要机制。

## 外部关联

- GitHub Issue：
- 修复 PR：
