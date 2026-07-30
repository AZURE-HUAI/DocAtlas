"""Command line entry point."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterator

from .config import (
    CATEGORY_LABELS,
    CATEGORY_IDS,
    DATA_DIR,
    DATA_ROOT,
    DATASET,
    DATASET_ID,
    DB_PATH,
    LANGUAGE,
    REPO_ROOT,
)
from .runtime import bind
from .util import log, set_log_file
from .net import REQUEST_LIMITER
from .db import connect_db, initialize_db
from .discover import discover_inventory
from .documents import fetch_document
from .store import store_document_result
from .crawl import crawl_documents, reprocess_stored_documents
from .assets import download_assets
from .coverage import admit_linked_targets
from .relations import build_cross_index, link_new_pages
from .search import chunk_or_section, knowledge_id, search_chunks
from .export import export_markdown
from .reports import write_reports, write_site_inventory
from .context import (
    answer,
    build_context_pack,
    describe_lookup,
    exact_page_hint,
    related_payload,
    render_context_markdown,
)
from .ondemand import (
    DEFAULT_FETCH_LIMIT,
    fetch_now,
    find_uncrawled_candidates,
    inventory_lookup,
)
from .validate import validate_contract
from .versions import MODES as VERSION_MODES, parse_intent as parse_version_intent


def require_inventory(connection: sqlite3.Connection) -> None:
    """On an empty library, say what to do next instead of returning a blank."""
    if connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]:
        return
    raise SystemExit(
        f"Dataset {DATASET_ID} has no page inventory yet (data dir: {DATA_DIR}).\n"
        "Enumerate the site inventory first; this reads feeds only, no bodies:\n"
        "    python -m docatlas crawl --discovery-only\n"
        "Once enumerated you can query straight away — pages missing locally are "
        "fetched on demand, so there is no need to download the whole site first."
    )


def require_complete_inventory(connection: sqlite3.Connection, refusal: str) -> None:
    """Refuse the body stage until the inventory is frozen as complete.

    A library crawled from half an inventory cannot tell "the site does not have
    this page" from "we have not enumerated it yet", and the next step for those
    two is opposite. Better to stop here.
    """
    row = connection.execute(
        "SELECT value FROM metadata WHERE key='inventory_status'"
    ).fetchone()
    if not row or row[0] != "complete":
        raise SystemExit(
            f"The page inventory is not frozen as complete, {refusal}.\n"
            "Run crawl --discovery-only first and confirm zero failed feeds."
        )


def print_json(payload: Any) -> None:
    """Every subcommand's JSON goes through here, so the shape stays consistent."""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@contextlib.contextmanager
def opened(*, needs_inventory: bool = False) -> Iterator[sqlite3.Connection]:
    """Open the current dataset's database, closing it whatever happens.

    Each subcommand used to write its own connect / initialize / ... / close, and
    an exception anywhere in between skipped the closing call. A CLI process exits
    and the OS cleans up, so nothing looked wrong; but the same sequence was copied
    into a dozen early-return branches such as `--json`, and one missing close is a
    silent leak. As one context manager, close is written once, inside finally.
    """
    connection = connect_db()
    try:
        initialize_db(connection)
        if needs_inventory:
            require_inventory(connection)
        yield connection
    finally:
        connection.close()


def command_crawl(args: argparse.Namespace) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    set_log_file(args.log_file)
    REQUEST_LIMITER.configure(args.requests_per_second)
    with opened() as connection:
        if args.skip_discovery:
            if not connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]:
                raise SystemExit("no page inventory yet; --skip-discovery needs one")
            require_complete_inventory(connection, "refusing to start the body stage")
        else:
            discover_inventory(
                connection,
                workers=args.sitemap_workers,
                refresh=args.refresh_sitemaps,
            )
        if args.discovery_only:
            print_json(write_site_inventory(connection))
            print_json(write_reports(connection))
            return 0
        if not args.skip_discovery:
            if write_site_inventory(connection)["status"] != "complete":
                raise SystemExit(
                    "The page inventory is still incomplete; the body stage did "
                    "not start. Re-run the discover stage to finish failed feeds."
                )
        crawl_documents(
            connection,
            workers=args.workers,
            delay=args.delay,
            max_pages=args.max_pages,
            sample_per_category=args.sample_per_category,
            refresh=args.refresh_pages,
            category=args.category,
        )
        relation_stats = build_cross_index(connection)
        log(
            "Cross index: "
            f"entities {relation_stats['entities']:,}; "
            f"relations {relation_stats['relations']:,}"
        )
        if args.download_assets:
            download_assets(
                connection,
                workers=args.asset_workers,
                max_assets=args.max_assets,
            )
        if args.export:
            export_markdown(connection, shard_mb=args.shard_mb)
        print_json(write_reports(connection, manifest=True))
    return 0


def command_assets(args: argparse.Namespace) -> int:
    REQUEST_LIMITER.configure(args.requests_per_second)
    with opened() as connection:
        download_assets(
            connection, workers=args.workers, max_assets=args.max_assets
        )
        print_json(write_reports(connection))
    return 0


def command_reprocess(args: argparse.Namespace) -> int:
    with opened(needs_inventory=True) as connection:
        processed = reprocess_stored_documents(
            connection, limit=args.limit, force=args.force
        )
        print_json(
            {"reprocessed_pages": processed, "stats": write_reports(connection)}
        )
    return 0


def command_fetch_pages(args: argparse.Namespace) -> int:
    REQUEST_LIMITER.configure(args.requests_per_second)
    with opened() as connection:
        require_complete_inventory(connection, "refusing to fetch targeted bodies")
        placeholders = ",".join("?" for _ in args.page_ids)
        rows = list(
            connection.execute(
                f"""
                SELECT id, url, path, category FROM pages
                WHERE id IN ({placeholders})
                ORDER BY id
                """,
                args.page_ids,
            )
        )
        if len(rows) != len(set(args.page_ids)):
            missing = sorted(set(args.page_ids) - {row["id"] for row in rows})
            raise SystemExit(f"page id(s) not found: {missing}")
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers
        ) as executor:
            future_to_row = {
                executor.submit(bind(fetch_document), row, 0.05): row for row in rows
            }
            for future in concurrent.futures.as_completed(future_to_row):
                row = future_to_row[future]
                result = future.result()
                store_document_result(connection, result, row["category"])
                connection.commit()
                log(
                    f"Targeted page {row['id']}: "
                    f"{'ok' if result['ok'] else result['error']}"
                )
        print_json(build_cross_index(connection))
    return 0


def command_export(args: argparse.Namespace) -> int:
    with opened() as connection:
        export_markdown(connection, shard_mb=args.shard_mb)
        print_json(write_reports(connection))
    return 0


def command_search(args: argparse.Namespace) -> int:
    with opened(needs_inventory=True) as connection:
        rows = search_chunks(
            connection, args.query, limit=args.limit, category=args.category
        )
        hint = exact_page_hint(connection, args.query, args.category)
        if args.json:
            # Empty and non-empty results share one shape: a caller should not need
            # two parsers for "did this match anything". When empty, an extra lookup
            # says which kind of nothing it was.
            payload: dict[str, Any] = {"results": [dict(row) for row in rows]}
            if hint:
                payload["exact_page_pending"] = True
            if not rows:
                payload["lookup"] = inventory_lookup(
                    connection, args.query, category=args.category
                )
            print_json(payload)
            return 0 if rows else 1
        if not rows:
            for line in describe_lookup(
                inventory_lookup(connection, args.query, category=args.category)
            ):
                print(line)
            return 1
        for index, row in enumerate(rows, 1):
            label = CATEGORY_LABELS.get(row["category"], row["category"])
            print(
                f"\n[{index}] knowledge id K{row['id']} | "
                f"{row['page_title']} — {row['heading_path']}"
            )
            print(f"category: {label}   stage: {row['match_stage']}   score: {row['score']}")
            print(f"knowledge type: {row['knowledge_type']}")
            print(f"snippet: {row['snippet']}")
            print(f"DOC source: {row['source_url']}")
        for line in hint:
            print(f"\n{line}" if line.startswith("Hint") else line)
    return 0


def command_show(args: argparse.Namespace) -> int:
    numeric_id = knowledge_id(str(args.section_id))
    if numeric_id is None:
        print(f"unreadable knowledge id: {args.section_id!r} (expected form: K9290)")
        return 2
    with opened(needs_inventory=True) as connection:
        row = chunk_or_section(connection, numeric_id)
        if not row:
            print(f"knowledge id {args.section_id} not found.")
            return 1
        print_json(dict(row)) if args.json else print(row["content_md"])
    return 0


def command_context(args: argparse.Namespace) -> int:
    """Machine-readable context pack (JSON), for programs."""
    with opened(needs_inventory=True) as connection:
        print_json(
            build_context_pack(
                connection,
                args.query,
                token_budget=args.token_budget,
                category=args.category,
            )
        )
    return 0


def command_ask(args: argparse.Namespace) -> int:
    """One command for directly readable answer material. Used by AI and humans.

    Pages missing locally are fetched automatically, so there is no need to crawl
    the whole site up front.
    """
    try:
        version_intent = parse_version_intent(
            args.version_mode, args.version_target
        )
    except ValueError as exc:
        print(exc)
        return 2
    with opened(needs_inventory=True) as connection:
        REQUEST_LIMITER.configure(0)
        payload = answer(
            connection,
            args.query,
            token_budget=args.token_budget,
            category=args.category,
            allow_fetch=not args.no_fetch,
            fetch_limit=args.fetch_limit,
            quiet=args.json,
            version_intent=version_intent,
        )
        print_json(payload) if args.json else print(render_context_markdown(payload))
    return 0 if payload["primary_knowledge"] else 1


def command_get(args: argparse.Namespace) -> int:
    """On-demand fetch: bring back only the pages that are actually needed."""
    with opened(needs_inventory=True) as connection:
        REQUEST_LIMITER.configure(0)
        candidates = find_uncrawled_candidates(
            connection, args.query, limit=args.limit, category=args.category
        )
        if not candidates:
            lookup = inventory_lookup(connection, args.query, category=args.category)
            if lookup["crawled_pages"]:
                print(f'already held locally, just query it: ask "{args.query}"')
                for page in lookup["crawled_pages"]:
                    print(f"  {page['path']}")
            else:
                for line in describe_lookup(lookup):
                    print(line)
            return 1
        print(f"about to fetch {len(candidates)} page(s):")
        for row in candidates:
            label = CATEGORY_LABELS.get(row["category"], row["category"])
            print(f"  [{label}] {row['path']}")
        outcome = fetch_now(connection, candidates)
        outcome["relations"] = link_new_pages(
            connection, [page["page_id"] for page in outcome["pages"]]
        )
        print(
            f"\nDone: ok {outcome['succeeded']}; failed {outcome['failed']}; "
            f"new relations {outcome['relations']}"
        )
        for page in outcome["pages"]:
            print(f"  ✓ {page['title'] or page['path']}")
        if outcome["succeeded"]:
            print(f'\nready to query: ask "{args.query}"')
    return 0 if outcome["succeeded"] else 1


def command_cross_index(_: argparse.Namespace) -> int:
    with opened() as connection:
        print_json(build_cross_index(connection))
    return 0


def command_related(args: argparse.Namespace) -> int:
    """One-hop cross relations. Implemented in context.related_payload; MCP shares it."""
    with opened(needs_inventory=True) as connection:
        result = related_payload(connection, str(args.subject).strip())
        print_json(result)
    return 0 if result["status"] == "ok" else 1


def command_inventory(args: argparse.Namespace) -> int:
    with opened() as connection:
        if args.admit_linked or args.show_linked:
            print_json(
                admit_linked_targets(
                    connection,
                    limit=args.limit,
                    min_links=args.min_links,
                    dry_run=args.show_linked,
                )
            )
            if args.show_linked:
                return 0
        print_json(write_site_inventory(connection))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    with opened() as connection:
        payload = validate_contract(connection, args.phase)
        print_json(payload)
    return 0 if payload["status"] == "pass" else 1


def command_stats(args: argparse.Namespace) -> int:
    with opened() as connection:
        print_json(write_reports(connection, manifest=args.manifest))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    """Report the whole machine rather than one library.

    Registered here so it shows up in `--help` like everything else, but
    `__main__` dispatches it before importing this module as well: the state it
    exists to explain includes "no dataset chosen", which stops that import.
    """
    from .doctor import run

    return run(as_json=args.json)


def command_mcp(_: argparse.Namespace) -> int:
    """Run as an MCP server, for MCP-capable AI clients."""
    from .mcpserver import serve  # imported only when MCP is actually run

    return serve()


def command_paths(_: argparse.Namespace) -> int:
    """Report where the data lives. PowerShell scripts use this to locate logs and
    databases, so the path rules are written once in config.py and nowhere else.
    """
    from .runtime import available_dataset_ids

    print_json(
        {
            "dataset": DATASET_ID,
            # The source language is the dataset's to declare, not the program's to
            # assume. Skill installation writes it into the instructions so the AI
            # knows which language to phrase queries in.
            "language": LANGUAGE,
            # Which libraries exist here. Without MCP this is the only way to find
            # them, short of reading the datasets/ directory — which is exactly what
            # the skill manual should not have to teach.
            "datasets": available_dataset_ids(),
            "repo_root": str(REPO_ROOT),
            "data_root": str(DATA_ROOT),
            "data_dir": str(DATA_DIR),
            "database": str(DB_PATH),
            "exists": DB_PATH.exists(),
        }
    )
    return 0


def skill_substitutions() -> dict[str, str]:
    """Placeholders in the skill docs -> the current dataset's actual content.

    The skill docs are an operating manual for an AI, so their wording has to stay
    generic: "which library is installed" is filled in from the dataset and must
    never be hardcoded. Hardcoding it assumes everyone installed the same docs.
    """
    from .mcpserver import TOOLS
    from .runtime import available_dataset_ids
    from .validate import expected_evidence_kinds

    return {
        # Tool names come from the MCP server itself, so the manual can never name
        # a tool that does not exist.
        "DOCATLAS_MCP_TOOLS": ", ".join(f"`{tool['name']}`" for tool in TOOLS),
        "DOCATLAS_DATASETS": ", ".join(f"`{key}`" for key in available_dataset_ids()),
        # `as_posix`, not `str`: on Windows the spelling of this path follows
        # whichever shell started Python — PowerShell yields backslashes, Git
        # Bash forward slashes — so `str` makes the rendered document depend on
        # how the installer happened to be launched. Two installs from two
        # shells would then differ byte for byte, and any check comparing the
        # installed copy against a fresh rendering reports a permanent false
        # "out of date". Forward slashes are valid in every shell here.
        "DOCATLAS_ROOT": REPO_ROOT.as_posix(),
        "DATASET_ID": DATASET_ID,
        "DATASET_NAME": DATASET.name,
        "DATASET_LANGUAGE": LANGUAGE,
        "DATASET_CATEGORIES": ", ".join(
            CATEGORY_LABELS.get(key, key) for key in DATASET.query_categories
        ),
        "DATASET_CATEGORY_IDS": " / ".join(
            f"`{key}`" for key in DATASET.query_categories
        ),
        "DATASET_TRIGGERS": ", ".join(DATASET.skill_triggers) or "(not configured)",
        "DATASET_EVIDENCE_KINDS": ", ".join(
            f"`{kind}`" for kind in expected_evidence_kinds()
        ),
    }


def command_render_skill(args: argparse.Namespace) -> int:
    """Fill the skill template for the current dataset and print it; the installer
    puts the result in place.

    Filling happens on the Python side because only Python knows the dataset. The
    installer then only has to place files, and a rewrite for another platform need
    not copy any of this knowledge.
    """
    template = Path(args.template)
    if not template.exists():
        print(f"template not found: {template}", file=sys.stderr)
        return 1
    text = template.read_text(encoding="utf-8")
    for name, value in skill_substitutions().items():
        text = text.replace("{{" + name + "}}", value)
    if leftover := sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", text))):
        print(f"{template.name} has unknown placeholders: {', '.join(leftover)}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"DocAtlas local documentation knowledge base (dataset: {DATASET_ID})"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl = subparsers.add_parser("crawl", help="discover and fetch official docs")
    crawl.add_argument("--workers", type=int, default=8, help="body concurrency")
    crawl.add_argument(
        "--sitemap-workers", type=int, default=2, help="feed concurrency"
    )
    crawl.add_argument(
        "--asset-workers", type=int, default=8, help="image concurrency"
    )
    crawl.add_argument(
        "--delay", type=float, default=0.0, help="polite delay after each success (s)"
    )
    crawl.add_argument(
        "--requests-per-second",
        type=float,
        default=0.0,
        help="max request rate across all threads; 0 means no self-imposed limit "
        "(the crawler still backs off when the server answers 429/403)",
    )
    crawl.add_argument(
        "--max-pages", type=int, default=0, help="max pages this run; 0 means all"
    )
    crawl.add_argument(
        "--sample-per-category",
        type=int,
        default=0,
        help="fetch only N pages per category, for acceptance checks",
    )
    crawl.add_argument(
        "--skip-discovery", action="store_true", help="skip feed discovery"
    )
    crawl.add_argument(
        "--discovery-only",
        action="store_true",
        help="build the page inventory only, fetching no bodies",
    )
    crawl.add_argument(
        "--refresh-sitemaps", action="store_true", help="re-read successful feeds"
    )
    crawl.add_argument(
        "--refresh-pages", action="store_true", help="re-read successful pages"
    )
    crawl.add_argument(
        "--download-assets", action="store_true", help="download referenced images"
    )
    crawl.add_argument(
        "--max-assets", type=int, default=0, help="max images to download this run"
    )
    crawl.add_argument(
        "--export", action="store_true", help="write Markdown shards after fetching"
    )
    crawl.add_argument(
        "--shard-mb", type=int, default=8, help="target Markdown shard size"
    )
    crawl.add_argument(
        "--category",
        choices=list(CATEGORY_IDS),
        help="fetch only this category, e.g. to finish one that lags behind",
    )
    crawl.add_argument(
        "--log-file", help="also write a UTF-8 progress log"
    )
    crawl.set_defaults(func=command_crawl)

    assets = subparsers.add_parser("assets", help="fetch missing referenced images")
    assets.add_argument("--workers", type=int, default=8)
    assets.add_argument("--requests-per-second", type=float, default=0.0)
    assets.add_argument("--max-assets", type=int, default=0)
    assets.set_defaults(func=command_assets)

    reprocess = subparsers.add_parser(
        "reprocess", help="re-split knowledge from stored raw JSON, offline"
    )
    reprocess.add_argument("--limit", type=int, default=0)
    reprocess.add_argument(
        "--force",
        action="store_true",
        help="redo pages already at the current chunker version (default does only "
        "the outstanding ones, so it resumes after an interruption)",
    )
    reprocess.set_defaults(func=command_reprocess)

    fetch_pages = subparsers.add_parser(
        "fetch-pages", help="fetch bodies for specific page ids, for sampling"
    )
    fetch_pages.add_argument("page_ids", type=int, nargs="+")
    fetch_pages.add_argument("--workers", type=int, default=2)
    fetch_pages.add_argument("--requests-per-second", type=float, default=0.0)
    fetch_pages.set_defaults(func=command_fetch_pages)

    export = subparsers.add_parser("export", help="write AI-friendly Markdown shards")
    export.add_argument("--shard-mb", type=int, default=8)
    export.set_defaults(func=command_export)

    search = subparsers.add_parser("search", help="full-text query across the library")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--category", choices=list(CATEGORY_IDS))
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    show = subparsers.add_parser("show", help="read a full section by knowledge id")
    show.add_argument("section_id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=command_show)

    ask = subparsers.add_parser(
        "ask",
        help="recommended entry point: readable answer material within a token "
        "budget (Markdown)",
    )
    ask.add_argument("query")
    ask.add_argument(
        "--token-budget", type=int, default=3000, help="context budget, default 3000"
    )
    ask.add_argument("--category", choices=list(CATEGORY_IDS))
    ask.add_argument("--json", action="store_true", help="emit structured JSON instead")
    ask.add_argument(
        "--no-fetch",
        action="store_true",
        help="answer from local content only, never fetching",
    )
    ask.add_argument(
        "--fetch-limit",
        type=int,
        default=DEFAULT_FETCH_LIMIT,
        help=f"max pages to fetch automatically, default {DEFAULT_FETCH_LIMIT}",
    )
    # Version intent is decided by the asker (or the AI above) and passed in;
    # DocAtlas never guesses it.
    ask.add_argument(
        "--version-target",
        metavar="VERSION",
        help="limit to a version, spelled as that library spells it",
    )
    ask.add_argument(
        "--version-mode",
        choices=list(VERSION_MODES),
        help=(
            "strict=only what exists in that version; migration=trace changes "
            "between versions, promoting content that states the difference; "
            "compare=keep everything and mark which version each applies to; "
            "any=no limit. Given only --version-target, strict is used."
        ),
    )
    ask.set_defaults(func=command_ask)

    get = subparsers.add_parser(
        "get", help="on-demand fetch: bring back only the pages needed"
    )
    # The example comes from the dataset's own triggers. A hardcoded symbol name
    # would point users of every other library at something it does not contain.
    get.add_argument(
        "query",
        help="page name or official URL"
        + (f", e.g. {', '.join(DATASET.skill_triggers[:2])}" if DATASET.skill_triggers else ""),
    )
    get.add_argument("--limit", type=int, default=DEFAULT_FETCH_LIMIT)
    get.add_argument("--category", choices=list(CATEGORY_IDS))
    get.set_defaults(func=command_get)

    context_parser = subparsers.add_parser(
        "context", help="same as ask but always JSON, for programmatic callers"
    )
    context_parser.add_argument("query")
    context_parser.add_argument("--token-budget", type=int, default=3000)
    context_parser.add_argument("--category", choices=list(CATEGORY_IDS))
    context_parser.set_defaults(func=command_context)

    cross_index = subparsers.add_parser(
        "cross-index", help="resolve official links and build entity relations"
    )
    cross_index.set_defaults(func=command_cross_index)

    related = subparsers.add_parser(
        "related", help="one-hop cross relations for a chunk or entity"
    )
    related.add_argument("subject", help="knowledge id (e.g. K123) or entity name")
    related.set_defaults(func=command_related)

    inventory = subparsers.add_parser(
        "inventory", help="freeze and validate the page inventory"
    )
    inventory.add_argument(
        "--show-linked",
        action="store_true",
        help="report only: in-scope bodies reference these, the inventory lacks "
        "them (writes nothing)",
    )
    inventory.add_argument(
        "--admit-linked",
        action="store_true",
        help="collect those referenced pages into the inventory (status pending, "
        "one hop only)",
    )
    inventory.add_argument(
        "--min-links", type=int, default=1, help="minimum references to collect one"
    )
    inventory.add_argument(
        "--limit", type=int, default=None, help="max pages to collect this run"
    )
    inventory.set_defaults(func=command_inventory)

    validate = subparsers.add_parser(
        "validate", help="validate the inventory or body stage against the contract"
    )
    validate.add_argument(
        "--phase", choices=("inventory", "content"), default="inventory"
    )
    validate.set_defaults(func=command_validate)

    stats = subparsers.add_parser("stats", help="print coverage and failure stats")
    stats.add_argument(
        "--manifest",
        action="store_true",
        help="also rewrite the per-page manifest.jsonl (near 100 MB, off by default)",
    )
    stats.set_defaults(func=command_stats)

    paths = subparsers.add_parser("paths", help="print dataset and data directory paths")
    paths.set_defaults(func=command_paths)

    doctor = subparsers.add_parser(
        "doctor", help="what is installed, the state of every library, what to do next"
    )
    doctor.add_argument("--json", action="store_true", help="report as JSON")
    doctor.set_defaults(func=command_doctor)

    render = subparsers.add_parser(
        "render-skill", help="fill the skill template for this dataset and print it"
    )
    render.add_argument("template", help="path to the template file")
    render.set_defaults(func=command_render_skill)

    mcp = subparsers.add_parser(
        "mcp", help="run as an MCP server, for MCP-capable clients"
    )
    mcp.set_defaults(func=command_mcp)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        log("interrupted; committed batches will resume on the next run")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
