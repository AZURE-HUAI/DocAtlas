"""数据集配置：把 datasets/*.toml 读成一个对象，并挂上对应的适配器。

三层分工：

    core（本包其余模块）  只懂通用的事：HTTP、SQLite、切块、检索、上下文预算。
                          不知道 Epic 是谁，也不知道 Unreal 是什么。
    sources/<name>.py     懂一个站点：怎么列出全部页面、怎么把它的返回解析成文档。
    knowledge/<name>.py   懂一个领域：Unreal 的 K2_ 前缀、蓝图↔C++ 的对应关系。
                          可选，留空也能跑，只是少了这些额外线索。

加一个版本改 toml；加一个站点写一个 sources 模块。两件事互不影响。
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
    """一个数据集的全部配置。适配器拿它当上下文，不再自己写死版本号和网址。"""

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
    # 哪些分类是 API 参考（影响首段该算"摘要"还是"概述"，以及检索偏好）。
    api_categories: tuple[str, ...] = ()
    # 哪些分类爱出大段罗列，检索时要压一压。
    verbose_categories: tuple[str, ...] = ()
    # 概念性提问时各分类的加减分。没配就是 0，检索照常工作，只是没针对性调优。
    concept_category_bonus: dict[str, float] = field(default_factory=dict)
    # 同名候选优先抓哪一类。数字越小越优先。
    category_priority: dict[str, int] = field(default_factory=dict)
    # 允许一页都没有的分类。默认所有声明的分类都必须枚举到页面——
    # 一个分类空着，几乎总是分类规则写错了，而不是官方真的没有这类文档。
    optional_categories: tuple[str, ...] = ()
    # 用户问题里出现哪些词，AI 就该想到查这个库。纯数据，没有逻辑——
    # 装技能时填进技能描述，Claude Code 靠它决定要不要唤起这个技能。
    skill_triggers: tuple[str, ...] = ()
    # 清单范围策略。目前只有一项：范围内正文引用到、但整个目录都没被枚举过的
    # 页面归哪一类。不配就不收——一个数据集要不要越出自己声明的目录，
    # 是它自己的决定，核心不替它拿主意。
    inventory: dict[str, Any] = field(default_factory=dict)

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def inventory_option(self, key: str, default: Any = None) -> Any:
        return self.inventory.get(key, default)

    @property
    def query_categories(self) -> tuple[str, ...]:
        """检索时可以合法用来过滤的分类全集。

        跟 `categories` 是两件事，不能共用一个：`categories` 是"分类 → 路径前缀"
        的**枚举规则**，只有声明了目录的分类才写得进去；而引用闭包收进来的那一类
        按定义没有固定前缀（它收的正是"散落在声明目录之外、被正文引用到"的页面）。

        但这些页面确实带着这个分类落进了库。拿枚举规则当"合法分类的全集"用，
        库里就会有一整类内容既不出现在能力清单里、传进来还被当成拼错拒掉——
        AI 按手册"知道分类就传 category"照做，反而把最相关的那一页滤掉了。
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
            f"找不到数据集配置 {path}。\n"
            f"现有数据集：{'、'.join(available) or '（一个都没有）'}\n"
            f"换数据集请设环境变量 DOCATLAS_DATASET。"
        )
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("id", "version", "source") if not raw.get(k)]
    if missing:
        raise SystemExit(f"{path} 缺少必填项：{'、'.join(missing)}")
    if raw["id"] != dataset_id:
        raise SystemExit(
            f"{path} 里的 id 是 {raw['id']!r}，和文件名 {dataset_id!r} 不一致。"
            f"两者必须相同，否则数据目录会对不上。"
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
        # 只有确实缺这个模块才报配置错；模块里自己 import 挂了要如实抛出。
        if exc.name not in (f"docatlas.{package}.{name}", name):
            raise
        available = sorted(
            p.stem
            for p in (Path(__file__).resolve().parent / package).glob("*.py")
            if not p.stem.startswith("_")
        )
        raise SystemExit(
            f"找不到{kind} {name!r}（应为 docatlas/{package}/{name}.py）。\n"
            f"现有的有：{'、'.join(available) or '（一个都没有）'}"
        ) from exc


def load_source(dataset: Dataset) -> ModuleType:
    return _load_module("sources", dataset.source, "来源适配器")


def load_knowledge(dataset: Dataset) -> ModuleType | None:
    """领域知识包是可选的：没有它一样能抓能搜，只是少了领域特有的线索。"""
    if not dataset.knowledge:
        return None
    return _load_module("knowledge", dataset.knowledge, "领域知识包")


def knowledge_hook(pack: ModuleType | None, name: str, default: Any = None) -> Any:
    """按名字取领域知识包提供的能力；没挂包、或包没实现这一项，就用默认值。

    刻意不用抽象基类：知识包只是一个模块，实现哪几个函数就提供哪几项能力，
    不必为了满足接口去写一堆空方法。
    """
    if pack is None:
        return default
    return getattr(pack, name, default)
