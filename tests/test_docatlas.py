"""离线回归测试。

不联网、不碰真实数据库——每个用例自己建一个临时库。
跑法：

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import dataclasses
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config 在导入时就固定路径，所以必须先把数据根指到临时目录，避免动到真实知识库。
# 数据集仍用真实的那一份配置——这样 datasets/*.toml 本身也在测试覆盖范围内。
_TEMP_HOME = tempfile.mkdtemp(prefix="docatlas_test_")
os.environ["DOCATLAS_HOME"] = _TEMP_HOME
os.environ.pop("DOCATLAS_DATASET", None)

from docatlas import (  # noqa: E402
    chunking, config, context, crawl, dataset, db, discover, net, ondemand,
    constants, relations, runtime, search, store, text, validate,
)
from docatlas import mcpserver  # noqa: E402
from docatlas.knowledge import unreal  # noqa: E402
from docatlas.sources import epic_ue  # noqa: E402
from docatlas.db import connect_db, initialize_db  # noqa: E402
from docatlas.documents import transform_document  # noqa: E402


@contextlib.contextmanager
def using(**overrides):
    """临时换掉当前数据集的某几样东西（来源适配器、知识包、配置）。

    以前这件事要挨个改模块级全局变量（`discover.SOURCE`、`db.SOURCE`、
    `search.KNOWLEDGE`…），漏掉一个，测试就悄悄跑在真适配器上——
    ENH-004 的开库 bug 正是这么漏过去的。现在整套一起换，漏不掉。
    """
    base = runtime.active()
    if "dataset" in overrides:
        overrides["dataset"] = dataclasses.replace(
            base.dataset, **overrides.pop("dataset")
        )
    with runtime.use(dataclasses.replace(base, **overrides)) as workspace:
        yield workspace


def temp_db(case: unittest.TestCase) -> sqlite3.Connection:
    """一个建好表的临时库，用例结束时自动关闭并删除。"""
    return temp_library(case)[0]


def temp_library(case: unittest.TestCase) -> tuple[sqlite3.Connection, Path]:
    """一个建好表的临时数据集目录，返回 (连接, 数据目录)。

    库文件必须叫 knowledge.sqlite3：Workspace 就是按这个名字找库的，
    随便起个名字会让"这个数据集还没建过"的判断一直成立。
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
    """在库里放一个实体（连同它必须依附的页面），返回 entity id。

    实体挂在页面上是 schema 的硬要求（外键），所以测试也得照做——
    绕过它建出来的库和真实的库不是一回事，测出来的结论也就不算数。
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
    """够用的 sqlite3.Row 替身：transform_document 只做下标访问。"""


class ChunkingTests(unittest.TestCase):
    def test_normalize_name_ignores_case_and_punctuation(self):
        self.assertEqual(
            chunking.normalize_name("Set Timer by Function Name"),
            chunking.normalize_name("settimerbyfunctionname"),
        )
        self.assertEqual(
            chunking.normalize_name("UKismetSystemLibrary::K2_SetTimer"),
            "ukismetsystemlibraryk2settimer",
        )

    def test_humanize_cpp_identifier(self):
        self.assertEqual(
            chunking.humanize_cpp_identifier("UKismetSystemLibrary::K2_SetTimer"),
            "K2 Set Timer",
        )

    def test_chunks_never_exceed_hard_token_limit(self):
        # 一段远超上限的连续正文，必须被切成多块且每块都在限额内。
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

    def test_entity_aliases_cover_k2_and_unreal_prefixes(self):
        descriptor = chunking_entity(
            title="UKismetSystemLibrary::K2_SetTimer",
            path="/documentation/unreal-engine/API/Runtime/Engine/UKismetSystemLibrary/K2_SetTimer",
            category="cpp_api",
        )
        aliases = {alias for alias, _kind in descriptor["aliases"]}
        self.assertIn("K2_SetTimer", aliases)
        self.assertIn("Set Timer", aliases)


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


class SearchQueryTests(unittest.TestCase):
    def test_expression_ladder_goes_precise_to_loose(self):
        stages = [stage for stage, _ in search.fts_expressions("set timer by name")]
        self.assertEqual(stages[0], "phrase")
        self.assertIn("all_terms", stages)
        self.assertIn("any_term", stages)
        self.assertEqual(stages[-1], "prefix")

    def test_stopwords_dropped_only_in_loose_stages(self):
        expressions = dict(search.fts_expressions("how do I use nanite tessellation"))
        self.assertIn('"how"', expressions["all_terms"])
        self.assertNotIn('"how"', expressions["any_term"])
        self.assertIn('"nanite"', expressions["any_term"])

    def test_single_token_has_no_phrase_stage(self):
        stages = [stage for stage, _ in search.fts_expressions("nanite")]
        self.assertNotIn("phrase", stages)

    def test_empty_query_yields_nothing(self):
        self.assertEqual(search.fts_expressions("   "), [])

    def test_knowledge_type_ranking_prefers_answers_over_indexes(self):
        self.assertGreater(
            search.API_TYPE_BONUS["parameters"],
            search.API_TYPE_BONUS["navigation"],
        )

    def test_identifier_queries_are_treated_as_api_lookups(self):
        for query in ("K2_SetTimer", "UKismetSystemLibrary::K2_SetTimer", "AActor"):
            self.assertEqual(
                search.query_profile(query, entity_hit=False), "api", query
            )

    def test_plain_words_are_treated_as_concept_questions(self):
        for query in ("nanite", "how do I set up virtual shadow maps"):
            self.assertEqual(
                search.query_profile(query, entity_hit=False), "concept", query
            )

    def test_entity_hit_forces_api_profile(self):
        self.assertEqual(search.query_profile("nanite", entity_hit=True), "api")

    def test_member_listings_are_pushed_back_only_for_concept_questions(self):
        # 大段成员罗列回答不了"这是什么"，但问一个具体符号时，答案往往**就在**
        # 那张表里：官方不给属性单独出页面，`TargetArmLength` 只记在所属类的
        # 成员表中。一律压后就等于把唯一的官方定义压掉。
        row = {
            "knowledge_type": "details",
            "category": (config.DATASET.verbose_categories or ("cpp_api",))[0],
            "token_estimate": 600,
            "quality_score": 1.0,
            "page_title": "USpringArmComponent",
            "heading_path": "USpringArmComponent > Variables",
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
        # API 提问反过来：返回值比泛泛的概览更有用。
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
        # 一次限流事件里所有在途请求都会失败；速率只应该降一档。
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

        # 冷却结束后又被拒 → 说明还是太快，停得更久一点。
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
            self.limiter.cooldown_until = 0.0  # 模拟多个独立的限流事件
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
    """按需抓取的定位逻辑：能不能从"还没抓的清单"里认出用户要的那一页。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        initialize_db(self.connection)
        self.connection.execute(
            "INSERT INTO sitemaps(url, category, status) "
            "VALUES('https://example.invalid/s.xml', 'guides', 'success')"
        )
        for path, category in [
            ("/documentation/unreal-engine/API/Runtime/Engine/ACharacter/GetCharacterMovement", "cpp_api"),
            ("/documentation/unreal-engine/BlueprintAPI/Utilities/Time/SetTimerbyFunctionName", "blueprint_api"),
            ("/documentation/unreal-engine/nanite-virtualized-geometry-in-unreal-engine", "guides"),
        ]:
            self.connection.execute(
                "INSERT INTO pages(url, path, category, sitemap_url, route_depth) "
                "VALUES(?, ?, ?, 'https://example.invalid/s.xml', 3)",
                (f"https://example.invalid{path}", path, category),
            )
        self.connection.commit()
        initialize_db(self.connection)  # 触发 normalized_slug 回填

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
        self.assertIn("getcharactermovement", slugs)

    def test_finds_page_by_official_identifier(self):
        rows = self.find("GetCharacterMovement")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "cpp_api")

    def test_finds_page_by_human_phrasing(self):
        # 用户说的是带空格的显示名，URL 里是连在一起的。
        rows = self.find("Set Timer by Function Name")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "blueprint_api")

    def test_partial_word_finds_longer_slug(self):
        rows = self.find("nanite")
        self.assertTrue(rows)
        self.assertIn("nanite", rows[0]["path"])

    def test_exact_only_skips_partial_matches(self):
        self.assertEqual(self.find("nanite", exact_only=True), [])

    def test_short_query_does_not_scoop_up_everything(self):
        self.assertEqual(self.find("api"), [])

    def test_category_filter_is_respected(self):
        self.assertEqual(self.find("GetCharacterMovement", category="guides"), [])

    def test_missing_exact_pages_counts_only_uncrawled(self):
        self.assertEqual(
            ondemand.missing_exact_pages(self.connection, "GetCharacterMovement"), 1
        )
        self.connection.execute(
            "UPDATE pages SET status='success' WHERE normalized_slug=?",
            ("getcharactermovement",),
        )
        self.assertEqual(
            ondemand.missing_exact_pages(self.connection, "GetCharacterMovement"), 0
        )

    def test_unknown_name_finds_nothing(self):
        self.assertEqual(self.find("zzzznotarealsymbol"), [])

    def test_fetch_now_on_empty_list_is_a_noop(self):
        outcome = ondemand.fetch_now(self.connection, [])
        self.assertEqual(outcome["requested"], 0)
        self.assertEqual(outcome["succeeded"], 0)


class RedirectHandlingTests(unittest.TestCase):
    """Epic 的文档接口用 302 + 空 Location + 正文 redirect_url 表示"页面搬家"。"""

    def _run(self, response):
        connection = FakeConnection(response)
        original = net._connection
        net._connection = lambda scheme, host, timeout: connection
        try:
            return net._request_once("https://dev.epicgames.invalid/x", 10)
        finally:
            net._connection = original

    def test_302_without_location_returns_body(self):
        payload = b'{"redirect_url":"https://dev.epicgames.invalid/moved"}'
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
        block = {"type": "code", "code": "TMap<FName, TSubclassOf<AActor>> Map;"}
        self.assertIn("TMap<FName, TSubclassOf<AActor>> Map;", collect_strings(block))

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
    """建一个小库，走完 抓取结果 → 落库 → 检索 → 上下文包 全流程。"""

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
            "/documentation/unreal-engine/BlueprintAPI/Utilities/Time/SetTimerbyFunctionName",
            "blueprint_api",
            "Set Timer by Function Name",
            [
                {"type": "heading", "level": 2, "text": "Inputs"},
                text_block("Function Name — delegate function name."),
                {"type": "heading", "level": 2, "text": "Outputs"},
                text_block("Return Value — the timer handle."),
            ],
        )
        self.add_page(
            "/documentation/unreal-engine/nanite-overview",
            "guides",
            "Nanite Virtualized Geometry",
            [
                text_block("Nanite is a virtualized micropolygon geometry system."),
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
            self.connection, "what does nanite require", limit=5
        )
        self.assertTrue(results, "松散提问也应该有结果")
        self.assertTrue(
            any("Nanite" in row["page_title"] for row in results)
        )

    def test_every_chunk_carries_its_source_url(self):
        self.seed()
        rows = self.connection.execute(
            "SELECT source_anchor, content_md FROM chunks"
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["source_anchor"].startswith("https://"))
            self.assertIn("DOC 原出处", row["content_md"])

    def test_context_pack_respects_token_budget(self):
        self.seed()
        for budget in (50, 200, 1000, 5000):
            pack = context.build_context_pack(
                self.connection, "Nanite", token_budget=budget, category=None
            )
            self.assertLessEqual(
                pack["estimated_tokens"],
                budget,
                f"预算 {budget} 被突破了",
            )

    def test_context_pack_caps_chunks_per_page(self):
        self.seed()
        pack = context.build_context_pack(
            self.connection, "Nanite", token_budget=100000, category=None
        )
        per_page: dict[int, int] = {}
        for item in pack["primary_knowledge"]:
            per_page[item["page_id"]] = per_page.get(item["page_id"], 0) + 1
        for count in per_page.values():
            self.assertLessEqual(count, context.MAX_CHUNKS_PER_PAGE)

    def test_context_markdown_is_cheaper_than_json(self):
        self.seed()
        pack = context.build_context_pack(
            self.connection, "Nanite", token_budget=3000, category=None
        )
        markdown = context.render_context_markdown(pack)
        as_json = json.dumps(pack, ensure_ascii=False, indent=2)
        self.assertLess(len(markdown), len(as_json))
        self.assertIn("DOC 原出处", markdown)

    def test_empty_result_renders_a_useful_message(self):
        self.seed()
        pack = context.build_context_pack(
            self.connection, "zzzznotarealthing", token_budget=1000, category=None
        )
        markdown = context.render_context_markdown(pack)
        self.assertIn("没有命中", markdown)


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
    """小节合并：解决"一页被切成几个二十字碎块"的问题。"""

    def chunk(self, sections):
        return chunking.chunk_sections(
            sections, page_title="Page", category="blueprint_api", document_type=None
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
        # 合并不等于抹掉子标题——读的人还得看得出哪段是输入、哪段是输出。
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
        # Epic 蓝图页的 `Navigation` 名不副实：面包屑之外还装着节点描述和
        # `Target is X`，是全页最有用的信息，不能丢。但也不能让它给整块
        # 贴上"导航"标签——那会让这块在检索里被一路扣分。
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
        # 一段刚好切成"一大块 + 一小截"，小截必须并回去。
        body = ("paragraph body text. " * 120).strip() + "\n\nshort tail."
        sections = [make_section("Body", body, position=0)]
        chunks = self.chunk(sections)
        if len(chunks) > 1:
            self.assertGreaterEqual(chunks[-1]["token_estimate"], 50)
        self.assertIn("short tail.", chunks[-1]["content_md"])

    def test_merged_chunk_is_attributed_to_the_first_section(self):
        # chunks.section_id 非空，合并块必须挂在组里第一个小节上。
        sections = [
            make_section("Inputs", "a", position=3, knowledge_type="parameters"),
            make_section("Outputs", "b", position=4, knowledge_type="returns"),
        ]
        self.assertEqual(self.chunk(sections)[0]["section_position"], 3)


class ImportSmokeTests(unittest.TestCase):
    """每个模块都要能导入。

    看着像废话，但它抓过一个真实的错：cli.py 里写坏了一个 f-string，
    69 个用例全过——因为没有一个用例 import 过 cli。
    语法错误不该等到用户敲命令时才发现。
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
            except Exception as exc:  # noqa: BLE001 —— 就是要抓全部
                failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_cli_parser_builds_and_every_command_has_a_handler(self):
        from docatlas.cli import build_parser

        parser = build_parser()
        actions = [
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        ]
        self.assertTrue(actions, "命令行应该有子命令")
        for name, sub in actions[0].choices.items():
            self.assertTrue(
                sub.get_default("func"), f"子命令 {name} 没有绑定实现"
            )


class EvidenceCoverageTests(unittest.TestCase):
    """守住一条教训：加工规则一改，可能把某类关系整类做没。

    只看"跑完没报错"发现不了——所有健康检查都会通过，只是某类证据静静地
    变成 0 条。所以验收要盯每类证据的产出量。
    """

    def test_expected_kinds_cover_generic_plus_domain(self):
        kinds = validate.expected_evidence_kinds()
        self.assertIn("official_link", kinds)  # 任何文档站都有
        for kind in unreal.DERIVED_EVIDENCE_KINDS:
            self.assertIn(kind, kinds, "领域知识包声明会推出的证据必须被验收覆盖")

    def test_domain_kinds_match_what_the_rules_actually_emit(self):
        # 声明了却没人生产，等于验收永远失败；生产了却没声明，等于漏检。
        source = Path(unreal.__file__).read_text(encoding="utf-8")
        emitted = set(re.findall(r"""evidence_kind=["']([^"']+)["']""", source))
        self.assertEqual(
            emitted,
            set(unreal.DERIVED_EVIDENCE_KINDS),
            "DERIVED_EVIDENCE_KINDS 必须和规则实际产出的证据类型完全一致",
        )


class FetchedLanguageTests(unittest.TestCase):
    """数据集里的 language 是"去要哪一版"的指令，不是站点的事实，所以没法自动填。

    但"要的和给的是不是一回事"能自动查：站点没有你要的语言时多半不报错，
    只不声不响回默认语言，于是你得到一个标着德语的英文库。
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
        self.assertEqual(counts["en-us"], 2)  # 大小写不该算成两种
        self.assertEqual(counts["de-de"], 1)

    def test_a_silently_substituted_language_is_visible(self):
        # 这条是关键：要了德语、拿回英语，必须看得出来，不能悄悄过去。
        counts = validate.fetched_locales(self._connection(["en-us"] * 5))
        wrong = sum(n for code, n in counts.items() if code != "de-de")
        self.assertEqual(wrong, 5)

    def test_corrupt_archives_do_not_crash_the_check(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE raw_documents(raw_json BLOB)")
        connection.execute("INSERT INTO raw_documents VALUES(?)", (b"not zlib",))
        self.assertEqual(validate.fetched_locales(connection), collections.Counter())

    def test_adapter_reports_nothing_when_the_site_says_nothing(self):
        self.assertIsNone(epic_ue.document_locale({"title": "x"}))


class SkillTemplateTests(unittest.TestCase):
    """技能文档是 AI 的操作手册，写错了不会报错，只会让 AI 照着做错事。"""

    SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "docatlas"

    def _docs(self):
        files = sorted(self.SKILL_DIR.glob("*.md"))
        self.assertTrue(files, "技能目录里一个 .md 都没有")
        return {path.name: path.read_text(encoding="utf-8") for path in files}

    def test_every_placeholder_has_a_filler(self):
        # 漏填不报错，只会让 AI 读到字面的 {{...}} 然后照着找不存在的东西。
        from docatlas.cli import skill_substitutions

        fillers = skill_substitutions()
        found = False
        for name, text in self._docs().items():
            for placeholder in set(re.findall(r"\{\{([A-Z_]+)\}\}", text)):
                found = True
                self.assertIn(
                    placeholder, fillers, f"{name} 里的 {placeholder} 没人认识"
                )
        self.assertTrue(found, "占位符一个都没有？那模板机制已经失效了")

    def test_fillers_are_not_empty(self):
        # 填成空字符串比不填还糟：AI 读到的是一句缺了主语的话。
        from docatlas.cli import skill_substitutions

        for name, value in skill_substitutions().items():
            self.assertTrue(value.strip(), f"{name} 填出来是空的")

    def test_skill_does_not_hardcode_the_current_dataset(self):
        """措辞必须通用：装什么库由数据集填，不能写死成当前这一份。

        写死了就等于假定所有人装的都是同一份文档、说同一种语言。
        """
        for placeholder in ("DATASET_NAME", "DATASET_TRIGGERS", "DATASET_LANGUAGE"):
            self.assertIn("{{" + placeholder + "}}", self._docs()["SKILL.md"])
        product = config.DATASET.product
        for name, text in self._docs().items():
            self.assertNotIn(
                product,
                text,
                f"{name} 里写死了当前产品名 {product!r}，换个数据集就不对了",
            )

    def test_skill_points_at_the_build_workflows(self):
        # 不指过去，AI 就只会查、不会建，用户又得自己去碰 TOML。
        self.assertIn("WORKFLOWS.md", self._docs()["SKILL.md"])

    def test_documented_commands_all_exist(self):
        # 手册里写一条不存在的命令，AI 会照着跑然后失败——而且没人会先发现。
        from docatlas.cli import build_parser

        real = set(build_parser()._subparsers._group_actions[0].choices)
        for name, text in self._docs().items():
            for command in set(re.findall(r"python -m docatlas ([a-z-]+)", text)):
                self.assertIn(command, real, f"{name} 写了不存在的命令：{command}")


class TargetTypeResolutionTests(unittest.TestCase):
    """`Target is X` 里的 X 到哪个词为止，只能靠别名表定，不能靠标点。

    合并小节之后，正文里紧跟着 X 的就是下一段内容，中间没有句号也没有换行：
    原先靠"这句正好是小节结尾"收边的写法，一夜之间从 1,336 命中掉到 4。
    """

    def _graph(self):
        connection = temp_db(self)
        seed_entity(connection, entity_type="cpp_symbol", name="AActor")
        seed_entity(connection, entity_type="cpp_symbol", name="UActorComponent",
                    aliases=[("Actor Component", "cpp_humanized_name")])
        connection.commit()
        return relations.RelationGraph(connection)

    def test_name_ends_where_the_known_entities_say_it_does(self):
        # 真实形状：名字后面直接接下一段正文，没有任何标点。
        name, targets = unreal._resolve_target(
            self._graph(), "Actor Component Inputs Type Name Description"
        )
        self.assertEqual(name, "Actor Component")
        self.assertEqual([t.name for t in targets], ["UActorComponent"])

    def test_longer_name_wins_over_its_own_prefix(self):
        # "Actor" 也认得出来。从短往长试的话会停在它，指错实体。
        name, _ = unreal._resolve_target(self._graph(), "Actor Component Inputs")
        self.assertEqual(name, "Actor Component")

    def test_unknown_target_links_to_nothing(self):
        # 绝大多数 Target 指向还没抓的 C++ 页；认不出来就该老实不连。
        name, targets = unreal._resolve_target(
            self._graph(), "Gameplay Ability Blueprint Library Inputs"
        )
        self.assertEqual((name, targets), ("", []))

    def test_prose_containing_the_words_is_not_a_declaration(self):
        # "When Flatten Target is enabled..." 不是声明，别硬连。
        match = unreal.TARGET_IS_PATTERN.search(
            "When Flatten Target is enabled you can show a preview grid"
        )
        self.assertIsNotNone(match)
        self.assertEqual(unreal._resolve_target(self._graph(), match.group(1))[1], [])


class McpProtocolTests(unittest.TestCase):
    """MCP 是手写的（不引第三方 SDK），协议层必须有测试守着。"""

    def test_handshake_echoes_the_client_protocol_version(self):
        # 硬报一个版本会让老客户端直接拒绝握手。
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        })
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", reply["result"]["capabilities"])

    def test_notifications_get_no_reply(self):
        # 通知没有 id，回一个响应会让客户端报协议错误。
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

    def test_unknown_tool_is_a_protocol_error(self):
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        self.assertEqual(reply["error"]["code"], -32602)

    def test_tool_failure_is_reported_not_crashed(self):
        # 工具抛异常必须变成 isError 结果，不能把整个连接搞崩。
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
        # 调用方能改的错要走 isError 结果，而不是 JSON-RPC 协议错误：
        # 协议错误是"这个请求不合法"，这里是"请求合法但参数得改"。
        reply = mcpserver.handle({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {
                "name": "docatlas_show",
                "arguments": {"chunk_id": "; DROP TABLE"},
            },
        })
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("看不懂", reply["result"]["content"][0]["text"])
        self.assertNotIn("Traceback", reply["result"]["content"][0]["text"])

    def test_an_unknown_dataset_id_does_not_kill_the_server(self):
        """load_dataset 用 SystemExit 报错，普通 except Exception 抓不到它。

        不接住的话，一个拼错的 dataset_id 会让整个 MCP 进程直接退出。
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
        """分类 enum 写进协议，换个数据集就成了骗人的。

        客户端会缓存 tools/list，enum 里列着另一个库的分类比不列更糟。
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
    """核心不该认识 Epic 或 Unreal——这些用例守住那条界线。"""

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
        # 不一致会让数据目录和配置对不上，宁可拒绝启动也不要静默用错目录。
        directory = self.write_dataset("a", 'id="b"\nversion="1"\nsource="x"')
        with self.assertRaises(SystemExit) as caught:
            dataset.load_dataset("a", directory)
        self.assertIn("不一致", str(caught.exception))

    def test_unknown_adapter_is_a_clear_error(self):
        directory = self.write_dataset(
            "d", 'id="d"\nversion="1"\nsource="no_such_site"'
        )
        loaded = dataset.load_dataset("d", directory)
        with self.assertRaises(SystemExit) as caught:
            dataset.load_source(loaded)
        self.assertIn("epic_ue", str(caught.exception))

    def test_knowledge_pack_is_optional(self):
        directory = self.write_dataset("d", 'id="d"\nversion="1"\nsource="epic_ue"')
        loaded = dataset.load_dataset("d", directory)
        self.assertIsNone(dataset.load_knowledge(loaded))
        # 没挂知识包时，取任何能力都应安静地拿到默认值，而不是崩溃。
        self.assertEqual(dataset.knowledge_hook(None, "build_relations", None), None)
        self.assertEqual(dataset.knowledge_hook(None, "RELATION_LABELS", {}), {})

    def test_adapter_urls_follow_the_dataset_version(self):
        # "加一个版本 = 改配置"的核心保证：换 version 就换出正确的地址。
        directory = self.write_dataset(
            "d",
            'id="d"\nversion="9.9"\nsource="epic_ue"\nlanguage="en-US"\n'
            '[source_options]\nbase_url="https://example.test"\n'
            'document_api="https://example.test/api"\n'
            'doc_prefix="/documentation/unreal-engine/"\n',
        )
        loaded = dataset.load_dataset("d", directory)
        adapter = dataset.load_source(loaded)
        self.assertIn("9.9", adapter.canonical_url(loaded, "/documentation/unreal-engine/x"))
        self.assertTrue(
            adapter.canonical_url(loaded, "/documentation/unreal-engine/x").startswith(
                "https://example.test/"
            )
        )
        self.assertIn("application_version=9.9", adapter.document_request_url(loaded, "/x"))

    def test_adapter_rejects_other_hosts_and_languages(self):
        loaded = config.DATASET
        adapter = config.SOURCE
        self.assertIsNone(adapter.normalize_link_target(loaded, "https://example.com/x"))
        self.assertIsNone(
            adapter.normalize_location(
                loaded, "https://dev.epicgames.com/documentation/unreal-engine/x?lang=zh-CN"
            )
        )
        # 语言前缀要剥掉，否则同一篇文档会被当成两页。
        path, _ = adapter.normalize_location(
            loaded, "https://dev.epicgames.com/documentation/zh-cn/unreal-engine/x"
        )
        self.assertEqual(path, "/documentation/unreal-engine/x")


class RuntimeWorkspaceTests(unittest.TestCase):
    """一个进程能同时握住多个数据集——MCP 靠这个在一个连接里服务多个库。"""

    def test_switching_workspace_switches_database_and_adapter(self):
        other = dataclasses.replace(
            runtime.active(),
            dataset=dataclasses.replace(
                runtime.active().dataset, id="other-lib", name="另一个库"
            ),
            data_dir=Path(tempfile.mkdtemp(prefix="docatlas_ws_")),
        )
        default_db = runtime.active().db_path
        with runtime.use(other) as switched:
            self.assertEqual(switched.id, "other-lib")
            self.assertNotEqual(switched.db_path, default_db)
            # 派生配置也得跟着换，否则换了库还在用上一个库的分类和标签。
            self.assertEqual(db.connect_db.__module__, "docatlas.db")
            self.assertEqual(runtime.active().name, "另一个库")
        self.assertEqual(runtime.active().db_path, default_db)

    def test_workspace_is_restored_even_when_the_body_raises(self):
        before = runtime.active().id
        with self.assertRaises(ZeroDivisionError):
            with runtime.use(dataclasses.replace(runtime.active())):
                raise ZeroDivisionError
        self.assertEqual(runtime.active().id, before)

    def test_worker_threads_inherit_the_active_dataset(self):
        """抓页面全在线程池里干，不显式传下去就会用错适配器。

        `contextvars` 不跨线程：worker 拿到的是空上下文，`active()` 会退回
        进程默认数据集。真实后果是拿 A 站的适配器解析 B 站的页面、写进 B 的
        库，全程不报错。所以线程池必须提交 `bind(fn)`。
        """
        import concurrent.futures

        with using(dataset={"id": "in-thread"}):
            bound = runtime.bind(lambda: runtime.active().id)
            naked = lambda: runtime.active().id  # noqa: E731
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                self.assertEqual(executor.submit(bound).result(), "in-thread")
                # 反向证明这个测试真的能抓到问题：不 bind 就退回默认数据集。
                self.assertEqual(
                    executor.submit(naked).result(), runtime.DEFAULT_DATASET_ID
                )

    def test_every_thread_pool_submits_a_bound_callable(self):
        """新写的线程池不 bind 就会重蹈覆辙，所以这条界线由测试守着。"""
        offenders = []
        for path in Path("docatlas").glob("*.py"):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "executor.submit(" in line or "executor.map(" in line:
                    if "bind(" not in line:
                        offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "线程池任务必须用 runtime.bind() 包一层")


class UnrealKnowledgeTests(unittest.TestCase):
    def test_k2_prefix_is_stripped_into_searchable_aliases(self):
        aliases = dict(
            (alias_type, alias)
            for alias, alias_type in unreal.extra_entity_aliases(
                title="UKismetSystemLibrary::K2_SetTimer",
                category="cpp_api",
                segments=["api", "Runtime", "Engine"],
            )
        )
        self.assertEqual(aliases["k2_base_name"], "SetTimer")
        self.assertEqual(aliases["k2_humanized_name"], "Set Timer")

    def test_type_prefix_stripped_only_on_class_pages(self):
        class_page = dict(
            (t, a)
            for a, t in unreal.extra_entity_aliases(
                title="AActor", category="cpp_api", segments=["api"]
            )
        )
        self.assertEqual(class_page["unreal_prefix_stripped"], "Actor")
        # 成员页的首字母大写不是类型前缀，不能脱。
        member_page = dict(
            (t, a)
            for a, t in unreal.extra_entity_aliases(
                title="AActor::Tick", category="cpp_api", segments=["api"]
            )
        )
        self.assertNotIn("unreal_prefix_stripped", member_page)

    def test_non_cpp_categories_get_no_unreal_aliases(self):
        self.assertEqual(
            unreal.extra_entity_aliases(
                title="Nanite", category="guides", segments=["nanite"]
            ),
            set(),
        )


class HeadingAnchorTests(unittest.TestCase):
    """标题里的链接目标是给浏览器的，不是标题文字。

    不剥掉它，锚点会把整条 URL 拼进 fragment，生成一个官方页面里根本不存在
    的地址——正文照样能读，但引用点过去落不到那一节。
    """

    def test_link_target_never_leaks_into_the_anchor(self):
        anchor = text.heading_anchor(
            "[Constrained algorithms](https://en.cppreference.com/cpp/algorithm/ranges)"
            " (since C++20)"
        )
        self.assertNotIn("http", anchor)
        self.assertNotIn("cppreference", anchor)
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
    """用户抄官方写法时常连命名空间一起抄，页面地址却只有末尾那个名字。"""

    def test_namespace_is_stripped_to_the_last_segment(self):
        self.assertEqual(text.qualifier_tail("std::from_chars"), "from_chars")
        self.assertEqual(text.qualifier_tail("math.floor"), "floor")

    def test_unqualified_or_too_short_names_add_nothing(self):
        self.assertEqual(text.qualifier_tail("Nanite"), "")
        self.assertEqual(text.qualifier_tail("std::x"), "")

    def test_query_names_start_with_the_query_itself(self):
        names = search.query_names("std::from_chars")
        self.assertEqual(names[0], chunking.normalize_name("std::from_chars"))
        self.assertIn("fromchars", names)

    def test_query_names_are_deduplicated(self):
        names = search.query_names("Nanite")
        self.assertEqual(len(names), len(set(names)))

    def test_accessor_prefix_leads_back_to_the_property(self):
        # 官方不给 BlueprintReadWrite 属性的 Setter 单独出页面，
        # 所以按 Setter 名字搜时得顺带按属性本名再找一次。
        self.assertIn("TargetArmLength", unreal.query_aliases("SetTargetArmLength"))
        self.assertIn("targetarmlength", search.query_names("Set Target Arm Length"))

    def test_k2_prefix_is_tried_for_identifier_shaped_queries(self):
        self.assertIn("K2_SetTimer", unreal.query_aliases("SetTimer"))
        # 整句话前面加 K2_ 没有意义。
        self.assertNotIn(
            "K2_Set Timer by Function Name",
            unreal.query_aliases("Set Timer by Function Name"),
        )


class PageSlugTests(unittest.TestCase):
    """静态站点把实现细节写进地址（`fields.html`），用户说的是页面名。"""

    def test_document_extension_is_not_part_of_the_name(self):
        self.assertEqual(db.page_slug("/modeling/geometry_nodes/fields.html"), "fields")
        self.assertEqual(db.page_slug("/a/b/index.php"), "index")

    def test_dots_inside_real_names_are_kept(self):
        # `UObject.Tick`、`v5.8` 里的点不是扩展名，切掉会把名字弄错。
        self.assertEqual(db.page_slug("/API/UObject.Tick"), "uobjecttick")
        self.assertEqual(db.page_slug("/notes/release-5.8"), "release58")

    def test_slug_rules_are_versioned_so_old_rows_get_recomputed(self):
        # 不重算的话，新规则只对以后发现的页面生效，同一个库里两套 slug 并存。
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
    """列改名只改了 `pages` 表；`metadata`/`tags` 是键值表，改名不能靠
    RENAME COLUMN，得单独迁移，否则老库里旧 key/tag_type 会一直残留。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connection = connect_db(Path(self.directory.name) / "t.sqlite3")
        self.addCleanup(self.connection.close)
        initialize_db(self.connection)

    def _real_chunk_id(self):
        # chunk_tags 对 chunk_id 有外键约束，得挂在一个真实存在的块上。
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
        # 老库先跑过一次旧代码写下 ue_version，再跑新代码又写了 doc_version：
        # 两行同时存在，旧的那行就是死数据。
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
        # 只有站点地图型来源有"总入口"，键名不该把这个假设写死在数据里。
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
        # 挂真实的块，storing 时已经自动打上 (VERSION, 'doc_version') 标签；
        # 手造一个同名的 'ue_version' 旧标签，制造 UNIQUE(name, tag_type) 撞车。
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
    """清单里明明有那一页，却因为写法差一点就找不到——这条路必须走得通。"""

    PAGES = [
        ("/documentation/unreal-engine/BlueprintAPI/Camera/SetFieldOfView", "blueprint_api"),
        ("/modeling/geometry_nodes/fields.html", "guides"),
        ("/render/shader_nodes/textures/wave.html", "guides"),
        ("/cpp/utility/from_chars", "cpp_api"),
        ("/documentation/unreal-engine/nanite-virtualized-geometry", "guides"),
        ("/cpp/chrono/duration/duration_cast", "cpp_api"),
        ("/documentation/unreal-engine/render-target-guide", "guides"),
        ("/documentation/unreal-engine/BlueprintAPI/Camera/SetAspectRatio", "blueprint_api"),
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
        initialize_db(self.connection)  # 触发 normalized_slug 回填

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def find(self, query, **kwargs):
        return ondemand.find_uncrawled_candidates(
            self.connection, query, limit=5, **kwargs
        )

    def test_official_page_name_finds_the_html_page(self):
        # 以前必须输入 "fieldshtml" 才找得到，用户不该知道站点用什么扩展名。
        rows = self.find("Fields")
        self.assertEqual([row["path"] for row in rows], ["/modeling/geometry_nodes/fields.html"])

    def test_qualified_cpp_symbol_finds_the_unqualified_page(self):
        rows = self.find("std::from_chars")
        self.assertEqual([row["path"] for row in rows], ["/cpp/utility/from_chars"])

    def test_full_official_title_is_covered_by_the_path(self):
        rows = self.find("Wave Texture Node")
        self.assertEqual(
            [row["path"] for row in rows], ["/render/shader_nodes/textures/wave.html"]
        )

    def test_concept_questions_do_not_scoop_up_pages(self):
        # 覆盖档要求每个实词都出现，所以泛泛的提问不会触发一堆补抓。
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

    def test_lookup_reports_pages_that_are_already_local(self):
        self.connection.execute(
            "UPDATE pages SET status='success' WHERE normalized_slug='setfieldofview'"
        )
        lookup = ondemand.inventory_lookup(self.connection, "Set Field Of View")
        self.assertEqual(lookup["pending_pages"], [])
        self.assertTrue(lookup["crawled_pages"])

    def test_describe_lookup_gives_a_different_answer_for_each_state(self):
        # 三种"没有"必须给三种下一步，否则调用方只能瞎猜。
        pending = context.describe_lookup(
            ondemand.inventory_lookup(self.connection, "Set Field Of View")
        )
        missing = context.describe_lookup(
            ondemand.inventory_lookup(self.connection, "zzzznotarealpage")
        )
        self.assertIn("get", "\n".join(pending))
        self.assertNotEqual(pending, missing)
        self.assertIn("没有对得上的页面", "\n".join(missing))


    def test_a_symbol_inside_a_longer_question_still_locates_its_page(self):
        """BUG-008 的残余边界：整条查询对不上，但里面某个符号正好是一页的名字。

        `duration_cast milliseconds` 以前一页都找不到——规范化后的
        `durationcast` 拿去比原始路径里的 `duration_cast`，下划线一挡就永远
        对不上；何况 `milliseconds` 压根不在那条路径里，"每个实词都要命中"
        这一档也过不了。
        """
        rows = self.find("duration_cast milliseconds")
        self.assertEqual(
            [row["path"] for row in rows], ["/cpp/chrono/duration/duration_cast"]
        )
        self.assertEqual(rows[0]["match_stage"], "token_exact_slug")

    def test_separators_in_the_path_do_not_block_coverage(self):
        # `render target` 的实词都在 `/…/render-targets-in-unreal` 里，
        # 只是被连字符断开了。拍平之后才对得上。
        rows = self.find("render target guide")
        self.assertIn(
            "/documentation/unreal-engine/render-target-guide",
            [row["path"] for row in rows],
        )

    def test_a_plain_word_alone_never_triggers_a_fetch(self):
        """反向保证：只有符号形状的词够格单独定位。

        `milliseconds` 是普通英文词，单独拿去对 slug 会扫回一大片；
        `duration_cast` 带下划线，基本只可能是那一页。
        """
        self.assertEqual(ondemand.identifier_tokens("milliseconds"), [])
        self.assertEqual(ondemand.identifier_tokens("how do I make an object glow"), [])
        self.assertEqual(
            ondemand.identifier_tokens("duration_cast milliseconds"), ["durationcast"]
        )

    def test_weak_candidates_are_reported_but_never_fetched(self):
        """"线索不够所以没敢抓"和"确实没有这一页"要分开说。"""
        lookup = ondemand.inventory_lookup(self.connection, "camera settings")
        # 不敢抓：没有一页能确定就是它。
        self.assertEqual(lookup["pending_pages"], [])
        # 但也不能说"官方没有"——清单里确实有沾边的。
        self.assertTrue(lookup["weak_candidates"])
        steps = "\n".join(context.describe_lookup(lookup))
        self.assertIn("没有把握", steps)
        self.assertNotIn("官方文档确实没有这一页", steps)
        # 报告归报告，补抓一页都不许多抓。
        self.assertEqual(self.find("camera settings"), [])

    def test_a_truly_absent_name_still_says_the_docs_do_not_have_it(self):
        lookup = ondemand.inventory_lookup(self.connection, "zzzznotarealpage")
        self.assertEqual(lookup["weak_candidates"], [])
        self.assertIn(
            "官方文档确实没有这一页", "\n".join(context.describe_lookup(lookup))
        )


class SampleQuotaTests(unittest.TestCase):
    """`--sample-per-category N` 是每类的上限，不是全局配额。

    某类不足 N 页时，缺额被转到别的类去补，就会让那些类超过 N——
    抽样也就不成其为抽样了。
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
        connection = self._connection(
            {"cpp_api": 9, "blueprint_api": 20, "guides": 100}
        )
        quota = crawl.sample_quota(connection, 20)
        self.assertEqual(quota, {"guides": 20, "blueprint_api": 20, "cpp_api": 9})
        rows = crawl.select_page_batch(
            connection, batch_size=999, refresh=False, sample_per_category=20
        )
        counts = collections.Counter(row["category"] for row in rows)
        self.assertEqual(dict(counts), {"guides": 20, "blueprint_api": 20, "cpp_api": 9})

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
        connection = self._connection({"guides": 100, "cpp_api": 100})
        self.assertEqual(
            crawl.sample_quota(connection, 5, category="cpp_api"), {"cpp_api": 5}
        )


class InventoryValidationTests(unittest.TestCase):
    """空库返回 pass、退出码 0，是最危险的一种"绿"。"""

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
    """站点没有 sitemap 时，适配器换掉两个函数就能列页——核心一行不用改。"""

    class FakeSource:
        """一个只会分页的假站点：两页 API，各带自己的分类。"""

        PAGES = {
            "https://example.invalid/api?page=1": [
                ("guides", "https://example.invalid/a"),
                ("cpp_api", "https://example.invalid/b"),
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
        # 建库这一步也要跑在假来源上。先拿真来源初始化、之后才替换适配器，
        # 等于永远测不到"这个来源根本没有站点地图"的那条路径。
        with self.quiet_source(source or self.FakeSource):
            initialize_db(connection)
        return connection

    def test_a_feed_only_source_can_initialize_the_database(self):
        # 只实现 inventory_feeds / read_feed 的来源没有 sitemap_index_url，
        # 开库时无条件去问它要总入口，第一步就 AttributeError。
        self.assertFalse(hasattr(self.FakeSource, "sitemap_index_url"))
        connection = self._connection()
        self.assertEqual(
            connection.execute(
                "SELECT value FROM metadata WHERE key='inventory_index'"
            ).fetchone()[0],
            "",
        )
        # 溯源信息缺一条不影响别的：库该建的都建齐了。
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
        # 条目自己声明的分类要赢过入口的分类——一个入口列多类必须表达得出来。
        self.assertEqual(counts, {"guides": 2, "cpp_api": 1})
        # 元数据和 sitemap 路径完全一样，验收合同不因为换了来源就放松。
        row = connection.execute(
            "SELECT doc_version, locale, route_depth, sitemap_url FROM pages LIMIT 1"
        ).fetchone()
        self.assertTrue(all(value is not None for value in tuple(row)))

    def test_a_failing_feed_is_recorded_not_swallowed(self):
        class Broken(self.FakeSource):
            @staticmethod
            def read_feed(dataset, url):
                raise TimeoutError("站点没响应")

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
        """换掉适配器，顺便把进度日志收进黑洞——测试输出该只有测试结果。

        整个 workspace 一起换，所以 `discover` 和 `db` 看到的是同一个假来源。
        以前是逐个模块改全局变量，只改了 `discover` 那份，于是建库始终跑在
        真来源上，"开库时无条件问来源要站点地图总入口"这个 bug 一直没被测到。
        """
        with using(source=source), contextlib.redirect_stdout(io.StringIO()):
            yield

    def _check_status(self, report, name):
        return next(c for c in report["checks"] if c["name"] == name)["status"]


class GenericRelationLayerTests(unittest.TestCase):
    """没有领域知识包时，通用关系能力仍然要能用。

    这是 ENH-003 想验证的那条边界：连接、存储、查询、解释关系是通用的，
    "为什么有关"才是领域的。
    """

    def test_relation_labels_fall_back_to_the_generic_set(self):
        with using(knowledge=None) as workspace:
            self.assertIn("belongs_to", workspace.relation_labels)
            self.assertIn("official_link", workspace.evidence_labels)

    def test_official_link_is_expected_without_any_knowledge_pack(self):
        with using(knowledge=None):
            self.assertEqual(validate.expected_evidence_kinds(), ["official_link"])

    def test_query_names_work_without_a_knowledge_pack(self):
        with using(knowledge=None):
            self.assertEqual(search.query_names("Nanite"), ["nanite"])


class InventoryCoverageTests(unittest.TestCase):
    """BUG-011：正文链过去、清单里却没有，不能说成"官方确实没有这一页"。

    两件事的下一步完全相反——一个是改查询词，一个是改来源适配器的枚举范围。
    说错方向，用户会在错误的地方反复试。
    """

    def _linked_to_a_missing_page(self):
        connection = temp_db(self)
        seed_entity(connection, entity_type="guide", name="Shader Group",
                    path="/render/shader_nodes/groups")
        # 已抓页面链向 Interface Node Groups，但清单里没有这一页。
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
        self.assertIn("来源没有枚举到", joined)
        self.assertNotIn("官方文档确实没有这一页", joined)

    def test_a_genuinely_absent_name_still_says_so(self):
        """反向保证：真没有的东西不能都赖到来源范围上。"""
        connection = self._linked_to_a_missing_page()
        result = context.related_payload(connection, "Absolutely Nothing")
        self.assertEqual(result["status"], "entity_not_found")
        self.assertIn(
            "官方文档确实没有这一页", chr(10).join(result["next_steps"])
        )

    def test_gaps_separate_not_fetched_from_not_enumerated(self):
        connection = self._linked_to_a_missing_page()
        # 再加一条：目标在清单里，只是还没抓正文。
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
        self.assertEqual(gaps["pending_targets"], 1, "清单里有、没抓——可以补抓")
        self.assertEqual(gaps["missing_targets"], 1, "清单里没有——要改枚举范围")
        self.assertEqual(
            gaps["top_uncovered_areas"],
            [{"area": "/interface/controls/nodes", "links": 1}],
        )

    def test_scattered_misses_are_not_reported_as_a_scope_gap(self):
        """零星对不上是正常噪音；只有整个目录一页都没枚举到才算范围划漏。"""
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
        # 同目录下清单里有页面，所以不算"整个目录没枚举到"。
        self.assertEqual(gaps["uncovered_areas"], 0)


class McpMultiDatasetTests(unittest.TestCase):
    """ENH-006 的正题：一个 MCP 连接同时服务多个文档库。

    以前一个进程锁死一个数据集，查第二个库要在客户端里再配一条 server 记录。
    实测的结果是没人配——181 次跨库调用全部退回了命令行。
    """

    def setUp(self):
        self.libraries = {}
        for key, title in (("lib-a", "Alpha 手册"), ("lib-b", "Beta 手册")):
            connection, data_dir = temp_library(self)
            seed_entity(connection, entity_type="guide", name=f"{title} 首页",
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
                    make_document(f"{title} 专题", [text_block(f"只有 {title} 才有这段话")]),
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
                       {"query": "专题", "dataset_id": "lib-a", "no_fetch": True})
        b = self._call("docatlas_ask",
                       {"query": "专题", "dataset_id": "lib-b", "no_fetch": True})
        self.assertFalse(a["isError"], a)
        self.assertFalse(b["isError"], b)
        self.assertIn("只有 Alpha 手册 才有这段话", a["content"][0]["text"])
        self.assertIn("只有 Beta 手册 才有这段话", b["content"][0]["text"])
        # 反向保证：真的是两个库，不是同一个库回了两次。
        self.assertNotIn("Beta", a["content"][0]["text"])

    def test_routing_does_not_leak_into_the_next_call(self):
        """切库必须在调用结束后还原，否则下一次不带 dataset_id 的调用会串味。"""
        before = runtime.active().id
        self._call("docatlas_ask",
                   {"query": "专题", "dataset_id": "lib-b", "no_fetch": True})
        self.assertEqual(runtime.active().id, before)

    def test_structured_results_carry_the_dataset_identity(self):
        result = self._call(
            "docatlas_ask",
            {"query": "专题", "dataset_id": "lib-a", "no_fetch": True,
             "format": "json"},
        )
        payload = result["structuredContent"]
        self.assertEqual(payload["dataset"]["dataset_id"], "lib-a")
        self.assertEqual(payload["dataset"]["product"], "Alpha 手册")
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["knowledge"][0]["knowledge_id"].startswith("K"))
        self.assertTrue(payload["contract_version"])
        # 不认识 structuredContent 的客户端要能从文本里读到同样的内容。
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)

    def test_markdown_stays_the_default_so_queries_do_not_pay_for_json(self):
        markdown = self._call(
            "docatlas_ask",
            {"query": "专题", "dataset_id": "lib-a", "no_fetch": True},
        )
        self.assertNotIn("structuredContent", markdown)
        self.assertLess(
            len(markdown["content"][0]["text"]),
            len(
                self._call(
                    "docatlas_ask",
                    {"query": "专题", "dataset_id": "lib-a", "no_fetch": True,
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
        # 没挂领域包也要有通用关系能力，不能显示成"没有关系功能"。
        self.assertIsNone(report["knowledge_pack"])
        self.assertIn("official_link", report["evidence_kinds"])
        self.assertIn("belongs_to", report["relation_types"])

    def test_a_wrong_category_lists_the_valid_ones(self):
        result = self._call(
            "docatlas_ask",
            {"query": "专题", "dataset_id": "lib-a", "category": "not_a_category"},
        )
        self.assertTrue(result["isError"])
        self.assertIn("guides", result["content"][0]["text"])


class BreadcrumbNoiseTests(unittest.TestCase):
    """BUG-002：面包屑把整站目录名塞进每一页的全文索引。

    真实库里 25,565 个知识块有 6,854 个（27%）正文以面包屑开头，而它们几乎
    全被判成 parameters / returns——于是"Blueprint Camera …"这类查询会命中
    该目录下的每一页，命中的全是面包屑，正文一个词都没对上。
    """

    TRAIL = "[BlueprintAPI](https://x/a) > [BlueprintAPI/Camera](https://x/b)"

    def test_a_breadcrumb_trail_is_dropped(self):
        body = f"{self.TRAIL}\n\nSet Field Of View\n\nTarget is Camera Component"
        cleaned = chunking.strip_breadcrumbs(body)
        self.assertNotIn("https://x/a", cleaned)
        # Target is 这行是 targets_type 关系的证据，绝不能跟着一起删掉。
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
        # 真实形状：小节被合并成一块，导航那一节的正文就跟着进了全文索引。
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
            category="blueprint_api",
            document_type=None,
        )
        indexed = " ".join(chunk["content_text"] for chunk in chunks)
        self.assertNotIn("https://x/a", indexed)
        self.assertNotIn("BlueprintAPI/Camera", indexed)
        self.assertIn("Target is Camera Component", indexed)
        self.assertIn("In Field Of View", indexed)

    def test_changing_the_rule_bumps_the_chunker_version(self):
        """切分规则变了不改版本号，旧块会和新块混在同一个库里。"""
        self.assertEqual(constants.CHUNKER_VERSION, "v5")


class CrossLanguageDiagnosisTests(unittest.TestCase):
    """ENH-005：单语库里用另一套文字提问，必然一无所获。

    这不是 Bug——库里本来就没有那种文字的正文。但空结果不含信息量，
    用户没法从中判断该改成什么。所以要认出这一种"没有"。

    判断只看字形，语言来自数据集的 language 字段：不假设用户说中文，
    也不假设文档是英文。
    """

    def test_script_is_detected_not_guessed_from_the_user(self):
        # 同一条查询，在不同语言的库里结论相反——语言来自数据集，不是猜的。
        self.assertEqual(text.script_mismatch("如何设置视野", "en-US"), "han")
        self.assertEqual(text.script_mismatch("如何设置视野", "zh-CN"), "")
        self.assertEqual(text.script_mismatch("Set Field Of View", "zh-CN"), "latin")
        self.assertEqual(text.script_mismatch("Set Field Of View", "en-US"), "")

    def test_mixed_scripts_go_with_the_dominant_one(self):
        # 英文库里问"Nanite 是什么"——主体是汉字，算异语言。
        self.assertEqual(text.script_mismatch("Nanite 到底是个什么东西呢", "en-US"), "han")
        # 但夹一两个汉字的英文查询不算。
        self.assertEqual(text.script_mismatch("Nanite virtualized geometry 用法", "en-US"), "")

    def test_japanese_kanji_and_kana_both_belong_to_japanese(self):
        # 日文汉字假名混写，两套字都是它自己的，不能判成异语言。
        self.assertEqual(text.script_mismatch("ノードの設定", "ja-JP"), "")
        self.assertEqual(text.script_mismatch("設定", "ja-JP"), "")

    def test_an_unknown_language_tag_falls_back_to_latin(self):
        self.assertEqual(text.expected_scripts("xx-YY"), ("latin",))

    def test_symbols_only_queries_are_never_flagged(self):
        for query in ("K2_SetTimer", "UPROPERTY", "123", "", "   "):
            self.assertEqual(text.script_mismatch(query, "en-US"), "", query)

    def test_the_empty_result_says_which_kind_of_nothing_it_is(self):
        connection = temp_db(self)
        lookup = ondemand.inventory_lookup(connection, "把小方块撒到网格表面")
        steps = chr(10).join(context.describe_lookup(lookup))
        self.assertIn("汉字", steps)
        self.assertIn("en-US", steps)
        # 关键：不能说成"官方文档确实没有这一页"——那是另一种"没有"。
        self.assertNotIn("官方文档确实没有这一页", steps)

    def test_a_same_script_miss_still_reads_as_a_genuine_miss(self):
        connection = temp_db(self)
        lookup = ondemand.inventory_lookup(connection, "zzzznotarealpage")
        steps = chr(10).join(context.describe_lookup(lookup))
        self.assertIn("官方文档确实没有这一页", steps)
        self.assertNotIn("汉字", steps)

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
    """ENH-006 的验收线：新数据集只实现一个函数就能建出真关系。

    "不修改 MCP server 和通用关系核心" 是这些用例真正要钉死的东西——
    下面的假领域包既没写 SQL、也不认识表结构和 entity id。
    """

    class ToyDomain:
        """一个假产品的领域包：组件名和类名对得上就算一条关系。"""

        DERIVED_EVIDENCE_KINDS = ("toy_name_match",)
        RELATION_LABELS = {"implements": "实现自"}

        @staticmethod
        def relation_rules(graph):
            for source, target, name in graph.name_matches("widget", "toy_class"):
                yield relations.RelationCandidate(
                    source=source,
                    target=target,
                    relation_type="implements",
                    evidence_kind="toy_name_match",
                    confidence=0.95,
                    note=f"名字都叫 {name}",
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
        self.assertEqual(row["note"], "名字都叫 Particle Emitter")
        # 归属必须记成领域包，否则全量重建时清不掉、也分不清谁建的。
        self.assertEqual(row["origin"], "toy")

    def test_official_links_still_build_without_any_knowledge_pack(self):
        """没有领域包时，"正文链到另一篇文档"这条通用关系必须照样成立。"""
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
        self.assertIn("belongs_to", kinds, "抓来的事实不该被领域包的重建清掉")
        self.assertNotIn("stale", kinds, "领域包上一轮的推导必须清干净")

    def test_incremental_runs_the_same_rules_as_the_full_build(self):
        """按需抓取补页后，领域关系必须和全量重建建出来的一样。

        以前增量是另一个函数，三种领域关系里只补了一种，另外两种在增量路径上
        永远是空的——"全都跑过了"的库和"边用边补"的库内容不一样。
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
        """连不上的目标要变成诊断，不能悄悄消失，更不能硬连一个。"""
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
        # 置信度被夹回合法区间，而不是原样写进去。
        self.assertEqual(
            connection.execute("SELECT confidence FROM relations").fetchone()[0], 1.0
        )


class RelatedContractTests(unittest.TestCase):
    """`related` 以前用一个裸 `[]` 表示三种完全不同的状态。"""

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
            "/documentation/unreal-engine/nanite-overview",
            "guides",
            "Nanite Virtualized Geometry",
            [text_block("Nanite is a virtualized micropolygon geometry system.")],
        )
        result = context.related_payload(
            self.connection, "Nanite Virtualized Geometry"
        )
        self.assertEqual(result["status"], "entity_found_but_no_relations")
        self.assertTrue(result["entities"])
        self.assertTrue(result["next_steps"])

    def test_nothing_anywhere_is_its_own_state(self):
        result = context.related_payload(self.connection, "zzzznotarealthing")
        self.assertEqual(result["status"], "entity_not_found")
        self.assertEqual(result["lookup"]["pending_pages"], [])

    def test_missing_knowledge_id_is_not_treated_as_a_missing_page(self):
        # K 编号是知识块 ID，不是页面名字——查不到的话是"编号不存在"，
        # 跟"官方没有这一页/清单里有还没抓"是完全不同的诊断，不能套用
        # inventory_lookup（那是拿名字去比对页面标题/路径，对数字编号毫无意义）。
        result = context.related_payload(self.connection, "K999999")
        self.assertEqual(result["status"], "knowledge_id_not_found")
        self.assertNotIn("lookup", result)
        self.assertTrue(result["next_steps"])


class McpRelatedEvidenceTests(unittest.TestCase):
    """SKILL.md 明确承诺 `related` 每条关系都带 `note` 和出处；MCP 是
    Skill 优先用的入口，文本渲染丢了这两个字段，承诺就是空的。"""

    class _NoCloseConnection:
        """`tool_related` 用完连接会自己 close；测试要在同一个连接上继续
        断言，所以拿一层代理挡掉 close，真连接留给 tearDown 收尾。"""

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
            " 'https://example.invalid/evidence-page', '同名但未核实',"
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
        self.assertIn("同名但未核实", output)


class NeutralNamingTests(unittest.TestCase):
    """接了 cppreference / Blender 之后，输出里还写着 UE 就是在骗人。"""

    def test_no_module_hardcodes_the_unreal_product_name(self):
        offenders = []
        for path in sorted(Path(config.REPO_ROOT / "docatlas").rglob("*.py")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                # 老库改名那几行必须留着旧名字，否则升级不上来。
                if any(
                    marker in line
                    for marker in (
                        "rename_column_if_present",
                        "migrate_metadata_key",
                        "migrate_tag_type",
                    )
                ):
                    continue
                if re.search(r'f"UE \{|"ue_version"', line):
                    offenders.append(f"{path.name}:{number} {line.strip()}")
        self.assertEqual(offenders, [])

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
    """MCP 是 AI 真正调的入口，公开合同和实现不许对不上。"""

    def test_every_argument_the_handlers_read_is_declared(self):
        # `fetch_limit` 曾经被 tool_ask 读取却没写进 inputSchema，
        # 于是没有任何客户端知道可以传它。
        import inspect

        from docatlas import mcpserver

        for tool in mcpserver.TOOLS:
            handler = mcpserver.HANDLERS[tool["name"]]
            source = inspect.getsource(handler)
            declared = set(tool["inputSchema"].get("properties", {}))
            read = set(re.findall(r"arguments\.get\(\s*[\"'](\w+)[\"']", source))
            self.assertLessEqual(
                read, declared, f"{tool['name']} 读了没公开的参数：{read - declared}"
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

    def test_cli_and_mcp_answer_through_the_same_function(self):
        # 两边各写一套"要不要补抓"的判断，迟早会给出不一样的答案。
        import inspect

        from docatlas import cli, mcpserver

        self.assertIn("answer(", inspect.getsource(cli.command_ask))
        self.assertIn("answer(", inspect.getsource(mcpserver.tool_ask))


if __name__ == "__main__":
    unittest.main()
