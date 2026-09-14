# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Interim narration lines replace each other instead of stacking forever."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from navin.bus.events import OutboundMessage
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import AssistantMessage, ProgressLine, ToolCall
from tests.test_tui_queue import make_app


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


@pytest.mark.parametrize("show_tools", [True, False])
def test_streamed_steps_replace_narration_and_leave_only_the_final_answer(tmp_path, show_tools):
    async def run():
        app = make_app(tmp_path)
        app.prefs.show_tools = show_tools
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("Ajouter le support des archives")
            await app.runtime.bus.consume_inbound()
            app.composer.set_text("brouillon")

            async def dispatch(content="", **metadata):
                await app.runtime._dispatch(OutboundMessage("cli", "direct", content, metadata=metadata))

            steps = [
                "Je regarde ou la liste des types acceptes est definie.",
                "Je mets les etapes au tableau puis j'implemente.",
                "Now the backend extraction.",
                "Maintenant le code d'extraction cote backend :",
                "Now the frontend whitelist plus tests.",
            ]
            for index, step in enumerate(steps):
                await dispatch(step[:10], _stream_delta=True)
                await dispatch(step[10:], _stream_delta=True)
                await dispatch(_stream_end=True, _resuming=True)
                await pilot.pause()
                block = app._current
                assert block.text == ""
                assert block.query_one(Markdown).source == ""
                assert len(block.query(ProgressLine)) == 1
                assert step in str(block.query_one(ProgressLine).content)
                assert not block.query_one(".assistant-preview", Static).display
                assert all(previous not in str(block.query_one(ProgressLine).content) for previous in steps[:index])
                await block.tool_event(str(index), "exec", "end", {"command": f"check-{index}"}, "ok", None, None, visible=show_tools)

            # A structured question can open the body while the agent is paused.
            await app._current.reveal()
            await dispatch("Support des archives ", _stream_delta=True)
            await dispatch("ajoute et verifie.", _stream_delta=True)
            await dispatch(_stream_end=True)
            await pilot.pause()
            await pilot.wait_for_scheduled_animations()
            assert not app._current.query(ProgressLine)
            final = "Support des archives ajoute et verifie."
            assert app._current.text == final
            await dispatch(final, _streamed=True)
            await pilot.pause()
            await pilot.wait_for_scheduled_animations()
            assert app._current.finished
            assert app._current.query_one(Markdown).source == final
            assert len(app._current.query(ToolCall)) == (len(steps) if show_tools else 0)
            assert all(step not in app._current.copy_text() for step in steps)
            assert app.composer.text == "brouillon"
            assert app.transcript.is_vertical_scroll_end
    asyncio.run(run())


def test_final_response_replaces_streamed_draft(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("Verifier le support des archives")
            await app.runtime.bus.consume_inbound()
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Je vais verifier.", metadata={"_stream_delta": True}))
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Verification terminee.", metadata={"_streamed": True}))
            await pilot.pause()
            assert app._current.text == "Verification terminee."
            assert app._current.query_one(Markdown).source == "Verification terminee."
    asyncio.run(run())


def test_long_interim_text_stays_compact_after_an_open_question():
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(90, 30)) as pilot:
            block = app.block
            narration = "Je prepare les archives. " + "Details intermediaires. " * 100
            await block.delta(narration)
            await block.reveal()
            await block.stream_end(resuming=True)
            # A duplicate or empty segment must not restore the expanded text.
            await block.stream_end(resuming=True)
            await pilot.pause()
            assert block.text == ""
            assert block.query_one(Markdown).source == ""
            assert not block._open
            assert not block.query_one(".assistant-preview", Static).display
            assert len(block.query(ProgressLine)) == 1
            assert str(block.query_one(ProgressLine).content) == "· Je prepare les archives."
            await block.delta("Termine.")
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
            assert not block.query(ProgressLine)
            assert block.query_one(Markdown).source == "Termine."
    asyncio.run(run())
