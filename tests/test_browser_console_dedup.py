# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A broken dev server must not stall or drown the agent's browser turn.

A Next.js dev server whose HMR websocket is down retries the same
``ws://.../_next/webpack-hmr`` error every second. Before the fix, every
retry appended an identical console line (300-slot deque, 100-line reads),
so the model read the same failure over and over, and a navigation to an
unreachable server had no retry budget at all. These pin the two product
rules: collapse consecutive repeats, and cap connection attempts at 3
before telling the model to skip and move on.
"""

from __future__ import annotations

import unittest

from navin.agent.tools.browser import BrowserTool, BrowserToolConfig, _BrowserSession


class _FakeMsg:
    def __init__(self, type_: str, text: str) -> None:
        self.type = type_
        self.text = text


class _FakeRequest:
    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url
        self.failure = "net::ERR_INVALID_HTTP_RESPONSE"


class _FakeResponse:
    def __init__(self, request: _FakeRequest) -> None:
        self.request = request
        self.url = request.url
        self.status = 500
        self.headers = {}


class _FakePage:
    """Records the handlers _wire_events registers, so tests can fire them."""

    def __init__(self, url: str = "https://example.test/") -> None:
        self.url = url
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


class _GotoPage:
    """Counts goto calls and fails the first ``fail_times`` of them."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self.last_args: dict | None = None

    async def goto(self, url: str, **kwargs) -> None:
        self.calls += 1
        self.last_args = {"url": url, **kwargs}
        if self.calls <= self._fail_times:
            raise RuntimeError("net::ERR_CONNECTION_REFUSED at http://127.0.0.1:3015")


class ConsoleDedupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _BrowserSession(BrowserToolConfig())

    def test_identical_repeats_collapse_into_one_line(self) -> None:
        hmr = (
            "[error] WebSocket connection to "
            "'ws://127.0.0.1:3015/_next/webpack-hmr?id=wfIgIsME4WapvmN' "
            "failed: Error during WebSocket handshake: net::ERR_INVALID_HTTP_RESPONSE"
        )
        for _ in range(11):
            self.session.log_console(hmr)
        entries = list(self.session.console)
        self.assertEqual(len(entries), 1, entries)
        self.assertIn("(x 11)", entries[0])
        self.assertIn("webpack-hmr", entries[0])

    def test_a_new_message_breaks_the_run_and_starts_its_own(self) -> None:
        self.session.log_console("[error] first failure")
        for _ in range(4):
            self.session.log_console("[error] second failure")
        self.session.log_console("[log] ready")
        entries = list(self.session.console)
        self.assertEqual(len(entries), 3, entries)
        self.assertEqual(entries[0], "[error] first failure")
        self.assertIn("(x 4)", entries[1])
        self.assertEqual(entries[2], "[log] ready")

    def test_clear_console_resets_the_dedup_state(self) -> None:
        self.session.log_console("[error] same")
        self.session.log_console("[error] same")
        self.session.clear_console()
        self.session.log_console("[error] same")
        entries = list(self.session.console)
        self.assertEqual(len(entries), 1, entries)
        self.assertNotIn("(x", entries[0])

    def test_wire_events_routes_failures_through_the_dedup(self) -> None:
        page = _FakePage()
        self.session._wire_events(page)
        on_console = page.handlers["console"]
        on_page_error = page.handlers["pageerror"]
        on_request_failed = page.handlers["requestfailed"]
        # The real flood: each message repeats many times in a row, exactly
        # like the HMR websocket retry loop in the user's console dump.
        for _ in range(5):
            on_console(_FakeMsg("error", "web-socket.js:60 handshake failed"))
        for _ in range(5):
            on_page_error("Uncaught Error: boom")
        for _ in range(5):
            on_request_failed(_FakeRequest("GET", "http://127.0.0.1:3015/_next/webpack-hmr"))
        entries = list(self.session.console)
        self.assertEqual(len(entries), 3, entries)
        self.assertIn("(x 5)", entries[0])
        self.assertIn("(x 5)", entries[1])
        self.assertIn("(x 5)", entries[2])
        # The summary filter still sees the entries as problems.
        issues = [e for e in entries if e.startswith(("[error]", "[pageerror]", "[requestfailed]"))]
        self.assertEqual(len(issues), 3)


class BoundedNavigationTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_on_first_attempt_returns_none(self) -> None:
        tool = BrowserTool()
        page = _GotoPage()
        result = await tool._goto_with_retry(page, "http://127.0.0.1:3015/")
        self.assertIsNone(result)
        self.assertEqual(page.calls, 1)

    async def test_transient_failures_retry_up_to_three_attempts(self) -> None:
        tool = BrowserTool()
        page = _GotoPage(fail_times=2)
        result = await tool._goto_with_retry(page, "http://127.0.0.1:3015/")
        self.assertIsNone(result)
        self.assertEqual(page.calls, 3)

    async def test_a_dead_server_stops_at_three_attempts_and_says_move_on(self) -> None:
        tool = BrowserTool()
        page = _GotoPage(fail_times=99)
        result = await tool._goto_with_retry(page, "http://127.0.0.1:3015/")
        self.assertIsNotNone(result)
        self.assertEqual(page.calls, 3)
        self.assertIn("after 3 attempts", result)
        self.assertIn("ERR_CONNECTION_REFUSED", result)
        self.assertIn("Skip browser testing", result)

    async def test_each_attempt_uses_a_short_bounded_timeout(self) -> None:
        # The default navigation timeout is 15s; a dead server must not
        # burn 15s per attempt on top of the 3-attempt cap.
        tool = BrowserTool()
        page = _GotoPage()
        await tool._goto_with_retry(page, "http://127.0.0.1:3015/")
        self.assertEqual(page.last_args.get("timeout"), 5_000)


if __name__ == "__main__":
    unittest.main()
