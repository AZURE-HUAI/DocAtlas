"""Shortcut names for the process's default dataset.

The actual runtime lives in `runtime.py`: a `Workspace` bundles the config, the
source adapter, the knowledge pack and the data directory, and `runtime.active()`
returns whichever one is currently in effect.

This module is only a shortcut to **the default one**, for the CLI. The CLI
serves a single dataset per process, so switching library means setting
`DOCATLAS_DATASET` and running again — simpler than keeping the whole codebase
ready to switch at any moment.

Retrieval paths (db / search / context / ondemand / relations ...) must **not**
read values from here; they go through `runtime.active()`. MCP switches datasets
inside one process, while anything read from here was fixed at startup and will
not follow the switch.

No specific product or site name belongs in this file.
"""

from __future__ import annotations

from .constants import (  # noqa: F401  (kept for older `from .config import ...`)
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

# One dataset = one version of one product, owning its own directory and its own
# database. Deliberately not merged into one big library: deletion, backup and
# fault isolation are all far simpler kept apart.
#
# Every value below derives from "the dataset currently in effect", so it is
# computed **on demand** rather than at import time. Computed at import, a
# process that has not chosen a dataset yet could not even `import docatlas.net`
# — and the program ships no built-in default library (what to install is the
# user's choice), while the MCP server may well run with no default at all, each
# call carrying its own `dataset_id`.
_FROM_WORKSPACE = {
    "DATASET_ID": lambda default: default.id,
    "DATA_DIR": lambda default: default.data_dir,
    "DB_PATH": lambda default: default.db_path,
    "EXPORT_DIR": lambda default: default.export_dir,
    "ASSET_DIR": lambda default: default.asset_dir,
    "DATASET": lambda default: default.dataset,
    "SOURCE": lambda default: default.source,
    "KNOWLEDGE": lambda default: default.knowledge,
    "VERSION": lambda default: default.version,
    "LANGUAGE": lambda default: default.language,
    # Every category a page may carry. Uses query_categories rather than
    # categories: the latter is the "category -> path prefix" enumeration rule,
    # and the category collected by the reference closure has no prefix to put
    # there, yet its pages are stored all the same. CLI options, sampling
    # quotas, exports and reports all want "categories that can be stored".
    "CATEGORY_IDS": lambda default: default.dataset.query_categories,
    "CATEGORY_LABELS": lambda default: default.category_labels,
    "ENTITY_TYPES": lambda default: default.dataset.entity_types,
    # Path prefix: what makes a path count as a document of this dataset.
    "DOC_PREFIX": lambda default: default.doc_prefix,
}


def __getattr__(name: str):
    if name in _FROM_WORKSPACE:
        return _FROM_WORKSPACE[name](active())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
