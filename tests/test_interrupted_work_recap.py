# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A turn that dies on a provider error still says what it already did."""

from __future__ import annotations

import json

from navin.agent.runner import interrupted_work_recap


def _call(name: str, args: dict) -> dict:
    return {"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_the_recap_lists_edits_and_commands_of_this_run_only() -> None:
    messages = [
        {"role": "user", "content": "old turn"},
        {"role": "assistant", "content": "", "tool_calls": [_call("edit_file", {"path": "old.py"})]},
        {"role": "user", "content": "restyle the sidebar"},
        {"role": "assistant", "content": "", "tool_calls": [
            _call("edit_file", {"path": "src/Sidebar.tsx"}),
            _call("exec", {"command": "npx tsc --noEmit"}),
        ]},
        {"role": "tool", "content": "ok", "_tool_status": "ok"},
        {"role": "tool", "content": "Exit code: 2", "_tool_status": "error"},
        {"role": "assistant", "content": "", "tool_calls": [
            _call("apply_patch", {"edits": [{"path": "src/globals.css"}]}),
        ]},
    ]
    recap = interrupted_work_recap(messages, 2)
    assert "`src/Sidebar.tsx`" in recap and "`src/globals.css`" in recap
    assert "old.py" not in recap
    assert "Commands run: 1, 1 tool call(s) failed" in recap
    assert "continue" in recap


def test_nothing_to_say_when_nothing_was_done() -> None:
    assert interrupted_work_recap([{"role": "user", "content": "hi"}], 0) == ""
