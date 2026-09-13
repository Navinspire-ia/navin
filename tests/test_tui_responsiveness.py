# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep input responsive under bursts and require an intentional stop action."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from textual.app import App, ComposeResult
from textual.widgets import Markdown, Static

from navin.bus.queue import MessageBus
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.screens import PickerScreen, PickItem
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import (
    AssistantMessage,
    Composer,
    Sidebar,
    SlashMenu,
    SystemNote,
    ToolCall,
    ToolCluster,
    Transcript,
    UserMessage,
)


class InteractionHost(NavinApp):
    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.bus = MessageBus()
        self._engine_ready = True
        self.slash_rows = [{"command": "/help", "title": "Help"}]
        self.query_one(Sidebar).display = False
        self.composer.focus()

    async def on_unmount(self, event):
        event.prevent_default()
        self.runtime._closed = True

    def _refresh_side(self):
        self._set_status()

    def _load_account(self, *args, **kwargs):
        pass


class InterruptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        self.app = InteractionHost(SimpleNamespace(workspace_path=Path(directory.name)), prefs=prefs)

    async def start_turn(self):
        await self.app.submit_text("keep working")
        await self.app.runtime.bus.consume_inbound()

    async def test_clear_draft_and_change_mode_keep_active_turn(self):
        app = self.app
        async with app.run_test(size=(100, 32)) as pilot:
            await self.start_turn()
            block = await app._ensure_assistant()
            app.composer.set_text("unfinished follow-up")
            await pilot.press("ctrl+c")
            self.assertEqual(app.composer.text, "")
            self.assertTrue(app.runtime.turn_active)
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            await app._apply_mode("agent")
            self.assertEqual(app.prefs.mode, "agent")
            self.assertIs(app._current, block)
            self.assertTrue(app.runtime.turn_active)
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            await pilot.press("escape")
            stop = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 1)
            self.assertEqual(stop.content, "/stop")
            notes = " ".join(str(note.content) for note in app.query(SystemNote))
            self.assertIn("Stop requested (Esc)", notes)

    async def test_copy_shortcut_in_modal_does_not_stop_background_work(self):
        app = self.app
        async with app.run_test(size=(100, 32)) as pilot:
            await self.start_turn()
            await app.push_screen(PickerScreen("Mode", [PickItem("agent", "Agent")]))
            await pilot.press("ctrl+c")
            self.assertEqual(len(app.screen_stack), 2)
            self.assertTrue(app.runtime.turn_active)
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            await pilot.press("escape")
            self.assertEqual(len(app.screen_stack), 1)
            self.assertEqual(app.runtime.bus.inbound_size, 0)

    async def test_escape_closes_slash_menu_before_stopping(self):
        app = self.app
        async with app.run_test(size=(100, 32)) as pilot:
            await self.start_turn()
            await pilot.press("/")
            await pilot.pause()
            self.assertTrue(app.query_one(SlashMenu).visible_menu)
            await pilot.press("escape")
            self.assertFalse(app.query_one(SlashMenu).visible_menu)
            self.assertFalse(app.composer.menu_open)
            self.assertEqual(app.runtime.bus.inbound_size, 0)

    async def test_animation_does_not_resolve_model_settings_each_frame(self):
        app = self.app
        async with app.run_test(size=(100, 32)):
            await self.start_turn()
            with patch.object(app.runtime, "reasoning_details", return_value=("High", ())) as resolve:
                app._set_status()
                for _ in range(20):
                    app._tick_spinner()
                self.assertEqual(resolve.call_count, 1)


class StreamHost(App):
    def __init__(self):
        super().__init__()
        self.block = AssistantMessage("Navin")
        for theme in NAVIN_THEMES:
            self.register_theme(theme)
        self.theme = "navin"

    def compose(self) -> ComposeResult:
        with Transcript():
            yield self.block
        yield Composer()


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_bursts_are_coalesced_and_final_text_is_complete(self):
        app = StreamHost()
        async with app.run_test(size=(110, 32)) as pilot:
            block = app.block
            with patch.object(block, "_refresh_preview", wraps=block._refresh_preview) as paint:
                for index in range(1000):
                    await block.delta(f"word{index} ")
                self.assertLess(paint.call_count, 10)
                await block.finish(latency_ms=None, model=None, preset=None)
            body = block.query_one(Markdown)
            self.assertEqual(body.source, block.text)
            self.assertTrue(body.source.endswith("word999 "))
            await pilot.pause()
            self.assertEqual(body.query_one("MarkdownParagraph").styles.color.hex, "#FFFFFF")

    async def test_typing_during_stream_preserves_input_and_markdown_blocks(self):
        app = StreamHost()
        async with app.run_test(size=(100, 32)) as pilot:
            block = app.block
            await block.set_text("First paragraph.\n\n")
            await block.reveal()
            body = block.query_one(Markdown)
            first = body.query_one("MarkdownParagraph")
            app.query_one(Composer).focus()

            async def stream():
                for _ in range(12):
                    for _ in range(30):
                        await block.delta("more ")
                    await asyncio.sleep(0.04)

            producer = asyncio.create_task(stream())
            await pilot.press("f", "l", "u", "i", "d", "e")
            await producer
            await block.stream_end()
            self.assertEqual(app.query_one(Composer).text, "fluide")
            self.assertEqual(body.source, block.text)
            # Appending does not replace completed paragraphs on every frame.
            self.assertIs(body.query_one("MarkdownParagraph"), first)

    async def test_tool_output_flushes_before_completion_and_retains_full_copy(self):
        app = StreamHost()
        async with app.run_test(size=(100, 32)) as pilot:
            await app.block.tool_event("run", "exec", "start", {"command": "run-checks"}, None, None, None)
            row = app.block.query_one(ToolCall)
            row.toggle()
            with patch.object(row, "_refresh_body", wraps=row._refresh_body) as paint:
                for index in range(250):
                    row.apply(phase="output", output=f"case_{index:03d} passed\n")
                self.assertLess(paint.call_count, 10)
                row.apply(phase="end", result="250 passed")
            self.assertIn("case_249", row.copy_text())
            row.action_show_full()
            self.assertIn("case_249", str(row.query_one(".tool-body", Static).content))
            await pilot.pause()
            with patch.object(row, "_refresh_head", wraps=row._refresh_head) as paint:
                await pilot.pause(0.3)
                self.assertEqual(paint.call_count, 0)

    async def test_adding_tool_does_not_repaint_all_previous_rows(self):
        app = StreamHost()
        async with app.run_test(size=(100, 32)):
            for index in range(15):
                await app.block.tool_event(str(index), "read_file", "end", {"path": f"file{index}.py"}, "content", None, None)
            cluster = app.block.query_one(ToolCluster)
            first = cluster.tools[0]
            with patch.object(first, "_refresh_head", wraps=first._refresh_head) as paint:
                await app.block.tool_event("last", "read_file", "end", {"path": "last.py"}, "content", None, None)
                paint.assert_not_called()
            self.assertTrue(all(row.tree_mark == "├ " for row in cluster.tools[:-1]))
            self.assertEqual(cluster.tools[-1].tree_mark, "└ ")


def _history_turn(index: int, *, tools: bool = True) -> list[dict]:
    row = {
        "role": "assistant",
        "content": f"answer {index}",
        "metadata": {"latency_ms": 10, "model": "test-model"},
        "tools": [],
    }
    if tools:
        row["tools"] = [
            {
                "id": f"call_{index}",
                "name": "edit_file",
                "arguments": {"path": "navin/tui/app.py"},
                "result": "Successfully edited navin/tui/app.py",
                "file_edits": [
                    {
                        "version": 1,
                        "call_id": f"call_{index}",
                        "tool": "edit_file",
                        "path": "navin/tui/app.py",
                        "phase": "end",
                        "added": 1,
                        "deleted": 0,
                    }
                ],
                "phase": "end",
            }
        ]
    return [
        {"role": "user", "content": f"question {index}", "metadata": {}, "tools": []},
        row,
    ]


class SessionSwitchDuringLoadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        self.app = InteractionHost(SimpleNamespace(workspace_path=Path(directory.name)), prefs=prefs)

    async def test_opening_another_session_while_history_loads(self):
        app = self.app
        heavy = []
        for index in range(12):
            heavy.extend(_history_turn(index))
        other = [
            {"role": "user", "content": "hello other", "metadata": {}, "tools": []},
            {
                "role": "assistant",
                "content": "hi from other",
                "metadata": {},
                "tools": [],
            },
        ]

        def fake_history():
            if app.runtime.session_key == "cli:other":
                return other, 0
            return heavy, 0

        app.runtime.history = fake_history
        original_finish = AssistantMessage.finish

        async def slow_finish(self, *args, **kwargs):
            await asyncio.sleep(0.02)
            return await original_finish(self, *args, **kwargs)

        async with app.run_test(size=(100, 32)) as pilot:
            with patch.object(AssistantMessage, "finish", slow_finish):
                render = asyncio.create_task(app._render_history())
                await asyncio.sleep(0.03)
                await app._switch_session("cli:other")
                await render
            await pilot.pause()
            self.assertEqual(app.runtime.session_key, "cli:other")
            self.assertEqual(
                [row.raw_text for row in app.query(UserMessage)],
                ["hello other"],
            )
            self.assertEqual(
                [row.text for row in app.query(AssistantMessage)],
                ["hi from other"],
            )
