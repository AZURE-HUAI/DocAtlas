---
id: BUG-003
title: "本地搜索没有命中已存在的 `Set Field Of View` 蓝图 API 页面"
type: bug
status: open
lifecycle: unresolved
priority: high
area: inventory
labels: [search, related, blueprint-api, on-demand-fetch]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
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
