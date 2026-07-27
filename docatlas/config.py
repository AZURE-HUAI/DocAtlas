"""进程默认数据集的快捷名字。

真正的运行时在 `runtime.py`：一个 `Workspace` 把配置、来源适配器、领域知识包
和数据目录捆在一起，`runtime.active()` 给出当前生效的那一个。

这个文件只是**默认那一个**的快捷方式，给命令行用——命令行一个进程只服务一个
数据集，换库就换 `DOCATLAS_DATASET` 再跑一次，比让全套代码随时准备切换简单。

检索路径（db / search / context / ondemand / relations …）**不要**从这里取值，
要走 `runtime.active()`：MCP 会在一个进程里切换数据集，从这里取到的是启动时
定下的那一个，切了也不会变。这个文件里再也不该出现任何具体产品或网站的名字。
"""

from __future__ import annotations

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
from .runtime import (  # noqa: F401
    DATA_ROOT,
    DATASET_CONFIG_DIR,
    REPO_ROOT,
    active,
)

_DEFAULT = active()

# 一个数据集 = 一个产品的一个版本，独占一个目录、一个数据库。
# 不合并成大库：删除、备份、出问题时的隔离都简单得多。
DATASET_ID = _DEFAULT.id
DATA_DIR = _DEFAULT.data_dir
DB_PATH = _DEFAULT.db_path
EXPORT_DIR = _DEFAULT.export_dir
ASSET_DIR = _DEFAULT.asset_dir

DATASET = _DEFAULT.dataset
SOURCE = _DEFAULT.source
KNOWLEDGE = _DEFAULT.knowledge

VERSION = _DEFAULT.version
LANGUAGE = _DEFAULT.language
# 页面可能带的分类全集。用的是 query_categories 而不是 categories：后者是
# "分类 → 路径前缀"的枚举规则，引用闭包收进来的那一类没有前缀，写不进去，
# 但它的页面照样落库。命令行选项、抽样配额、导出和报表要的都是"能落库的分类"。
CATEGORY_IDS = _DEFAULT.dataset.query_categories
CATEGORY_LABELS = _DEFAULT.category_labels
ENTITY_TYPES = _DEFAULT.dataset.entity_types
# 路径前缀：什么样的路径才算"这个数据集的一篇文档"。
DOC_PREFIX = _DEFAULT.doc_prefix
