"""Versioned activity events derived from legacy tool events (additive, backward compatible)."""

from typing import Any

import pytest

from navin.utils.activity_events import (
    ACTIVITY_EVENTS_VERSION,
    activity_events_from_tool_events,
    make_event,
)


def test_maps_start_and_end_with_files_and_tests():
    events = activity_events_from_tool_events(
        [
            {"version": 1, "phase": "start", "call_id": "c1", "name": "test_run"},
            {"version": 1, "phase": "end", "call_id": "c1", "name": "test_run", "files": ["a.py", {"path": "b.py"}]},
        ],
        agent="sub-1",
        parent="main",
    )
    assert [e["type"] for e in events] == ["tool.started", "tool.finished", "file.changed", "file.changed", "test.completed"]
    assert all(e["v"] == ACTIVITY_EVENTS_VERSION for e in events)
    assert events[0]["status"] == "running" and events[1]["status"] == "ok"
    assert events[0]["agent"] == "sub-1" and events[0]["parent"] == "main"
    assert events[2]["parent"] == "c1" and events[3]["data"]["path"] == "b.py"
    assert len({e["id"] for e in events}) == len(events)


def test_error_carries_the_message_and_bad_entries_are_skipped():
    bad: list[Any] = [None, "x", {"phase": "weird"}, {"phase": "error", "call_id": "c2", "name": "exec", "error": "boom"}]
    events = activity_events_from_tool_events(bad)
    assert len(events) == 1
    assert events[0]["type"] == "tool.finished" and events[0]["status"] == "error"
    assert events[0]["data"]["error"] == "boom"


def test_empty_input_and_unknown_type():
    assert activity_events_from_tool_events(None) == []
    with pytest.raises(ValueError):
        make_event("tool.exploded", event_id="x")
