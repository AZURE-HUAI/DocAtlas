"""Small helpers for names and text. Pure string work, no config, no product.

Kept in its own module so source adapters and knowledge packs can use it without
importing config / chunking, which would trigger dataset loading.
"""

from __future__ import annotations

import re

from .constants import MARKDOWN_LINK_RE, MARKDOWN_MARKUP_RE


def normalize_name(value: str) -> str:
    """Reduce to lowercase alphanumerics, to test whether two names are the same.

    `Set Timer by Function Name`, `SetTimerByFunctionName` and
    `set_timer_by_function_name` all normalize to the same string, so however the
    user typed it, it matches.
    """
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def qualifier_tail(value: str) -> str:
    """Last segment of a qualified name: `std::from_chars` -> `from_chars`.

    Users copying official spelling often bring the namespace along, while page
    URLs usually carry only the final name. Returns an empty string when there is
    no qualifier or the tail is too short: better for this stage to find nothing
    than to widen into noise.
    """
    tail = re.split(r"::|\.", value.strip())[-1].strip()
    if tail == value.strip() or len(re.sub(r"[^a-z0-9]+", "", tail.casefold())) < 3:
        return ""
    return tail


def qualifier_segments(value: str) -> list[str]:
    """Segments before the last: `std::ranges::sort` -> `['std', 'ranges']`.

    The tail says what a thing is called; the leading segments say where it
    lives, and URLs frequently carry that location verbatim
    (`/cpp/algorithm/ranges/sort`). Stripping to the tail alone discards location
    the user already typed, collapsing `std::ranges::sort` and `std::sort` into
    one query and leaving four pages named `sort` to be told apart by nothing
    better than path depth.

    Returns an empty list when there is no qualifier: a period in an ordinary
    sentence is not a qualifier, so every segment must contain alphanumerics.
    """
    parts = [part.strip() for part in re.split(r"::|\.", value.strip())]
    if len(parts) < 2 or not all(re.search(r"[A-Za-z0-9]", part) for part in parts):
        return []
    return parts[:-1]


def qualifier_suffixes(value: str, *, limit: int = 2) -> list[str]:
    """Suffix spellings of a qualified name, longest first.

    `std::ranges::views::transform` -> `['ranges::views::transform',
    'views::transform']`. A suffix of a name still points at the same thing: C++
    namespace aliases (`std::views` *is* `std::ranges::views`), Python
    re-exports and abbreviated Java package names are all this one phenomenon,
    which is why the rule lives here and belongs to no single site.

    Deliberately excludes the bare tail (`transform`), the source of ambiguity
    when many unrelated pages share one tail, and handled separately by
    `qualifier_tail`. The limit is deliberate too: one name can derive endless
    aliases, growing the index without improving precision.
    """
    parts = qualifier_segments(value)
    if not parts:
        return []
    tail = re.split(r"::|\.", value.strip())[-1].strip()
    return [
        "::".join([*parts[index:], tail]) for index in range(1, len(parts))
    ][:limit]


def humanize_cpp_identifier(value: str) -> str:
    """Split an identifier into words: `AB_GetItemCount` -> `AB Get Item Count`."""
    value = value.split("::")[-1].replace("_", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def heading_visible_text(value: str) -> str:
    """The part of a heading a reader actually sees.

    Headings converted from HTML often carry links and inline code:

        ### [Constrained algorithms](https://.../ranges) (since C++20)

    The link target is for the browser, not part of the heading text. Without
    stripping it first, the anchor below would splice the whole URL into the
    fragment and produce an address that does not exist on the official page.
    """
    visible = MARKDOWN_LINK_RE.sub(r"\1", value)
    return MARKDOWN_MARKUP_RE.sub(" ", visible).strip()


def heading_anchor(value: str) -> str:
    anchor = re.sub(r"[^a-z0-9]+", "", heading_visible_text(value).casefold())
    return anchor or "content"


# Which writing system a piece of text uses. Decided by Unicode block, with no
# reference to any particular language: the question here is "written in what
# characters", not "spoken in what tongue".
_SCRIPT_RANGES: tuple[tuple[str, str, tuple[tuple[int, int], ...]], ...] = (
    ("han", "Han", ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))),
    ("kana", "Kana", ((0x3040, 0x309F), (0x30A0, 0x30FF))),
    ("hangul", "Hangul", ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF))),
    ("cyrillic", "Cyrillic", ((0x0400, 0x04FF),)),
    ("greek", "Greek", ((0x0370, 0x03FF),)),
    ("hebrew", "Hebrew", ((0x0590, 0x05FF),)),
    ("arabic", "Arabic", ((0x0600, 0x06FF),)),
    ("devanagari", "Devanagari", ((0x0900, 0x097F),)),
    ("thai", "Thai", ((0x0E00, 0x0E7F),)),
    ("latin", "Latin", ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F))),
)

SCRIPT_NAMES = {key: label for key, label, _ in _SCRIPT_RANGES}

# Which scripts a language tag is written in. One language may use more than one
# (Japanese mixes kana and han). This is a fact about writing systems, not an
# assumption about the user.
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
    """Which script this text is mostly written in; empty if unrecognised."""
    counts: dict[str, int] = {}
    for character in value:
        if key := _script_of(character):
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def expected_scripts(language: str) -> tuple[str, ...]:
    """Scripts a body of text in this language tag should use; unknown -> Latin."""
    return _LANGUAGE_SCRIPTS.get(language.split("-")[0].casefold(), ("latin",))


def script_of_language(language: str) -> str:
    """Primary script for this language tag, for use in human-readable hints."""
    return expected_scripts(language)[0]


def script_mismatch(query: str, language: str) -> str:
    """Script of the query when it is plainly not the dataset's own script.

    Asking a question in one script of a library written in another is not a bug:
    the library simply holds no text in that script. But an empty result carries
    no information, so the user cannot tell what to change. Naming this specific
    kind of "not found" is what allows an actionable next step such as "ask again
    using the original spelling".

    Compares scripts only and never guesses the language: whatever language the
    dataset declares is what the comparison uses.
    """
    script = dominant_script(query)
    if not script:
        return ""
    return "" if script in expected_scripts(language) else script
