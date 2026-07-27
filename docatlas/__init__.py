"""DocAtlas —— 本地技术文档知识库。

三层分工（见 docs/ARCHITECTURE.md）：

    核心 docatlas/*.py       通用能力，不认识任何具体网站或技术领域
    sources/<name>.py        懂一个文档站：怎么列页、怎么解析
    knowledge/<name>.py      懂一个技术领域的行话（可选）

核心内部的依赖方向：constants/text → dataset → runtime → config → util → net
→ db → discover/htmlmd → chunking → documents → store → relations →
crawl/assets/ondemand → search/context → export/reports/validate →
cli/mcpserver。

**这个文件不做任何事。** 曾经它 `from .config import VERSION`，于是一句
`import docatlas` 就会读 toml、import 来源适配器和领域知识包（实测 14 个模块、
0.11 秒），而且默认数据集配置一坏，整个包连 import 都失败——偏偏
`docatlas_list_datasets` 的全部意义就是"配置坏了也要能列出来告诉你哪坏了"。
入口一律晚绑定：真要用哪个模块的时候再 import 它。
"""


def main() -> int:
    from .cli import main as _main

    return _main()


__all__ = ["main"]
