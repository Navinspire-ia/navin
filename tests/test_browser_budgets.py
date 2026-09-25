# Tests for the bounded click/settle budgets of the browser tool
# (board t-5686314b): a click must cost at most ~3 s + 1.5 s, settle at most
# ~2 s + 0.15 s, and the default locator timeout must stay at 8 s.
# Also covers the scroll_infinite time budget (board t-df2dc8ef, ~8 s max).

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from navin.agent.tools import browser as browser_module
from navin.agent.tools.browser import BrowserTool, BrowserToolConfig


class _FakeTarget:
    def __init__(self, fail_clicks: bool = False) -> None:
        self.fail_clicks = fail_clicks
        self.click_timeouts: list[int] = []
        self.evaluated = False

    async def click(self, timeout: int, **_: object) -> None:
        self.click_timeouts.append(timeout)
        if self.fail_clicks:
            raise RuntimeError("intercepted by overlay")

    async def evaluate(self, _script: str) -> None:
        self.evaluated = True


class _FakePage:
    def __init__(self) -> None:
        self.load_timeouts: list[int] = []

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        self.load_timeouts.append((state, timeout))


class BrowserBudgetsTests(unittest.TestCase):
    def test_default_timeout_is_bounded(self) -> None:
        config = BrowserToolConfig()
        self.assertEqual(config.default_timeout_ms, 8_000)

    def test_click_budgets_are_bounded(self) -> None:
        tool = BrowserTool(config=BrowserToolConfig())

        target = _FakeTarget()
        asyncio.run(tool._click(target))
        self.assertEqual(target.click_timeouts, [3_000])

        stubborn = _FakeTarget(fail_clicks=True)
        asyncio.run(tool._click(stubborn))
        self.assertEqual(stubborn.click_timeouts, [3_000, 1_500])
        self.assertTrue(stubborn.evaluated)

    def test_settle_budgets_are_bounded(self) -> None:
        tool = BrowserTool(config=BrowserToolConfig())
        page = _FakePage()
        pauses: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            pauses.append(seconds)

        original_sleep = asyncio.sleep
        asyncio.sleep = fake_sleep  # type: ignore[assignment]
        try:
            asyncio.run(tool._settle(page))
        finally:
            asyncio.sleep = original_sleep  # type: ignore[assignment]

        self.assertEqual(page.load_timeouts, [("load", 2_000)])
        self.assertEqual(pauses, [0.15])


class _InfiniteScrollPage:
    """Page whose height keeps growing so scroll_infinite never self-stops."""

    def __init__(self) -> None:
        self.evaluate_calls = 0

    @property
    def url(self) -> str:
        return "https://example.test/feed"

    async def evaluate(self, js: str) -> object:
        if "innerText" in js:
            return "abcdefghij"
        if "scrollTo" in js:
            return None
        self.evaluate_calls += 1
        return 1_000 + 500 * self.evaluate_calls


class _FakeLoop:
    def __init__(self, clock: dict[str, float]) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock["t"]


class _FakeAsyncio:
    def __init__(self, clock: dict[str, float]) -> None:
        self._clock = clock

    def get_running_loop(self) -> _FakeLoop:
        return _FakeLoop(self._clock)

    async def sleep(self, seconds: float) -> None:
        self._clock["t"] += seconds


class ScrollInfiniteBudgetTests(unittest.IsolatedAsyncioTestCase):
    def _tool_and_session(self) -> tuple[BrowserTool, object]:
        tool = BrowserTool(config=BrowserToolConfig())
        session = browser_module._BrowserSession(BrowserToolConfig())
        return tool, session

    async def test_scroll_infinite_stops_on_time_budget(self) -> None:
        tool, session = self._tool_and_session()
        page = _InfiniteScrollPage()
        clock = {"t": 0.0}

        async def fake_ensure_page() -> _InfiniteScrollPage:
            return page

        session.ensure_page = fake_ensure_page  # type: ignore[method-assign]
        with (
            patch.object(BrowserTool, "_SCROLL_INFINITE_BUDGET_S", 1.0),
            patch.object(browser_module, "asyncio", _FakeAsyncio(clock)),
        ):
            result = await tool._dispatch(session, "scroll_infinite", {})
        # pause 0.45 s per round: rounds 0..2 fit inside the 1 s budget, the
        # 4th round is refused once the fake clock passes the deadline.
        self.assertIn("after 3 of up to 8 rounds", result)
        self.assertIn("(time budget reached)", result)

    async def test_scroll_infinite_without_budget_cap_reports_rounds(self) -> None:
        tool, session = self._tool_and_session()
        page = _InfiniteScrollPage()
        clock = {"t": 0.0}

        async def fake_ensure_page() -> _InfiniteScrollPage:
            return page

        session.ensure_page = fake_ensure_page  # type: ignore[method-assign]
        with patch.object(browser_module, "asyncio", _FakeAsyncio(clock)):
            result = await tool._dispatch(session, "scroll_infinite", {"index": 3})
        self.assertIn("after 3 of up to 3 rounds", result)
        self.assertNotIn("(time budget reached)", result)


if __name__ == "__main__":
    unittest.main()
