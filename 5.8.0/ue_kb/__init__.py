"""UE 5.8 官方文档本地知识库。

分层：config → util → net → db → discover → htmlmd → chunking →
documents → store → crawl/assets → crossindex → search/context →
export/reports/validate → cli。每层只依赖它上面的层。
"""

from .config import VERSION

__all__ = ["VERSION", "main"]


def main() -> int:
    from .cli import main as _main

    return _main()
