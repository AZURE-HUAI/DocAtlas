---
id: ENH-001
title: "Skill 安装器支持多个客户端"
type: enhancement
status: open
lifecycle: unresolved
priority: medium
area: installer
labels: [skill, installer, codex, claude-code, windows]
reported_at: 2026-07-26
resolved_at: null
github_issue: null
fix_pr: null
related: []
---

# 背景

当前 `scripts/install-skill.ps1` 只安装到 Claude Code。手动安装到 Codex 时，
Windows PowerShell 写出的 UTF-8 BOM 会导致 Codex 无法识别 `SKILL.md`。

## 目标

降低不同 AI 客户端安装和更新 DocAtlas Skill 时的手工差异，并在安装后确认
客户端能够识别技能。

## 可能方向

- 支持 Codex、Claude Code 等常见客户端的路径探测。
- 统一完成模板渲染。
- 使用 UTF-8（无 BOM）写入。
- 安装后执行最小识别验证。

也可以选择保留按客户端拆分的薄入口，只共享编码和模板逻辑。具体支持范围、
参数和探测方式由实现时根据客户端行为与维护成本决定。

## 验证思路

- 在隔离的临时目录分别执行 Codex 与 Claude Code 安装，确认文件落入预期位置。
- 检查生成的 `SKILL.md` 使用 UTF-8 且没有 BOM，并能被对应客户端识别。
- 重复执行安装或更新，确认不会产生重复内容或破坏已有有效配置。

## 非目标

本议题不要求立即支持所有 AI 客户端，也不要求引入完整安装框架。
