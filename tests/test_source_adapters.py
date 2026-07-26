from __future__ import annotations

import unittest

from docatlas.dataset import load_dataset
from docatlas.runtime import DATASET_CONFIG_DIR
from docatlas.sources import blender_manual, cppreference


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


if __name__ == "__main__":
    unittest.main()
