---
id: BUG-003
title: "本地搜索没有命中已存在的 `Set Field Of View` 蓝图 API 页面"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: inventory
labels: [search, related, blueprint-api, on-demand-fetch]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
related: [BUG-002, BUG-005]
---

# 问题

标题完全一致的 UE 5.8 蓝图 API 页面在线存在，但本地 `search` 和 `related`
均未命中。

## 复现

```powershell
python -m docatlas search "Set Field Of View" --limit 12
python -m docatlas related "Set Field Of View"
```

## 实际结果

- `search` 返回 Viewport Toolbar、First Person Rendering、Ray Tracing
  Performance Guide 等页面，没有返回名称完全一致的蓝图 API 页面。
- `related` 约 340 毫秒后只返回 `[]`。
- `related` 没有按需补抓，也没有说明实体不存在、页面未抓取，还是实体存在但
  没有关系。

## 外部核对

Epic 在线官方文档存在该 UE 5.8 页面：

```text
https://dev.epicgames.com/documentation/unreal-engine/BlueprintAPI/Camera/SetFieldOfView
```

## 期望结果

- 官方清单或在线文档中已知存在的精确页面，不应无说明地表现为不存在。
- 用户能够区分页面未发现、未抓取、未建立实体和没有关系。
- `search`、`ask` 与 `related` 对同一页面的可发现性保持一致或能解释差异。

## 可能方向

- 核对冻结清单、正文抓取、实体生成和搜索索引之间的一致性。
- 评估 `related` 是否适合按需补抓；如果不适合，也可以提供可执行的下一步提示。
- 考虑加入“官方页面存在但本地精确查询未命中”的验证用例。

按需补抓只是参考可能，并非本议题指定的解决方式。

## 调查记录

尚未确认是页面未进入冻结清单、尚未抓取、分类错误、标题规范化问题，
还是精确匹配排序被其他结果压低。

## 验证

先确认事实：该页确实在冻结清单里，只是正文未抓。

```text
sqlite> SELECT id,path,category,status FROM pages WHERE normalized_slug='setfieldofview';
189233 | /documentation/unreal-engine/BlueprintAPI/Camera/SetFieldOfView | blueprint_api | pending
```

修复后：

```powershell
python -m docatlas related "Set Field Of View"
```

```json
{
  "status": "entity_not_found",
  "next_steps": [
    "本地没有正文，但全站清单里有 5 个页面对得上：",
    "  [蓝图 API] /documentation/unreal-engine/BlueprintAPI/Camera/SetFieldOfView",
    "  …",
    "取回来再查：python -m docatlas get \"Set Field Of View\""
  ]
}
```

```powershell
python -m docatlas search "Get Effective Field Of View" --limit 2
# …结果之后追加：
# 提示：全站清单里还有 1 个页面的名字与「Get Effective Field Of View」完全一致，
#       但正文尚未抓取，所以不在上面的结果里。
```

```powershell
python -m docatlas ask "Set Field Of View" --token-budget 1500
# 2.72 秒（含联网补抓 5 页），首条即 Set Field Of View，带 Inputs/Outputs 表和原出处
```

回归测试：`InventoryCandidateTests`、`RelatedContractTests` 共 10 个用例。

## 解决记录

**根因**：`search` 和 `related` 只看已加工的知识块，对"冻结清单里有这一页、
只是正文没取"完全没有可见性。页面不是不存在，是没被取回来——但两种情况在输出上
长得一模一样（一个空数组、一串沾边的结果）。

**改动**：新增 `ondemand.inventory_lookup()`，用与按需抓取相同的三档定位规则去
查清单，返回"对得上但未抓的页面"和"对得上且已抓的页面"两个列表；
`context.describe_lookup()` 把它翻译成可执行的下一步。三个入口全部接上：

- `search` 空结果 → 说明是哪一种"没有"；有结果但清单里还躺着同名页 →
  末尾追加 `exact_page_hint()` 提示（这一条专门解决"沾边结果掩盖了真页面"）。
- `related` → 结构化状态 + `lookup` + `next_steps`（见 BUG-005）。
- `ask` → 空结果时把清单情况写进 Markdown；非空时按 BUG-002 的规则自动补抓。

MCP 的 `docatlas_search` / `docatlas_related` 调的是同一批函数，不是另写一份。

**为什么不让 `related` 自己补抓**：`related` 是"看关系"，不是"取内容"。
让它联网会让一个只读命令变成会改数据的命令，而且用户往往只是想确认某个名字
在不在库里。给出可执行的下一步比替用户做决定更合适。

## 外部关联

- GitHub Issue：
- 修复 PR：
