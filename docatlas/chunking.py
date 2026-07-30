"""Split a body into heading-scoped sections and knowledge chunks."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from .constants import (
    CODE_FENCE_RE,
    HEADING_RE,
    KNOWLEDGE_TYPE_RULES,
    TRAILING_HEADING_RE,
)
from .runtime import active
from .htmlmd import plain_text
# These helpers live in text.py (adapters and knowledge packs need them and must
# not route back through config); re-exported here so existing
# `from .chunking import normalize_name` keeps working.
from .text import heading_anchor, humanize_cpp_identifier, normalize_name  # noqa: F401


def classify_knowledge_type(
    heading: str,
    *,
    position: int,
    category: str,
) -> str:
    for knowledge_type, pattern in KNOWLEDGE_TYPE_RULES:
        if pattern.search(heading):
            return knowledge_type
    if position == 0:
        # An API reference page opens with a summary of what the function does;
        # a guide opens with an overview of the whole article.
        if category in active().dataset.api_categories:
            return "summary"
        return "overview"
    return "details"


def hard_bound_markdown_chunks(
    values: Iterable[str], max_chars: int
) -> list[str]:
    bounded: list[str] = []
    for value in values:
        if len(value) <= max_chars:
            if value.strip():
                bounded.append(value)
            continue
        stripped = value.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            fence = lines[0]
            has_closing_fence = (
                len(lines) > 1 and lines[-1].strip().startswith("```")
            )
            payload_lines = (
                lines[1:-1] if has_closing_fence else lines[1:]
            )
            payload = "\n".join(payload_lines)
            payload_limit = max(1, max_chars - len(fence) - 6)
            for index in range(0, len(payload), payload_limit):
                piece = payload[index : index + payload_limit]
                bounded.append(f"{fence}\n{piece}\n```")
            continue
        bounded.extend(
            value[index : index + max_chars]
            for index in range(0, len(value), max_chars)
            if value[index : index + max_chars].strip()
        )
    return bounded


def split_large_markdown_unit(unit: str, max_chars: int) -> list[str]:
    lines = unit.splitlines()
    if len(unit) <= max_chars:
        return [unit]
    if len(lines) >= 3 and all(
        line.lstrip().startswith("|") for line in lines[:2]
    ):
        header = lines[:2]
        chunks: list[str] = []
        current = header.copy()
        for row in lines[2:]:
            candidate = "\n".join(current + [row])
            if len(candidate) > max_chars and len(current) > 2:
                chunks.append("\n".join(current))
                current = header + [row]
            else:
                current.append(row)
        if len(current) > 2:
            chunks.append("\n".join(current))
        return hard_bound_markdown_chunks(chunks or [unit], max_chars)
    list_lines = [
        line for line in lines
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line)
    ]
    if len(lines) > 1 and len(list_lines) >= max(2, len(lines) // 2):
        chunks = []
        current: list[str] = []
        for line in lines:
            if len("\n".join(current + [line])) > max_chars and current:
                chunks.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append("\n".join(current))
        return hard_bound_markdown_chunks(chunks, max_chars)
    if unit.strip().startswith("```"):
        fence = lines[0] if lines else "```"
        closing = "```"
        payload = lines[1:-1] if len(lines) > 2 else lines[1:]
        chunks = []
        current: list[str] = []
        for line in payload:
            payload_limit = max(1, max_chars - len(fence) - 8)
            line_parts = (
                [
                    line[index : index + payload_limit]
                    for index in range(0, len(line), payload_limit)
                ]
                if len(line) > payload_limit
                else [line]
            )
            for line_part in line_parts:
                if (
                    len("\n".join(current + [line_part])) > payload_limit
                    and current
                ):
                    chunks.append("\n".join([fence, *current, closing]))
                    current = [line_part]
                else:
                    current.append(line_part)
        if current:
            chunks.append("\n".join([fence, *current, closing]))
        return hard_bound_markdown_chunks(chunks or [unit], max_chars)
    sentences = re.split(r"(?<=[.!?。！？])\s+", unit)
    chunks = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    bounded: list[str] = []
    for chunk in chunks or [unit]:
        if len(chunk) <= max_chars:
            bounded.append(chunk)
            continue
        chunk_lines = chunk.splitlines()
        if len(chunk_lines) > 1:
            current_lines: list[str] = []
            for line in chunk_lines:
                if len(line) > max_chars:
                    if any(current_lines):
                        bounded.append("\n".join(current_lines))
                    current_lines = []
                    bounded.extend(
                        line[index : index + max_chars]
                        for index in range(0, len(line), max_chars)
                    )
                    continue
                if len("\n".join(current_lines + [line])) > max_chars and current_lines:
                    if any(current_lines):
                        bounded.append("\n".join(current_lines))
                    current_lines = [line]
                else:
                    current_lines.append(line)
            if any(current_lines):
                bounded.append("\n".join(current_lines))
        else:
            bounded.extend(
                chunk[index : index + max_chars]
                for index in range(0, len(chunk), max_chars)
            )
    return hard_bound_markdown_chunks(bounded, max_chars)


def markdown_units(markdown: str, max_chars: int) -> list[str]:
    raw_units: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                if any(part.strip() for part in current):
                    raw_units.append("\n".join(current).strip())
                current = [line]
                in_fence = True
            else:
                current.append(line)
                raw_units.append("\n".join(current).strip())
                current = []
                in_fence = False
            continue
        if in_fence:
            current.append(line)
            continue
        if not stripped:
            if any(part.strip() for part in current):
                raw_units.append("\n".join(current).strip())
            current = []
            continue
        current.append(line)
    if any(part.strip() for part in current):
        raw_units.append("\n".join(current).strip())
    units: list[str] = []
    for unit in raw_units:
        units.extend(split_large_markdown_unit(unit, max_chars))
    return units


# Some sections have a heading that misdescribes their content, so the heading
# name cannot be taken as the conclusion.
#
# The classic case is a `Navigation` section that, besides the breadcrumb, also
# holds the sentence describing **what the node does** plus its `Target is X` —
# the most useful information on the page. Such sections must not be dropped, but
# neither should they decide the type of the merged chunk, or the whole chunk
# counts as navigation and is penalized to the bottom of retrieval.
WEAK_TYPE_LABELS = frozenset({"navigation"})

# A breadcrumb: a whole line holding nothing but links and separators.
# Example: `[Section](...) > [Section/Subsection](...)`
_BREADCRUMB_RE = re.compile(
    r"^[ \t]*\[[^\]]*\]\([^)\s]*\)"
    r"(?:[ \t]*[>›»][ \t]*\[[^\]]*\]\([^)\s]*\))+[ \t]*$",
    re.M,
)


def strip_breadcrumbs(markdown: str) -> str:
    """Drop the navigation breadcrumb line.

    A breadcrumb is page decoration, not content. Kept, it puts the site's
    directory names and two or three URLs into the full-text index of **every**
    page, so a query naming a directory plus a feature matches every page in
    that directory — all on breadcrumb text, with not one body word matching. The
    page actually wanted is pushed down by a crowd of its own siblings.

    Where a page sits in the site is already recorded twice, in `source_url` and
    `context_prefix`; a third copy is not needed.

    Only "a whole line of two or more chained links" counts, so a lone link in a
    body is never removed by mistake.
    """
    return _BREADCRUMB_RE.sub("", markdown)

# A trailing remainder shorter than this many characters merges back into the
# previous chunk, rather than leaving a stub of a couple of dozen words.
RUNT_TAIL_CHARS = 400


def _parent_path(heading_path: str) -> str:
    """`Page > A > x` -> `Page > A`, to test whether two sections share a topic."""
    head, separator, _tail = heading_path.rpartition(" > ")
    return head if separator else heading_path


def group_sections_for_chunking(
    sections: list[dict[str, Any]], target_chars: int
) -> list[list[dict[str, Any]]]:
    """Group consecutive small sections, then chunk the group.

    Why this step exists: sections split by heading include many paragraphs like
    `Inputs` that are one table header long. Chunked alone, each is twenty or
    thirty tokens, too small to answer anything while still consuming a retrieval
    slot. Accumulating adjacent small sections under the same parent heading up to
    a target size is what produces a usable piece of knowledge.

    Breaks in only two cases: the parent heading changed (the topic changed), or
    the target size was reached. A paragraph already over the target forms its own
    group and goes through the ordinary splitting path.
    """
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if current:
            groups.append(current)
            current = []
            current_chars = 0

    for section in sections:
        size = len(section["body_md"] or "")
        if size >= target_chars:
            flush()
            groups.append([section])
            continue
        if current and (
            _parent_path(current[0]["heading_path"])
            != _parent_path(section["heading_path"])
            or current_chars + size > target_chars
        ):
            flush()
        current.append(section)
        current_chars += size
    flush()
    return groups


def _group_knowledge_type(group: list[dict[str, Any]]) -> str:
    """Which type the merged chunk counts as.

    Takes the first type that actually fits its content. One like `navigation`,
    whose heading name does not match what it holds, must not represent the whole
    chunk: content carrying a node description, its parameters and its return
    value labelled "navigation" gets penalized all the way down in retrieval.
    """
    for section in group:
        if section["knowledge_type"] not in WEAK_TYPE_LABELS:
            return section["knowledge_type"]
    return group[0]["knowledge_type"]


def merge_sections(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Join a group of sections into one synthetic section for the chunker.

    Subheadings stay in the body verbatim: merging exists to make one chunk
    complete enough, not to erase labels like `Inputs` / `Outputs`.
    """
    if len(group) == 1:
        return group[0]
    first = group[0]
    parts = []
    for section in group:
        heading = "#" * min(max(section["heading_level"], 1), 6)
        parts.append(f"{heading} {section['title']}\n\n{section['body_md']}".strip())
    parent = _parent_path(first["heading_path"])
    # Fields are listed explicitly rather than using **first: a synthetic
    # section's body is no longer the first section's, so an inherited
    # content_hash / token_estimate would be wrong and would eventually be
    # trusted by someone.
    return {
        # Attributed to the group's first section: the chunks table requires a
        # non-null section_id, and the group does begin there in the document.
        "position": first["position"],
        "heading_path": parent,
        "title": parent.rpartition(" > ")[2] or first["title"],
        "heading_level": min(section["heading_level"] for section in group),
        "body_md": "\n\n".join(parts),
        "knowledge_type": _group_knowledge_type(group),
        "source_url": first["source_url"],
        "source_anchor": first["source_anchor"],
        "quality_score": min(section["quality_score"] for section in group),
    }


def chunk_sections(
    sections: list[dict[str, Any]],
    *,
    page_title: str,
    category: str,
    document_type: str | None,
    target_chars: int = 2200,
    max_chars: int = 3200,
) -> list[dict[str, Any]]:
    """Chunking entry point for a whole page: group the small, split the large."""
    chunks: list[dict[str, Any]] = []
    for group in group_sections_for_chunking(sections, target_chars):
        chunks.extend(
            chunk_section(
                merge_sections(group),
                page_title=page_title,
                category=category,
                document_type=document_type,
                target_chars=target_chars,
                max_chars=max_chars,
            )
        )
    return chunks


def chunk_section(
    section: dict[str, Any],
    *,
    page_title: str,
    category: str,
    document_type: str | None,
    target_chars: int = 2200,
    max_chars: int = 3200,
) -> list[dict[str, Any]]:
    body_md = strip_breadcrumbs(section["body_md"] or "")
    if not plain_text(body_md):
        return []
    units = markdown_units(body_md, max_chars)
    bodies: list[str] = []
    current: list[str] = []
    current_chars = 0
    for unit in units:
        separator = 2 if current else 0
        if current and current_chars + separator + len(unit) > max_chars:
            bodies.append("\n\n".join(current))
            current = [unit]
            current_chars = len(unit)
            continue
        current.append(unit)
        current_chars += separator + len(unit)
        if current_chars >= target_chars:
            bodies.append("\n\n".join(current))
            current = []
            current_chars = 0
    if current:
        bodies.append("\n\n".join(current))
    # Merge a small trailing remainder back: a stub of a couple of dozen words
    # answers nothing and still occupies a retrieval slot. Merging pushes the
    # previous chunk slightly over target at worst, still inside the hard cap.
    if (
        len(bodies) > 1
        and len(bodies[-1]) < RUNT_TAIL_CHARS
        and len(bodies[-2]) + 2 + len(bodies[-1]) <= max_chars
    ):
        tail = bodies.pop()
        bodies[-1] = f"{bodies[-1]}\n\n{tail}"
    if not bodies:
        bodies = [body_md or "(No textual content)"]

    chunk_count = len(bodies)
    workspace = active()
    label = workspace.category_labels.get(category, category)
    context_prefix = " | ".join(
        part
        for part in (
            # Product and version both come from the dataset: hardcoding either
            # would stamp every other dataset's chunks with the wrong product.
            f"{workspace.dataset.product} {workspace.version}",
            label,
            document_type,
            page_title,
            section["heading_path"],
            section["knowledge_type"],
        )
        if part
    )
    chunks: list[dict[str, Any]] = []
    for index, body in enumerate(bodies):
        part_suffix = f" ({index + 1}/{chunk_count})" if chunk_count > 1 else ""
        title = f"{section['title']}{part_suffix}"
        heading = "#" * min(max(section["heading_level"], 1), 6)
        source_line = (
            f"> DOC source: [{section['source_anchor']}]"
            f"({section['source_anchor']})"
        )
        content_md = f"{heading} {title}\n\n{body}\n\n{source_line}".strip()
        text = plain_text(body)
        chunks.append(
            {
                "section_position": section["position"],
                "chunk_index": index,
                "chunk_count": chunk_count,
                "knowledge_type": section["knowledge_type"],
                "title": title,
                "heading_path": section["heading_path"],
                "context_prefix": context_prefix,
                "content_md": content_md,
                "content_text": text,
                "source_url": section["source_url"],
                "source_anchor": section["source_anchor"],
                "token_estimate": max(1, (len(text) + 3) // 4),
                "content_hash": hashlib.sha256(
                    f"{context_prefix}\n{text}".encode("utf-8")
                ).hexdigest(),
                "quality_score": section["quality_score"],
            }
        )
    return chunks


def fenced_line_numbers(lines: list[str]) -> set[int]:
    """Line numbers inside a code fence. Only paired fences count.

    A `# comment` inside a fence is not a heading. Without excluding them, a bash
    or Python example is split into several sections on the spot, with things like
    `# Use the draftHash from the previous step` sitting in `heading_path` — the
    code block is fragmented and the heading is fictional.

    An unpaired fence never counts. Real documents do contain them, and treating
    "from here to end of file is code" would swallow every genuine heading after
    it, which is worse than ignoring the fence.
    """
    opened: int | None = None
    inside: set[int] = set()
    for index, line in enumerate(lines):
        if not CODE_FENCE_RE.match(line):
            continue
        if opened is None:
            opened = index
        else:
            inside.update(range(opened + 1, index))
            opened = None
    return inside


def heading_at(line: str) -> tuple[str, int, str] | None:
    """Whether this line is a heading: returns (text before it, level, title),
    or None.

    When the whole line is a heading the preceding text is empty; when the heading
    trails an inline element, the first half of the line is body and only the tail
    is the heading (see `TRAILING_HEADING_RE`).
    """
    if match := HEADING_RE.match(line):
        return "", len(match["hashes"]), match["title"]
    if match := TRAILING_HEADING_RE.match(line):
        return match["lead"], len(match["hashes"]), match["title"]
    return None


def description_repeats_lead(
    description: str, lines: list[str], fenced: set[int]
) -> bool:
    """Whether the body already opens with the summary sentence.

    On most sites the "summary" *is* the body's first sentence, so re-inserting it
    ahead of the body shows the reader the same sentence twice in a row.

    The test is "does this opening contain that sentence", which is neither
    equality nor "starts with":

    - A summary is often a truncated version of the first body sentence, where the
      body version still carries `**bold**` and a continuation, so comparing for
      equality would still duplicate.
    - The summary sentence is not necessarily first either. A page may open with a
      notice table, putting the first body sentence after it, while the adapter
      skipped table rows when choosing the summary; "starts with" would miss that
      too.

    Only the lines before the first heading are examined: duplication can only
    happen there, since that is exactly where the summary gets inserted. This also
    guarantees the body is never left empty when the summary is skipped — a match
    means the sentence was already in that body. Pages whose summary comes from
    metadata and appears nowhere in the body never reach this point and still get
    it prepended.
    """
    lead: list[str] = []
    for index, line in enumerate(lines):
        found = heading_at(line) if index not in fenced else None
        if found is not None:
            # The half-line before a heading is still body
            # (`TRAILING_HEADING_RE`) and has to be counted.
            lead.append(found[0])
            break
        lead.append(line)
    wanted = normalize_name(description)
    return bool(wanted) and wanted in normalize_name(plain_text("\n".join(lead)))


def split_sections(
    *,
    title: str,
    description: str,
    markdown: str,
    source_url: str,
    category: str,
) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    fenced = fenced_line_numbers(lines)
    raw_sections: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    current = {
        "level": 1,
        "title": title or "Untitled",
        "path": title or "Untitled",
        "lines": [],
    }
    if (
        description
        and normalize_name(description) != normalize_name(title)
        and not description_repeats_lead(description, lines, fenced)
    ):
        current["lines"].append(description)

    def finish() -> None:
        content = "\n".join(current["lines"]).strip()
        if content:
            raw_sections.append(
                {
                    "heading_level": current["level"],
                    "title": current["title"],
                    "heading_path": current["path"],
                    "body": content,
                }
            )

    for index, line in enumerate(lines):
        found = heading_at(line) if index not in fenced else None
        if found is None:
            current["lines"].append(line)
            continue
        lead, level, heading = found
        if lead:
            # The half-line before a heading belongs to the previous section's
            # body and must not travel with the heading.
            current["lines"].append(lead)
        finish()
        heading = heading.strip()
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, heading))
        full_path = " > ".join([title, *(item[1] for item in heading_stack)])
        current = {
            "level": level,
            "title": heading,
            "path": full_path,
            "lines": [],
        }
    finish()

    if not raw_sections:
        raw_sections.append(
            {
                "heading_level": 1,
                "title": title or "Untitled",
                "heading_path": title or "Untitled",
                "body": description or "(No textual content)",
            }
        )

    sections: list[dict[str, Any]] = []
    seen_anchors: dict[str, int] = {}
    for position, section in enumerate(raw_sections):
        body = section.pop("body").strip()
        knowledge_type = classify_knowledge_type(
            section["title"], position=position, category=category
        )
        base_anchor = heading_anchor(section["title"])
        seen_anchors[base_anchor] = seen_anchors.get(base_anchor, 0) + 1
        duplicate_suffix = (
            f"-{seen_anchors[base_anchor]}"
            if seen_anchors[base_anchor] > 1
            else ""
        )
        source_anchor = (
            source_url
            if position == 0
            else f"{source_url}#{base_anchor}{duplicate_suffix}"
        )
        source_line = f"> DOC source: [{source_anchor}]({source_anchor})"
        heading = "#" * min(max(section["heading_level"], 1), 6)
        content_md = f"{heading} {section['title']}\n\n{body}\n\n{source_line}".strip()
        text = plain_text(body)
        # Full marks only with a body and an official source URL.
        quality_score = (
            1.0
            if text and (ws := active()).source.is_official_url(ws.dataset, source_url)
            else 0.7
        )
        sections.append(
            {
                **section,
                "position": position,
                "knowledge_type": knowledge_type,
                "source_anchor": source_anchor,
                "body_md": body,
                "content_md": content_md,
                "content_text": text,
                "source_url": source_url,
                "token_estimate": max(1, (len(text) + 3) // 4),
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "quality_score": quality_score,
            }
        )
    return sections
