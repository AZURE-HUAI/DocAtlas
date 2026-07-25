"""常量、路径与分类定义。整个知识库唯一的配置来源。"""

from __future__ import annotations

import os
from pathlib import Path
import re


VERSION = "5.8"
LANGUAGE = "en-US"
USER_AGENT = "UE58OfflineDocs/1.0 (+local educational archive)"
SITEMAP_INDEX_URL = "https://dev.epicgames.com/documentation/sitemap.xml"
DOCUMENT_API_URL = (
    "https://dev.epicgames.com/community/api/documentation/document.json"
)
DOC_PREFIX = "/documentation/unreal-engine/"

# 知识库根目录：数据库、导出、图片、报告都放在这里。
# 默认是本包的上一级（也就是 5.8.0 目录），可用环境变量 UE_KB_HOME 覆盖。
DATA_DIR = Path(
    os.environ.get("UE_KB_HOME") or Path(__file__).resolve().parent.parent
).resolve()
SCRIPT_DIR = DATA_DIR  # 兼容旧名称
DB_PATH = DATA_DIR / "ue58_docs.sqlite3"
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
