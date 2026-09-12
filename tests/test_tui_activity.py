# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise terminal controls, not just the strings used to build them."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, Static

from navin.tui.runtime import UiFileEdit
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import AssistantMessage, ToolCall, ToolCluster
from navin.utils.file_edit_events import (
    build_file_edit_end_event,
    prepare_file_edit_trackers,
)


class ActivityHost(App):
    def __init__(self, theme="navin"):
        super().__init__()
        self.block = AssistantMessage("Navin")
        for registered in NAVIN_THEMES:
            self.register_theme(registered)
        self.theme = theme

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield self.block


def test_script_header_is_compact_and_full_command_remains_accessible():
    async def run():
        app = ActivityHost()
        command = "PGPASSWORD='demo-password' psql <<'SQL'\nSELECT 1;\nSQL"
        async with app.run_test(size=(80, 24)) as pilot:
            await app.block.tool_event("sql", "exec", "end", {"command": command}, "1 row", None, None)
            row = app.block.query_one(ToolCall)
            assert "\n" not in row._head_text()
            assert "3 lines" in row._head_text()
            assert "demo-password" not in row.copy_text()
            assert "SELECT 1;" in row.copy_text()
            assert row.arguments["command"] == command
            details = row.query_one(".tool-command", Static)
            assert not details.display
            await pilot.pause()
            await pilot.click(row.query_one(".tool-more", Button))
            assert details.display
            assert "SELECT 1;" in str(details.content)
            assert "demo-password" not in app.export_screenshot()
    asyncio.run(run())


def test_binary_delete_does_not_repeat_tool_summary_or_invent_source_lines():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(80, 24)):
            await app.block.tool_event("del", "manage_files", "start", {"action": "delete", "paths": ["cache.pyc"]}, None, None, None)
            await app.block.note_file_edit("cache.pyc", 0, 0, call_id="del", kind="delete", binary=True, tool="manage_files")
            await app.block.tool_event("del", "manage_files", "end", {}, "Deleted 1 file(s), 1 directory tree(s): home", None, None)
            row = app.block.query_one(ToolCall)
            assert "Deleted cache.pyc" in row._head_text()
            assert "directory tree" not in row.copy_text()
            assert "No text preview." in str(row.query_one(".tool-body", Static).content)
    asyncio.run(run())


async def deliver_file(block, payload):
    event = UiFileEdit.from_payload(payload)
    await block.note_file_edit(
        event.path, event.added, event.removed, call_id=event.call_id,
        kind=event.kind, diff=event.diff, phase=event.phase, tool=event.tool,
        error=event.error, truncated=event.truncated, binary=event.binary,
    )


def test_multi_file_and_repeated_events_keep_separate_diffs_and_exact_totals(tmp_path):
    async def run():
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src/a.py").write_text("value = 1\n")
        params = {"edits": [{"path": "src/a.py"}, {"path": "tests/a.py"}]}
        trackers = prepare_file_edit_trackers(
            call_id="patch", tool_name="apply_patch", tool=None,
            workspace=tmp_path, params=params,
        )
        (tmp_path / "src/a.py").write_text("value = 2\n")
        (tmp_path / "tests/a.py").write_text("from src.a import value\nassert value == 2\n")
        events = [build_file_edit_end_event(tracker) for tracker in trackers]
        app = ActivityHost()
        async with app.run_test(size=(100, 32)):
            block = app.block
            await block.tool_event("patch", "apply_patch", "start", params, None, None, None)
            for event in events + events:  # Duplicate delivery after reconnect.
                await deliver_file(block, event)
            await block.tool_event("patch", "apply_patch", "end", params, "Applied", None, None)
            cluster = block.query_one(ToolCluster)
            assert "Edited 2 files (+3 -1)" in cluster._head_text()
            assert len(cluster.tools) == 2
            first, second = cluster.tools
            assert "src/a.py" in first._head_text() and "tests/a.py" in second._head_text()
            assert "-value = 1" in first.copy_text()
            assert "+assert value == 2" in second.copy_text()
            assert "assert value" not in first.copy_text()
            # A later edit remains chronological and does not overwrite the
            # first patch's proof or adopt another directory's namesake.
            await block.tool_event("later", "edit_file", "start", {"path": "src/a.py"}, None, None, None)
            await block.note_file_edit("src/a.py", 1, 1, call_id="later", kind="edit", diff="@@ -1 +1 @@\n-value = 2\n+value = 3\n")
            assert "-value = 1" in first.copy_text() and "+value = 2" in first.copy_text()
            assert "value = 3" not in first.copy_text()
            assert len(cluster.tools) == 3
            assert "2 files (+4 -2)" in cluster._head_text()
    asyncio.run(run())


@pytest.mark.parametrize("theme,width", [("navin", 110), ("navin-light", 46)])
def test_full_output_button_keyboard_and_selection_work(theme, width):
    async def run():
        app = ActivityHost(theme)
        async with app.run_test(size=(width, 28)) as pilot:
            block = app.block
            command = ".venv/bin/python -m pytest tests/test_first.py tests/test_last.py -q"
            output = "\n".join(f"case_{index:03d} passed" for index in range(95))
            await block.tool_event("run", "exec", "end", {"command": command}, output, None, None)
            row = block.query_one(ToolCall)
            await pilot.pause()
            assert command in row._head_text()
            assert row.added == row.removed == 0
            more = row.query_one(".tool-more", Button)
            assert more.display and "+63 lines" in str(more.label)
            assert "case_094" not in str(row.query_one(".tool-body", Static).content)
            more.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click(more)
            assert "case_094" in str(row.query_one(".tool-body", Static).content)
            assert "case_094" in row.copy_text()
            assert command in row.copy_text()
            row.focus()
            await pilot.press("end")
            await pilot.pause()
            viewport = row.query_one(".tool-output", VerticalScroll)
            assert viewport.scroll_y == viewport.max_scroll_y > 0
            assert "case_094" in app.export_screenshot(), "The last line must actually be visible"
            await pilot.press("home")
            await pilot.pause()
            assert viewport.scroll_y == 0
            await pilot.press("pagedown")
            await pilot.pause()
            assert viewport.scroll_y > 0
            await pilot.press("home")
            await pilot.pause()
            # Clicking output to select/copy it must not hide that output.
            body = row.query_one(".tool-body", Static)
            body.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click(body, offset=(2, 1))
            assert row._open
            row.focus()
            await pilot.press("enter")
            assert not body.display
            await pilot.press("enter", "f")
            assert body.display
            assert "case_094" not in str(body.content)
            await pilot.press("f")
            assert "case_094" in str(body.content)
            await block.finish(latency_ms=1, model="test", preset=None)
            assert body.display
    asyncio.run(run())


def test_added_deleted_reverted_failed_and_cancelled_have_distinct_outcomes(tmp_path):
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 35)) as pilot:
            block = app.block
            for call_id, operation, added, removed in [
                ("added", "create", 2, 0), ("deleted", "delete", 0, 2), ("undo", "restore", 1, 1),
            ]:
                await block.note_file_edit(
                    f"{call_id}.py", added, removed, call_id=call_id, kind=operation,
                    diff="--- file\n+++ file\n@@ -1 +1 @@\n-old\n+new\n",
                )
            await block.tool_event("failed", "write_file", "error", {"path": "never.py", "content": "false success"}, None, "Permission denied", None)
            await block.tool_event("cancelled", "exec", "start", {"command": "sleep 60"}, None, None, None)
            await block.finish(latency_ms=20, model="test", preset=None)
            heads = [row._head_text() for row in block.query(ToolCall)]
            assert any("Added added.py (+2 -0)" in head for head in heads)
            assert any("Deleted deleted.py (+0 -2)" in head for head in heads)
            assert any("Reverted undo.py (+1 -1)" in head for head in heads)
            assert any("Failed" in head and "never.py" in head for head in heads)
            assert any("Cancelled" in head and "sleep 60" in head for head in heads)
            failed = next(row for row in block.query(ToolCall) if row.call_id == "failed")
            assert "false success" not in failed.copy_text()
            assert "Permission denied" in failed.copy_text()
            assert failed.added == failed.removed == 0
            foot = str(block.query_one(".assistant-foot", Static).content)
            assert "1 failed" in foot and "1 cancelled" in foot
            assert "ran 1 command" not in foot
            cluster = block.query(ToolCluster).first()
            cluster.focus()
            await pilot.press("enter")
            assert cluster.has_class("-collapsed")
            await pilot.press("enter")
            assert not cluster.has_class("-collapsed")
    asyncio.run(run())


def test_diff_truncation_is_explicit_and_does_not_change_global_counts():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(90, 20)):
            await deliver_file(app.block, {
                "call_id": "large", "tool": "write_file", "phase": "end", "path": "large.py",
                "operation": "create", "added": 900, "deleted": 0,
                "diff": {"text": "@@ -0,0 +1 @@\n+first_line\n", "truncated": True},
            })
            row = app.block.query_one(ToolCall)
            assert "+900 -0" in row._head_text()
            assert "truncated by source" in str(row.query_one(".tool-body", Static).content)
            assert "truncated by source" in row.copy_text()
    asyncio.run(run())


def test_theme_switch_repaints_existing_diff_and_chunked_stdout_stays_intact():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(90, 22)) as pilot:
            await app.block.note_file_edit("theme.py", 1, 0, call_id="file", kind="create", diff="@@ -0,0 +1 @@\n+values = [1]\n")
            row = app.block.query_one(ToolCall)
            before = row.query_one(".tool-body", Static).content
            app.theme = "navin-light"
            await pilot.pause()
            after = row.query_one(".tool-body", Static).content
            assert any("on #20392b" in str(span.style).lower() for span in before.spans)
            assert any("on #e5f0e8" in str(span.style).lower() for span in after.spans)
            await app.block.tool_event("run", "exec", "start", {"command": "pytest -q"}, None, None, None)
            for chunk in ("test_", "prices PASSED\n", "1 passed\n"):
                await app.block.tool_event("run", "exec", "output", {}, None, None, chunk)
            await app.block.tool_event("run", "exec", "end", {}, "test_prices PASSED\n1 passed\n", None, None)
            run = next(tool for tool in app.block.query(ToolCall) if tool.call_id == "run")
            assert run.copy_text().count("test_prices PASSED") == 1
            assert run.copy_text().count("1 passed") == 1
    asyncio.run(run())


def test_unbalanced_brackets_in_source_never_leak_style_tags():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(80, 16)) as pilot:
            await app.block.note_file_edit(
                "brackets.py", 4, 0, call_id="brackets", kind="create",
                diff="@@ -0,0 +1,4 @@\n+values = [\n+    [1, 2],\n+]\n+text = '[bold] literal'\n",
            )
            await pilot.pause()
            body = app.block.query_one(".tool-body", Static)
            text = body.content.plain
            assert "values = [" in text and "[1, 2]" in text
            assert "text = '[bold] literal'" in text
            assert "[/]" not in text and r"\[" not in text
            # The actual visual must have the same literal text as the copy.
            assert "[/]" not in app.export_screenshot()
    asyncio.run(run())


def test_binary_file_does_not_claim_zero_changed_lines(tmp_path):
    async def run():
        trackers = prepare_file_edit_trackers(
            call_id="binary", tool_name="write_file", tool=None, workspace=tmp_path,
            params={"path": "asset.bin"},
        )
        (tmp_path / "asset.bin").write_bytes(b"\x00binary asset")
        app = ActivityHost()
        async with app.run_test(size=(90, 20)):
            await deliver_file(app.block, build_file_edit_end_event(trackers[0]))
            row = app.block.query_one(ToolCall)
            assert "Added asset.bin" in row._head_text()
            assert "+0 -0" not in row._head_text()
            assert "No text preview." in str(row.query_one(".tool-body").content)
            assert "+0 -0" not in app.block.query_one(ToolCluster)._head_text()
    asyncio.run(run())
