# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Core schemas every step, the long tail on demand (agent.tool_deferral)."""

from __future__ import annotations

import asyncio

import pytest

from navin.agent import tool_deferral
from navin.agent.tool_deferral import (
    TOOL_SEARCH_NAME,
    mark_loaded,
    schema_name,
    visible_tool_definitions,
)
from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.tool_search import ToolSearchTool


def _schema(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"{name} tool. More details here.",
            "parameters": {"type": "object", "properties": {"action": {"type": "string"}}},
        },
    }


DEFS = [
    _schema("board", "Shared project task board. Plans and steps."),
    _schema("browser", "Control a real headless Chromium browser."),
    _schema("cron", "Schedule reminders and recurring jobs."),
    _schema("exec", "Run a shell command."),
    _schema("read_file", "Read a file."),
    _schema(TOOL_SEARCH_NAME, "Load tools."),
    _schema("write_file", "Write a file."),
]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("NAVIN_DEFER_TOOLS", raising=False)
    tool_deferral.reset_for_tests()
    yield
    tool_deferral.reset_for_tests()


def _names(defs):
    return [schema_name(d) for d in defs]


def test_only_core_schemas_are_sent_until_a_tool_is_loaded():
    visible = visible_tool_definitions(DEFS, "s1")
    assert _names(visible) == ["exec", "read_file", TOOL_SEARCH_NAME, "write_file"]
    listing = visible[2]["function"]["description"]
    for name in ("board", "browser", "cron"):
        assert f"- {name}:" in listing


def test_loaded_tools_append_after_core_and_keep_the_listing_stable():
    first = visible_tool_definitions(DEFS, "s1")
    mark_loaded("s1", ["cron"])
    mark_loaded("s1", ["board"])
    second = visible_tool_definitions(DEFS, "s1")
    assert _names(second) == ["exec", "read_file", TOOL_SEARCH_NAME, "write_file", "cron", "board"]
    # Everything before the appended schemas is byte-identical: cache-safe.
    assert second[: len(first)] == first


def test_loading_is_per_conversation():
    mark_loaded("s1", ["browser"])
    assert "browser" not in _names(visible_tool_definitions(DEFS, "s2"))


def test_denied_tools_are_never_resurrected_by_a_load():
    mark_loaded("s1", ["browser"])
    filtered = [d for d in DEFS if schema_name(d) != "browser"]
    visible = visible_tool_definitions(filtered, "s1")
    assert "browser" not in _names(visible)
    assert "- browser:" not in visible[2]["function"]["description"]


def test_forced_tool_is_always_sent():
    visible = visible_tool_definitions(DEFS, "s1", always=("cron",))
    assert "cron" in _names(visible)


def test_disabled_by_env_sends_everything(monkeypatch):
    monkeypatch.setenv("NAVIN_DEFER_TOOLS", "0")
    assert visible_tool_definitions(DEFS, "s1") == DEFS


def test_without_tool_search_nothing_is_deferred():
    defs = [d for d in DEFS if schema_name(d) != TOOL_SEARCH_NAME]
    assert visible_tool_definitions(defs, "s1") == defs


def _search(query: str, session_key: str = "s1"):
    tool = ToolSearchTool()
    ctx = RequestContext(channel="cli", chat_id="c", session_key=session_key)
    with request_context(ctx):
        return asyncio.run(tool.execute(query=query))


def test_tool_search_select_loads_exact_names():
    visible_tool_definitions(DEFS, "s1")
    result = _search("select:cron,board")
    assert "cron(action)" in str(result) and "board(action)" in str(result)
    assert tool_deferral.loaded_tools("s1") == ["cron", "board"]


def test_tool_search_keywords_rank_matching_tools():
    visible_tool_definitions(DEFS, "s1")
    _search("schedule a reminder")
    assert tool_deferral.loaded_tools("s1") == ["cron"]


def test_tool_search_unknown_name_is_an_error_and_loads_nothing():
    visible_tool_definitions(DEFS, "s1")
    result = _search("select:nope")
    assert "nope" in str(result)
    assert tool_deferral.loaded_tools("s1") == []


def test_message_naming_a_tool_loads_it_for_the_first_step():
    from navin.agent.loop import AgentLoop

    assert AgentLoop._load_tools_named_by_text("edit the jupyter notebook", "s1") is True
    assert "notebook_edit" in tool_deferral.loaded_tools("s1")


def test_no_session_means_no_deferral():
    from navin.agent.loop import AgentLoop

    assert AgentLoop._load_tools_named_by_text("hello", None) is False


def test_runtime_context_already_in_history_is_not_repeated():
    from navin.agent.loop import AgentLoop
    from navin.runtime_context import RuntimeContextBlock

    project_map = RuntimeContextBlock(source="metagraph", content="[Runtime Context]\nmap v1\n[/Runtime Context]")
    git = RuntimeContextBlock(source="git", content="[Runtime Context]\nbranch main, 2 changed\n[/Runtime Context]")
    history = [
        {"role": "user", "content": "fix it\n\n" + project_map.content},
        {"role": "assistant", "content": "done"},
    ]
    kept = AgentLoop._drop_repeated_runtime_context([project_map, git], history)
    assert kept == [git]
    # Compacted away: nothing to point back to, so the block is sent again.
    assert AgentLoop._drop_repeated_runtime_context([project_map], []) == [project_map]
