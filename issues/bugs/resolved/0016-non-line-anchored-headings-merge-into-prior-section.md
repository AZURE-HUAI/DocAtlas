---
id: BUG-016
title: "标题没被识别，整段正文静默并入前一节（行内元素后的标题标记，与布局表格里的整节内容）"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: chunking
labels: [chunking, headings, cppreference, roblox]
reported_at: 2026-07-27
resolved_at: 2026-07-27
github_issue: null
fix_pr: null
related: [BUG-010, BUG-014, BUG-019]
---

# 问题

`docatlas/chunking.py` 的 `split_sections()` 用 `docatlas/constants.py:28` 的
`HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")` 逐行判断是否为标题。这个正则
要求 `#` 标记必须独占一整行（`^...$` 两端锚定）。当官方页面把一个真实的 ATX 标题
标记放在"不独占一行"的位置时——无论是嵌进表格单元格，还是因为上一个内联元素
（图片）后面缺了一个换行——`HEADING_RE` 都识别不到，这段内容连同它自己的标题
一起，被**静默**并入前一个已识别标题的小节里，对外呈现的 `heading_path`/标题
从此是错的，且往往和小节正文本身的措辞直接矛盾。

两个完全独立的测试方向、两个不同数据集，用两种不同触发方式各自独立发现了同一
根因：

1. **cppreference**：把简短的版本化语法变体塞进版本标注表格的单元格里，标题
   标记出现在表格行内，不在行首。
2. **Roblox Creator Hub**：`.md` 导出把一张图片和紧随其后的 `## 二级标题` 挤在
   同一行（图片 Markdown 后面本该换行，却只有一个空格），标题标记因此不在行首。

## 复现

**cppreference（K1059，闸门1 已用完整 MCP 规范调用复跑）**：

```
docatlas_ask(query="designated initializers", dataset_id="cppreference-2026-07-26",
             category="language", version_target="C++20", version_mode="strict",
             token_budget=1500)
→ 4 条知识块（K1053 概述、K1061 Notes、K1118/K1124 Reference initialization），
  没有一条是真正解释 designated initializers 语法规则和示例的正文。

docatlas_show(chunk_id="K1059", dataset_id="cppreference-2026-07-26")
→ 标题显示为"### Arrays with unknown bounds"，但正文后半段完整包含
  "### Designated initializers The syntax forms (3,4) are known as designated
  initializers: ... A a{.y = 2,.x = 1}; // error ..." ——真正的答案文本混在这个
  错误标题的小节里，被 |...|(since C++20)| 表格语法包住。
```

**Roblox Creator Hub（K2655，闸门1 已用完整 MCP 规范调用复跑）**：

```
docatlas_ask(query="Geometry Nodes modifier"...)  # 见 BUG-015，另一议题
docatlas_show(chunk_id="K2655", dataset_id="roblox-creator-2026-07-26")
→ 标题显示为"### Create tab"，但正文是 Fill / Sea Level 两个工具的说明，且正文
  内嵌的两张图片说明文字自己写着"Fill tool indicated in Edit tab of Terrain
  Editor"、"Sea Level tool indicated in Edit tab of Terrain Editor"——小节自己的
  标题和小节自己的正文直接矛盾。

主智能体直接用 Invoke-WebRequest 拉取官方 .md 原始字节（不经任何摘要/AI 处理）核实：
https://create.roblox.com/docs/en-us/studio/terrain-editor.md
在 "### Clear" 小节末尾找到：
  ...Create-Tab-Clear.png) ## Edit tab

  The **Edit** tab includes the [Select](#select)...
"## Edit tab" 紧跟在上一张图片的 Markdown 后面、同一行，中间只有一个空格，没有
换行——这不是独立的一行，`HEADING_RE` 因此永远匹配不到它，"Edit tab" 这个二级
标题从未被识别，后续 Select/Transform/Fill/Sea Level/Draw/Sculpt/Smooth/Paint/
Flatten 九个小节全部原样延续了上一个真正被识别到的标题"Create tab"。
```

## 期望结果

- 官方页面里真实存在、且有独立锚点/导航项的标题（如 `#edit-tab`、
  `#Designated_initializers`），即使标记位置不在行首，也不应该被完全忽略、
  内容被并入无关的前一个标题。
- 至少不应该在毫无信号的情况下，让一段小节正文携带一个和它自己内容矛盾的标题
  对外展示——这比"没找到"更容易误导用户，因为用户会误信这个错误的标题/归属。

## 根因定位（闸门 3）

`docatlas/constants.py:28`：

```python
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
```

`docatlas/chunking.py` 的 `split_sections()`（约 490-508 行）逐行调用
`HEADING_RE.match(line)`；不匹配时该行被并入 `current["lines"]`（即当前小节的
正文），直到遇到下一个真正匹配的标题才 `finish()` 并切换小节。这段逻辑本身
（`heading_stack` 按层级出栈入栈，见 498-501 行）是正确的——本议题不是"切换
逻辑算错"，是"该切换的地方，标题标记根本没能进入判断"。

两处触发位置不同，但都会让本该独立的标题行"消失"在上一行的正文里：

- cppreference：标题标记出现在表格单元格 `| ### xxx ... | (since C++20) |`
  内部，不是独立一行。
- Roblox：标题标记跟在上一个内联元素（图片）后面、同一物理行，不是独立一行。

## 复现强度 / 四道闸门结论

- **闸门1（规范调用复跑）**：通过。两处均用完整 MCP、显式 `dataset_id`、官方
  术语、合理预算复跑，结果稳定复现。
- **闸门2（内容是否在库里）**：通过，且两处都不是"来源没收录"——`docatlas_show`
  证实内容完整存在，只是标题元数据挂错了小节；Roblox 一处额外核实过不是站点
  改版/来源漂移（用未经处理的原始字节直接确认了缺换行这个具体字符层面的问题）。
- **闸门3（能否说出改哪一层）**：通过，见上"根因定位"，具体到
  `docatlas/constants.py:28` 与 `docatlas/chunking.py` 的 `split_sections()`。
- **闸门4（最终输出层复现）**：通过。cppreference 一处经完整 `docatlas_ask`
  复现（子智能体 2 次+主智能体 1 次，共 3 次独立复现）；Roblox 一处经完整
  `docatlas_ask`/`docatlas_show` 复现（子智能体 3 次+主智能体 1 次）。

子智能体报告已自查确认与已解决的 BUG-002（性能与排序）、BUG-008（pending 页面
补抓）、BUG-010（含链接的标题生成不可跳转锚点）、BUG-014（URL fragment 未限定
小节）均不是同一根因——那几条处理的是"标题已经被正确识别、但锚点或补抓路径算
错"，本议题是"标题从未被识别，内容整段并错位置"，发生在更早的切分阶段。

## 影响范围

两个独立数据集（`cppreference-2026-07-26`、`roblox-creator-2026-07-26`）、两种
不同触发方式各自独立命中，机制本身在 `docatlas/chunking.py` 通用核心里，对任何
数据集都生效。cppreference 大量使用"版本标注表格"这一约定收录简短语法变体
（同一页 K1053 的语法表格里能看到同样的模式：`(3)` `(4)` 加 `(since C++20)`
标注），推测这一模式在该数据集内不止一处；具体还有多少页受影响未做全量扫描，
需要建库层面的批量核查才能给出准确数字，本次测试只确认了机制真实存在且可复现。

## 验证

`CHUNKER_VERSION` v5 → v6，四个数据集全部 `reprocess` 重加工。

**Roblox 地形编辑器页**（`/docs/en-us/studio/terrain-editor`），修复前九个小节
全挂在 `Create tab` 下，修复后：

```text
Terrain Editor > Terrain Editor > Create tab
Terrain Editor > Terrain Editor > Create tab > Clear
Terrain Editor > Terrain Editor > Edit tab          ← 认出来了
Terrain Editor > Terrain Editor > Edit tab > Select
Terrain Editor > Terrain Editor > Edit tab > Transform
Terrain Editor > Terrain Editor > Edit tab > Draw
…Sculpt / Smooth / Paint / Flatten 同样归到 Edit tab 下
```

（`Fill` 与 `Sea Level` 被 `chunk_sections` 按既有规则并进了相邻块，取的是共同
父级路径 `Edit tab`——`split_sections` 本身切出了全部 16 个小节，这不是本议题
的问题。）

**cppreference designated initializers**，修复前 4 条知识块里没有一条是正文，
修复后：

```powershell
$env:DOCATLAS_DATASET='cppreference-2026-07-26'
python -m docatlas ask "designated initializers" --category language --token-budget 1500 --no-fetch
#  ## 2. Aggregate initialization > Designated initializers  [details]
#     https://cppreference.com/cpp/language/aggregate_initialization#designatedinitializers
```

小节独立了，锚点也和官网对得上。

回归测试见 `HeadingRecognitionTests`（7 条）。变异检验：不认围栏、落单围栏当
开头、不认行内元素后的标题、布局表格照旧压平——四种改法各自都让测试变红。

## 解决记录

两个方向的现象都属实，**但它们不是同一个根因**。报告把"两个独立方向撞见同一
根因"当成互相印证的证据，这一点经复核不成立：Roblox 那半确实卡在
`HEADING_RE` 的行锚定上，cppreference 那半根本不在切分层。

### Roblox：标题挤在行内元素后面（报告说对了）

官方 `.md` 导出第 57 行：

```text
![Clear tool ...Create-Tab-Clear.png) ## Edit tab
```

`## Edit tab` 和上一张图片挤在同一行，`HEADING_RE` 的 `^` 匹配不到，后面
Select/Transform/Fill/Sea Level/Draw/Sculpt/Smooth/Paint/Flatten **九个小节**
全部继承了错误的 `Create tab`。

新增 `TRAILING_HEADING_RE`，只认紧跟在**闭合的链接/图片括号**后面的标题标记，
并把前半行留给上一节当正文。要求闭合括号是为了挡掉误判——全库扫描里，行尾带
`#` 的还有汇编注释 `movl input(%rip), %eax # eax = input` 和 ASCII 码表，
它们都不是标题，加上这个条件之后一条都不会被误认。

### cppreference：布局表格把整节内容压成一行（报告的定位是错的）

报告说是"标题标记出现在表格单元格内部、不在行首"。拉原始 HTML 看，实际是：

```html
<table class="t-rev-begin"><tr class="t-rev t-since-cxx20"><td>
  <h3><span id="Designated_initializers">Designated initializers</span></h3>
  <p>The syntax forms (3,4) are known as designated initializers…</p>
  <pre>A a{.y = 2,.x = 1};</pre>
```

cppreference 拿 `<table>` 当**版本标注的布局容器**，一个 `<td>` 里装着完整的
一节。`htmlmd.py` 在 `</td>` 处把整格 `replace("\n", " ")` 压成一行，标题、
段落和代码块一起没了。**按报告的思路去放宽 `HEADING_RE` 修不好这个**：就算
匹配上，"标题文字"会是整节正文。

改在 `htmlmd.py`：单元格里出现标题或代码围栏时，这张表就不是数据表，是布局
容器——把这一格的内容按块输出，不塞进表格行。只认这两样，因为它们在 Markdown
表格里确实无法表达；多段纯文字被压成一行只是难看，不改变意思，不值得为它改变
表格形状。普通数据表完全不受影响（有回归测试守着）。

### 顺带查出一条报告没发现的（BUG-019）

为了确认放宽标题识别不会误伤，先写脚本全库扫了一遍三个数据集的标题异常，
结果撞见 `split_sections()` 压根没有代码围栏跟踪——见 [[BUG-019]]。两条同属
"逐行判断标题、不理会 Markdown 块结构"，在同一次改动里一起修了。

## 外部关联

- GitHub Issue：
- 修复 PR：
