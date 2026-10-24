# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Interim narration stays in the chain: narration, tools, narration, answer.
Bare tool hints are transient and replace each other."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from navin.bus.events import OutboundMessage
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import (
    NARRATION_CHARS,
    AssistantMessage,
    ProgressLine,
    ToolCall,
    narration_text,
)
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


def test_progress_is_literal_and_keeps_the_whole_narration():
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(48, 28)) as pilot:
            for index, text in enumerate(["Je verifie [ces fichiers. " * 20, "[/dim] C:\\work\\"]):
                await app.block.progress(text)
                await pilot.pause()
                lines = list(app.block.query(ProgressLine))
                assert len(lines) == index + 1
                # Markup stays literal and the narration is not cut to one
                # sentence: it wraps, like Claude Code's interim lines.
                assert str(lines[-1].content) == f"· {narration_text(text)}"
                assert lines[-1].region.height >= 1
    asyncio.run(run())


def test_narration_lines_stay_in_order_and_survive_the_answer():
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(92, 28)) as pilot:
            block = app.block
            await block.progress("Je m'attaque aux trois problemes.")
            await block.progress("Je m'attaque aux trois problemes.")
            await block.tool_event("ls", "exec", "end", {"command": "ls"}, "a\nExit code: 0", None, None)
            await block.progress("D'abord le backend.")
            await pilot.pause()
            lines = list(block.query(ProgressLine))
            assert [line.narration for line in lines] == [
                "Je m'attaque aux trois problemes.", "D'abord le backend.",
            ]
            assert block._activity_blocks[0] is lines[0] and block._activity_blocks[-1] is lines[1]
            await block.set_text("Termine.")
            await pilot.pause()
            assert len(block.query(ProgressLine)) == 2

    asyncio.run(run())


def test_same_narration_after_a_tool_card_is_not_printed_twice():
    """Regression: the stream end and the progress event both delivered the
    narration, with a tool card in between, and the journal showed it twice."""
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(92, 28)) as pilot:
            block = app.block
            await block.progress("Je lance les tests.")
            await block.tool_event("t1", "exec", "end", {"command": "ls"}, "a\nExit code: 0", None, None)
            await block.progress("Je lance les tests.")
            await block.progress("| a | b |\n|---|---|\n| 1 | 2 |")
            await block.tool_event("t2", "exec", "end", {"command": "pwd"}, "/\nExit code: 0", None, None)
            await block.progress("| a | b |\n|---|---|\n| 1 | 2 |")
            await block.progress("Ensuite le build.")
            await pilot.pause()
            assert [line.narration for line in block.query(ProgressLine)] == [
                "Je lance les tests.", "Ensuite le build.",
            ]
            assert len([b for b in block._activity_blocks if type(b).__name__ == "InterimMarkdown"]) == 1

    asyncio.run(run())


def test_transient_hints_replace_each_other_and_leave_when_the_turn_finishes():
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(92, 28)) as pilot:
            block = app.block
            await block.progress("D'abord le backend.")
            await block.progress("read_file(a.py)", transient=True)
            await block.progress("read_file(b.py)", transient=True)
            await pilot.pause()
            assert [line.narration for line in block.query(ProgressLine)] == [
                "D'abord le backend.", "read_file(b.py)",
            ]
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
            assert [line.narration for line in block.query(ProgressLine)] == ["D'abord le backend."]

    asyncio.run(run())


@pytest.mark.parametrize("show_tools", [True, False])
def test_streamed_steps_keep_one_narration_line_each_before_the_final_answer(tmp_path, show_tools):
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
                lines = list(block.query(ProgressLine))
                assert len(lines) == index + 1
                assert step in str(lines[-1].content)
                assert not block.query_one(".assistant-preview", Static).display
                await block.tool_event(str(index), "exec", "end", {"command": f"check-{index}"}, "ok", None, None, visible=show_tools)

            # A structured question can open the body while the agent is paused.
            await app._current.reveal()
            await dispatch("Support des archives ", _stream_delta=True)
            await dispatch("ajoute et verifie.", _stream_delta=True)
            await dispatch(_stream_end=True)
            await pilot.pause()
            await pilot.wait_for_scheduled_animations()
            assert len(app._current.query(ProgressLine)) == len(steps)
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
            kept = str(block.query_one(ProgressLine).content)
            assert kept.startswith("· Je prepare les archives. Details intermediaires.")
            assert kept.endswith("…") and len(kept) <= NARRATION_CHARS + 2
            await block.delta("Termine.")
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
            assert len(block.query(ProgressLine)) == 1
            assert block.query_one(Markdown).source == "Termine."
    asyncio.run(run())


def test_progress_echo_of_live_stream_is_not_shown_twice():
    """The journal briefly showed the same narration twice before catching up."""
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(90, 30)) as pilot:
            block = app.block
            await block.delta("Je lis le fichier de config.")
            await block.progress("Je lis le fichier de config.")
            await pilot.pause()
            assert not list(block.query(ProgressLine))
            await block.stream_end(resuming=True)
            await pilot.pause()
            lines = [line.narration for line in block.query(ProgressLine)]
            assert lines == [narration_text("Je lis le fichier de config.")]
            # A different narration during a live stream is still kept.
            await block.delta("Autre texte en cours.")
            await block.progress("Je lance les tests.")
            await pilot.pause()
            assert len(list(block.query(ProgressLine))) == 2
    asyncio.run(run())


def test_discarded_segment_after_a_mid_stream_retry_leaves_no_trace():
    """A retry streams the whole answer again: the partial text must vanish,
    not linger as a narration line above the answer."""
    async def run():
        app = ProgressHost()
        async with app.run_test(size=(90, 30)) as pilot:
            block = app.block
            await block.delta("The ans")
            await pilot.pause()
            await block.stream_end(resuming=True, discarded=True)
            await pilot.pause()
            assert block.text == ""
            assert not list(block.query(ProgressLine))
            await block.delta("The answer is 42.")
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
            assert not list(block.query(ProgressLine))
            assert block.query_one(Markdown).source == "The answer is 42."
    asyncio.run(run())


def test_runtime_forwards_the_discarded_flag(tmp_path):
    from navin.bus.outbound_events import StreamEndEvent, outbound_message_for_event
    from navin.tui.runtime import UiStreamEnd

    async def run():
        app = make_app(tmp_path)
        seen = []

        async def emit(event):
            seen.append(event)

        app.runtime._emit = emit  # type: ignore[method-assign]
        await app.runtime._dispatch(outbound_message_for_event(
            channel="cli", chat_id="direct",
            event=StreamEndEvent(stream_id="s0", resuming=True, discarded=True),
        ))
        assert seen == [UiStreamEnd(resuming=True, discarded=True)]
    asyncio.run(run())
