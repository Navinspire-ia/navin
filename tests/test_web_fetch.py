"""Tests for the web_fetch tool (navin.agent.tools.web).

Two regressions matter here: the download must stop at the byte ceiling
instead of buffering an arbitrarily large body before truncating to maxChars,
and every failure must come back as a ToolResult error the loop can see, not
as a plain JSON string that fail_on_tool_error walks straight past.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from navin.agent.tools import web as web_mod
from navin.agent.tools.base import ToolResult
from navin.agent.tools.web import _MAX_FETCH_BYTES, WebFetchTool, _read_body_capped


def _run(coro):
    return asyncio.run(coro)


class _FakeStreamResponse:
    """Just enough of a streamed httpx.Response for _fetch_readability."""

    def __init__(self, payload: bytes, content_type: str = "text/plain") -> None:
        self._payload = payload
        self.headers = {"content-type": content_type}
        self.status_code = 200
        self.url = "http://example.com/big"
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        chunk = 64 * 1024
        for i in range(0, len(self._payload), chunk):
            yield self._payload[i : i + chunk]


def _patched_stream(response: _FakeStreamResponse | None, error: str | None):
    async def fake_stream(client, url, headers=None):
        return response, None, error

    return mock.patch.object(web_mod, "_stream_with_safe_redirects", fake_stream)


class ReadBodyCappedTest(unittest.TestCase):
    def test_a_body_under_the_limit_comes_back_whole(self) -> None:
        body, truncated = _run(_read_body_capped(_FakeStreamResponse(b"x" * 100), limit=200))
        self.assertEqual(body, b"x" * 100)
        self.assertFalse(truncated)

    def test_a_body_over_the_limit_is_cut_and_flagged(self) -> None:
        payload = bytes(range(256)) * 1024  # 256 KiB
        body, truncated = _run(_read_body_capped(_FakeStreamResponse(payload), limit=1000))
        self.assertEqual(len(body), 1000)
        self.assertEqual(body, payload[:1000])
        self.assertTrue(truncated)


class ByteCeilingTest(unittest.TestCase):
    def test_an_oversized_page_stops_at_the_ceiling_and_says_so(self) -> None:
        oversized = _FakeStreamResponse(b"a" * (_MAX_FETCH_BYTES + 100_000))
        with _patched_stream(oversized, None):
            out = _run(WebFetchTool()._fetch_readability("http://example.com/big", "text", 10**9))
        data = json.loads(out)
        self.assertTrue(data["truncated"])
        # The text carries the untrusted banner but never more than the ceiling
        # plus that framing, proving nothing beyond the cap was buffered.
        self.assertLessEqual(len(data["text"]), _MAX_FETCH_BYTES + 200)

    def test_a_small_page_is_returned_untruncated(self) -> None:
        with _patched_stream(_FakeStreamResponse(b"hello world"), None):
            out = _run(WebFetchTool()._fetch_readability("http://example.com/ok", "text", 50_000))
        data = json.loads(out)
        self.assertFalse(data["truncated"])
        self.assertIn("hello world", data["text"])


class ErrorMarkingTest(unittest.TestCase):
    def _assert_marked_error(self, result) -> None:
        self.assertIsInstance(result, ToolResult)
        self.assertTrue(result.is_error)
        self.assertIn("error", json.loads(str(result)))

    def test_an_invalid_url_is_a_tool_error_not_plain_json(self) -> None:
        result = _run(WebFetchTool().execute(url="ftp://example.com/file"))
        self._assert_marked_error(result)

    def test_a_blocked_redirect_is_a_tool_error(self) -> None:
        with _patched_stream(None, "Redirect blocked: private address"):
            result = _run(WebFetchTool()._fetch_readability("http://example.com", "text", 50_000))
        self._assert_marked_error(result)

    def test_a_missing_response_is_a_tool_error(self) -> None:
        with _patched_stream(None, None):
            result = _run(WebFetchTool()._fetch_readability("http://example.com", "text", 50_000))
        self._assert_marked_error(result)


class MaxCharsSchemaTest(unittest.TestCase):
    def test_the_documented_default_of_zero_passes_validation(self) -> None:
        errors = WebFetchTool().validate_params({"url": "https://example.com", "maxChars": 0})
        self.assertEqual(errors, [])

    def test_zero_means_the_tool_default_at_execution_time(self) -> None:
        captured: dict[str, int] = {}

        async def spy(url, max_chars):
            captured["max_chars"] = max_chars
            return "ok"

        tool = WebFetchTool()
        with mock.patch.object(web_mod, "_validate_url_safe", return_value=(True, "")), \
                mock.patch.object(tool, "_fetch_jina", spy), \
                mock.patch.object(web_mod.httpx, "AsyncClient", side_effect=RuntimeError("no network")):
            _run(tool.execute(url="https://example.com", maxChars=0))
        self.assertEqual(captured["max_chars"], tool.max_chars)


if __name__ == "__main__":
    unittest.main()
