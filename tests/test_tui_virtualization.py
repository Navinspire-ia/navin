# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Off-screen transcript blocks live as records; one frame clock paints streams."""

import asyncio

from textual import events
from textual.widgets import Static

from navin.tui.frame_scheduler import FrameScheduler, frame_scheduler
from navin.tui.settled import SettledMarkdown, render_markdown_lines
from navin.tui.virtual import BlockRecord, RecordGap
from navin.tui.widgets import AssistantMessage, SystemNote, ToolCall, Transcript, UserMessage
from tests.test_tui_queue import make_app

ANSWER = """Done: **all checks** pass in `navin/tui/app.py`.

- First point with `pytest -q`
- 14 tests passed

| col | val |
|-----|-----|
| a   | 1   |

```python
x = 1
```
"""


async def _fill(app, pilot, count: int, *, chunk: int = 100) -> Transcript:
    transcript = app.transcript
    for start in range(0, count, chunk):
        for index in range(start, min(count, start + chunk)):
            if index % 2:
                await transcript.mount(SystemNote(f"answer {index}\nline two of {index}"))
            else:
                await transcript.mount(UserMessage(f"question {index}"))
        transcript.scroll_end(animate=False, immediate=True)
        await pilot.pause()
        await transcript.sync_window()
    await pilot.pause()
    await transcript.sync_window()
    return transcript


def _visible_widgets(transcript: Transcript):
    top = transcript.scroll_y
    bottom = top + transcript.scrollable_content_region.height
    out = []
    for child in transcript.children:
        if isinstance(child, RecordGap) or not child.display:
            continue
        region = child.virtual_region
        if region.bottom > top and region.y < bottom:
            out.append((child, region.y))
    return out


def test_two_thousand_messages_keep_a_bounded_number_of_widgets(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = await _fill(app, pilot, 2000)
            mounted = transcript.mounted_block_count()
            assert mounted <= 60, mounted
            assert transcript.windowed_count + mounted == 2000
            nodes = len(list(transcript.walk_children()))
            assert nodes <= 200, nodes
            # Records stand in for widgets in reading order, with their text.
            blocks = list(transcript.blocks())
            assert len(blocks) == 2000
            assert isinstance(blocks[0], UserMessage) and isinstance(blocks[0], BlockRecord)
            assert blocks[0].raw_text == "question 0"
            assert transcript.is_vertical_scroll_end
            assert isinstance(transcript.children[-1], SystemNote)
    asyncio.run(run())


def test_scrolling_up_builds_older_blocks_with_a_stable_anchor(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = await _fill(app, pilot, 400)
            for _ in range(6):
                transcript.page(-1)
                await pilot.pause()
                await transcript.sync_window()
            assert not transcript.auto_follow
            before = _visible_widgets(transcript)
            assert before, "something is on screen"
            anchor, anchor_y = before[0]
            screen_y = anchor_y - transcript.scroll_y
            # A pass that builds and drops around the viewport keeps the
            # reader's block exactly where it was.
            transcript.scroll_relative(y=-transcript.scrollable_content_region.height, animate=False, immediate=True)
            await transcript.sync_window()
            transcript.scroll_relative(y=transcript.scrollable_content_region.height, animate=False, immediate=True)
            await transcript.sync_window()
            await pilot.pause()
            assert anchor.is_attached
            assert anchor.virtual_region.y - transcript.scroll_y == screen_y
            # All the way up: the very first message comes back as a widget.
            for _ in range(200):
                if not transcript.windowed_count or not isinstance(transcript.children[0], RecordGap):
                    break
                transcript.scroll_home(animate=False, immediate=True)
                await pilot.pause()
                await transcript.sync_window()
            first = transcript.children[0]
            assert isinstance(first, UserMessage) and first.raw_text == "question 0"
            assert transcript.mounted_block_count() <= 80
    asyncio.run(run())


def test_find_copy_and_export_see_blocks_held_as_records(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = await _fill(app, pilot, 300)
            assert transcript.windowed_count
            assert app._last_copyable_text().startswith("question 298")
            app._find_move("question 12", 0)
            hit = app._find_hits[0]
            assert isinstance(hit, BlockRecord)
            # The hit is mounted hidden, shown, then scrolled to after the next
            # refresh: wait for that end state, which a loaded machine reaches
            # later, instead of checking the first frame the widget exists.
            def in_view(widget):
                region, top = widget.virtual_region, transcript.scroll_y
                height = transcript.scrollable_content_region.height
                return region.y >= top - 1 and region.bottom <= top + height + 1

            found = []
            for _ in range(200):
                await pilot.pause(0.05)
                found = [
                    c for c in transcript.children
                    if isinstance(c, UserMessage) and c.has_class("-find") and c.display
                ]
                if found and found[0].virtual_region.height and in_view(found[0]):
                    break
            assert found and found[0].raw_text == "question 12"
            region = found[0].virtual_region
            top = transcript.scroll_y
            assert region.height, "the hit is laid out"
            assert region.y >= top - 1 and region.bottom <= top + transcript.scrollable_content_region.height + 1, (
                region, top)
    asyncio.run(run())


def test_finished_turn_comes_back_as_one_settled_answer_with_working_tools(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            transcript = app.transcript
            block = AssistantMessage("navin")
            await transcript.add(block)
            await block.tool_event("t1", "exec", "start", {"command": "pytest -q"}, None, None, None)
            await block.tool_event("t1", "exec", "end", {"command": "pytest -q"}, "6 passed\nExit code: 0", None, None)
            await block.tool_event("r1", "read_file", "start", {"path": "a.py"}, None, None, None)
            await block.tool_event("r1", "read_file", "end", {"path": "a.py"}, "x = 1", None, None)
            await block.delta(ANSWER)
            await block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            copied = block.copy_text()
            record = block.snapshot_record()
            assert record is not None and isinstance(record, AssistantMessage)
            assert record.copy_text() == copied
            rebuilt = record.build()
            await transcript.mount(rebuilt)
            await pilot.pause()
            body = rebuilt.query_one(".assistant-body.-answer")
            assert isinstance(body, SettledMarkdown) and body.display
            assert len(list(rebuilt.walk_children())) < len(list(block.walk_children()))
            assert rebuilt.text == block.text and rebuilt.copy_text() == copied
            tools = list(rebuilt.query(ToolCall))
            assert [tool.tool_name for tool in tools] == [tool.tool_name for tool in block.query(ToolCall)]
            assert all(tool.phase == "end" for tool in tools)
            run_row = next(tool for tool in tools if tool.tool_name == "exec")
            opened = run_row._open
            run_row.toggle()
            assert run_row._open is not opened
            assert "6 passed" in run_row.copy_text()
            # The settled answer keeps the answer's words, selectable as text.
            plain = str(body.render().plain)
            assert "all checks" in plain and "pytest -q" in plain and "x = 1" in plain
    asyncio.run(run())


def test_settled_markdown_lines_fit_the_width():
    lines = render_markdown_lines(ANSWER + "\n" + "word " * 80, 40)
    assert lines and all(line.cell_length <= 40 for line in lines)
    assert any(line.plain.startswith("• First point") for line in lines)


def test_keyboard_runs_before_a_pending_stream_frame(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            order: list[str] = []
            block = AssistantMessage("navin")
            await app.transcript.add(block)
            await pilot.pause()
            original = block._flush_stream

            async def flush():
                # Which came first: the key (already in the prompt) or the frame.
                order.append("key-then-frame" if app.composer.text.endswith("z") else "frame-first")
                await original()

            block._flush_stream = flush
            app.composer.focus()
            await pilot.pause()
            scheduler = frame_scheduler(app)
            await block.delta("streamed words")
            assert scheduler.pending((block, "stream"))
            # Make the frame due now, with a key press already queued.
            key, (_due, callback) = next(iter(scheduler._items.items()))
            scheduler._items[key] = (0.0, callback)
            app.composer.post_message(events.Key("z", "z"))
            await scheduler._frame()
            assert order == [], "the frame waits while a key is queued"
            assert scheduler.deferred_for_keys >= 1
            await pilot.pause(0.2)
            assert order[:1] == ["key-then-frame"]
            assert app.composer.text.endswith("z")
            assert block._painted_markdown == "streamed words"
    asyncio.run(run())


def test_frame_scheduler_coalesces_and_backs_off_after_an_overrun(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 20)) as pilot:
            scheduler = FrameScheduler(app, frame_seconds=0.02, budget_seconds=0.05)
            calls: list[str] = []
            scheduler.request("a", lambda: calls.append("a"))
            scheduler.request("a", lambda: calls.append("a2"))
            assert len(scheduler) == 1
            await pilot.pause(0.1)
            assert calls == ["a2"], "two requests before a frame paint once, the newest state"
            started = asyncio.get_running_loop().time()
            stamps: list[float] = []

            def slow():
                import time

                stamps.append(asyncio.get_running_loop().time())
                time.sleep(0.05)  # an overrun frame
                if len(stamps) < 3:
                    scheduler.request("slow", slow, delay=0)

            scheduler.request("slow", slow, delay=0)
            await pilot.pause(0.5)
            assert len(stamps) == 3
            assert scheduler.overruns >= 2
            # Frames after an overrun wait a full frame instead of stacking.
            assert all(b - a >= 0.05 + 0.02 - 0.005 for a, b in zip(stamps, stamps[1:])), (started, stamps)
            label = Static("x")
            await app.mount(label)
            assert not scheduler.pending("missing")
    asyncio.run(run())
