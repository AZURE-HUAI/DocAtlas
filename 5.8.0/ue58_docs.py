#!/usr/bin/env python3
"""UE 5.8 官方文档本地知识库 —— 兼容入口。

真正的实现已经拆分到 `ue_kb/` 包里。这个文件只负责把命令转过去，
让所有已有的脚本和习惯用法（`python ue58_docs.py search ...`）继续可用。

等价写法：`python -m ue_kb search ...`
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ue_kb.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
