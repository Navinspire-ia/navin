"""Tests for the scrape tool (Python fallback path)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.scrape import (
    ScrapeTool,
    ScrapeToolConfig,
    _clean_text,
    _export_py,
    _extract_html_py,
    annotate_pages,
    detect_wall,
)

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Hello Docs</title>
  <meta name="description" content="A demo page"/>
  <meta property="og:title" content="Hello Docs OG"/>
</head>
<body>
  <nav>Skip me</nav>
  <main>
    <h1>Hello Docs</h1>
    <p>First paragraph with <strong>bold</strong> text.</p>
    <ul><li>One</li><li>Two</li></ul>
    <a href="/next">Next</a>
    <a href="https://example.com/out">Out</a>
  </main>
  <footer>Chrome</footer>
</body>
</html>
"""


class ScrapeHelpersTest(unittest.TestCase):
    def test_clean_text_collapses_whitespace(self) -> None:
        self.assertEqual(_clean_text("  a \t b\n\n\nc  "), "a b\n\nc")

    def test_extract_html_py(self) -> None:
        extracted = _extract_html_py(SAMPLE_HTML, "https://example.com/docs")
        self.assertEqual(extracted["title"], "Hello Docs")
        self.assertIn("First paragraph", extracted["text"])
        self.assertIn("https://example.com/next", extracted["links"])
        self.assertEqual(extracted["meta"].get("description"), "A demo page")

    def test_extract_recovers_body_buried_in_divs(self) -> None:
        """No <article>/<main>: the story nested in divs is still recovered."""
        story = " ".join(
            "This is the real article body that a reader came for." for _ in range(12)
        )
        page = (
            "<html><head><title>Deep</title></head><body>"
            "<div class='nav'>Home About Contact Login Signup Newsletter</div>"
            f"<div class='post'><div class='content'><p>{story}</p></div></div>"
            "<div class='footer'>Copyright junk cookie banner ad ad ad</div>"
            "</body></html>"
        )
        extracted = _extract_html_py(page, "https://example.com/post")
        self.assertIn("real article body", extracted["text"])

    def test_readability_main_isolates_the_article(self) -> None:
        from navin.agent.tools.scrape import _readability_main

        story = " ".join(
            "Readability keeps the dense paragraph that carries the story." for _ in range(30)
        )
        page = (
            "<html><head><title>News</title></head><body>"
            "<header><nav>menu links everywhere</nav></header>"
            f"<article><p>{story}</p></article>"
            "<footer>tiny footer</footer></body></html>"
        )
        main = _readability_main(page)
        self.assertIsNotNone(main)
        self.assertIn("carries the story", main)


class ScrapeWallDetectionTest(unittest.TestCase):
    def test_ok_page_has_no_wall(self) -> None:
        self.assertIsNone(
            detect_wall(
                status=200,
                title="Hello Docs",
                text="First paragraph with enough readable content for a real page.",
            )
        )

    def test_cloudflare_challenge(self) -> None:
        wall = detect_wall(status=403, title="Just a moment...", text="Checking your browser")
        assert wall is not None
        self.assertEqual(wall["kind"], "cloudflare")
        self.assertTrue(wall["human"])

    def test_captcha(self) -> None:
        wall = detect_wall(html='<div class="g-recaptcha" data-sitekey="x"></div>')
        assert wall is not None
        self.assertEqual(wall["kind"], "captcha")
        self.assertTrue(wall["human"])

    def test_paywall(self) -> None:
        wall = detect_wall(text="Subscribe to continue reading this premium article")
        assert wall is not None
        self.assertEqual(wall["kind"], "paywall")
        self.assertTrue(wall["human"])

    def test_empty_shell_is_soft(self) -> None:
        wall = detect_wall(status=200, title="App", text=" ", html="<script></script>" * 10)
        assert wall is not None
        self.assertEqual(wall["kind"], "empty_shell")
        self.assertFalse(wall["human"])

    def test_annotate_pages(self) -> None:
        pages = annotate_pages(
            [
                {
                    "url": "https://example.com",
                    "status": 403,
                    "title": "Just a moment...",
                    "text": "cf-browser-verification",
                }
            ]
        )
        self.assertEqual(pages[0]["wall"]["kind"], "cloudflare")


class ScrapeToolExportTest(unittest.TestCase):
    def test_export_csv_and_json(self) -> None:
        rows = [
            {
                "url": "https://example.com/a",
                "title": "A",
                "status": 200,
                "text": "hello",
                "error": None,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_info = _export_py(rows, "csv", root / "out.csv")
            _export_py(rows, "json", root / "out.json")
            self.assertEqual(csv_info["count"], 1)
            self.assertTrue((root / "out.csv").is_file())
            self.assertTrue((root / "out.json").is_file())
            payload = json.loads((root / "out.json").read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["title"], "A")


class ScrapeToolExecuteTest(unittest.TestCase):
    def test_extract_and_clean_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = ScrapeTool(workspace=tmp, config=ScrapeToolConfig())
            extracted = asyncio.run(
                tool.execute(
                    action="extract",
                    html=SAMPLE_HTML,
                    url="https://example.com/docs",
                )
            )
            self.assertIsInstance(extracted, str)
            data = json.loads(extracted)
            self.assertEqual(data["title"], "Hello Docs")

            cleaned = asyncio.run(tool.execute(action="clean", text="  foo \n bar  "))
            self.assertEqual(cleaned, "foo\nbar")

    def test_export_action_writes_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = ScrapeTool(workspace=tmp, config=ScrapeToolConfig())
            records = json.dumps(
                {
                    "pages": [
                        {
                            "url": "https://example.com",
                            "title": "Example",
                            "status": 200,
                            "text": "body",
                            "error": None,
                        }
                    ]
                }
            )
            result = asyncio.run(
                tool.execute(
                    action="export",
                    records=records,
                    format="json",
                    path="scrape/demo.json",
                )
            )
            info = json.loads(result)
            out = Path(info["path"])
            self.assertTrue(out.is_file())
            self.assertEqual(info["count"], 1)


if __name__ == "__main__":
    unittest.main()
