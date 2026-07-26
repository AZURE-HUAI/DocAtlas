---
id: ENH-001
title: "Skill 安装器支持多个客户端"
type: enhancement
status: in_progress
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

## 验证

```powershell
.\scripts\install-skill.ps1 -Target C:\Users\HUAI\AppData\Local\Temp\…\skill-test
```

在隔离临时目录验证：

- 两个文件（`SKILL.md`、`WORKFLOWS.md`）都落地。
- 首三字节不是 `EF BB BF`（**无 BOM**）。
- 文件里没有残留的 `{{占位符}}`。
- `SKILL.md` 以 `---` 开头（frontmatter 完好）。
- 重复执行两次，内容逐字节一致，不产生重复段落。

检测模式（不带参数）在本机同时命中 `~/.claude` 与 `~/.codex`，
两边各装一份；`-Client codex` 只装一份。

上述四项检查**写在脚本里**，安装后立即执行，任何一项不满足就 `throw`——
"命令跑完没报错"不等于客户端认得出这个技能。

## 解决记录

**根因两条**：安装目标写死成 Claude Code；写文件用了
`Set-Content -Encoding UTF8`，Windows PowerShell 5.1 的这个组合会写出 BOM，
Codex 读到 BOM 就解析不出 `SKILL.md` 的 frontmatter。

**改动**（`scripts/install-skill.ps1`）：

- 客户端表 `$ClientHomes`：`claude-code` → `~/.claude`，`codex` → `~/.codex`
  （两者都是 `<家目录>/skills/<技能名>/`）。加新客户端就加一行，别的逻辑不动。
- 只装到**真实存在**的客户端目录，不凭空创建。
- `-Client` 选一个、`-Target` 指定任意目录（测试和不常见客户端用）。
- `Write-Utf8NoBom()` 用 `[System.IO.File]::WriteAllText` + 不带 BOM 的
  `UTF8Encoding`，绕开 PowerShell 5.1 的默认行为。
- 安装后自检：文件非空、无 BOM、无残留占位符、frontmatter 完好。

模板渲染仍然全部交给 `python -m docatlas render-skill`——只有 Python 认识数据集。
换个平台重写一个安装脚本时，不需要把数据集那套知识再抄一遍。

**非目标照旧**：没有引入安装框架，没有支持"所有" AI 客户端。

## 外部关联

- GitHub Issue：
- 修复 PR：
