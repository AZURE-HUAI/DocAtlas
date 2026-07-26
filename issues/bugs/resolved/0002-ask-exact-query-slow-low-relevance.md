---
id: BUG-002
title: "精确 `ask` 查询曾出现性能与排序问题"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: search
labels: [ask, performance, ranking, blueprint-api]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-001, BUG-003, ENH-005, ENH-007, ENH-008]
---

# 问题

精确页面查询曾被补抓缺口和面包屑噪音干扰，并出现明显延迟。

## 复现

```powershell
python -m docatlas ask "Blueprint Camera zoom Set Field Of View FOV" `
  --token-budget 2500 --category blueprint_api --fetch-limit 3
```

## 验证

| 项目 | 修复前 | 修复后 |
|---|---:|---:|
| 耗时 | 39.2 秒 | 0.49 秒 |
| 首位 | OpenCV Camera View Info | Set Field Of View |

面包屑已从检索正文中清除，关系证据保持完整。版本语义由 `ENH-008` 的结构化合同
处理；自然语言和跨语言改写由 AI/Skill 处理。

## 解决记录

修复了分类查询性能、精确页面按需补抓和面包屑索引噪音。原问题均已验证完成，
因此状态为 `resolved`；不再用本议题承载自然语言理解问题。
