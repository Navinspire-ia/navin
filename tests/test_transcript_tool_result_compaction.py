# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Oversized tool results are cut down before they reach the conversation UI.

A studio tool (career, tenders, CRM...) can return megabytes of JSON per call.
That result rode in the transcript as is, so a 15-message chat weighed 4.6 MB
on every open and froze the desktop. The journal only renders a few text keys
of a result; everything else is dropped past a budget, both on the wire and
when an old transcript is replayed.
"""

from __future__ import annotations

import json

from navin.webui import transcript

BIG = "x" * 200_000


def test_small_results_pass_through_unchanged():
    small = {"content": "ok", "status": "done", "rows": [1, 2, 3]}
    assert transcript.compact_tool_event_result(small) is small
    assert transcript.compact_tool_event_result("hello") == "hello"
    assert transcript.compact_tool_event_result(None) is None
    assert transcript.compact_tool_event_result(42) == 42


def test_long_string_keeps_head_and_tail():
    text = "HEAD" + ("a" * 100_000) + "TAIL"
    out = transcript.compact_tool_event_result(text, limit=4_000)
    assert len(out) < 5_000
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "chars omitted" in out


def test_oversized_dict_keeps_the_keys_the_journal_renders():
    result = {
        "content": "Résumé lisible pour le journal",
        "status": "ok",
        "rows": [{"id": i, "blob": BIG} for i in range(5)],
        "html": BIG,
    }
    out = transcript.compact_tool_event_result(result, limit=16_000)
    assert out["content"] == "Résumé lisible pour le journal"
    assert out["status"] == "ok"
    assert out["truncated"] is True
    assert out["truncated_chars"] > 1_000_000
    assert set(out["dropped_keys"]) == {"rows", "html"}
    assert len(json.dumps(out)) < 2_000


def test_oversized_text_blocks_become_bounded_text():
    blocks = [{"type": "text", "text": BIG}, {"type": "text", "text": "fin"}]
    out = transcript.compact_tool_event_result(blocks, limit=8_000)
    assert isinstance(out, str)
    assert len(out) < 9_000
    assert out.endswith("fin")


def test_oversized_list_of_records_keeps_leading_items_and_a_marker():
    items = [{"id": i, "blob": "y" * 5_000} for i in range(100)]
    out = transcript.compact_tool_event_result(items, limit=20_000)
    assert isinstance(out, list)
    assert out[0] == items[0]
    assert out[-1]["truncated"] is True
    assert out[-1]["omitted_items"] == 100 - (len(out) - 1)
    assert len(json.dumps(out)) < 25_000


def test_compact_tool_events_for_ui_only_copies_what_it_changes():
    small = {"name": "read_file", "phase": "end", "result": {"content": "ok"}}
    big = {"name": "career_search", "phase": "end", "result": {"content": "12 offres", "offers": BIG}}
    start = {"name": "career_search", "phase": "start", "arguments": "{}"}
    out = transcript.compact_tool_events_for_ui([start, small, big, "noise"])
    assert out[0] is start
    assert out[1] is small
    assert out[2] is not big
    assert out[2]["result"]["content"] == "12 offres"
    assert out[2]["result"]["truncated"] is True
    assert big["result"]["offers"] == BIG, "the agent's own event must stay intact"
    assert out[3] == "noise"
    assert transcript.compact_tool_events_for_ui("not a list") == []


def test_replayed_transcript_bounds_old_oversized_results(tmp_path, monkeypatch):
    key = "websocket:heavy-chat"
    path = tmp_path / "t.jsonl"
    monkeypatch.setattr(transcript, "webui_transcript_path", lambda _key: path)
    rows = [
        {"event": "user", "text": "cherche des offres"},
        {
            "event": "message",
            "kind": "tool_hint",
            "text": "",
            "tool_events": [
                {"name": "career_search", "phase": "start", "call_id": "c1", "arguments": "{}"},
                {
                    "name": "career_search",
                    "phase": "end",
                    "call_id": "c1",
                    "result": {"content": "12 offres", "offers": [{"html": BIG} for _ in range(10)]},
                },
            ],
        },
        {"event": "message", "text": "Voici 12 offres."},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert path.stat().st_size > 2_000_000

    messages = transcript.replay_transcript_to_ui_messages(transcript.read_transcript_lines(key))
    wire = json.dumps(messages, ensure_ascii=False)
    assert len(wire) < 50_000
    events = [ev for m in messages for ev in (m.get("toolEvents") or [])]
    ended = [ev for ev in events if ev.get("phase") == "end"]
    assert ended and ended[0]["result"]["content"] == "12 offres"
    assert ended[0]["result"]["truncated"] is True
    assert "offers" in ended[0]["result"]["dropped_keys"]
