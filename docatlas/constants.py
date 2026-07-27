"""跟任何站点、任何产品都无关的常量。

单独一个文件是为了断开循环引用：config.py 在启动时要加载来源适配器，
而适配器也需要这些常量——它们从这里拿，就不必回头去 import 半成品的 config。
"""

from __future__ import annotations

import re


# 切块规则的版本号。规则一改就要 +1，chunks.parser_version 记录每块由哪版产出，
# 中途换规则时才分得清哪些块是旧的、需要重切。
CHUNKER_VERSION = "v8"
USER_AGENT = "DocAtlas/1.0 (+local educational archive)"

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
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
CODE_FENCE_RE = re.compile(r"^\s{0,3}(?:`{3,}|~{3,})")
# 标题标记挂在行内元素后面、不独占一行：`![图](地址) ## Edit tab`。官方
# Markdown 导出少一个换行就长这样，而漏认一个标题，它后面所有小节都会挂到
# 上一节名下。必须紧跟着闭合的链接/图片括号才算——汇编注释
# `movl input(%rip), %eax # eax = input` 的 `#` 同样在行尾，那不是标题。
TRAILING_HEADING_RE = re.compile(
    r"^(?P<lead>.*\]\([^)\s]*\))\s+(?P<hashes>#{1,6})\s+(?P<title>\S.*?)\s*$"
)
MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
MARKDOWN_TARGET_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\((https?://[^)\s]+)(?:\s+\"[^\"]*\")?\)",
    re.IGNORECASE,
)
MARKDOWN_MARKUP_RE = re.compile(r"[`*_>#|~]+")
WHITESPACE_RE = re.compile(r"[ \t]+")

# 小节标题长什么样，就大致知道这段是干嘛的。跟具体产品无关：
# 任何技术文档都有"参数""返回值""示例""注意事项"这些段落。
KNOWLEDGE_TYPE_RULES = (
    ("parameters", re.compile(r"\b(inputs?|parameters?|arguments?|properties)\b", re.I)),
    ("returns", re.compile(r"\b(outputs?|returns?|return value|results?)\b", re.I)),
    ("examples", re.compile(r"\b(examples?|usage|how to use|walkthrough)\b", re.I)),
    ("remarks", re.compile(r"\b(remarks?|notes?|cautions?|warnings?|limitations?|considerations?)\b", re.I)),
    ("signature", re.compile(r"\b(syntax|declaration|definition|signature|header|include)\b", re.I)),
    ("navigation", re.compile(r"\b(navigation|breadcrumbs?|hierarchy)\b", re.I)),
    ("references", re.compile(r"\b(related|references?|see also|prerequisites?)\b", re.I)),
)
