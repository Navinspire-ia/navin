# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Desktop ownership, live controls and interruption at native input boundaries."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.agent.tools import browser, computer
from navin.agent.tools.context import request_context
from navin.agent.tools.registry import ToolRegistry
from navin.computer.base import ComputerBackend, WindowInfo
from navin.webui.live_control import close_session, dispatch_input
from tests.test_computer_tool import FakeBackend, _ctx


class DesktopControlTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stop = mock.patch("navin.computer.policy.stop_file", return_value=Path(self.tmp.name) / "STOP")
        self.stop.start()
        computer._SESSIONS.clear()
        browser._SESSIONS.clear()
        self.backend = FakeBackend()
        self.config = computer.ComputerToolConfig(
            enabled=True, ask="never", settle_ms=0, audit_log=False,
            audit_screenshots=False, live_view=False,
        )
        self.tool = computer.ComputerTool(config=self.config, backend_factory=lambda: self.backend)

    async def asyncTearDown(self) -> None:
        await computer.shutdown_computer_sessions()
        browser._SESSIONS.clear()
        self.stop.stop()
        self.tmp.cleanup()

    async def call(self, *, session_key="test:session", **kwargs):
        with request_context(_ctx(session_key=session_key)):
            return await self.tool.execute(**kwargs)

    async def start(self) -> str:
        await self.call(action="screenshot")
        return f"desktop-{computer._SESSIONS['test:session'].id}"

    async def test_aliases_work_through_the_agent_tool_registry(self) -> None:
        registry = ToolRegistry()
        registry.register(self.tool)
        with request_context(_ctx()):
            await registry.execute("computer", {"action": "screenshot"})
            typed = await registry.execute("computer", {"action": "type_text", "text": "registry input"})
            self.assertIn("typed", str(typed))
            self.assertIn(("type", "registry input"), self.backend.calls)
            closed = await registry.execute("computer", {"action": "done"})
            self.assertFalse(getattr(closed, "is_error", False), str(closed))
        self.assertNotIn("test:session", computer._SESSIONS)

    async def test_unicode_input_restores_a_borrowed_keyboard_mapping(self) -> None:
        from navin.computer.x11 import X11Backend

        backend = X11Backend()
        display = mock.MagicMock()
        display.display.info.min_keycode = 8
        display.display.info.max_keycode = 9
        original = (67, 68, 69, 70, 71, 72)
        display.get_keyboard_mapping.side_effect = [[(65, 66), original], [original]]
        with mock.patch.object(backend, "_xd", return_value=display), mock.patch("navin.computer.x11.time.sleep"):
            backend._remap_spare(0x010003A9)
            backend._restore_spare()
        display.change_keyboard_mapping.assert_called_with(9, [original])
        self.assertIsNone(backend._spare_keycode)

    async def test_takeover_blocks_agent_until_user_releases_it(self) -> None:
        live_id = await self.start()
        result = await dispatch_input("test:session", "takeover", {}, live_id=live_id)
        self.assertTrue(result["user_control"])
        refused = await self.call(action="type", text="agent text")
        self.assertTrue(refused.is_error)
        self.assertIn("user has control", str(refused))
        self.assertTrue((await self.call(action="resume")).is_error)
        await dispatch_input("test:session", "text", {"text": "user text"}, live_id=live_id)
        self.assertNotIn(("type", "agent text"), self.backend.calls)
        self.assertIn(("type", "user text"), self.backend.calls)
        await dispatch_input("test:session", "release", {}, live_id=live_id)
        self.assertIn("typed", str(await self.call(action="type", text="agent text")))

    async def test_inputs_target_the_displayed_session_when_browser_also_exists(self) -> None:
        live_id = await self.start()
        page = browser._BrowserSession(browser.BrowserToolConfig())
        page.page = mock.MagicMock()
        page.page.is_closed.return_value = False
        page.page.keyboard.type = mock.AsyncMock()
        browser._SESSIONS["test:session"] = page
        with self.assertRaisesRegex(ValueError, "both a desktop and browser"):
            await dispatch_input("test:session", "text", {"text": "wrong"})
        await dispatch_input("test:session", "text", {"text": "desktop"}, live_id=live_id)
        page.page.keyboard.type.assert_not_called()
        self.assertIn(("type", "desktop"), self.backend.calls)
        await close_session("test:session", live_id=live_id)
        self.assertIs(browser._SESSIONS["test:session"], page)
        self.assertNotIn("test:session", computer._SESSIONS)
        with self.assertRaisesRegex(ValueError, "no longer open"):
            await dispatch_input("test:session", "text", {"text": "stale"}, live_id=live_id)
        page.page.keyboard.type.assert_not_called()

    async def test_browser_takeover_blocks_navigation(self) -> None:
        session = browser._BrowserSession(browser.BrowserToolConfig())
        session.user_control = True
        browser._SESSIONS["test:session"] = session
        tool = browser.BrowserTool()
        with request_context(_ctx()), mock.patch.object(tool, "_dispatch", new_callable=mock.AsyncMock) as dispatch:
            result = await tool.execute(action="navigate", url="https://example.com")
        self.assertTrue(result.is_error)
        dispatch.assert_not_called()

    async def test_one_conversation_owns_the_physical_desktop_until_close(self) -> None:
        await self.call(action="screenshot", session_key="one")
        await self.call(action="screenshot", session_key="two")
        await self.call(action="type", text="one", session_key="one")
        result = await self.call(action="type", text="two", session_key="two")
        self.assertTrue(result.is_error)
        self.assertIn("another conversation", str(result))
        self.assertNotIn(("type", "two"), self.backend.calls)
        await self.call(action="close", session_key="one")
        await self.call(action="type", text="two", session_key="two")
        self.assertIn(("type", "two"), self.backend.calls)

    async def test_long_typing_stops_at_next_chunk_when_user_takes_over(self) -> None:
        live_id = await self.start()
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        original = self.backend.type_text

        def blocking_type(text, *, delay_ms):
            original(text, delay_ms=delay_ms)
            loop.call_soon_threadsafe(started.set)
            release.wait(2)

        self.backend.type_text = blocking_type
        action = asyncio.create_task(self.call(action="type", text="x" * 256))
        await asyncio.wait_for(started.wait(), 2)
        takeover = asyncio.create_task(dispatch_input("test:session", "takeover", {}, live_id=live_id))
        await asyncio.sleep(0)
        release.set()
        result = await asyncio.wait_for(action, 2)
        await asyncio.wait_for(takeover, 2)
        self.assertTrue(result.is_error)
        self.assertEqual([call for call in self.backend.calls if call[0] == "type"], [("type", "x" * 64)])

    async def test_cancellation_keeps_native_worker_inside_display_lock(self) -> None:
        await self.start()
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def blocking_type(text, *, delay_ms):
            loop.call_soon_threadsafe(started.set)
            release.wait(2)

        self.backend.type_text = blocking_type
        action = asyncio.create_task(self.call(action="type", text="first"))
        await asyncio.wait_for(started.wait(), 2)
        action.cancel()
        second = asyncio.create_task(self.call(action="screenshot", session_key="second"))
        await asyncio.sleep(0.02)
        self.assertFalse(second.done(), "a native thread is still using the display")
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await action
        self.assertIsInstance(await asyncio.wait_for(second, 2), list)

    async def test_hold_key_releases_modifiers_on_takeover(self) -> None:
        live_id = await self.start()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        original = self.backend.key

        def key(combo, *, down=None):
            original(combo, down=down)
            if down:
                loop.call_soon_threadsafe(started.set)

        self.backend.key = key
        action = asyncio.create_task(self.call(action="hold_key", text="shift", duration=30))
        await asyncio.wait_for(started.wait(), 2)
        takeover = asyncio.create_task(dispatch_input("test:session", "takeover", {}, live_id=live_id))
        result = await asyncio.wait_for(action, 2)
        await takeover
        self.assertTrue(result.is_error)
        self.assertIn(("key", "shift", False), self.backend.calls)

    async def test_settings_changes_apply_without_restarting_the_agent(self) -> None:
        current = self.config
        self.tool = computer.ComputerTool(
            config=current, backend_factory=lambda: self.backend, config_loader=lambda: current,
        )
        await self.start()
        current = current.model_copy(update={"allowed_apps": ["Calculator"]})
        result = await self.call(action="type", text="blocked")
        self.assertTrue(result.is_error)
        self.assertNotIn(("type", "blocked"), self.backend.calls)
        current = current.model_copy(update={"enabled": False})
        self.assertIn("disabled", str(await self.call(action="screenshot")))
        self.assertNotIn("test:session", computer._SESSIONS)

    async def test_protected_screen_is_not_captured_after_wait_or_resume(self) -> None:
        await self.start()
        self.backend.windows = lambda: [WindowInfo("vault", "Bitwarden", 0, 0, 500, 500, app="Bitwarden", active=True)]
        shots = self.backend.calls.count(("screenshot",))
        for action in ("wait", "resume"):
            result = await self.call(action=action, duration=0.001)
            self.assertTrue(result.is_error)
        self.assertEqual(self.backend.calls.count(("screenshot",)), shots)

    async def test_hidden_protected_window_cannot_be_snapshotted(self) -> None:
        await self.start()
        original = self.backend.windows()
        self.backend.windows = lambda: [*original, WindowInfo("vault", "Bitwarden", 0, 0, 500, 500)]
        result = await self.call(action="snapshot", window="vault")
        self.assertTrue(result.is_error)
        self.assertNotIn(("snapshot", "vault"), self.backend.calls)

    async def test_out_of_bounds_coordinates_do_not_silently_click_screen_edges(self) -> None:
        await self.start()
        for coordinate in ([1366, 10], [-1, 10], [10, 683], [float("nan"), 10], [float("inf"), 10]):
            result = await self.call(action="click", coordinate=coordinate)
            self.assertTrue(result.is_error, result)
        self.assertFalse(any(call[0] == "click" for call in self.backend.calls))

    async def test_audit_links_each_saved_verification_screenshot(self) -> None:
        self.config.audit_log = True
        self.config.audit_screenshots = True
        folder = Path(self.tmp.name) / "audit"
        with mock.patch("navin.computer.session.ComputerSession._artifact_dir", return_value=folder):
            await self.start()
            await self.call(action="type", text="recorded")
        rows = [json.loads(line) for line in (folder / "actions.jsonl").read_text().splitlines()]
        self.assertTrue(all(Path(row["screenshot"]).is_file() for row in rows))

    async def test_failed_focus_is_reported_and_old_refs_are_invalidated(self) -> None:
        await self.start()
        await self.call(action="snapshot")
        await self.call(action="type", text="changed layout")
        self.assertIn("not in the last snapshot", str(await self.call(action="click", ref=0)))
        self.backend.focus_window = lambda window_id: False
        self.assertIn("could not focus", str(await self.call(action="focus_window", window="calc")))


class DragCleanupTest(unittest.TestCase):
    def test_mouse_is_released_when_drag_movement_fails(self) -> None:
        backend = FakeBackend()
        original = backend.move

        def move(x, y):
            if x > 100:
                raise RuntimeError("display disconnected")
            original(x, y)

        backend.move = move
        with self.assertRaisesRegex(RuntimeError, "display disconnected"):
            ComputerBackend.drag(backend, 100, 100, 200, 200)
        self.assertFalse([call for call in backend.calls if call[0] == "button"][-1][-1])


class ComputerSkillWiringTest(unittest.TestCase):
    def test_computer_skill_is_preloaded_exactly_once_when_tool_is_enabled(self) -> None:
        from navin.agent.loop import AgentLoop
        from navin.bus.events import InboundMessage

        msg = InboundMessage(channel="test", sender_id="user", chat_id="chat", content="continue")
        loop = SimpleNamespace(tools={"computer": object()})
        names = AgentLoop._preload_skills_for_message(loop, msg)
        self.assertEqual(names.count("computer-use"), 1)
        loop.tools = {}
        self.assertNotIn("computer-use", AgentLoop._preload_skills_for_message(loop, msg) or [])
