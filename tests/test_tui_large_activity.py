# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Large file operations retain their evidence without flooding the UI."""

import asyncio
from unittest.mock import patch

from textual import events
from textual.widgets import Button

from navin.tui.screens import PickerScreen, PickItem
from navin.tui.widgets import ACTIVITY_PAGE_SIZE, AssistantMessage, ToolCall, ToolCluster
from tests.test_tui_queue import make_app


def test_large_operation_keeps_input_stop_and_deferred_diffs_available(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 36)) as pilot:
            await app.submit_text("Clean up this project")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("Queued follow-up")
            block = await app._ensure_assistant()
            await block.tool_event("delete", "manage_files", "start", {"action": "delete"}, None, None, None)
            for index in range(790):
                await block.note_file_edit(
                    f"src/file_{index}.py", 0, 18, call_id="delete", tool="manage_files",
                    kind="delete", diff=f"@@ -1 +0,0 @@\n-value_{index} = 1",
                )
            await block.tool_event("delete", "manage_files", "end", {}, "Deleted files", None, None)
            await pilot.pause()
            cluster = block.query_one(ToolCluster)
            assert len(cluster.tools) == 790
            assert len(cluster.query(ToolCall)) == ACTIVITY_PAGE_SIZE
            assert "790 files (+0 -14220)" in cluster._head_text()
            assert "value_789" in cluster.tools[-1].copy_text()
            assert cluster.tools[-1].phase == "end"
            assert cluster.tools[-1]._output_timer is None
            await pilot.press("o", "k")
            assert app.composer.text == "ok"
            await pilot.press("escape")
            stop = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert stop.content == "/stop"
            assert app.runtime.session_key in app._queue_paused
            assert len(app._queued_prompts[app.runtime.session_key]) == 1
            more = cluster.query_one(".cluster-more", Button)
            more.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click(more)
            assert len(cluster.query(ToolCall)) == 2 * ACTIVITY_PAGE_SIZE
            revealed = cluster.tools[ACTIVITY_PAGE_SIZE]
            assert revealed.is_attached
            assert f"value_{ACTIVITY_PAGE_SIZE}" in revealed.copy_text()
            await app._flush_unsent_work()
    asyncio.run(run())


def test_history_mount_does_not_suppress_input_or_navigation_frames(tmp_path):
    async def run():
        app = make_app(tmp_path)
        app.runtime.history_snapshot = lambda: [{"role": "assistant", "content": "Saved reply"}]
        entered, release = asyncio.Event(), asyncio.Event()
        paint = app._paint_history_rows

        async def delayed_paint(*args, **kwargs):
            entered.set()
            await release.wait()
            await paint(*args, **kwargs)

        async with app.run_test(size=(110, 36)) as pilot:
            with patch.object(app, "_paint_history_rows", delayed_paint):
                painting = asyncio.create_task(app._render_history())
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    assert not app._batch_count
                    with patch.object(app, "_display", wraps=app._display) as display:
                        await app.composer._on_key(events.Key("x", "x"))
                        assert display.called
                    await app.push_screen(PickerScreen("Sessions", [PickItem("other", "Other session")]))
                    await pilot.pause()
                    assert isinstance(app.screen, PickerScreen)
                    app.pop_screen()
                finally:
                    release.set()
                    await painting
            assert app.transcript.visible
            assert app.composer.text == "x"
            await app._flush_unsent_work()
    asyncio.run(run())


def test_reopen_large_saved_operation_retains_every_file_and_failure(tmp_path):
    async def run():
        app = make_app(tmp_path)
        edits = [{
            "path": f"src/saved_{index}.py", "added": 1, "removed": 1,
            "call_id": "batch", "tool": "apply_patch", "kind": "edit", "phase": "end",
            "diff": f"@@ -1 +1 @@\n-old_{index}\n+new_{index}",
        } for index in range(790)]
        edits[-1].update(phase="error", error="File could not be changed")
        app.runtime.history_snapshot = lambda: [
            {"role": "user", "content": "Update the project"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "batch", "function": {"name": "apply_patch", "arguments": {}},
            }]},
            {"role": "tool", "tool_call_id": "batch", "content": "Updated files", "_file_edits": edits},
        ]
        async with app.run_test(size=(110, 36)) as pilot:
            await app._render_history()
            await pilot.pause()
            block = app.query_one(AssistantMessage)
            cluster = block.query_one(ToolCluster)
            assert len(cluster.tools) == 790
            assert len(app.query(ToolCall)) == ACTIVITY_PAGE_SIZE
            assert "1 failed" in cluster._head_text()
            assert "File could not be changed" in cluster.tools[-1].copy_text()
            assert "new_788" in cluster.tools[-2].copy_text()
            await pilot.press("r", "e", "p", "r", "i", "s", "e")
            assert app.composer.text == "reprise"
            assert app.transcript.visible
            await app._flush_unsent_work()
    asyncio.run(run())
