"""Dataset configuration: read datasets/*.toml into an object, attach adapters.

Three layers:

    core (rest of this package)  Generic concerns only: HTTP, SQLite, chunking,
                                 retrieval, context budget. Knows no vendor
                                 and no product.
    sources/<name>.py            One site: how to list all its pages, how to
                                 parse its responses into documents.
    knowledge/<name>.py          One domain: its naming conventions, and which
                                 of its APIs are two faces of one thing.
                                 Optional; without one everything still runs,
                                 just with fewer clues.

Adding a version means editing a toml. Adding a site means writing one sources
module. Neither affects the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from pathlib import Path
import tomllib
from types import ModuleType
from typing import Any


@dataclass(frozen=True)
class Dataset:
    """All configuration for one dataset.

    Adapters take this as their context instead of hardcoding version numbers and
    URLs of their own.
    """

    id: str
    name: str
    product: str
    version: str
    language: str
    source: str
    knowledge: str | None
    options: dict[str, Any] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)
    category_labels: dict[str, str] = field(default_factory=dict)
    entity_types: dict[str, str] = field(default_factory=dict)
    # Which categories are API reference. Affects whether a first paragraph reads
    # as a summary or an overview, and biases retrieval towards signatures.
    api_categories: tuple[str, ...] = ()
    # Categories prone to long member listings; pushed down when ranking.
    verbose_categories: tuple[str, ...] = ()
    # Per-category score adjustment for concept questions. Unset means 0:
    # retrieval works as usual, just without this tuning.
    concept_category_bonus: dict[str, float] = field(default_factory=dict)
    # Which category to fetch first among same-named candidates. Lower wins.
    category_priority: dict[str, int] = field(default_factory=dict)
    # Categories permitted to hold no pages. By default every declared category
    # must enumerate some: an empty one is nearly always a mistaken category rule
    # rather than the site genuinely lacking that kind of documentation.
    optional_categories: tuple[str, ...] = ()
    # Words in a user's question that should bring this library to mind. Pure
    # data with no logic: filled into the skill description at install time, and
    # the client uses it to decide whether to invoke the skill at all.
    skill_triggers: tuple[str, ...] = ()
    # Inventory scope policy. One entry so far: which category to file pages that
    # in-scope bodies reference but whose directory was never enumerated. Unset
    # collects nothing — whether a dataset steps outside its own declared
    # directories is its decision, not the core's.
    inventory: dict[str, Any] = field(default_factory=dict)

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def inventory_option(self, key: str, default: Any = None) -> Any:
        return self.inventory.get(key, default)

    @property
    def query_categories(self) -> tuple[str, ...]:
        """Every category that may legitimately be used as a retrieval filter.

        Distinct from `categories`, and the two cannot be the same value.
        `categories` is the **enumeration rule** mapping a category to a path
        prefix, so only categories with a declared directory can appear in it,
        whereas the category collected by the reference closure has no fixed
        prefix by definition: it collects precisely those pages that sit outside
        the declared directories and are referenced by page bodies.

        Those pages nevertheless land in the database carrying that category.
        Using the enumeration rule as "the set of legal categories" would leave a
        whole class of content absent from the capability listing and rejected as
        a typo when passed in, so an AI following the manual's "pass category
        when you know it" would filter out the most relevant page.
        """
        referenced = str(self.inventory.get("referenced_category") or "")
        if not referenced or referenced in self.categories:
            return tuple(self.categories)
        return (*self.categories, referenced)


def dataset_path(dataset_id: str, config_dir: Path) -> Path:
    return config_dir / f"{dataset_id}.toml"


def load_dataset(dataset_id: str, config_dir: Path) -> Dataset:
    path = dataset_path(dataset_id, config_dir)
    if not path.exists():
        available = sorted(p.stem for p in config_dir.glob("*.toml"))
        raise SystemExit(
            f"Dataset config not found: {path}.\n"
            f"Existing datasets: {', '.join(available) or '(none)'}\n"
            f"Set the DOCATLAS_DATASET environment variable to switch dataset."
        )
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("id", "version", "source") if not raw.get(k)]
    if missing:
        raise SystemExit(f"{path} is missing required keys: {', '.join(missing)}")
    if raw["id"] != dataset_id:
        raise SystemExit(
            f"id in {path} is {raw['id']!r} but the file is named {dataset_id!r}. "
            f"They must match, otherwise the data directory will not line up."
        )
    search = raw.get("search") or {}
    return Dataset(
        id=raw["id"],
        name=raw.get("name") or raw["id"],
        product=raw.get("product") or raw["id"],
        version=str(raw["version"]),
        language=raw.get("language") or "en-US",
        source=raw["source"],
        knowledge=raw.get("knowledge") or None,
        options=raw.get("source_options") or {},
        categories=raw.get("categories") or {},
        category_labels=raw.get("category_labels") or {},
        entity_types=raw.get("entity_types") or {},
        api_categories=tuple(raw.get("api_categories") or ()),
        verbose_categories=tuple(search.get("verbose_categories") or ()),
        concept_category_bonus={
            k: float(v)
            for k, v in (search.get("concept_category_bonus") or {}).items()
        },
        category_priority={
            k: int(v) for k, v in (raw.get("category_priority") or {}).items()
        },
        optional_categories=tuple(raw.get("optional_categories") or ()),
        skill_triggers=tuple((raw.get("skill") or {}).get("triggers") or ()),
        inventory=raw.get("inventory") or {},
    )


def _load_module(package: str, name: str, kind: str) -> ModuleType:
    try:
        return importlib.import_module(f"docatlas.{package}.{name}")
    except ModuleNotFoundError as exc:
        # Only report a config error when this module is genuinely absent; an
        # import failure inside the module itself must propagate unchanged.
        if exc.name not in (f"docatlas.{package}.{name}", name):
            raise
        available = sorted(
            p.stem
            for p in (Path(__file__).resolve().parent / package).glob("*.py")
            if not p.stem.startswith("_")
        )
        raise SystemExit(
            f"{kind} {name!r} not found (expected docatlas/{package}/{name}.py).\n"
            f"Available: {', '.join(available) or '(none)'}"
        ) from exc


def load_source(dataset: Dataset) -> ModuleType:
    return _load_module("sources", dataset.source, "source adapter")


def load_knowledge(dataset: Dataset) -> ModuleType | None:
    """Knowledge packs are optional: crawl and search work fine without one,
    only with fewer domain-specific clues.
    """
    if not dataset.knowledge:
        return None
    return _load_module("knowledge", dataset.knowledge, "knowledge pack")


def knowledge_hook(pack: ModuleType | None, name: str, default: Any = None) -> Any:
    """Look up a capability by name from the knowledge pack, else the default.

    Deliberately not an abstract base class: a knowledge pack is just a module and
    offers exactly the functions it implements, with no empty methods written
    purely to satisfy an interface.
    """
    if pack is None:
        return default
    return getattr(pack, name, default)
