# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Completion boundaries and prompt editing through actual terminal widgets."""

import asyncio

import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

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
