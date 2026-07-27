# DocAtlas

给 AI 用的本地文档知识库。把官方技术文档抓下来切成小块、建好索引和交叉关系，
AI 查的时候拿到**刚好够用的那几段**，每段都带原始 URL。

解决的是这个问题：AI 回答技术问题，要么凭记忆瞎编，要么把整页文档塞进上下文
然后没预算干正事。

仓库里只有程序。已经写好了四个站点的适配器和配置——Unreal Engine 5.8、
cppreference、Blender Manual、Roblox Creator Hub——**文档数据要自己抓**（见下）。
加别的站点写一个适配器就行，核心代码不用动。

## 安装

需要 Python 3.11+，不装任何第三方包。

```bash
git clone https://github.com/AZURE-HUAI/DocAtlas.git
cd DocAtlas
python install.py
```

`install.py` 装好 Skill、注册 MCP，并且真起一次服务器确认连得上——没通过就不写
任何配置。Claude Code 和 Codex 会自动注册，其他客户端用 `--print` 打印配置片段
自己粘。数据想放别的盘：`--data-dir D:/DocAtlasData`。

## 建库

```bash
python -m docatlas crawl --discovery-only    # 枚举全站页面清单，几十分钟
```

**做完这一步就能用了。** 清单记着每一页在哪，所以本地还没有的页面，AI 问到时会
当场抓回来——不需要先把二十万页全下载下来。想要全量：

```bash
python -m docatlas crawl --skip-discovery    # 随时可中断，下次自动续传
```

默认建的是 UE 5.8。换别的：先设 `DOCATLAS_DATASET=cppreference-2026-07-26`，
再照上面跑。四个库互不干扰，删掉一个不影响另一个。

## 用

在 Claude Code 之类的客户端里直接问就行，AI 会自己查库、自己带上出处。
也可以自己敲：`python -m docatlas ask "Nanite"`。

## 更多

- [使用手册](docs/USAGE.md) —— 全部命令、抓取、数据结构、加新数据集
- [代码架构](docs/ARCHITECTURE.md) · [数据合同](docs/DATA_CONTRACT.md) · [AI 检索规则](docs/AI_ROUTING.md)
- [问题记录](issues/README.md) · [参与开发](CONTRIBUTING.md)
