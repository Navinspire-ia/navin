# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The desktop tool: coordinate mapping, safety gates and the action vocabulary.

Everything runs against a fake backend that records what it was asked to do,
so the tests are the same on a headless CI box and on a developer's desktop.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from navin.agent.approval import (
    ApprovalDecision,
    ApprovalRequest,
    bind_approval_gate,
    reset_approval_gate,
)
from navin.agent.tools.computer import (
    _SESSIONS,
    ComputerTool,
    ComputerToolConfig,
    dispatch_live_input,
)
from navin.agent.tools.context import RequestContext, request_context
from navin.computer.base import (
    Check,
    ComputerBackend,
    PermissionMissingError,
    ScreenInfo,
    Screenshot,
    UIElement,
    WindowInfo,
)
from navin.computer.detect import detect_platform
from navin.computer.keys import KeyCombo, parse_combo
from navin.computer.policy import AppPolicy, engage_stop, release_stop
from navin.computer.safety import TakeoverMonitor, classify_risk, risk_for_text


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 60, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeBackend(ComputerBackend):
    name = "fake"
    label = "Fake desktop"

    def __init__(self, width: int = 2000, height: int = 1000) -> None:
        self.width = width
        self.height = height
        self.calls: list[tuple[Any, ...]] = []
        self.cursor = (10, 10)
        self._png = _png(width, height)

    def screen(self) -> ScreenInfo:
        return ScreenInfo(width=self.width, height=self.height)

    def screenshot(self) -> Screenshot:
        self.calls.append(("screenshot",))
        return Screenshot(png=self._png, width=self.width, height=self.height)

    def cursor_position(self) -> tuple[int, int]:
        return self.cursor

    def move(self, x: int, y: int) -> None:
        self.calls.append(("move", x, y))
        self.cursor = (x, y)

    def button(self, x: int, y: int, button: str, *, down: bool) -> None:
        self.calls.append(("button", x, y, button, down))

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.calls.append(("click", x, y, button, count))
        self.cursor = (x, y)

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.calls.append(("scroll", x, y, dx, dy))
        self.cursor = (x, y)

    def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        self.calls.append(("type", text))

    def key(self, combo: KeyCombo, *, down: bool | None = None) -> None:
        self.calls.append(("key", combo.label(), down))

    def windows(self) -> list[WindowInfo]:
        return [
            WindowInfo(
                id="7",
                title="Notes - Untitled",
                left=100,
                top=50,
                width=800,
                height=600,
                app="notes",
                active=True,
            ),
            WindowInfo(
                id="9", title="Calculator", left=1000, top=100, width=400, height=500, app="calc"
            ),
        ]

    def focus_window(self, window_id: str) -> bool:
        self.calls.append(("focus", window_id))
        return True

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[UIElement]:
        self.calls.append(("snapshot", window_id))
        return [
            UIElement(ref=0, role="Button", name="Save", left=200, top=100, width=100, height=50),
            UIElement(
                ref=1,
                role="Edit",
                name="Body",
                left=200,
                top=200,
                width=600,
                height=300,
                value="hello",
            ),
        ]


def _ctx(model: str = "gpt-4o", **meta: Any) -> RequestContext:
    class _Runtime:
        def __init__(self) -> None:
            self.model = model

    return RequestContext(
        channel="test",
        chat_id="c1",
        session_key=meta.pop("session_key", "test:session"),
        runtime=_Runtime(),
        turn_id=meta.pop("turn_id", "turn-1"),
    )


def _image_blocks(result: Any) -> tuple[list[dict[str, Any]], str]:
    assert isinstance(result, list), result
    images = [b for b in result if b.get("type") == "image_url"]
    texts = " ".join(b.get("text", "") for b in result if b.get("type") == "text")
    return images, texts


class KeyParsingTest(unittest.TestCase):
    def test_common_spellings(self) -> None:
        self.assertEqual(parse_combo("ctrl+s"), KeyCombo(("ctrl",), "s"))
        self.assertEqual(parse_combo("Ctrl+Shift+T"), KeyCombo(("ctrl", "shift"), "T"))
        self.assertEqual(parse_combo("Return"), KeyCombo((), "enter"))
        self.assertEqual(parse_combo("cmd+space"), KeyCombo(("meta",), "space"))
        self.assertEqual(parse_combo("super"), KeyCombo((), "meta"))
        self.assertEqual(parse_combo("alt+F4"), KeyCombo(("alt",), "f4"))
        self.assertEqual(parse_combo("PageDown"), KeyCombo((), "pagedown"))
        self.assertEqual(parse_combo("ctrl++"), KeyCombo(("ctrl",), "+"))
        self.assertEqual(parse_combo("Control_L+c"), KeyCombo(("ctrl",), "c"))

    def test_garbage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_combo("")
        with self.assertRaises(ValueError):
            parse_combo("ctrl+")
        with self.assertRaises(ValueError):
            parse_combo("notakey")


class SafetyTest(unittest.TestCase):
    def test_destructive_shortcuts_are_named(self) -> None:
        self.assertEqual(classify_risk("key", {"text": "alt+f4"}).rule, "close-window")
        self.assertEqual(classify_risk("key", {"text": "F4+alt"}).rule, "close-window")
        self.assertEqual(classify_risk("key", {"text": "win+l"}).rule, "lock-session")
        self.assertIsNone(classify_risk("key", {"text": "ctrl+s"}))
        self.assertIsNone(classify_risk("left_click", {"coordinate": [1, 2]}))

    def test_destructive_typed_commands(self) -> None:
        self.assertEqual(risk_for_text("rm -rf ~/projects").rule, "rm-rf")
        self.assertEqual(risk_for_text("sudo apt purge navin").rule, "sudo")
        self.assertEqual(risk_for_text("git push --force origin main").rule, "git-destructive")
        self.assertEqual(risk_for_text("Remove-Item C:\\x -Recurse").rule, "remove-item")
        self.assertIsNone(risk_for_text("Bonjour, voici le rapport du jour"))
        self.assertIsNone(risk_for_text("format the paragraph nicely"))

    def test_takeover_monitor(self) -> None:
        monitor = TakeoverMonitor(threshold_px=40)
        self.assertIsNone(monitor.observe((500, 500)))
        monitor.left_cursor_at((500, 500))
        self.assertIsNone(monitor.observe((510, 505)))
        reason = monitor.observe((700, 500))
        self.assertIn("someone is using this computer", reason or "")
        self.assertTrue(monitor.paused)
        monitor.resume()
        self.assertFalse(monitor.paused)
        self.assertIn("top-left corner", monitor.observe((0, 0)) or "")


class DetectTest(unittest.TestCase):
    def test_explicit_display_forces_x11_on_linux(self) -> None:
        import sys

        if sys.platform != "linux":
            self.skipTest("linux only")
        choice = detect_platform(preferred="auto", display=":99")
        self.assertEqual(choice.name, "x11")

    def test_headless_linux_has_no_backend(self) -> None:
        import sys

        if sys.platform != "linux":
            self.skipTest("linux only")
        saved = {
            k: os.environ.pop(k, None) for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")
        }
        try:
            self.assertEqual(detect_platform().name, "none")
            self.assertEqual(detect_platform(preferred="x11").name, "none")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class ComputerToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _SESSIONS.clear()
        self.backend = FakeBackend()
        self.config = ComputerToolConfig(
            enabled=True,
            ask="destructive",
            settle_ms=0,
            audit_log=False,
            audit_screenshots=False,
            live_view=False,
        )
        self.tool = ComputerTool(config=self.config, backend_factory=lambda: self.backend)

    def tearDown(self) -> None:
        _SESSIONS.clear()

    async def _run(self, **kwargs: Any) -> Any:
        with request_context(_ctx()):
            return await self.tool.execute(**kwargs)

    async def test_screenshot_is_scaled_and_returned_as_image(self) -> None:
        result = await self._run(action="screenshot")
        images, text = _image_blocks(result)
        self.assertEqual(len(images), 1)
        self.assertIn("screenshot 1366x683 = screen 2000x1000", text)
        self.assertNotIn("If no image appears", text)

    async def test_blind_model_gets_setup_guidance_before_accessing_the_desktop(self) -> None:
        with request_context(_ctx(model="deepseek-chat")):
            result = await self.tool.execute(action="screenshot")
        self.assertTrue(result.is_error)
        self.assertIn("Computer needs a vision model", result)
        self.assertIn("Settings > Computer", result)
        self.assertFalse(self.backend.calls)
        self.assertIn("Wait", result.recovery_hint)

    async def test_screen_is_the_public_alias_and_returns_the_real_image(self) -> None:
        result = await self._run(action="screen")
        images, text = _image_blocks(result)
        self.assertEqual(len(images), 1)
        self.assertTrue(text.startswith("Screen."))
        self.assertTrue(self.tool.call_read_only({"action": "screen"}))

    async def test_permission_recovery_uses_the_same_tool_without_a_cli(self) -> None:
        with mock.patch.object(self.backend, "screenshot", side_effect=PermissionMissingError("Screen Recording needs consent", permission="screen_recording")):
            result = await self._run(action="screen")
        self.assertTrue(result.is_error)
        self.assertIn("action=permissions, kind=screen_recording", result)
        self.assertNotIn("navin computer permissions", result)
        self.assertIn("wait for their consent", result.recovery_hint)

    async def test_permissions_do_not_require_vision_or_send_desktop_input(self) -> None:
        with request_context(_ctx(model="deepseek-chat")), \
             mock.patch.object(self.backend, "request_permissions", return_value=[Check("accessibility", False, "awaiting consent")]) as request:
            result = await self.tool.execute(action="permissions")
        request.assert_called_once_with("all")
        self.assertIn("Awaiting your consent", result)
        self.assertFalse(self.backend.calls)

    async def test_stopped_computer_does_not_open_a_permission_request(self) -> None:
        engage_stop("paused for test")
        try:
            with mock.patch.object(self.backend, "request_permissions") as request:
                result = await self._run(action="permissions")
            self.assertTrue(result.is_error)
            request.assert_not_called()
        finally:
            release_stop()

    async def test_click_maps_screenshot_pixels_to_screen_pixels(self) -> None:
        await self._run(action="screenshot")
        result = await self._run(action="left_click", coordinate=[683, 341])
        clicks = [c for c in self.backend.calls if c[0] == "click"]
        self.assertEqual(len(clicks), 1)
        _, x, y, button, count = clicks[0]
        self.assertAlmostEqual(x, 1000, delta=2)
        self.assertAlmostEqual(y, 499, delta=2)
        self.assertEqual((button, count), ("left", 1))
        images, text = _image_blocks(result)
        self.assertEqual(len(images), 1, "every action returns a fresh screenshot")
        self.assertIn("left_click at (683, 341)", text)

    async def test_aliases_and_anthropic_names_both_work(self) -> None:
        await self._run(action="screenshot")
        await self._run(action="click", x=100, y=100, button="right")
        await self._run(action="double_click", coordinate=[10, 10])
        await self._run(action="drag", start_coordinate=[10, 10], coordinate=[100, 100])
        await self._run(
            action="scroll", coordinate=[50, 50], scroll_direction="up", scroll_amount=5
        )
        clicks = [c for c in self.backend.calls if c[0] == "click"]
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0][3], "right")
        self.assertEqual(clicks[1][4], 2, "double_click clicks twice")
        # drag goes through the base implementation: move + button down/up
        self.assertIn(("button", 15, 15, "left", True), self.backend.calls)
        scroll = [c for c in self.backend.calls if c[0] == "scroll"][0]
        self.assertEqual((scroll[3], scroll[4]), (0, -5))
        # `done` mirrors the browser tool and closes the session.
        self.assertIn("closed", str(await self._run(action="done")).lower())

    async def test_actions_need_a_screenshot_first(self) -> None:
        result = await self._run(action="left_click", coordinate=[5, 5])
        self.assertTrue(str(result).startswith("Error:"))
        self.assertIn("take a screenshot first", str(result))

    async def test_type_and_key(self) -> None:
        await self._run(action="screenshot")
        await self._run(action="type", text="Bonjour", screenshot=False)
        result = await self._run(action="key", text="ctrl+s", screenshot=False)
        self.assertIn(("type", "Bonjour"), self.backend.calls)
        self.assertIn(("key", "ctrl+s", None), self.backend.calls)
        self.assertIsInstance(result, str)
        self.assertIn("pressed ctrl+s", result)

    async def test_snapshot_refs_click_element_centres(self) -> None:
        await self._run(action="screenshot")
        listing = await self._run(action="snapshot")
        self.assertIn('"name": "Save"', listing)
        await self._run(action="left_click", ref=0)
        click = [c for c in self.backend.calls if c[0] == "click"][-1]
        self.assertEqual((click[1], click[2]), (250, 125))
        bad = await self._run(action="left_click", ref=42)
        self.assertIn("not in the last snapshot", str(bad))

    async def test_windows_are_listed_in_screenshot_coordinates(self) -> None:
        await self._run(action="screenshot")
        listing = await self._run(action="windows")
        self.assertIn("Calculator", listing)
        self.assertIn('"x": 683', listing)  # 1000 screen px -> 683 screenshot px
        await self._run(action="focus_window", window="calc", screenshot=False)
        self.assertIn(("focus", "9"), self.backend.calls)
        missing = await self._run(action="focus_window", window="Nope")
        self.assertIn("no window matches", str(missing))

    async def test_destructive_shortcut_is_refused_without_an_approver(self) -> None:
        await self._run(action="screenshot")
        result = await self._run(action="key", text="alt+f4")
        self.assertIn("Error: refused", str(result))
        self.assertNotIn(("key", "alt+f4", None), self.backend.calls)

    async def test_destructive_shortcut_goes_through_when_approved(self) -> None:
        asked: list[ApprovalRequest] = []

        class Gate:
            async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
                asked.append(request)
                return ApprovalDecision(allowed=True)

        token = bind_approval_gate(Gate())
        try:
            await self._run(action="screenshot")
            await self._run(action="key", text="alt+f4", screenshot=False)
        finally:
            reset_approval_gate(token)
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0].scope, "computer:close-window")
        self.assertIn(("key", "alt+f4", None), self.backend.calls)

    async def test_ask_always_still_acts_when_unattended(self) -> None:
        self.tool.config = ComputerToolConfig(
            enabled=True, ask="always", settle_ms=0, audit_log=False, live_view=False
        )
        await self._run(action="screenshot")
        await self._run(action="type", text="ok", screenshot=False)
        self.assertIn(("type", "ok"), self.backend.calls)

    async def test_ask_never_skips_the_gate_even_for_risky_keys(self) -> None:
        self.tool.config = ComputerToolConfig(
            enabled=True, ask="never", settle_ms=0, audit_log=False, live_view=False
        )
        await self._run(action="screenshot")
        await self._run(action="key", text="alt+f4", screenshot=False)
        self.assertIn(("key", "alt+f4", None), self.backend.calls)

    async def test_a_human_moving_the_mouse_pauses_the_run(self) -> None:
        await self._run(action="screenshot")
        await self._run(action="left_click", coordinate=[100, 100], screenshot=False)
        self.backend.cursor = (900, 900)  # the user grabbed the mouse
        result = await self._run(action="left_click", coordinate=[100, 100], screenshot=False)
        self.assertIn("someone is using this computer", str(result))
        again = await self._run(action="type", text="x", screenshot=False)
        self.assertIn("paused", str(again).lower())
        resumed = await self._run(action="resume")
        _image_blocks(resumed)
        await self._run(action="type", text="x", screenshot=False)
        self.assertIn(("type", "x"), self.backend.calls)

    async def test_per_turn_budget_stops_a_runaway_loop(self) -> None:
        self.tool.config = ComputerToolConfig(
            enabled=True,
            ask="never",
            settle_ms=0,
            audit_log=False,
            live_view=False,
            max_actions_per_turn=2,
        )
        await self._run(action="screenshot")
        await self._run(action="type", text="a", screenshot=False)
        await self._run(action="type", text="b", screenshot=False)
        blocked = await self._run(action="type", text="c", screenshot=False)
        self.assertIn("2 computer actions in this turn already", str(blocked))
        # A new turn starts a new budget.
        with request_context(_ctx(turn_id="turn-2")):
            fine = await self.tool.execute(action="type", text="d", screenshot=False)
        self.assertNotIn("Error", str(fine))

    async def test_zoom_returns_a_crop(self) -> None:
        await self._run(action="screenshot")
        result = await self._run(action="zoom", region=[100, 100, 300, 200])
        images, text = _image_blocks(result)
        self.assertEqual(len(images), 1)
        self.assertIn("Zoom of screenshot region", text)

    async def test_status_and_screen_info(self) -> None:
        status = await self._run(action="status")
        self.assertIn("computer tool enabled: True", status)
        self.assertIn("user control: False", status)
        self.assertIn("ask policy: destructive", status)
        info = await self._run(action="screen_info")
        self.assertIn('"w": 2000', info)

    async def test_unknown_action_is_an_error(self) -> None:
        result = await self._run(action="fly")
        self.assertTrue(str(result).startswith("Error:"))

    async def test_live_input_from_the_panel_drives_the_desktop(self) -> None:
        await self._run(action="screenshot")
        await dispatch_live_input(
            "test:session", "click", {"x": 683, "y": 341, "width": 1366, "height": 683}
        )
        click = [c for c in self.backend.calls if c[0] == "click"][-1]
        self.assertAlmostEqual(click[1], 1000, delta=2)
        await dispatch_live_input("test:session", "text", {"text": "hi"})
        self.assertIn(("type", "hi"), self.backend.calls)
        with self.assertRaises(ValueError):
            await dispatch_live_input("other", "click", {"x": 1, "y": 1})

    async def test_read_only_classification(self) -> None:
        self.assertTrue(self.tool.call_read_only({"action": "screenshot"}))
        self.assertTrue(self.tool.call_read_only({"action": "windows"}))
        self.assertFalse(self.tool.call_read_only({"action": "click"}))
        self.assertFalse(self.tool.call_read_only({"action": "type"}))


class AppPolicyTest(unittest.TestCase):
    def _win(self, title: str, app: str = "") -> WindowInfo:
        return WindowInfo(
            id="1", title=title, left=0, top=0, width=10, height=10, app=app, active=True
        )

    def test_protected_apps_block_looking_and_acting(self) -> None:
        policy = AppPolicy()
        vault = self._win("Personal - 1Password")
        self.assertFalse(policy.evaluate("screenshot", mutating=False, active=vault).allowed)
        verdict = policy.evaluate("left_click", mutating=True, active=vault)
        self.assertFalse(verdict.allowed)
        self.assertTrue(verdict.rule.startswith("protected:"))
        self.assertTrue(
            policy.evaluate("screenshot", mutating=False, active=self._win("Notes")).allowed
        )
        # Globs: KeePass* covers KeePassXC.
        self.assertFalse(
            policy.evaluate("type", mutating=True, active=self._win("KeePassXC")).allowed
        )

    def test_blocked_allowed_and_ask_lists(self) -> None:
        policy = AppPolicy(blocked=["Terminal"], allowed=["Notes", "Calc*"], ask=["Mail"])
        self.assertFalse(
            policy.evaluate("type", mutating=True, active=self._win("Terminal")).allowed
        )
        self.assertTrue(policy.evaluate("type", mutating=True, active=self._win("Notes")).allowed)
        self.assertTrue(
            policy.evaluate("type", mutating=True, active=self._win("Calculator")).allowed
        )
        outside = policy.evaluate("type", mutating=True, active=self._win("Photos"))
        self.assertFalse(outside.allowed)
        self.assertIn("focus_window", outside.reason)
        # Getting to an allowed app is fine, to a blocked one is not.
        self.assertTrue(
            policy.evaluate(
                "focus_window", mutating=True, active=self._win("Photos"), target=self._win("Notes")
            ).allowed
        )
        self.assertFalse(
            policy.evaluate(
                "focus_window",
                mutating=True,
                active=self._win("Notes"),
                target=self._win("Terminal"),
            ).allowed
        )
        # Reads are never limited by the allow-list.
        self.assertTrue(
            policy.evaluate("screenshot", mutating=False, active=self._win("Photos")).allowed
        )
        # Unknown active window with an allow-list: refuse rather than guess.
        self.assertFalse(policy.evaluate("type", mutating=True, active=None).allowed)
        asked = AppPolicy(ask=["Mail"]).evaluate(
            "type", mutating=True, active=self._win("Inbox", app="Mail")
        )
        self.assertTrue(asked.allowed)
        self.assertTrue(asked.ask)
        self.assertEqual(asked.rule, "ask:mail")

    def test_card_numbers_count_as_risky_text(self) -> None:
        self.assertEqual(risk_for_text("4111 1111 1111 1111").rule, "card-number")
        self.assertIsNone(risk_for_text("call 0612345678 tomorrow"))


class PolicyGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _SESSIONS.clear()
        self.backend = FakeBackend()
        self.tmp = tempfile.TemporaryDirectory()
        self._stop_patch = mock.patch(
            "navin.computer.policy.stop_file", lambda: Path(self.tmp.name) / "STOP"
        )
        self._stop_patch.start()

    def tearDown(self) -> None:
        self._stop_patch.stop()
        self.tmp.cleanup()
        _SESSIONS.clear()

    def _tool(self, **overrides: Any) -> ComputerTool:
        options: dict[str, Any] = {
            "enabled": True,
            "ask": "destructive",
            "settle_ms": 0,
            "audit_log": False,
            "live_view": False,
        }
        options.update(overrides)
        config = ComputerToolConfig(**options)
        return ComputerTool(config=config, backend_factory=lambda: self.backend)

    async def _run(self, tool: ComputerTool, **kwargs: Any) -> Any:
        with request_context(_ctx()):
            return await tool.execute(**kwargs)

    async def test_kill_switch_halts_everything_but_status(self) -> None:
        tool = self._tool()
        await self._run(tool, action="screenshot")
        engage_stop("meeting in progress")
        result = await self._run(tool, action="left_click", coordinate=[5, 5])
        self.assertIn("computer use is stopped", str(result))
        self.assertIn("meeting in progress", str(result))
        self.assertIn("navin computer go", str(result))
        self.assertIn("stopped: meeting in progress", str(await self._run(tool, action="status")))
        self.assertNotIn(("click", 7, 7, "left", 1), self.backend.calls)
        self.assertIn("stopped", str(await self._run(tool, action="screenshot")))
        self.assertTrue(release_stop())
        self.assertFalse(release_stop())
        await self._run(tool, action="left_click", coordinate=[5, 5])
        self.assertTrue(any(c[0] == "click" for c in self.backend.calls))

    async def test_blocked_app_refuses_actions_but_not_screenshots(self) -> None:
        tool = self._tool(blocked_apps=["notes"])  # FakeBackend's active window app is "notes"
        result = await self._run(tool, action="screenshot")
        self.assertIsInstance(result, list)
        denied = await self._run(tool, action="type", text="hello")
        self.assertIn("blocked by policy", str(denied))
        self.assertNotIn(("type", "hello"), self.backend.calls)

    async def test_allow_list_lets_focus_window_reach_an_allowed_app(self) -> None:
        tool = self._tool(allowed_apps=["Calculator"])
        await self._run(tool, action="screenshot")
        denied = await self._run(tool, action="type", text="1+1")
        self.assertIn("outside the allowed applications", str(denied))
        ok = await self._run(tool, action="focus_window", window="Calc", screenshot=False)
        self.assertIn("focused window 9", str(ok))
        blocked = await self._run(tool, action="focus_window", window="Notes", screenshot=False)
        self.assertIn("outside the allowed applications", str(blocked))

    async def test_protected_app_in_front_hides_the_screen(self) -> None:
        tool = self._tool(protected_apps=["Notes"])
        result = await self._run(tool, action="screenshot")
        self.assertIn("protected application is in front", str(result))
        self.assertNotIn(("screenshot",), self.backend.calls)

    async def test_ask_apps_force_an_approval(self) -> None:
        asked: list[ApprovalRequest] = []

        class Gate:
            async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
                asked.append(request)
                return ApprovalDecision(allowed=len(asked) == 1)

        tool = self._tool(ask="never", ask_apps=["notes"])
        token = bind_approval_gate(Gate())
        try:
            await self._run(tool, action="screenshot")
            await self._run(tool, action="type", text="first", screenshot=False)
            refused = await self._run(tool, action="type", text="second", screenshot=False)
        finally:
            reset_approval_gate(token)
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked[0].scope, "computer:ask:notes")
        self.assertIn(("type", "first"), self.backend.calls)
        self.assertNotIn(("type", "second"), self.backend.calls)
        self.assertIn("refused", str(refused))

    async def test_dedicated_mode_needs_a_reserved_display(self) -> None:
        import sys

        if sys.platform != "linux":
            self.skipTest("display semantics are Linux-specific here")
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
            tool = self._tool(session_mode="dedicated")
            result = await self._run(tool, action="screenshot")
            self.assertIn("no display is reserved", str(result))
            tool = self._tool(session_mode="dedicated", display=":99")
            result = await self._run(tool, action="screenshot")
            self.assertIsInstance(result, list)
            tool = self._tool(session_mode="dedicated", display=":0")
            self.assertIn("no display is reserved", str(await self._run(tool, action="screenshot")))


class ConfigWiringTest(unittest.TestCase):
    def test_computer_defaults_to_enabled_and_autonomous(self) -> None:
        from navin.config.schema import Config

        config = Config()
        self.assertTrue(config.tools.computer.enabled)
        self.assertEqual(config.tools.computer.ask, "never")

    def test_explicit_computer_opt_out_is_preserved(self) -> None:
        from navin.config.schema import Config

        config = Config.model_validate({"tools": {"computer": {"enabled": False, "ask": "always"}}})
        self.assertFalse(config.tools.computer.enabled)
        self.assertEqual(config.tools.computer.ask, "always")

    def test_tool_is_discovered_but_only_registered_when_enabled(self) -> None:
        from navin.agent.tools.loader import ToolLoader

        classes = ToolLoader().discover()
        self.assertTrue(any(cls.__name__ == "ComputerTool" for cls in classes))

        class Ctx:
            class config:  # noqa: N801 - mimics ToolsConfig attribute access
                computer = ComputerToolConfig(enabled=False)

        self.assertFalse(ComputerTool.enabled(Ctx()))
        Ctx.config.computer = ComputerToolConfig(enabled=True)
        self.assertTrue(ComputerTool.enabled(Ctx()))
