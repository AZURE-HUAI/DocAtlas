"""路径解析，以及当前生效的数据集、来源适配器、领域知识包。

站点特有的东西（网址、分类规则、实体类型）都搬进 datasets/*.toml 和
docatlas/sources/ 了；跟站点无关的常量在 constants.py。
这个文件里再也不该出现任何具体产品或网站的名字。
"""

from __future__ import annotations

import os
from pathlib import Path

from .constants import (  # noqa: F401  （给老的 from .config import … 留着）
    CHUNKER_VERSION,
    HEADING_RE,
    IMAGE_EXTENSIONS,
    KNOWLEDGE_TYPE_RULES,
    MARKDOWN_LINK_RE,
    MARKDOWN_MARKUP_RE,
    MARKDOWN_TARGET_RE,
    RETRYABLE_HTTP_CODES,
    URL_RE,
    USER_AGENT,
    WHITESPACE_RE,
)
from .dataset import load_dataset, load_knowledge, load_source


# 代码根：本包的上一级，也就是 Git 仓库根。放程序，不放数据。
REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_CONFIG_DIR = REPO_ROOT / "datasets"

# 数据根：所有数据集的家。默认 <仓库>/data，可用 DOCATLAS_HOME 挪到别的盘。
# 代码目录和数据目录从此各归各的，加一个新版本不需要复制一份程序。
DATA_ROOT = Path(
    os.environ.get("DOCATLAS_HOME") or REPO_ROOT / "data"
).resolve()

# 一个数据集 = 一个产品的一个版本，独占一个目录、一个数据库。
# 不合并成大库：删除、备份、出问题时的隔离都简单得多。
DATASET_ID = os.environ.get("DOCATLAS_DATASET") or "epic-ue-5.8"
DATA_DIR = DATA_ROOT / DATASET_ID
DB_PATH = DATA_DIR / "knowledge.sqlite3"
EXPORT_DIR = DATA_DIR / "exports"
ASSET_DIR = DATA_DIR / "assets"

# 一个进程只服务一个数据集，在这里定下来。要查别的数据集就换 DOCATLAS_DATASET
# 再跑一次——比让全套代码随时准备切换简单得多，也不会有状态串味的问题。
DATASET = load_dataset(DATASET_ID, DATASET_CONFIG_DIR)
SOURCE = load_source(DATASET)
KNOWLEDGE = load_knowledge(DATASET)

VERSION = DATASET.version
LANGUAGE = DATASET.language
CATEGORY_PATTERNS = DATASET.categories
CATEGORY_LABELS = DATASET.category_labels
ENTITY_TYPES = DATASET.entity_types
# 路径前缀：什么样的路径才算"这个数据集的一篇文档"。
DOC_PREFIX = DATASET.option("doc_prefix", "/")
