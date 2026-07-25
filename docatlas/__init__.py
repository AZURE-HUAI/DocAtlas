"""DocAtlas —— 本地技术文档知识库。

三层分工（见 docs/ARCHITECTURE.md）：

    核心 docatlas/*.py       通用能力，不认识任何具体网站或技术领域
    sources/<name>.py        懂一个文档站：怎么列页、怎么解析
    knowledge/<name>.py      懂一个技术领域的行话（可选）

核心内部的依赖方向：constants/text → dataset → config → util → net → db →
discover/htmlmd → chunking → documents → store → crawl/assets/ondemand →
crossindex → search/context → export/reports/validate → cli/mcpserver。
"""

from .config import VERSION

__all__ = ["VERSION", "main"]


def main() -> int:
    from .cli import main as _main

    return _main()
