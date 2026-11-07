# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Prompt index status is derived from saved, possibly rotated turns."""

from navin.webui import transcript as tr


def test_webui_prompts_full_text_timestamp_and_turn_status(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "get_webui_dir", lambda: tmp_path)
    key = "websocket:prompt-status"

    def append(event, **fields):
        tr.append_transcript_object(key, {"event": event, "chat_id": "prompt-status", **fields})

    full_text = "Long text " * 650
    append("user", text=full_text, created_at_ms=1_700_000_000_000)
    append("message", kind="progress", text="working")
    append("message", text="Final answer")
    append("turn_end")
    append("user", text="Interrupted")
    append("delta", text="partial")
    append("turn_end")
    append("user", text="Still running")
    append("message", kind="progress", text="Tool started")
    # A new user row closes the interrupted turn even without a turn_end.
    append("user", text="Model failed")
    append("message", kind="error", text="Model error")
    append("turn_end")
    append("user", text="Tool failed")
    append("message", kind="progress", text="Tool error", tool_events=[{"phase": "error"}])
    append("turn_end")

    prompts = tr.transcript_user_prompts(key)
    assert [p["status"] for p in prompts] == ["done", "unfinished", "unfinished", "error", "error"]
    assert [p["index"] for p in prompts] == list(range(5))
    assert prompts[0]["text"] == full_text
    assert prompts[0]["timestamp"] == prompts[0]["created_at"] == 1_700_000_000_000
    tr._flush_all_append_buffers()


def test_webui_prompts_tool_failure_then_final_answer_is_done(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "get_webui_dir", lambda: tmp_path)
    key = "websocket:prompt-tool-recovered"

    def append(event, **fields):
        tr.append_transcript_object(key, {"event": event, "chat_id": "prompt-tool-recovered", **fields})

    append("user", text="Tool failed then answered")
    append("message", kind="progress", text="Tool error", tool_events=[{"phase": "error"}])
    append("message", text="Recovered, here is the answer")
    append("turn_end")
    append("user", text="Answered then tool failed")
    append("message", text="Partial answer")
    append("message", kind="progress", text="Tool error", tool_events=[{"phase": "error"}])
    append("turn_end")
    append("user", text="Tool failed then streamed answer")
    append("message", kind="progress", text="Tool error", tool_events=[{"phase": "error"}])
    append("stream_end", text="Streamed final answer")
    append("turn_end")
    append("user", text="Turn error after an answer")
    append("message", text="Answer")
    append("turn_error", text="boom")
    append("turn_end")

    prompts = tr.transcript_user_prompts(key)
    assert [p["status"] for p in prompts] == ["done", "error", "done", "error"]
    tr._flush_all_append_buffers()
