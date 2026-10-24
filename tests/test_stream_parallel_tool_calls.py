# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Parallel tool calls streamed under one index stay separate calls."""

from __future__ import annotations

from navin.providers.openai_compat_provider import OpenAICompatProvider


def _chunk(tool_call: dict) -> dict:
    return {"choices": [{"delta": {"tool_calls": [tool_call]}, "finish_reason": None}]}


def test_gemini_parallel_calls_under_index_zero_are_not_merged():
    chunks = [
        _chunk({"index": 0, "id": "a", "function": {"name": "board", "arguments": '{"task_id":"t-1","action":"get"}'}}),
        _chunk({"index": 0, "id": "b", "function": {"name": "list_dir", "arguments": '{"path":"build/i18n"}'}}),
    ]
    response = OpenAICompatProvider._parse_chunks(chunks)
    assert [(c.name, c.arguments) for c in response.tool_calls] == [
        ("board", {"task_id": "t-1", "action": "get"}),
        ("list_dir", {"path": "build/i18n"}),
    ]


def test_calls_without_ids_split_on_a_new_name_after_complete_arguments():
    chunks = [
        _chunk({"function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}),
        _chunk({"function": {"name": "list_dir", "arguments": '{"path":"src"}'}}),
    ]
    response = OpenAICompatProvider._parse_chunks(chunks)
    assert [c.name for c in response.tool_calls] == ["read_file", "list_dir"]


def test_one_call_streamed_in_pieces_stays_one_call():
    chunks = [
        _chunk({"index": 0, "id": "a", "function": {"name": "read_file", "arguments": '{"pa'}}),
        _chunk({"index": 0, "function": {"arguments": 'th":"a.py"}'}}),
        _chunk({"index": 1, "id": "b", "function": {"name": "list_dir", "arguments": ""}}),
        _chunk({"index": 1, "function": {"arguments": '{"path":"src"}'}}),
    ]
    response = OpenAICompatProvider._parse_chunks(chunks)
    assert [(c.name, c.arguments) for c in response.tool_calls] == [
        ("read_file", {"path": "a.py"}),
        ("list_dir", {"path": "src"}),
    ]
