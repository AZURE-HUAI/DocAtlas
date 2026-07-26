"""名字与文本的小工具。纯字符串处理，不依赖配置、不认识任何产品。

单独放一个文件，是为了让来源适配器和领域知识包能直接用它，
而不必去 import 会触发数据集加载的 config / chunking。
"""

from __future__ import annotations

import re

from .constants import MARKDOWN_LINK_RE, MARKDOWN_MARKUP_RE


def normalize_name(value: str) -> str:
    """只留小写字母和数字，用来判断"两个名字其实是同一个"。

    `Set Timer by Function Name`、`SetTimerByFunctionName`、`set_timer_by_function_name`
    标准化之后完全相同——用户怎么打字都能对上。
    """
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def qualifier_tail(value: str) -> str:
    """限定名的最后一段：`std::from_chars` → `from_chars`，`math.floor` → `floor`。

    用户抄官方写法时经常连命名空间一起抄，而页面地址通常只有末尾那个名字。
    没有限定符、或末段太短就返回空串——宁可这一档没结果，也别放宽到乱命中。
    """
    tail = re.split(r"::|\.", value.strip())[-1].strip()
    if tail == value.strip() or len(re.sub(r"[^a-z0-9]+", "", tail.casefold())) < 3:
        return ""
    return tail


def humanize_cpp_identifier(value: str) -> str:
    """把标识符拆成人话：`K2_SetTimer` → `K2 Set Timer`。"""
    value = value.split("::")[-1].replace("_", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def heading_visible_text(value: str) -> str:
    """标题里人眼看得见的那部分。

    HTML 转出来的标题经常带链接和行内代码：

        ### [Constrained algorithms](https://…/ranges) (since C++20)

    链接目标是给浏览器看的，不是标题文字。不先剥掉它，下面的锚点就会把
    整条 URL 拼进 fragment，生成一个官方页面里根本不存在的地址。
    """
    visible = MARKDOWN_LINK_RE.sub(r"\1", value)
    return MARKDOWN_MARKUP_RE.sub(" ", visible).strip()


def heading_anchor(value: str) -> str:
    anchor = re.sub(r"[^a-z0-9]+", "", heading_visible_text(value).casefold())
    return anchor or "content"
