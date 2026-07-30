"""What is on this machine, what state it is in, and the one command that moves
it forward.

Deliberately usable **before anything is set up**: the moment someone most needs
to be told what to do next is the moment nothing is configured yet. So this
module imports no dataset-bound name — `docatlas.cli` resolves the current
dataset while being imported, which is exactly the failure this is meant to
explain rather than reproduce.

Every other command answers for one library. This one answers for the machine.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import clients
from .constants import CHUNKER_VERSION
from .runtime import (
    DATASET_CONFIG_DIR,
    DatasetNotChosen,
    available_dataset_ids,
    default_dataset_id,
    local_settings,
    workspace,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
# The adapter the shipped template names. A dataset built on it documents an
# invented site, so it is a thing to copy rather than a thing to crawl.
TEMPLATE_SOURCE = "example"
SKILL_NAME = "docatlas"


def _env_prefix(dataset_id: str, *, windows: bool | None = None) -> str:
    """How to name a non-default library for one command, in this shell.

    PowerShell has no `VAR=value command` form, so the POSIX spelling printed
    to a Windows user is not a hint but a command that fails.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return f"$env:DOCATLAS_DATASET='{dataset_id}'; "
    return f"DOCATLAS_DATASET={dataset_id} "


def _counts(connection: sqlite3.Connection) -> dict[str, Any]:
    pages = dict(
        connection.execute(
            "SELECT COALESCE(status, '(none)'), COUNT(*) FROM pages GROUP BY 1"
        )
    )
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    stale = [
        version
        for (version,) in connection.execute(
            "SELECT DISTINCT COALESCE(parser_version, '(none)') FROM pages"
            " WHERE status = 'success'"
        )
        if version != CHUNKER_VERSION
    ]
    try:
        assets_pending = connection.execute(
            "SELECT COUNT(*) FROM assets"
            " WHERE status IN ('pending', 'failed') AND attempts < 6"
        ).fetchone()[0]
    except sqlite3.Error:
        assets_pending = 0  # a library built before assets existed
    return {
        "pages_fetched": pages.get("success", 0),
        "pages_listed": sum(pages.values()),
        "pages_failed": pages.get("failed", 0),
        "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "inventory": metadata.get("inventory_status", "incomplete"),
        "stale_chunker_versions": stale,
        "assets_pending": assets_pending,
    }


def inspect_dataset(dataset_id: str, *, is_default: bool) -> dict[str, Any]:
    """One library: what it holds and what it needs next.

    A broken config is reported rather than raised. A diagnostic that dies on
    the first bad entry cannot diagnose the machine it was run on.
    """
    report: dict[str, Any] = {"id": dataset_id, "default": is_default}
    try:
        space = workspace(dataset_id)
    except Exception as error:  # a malformed or unreadable toml
        report["state"] = "broken config"
        report["detail"] = f"{type(error).__name__}: {error}"
        report["next"] = f"fix datasets/{dataset_id}.toml"
        return report

    report["product"] = space.dataset.product
    report["version"] = space.dataset.version
    report["database"] = str(space.db_path)
    prefix = "" if is_default else _env_prefix(dataset_id)

    if space.dataset.source == TEMPLATE_SOURCE:
        # The shipped template describes an invented site, so "crawl it" would
        # send a newcomer at a host that does not resolve. It is a worked
        # example to copy, and saying so is the useful answer.
        report["state"] = "template"
        report["detail"] = (
            "a worked example for an invented site, so there is nothing to"
            " crawl; copy it to describe real documentation"
        )
        report["next"] = "see datasets/EXAMPLE.toml and docatlas/sources/example.py"
        return report

    if not space.db_path.exists():
        report["state"] = "not built"
        report["detail"] = "no database yet"
        report["next"] = f"{prefix}python -m docatlas crawl --discovery-only"
        return report

    connection = sqlite3.connect(f"file:{space.db_path}?mode=ro", uri=True)
    try:
        report.update(_counts(connection))
    finally:
        connection.close()

    if report["pages_listed"] == 0:
        report["state"] = "not built"
        report["detail"] = "database created but no pages listed yet"
        report["next"] = f"{prefix}python -m docatlas crawl --discovery-only"
    elif report["inventory"] != "complete":
        report["state"] = "inventory incomplete"
        report["detail"] = f"{report['pages_listed']:,} pages listed so far"
        report["next"] = f"{prefix}python -m docatlas crawl --discovery-only"
    elif report["stale_chunker_versions"]:
        # Ahead of everything else: the library is readable but the stored
        # chunks were built by rules this code no longer uses.
        found = ", ".join(sorted(report["stale_chunker_versions"]))
        report["state"] = "needs reprocessing"
        report["detail"] = f"chunked by {found}, this build writes {CHUNKER_VERSION}"
        report["next"] = f"{prefix}python -m docatlas reprocess"
    elif report["pages_fetched"] == 0:
        report["state"] = "ready (inventory only)"
        report["detail"] = (
            f"{report['pages_listed']:,} pages listed, none fetched yet"
            " — pages are fetched on demand, so this is already usable"
        )
        report["next"] = f'{prefix}python -m docatlas ask "<a term from these docs>"'
    else:
        report["state"] = "ready"
        report["detail"] = (
            f"{report['pages_fetched']:,} of {report['pages_listed']:,} pages"
            f" fetched, {report['chunks']:,} chunks"
        )
        if report["pages_fetched"] < report["pages_listed"]:
            report["next"] = f"{prefix}python -m docatlas crawl --skip-discovery"
        else:
            report["next"] = None
    return report


def inspect_skill() -> dict[str, Any]:
    """Where the Skill is installed, and whether it still matches this checkout.

    The Skill is a **snapshot**: it names one library and quotes this manual as
    they both were at install time. Comparing the installed copy against a fresh
    rendering is what catches the two ways it silently goes stale — a different
    library became the default, or the repository moved on.
    """
    found = {
        client: clients.skill_dir(client, SKILL_NAME)
        for client in clients.NAMES
        if (clients.skill_dir(client, SKILL_NAME) / "SKILL.md").exists()
    }
    report: dict[str, Any] = {
        "installed": sorted(found),
        # Where each client was looked for, so a Skill written somewhere the
        # client does not read is visible rather than reported as installed.
        "looked_in": {client: str(clients.home(client)) for client in clients.NAMES},
        "moved_by": {
            client: variable
            for client in clients.NAMES
            if (variable := clients.override(client))
        },
    }
    if not found:
        report["state"] = "not installed"
        report["next"] = "python install.py"
        return report

    try:
        # Imported here, not at module scope: it resolves the current dataset,
        # which is the very thing that may not be settled yet.
        from .cli import skill_substitutions
    except DatasetNotChosen:
        report["state"] = "cannot check (no default library chosen)"
        report["next"] = "python install.py --dataset <id>"
        return report

    substitutions = skill_substitutions()
    stale = []
    for client, target in found.items():
        for template in sorted((REPO_ROOT / "skills" / "docatlas").glob("*.md")):
            text = template.read_text(encoding="utf-8")
            for name, value in substitutions.items():
                text = text.replace("{{" + name + "}}", value)
            installed = target / template.name
            if not installed.exists() or installed.read_text(encoding="utf-8") != text:
                stale.append(client)
                break
    report["stale"] = sorted(set(stale))
    if stale:
        report["state"] = "out of date"
        report["next"] = "python install.py"
    else:
        report["state"] = "up to date"
        report["next"] = None
    return report


def inspect_mcp() -> dict[str, Any]:
    """Which clients have the MCP server registered.

    Read-only and by file: asking each client's own CLI would be slower, and
    some of them are not on PATH even when installed.
    """
    registered = []
    claude = clients.mcp_config("claude-code")
    if claude.exists():
        try:
            if "docatlas" in json.loads(claude.read_text(encoding="utf-8")).get(
                "mcpServers", {}
            ):
                registered.append("claude-code")
        except (json.JSONDecodeError, OSError):
            pass
    codex = clients.mcp_config("codex")
    if codex.exists() and "[mcp_servers.docatlas]" in codex.read_text(
        encoding="utf-8", errors="replace"
    ):
        registered.append("codex")
    return {
        "registered": registered,
        "state": "registered" if registered else "not registered",
        "next": None if registered else "python install.py",
    }


def collect() -> dict[str, Any]:
    settings = local_settings()
    try:
        chosen: str | None = default_dataset_id()
    except DatasetNotChosen:
        chosen = None
    ids = available_dataset_ids()
    return {
        "program": str(REPO_ROOT),
        "datasets_dir": str(DATASET_CONFIG_DIR),
        "data_root": str(settings.get("home") or REPO_ROOT / "data"),
        "default_dataset": chosen,
        "default_pinned": bool(settings.get("dataset")),
        "libraries": [
            inspect_dataset(dataset_id, is_default=dataset_id == chosen)
            for dataset_id in ids
        ],
        "skill": inspect_skill(),
        "mcp": inspect_mcp(),
    }


def _render(report: dict[str, Any]) -> str:
    lines = [
        "DocAtlas",
        f"  program   {report['program']}",
        f"  data      {report['data_root']}",
    ]
    if report["default_dataset"]:
        pinned = "pinned by install.py" if report["default_pinned"] else "the only one here"
        lines.append(f"  default   {report['default_dataset']}  ({pinned})")
    else:
        lines.append(
            "  default   none chosen — every command needs a library named,"
            "\n            settle it with: python install.py --dataset <id>"
        )

    libraries = report["libraries"]
    lines.append("")
    lines.append(f"Libraries ({len(libraries)})")
    if not libraries:
        lines.append("  none — add a .toml under datasets/ (copy EXAMPLE.toml)")
    for library in libraries:
        mark = " *" if library["default"] else ""
        lines.append("")
        lines.append(f"  {library['id']}{mark}")
        lines.append(f"    {library['state']} — {library.get('detail', '')}")
        if pending := library.get("assets_pending"):
            noun = "image" if pending == 1 else "images"
            lines.append(
                f"    note: {pending:,} referenced {noun} not downloaded"
                " (optional; links still point at the site)"
            )
        if library.get("next"):
            lines.append(f"    next: {library['next']}")

    lines.append("")
    lines.append("Agent setup")
    skill, mcp = report["skill"], report["mcp"]
    where = ", ".join(skill["installed"]) or "nowhere"
    lines.append(f"  Skill   {skill['state']} ({where})")
    if skill.get("next"):
        lines.append(f"          next: {skill['next']}")
    lines.append(f"  MCP     {mcp['state']} ({', '.join(mcp['registered']) or 'nowhere'})")
    if mcp.get("next"):
        lines.append(f"          next: {mcp['next']}")
    # Printed every time. A Skill written where the client does not read it
    # looks identical to a working install from both sides, so the only
    # defence is that the paths are on screen to be disagreed with.
    for client, location in skill.get("looked_in", {}).items():
        moved = skill.get("moved_by", {}).get(client)
        lines.append(f"  {client:14s}{location}" + (f"  (via ${moved})" if moved else ""))
    lines.append("")
    return "\n".join(lines)


def run(as_json: bool = False) -> int:
    report = collect()
    print(json.dumps(report, indent=2, ensure_ascii=False) if as_json else _render(report))
    return 0
