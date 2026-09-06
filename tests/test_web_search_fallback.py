"""web_search must not die with DuckDuckGo: ddgs failures fall back to the
keyless HTML endpoint, and when even that is empty the error points the agent
at the Playwright browser tool instead of leaving it stranded."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest import mock

from navin.agent.tools.web import (
    _DDG_HTML_RESULT_RE,
    _DDG_HTML_SNIPPET_RE,
    WebSearchConfig,
    WebSearchTool,
    _ddg_unwrap_redirect,
)

_DDG_HTML_FIXTURE = """
<div class="result results_links results_links_deep web-result ">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc&amp;rut=abc">
     Example <b>Doc</b></a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc">
     A snippet about the doc.</a>
</div>
"""


class DdgRedirectUnwrapTest(unittest.TestCase):
    def test_uddg_redirect_is_decoded(self) -> None:
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1&rut=x"
        self.assertEqual(_ddg_unwrap_redirect(href), "https://example.com/page?a=1")

    def test_plain_url_is_left_alone(self) -> None:
        self.assertEqual(
            _ddg_unwrap_redirect("https://example.com/x"), "https://example.com/x"
        )


class DdgHtmlParsingTest(unittest.TestCase):
    def test_result_and_snippet_regexes_match_the_html_layout(self) -> None:
        links = _DDG_HTML_RESULT_RE.findall(_DDG_HTML_FIXTURE)
        self.assertEqual(len(links), 1)
        href, title = links[0]
        self.assertIn("uddg=", href)
        self.assertIn("Example", title)
        snippets = _DDG_HTML_SNIPPET_RE.findall(_DDG_HTML_FIXTURE)
        self.assertEqual(len(snippets), 1)
        self.assertIn("snippet about the doc", snippets[0])


class _FakeDDGS:
    """Stand-in ddgs module whose text() always raises (rate limit)."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def text(self, *args, **kwargs):
        raise RuntimeError("Ratelimit")


def _install_fake_ddgs() -> None:
    module = types.ModuleType("ddgs")
    module.DDGS = _FakeDDGS
    sys.modules["ddgs"] = module


class DuckDuckGoFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._ddgs_backup = sys.modules.get("ddgs")
        _install_fake_ddgs()
        self.tool = WebSearchTool(config=WebSearchConfig(provider="duckduckgo"))

    def tearDown(self) -> None:
        if self._ddgs_backup is not None:
            sys.modules["ddgs"] = self._ddgs_backup
        else:
            sys.modules.pop("ddgs", None)

    def test_ddgs_failure_uses_the_html_fallback(self) -> None:
        with mock.patch.object(
            self.tool,
            "_search_duckduckgo_html",
            new=mock.AsyncMock(return_value="Results for: q\n1. Example"),
        ) as fallback:
            out = asyncio.run(self.tool._search_duckduckgo("q", 3))
        fallback.assert_awaited_once_with("q", 3)
        self.assertIn("Example", out)
        self.assertNotIn("Error:", out)

    def test_double_failure_points_to_the_browser_tool(self) -> None:
        with mock.patch.object(
            self.tool,
            "_search_duckduckgo_html",
            new=mock.AsyncMock(return_value=None),
        ):
            out = asyncio.run(self.tool._search_duckduckgo("q", 3))
        text = str(out)
        self.assertIn("Error:", text)
        self.assertIn("browser", text)
        self.assertIn("Playwright", text)

    def test_empty_ddgs_results_still_try_the_html_fallback(self) -> None:
        sys.modules["ddgs"].DDGS = type(  # type: ignore[attr-defined]
            "_EmptyDDGS",
            (),
            {
                "__init__": lambda self, *a, **k: None,
                "text": lambda self, *a, **k: [],
            },
        )
        with mock.patch.object(
            self.tool,
            "_search_duckduckgo_html",
            new=mock.AsyncMock(return_value="Results for: q\n1. Example"),
        ) as fallback:
            out = asyncio.run(self.tool._search_duckduckgo("q", 3))
        fallback.assert_awaited_once()
        self.assertIn("Example", out)


if __name__ == "__main__":
    unittest.main()
