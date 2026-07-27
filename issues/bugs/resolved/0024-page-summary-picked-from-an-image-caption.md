---
id: BUG-024
title: "页面摘要挑中了图注和参数标签，三个适配器各抄一份规则各漏一处"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: adapters
labels: [adapters, chunking, data-quality]
reported_at: 2026-07-28
resolved_at: 2026-07-28
github_issue: null
fix_pr: null
related: [BUG-022]
---

# 问题

修 [[BUG-022]] 时顺着摘要这条链查下去发现的：Blender 的节点页，
`pages.description` 存的不是一句话，是图片语法拍平后的残骸。

```text
![Blur Attribute node. ](https://docs.blender.org/manual/en/5.2/ images/node-types GeometryNodeBlurAttribute.webp)
```

这一列会原样进 Markdown 导出（`export.py`），也是"这一页在讲什么"的唯一
简短答案，等于导出的每个节点页都顶着一行乱码。

## 复现

```bash
DOCATLAS_DATASET=blender-manual-5.2 python -m docatlas export
```

或直接查库：

```sql
SELECT description FROM pages WHERE path LIKE '%geometry_nodes/attribute/%';
```

## 根因定位

三个适配器（cppreference、Blender、Roblox）各自抄了一份"挑正文第一句当摘要"
的代码。cppreference 和 Blender 那两份一字不差，Roblox 的多排除了一个 `*`：

```python
description = next(
    (plain_text(line) for line in markdown.splitlines()
     if line.strip() and not line.lstrip().startswith(("#", "|", "-"))), "")
```

三份都漏了同一件事：**整行只有一张图时，这行也算"正文第一句"**，于是图的 alt
成了摘要。Blender 的节点页每一页开头都是示意图，所以整站中招。

顺带暴露的另外两处：

- 第一句拍平后是空串时，旧代码返回空串就收工，不往下找。cppreference 因此有
  **128 页压根没有摘要**。
- 短标签也会被当成摘要。`shader_to_rgb` 页开头是徽章 `EEVEE Only`，
  `bright_contrast` 页开头是参数名 `Image`。

## 解决记录

抽成 `htmlmd.lead_sentence()` 一处实现，三个适配器共用。放 `htmlmd.py` 是因为
它已经装着 `plain_text`，而这条规则是 Markdown 文本处理，不认识任何站点。

跳过：标题、表格行、列表项、纯图片行（外面再套一层链接的写法也算），
以及短于 `MIN_LEAD_CHARS` 的标签。都不合格就返回空串
——宁可没有摘要，也不要拿一句不知所云的东西冒充；页面叫什么，标题里本来就有。

三个刻意的选择：

- **按字数不按词数。** 词数要靠空格切，而中日韩整句话一个空格都没有，按词数
  会把这些语言的正常句子全判成标签。数据集自己声明语言，这里不做假设。
- **不跳引用块。** Roblox 有一批页面的开场白就写在 `> …` 里，`plain_text`
  会把记号去掉，正好是要的那句。
- **只把"星号加空格"当列表项。** `**加粗**` 开头的是正文首句，按 `*` 一刀切会
  连 `**Chaos Physics** is a light-weight…` 这种正经开头一起丢掉。

试过但否决了的：**只在第一个标题之前找**。这条规则更漂亮，`bright_contrast`
那种"整页只有图和参数表"的页面会正确地空着。但实测 Roblox 107 页全部落空
（它的页面从标题开始写），blender 空页从 8 涨到 9、cppreference 从 1 涨到 4。
为一个更干净的边界赔上三个库的摘要，不值。

摘要变了会连带改 0 号小节的内容，所以 `CHUNKER_VERSION` 升到 v8。

## 验证

四个数据集重跑真实原始页面（`epic-ue-5.8` 抽 400 页）：

| 数据集 | 抽查 | 摘要变了 | 原本是图片残骸 | 改后仍是残骸 | 改后为空 |
|---|---|---|---|---|---|
| blender-manual-5.2 | 91 | 65 | 5 | **0** | **0** |
| cppreference-2026-07-26 | 193 | 135 | 0 | **0** | **0** |
| epic-ue-5.8 | 400 | 0 | 0 | **0** | **0** |
| roblox-creator-2026-07-26 | 107 | 1 | 0 | **0** | **0** |

两个大数字都核实过成因，不是回归：

- Blender 那 65 页全是同一件事——图注换成了真正的说明。
  `attribute_statistic` 从 `Attribute Statistic node.` 变成
  `The Attribute Statistic node evaluates a field on a geometry…`。
  （"原本是图片残骸"只数了拍平后仍看得见 `![` 的那 5 页，其余 60 页是 alt
  被干净渲染的同一个缺陷。）
- cppreference 那 135 页里有 **128 页原本压根没有摘要**，是上面第二处成因。

Epic 一页没动，因为它的摘要来自页面 JSON，不走这条路——正好是反向对照。

重加工后抽查确认落库：三页原来顶着图注的 Blender 节点页现在都是完整句子。

回归测试见 `LeadSentenceTests`（8 条）。把 `lead_sentence` 还原成旧的那份
推导式，5 条断言新行为的用例全红，3 条反向控制组（`**加粗**` 开头、引用块
开头、中文整句）全绿。

## 外部关联

- GitHub Issue：
- 修复 PR：
