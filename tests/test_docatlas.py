"""Offline regression tests.

No network, no real database — every case builds its own temporary library.
To run:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import contextvars
import dataclasses
import io
import json
import os

from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config fixes its paths at import time, so the data root has to point at a
# temporary directory before that happens, or a real library gets written to.
_TEMP_HOME = tempfile.mkdtemp(prefix="docatlas_test_")
os.environ["DOCATLAS_HOME"] = _TEMP_HOME
# Which library to run on has to be said **explicitly**: the program has no
# built-in default, since what you install is your choice. The suite runs on
# the template dataset that ships with the repository, so it needs no real
# site and no particular product to be installed.
os.environ["DOCATLAS_DATASET"] = "EXAMPLE"

from docatlas import (  # noqa: E402
    chunking, clients, config, context, coverage, crawl, dataset, db, discover,
    doctor, htmlmd, members, net, ondemand, constants, relations, runtime,
    search, store, text, validate, versions,
)
from docatlas import mcpserver  # noqa: E402
from docatlas.db import connect_db, initialize_db  # noqa: E402
from docatlas.documents import transform_document  # noqa: E402


@contextlib.contextmanager
def using(**overrides):
    """Swap parts of the active workspace: adapter, knowledge pack, dataset.

    The whole workspace is replaced at once. Patching module-level globals one
    by one is how a test ends up silently running against the real adapter:
    miss one and nothing tells you.
    """
    base = runtime.active()
    if "dataset" in overrides:
        overrides["dataset"] = dataclasses.replace(
            base.dataset, **overrides.pop("dataset")
        )
    with runtime.use(dataclasses.replace(base, **overrides)) as workspace:
        yield workspace


def temp_db(case: unittest.TestCase) -> sqlite3.Connection:
    """A temporary library with its tables built, closed and deleted on exit."""
    return temp_library(case)[0]


def temp_library(case: unittest.TestCase) -> tuple[sqlite3.Connection, Path]:
    """A temporary dataset directory; returns (connection, data directory).

    The file has to be named knowledge.sqlite3: that is the name a Workspace
    looks for, and any other one leaves "this dataset was never built" true
    forever.
    """
    directory = tempfile.TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    data_dir = Path(directory.name)
    connection = connect_db(data_dir / "knowledge.sqlite3")
    case.addCleanup(connection.close)
    initialize_db(connection)
    return connection, data_dir


def seed_entity(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    name: str,
    path: str | None = None,
    owner_type: str | None = None,
    aliases: list[tuple[str, str]] | None = None,
) -> int:
    """Put an entity in the library, with the page it must hang off; returns
    the entity id.

    An entity belongs to a page by foreign key, so the tests obey that too:
    a library built around the constraint is not the same thing as a real one,
    and conclusions drawn from it would not carry over.
    """
    path = path or f"/{text.normalize_name(name)}"
    url = f"https://example.invalid{path}"
    page_id = connection.execute(
        "INSERT INTO pages(url, path, category, status, title, route_depth)"
        " VALUES(?, ?, 'guides', 'success', ?, 2)",
        (url, path, name),
    ).lastrowid
    now = "2026-07-26T00:00:00Z"
    entity_id = connection.execute(
        "INSERT INTO entities(page_id, entity_type, canonical_name, normalized_name,"
        " owner_type, source_url, version, created_at, updated_at)"
        " VALUES(?, ?, ?, ?, ?, ?, '1', ?, ?)",
        (page_id, entity_type, name, text.normalize_name(name), owner_type, url,
         now, now),
    ).lastrowid
    for alias, alias_type in aliases or []:
        connection.execute(
            "INSERT OR IGNORE INTO entity_aliases(entity_id, alias, normalized_alias,"
            " alias_type, source) VALUES(?, ?, ?, ?, 'test')",
            (entity_id, alias, text.normalize_name(alias), alias_type),
        )
    return entity_id


def make_document(title: str, blocks: list[dict]) -> bytes:
    return json.dumps(
        {"title": title, "description": f"{title} description", "blocks": blocks},
        ensure_ascii=False,
    ).encode("utf-8")


def text_block(value: str) -> dict:
    return {"type": "paragraph", "text": value}


class FakeRow(dict):
    """Enough of an sqlite3.Row: transform_document only subscripts it."""


class ChunkingTests(unittest.TestCase):
    def test_normalize_name_ignores_case_and_punctuation(self):
        self.assertEqual(
            chunking.normalize_name("Set Timer by Function Name"),
            chunking.normalize_name("settimerbyfunctionname"),
        )
        self.assertEqual(
            chunking.normalize_name("Timing::Library::SetTimer"),
            "timinglibrarysettimer",
        )

    def test_humanize_cpp_identifier(self):
        self.assertEqual(
            chunking.humanize_cpp_identifier("Timing::Library::SetTimer"),
            "Set Timer",
        )

    def test_chunks_never_exceed_hard_token_limit(self):
        # A single body far over the limit must split, with every piece under it.
        body = " ".join(f"word{index}" for index in range(6000))
        section = {
            "position": 0,
            "title": "Big",
            "heading_path": "Page > Big",
            "heading_level": 2,
            "body_md": body,
            "knowledge_type": "details",
            "source_url": "https://example.invalid/x",
            "source_anchor": "https://example.invalid/x#big",
            "quality_score": 1.0,
        }
        chunks = chunking.chunk_section(
            section, page_title="Page", category="guides", document_type=None
        )
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk["token_estimate"], 900)

    def test_code_fence_stays_balanced_after_splitting(self):
        fence = "```cpp\n" + "\n".join(f"int value{i} = {i};" for i in range(900)) + "\n```"
        units = chunking.markdown_units(fence, max_chars=3200)
        self.assertGreater(len(units), 1)
        for unit in units:
            self.assertEqual(unit.count("```") % 2, 0, unit[:80])


def chunking_entity(*, title: str, path: str, category: str) -> dict:
    from docatlas.documents import entity_descriptor

    return entity_descriptor(
        title=title,
        path=path,
        category=category,
        source_url="https://example.invalid/doc",
        source_type=None,
        document_type=None,
    )


class LeadSentenceTests(unittest.TestCase):
    """Which sentence becomes the summary. One rule, shared by every adapter.

    A copy per adapter drifts apart, and every copy missed the same thing: a
    line holding nothing but an image had the image's alt text taken as the
    summary. Sites that open each page with a diagram then store flattened
    image syntax as the page description, and it reaches the Markdown export.
    """

    def test_an_image_only_line_is_not_the_summary(self):
        """A common shape: diagram first, the real description after it."""
        self.assertEqual(
            htmlmd.lead_sentence(
                "![Blur Attribute node.](https://example.invalid/x.webp)\n\n"
                "The Blur Attribute node smooths values between neighbors.\n"
            ),
            "The Blur Attribute node smooths values between neighbors.",
        )

    def test_an_image_wrapped_in_a_link_is_also_skipped(self):
        self.assertEqual(
            htmlmd.lead_sentence(
                "[![Node.](https://example.invalid/a.png)](https://example.invalid/a)\n"
                "This node converts a shader to a color value.\n"
            ),
            "This node converts a shader to a color value.",
        )

    def test_a_bare_label_is_not_a_summary(self):
        """A badge or a parameter name is not "what this page is about"."""
        self.assertEqual(
            htmlmd.lead_sentence(
                "EEVEE Only\n\nThe Shader to RGB node is used for stylized shading.\n"
            ),
            "The Shader to RGB node is used for stylized shading.",
        )

    def test_a_bold_opening_is_kept(self):
        """A `**bold**` opening is the first sentence, not a list item.

        Rejecting every line that starts with `*` loses it.
        """
        self.assertEqual(
            htmlmd.lead_sentence("**Chaos Physics** is a light-weight solution.\n"),
            "Chaos Physics is a light-weight solution.",
        )

    def test_a_blockquote_opening_is_kept(self):
        """Some sites put the opening line in a blockquote; strip the marker
        and that is the sentence.
        """
        self.assertEqual(
            htmlmd.lead_sentence("> This service manages player data storage.\n"),
            "This service manages player data storage.",
        )

    def test_headings_tables_and_lists_are_skipped(self):
        self.assertEqual(
            htmlmd.lead_sentence(
                "# Page title\n| a | b |\n| --- | --- |\n- bullet item here\n"
                "* star bullet item\n\nThe actual description sentence lives here.\n"
            ),
            "The actual description sentence lives here.",
        )

    def test_a_page_with_nothing_quotable_gets_no_summary(self):
        """Nothing quotable means no summary. The page name is in the title."""
        self.assertEqual(
            htmlmd.lead_sentence("## Inputs\n\n  Image\n\n  Brightness\n"), ""
        )

    def test_length_is_measured_in_characters_not_words(self):
        """Measured in characters, not words: a CJK sentence has no spaces and
        would be dismissed as a label if words were counted.
        """
        self.assertEqual(
            htmlmd.lead_sentence("这个节点用于平滑相邻几何元素之间的属性值。\n"),
            "这个节点用于平滑相邻几何元素之间的属性值。",
        )


class PageSummaryTests(unittest.TestCase):
    """A summary is prepended to the body only when the body does not already
    say it.

    For many adapters the "summary" *is* the body's first sentence, taken
    straight off the first line of markdown. Inserting it again in front of the
    body shows the reader the same sentence twice in a row.

    But summaries cannot simply be dropped: where one comes from page metadata
    or front matter, the body never states it, and removing it deletes the
    page's opening line. On some sites the great majority of pages have nothing
    in section 0 except that summary.
    """

    def first_body(self, *, title, description, markdown):
        sections = chunking.split_sections(
            title=title,
            description=description,
            markdown=markdown,
            source_url="https://example.invalid/page",
            category="guides",
        )
        return sections[0]["body_md"] if sections else ""

    def test_a_summary_lifted_from_the_first_line_is_not_repeated(self):
        """One shape: the summary is the first line of the body, word for word."""
        lead = "C++20 is a major version after C++17."
        body = self.first_body(
            title="C++20",
            description=lead,
            markdown=f"{lead}\n\n## New language features\n\n- Concepts\n",
        )
        self.assertEqual(body.count(lead), 1, f"the same sentence twice: {body!r}")

    def test_a_truncated_summary_of_the_first_sentence_is_not_repeated(self):
        """Another shape: the summary is a truncated first sentence, while the
        body keeps the bold and the rest of it.

        Comparing for equality is not enough — only the opening matches, so an
        equality test lets the repetition through.
        """
        body = self.first_body(
            title="Physics",
            description="Chaos Physics is a light-weight physics simulation solution.",
            markdown=(
                "**Chaos Physics** is a light-weight physics simulation solution,"
                " built from the ground up.\n\n## Destruction\n"
            ),
        )
        self.assertNotIn("solution.\n", body.replace("\n\n", "\n")[:200])
        self.assertEqual(body.count("light-weight"), 1, body)

    def test_a_summary_sitting_behind_an_opening_table_is_not_repeated(self):
        """The summary sentence need not come first.

        A page may open with a caveat table, putting the first sentence of
        prose after it — and picking a summary skips table rows. Testing only
        "does the body start with the summary" misses this case.
        """
        lead = "The following tables present compiler support for new C++ features."
        body = self.first_body(
            title="Compiler support",
            description=lead,
            markdown=f"| Notice | This page may lag behind. |\n| --- | --- |\n\n{lead}\n",
        )
        self.assertEqual(body.count(lead), 1, f"the same sentence twice: {body!r}")

    def test_a_summary_the_body_never_states_is_kept(self):
        """Control case: a summary from metadata is nowhere in the body and has
        to stay.

        Lose this and, on some sites, section 0 goes empty across hundreds of
        pages.
        """
        summary = "Learn how to use the PCG framework."
        body = self.first_body(
            title="PCG Biome Core",
            description=summary,
            markdown="The PCG Biome plugins are examples of the framework.\n",
        )
        self.assertIn(summary, body)

    def test_a_summary_is_kept_when_the_body_opens_with_a_heading(self):
        """When the body opens with a heading, section 0 holds nothing but the
        summary, so dropping it is worse still.
        """
        summary = "How to implement your character."
        body = self.first_body(
            title="Character",
            description=summary,
            markdown="## Goals\n\nMake a new character.\n",
        )
        self.assertEqual(body.strip(), summary)

    def test_a_summary_that_merely_restates_the_title_is_still_dropped(self):
        """The existing title deduplication still applies."""
        body = self.first_body(
            title="Attribute Statistic node",
            description="Attribute Statistic node.",
            markdown="The node evaluates a field on a geometry.\n",
        )
        self.assertNotIn("Attribute Statistic node.", body)


class SearchQueryTests(unittest.TestCase):
    def test_expression_ladder_goes_precise_to_loose(self):
        stages = [stage for stage, _ in search.fts_expressions("set timer by name")]
        self.assertEqual(stages[0], "phrase")
        self.assertIn("all_terms", stages)
        self.assertIn("any_term", stages)
        self.assertEqual(stages[-1], "prefix")

    def test_stopwords_dropped_only_in_loose_stages(self):
        expressions = dict(search.fts_expressions("how do I use streaming tessellation"))
        self.assertIn('"how"', expressions["all_terms"])
        self.assertNotIn('"how"', expressions["any_term"])
        self.assertIn('"streaming"', expressions["any_term"])

    def test_single_token_has_no_phrase_stage(self):
        stages = [stage for stage, _ in search.fts_expressions("streaming")]
        self.assertNotIn("phrase", stages)

    def test_empty_query_yields_nothing(self):
        self.assertEqual(search.fts_expressions("   "), [])

    def test_knowledge_type_ranking_prefers_answers_over_indexes(self):
        self.assertGreater(
            search.API_TYPE_BONUS["parameters"],
            search.API_TYPE_BONUS["navigation"],
        )

    def test_identifier_queries_are_treated_as_api_lookups(self):
        for query in ("set_timer", "Widget::resize", "getWidget"):
            self.assertEqual(
                search.query_profile(query, entity_hit=False), "api", query
            )

    def test_plain_words_are_treated_as_concept_questions(self):
        for query in ("streaming", "how do I set up virtual shadow maps"):
            self.assertEqual(
                search.query_profile(query, entity_hit=False), "concept", query
            )

    def test_entity_hit_forces_api_profile(self):
        self.assertEqual(search.query_profile("streaming", entity_hit=True), "api")

    def test_member_listings_are_pushed_back_only_for_concept_questions(self):
        # A long member listing does not answer "what is this", but when a
        # specific symbol is asked for the answer is often **inside** that
        # table: many sites give a property no page of its own, recording it
        # only in its owner's member table. Pushing it back unconditionally
        # buries the one official definition there is.
        row = {
            "knowledge_type": "details",
            "category": (config.DATASET.verbose_categories or ("reference",))[0],
            "token_estimate": 600,
            "quality_score": 1.0,
            "page_title": "LayoutContainer",
            "heading_path": "LayoutContainer > Variables",
        }
        def score(profile):
            return search._score(
                row,
                "all_terms",
                0,
                search._Scoring(
                    workspace=runtime.active(),
                    terms=set(),
                    normalized_query="targetarmlength",
                    profile=profile,
                ),
            )

        api = score("api")
        concept = score("concept")
        self.assertGreater(api - concept, 5.0)

    def test_concept_profile_prefers_overview_over_return_tables(self):
        self.assertGreater(
            search.CONCEPT_TYPE_BONUS["overview"],
            search.CONCEPT_TYPE_BONUS["returns"],
        )
        # For an API lookup it is the other way round: a return value beats a
        # general overview.
        self.assertGreater(
            search.API_TYPE_BONUS["returns"],
            search.CONCEPT_TYPE_BONUS["returns"],
        )


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.limiter = net.GlobalRateLimiter()

    def test_success_streak_raises_rate(self):
        start = self.limiter.requests_per_second
        for _ in range(self.limiter.PROBE_EVERY):
            self.limiter.record_success()
        self.assertGreater(self.limiter.requests_per_second, start)

    def test_one_backoff_per_throttle_episode(self):
        start = self.limiter.requests_per_second
        # Every request in flight fails during one throttling event; the rate
        # should drop a single step, not once per failure.
        for _ in range(10):
            self.limiter.penalize(5.0)
        self.assertAlmostEqual(
            self.limiter.requests_per_second,
            start * self.limiter.BACKOFF_FACTOR,
            places=6,
        )
        self.assertEqual(self.limiter.throttle_events, 1)

    def test_cooldown_escalates_only_on_repeat_throttling(self):
        import time as _time

        self.limiter.penalize(0.0)
        first = self.limiter.cooldown_until - _time.monotonic()
        self.assertLessEqual(first, self.limiter.BASE_COOLDOWN + 0.5)

        # Refused again after the cooldown means still too fast: wait longer.
        self.limiter.cooldown_until = 0.0
        self.limiter.penalize(0.0)
        second = self.limiter.cooldown_until - _time.monotonic()
        self.assertGreater(second, first)
        self.assertLessEqual(second, self.limiter.MAX_COOLDOWN)

    def test_retry_after_header_wins_over_escalation(self):
        import time as _time

        self.limiter.penalize(30.0)
        self.assertGreater(
            self.limiter.cooldown_until - _time.monotonic(),
            self.limiter.BASE_COOLDOWN,
        )

    def test_rate_never_falls_below_floor(self):
        for _ in range(50):
            self.limiter.cooldown_until = 0.0  # separate throttling events
            self.limiter.penalize(1.0)
        self.assertGreaterEqual(self.limiter.requests_per_second, self.limiter.MIN_RATE)

    def test_fixed_rate_disables_adaptation(self):
        self.limiter.configure(2.5)
        for _ in range(100):
            self.limiter.record_success()
        self.assertEqual(self.limiter.requests_per_second, 2.5)


class FakeResponse:
    def __init__(self, status, headers, body=b"", reason="stub"):
        self.status = status
        self.reason = reason
        self.headers = headers
        self._body = body

    def read(self):
        return self._body


class FakeConnection:
    def __init__(self, response):
        self._response = response
        self.requests = []

    def request(self, method, target, headers=None):
        self.requests.append((method, target))

    def getresponse(self):
        return self._response

    def close(self):
        pass


class OnDemandLookupTests(unittest.TestCase):
    """Locating for on-demand fetching: can the wanted page be recognised
    among the inventory entries whose bodies are not fetched yet."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        for path, category in [
            ("/docs/reference/core/Container/GetLayoutStrategy", "reference"),
            ("/docs/reference/nodes/Timing/SetTimerbyFunctionName", "reference"),
            ("/docs/guides/streaming-geometry-in-exampleware", "guides"),
        ]:
            self.connection.execute(
                "INSERT INTO pages(url, path, category, sitemap_url, route_depth) "
                "VALUES(?, ?, ?, 'https://example.invalid/s.xml', 3)",
                (f"https://example.invalid{path}", path, category),
            )
        self.connection.commit()
        initialize_db(self.connection)  # triggers the normalized_slug backfill

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def find(self, query, **kwargs):
        return ondemand.find_uncrawled_candidates(
            self.connection, query, limit=5, **kwargs
        )

    def test_slug_is_backfilled(self):
        slugs = {
            row[0]
            for row in self.connection.execute(
                "SELECT normalized_slug FROM pages"
            )
        }
        self.assertIn("getlayoutstrategy", slugs)

    def test_partial_word_finds_longer_slug(self):
        rows = self.find("streaming")
        self.assertTrue(rows)
        self.assertIn("streaming", rows[0]["path"])

    def test_short_query_does_not_scoop_up_everything(self):
        self.assertEqual(self.find("api"), [])

    def test_category_filter_is_respected(self):
        self.assertEqual(self.find("GetLayoutStrategy", category="guides"), [])

    def test_missing_exact_pages_counts_only_uncrawled(self):
        self.assertEqual(
            ondemand.missing_exact_pages(self.connection, "GetLayoutStrategy"), 1
        )
        self.connection.execute(
            "UPDATE pages SET status='success' WHERE normalized_slug=?",
            ("getlayoutstrategy",),
        )
        self.assertEqual(
            ondemand.missing_exact_pages(self.connection, "GetLayoutStrategy"), 0
        )

    def test_fetch_now_on_empty_list_is_a_noop(self):
        outcome = ondemand.fetch_now(self.connection, [])
        self.assertEqual(outcome["requested"], 0)
        self.assertEqual(outcome["succeeded"], 0)


class RedirectHandlingTests(unittest.TestCase):
    """A document API may signal "this page moved" with a 302, an empty
    Location, and a redirect_url in the body instead."""

    def _run(self, response):
        connection = FakeConnection(response)
        original = net._connection
        net._connection = lambda scheme, host, timeout: connection
        try:
            return net._request_once("https://example.invalid/x", 10)
        finally:
            net._connection = original

    def test_302_without_location_returns_body(self):
        payload = b'{"redirect_url":"https://example.invalid/moved"}'
        body, _url, _ct = self._run(
            FakeResponse(302, {"Content-Type": "application/json"}, payload)
        )
        self.assertEqual(body, payload)

    def test_302_without_location_or_body_still_raises(self):
        with self.assertRaises(net.HTTPResponseError):
            self._run(FakeResponse(302, {}, b""))

    def test_client_errors_still_raise(self):
        with self.assertRaises(net.HTTPResponseError) as caught:
            self._run(FakeResponse(404, {}, b"nope"))
        self.assertEqual(caught.exception.code, 404)


class HtmlCleanupTests(unittest.TestCase):
    def test_inline_html_in_text_fields_becomes_markdown(self):
        from docatlas.htmlmd import collect_strings

        block = {
            "type": "custom",
            "description": "<strong>Virtual Shadow Maps</strong> are <em>new</em>.",
        }
        rendered = " ".join(collect_strings(block))
        self.assertIn("**Virtual Shadow Maps**", rendered)
        self.assertNotIn("<strong>", rendered)

    def test_code_keeps_angle_brackets(self):
        from docatlas.htmlmd import collect_strings, maybe_html_to_markdown

        self.assertEqual(
            maybe_html_to_markdown("TArray<int32> Values;"), "TArray<int32> Values;"
        )
        block = {"type": "code", "code": "Table<Name, Handle<Node>> Map;"}
        self.assertIn("Table<Name, Handle<Node>> Map;", collect_strings(block))

    def test_plain_text_leaves_no_tag_fragments(self):
        from docatlas.htmlmd import plain_text

        cleaned = plain_text("<strong>VSM</strong> is <em>new</em>")
        self.assertNotIn("<", cleaned)
        self.assertNotIn("strong", cleaned)


class ThrottleClassificationTests(unittest.TestCase):
    def test_throttle_errors_are_not_page_failures(self):
        self.assertTrue(store._is_throttled("HTTP 429: Too Many Requests"))
        self.assertTrue(store._is_throttled("HTTP 503: Service Unavailable"))
        self.assertFalse(store._is_throttled("HTTP 404: Not Found"))
        self.assertFalse(store._is_throttled("HTTP 302: Found"))


class EndToEndTests(unittest.TestCase):
    """Build a small library and walk the whole path: fetch result → storage
    → retrieval → context pack."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "test.sqlite3"
        self.connection = connect_db(self.db_path)
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/sitemap.xml', 'guides', 'success')"
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def add_page(self, path: str, category: str, title: str, blocks: list[dict]) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO pages(url, path, category, sitemap_url, doc_version, locale,
                              route_depth, discovered_at, last_seen_at)
            VALUES(?, ?, ?, 'https://example.invalid/sitemap.xml', '5.8', 'en-US',
                   3, '2026-01-01', '2026-01-01')
            """,
            (f"https://example.invalid{path}", path, category),
        )
        page_id = cursor.lastrowid
        row = FakeRow(id=page_id, path=path, url=f"https://example.invalid{path}",
                      category=category)
        result = transform_document(row, make_document(title, blocks))
        store.store_document_result(self.connection, result, category)
        self.connection.commit()
        return page_id

    def seed(self):
        self.add_page(
            "/docs/reference/nodes/Timing/SetTimerbyFunctionName",
            "reference",
            "Set Timer by Function Name",
            [
                {"type": "heading", "level": 2, "text": "Inputs"},
                text_block("Function Name — delegate function name."),
                {"type": "heading", "level": 2, "text": "Outputs"},
                text_block("Return Value — the timer handle."),
            ],
        )
        self.add_page(
            "/docs/guides/streaming-overview",
            "guides",
            "Streaming Virtualized Geometry",
            [
                text_block("Streaming is a virtualized micropolygon geometry system."),
                {"type": "heading", "level": 2, "text": "Requirements"},
                text_block("Requires DX12 and the deferred renderer."),
            ],
        )

    def test_search_finds_entity_by_exact_name(self):
        self.seed()
        results = search.search_chunks(
            self.connection, "Set Timer by Function Name", limit=5
        )
        self.assertTrue(results)
        self.assertEqual(results[0]["match_stage"], "entity")
        self.assertIn("Set Timer", results[0]["page_title"])

    def test_search_handles_natural_language_question(self):
        self.seed()
        results = search.search_chunks(
            self.connection, "what does streaming require", limit=5
        )
        self.assertTrue(results, "a loose question should still return something")
        self.assertTrue(
            any("Streaming" in row["page_title"] for row in results)
        )

    def test_every_chunk_carries_its_source_url(self):
        self.seed()
        rows = self.connection.execute(
            "SELECT source_anchor, content_md FROM chunks"
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["source_anchor"].startswith("https://"))
            self.assertIn("DOC source", row["content_md"])

    def test_context_pack_respects_token_budget(self):
        self.seed()
        for budget in (50, 200, 1000, 5000):
            pack = context.build_context_pack(
                self.connection, "Streaming", token_budget=budget, category=None
            )
            self.assertLessEqual(
                pack["estimated_tokens"],
                budget,
                f"budget {budget} was exceeded",
            )

    @staticmethod
    def _candidates() -> list[dict]:
        """One page with five strong candidates, three pages with one weak one.

        The shape to reproduce: the right page offers many closely ranked
        chunks, a hard per-page cap lets only two through, and the rest of the
        budget goes to lower-scoring material from elsewhere.
        """
        def chunk(chunk_id: int, page_id: int) -> dict:
            return {
                "id": chunk_id, "page_id": page_id, "token_estimate": 100,
                "content_md": "x" * 40, "content_hash": f"h{chunk_id}",
            }

        return [chunk(index, 1) for index in range(5)] + [
            chunk(10 + index, 10 + index) for index in range(3)
        ]

    def test_a_roomy_budget_does_not_skip_better_chunks_of_the_same_page(self):
        """Skipping them substitutes lower-scoring material from other pages,
        which is a worse answer."""
        selected, _used = context._select_primary(self._candidates(), 800)
        from_best = [row for row in selected if row["page_id"] == 1]
        self.assertGreater(
            len(from_best),
            context.MIN_CHUNKS_PER_PAGE,
            "budget had room, yet the best page still gave only its floor",
        )

    def test_one_page_still_cannot_eat_the_whole_budget(self):
        selected, _used = context._select_primary(self._candidates(), 800)
        pages = {row["page_id"] for row in selected}
        self.assertGreater(len(pages), 1, "a single page means the share cap did nothing")

    def test_a_small_budget_still_gets_the_floor(self):
        """When the share leaves no room for a second chunk it is given anyway,
        or this is worse than what it replaced."""
        selected, _used = context._select_primary(self._candidates(), 250)
        from_best = [row for row in selected if row["page_id"] == 1]
        self.assertEqual(len(from_best), context.MIN_CHUNKS_PER_PAGE)

    def test_no_single_page_eats_the_budget(self):
        """The same on real data: what is capped is a share of the budget,
        not a number of chunks."""
        self.seed()
        for budget in (1500, 3000, 100000):
            pack = context.build_context_pack(
                self.connection, "Streaming", token_budget=budget, category=None
            )
            per_page: dict[int, list[int]] = {}
            for item in pack["primary_knowledge"]:
                per_page.setdefault(item["page_id"], []).append(
                    int(item["token_estimate"] or 1)
                )
            allowance = int(
                budget * context.PRIMARY_BUDGET_RATIO * context.MAX_PAGE_BUDGET_RATIO
            )
            for tokens in per_page.values():
                if len(tokens) <= context.MIN_CHUNKS_PER_PAGE:
                    continue  # floor chunks are exempt from the share cap
                self.assertLessEqual(
                    sum(tokens[:-1]),
                    allowance,
                    f"at budget {budget} a page went over its share",
                )

    def test_context_markdown_is_cheaper_than_json(self):
        self.seed()
        pack = context.build_context_pack(
            self.connection, "Streaming", token_budget=3000, category=None
        )
        markdown = context.render_context_markdown(pack)
        as_json = json.dumps(pack, ensure_ascii=False, indent=2)
        self.assertLess(len(markdown), len(as_json))
        self.assertIn("DOC source", markdown)

    def test_empty_result_renders_a_useful_message(self):
        self.seed()
        pack = context.build_context_pack(
            self.connection, "zzzznotarealthing", token_budget=1000, category=None
        )
        markdown = context.render_context_markdown(pack)
        self.assertIn("No match in the local library", markdown)

    # ---- the query names a page outright ----------------------------------

    def seed_named_page(self):
        """One broad overview page and one specific target page.

        The overview is deliberately stuffed with the words in the address, to
        reproduce the real failure: treated as ordinary full-text, the words in
        the URL make the overview win and push the named page down.
        """
        self.add_page(
            "/docs/guides/overview",
            "guides",
            "Documentation Overview",
            [
                text_block(
                    "These docs cover the whole documentation set. "
                    "The guides are organised by area."
                ),
            ],
        )
        return self.add_page(
            "/docs/guides/timer-handles",
            "guides",
            "Timer Handles",
            [
                text_block("A timer handle identifies a scheduled callback."),
                {"type": "heading", "level": 2, "text": "Clearing"},
                text_block("Clear the handle to cancel the callback."),
            ],
        )

    def test_an_official_url_scopes_the_answer_to_that_page(self):
        """Recognising the URL is not enough — the **answer** has to land on
        that page.

        The locator can resolve a URL to the right path while the context pack
        still runs full-text search over the whole URL string, putting the
        overview first and the target below it. The most precise input a user
        can give, answered with a different page.
        """
        target_id = self.seed_named_page()
        pack = context.build_context_pack(
            self.connection,
            "https://example.invalid/docs/en/guides/timer-handles",
            token_budget=3000,
            category=None,
        )
        self.assertTrue(pack["primary_knowledge"], "the named page must have content")
        self.assertEqual(
            {item["page_id"] for item in pack["primary_knowledge"]},
            {target_id},
            "pages nobody named leaked into the answer",
        )

    def test_an_inventory_path_scopes_the_answer_the_same_way(self):
        target_id = self.seed_named_page()
        pack = context.build_context_pack(
            self.connection,
            "/docs/guides/timer-handles",
            token_budget=3000,
            category=None,
        )
        self.assertEqual(
            {item["page_id"] for item in pack["primary_knowledge"]}, {target_id}
        )

    def test_a_named_page_with_no_body_yet_returns_nothing_rather_than_others(self):
        """A named page with no body yet returns nothing rather than a stand-in.

        An empty result makes `answer()` fetch that page; returning something
        unrelated stops the fetch happening at all, and the user gets an answer
        to a question they did not ask that still looks like an answer.
        """
        self.seed_named_page()
        self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, status, route_depth)"
            " VALUES('https://example.invalid/docs/guides/not-yet',"
            " '/docs/guides/not-yet', 'guides',"
            " 'https://example.invalid/sitemap.xml', 'pending', 3)"
        )
        self.connection.commit()
        pack = context.build_context_pack(
            self.connection,
            "/docs/guides/not-yet",
            token_budget=3000,
            category=None,
        )
        self.assertEqual(pack["primary_knowledge"], [])

    def test_a_normal_query_is_untouched_by_the_named_page_path(self):
        """Control case: an ordinary query with no address behaves exactly as
        it did before."""
        self.seed_named_page()
        pack = context.build_context_pack(
            self.connection, "timer handle", token_budget=3000, category=None
        )
        self.assertTrue(pack["primary_knowledge"])
        self.assertIn(
            "Timer Handles", pack["primary_knowledge"][0]["page_title"]
        )


class RelevanceRankingTests(unittest.TestCase):
    """Ranking across stages goes by bm25 relevance, not by how many query
    words a page happens to contain.

    The typical shape: one extra ubiquitous word in the query lets a huge
    release-notes page collect every keyword and displace the page that is
    actually about the subject but happens to lack that one word.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = connect_db(Path(self.directory.name) / "test.sqlite3")
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/sitemap.xml', 'guides', 'success')"
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def add_page(self, path, title, blocks):
        cursor = self.connection.execute(
            """
            INSERT INTO pages(url, path, category, sitemap_url, doc_version, locale,
                              route_depth, discovered_at, last_seen_at)
            VALUES(?, ?, 'guides', 'https://example.invalid/sitemap.xml', '5.8',
                   'en-US', 3, '2026-01-01', '2026-01-01')
            """,
            (f"https://example.invalid{path}", path),
        )
        page_id = cursor.lastrowid
        row = FakeRow(id=page_id, path=path, url=f"https://example.invalid{path}",
                      category="guides")
        store.store_document_result(
            self.connection,
            transform_document(row, make_document(title, blocks)),
            "guides",
        )
        self.connection.commit()
        return page_id

    def seed(self):
        """One page short and precise (missing the word "node"), one long and
        mixed (holding all three words).

        Twenty ordinary pages go in first so that "node" really is ubiquitous.
        That step cannot be skipped: in a corpus of two or three pages every
        word is rare, the IDF is inverted, and the real shape never appears.
        """
        for n in range(20):
            self.add_page(
                f"/nodes/{n}",
                f"Node Reference {n}",
                [
                    text_block(
                        f"This node exposes pins on the blueprint node graph. "
                        f"Connect the node output to another node input {n}."
                    )
                ],
            )
        focused = self.add_page(
            "/random-streams",
            "Random Streams",
            [
                text_block(
                    "A random stream produces a repeatable sequence of random "
                    "values from a single seed. Expose the initial seed to change "
                    "which stream of random values the blueprint variable yields."
                )
            ],
        )
        filler = " ".join(
            f"Fixed an unrelated regression in subsystem {n} affecting editor "
            f"startup, asset cooking and platform packaging behaviour."
            for n in range(40)
        )
        bloated = self.add_page(
            "/release-notes",
            "Release Notes",
            [
                text_block(
                    f"{filler} Added a Shared State input to the Array Random Get "
                    f"node so random state is shared between nodes. Improved audio "
                    f"stream chunk loading. {filler}"
                )
            ],
        )
        return focused, bloated

    def best_per_page(self, query, limit=20):
        """Keep each page's best chunk: a long page splits into several, which
        land in different stages."""
        best = {}
        for row in search.search_chunks(self.connection, query, limit=limit):
            best.setdefault(row["page_id"], row)
        return best

    def test_focused_page_beats_a_bloated_page_that_matches_every_term(self):
        focused, bloated = self.seed()
        results = search.search_chunks(
            self.connection, "random stream node", limit=5
        )
        self.assertTrue(results)
        self.assertIn(
            bloated,
            {row["page_id"] for row in results},
            "control: the long page did reach the pool, it was not filtered out",
        )
        self.assertEqual(
            results[0]["page_id"],
            focused,
            "the short precise page must come first, missing word and all",
        )

    def test_the_winner_comes_from_the_looser_stage(self):
        """Confirm the win came from comparing across stages, not from both
        pages happening to land in the same one."""
        focused, _ = self.seed()
        best = self.best_per_page("random stream node")
        self.assertEqual(best[focused]["match_stage"], "any_term")

    def test_score_gap_tracks_bm25_not_result_position(self):
        """The gap has to come from bm25, not from being one rank apart.

        Without this, removing relevance entirely and scoring by rank still
        passes the two assertions above, because the title match alone lifts
        the right page. That would test the title bonus, not this behaviour.
        """
        focused, bloated = self.seed()
        best = self.best_per_page("random stream node")
        self.assertGreater(
            best[focused]["score"] - best[bloated]["score"],
            search.RELEVANCE_WEIGHT * 0.5,
            "relevance is not really taking part in the score",
        )

    def test_fts_hits_come_back_best_first(self):
        """The pool is the top N, so it must already be in bm25 order before the
        cut — with tens of thousands of hits, which batch survives LIMIT would
        otherwise be luck."""
        self.seed()
        rows = search._fts_hits(
            self.connection, '"node" OR "random" OR "stream"', None, 10
        )
        scores = [row["bm25_score"] for row in rows]
        self.assertLess(min(scores), -1.0, "the fixture must separate, or this asserts nothing")
        self.assertEqual(scores, sorted(scores), "more negative bm25 is better, so ascending")

    def test_relevance_baseline_spans_every_comparable_stage(self):
        """The normalisation baseline spans stages: the best match in a loose
        stage should not score the same as a poor one in a precise stage."""
        batches = [
            ("all_terms", [FakeRow(bm25_score=-2.0), FakeRow(bm25_score=-1.0)]),
            ("any_term", [FakeRow(bm25_score=-8.0)]),
        ]
        strict, loose = search.stage_relevance(batches)
        self.assertEqual(loose, [1.0])
        self.assertEqual(strict, [0.25, 0.125])

    def test_unlike_scales_fall_back_to_result_position(self):
        """`phrase` and `prefix` bm25 are different scales and must not be
        normalised together."""
        batches = [
            ("phrase", [FakeRow(bm25_score=-9.0)]),
            ("all_terms", [FakeRow(bm25_score=-4.0)]),
        ]
        phrase, all_terms = search.stage_relevance(batches)
        self.assertIsNone(phrase)
        self.assertEqual(all_terms, [1.0])

    def test_relevance_outweighs_the_stage_it_came_from(self):
        """On one scale, better bm25 always scores higher — even from a looser
        stage."""
        ctx = search._Scoring(
            workspace=runtime.active(), terms=set(), normalized_query=""
        )
        row = FakeRow(
            knowledge_type="details", category="guides", quality_score=0.0,
            page_title="", heading_path="", source_url="",
        )
        strong = search._score(row, "any_term", 0, ctx, relevance=1.0)
        weak = search._score(row, "all_terms", 0, ctx, relevance=0.3)
        self.assertGreater(strong, weak)

    def test_stage_base_still_ranks_precise_evidence_above_loose_matches(self):
        """Control case: comparing across stages does not abolish stages."""
        self.assertGreater(
            search.STAGE_BASE["entity"],
            search.STAGE_BASE["phrase"],
        )
        self.assertGreater(
            search.STAGE_BASE["phrase"],
            search.STAGE_BASE["all_terms"] + search.RELEVANCE_WEIGHT * 0.5,
        )


def make_section(title, body, *, position, level=2, knowledge_type="details",
                 parent="Page"):
    return {
        "position": position,
        "title": title,
        "heading_path": f"{parent} > {title}",
        "heading_level": level,
        "body_md": body,
        "knowledge_type": knowledge_type,
        "source_url": "https://example.invalid/x",
        "source_anchor": f"https://example.invalid/x#{title.lower()}",
        "quality_score": 1.0,
    }


class ChunkMergingTests(unittest.TestCase):
    """Merging sections, so a page does not become a handful of twenty-word
    fragments."""

    def chunk(self, sections):
        return chunking.chunk_sections(
            sections, page_title="Page", category="reference", document_type=None
        )

    def test_small_neighbours_merge_into_one_usable_chunk(self):
        sections = [
            make_section("Inputs", "| Name | Type |\n|---|---|\n| A | int |", position=0,
                         knowledge_type="parameters"),
            make_section("Outputs", "| Name | Type |\n|---|---|\n| R | bool |", position=1,
                         knowledge_type="returns"),
        ]
        chunks = self.chunk(sections)
        self.assertEqual(len(chunks), 1)
        # Merging does not erase the subheadings: a reader still has to see
        # which part is inputs and which is outputs.
        self.assertIn("Inputs", chunks[0]["content_md"])
        self.assertIn("Outputs", chunks[0]["content_md"])

    def test_merging_stops_at_a_different_parent(self):
        sections = [
            make_section("A", "short one", position=0, parent="Page > Topic1"),
            make_section("B", "short two", position=1, parent="Page > Topic2"),
        ]
        self.assertEqual(len(self.chunk(sections)), 2)

    def test_large_sections_are_never_merged_away(self):
        sections = [
            make_section("Small", "tiny", position=0),
            make_section("Big", "x " * 2000, position=1),
        ]
        chunks = self.chunk(sections)
        self.assertGreaterEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(chunk["token_estimate"], 900)

    def test_navigation_content_is_kept_but_does_not_label_the_chunk(self):
        # A section called `Navigation` may be misnamed: besides breadcrumbs it
        # can hold the description and the most useful facts on the page, so it
        # cannot be dropped. Nor may it label the whole chunk "navigation",
        # which would penalise that chunk throughout retrieval.
        sections = [
            make_section(
                "Navigation",
                "Home > API > Thing\n\nReturns true if blocked\n\nTarget is Ability System Component",
                position=0, knowledge_type="navigation",
            ),
            make_section("Inputs", "real content here", position=1,
                         knowledge_type="parameters"),
        ]
        chunks = self.chunk(sections)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Returns true if blocked", chunks[0]["content_text"])
        self.assertIn("Target is Ability System Component", chunks[0]["content_text"])
        self.assertEqual(chunks[0]["knowledge_type"], "parameters")

    def test_navigation_alone_still_keeps_its_own_label(self):
        sections = [
            make_section("Navigation", "Home > API", position=0,
                         knowledge_type="navigation"),
        ]
        self.assertEqual(self.chunk(sections)[0]["knowledge_type"], "navigation")

    def test_no_runt_tail_chunk_is_left_behind(self):
        # A body that splits into one large chunk and a runt; the runt has to
        # merge back.
        body = ("paragraph body text. " * 120).strip() + "\n\nshort tail."
        sections = [make_section("Body", body, position=0)]
        chunks = self.chunk(sections)
        if len(chunks) > 1:
            self.assertGreaterEqual(chunks[-1]["token_estimate"], 50)
        self.assertIn("short tail.", chunks[-1]["content_md"])

    def test_merged_chunk_is_attributed_to_the_first_section(self):
        # chunks.section_id is NOT NULL, so a merged chunk hangs off the first
        # section of the group.
        sections = [
            make_section("Inputs", "a", position=3, knowledge_type="parameters"),
            make_section("Outputs", "b", position=4, knowledge_type="returns"),
        ]
        self.assertEqual(self.chunk(sections)[0]["section_position"], 3)


class ImportSmokeTests(unittest.TestCase):
    """Every module must import.

    It looks like a formality, but it has caught a real one: a broken f-string
    in cli.py, with the whole suite passing, because no case imported cli. A
    syntax error should not wait for a user to type the command.
    """

    def test_every_module_imports(self):
        import importlib
        import pkgutil

        import docatlas

        failures = []
        for module in pkgutil.walk_packages(
            docatlas.__path__, prefix="docatlas."
        ):
            try:
                importlib.import_module(module.name)
            except Exception as exc:  # noqa: BLE001 — catching everything is the point
                failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_cli_parser_builds_and_every_command_has_a_handler(self):
        from docatlas.cli import build_parser

        parser = build_parser()
        actions = [
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        ]
        self.assertTrue(actions, "the CLI should have subcommands")
        for name, sub in actions[0].choices.items():
            self.assertTrue(
                sub.get_default("func"), f"subcommand {name} has no implementation bound"
            )


class FetchedLanguageTests(unittest.TestCase):
    """A dataset's `language` is an instruction about which edition to request,
    not a fact about the site, so it cannot be filled in automatically.

    Whether the two agree can be checked automatically, though. Asked for a
    language it does not have, a site usually does not fail — it quietly serves
    its default, leaving an English library labelled German.
    """

    def _connection(self, locales):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE raw_documents(raw_json BLOB)")
        for locale in locales:
            payload = json.dumps({"title": "x", "locale": locale}).encode("utf-8")
            connection.execute(
                "INSERT INTO raw_documents VALUES(?)", (zlib.compress(payload),)
            )
        return connection

    def test_counts_what_the_server_actually_returned(self):
        counts = validate.fetched_locales(self._connection(["en-US", "en-us", "de-de"]))
        self.assertEqual(counts["en-us"], 2)  # case must not make it two
        self.assertEqual(counts["de-de"], 1)

    def test_a_silently_substituted_language_is_visible(self):
        # The one that matters: asked for German, given English, and that has
        # to be visible rather than pass in silence.
        counts = validate.fetched_locales(self._connection(["en-us"] * 5))
        wrong = sum(n for code, n in counts.items() if code != "de-de")
        self.assertEqual(wrong, 5)

    def test_corrupt_archives_do_not_crash_the_check(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE raw_documents(raw_json BLOB)")
        connection.execute("INSERT INTO raw_documents VALUES(?)", (b"not zlib",))
        self.assertEqual(validate.fetched_locales(connection), collections.Counter())

    def test_adapter_reports_nothing_when_the_site_says_nothing(self):
        # Ask the current dataset's adapter; name no site.
        self.assertIsNone(config.SOURCE.document_locale({"title": "x"}))


class SkillTemplateTests(unittest.TestCase):
    """The skill documents are an operating manual for an agent: a mistake in
    them raises nothing, it just makes the agent do the wrong thing."""

    SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "docatlas"

    def _docs(self):
        files = sorted(self.SKILL_DIR.glob("*.md"))
        self.assertTrue(files, "the skill directory holds no .md at all")
        return {path.name: path.read_text(encoding="utf-8") for path in files}

    def test_every_placeholder_has_a_filler(self):
        # A missed placeholder raises nothing; the agent reads a literal
        # {{...}} and goes looking for something that does not exist.
        from docatlas.cli import skill_substitutions

        fillers = skill_substitutions()
        found = False
        for name, text in self._docs().items():
            for placeholder in set(re.findall(r"\{\{([A-Z_]+)\}\}", text)):
                found = True
                self.assertIn(
                    placeholder, fillers, f"{name}: nobody knows {placeholder}"
                )
        self.assertTrue(found, "no placeholders at all means the template mechanism is dead")

    def test_fillers_are_not_empty(self):
        # An empty filler is worse than none: the agent reads a sentence with
        # its subject missing.
        from docatlas.cli import skill_substitutions

        for name, value in skill_substitutions().items():
            self.assertTrue(value.strip(), f"{name} filled in empty")

    def test_skill_does_not_hardcode_the_current_dataset(self):
        """The wording stays generic: which library is installed comes from the
        dataset and is never written into the text.

        Hardcoding it assumes everyone installed the same documentation, in the
        same language.
        """
        for placeholder in ("DATASET_NAME", "DATASET_TRIGGERS", "DATASET_LANGUAGE"):
            self.assertIn("{{" + placeholder + "}}", self._docs()["SKILL.md"])
        product = config.DATASET.product
        for name, text in self._docs().items():
            self.assertNotIn(
                product,
                text,
                f"{name} hardcodes the current product {product!r}",
            )

    def test_skill_points_at_the_build_workflows(self):
        # Without the pointer an agent only queries and never builds, leaving
        # the user to work out the TOML alone.
        self.assertIn("WORKFLOWS.md", self._docs()["SKILL.md"])

    def test_documented_commands_all_exist(self):
        # A command in the manual that does not exist gets run and fails, and
        # nobody finds out first.
        from docatlas.cli import build_parser

        real = set(build_parser()._subparsers._group_actions[0].choices)
        for name, text in self._docs().items():
            for command in set(re.findall(r"python -m docatlas ([a-z-]+)", text)):
                self.assertIn(command, real, f"{name} documents a command that does not exist: {command}")


class McpProtocolTests(unittest.TestCase):
    """The MCP server is hand-written, with no third-party SDK, so the
    protocol layer needs tests of its own."""

    def test_list_datasets_survives_no_default_chosen(self):
        """With several libraries and no default chosen, `docatlas_list_datasets`
        must not fall over.

        This tool exists so an agent can find out what is on the machine and
        whether to pass a dataset_id, which makes it the last one that should
        fail in exactly that state. Calling `runtime.active().id` to mark the
        default raises `DatasetNotChosen` when there is none, and the client
        gets a raw Python traceback instead of a sentence.
        """
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        original = runtime.LOCAL_SETTINGS
        runtime.LOCAL_SETTINGS = Path(directory.name) / ".docatlas-local.toml"
        self.addCleanup(setattr, runtime, "LOCAL_SETTINGS", original)
        previous = os.environ.pop("DOCATLAS_DATASET", None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, "DOCATLAS_DATASET", previous)
        two_datasets_installed(self)
        self.assertGreater(len(runtime.available_dataset_ids()), 1)

        def call():
            return mcpserver.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "docatlas_list_datasets",
                          "arguments": {"format": "json"}},
            })

        reply = contextvars.Context().run(call)
        self.assertFalse(reply["result"]["isError"], reply["result"])
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertIsNone(payload["default_dataset_id"])
        self.assertGreaterEqual(len(payload["datasets"]), 2)

    def test_handshake_echoes_the_client_protocol_version(self):
        # Insisting on one version makes older clients refuse the handshake.
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        })
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", reply["result"]["capabilities"])

    def test_notifications_get_no_reply(self):
        # A notification has no id; answering it is a protocol error at the
        # client's end.
        self.assertIsNone(
            mcpserver.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_every_advertised_tool_has_a_handler(self):
        advertised = {tool["name"] for tool in mcpserver.TOOLS}
        self.assertEqual(advertised, set(mcpserver.HANDLERS))

    def test_tool_schemas_are_well_formed(self):
        for tool in mcpserver.TOOLS:
            self.assertTrue(tool["description"], tool["name"])
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            for required in schema.get("required", []):
                self.assertIn(required, schema["properties"], tool["name"])

    def _serve(self, payload: str):
        out, err = io.StringIO(), io.StringIO()
        stderr = sys.stderr
        sys.stderr = err
        try:
            self.assertEqual(mcpserver.serve(io.StringIO(payload), out), 0)
        finally:
            sys.stderr = stderr
        return out.getvalue(), err.getvalue()

    def test_a_leading_bom_does_not_silence_the_server(self):
        """A Windows client easily writes a BOM at the head of the pipe: taking
        StandardInput in .NET flushes the UTF-8 preamble. With it, the JSON does
        not parse, and a server that drops bad lines **in silence** looks from
        the client's side like a process that started and never answers."""
        request = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        })
        out, _ = self._serve("﻿" + request + "\n")
        self.assertTrue(out.strip(), "the request with a BOM was dropped")
        self.assertEqual(json.loads(out)["result"]["protocolVersion"], "2024-11-05")

    def test_an_unparsable_line_says_so_on_stderr(self):
        """A bad line is still dropped, but not in silence, or there is nothing
        to investigate. The protocol owns stdout, so the notice goes to stderr."""
        out, err = self._serve("{not json\n")
        self.assertEqual(out, "", "stdout carries the protocol and nothing else")
        self.assertIn("{not json", err)

    def test_unknown_tool_is_a_protocol_error(self):
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        self.assertEqual(reply["error"]["code"], -32602)

    def test_tool_failure_is_reported_not_crashed(self):
        # An exception in a tool becomes an isError result rather than taking
        # the connection down.
        original = mcpserver.HANDLERS["docatlas_show"]
        mcpserver.HANDLERS["docatlas_show"] = lambda _a: 1 / 0
        try:
            reply = mcpserver.handle({
                "jsonrpc": "2.0", "id": 10, "method": "tools/call",
                "params": {"name": "docatlas_show", "arguments": {}},
            })
        finally:
            mcpserver.HANDLERS["docatlas_show"] = original
        self.assertTrue(reply["result"]["isError"])
        self.assertNotIn("error", reply)

    def test_bad_chunk_id_is_rejected_politely(self):
        # A mistake the caller can fix belongs in an isError result, not a
        # JSON-RPC protocol error: that one means "this request is malformed",
        # while this means "the request is fine, the argument needs changing".
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {
                "name": "docatlas_show",
                "arguments": {"chunk_id": "; DROP TABLE"},
            },
        })
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("Unreadable", reply["result"]["content"][0]["text"])
        self.assertNotIn("Traceback", reply["result"]["content"][0]["text"])

    def test_an_unknown_dataset_id_does_not_kill_the_server(self):
        """load_dataset reports failure with SystemExit, which a plain
        `except Exception` does not catch.

        Unhandled, one mistyped dataset_id takes the whole MCP process down.
        """
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {
                "name": "docatlas_ask",
                "arguments": {"query": "x", "dataset_id": "no-such-library"},
            },
        })
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("no-such-library", reply["result"]["content"][0]["text"])

    def test_tools_do_not_hardcode_one_datasets_categories(self):
        """Category enums written into the protocol become lies on the next
        dataset.

        Clients cache tools/list, so an enum naming another library's categories
        is worse than no enum at all.
        """
        for tool in mcpserver.TOOLS:
            category = tool["inputSchema"]["properties"].get("category")
            if category:
                self.assertNotIn("enum", category, tool["name"])

    def test_every_query_tool_can_choose_a_dataset(self):
        for tool in mcpserver.TOOLS:
            self.assertIn(
                "dataset_id", tool["inputSchema"]["properties"], tool["name"]
            )


class DatasetLayeringTests(unittest.TestCase):
    """The core must know no product at all; these cases hold that line."""

    def write_dataset(self, name: str, body: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="docatlas_ds_"))
        (directory / f"{name}.toml").write_text(body, encoding="utf-8")
        return directory

    def test_missing_dataset_names_what_is_available(self):
        directory = self.write_dataset("only-this", 'id="only-this"\nversion="1"\nsource="x"')
        with self.assertRaises(SystemExit) as caught:
            dataset.load_dataset("nope", directory)
        self.assertIn("only-this", str(caught.exception))

    def test_id_must_match_filename(self):
        # A mismatch puts the data directory and the configuration out of step;
        # better to refuse to start than to write to the wrong one in silence.
        directory = self.write_dataset("a", 'id="b"\nversion="1"\nsource="x"')
        with self.assertRaises(SystemExit) as caught:
            dataset.load_dataset("a", directory)
        self.assertIn("must match", str(caught.exception))

    def test_unknown_adapter_is_a_clear_error(self):
        directory = self.write_dataset(
            "d", 'id="d"\nversion="1"\nsource="no_such_site"'
        )
        loaded = dataset.load_dataset("d", directory)
        with self.assertRaises(SystemExit) as caught:
            dataset.load_source(loaded)
        # The error lists what else is available; which those are is up to the
        # machine.
        self.assertIn("no_such_site", str(caught.exception))
        self.assertIn(config.DATASET.source, str(caught.exception))

    def test_knowledge_pack_is_optional(self):
        directory = self.write_dataset(
            "d", f'id="d"\nversion="1"\nsource="{config.DATASET.source}"'
        )
        loaded = dataset.load_dataset("d", directory)
        self.assertIsNone(dataset.load_knowledge(loaded))
        # With no pack attached, asking for any capability quietly returns the
        # default rather than raising.
        self.assertEqual(dataset.knowledge_hook(None, "build_relations", None), None)
        self.assertEqual(dataset.knowledge_hook(None, "RELATION_LABELS", {}), {})

class DataRootTests(unittest.TestCase):
    """Where the program lives and where the data lives are two questions: the
    repository can sit on the system drive and a library of hundreds of
    thousands of pages on another."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Path(self.directory.name) / ".docatlas-local.toml"
        original = runtime.LOCAL_SETTINGS
        runtime.LOCAL_SETTINGS = self.settings
        self.addCleanup(setattr, runtime, "LOCAL_SETTINGS", original)
        self.addCleanup(self.directory.cleanup)

    def write(self, **values):
        self.settings.write_text(
            "".join(f'{key} = "{value}"\n' for key, value in values.items()),
            encoding="utf-8",
        )

    def resolve(self, home: str | None = None):
        previous = os.environ.pop("DOCATLAS_HOME", None)
        if home:
            os.environ["DOCATLAS_HOME"] = home
        try:
            return runtime._data_root()
        finally:
            os.environ.pop("DOCATLAS_HOME", None)
            if previous is not None:
                os.environ["DOCATLAS_HOME"] = previous

    def test_defaults_to_the_data_dir_inside_the_repo(self):
        self.assertEqual(self.resolve(), (runtime.REPO_ROOT / "data").resolve())

    def test_the_installed_choice_is_read_from_a_file(self):
        """It has to be a file: an MCP client starting a subprocess does not
        carry your shell's environment, so with DOCATLAS_HOME alone the library
        the command line finds is invisible to MCP."""
        chosen = Path(self.directory.name) / "elsewhere"
        self.write(home=chosen.as_posix())
        self.assertEqual(self.resolve(), chosen.resolve())

    def test_the_environment_variable_still_wins(self):
        """A one-off move should not force a file to be edited."""
        self.write(home=(Path(self.directory.name) / "installed").as_posix())
        temporary = Path(self.directory.name) / "just-this-once"
        self.assertEqual(self.resolve(str(temporary)), temporary.resolve())

    def test_a_broken_settings_file_is_loud(self):
        """Falling back to the default in silence leaves someone puzzling over
        answers from a different library."""
        self.settings.write_text("home = not valid toml\n", encoding="utf-8")
        with self.assertRaises(Exception):
            runtime.local_settings()


def two_datasets_installed(case: unittest.TestCase) -> Path:
    """Make the machine look as though two datasets were installed; returns
    that config directory.

    These cases verify what happens with several to choose from and no choice
    made. Leaning on the machine happening to have several is unreliable: a
    repository carrying only the template has one, and once the premise is gone
    the cases stay green while verifying nothing.
    """
    directory = Path(tempfile.mkdtemp(prefix="docatlas_many_"))
    case.addCleanup(shutil.rmtree, directory, True)
    for name in ("alpha-docs", "beta-docs"):
        (directory / f"{name}.toml").write_text(
            f'id="{name}"\nversion="1"\nsource="example"\n', encoding="utf-8"
        )
    original = runtime.DATASET_CONFIG_DIR
    runtime.DATASET_CONFIG_DIR = directory
    case.addCleanup(setattr, runtime, "DATASET_CONFIG_DIR", original)
    return directory


class DefaultDatasetTests(unittest.TestCase):
    """Which library to install is the user's choice; the program picks none."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Path(self.directory.name) / ".docatlas-local.toml"
        original = runtime.LOCAL_SETTINGS
        runtime.LOCAL_SETTINGS = self.settings
        self.addCleanup(setattr, runtime, "LOCAL_SETTINGS", original)
        self.addCleanup(self.directory.cleanup)
        self.previous = os.environ.pop("DOCATLAS_DATASET", None)
        self.addCleanup(self.restore)

    def restore(self):
        os.environ.pop("DOCATLAS_DATASET", None)
        if self.previous is not None:
            os.environ["DOCATLAS_DATASET"] = self.previous

    def test_no_product_is_hardcoded_as_the_default(self):
        """Hardcode a product and someone who built a different library first
        has to work out that they were defaulted onto another one — a mistake
        that looks like nothing more than "not found"."""
        source = (runtime.REPO_ROOT / "docatlas" / "runtime.py").read_text(
            encoding="utf-8"
        )
        for dataset_id in runtime.available_dataset_ids():
            self.assertNotIn(dataset_id, source, "the runtime hardcodes a dataset id")

    def test_the_installed_choice_is_used(self):
        chosen = runtime.available_dataset_ids()[0]
        self.settings.write_text(f'dataset = "{chosen}"\n', encoding="utf-8")
        self.assertEqual(runtime.default_dataset_id(), chosen)

    def test_the_environment_variable_still_wins(self):
        self.settings.write_text(
            f'dataset = "{runtime.available_dataset_ids()[0]}"\n', encoding="utf-8"
        )
        os.environ["DOCATLAS_DATASET"] = "just-this-once"
        self.assertEqual(runtime.default_dataset_id(), "just-this-once")

    def test_without_a_choice_it_says_so_instead_of_guessing(self):
        """Several libraries and no choice made: say a setting is missing,
        not that nothing was found."""
        two_datasets_installed(self)
        with self.assertRaises(runtime.DatasetNotChosen) as caught:
            runtime.default_dataset_id()
        for dataset_id in runtime.available_dataset_ids():
            self.assertIn(dataset_id, str(caught.exception), "the error lists no options")

    def test_a_single_configured_dataset_needs_no_choice(self):
        """One installed library is unambiguous; do not demand an extra step."""
        only_one = Path(self.directory.name) / "only-one"
        only_one.mkdir()
        (only_one / "solo-dataset.toml").write_text("", encoding="utf-8")
        original_dir = runtime.DATASET_CONFIG_DIR
        runtime.DATASET_CONFIG_DIR = only_one
        self.addCleanup(setattr, runtime, "DATASET_CONFIG_DIR", original_dir)
        self.assertEqual(runtime.default_dataset_id(), "solo-dataset")


class InstallerSkillStepTests(unittest.TestCase):
    """With several datasets and no default, the skill step of `install.py`
    must not crash.

    `skill_substitutions()` fills placeholders like {{DATASET_NAME}} and cannot
    avoid `active()`, which raises `DatasetNotChosen` — a `RuntimeError`
    subclass — when no default was chosen. `main()` catching only its own
    `Failed` means the "skipping the Skill" line never prints, and the user gets
    a raw traceback from deep in the import chain, directly after a line saying
    the default library is unset. The two contradict each other.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        settings = Path(self.directory.name) / ".docatlas-local.toml"
        original = runtime.LOCAL_SETTINGS
        runtime.LOCAL_SETTINGS = settings
        self.addCleanup(setattr, runtime, "LOCAL_SETTINGS", original)
        self.addCleanup(self.directory.cleanup)
        self.previous = os.environ.pop("DOCATLAS_DATASET", None)
        self.addCleanup(self.restore)
        two_datasets_installed(self)

    def restore(self):
        os.environ.pop("DOCATLAS_DATASET", None)
        if self.previous is not None:
            os.environ["DOCATLAS_DATASET"] = self.previous

    def load_installer(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "docatlas_install_under_test", runtime.REPO_ROOT / "install.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_as_a_fresh_process_would(self, callable_):
        """Run inside an empty `contextvars.Context`, forcing the modules
        involved to execute their top level again.

        Reproducing a genuinely fresh process with no default chosen means
        defeating two layers of caching:

        1. `runtime.active()` stores its result in a module-level `ContextVar`.
           Hundreds of cases call it directly or indirectly under the bootstrap
           environment, and once that ContextVar is set it never changes again —
           hence a brand-new empty `Context`, so it hits `except LookupError`.
        2. `cli.py` says `from .config import DATASET_ID` at its top level,
           which copies the value into an ordinary attribute of `cli.py` on
           **first** import and never again. Other cases import it, so both it
           and `docatlas.config` have to be dropped from `sys.modules`.

        Clear neither and this case passes alone but silently verifies nothing
        as part of the whole suite.
        """
        for name in ("docatlas.cli", "docatlas.config"):
            sys.modules.pop(name, None)
        self.addCleanup(lambda: (sys.modules.pop("docatlas.cli", None),
                                  sys.modules.pop("docatlas.config", None)))
        return contextvars.Context().run(callable_)

    def test_skill_step_reports_instead_of_crashing_with_no_default_chosen(self):
        self.assertGreater(
            len(runtime.available_dataset_ids()), 1,
            "more than one dataset configured is the real first-run state",
        )
        installer = self.load_installer()
        with self.assertRaises(runtime.DatasetNotChosen):
            self.run_as_a_fresh_process_would(installer.render_skill)

        # --skip-mcp: this checks only whether the skill step crashes, and must
        # not touch a real client registration on the way past.
        exit_code = self.run_as_a_fresh_process_would(
            lambda: installer.main(["--skip-mcp"])
        )
        self.assertEqual(exit_code, 0, "skipping the Skill must not fail the install")


class UninstallCodexEntryTests(unittest.TestCase):
    """Uninstalling edits a file this project does not own.

    `~/.codex/config.toml` holds the user's other MCP servers and their
    settings. Removing our section by rewriting the file, or by matching too
    greedily, silently deletes configuration nobody asked us to touch — and the
    damage only shows up the next time some other tool fails to start.
    """

    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "docatlas_install_uninstall_test", runtime.REPO_ROOT / "install.py"
        )
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)

    def test_only_our_section_goes_wherever_it_sits(self):
        neighbour = '[mcp_servers.other]\ncommand = "a"\n'
        ours = '[mcp_servers.docatlas]\ncommand = "py"\nargs = ["x"]\n'
        for name, text in {
            "alone": ours,
            "ours last": f"{neighbour}\n{ours}",
            "ours first": f"{ours}\n{neighbour}",
            "between others": f'[tools]\nweb = true\n\n{ours}\n{neighbour}',
        }.items():
            stripped, found = self.installer.strip_codex_entry(text)
            self.assertTrue(found, name)
            self.assertNotIn("docatlas", stripped, name)
            if neighbour in text:
                self.assertIn("[mcp_servers.other]", stripped, name)
                self.assertIn('command = "a"', stripped, name)
            if "[tools]" in text:
                self.assertIn("web = true", stripped, name)
            self.assertTrue(not stripped or stripped.endswith("\n"), name)

    def test_a_config_that_never_had_us_is_returned_byte_for_byte(self):
        text = '[mcp_servers.other]\ncommand = "a"\n\n[tools]\nweb = true\n'
        stripped, found = self.installer.strip_codex_entry(text)
        self.assertFalse(found)
        self.assertEqual(stripped, text)


class DoctorTests(unittest.TestCase):
    """`doctor` has to answer in the states where nothing else will."""

    def test_the_template_is_something_to_copy_not_something_to_crawl(self):
        """The shipped template describes an invented site.

        Telling a newcomer to crawl it sends them at a host that does not
        resolve, and the failure looks like DocAtlas being broken rather than
        like the template being a worked example.
        """
        report = doctor.inspect_dataset("EXAMPLE", is_default=True)
        self.assertEqual(report["state"], "template")
        self.assertNotIn("crawl", report["next"])
        self.assertIn("example.py", report["next"])

    def test_a_broken_config_is_reported_rather_than_raised(self):
        """A diagnostic that dies on the first bad entry cannot diagnose."""
        directory = Path(tempfile.mkdtemp(prefix="docatlas_broken_"))
        self.addCleanup(shutil.rmtree, directory, True)
        (directory / "wrecked.toml").write_text("id = \nnot toml at all", encoding="utf-8")
        original = runtime.DATASET_CONFIG_DIR
        runtime.DATASET_CONFIG_DIR = directory
        self.addCleanup(setattr, runtime, "DATASET_CONFIG_DIR", original)

        report = doctor.inspect_dataset("wrecked", is_default=False)
        self.assertEqual(report["state"], "broken config")
        self.assertIn("wrecked.toml", report["next"])

    def test_each_shell_is_given_a_line_it_can_actually_run(self):
        """`VAR=value command` is not a thing in PowerShell.

        Printing the POSIX spelling to a Windows user does not give them a hint
        to adapt; it gives them a command that errors.
        """
        posix = doctor._env_prefix("some-docs", windows=False)
        powershell = doctor._env_prefix("some-docs", windows=True)
        self.assertEqual(posix, "DOCATLAS_DATASET=some-docs ")
        self.assertTrue(powershell.startswith("$env:DOCATLAS_DATASET="))
        self.assertIn(";", powershell)


class ClientLocationTests(unittest.TestCase):
    """Both clients can be moved elsewhere by an environment variable.

    Getting this wrong is silent at both ends: the Skill is written into a
    directory the client never reads, the installer reports success, and the
    client goes on knowing nothing. Nothing raises, so only these cases stand
    between that and a user who thinks the install worked.
    """

    MOVEABLE = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")

    def setUp(self):
        self.saved = {key: os.environ.get(key) for key in self.MOVEABLE}
        for key in self.MOVEABLE:
            os.environ.pop(key, None)
        self.addCleanup(self.restore)

    def restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_unset_means_the_documented_default(self):
        self.assertEqual(clients.home("claude-code"), Path.home() / ".claude")
        self.assertEqual(clients.home("codex"), Path.home() / ".codex")
        self.assertIsNone(clients.override("claude-code"))

    def test_setting_the_variable_moves_the_skill_directory(self):
        for client, variable in (("claude-code", "CLAUDE_CONFIG_DIR"),
                                 ("codex", "CODEX_HOME")):
            os.environ[variable] = str(Path(tempfile.gettempdir()) / "moved")
            self.assertEqual(
                clients.skill_dir(client, "docatlas"),
                Path(tempfile.gettempdir()) / "moved" / "skills" / "docatlas",
                client,
            )
            self.assertEqual(clients.override(client), variable)
            del os.environ[variable]

    def test_claude_config_is_a_sibling_by_default_but_a_child_once_moved(self):
        """The asymmetry that makes "default path plus filename" wrong.

        Unset, Claude Code reads `~/.claude.json`, which sits *beside*
        `~/.claude` rather than inside it. Set, the file moves *into* the
        override directory. Verified by running the client itself with the
        variable set and seeing where it wrote.
        """
        self.assertEqual(clients.mcp_config("claude-code"), Path.home() / ".claude.json")
        moved = Path(tempfile.gettempdir()) / "moved-claude"
        os.environ["CLAUDE_CONFIG_DIR"] = str(moved)
        self.assertEqual(clients.mcp_config("claude-code"), moved / ".claude.json")

    def test_codex_config_follows_its_variable(self):
        self.assertEqual(clients.mcp_config("codex"), Path.home() / ".codex" / "config.toml")
        moved = Path(tempfile.gettempdir()) / "moved-codex"
        os.environ["CODEX_HOME"] = str(moved)
        self.assertEqual(clients.mcp_config("codex"), moved / "config.toml")


class SkillRenderingIsReproducibleTests(unittest.TestCase):
    """The rendered Skill must not depend on how Python was started."""

    def test_the_program_path_is_spelled_the_same_from_every_shell(self):
        """On Windows `str(Path)` follows the shell that launched Python.

        PowerShell yields `C:\\Users\\...`, Git Bash `C:/Users/...`, for the
        same directory. Rendering that into the Skill makes the document differ
        byte for byte depending on where the installer was run from, which both
        breaks idempotence and makes any comparison against a fresh rendering
        report a permanent, unfixable "out of date".
        """
        from docatlas import cli

        # A stand-in whose two spellings genuinely differ, so this fails on any
        # platform and under any shell. Reading the real root instead would go
        # green wherever the shell happens to hand back forward slashes, hiding
        # the bug everywhere except the one place it shows.
        class WindowsStylePath:
            def __str__(self):
                return r"C:\Users\someone\DocAtlas"

            def as_posix(self):
                return "C:/Users/someone/DocAtlas"

        original = cli.REPO_ROOT
        cli.REPO_ROOT = WindowsStylePath()
        self.addCleanup(setattr, cli, "REPO_ROOT", original)

        root = cli.skill_substitutions()["DOCATLAS_ROOT"]
        self.assertNotIn("\\", root, "the program path must not carry backslashes")
        self.assertEqual(root, "C:/Users/someone/DocAtlas")


class RuntimeWorkspaceTests(unittest.TestCase):
    """One process holds several datasets at once, which is how MCP serves
    several libraries over one connection."""

    def test_switching_workspace_switches_database_and_adapter(self):
        other = dataclasses.replace(
            runtime.active(),
            dataset=dataclasses.replace(
                runtime.active().dataset, id="other-lib", name="Another Library"
            ),
            data_dir=Path(tempfile.mkdtemp(prefix="docatlas_ws_")),
        )
        default_db = runtime.active().db_path
        with runtime.use(other) as switched:
            self.assertEqual(switched.id, "other-lib")
            self.assertNotEqual(switched.db_path, default_db)
            # Derived configuration switches too, or a swapped library keeps the
            # previous one's categories and labels.
            self.assertEqual(db.connect_db.__module__, "docatlas.db")
            self.assertEqual(runtime.active().name, "Another Library")
        self.assertEqual(runtime.active().db_path, default_db)

    def test_workspace_is_restored_even_when_the_body_raises(self):
        before = runtime.active().id
        with self.assertRaises(ZeroDivisionError):
            with runtime.use(dataclasses.replace(runtime.active())):
                raise ZeroDivisionError
        self.assertEqual(runtime.active().id, before)

    def test_worker_threads_inherit_the_active_dataset(self):
        """Fetching runs in a thread pool, and without passing the workspace
        through explicitly the wrong adapter gets used.

        `contextvars` do not cross threads: a worker starts with an empty
        context and `active()` falls back to the process default. The result is
        one site's adapter parsing another site's pages into that other
        library, without an error anywhere. So pool submissions go through
        `bind(fn)`.
        """
        import concurrent.futures

        with using(dataset={"id": "in-thread"}):
            bound = runtime.bind(lambda: runtime.active().id)
            naked = lambda: runtime.active().id  # noqa: E731
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                self.assertEqual(executor.submit(bound).result(), "in-thread")
                # Proof this case can catch it: without bind the worker falls
                # back to the default dataset.
                self.assertEqual(
                    executor.submit(naked).result(), runtime.default_dataset_id()
                )

    def test_every_thread_pool_submits_a_bound_callable(self):
        """A new thread pool without bind repeats the same fault, so a test
        holds the line."""
        offenders = []
        for path in Path("docatlas").glob("*.py"):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "executor.submit(" in line or "executor.map(" in line:
                    if "bind(" not in line:
                        offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "pool work must be wrapped in runtime.bind()")


class HeadingAnchorTests(unittest.TestCase):
    """A link target inside a heading is for the browser, not part of the
    heading text.

    Left in, the anchor builds the whole URL into the fragment and produces an
    address the official page does not have. The body still reads fine, but a
    citation following it lands nowhere near that section.
    """

    def test_link_target_never_leaks_into_the_anchor(self):
        anchor = text.heading_anchor(
            "[Constrained algorithms](https://example.invalid/docs/reference/ranges)"
            " (since C++20)"
        )
        self.assertNotIn("http", anchor)
        self.assertNotIn("example.invalid", anchor)
        self.assertEqual(anchor, "constrainedalgorithmssincec20")

    def test_inline_code_and_plain_headings_still_work(self):
        self.assertEqual(text.heading_anchor("`TArray` Members"), "tarraymembers")
        self.assertEqual(text.heading_anchor("Inputs"), "inputs")

    def test_a_heading_with_no_visible_text_falls_back(self):
        self.assertEqual(text.heading_anchor("[](https://example.invalid/x)"), "content")

    def test_repeated_headings_stay_distinguishable(self):
        sections = chunking.split_sections(
            title="Page",
            description="",
            markdown="## Inputs\n\na\n\n## Inputs\n\nb\n",
            source_url="https://example.invalid/p",
            category="guides",
        )
        anchors = [section["source_anchor"] for section in sections]
        self.assertEqual(len(anchors), len(set(anchors)))


class QualifierAndAliasTests(unittest.TestCase):
    """People copy the official spelling with its namespace, while the page
    address carries only the last segment."""

    def test_namespace_is_stripped_to_the_last_segment(self):
        self.assertEqual(text.qualifier_tail("std::from_chars"), "from_chars")
        self.assertEqual(text.qualifier_tail("math.floor"), "floor")

    def test_unqualified_or_too_short_names_add_nothing(self):
        self.assertEqual(text.qualifier_tail("Streaming"), "")
        self.assertEqual(text.qualifier_tail("std::x"), "")

    def test_query_names_start_with_the_query_itself(self):
        names = search.query_names("std::from_chars")
        self.assertEqual(names[0], chunking.normalize_name("std::from_chars"))
        self.assertIn("fromchars", names)

    def test_query_names_are_deduplicated(self):
        names = search.query_names("Streaming")
        self.assertEqual(len(names), len(set(names)))


class PageSlugTests(unittest.TestCase):
    """Static sites write implementation detail into the address (`topic.html`)
    while people say the page name."""

    def test_document_extension_is_not_part_of_the_name(self):
        self.assertEqual(db.page_slug("/modeling/geometry_nodes/fields.html"), "fields")
        self.assertEqual(db.page_slug("/a/b/index.php"), "index")

    def test_dots_inside_real_names_are_kept(self):
        # A dot inside `Type.Method` or `v5.8` is not an extension; cutting at
        # it produces the wrong name.
        self.assertEqual(db.page_slug("/API/Node.Tick"), "nodetick")
        self.assertEqual(db.page_slug("/notes/release-5.8"), "release58")

    def test_slug_rules_are_versioned_so_old_rows_get_recomputed(self):
        # Without recomputing, new rules apply only to pages found later and
        # one library holds two kinds of slug.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        connection = connect_db(Path(directory.name) / "t.sqlite3")
        initialize_db(connection)
        connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, route_depth) "
            "VALUES('https://example.invalid/x/fields.html',"
            " '/x/fields.html', 'guides', 'https://example.invalid/s.xml', 2)"
        )
        connection.execute("UPDATE pages SET normalized_slug='fieldshtml'")
        connection.execute("DELETE FROM metadata WHERE key='slug_version'")
        connection.commit()
        db.backfill_page_slugs(connection)
        self.assertEqual(
            connection.execute("SELECT normalized_slug FROM pages").fetchone()[0],
            "fields",
        )
        connection.close()


class MetadataAndTagRenameTests(unittest.TestCase):
    """Renaming a column touches only `pages`. `metadata` and `tags` are
    key-value tables where RENAME COLUMN does not apply, so they migrate
    separately or the old key and tag_type linger in an older database."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        self.addCleanup(self.connection.close)
        initialize_db(self.connection)

    def _real_chunk_id(self):
        # chunk_tags has a foreign key on chunk_id, so it needs a real chunk.
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        cursor = self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, doc_version,"
            " locale, route_depth, discovered_at, last_seen_at) VALUES("
            "'https://example.invalid/x', '/x', 'guides',"
            " 'https://example.invalid/s.xml', '5.8', 'en-US', 1,"
            " '2026-01-01', '2026-01-01')"
        )
        row = FakeRow(
            id=cursor.lastrowid,
            path="/x",
            url="https://example.invalid/x",
            category="guides",
        )
        store.store_document_result(
            self.connection,
            transform_document(row, make_document("X", [text_block("body text")])),
            "guides",
        )
        self.connection.commit()
        return self.connection.execute("SELECT id FROM chunks").fetchone()[0]

    def test_metadata_key_renamed_when_only_old_key_present(self):
        self.connection.execute("DELETE FROM metadata WHERE key='doc_version'")
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES('ue_version', '5.8')"
        )
        self.connection.commit()
        db.migrate_metadata_key(self.connection, "ue_version", "doc_version")
        self.connection.commit()
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM metadata WHERE key='ue_version'"
            ).fetchone()
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key='doc_version'"
            ).fetchone()[0],
            "5.8",
        )

    def test_stale_old_key_dropped_when_new_key_already_written(self):
        # An older database ran the old code, writing the old key, then the new
        # code, writing the new one. Both rows exist and the old one is dead.
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES('ue_version', '5.8')"
        )
        self.connection.commit()
        db.migrate_metadata_key(self.connection, "ue_version", "doc_version")
        self.connection.commit()
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM metadata WHERE key='ue_version'"
            ).fetchone()
        )

    def test_sitemap_index_key_is_migrated_to_the_generic_name(self):
        # Only sitemap-style sources have one entry point, and the key name
        # should not write that assumption into the data.
        self.connection.execute("DELETE FROM metadata WHERE key='inventory_index'")
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES('sitemap_index', 'https://x/s.xml')"
        )
        self.connection.commit()
        db.migrate_metadata_key(self.connection, "sitemap_index", "inventory_index")
        self.connection.commit()
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM metadata WHERE key='sitemap_index'"
            ).fetchone()
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key='inventory_index'"
            ).fetchone()[0],
            "https://x/s.xml",
        )

    def test_tag_type_renamed_when_only_old_type_present(self):
        self.connection.execute("DELETE FROM tags WHERE tag_type='doc_version'")
        self.connection.commit()
        self.connection.execute(
            "INSERT INTO tags(name, tag_type) VALUES('5.8', 'ue_version')"
        )
        self.connection.commit()
        db.migrate_tag_type(self.connection, "ue_version", "doc_version")
        self.connection.commit()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE tag_type='ue_version'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE tag_type='doc_version' AND name='5.8'"
            ).fetchone()[0],
            1,
        )

    def test_chunk_tags_repointed_when_both_tag_types_collide(self):
        # A real chunk is already tagged (VERSION, 'doc_version') when stored;
        # adding the old tag_type by hand under the same name creates the
        # UNIQUE(name, tag_type) collision.
        chunk_id = self._real_chunk_id()
        new_id = self.connection.execute(
            "SELECT id FROM tags WHERE name=? AND tag_type='doc_version'",
            (config.DATASET.version,),
        ).fetchone()[0]
        old_id = self.connection.execute(
            "INSERT INTO tags(name, tag_type) VALUES(?, 'ue_version')",
            (config.DATASET.version,),
        ).lastrowid
        self.connection.execute(
            "INSERT INTO chunk_tags(chunk_id, tag_id) VALUES(?, ?)",
            (chunk_id, old_id),
        )
        self.connection.commit()
        db.migrate_tag_type(self.connection, "ue_version", "doc_version")
        self.connection.commit()
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM tags WHERE id=?", (old_id,)
            ).fetchone()
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM chunk_tags WHERE chunk_id=? AND tag_id=?",
                (chunk_id, new_id),
            ).fetchone()[0],
            1,
        )


class InventoryCandidateTests(unittest.TestCase):
    """The page is in the inventory and a slightly different spelling makes it
    unfindable. That path has to work."""

    PAGES = [
        ("/docs/reference/nodes/Camera/SetFieldOfView", "reference"),
        ("/modeling/geometry_nodes/fields.html", "guides"),
        ("/render/shader_nodes/textures/wave.html", "guides"),
        ("/docs/reference/utility/from_chars", "reference"),
        ("/docs/guides/streaming-geometry", "guides"),
        ("/docs/reference/chrono/duration_cast", "reference"),
        ("/docs/guides/render-target-guide", "guides"),
        ("/docs/reference/nodes/Camera/SetAspectRatio", "reference"),
    ]

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        for path, category in self.PAGES:
            self.connection.execute(
                "INSERT INTO pages(url, path, category, sitemap_url, route_depth) "
                "VALUES(?, ?, ?, 'https://example.invalid/s.xml', 3)",
                (f"https://example.invalid{path}", path, category),
            )
        self.connection.commit()
        initialize_db(self.connection)  # triggers the normalized_slug backfill

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def find(self, query, **kwargs):
        return ondemand.find_uncrawled_candidates(
            self.connection, query, limit=5, **kwargs
        )

    def test_official_page_name_finds_the_html_page(self):
        # Nobody should have to know which file extension a site uses.
        rows = self.find("Fields")
        self.assertEqual([row["path"] for row in rows], ["/modeling/geometry_nodes/fields.html"])

    def test_qualified_cpp_symbol_finds_the_unqualified_page(self):
        rows = self.find("std::from_chars")
        self.assertEqual([row["path"] for row in rows], ["/docs/reference/utility/from_chars"])

    def test_full_official_title_is_covered_by_the_path(self):
        rows = self.find("Wave Texture Node")
        self.assertEqual(
            [row["path"] for row in rows], ["/render/shader_nodes/textures/wave.html"]
        )

    def test_concept_questions_do_not_scoop_up_pages(self):
        # The coverage stage needs every content word, so a general question
        # does not set off a pile of fetches.
        self.assertEqual(self.find("how do I make an object glow"), [])

    def test_exact_only_still_means_exact(self):
        self.assertEqual(self.find("Wave Texture Node", exact_only=True), [])
        self.assertTrue(self.find("Fields", exact_only=True))

    def test_lookup_separates_not_crawled_from_not_existing(self):
        known = ondemand.inventory_lookup(self.connection, "Set Field Of View")
        self.assertTrue(known["pending_pages"])
        self.assertEqual(known["pending_pages"][0]["matched_by"], "exact_slug")
        unknown = ondemand.inventory_lookup(self.connection, "zzzznotarealpage")
        self.assertEqual(unknown["pending_pages"], [])
        self.assertEqual(unknown["crawled_pages"], [])

    def test_a_page_the_site_redirected_away_says_so_instead_of_rephrase(self):
        """The site withdrew this page and redirects elsewhere, so "reword and
        retry" can never work.

        A real library holds dozens of these: an old address redirects to a
        documentation home page and fetches back as a shell with no body. Naming
        that page necessarily gives an empty answer, and an empty answer plus
        "reword it" keeps someone pushing in the wrong direction.
        """
        self.connection.execute(
            "UPDATE pages SET status='redirect',"
            " redirect_url='https://example.invalid/moved-here'"
            " WHERE normalized_slug='setfieldofview'"
        )
        self.connection.commit()
        lookup = ondemand.inventory_lookup(self.connection, "Set Field Of View")
        steps = "\n".join(context.describe_lookup(lookup))
        self.assertIn("https://example.invalid/moved-here", steps)
        self.assertIn("redirected by the site", steps)
        self.assertNotIn("Reword and retry", steps)

    def test_a_dead_address_points_at_the_live_page_of_the_same_name(self):
        """The real shape: the old address is withdrawn and redirects to a home
        page, while the page it moved to is already in the library.

        Following the redirect only yields the home page. So the redirect is
        reported as it stands rather than followed, and the live page of the
        same name is offered for the user to confirm.
        """
        self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, status, route_depth)"
            " VALUES('https://example.invalid/new/SetFieldOfView',"
            " '/new/SetFieldOfView', 'blueprint_api',"
            " 'https://example.invalid/s.xml', 'success', 3)"
        )
        self.connection.execute(
            "UPDATE pages SET status='redirect', redirect_url='https://example.invalid/home'"
            " WHERE path LIKE '%/nodes/Camera/SetFieldOfView'"
        )
        self.connection.commit()
        initialize_db(self.connection)  # backfill the new page's normalized_slug
        steps = "\n".join(
            context.describe_lookup(
                ondemand.inventory_lookup(self.connection, "Set Field Of View")
            )
        )
        self.assertIn("https://example.invalid/home", steps)
        self.assertIn("/new/SetFieldOfView", steps)
        self.assertIn("another live page of the same name", steps)

    def test_lookup_reports_pages_that_are_already_local(self):
        self.connection.execute(
            "UPDATE pages SET status='success' WHERE normalized_slug='setfieldofview'"
        )
        lookup = ondemand.inventory_lookup(self.connection, "Set Field Of View")
        self.assertEqual(lookup["pending_pages"], [])
        self.assertTrue(lookup["crawled_pages"])

    def test_describe_lookup_gives_a_different_answer_for_each_state(self):
        # Three kinds of "nothing" need three next steps, or the caller guesses.
        pending = context.describe_lookup(
            ondemand.inventory_lookup(self.connection, "Set Field Of View")
        )
        missing = context.describe_lookup(
            ondemand.inventory_lookup(self.connection, "zzzznotarealpage")
        )
        self.assertIn("get", "\n".join(pending))
        self.assertNotEqual(pending, missing)
        self.assertIn("no inventory page in this dataset matches", "\n".join(missing))


    def test_a_symbol_inside_a_longer_question_still_locates_its_page(self):
        """The whole query matches nothing, but one symbol in it is exactly a
        page name.

        A normalised `durationcast` compared against `duration_cast` in the raw
        path never matches while the underscore is there; and the other word is
        not in that path at all, so the "every content word must hit" stage does
        not fire either.
        """
        rows = self.find("duration_cast milliseconds")
        self.assertEqual(
            [row["path"] for row in rows], ["/docs/reference/chrono/duration_cast"]
        )
        self.assertEqual(rows[0]["match_stage"], "token_exact_slug")

    def test_separators_in_the_path_do_not_block_coverage(self):
        # Both content words are in the path, split by hyphens. Only after
        # flattening do they line up.
        rows = self.find("render target guide")
        self.assertIn(
            "/docs/guides/render-target-guide",
            [row["path"] for row in rows],
        )

    def test_a_plain_word_alone_never_triggers_a_fetch(self):
        """Control case: only symbol-shaped words may locate on their own.

        A plain English noun taken to the slugs sweeps back a whole region,
        whereas a compound identifier can realistically be only that one page.
        """
        self.assertEqual(ondemand.identifier_tokens("milliseconds"), [])
        self.assertEqual(ondemand.identifier_tokens("how do I make an object glow"), [])
        self.assertEqual(
            ondemand.identifier_tokens("duration_cast milliseconds"), ["durationcast"]
        )

    def test_weak_candidates_are_reported_but_never_fetched(self):
        """"Not confident enough to fetch" and "there is no such page" are
        reported apart."""
        query = "camera aspect ratio settings"
        lookup = ondemand.inventory_lookup(self.connection, query)
        # Not fetched: no page is certainly the one.
        self.assertEqual(lookup["pending_pages"], [])
        # But not "the site does not have it" either: the inventory holds one
        # that is a single word short of matching.
        self.assertEqual(
            [item["path"] for item in lookup["weak_candidates"]],
            ["/docs/reference/nodes/Camera/SetAspectRatio"],
        )
        steps = "\n".join(context.describe_lookup(lookup))
        self.assertIn("Not confident which page", steps)
        self.assertNotIn("the site does not have", steps)
        # Reporting is reporting: not one extra page gets fetched.
        self.assertEqual(self.find(query), [])

    def test_a_sentence_full_of_function_words_gets_no_candidates(self):
        """More than half function words means a sentence, not a page name.

        In "how do I make an object glow" the remaining make + object hit a pile
        of unrelated pages. Offering them only misleads; saying nothing was
        found is more honest than passing weak matches off as a full answer.
        """
        for sentence in (
            "how do I make an object glow",
            "what is the best way to do lighting in my game",
        ):
            self.assertEqual(
                ondemand.weak_candidates(self.connection, sentence), [], sentence
            )
            self.assertEqual(self.find(sentence), [], sentence)

    def test_a_truly_absent_name_never_speaks_for_the_official_site(self):
        """Found nothing means "not in this dataset", never "not in the docs".

        All DocAtlas can see is its own inventory, whose scope is the
        directories the dataset declares. A library that declares two of a
        site's directories cannot find a page in a third — and answering "the
        official documentation does not have this page" about a page that is
        live on the site is not a matter of phrasing: it sends the user off to
        reword the query when what has to change is the collected scope, and no
        wording can ever produce a result.
        """
        lookup = ondemand.inventory_lookup(self.connection, "zzzznotarealpage")
        self.assertEqual(lookup["weak_candidates"], [])
        steps = "\n".join(context.describe_lookup(lookup))
        self.assertNotIn("the site does not have", steps)
        self.assertIn("no inventory page in this dataset matches", steps)
        # Say where the "nothing" ends: this dataset collects these categories.
        self.assertIn("coverage", steps)


class QualifiedTargetTests(unittest.TestCase):
    """The user was as precise as possible — an exact address, a fully
    qualified name — and still did not get the page.

    The common thread is that more precise input became less useful: the whole
    address was treated as ordinary text, and among four pages of the same name
    the shallowest path won. Neither is a ranking preference; both are
    deterministic locating that ignored information plainly present in the
    query.
    """

    PAGES = [
        "/docs/guides/coroutines",
        "/docs/reference/algorithm/sort",
        "/docs/reference/algorithm/ranges/sort",
        "/docs/reference/container/list/sort",
    ]

    def setUp(self):
        self.connection = temp_db(self)
        for path in self.PAGES:
            self.connection.execute(
                "INSERT INTO pages(url, path, category, status, route_depth)"
                " VALUES(?, ?, 'reference', 'pending', ?)",
                (f"https://example.invalid{path}", path, path.count("/")),
            )
        self.connection.commit()
        initialize_db(self.connection)  # backfill normalized_slug

    def find(self, query, **kwargs):
        return ondemand.find_uncrawled_candidates(
            self.connection, query, limit=5, **kwargs
        )

    def test_an_official_url_locates_exactly_that_page(self):
        """Pasting an address is the strongest clue there is, and used to be the
        most useless input.

        The whole URL was normalised as ordinary text into one long run of
        letters that matched no slug at all.
        """
        rows = self.find("https://example.invalid/docs/guides/coroutines")
        self.assertEqual([row["path"] for row in rows], ["/docs/guides/coroutines"])
        self.assertEqual(rows[0]["match_stage"], "exact_url")

    def test_a_url_wrapped_in_a_sentence_still_counts(self):
        rows = self.find(
            "have a look at https://example.invalid/docs/reference/algorithm"
            "/ranges/sort and tell me what it says"
        )
        self.assertEqual(
            [row["path"] for row in rows], ["/docs/reference/algorithm/ranges/sort"]
        )

    def test_a_path_printed_in_next_steps_can_be_pasted_straight_back(self):
        """`related` prints paths in its own next step, so that command has to
        actually run.

            python -m docatlas get "/docs/reference/algorithm/ranges/sort"

        Name matching sees only the last segment, so the next step the system
        suggested fails: follow it and the answer is that the inventory has no
        matching page either.
        """
        rows = self.find("/docs/reference/algorithm/ranges/sort")
        self.assertEqual(
            [row["path"] for row in rows], ["/docs/reference/algorithm/ranges/sort"]
        )
        self.assertEqual(rows[0]["match_stage"], "exact_url")

    def test_a_path_that_is_not_in_the_inventory_matches_nothing(self):
        self.assertEqual(self.find("/docs/nothing/here/at/all"), [])

    def test_a_path_variant_is_canonicalised_by_the_adapter(self):
        """Paths have variant spellings too, and recognising them is site
        knowledge rather than something the core should guess.

        Here it is a locale segment: the same document with or without `/en/`
        has to land on one page, or it is stored twice. Other sites use a wiki
        prefix or the like — different shape, same point.
        """
        rows = self.find("/docs/en/reference/algorithm/ranges/sort")
        self.assertEqual(
            [row["path"] for row in rows], ["/docs/reference/algorithm/ranges/sort"]
        )

    def test_a_url_from_another_site_is_not_a_page_of_this_dataset(self):
        """Whether an address belongs to this dataset is the source adapter's
        answer; the core knows no sites."""
        self.assertEqual(
            ondemand.target_paths(
                "https://elsewhere.invalid/docs/reference/algorithm/sort"
            ),
            [],
        )
        self.assertEqual(self.find("https://elsewhere.invalid/nothing/here"), [])

    def test_namespace_in_the_query_picks_the_right_page_of_that_name(self):
        """`a::ranges::sort` and `a::sort` are two pages, and the qualifier is
        the segment that tells them apart.

        Using the qualifier only to strip out the final `sort` throws the rest
        away, so among four pages of that name the shallowest path won.
        """
        rows = self.find("std::ranges::sort")
        self.assertEqual(rows[0]["path"], "/docs/reference/algorithm/ranges/sort")

    def test_the_unqualified_symbol_still_gets_the_top_level_page(self):
        """Control case: without that middle segment the original order must
        not be disturbed."""
        rows = self.find("std::sort")
        self.assertEqual(rows[0]["path"], "/docs/reference/algorithm/sort")

    def test_qualifier_segments_are_only_taken_from_qualified_tokens(self):
        self.assertEqual(text.qualifier_segments("std::ranges::sort"), ["std", "ranges"])
        self.assertEqual(text.qualifier_segments("sort"), [])
        self.assertEqual(text.qualifier_segments("how do I sort a list"), [])


class UrlFragmentTests(unittest.TestCase):
    """A `#section` on a URL is the finest identification a user can give and
    must not be dropped.

    The layer below page-level scoping: `…/some-page#screen-insets` used only
    `/some-page` and threw the fragment away, so the answer fell back to a page
    overview while the body of the wanted section sat in the local library.

    The stored anchor is flattened (`screeninsets`) while the official href is
    hyphenated (`screen-insets`) — the same normalisation on both sides is what
    makes them meet.
    """

    PAGE = "/docs/guides/on-screen-containers"
    ROOT = "On-screen UI containers"
    PROPS = f"{ROOT} > Container properties"
    INSETS = f"{PROPS} > Screen insets"

    # Built to the shape of a real library: the last chunk has an anchor of its
    # own yet sits under the previous heading in heading_path. Matching anchors
    # exactly and nothing else misses it.
    CHUNKS = [
        ("overview", ROOT, "", "On-screen UI containers hold interface elements."),
        ("parameters", PROPS, "containerproperties", "Container properties table."),
        ("details", INSETS, "screeninsets",
         "ScreenInsets controls how a container is inset from the screen edges."),
        ("details", INSETS, "coreuisafeinsets",
         "CoreUISafeInsets keeps interface clear of the CoreUI top bar."),
        ("details", PROPS, "displayorder", "DisplayOrder decides which container draws on top."),
    ]

    def setUp(self):
        self.connection = temp_db(self)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status)"
            " VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        page_url = f"https://example.invalid{self.PAGE}"
        self.page_id = self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, status, title, route_depth)"
            " VALUES(?, ?, 'guides', 'https://example.invalid/s.xml', 'success', ?, 3)",
            (page_url, self.PAGE, self.ROOT),
        ).lastrowid
        now = "2026-07-27T00:00:00Z"
        self.by_anchor: dict[str, int] = {}
        for index, (kind, heading_path, anchor, body) in enumerate(self.CHUNKS):
            section_id = self.connection.execute(
                "INSERT INTO sections(page_id, position, heading_level, heading_path,"
                " title, content_md, content_text, source_url, token_estimate)"
                " VALUES(?, ?, 2, ?, ?, ?, ?, '', 10)",
                (self.page_id, index, heading_path, heading_path.split(" > ")[-1],
                 body, body),
            ).lastrowid
            anchored = f"{page_url}#{anchor}" if anchor else page_url
            chunk_id = self.connection.execute(
                "INSERT INTO chunks(section_id, page_id, chunk_index, chunk_count,"
                " knowledge_type, title, heading_path, context_prefix, content_md,"
                " content_text, source_url, source_anchor, token_estimate,"
                " content_hash, quality_score, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 20, ?, 1.0, ?, ?)",
                (section_id, self.page_id, index, len(self.CHUNKS), kind,
                 heading_path.split(" > ")[-1], heading_path, body, body,
                 page_url, anchored, f"hash{index}", now, now),
            ).lastrowid
            self.by_anchor[anchor or "(root)"] = chunk_id
        self.connection.commit()

    def ask(self, query, **kwargs):
        return context.build_context_pack(
            self.connection, query, token_budget=3000, category=None, **kwargs
        )

    def url(self, fragment=""):
        # Ask the dataset for the canonical address; no hostname is written in,
        # so this holds on any dataset.
        base = config.SOURCE.canonical_url(config.DATASET, self.PAGE)
        return f"{base}#{fragment}" if fragment else base

    def test_the_official_dash_form_maps_to_the_local_anchor(self):
        """The official href is `#screen-insets`; the library stores
        `screeninsets`.

        Both sides normalise to lowercase letters and digits only, so the bridge
        is generic and needs no site knowledge.
        """
        self.assertEqual(ondemand.target_fragment(self.url("screen-insets")), "screeninsets")
        self.assertEqual(ondemand.target_fragment(self.url("ScreenInsets")), "screeninsets")
        self.assertEqual(ondemand.target_fragment(self.url()), "")

    def test_a_url_fragment_selects_that_section(self):
        pack = self.ask(self.url("screen-insets"))
        ids = [item["id"] for item in pack["primary_knowledge"]]
        self.assertTrue(ids, "the section body is in the library; this cannot be empty")
        self.assertEqual(
            ids[0], self.by_anchor["screeninsets"], "first must be the section the fragment names"
        )
        self.assertNotIn(
            self.by_anchor["(root)"], ids, "falling back to the page overview is the failure"
        )

    def test_a_subsection_under_it_comes_along(self):
        """A subsection carries its own anchor yet belongs under the named one.

        Exact anchor matching alone misses it, and it is precisely what the user
        opened that section to read. The anchor identifies which section;
        `heading_path` decides where that section ends.
        """
        pack = self.ask(self.url("screen-insets"))
        ids = set(item["id"] for item in pack["primary_knowledge"])
        self.assertEqual(
            ids,
            {self.by_anchor["screeninsets"], self.by_anchor["coreuisafeinsets"]},
        )

    def test_a_fragment_that_matches_nothing_says_so(self):
        """An unrecognised fragment says so instead of quietly falling back.

        Falling back in silence shows the user a plausible-looking answer with
        no sign that the section they named was never used.
        """
        pack = self.ask(self.url("no-such-section"))
        intent = pack["fragment_intent"]
        self.assertEqual(intent["fragment"], "nosuchsection")
        self.assertFalse(intent["matched"])
        # The section is unrecognised but the page is right: the page-level
        # contract still holds.
        self.assertTrue(pack["primary_knowledge"])
        self.assertEqual(
            {item["page_id"] for item in pack["primary_knowledge"]}, {self.page_id}
        )

    def test_a_matched_fragment_is_reported_back(self):
        intent = self.ask(self.url("screen-insets"))["fragment_intent"]
        self.assertTrue(intent["matched"])
        self.assertEqual(intent["section"], "Screen insets")

    def test_a_url_without_a_fragment_keeps_the_page_level_contract(self):
        """Control case: the page-level behaviour is unchanged."""
        pack = self.ask(self.url())
        self.assertNotIn("fragment_intent", pack)
        self.assertEqual(
            {item["page_id"] for item in pack["primary_knowledge"]}, {self.page_id}
        )


class ParentHeadingAnchorTests(unittest.TestCase):
    """A parent heading with no body of its own still has a usable anchor.

    Splitting keeps only sections that have a body, so a heading that merely
    leads a group of subheadings leaves no record at all. Real libraries are
    full of pages shaped like this:

        ## New library features     ← subheadings follow, not a word of its own
        ### New headers
        ### Library features

    The official page does have a `#New_library_features` anchor, yet pasting
    it is answered with "this page has no such section" and the whole page.

    It was never really lost: the subsections carry it verbatim in
    `heading_path`, so flattening that path by the same rule the anchors use
    recovers it — without inventing a bodyless record for an empty heading.
    """

    # The path follows the test dataset; the heading levels copy the shape of a
    # real page whose parent heading only leads subheadings. That structure is
    # what is under test, and it is site-independent.
    PAGE = "/docs/guides/release-2-0"
    ROOT = "Release 2.0"
    LANG = f"{ROOT} > New language features"
    # This level has no section of its own; it exists only as a segment of the
    # subsections' heading_path.
    LIB = f"{ROOT} > New library features"

    CHUNKS = [
        ("summary", ROOT, "", "Release 2.0 is a major version after 1.0."),
        ("details", LANG, "newlanguagefeatures", "Concepts, modules, coroutines."),
        ("details", f"{LIB} > New headers", "newheaders", "Three headers were added."),
        ("details", f"{LIB} > Library features", "libraryfeatures", "Formatting library."),
        ("details", f"{ROOT} > Defect reports", "defectreports", "Applied retroactively."),
    ]

    def setUp(self):
        self.connection = temp_db(self)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status)"
            " VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        page_url = f"https://example.invalid{self.PAGE}"
        self.page_id = self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, status, title, route_depth)"
            " VALUES(?, ?, 'guides', 'https://example.invalid/s.xml', 'success', ?, 3)",
            (page_url, self.PAGE, self.ROOT),
        ).lastrowid
        now = "2026-07-28T00:00:00Z"
        self.by_anchor: dict[str, int] = {}
        for index, (kind, heading_path, anchor, body) in enumerate(self.CHUNKS):
            section_id = self.connection.execute(
                "INSERT INTO sections(page_id, position, heading_level, heading_path,"
                " title, content_md, content_text, source_url, token_estimate)"
                " VALUES(?, ?, 2, ?, ?, ?, ?, '', 10)",
                (self.page_id, index, heading_path, heading_path.split(" > ")[-1],
                 body, body),
            ).lastrowid
            anchored = f"{page_url}#{anchor}" if anchor else page_url
            chunk_id = self.connection.execute(
                "INSERT INTO chunks(section_id, page_id, chunk_index, chunk_count,"
                " knowledge_type, title, heading_path, context_prefix, content_md,"
                " content_text, source_url, source_anchor, token_estimate,"
                " content_hash, quality_score, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 20, ?, 1.0, ?, ?)",
                (section_id, self.page_id, index, len(self.CHUNKS), kind,
                 heading_path.split(" > ")[-1], heading_path, body, body,
                 page_url, anchored, f"hash{index}", now, now),
            ).lastrowid
            self.by_anchor[anchor or "(root)"] = chunk_id
        self.connection.commit()

    def ask(self, fragment):
        url = f"{config.SOURCE.canonical_url(config.DATASET, self.PAGE)}#{fragment}"
        return context.build_context_pack(
            self.connection, url, token_budget=3000, category=None
        )

    def test_a_body_less_parent_heading_still_resolves(self):
        pack = self.ask("New_library_features")
        intent = pack["fragment_intent"]
        self.assertTrue(intent["matched"], "the official page has this anchor")
        self.assertEqual(intent["section"], "New library features")

    def test_it_selects_exactly_that_branch(self):
        """What comes back is that branch, not the whole page."""
        ids = {item["id"] for item in self.ask("New_library_features")["primary_knowledge"]}
        self.assertEqual(
            ids, {self.by_anchor["newheaders"], self.by_anchor["libraryfeatures"]}
        )

    def test_a_heading_with_its_own_body_still_matches_by_anchor(self):
        """Control case: a section with its own anchor takes the original path,
        unchanged."""
        pack = self.ask("New_language_features")
        self.assertEqual(pack["fragment_intent"]["section"], "New language features")
        self.assertEqual(
            {item["id"] for item in pack["primary_knowledge"]},
            {self.by_anchor["newlanguagefeatures"]},
        )

    def test_a_fragment_matching_no_heading_at_all_still_says_so(self):
        """Control case: an unrecognisable fragment is still owned up to; the
        new fallback must not paper over it."""
        intent = self.ask("no-such-section")["fragment_intent"]
        self.assertFalse(intent["matched"])
        self.assertIsNone(intent["section"])


class SampleQuotaTests(unittest.TestCase):
    """`--sample-per-category N` caps each category; it is not a global quota.

    When a category has fewer than N pages, moving the shortfall to the others
    pushes them past N, and the sample stops being a sample.
    """

    def _connection(self, sizes):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        connection = connect_db(Path(directory.name) / "t.sqlite3")
        initialize_db(connection)
        connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        for category, count in sizes.items():
            for index in range(count):
                connection.execute(
                    "INSERT INTO pages(url, path, category, sitemap_url, route_depth)"
                    " VALUES(?, ?, ?, 'https://example.invalid/s.xml', 2)",
                    (
                        f"https://example.invalid/{category}/{index}",
                        f"/{category}/{index}",
                        category,
                    ),
                )
        connection.commit()
        self.addCleanup(connection.close)
        return connection

    def test_short_category_does_not_inflate_the_others(self):
        connection = self._connection({"reference": 9, "guides": 100})
        quota = crawl.sample_quota(connection, 20)
        self.assertEqual(quota, {"guides": 20, "reference": 9})
        rows = crawl.select_page_batch(
            connection, batch_size=999, refresh=False, sample_per_category=20
        )
        counts = collections.Counter(row["category"] for row in rows)
        self.assertEqual(dict(counts), {"guides": 20, "reference": 9})

    def test_rerunning_does_not_keep_growing_a_finished_category(self):
        connection = self._connection({"guides": 100})
        connection.execute(
            "UPDATE pages SET status='success' WHERE id IN"
            " (SELECT id FROM pages ORDER BY id LIMIT 20)"
        )
        connection.commit()
        self.assertEqual(crawl.sample_quota(connection, 20), {})
        self.assertEqual(
            crawl.select_page_batch(
                connection, batch_size=999, refresh=False, sample_per_category=20
            ),
            [],
        )

    def test_a_single_category_run_ignores_the_others(self):
        connection = self._connection({"guides": 100, "reference": 100})
        self.assertEqual(
            crawl.sample_quota(connection, 5, category="reference"), {"reference": 5}
        )


class InventoryValidationTests(unittest.TestCase):
    """An empty library reporting pass with exit code 0 is the most dangerous
    kind of green there is."""

    def _connection(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        connection = connect_db(Path(directory.name) / "t.sqlite3")
        initialize_db(connection)
        self.addCleanup(connection.close)
        return connection

    def _check(self, report, name):
        return next(c for c in report["checks"] if c["name"] == name)

    def test_a_brand_new_empty_database_fails(self):
        report = validate.validate_contract(self._connection(), "inventory")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(self._check(report, "inventory_not_empty")["status"], "fail")

    def test_an_empty_declared_category_fails(self):
        connection = self._connection()
        connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        for category in list(config.DATASET.categories)[:-1]:
            connection.execute(
                "INSERT INTO pages(url, path, category, sitemap_url, doc_version,"
                " locale, route_depth) VALUES(?, ?, ?,"
                " 'https://example.invalid/s.xml', '5.8', 'en-US', 2)",
                (f"https://example.invalid/{category}", f"/{category}", category),
            )
        connection.commit()
        report = validate.validate_contract(connection, "inventory")
        empty = self._check(report, "declared_categories_have_pages")
        self.assertEqual(empty["status"], "fail")
        self.assertIn(list(config.DATASET.categories)[-1], empty["requirement"])

    def test_a_complete_inventory_passes(self):
        connection = self._connection()
        connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        for category in config.DATASET.categories:
            connection.execute(
                "INSERT INTO pages(url, path, category, sitemap_url, doc_version,"
                " locale, route_depth) VALUES(?, ?, ?,"
                " 'https://example.invalid/s.xml', '5.8', 'en-US', 2)",
                (f"https://example.invalid/{category}", f"/{category}", category),
            )
        connection.commit()
        report = validate.validate_contract(connection, "inventory")
        self.assertEqual(report["status"], "pass", report["checks"])

    def test_optional_categories_are_allowed_to_be_empty(self):
        with using(dataset={"optional_categories": tuple(config.DATASET.categories)}):
            connection = self._connection()
            connection.execute(
                "INSERT INTO sitemaps(url, category, status) "
                "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
            )
            connection.execute(
                "INSERT INTO pages(url, path, category, sitemap_url, doc_version,"
                " locale, route_depth) VALUES('https://example.invalid/a', '/a',"
                " 'guides', 'https://example.invalid/s.xml', '5.8', 'en-US', 2)"
            )
            connection.commit()
            report = validate.validate_contract(connection, "inventory")
            self.assertEqual(
                self._check(report, "declared_categories_have_pages")["status"], "pass"
            )


class InventoryFeedHookTests(unittest.TestCase):
    """Without sitemaps, an adapter swaps two functions to enumerate pages and
    the core changes not one line."""

    class FakeSource:
        """A stub site that only paginates: two API pages, each with its own
        category."""

        PAGES = {
            "https://example.invalid/api?page=1": [
                ("guides", "https://example.invalid/a"),
                ("reference", "https://example.invalid/b"),
            ],
            "https://example.invalid/api?page=2": [
                ("guides", "https://example.invalid/c"),
            ],
        }

        @staticmethod
        def inventory_feeds(dataset):
            return [(url, None) for url in InventoryFeedHookTests.FakeSource.PAGES]

        @staticmethod
        def read_feed(dataset, url):
            return InventoryFeedHookTests.FakeSource.PAGES[url]

        @staticmethod
        def normalize_location(dataset, location):
            path = "/" + location.rsplit("/", 1)[-1]
            return (path, location)

    def _connection(self, source=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        connection = connect_db(Path(directory.name) / "t.sqlite3")
        self.addCleanup(connection.close)
        # Database creation runs on the stub source too. Initialising with the
        # real one first and swapping afterwards never exercises the path where
        # a source has no sitemap at all.
        with self.quiet_source(source or self.FakeSource):
            initialize_db(connection)
        return connection

    def test_a_feed_only_source_can_initialize_the_database(self):
        # A source implementing only inventory_feeds / read_feed has no
        # sitemap_index_url, so asking it for one unconditionally on open is an
        # AttributeError at the first step.
        self.assertFalse(hasattr(self.FakeSource, "sitemap_index_url"))
        connection = self._connection()
        self.assertEqual(
            connection.execute(
                "SELECT value FROM metadata WHERE key='inventory_index'"
            ).fetchone()[0],
            "",
        )
        # One missing provenance value affects nothing else: the database is
        # still built in full.
        self.assertEqual(
            connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0],
            "3",
        )

    def test_sitemap_sources_still_record_their_index(self):
        class WithSitemap(self.FakeSource):
            @staticmethod
            def sitemap_index_url(dataset):
                return "https://example.invalid/sitemap.xml"

        connection = self._connection(WithSitemap)
        self.assertEqual(
            connection.execute(
                "SELECT value FROM metadata WHERE key='inventory_index'"
            ).fetchone()[0],
            "https://example.invalid/sitemap.xml",
        )

    def test_adapter_supplied_inventory_lands_in_the_same_tables(self):
        connection = self._connection()
        with self.quiet_source(self.FakeSource):
            total = discover.discover_inventory(connection, workers=2, refresh=False)
        self.assertEqual(total, 3)
        counts = {
            row["category"]: row["count"]
            for row in connection.execute(
                "SELECT category, COUNT(*) AS count FROM pages GROUP BY category"
            )
        }
        # A category declared by the entry itself beats the feed's, so one feed
        # listing several categories can be expressed.
        self.assertEqual(counts, {"guides": 2, "reference": 1})
        # Metadata matches the sitemap path exactly: the contract does not
        # loosen because the source changed.
        row = connection.execute(
            "SELECT doc_version, locale, route_depth, sitemap_url FROM pages LIMIT 1"
        ).fetchone()
        self.assertTrue(all(value is not None for value in tuple(row)))

    def test_a_failing_feed_is_recorded_not_swallowed(self):
        class Broken(self.FakeSource):
            @staticmethod
            def read_feed(dataset, url):
                raise TimeoutError("the site did not answer")

        connection = self._connection()
        with self.quiet_source(Broken):
            discover.discover_inventory(connection, workers=1, refresh=False)
        failed = connection.execute(
            "SELECT COUNT(*) FROM sitemaps WHERE status='failed'"
        ).fetchone()[0]
        self.assertEqual(failed, 2)
        report = validate.validate_contract(connection, "inventory")
        self.assertEqual(
            self._check_status(report, "inventory_feeds_complete"), "fail"
        )

    @contextlib.contextmanager
    def quiet_source(self, source):
        """Swap the adapter and swallow the progress log: test output should be
        test results.

        The whole workspace is swapped, so `discover` and `db` see the same stub
        source. Patching module globals one at a time reaches only one of them,
        leaving database creation on the real source — which is how the "ask the
        source for a sitemap index unconditionally on open" fault went untested.
        """
        with using(source=source), contextlib.redirect_stdout(io.StringIO()):
            yield

    def _check_status(self, report, name):
        return next(c for c in report["checks"] if c["name"] == name)["status"]


class GenericRelationLayerTests(unittest.TestCase):
    """Without a knowledge pack, the generic relation layer still works.

    The line under test: linking, storing, querying and explaining relations are
    generic, and only "why are these related" is domain knowledge.
    """

    def test_relation_labels_fall_back_to_the_generic_set(self):
        with using(knowledge=None) as workspace:
            self.assertIn("belongs_to", workspace.relation_labels)
            self.assertIn("official_link", workspace.evidence_labels)

    def test_only_core_evidence_is_expected_without_any_knowledge_pack(self):
        """With no pack attached, only core evidence is expected, and not one
        domain kind may slip in.

        There are two core kinds, and the second depends on whether the adapter
        reads member tables: official links exist on any site, member tables
        only where the adapter understands type pages. So the comparison is
        between two stubs differing in exactly that capability, rather than two
        real sites — a real site gaining the capability would silently stop this
        verifying anything.
        """
        without_members = types.SimpleNamespace()
        with_members = types.SimpleNamespace(page_members=lambda *a, **k: [])
        with using(knowledge=None, source=without_members):
            self.assertEqual(validate.expected_evidence_kinds(), ["official_link"])
        with using(knowledge=None, source=with_members):
            self.assertEqual(
                validate.expected_evidence_kinds(),
                ["official_link", "page_member_table"],
            )

    def test_query_names_work_without_a_knowledge_pack(self):
        with using(knowledge=None):
            self.assertEqual(search.query_names("Streaming"), ["streaming"])


class InventoryCoverageTests(unittest.TestCase):
    """A body links to a page the inventory does not hold, and that is not
    "the official docs have no such page".

    The next step is the opposite in each case: reword the query, or widen the
    source adapter's enumeration. Point the wrong way and the user keeps trying
    in the wrong place.
    """

    def _linked_to_a_missing_page(self):
        connection = temp_db(self)
        seed_entity(connection, entity_type="guide", name="Shader Group",
                    path="/render/shader_nodes/groups")
        # A fetched page links to a target the inventory does not hold.
        connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " anchor_text, link_kind, evidence_kind, source_url, created_at)"
            " VALUES(1, 'https://example.invalid/interface/controls/nodes/groups',"
            " '/interface/controls/nodes/groups', 'Node Groups',"
            " 'official_reference', 'official_link',"
            " 'https://example.invalid/render/shader_nodes/groups', 'now')"
        )
        connection.commit()
        return connection

    def test_a_linked_but_unlisted_page_is_found_by_name(self):
        connection = self._linked_to_a_missing_page()
        found = ondemand.linked_but_unlisted(connection, "Groups")
        self.assertEqual(
            [item["path"] for item in found], ["/interface/controls/nodes/groups"]
        )

    def test_related_says_out_of_inventory_not_entity_not_found(self):
        connection = self._linked_to_a_missing_page()
        result = context.related_payload(connection, "Groups")
        self.assertEqual(result["status"], "target_outside_inventory")
        joined = chr(10).join(result["next_steps"])
        self.assertIn("our source never enumerated it", joined)
        self.assertNotIn("the site does not have", joined)

    def test_a_genuinely_absent_name_still_says_so(self):
        """Control case: a genuine absence is not blamed on the source scope.

        And it stops at "not in this dataset": DocAtlas never looked at the live
        site, so it cannot say the documentation lacks it either.
        """
        connection = self._linked_to_a_missing_page()
        result = context.related_payload(connection, "Absolutely Nothing")
        self.assertEqual(result["status"], "entity_not_found")
        joined = chr(10).join(result["next_steps"])
        self.assertIn("no inventory page in this dataset matches", joined)
        self.assertNotIn("our source never enumerated it", joined)

    def test_gaps_separate_not_fetched_from_not_enumerated(self):
        connection = self._linked_to_a_missing_page()
        # And one more: the target is in the inventory, body not fetched.
        connection.execute(
            "INSERT INTO pages(url, path, category, status, route_depth)"
            " VALUES('https://example.invalid/modeling/fields',"
            " '/modeling/fields', 'guides', 'pending', 2)"
        )
        target_id = connection.execute(
            "SELECT id FROM pages WHERE path='/modeling/fields'"
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " target_page_id, anchor_text, link_kind, evidence_kind, source_url,"
            " created_at) VALUES(1, 'https://example.invalid/modeling/fields',"
            " '/modeling/fields', ?, 'Fields', 'official_reference',"
            " 'official_link', 'https://example.invalid/x', 'now')",
            (target_id,),
        )
        connection.commit()
        gaps = relations.link_target_gaps(connection)
        self.assertEqual(gaps["pending_targets"], 1, "in the inventory, unfetched: can be fetched")
        self.assertEqual(gaps["missing_targets"], 1, "not in the inventory: enumeration must change")
        self.assertEqual(
            gaps["top_uncovered_areas"],
            [{"area": "/interface/controls/nodes", "links": 1}],
        )

    def test_scattered_misses_are_not_reported_as_a_scope_gap(self):
        """Stray misses are ordinary noise; only a whole area with nothing
        enumerated is a gap in scope."""
        connection = temp_db(self)
        seed_entity(connection, entity_type="guide", name="Fields",
                    path="/modeling/geometry_nodes/fields")
        connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " anchor_text, link_kind, evidence_kind, source_url, created_at)"
            " VALUES(1, 'https://example.invalid/modeling/geometry_nodes/typo',"
            " '/modeling/geometry_nodes/typo', 'Typo', 'official_reference',"
            " 'official_link', 'https://example.invalid/x', 'now')"
        )
        connection.commit()
        gaps = relations.link_target_gaps(connection)
        self.assertEqual(gaps["missing_targets"], 1)
        # The inventory holds pages in the same area, so it is not uncovered.
        self.assertEqual(gaps["uncovered_areas"], 0)


class McpMultiDatasetTests(unittest.TestCase):
    """One MCP connection serves several libraries at once.

    A process pinned to one dataset means querying a second library needs
    another server entry in the client config. In practice nobody adds one, and
    every cross-library call falls back to the command line.
    """

    def setUp(self):
        self.libraries = {}
        for key, title in (("lib-a", "Alpha Manual"), ("lib-b", "Beta Manual")):
            connection, data_dir = temp_library(self)
            seed_entity(connection, entity_type="guide", name=f"{title} Home",
                        path=f"/{key}/index")
            store.store_document_result(
                connection,
                transform_document(
                    FakeRow(
                        id=connection.execute(
                            "INSERT INTO pages(url, path, category, status,"
                            " route_depth) VALUES(?, ?, 'guides', 'pending', 2)",
                            (f"https://{key}.invalid/topic", f"/{key}/topic"),
                        ).lastrowid,
                        path=f"/{key}/topic",
                        url=f"https://{key}.invalid/topic",
                        category="guides",
                    ),
                    make_document(f"{title} Topic", [text_block(f"only {title} has this line")]),
                ),
                "guides",
            )
            connection.commit()
            base = runtime.active()
            self.libraries[key] = dataclasses.replace(
                base,
                dataset=dataclasses.replace(base.dataset, id=key, name=title,
                                            product=title, knowledge=None),
                knowledge=None,
                data_dir=data_dir,
            )

        real_workspace = runtime.workspace

        def routed(dataset_id):
            if dataset_id in self.libraries:
                return self.libraries[dataset_id]
            return real_workspace(dataset_id)

        runtime.workspace = routed
        mcpserver.runtime.workspace = routed
        self.addCleanup(setattr, runtime, "workspace", real_workspace)
        self.addCleanup(setattr, mcpserver.runtime, "workspace", real_workspace)

    def _call(self, tool, arguments):
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        self.assertNotIn("error", reply, reply)
        return reply["result"]

    def test_one_connection_answers_from_two_different_libraries(self):
        a = self._call("docatlas_ask",
                       {"query": "Topic", "dataset_id": "lib-a", "no_fetch": True})
        b = self._call("docatlas_ask",
                       {"query": "Topic", "dataset_id": "lib-b", "no_fetch": True})
        self.assertFalse(a["isError"], a)
        self.assertFalse(b["isError"], b)
        self.assertIn("only Alpha Manual has this line", a["content"][0]["text"])
        self.assertIn("only Beta Manual has this line", b["content"][0]["text"])
        # Control: these really are two libraries, not one answering twice.
        self.assertNotIn("Beta", a["content"][0]["text"])

    def test_routing_does_not_leak_into_the_next_call(self):
        """Switching library is undone when the call ends, or the next call
        without a dataset_id picks up the wrong one."""
        before = runtime.active().id
        self._call("docatlas_ask",
                   {"query": "Topic", "dataset_id": "lib-b", "no_fetch": True})
        self.assertEqual(runtime.active().id, before)

    def test_structured_results_carry_the_dataset_identity(self):
        result = self._call(
            "docatlas_ask",
            {"query": "Topic", "dataset_id": "lib-a", "no_fetch": True,
             "format": "json"},
        )
        payload = result["structuredContent"]
        self.assertEqual(payload["dataset"]["dataset_id"], "lib-a")
        self.assertEqual(payload["dataset"]["product"], "Alpha Manual")
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["knowledge"][0]["knowledge_id"].startswith("K"))
        self.assertTrue(payload["contract_version"])
        # A client that does not know structuredContent must read the same thing
        # out of the text.
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)

    def test_markdown_stays_the_default_so_queries_do_not_pay_for_json(self):
        markdown = self._call(
            "docatlas_ask",
            {"query": "Topic", "dataset_id": "lib-a", "no_fetch": True},
        )
        self.assertNotIn("structuredContent", markdown)
        self.assertLess(
            len(markdown["content"][0]["text"]),
            len(
                self._call(
                    "docatlas_ask",
                    {"query": "Topic", "dataset_id": "lib-a", "no_fetch": True,
                     "format": "json"},
                )["content"][0]["text"]
            ),
        )

    def test_listing_reports_each_library_and_its_capabilities(self):
        payload = self._call(
            "docatlas_list_datasets", {"dataset_id": "lib-a", "format": "json"}
        )["structuredContent"]
        report = payload["datasets"][0]
        self.assertEqual(report["dataset_id"], "lib-a")
        self.assertEqual(report["state"], "ready")
        self.assertIn("guides", report["categories"])
        # With no pack attached the generic relation layer is still there, and
        # must not be reported as "no relation support".
        self.assertIsNone(report["knowledge_pack"])
        self.assertIn("official_link", report["evidence_kinds"])
        self.assertIn("belongs_to", report["relation_types"])

    def test_a_wrong_category_lists_the_valid_ones(self):
        result = self._call(
            "docatlas_ask",
            {"query": "Topic", "dataset_id": "lib-a", "category": "not_a_category"},
        )
        self.assertTrue(result["isError"])
        self.assertIn("guides", result["content"][0]["text"])


class BreadcrumbNoiseTests(unittest.TestCase):
    """Breadcrumbs push the whole site's directory names into every page's
    full-text index.

    In a real library over a quarter of all chunks opened with a breadcrumb, and
    nearly all of them were classified as parameters or returns. A query naming
    a directory then matches every page under it, matching the breadcrumb every
    time while not a word of the body lines up.
    """

    TRAIL = "[BlueprintAPI](https://x/a) > [BlueprintAPI/Camera](https://x/b)"

    def test_a_breadcrumb_trail_is_dropped(self):
        body = f"{self.TRAIL}\n\nSet Field Of View\n\nTarget is Camera Component"
        cleaned = chunking.strip_breadcrumbs(body)
        self.assertNotIn("https://x/a", cleaned)
        # This line is the evidence behind a relation and must survive the
        # breadcrumb removal.
        self.assertIn("Target is Camera Component", cleaned)
        self.assertIn("Set Field Of View", cleaned)

    def test_prose_links_and_lone_links_survive(self):
        for body in (
            "See the [Camera docs](https://x/c) for details.",
            "[Only One](https://x/d)",
        ):
            self.assertEqual(chunking.strip_breadcrumbs(body), body)

    def test_other_separators_count_too(self):
        self.assertEqual(
            chunking.strip_breadcrumbs("[A](https://x/a) › [B](https://x/b)").strip(),
            "",
        )

    def _section(self, position, title, body, knowledge_type):
        return {
            "position": position,
            "heading_level": 2,
            "heading_path": f"Set Field Of View > {title}",
            "title": title,
            "body_md": body,
            "knowledge_type": knowledge_type,
            "source_url": "https://x/page",
            "source_anchor": "https://x/page",
            "quality_score": 1.0,
        }

    def test_chunks_do_not_carry_the_trail_into_the_index(self):
        # The real shape: sections merge into one chunk, so the navigation
        # section's body rides into the full-text index with them.
        chunks = chunking.chunk_sections(
            [
                self._section(
                    0,
                    "Navigation",
                    f"{self.TRAIL}\n\nTarget is Camera Component",
                    "navigation",
                ),
                self._section(
                    1,
                    "Inputs",
                    "| Type | Name |\n| --- | --- |\n| real | In Field Of View |",
                    "parameters",
                ),
            ],
            page_title="Set Field Of View",
            category="reference",
            document_type=None,
        )
        indexed = " ".join(chunk["content_text"] for chunk in chunks)
        self.assertNotIn("https://x/a", indexed)
        self.assertNotIn("BlueprintAPI/Camera", indexed)
        self.assertIn("Target is Camera Component", indexed)
        self.assertIn("In Field Of View", indexed)

    def test_changing_the_rule_bumps_the_chunker_version(self):
        """A chunking rule change without a version bump mixes old and new chunks.

        Pinned on purpose: any change to the rules has to update this line
        deliberately, so a rule change can never ship silently.
        """
        self.assertEqual(constants.CHUNKER_VERSION, "v9")


class CrossLanguageDiagnosisTests(unittest.TestCase):
    """Querying a single-language library in another script finds nothing, as
    it must.

    That is not a fault — the library holds no text in that script. But an empty
    result carries no information, and the user cannot tell from it what to
    change. So this kind of "nothing" gets recognised for what it is.

    The test is on script alone, and the language comes from the dataset's
    `language` field: no assumption about which language the user speaks, and
    none about which the documentation is written in.
    """

    def test_script_is_detected_not_guessed_from_the_user(self):
        # The same query, opposite verdicts in libraries of different languages:
        # the language comes from the dataset, it is not guessed.
        self.assertEqual(text.script_mismatch("如何设置视野", "en-US"), "han")
        self.assertEqual(text.script_mismatch("如何设置视野", "zh-CN"), "")
        self.assertEqual(text.script_mismatch("Set Field Of View", "zh-CN"), "latin")
        self.assertEqual(text.script_mismatch("Set Field Of View", "en-US"), "")

    def test_mixed_scripts_go_with_the_dominant_one(self):
        # A query whose body is Han characters against a Latin-script library.
        self.assertEqual(text.script_mismatch("Widget 到底是个什么东西呢", "en-US"), "han")
        # But an English query with a character or two mixed in does not count.
        self.assertEqual(text.script_mismatch("Widget virtualized geometry 用法", "en-US"), "")

    def test_japanese_kanji_and_kana_both_belong_to_japanese(self):
        # Japanese mixes kanji and kana; both scripts are its own, so neither
        # counts as foreign.
        self.assertEqual(text.script_mismatch("ノードの設定", "ja-JP"), "")
        self.assertEqual(text.script_mismatch("設定", "ja-JP"), "")

    def test_an_unknown_language_tag_falls_back_to_latin(self):
        self.assertEqual(text.expected_scripts("xx-YY"), ("latin",))

    def test_symbols_only_queries_are_never_flagged(self):
        for query in ("set_timer", "MACRO_NAME", "123", "", "   "):
            self.assertEqual(text.script_mismatch(query, "en-US"), "", query)

    def test_the_empty_result_says_which_kind_of_nothing_it_is(self):
        connection = temp_db(self)
        lookup = ondemand.inventory_lookup(connection, "把小方块撒到网格表面")
        steps = chr(10).join(context.describe_lookup(lookup))
        self.assertIn("written in Han", steps)
        self.assertIn(config.DATASET.language, steps)
        # The key point: not "the documentation does not have this page", which
        # is a different kind of nothing.
        self.assertNotIn("the site does not have", steps)

    def test_a_same_script_miss_still_reads_as_a_genuine_miss(self):
        connection = temp_db(self)
        lookup = ondemand.inventory_lookup(connection, "zzzznotarealpage")
        steps = chr(10).join(context.describe_lookup(lookup))
        self.assertIn("no inventory page in this dataset matches", steps)
        self.assertNotIn("is written in", steps)

    def test_mcp_reports_language_mismatch_as_its_own_status(self):
        connection = temp_db(self)
        pack = context.answer(
            connection, "把小方块撒到网格表面", token_budget=800,
            category=None, allow_fetch=False, quiet=True,
        )
        payload = mcpserver._structured_ask(runtime.active(), pack)
        self.assertEqual(payload["status"], "language_mismatch")
        self.assertTrue(payload["next_steps"])


class RelationContractTests(unittest.TestCase):
    """A new dataset builds real relations by implementing one function.

    What these cases really pin down is "without touching the MCP server or the
    generic relation core": the stub pack below writes no SQL and knows nothing
    of tables or entity ids.
    """

    class ToyDomain:
        """A stub product's knowledge pack: a component and a class whose names
        line up count as a relation."""

        DERIVED_EVIDENCE_KINDS = ("toy_name_match",)
        RELATION_LABELS = {"implements": "implemented by"}

        @staticmethod
        def relation_rules(graph):
            for source, target, name in graph.name_matches("widget", "toy_class"):
                yield relations.RelationCandidate(
                    source=source,
                    target=target,
                    relation_type="implements",
                    evidence_kind="toy_name_match",
                    confidence=0.95,
                    note=f"both are called {name}",
                )

    def _seeded(self):
        connection = temp_db(self)
        seed_entity(connection, entity_type="widget", name="Particle Emitter",
                    path="/ui/particle-emitter")
        seed_entity(connection, entity_type="toy_class", name="ParticleEmitter",
                    path="/api/particle-emitter")
        connection.commit()
        return connection

    def test_a_new_domain_builds_relations_without_touching_the_core(self):
        connection = self._seeded()
        with using(knowledge=self.ToyDomain, dataset={"knowledge": "toy"}):
            outcome = relations.rebuild(connection)
        self.assertEqual(outcome["domain_relations"], 1)
        row = connection.execute(
            "SELECT relation_type, evidence_kind, confidence, note, origin"
            " FROM relations"
        ).fetchone()
        self.assertEqual(row["relation_type"], "implements")
        self.assertEqual(row["evidence_kind"], "toy_name_match")
        self.assertEqual(row["confidence"], 0.95)
        self.assertEqual(row["note"], "both are called Particle Emitter")
        # Ownership is recorded as the pack, or a full rebuild cannot clear it
        # and nobody can tell who created it.
        self.assertEqual(row["origin"], "toy")

    def test_official_links_still_build_without_any_knowledge_pack(self):
        """Without a pack, "a body links to another document" still holds."""
        connection = temp_db(self)
        seed_entity(connection, entity_type="guide", name="Fields",
                    path="/modeling/fields")
        seed_entity(connection, entity_type="guide", name="Capture Attribute",
                    path="/modeling/capture")
        connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " anchor_text, link_kind, evidence_kind, source_url, created_at)"
            " VALUES(1, 'https://example.invalid/modeling/capture',"
            " '/modeling/capture', 'Capture', 'official_reference',"
            " 'official_link', 'https://example.invalid/modeling/fields', 'now')"
        )
        connection.commit()
        with using(knowledge=None, dataset={"knowledge": None}):
            outcome = relations.rebuild(connection)
        self.assertEqual(outcome["official_links"], 1)
        row = connection.execute(
            "SELECT relation_type, evidence_kind, confidence, origin FROM relations"
        ).fetchone()
        self.assertEqual(row["relation_type"], "official_reference")
        self.assertEqual(row["evidence_kind"], "official_link")
        self.assertEqual(row["confidence"], 1.0)
        self.assertEqual(row["origin"], "core")

    def test_full_rebuild_drops_only_the_packs_own_relations(self):
        connection = self._seeded()
        connection.execute(
            "INSERT INTO relations(from_entity_id, to_entity_id, relation_type,"
            " evidence_kind, confidence, source_url, origin, created_at, updated_at)"
            " VALUES(1, 2, 'belongs_to', 'official_link', 1.0, 'u', 'core', 'n', 'n')"
        )
        connection.execute(
            "INSERT INTO relations(from_entity_id, to_entity_id, relation_type,"
            " evidence_kind, confidence, source_url, origin, created_at, updated_at)"
            " VALUES(2, 1, 'stale', 'toy_name_match', 0.5, 'u', 'toy', 'n', 'n')"
        )
        connection.commit()
        with using(knowledge=self.ToyDomain, dataset={"knowledge": "toy"}):
            relations.rebuild(connection)
        kinds = {
            r["relation_type"]
            for r in connection.execute("SELECT relation_type FROM relations")
        }
        self.assertIn("belongs_to", kinds, "crawled facts survive a pack rebuild")
        self.assertNotIn("stale", kinds, "last round's inferences must be gone")

    def test_incremental_runs_the_same_rules_as_the_full_build(self):
        """After an on-demand fetch, domain relations match a full rebuild.

        A separate incremental function fills in some kinds and never the
        others, so a library built in one pass and a library filled in as it was
        used end up holding different things.
        """
        connection = self._seeded()
        new_page = connection.execute(
            "SELECT page_id FROM entities WHERE entity_type='widget'"
        ).fetchone()["page_id"]
        with using(knowledge=self.ToyDomain, dataset={"knowledge": "toy"}):
            created = relations.link_new_pages(connection, [new_page])
        self.assertEqual(created, 1)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM relations WHERE relation_type='implements'"
            ).fetchone()[0],
            1,
        )

    def test_a_target_that_is_not_in_the_library_is_reported_not_invented(self):
        """An unreachable target becomes a diagnostic: it may not vanish
        quietly, and certainly may not be linked to something invented."""
        connection = self._seeded()
        graph = relations.RelationGraph(connection)
        self.assertEqual(graph.find("Nothing Like This"), [])
        self.assertIn("Nothing Like This", graph.unresolved)

    def test_an_over_ambiguous_name_stops_being_evidence(self):
        connection = temp_db(self)
        seed_entity(connection, entity_type="widget", name="Get Value")
        for index in range(relations.DEFAULT_MAX_AMBIGUITY + 1):
            seed_entity(connection, entity_type="toy_class", name="Get Value",
                        path=f"/api/get-value-{index}")
        connection.commit()
        graph = relations.RelationGraph(connection)
        self.assertEqual(list(graph.name_matches("widget", "toy_class")), [])

    def test_self_loops_and_out_of_range_confidence_are_rejected(self):
        connection = self._seeded()
        rows = list(connection.execute("SELECT * FROM entities ORDER BY id"))
        me = relations.Entity.of(rows[0])
        other = relations.Entity.of(rows[1])

        class Bad:
            DERIVED_EVIDENCE_KINDS = ()

            @staticmethod
            def relation_rules(graph):
                yield relations.RelationCandidate(me, me, "self", "toy", 1.0)
                yield relations.RelationCandidate(me, other, "", "toy", 1.0)
                yield relations.RelationCandidate(me, other, "ok", "toy", 7.5)

        with using(knowledge=Bad, dataset={"knowledge": "bad"}):
            outcome = relations.rebuild(connection)
        self.assertEqual(outcome["rejected"], 2)
        self.assertEqual(outcome["domain_relations"], 1)
        # Confidence is clamped into range rather than stored as given.
        self.assertEqual(
            connection.execute("SELECT confidence FROM relations").fetchone()[0], 1.0
        )


class RelatedContractTests(unittest.TestCase):
    """A bare `[]` used to stand for three completely different states."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def _add(self, path, category, title, blocks):
        cursor = self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, doc_version, locale,"
            " route_depth, discovered_at, last_seen_at) VALUES(?, ?, ?,"
            " 'https://example.invalid/s.xml', '5.8', 'en-US', 3,"
            " '2026-01-01', '2026-01-01')",
            (f"https://example.invalid{path}", path, category),
        )
        row = FakeRow(
            id=cursor.lastrowid,
            path=path,
            url=f"https://example.invalid{path}",
            category=category,
        )
        store.store_document_result(
            self.connection, transform_document(row, make_document(title, blocks)), category
        )
        self.connection.commit()

    def test_unknown_name_says_so_and_points_at_the_inventory(self):
        self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, route_depth) VALUES("
            "'https://example.invalid/BlueprintAPI/Camera/SetFieldOfView',"
            "'/BlueprintAPI/Camera/SetFieldOfView', 'blueprint_api',"
            "'https://example.invalid/s.xml', 3)"
        )
        self.connection.commit()
        initialize_db(self.connection)
        result = context.related_payload(self.connection, "Set Field Of View")
        self.assertEqual(result["status"], "entity_not_found")
        self.assertTrue(result["lookup"]["pending_pages"])
        self.assertTrue(result["next_steps"])

    def test_known_entity_without_relations_is_not_the_same_as_missing(self):
        self._add(
            "/docs/guides/streaming-overview",
            "guides",
            "Streaming Virtualized Geometry",
            [text_block("Streaming is a virtualized micropolygon geometry system.")],
        )
        result = context.related_payload(
            self.connection, "Streaming Virtualized Geometry"
        )
        self.assertEqual(result["status"], "entity_found_but_no_relations")
        self.assertTrue(result["entities"])
        self.assertTrue(result["next_steps"])

    def test_nothing_anywhere_is_its_own_state(self):
        result = context.related_payload(self.connection, "zzzznotarealthing")
        self.assertEqual(result["status"], "entity_not_found")
        self.assertEqual(result["lookup"]["pending_pages"], [])

    def test_missing_knowledge_id_is_not_treated_as_a_missing_page(self):
        # A K number is a chunk id, not a page name. Not finding one means the
        # id does not exist, which is a different diagnosis from "no such page"
        # or "in the inventory, unfetched", so inventory_lookup does not apply:
        # it compares names against titles and paths, which is meaningless for
        # a number.
        result = context.related_payload(self.connection, "K999999")
        self.assertEqual(result["status"], "knowledge_id_not_found")
        self.assertNotIn("lookup", result)
        self.assertTrue(result["next_steps"])


class McpRelatedEvidenceTests(unittest.TestCase):
    """SKILL.md promises that every relation from `related` carries a `note`
    and a source. MCP is the entry point the Skill prefers, so a text rendering
    that drops those two fields makes the promise empty."""

    class _NoCloseConnection:
        """`tool_related` closes the connection when it is done, while the test
        goes on asserting against it, so a proxy swallows close and the real
        connection is left to tearDown."""

        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            pass

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        self.addCleanup(self.connection.close)
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        self.connection.commit()

    def _add_page_entity(self, path, title):
        cursor = self.connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, doc_version,"
            " locale, route_depth, discovered_at, last_seen_at) VALUES(?, ?,"
            " 'guides', 'https://example.invalid/s.xml', '5.8', 'en-US', 3,"
            " '2026-01-01', '2026-01-01')",
            (f"https://example.invalid{path}", path),
        )
        row = FakeRow(
            id=cursor.lastrowid,
            path=path,
            url=f"https://example.invalid{path}",
            category="guides",
        )
        store.store_document_result(
            self.connection,
            transform_document(row, make_document(title, [text_block(title)])),
            "guides",
        )
        self.connection.commit()
        return self.connection.execute(
            "SELECT id FROM entities WHERE canonical_name=?", (title,)
        ).fetchone()[0]

    def test_related_text_includes_evidence_url_and_note(self):
        from_id = self._add_page_entity("/a", "Alpha Component")
        self._add_page_entity("/b", "Beta Component")
        self.connection.execute(
            "INSERT INTO relations(from_entity_id, to_entity_id, relation_type,"
            " evidence_kind, confidence, source_url, note, created_at, updated_at)"
            " VALUES(?, (SELECT id FROM entities WHERE canonical_name='Beta"
            " Component'), 'references', 'name_match', 0.6,"
            " 'https://example.invalid/evidence-page', 'same name, unverified',"
            " '2026-01-01', '2026-01-01')",
            (from_id,),
        )
        self.connection.commit()
        original_open = mcpserver._open
        mcpserver._open = lambda _workspace: self._NoCloseConnection(self.connection)
        try:
            output = mcpserver.tool_related({"subject": "Alpha Component"})
        finally:
            mcpserver._open = original_open
        self.assertIn("https://example.invalid/evidence-page", output)
        self.assertIn("same name, unverified", output)


class NeutralNamingTests(unittest.TestCase):
    """Once a second library is installed, output still naming the first one
    is a lie."""

    def test_core_modules_never_name_an_installed_product(self):
        """The core must not name any product, in code or in comments.

        Only `sources/<site>.py` and `knowledge/<domain>.py` are written for one
        site, so only they may name one. Naming a product anywhere else assumes
        the reader installed that particular library.

        The names to look for come from the datasets configured on this machine,
        so the check adapts instead of hardcoding a list that goes stale. A
        product id is a slug, and prose spells it out, so the leading segment is
        what gets searched: that segment is the brand, while the ones after it
        are qualifiers ("engine", "manual") too generic to match on.
        """
        from docatlas.runtime import DATASET_CONFIG_DIR, available_dataset_ids

        brands = set()
        for dataset_id in available_dataset_ids():
            product = dataset.load_dataset(dataset_id, DATASET_CONFIG_DIR).product
            head = re.split(r"[-_ ]", product.strip())[0]
            # Short segments collide with ordinary words and would only produce
            # false alarms.
            if len(head) >= 6:
                brands.add(head.casefold())
        self.assertTrue(brands, "no dataset to check the core against")

        core = Path(config.REPO_ROOT) / "docatlas"
        offenders = []
        for path in sorted(core.rglob("*.py")):
            relative = path.relative_to(core)
            if relative.parts[0] in ("sources", "knowledge"):
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                folded = line.casefold()
                for brand in brands:
                    if brand in folded:
                        offenders.append(f"{relative}:{number} names {brand!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_context_pack_reports_product_and_version_separately(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        connection = connect_db(Path(directory.name) / "t.sqlite3")
        self.addCleanup(connection.close)
        initialize_db(connection)
        pack = context.build_context_pack(
            connection, "anything", token_budget=100, category=None
        )
        self.assertEqual(pack["product"], config.DATASET.product)
        self.assertEqual(pack["version"], config.DATASET.version)
        self.assertNotIn("ue_version", pack)

    def test_chunk_context_prefix_uses_the_dataset_product(self):
        chunks = chunking.chunk_section(
            make_section("Body", "some text", position=0),
            page_title="Page",
            category="guides",
            document_type=None,
        )
        self.assertTrue(
            chunks[0]["context_prefix"].startswith(
                f"{config.DATASET.product} {config.DATASET.version}"
            ),
            chunks[0]["context_prefix"],
        )


class SkillMcpContractTests(unittest.TestCase):
    """MCP is the entry point an agent really calls, so the published contract
    and the implementation may not disagree."""

    def test_every_argument_the_handlers_read_is_declared(self):
        # An argument read by a handler but absent from inputSchema is one no
        # client ever knows it may pass.
        import inspect

        from docatlas import mcpserver

        for tool in mcpserver.TOOLS:
            handler = mcpserver.HANDLERS[tool["name"]]
            source = inspect.getsource(handler)
            declared = set(tool["inputSchema"].get("properties", {}))
            read = set(re.findall(r"arguments\.get\(\s*[\"'](\w+)[\"']", source))
            self.assertLessEqual(
                read, declared, f"{tool['name']} reads undeclared arguments: {read - declared}"
            )

    def test_skill_lists_the_tools_that_actually_exist(self):
        from docatlas.cli import skill_substitutions
        from docatlas import mcpserver

        listed = skill_substitutions()["DOCATLAS_MCP_TOOLS"]
        for tool in mcpserver.TOOLS:
            self.assertIn(tool["name"], listed)

    def test_skill_tells_the_ai_to_prefer_mcp(self):
        skill = (
            Path(config.REPO_ROOT) / "skills" / "docatlas" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("docatlas_ask", skill)
        self.assertIn("{{DOCATLAS_MCP_TOOLS}}", skill)



def seed_chunk(
    connection: sqlite3.Connection,
    *,
    title: str,
    heading_path: str,
    body: str,
) -> int:
    """Put a chunk in the library, with the page and section it must hang off;
    returns the chunk id.

    Through the real schema rather than a fake object: `chunk_versions` has a
    foreign key on `chunks`, and a library built around that is not the same
    thing as a real one.
    """
    now = "2026-07-26T00:00:00Z"
    slug = text.normalize_name(title) or "page"
    page_id = connection.execute(
        "INSERT INTO pages(url, path, category, status, title, route_depth)"
        " VALUES(?, ?, 'guides', 'success', ?, 2)",
        (f"https://example.invalid/{slug}", f"/{slug}", title),
    ).lastrowid
    section_id = connection.execute(
        "INSERT INTO sections(page_id, position, heading_level, heading_path,"
        " title, content_md, content_text, source_url, token_estimate)"
        " VALUES(?, 0, 2, ?, ?, ?, ?, '', 10)",
        (page_id, heading_path, title, body, body),
    ).lastrowid
    return connection.execute(
        "INSERT INTO chunks(section_id, page_id, chunk_index, chunk_count,"
        " knowledge_type, title, heading_path, context_prefix, content_md,"
        " content_text, source_url, source_anchor, token_estimate,"
        " content_hash, quality_score, created_at, updated_at)"
        " VALUES(?, ?, 0, 1, 'summary', ?, ?, '', ?, ?, '', '', 10, ?, 1.0, ?, ?)",
        (section_id, page_id, title, heading_path, body, body,
         text.normalize_name(body)[:32] or "h", now, now),
    ).lastrowid


class VersionIntentTests(unittest.TestCase):
    """Version intent: decided upstream, carried out by the core, which never
    guesses which version the user wants.

    Written in the test dataset's own version notation. The core recognises no
    notation at all — reading marks and ordering them is the domain layer's job
    — so what is verified here is what happens once the marks are in hand.
    """

    def setUp(self):
        self.connection = temp_db(self)

    def test_unknown_mode_is_refused_instead_of_silently_ignored(self):
        # Filtering nothing in silence is more dangerous than refusing: the
        # caller goes on believing the restriction took effect.
        with self.assertRaises(ValueError):
            versions.parse_intent("newest-first", "v1.0")

    def test_target_alone_means_strict(self):
        intent = versions.parse_intent(None, "v1.0")
        self.assertEqual(intent.mode, versions.STRICT)
        self.assertTrue(intent.excludes)

    def test_no_intent_at_all_changes_nothing(self):
        self.assertIsNone(versions.parse_intent(None, None))
        self.assertIsNone(versions.parse_intent("any", "v1.0"))

    def _rows(self, *chunk_ids):
        return [
            {"id": cid, "page_title": f"P{cid}", "score": 10.0} for cid in chunk_ids
        ]

    def _store_marks(self, chunk_id):
        row = self.connection.execute(
            "SELECT p.title, c.heading_path, c.content_text FROM chunks c"
            " JOIN pages p ON p.id=c.page_id WHERE c.id=?",
            (chunk_id,),
        ).fetchone()
        versions.store_marks(
            self.connection,
            chunk_id,
            f"{row['title']}\n{row['heading_path']}",
            row["content_text"],
        )

    def test_heading_marker_excludes_but_body_marker_does_not(self):
        """The rule that matters most here, settled by measurement.

        A mark in a heading qualifies the whole section; one in a row of a
        member table qualifies that row. Excluding on the latter hides the whole
        table — which is far older than that one row.
        """
        heading_limited = seed_chunk(
            self.connection,
            title="Annotations (since v2.0)",
            heading_path="Annotations (since v2.0)",
            body="Annotations attach build-time metadata.",
        )
        body_only = seed_chunk(
            self.connection,
            title="Widget",
            heading_path="Widget > Methods",
            body="resize changes the size. reflow (since v2.0) rebuilds the"
            " layout.",
        )
        for chunk_id in (heading_limited, body_only):
            self._store_marks(chunk_id)
        intent = versions.parse_intent("strict", "v1.0")
        kept, report = versions.apply(
            self.connection, self._rows(heading_limited, body_only), intent
        )
        self.assertEqual([row["id"] for row in kept], [body_only])
        self.assertEqual(report["excluded"], 1)

    def test_migration_lifts_content_that_names_another_version(self):
        """Strict and migration handle the same evidence in opposite ways.

        Which is exactly why "prefer the newer version" cannot be hardcoded: in
        a migration question, the passage naming the older version is the
        answer.
        """
        plain = seed_chunk(
            self.connection,
            title="Transfer Node",
            heading_path="Transfer Node",
            body="Transfers attributes between geometries.",
        )
        migration_evidence = seed_chunk(
            self.connection,
            title="Sample Node",
            heading_path="Sample Node > Examples",
            body="This recreates what the Transfer node did (until v1.5).",
        )
        self._store_marks(migration_evidence)
        rows = self._rows(plain, migration_evidence)
        rows[0]["score"] = 20.0  # ranks first when unrestricted
        kept, _ = versions.apply(
            self.connection, rows, versions.parse_intent("migration", "v2.0")
        )
        self.assertEqual(kept[0]["id"], migration_evidence)

        # Same data, and under strict nothing is dropped: `until` never
        # excludes.
        kept, report = versions.apply(
            self.connection,
            self._rows(plain, migration_evidence),
            versions.parse_intent("strict", "v2.0"),
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(report["excluded"], 0)

    def test_compare_keeps_everything_and_labels_it(self):
        chunk_id = seed_chunk(
            self.connection,
            title="Annotations (since v2.0)",
            heading_path="Annotations (since v2.0)",
            body="Annotations attach build-time metadata.",
        )
        self._store_marks(chunk_id)
        kept, report = versions.apply(
            self.connection,
            self._rows(chunk_id),
            versions.parse_intent("compare", "v1.0"),
        )
        self.assertEqual(report["excluded"], 0)
        self.assertEqual(kept[0]["applies_to"][0]["version"], "v2.0")

    def test_dataset_without_version_vocabulary_never_filters(self):
        """Not knowing means not filtering. Withholding content is an error that
        leaves no trace in the result.

        Through an adapter implementing no extension at all, rather than a real
        dataset that happens to lack version support — the day that one gains
        it, this case would quietly stop meaning anything.
        """
        chunk_id = seed_chunk(
            self.connection, title="Widget", heading_path="Widget", body="A widget."
        )
        with using(source=types.SimpleNamespace()):
            self.assertFalse(versions.supported())
            kept, report = versions.apply(
                self.connection,
                self._rows(chunk_id),
                versions.Intent(mode="strict", target="2.0", target_key="2.0"),
            )
        self.assertEqual(len(kept), 1)
        self.assertFalse(report["dataset_supports_versions"])
        self.assertIn("declares no version vocabulary", report["note"])

    def test_unrecognised_target_version_says_so_instead_of_filtering(self):
        # A different version notation: unable to read it, the library says so
        # rather than quietly filtering nothing.
        chunk_id = seed_chunk(
            self.connection, title="Lifetime", heading_path="Lifetime", body="x"
        )
        kept, report = versions.apply(
            self.connection,
            self._rows(chunk_id),
            versions.parse_intent("strict", "Release 2024"),
        )
        self.assertEqual(len(kept), 1)
        self.assertIn("does not recognise the version", report["note"])


class PageMemberTests(unittest.TestCase):
    """Members listed in a type page's tables must be able to become entities.

    The stub adapter below carries no product-specific vocabulary at all: if the
    core recognised any particular product's modifiers or entity types, these
    cases would collapse.
    """

    class ToySource:
        """Stub site: every `name | type` line of a section body is a member."""

        @staticmethod
        def page_members(dataset, *, category, title, path, sections):
            found = []
            for section in sections:
                if section["heading_path"].split(" > ")[-1] != "Fields":
                    continue
                for line in section["body_md"].splitlines():
                    name, _, kind = line.partition("|")
                    if name.strip():
                        found.append(
                            {
                                "name": name.strip(),
                                "entity_type": "toy_field",
                                "attributes": {"declared": kind.strip()},
                            }
                        )
            return found

    def _page(self, connection, title, path, body):
        page_id = connection.execute(
            "INSERT INTO pages(url, path, category, status, title, route_depth)"
            " VALUES(?, ?, 'guides', 'success', ?, 2)",
            (f"https://example.invalid{path}", path, title),
        ).lastrowid
        now = "2026-07-26T00:00:00Z"
        owner_id = connection.execute(
            "INSERT INTO entities(page_id, entity_type, canonical_name,"
            " normalized_name, source_url, version, created_at, updated_at)"
            " VALUES(?, 'toy_class', ?, ?, ?, '1', ?, ?)",
            (page_id, title, text.normalize_name(title),
             f"https://example.invalid{path}", now, now),
        ).lastrowid
        sections = [
            {
                "heading_path": f"{title} > Fields",
                "body_md": body,
                "knowledge_type": "details",
                "source_anchor": f"https://example.invalid{path}#fields",
            }
        ]
        with using(source=self.ToySource, knowledge=None, dataset={"knowledge": None}):
            found = members.collect(
                category="guides",
                title=title,
                path=path,
                source_url=f"https://example.invalid{path}",
                sections=sections,
                module=None,
            )
            store.store_members(connection, page_id, owner_id, found)
        connection.commit()
        return page_id, owner_id

    def test_members_become_entities_without_any_domain_knowledge(self):
        connection = temp_db(self)
        self._page(connection, "Widget", "/api/widget", "Length|float\nWidth|float")
        rows = list(
            connection.execute(
                "SELECT canonical_name, qualified_name, owner_type, entity_type"
                " FROM entities WHERE member_of_id IS NOT NULL ORDER BY canonical_name"
            )
        )
        self.assertEqual([r["canonical_name"] for r in rows], ["Length", "Width"])
        self.assertEqual(rows[0]["qualified_name"], "Widget::Length")
        self.assertEqual(rows[0]["owner_type"], "Widget")
        self.assertEqual(rows[0]["entity_type"], "toy_field")

    def test_the_same_member_name_on_two_pages_stays_two_things(self):
        """Same-named properties of different types stay apart — the whole
        reason an identity carries its owner."""
        connection = temp_db(self)
        self._page(connection, "Widget", "/api/widget", "Length|float")
        self._page(connection, "Gadget", "/api/gadget", "Length|int")
        qualified = sorted(
            row[0]
            for row in connection.execute(
                "SELECT qualified_name FROM entities WHERE canonical_name='Length'"
            )
        )
        self.assertEqual(qualified, ["Gadget::Length", "Widget::Length"])

    def test_belongs_to_is_built_by_the_core_not_by_a_domain_pack(self):
        connection = temp_db(self)
        _, owner_id = self._page(
            connection, "Widget", "/api/widget", "Length|float\nWidth|float"
        )
        with using(source=self.ToySource, knowledge=None, dataset={"knowledge": None}):
            outcome = relations.rebuild(connection)
        self.assertEqual(outcome["member_links"], 2)
        rows = list(
            connection.execute(
                "SELECT relation_type, evidence_kind, confidence, origin,"
                " to_entity_id FROM relations"
            )
        )
        self.assertEqual({r["relation_type"] for r in rows}, {"belongs_to"})
        self.assertEqual({r["evidence_kind"] for r in rows}, {"page_member_table"})
        self.assertEqual({r["confidence"] for r in rows}, {1.0})
        self.assertEqual({r["origin"] for r in rows}, {"core"})
        self.assertEqual({r["to_entity_id"] for r in rows}, {owner_id})

    def test_official_links_do_not_multiply_by_the_number_of_members(self):
        """N members and M links on a page must not become N×M relations.

        A link goes from this page to another, not once per member on it.
        """
        connection = temp_db(self)
        source_page, _ = self._page(
            connection, "Widget", "/api/widget", "A|int\nB|int\nC|int\nD|int"
        )
        self._page(connection, "Gadget", "/api/gadget", "E|int")
        connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " anchor_text, link_kind, evidence_kind, source_url, created_at)"
            " VALUES(?, 'https://example.invalid/api/gadget', '/api/gadget',"
            " 'Gadget', 'official_reference', 'official_link',"
            " 'https://example.invalid/api/widget', 'now')",
            (source_page,),
        )
        connection.commit()
        with using(source=self.ToySource, knowledge=None, dataset={"knowledge": None}):
            outcome = relations.rebuild(connection)
        self.assertEqual(outcome["official_links"], 1, "not one link per member")

    def test_incremental_builds_the_same_member_relations_as_a_full_run(self):
        connection = temp_db(self)
        page_id, _ = self._page(
            connection, "Widget", "/api/widget", "Length|float\nWidth|float"
        )
        with using(source=self.ToySource, knowledge=None, dataset={"knowledge": None}):
            relations.link_new_pages(connection, [page_id])
            incremental = connection.execute(
                "SELECT COUNT(*) FROM relations WHERE evidence_kind='page_member_table'"
            ).fetchone()[0]
            connection.execute("DELETE FROM relations")
            relations.rebuild(connection)
            full = connection.execute(
                "SELECT COUNT(*) FROM relations WHERE evidence_kind='page_member_table'"
            ).fetchone()[0]
        self.assertEqual(incremental, full)
        self.assertEqual(full, 2)

    def test_an_adapter_without_member_tables_changes_nothing(self):
        """A dataset without page_members costs nothing and changes nothing."""
        connection = temp_db(self)
        with using(source=types.SimpleNamespace()):
            self.assertFalse(members.supported())
            self.assertEqual(members.backfill(connection), 0)

    def test_domain_aliases_reach_members_through_the_knowledge_pack(self):
        """Domain aliases for members come from the knowledge pack; the core
        knows none of the names."""

        class ToyDomain:
            @staticmethod
            def member_aliases(*, name, entity_type, owner, attributes):
                return {(f"{name} ({attributes['declared']})", "toy_display")}

        connection = temp_db(self)
        page_id = connection.execute(
            "INSERT INTO pages(url, path, category, status, title, route_depth)"
            " VALUES('https://example.invalid/w', '/w', 'guides', 'success', 'W', 2)"
        ).lastrowid
        owner_id = connection.execute(
            "INSERT INTO entities(page_id, entity_type, canonical_name,"
            " normalized_name, source_url, version, created_at, updated_at)"
            " VALUES(?, 'toy_class', 'W', 'w', 'u', '1', 'n', 'n')",
            (page_id,),
        ).lastrowid
        with using(source=self.ToySource, knowledge=ToyDomain):
            found = members.collect(
                category="guides", title="W", path="/w", source_url="u",
                sections=[{"heading_path": "W > Fields", "body_md": "Length|float",
                           "knowledge_type": "details", "source_anchor": "u"}],
                module=None,
            )
            store.store_members(connection, page_id, owner_id, found)
        aliases = {
            row[0]
            for row in connection.execute(
                "SELECT alias FROM entity_aliases WHERE alias_type='toy_display'"
            )
        }
        self.assertEqual(aliases, {"Length (float)"})


class LinkClosureTests(unittest.TestCase):
    """How a page referenced by an in-scope body, but absent from the
    inventory, gets collected.

    The stub adapter answers only "which category of this site does this path
    belong to"; not one concrete directory is written into the core.
    """

    class ToySource:
        @staticmethod
        def categorize_path(dataset, path):
            return "guides" if path.startswith("/guide/") else None

        @staticmethod
        def normalize_link_target(dataset, target_url):
            return target_url.removeprefix("https://example.invalid") or None

        @staticmethod
        def canonical_url(dataset, path):
            return f"https://example.invalid{path}?v={dataset.version}"

    def _library(self, links):
        connection = temp_db(self)
        connection.execute(
            "INSERT INTO sitemaps(url, category, status)"
            " VALUES('https://example.invalid/feed.xml', 'guides', 'success')"
        )
        page_id = connection.execute(
            "INSERT INTO pages(url, path, category, sitemap_url, status, title,"
            " route_depth, doc_version, locale)"
            " VALUES('https://example.invalid/guide/start', '/guide/start',"
            " 'guides', 'https://example.invalid/feed.xml', 'success', 'Start',"
            " 2, '1', 'en')"
        ).lastrowid
        for target, count in links.items():
            for index in range(count):
                connection.execute(
                    "INSERT INTO page_links(from_page_id, target_url, target_path,"
                    " anchor_text, link_kind, evidence_kind, source_url, created_at)"
                    " VALUES(?, ?, ?, ?, 'reference', 'official_link', 'u', 'now')",
                    (page_id, f"https://example.invalid{target}", target, f"a{index}"),
                )
        connection.commit()
        return connection

    def test_a_referenced_page_the_adapter_recognises_is_admitted(self):
        connection = self._library({"/guide/advanced": 1})
        with using(source=self.ToySource):
            outcome = coverage.admit_linked_targets(connection)
        self.assertEqual(outcome["admitted"], 1)
        row = connection.execute(
            "SELECT category, status, sitemap_url FROM pages"
            " WHERE path='/guide/advanced'"
        ).fetchone()
        self.assertEqual(row["category"], "guides")
        self.assertEqual(row["status"], "pending")
        # Its provenance stays visible: no inventory entry point listed it.
        self.assertIsNone(row["sitemap_url"])

    def test_the_address_is_rebuilt_not_copied_from_the_link(self):
        """The address inside a link carries someone else's baggage and cannot
        be stored as the page address.

        In-body links are commonly written with a version parameter of their
        own, or a fragment. Stored verbatim into a library at a different
        version, what gets cited back is an address for the wrong one.
        """
        connection = self._library({"/guide/advanced": 1})
        connection.execute(
            "UPDATE page_links SET target_url=? WHERE target_path='/guide/advanced'",
            ("https://example.invalid/guide/advanced?v=0.1#section",),
        )
        connection.commit()
        with using(source=self.ToySource) as workspace:
            coverage.admit_linked_targets(connection)
            expected = f"https://example.invalid/guide/advanced?v={workspace.version}"
        self.assertEqual(
            connection.execute(
                "SELECT url FROM pages WHERE path='/guide/advanced'"
            ).fetchone()["url"],
            expected,
        )

    def test_a_page_outside_the_declared_scope_needs_the_dataset_to_say_so(self):
        """For a path the adapter does not recognise, whether to collect it is
        the dataset's call, not the core's."""
        connection = self._library({"/manual/glossary": 3})
        with using(source=self.ToySource, dataset={"inventory": {}}):
            silent = coverage.admit_linked_targets(connection, dry_run=True)
        self.assertEqual(silent["admitted"], 0)
        self.assertEqual(silent["outside_scope"], 1)
        with using(
            source=self.ToySource,
            dataset={"inventory": {"referenced_category": "referenced"}},
        ):
            opted_in = coverage.admit_linked_targets(connection)
        self.assertEqual(opted_in["admitted"], 1)
        self.assertEqual(opted_in["by_category"], {"referenced": 1})

    def test_the_closure_only_walks_one_hop(self):
        """Pages collected this way are pending, and their own links do not
        expand in turn.

        Another hop out has to be a deliberate decision, not a silent snowball.
        """
        connection = self._library({"/guide/advanced": 1})
        with using(source=self.ToySource):
            coverage.admit_linked_targets(connection)
            # The new page links out too, but it is unfetched and so is not a
            # starting point.
            new_page = connection.execute(
                "SELECT id FROM pages WHERE path='/guide/advanced'"
            ).fetchone()["id"]
            connection.execute(
                "INSERT INTO page_links(from_page_id, target_url, target_path,"
                " anchor_text, link_kind, evidence_kind, source_url, created_at)"
                " VALUES(?, 'https://example.invalid/guide/deeper', '/guide/deeper',"
                " 'x', 'reference', 'official_link', 'u', 'now')",
                (new_page,),
            )
            connection.commit()
            second = coverage.admit_linked_targets(connection, dry_run=True)
        self.assertEqual(second["candidates"], 0)

    def test_min_links_and_limit_bound_a_round(self):
        connection = self._library({"/guide/a": 3, "/guide/b": 1})
        with using(source=self.ToySource):
            self.assertEqual(
                coverage.admit_linked_targets(
                    connection, min_links=2, dry_run=True
                )["admitted"],
                1,
            )
            self.assertEqual(
                coverage.admit_linked_targets(connection, limit=1, dry_run=True)[
                    "admitted"
                ],
                1,
            )

    def test_a_dataset_that_declares_nothing_admits_nothing(self):
        """A dataset with neither categorize_path nor a scope policy is left
        entirely alone."""

        class Bare:
            @staticmethod
            def normalize_link_target(dataset, target_url):
                return None

        connection = self._library({"/guide/advanced": 1})
        with using(source=Bare, dataset={"inventory": {}}):
            outcome = coverage.admit_linked_targets(connection)
        self.assertEqual(outcome["admitted"], 0)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM pages WHERE path='/guide/advanced'"
            ).fetchone()[0],
            0,
        )

    def test_stored_links_are_rejudged_when_the_rule_changes(self):
        connection = self._library({})
        connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " anchor_text, link_kind, evidence_kind, source_url, created_at)"
            " VALUES(1, 'https://example.invalid/guide/late', NULL, 'a',"
            " 'reference', 'external_link', 'u', 'now')"
        )
        # A library built before the rule changed: the stamp is still the old one.
        connection.execute("DELETE FROM metadata WHERE key='link_targets'")
        connection.commit()
        with using(source=self.ToySource):
            self.assertEqual(coverage.reclassify_links(connection), 1)
            # Once the stamp is written, nothing is reclassified again.
            self.assertEqual(coverage.reclassify_links(connection), 0)
        row = connection.execute(
            "SELECT target_path, evidence_kind FROM page_links"
            " WHERE target_url LIKE '%/guide/late'"
        ).fetchone()
        self.assertEqual(row["target_path"], "/guide/late")
        self.assertEqual(row["evidence_kind"], "official_link")


class OpeningTheLibraryIsCheapAndReadOnly(unittest.TestCase):
    """Opening the library happens on every query, so it can be neither slow
    nor a write."""

    def setUp(self):
        self.connection = temp_db(self)

    def _statements(self):
        """Open the library again, recording every statement it issues."""
        seen: list[str] = []
        self.connection.set_trace_callback(
            lambda sql: seen.append(sql.strip().split()[0].upper())
        )
        try:
            initialize_db(self.connection)
        finally:
            self.connection.set_trace_callback(None)
        return seen

    def test_reopening_an_unchanged_library_writes_nothing(self):
        """A read-only query must not take a write lock.

        Rewriting nine metadata rows and committing on every open makes a
        read-only file unqueryable, and makes two processes wait on each other
        when querying while crawling.
        """
        written = [s for s in self._statements() if s in {"INSERT", "UPDATE", "DELETE"}]
        self.assertEqual(written, [], f"opening wrote to the database: {written}")

    def test_pending_metadata_lookup_uses_the_partial_index(self):
        """Finding pages that still lack derived metadata uses the index, never
        a full scan.

        Without the partial index, a library of 200k pages scans the whole pages
        table on every query — 0.105 seconds measured, the same order as the
        query itself.
        """
        plan = " ".join(
            row[3]
            for row in self.connection.execute(
                "EXPLAIN QUERY PLAN SELECT id, path FROM pages WHERE "
                + db.PENDING_METADATA_CONDITION
            )
        )
        self.assertIn("idx_pages_metadata_pending", plan)

    def test_backfill_still_catches_pages_added_later(self):
        """With the index in place, pages inserted later are still filled in.

        The regression a performance fix invites is "faster, but it missed some".
        """
        self.connection.execute(
            "INSERT INTO pages(url, path, category, status)"
            " VALUES('https://example.invalid/late', '/late', 'guides', 'pending')"
        )
        initialize_db(self.connection)
        row = self.connection.execute(
            "SELECT route_depth, doc_version, normalized_slug FROM pages"
            " WHERE path='/late'"
        ).fetchone()
        self.assertIsNotNone(row["route_depth"])
        self.assertIsNotNone(row["doc_version"])
        self.assertEqual(row["normalized_slug"], "late")


class DatasetNamesNeverReachSqlUnescaped(unittest.TestCase):
    """The ordering fragment is interpolated into SQL, and the names come from
    a toml somebody else wrote."""

    def test_quotes_in_a_category_name_cannot_break_out(self):
        evil = "x' THEN 1 END); DROP TABLE pages; --"
        clause = runtime.sql_priority_case("category", {evil: 1, "guides": 2})
        connection = temp_db(self)
        connection.execute(
            "INSERT INTO pages(url, path, category, status)"
            " VALUES('https://example.invalid/a', '/a', 'guides', 'pending')"
        )
        # Run it inside a real query: the syntax must be valid and the DROP
        # must not execute.
        rows = list(connection.execute(f"SELECT id FROM pages ORDER BY {clause}"))
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'"
            ).fetchone()
        )

    def test_empty_priority_is_a_constant(self):
        self.assertEqual(runtime.sql_priority_case("category", {}), "0")


class KnowledgeIdParsing(unittest.TestCase):
    """One rule for how a K number is written, shared by the CLI, MCP and
    related."""

    def test_accepts_both_written_forms(self):
        self.assertEqual(search.knowledge_id("K9290"), 9290)
        self.assertEqual(search.knowledge_id("k9290"), 9290)
        self.assertEqual(search.knowledge_id("9290"), 9290)
        self.assertEqual(search.knowledge_id("  K9290  "), 9290)

    def test_rejects_anything_else_instead_of_raising(self):
        """Unrecognised input returns None and the caller words it, rather than
        raising a ValueError with a traceback."""
        for junk in ("abc", "", "K", "K9a", "垃圾", "-1"):
            self.assertIsNone(search.knowledge_id(junk), junk)


class LinkTargetResolution(unittest.TestCase):
    """Resolving link targets touches only unresolved rows, without missing
    rows that were reclassified."""

    def setUp(self):
        self.connection = temp_db(self)
        for path in ("/a", "/b"):
            self.connection.execute(
                "INSERT INTO pages(url, path, category, status)"
                f" VALUES('https://example.invalid{path}', '{path}',"
                " 'guides', 'success')"
            )

    def _link(self, target_path, target_page_id=None):
        self.connection.execute(
            "INSERT INTO page_links(from_page_id, target_url, target_path,"
            " target_page_id, anchor_text, link_kind, evidence_kind,"
            " source_url, created_at)"
            " VALUES(1, 'https://example.invalid/x', ?, ?, 'x', 'official_reference',"
            " 'official_link', 'https://example.invalid/a', '2026-01-01')",
            (target_path, target_page_id),
        )
        return self.connection.execute(
            "SELECT id FROM page_links ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def test_resolves_rows_that_have_no_page_yet(self):
        link_id = self._link("/b")
        db.resolve_link_targets(self.connection)
        resolved = self.connection.execute(
            "SELECT target_page_id FROM page_links WHERE id=?", (link_id,)
        ).fetchone()["target_page_id"]
        self.assertEqual(
            resolved,
            self.connection.execute(
                "SELECT id FROM pages WHERE path='/b'"
            ).fetchone()["id"],
        )

    def test_reclassifying_a_link_clears_the_stale_page_id(self):
        """After a target path is reclassified, the old page_id is last round's
        verdict and has to be recomputed.

        Left in place, the link points forever at the page decided before the
        reclassification — and resolve_link_targets only looks at
        target_page_id IS NULL, so it cannot notice by itself.
        """
        page_a = self.connection.execute(
            "SELECT id FROM pages WHERE path='/a'"
        ).fetchone()["id"]
        link_id = self._link("/a", page_a)
        with using(source=types.SimpleNamespace(
            normalize_link_target=lambda dataset, url: "/b"
        )):
            self.connection.execute("DELETE FROM metadata WHERE key='link_targets'")
            coverage.reclassify_links(self.connection)
        row = self.connection.execute(
            "SELECT target_path, target_page_id FROM page_links WHERE id=?",
            (link_id,),
        ).fetchone()
        self.assertEqual(row["target_path"], "/b")
        self.assertEqual(
            row["target_page_id"],
            self.connection.execute(
                "SELECT id FROM pages WHERE path='/b'"
            ).fetchone()["id"],
        )


class ImportingThePackageHasNoSideEffects(unittest.TestCase):
    """`import docatlas` reads no configuration and imports no adapter.

    Importing a value from config at package level means one broken dataset
    configuration makes the package itself unimportable — while the entire point
    of listing datasets is to work when a configuration is broken and say which
    one it is.
    """

    def test_package_namespace_stays_lazy(self):
        import docatlas

        self.assertEqual(docatlas.__all__, ["main"])
        self.assertFalse(hasattr(docatlas, "VERSION"))

    def test_importing_it_in_a_fresh_process_loads_nothing_else(self):
        probe = (
            "import sys; import docatlas;"
            "print(sorted(m for m in sys.modules if m.startswith('docatlas')))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env={**os.environ, "DOCATLAS_DATASET": "no-such-dataset"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "['docatlas']")


class ReferencedCategoryTests(unittest.TestCase):
    """The category the reference closure collects into is a category like any
    other, and can be filtered on.

    Two throwaway datasets differing in exactly one thing: whether they declare
    a referenced_category. That is the difference under test, and it belongs to
    no particular site.
    """

    DECLARED = (
        'id="with-ref"\nversion="1"\nsource="example"\n'
        "[categories]\nnodes = \"/docs/nodes\"\n"
        "[category_labels]\nnodes = \"Nodes\"\nreferenced = \"Referenced\"\n"
        '[inventory]\nreferenced_category = "referenced"\n'
    )
    PLAIN = (
        'id="no-ref"\nversion="1"\nsource="example"\n'
        "[categories]\nnodes = \"/docs/nodes\"\n"
        "[category_labels]\nnodes = \"Nodes\"\n"
    )

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp(prefix="docatlas_refcat_"))
        (cls.directory / "with-ref.toml").write_text(cls.DECLARED, encoding="utf-8")
        (cls.directory / "no-ref.toml").write_text(cls.PLAIN, encoding="utf-8")
        cls.declared = dataset.load_dataset("with-ref", cls.directory)
        cls.plain = dataset.load_dataset("no-ref", cls.directory)

    def test_the_enumeration_rules_rightly_leave_it_out(self):
        """`categories` maps a category to a path prefix, and this one has none.

        Merging it in corrupts every prefix-based decision — an empty prefix
        most of all, since `path.startswith("")` is always true and files the
        whole library under it.
        """
        self.assertNotIn("referenced", self.declared.categories)

    def test_the_filterable_set_must_include_it(self):
        self.assertIn("referenced", self.declared.query_categories)
        for key in self.declared.categories:
            self.assertIn(key, self.declared.query_categories)

    def test_a_dataset_not_declaring_it_is_unchanged(self):
        self.assertEqual(self.plain.query_categories, tuple(self.plain.categories))

    def test_mcp_no_longer_rejects_it_as_a_typo(self):
        with using(dataset={"inventory": self.declared.inventory,
                            "categories": self.declared.categories,
                            "category_labels": self.declared.category_labels}) as ws:
            mcpserver._check_category(ws, "referenced")
            with self.assertRaises(mcpserver.ToolError):
                mcpserver._check_category(ws, "no_such_category")

    def test_the_sampling_quota_knows_about_it(self):
        """Pending pages brought in by the closure could never be sampled."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        connection = connect_db(Path(directory.name) / "t.sqlite3")
        initialize_db(connection)
        now = "2026-07-27T00:00:00Z"
        for index, category in enumerate(("nodes", "referenced")):
            connection.execute(
                "INSERT INTO pages(url, path, category, status, attempts,"
                " discovered_at, last_seen_at) VALUES(?, ?, ?, 'pending', 0, ?, ?)",
                (f"https://x/{index}", f"/p{index}", category, now, now),
            )
        connection.commit()
        with using(dataset={"inventory": self.declared.inventory,
                            "categories": self.declared.categories,
                            "category_labels": self.declared.category_labels}):
            quota = crawl.sample_quota(connection, 20)
        self.assertEqual(quota.get("referenced"), 1)
        connection.close()


class HeadingRecognitionTests(unittest.TestCase):
    """A heading that goes unrecognised files its whole body under the
    previous section's name."""

    def test_a_comment_inside_a_fence_is_not_a_heading(self):
        """A shell example otherwise splits into several sections, with a code
        comment sitting in `heading_path`."""
        lines = ["## Real", "```bash", "# Optional: send the hash", "curl -X POST", "```", "## Next"]
        self.assertEqual(chunking.fenced_line_numbers(lines), {2, 3})

    def test_an_unclosed_fence_does_not_count(self):
        """Real documentation does contain these.

        Treating everything from it to the end of the file as code swallows
        every real heading after it.
        """
        self.assertEqual(chunking.fenced_line_numbers(["```", "x = 1", "## Still A Heading"]), set())

    def test_a_heading_crowded_behind_an_image_is_still_found(self):
        """One missing newline in an official Markdown export looks like this."""
        line = "![Clear tool](../assets/Create-Tab-Clear.png) ## Edit tab"
        self.assertEqual(
            chunking.heading_at(line),
            ("![Clear tool](../assets/Create-Tab-Clear.png)", 2, "Edit tab"),
        )

    def test_not_every_hash_in_a_line_is_a_heading(self):
        for line in (
            "        movl    input(%rip), %eax   # eax = input",
            "see [the guide](https://x.dev/g) for more",
            "&#160;! &quot; # $&#160;%",
        ):
            self.assertIsNone(chunking.heading_at(line), line)

    def test_later_sections_no_longer_inherit_the_previous_name(self):
        markdown = "\n".join([
            "## Create tab", "Things you create.",
            "![Clear](../a/Clear.png) ## Edit tab", "Things you edit.",
            "```bash", "# not a heading", "```", "## Publish", "Things you publish.",
        ])
        sections = chunking.split_sections(
            title="Terrain Editor", description="", markdown=markdown,
            source_url="https://x/t", category="guides",
        )
        self.assertEqual(
            [item["title"] for item in sections],
            ["Create tab", "Edit tab", "Publish"],
        )
        # The first half of the line is the previous section's body and does
        # not travel with the heading.
        self.assertIn("Clear.png", sections[0]["body"] if "body" in sections[0] else sections[0]["content_md"])

    def test_sections_inside_a_layout_table_are_restored_as_blocks(self):
        """Some sites wrap a whole section in a `<table>` for layout.

        Flattened as a table row, its heading, paragraphs and code blocks all
        disappear together.
        """
        markdown, _assets = htmlmd.html_to_markdown(
            "<table><tbody><tr><td><h3>Designated initializers</h3>"
            "<p>The syntax forms are known as designated initializers.</p>"
            "<pre>A a{.y = 2};</pre></td><td>(since C++20)</td></tr></tbody></table>"
        )
        self.assertIn("### Designated initializers", markdown.splitlines())
        self.assertIn("(since C++20)", markdown)

    def test_an_ordinary_data_table_is_still_a_table(self):
        markdown, _assets = htmlmd.html_to_markdown(
            "<table><tr><th>Member</th><th>Meaning</th></tr>"
            "<tr><td>size</td><td>element count</td></tr></table>"
        )
        self.assertIn("| Member | Meaning |", markdown)
        self.assertIn("| size | element count |", markdown)


class QualifierMatchTests(unittest.TestCase):
    """A qualifier the user typed is positional information, not decoration."""

    def test_suffix_spellings(self):
        self.assertEqual(
            text.qualifier_suffixes("std::ranges::views::transform"),
            ["ranges::views::transform", "views::transform"],
        )
        self.assertEqual(text.qualifier_suffixes("std::views::transform"), ["views::transform"])

    def test_the_bare_last_segment_is_not_a_suffix(self):
        """A bare last segment is exactly where the ambiguity comes from — many
        things share it — so it is a stage of its own, last."""
        self.assertEqual(text.qualifier_suffixes("std::sort"), [])
        for suffixes in (text.qualifier_suffixes("a::b::c"), text.qualifier_suffixes("x::y")):
            self.assertNotIn("c", suffixes)
            self.assertNotIn("y", suffixes)

    def test_a_full_stop_in_prose_is_not_a_qualifier(self):
        self.assertEqual(text.qualifier_suffixes("how do I do this. thanks"), [])

    def test_a_query_tries_names_from_precise_to_loose(self):
        names = search.query_names("std::views::transform")
        self.assertEqual(names[0], chunking.normalize_name("std::views::transform"))
        self.assertLess(
            names.index(chunking.normalize_name("views::transform")),
            names.index(chunking.normalize_name("transform")),
            "the last segment collides most, so it comes after the suffixes",
        )


if __name__ == "__main__":
    unittest.main()
