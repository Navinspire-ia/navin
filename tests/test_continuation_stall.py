# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Continuations remember what failed, and stop spinning when nothing moves.

Seen live: a Vite app on Navin's own port 5173 could not start; every
budget slice re-listed the board, re-read the same files and retried the same
start, 22 times, until the user noticed.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from navin.session import turn_continuation as tc


def _call(call_id: str, name: str, **args) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _slice(*pairs: tuple[dict, str]) -> list[dict]:
    messages: list[dict] = [{"role": "user", "content": "go"}]
    for call, result in pairs:
        messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["function"]["name"], "content": result})
    return messages


def test_reads_of_known_files_and_failures_are_not_progress() -> None:
    seen: set[str] = set()
    first = _slice(
        (_call("1", "read_file", path="app/src/App.tsx"), "export default App"),
        (_call("2", "start_app"), "Error: port 5173 is Navin's editor, not the project."),
    )
    assert tc.slice_report(first, 1, seen).progressed  # first read of the file
    again = tc.slice_report(first, 1, seen)
    assert not again.progressed
    assert again.failures == ["start_app: Error: port 5173 is Navin's editor, not the project."]


def test_edits_successful_commands_and_board_moves_are_progress() -> None:
    assert tc.slice_report(_slice((_call("1", "edit_file", path="a.ts"), "Edited a.ts")), 1, set()).progressed
    assert tc.slice_report(_slice((_call("1", "exec", command="npm install"), "Exit code: 0\nadded 3")), 1, set()).progressed
    assert tc.slice_report(_slice((_call("1", "board", action="update", status="done"), "ok")), 1, set()).progressed
    assert not tc.slice_report(_slice((_call("1", "exec", command="git status && ls"), "clean")), 1, set()).progressed
    assert not tc.slice_report(_slice((_call("1", "board", action="list"), "Tasks (6)")), 1, set()).progressed
    failed = tc.slice_report(_slice((_call("1", "exec", command="npm run dev"), "Exit code: 1\nvite: not found")), 1, set())
    assert not failed.progressed
    assert failed.failures == ["exec(npm run dev): Exit code: 1"]


def _ctx(messages: list[dict], metadata: dict) -> SimpleNamespace:
    queue: asyncio.Queue = asyncio.Queue()
    return SimpleNamespace(
        session=SimpleNamespace(metadata=metadata),
        pending_queue=queue,
        stop_reason="max_iterations",
        msg=SimpleNamespace(metadata={}),
        session_key="websocket:c1",
        final_content="",
        all_messages=messages,
        initial_messages=messages[:1],
        visible_run_started_at=None,
        request_context=None,
        suppress_response=False,
    )


def test_two_stalled_slices_end_with_a_report_then_stop(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_continuation_kind", lambda **_kw: tc._TURN_CONTINUATION_KIND)
    monkeypatch.setattr(tc, "_park_board_after_hard_stop", lambda _ctx: None)
    monkeypatch.setattr(tc.dataclasses, "replace", lambda msg, **kw: SimpleNamespace(**kw))
    stuck = _slice(*[(_call(str(n), "start_app"), "Error: port 5173 is Navin's editor, not the project.") for n in range(6)])
    metadata: dict = {}

    # Slice 1 changed nothing: resume, naming the failure so it is not retried as is.
    ctx = _ctx(stuck, metadata)
    assert asyncio.run(tc.maybe_continue_turn(ctx))
    first = ctx.pending_queue.get_nowait().content
    assert "FAILED" in first and "port 5173" in first
    # Slice 2 changed nothing either: one last slice to tell the user what blocks.
    ctx = _ctx(stuck, metadata)
    assert asyncio.run(tc.maybe_continue_turn(ctx))
    report = ctx.pending_queue.get_nowait().content
    assert report.startswith("Stop retrying") and "port 5173" in report
    # After the report: no more slices.
    ctx = _ctx(stuck, metadata)
    assert not asyncio.run(tc.maybe_continue_turn(ctx))
    assert ctx.pending_queue.empty()


def test_progress_resets_the_count(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_continuation_kind", lambda **_kw: tc._TURN_CONTINUATION_KIND)
    monkeypatch.setattr(tc.dataclasses, "replace", lambda msg, **kw: SimpleNamespace(**kw))
    metadata: dict = {}
    stuck = _slice((_call("1", "exec", command="npm run dev"), "Exit code: 1"))
    working = _slice((_call("2", "edit_file", path="vite.config.ts"), "Edited"))
    for messages in (stuck, working, stuck, working, stuck):
        ctx = _ctx(messages, metadata)
        assert asyncio.run(tc.maybe_continue_turn(ctx))
        assert not ctx.pending_queue.get_nowait().content.startswith("Stop retrying")


def test_new_reads_around_the_same_failure_are_circling(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_continuation_kind", lambda **_kw: tc._TURN_CONTINUATION_KIND)
    monkeypatch.setattr(tc, "_park_board_after_hard_stop", lambda _ctx: None)
    monkeypatch.setattr(tc.dataclasses, "replace", lambda msg, **kw: SimpleNamespace(**kw))
    metadata: dict = {}
    failure = "Error: port 5173 is Navin's editor, not the project."
    contents = []
    for n in range(3):
        # Each slice reads a file it never read, then hits the same wall.
        messages = _slice(
            (_call(f"r{n}", "read_file", path=f"src/file{n}.ts"), "code"),
            *[(_call(f"s{n}{k}", "start_app"), failure) for k in range(6)],
        )
        ctx = _ctx(messages, metadata)
        if not asyncio.run(tc.maybe_continue_turn(ctx)):
            contents.append(None)
            break
        contents.append(ctx.pending_queue.get_nowait().content)
    assert contents[-1] is None or contents[-1].startswith("Stop retrying")
    assert any(c and c.startswith("Stop retrying") for c in contents)


def test_failing_command_with_a_long_log_is_a_failure() -> None:
    log = "vite v6 building...\n" + ("x" * 5000) + "\nerror during build\nExit code: 1"
    report = tc.slice_report(_slice((_call("1", "exec", command="npm run build"), log)), 1, set())
    assert not report.progressed and not report.changed
    assert report.failures == ["exec(npm run build): Exit code: 1"]


def test_moves_that_change_nothing() -> None:
    for call, result in [
        (_call("1", "exec", command="cd web && ls"), "a b"),
        (_call("1", "exec", command="curl -s localhost:5173"), "<html>"),
        (_call("1", "board", action="next"), "t-1"),
        (_call("1", "board", action="log", note="x"), "ok"),
        (_call("1", "board", action="update", status="done"), "Validation pending: task remains open."),
        (_call("1", "update_goal", action="complete"), "Validation pending: task remains open."),
    ]:
        report = tc.slice_report(_slice((call, result)), 1, set())
        assert not report.changed, call


def test_other_offsets_of_the_same_file_are_not_new() -> None:
    seen: set[str] = set()
    assert tc.slice_report(_slice((_call("1", "read_file", path="a.ts", offset=1), "x")), 1, seen).progressed
    assert not tc.slice_report(_slice((_call("2", "read_file", path="a.ts", offset=200, limit=50), "y")), 1, seen).progressed


def test_stuck_report_slice_has_no_tools_and_ends_the_run(monkeypatch) -> None:
    from navin.command.modules import ALLOWED_TOOLS_METADATA_KEY

    monkeypatch.setattr(tc, "_park_board_after_hard_stop", lambda _ctx: None)
    monkeypatch.setattr(tc.dataclasses, "replace", lambda msg, **kw: SimpleNamespace(**kw))
    real_kind = tc._continuation_kind
    monkeypatch.setattr(
        tc, "_continuation_kind",
        lambda **kw: None if (kw.get("session_metadata") or {}).get(tc._STALL_REPORT_SENT_KEY) else tc._TURN_CONTINUATION_KIND,
    )
    stuck = _slice(*[(_call(str(n), "start_app"), "Error: port taken") for n in range(6)])
    metadata: dict = {}
    for _ in range(2):
        ctx = _ctx(stuck, metadata)
        assert asyncio.run(tc.maybe_continue_turn(ctx))
        queued = ctx.pending_queue.get_nowait()
    assert queued.content.startswith("Stop retrying")
    assert queued.metadata[ALLOWED_TOOLS_METADATA_KEY] == []
    # The real selector refuses any further slice once the report is out.
    assert real_kind(
        stop_reason="max_iterations", pending_queue_available=True, session_metadata=metadata,
    ) is None


def test_short_slices_are_not_cut_after_a_few_retries(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_continuation_kind", lambda **_kw: tc._TURN_CONTINUATION_KIND)
    monkeypatch.setattr(tc.dataclasses, "replace", lambda msg, **kw: SimpleNamespace(**kw))
    metadata: dict = {}
    refused = _slice(*[(_call(str(n), "board", action="move", status="done"), "Validation pending: task remains open.") for n in range(2)])
    for _ in range(4):  # 8 refused closes: still below the wasted-call floor
        ctx = _ctx(refused, metadata)
        assert asyncio.run(tc.maybe_continue_turn(ctx))
        assert not ctx.pending_queue.get_nowait().content.startswith("Stop retrying")


def test_board_prompt_only_while_a_touched_task_is_open(tmp_path, monkeypatch) -> None:
    from navin.board import session_focus
    from navin.board.store import ProjectBoardStore

    store = ProjectBoardStore(tmp_path)
    task = store.create_task(title="Build it", status="in_progress", actor="agent", actor_type="agent")
    monkeypatch.setattr(session_focus, "touched", lambda _key: [task["id"]])
    meta = {"workspace_scope": {"project_path": str(tmp_path)}}
    assert tc._board_continuation_available(session_key="s", message_metadata=meta)
    store.update_task(task["id"], actor="user", actor_type="human", fields={"status": "done"})
    assert not tc._board_continuation_available(session_key="s", message_metadata=meta)


def test_unbounded_runs_stop_when_two_slices_change_nothing() -> None:
    from navin.agent.runner import AgentRunner

    messages: list[dict] = [{"role": "user", "content": "go"}]
    spec = SimpleNamespace(max_iterations=6)
    steps = []
    for iteration in AgentRunner._watched_iterations(spec, lambda: messages):
        steps.append(iteration)
        call = _call(f"c{iteration}", "start_app")
        messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        messages.append({"role": "tool", "tool_call_id": call["id"], "name": "start_app",
                         "content": "Error: port 5173 is taken"})
        assert iteration < 100
    # Two stalled slices of 6 steps (12 wasted calls), then it stops.
    assert steps == list(range(12))


def test_unbounded_runs_keep_going_while_they_change_things() -> None:
    from navin.agent.runner import AgentRunner

    messages: list[dict] = [{"role": "user", "content": "go"}]
    spec = SimpleNamespace(max_iterations=3)
    seen = 0
    for iteration in AgentRunner._watched_iterations(spec, lambda: messages):
        call = _call(f"e{iteration}", "edit_file", path=f"f{iteration}.py")
        messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        messages.append({"role": "tool", "tool_call_id": call["id"], "name": "edit_file", "content": "Edited"})
        seen = iteration
        if iteration >= 30:
            break
    assert seen >= 30


def test_rerunning_the_same_command_with_the_same_result_is_not_progress() -> None:
    passing = _slice((_call("1", "exec", command="pytest -q"), "3 passed\nExit code: 0"))
    state: dict = {}
    first, _ = tc.record_slice(state, passing, 1)
    assert first.progressed
    again, _ = tc.record_slice(state, passing, 1)
    assert not again.progressed
    changed = _slice((_call("1", "exec", command="pytest -q"), "4 passed\nExit code: 0"))
    assert tc.record_slice(state, changed, 1)[0].progressed


def test_seen_reads_trim_keeps_the_newest_entries() -> None:
    state: dict = {tc._SEEN_READS_KEY: [f"{i:012x}" for i in range(tc._SEEN_READS_MAX)]}
    fresh = _slice((_call("1", "read_file", path="new.py"), "x = 1"))
    tc.record_slice(state, fresh, 1)
    kept = state[tc._SEEN_READS_KEY]
    assert len(kept) == tc._SEEN_READS_MAX
    assert kept[0] == f"{1:012x}"  # oldest dropped, newest read kept


def test_file_contents_quoting_an_exit_code_are_not_a_failed_read() -> None:
    """A test file that contains the text "Exit code: 1" is content, not a command result.

    Seen live: reading tests that assert on exit codes was counted as a
    repeated failure, so an investigation slice looked stalled.
    """
    quoted = _slice(
        (_call("1", "read_file", path="tests/test_build.py"), 'assert out == "Exit code: 1\\nvite: not found"'),
        (_call("2", "grep", pattern="Exit code"), "tests/test_build.py:50: Exit code: 1"),
    )
    report = tc.slice_report(quoted, 1, set())
    assert report.progressed
    assert report.failures == []
    # A command that really exited non-zero is still a failure.
    failed = tc.slice_report(_slice((_call("3", "exec", command="npm test"), "Exit code: 1")), 1, set())
    assert failed.failures == ["exec(npm test): Exit code: 1"]
    # And a read that really failed (missing file) still counts as one.
    missing = tc.slice_report(_slice((_call("4", "read_file", path="nope.py"), "Error: file not found")), 1, set())
    assert missing.failures == ["read_file(nope.py): Error: file not found"]
