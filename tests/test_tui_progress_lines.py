# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Interim narration lines replace each other instead of stacking forever."""

import asyncio

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static  # noqa: F401  (kept for parity with sibling suites)

from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import AssistantMessage, ProgressLine


class ProgressHost(App):
    def __init__(self):
        super().__init__()
        self.block = AssistantMessage()
        for registered in NAVIN_THEMES:
            self.register_theme(registered)
        self.theme = "navin"

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield self.block


def test_progress_lines_replace_each_other_and_clear_on_finish():
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(92, 28)) as pilot:
            block = app.block
            await block.progress("Je m'attaque aux trois problemes.")
            await block.progress("D'abord le backend.")
            await pilot.pause()
            lines = list(block.query(ProgressLine))
            assert len(lines) == 1
            assert "backend" in str(lines[0].content)
            # Real body text drops the interim narration.
            await block.set_text("Termine.")
            await pilot.pause()
            assert not list(block.query(ProgressLine))

    asyncio.run(run())


def test_progress_line_is_removed_when_the_turn_finishes():
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(92, 28)) as pilot:
            block = app.block
            await block.progress("D'abord le backend.")
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
            assert not list(block.query(ProgressLine))

    asyncio.run(run())
