# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Completion boundaries and prompt editing through actual terminal widgets."""

import asyncio

import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from textual.widgets._markdown import (
    MarkdownFence,
    MarkdownH2,
    MarkdownH3,
    MarkdownParagraph,
    MarkdownTable,
)

from navin.agent.code_validation import CodeValidationState
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import AssistantMessage, Composer, ComposerMeta, ComposerShell


class FinishHost(App):
    def __init__(self, theme="navin"):
        super().__init__()
        self.block = AssistantMessage()
        self.prefixes = []
        self.submitted = []
        for registered in NAVIN_THEMES:
            self.register_theme(registered)
        self.theme = theme

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield self.block
        with ComposerShell():
            yield Composer(placeholder="Ask anything...")
            yield ComposerMeta()

    @on(Composer.SlashTyping)
    def slash(self, event):
        self.prefixes.append(event.prefix)

    @on(Composer.Submitted)
    def submit(self, event):
        self.submitted.append(event.text)


@pytest.mark.parametrize("theme,width", [("navin", 100), ("navin-light", 48)])
@pytest.mark.parametrize("saved_legacy_message", [False, True])
def test_validation_finish_is_structured_colored_and_copyable(theme, width, saved_legacy_message, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    async def run():
        state = CodeValidationState(revision=1, needs_tests=True, paths={
            "backend/tests/test_support_agent_disabled.py",
            "backend/gemini_live/support_ws_routes.py",
            "frontend/src/app/admin/tours/agent-runs/[runId]/page.tsx",
        })
        message = state.completion_message()
        if saved_legacy_message:
            message = (
                "The changes are saved, but the task is not validated. "
                "The agent repeatedly tried to finish without resolving these checks.\n\n"
                + state.missing()
            )
        app = FinishHost(theme)
        async with app.run_test(size=(width, 42)) as pilot:
            await app.block.set_text(message)
            await app.block.finish(latency_ms=10, model=None, preset=None)
            await pilot.pause()
            heading = app.block.query_one(MarkdownH2)
            table = app.block.query_one(MarkdownTable)
            command = app.block.query_one(MarkdownFence)
            assert app.block.has_class("-validation-pending")
            assert heading.styles.color != app.block.query_one(MarkdownParagraph).styles.color
            assert len(app.block.query(MarkdownH3)) == 3
            assert table.region.y > heading.region.y
            assert command.region.y > table.region.bottom
            assert table.region.right <= app.block.region.right
            assert command.query_one("#code-content").region.right <= command.region.right
            if width < 60:
                assert command.query_one("#code-content").content_size.height > 1
            assert [cell.content.plain for cell in table.query(".header, .cell")] == [
                "Item", "Status", "Changes", "Saved", "Tests", "No current result",
            ]
            source = app.block.query_one(Markdown).source
            assert "```bash\npython -m pytest backend/tests/test_support_agent_disabled.py\n```" in source
            assert "- `frontend/src/app/admin/tours/agent-runs/[runId]/page.tsx`" in source
            assert app.block.copy_text() == message
            composer = app.query_one(Composer)
            composer.focus()
            await pilot.press(*"Reprendre")
            assert composer.text == "Reprendre"
            await app.block.set_text("Validation completed.")
            assert not app.block.has_class("-validation-pending")
    asyncio.run(run())


@pytest.mark.parametrize("theme,width", [("navin", 92), ("navin-light", 48)])
def test_finish_separates_activity_and_answer_once(theme, width):
    async def run():
        app = FinishHost(theme)
        async with app.run_test(size=(width, 28)) as pilot:
            block = app.block
            separator = block.query_one(".assistant-finish", Static)
            assert not separator.display
            await block.tool_event("run", "exec", "end", {"command": "pytest -q"}, "2 passed", None, None)
            await block.delta("Validated. Run `npm install` for `src/app.ts`.")
            assert not separator.display
            await block.finish(latency_ms=12, model=None, preset=None)
            await block.finish(latency_ms=12, model=None, preset=None)
            await pilot.pause()
            assert len(block.query(".assistant-finish")) == 1
            assert separator.display
            assert separator.region.bottom <= block.query_one(Markdown).region.y
            assert "finish" not in block.copy_text()
            assert "npm install" in block.copy_text()
            await block.delta(" Another detail.")
            assert not separator.display
            await block.finish(latency_ms=12, model=None, preset=None)
            await block.set_text("Updated final answer.")
            assert separator.display
            assert block.text == "Updated final answer."
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_events_on_removed_block_do_not_crash():
    """Session switch removes transcript children; late events must be ignored."""
    async def run():
        app = FinishHost()
        async with app.run_test(size=(80, 24)) as pilot:
            block = app.block
            await block.set_text("Answer before the switch.")
            await pilot.pause()
            await block.remove()
            await pilot.pause()
            # The exact crash from the field report: NoMatches on a removed block.
            await block.tool_event("run", "exec", "end", {"command": "pytest"}, "ok", None, None)
            await block.note_file_edit("src/app.ts", 3, 1, diff="-a\n+b")
            await block.progress("working...")
            await block.subagent("t-1", "audit", "end", "done", None, 1, True, None)
            await block.delta(" more")
            await block.set_text("Late text.")
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_finish_survives_missing_chrome_while_still_attached():
    """History replay can finish a block after session switch tore its children."""
    async def run():
        app = FinishHost()
        async with app.run_test(size=(80, 24)) as pilot:
            block = app.block
            await block.tool_event("run", "exec", "end", {"command": "pytest"}, "ok", None, None)
            await block.set_text("Answer before the switch.")
            await pilot.pause()
            for node in list(block.query(".assistant-foot, .assistant-finish, .assistant-preview")):
                await node.remove()
            await block.finish(latency_ms=5, model=None, preset=None)
            block.hide_finish()
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_ready_helpers_return_none_when_detached():
    async def run():
        app = FinishHost()
        async with app.run_test(size=(80, 24)) as pilot:
            block = app.block
            await pilot.pause()
            assert await block._ready_preview() is not None
            assert await block._ready_body() is not None
            await block.remove()
            await pilot.pause()
            assert await block._ready_preview() is None
            assert await block._ready_body() is None
    asyncio.run(run(), debug=True)


def test_typing_keeps_cursor_inside_prompt_without_rebuilding_slash_menu():
    async def run():
        app = FinishHost()
        async with app.run_test(size=(60, 24)) as pilot:
            composer = app.query_one(Composer)
            composer.focus()
            await pilot.press(*"Bonjour tout le monde")
            assert composer.text == "Bonjour tout le monde"
            assert app.prefixes == []
            shell = app.query_one(ComposerShell)
            assert composer.region.y > shell.region.y
            assert composer.region.bottom <= shell.region.bottom
            await pilot.press("ctrl+j", *"suite", "enter")
            assert app.submitted == ["Bonjour tout le monde\nsuite"]
            composer.clear_text()
            await pilot.press("/", "h", "e", "l", "p", "space", "x")
            assert app.prefixes == ["/", "/h", "/he", "/hel", "/help", None]
            assert composer.text == "/help x"
            await pilot.pause()
    asyncio.run(run(), debug=True)
