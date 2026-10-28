# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Long chats stay fast: model text never breaks markup, old turns leave layout."""

import asyncio
import random

import textual.app
from rich.cells import cell_len
from textual._compositor import ChopsUpdate, LayoutUpdate
from textual.content import Content
from textual.geometry import Region

from navin.tui.runtime import UiAssistantMessage
from navin.tui.textmarkup import escape
from navin.tui.widgets import AssistantMessage, SystemNote, Transcript, UserMessage
from tests.test_tui_queue import make_app


class _Terminal:
    """What the user's terminal shows: every frame Textual writes, applied in order.

    Textual's own ``App._display`` writes nothing in headless tests, so the
    frames it would write (outside a batch, like the real one) are replayed
    on a grid of cells, the way ``ChopsUpdate.render_segments`` moves the
    cursor and writes each span.
    """

    def __init__(self, width: int, height: int) -> None:
        self.rows = [[" "] * width for _ in range(height)]
        self.frames = 0

    def _write(self, y: int, x: int, text: str) -> None:
        if not 0 <= y < len(self.rows):
            return
        row = self.rows[y]
        for char in text:
            if x >= len(row):
                break
            row[x] = char
            width = cell_len(char)
            for pad in range(1, width):
                if x + pad < len(row):
                    row[x + pad] = ""
            x += max(1, width)

    def apply(self, update) -> None:
        self.frames += 1
        if isinstance(update, LayoutUpdate):
            for offset, strips in enumerate(update.strips):
                self._write(update.region.y + offset, update.region.x, "".join(strip.text for strip in strips))
            return
        for y, x1, x2 in update.spans:
            for end, (x, strip) in zip(update.chop_ends[y], update.chops[y].items()):
                if strip is None or x > x2 or end <= x1:
                    continue
                if not (x2 > x >= x1 and end <= x2):
                    strip = strip.crop(0, min(end, x2) - x)
                self._write(y, x, strip.text)

    def lines(self) -> list[str]:
        return ["".join(row).rstrip() for row in self.rows]


def _composed(app) -> list[str]:
    """What the compositor says is on screen right now."""
    screen = _Terminal(app.size.width, app.size.height)
    for y, strip in enumerate(app.screen._compositor.render_strips()):
        screen._write(y, 0, strip.text)
    return screen.lines()


def _watch_terminal(app, monkeypatch) -> _Terminal:
    terminal = _Terminal(app.size.width, app.size.height)

    def display(self, screen, renderable):
        if renderable is not None and not self._batch_count and isinstance(renderable, (ChopsUpdate, LayoutUpdate)):
            terminal.apply(renderable)

    monkeypatch.setattr(textual.app.App, "_display", display)
    return terminal


async def _settled_terminal(app, pilot, terminal: _Terminal) -> list[tuple[int, str, str]]:
    """Rows where the terminal differs from the composition once idle.

    The whole screen counts: the chat, and the thin scrollbar Navin draws on
    the screen edge (a stale thumb was the first visible symptom).
    """
    for _ in range(8):
        await pilot.pause(0.05)
    truth = _composed(app)
    shown = terminal.lines()
    return [(y, shown[y], truth[y]) for y in range(len(truth)) if shown[y] != truth[y]]


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
    from navin.tui.virtual import BlockRecord, RecordGap

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = app.transcript
            for index in range(40):
                await transcript.add(UserMessage(f"question {index}"))
                await transcript.add(SystemNote(f"answer {index}\n" + "line\n" * 6))
            await pilot.pause()
            await transcript.sync_window()
            await pilot.pause()
            # Blocks far above the viewport are records, not widgets.
            assert isinstance(transcript.children[0], RecordGap)
            assert transcript.windowed_count > 0
            assert transcript.children[-1].display
            assert transcript.is_vertical_scroll_end
            assert len(list(transcript.blocks())) == 80
            before = transcript.windowed_count
            transcript.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await transcript.sync_window()
            await pilot.pause()
            # The top of the chat is widgets again; the tail became records.
            first = transcript.children[0]
            assert isinstance(first, UserMessage) and first.raw_text == "question 0"
            assert transcript.windowed_count and before
            assert not transcript.auto_follow
            # Find / jump builds a record back and scrolls to it.
            record = next(block for block in transcript.blocks() if isinstance(block, BlockRecord))
            assert transcript.reveal_windowed(through=record)
            for _ in range(20):
                await pilot.pause(0.05)
                if not any(block is record for block in transcript.blocks()):
                    break
            assert not any(block is record for block in transcript.blocks())
    asyncio.run(run())


def test_synchronized_output_is_forced_on_terminals_that_draw_frames_whole():
    from navin.tui.terminal import forces_synchronized_output

    assert forces_synchronized_output({"WT_SESSION": "x", "WSL_DISTRO_NAME": "Ubuntu"})
    assert forces_synchronized_output({"KITTY_WINDOW_ID": "1"})
    assert not forces_synchronized_output({"TERM": "xterm-256color"})
    # tmux and screen redraw on their own: Textual's detection decides there.
    assert not forces_synchronized_output({"WT_SESSION": "x", "TMUX": "/tmp/tmux"})
    assert forces_synchronized_output({"NAVIN_SYNC_OUTPUT": "1"})
    assert not forces_synchronized_output({"WT_SESSION": "x", "NAVIN_SYNC_OUTPUT": "0"})


def test_the_terminal_tab_title_is_navin_and_the_project_folder():
    from navin.tui.terminal import project_title, title_sequence

    assert project_title("/home/me/projects/my-app") == "navin-my-app"
    assert project_title("/home/me/projects/my-app/") == "navin-my-app"
    assert project_title("C:\\Users\\me\\my app") == "navin-my app"
    assert project_title("/") == "navin-cli"
    # OSC 0 names the tab (Windows Terminal, iTerm2, kitty, GNOME...); inside
    # tmux the screen-style rename follows, since OSC 0 only names the pane.
    assert title_sequence("navin-my-app", {}) == "\x1b]0;navin-my-app\x07"
    assert title_sequence("navin-my-app", {"TMUX": "/tmp/tmux-1000/default,1,0"}) == (
        "\x1b]0;navin-my-app\x07\x1bknavin-my-app\x1b\\"
    )
    # control characters in a folder name never reach the terminal
    assert title_sequence("navin-a\x1b]0;evil\x07b", {}) == "\x1b]0;navin-a]0;evilb\x07"


def test_windows_terminal_gets_whole_frames_without_a_decrqm_answer(tmp_path, monkeypatch):
    """Windows Terminal through WSL: frames are wrapped in mode 2026 from the first one."""
    monkeypatch.setenv("WT_SESSION", "session")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("NAVIN_SYNC_OUTPUT", raising=False)

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 20)) as pilot:
            app.screen.refresh()
            await pilot.pause()
            assert app._sync_available
        monkeypatch.delenv("WT_SESSION")
        plain = make_app(tmp_path)
        async with plain.run_test(size=(80, 20)) as pilot:
            plain.screen.refresh()
            await pilot.pause()
            assert not plain._sync_available
    asyncio.run(run())


def test_frames_dropped_inside_a_batch_are_painted_when_it_closes(tmp_path, monkeypatch):
    """Textual drops a frame composed in batch_update() and forgets its rows."""
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 20)) as pilot:
            await app.transcript.add(UserMessage("Q000 first"))
            await pilot.pause()
            terminal = _watch_terminal(app, monkeypatch)
            app.screen.refresh()
            assert not await _settled_terminal(app, pilot, terminal)
            row = next(y for y, line in enumerate(terminal.lines()) if "Q000 first" in line)
            # The rows the user's terminal still shows from before a layout run
            # inside a batch, as the chat window does when blocks come back.
            terminal.rows[row] = [" "] * len(terminal.rows[row])
            screen = app.screen
            with app.batch_update():
                screen._compositor._dirty_regions.add(Region(0, row, app.size.width, 1))
                screen._compositor_refresh()  # composed now, dropped by Textual
            await pilot.pause()
            assert "Q000 first" in terminal.lines()[row]
            assert not await _settled_terminal(app, pilot, terminal)
    asyncio.run(run())


def test_terminal_shows_the_chat_after_scrolling_sending_and_streaming(tmp_path, monkeypatch):
    """Lines stayed on the terminal twice after the chat window settled.

    Blocks leaving or coming back into the window are laid out inside
    ``batch_update()``. Textual drops a frame composed during a batch and
    forgets its regions, so the rows it covered kept their old text (the
    same lines twice, or blank rows) until something else repainted them.
    """
    from navin.tui.runtime import UiStreamDelta, UiStreamEnd
    from navin.tui.virtual import RecordGap

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = app.transcript
            terminal = _watch_terminal(app, monkeypatch)
            for index in range(30):
                await app.submit_text(f"Q{index:03d} question {index}")
                await app.runtime.bus.consume_inbound()
                body = "\n\n".join(f"A{index:03d}-{row} answer {index}" for row in range(6))
                await app._on_runtime_event(UiAssistantMessage(text=body, metadata={}))
                app.runtime._finish_turn({})
            await pilot.pause(0.6)
            await transcript.sync_window()
            await pilot.pause(0.3)
            app.animation_level = "none"
            assert not await _settled_terminal(app, pilot, terminal), "after the turns"
            assert isinstance(transcript.children[0], RecordGap)

            # Up to the top: the records there are built back into widgets.
            transcript.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await transcript.sync_window()
            assert isinstance(transcript.children[0], UserMessage)
            assert not await _settled_terminal(app, pilot, terminal), "after scrolling up"

            # Back down: the tail is built again, the top leaves the window.
            transcript.scroll_end(animate=False, immediate=True)
            await pilot.pause()
            await transcript.sync_window()
            assert not isinstance(transcript.children[-1], RecordGap)
            assert not await _settled_terminal(app, pilot, terminal), "after scrolling down"

            await app.submit_text("U000 the new message")
            await app.runtime.bus.consume_inbound()
            assert not await _settled_terminal(app, pilot, terminal), "after sending"
            for row in range(30):
                await app._on_runtime_event(UiStreamDelta(f"S{row:03d} streamed paragraph {row}.\n\n"))
                await pilot.pause(0.01)
            await app._on_runtime_event(UiStreamEnd())
            app.runtime._finish_turn({})
            await transcript.sync_window()
            assert not await _settled_terminal(app, pilot, terminal), "after streaming"
            assert any("S029 streamed paragraph 29." in line for line in terminal.lines())
            assert terminal.frames > 3
    asyncio.run(run())
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            for index in range(30):
                await app.transcript.add(SystemNote(f"note {index}\n" + "row\n" * 8))
            await pilot.pause()
            await app.transcript.sync_window()
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
            # Start inside the answer text: the copy icon under it is not text.
            body = app.query_one(AssistantMessage).query_one(".assistant-body")
            bottom_y = min(region.bottom, body.region.bottom) - 2
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


def test_drag_selection_repaints_only_blocks_whose_selection_changed():
    from textual.geometry import Offset
    from textual.selection import SELECT_ALL, Selection

    from navin.tui.app import NavinScreen

    class Block:
        def __init__(self, name):
            self.name = name

        def selection_updated(self, selection):
            repainted.append(self.name)

    repainted: list[str] = []
    first, middle, last, extra = (Block(name) for name in ("first", "middle", "last", "extra"))
    start = Selection(Offset(4, 0), None)
    # The drag moves down one block: "last" becomes fully selected, "extra" is the new end.
    old = {first: start, middle: SELECT_ALL, last: Selection(None, Offset(7, 1))}
    new = {first: start, middle: SELECT_ALL, last: SELECT_ALL, extra: Selection(None, Offset(2, 0))}
    asyncio.run(NavinScreen._watch_selections(None, old, new))
    assert sorted(repainted) == ["extra", "last"]
    repainted.clear()
    asyncio.run(NavinScreen._watch_selections(None, new, {}))
    assert sorted(repainted) == ["extra", "first", "last", "middle"]


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
            assert "show more (+6)" in lines[-1]
            await pilot.press("f7")
            await pilot.pause()
            expanded = str(panel.content).splitlines()
            assert len(expanded) == 1 + 12 + 1
            assert "show less" in expanded[-1]
            await pilot.press("f7")
            await pilot.pause()
            assert len(str(panel.content).splitlines()) <= AgentsPanel.MAX_ROWS
            # Same label for every agent: rows are named by their task instead.
            assert "Task number 0" in lines[1]
            assert "6m 10s · ↓ 335.5k tokens" in lines[1]
            assert not app.query(SystemNote), "progress never floods the chat"
            for index in range(12):
                await app._on_runtime_event(UiSubagent(
                    f"t{index}", "general-purpose", "done", "Completed", None, 2, True, None,
                ))
            for row in panel._agents.values():
                row["done_at"] -= 3600
            panel.tick(False, 0)
            assert panel.display, "finished agents stay until dismissed"
            assert panel.dismiss_finished() == 12
            assert not panel.display
    asyncio.run(run())


def test_eight_agents_show_six_rows_then_show_more_expands(tmp_path):
    from navin.tui.runtime import UiSubagent
    from navin.tui.widgets import AgentsPanel

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one("#agents", AgentsPanel)
            for index in range(8):
                await app._on_runtime_event(UiSubagent(
                    f"a{index}", f"Worker {index}", "awaiting_model", f"Step {index}",
                    None, 1, False, None,
                ))
            await pilot.pause()
            lines = str(panel.content).splitlines()
            assert len(lines) == 1 + 6 + 1
            assert "show more (+2)" in lines[-1]
            panel.toggle_expanded()
            lines = str(panel.content).splitlines()
            assert sum("Worker" in line for line in lines) == 8
            assert "show less" in lines[-1]
            await app._on_runtime_event(UiSubagent("a0", "Worker 0", "done", "Completed", None, 2, True, None))
            assert "Done" in str(panel.content)
            panel._agents["a0"]["done_at"] -= 3600
            panel.dismiss("a0")
            await app._on_runtime_event(UiSubagent("a1", "Worker 1", "awaiting_tools", "Reading files", None, 3, False, None))
            content = str(panel.content)
            assert "Worker 0" not in content
            assert "Reading files" in content
            for index in range(1, 3):
                await app._on_runtime_event(UiSubagent(f"a{index}", "w", "done", "Completed", None, 2, True, None))
                panel.dismiss(f"a{index}")
            panel.tick(False, 0)
            lines = str(panel.content).splitlines()
            assert len(lines) == 1 + 5
            assert not any("show" in line for line in lines)
    asyncio.run(run())


def test_prompt_stars_fill_the_row_and_drift_with_the_status_tick(tmp_path):
    from navin.tui.widgets import StarField

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            (top,) = app._stars
            assert isinstance(top, StarField) and top.is_attached
            assert top.region.y == app.composer.region.bottom, "the stars sit right under the prompt line"
            before = top.render_line(0)
            assert before.cell_length == top.outer_size.width
            assert before.text.strip(), "a few stars are visible"
            frame = top.frame
            for _ in range(app.STAR_STEP_TICKS * 5):
                app._drift_stars()
            assert top.frame > frame
            after = top.render_line(0)
            assert after.cell_length == before.cell_length
            assert after.text != before.text, "the stars move"
            app.animation_level = "none"
            frame = top.frame
            for _ in range(app.STAR_STEP_TICKS * 3):
                app._drift_stars()
            assert top.frame == frame, "reduced motion keeps the stars still"
    asyncio.run(run())


def test_changing_the_effort_fills_its_bars_and_glows_then_settles(tmp_path):
    from unittest.mock import patch

    from textual.widgets import Static

    from navin.tui.widgets import ComposerMeta

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            label = app.query_one("#meta-reasoning", Static)
            bars = app.query_one("#meta-effort", Static)

            def choose(effort):
                with patch.object(app.runtime, "reasoning_details", return_value=(effort, ())):
                    app._set_status()

            async def settle(condition, frames):
                # Waits on the state, not a frame count: a loaded machine runs
                # the animation timer late, never differently.
                deadline = ComposerMeta.EFFORT_FRAME_S * frames * 20
                waited = 0.0
                while not condition() and waited < deadline:
                    await pilot.pause(ComposerMeta.EFFORT_FRAME_S)
                    waited += ComposerMeta.EFFORT_FRAME_S
                assert condition()

            choose("low")
            await settle(
                lambda: str(bars.content) == "▂" and not label.has_class("-glow"),
                ComposerMeta.EFFORT_GLOW_FRAMES + 8,
            )
            choose("high")
            assert str(label.content) == "high"
            assert label.has_class("-glow") and str(bars.content) == "▂", "bars fill one per frame"
            await settle(lambda: len(str(bars.content)) >= 2, 3)
            await settle(
                lambda: str(bars.content) == "▂▄▆"
                and not label.has_class("-glow")
                and not bars.has_class("-glow"),
                ComposerMeta.EFFORT_GLOW_FRAMES + 6,
            )
            assert label.has_class("-lvl-3") and bars.has_class("-lvl-3") and not label.has_class("-lvl-1")
            assert label.styles.color != app.query_one("#meta-model", Static).styles.color, "the effort is colored"
            choose("")
            await settle(
                lambda: str(label.content) == "Auto" and str(bars.content) == "",
                ComposerMeta.EFFORT_GLOW_FRAMES + 8,
            )
    asyncio.run(run())
