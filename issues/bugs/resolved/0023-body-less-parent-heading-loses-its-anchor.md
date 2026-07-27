---
id: BUG-023
title: "父标题自己没有正文时整节消失，官方页面上存在的锚点在库里认不出来"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: context
labels: [chunking, context, anchors]
reported_at: 2026-07-28
resolved_at: 2026-07-28
github_issue: null
fix_pr: null
related: [BUG-014, BUG-022]
---

# 问题

用户反馈：空正文父标题的锚点丢了。

cppreference 的 C++20 页是这个形状——`## New library features` 底下直接就是子
标题，它自己一个字都没有：

```markdown
## New library features
### New headers
### Library features
```

官方页面上 `#New_library_features` 是个真实可跳转的锚点，但库里没有任何一块认领
它。用户把这个地址贴过来，[[BUG-014]] 建立的小节限定就失效，退回整页。

## 复现

```bash
DOCATLAS_DATASET=cppreference-2026-07-26 python -m docatlas ask "https://cppreference.com/cpp/20#New_library_features" --token-budget 600
```

修复前：

```text
注意：地址里的 `#newlibraryfeatures` 在本页没有对应的小节，下面是整页的内容。
```

后面跟着整页 3 块内容，其中第一块是页面概览，跟用户指的那一节没关系。

## 期望结果

限定到 `New library features` 这一节，只给它底下的内容。

## 根因定位

`chunking.split_sections` 的 `finish()` 只在有正文时才落一条记录：

```python
def finish() -> None:
    content = "\n".join(current["lines"]).strip()
    if content:
        raw_sections.append({...})
```

这条判断本身是对的——为一个空标题造一条没有正文的小节，只会在检索里多出一条
答不了任何问题的空块。问题是**它把标题和锚点也一起丢了**。

于是 `context._fragment_section` 按锚点找不到任何一块，直接返回空。

真实库里确认，C++20 页存下来的 9 个小节里没有 `New library features`，但它并没有
真的消失——子小节把它原样带在 `heading_path` 里：

```text
C++20 > New library features > New headers
C++20 > New library features > Library features
```

## 解决记录

`docatlas/context.py` 新增 `_ancestor_scope()`：按锚点认不出来时，再拿这个锚点去
比一遍 `heading_path` 上的每一段（用 `heading_anchor` 这条同样的拍平规则），命中
就把那一段的路径前缀当作这一节的范围。

选这条路而不是"给空标题补一条记录"，有三个理由：

1. **不往库里塞没有正文的空块。** 空块进了检索池就要占名额，而它答不了任何问题。
2. **不用重建已有的库。** 这是查询时的推导，现存数据库立刻就能用，不需要
   `reprocess`，也不需要动 `CHUNKER_VERSION`。
3. **信息本来就在。** `heading_path` 已经存了这一段，再存一份就是同一件事记两遍。

范围划定沿用 [[BUG-014]] 原有的那一套（`heading_path` 前缀匹配），只是这次的
scope 来自路径推导而不是命中块自己的 `heading_path`。认不出来时仍然如实报告
`matched: false`，没有拿新退路去掩盖真找不到的情况。

## 验证

**真实库**，同一条复现命令：

```text
已按地址里的 `#newlibraryfeatures` 限定到小节「New library features」。
```

返回 2 块，正是 `New headers` 和 `Library features`，页面概览那一块不再混进来。

回归测试见 `ParentHeadingAnchorTests`（4 条），夹具照搬 C++20 页的真实层级形状：
`New library features` 只作为子小节 `heading_path` 的一段存在，没有自己的记录。
两条断言新行为，两条是反向控制组——自带锚点的小节仍走原来那条路，完全对不上的
锚点仍然如实说找不到。把 `_ancestor_scope` 还原成恒空，前两条全红，后两条全绿。

## 外部关联

- GitHub Issue：
- 修复 PR：
