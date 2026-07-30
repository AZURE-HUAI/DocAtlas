"""Runtime for the dataset currently in effect.

A dataset is not an id but four things that must match: its config
(`datasets/*.toml`), its source adapter (how to list pages, how to parse bodies),
its knowledge pack (optional; what justifies saying two things are related) and
its data directory (where the library files live). Any mix-and-match of the four
is wrong — parsing one site's pages with another's adapter, or writing one site's
bodies into another's library, raises no error and quietly corrupts data. So they
are bundled into a `Workspace` and only ever swapped as a set.

Bundling them also solves something more important: **one process can hold
several datasets at once.** Choosing one at import time and forbidding changes
would confine an MCP server to a single library for its whole life, forcing a
second server entry in the client config for a second library — which in practice
nobody adds.

`active()` returns the current one; `use()` switches temporarily for a block of
code. Switching goes through `contextvars` rather than a plain global, so
concurrent calls each see their own dataset without bleeding into each other.

Derived config (category priority, concept bonuses, identifier shape, relation
labels) hangs off the Workspace instead of being module-level constants. A
module-level constant is computed once at import, which is exactly what breaks
with several datasets: after a switch, the constant still describes the previous
library.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
import functools
from functools import cached_property, lru_cache
import os
from pathlib import Path
import re
import tomllib
from types import ModuleType
from typing import Any, Callable, Iterator

from .dataset import (
    Dataset,
    knowledge_hook,
    load_dataset,
    load_knowledge,
    load_source,
)


# Code root: this package's parent, i.e. the git repository root. Holds the
# program, never data.
REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_CONFIG_DIR = REPO_ROOT / "datasets"

# The two things installation settles: where data lives, and which library is the
# default. These must land in a file — an MCP client starting a subprocess does not
# inherit the environment variables from your terminal, so relying on environment
# alone gives a library the CLI can find and MCP cannot. One per machine, not
# committed.
LOCAL_SETTINGS = REPO_ROOT / ".docatlas-local.toml"


def local_settings() -> dict[str, Any]:
    """Read the choices written at install time. Say so if unreadable; never fall
    back silently.

    This file is written by the installer, so a broken one means either a bad hand
    edit or an installer bug. Both deserve to be seen: silently reverting to a
    different library leaves someone puzzling over answers from the wrong one.
    """
    if not LOCAL_SETTINGS.exists():
        return {}
    return tomllib.loads(LOCAL_SETTINGS.read_text(encoding="utf-8"))


def _data_root() -> Path:
    """Data root: home to every dataset.

    Precedence is environment variable > install-time choice > `data/` in the
    repository. The environment comes first so a one-off relocation needs no file
    edits.
    """
    chosen = os.environ.get("DOCATLAS_HOME") or local_settings().get("home")
    return Path(chosen).resolve() if chosen else (REPO_ROOT / "data").resolve()


DATA_ROOT = _data_root()


class DatasetNotChosen(RuntimeError):
    """No dataset was chosen, and this machine has more than one to choose from."""


def default_dataset_id() -> str:
    """Which dataset to use when none was named explicitly.

    **There is no built-in default.** Which libraries to install is the user's
    choice and the program will not pick one for them: hardcoding a product means
    someone who built a different library first has to discover they were defaulted
    elsewhere, and that mistake merely looks like "not found". With exactly one
    library configured there is nothing to ask about.
    """
    chosen = os.environ.get("DOCATLAS_DATASET") or local_settings().get("dataset")
    if chosen:
        return str(chosen)
    if len(available := available_dataset_ids()) == 1:
        return available[0]
    raise DatasetNotChosen(
        "No dataset was specified. "
        + (
            f"Available here: {', '.join(available)}. "
            if available
            else "There are no configs under datasets/. "
        )
        + "Set DOCATLAS_DATASET for one run, or settle it with "
          "python install.py --dataset <id>."
    )


# What counts as an "identifier". The generic rule recognizes `::`, underscores
# and camel case; a knowledge pack may add shapes of its own.
DEFAULT_IDENTIFIER_PATTERN = r"::|_[A-Za-z]|[a-z][A-Z]"

# Human-readable names for the generic relations. A knowledge pack may add its own.
CORE_RELATION_LABELS = {
    "belongs_to": "belongs to",
    "parameter_type": "parameter type",
    "return_type": "return type",
    "signature_reference": "referenced in signature",
    "example_reference": "referenced in example",
    "official_reference": "related official document",
}

CORE_EVIDENCE_LABELS = {
    "official_link": "official documentation link",
    "page_member_table": "member table on the official page",
}

# Ordering of related items: domain-specific relations (a correspondence backed by
# hard evidence) come before generic ones, so the generic numbers are larger.
CORE_RELATION_PRIORITY = {
    "parameter_type": 3,
    "return_type": 4,
    "belongs_to": 7,
}


def sql_priority_case(column: str, ranks: dict[str, int], default: int | None = None) -> str:
    """Compile a "what comes first" mapping into a SQL CASE. Lower sorts earlier.

    The names come from dataset config and knowledge packs, i.e. **third-party
    files**. DocAtlas is meant to be installed by strangers, so someone else's
    `datasets/*.toml` cannot be treated as trusted input. Interpolated straight
    into SQL, a category name containing a quote would at best break every query
    with a syntax error and at worst rewrite the whole statement. So single quotes
    are doubled per the SQL standard, and every rank goes through `int()`.

    The escaping lives in this one place: duplicated, each copy has to remember to
    escape, and missing one copy is the same as not doing it at all.
    """
    if not ranks:
        return "0"
    branches = " ".join(
        "WHEN '{}' THEN {:d}".format(name.replace("'", "''"), int(rank))
        for name, rank in sorted(ranks.items(), key=lambda item: item[1])
    )
    fallback = max(ranks.values()) + 1 if default is None else default
    return f"CASE {column} {branches} ELSE {int(fallback):d} END"


@dataclass(frozen=True)
class Workspace:
    """One dataset's complete runtime. Immutable: swap the object, not its parts."""

    dataset: Dataset
    source: ModuleType
    knowledge: ModuleType | None
    data_dir: Path

    # ---- identity ------------------------------------------------------

    @property
    def id(self) -> str:
        return self.dataset.id

    @property
    def name(self) -> str:
        return self.dataset.name

    @property
    def version(self) -> str:
        return self.dataset.version

    @property
    def language(self) -> str:
        return self.dataset.language

    @property
    def category_labels(self) -> dict[str, str]:
        return self.dataset.category_labels

    @property
    def doc_prefix(self) -> str:
        """What makes a path count as a document of this dataset."""
        return self.dataset.option("doc_prefix", "/")

    # ---- paths ---------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "knowledge.sqlite3"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def asset_dir(self) -> Path:
        return self.data_dir / "assets"

    # ---- knowledge pack ------------------------------------------------

    def hook(self, name: str, default: Any = None) -> Any:
        """Look up a capability by name from the knowledge pack, else the default."""
        return knowledge_hook(self.knowledge, name, default)

    def extension(self, name: str, default: Any = None) -> Any:
        """Look up a domain capability: knowledge pack first, then source adapter.

        Some capabilities belong naturally to the adapter rather than a knowledge
        pack: a site's bracket convention for marking versions is the same kind of
        knowledge as how to parse that site's pages, and there is no reason to
        attach a whole knowledge pack for it. Semantics that hold across a domain
        regardless of site belong to the knowledge pack. Both are allowed, with the
        pack winning: it is attached per dataset and so is the more specific of the
        two.
        """
        found = knowledge_hook(self.knowledge, name, None)
        if found is None:
            found = getattr(self.source, name, None)
        return default if found is None else found

    # ---- derived config (computed once per dataset, then cached) --------

    @cached_property
    def category_priority(self) -> dict[str, int]:
        """Which category to fetch first among same-named candidates. Lower wins."""
        return self.dataset.category_priority

    @cached_property
    def concept_category_bonus(self) -> dict[str, float]:
        """Per-category adjustment for concept questions. Empty is fine and works."""
        return self.dataset.concept_category_bonus

    @cached_property
    def identifier_re(self) -> re.Pattern[str]:
        return re.compile(self.hook("IDENTIFIER_PATTERN", DEFAULT_IDENTIFIER_PATTERN))

    @cached_property
    def relation_labels(self) -> dict[str, str]:
        return {**CORE_RELATION_LABELS, **self.hook("RELATION_LABELS", {})}

    @cached_property
    def evidence_labels(self) -> dict[str, str]:
        return {**CORE_EVIDENCE_LABELS, **self.hook("EVIDENCE_LABELS", {})}

    @cached_property
    def relation_priority(self) -> dict[str, int]:
        return {**self.hook("RELATION_PRIORITY", {}), **CORE_RELATION_PRIORITY}

    @cached_property
    def relation_priority_sql(self) -> str:
        """SQL fragment ordering relations: domain-specific ones before generic."""
        return sql_priority_case("r.relation_type", self.relation_priority, default=8)

    @cached_property
    def category_priority_sql(self) -> str:
        """SQL fragment for which category of same-named candidate comes first."""
        return sql_priority_case("category", self.category_priority)


@lru_cache(maxsize=8)
def workspace(dataset_id: str) -> Workspace:
    """Assemble a dataset runtime by id.

    Cached: assembling reads a toml and imports two modules, while MCP calls for
    the same dataset repeatedly. What is cached is immutable, so there is no state
    to bleed between callers.
    """
    dataset = load_dataset(dataset_id, DATASET_CONFIG_DIR)
    return Workspace(
        dataset=dataset,
        source=load_source(dataset),
        knowledge=load_knowledge(dataset),
        data_dir=DATA_ROOT / dataset_id,
    )


def available_dataset_ids() -> list[str]:
    return sorted(path.stem for path in DATASET_CONFIG_DIR.glob("*.toml"))


_active: contextvars.ContextVar[Workspace] = contextvars.ContextVar("docatlas_workspace")


def active() -> Workspace:
    """The current one. Absent an explicit switch, whatever DOCATLAS_DATASET names."""
    try:
        return _active.get()
    except LookupError:
        default = workspace(default_dataset_id())
        _active.set(default)
        return default


@contextlib.contextmanager
def use(target: Workspace | str) -> Iterator[Workspace]:
    """Switch dataset for this block. Always restored on exit, exceptions included."""
    chosen = target if isinstance(target, Workspace) else workspace(target)
    token = _active.set(chosen)
    try:
        yield chosen
    finally:
        _active.reset(token)


def bind(function: Callable[..., Any]) -> Callable[..., Any]:
    """Bind the current dataset into a function so another thread sees the same one.

    `contextvars` do not cross threads: a worker in a thread pool receives an empty
    context, so `active()` quietly falls back to the process default. All page
    fetching happens in a thread pool, so failing to bind means parsing one site's
    pages with another site's adapter and writing the result into the wrong
    library. Nothing raises; the data is simply wrong.

    Always submit as `executor.submit(bind(fn), ...)`.
    """
    workspace = active()

    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        with use(workspace):
            return function(*args, **kwargs)

    return run
