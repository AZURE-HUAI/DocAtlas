"""Data contract validation."""

from __future__ import annotations

import collections
import json
import sqlite3
from typing import Any
import zlib

from . import relations
from .constants import CHUNKER_VERSION
from .runtime import active
from .util import utc_now

# How many stored bodies to sample when checking language. Decompressing all of
# them is slow, and this failure mode is all-or-nothing, so a sample suffices.
LOCALE_SAMPLE_SIZE = 300


def fetched_locales(connection: sqlite3.Connection) -> collections.Counter:
    """Which language versions the server actually returned, counted by document.

    A dataset's `language` is an instruction, not a fact: when a site lacks the
    language you asked for, it usually does not error but quietly returns its
    default. Without checking, you end up with an English library labelled German.
    """
    read_locale = getattr(active().source, "document_locale", None)
    if read_locale is None:
        return collections.Counter()
    seen: collections.Counter = collections.Counter()
    for (blob,) in connection.execute(
        "SELECT raw_json FROM raw_documents WHERE raw_json IS NOT NULL"
        " ORDER BY RANDOM() LIMIT ?",
        (LOCALE_SAMPLE_SIZE,),
    ):
        try:
            payload = json.loads(zlib.decompress(blob))
        except (zlib.error, ValueError):
            continue  # broken archives are another check's business; language only
        locale = read_locale(payload)
        if locale:
            seen[locale.lower()] += 1
    return seen


def expected_evidence_kinds() -> list[str]:
    """Which kinds of relation evidence this dataset ought to produce.

    Official links are generic and exist on any documentation site; member tables
    only appear when the source adapter recognizes them; the rest are declared by
    the knowledge pack as what it will infer.
    """
    from .members import supported as members_supported

    kinds = ["official_link"]
    if members_supported():
        kinds.append("page_member_table")
    kinds.extend(active().hook("DERIVED_EVIDENCE_KINDS", ()))
    return kinds


def link_coverage_observation(connection: sqlite3.Connection) -> dict[str, Any]:
    """Whether the source inventory missed directories other pages link into.

    Deliberately **not** treated as a contract violation: whether an official
    sitemap lists a directory is the site's own business, and failing the library
    for it would turn a healthy build red for no reason. But it must be said out
    loud — "other pages link there and the inventory has it not" is the only early
    signal that the source scope was drawn too narrowly, and unreported it surfaces
    only when a user cannot find something.
    """
    gaps = relations.link_target_gaps(connection)
    areas = "; ".join(
        f"{item['area']} ({item['links']} links)"
        for item in gaps["top_uncovered_areas"]
    )
    return {
        "name": "inventory_link_coverage",
        "pending_targets": gaps["pending_targets"],
        "missing_targets": gaps["missing_targets"],
        "uncovered_areas": gaps["uncovered_areas"],
        "detail": (
            f"{gaps['pending_targets']:,} links point at pages the inventory has "
            "but whose bodies are not fetched yet (get or ask will fill them in)."
            + (
                f"A further {gaps['missing_targets']:,} point at pages the inventory "
                f"lacks, in {gaps['uncovered_areas']} unenumerated directories: {areas}. "
                "These need the source adapter's enumeration scope changed; no "
                "amount of fetching will produce them."
                if gaps["uncovered_areas"]
                else "No wholly missing directories found."
            )
        ),
    }


def validate_contract(
    connection: sqlite3.Connection, phase: str
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, failures: int, requirement: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "pass" if failures == 0 else "fail",
                "failures": failures,
                "requirement": requirement,
            }
        )

    feeds_total, feeds_ok = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(status='success'), 0) FROM sitemaps"
    ).fetchone()
    add(
        "inventory_feeds_complete",
        feeds_total - feeds_ok,
        f"every inventory feed must be read successfully ({feeds_total}, ok {feeds_ok})",
    )
    # Counting only "non-conforming rows" is not enough: a freshly created empty
    # library has no rows, so nothing fails, everything passes and the exit code is
    # 0. Empty is not the same as valid, so confirm separately that it holds data.
    page_total = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    add(
        "inventory_not_empty",
        0 if feeds_ok and page_total else 1,
        "a dataset needs at least one successful inventory feed and one page; "
        f"currently {feeds_ok} feed(s) and {page_total:,} page(s). "
        "Zero means `crawl --discovery-only` never ran, or failed part way",
    )
    counts = {
        row["category"]: row["count"]
        for row in connection.execute(
            "SELECT category, COUNT(*) AS count FROM pages GROUP BY category"
        )
    }
    # A category declared in config that enumerated no pages is nearly always a
    # mistaken category rule — an error that raises nothing and merely leaves a
    # whole class of documentation quietly absent.
    required = [
        key
        for key in active().dataset.categories
        if key not in active().dataset.optional_categories
    ]
    empty = [key for key in required if not counts.get(key)]
    add(
        "declared_categories_have_pages",
        len(empty),
        "every category declared in config must enumerate pages: "
        + (
            ", ".join(f"{key} is 0" for key in empty)
            + " (list genuinely-empty categories in optional_categories)"
            if empty
            else ", ".join(f"{key}x{counts.get(key, 0):,}" for key in required)
            or "(no categories declared)"
        ),
    )
    add(
        "page_inventory_metadata",
        connection.execute(
            """
            SELECT COUNT(*) FROM pages
            WHERE url IS NULL OR path IS NULL OR category IS NULL
               OR doc_version IS NULL OR locale IS NULL
               OR route_depth IS NULL
            """
        ).fetchone()[0],
        "pages must carry a path, category, version and language",
    )
    # Inventory feeds are deliberately excluded from the check above: pages arrive
    # by two routes, listed by a feed, or referenced by an in-scope body
    # (`coverage.admit_linked_targets`, which leaves `sitemap_url` empty). Making
    # "must have a sitemap" a hard requirement would forbid the second route.
    add(
        "page_provenance",
        connection.execute(
            "SELECT COUNT(*) FROM pages WHERE sitemap_url IS NULL"
            " AND path NOT IN (SELECT target_path FROM page_links"
            "                  WHERE target_path IS NOT NULL)"
        ).fetchone()[0],
        "a page with no inventory feed must have been referenced by a body",
    )
    duplicate_paths = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT path FROM pages GROUP BY path HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    add("unique_page_paths", duplicate_paths, "normalized page paths must be unique")
    if phase == "content":
        add(
            "successful_page_raw_revision",
            connection.execute(
                """
                SELECT COUNT(*) FROM pages p
                WHERE p.status='success'
                  AND NOT EXISTS (
                      SELECT 1 FROM raw_documents r WHERE r.page_id=p.id
                  )
                """
            ).fetchone()[0],
            "every successful page must have a stored raw revision",
        )
        add(
            "successful_page_entity",
            connection.execute(
                """
                SELECT COUNT(*) FROM pages p
                WHERE p.status='success'
                  AND NOT EXISTS (
                      SELECT 1 FROM entities e WHERE e.page_id=p.id
                  )
                """
            ).fetchone()[0],
            "every successful page must have a primary entity",
        )
        add(
            "chunk_required_fields",
            connection.execute(
                """
                SELECT COUNT(*) FROM chunks
                WHERE trim(title)='' OR trim(heading_path)=''
                   OR trim(content_text)='' OR trim(source_url)=''
                   OR trim(source_anchor)='' OR trim(knowledge_type)=''
                   OR token_estimate <= 0
                """
            ).fetchone()[0],
            "chunks need a title, level, body, type, source and token estimate",
        )
        add(
            "chunk_size_limit",
            connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE token_estimate > 900"
            ).fetchone()[0],
            "a chunk must not be estimated above 900 tokens",
        )
        add(
            "relation_evidence",
            connection.execute(
                """
                SELECT COUNT(*) FROM relations
                WHERE trim(evidence_kind)='' OR trim(source_url)=''
                   OR confidence < 0 OR confidence > 1
                """
            ).fetchone()[0],
            "relations need evidence, a source and a valid confidence",
        )
        add(
            "chunk_parser_version",
            connection.execute(
                "SELECT COUNT(*) FROM pages WHERE status='success'"
                " AND COALESCE(parser_version,'')!=?",
                (CHUNKER_VERSION,),
            ).fetchone()[0],
            f"every successful page must be processed by chunker {CHUNKER_VERSION}",
        )
        add(
            "chunk_neighbour_pointers",
            connection.execute(
                """
                SELECT COUNT(*) FROM chunks c
                WHERE c.next_chunk_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM chunks n
                      WHERE n.id=c.next_chunk_id AND n.page_id=c.page_id
                  )
                """
            ).fetchone()[0],
            "neighbour pointers must reference real chunks on the same page",
        )
        # A processing change can wipe out an entire kind of evidence, which
        # "it ran without error" will never reveal; only the per-kind output counts
        # will. Dropping one kind of section once zeroed a whole class of relations.
        expected = expected_evidence_kinds()
        missing = [
            kind
            for kind in expected
            if connection.execute(
                "SELECT COUNT(*) FROM relations WHERE evidence_kind=?", (kind,)
            ).fetchone()[0]
            == 0
        ]
        add(
            "relation_evidence_coverage",
            len(missing),
            "every expected kind of relation evidence must actually be present: "
            + (", ".join(missing) + " has none" if missing else ", ".join(expected)),
        )
        # Language is **chosen**, not guessed, so it cannot be filled in
        # automatically; but whether the choice took effect can be checked.
        locales = fetched_locales(connection)
        language = active().language
        wrong = sum(n for code, n in locales.items() if code != language.lower())
        add(
            "fetched_language_matches_declaration",
            wrong,
            f"fetched bodies should all be in the declared {language}"
            + (
                ", sampled: " + ", ".join(f"{c}x{n}" for c, n in locales.most_common())
                if wrong
                else ""
            ),
        )
    failed = sum(1 for check in checks if check["status"] == "fail")
    report = {
        "phase": phase,
        "status": "pass" if failed == 0 else "fail",
        "checked_at": utc_now(),
        "checks": checks,
    }
    if phase == "content":
        # Observations take no part in pass/fail: they are worth knowing, not
        # contract violations.
        report["observations"] = [link_coverage_observation(connection)]
    return report
