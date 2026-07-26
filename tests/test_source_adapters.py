from __future__ import annotations

import unittest

from docatlas.dataset import load_dataset
from docatlas.runtime import DATASET_CONFIG_DIR
from docatlas.sources import blender_manual, cppreference, roblox_creator


class CppreferenceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset("cppreference-2026-07-26", DATASET_CONFIG_DIR)

    def test_categories_and_paths_are_stable(self):
        self.assertEqual(
            cppreference._category_for_title("cpp/language/reference"), "language"
        )
        self.assertEqual(
            cppreference._category_for_title("cpp/compiler support/20"),
            "compiler_support",
        )
        path, url = cppreference.normalize_location(
            self.dataset,
            "https://en.cppreference.com/w/cpp/container/vector",
        )
        self.assertEqual(path, "/cpp/container/vector")
        self.assertEqual(url, "https://cppreference.com/cpp/container/vector")

    def test_parse_document_keeps_body_and_absolutizes_links(self):
        body = b"""
        <html lang="en"><head><title>std::vector - cppreference.com</title></head>
        <body><h1 id="firstHeading">std::vector</h1>
        <div class="mw-content-ltr mw-parser-output">
          <table class="t-navbar"><tr><td>Global navigation noise</td></tr></table>
          <p>Sequence container.</p>
          <h2>Member functions</h2>
          <p><a href="/w/cpp/container/vector/size">size</a></p>
          <script>navigationNoise()</script>
        </div></body></html>
        """
        parsed = cppreference.parse_document(
            self.dataset, "/cpp/container/vector", body
        )
        self.assertEqual(parsed["title"], "std::vector")
        self.assertIn("Sequence container.", parsed["markdown"])
        self.assertIn(
            "https://cppreference.com/w/cpp/container/vector/size",
            parsed["markdown"],
        )
        self.assertNotIn("navigationNoise", parsed["markdown"])
        self.assertNotIn("Global navigation noise", parsed["markdown"])


class BlenderManualAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset("blender-manual-5.2", DATASET_CONFIG_DIR)

    def test_fixed_version_rejects_latest_and_other_manual_areas(self):
        path, url = blender_manual.normalize_location(
            self.dataset,
            "https://docs.blender.org/manual/en/5.2/"
            "render/shader_nodes/shader/principled.html",
        )
        self.assertEqual(path, "/render/shader_nodes/shader/principled")
        self.assertIn("/manual/en/5.2/", url)
        self.assertIsNone(
            blender_manual.normalize_location(
                self.dataset,
                "https://docs.blender.org/manual/en/latest/"
                "render/shader_nodes/shader/principled.html",
            )
        )
        self.assertIsNone(
            blender_manual.normalize_location(
                self.dataset,
                "https://docs.blender.org/manual/en/5.2/editors/geometry_node.html",
            )
        )

    def test_search_index_and_html_parsing(self):
        index = (
            b'Search.setIndex({"docnames":["render/shader_nodes/index",'
            b'"modeling/geometry_nodes/index"],"envversion":64})'
        )
        self.assertEqual(
            blender_manual._docnames(index),
            ["render/shader_nodes/index", "modeling/geometry_nodes/index"],
        )
        body = """
        <html lang="en"><body>
        <article role="main" id="furo-main-content">
          <h1>Principled BSDF<a class="headerlink" href="#principled-bsdf">¶</a></h1>
          <p>Combines multiple layers into one node.</p>
          <h2>Inputs<a class="headerlink" href="#inputs">¶</a></h2>
          <p><a href="../index.html">Shader Nodes</a></p>
          <style>.noise { display: none; }</style>
        </article></body></html>
        """.encode("utf-8")
        parsed = blender_manual.parse_document(
            self.dataset, "/render/shader_nodes/shader/principled", body
        )
        self.assertEqual(parsed["title"], "Principled BSDF")
        self.assertIn("Combines multiple layers", parsed["markdown"])
        self.assertIn("/manual/en/5.2/render/shader_nodes/index.html", parsed["markdown"])
        self.assertNotIn("display: none", parsed["markdown"])
        self.assertNotIn("¶", parsed["markdown"])


class RobloxCreatorAdapterTests(unittest.TestCase):
    """第四个领域的接入验收：只新增适配器和数据集配置，不动核心。"""

    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset("roblox-creator-2026-07-26", DATASET_CONFIG_DIR)

    def test_engine_api_and_open_cloud_never_bleed_into_each_other(self):
        """两套系统靠**地址**分开，不靠关键词。

        `cloud-services` 是最容易错的那一处：DataStoreService、
        MemoryStoreService、HttpService 都写在这个目录下，而它们是实验内部
        用 `game:GetService()` 拿的 Engine 服务，不是外部 HTTP 的 Open Cloud。
        少一个斜杠（`en-us/cloud` 而不是 `en-us/cloud/`）就会整批判错。
        """
        engine = (
            "/docs/reference/engine/classes/Part",
            "/docs/reference/engine/enums/Material",
            "/docs/reference/engine/datatypes/CFrame",
        )
        cloud = (
            "/docs/cloud/reference/features/assets",
            "/docs/en-us/cloud/guides/data-stores",
            "/docs/en-us/cloud/auth/api-keys",
        )
        for path in engine:
            self.assertEqual(
                roblox_creator.categorize_path(self.dataset, path), "engine_api", path
            )
        for path in cloud:
            self.assertEqual(
                roblox_creator.categorize_path(self.dataset, path), "open_cloud", path
            )
        # 实验内部的服务指南既不是 Open Cloud，也不是自动生成的 API 参考。
        for path in (
            "/docs/en-us/cloud-services/data-stores",
            "/docs/en-us/cloud-services/http-service",
            "/docs/en-us/cloud-services/memory-stores/queue",
        ):
            self.assertEqual(
                roblox_creator.categorize_path(self.dataset, path),
                "studio_guides",
                path,
            )

    def test_the_same_page_written_two_ways_becomes_one_path(self):
        """索引里同一个 Engine 类会带语言段也会不带；正文链接则一律省掉。

        不收敛的话，同一页在清单里占两条，还会因为路径不同被判成两个分类。
        实测这一条影响 1,260 页——占枚举总数的三分之一。
        """
        canonical = roblox_creator._canonical_path
        for written in (
            "/docs/reference/engine/classes/DataStore",
            "/docs/en-us/reference/engine/classes/DataStore",
        ):
            self.assertEqual(
                canonical(self.dataset, written),
                "/docs/reference/engine/classes/DataStore",
            )
        # 反方向：正文链接省掉的语言段要补回去，页面自己在 frontmatter 里
        # 写明了正规写法就是带语言段的那种。
        for written in ("/docs/studio/setup", "/docs/en-us/studio/setup"):
            self.assertEqual(
                canonical(self.dataset, written), "/docs/en-us/studio/setup"
            )

    def test_the_index_is_read_but_the_full_text_dump_is_not(self):
        """索引开头的入口一览里，只有 `.md` 是页面，`.txt` 是索引本身。

        少了裸地址那一条，官方明确列为入口的 deprecated 清单永远进不来；
        多收了 `.txt`，就会把不该下载的全站正文拉进清单。
        """
        feed = "\n".join(
            [
                "<!-- Last updated: 2026-07-24T23:56:32Z -->",
                "- Full content (single file): /docs/llms-full.txt",
                "- Engine API index: /docs/reference/engine/llms.txt",
                "- Deprecated API inventory: /docs/reference/engine/deprecated.md",
                "",
                "- [Part](/docs/reference/engine/classes/Part.md): A common BasePart.",
                "- [Studio setup](/docs/en-us/studio/setup.md): Install Studio.",
                "- [External](https://devforum.roblox.com/t/1): not ours",
            ]
        )
        entries = self._read(feed)
        self.assertEqual(
            entries,
            [
                ("engine_api", "https://create.roblox.com/docs/reference/engine/deprecated"),
                ("engine_api", "https://create.roblox.com/docs/reference/engine/classes/Part"),
                ("studio_guides", "https://create.roblox.com/docs/en-us/studio/setup"),
            ],
        )

    def _read(self, feed_text):
        import docatlas.sources.roblox_creator as module

        original = module.fetch_bytes
        module.fetch_bytes = lambda *a, **k: (feed_text.encode("utf-8"), None, None)
        try:
            return module.read_feed(self.dataset, "https://create.roblox.com/x.txt")
        finally:
            module.fetch_bytes = original

    def test_frontmatter_supplies_the_title_and_the_heading_backs_it_up(self):
        with_frontmatter = (
            b'---\nname: Part\ntype: class\nsummary: "A common BasePart."\n'
            b"---\n\n# Class: Part\n\n> A common [BasePart](/docs/x.md).\n"
        )
        parsed = roblox_creator.parse_document(
            self.dataset, "/docs/reference/engine/classes/Part", with_frontmatter
        )
        self.assertEqual(parsed["title"], "Part")
        self.assertEqual(parsed["description"], "A common BasePart.")
        self.assertEqual(parsed["document_type"], "class")
        self.assertNotIn("summary:", parsed["markdown"])
        # 生成页没有 frontmatter 标题，标题在正文的一级标题里；
        # 退回路径末段只会得到 "deprecated" 这种查不到的名字。
        generated = b"# Deprecated Roblox Engine APIs\n\nTotal: 577\n"
        self.assertEqual(
            roblox_creator.parse_document(
                self.dataset, "/docs/reference/engine/deprecated", generated
            )["title"],
            "Deprecated Roblox Engine APIs",
        )

    def test_official_link_is_not_gated_on_the_declared_scope(self):
        target = roblox_creator.normalize_link_target
        self.assertEqual(
            target(self.dataset, "https://create.roblox.com/docs/en-us/art/modeling.md"),
            "/docs/en-us/art/modeling",
        )
        self.assertEqual(target(self.dataset, "/docs/studio/setup"), "/docs/en-us/studio/setup")
        self.assertIsNone(target(self.dataset, "https://devforum.roblox.com/t/1"))

    def test_the_snapshot_date_is_the_version_because_the_site_has_none(self):
        self.assertEqual(self.dataset.version, "2026-07-26")
        self.assertIsNone(self.dataset.knowledge, "第一轮刻意不挂领域知识包")
        self.assertEqual(
            sorted(self.dataset.categories),
            ["engine_api", "luau", "open_cloud", "studio_guides"],
        )


if __name__ == "__main__":
    unittest.main()
