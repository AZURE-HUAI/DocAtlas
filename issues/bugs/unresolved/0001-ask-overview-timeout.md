---
id: BUG-001
title: "`ask` 查询宽泛版本概览时超时且没有进度输出"
type: bug
status: open
lifecycle: unresolved
priority: high
area: query
labels: [ask, performance, diagnostics]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: [BUG-002]
---

# 问题

在 `epic-ue-5.8` 数据集上查询 UE 5.8 的整体更新概览时，`ask` 连续两次超过
60 秒，最终被外部超时终止，期间没有返回正文或进度信息。

## 复现

第一次查询会自动补抓，并且误将发行说明限定在 `guides`：

```powershell
python -m docatlas ask "Unreal Engine 5.8 release notes what's new major new features changes" --token-budget 6000 --category guides
```

第二次改用正确的 `community_docs` 分类，并通过 `--no-fetch` 排除网络补抓影响，
仍然复现：

```powershell
python -m docatlas ask "Summarize the most important new features and workflow changes in Unreal Engine 5.8 Release Notes across Rendering, Animation, MetaHuman, Worldbuilding, PCG, Gameplay, UI, Audio, Developer and Platform" --token-budget 6000 --category community_docs --no-fetch
```

## 实际结果

- 两次查询都超过 60 秒并被外部终止。
- 运行期间没有正文、阶段或进度输出。
- `--no-fetch` 没有消除问题。

## 对照

- `python -m docatlas search "Unreal Engine 5.8 Release Notes" --limit 20`
  约 0.5 秒返回。
- 连续执行多条 `python -m docatlas show K<id>`，每组约 2–3 秒返回。
- 数据和发行说明知识块本身可读取，问题集中在宽泛 `ask` 查询路径。

## 期望结果

- 用户能够在合理时间内得到结果，或明确知道查询仍在进行、停在哪个阶段。
- 分类选错、没有命中和真正的长时间计算能够被区分。
- `--no-fetch` 场景的性能问题能够被定位，而不是只表现为外部超时。

## 可能方向

下面仅供排查和设计时参考：

- 在无候选或分类不匹配时更早结束。
- 为较长查询提供阶段性状态、耗时记录或内部诊断。
- 评估是否需要可配置的超时、取消或部分结果。

具体采用哪种方式，应结合延迟实际发生的阶段和 CLI/MCP 的共同需求决定。

## 临时绕行

先用 `search` 定位发行说明知识块，再用 `show` 分段读取和整理。

## 调查记录

目前只确认上述现象，尚未定位具体代码原因。
