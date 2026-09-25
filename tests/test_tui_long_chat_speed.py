# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Long chats stay fast: model text never breaks markup, old turns leave layout."""

import asyncio
import random

from textual.content import Content

from navin.tui.runtime import UiAssistantMessage
from navin.tui.textmarkup import escape
from navin.tui.widgets import AssistantMessage, SystemNote, Transcript, UserMessage
from tests.test_tui_queue import make_app


def test_escape_round_trips_any_text_in_textual_markup():
    rng = random.Random(7)
    alphabet = list("ab \\[]/$=\n") + ["[/]", "[bold]", "\\[", "[$x]", "\\\\"]
    for _ in range(3000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        markup = f"[$accent]✓[/] {escape(raw)}  [$text-muted]clic[/]"
        assert Content.from_markup(markup).plain == f"✓ {raw}  clic"


def test_folded_preview_of_model_text_with_closing_tags_renders():
    # Reported from history restore: MarkupError "auto closing tag ('[/]')
    # has nothing to close" once a backslash met a truncated preview.
    for raw in ("Deployment terminé [/]", "a\\[/] " * 60, "x \\" * 90 + " [$x] fin"):
        block = AssistantMessage.__new__(AssistantMessage)
        block._buffer = [raw]
        block.finished = False
        Content.from_markup(block._preview_markup())


def test_old_turns_leave_layout_and_come_back_on_scroll_up(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = app.transcript
            for index in range(40):
                await transcript.add(UserMessage(f"question {index}"))
                await transcript.add(SystemNote(f"answer {index}\n" + "line\n" * 6))
            await pilot.pause()
            transcript._trim()
            await pilot.pause()
            hidden = [child for child in transcript.children if not child.display]
            assert hidden, "blocks far above the viewport should leave layout"
            assert hidden == list(transcript.children[: len(hidden)])
            assert transcript.children[-1].display
            assert transcript.is_vertical_scroll_end
            before = transcript.windowed_count
            transcript.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await pilot.pause()
            assert transcript.windowed_count < before
            assert not transcript.auto_follow
            # Find / jump reveals everything from a hidden block down.
            first = transcript.children[0]
            transcript.reveal_windowed(through=first)
            assert all(child.display for child in transcript.children)
    asyncio.run(run())


def test_live_turns_keep_working_after_older_blocks_are_windowed(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            for index in range(30):
                await app.transcript.add(SystemNote(f"note {index}\n" + "row\n" * 8))
            await pilot.pause()
            app.transcript._trim()
            await app.submit_text("next")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiAssistantMessage(text="final **answer**", metadata={}))
            await pilot.pause()
            last = app.query(AssistantMessage).last()
            assert last.display and last.text == "final **answer**"
            assert isinstance(app.transcript, Transcript)
            assert app.transcript.windowed_count > 0
    asyncio.run(run())


def test_notifications_leave_as_soon_as_the_answer_continues(tmp_path):
    from navin.tui.runtime import UiNotification, UiStreamDelta

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await app.submit_text("go")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiStreamDelta("Working on it. "))
            await app._on_runtime_event(UiNotification("Checkpoint", "info", "saved"))
            await pilot.pause()
            assert any(getattr(n, "transient", False) for n in app.query(SystemNote))
            await app._on_runtime_event(UiStreamDelta("Still the same answer."))
            await pilot.pause()
            assert not [n for n in app.query(SystemNote) if n.display and getattr(n, "transient", False)]
            # The answer continues in the same bubble instead of splitting.
            assert len(app.query(AssistantMessage)) == 1
            await app._on_runtime_event(UiNotification("Broken", "error", "keep me"))
            await pilot.pause()
            errors = [n for n in app.query(SystemNote) if not getattr(n, "transient", False)]
            assert errors, "errors stay in the chat"
    asyncio.run(run())


def test_dragging_a_selection_above_the_chat_scrolls_and_copies_the_whole_reply(tmp_path):
    from textual import events

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 20)) as pilot:
            await app.submit_text("q")
            await app.runtime.bus.consume_inbound()
            text = "\n\n".join(f"Paragraph {index} of the answer." for index in range(40))
            await app._on_runtime_event(UiAssistantMessage(text=text, metadata={}))
            app.runtime._finish_turn({})
            await pilot.pause(0.3)
            transcript = app.transcript
            screen = app.screen
            region = transcript.region
            bottom_y = region.bottom - 2
            start_before = transcript.scroll_y

            def mouse(kind, y, button=1):
                return kind(None, 5, y, 0, 0, button, False, False, False, screen_x=5, screen_y=y)

            screen._forward_event(mouse(events.MouseDown, bottom_y))
            screen._forward_event(mouse(events.MouseMove, bottom_y - 3))
            # Hold the button above the chat: the terminal sends no more events.
            screen._forward_event(mouse(events.MouseMove, region.y))
            await pilot.pause(1.5)
            assert transcript.scroll_y < start_before, "the chat scrolls while the drag holds the top edge"
            screen._forward_event(mouse(events.MouseUp, region.y))
            await pilot.pause()
            selected = screen.get_selected_text() or ""
            last_visible = selected.splitlines()[-1] if selected else ""
            first = int(selected.split("Paragraph ", 1)[1].split(" ", 1)[0])
            assert first < 30, selected[:80]
            # Nothing between the two ends is lost, including blocks now off screen.
            for index in range(first + 1, 38):
                assert f"Paragraph {index} " in selected, (index, last_visible)
    asyncio.run(run())


def test_parallel_agents_show_under_the_chat_in_at_most_ten_rows(tmp_path):
    from navin.tui.runtime import UiSubagent
    from navin.tui.widgets import AgentsPanel

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            panel = app.query_one("#agents", AgentsPanel)
            assert not panel.display
            for index in range(12):
                await app._on_runtime_event(UiSubagent(
                    f"t{index}", "general-purpose", "awaiting_tools", f"Running step {index}",
                    None, 1, False, None, started_ms_ago=370_000, tokens=335_500,
                    task_description=f"Task number {index}",
                ))
            await pilot.pause()
            assert panel.display
            lines = str(panel.content).splitlines()
            assert lines[0].startswith("● main")
            assert len(lines) <= AgentsPanel.MAX_ROWS
            assert "+4 more agents" in lines[-1]
            # Same label for every agent: rows are named by their task instead.
            assert "Task number 0" in lines[1]
            assert "6m 10s · ↓ 335.5k tokens" in lines[1]
            assert not app.query(SystemNote), "progress never floods the chat"
            for index in range(12):
                await app._on_runtime_event(UiSubagent(
                    f"t{index}", "general-purpose", "done", "Completed", None, 2, True, None,
                ))
            for row in panel._agents.values():
                row["done_at"] -= AgentsPanel.DONE_LINGER_S + 1
            panel.tick(False, 0)
            assert not panel.display
    asyncio.run(run())
