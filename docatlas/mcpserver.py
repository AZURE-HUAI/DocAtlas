"""MCP server: let MCP-capable AI clients query this knowledge base directly.

Once installed, clients such as Claude Desktop, Cursor or Cline get tools like
`docatlas_ask` without anyone having to remember command lines.

**No MCP SDK, on purpose.** The whole project needs no third-party package, so
installing it means "if you have Python, it runs". The MCP stdio transport is
line-delimited JSON-RPC 2.0, and only three methods need implementing
(initialize / tools/list / tools/call) — under two hundred lines with the standard
library, which is not worth a dependency tree and its version conflicts.

This layer is a **thin shell**: it reimplements no retrieval logic and forwards
everything to ask / search / related, so the CLI and MCP can never disagree.

**One server serves every dataset on the machine.** Every tool takes an optional
`dataset_id`, defaulting to the one chosen at startup. Locking one process to one
library would force a second server entry in the client config to reach a second
library, which in practice nobody adds.

Tool inputs and outputs are **domain-neutral**: nothing here knows any particular
product or its vocabulary. Categories, relation types and evidence kinds are all
declared by the dataset and discovered through `docatlas_list_datasets` rather
than written into the protocol.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import runtime
from .context import (
    answer,
    describe_fragment,
    describe_lookup,
    exact_page_hint,
    related_payload,
    render_context_markdown,
)
from .db import connect_db, initialize_db
from .net import REQUEST_LIMITER
from .ondemand import DEFAULT_FETCH_LIMIT, inventory_lookup
from .search import chunk_or_section, knowledge_id, search_chunks
from .text import script_mismatch
from . import versions


SERVER_NAME = "docatlas"
SERVER_VERSION = "2.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

# Contract version for structured results. Bump when a field changes meaning, so a
# caller can tell whether its existing parsing still holds.
CONTRACT_VERSION = "1"


class ToolError(Exception):
    """An error the caller can fix (no such dataset, misspelled category, unbuilt
    library).

    Kept apart from program bugs: these must state an actionable next step rather
    than hand back a traceback.
    """


def _dataset_catalogue() -> list[tuple[str, str]]:
    """Datasets on this machine as (id, name). Broken configs are skipped rather
    than allowed to take the whole server down.

    `SystemExit` has to be caught because `load_dataset` uses it to report config
    errors, which is right for a CLI, and it derives from `BaseException`, so
    `except Exception` misses it. But **only that one**: catching `BaseException`
    would swallow Ctrl-C too, so interrupting the startup listing would be read as
    "this config cannot be read" before moving on to the next.
    """
    catalogue = []
    for dataset_id in runtime.available_dataset_ids():
        try:
            catalogue.append((dataset_id, runtime.workspace(dataset_id).name))
        except (Exception, SystemExit):  # noqa: BLE001  config errors raise SystemExit
            catalogue.append((dataset_id, "(config unreadable)"))
    return catalogue


def _dataset_hint() -> str:
    listing = "; ".join(f"{key} ({name})" for key, name in _dataset_catalogue())
    listing = listing or "(none)"
    try:
        fallback = f"Omit to use the default ({runtime.active().id}). "
    except runtime.DatasetNotChosen:
        # No built-in default, and more than one library present: this field becomes
        # required, and the tool schema has to say so or the AI will not know.
        fallback = "This machine has no default library, so it is required. "
    return f"Which documentation library to query. {fallback}Available: {listing}"


_DATASET_PROPERTY = {"type": "string", "description": _dataset_hint()}
_FORMAT_PROPERTY = {
    "type": "string",
    "enum": ["markdown", "json"],
    "default": "markdown",
    "description": (
        "markdown is for humans and AIs to read and costs a third fewer tokens "
        "than JSON for the same content, so it is the default. Use json only when "
        "you need stable fields to parse."
    ),
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "docatlas_ask",
        "description": (
            "Query the local technical documentation knowledge base and get answer "
            "material trimmed to a token budget (body text + source URLs + pointers "
            "to related items). Prefer this over recalling documentation from "
            "memory. Pages not held locally are fetched automatically, usually in a "
            "second or two. Use docatlas_list_datasets to see which libraries exist "
            "and what each one covers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look up. Official spelling in the dataset's own "
                        "source language is usually the most accurate; proper "
                        "nouns, verbatim error text and code symbols work best "
                        "unchanged. If the hits are poor, rephrase and try again."
                    ),
                },
                "dataset_id": _DATASET_PROPERTY,
                "token_budget": {
                    "type": "integer",
                    "description": "Hard context budget. 1500 for simple questions, 3000 default, 6000 to read a page through.",
                    "default": 3000,
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Restrict to a category. Optional. Categories differ per "
                        "dataset; check docatlas_list_datasets. A wrong value "
                        "returns the library's legal values."
                    ),
                },
                "no_fetch": {
                    "type": "boolean",
                    "description": "Never fetch; answer from local content only.",
                    "default": False,
                },
                "fetch_limit": {
                    "type": "integer",
                    "description": "Max pages to fetch when needed. Default 5, which suffices; raising it only slows things down.",
                    "default": DEFAULT_FETCH_LIMIT,
                },
                "version_target": {
                    "type": "string",
                    "description": (
                        "The version the user means, spelled as that library "
                        "spells it. You decide the version intent and pass it in; "
                        "DocAtlas never infers it from the question. See "
                        "version_vocabulary in docatlas_list_datasets for the "
                        "spelling a library accepts."
                    ),
                },
                "version_mode": {
                    "type": "string",
                    "enum": list(versions.MODES),
                    "description": (
                        "strict=the user pinned that version, so exclude anything "
                        "that did not exist in it; migration=the user is asking "
                        "what something used to be and what replaced it, so "
                        "content stating the difference is promoted and older "
                        "content is the answer rather than noise; compare=several "
                        "versions are being contrasted, so drop nothing and mark "
                        "what each applies to; any=no limit. Given only "
                        "version_target, strict is used."
                    ),
                },
                "format": _FORMAT_PROPERTY,
            },
            "required": ["query"],
        },
    },
    {
        "name": "docatlas_search",
        "description": (
            "Lists titles, knowledge types, how each matched, scores and sources, "
            "without body text. Use it when unsure which entry to read and you want "
            "to scan a table of contents first. For answers, use docatlas_ask."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords."},
                "dataset_id": _DATASET_PROPERTY,
                "limit": {"type": "integer", "description": "Max results", "default": 10},
                "category": {"type": "string", "description": "Restrict to a category"},
                "format": _FORMAT_PROPERTY,
            },
            "required": ["query"],
        },
    },
    {
        "name": "docatlas_show",
        "description": (
            "Expand one entry's full body by knowledge id. The id comes from the "
            "K<number> in docatlas_ask / docatlas_search results. Ids are valid only "
            "within the same library and the same build of it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Knowledge id, e.g. K9290 or 9290",
                },
                "dataset_id": _DATASET_PROPERTY,
            },
            "required": ["chunk_id"],
        },
    },
    {
        "name": "docatlas_related",
        "description": (
            "Cross relations for a name or knowledge id: what it belongs to, which "
            "interface it corresponds to, what type it acts on. Each carries a "
            "direction, evidence kind, confidence, note and source URL. Get the body "
            "with docatlas_ask first, then use this to connect related things. When "
            "nothing is found it distinguishes: no such entity, no such id, the "
            "entity exists but has no relations, a relation target is not fetched "
            "yet, and a relation target is outside the inventory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Entity name, or a K<number> from docatlas_ask",
                },
                "dataset_id": _DATASET_PROPERTY,
                "format": _FORMAT_PROPERTY,
            },
            "required": ["subject"],
        },
    },
    {
        "name": "docatlas_list_datasets",
        "description": (
            "List every documentation library on this machine, with each one's "
            "product, version, language, categories, which relation and evidence "
            "kinds it produces, and how far its data has been built. Use it before "
            "answering when unsure which library to query, or whether a library "
            "covers this kind of content at all."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": "Report on one library only; omit for all.",
                },
                "format": _FORMAT_PROPERTY,
            },
        },
    },
]


def _workspace(arguments: dict[str, Any]) -> runtime.Workspace:
    """Select a library by dataset_id; without one, use the installed default."""
    dataset_id = str(arguments.get("dataset_id") or "").strip()
    if not dataset_id:
        try:
            return runtime.active()
        except runtime.DatasetNotChosen as exc:
            # More than one library and no default ever set. This is not a fault but
            # a missing argument — say so, so the AI supplies dataset_id and retries
            # rather than reading it as "nothing found".
            raise ToolError(f"{exc} Or pass dataset_id directly in this call.") from exc
    try:
        return runtime.workspace(dataset_id)
    except SystemExit as exc:
        # load_dataset reports config errors with SystemExit, which is right for a
        # CLI. Uncaught inside the server, one misspelled dataset_id would exit the
        # whole MCP process: SystemExit derives from BaseException, so a plain
        # `except Exception` does not catch it.
        raise ToolError(str(exc)) from exc


def _open(workspace: runtime.Workspace):
    """Open this library; if never built, say what to do rather than create an empty one."""
    if not workspace.db_path.exists():
        raise ToolError(
            f"Dataset {workspace.id} ({workspace.name}) has no data yet.\n"
            f"Enumerate the site inventory first (feeds only, no bodies):\n"
            f"    $env:DOCATLAS_DATASET='{workspace.id}'\n"
            f"    python -m docatlas crawl --discovery-only"
        )
    connection = connect_db(workspace.db_path)
    initialize_db(connection)
    return connection


def _check_category(workspace: runtime.Workspace, category: str | None) -> None:
    known = workspace.dataset.query_categories
    if category and category not in known:
        raise ToolError(
            f"{workspace.id} has no category {category!r}. "
            f"Available: {', '.join(sorted(known)) or '(no categories declared)'}"
        )


def _identity(workspace: runtime.Workspace) -> dict[str, Any]:
    return {
        "dataset_id": workspace.id,
        "name": workspace.name,
        "product": workspace.dataset.product,
        "version": workspace.version,
        "language": workspace.language,
    }


def tool_ask(arguments: dict[str, Any]) -> Any:
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ToolError("A query is required.")
    workspace = _workspace(arguments)
    category = arguments.get("category")
    _check_category(workspace, category)
    with runtime.use(workspace):
        # Version intent resolves in this library's context: what counts as a legal
        # version spelling is the dataset's call.
        try:
            version_intent = versions.parse_intent(
                arguments.get("version_mode"), arguments.get("version_target")
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        connection = _open(workspace)
        REQUEST_LIMITER.configure(0)
        try:
            # The same answer() the CLI calls, so the two can never diverge.
            # quiet=True because stdout belongs to the protocol; not one byte may
            # go there.
            pack = answer(
                connection,
                query,
                token_budget=int(arguments.get("token_budget") or 3000),
                category=category,
                allow_fetch=not arguments.get("no_fetch"),
                fetch_limit=int(arguments.get("fetch_limit") or DEFAULT_FETCH_LIMIT),
                quiet=True,
                version_intent=version_intent,
            )
            if arguments.get("format") != "json":
                return render_context_markdown(pack)
            return _structured_ask(workspace, pack)
        finally:
            connection.close()


def _structured_ask(
    workspace: runtime.Workspace, pack: dict[str, Any]
) -> dict[str, Any]:
    lookup = pack.get("lookup") or {}
    # Four kinds of "nothing", four different next steps. Flattening them into one
    # no_match would leave the caller guessing.
    if pack["primary_knowledge"]:
        status = "ok"
    elif lookup.get("pending_pages"):
        status = "pages_not_fetched"
    elif lookup.get("weak_candidates"):
        status = "candidates_too_weak"
    elif lookup.get("linked_targets"):
        status = "target_outside_inventory"
    elif script_mismatch(pack["query"], workspace.language):
        status = "language_mismatch"
    else:
        status = "no_match"
    result = {
        "contract_version": CONTRACT_VERSION,
        "dataset": _identity(workspace),
        "query": pack["query"],
        "status": status,
        "token_budget": pack["token_budget"],
        "estimated_tokens": pack["estimated_tokens"],
        "knowledge": [
            {
                "knowledge_id": f"K{item['id']}",
                "title": item["page_title"],
                "heading_path": item["heading_path"],
                "category": item["category"],
                "knowledge_type": item["knowledge_type"],
                "tokens": item["token_estimate"],
                "source_url": item["source_url"],
                "content_md": item["content_md"],
                # Present only when the document actually states an applicable
                # version. Absent is not the same as "applies to every version";
                # it only means this passage does not say. Draw no conclusion.
                **(
                    {"applies_to": item["applies_to"]}
                    if item.get("applies_to")
                    else {}
                ),
            }
            for item in pack["primary_knowledge"]
        ],
        "relations": [
            {
                "relation_type": item["relation_type"],
                "related_name": item["canonical_name"],
                "related_type": item["entity_type"],
                "evidence_kind": item["evidence_kind"],
                "confidence": item["confidence"],
                "note": item["note"],
                "evidence_url": item["source_url"],
                "expand_knowledge_id": (
                    f"K{item['expand_chunk_id']}" if item["expand_chunk_id"] else None
                ),
            }
            for item in pack["one_hop_relations"]
        ],
    }
    if fetch := pack.get("on_demand_fetch"):
        result["fetch"] = {
            "requested": fetch["requested"],
            "succeeded": fetch["succeeded"],
            "failed": fetch["failed"],
        }
    # Echo the version conditions back verbatim: that is how a caller can tell
    # whether "I did not see X" means a version filter removed it or it was never
    # there. Filtering silently is the hardest kind of error to trace.
    if applied := pack.get("version_intent"):
        result["version_intent"] = {
            **applied,
            "explanation": versions.describe(applied),
        }
    # Likewise, whether a `#fragment` in the URL was used must be echoed back.
    # Failing to find that section and silently returning the whole page leaves the
    # caller believing what it sees is the section the user meant.
    if fragment := pack.get("fragment_intent"):
        result["fragment_intent"] = {
            **fragment,
            "explanation": describe_fragment(fragment),
        }
    if status != "ok":
        result["next_steps"] = describe_lookup(lookup) if lookup else []
    return result


def tool_search(arguments: dict[str, Any]) -> Any:
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ToolError("A query is required.")
    workspace = _workspace(arguments)
    category = arguments.get("category")
    _check_category(workspace, category)
    with runtime.use(workspace):
        connection = _open(workspace)
        try:
            rows = search_chunks(
                connection,
                query,
                limit=int(arguments.get("limit") or 10),
                category=category,
            )
            if arguments.get("format") == "json":
                lookup = (
                    inventory_lookup(connection, query, category=category)
                    if not rows
                    else {}
                )
                return {
                    "contract_version": CONTRACT_VERSION,
                    "dataset": _identity(workspace),
                    "query": query,
                    "status": "ok" if rows else "no_match",
                    "results": [
                        {
                            "knowledge_id": f"K{row['id']}",
                            "title": row["page_title"],
                            "heading_path": row["heading_path"],
                            "category": row["category"],
                            "knowledge_type": row["knowledge_type"],
                            "match_stage": row["match_stage"],
                            "score": row["score"],
                            "snippet": row["snippet"],
                            "source_url": row["source_url"],
                        }
                        for row in rows
                    ],
                    "next_steps": describe_lookup(lookup) if lookup else [],
                }
            if not rows:
                return "\n".join(
                    describe_lookup(
                        inventory_lookup(connection, query, category=category)
                    )
                )
            labels = workspace.category_labels
            lines = []
            for index, row in enumerate(rows, 1):
                label = labels.get(row["category"], row["category"])
                lines.append(
                    f"[{index}] K{row['id']} | {row['page_title']} — {row['heading_path']}\n"
                    f"    category: {label}  type: {row['knowledge_type']}  "
                    f"stage: {row['match_stage']}  score: {row['score']}\n"
                    f"    {row['snippet']}\n"
                    f"    DOC source: {row['source_url']}"
                )
            lines.extend(exact_page_hint(connection, query, category))
            return "\n".join(lines)
        finally:
            connection.close()


def tool_show(arguments: dict[str, Any]) -> Any:
    raw_id = str(arguments.get("chunk_id") or "").strip()
    numeric_id = knowledge_id(raw_id)
    if numeric_id is None:
        raise ToolError(f"Unreadable knowledge id: {raw_id!r} (expected form: K9290)")
    workspace = _workspace(arguments)
    with runtime.use(workspace):
        connection = _open(workspace)
        try:
            row = chunk_or_section(connection, numeric_id)
            if row is None:
                raise ToolError(
                    f"{workspace.id} has no knowledge id {raw_id}. Ids are valid "
                    "only within the same library and the same build of it — a "
                    "rebuild changes them, so do not reuse ids from older results. "
                    "Get a currently valid one with docatlas_search first."
                )
            return row["content_md"]
        finally:
            connection.close()


def tool_related(arguments: dict[str, Any]) -> Any:
    subject = str(arguments.get("subject") or "").strip()
    if not subject:
        raise ToolError("An entity name or knowledge id is required.")
    workspace = _workspace(arguments)
    with runtime.use(workspace):
        connection = _open(workspace)
        try:
            result = related_payload(connection, subject)
            if arguments.get("format") == "json":
                return {
                    "contract_version": CONTRACT_VERSION,
                    "dataset": _identity(workspace),
                    **result,
                }
            return _render_related(workspace, result)
        finally:
            connection.close()


def _render_related(workspace: runtime.Workspace, result: dict[str, Any]) -> str:
    lines = [f"status: {result['status']}"]
    # List the entities found even when there are no relations: "no such thing" and
    # "found it, but it connects to nothing" are two different answers to a caller.
    for item in result["entities"]:
        entity = item["entity"]
        lines.append("")
        lines.append(f"## {entity['name']} ({entity['type']})")
        lines.append(f"   {entity['source_url']}")
        for relation in item["relations"]:
            kind = workspace.relation_labels.get(
                relation["relation_type"], relation["relation_type"]
            )
            evidence = workspace.evidence_labels.get(
                relation["evidence_kind"], relation["evidence_kind"]
            )
            lines.append(
                f"   - {relation['relation_type']} ({kind})"
                f" -> {relation['related_name']}"
                f" ({relation['related_type']}, {relation['direction']})"
                f"  evidence: {evidence}"
                f"  confidence {relation['confidence']:.2f}"
            )
            lines.append(f"     source: {relation['evidence_url']}")
            if relation["note"]:
                lines.append(f"     note: {relation['note']}")
    if result["next_steps"]:
        lines.extend(["", *result["next_steps"]])
    return "\n".join(lines)


def _dataset_report(workspace: runtime.Workspace) -> dict[str, Any]:
    """One library's identity, capabilities and build progress.

    "Capabilities" are declared by the dataset rather than hardcoded here:
    categories come from config, and relation and evidence kinds from the knowledge
    pack. Without a knowledge pack the generic official-link relations still apply.
    """
    report: dict[str, Any] = {
        **_identity(workspace),
        "categories": {
            key: workspace.category_labels.get(key, key)
            for key in sorted(workspace.dataset.query_categories)
        },
        "knowledge_pack": workspace.dataset.knowledge,
        "source_adapter": workspace.dataset.source,
        "relation_types": sorted(workspace.relation_labels),
        "evidence_kinds": sorted(
            {"official_link", *workspace.hook("DERIVED_EVIDENCE_KINDS", ())}
        ),
        "triggers": list(workspace.dataset.skill_triggers),
    }
    # Whether version filtering is possible, and in what spelling, has to be
    # discoverable, or a caller can only guess a version_target and be told the
    # library does not recognize it.
    with runtime.use(workspace):
        if versions.supported():
            report["version_vocabulary"] = versions.vocabulary() or workspace.version
            report["version_modes"] = list(versions.MODES)
    if not workspace.db_path.exists():
        report["state"] = "not_built"
        return report
    connection = connect_db(workspace.db_path)
    try:
        counted = dict(
            connection.execute(
                "SELECT"
                " (SELECT COUNT(*) FROM pages) AS pages,"
                " (SELECT COUNT(*) FROM pages WHERE status='success') AS fetched,"
                " (SELECT COUNT(*) FROM chunks) AS knowledge,"
                " (SELECT COUNT(*) FROM relations) AS relations"
            ).fetchone()
        )
    except Exception:  # noqa: BLE001  a half-built library must not break the listing
        report["state"] = "unreadable"
        return report
    finally:
        connection.close()
    report["state"] = "ready" if counted["knowledge"] else "inventory_only"
    report["counts"] = counted
    return report


def tool_list_datasets(arguments: dict[str, Any]) -> Any:
    only = str(arguments.get("dataset_id") or "").strip()
    ids = [only] if only else runtime.available_dataset_ids()
    reports = []
    for dataset_id in ids:
        try:
            reports.append(_dataset_report(runtime.workspace(dataset_id)))
        except SystemExit as exc:
            if only:
                raise ToolError(str(exc)) from exc
            reports.append({"dataset_id": dataset_id, "state": "broken_config"})
    # The entire point of this tool is finding out what the machine holds, so it
    # cannot itself require a default library to be chosen — having no default is
    # exactly when it is most needed to work out whether to pass dataset_id.
    try:
        default = runtime.active().id
    except runtime.DatasetNotChosen:
        default = None
    if arguments.get("format") == "json":
        return {
            "contract_version": CONTRACT_VERSION,
            "default_dataset_id": default,
            "datasets": reports,
        }
    lines = (
        [f"Default dataset: {default} (used when dataset_id is omitted)", ""]
        if default
        else ["No default library here; pass dataset_id on every call.", ""]
    )
    for report in reports:
        marker = "→" if report["dataset_id"] == default else " "
        state = {
            "ready": "ready to query",
            "inventory_only": "inventory only, bodies not fetched",
            "not_built": "no data yet",
            "unreadable": "database unreadable",
            "broken_config": "config broken",
        }.get(report.get("state", ""), report.get("state", ""))
        lines.append(
            f"{marker} {report['dataset_id']}"
            + (
                f" — {report.get('name')} ({report.get('product')} "
                f"{report.get('version')}, source language {report.get('language')})"
                if report.get("name")
                else ""
            )
        )
        lines.append(f"    state: {state}")
        if counts := report.get("counts"):
            lines.append(
                f"    inventory {counts['pages']:,} pages, fetched {counts['fetched']:,}, "
                f"chunks {counts['knowledge']:,}, relations {counts['relations']:,}"
            )
        if categories := report.get("categories"):
            lines.append("    categories: " + ", ".join(categories))
        if report.get("relation_types"):
            lines.append("    relation types: " + ", ".join(report["relation_types"]))
        lines.append("")
    return "\n".join(lines).rstrip()


HANDLERS = {
    "docatlas_ask": tool_ask,
    "docatlas_search": tool_search,
    "docatlas_show": tool_show,
    "docatlas_related": tool_related,
    "docatlas_list_datasets": tool_list_datasets,
}


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    """A tool's return value -> an MCP result.

    A string goes back as text; a structured result goes back as both
    `structuredContent` and a JSON text copy, because the protocol requires a
    client that does not understand `structuredContent` to be able to read the same
    thing from the text. Returning Markdown by default is precisely so that not
    every query pays for that JSON in tokens.
    """
    if isinstance(value, str):
        return {"content": [{"type": "text", "text": value}], "isError": is_error}
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": value,
        "isError": is_error,
    }


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """The response to send back; None for notifications."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        # Echo back the protocol version the client declared: the three methods used
        # here are stable across versions, and asserting one would make older clients
        # refuse the handshake outright.
        requested = (message.get("params") or {}).get("protocolVersion")
        return _result(
            request_id,
            {
                "protocolVersion": requested or DEFAULT_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"No such tool: {name}")
        try:
            value = handler(params.get("arguments") or {})
        except ToolError as exc:
            # An error the caller can fix: state it plainly, no traceback.
            return _result(request_id, _tool_result(str(exc), is_error=True))
        except Exception:  # a bug still returns as a result, never kills the link
            return _result(
                request_id,
                _tool_result("Query failed:\n" + traceback.format_exc(), is_error=True),
            )
        return _result(request_id, _tool_result(value))

    if method == "ping":
        return _result(request_id, {})

    if request_id is None:
        return None
    return _error(request_id, -32601, f"Unsupported method: {method}")


def serve(stdin=None, stdout=None) -> int:
    """Run MCP over stdin/stdout.

    The protocol owns stdout, so every log line must go to stderr — one byte written
    to stdout makes the client fail to parse.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    catalogue = ", ".join(key for key, _ in _dataset_catalogue())
    try:
        opening = f"default dataset {runtime.active().id}"
    except runtime.DatasetNotChosen:
        # No default must not stop the server: every tool can carry its own
        # dataset_id, so it remains fully usable.
        opening = "no default dataset; pass dataset_id when calling"
    print(
        f"[docatlas] MCP server started. {opening}; available: {catalogue}",
        file=sys.stderr,
        flush=True,
    )
    for line in stdin:
        # Strip a BOM while here: Windows clients easily write one at the start of
        # the pipe (.NET flushes the UTF-8 preamble as soon as StandardInput is
        # taken), and json cannot parse a line carrying it.
        line = line.strip().lstrip("﻿").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # Dropping a bad line is right, but doing it silently is not: the client
            # sees a server that started and then never answers, with nothing to go
            # on.
            print(
                f"[docatlas] skipped an unparseable request line: {line[:120]}",
                file=sys.stderr,
                flush=True,
            )
            continue
        response = handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0
