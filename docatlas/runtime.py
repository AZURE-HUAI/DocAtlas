"""当前生效的数据集运行时。

一个数据集不是一个 id，而是四样必须配套的东西：配置（`datasets/*.toml`）、
来源适配器（怎么列页面、怎么解析正文）、领域知识包（可选，凭什么说两个东西
有关）、数据目录（库文件在哪）。四样任意搭配都是错的——拿 A 的适配器去解析
B 的页面，或者往 A 的库里写 B 的正文，都不会报错，只会静静地写坏数据。
所以它们捆成一个 `Workspace`，要换就整套换。

捆起来之后顺带解决了一件更要紧的事：**一个进程可以同时握着好几个数据集**。
以前 `config.py` 在导入时选定一个，全程不许换，于是 MCP 服务器一辈子只能服务
一个文档库；想查第二个库，得在客户端里再配一条 server 记录。实测的结果是
没人配——181 次跨库调用全部退回了命令行。

`active()` 给出当前这一个，`use()` 在一段代码里临时切换。切换走
`contextvars` 而不是普通全局变量：并发调用各自看到自己的数据集，不会串味。

派生配置（分类优先级、概念加分、标识符形状、关系标签）都挂在 Workspace 上，
不再是模块级常量。模块级常量是"导入时算一次"，那正好是多数据集下最容易
出错的地方：换了数据集，常量还是上一个库的。
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


# 代码根：本包的上一级，也就是 Git 仓库根。放程序，不放数据。
REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_CONFIG_DIR = REPO_ROOT / "datasets"

# 安装时定下来的两件事：数据放哪、默认查哪个库。必须落到文件里——MCP 客户端起
# 子进程时不会带上你终端里的环境变量，只认环境变量的话，命令行查得到的库、
# MCP 查不到。一机一份，不入库。
LOCAL_SETTINGS = REPO_ROOT / ".docatlas-local.toml"


def local_settings() -> dict[str, Any]:
    """读安装时写下的选择。读不出来就直说，不要悄悄退回默认值。

    这份文件是安装器写的；它坏了要么是手改坏了、要么是安装器有 bug。两种都该
    被看见——静悄悄地换回另一个库，会让人对着错误的答案查半天。
    """
    if not LOCAL_SETTINGS.exists():
        return {}
    return tomllib.loads(LOCAL_SETTINGS.read_text(encoding="utf-8"))


def _data_root() -> Path:
    """数据根：所有数据集的家。

    优先级是环境变量 > 安装时的选择 > 仓库里的 `data/`。环境变量排最前，是为了
    临时挪一次不用改任何文件。
    """
    chosen = os.environ.get("DOCATLAS_HOME") or local_settings().get("home")
    return Path(chosen).resolve() if chosen else (REPO_ROOT / "data").resolve()


DATA_ROOT = _data_root()


class DatasetNotChosen(RuntimeError):
    """没定下来查哪个库，而本机不止一个可选。"""


def default_dataset_id() -> str:
    """没显式指定时用哪个数据集。

    **不设内置默认。** 装什么库是用户自己的选择，程序不替他挑一个——写死某个
    产品，别人建了别的库还得先发现自己被默认到了另一个上面，而这种错误看起来
    只是"查不到"。只配了一个库时不必问，那没有歧义。
    """
    chosen = os.environ.get("DOCATLAS_DATASET") or local_settings().get("dataset")
    if chosen:
        return str(chosen)
    if len(available := available_dataset_ids()) == 1:
        return available[0]
    raise DatasetNotChosen(
        "没有指定要查哪个数据集。"
        + (
            f"本机可选：{'、'.join(available)}。"
            if available
            else "datasets/ 下没有任何配置。"
        )
        + " 临时指定用 DOCATLAS_DATASET；定下来用 python install.py --dataset <id>。"
    )


# 什么样的词算"标识符"。通用规则认得 `::`、下划线和驼峰；领域知识包可以
# 补充自己的形状（比如 Unreal 的 AActor、FVector）。
DEFAULT_IDENTIFIER_PATTERN = r"::|_[A-Za-z]|[a-z][A-Z]"

# 通用关系的中文说法。领域知识包可以补自己那几种（蓝图↔C++ 之类）。
CORE_RELATION_LABELS = {
    "belongs_to": "所属",
    "parameter_type": "参数类型",
    "return_type": "返回值类型",
    "signature_reference": "签名引用",
    "example_reference": "示例引用",
    "official_reference": "官方相关文档",
}

CORE_EVIDENCE_LABELS = {
    "official_link": "官方文档链接",
    "page_member_table": "官方页面的成员表",
}

# 相关项的排序：领域特有的关系（有实锤证据的对应）排在通用关系前面，
# 所以通用关系的数字都比较大。
CORE_RELATION_PRIORITY = {
    "parameter_type": 3,
    "return_type": 4,
    "belongs_to": 7,
}


def sql_priority_case(column: str, ranks: dict[str, int], default: int | None = None) -> str:
    """把"谁排前面"的映射编译成一段 SQL CASE。数字越小越靠前。

    名字来自数据集配置和领域知识包，也就是**第三方文件**——DocAtlas 是要给
    陌生人装的，别人写的 `datasets/*.toml` 不能当可信输入。直接拼进 SQL 的话，
    一个带引号的分类名轻则让所有查询报语法错，重则改写整条语句。所以这里按
    SQL 标准把单引号翻倍转义，档位一律过 `int()`。

    转义只在这一处做：以前 ondemand 和 runtime 各写了一份一模一样的拼接，
    两份都要记得转义，而只要漏一份就等于没做。
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
    """一个数据集的完整运行时。不可变——要换数据集就换整个对象。"""

    dataset: Dataset
    source: ModuleType
    knowledge: ModuleType | None
    data_dir: Path

    # ---- 身份 ----------------------------------------------------------

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
        """什么样的路径才算"这个数据集的一篇文档"。"""
        return self.dataset.option("doc_prefix", "/")

    # ---- 路径 ----------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "knowledge.sqlite3"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def asset_dir(self) -> Path:
        return self.data_dir / "assets"

    # ---- 领域知识包 ----------------------------------------------------

    def hook(self, name: str, default: Any = None) -> Any:
        """按名字取领域知识包提供的能力；没挂包就用默认值。"""
        return knowledge_hook(self.knowledge, name, default)

    def extension(self, name: str, default: Any = None) -> Any:
        """按名字取领域能力，知识包优先，其次来源适配器。

        有些能力天然属于来源适配器而不是知识包：`(since C++20)` 是
        cppreference 的排版约定，"这个站怎么写版本"和"怎么解析这个站的页面"
        是同一类知识，没有理由为它单独挂一个知识包。而蓝图↔C++ 的对应属于
        领域语义，归知识包。两处都允许，知识包优先——它是给单个数据集挂的，
        比适配器更具体。
        """
        found = knowledge_hook(self.knowledge, name, None)
        if found is None:
            found = getattr(self.source, name, None)
        return default if found is None else found

    # ---- 派生配置（按数据集算一次，之后走缓存）------------------------

    @cached_property
    def category_priority(self) -> dict[str, int]:
        """同名候选优先抓哪一类。数字越小越优先。"""
        return self.dataset.category_priority

    @cached_property
    def concept_category_bonus(self) -> dict[str, float]:
        """概念性提问时各分类的加减分。没配就是空的，检索照常工作。"""
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
        """关系排序的 SQL 片段。领域关系（有实锤对应）排在通用关系前面。"""
        return sql_priority_case("r.relation_type", self.relation_priority, default=8)

    @cached_property
    def category_priority_sql(self) -> str:
        """同名候选优先抓哪一类的 SQL 片段。"""
        return sql_priority_case("category", self.category_priority)


@lru_cache(maxsize=8)
def workspace(dataset_id: str) -> Workspace:
    """按 id 装配一个数据集运行时。

    带缓存：装配要读 toml 并 import 两个模块，而 MCP 会对同一个数据集反复
    调用。缓存的是不可变对象，不存在状态串味的问题。
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
    """当前这一个。没显式切换过就是 `DOCATLAS_DATASET` 指定的那个。"""
    try:
        return _active.get()
    except LookupError:
        default = workspace(default_dataset_id())
        _active.set(default)
        return default


@contextlib.contextmanager
def use(target: Workspace | str) -> Iterator[Workspace]:
    """在这段代码里临时换一个数据集。退出时一定还原，异常也还原。"""
    chosen = target if isinstance(target, Workspace) else workspace(target)
    token = _active.set(chosen)
    try:
        yield chosen
    finally:
        _active.reset(token)


def bind(function: Callable[..., Any]) -> Callable[..., Any]:
    """把当前数据集绑进一个函数，让它在别的线程里也认得同一个数据集。

    `contextvars` 不跨线程：线程池里的 worker 拿到的是一份空上下文，
    `active()` 于是悄悄退回进程默认数据集。抓页面的活儿全在线程池里干，
    所以不绑的后果是——用 A 站的适配器去解析 B 站的页面，然后写进 B 的库。
    全程不报错，只是数据是错的。

    往线程池提交任务时一律 `executor.submit(bind(fn), …)`。
    """
    workspace = active()

    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        with use(workspace):
            return function(*args, **kwargs)

    return run
