"""Constants that belong to no particular site or product.

Kept in its own module to break a circular import: config.py loads source
adapters at startup, and those adapters need these constants. Taking them from
here means they never have to reach back into a half-initialised config.
"""

from __future__ import annotations

import re


# Version of the chunking rules. Bump it on every rule change;
# chunks.parser_version records which version produced each chunk, which is what
# makes stale chunks identifiable when the rules move on.
CHUNKER_VERSION = "v9"
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
# A heading marker trailing an inline element instead of owning its own line:
# `![img](url) ## Edit tab`. Official Markdown exports look like this whenever a
# newline is missing, and a missed heading drags every section after it under the
# previous heading. The marker must follow a closed link/image bracket to count:
# an assembly comment such as `movl input(%rip), %eax # eax = input` also ends
# with a `#`, and that is not a heading.
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

# A section's heading shape says roughly what the section is for. Independent of
# any product: every technical document has "parameters", "return value",
# "examples" and "notes" sections.
KNOWLEDGE_TYPE_RULES = (
    ("parameters", re.compile(r"\b(inputs?|parameters?|arguments?|properties)\b", re.I)),
    ("returns", re.compile(r"\b(outputs?|returns?|return value|results?)\b", re.I)),
    ("examples", re.compile(r"\b(examples?|usage|how to use|walkthrough)\b", re.I)),
    ("remarks", re.compile(r"\b(remarks?|notes?|cautions?|warnings?|limitations?|considerations?)\b", re.I)),
    ("signature", re.compile(r"\b(syntax|declaration|definition|signature|header|include)\b", re.I)),
    ("navigation", re.compile(r"\b(navigation|breadcrumbs?|hierarchy)\b", re.I)),
    ("references", re.compile(r"\b(related|references?|see also|prerequisites?)\b", re.I)),
)
