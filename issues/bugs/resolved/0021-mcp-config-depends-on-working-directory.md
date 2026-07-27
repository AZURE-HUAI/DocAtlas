---
id: BUG-021
title: "MCP 启动方式依赖工作目录，README 给的 cwd 写法在 Claude Code 上静默失效"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: install
labels: [mcp, install, portability]
reported_at: 2026-07-28
resolved_at: 2026-07-28
github_issue: null
fix_pr: null
related: [ENH-002]
---

# 问题

用户按 README 的示范给 MCP 配置加上 `cwd`，Claude Code 连接依然失败，报错停在
`No module named docatlas`。

DocAtlas 不是装进 Python 环境的包，就是一个普通文件夹，所以 `python -m docatlas mcp`
只有在仓库目录下才找得到它。README 用 `cwd` 解决这件事——但 **`cwd` 是各家客户端
自己决定支不支持的字段**，不是 MCP 协议的一部分。

实测两个客户端，结论相反：

| 客户端 | 支持 `cwd` | 证据 |
|---|---|---|
| Codex | 支持 | `~/.codex/config.toml` 里 `[mcp_servers.node_repl]` 等条目本来就在用 |
| Claude Code | **不支持** | `claude mcp add --help` 没有 `--cwd`；写出的条目只有 `type` / `command` / `args` / `env` |

README 那段大概是照着 Codex 验的，搬到 Claude Code 就失效——而且失效得很安静：
配置本身是合法 JSON，客户端照常起进程，只是进程找不到包就退了。

## 复现

```console
$ cd C:/Users/HUAI            # 任意非仓库目录
$ python -m docatlas paths
C:/msys64/ucrt64/bin/python.exe: No module named docatlas
```

`claude mcp add docatlas -s project -- <python> <repo>/mcp_server.py` 写出的
`.mcp.json` 证明了 Claude Code 的 stdio schema：

```json
{"mcpServers": {"docatlas": {
  "type": "stdio", "command": "…python.exe", "args": ["…"], "env": {}}}}
```

没有 `cwd` 这一项。

## 期望结果

一条启动命令，在所有 MCP 客户端上都成立，不依赖工作目录、不依赖环境变量、
也不需要 `pip install`。

## 根因定位

启动方式（`python -m docatlas`）要求包在 `sys.path` 上，而**谁来保证这一点**被
推给了客户端配置。客户端各家不一样，这个保证就不成立。

## 解决记录

1. **新增仓库根的 `mcp_server.py` 当入口。** Python 启动一个脚本时会把**脚本所在
   目录**放进 `sys.path[0]`，也就是仓库根，于是 `import docatlas` 从任何工作目录
   都成立。配置里只要给出这个文件的绝对路径，`cwd` / `PYTHONPATH` / 安装一个都
   不需要。这不是变通，是 Python 自己的行为，所有客户端一视同仁。

2. **服务器容忍开头的 BOM，并且不再静默丢弃坏行。** 排查过程中发现：PowerShell 5.1
   一取 `StandardInput` 就会把 UTF-8 preamble 冲进管道，于是第一条请求变成
   `﻿{"jsonrpc":…}`，`json.loads` 解不开。原来的 `serve()` 对坏行是**一声不吭地
   continue**——客户端那头看到的是"进程起来了却永远不回话"，没有任何线索可查。
   现在开头的 BOM 会被去掉，真解不开的行会往 stderr 写一句（stdout 归协议独占）。

3. **新增 `install.py`，取代两个 PowerShell 安装脚本。** 用 Python 写是因为
   DocAtlas 本来就要 Python，用它当安装器就自动跨平台，也不用为每个系统各维护一份。
   它做四件事：定数据位置、验证握手、注册 MCP、装技能。

   - **先验证，再写配置**：注册前先从一个陌生目录把服务器真起一次、走完
     `initialize`，通不过就什么都不写。第一版把写配置放在了验证前面，一次失败就
     在用户的配置里留下半截条目——比没装还难查。
   - **不代改别人的现成配置**：Claude Code 交给官方的 `claude mcp add`；Codex 只在
     没有该条目时**追加**（TOML 里已有同名段落再追加会直接让它解析失败）；其余
     客户端只打印片段。开发中曾试过解析并重写 `claude_desktop_config.json`，一次
     PowerShell 的 JSON 往返就把条目写成了字符串 `"@{command=…; args=System.Object[]}"`
     ——几千行的第三方配置不该由安装器重写，这条路已经整个去掉。

4. **数据位置可以和程序分开。** 原本只认 `DOCATLAS_HOME` 环境变量，而 **MCP 客户端
   起子进程时不会带上你终端里的环境变量**——只靠它的话，命令行查得到的库、MCP
   查不到。现在 `runtime._data_root()` 的优先级是：环境变量 > 安装时写下的
   `.docatlas-home` > 仓库里的 `data/`。环境变量仍排最前，是为了临时换一个库跑
   一次不用改文件。

## 验证

**协议层**：新进程从 `C:/Users/HUAI` 启动、显式剔除 `PYTHONPATH`，走 stdin/stdout
真实 JSON-RPC，`initialize` + `tools/list` + 三个工具调用逐条断言，5/5 通过。

**注册**：`claude mcp list` 显示
`docatlas: …python.exe …/mcp_server.py - ✔ Connected`。

**数据位置**：`--data-dir` 指到临时盘后 `python -m docatlas paths` 跟着变，
`DOCATLAS_HOME` 能盖过它，还原后回到 `<仓库>/data`。

回归测试见 `McpProtocolTests`（BOM 与坏行提示）和 `DataRootTests`（三级优先级，
含"空的指针文件不能解成仓库根本身"——那会把数据倒进代码目录）。

## 外部关联

- GitHub Issue：
- 修复 PR：
