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


def qualifier_segments(value: str) -> list[str]:
    """限定名里末段之前的那几段：`std::ranges::sort` → `['std', 'ranges']`。

    末段是"叫什么"，前面几段是"在哪儿"，而地址里往往原样带着这个位置
    （`/cpp/algorithm/ranges/sort`）。只剥末段就等于把用户已经打出来的
    位置信息扔了：`std::ranges::sort` 和 `std::sort` 于是变成同一条查询，
    四个都叫 sort 的页面里只能靠"路径浅的排前面"瞎猜（BUG-008）。

    没有限定符就返回空列表——普通句子里的句点不该被当成限定符，
    所以只认段与段之间都有字母数字的写法。
    """
    parts = [part.strip() for part in re.split(r"::|\.", value.strip())]
    if len(parts) < 2 or not all(re.search(r"[A-Za-z0-9]", part) for part in parts):
        return []
    return parts[:-1]


def qualifier_suffixes(value: str, *, limit: int = 2) -> list[str]:
    """限定名逐段去掉开头，剩下的那几个后缀写法，长的在前。

    `std::ranges::views::transform` → `['ranges::views::transform',
    'views::transform']`。名字的后缀仍然指着同一个东西：C++ 的命名空间别名
    （标准里 `std::views` 就是 `std::ranges::views`）、Python 的重导出、
    Java 的包名简写都是这一件事，所以规则在这里，不属于任何一个站点。

    刻意不含只剩末段的那一档（`transform`）——那正是"八个都叫 transform"的
    歧义来源，由 `qualifier_tail` 单独当最后一档处理。也刻意有上限：一个名字
    派生出无穷多别名，索引白白变大，换不来更准。
    """
    parts = qualifier_segments(value)
    if not parts:
        return []
    tail = re.split(r"::|\.", value.strip())[-1].strip()
    return [
        "::".join([*parts[index:], tail]) for index in range(1, len(parts))
    ][:limit]


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


# 一段文字用的是哪套书写系统。按 Unicode 区段判断，不涉及任何具体语言——
# 这里回答的是"用什么字写的"，不是"说的是哪国话"。
_SCRIPT_RANGES: tuple[tuple[str, str, tuple[tuple[int, int], ...]], ...] = (
    ("han", "汉字", ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))),
    ("kana", "假名", ((0x3040, 0x309F), (0x30A0, 0x30FF))),
    ("hangul", "谚文", ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF))),
    ("cyrillic", "西里尔字母", ((0x0400, 0x04FF),)),
    ("greek", "希腊字母", ((0x0370, 0x03FF),)),
    ("hebrew", "希伯来字母", ((0x0590, 0x05FF),)),
    ("arabic", "阿拉伯字母", ((0x0600, 0x06FF),)),
    ("devanagari", "天城文", ((0x0900, 0x097F),)),
    ("thai", "泰文", ((0x0E00, 0x0E7F),)),
    ("latin", "拉丁字母", ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F))),
)

SCRIPT_NAMES = {key: label for key, label, _ in _SCRIPT_RANGES}

# 语言标签用哪套字写。一种语言可以用不止一套（日文汉字假名混写）。
# 这是书写系统的事实，不是对用户的假设。
_LANGUAGE_SCRIPTS = {
    "zh": ("han",),
    "ja": ("kana", "han"),
    "ko": ("hangul", "han"),
    "ru": ("cyrillic",),
    "uk": ("cyrillic",),
    "bg": ("cyrillic",),
    "sr": ("cyrillic", "latin"),
    "el": ("greek",),
    "he": ("hebrew",),
    "ar": ("arabic",),
    "fa": ("arabic",),
    "ur": ("arabic",),
    "hi": ("devanagari",),
    "mr": ("devanagari",),
    "ne": ("devanagari",),
    "th": ("thai",),
}


def _script_of(character: str) -> str:
    code = ord(character)
    for key, _label, ranges in _SCRIPT_RANGES:
        if any(low <= code <= high for low, high in ranges):
            return key
    return ""


def dominant_script(value: str) -> str:
    """这段文字主要用哪套书写系统写的；认不出来返回空串。"""
    counts: dict[str, int] = {}
    for character in value:
        if key := _script_of(character):
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def expected_scripts(language: str) -> tuple[str, ...]:
    """这个语言标签的正文该用哪套字写。不认识的语言按拉丁字母算。"""
    return _LANGUAGE_SCRIPTS.get(language.split("-")[0].casefold(), ("latin",))


def script_of_language(language: str) -> str:
    """这个语言标签主要用哪套字写，取第一种，用于说人话的提示。"""
    return expected_scripts(language)[0]


def script_mismatch(query: str, language: str) -> str:
    """查询用的字和数据集原文的字明显不是一套时，返回查询用的那一套。

    英文库里查不到中文问题不是 Bug——库里本来就没有中文正文。但空结果本身
    不含信息量，用户没法从中判断该改成什么。认出这一种"没有"，才能给出
    "换成原文写法再问一次"这样能照做的下一步。

    只看字形，不猜语种：数据集声明什么语言，就按什么语言比。
    """
    script = dominant_script(query)
    if not script:
        return ""
    return "" if script in expected_scripts(language) else script
