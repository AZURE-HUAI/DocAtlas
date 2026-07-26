---
id: BUG-008
title: "`ask` 无法按官方页面名补抓 route slug 不完全相同的 pending 页面"
type: bug
status: open
lifecycle: unresolved
priority: high
area: on-demand
labels: [ask, on-demand-fetch, inventory, ranking, multi-dataset]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-002, BUG-003]
---

# 问题

页面已经在冻结清单中但正文为 pending 时，`ask` 只在整条查询规范化后与 URL 最后
一段完全一致时强制补抓。普通官方页面名、带命名空间的 C++ 符号，以及 URL 含
`.html` 的站点都很难满足这个条件；只要已有小样能提供弱相关块，查询会快速返回
错误页面，不抓真正目标页。

## 环境

- 数据集：`cppreference-2026-07-26`、`blender-manual-5.2` 临时小样
- 版本：C++20 学习基线；Blender 5.2
- 入口：CLI

## 复现

Blender 的 `Fields` 页面已在 695 页清单中，路径为
`/modeling/geometry_nodes/fields.html`：

```powershell
$env:DOCATLAS_DATASET='blender-manual-5.2'
python -m docatlas ask "Fields" --category geometry_nodes --token-budget 1600 --json
python -m docatlas get "fields" --limit 1
python -m docatlas ask "Fields" --category geometry_nodes --token-budget 1600 --json
```

`.html` 路径的精确对照：

```powershell
python -m docatlas get "wave" --limit 1
python -m docatlas get "wavehtml" --limit 2
```

## 实际结果

`Fields`：

- 补抓前 `ask` 226 ms、退出码 0，首三位为 Capture Attribute、Transfer
  Attributes、Capture Attribute Inputs，没有 Fields。
- `get "fields"` 1.367 秒明确从 pending 清单抓到
  `/modeling/geometry_nodes/fields.html`，成功并新建 6 条关系。
- 补抓后同一 `ask` 176 ms，Fields 立即成为首位。

`Wave Texture Node`：

- 官方完整名称 `ask` 首位为 Image Texture，随后是 Noise 与 White Noise。
- `get "wave"` 因短词不做包含匹配而报告清单里没有页面。
- `get "wavehtml"` 1.734 秒从同一清单抓到 Shader 与 Geometry 两张 Wave 页面。

36 轮压力测试中的同类结果：

- C++ 12 个首轮 `ask` 正确首位 0/12；`std::from_chars`、RAII、`std::ranges::sort`、
  `std::jthread` 等在线精确页均存在且可访问。
- Blender Shader 正确首位 1/12；Geometry 正确首位 4/12。
- 相关查询通常在约 0.2–0.5 秒返回已有弱相关小样，表明并非网络补抓超时。

## 期望结果

- 冻结清单中存在明显对应的 pending 页面时，官方页面名和常见限定符写法能够触发
  候选补抓。
- `.html` 等站点实现细节不应成为用户必须输入的页面名组成部分。
- 若候选存在但不够确定，结果应说明没有补抓的原因和可执行的下一步，而不是把弱
  相关本地块当作完整回答。

## 可能方向

可以评估基于标题线索、路径多段、去扩展名 slug、命名空间剥离和短语覆盖率的候选
生成。补抓策略仍应有严格页数上限，避免把自然问题变成宽泛爬取。

## 临时绕行

先把问题改写成 URL 最后一段的精确 slug；对于 HTML 文档，本次测试甚至需要附加
`html`，例如 `wavehtml`。显式 `get` 后再运行原始 `ask`。

## 调查记录

- Blender `stats` 在模拟学习者测试前后保持 58 个 success，证明大量精确名称查询
  没有触发补抓。
- 主智能体使用 `get` 正向证明目标页已在 pending 清单，不是 inventory 真缺页。
- 临时数据集和来源适配器已按测试要求删除。

## 验证

建立只抓少量正文的清单，对 C++ 限定符、带空格官方标题、`.html` 路径和短 slug
分别测试“补抓前 → ask → 补抓后”结果。

## 解决记录


## 外部关联

- GitHub Issue：
- 修复 PR：
