# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An imported chat shows its tool steps and never the loop's own nudges."""

from __future__ import annotations

import json

from navin.webui import transcript

KEY = "websocket:imported-1"
MESSAGES = [
    {"role": "user", "content": "/forge build an invoice app"},
    {"role": "user", "content": "Scope reminder: the request names these targets: invoice. Go."},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "a.py"}'}},
    ]},
    {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    {"role": "assistant", "content": "Done."},
]


def _rows(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "webui_transcript_path", lambda key: tmp_path / "t.jsonl")
    return tmp_path / "t.jsonl"


def test_rebuild_keeps_tools_and_hides_nudges(tmp_path, monkeypatch):
    path = _rows(tmp_path, monkeypatch)
    transcript.write_session_messages_as_transcript(KEY, MESSAGES)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    texts = [r.get("text") for r in rows]
    assert not any("Scope reminder" in (t or "") for t in texts)
    assert any(r.get("tool_events", [{}])[0].get("name") == "write_file" for r in rows if r.get("tool_events"))
    assert not transcript.drop_stale_rebuilt_transcript(KEY, MESSAGES)


def test_an_old_text_only_rebuild_is_dropped(tmp_path, monkeypatch):
    path = _rows(tmp_path, monkeypatch)
    path.write_text('{"event":"user","text":"/forge"}\n{"event":"message","text":"Done."}\n')
    monkeypatch.setattr(transcript, "delete_webui_transcript", lambda key: path.unlink() or True)
    assert transcript.drop_stale_rebuilt_transcript(KEY, MESSAGES)
    assert not path.exists()
