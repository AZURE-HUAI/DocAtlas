"""把正文切成带标题层级的小节与知识块。"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from .constants import HEADING_RE, KNOWLEDGE_TYPE_RULES
from .runtime import active
from .htmlmd import plain_text
# 这几个小工具搬到 text.py 了（适配器和知识包要用，不能绕回 config）；
# 这里继续导出，老的 from .chunking import normalize_name 照常可用。
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
        # API 参考的开头是"这个函数干嘛"的摘要；教程的开头是全篇概述。
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


# 有些小节的标题名对不上它的内容，不能拿标题名当结论。
#
# 典型例子是 Epic 蓝图页的 `Navigation`：名字像面包屑，实际上除了面包屑，
# 还装着**这个节点是干嘛的**那句描述，以及 `Target is X`——全页最有用的信息
# 都在里面。所以这类小节不能丢，但也不该由它来决定合并后那一块算什么类型，
# 否则整块会被当成导航，在检索里被扣分扣到底。
WEAK_TYPE_LABELS = frozenset({"navigation"})

# 面包屑：一整行里除了链接就只有分隔符。
# 例：`[BlueprintAPI](…) > [BlueprintAPI/Camera](…)`
_BREADCRUMB_RE = re.compile(
    r"^[ \t]*\[[^\]]*\]\([^)\s]*\)"
    r"(?:[ \t]*[>›»][ \t]*\[[^\]]*\]\([^)\s]*\))+[ \t]*$",
    re.M,
)


def strip_breadcrumbs(markdown: str) -> str:
    """去掉导航面包屑那一行。

    面包屑是页面装饰，不是内容。留着它，整站的目录名和两三条 URL 会进到
    **每一页**的全文索引里，于是"Blueprint Camera 怎么改视野"这样的查询会
    命中该目录下的每一页——命中的全是面包屑，正文一个词都没对上。真正想要
    的那一页反而被一堆同目录的兄弟页挤下去。

    页面在站点里的位置，`source_url` 和 `context_prefix` 已经各存了一份，
    不需要第三份。

    只认"两个以上链接串成的一整行"：正文里孤零零的一个链接不会被误删。
    """
    return _BREADCRUMB_RE.sub("", markdown)

# 切分后剩下的尾巴短于这个字符数，就并回上一块，别留一条二十来字的孤块。
RUNT_TAIL_CHARS = 400


def _parent_path(heading_path: str) -> str:
    """`Page > A > x` → `Page > A`。用来判断两个小节是不是同一个话题下的。"""
    head, separator, _tail = heading_path.rpartition(" > ")
    return head if separator else heading_path


def group_sections_for_chunking(
    sections: list[dict[str, Any]], target_chars: int
) -> list[list[dict[str, Any]]]:
    """把连续的小段落攒成一组，再去切块。

    为什么需要这一步：按标题切出来的小节里，有大量像 `Inputs` 这种
    只有一行表头的段落。单独成块的话，一块只有二三十个 token，
    既答不了问题又占检索名额。把同一个父标题下相邻的小段落攒到
    目标大小，才是一条能用的知识。

    只在两种情况下断开：换了父标题（话题变了），或者攒够了目标大小。
    本来就超标的大段落自己单独一组，走原来的切分逻辑。
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
    """合并后这一块算什么类型。

    取第一个"名副其实"的类型。像 `navigation` 这种标题名和内容对不上的，
    不该代表整块——一块含着节点描述、参数和返回值的内容被标成"导航"，
    检索时会被一路扣分扣到底。
    """
    for section in group:
        if section["knowledge_type"] not in WEAK_TYPE_LABELS:
            return section["knowledge_type"]
    return group[0]["knowledge_type"]


def merge_sections(group: list[dict[str, Any]]) -> dict[str, Any]:
    """把一组小节拼成一个"合成小节"，交给原来的切块逻辑处理。

    子标题原样保留在正文里——合并是为了让一块内容足够完整，
    不是把 `Inputs` / `Outputs` 这些标签抹掉。
    """
    if len(group) == 1:
        return group[0]
    first = group[0]
    parts = []
    for section in group:
        heading = "#" * min(max(section["heading_level"], 1), 6)
        parts.append(f"{heading} {section['title']}\n\n{section['body_md']}".strip())
    parent = _parent_path(first["heading_path"])
    # 明确列出每个字段，不用 **first：合成小节的正文已经不是第一个小节的了，
    # 顺手继承过来的 content_hash / token_estimate 会是错的，留着迟早被人误用。
    return {
        # 归属到组里的第一个小节：chunks 表要求 section_id 非空，
        # 而这一组在文档里本来就是从它开始的。
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
    """一整页的切块入口：先攒小的，再切大的。"""
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
    # 最后剩的一小截并回上一块：一条二十来字的孤块既答不了问题，
    # 又白占一个检索名额。并回去顶多让上一块略超目标，仍在硬上限内。
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
            # 产品和版本都来自数据集：写死 "UE" 会让 Blender 的知识块也标成 UE。
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
        # 有正文、且出处是官方地址，才算满分。
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
