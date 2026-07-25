"""把正文切成带标题层级的小节与知识块。"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from .config import CATEGORY_LABELS, HEADING_RE, KNOWLEDGE_TYPE_RULES, VERSION
from .htmlmd import plain_text


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def humanize_cpp_identifier(value: str) -> str:
    value = value.split("::")[-1].replace("_", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def heading_anchor(value: str) -> str:
    anchor = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return anchor or "content"


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
        if category in {"blueprint_api", "cpp_api", "python_api", "node_reference"}:
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


def chunk_section(
    section: dict[str, Any],
    *,
    page_title: str,
    category: str,
    document_type: str | None,
    target_chars: int = 2200,
    max_chars: int = 3200,
) -> list[dict[str, Any]]:
    if not plain_text(section["body_md"]):
        return []
    units = markdown_units(section["body_md"], max_chars)
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
    if not bodies:
        bodies = [section["body_md"] or "(No textual content)"]

    chunk_count = len(bodies)
    label = CATEGORY_LABELS.get(category, category)
    context_prefix = " | ".join(
        part
        for part in (
            f"UE {VERSION}",
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
        part_suffix = f"（{index + 1}/{chunk_count}）" if chunk_count > 1 else ""
        title = f"{section['title']}{part_suffix}"
        heading = "#" * min(max(section["heading_level"], 1), 6)
        source_line = (
            f"> DOC 原出处：[{section['source_anchor']}]"
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


def split_sections(
    *,
    title: str,
    description: str,
    markdown: str,
    source_url: str,
    category: str,
) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    raw_sections: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    current = {
        "level": 1,
        "title": title or "Untitled",
        "path": title or "Untitled",
        "lines": [],
    }
    if description and normalize_name(description) != normalize_name(title):
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

    for line in lines:
        match = HEADING_RE.match(line)
        if not match:
            current["lines"].append(line)
            continue
        finish()
        level = len(match.group(1))
        heading = match.group(2).strip()
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
        source_line = f"> DOC 原出处：[{source_anchor}]({source_anchor})"
        heading = "#" * min(max(section["heading_level"], 1), 6)
        content_md = f"{heading} {section['title']}\n\n{body}\n\n{source_line}".strip()
        text = plain_text(body)
        quality_score = 1.0 if text and source_url.startswith(
            "https://dev.epicgames.com/documentation/"
        ) else 0.7
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
