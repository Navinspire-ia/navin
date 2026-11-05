# Tests for the memory and concurrency limits of the agent's local browsers
# (board t-fd480720, incident 2026-11-05: an unbounded headless Chromium filled
# the WSL VM). Fakes only: no real browser is launched.

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import patch

import psutil

from navin.agent.tools import browser as browser_module
from navin.agent.tools.base import ToolResult
from navin.agent.tools.browser import BrowserTool, BrowserToolConfig, _BrowserSession

_MB = 1024 * 1024
_STATE = {"cookies": [{"name": "sid", "domain": "app.test", "path": "/", "value": "x"}], "origins": []}


class _FakePage:
    url = "about:blank"

    def __init__(self) -> None:
        self.handlers: list[str] = []

    def on(self, event: str, _handler: Any) -> None:
        self.handlers.append(event)

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, _script: str) -> None:
        return None


class _FakeContext:
    def __init__(self, state: dict[str, Any] | None = None, *, fail_state: bool = False) -> None:
        self.state = state
        self.fail_state = fail_state
        self.closed = False
        self.kwargs: dict[str, Any] = {}
        self.pages: list[_FakePage] = []

    async def storage_state(self) -> dict[str, Any]:
        if self.fail_state:
            raise RuntimeError("context is gone")
        return dict(self.state or {})

    async def close(self) -> None:
        self.closed = True

    def set_default_timeout(self, _ms: int) -> None:
        return None

    async def add_init_script(self, script: str) -> None:
        return None

    async def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page


class _FakeBrowser:
    def __init__(self) -> None:
        self.connected = True
        self.contexts: list[_FakeContext] = []

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.connected = False

    async def new_context(self, **kwargs: Any) -> _FakeContext:
        context = _FakeContext()
        context.kwargs = kwargs
        self.contexts.append(context)
        return context


class _FakeChromium:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.browsers: list[_FakeBrowser] = []

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launches.append(kwargs)
        browser = _FakeBrowser()
        self.browsers.append(browser)
        return browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()

    async def stop(self) -> None:
        return None


def _live_session(config: BrowserToolConfig, *, last_used: float, state: dict[str, Any] | None = None,
                  pid: int | None = None) -> _BrowserSession:
    session = _BrowserSession(config)
    session._pw = _FakePlaywright()
    session._browser = _FakeBrowser()
    session._context = _FakeContext(state)
    session.page = _FakePage()
    session.last_used = last_used
    session.browser_pid = pid
    return session


async def _no_cloud(_self: _BrowserSession) -> bool:
    return False


class _LimitsCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_sessions = dict(browser_module._SESSIONS)
        browser_module._SESSIONS.clear()
        self.scopes: list[dict[str, Any]] = []
        self.oom: list[tuple[int, int]] = []
        pid_snapshots = iter([set(), {5001}, {5001}, {5001, 5002}, {5001, 5002}, {5001, 5002, 5003}])

        def fake_pids() -> set[int]:
            return next(pid_snapshots)

        def fake_scope(prefix: str, pids: list[int], **kwargs: Any) -> browser_module.ScopeResult:
            self.scopes.append({"prefix": prefix, "pids": list(pids), **kwargs})
            return browser_module.ScopeResult(unit=f"{prefix}.scope")

        def fake_oom(pid: int, value: int) -> bool:
            self.oom.append((pid, value))
            return True

        patches = [
            patch.object(browser_module, "_chromium_main_pids", fake_pids),
            patch.object(browser_module, "start_scope", fake_scope),
            patch.object(browser_module, "raise_oom_score", fake_oom),
            patch.object(browser_module, "_tree_pids", lambda pid: [pid, pid + 100]),
            patch.object(browser_module._BrowserSession, "_connect_cloud_browser", _no_cloud),
            # The real machine's free memory must not decide these tests.
            patch.object(browser_module, "_free_memory_share", lambda: self.free_share),
        ]
        self.free_share = 1.0
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def asyncTearDown(self) -> None:
        await browser_module._stop_watchdog()
        browser_module._SESSIONS.clear()
        browser_module._SESSIONS.update(self._saved_sessions)


class ConfigDefaultsTests(unittest.TestCase):
    def test_defaults_and_aliases(self) -> None:
        config = BrowserToolConfig()
        self.assertEqual(config.max_live_browsers, 3)
        self.assertEqual(config.idle_close_minutes, 15.0)
        self.assertEqual(config.memory_limit_mb, 0)
        parsed = BrowserToolConfig.model_validate(
            {"maxLiveBrowsers": 1, "idleCloseMinutes": 2.5, "memoryLimitMb": 700}
        )
        self.assertEqual((parsed.max_live_browsers, parsed.idle_close_minutes, parsed.memory_limit_mb), (1, 2.5, 700))

    def test_memory_cap_auto_explicit_and_off(self) -> None:
        class _Ram:
            def __init__(self, total: int) -> None:
                self.total = total

        with patch.object(psutil, "virtual_memory", lambda: _Ram(8 * 1024 * _MB)):
            self.assertEqual(browser_module._memory_cap_bytes(BrowserToolConfig()), 2048 * _MB)
        with patch.object(psutil, "virtual_memory", lambda: _Ram(64 * 1024 * _MB)):
            self.assertEqual(browser_module._memory_cap_bytes(BrowserToolConfig()), 4096 * _MB)
        self.assertEqual(browser_module._memory_cap_bytes(BrowserToolConfig(memory_limit_mb=900)), 900 * _MB)
        self.assertEqual(browser_module._memory_cap_bytes(BrowserToolConfig(memory_limit_mb=-1)), 0)


class _FakeProc:
    def __init__(self, pid: int, ppid: int, cmdline: list[str]) -> None:
        self.pid = pid
        self._ppid = ppid
        self._cmdline = cmdline

    def cmdline(self) -> list[str]:
        return self._cmdline

    def ppid(self) -> int:
        return self._ppid


class MainPidDetectionTests(unittest.TestCase):
    def test_only_the_browser_main_process_is_kept(self) -> None:
        procs = [
            _FakeProc(10, 1, ["node", "cli.js", "run-driver"]),
            _FakeProc(11, 10, ["chrome", "--headless", "--remote-debugging-pipe"]),
            _FakeProc(12, 11, ["chrome", "--type=renderer", "--remote-debugging-pipe"]),
            _FakeProc(13, 11, ["chrome", "--type=gpu-process"]),
        ]

        class _Root:
            def children(self, recursive: bool = False) -> list[_FakeProc]:
                return procs

        with patch.object(psutil, "Process", lambda *args: _Root()):
            self.assertEqual(browser_module._chromium_main_pids(), {11})
        self.assertEqual(browser_module._new_browser_pid({11}, {11, 20}), 20)
        self.assertIsNone(browser_module._new_browser_pid({11}, {11}))
        self.assertIsNone(browser_module._new_browser_pid(set(), {20, 21}))


class ScopeTests(unittest.TestCase):
    def test_start_scope_gets_the_cap(self) -> None:
        calls: list[dict[str, Any]] = []
        oom: list[tuple[int, int]] = []

        def fake_scope(prefix: str, pids: list[int], **kwargs: Any) -> browser_module.ScopeResult:
            calls.append({"prefix": prefix, "pids": list(pids), **kwargs})
            return browser_module.ScopeResult(reason="not Linux")

        def fake_oom(pid: int, value: int) -> bool:
            oom.append((pid, value))
            return True

        with (
            patch.object(browser_module, "start_scope", fake_scope),
            patch.object(browser_module, "raise_oom_score", fake_oom),
            patch.object(browser_module, "_tree_pids", lambda pid: [pid, 77]),
        ):
            result = browser_module._isolate_browser(70, 512 * _MB)
            self.assertIsNone(browser_module._isolate_browser(71, 0))
        self.assertEqual(result, browser_module.ScopeResult(reason="not Linux"))
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertTrue(call["prefix"].startswith(f"navin-browser-{os.getpid()}-"))
        self.assertEqual(call["pids"], [70, 77])
        self.assertEqual(call["memory_max"], 512 * _MB)
        self.assertEqual(call["memory_swap_max"], 256 * _MB)
        self.assertIn((70, 300), oom)
        self.assertIn((77, 300), oom)
        self.assertIn((71, 300), oom)


class ConcurrencyTests(_LimitsCase):
    async def test_lru_idle_session_is_released_at_the_limit(self) -> None:
        config = BrowserToolConfig(max_live_browsers=2, memory_limit_mb=600)
        oldest = _live_session(config, last_used=10.0, state=_STATE)
        newer = _live_session(config, last_used=20.0)
        browser_module._SESSIONS.update({"a": oldest, "b": newer})
        fresh = _BrowserSession(config)
        fresh._pw = _FakePlaywright()
        browser_module._SESSIONS["c"] = fresh

        page = await fresh.ensure_page()

        self.assertIsNotNone(page)
        self.assertIsNone(oldest._browser)
        self.assertIn("make room", oldest.closed_reason)
        self.assertEqual(oldest._storage_state, _STATE)
        self.assertTrue(newer.local_browser_live())
        self.assertEqual(newer.closed_reason, "")
        self.assertTrue(fresh.local_browser_live())
        self.assertEqual(fresh.browser_pid, 5001)
        self.assertEqual(len(self.scopes), 1)
        self.assertEqual(self.scopes[0]["memory_max"], 600 * _MB)
        self.assertEqual(self.scopes[0]["memory_swap_max"], 300 * _MB)
        self.assertEqual(self.scopes[0]["pids"], [5001, 5101])
        self.assertIn((5001, 300), self.oom)
        self.assertIsNotNone(browser_module._WATCHDOG_TASK)

    async def test_busy_sessions_are_never_evicted_and_the_wait_ends_with_an_error(self) -> None:
        config = BrowserToolConfig(max_live_browsers=2)
        busy = _live_session(config, last_used=1.0)
        watched = _live_session(config, last_used=2.0)
        watched.user_control = True
        browser_module._SESSIONS.update({"busy": busy, "watched": watched})

        async def launch_only(self: _BrowserSession) -> Any:
            await self._launch_local({})
            return _FakePage()

        await busy.lock.acquire()
        try:
            with (
                patch.object(browser_module, "_LIMIT_WAIT_S", 0.2),
                patch.object(browser_module, "_LIMIT_POLL_S", 0.05),
                patch.object(browser_module._BrowserSession, "ensure_page", launch_only),
            ):
                result = await BrowserTool(config=config).execute(action="snapshot")
        finally:
            busy.lock.release()

        self.assertIsInstance(result, ToolResult)
        self.assertTrue(result.is_error)
        self.assertIn("2 browsers are already open", result)
        self.assertIn("maxLiveBrowsers", result)
        self.assertIn("action=close", result)
        self.assertTrue(busy.local_browser_live())
        self.assertTrue(watched.local_browser_live())
        self.assertEqual(busy.closed_reason, "")
        self.assertEqual(self.scopes, [])

    async def test_a_live_view_alone_does_not_keep_a_browser_open(self) -> None:
        config = BrowserToolConfig(max_live_browsers=1, memory_limit_mb=-1)
        watched = _live_session(config, last_used=1.0, state=_STATE)
        watched._live_started = True
        browser_module._SESSIONS["watched"] = watched
        fresh = _BrowserSession(config)
        fresh._pw = _FakePlaywright()
        browser_module._SESSIONS["fresh"] = fresh

        await fresh._launch_local({})

        self.assertIsNone(watched._browser)
        self.assertFalse(watched._live_started)
        self.assertIn("make room", watched.closed_reason)
        self.assertEqual(watched._storage_state, _STATE)
        self.assertTrue(fresh.local_browser_live())

    async def test_no_limit_when_zero(self) -> None:
        config = BrowserToolConfig(max_live_browsers=0, memory_limit_mb=-1)
        others = {f"s{i}": _live_session(config, last_used=float(i)) for i in range(4)}
        browser_module._SESSIONS.update(others)
        fresh = _BrowserSession(config)
        fresh._pw = _FakePlaywright()
        await fresh._launch_local({})
        self.assertTrue(all(s.local_browser_live() for s in others.values()))
        self.assertTrue(fresh.local_browser_live())
        self.assertEqual(self.scopes, [])


class ReleaseTests(_LimitsCase):
    async def test_release_keeps_logins_for_the_next_context(self) -> None:
        config = BrowserToolConfig()
        session = _live_session(config, last_used=0.0, state=_STATE)
        browser_module._SESSIONS["default"] = session

        await session.release("test reason")

        self.assertIsNone(session._browser)
        self.assertIsNone(session.browser_pid)
        self.assertEqual(session._storage_state, _STATE)
        self.assertEqual(session.closed_reason, "test reason")

        session._pw = _FakePlaywright()
        await session.ensure_page()
        new_browser = session._pw.chromium.browsers[0]
        self.assertEqual(new_browser.contexts[0].kwargs.get("storage_state"), _STATE)
        self.assertIsNone(session._storage_state)

    async def test_release_without_state_still_closes(self) -> None:
        session = _live_session(BrowserToolConfig(), last_used=0.0)
        session._context = _FakeContext(fail_state=True)
        await session.release("over the limit")
        self.assertIsNone(session._browser)
        self.assertIsNone(session._storage_state)
        self.assertEqual(session.closed_reason, "over the limit")

    async def test_notice_is_reported_once(self) -> None:
        config = BrowserToolConfig()
        session = _live_session(config, last_used=0.0, state=_STATE)
        browser_module._SESSIONS["default"] = session
        await session.release("closed after 16 minutes without use")
        tool = BrowserTool(config=config)

        first = await tool.execute(action="console")
        second = await tool.execute(action="console")

        self.assertIn("Navin closed this chat's browser earlier (closed after 16 minutes without use)", first)
        self.assertIn("logins kept", first)
        self.assertNotIn("Navin closed", second)


class WatchdogTests(_LimitsCase):
    async def test_idle_reaper_closes_after_the_delay(self) -> None:
        config = BrowserToolConfig(idle_close_minutes=15, memory_limit_mb=-1)
        stale = _live_session(config, last_used=1000.0 - 16 * 60, state=_STATE)
        recent = _live_session(config, last_used=1000.0 - 60)
        browser_module._SESSIONS.update({"stale": stale, "recent": recent})

        with patch.object(browser_module, "_now", lambda: 1000.0):
            left = await browser_module._watch_pass()

        self.assertTrue(left)
        self.assertIsNone(stale._browser)
        self.assertEqual(stale.closed_reason, "closed after 16 minutes without use")
        self.assertEqual(stale._storage_state, _STATE)
        self.assertTrue(recent.local_browser_live())

        with patch.object(browser_module, "_now", lambda: 1000.0 + 20 * 60):
            left = await browser_module._watch_pass()
        self.assertFalse(left)
        self.assertIsNone(recent._browser)

    async def test_low_machine_memory_closes_idle_browsers_oldest_first(self) -> None:
        config = BrowserToolConfig(idle_close_minutes=0, memory_limit_mb=-1)
        oldest = _live_session(config, last_used=1.0, state=_STATE)
        newer = _live_session(config, last_used=2.0)
        busy = _live_session(config, last_used=0.5)
        busy.user_control = True
        browser_module._SESSIONS.update({"oldest": oldest, "newer": newer, "busy": busy})
        # One read for the pass, then one before each candidate (busy, oldest, newer).
        shares = iter([0.05, 0.05, 0.05, 0.30])

        with patch.object(browser_module, "_free_memory_share", lambda: next(shares)):
            left = await browser_module._watch_pass()

        self.assertTrue(left)
        self.assertIsNone(oldest._browser)
        self.assertEqual(oldest.closed_reason, "closed because the machine had only 5% of its memory free")
        self.assertEqual(oldest._storage_state, _STATE)
        # Enough memory came back: the next idle browser stays open.
        self.assertTrue(newer.local_browser_live())
        self.assertTrue(busy.local_browser_live())

    async def test_idle_reaper_off_and_busy_sessions_kept(self) -> None:
        never = _live_session(BrowserToolConfig(idle_close_minutes=0, memory_limit_mb=-1), last_used=0.0)
        busy = _live_session(BrowserToolConfig(idle_close_minutes=1, memory_limit_mb=-1), last_used=0.0)
        busy.user_control = True
        browser_module._SESSIONS.update({"never": never, "busy": busy})
        with patch.object(browser_module, "_now", lambda: 1e6):
            await browser_module._watch_pass()
        self.assertTrue(never.local_browser_live())
        self.assertTrue(busy.local_browser_live())

    async def test_memory_cap_releases_idle_and_kills_busy(self) -> None:
        config = BrowserToolConfig(memory_limit_mb=100, idle_close_minutes=0)
        idle = _live_session(config, last_used=0.0, state=_STATE, pid=111)
        busy = _live_session(config, last_used=0.0, pid=222)
        small = _live_session(config, last_used=0.0, pid=333)
        browser_module._SESSIONS.update({"idle": idle, "default": busy, "small": small})
        killed: list[int] = []
        usage = {111: 200 * _MB, 222: 300 * _MB, 333: 50 * _MB}

        def fake_kill(pid: int) -> int:
            killed.append(pid)
            busy._browser.connected = False
            return 3

        await busy.lock.acquire()
        try:
            with (
                patch.object(browser_module, "_tree_rss_bytes", lambda pid: usage[pid]),
                patch.object(browser_module, "_kill_tree", fake_kill),
            ):
                await browser_module._watch_pass()
        finally:
            busy.lock.release()

        self.assertIsNone(idle._browser)
        self.assertEqual(idle.closed_reason, "used 200 MB, over the 100 MB limit for one browser")
        self.assertEqual(idle._storage_state, _STATE)
        self.assertEqual(killed, [222])
        self.assertEqual(busy.closed_reason, "used 300 MB, over the 100 MB limit for one browser")
        self.assertTrue(busy._killed_by_navin)
        self.assertTrue(small.local_browser_live())

        result = await BrowserTool(config=config).execute(action="console")
        self.assertIn("Navin stopped this chat's browser during an earlier action", result)
        self.assertIsNone(busy._browser)
        self.assertFalse(busy._killed_by_navin)
        self.assertEqual(busy.closed_reason, "")

    async def test_shutdown_stops_the_watchdog(self) -> None:
        session = _live_session(BrowserToolConfig(), last_used=0.0)
        browser_module._SESSIONS["default"] = session
        browser_module._ensure_watchdog()
        task = browser_module._WATCHDOG_TASK
        self.assertIsNotNone(task)
        closed = await browser_module.shutdown_browser_sessions()
        self.assertEqual(closed, 1)
        self.assertIsNone(browser_module._WATCHDOG_TASK)
        await asyncio.sleep(0)
        self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()
