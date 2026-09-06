"""Tests for scrape_ops helpers and new scrape/browser actions (no live network)."""

from __future__ import annotations

import json
import tempfile
import unittest

from navin.agent.tools.browser import BrowserTool, BrowserToolConfig, _BrowserSession
from navin.agent.tools.browser_use_bridge import browser_use_available, format_action_result
from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig
from navin.agent.tools.scrape_ops import (
    enrich_record,
    extract_tables,
    parse_sitemap_urls,
)


class ScrapeOpsTest(unittest.TestCase):
    def test_enrich_adds_wordcount_and_fetched_at(self) -> None:
        row = enrich_record({"text": "one two three", "meta": {"og:title": "T"}})
        self.assertEqual(row["wordCount"], 3)
        self.assertIn("fetchedAt", row)
        self.assertEqual(row["title"], "T")

    def test_extract_tables(self) -> None:
        html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        tables = extract_tables(html)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["rows"][0], ["A", "B"])
        self.assertEqual(tables[0]["rows"][1], ["1", "2"])

    def test_parse_sitemap_urls(self) -> None:
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/a</loc></url>
          <url><loc>https://example.com/b</loc></url>
        </urlset>"""
        urls = parse_sitemap_urls(xml)
        self.assertEqual(urls, ["https://example.com/a", "https://example.com/b"])


class ScrapeNewActionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_tables_and_enrich_actions(self) -> None:
        tool = ScrapeTool(config=ScrapeToolConfig(), workspace=tempfile.mkdtemp())
        html = "<html><body><table><tr><td>x</td></tr></table></body></html>"
        tables_raw = await tool.execute(action="tables", html=html)
        tables = json.loads(tables_raw if isinstance(tables_raw, str) else tables_raw)
        self.assertGreaterEqual(tables["tables"][0]["rowCount"], 1)

        enrich_raw = await tool.execute(
            action="enrich",
            records=json.dumps({"pages": [{"text": "hello world", "meta": {}}]}),
        )
        enrich = json.loads(enrich_raw if isinstance(enrich_raw, str) else enrich_raw)
        self.assertEqual(enrich["pages"][0]["wordCount"], 2)


class BrowserParityActionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_history_and_done_without_browser(self) -> None:
        tool = BrowserTool(config=BrowserToolConfig())
        session = _BrowserSession(tool.config)
        # history / done do not need a live page
        done = await tool._dispatch(session, "done", {"text": "exported scrape/out.csv"})
        self.assertIn("DONE:", done)
        hist = await tool._dispatch(session, "history", {})
        self.assertIn("done", hist)

    async def test_tabs_list_empty_then_track(self) -> None:
        session = _BrowserSession(BrowserToolConfig())
        self.assertEqual(session.list_tabs(), [])

    def test_browser_use_bridge_available_or_hint(self) -> None:
        # Installed in this env after Vague 1; still assert API shape.
        _ = browser_use_available()
        class R:
            error = None
            extracted_content = "ok"
            long_term_memory = None
        self.assertEqual(format_action_result(R()), "ok")


if __name__ == "__main__":
    unittest.main()
