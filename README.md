# DocAtlas

把官方技术文档抓到本地，切成带出处的知识块，让 AI 查得又快又准。

> AI 回答技术问题时，要么凭记忆瞎编，要么把整页文档塞进上下文然后没预算干正事。
> DocAtlas 让它拿到**刚好够用的那几段**，每段都能追溯到官方页面。

## 能做什么

- **答案带出处** —— 每一条都附原始 URL，可以点开核对，不用猜是不是编的。
- **离线可查** —— 文档存在本机，断网也能用。
- **省 token** —— 按预算给最相关的几段，不是把整页塞进去。
- **接进 AI** —— 一条命令装好 Skill 和 MCP，Claude Code、Codex、Cursor 等都能用。
- **不限产品** —— 已内置 Unreal Engine 5.8、cppreference、Blender Manual、
  Roblox Creator Hub；加别的站点写一个适配器就行，核心代码不用动。

## 安装

需要 Python 3.11+，**不装任何第三方包**。程序放哪都行，不用装进 Python 环境。

```bash
git clone https://github.com/AZURE-HUAI/DocAtlas.git
cd DocAtlas
python install.py
```

`install.py` 会装好 Skill、注册 MCP，并且真起一次服务器确认连得上——没通过就
不写任何配置。数据想放别的盘（程序和数据可以分开）：

```bash
python install.py --data-dir D:/DocAtlasData
```

不认识的客户端用 `python install.py --print` 打印配置片段，自己粘过去。

## 开始用

```powershell
python -m docatlas crawl --discovery-only   # 枚举全站页面清单，几十分钟
python -m docatlas ask "Nanite"             # 然后就能查了
```

清单里记着每一页在哪，所以本地没有的页面 `ask` 会当场抓回来——
**不需要先把二十万页全下载下来**。想要完全离线：

```powershell
python -m docatlas crawl --skip-discovery   # 抓全部正文，随时可中断，自动续传
.\docatlas.ps1 start                        # Windows 上还可以丢到后台跑
```

装了 Skill 或 MCP 之后，在 Claude Code 之类的客户端里直接问就行，AI 会自己查库、
自己带上出处，不用你敲命令。

常用命令，都写作 `python -m docatlas <命令>`：

| 命令 | 干嘛 |
|---|---|
| `ask "Nanite"` | 给出整理好的答案材料（最常用） |
| `search "Set Timer"` | 只列标题和出处，不展开正文 |
| `show K9290` | 展开某一条知识的完整内容 |
| `related "ACharacter"` | 看相关的类、节点、API 之间的对应关系 |
| `stats` | 看抓取进度和覆盖率 |

## 更多

- [使用手册](docs/USAGE.md) —— 全部命令、抓取、数据结构、加新数据集
- [代码架构](docs/ARCHITECTURE.md) —— 三层分工与各模块职责
- [数据合同](docs/DATA_CONTRACT.md) —— 数据结构与字段约定
- [AI 检索规则](docs/AI_ROUTING.md) —— 检索策略与上下文预算
- [问题记录](issues/README.md) —— 已知问题、复现证据与解决归档
- [参与开发](CONTRIBUTING.md) —— 分支、测试、PR 要求

```powershell
python -m unittest discover -s tests    # 299 个用例，离线，不碰真实数据库
```
