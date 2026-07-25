"""离线回归测试。

不联网、不碰真实数据库——每个用例自己建一个临时库。
跑法：

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config 在导入时就固定路径，所以必须先把数据根指到临时目录，避免动到真实知识库。
# 数据集仍用真实的那一份配置——这样 datasets/*.toml 本身也在测试覆盖范围内。
_TEMP_HOME = tempfile.mkdtemp(prefix="docatlas_test_")
os.environ["DOCATLAS_HOME"] = _TEMP_HOME
os.environ.pop("DOCATLAS_DATASET", None)

from docatlas import (  # noqa: E402
    chunking, config, context, dataset, net, ondemand, search, store, validate,
)
from docatlas import mcpserver  # noqa: E402
from docatlas.knowledge import unreal  # noqa: E402
from docatlas.db import connect_db, initialize_db  # noqa: E402
from docatlas.documents import transform_document  # noqa: E402


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
            INSERT INTO pages(url, path, category, sitemap_url, ue_version, locale,
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

    def test_domain_kinds_are_actually_produced_by_the_pack(self):
        # 声明了却没人生产，等于验收永远失败；反过来生产了却没声明，等于漏检。
        source = (Path(unreal.__file__)).read_text(encoding="utf-8")
        for kind in unreal.DERIVED_EVIDENCE_KINDS:
            self.assertIn(f"'{kind}'", source, f"{kind} 没有任何地方写入")


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
        self.assertIn("看不懂", mcpserver.tool_show({"chunk_id": "; DROP TABLE"}))


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


if __name__ == "__main__":
    unittest.main()
