"""The answer layer: the single implementation of `ask` and `related`.

Both the CLI and MCP call in here, so the two entry points cannot give different
answers — each having its own notion of "should we fetch" is how they drift apart.

This layer's central duty is **protecting the context**. Retrieval can find dozens
of relevant items, but handing an AI all of them only buries the real answer. So a
context pack does four things:

1. **Hard budget**: stop once accumulated tokens exceed it, with no "one last item
   slightly over is fine".
2. **Per-page cap**: one page contributes a limited share, so a single long
   article cannot consume the whole budget.
3. **Deduplication**: chunks with the same content hash are kept once
   (documentation sites copy and paste heavily).
4. **One-hop relations as pointers only**: related items give a name, evidence,
   confidence and a command to expand, rather than inlining their bodies.

Two more things are part of "answering", so they live here too:

- `answer()` decides whether to fetch from the inventory. The test is not "are
  there local results" but **"does any result's page title equal the name the user
  asked for"** — otherwise a few tangentially related local chunks keep the real
  target page permanently out of reach.
- **Diagnosis** when nothing is found (`describe_lookup` / `exact_page_hint`). An
  empty result carries no information: the site not having the page, the inventory
  having it unfetched, and the name being spelled differently all demand different
  next steps, so which one it is must be stated.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .chunking import normalize_name
from .ondemand import (
    DEFAULT_FETCH_LIMIT,
    ensure_available,
    inventory_lookup,
    missing_exact_pages,
    target_fragment,
    target_paths,
)
from .relations import page_link_status
from .runtime import active
from .search import page_chunks, query_names, search_chunks
from .text import SCRIPT_NAMES, heading_anchor, script_mismatch, script_of_language
from . import versions

# How many chunks a page is guaranteed, and the largest budget share it may take.
#
# What needs preventing is "one long article eats the whole budget", and that is a
# matter of **budget share**. A hardcoded "2 chunks per page" backfires as the
# budget grows: a budget of 6000 is precisely someone saying "I want to read this
# page through", yet it would still get 2 chunks, leaving the remaining budget to
# be filled from other pages whose content scores lower.
#
# The guaranteed 2 cannot be dropped: with a small budget, a share-based limit
# cannot fit a second chunk, which would be worse than before.
MIN_CHUNKS_PER_PAGE = 2
MAX_PAGE_BUDGET_RATIO = 0.6
PRIMARY_BUDGET_RATIO = 0.8
# Relations below this confidence stay out of the context: better to omit one than
# to let an AI present a guess as an official correspondence.
MIN_RELATION_CONFIDENCE = 0.8


def _select_primary(
    candidates: list[dict[str, Any]], budget: int
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    page_count: dict[int, int] = {}
    page_tokens: dict[int, int] = {}
    seen_hashes: set[str] = set()
    page_allowance = int(budget * MAX_PAGE_BUDGET_RATIO)
    used = 0
    for row in candidates:
        tokens = int(row["token_estimate"] or 1)
        page_id = row["page_id"]
        content_hash = row.get("content_hash")
        if (
            page_count.get(page_id, 0) >= MIN_CHUNKS_PER_PAGE
            and page_tokens.get(page_id, 0) + tokens > page_allowance
        ):
            continue
        if content_hash and content_hash in seen_hashes:
            continue
        if used + tokens > budget:
            continue
        selected.append(row)
        page_count[page_id] = page_count.get(page_id, 0) + 1
        page_tokens[page_id] = page_tokens.get(page_id, 0) + tokens
        if content_hash:
            seen_hashes.add(content_hash)
        used += tokens
    if not selected and candidates:
        # The single most relevant item exceeds the budget by itself: truncate it,
        # rather than dropping it entirely or forcing the whole thing in.
        head = dict(candidates[0])
        keep_chars = max(200, budget * 4)
        if len(head["content_md"]) > keep_chars:
            head["content_md"] = head["content_md"][:keep_chars] + "\n\n... (truncated to fit the budget)"
            head["truncated"] = True
        head["token_estimate"] = min(int(head["token_estimate"] or 1), budget)
        selected.append(head)
        used = head["token_estimate"]
    return selected, used


def _one_hop_relations(
    connection: sqlite3.Connection, chunk_ids: list[int], limit: int
) -> list[dict[str, Any]]:
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    # A primary item's own entity does not count as "related", or the output would
    # say "X is related to X".
    own_entity_ids = {
        row["entity_id"]
        for row in connection.execute(
            f"SELECT DISTINCT entity_id FROM knowledge_entities "
            f"WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
    }
    rows = connection.execute(
        f"""
        SELECT DISTINCT
            related.id            AS entity_id,
            related.canonical_name,
            related.entity_type,
            related.qualified_name,
            related.source_url,
            p.route_depth,
            r.relation_type,
            r.evidence_kind,
            r.confidence,
            r.note
        FROM knowledge_entities ke
        JOIN relations r
            ON r.from_entity_id=ke.entity_id OR r.to_entity_id=ke.entity_id
        JOIN entities related
            ON related.id=CASE
                WHEN r.from_entity_id=ke.entity_id THEN r.to_entity_id
                ELSE r.from_entity_id
            END
        JOIN pages p ON p.id=related.page_id
        WHERE ke.chunk_id IN ({placeholders})
          AND r.confidence >= ?
        ORDER BY
            {active().relation_priority_sql},
            r.confidence DESC,
            p.route_depth DESC,
            related.canonical_name
        LIMIT ?
        """,
        (*chunk_ids, MIN_RELATION_CONFIDENCE, limit * 4),
    )
    seen: set[int] = set(own_entity_ids)
    relations: list[dict[str, Any]] = []
    for row in rows:
        if row["entity_id"] in seen:
            continue
        # route_depth <= 1 is a top-level index page; pointing at one is useless.
        if (row["route_depth"] or 9) <= 1:
            continue
        seen.add(row["entity_id"])
        item = dict(row)
        best = connection.execute(
            """
            SELECT c.id FROM chunks c
            JOIN entities e ON e.page_id=c.page_id
            WHERE e.id=?
              AND c.knowledge_type IN ('summary', 'signature', 'overview')
            ORDER BY CASE c.knowledge_type
                WHEN 'signature' THEN 1 WHEN 'summary' THEN 2 ELSE 3 END,
                c.chunk_index
            LIMIT 1
            """,
            (row["entity_id"],),
        ).fetchone()
        item["expand_chunk_id"] = best["id"] if best else None
        relations.append(item)
        if len(relations) >= limit:
            break
    return relations


def _named_pages(
    connection: sqlite3.Connection, query: str, category: str | None
) -> list[sqlite3.Row]:
    """Whether the query names a page outright — an official URL, or an inventory path.

    That is more certain than any name, so it decides *which page the answer comes
    from*, not merely which page ranks first. Whether a URL is recognizable is the
    source adapter's answer (see `ondemand.target_paths`).
    """
    paths = target_paths(query)
    if not paths:
        return []
    sql = f"SELECT id, title FROM pages WHERE path IN ({','.join('?' for _ in paths)})"
    params: list[Any] = list(paths)
    if category:
        sql += " AND category=?"
        params.append(category)
    return list(connection.execute(sql, params))


def _chunk_anchor(chunk: dict[str, Any]) -> str:
    """This chunk's own anchor, flattened the same way a fragment is. Empty if none."""
    url = chunk.get("source_url") or ""
    return normalize_name(url.split("#", 1)[1]) if "#" in url else ""


def _ancestor_scope(chunks: list[dict[str, Any]], fragment: str) -> str:
    """Recover a fragment pointing at a body-less parent heading via `heading_path`.

    Section splitting keeps only sections that have a body, so a parent heading
    which merely introduces subheadings and writes nothing itself disappears. A page
    whose `## Overview` is followed straight by `### Details` is exactly this: the
    official page has an `#Overview` anchor, no chunk in the library claims it, and
    pasting that URL only gets the user told the page has no matching section.

    But it was not truly lost — the child sections carry it verbatim in their
    `heading_path`. Applying the same flattening rule used for anchors to each
    segment of that path recovers the section's extent. That avoids both inventing a
    body-less record for an empty heading and rebuilding an already-built library.
    """
    for chunk in chunks:
        segments = chunk["heading_path"].split(" > ")
        for depth, segment in enumerate(segments, start=1):
            if heading_anchor(segment) == fragment:
                return " > ".join(segments[:depth])
    return ""


def _fragment_section(
    chunks: list[dict[str, Any]], fragment: str
) -> tuple[list[dict[str, Any]], str]:
    """Select the section a fragment points at; returns (its chunks, section name).

    Both steps are required:

    - **The anchor identifies which section.** Anchors come from flattening a
      heading, and an official href flattens to exactly the same thing. Failing
      that, check whether the anchor matches a segment of some `heading_path` — when
      a parent heading has no body of its own, that is the only record left of it
      (`_ancestor_scope`).
    - **`heading_path` bounds where the section ends.** A section may hold several
      chunks, and those carrying a subheading of their own have an anchor that is
      not the section's anchor. Matching by anchor alone returns one chunk and
      leaves out what the user clicked through to read.
    """
    roots = [chunk for chunk in chunks if _chunk_anchor(chunk) == fragment]
    if roots:
        scopes = {chunk["heading_path"] for chunk in roots}
        name = roots[0]["heading_path"].split(" > ")[-1]
    elif ancestor := _ancestor_scope(chunks, fragment):
        scopes = {ancestor}
        name = ancestor.split(" > ")[-1]
    else:
        return [], ""
    selected = [
        chunk
        for chunk in chunks
        if any(
            chunk["heading_path"] == scope
            or chunk["heading_path"].startswith(f"{scope} > ")
            for scope in scopes
        )
    ]
    return selected, name


def build_context_pack(
    connection: sqlite3.Connection,
    query: str,
    *,
    token_budget: int,
    category: str | None,
    version_intent: versions.Intent | None = None,
) -> dict[str, Any]:
    named = _named_pages(connection, query, category)
    # A query carrying an official URL or inventory path has already named the page.
    # Running full-text search over the whole URL then gets it backwards: words in
    # it such as `documentation` or `language` guarantee a win for the top-level
    # index page while the named page ranks below. With a unique title, search on
    # the title instead.
    retrieval_query = named[0]["title"] if len(named) == 1 and named[0]["title"] else query

    candidates = search_chunks(
        connection, retrieval_query, limit=60, category=category
    )
    fragment_report: dict[str, Any] | None = None
    if named:
        named_ids = {row["id"] for row in named}
        candidates = [row for row in candidates if row["page_id"] in named_ids]
        # Named down to a section: read the whole page back and pick from it. The
        # user has been as specific as possible, and letting full-text search
        # compete only ranks unrelated sections first.
        fragment = target_fragment(query)
        # Read by page when retrieval did not surface it (empty title, or on-page
        # wording unlike the title). But **never** substitute another page: that
        # would make answer() believe an answer exists, so the page that should be
        # fetched never is, and the user gets something that looks like an answer to
        # a different question.
        if fragment or not candidates:
            candidates = page_chunks(connection, sorted(named_ids), limit=60)
        if fragment:
            section, section_name = _fragment_section(candidates, fragment)
            fragment_report = {
                "fragment": fragment,
                "matched": bool(section),
                "section": section_name or None,
            }
            if section:
                # A named section bounds the answer to that section. Carrying the
                # rest of the page along as context and relying on the per-page cap
                # to trim it would hang this guarantee on an unrelated constant, so
                # relaxing that cap would bring the whole page back. The limit is
                # enforced at this level instead.
                candidates = section
    # Version intent applies before the budget is trimmed: first decide what counts
    # for this version, then what fits. The other way round, excluded content has
    # already consumed slots.
    candidates, version_report = versions.apply(
        connection, candidates, version_intent
    )

    normalized = normalize_name(query)
    exact_page_ids = {
        row["page_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT e.page_id
            FROM entities e
            JOIN pages p ON p.id=e.page_id
            LEFT JOIN entity_aliases a ON a.entity_id=e.id
            WHERE (e.normalized_name=? OR a.normalized_alias=?)
              AND (? IS NULL OR p.category=?)
            """,
            (normalized, normalized, category, category),
        )
    }
    # On an exact name hit, look only at that entity's own page — do not drag in
    # sibling functions from the same directory. Skipped when already limited to a
    # named page: that is more certain than a name.
    scoped = [row for row in candidates if row["page_id"] in exact_page_ids]
    if scoped and not named:
        candidates = scoped

    primary_budget = max(1, int(token_budget * PRIMARY_BUDGET_RATIO))
    primary, used = _select_primary(candidates, primary_budget)
    relations = _one_hop_relations(
        connection, [row["id"] for row in primary], limit=8
    )

    dataset = active().dataset
    pack = {
        "query": query,
        "dataset": dataset.id,
        "product": dataset.product,
        "version": dataset.version,
        "token_budget": token_budget,
        "estimated_tokens": used,
        "primary_knowledge": primary,
        "one_hop_relations": relations,
        "retrieval_policy": {
            "min_chunks_per_page": MIN_CHUNKS_PER_PAGE,
            "max_page_budget_ratio": MAX_PAGE_BUDGET_RATIO,
            "primary_budget_ratio": PRIMARY_BUDGET_RATIO,
            "graph_depth": 1,
            "min_relation_confidence": MIN_RELATION_CONFIDENCE,
            "relations_are_pointers_only": True,
            "large_cpp_member_indexes": "ranked down",
            "exact_entity_scope": bool(scoped),
            # The answer is bounded to the page the query named. A caller can relay
            # it confidently: this is not "retrieval thought it looked closest", it
            # is what the user pointed at.
            "named_page_scope": bool(named),
        },
    }
    if version_report:
        pack["version_intent"] = version_report
    # Same principle as the version conditions: a limit applied must be stated.
    # Filtering to a section silently, or failing to identify it and quietly
    # returning a page overview, both leave the caller thinking they saw everything.
    if fragment_report:
        pack["fragment_intent"] = fragment_report
    return pack


def _has_exact_local_hit(pack: dict[str, Any], query: str) -> bool:
    """Whether any local result has a page title equal to the name asked for.

    Only that means it was really found; several pages merely mentioning the word
    does not. This test decides whether to fetch from the inventory — otherwise a
    pile of weakly related local chunks keeps the real target page out.
    """
    wanted = set(query_names(query))
    return any(
        normalize_name(item["page_title"] or "") in wanted
        for item in pack["primary_knowledge"]
    )


def answer(
    connection: sqlite3.Connection,
    query: str,
    *,
    token_budget: int,
    category: str | None,
    allow_fetch: bool = True,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    quiet: bool = False,
    version_intent: versions.Intent | None = None,
) -> dict[str, Any]:
    """The single implementation of `ask`, shared by the CLI and MCP."""

    def build() -> dict[str, Any]:
        return build_context_pack(
            connection,
            query,
            token_budget=token_budget,
            category=category,
            version_intent=version_intent,
        )

    pack = build()
    if allow_fetch:
        # A named page that already has a body is an exact hit — no need to gather
        # pages with similar names. Named but still empty **must** take the fetch
        # path.
        named_and_local = (
            pack["retrieval_policy"]["named_page_scope"]
            and bool(pack["primary_knowledge"])
        )
        exact_local = named_and_local or _has_exact_local_hit(pack, query)
        needs_fetch = not exact_local or missing_exact_pages(
            connection, query, category
        )
        if needs_fetch:
            # With an exact hit already, fetch only the same-named page rather than
            # pulling in everything with a similar name.
            fetched = ensure_available(
                connection,
                query,
                limit=fetch_limit,
                category=category,
                quiet=quiet,
                exact_only=exact_local,
            )
            if fetched["succeeded"]:
                pack = build()
            pack["on_demand_fetch"] = fetched
    if not pack["primary_knowledge"]:
        pack["lookup"] = inventory_lookup(connection, query, category=category)
    return pack


KNOWLEDGE_ID_RE = re.compile(r"[Kk]?\d+")


def _subject_entities(
    connection: sqlite3.Connection, subject: str
) -> list[sqlite3.Row]:
    if KNOWLEDGE_ID_RE.fullmatch(subject):
        chunk_id = int(subject[1:] if subject[:1].casefold() == "k" else subject)
        return list(
            connection.execute(
                """
                SELECT e.* FROM knowledge_entities ke
                JOIN entities e ON e.id=ke.entity_id
                WHERE ke.chunk_id=?
                ORDER BY ke.confidence DESC
                """,
                (chunk_id,),
            )
        )
    # An OR across two tables makes SQLite abandon its indexes; split into a UNION
    # so each branch uses its own.
    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for normalized in query_names(subject):
        for row in connection.execute(
            """
            SELECT * FROM entities WHERE id IN (
                SELECT id FROM entities WHERE normalized_name=?
                UNION
                SELECT entity_id FROM entity_aliases WHERE normalized_alias=?
            )
            ORDER BY entity_type, canonical_name
            LIMIT 20
            """,
            (normalized, normalized),
        ):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        if rows:
            break
    return rows


def related_payload(
    connection: sqlite3.Connection, subject: str
) -> dict[str, Any]:
    """One-hop cross relations, with an explicit status.

    A bare array cannot express the difference between no entity matched, the entity
    exists but has no relations, and the target page is in the inventory but
    unfetched. All three would be one `[]`, leaving the caller unable to tell
    whether to rewrite the query, fetch, or rebuild relations. So the status is
    stated.
    """
    entities = []
    for entity in _subject_entities(connection, subject):
        relations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    r.relation_type,
                    r.evidence_kind,
                    r.confidence,
                    r.source_url AS evidence_url,
                    r.note,
                    CASE WHEN r.from_entity_id=? THEN 'outgoing' ELSE 'incoming' END
                        AS direction,
                    related.id AS related_entity_id,
                    related.canonical_name AS related_name,
                    related.entity_type AS related_type,
                    related.qualified_name AS related_qualified_name,
                    related.source_url AS related_source_url
                FROM relations r
                JOIN entities related ON related.id=CASE
                    WHEN r.from_entity_id=? THEN r.to_entity_id
                    ELSE r.from_entity_id
                END
                WHERE r.from_entity_id=? OR r.to_entity_id=?
                ORDER BY r.confidence DESC, r.relation_type, related.canonical_name
                """,
                (entity["id"], entity["id"], entity["id"], entity["id"]),
            )
        ]
        entities.append(
            {
                "entity": {
                    "id": entity["id"],
                    "page_id": entity["page_id"],
                    "name": entity["canonical_name"],
                    "type": entity["entity_type"],
                    "qualified_name": entity["qualified_name"],
                    "module": entity["module"],
                    "owner_type": entity["owner_type"],
                    "source_url": entity["source_url"],
                    "version": entity["version"],
                },
                "relations": relations,
            }
        )
    if not entities:
        # A K id identifies a chunk, not a page name — failing to find one means
        # "no such id", which has nothing to do with whether the inventory holds a
        # page, so inventory_lookup's diagnosis does not apply.
        status = (
            "knowledge_id_not_found"
            if KNOWLEDGE_ID_RE.fullmatch(subject)
            else "entity_not_found"
        )
    elif any(item["relations"] for item in entities):
        status = "ok"
    else:
        status = "entity_found_but_no_relations"
    result: dict[str, Any] = {
        "subject": subject,
        "status": status,
        "entities": entities,
        "next_steps": [],
    }
    if status == "entity_not_found":
        result["lookup"] = inventory_lookup(connection, subject)
        # Keep "the site does not have this page" apart from "our source never
        # enumerated it": the first ends there, the second is our own coverage
        # problem, and no amount of rewording the query will ever fix it.
        if result["lookup"].get("linked_targets"):
            result["status"] = status = "target_outside_inventory"
        result["next_steps"] = describe_lookup(result["lookup"])
    elif status == "knowledge_id_not_found":
        result["next_steps"] = [
            f"{subject} is not a knowledge id present locally. K ids can only be "
            "read from search / ask results, never guessed or reused from older "
            "output — a rebuild changes them. Run "
            'python -m docatlas search "<keywords>" to get a currently valid one.',
        ]
    elif status == "entity_found_but_no_relations":
        # A blanket "no relations" is not actionable. Whether the pages it points at
        # are unfetched, or absent from the inventory entirely, implies completely
        # different next steps, so look the target status up and say which.
        targets = page_link_status(
            connection, [item["entity"]["page_id"] for item in entities]
        )
        result["link_targets"] = targets
        steps: list[str] = []
        if targets["pending"]:
            steps.append(
                f"This page links to {len(targets['pending'])} page(s) the inventory "
                "has but whose bodies are not fetched. Retrieve them and the "
                "relations appear:"
            )
            steps.extend(f'  python -m docatlas get "{t["path"]}"' for t in targets["pending"])
        if targets["missing"]:
            steps.append(
                f"A further {len(targets['missing'])} link target(s) are absent from "
                "the inventory — the site has them, the source adapter never "
                "enumerated them, and no amount of fetching will produce them:"
            )
            steps.extend(f"  {t['url']}" for t in targets["missing"])
        if not steps:
            steps = [
                "This entity is in the library and the pages it points at are all "
                "fetched; it simply does not point anywhere else.",
                "Try a more specific name, or use search to see which pages mention it.",
            ]
        result["next_steps"] = steps
    return result


def describe_lookup(lookup: dict[str, Any]) -> list[str]:
    """Turn "what the inventory knows" into next steps a human or AI can act on.

    An empty result carries no information: the site not having the page, the
    inventory having it unfetched, and the name being spelled differently all demand
    different next steps, so which one it is must be stated.
    """
    workspace = active()
    pending = lookup["pending_pages"]
    crawled = lookup["crawled_pages"]
    if pending:
        lines = [f"No local body, but {len(pending)} inventory page(s) match:"]
        for page in pending:
            label = workspace.category_labels.get(page["category"], page["category"])
            lines.append(f"  [{label}] {page['path']}")
        lines.append(f'Retrieve then query: python -m docatlas get "{lookup["query"]}"')
        return lines
    if crawled:
        # When a site withdraws a page and redirects elsewhere, what comes back is an
        # empty shell with no body. "Rephrase and retry" can never work then: the
        # content is no longer at that URL. Note the redirect target is not
        # necessarily the page's new home — a withdrawn page may redirect to the
        # documentation front page, and following it would only yield that. So this
        # reports the redirect faithfully and does not follow it on the user's behalf.
        moved = [page for page in crawled if page.get("redirect_url")]
        live = [page for page in crawled if not page.get("redirect_url")]
        if moved:
            lines = [
                f"{len(moved)} page(s) are redirected by the site and came back with "
                "no body — the content is no longer at the original URL, and "
                "rewording the query will not help:"
            ]
            lines.extend(f"  {page['path']} → {page['redirect_url']}" for page in moved)
            if live:
                # A live page with the same name is probably where the site moved it
                # — but that is inference, so it is offered for confirmation rather
                # than substituted in as the answer.
                lines.append("The library holds another live page of the same name, likely its new home:")
                lines.extend(f"  {page['path']}" for page in live)
                lines.append(f'  python -m docatlas ask "{live[0]["path"]}"')
            else:
                lines.append("Work out the name to query from the redirected official page, then query again.")
            return lines
        return [
            f"{len(crawled)} same-named page(s) are already fetched, but no chunk matched these keywords.",
            f'Reword and retry, or read the page directly: python -m docatlas ask "{lookup["query"]}"',
        ]
    if weak := lookup.get("weak_candidates"):
        # "Too little to go on, so nothing was fetched" and "there really is no such
        # page" are different. Show the candidates and let the reader decide, rather
        # than closing the road with "the site does not have it".
        lines = [
            f"Not confident which page to retrieve, so nothing was fetched automatically. These inventory pages are close:",
        ]
        for page in weak:
            label = workspace.category_labels.get(page["category"], page["category"])
            lines.append(f"  [{label}] {page['path']}")
        lines.append(
            "If one looks right, fetch it: "
            f'python -m docatlas get "{lookup["query"]}"; '
            "or query again using the page's official name, which fetches automatically."
        )
        return lines
    if linked := lookup.get("linked_targets"):
        # A fetched page links there and the inventory lacks it — that is not "the
        # site does not have it", it is us not enumerating it. Calling it absent
        # sends the reader rewriting the query forever, for nothing.
        lines = [
            f"Pages already held locally contain body links to \u2018{lookup['query']}\u2019, "
            "but that page is absent from the inventory — the site has it, our source "
            "never enumerated it.",
        ]
        lines.extend(f"  {item['url']}" for item in linked)
        lines.append(
            "Open the official URL above directly. Getting it into the library means "
            "widening the source adapter's enumeration scope (see WORKFLOWS.md); no "
            "amount of refetching will produce it."
        )
        return lines
    if foreign := script_mismatch(lookup["query"], workspace.language):
        # Asking in one script of a single-language library can only find nothing —
        # that is not "the site lacks this page" but "this library holds no text in
        # that script". The two point at completely different next steps.
        return [
            f"This library's text is in {workspace.language}"
            f" ({SCRIPT_NAMES.get(script_of_language(workspace.language), '')}), "
            f"while this query is written in {SCRIPT_NAMES.get(foreign, foreign)}, "
            "so nothing can possibly match — this is not the site lacking the page.",
            f"Rephrase the keywords in the official spelling used in {workspace.language} "
            "and query again: page titles, function names, menu items as written. "
            "Proper nouns and code symbols are usually left untranslated.",
        ]
    # Past here there truly is no clue left, but the boundary of "nothing" stops at
    # this dataset. DocAtlas sees only its own inventory, whose scope is the
    # directories the dataset declared, so saying "the official docs do not have this
    # page" draws a conclusion about something it cannot see. The cost is real: a
    # dataset that never declared a directory cannot find pages living under it, and
    # answering "the site does not have it" sends the user rewording the query over
    # and over while the page sits perfectly well on the official site. Rewording a
    # query can never fix a coverage-scope problem.
    scope = ", ".join(
        workspace.category_labels.get(key, key)
        for key in workspace.dataset.query_categories
    )
    return [
        "No results, and no inventory page in this dataset matches either.",
        f"This library's coverage is: {scope or '(no categories declared)'}. "
        "DocAtlas sees only its own inventory and cannot conclude from that the site "
        "lacks the page.",
        f"Try again in the official spelling used in {workspace.language}; if you know "
        "the exact address, pass the official URL as the query, which locates the page "
        "directly. If the site has it and this does not, that is a coverage problem, "
        "and the source adapter or the dataset's declared directories need widening "
        "(see WORKFLOWS.md).",
    ]


def exact_page_hint(
    connection: sqlite3.Connection, query: str, category: str | None = None
) -> list[str]:
    """There are results, yet an inventory page is named exactly this — say so.

    Otherwise the user sees a list of tangential pages with no idea the real match is
    sitting in the inventory, one command away. "Not found" and "not retrieved yet"
    are different things.
    """
    missing = missing_exact_pages(connection, query, category)
    if not missing:
        return []
    return [
        f"Hint: {missing} more page(s) in the inventory are named exactly '{query}' "
        "but their bodies are not fetched, so they are absent from the results above.",
        f'Retrieve them: python -m docatlas get "{query}" (or just use ask, which fetches)',
    ]


def _render_empty(pack: dict[str, Any]) -> list[str]:
    """On a miss, spell out what the inventory knows instead of just "not found"."""
    workspace = active()
    lookup = pack.get("lookup") or {}
    pending = lookup.get("pending_pages") or []
    if not pending:
        # Diagnosis is unified in describe_lookup: a foreign script, too little to go
        # on, never enumerated by the source, and genuinely absent each have their own
        # next step, and a second copy here would drift from it.
        return ["No match in the local library.", "", *describe_lookup(lookup or {
            "query": pack["query"], "pending_pages": [], "crawled_pages": [],
        })]
    lines = [
        f"No local body for this page yet, but {len(pending)} inventory page(s) match:",
        "",
    ]
    for page in pending:
        label = workspace.category_labels.get(page["category"], page["category"])
        lines.append(f"- [{label}] {page['path']}")
    lines.extend(
        [
            "",
            "Nothing was fetched this time (perhaps --no-fetch, or the fetch failed). Retrieve and ask again:",
            "",
            f'    python -m docatlas get "{pack["query"]}"',
        ]
    )
    return lines


def describe_fragment(intent: dict[str, Any]) -> str:
    """One line on whether a `#fragment` took effect. Especially when it did not.

    Quietly falling back to a page overview shows the user a plausible-looking answer
    with no hint that the section they named was never used.
    """
    if not intent:
        return ""
    if intent.get("matched"):
        return f"Limited by the `#{intent['fragment']}` in the URL to section \u2018{intent['section']}\u2019."
    return (
        f"Note: the `#{intent['fragment']}` in the URL matches no section on this "
        "page, so the whole page follows. The section may have been renamed; query "
        "again using a heading that actually appears on the page."
    )


def render_context_markdown(pack: dict[str, Any]) -> str:
    """Render a context pack as Markdown for an AI to read.

    For the same content, Markdown costs 30-40% fewer tokens than JSON — quotes,
    escaping and field names all cost money, and an AI reads it less easily too.
    """
    workspace = active()
    lines: list[str] = [
        f"# Documentation search: {pack['query']}",
        "",
        f"Source: {workspace.name} ({pack['product']} {pack['version']}). "
        f"Budget {pack['token_budget']:,} tokens, about {pack['estimated_tokens']:,} used, "
        f"{len(pack['primary_knowledge'])} chunk(s).",
        "",
    ]
    # A version filter must be stated. Silently dropping a few is harder to notice
    # than an outright error.
    if lines_about_version := versions.describe(pack.get("version_intent") or {}):
        lines.extend([*lines_about_version, ""])
    if line_about_fragment := describe_fragment(pack.get("fragment_intent") or {}):
        lines.extend([line_about_fragment, ""])
    if not pack["primary_knowledge"]:
        lines.extend(_render_empty(pack))
        return "\n".join(lines)

    for index, item in enumerate(pack["primary_knowledge"], 1):
        label = workspace.category_labels.get(item["category"], item["category"])
        lines.append(
            f"## {index}. {item['heading_path']}  "
            f"[{label} · {item['knowledge_type']} · K{item['id']}]"
        )
        lines.append("")
        lines.append(item["content_md"].strip())
        lines.append("")

    if pack["one_hop_relations"]:
        lines.append("---")
        lines.append("")
        lines.append("## Cross relations (pointers only, bodies not expanded)")
        lines.append("")
        for relation in pack["one_hop_relations"]:
            kind = workspace.relation_labels.get(
                relation["relation_type"], relation["relation_type"]
            )
            evidence = workspace.evidence_labels.get(
                relation["evidence_kind"], relation["evidence_kind"]
            )
            expand = (
                f"  expand: `show K{relation['expand_chunk_id']}`"
                if relation["expand_chunk_id"]
                else ""
            )
            lines.append(
                f"- **{kind}**: {relation['canonical_name']} "
                f"({relation['entity_type']})  evidence: {evidence}  "
                f"confidence {relation['confidence']:.2f}{expand}"
            )
            lines.append(f"  {relation['source_url']}")
        lines.append("")
    return "\n".join(lines)
