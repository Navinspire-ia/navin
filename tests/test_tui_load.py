# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Input and navigation remain available while work and durable writes run."""

import asyncio
import gc
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from textual import events
from textual.widgets import Markdown, Static

from navin.agent.context_governance import ContextGovernor
from navin.bus.events import OutboundMessage
from navin.bus.outbound_events import StreamDeltaEvent, StreamEndEvent
from navin.bus.queue import MessageBus
from navin.providers.base import LLMResponse
from navin.tui.frames import terminal_gc_policy
from navin.tui.runtime import TuiRuntime, UiStreamDelta, UiStreamEnd
from navin.tui.screens import PickerScreen, PickItem
from navin.tui.widgets import ToolCall
from tests.test_tui_queue import make_app
from tests.test_turn_recovery import (
    LocalProvider,
    isolated_config,  # noqa: F401 - isolate machine skills
    make_loop,
)


def test_terminal_gc_policy_keeps_collection_enabled_and_restores_nested_scopes():
    thresholds = gc.get_threshold()
    enabled = gc.isenabled()
    try:
        gc.enable()
        with terminal_gc_policy():
            assert gc.isenabled()
            assert gc.get_threshold()[0] >= 4000
            configured = gc.get_threshold()
            with terminal_gc_policy():
                assert gc.get_threshold() == configured
            assert gc.get_threshold() == configured
        assert gc.get_threshold() == thresholds
        gc.disable()
        with terminal_gc_policy():
            assert not gc.isenabled()
            assert gc.get_threshold() == thresholds
    finally:
        gc.set_threshold(*thresholds)
        if enabled:
            gc.enable()
        else:
            gc.disable()


def test_copy_keeps_the_newest_line_when_output_reaches_the_retention_limit(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)):
            row = ToolCall("run", "exec", {"command": "pytest checks.py"})
            await app.transcript.mount(row)
            row.apply(phase="output", output="".join(f"case_{i:04d}: passed\n" for i in range(5000)))
            row.apply(phase="output", output="FINAL_RESULT: all checks passed\n")
            assert len(row.output_lines) == 5000
            copied = row.copy_text()
            assert "case_0000:" not in copied
            assert "case_0001:" in copied
            assert "FINAL_RESULT: all checks passed" in copied
            row.apply(phase="cancelled", error="Interrupted before a result was received.")
            assert "Interrupted" in row.copy_text()
            assert "FINAL_RESULT: all checks passed" in row.copy_text()
            row.apply(phase="end", result="Checks complete.")
            assert "Checks complete." in row.copy_text()
    asyncio.run(run())


def test_slow_disk_does_not_block_typing_navigation_or_lose_newest_draft(tmp_path, monkeypatch):
    async def run():
        app = make_app(tmp_path)
        entered = threading.Event()
        release = threading.Event()
        save = app._session_store.save
        thread_ids = []

        def slow_save(*args, **kwargs):
            thread_ids.append(threading.get_ident())
            entered.set()
            assert release.wait(5), "UI must release the writer without being blocked by it"
            return save(*args, **kwargs)

        async with app.run_test(size=(100, 32)) as pilot:
            await app._flush_unsent_work()
            monkeypatch.setattr(app._session_store, "save", slow_save)
            try:
                app.composer.set_text("old draft")
                app._save_unsent_work()
                while not entered.is_set():
                    await asyncio.sleep(0.01)
                assert thread_ids[0] != threading.get_ident()
                await pilot.press("end", "x")
                assert app.composer.text == "old draftx"
                await app.push_screen(PickerScreen("Sessions", [PickItem("test", "Other session")]))
                await pilot.press("escape")
                assert len(app.screen_stack) == 1
                # Reverting to a previously saved draft while an older write
                # is in flight must still overwrite that in-flight snapshot.
                app.composer.set_text("")
                app._save_unsent_work()
            finally:
                release.set()
                await app._flush_unsent_work()
            assert app._session_store.load("cli:direct")["draft"] == ""
    asyncio.run(run())


def test_burst_consumer_coalesces_deltas_and_preserves_stream_boundaries(tmp_path):
    async def run():
        events = []
        runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=events.append)
        runtime.bus = MessageBus()
        expected = []
        for stream in ("a", "b"):
            parts = [f"{stream}{i} " for i in range(900)]
            expected.append("".join(parts))
            for text in parts:
                await runtime.bus.publish_outbound(OutboundMessage(
                    "cli", "direct", text, event=StreamDeltaEvent(text, stream_id=stream),
                ))
            await runtime.bus.publish_outbound(OutboundMessage(
                "cli", "direct", "", event=StreamEndEvent(stream_id=stream),
            ))
        task = asyncio.create_task(runtime._consume_outbound())
        ticks = 0
        try:
            async with asyncio.timeout(3):
                while sum(isinstance(event, UiStreamEnd) for event in events) < 2:
                    ticks += 1
                    await asyncio.sleep(0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        segments = [""]
        for event in events:
            if isinstance(event, UiStreamDelta):
                segments[-1] += event.text
            elif isinstance(event, UiStreamEnd):
                segments.append("")
        assert segments == [*expected, ""]
        assert sum(isinstance(event, UiStreamDelta) for event in events) <= 10
        assert ticks >= 2
    asyncio.run(run())


def test_parallel_context_preparation_keeps_ui_available(tmp_path, monkeypatch):
    async def run():
        loop = make_loop(tmp_path, LocalProvider(LLMResponse(content="One"), LLMResponse(content="Two")))
        app = make_app(tmp_path)
        build = loop.context.build_messages
        govern = ContextGovernor.prepare_for_model
        phases = []

        def slow_build(*args, **kwargs):
            phases.append(("build", threading.get_ident()))
            time.sleep(0.15)
            return build(*args, **kwargs)

        def slow_govern(*args, **kwargs):
            phases.append(("govern", threading.get_ident()))
            time.sleep(0.15)
            return govern(*args, **kwargs)

        monkeypatch.setattr(loop.context, "build_messages", slow_build)
        monkeypatch.setattr(ContextGovernor, "prepare_for_model", slow_govern)
        try:
            async with app.run_test(size=(100, 32)) as pilot:
                tasks = [asyncio.create_task(loop.process_direct(
                    "Complete this task", session_key=f"cli:load-{i}", chat_id=f"load-{i}",
                )) for i in range(2)]
                await pilot.press("f", "l", "u", "i", "d", "e")
                assert app.composer.text == "fluide"
                results = await asyncio.wait_for(asyncio.gather(*tasks), 30)
                assert {result.content for result in results} == {"One", "Two"}
                assert {name for name, _ in phases} == {"build", "govern"}
                assert all(thread_id != threading.get_ident() for _, thread_id in phases)
                await app._flush_unsent_work()
        finally:
            loop.stop()
            await loop.close_mcp()
    asyncio.run(run())


def test_live_output_of_same_size_repaints_without_reflowing_the_transcript(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 40)) as pilot:
            row = ToolCall("run", "exec", {"command": "pytest checks.py"})
            await app.transcript.mount(row)
            row.apply(phase="output", output="\n".join(f"old_{i:03}" for i in range(80)))
            row.toggle()
            # Output is coalesced on a 100 ms timer. Let the initial frame
            # and its scrollbar layout settle before measuring a replacement.
            await pilot.pause(0.15)
            body = row.query_one(".tool-body", Static)
            with patch.object(body, "update", wraps=body.update) as update:
                row.apply(phase="output", output="\n".join(f"new_{i:03}" for i in range(80)), output_mode="snapshot")
                row._refresh_body()
                assert update.call_count == 1
                assert update.call_args.kwargs["layout"] is False
            assert "new_074" in str(body.content)
            assert "new_079" in row.copy_text()
            row.apply(phase="end")
    asyncio.run(run())


def test_hidden_live_output_keeps_latest_data_and_repaints_when_navigation_closes(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 40)) as pilot:
            row = ToolCall("run", "exec", {"command": "pytest checks.py"})
            await app.transcript.mount(row)
            row.apply(phase="output", output="\n".join(f"old_{i:03}" for i in range(80)))
            row.toggle()
            await pilot.pause()
            body = row.query_one(".tool-body", Static)
            await app.push_screen(PickerScreen("Sessions", [PickItem("a", "A")]))
            with patch.object(body, "update", wraps=body.update) as update:
                row.apply(phase="output", output="\n".join(f"latest_{i:03}" for i in range(80)), output_mode="snapshot")
                await pilot.pause(0.15)
                update.assert_not_called()
                assert "latest_079" in row.copy_text()
            app.pop_screen()
            await pilot.pause(0.2)
            assert "latest_074" in str(body.content)
            assert "old_074" not in str(body.content)
            row.apply(phase="end")
    asyncio.run(run())


def test_typing_paints_actual_characters_without_consuming_pending_transcript_work(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            compositor = app.screen._compositor
            pending = app.transcript.region
            compositor._dirty_regions.add(pending)
            frames = []
            display = app._display

            def capture(screen, frame):
                if frame is not None and not app._batch_count:
                    frames.append("".join(segment.text for segment in app.console.render(frame)
                                          if not segment.control))
                return display(screen, frame)

            with patch.object(app, "_display", capture):
                await app.composer._on_key(events.Key("x", "x"))
                assert frames and "x" in frames[-1]
                assert pending in compositor._dirty_regions
                with app.batch_update():
                    count = len(frames)
                    await app.composer._on_key(events.Key("y", "y"))
                    assert len(frames) == count
            await pilot.pause()
            assert app.composer.text == "xy"
            assert "xy" in "".join(strip.text for strip in app.composer.render_lines(app.composer.size.region))
    asyncio.run(run())


def test_modal_buffers_markdown_until_close_and_completion_remains_exact(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            block = await app._ensure_assistant()
            await block.set_text("First paragraph.\n\n")
            await block.reveal()
            body = block.query_one(Markdown)
            conversation = app.screen
            await app.push_screen(PickerScreen("Sessions", [PickItem("a", "A")]))
            assert not conversation.is_current
            with patch.object(body, "append", wraps=body.append) as append:
                await block.delta("Pending paragraph.\n\n")
                await pilot.pause(0.15)
                append.assert_not_called()
                assert block.text.endswith("Pending paragraph.\n\n")
                app.pop_screen()
                await pilot.pause(0.2)
                assert conversation.is_current
                assert body.source == block.text
            await block.finish(latency_ms=1, model=None, preset=None)
            assert body.source == block.text
    asyncio.run(run())


def test_paragraph_mount_does_not_pause_input_frames(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            block = await app._ensure_assistant()
            await block.set_text("Initial paragraph.\n\n")
            await block.reveal()
            await pilot.pause()
            body = block.query_one(Markdown)
            first = body.query_one("MarkdownParagraph")
            mount = body.mount_all
            entered, release = asyncio.Event(), asyncio.Event()

            async def delayed_mount(children):
                entered.set()
                await release.wait()
                await mount(children)

            with patch.object(body, "mount_all", delayed_mount):
                await block.delta("Next paragraph.\n\n")
                painting = asyncio.create_task(block._paint_body())
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    with patch.object(app, "_display", wraps=app._display) as display:
                        await app.composer._on_key(events.Key("x", "x"))
                        assert app.composer.text == "x"
                        assert display.called
                        assert not app._batch_count
                    await app.push_screen(PickerScreen("Sessions", [PickItem("a", "A")]))
                    await pilot.pause()
                    assert app.screen.query_one(".picker-title", Static).content == "Sessions"
                finally:
                    release.set()
                    await painting
            app.pop_screen()
            await block.finish(latency_ms=1, model=None, preset=None)
            assert body.source == "Initial paragraph.\n\nNext paragraph.\n\n"
            assert body.query_one("MarkdownParagraph") is first
    asyncio.run(run())


def test_live_follow_preserves_manual_scroll_and_resumes_at_the_bottom(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            block = await app._ensure_assistant()
            await block.set_text("\n\n".join(f"Paragraph {i}" for i in range(40)))
            await block.reveal()
            app.transcript.follow()
            await pilot.pause()
            assert app.transcript.is_vertical_scroll_end
            app.transcript.nudge(-10)
            await pilot.pause()
            position = app.transcript.scroll_y
            assert not app.transcript.auto_follow
            await block.delta("\n\nMore output.")
            await pilot.pause(0.15)
            assert app.transcript.scroll_y == position
            app.transcript.nudge(1000)
            await pilot.pause()
            await block.delta("\n\nNewest output.")
            await pilot.pause(0.15)
            assert app.transcript.is_vertical_scroll_end
    asyncio.run(run())


def test_animated_scroll_to_bottom_does_not_remove_its_own_animation(tmp_path):
    """Reproduce the scroll_y KeyError reported at the last animation frame."""
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.transcript.add(Static("A received line\n" * 100))
            await pilot.pause()
            app.transcript.nudge(-20)
            await pilot.pause()
            assert not app.transcript.auto_follow
            for _ in range(3):
                app.transcript.scroll_end(animate=True, duration=0.1, immediate=True)
                animator = app.animator
                key = (id(app.transcript), "scroll_y")
                assert key in animator._animations
                with patch.object(animator, "_get_time", return_value=animator._get_time() + 1):
                    animator()  # Previously raised KeyError here.
                await pilot.pause()
                assert app.transcript.auto_follow and app.transcript.is_vertical_scroll_end
                app.transcript.nudge(-20)
                await pilot.pause()
    asyncio.run(run())


def test_stream_keeps_completed_paragraph_layout_and_updates_inline_formatting(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)):
            block = await app._ensure_assistant()
            await block.set_text("Completed paragraph.\n\n")
            await block.reveal()
            body = block.query_one(Markdown)
            first = body.query_one("MarkdownParagraph")
            with patch.object(first, "set_content", wraps=first.set_content) as update:
                await body.append("**bold")
                update.assert_not_called()
            tail = body.children[-1]
            before = tail._content
            await body.append("**")
            assert body.children[-1] is tail
            assert not tail._content.is_same(before)
            assert tail._content.plain == "bold"
            assert tail.source == "**bold**"
            await body.update("Restored paragraph.\n\n")
            await body.append("Following paragraph.\n\n")
            assert [child._content.plain for child in body.children] == [
                "Restored paragraph.", "Following paragraph.",
            ]
            await body.update("```python\nprint('one')\n")
            await body.append("print('two')\n```\n")
            assert body.children[0].source == "```python\nprint('one')\nprint('two')\n```\n"
    asyncio.run(run())


def test_resize_defers_hidden_log_formatting_until_it_scrolls_into_view(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            row = ToolCall("run", "exec", {"command": "pytest checks.py"})
            await app.transcript.mount(row)
            row.apply(phase="output", output="\n".join(f"old_{i:03}" for i in range(80)))
            row.toggle()
            await pilot.pause()
            await app.transcript.mount(Static("Later content\n" * 60))
            app.transcript.follow()
            await pilot.pause()
            assert not app.screen.can_view_partial(row)
            with patch("navin.tui.widgets.preview_rows") as preview:
                row.apply(phase="output", output="\n".join(f"latest_{i:03}" for i in range(80)), output_mode="snapshot")
                row.on_resize()
                await pilot.pause(0.15)
                preview.assert_not_called()
            app.transcript.nudge(-1000)
            await pilot.pause(0.2)
            assert "latest_074" in str(row.query_one(".tool-body", Static).content)
            row.apply(phase="end")
    asyncio.run(run())


def test_session_picker_reveals_current_session_on_open(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            picker = PickerScreen("Sessions", [PickItem(str(i), f"Session {i}") for i in range(250)], current="123")
            await app.push_screen(picker)
            await pilot.pause()
            options = picker.query_one("#options")
            assert "Session 123" in "\n".join(strip.text for strip in options.render_lines(options.size.region))
            await pilot.press("down")
            assert picker._highlighted_item().id == "124"
    asyncio.run(run())
