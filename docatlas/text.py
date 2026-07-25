"""名字与文本的小工具。纯字符串处理，不依赖配置、不认识任何产品。

单独放一个文件，是为了让来源适配器和领域知识包能直接用它，
而不必去 import 会触发数据集加载的 config / chunking。
"""

from __future__ import annotations

import re


def normalize_name(value: str) -> str:
    """只留小写字母和数字，用来判断"两个名字其实是同一个"。

    `Set Timer by Function Name`、`SetTimerByFunctionName`、`set_timer_by_function_name`
    标准化之后完全相同——用户怎么打字都能对上。
    """
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def humanize_cpp_identifier(value: str) -> str:
    """把标识符拆成人话：`K2_SetTimer` → `K2 Set Timer`。"""
    value = value.split("::")[-1].replace("_", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def heading_anchor(value: str) -> str:
    anchor = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return anchor or "content"
