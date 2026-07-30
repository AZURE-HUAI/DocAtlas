"""On-demand fetching: pull a page only when something actually needs it.

The site inventory is frozen before any body is fetched, so even for a page
whose body is missing we **still know it exists, which category it is in, and
what its URL is**. That is what makes on-demand fetching possible: no aimless
crawling, just a direct hit on the page in question.

Locating runs in three tiers, most certain to loosest, stopping as soon as one
of them has enough:

    A exact     the slug matches the name outright
                /…/<owner>/<Symbol>            ← "Symbol"
                /…/<group>/<topic>.html        ← "Topic" (the extension is
                                                 not part of the name)
                /…/<header>/<function>         ← "ns::function" (qualifiers
                                                 stripped)
    B contains  the slug contains the term
                /…/<feature>-<detail>-<more>   ← "Feature"
    C covers    every content word of the query appears in the path
                /<area>/<group>/<page>-<kind>  ← "Page Kind"

Tier C demands that **all** content words hit, so a conceptual question like
"how do I make an object glow" never triggers a fetch by accident; and when it
does hit, that page really is worth having.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from typing import Any
import urllib.parse

from .chunking import normalize_name
from .constants import URL_RE
from .db import page_slug
from .documents import fetch_document
from .relations import link_new_pages
from .runtime import active, bind
from .search import query_names, tokenize, STOPWORDS
from .text import qualifier_segments, qualifier_tail
from .store import store_document_result
from .util import log

# Pages one on-demand fetch may pull at most. The point is to fill in the page
# that is missing, not to crawl a neighbourhood while we are there.
DEFAULT_FETCH_LIMIT = 5
MAX_FETCH_LIMIT = 40

# The path-coverage tier needs at least this many content words, otherwise a
# single word sweeps back a whole region of the site.
MIN_COVERAGE_TOKENS = 2
# Very short words (as, id, ui) match tens of thousands of pages when used for
# containment, so they are only allowed in the exact tier.
MIN_CONTAINS_CHARS = 5


def coverage_tokens(query: str) -> list[str]:
    """The content words the path-coverage tier requires all of."""
    tokens = []
    for token in tokenize(query):
        if token.casefold() in STOPWORDS:
            continue
        stripped = normalize_name(token)
        if len(stripped) >= 3:
            tokens.append(stripped)
    return tokens[:6]  # beyond this it is a sentence, and never all matches


# Separators found in addresses, stripped before comparing: a typed
# `duration_cast` normalises to `durationcast` while the path spells it
# `duration_cast`. Leave the underscore in and the two strings can never meet,
# so a page plainly present in the inventory is reported as missing.
_PATH_SEPARATORS = ("_", "-", ".", "~", "+", "%20", " ")


def _flattened_path(column: str = "path") -> str:
    """Flatten a path the way `normalize_name` flattens a word, so the two
    can be compared directly."""
    expression = f"lower({column})"
    for separator in _PATH_SEPARATORS:
        expression = f"replace({expression}, '{separator}', '')"
    return expression


def _flatten(path: str) -> str:
    """Python twin of `_flattened_path`; the two must stay in step."""
    flat = path.casefold()
    for separator in _PATH_SEPARATORS:
        flat = flat.replace(separator, "")
    return flat


def target_paths(query: str) -> list[str]:
    """Pages of this dataset named outright in the query — URL or path.

    Pasting an exact URL is the **strongest** clue a user can give, more
    certain than any name, because it already points at the page. Yet it used
    to be the most useless input: the whole address was normalised as ordinary
    text into one long run of letters that matched no slug at all, so giving a
    precise address fetched nothing.

    The same holds for paths, and matters more there: `related` and
    `describe_lookup` print paths in their own `next_steps` (`get
    "/<area>/<group>/index"`). Name matching sees only the last segment of
    such a path, so **the next step the system itself suggested does not
    run**.

    Whether an address belongs to this dataset, and which page it is, is for
    the source adapter to answer; paths are compared as-is, since they are
    already written the way our own library writes them. The core knows no
    sites.
    """
    dataset = active().dataset
    resolve = active().extension("normalize_link_target")
    found: list[str] = []

    def remember(path: str | None) -> None:
        if path and path not in found:
            found.append(path)

    for candidate in URL_RE.findall(query):
        if resolve:
            remember(resolve(dataset, candidate))
    for token in query.split():
        if not token.startswith("/") or "://" in token:
            continue
        # Ask the adapter first: a path may carry variant spellings such as a
        # locale segment (`/documentation/<locale>/<product>/…` differs from
        # the canonical form in the inventory by one segment). When the
        # adapter does not recognise it (a missing extension, say), compare
        # as-is — it is already our library's own spelling.
        if resolve:
            remember(resolve(dataset, token))
        remember(token.rstrip("/") or None)
    return found


def target_fragment(query: str) -> str:
    """The **section** an address points at: `…/some-page#screen-insets` →
    `screeninsets`.

    There is a level below the page. When a user opens a section on the
    official page and copies the address, the `#…` is the part they mean;
    drop it and the answer falls back to a page overview while the body of
    the section asked for is already in the library.

    The official href and the anchor we store need not agree on spelling
    (`screen-insets` versus `screeninsets`): our anchors come from flattened
    headings (see `text.heading_anchor`), so flattening both sides by the same
    rule makes them meet. Pure string handling, no site knowledge required.
    """
    for candidate in URL_RE.findall(query):
        fragment = urllib.parse.urlsplit(candidate).fragment
        if key := normalize_name(urllib.parse.unquote(fragment)):
            return key
    return ""


def query_qualifiers(query: str) -> list[str]:
    """The "where does this live" segments of a query: `a::b::name` → `['b']`.

    Only segments long enough to mean something: a two or three letter
    namespace collides with any path at all, so ordering by it is random.
    """
    found: list[str] = []
    for token in tokenize(query):
        for segment in qualifier_segments(token):
            normalized = normalize_name(segment)
            if len(normalized) >= MIN_CONTAINS_CHARS and normalized not in found:
                found.append(normalized)
    return found


def identifier_tokens(query: str) -> list[str]:
    """Words in the query that are visibly symbols: `::`, underscores, camel
    case.

    Ordinary English words do not count. A plain noun taken to the slugs
    sweeps back a whole region, whereas a compound identifier can realistically
    be only that one page — so only the latter is allowed to locate on its own.
    This is also why conceptual questions never trigger a fetch: "how do I
    make an object glow" contains no symbol-shaped word at all.
    """
    identifier_re = active().identifier_re
    found: list[str] = []
    for token in tokenize(query):
        if not identifier_re.search(token):
            continue
        for candidate in (token, qualifier_tail(token)):
            normalized = normalize_name(candidate)
            if len(normalized) >= MIN_CONTAINS_CHARS and normalized not in found:
                found.append(normalized)
    return found


def _candidate_queries(query: str) -> list[tuple[str, str, tuple[Any, ...]]]:
    """Returns [(tier, WHERE fragment, params)], most certain to loosest."""
    stages: list[tuple[str, str, tuple[Any, ...]]] = []
    # Addresses come before every name: an address is not "much like that
    # page", it is that page.
    if paths := target_paths(query):
        stages.append(
            ("exact_url", f"path IN ({','.join('?' for _ in paths)})", tuple(paths))
        )
    names = query_names(query)
    if not names:
        return stages
    placeholders = ",".join("?" for _ in names)
    stages.append(("exact_slug", f"normalized_slug IN ({placeholders})", tuple(names)))
    # The whole query matches nothing, but one symbol inside it is exactly a
    # page name — the identifier in "some_identifier some noun". Only
    # symbol-shaped words qualify, so natural-language questions stay out.
    if tokens := [name for name in identifier_tokens(query) if name not in names]:
        stages.append(
            (
                "token_exact_slug",
                f"normalized_slug IN ({','.join('?' for _ in tokens)})",
                tuple(tokens),
            )
        )
    for name in names:
        if len(name) >= MIN_CONTAINS_CHARS:
            stages.append(("slug_contains", "normalized_slug LIKE ?", (f"%{name}%",)))
    tokens = coverage_tokens(query)
    if len(tokens) >= MIN_COVERAGE_TOKENS:
        flattened = _flattened_path()
        stages.append(
            (
                "path_covers_query",
                " AND ".join(f"{flattened} LIKE ?" for _ in tokens),
                tuple(f"%{token}%" for token in tokens),
            )
        )
    return stages


def _collect(
    connection: sqlite3.Connection,
    query: str,
    stages: list[tuple[str, str, tuple[Any, ...]]],
    *,
    status_clause: str,
    limit: int,
    category: str | None,
) -> list[sqlite3.Row]:
    category_clause = " AND category=?" if category else ""
    category_params: tuple[Any, ...] = (category,) if category else ()
    order = f"ORDER BY {active().category_priority_sql}, route_depth, id"
    qualifiers = query_qualifiers(query)
    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for stage, where, params in stages:
        found = list(
            connection.execute(
                f"SELECT id, url, path, category, status, redirect_url, ? AS match_stage "
                f"FROM pages WHERE ({where}) AND {status_clause}{category_clause} "
                f"{order} LIMIT ?",
                (stage, *params, *category_params, limit * 3),
            )
        )
        # When several pages in one tier share a name, the qualifier the user
        # wrote out is the segment that tells them apart: the `b` of `a::b::name`
        # is spelled plainly in the address of the right page. A stable sort, so
        # with no qualifier hit the SQL order above survives untouched.
        if qualifiers:
            found.sort(
                key=lambda row: -sum(
                    1 for q in qualifiers if q in _flatten(row["path"])
                )
            )
        for row in found:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


_PENDING = "status IN ('pending', 'failed') AND attempts < 8"


def find_uncrawled_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category: str | None = None,
    exact_only: bool = False,
) -> list[sqlite3.Row]:
    """Inventory pages that are very likely what was asked for, body not
    fetched yet."""
    stages = _candidate_queries(query)
    if exact_only:
        stages = [stage for stage in stages if stage[0] == "exact_slug"]
    return _collect(
        connection, query, stages, status_clause=_PENDING, limit=limit, category=category
    )


def missing_exact_pages(
    connection: sqlite3.Connection, query: str, category: str | None = None
) -> int:
    """A page named exactly this sits in the inventory unfetched — always
    worth fetching.

    Otherwise this happens: asked for an exact symbol name, the only local
    match is a tutorial's code sample that mentions it in passing, so the
    answer is built from that snippet while the real API page is right there
    in the inventory.
    """
    names = query_names(query)
    if not names:
        return 0
    placeholders = ",".join("?" for _ in names)
    sql = (
        f"SELECT COUNT(*) FROM pages WHERE normalized_slug IN ({placeholders})"
        f" AND {_PENDING}"
    )
    params: list[Any] = list(names)
    if category:
        sql += " AND category=?"
        params.append(category)
    return connection.execute(sql, params).fetchone()[0]


def weak_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Inventory pages that are related but the clues are too thin to fetch.

    "The clues are not strong enough to fetch safely" and "the inventory
    really has no such page" are different things, and both used to surface as
    the same sentence. The first should put candidates on the table and let a
    human decide; only the second is a genuine absence.

    The condition here is far looser than the fetching tier (any one content
    word appearing in the path counts), so this **reports only, never
    fetches** — fetching on this condition would drag back a whole region of
    unrelated pages for one broad question.
    """
    raw = tokenize(query)
    tokens = coverage_tokens(query)
    # Three content words minimum before "one word short" means anything. One
    # short out of two leaves a single word, which is no better than picking a
    # word at random.
    if len(tokens) < MIN_COVERAGE_TOKENS + 1:
        return []
    # More than half function words: this is a sentence, not a page name. In
    # "how do I make an object glow" the how/do/I/an are all function words,
    # and the remaining make + object hit a pile of unrelated pages — noise,
    # not candidates. Showing them only misleads; better to say nothing found.
    if len(tokens) * 2 < len(raw):
        return []
    # The fetching tier requires **every** content word in the path. Here that
    # is relaxed by a full word: a page one word short is often the one wanted
    # ("camera field of view settings" missing settings), but one word short
    # is not enough to decide on the user's behalf, so report without fetching.
    flattened = _flattened_path()
    matched = " + ".join(f"(CASE WHEN {flattened} LIKE ? THEN 1 ELSE 0 END)" for _ in tokens)
    sql = f"SELECT path, category FROM pages WHERE {_PENDING} AND ({matched}) >= ?"
    params: list[Any] = [f"%{token}%" for token in tokens]
    params.append(len(tokens) - 1)
    if category:
        sql += " AND category=?"
        params.append(category)
    # Unordered on purpose: this is only a hint that a human might want a look,
    # and ORDER BY would force a full table scan before sorting.
    sql += " LIMIT ?"
    params.append(limit)
    return [
        {"path": row["path"], "category": row["category"]}
        for row in connection.execute(sql, params)
    ]


def linked_but_unlisted(
    connection: sqlite3.Connection, query: str, *, limit: int = 3
) -> list[dict[str, str]]:
    """Pages other pages link to that the site inventory never listed.

    This is the line between "the official docs really have no such page" and
    "our source never enumerated it". Getting that wrong costs real effort:
    the user keeps rewording the query when what needs changing is the source
    adapter's enumeration — the page sits on the official site perfectly well,
    we simply never listed it.

    Only reached when nothing at all was found, so this full scan is off the
    common path.
    """
    names = set(query_names(query))
    if not names:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in connection.execute(
        "SELECT DISTINCT target_path, target_url FROM page_links"
        " WHERE target_path IS NOT NULL AND target_page_id IS NULL"
    ):
        path = row["target_path"]
        if path in seen or page_slug(path) not in names:
            continue
        seen.add(path)
        found.append({"path": path, "url": row["target_url"]})
        if len(found) >= limit:
            break
    return found


def inventory_lookup(
    connection: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """What the inventory knows about this name.

    Without it, "not found" can only be an empty result, and the caller cannot
    tell whether the official docs have no such page, the page is sitting in
    the inventory with its body not yet fetched, or our source never
    enumerated it at all. The next step differs in all three cases.
    """
    stages = _candidate_queries(query)
    pending = _collect(
        connection, query, stages, status_clause=_PENDING, limit=limit, category=category
    )
    crawled = _collect(
        connection,
        query,
        stages,
        status_clause="status IN ('success', 'redirect')",
        limit=limit,
        category=category,
    )
    return {
        "query": query,
        "pending_pages": [
            {
                "path": row["path"],
                "url": row["url"],
                "category": row["category"],
                "matched_by": row["match_stage"],
            }
            for row in pending
        ],
        "crawled_pages": [
            {
                "path": row["path"],
                "category": row["category"],
                # Fetched is not the same as having a body: a page withdrawn
                # upstream leaves a shell holding nothing but a redirect. These
                # two let the caller tell "missed the query words" apart from
                # "this page no longer has content".
                "status": row["status"],
                "redirect_url": row["redirect_url"],
            }
            for row in crawled
        ],
        # The last two only count when nothing was found at all: they answer
        # which kind of absence this is.
        "linked_targets": (
            linked_but_unlisted(connection, query) if not pending and not crawled else []
        ),
        "weak_candidates": (
            weak_candidates(connection, query, category=category)
            if not pending and not crawled
            else []
        ),
    }


def fetch_now(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    workers: int = 4,
    quiet: bool = False,
) -> dict[str, Any]:
    """Fetch these pages now and store them. Returns success/failure counts."""
    if not rows:
        return {"requested": 0, "succeeded": 0, "failed": 0, "pages": []}

    succeeded = 0
    failed = 0
    fetched: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(workers, len(rows))
    ) as executor:
        future_to_row = {
            executor.submit(bind(fetch_document), row, 0.0): row for row in rows
        }
        for future in concurrent.futures.as_completed(future_to_row):
            row = future_to_row[future]
            result = future.result()
            store_document_result(connection, result, row["category"])
            if result["ok"]:
                succeeded += 1
                fetched.append(
                    {
                        "page_id": row["id"],
                        "path": row["path"],
                        "category": row["category"],
                        "title": result.get("title"),
                    }
                )
            else:
                failed += 1
                if not quiet:
                    log(f"On-demand fetch failed for {row['path']}: {result['error']}")
    connection.commit()
    return {
        "requested": len(rows),
        "succeeded": succeeded,
        "failed": failed,
        "pages": fetched,
    }


def ensure_available(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = DEFAULT_FETCH_LIMIT,
    category: str | None = None,
    quiet: bool = False,
    exact_only: bool = False,
) -> dict[str, Any]:
    """Fetch on the spot when nothing is held locally — the path `ask` takes
    when it finds no hit.

    `exact_only=True` is for "some local results exist, but the inventory also
    holds a page named exactly this": fill in that one page without dragging
    in a crowd of similarly named ones.
    """
    limit = max(1, min(limit, MAX_FETCH_LIMIT))
    candidates = find_uncrawled_candidates(
        connection, query, limit=limit, category=category, exact_only=exact_only
    )
    if not candidates:
        return {"requested": 0, "succeeded": 0, "failed": 0, "pages": []}
    if not quiet:
        category_labels = active().category_labels
        labels = ", ".join(
            sorted(
                {category_labels.get(r["category"], r["category"]) for r in candidates}
            )
        )
        log(f"Not held locally; fetching {len(candidates)} page(s) on demand ({labels})…")
    outcome = fetch_now(connection, candidates, quiet=quiet)
    outcome["relations"] = link_new_pages(
        connection, [page["page_id"] for page in outcome["pages"]]
    )
    return outcome
