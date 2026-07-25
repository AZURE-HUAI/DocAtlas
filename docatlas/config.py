"""常量、路径与分类定义。整个知识库唯一的配置来源。"""

from __future__ import annotations

import os
from pathlib import Path
import re


VERSION = "5.8"
LANGUAGE = "en-US"
# 切块规则的版本号。规则一改就要 +1，chunks.parser_version 记录每块由哪版产出，
# 中途换规则时才分得清哪些块是旧的、需要重切。
CHUNKER_VERSION = "v1"
USER_AGENT = "UE58OfflineDocs/1.0 (+local educational archive)"
SITEMAP_INDEX_URL = "https://dev.epicgames.com/documentation/sitemap.xml"
DOCUMENT_API_URL = (
    "https://dev.epicgames.com/community/api/documentation/document.json"
)
DOC_PREFIX = "/documentation/unreal-engine/"

# 代码根：本包的上一级，也就是 Git 仓库根。放程序，不放数据。
REPO_ROOT = Path(__file__).resolve().parent.parent

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

CATEGORY_PATTERNS = {
    "guides": "/unreal_engine/external/",
    "community_docs": "/unreal_engine/epic_developer_community/",
    "blueprint_api": "/unreal_engine/ue_blueprint_api_external/",
    "cpp_api": "/unreal_engine/ue_cpp_api_external/",
    "python_api": "/unreal_engine/ue_python_api_external/",
    "node_reference": "/unreal_engine/ue_noderef_api_external/",
}

CATEGORY_LABELS = {
    "guides": "教程与功能文档",
    "community_docs": "Epic 社区维护文档",
    "blueprint_api": "蓝图 API",
    "cpp_api": "C++ API",
    "python_api": "Python API",
    "node_reference": "节点参考",
}

RETRYABLE_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".avif",
}
URL_RE = re.compile(r"https?://[^\s\"'<>\\)]+", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
MARKDOWN_TARGET_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\((https?://[^)\s]+)(?:\s+\"[^\"]*\")?\)",
    re.IGNORECASE,
)
MARKDOWN_MARKUP_RE = re.compile(r"[`*_>#|~]+")
WHITESPACE_RE = re.compile(r"[ \t]+")
KNOWLEDGE_TYPE_RULES = (
    ("parameters", re.compile(r"\b(inputs?|parameters?|arguments?|properties)\b", re.I)),
    ("returns", re.compile(r"\b(outputs?|returns?|return value|results?)\b", re.I)),
    ("examples", re.compile(r"\b(examples?|usage|how to use|walkthrough)\b", re.I)),
    ("remarks", re.compile(r"\b(remarks?|notes?|cautions?|warnings?|limitations?|considerations?)\b", re.I)),
    ("signature", re.compile(r"\b(syntax|declaration|definition|signature|header|include)\b", re.I)),
    ("navigation", re.compile(r"\b(navigation|breadcrumbs?|hierarchy)\b", re.I)),
    ("references", re.compile(r"\b(related|references?|see also|prerequisites?)\b", re.I)),
)
ENTITY_TYPES = {
    "guides": "guide",
    "community_docs": "document",
    "blueprint_api": "blueprint_node",
    "cpp_api": "cpp_symbol",
    "python_api": "python_api",
    "node_reference": "editor_node",
}
