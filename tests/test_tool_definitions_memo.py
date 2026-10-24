"""The filtered tool list is reused while nothing changes, so its token count is cached."""

from __future__ import annotations

from navin.agent.tool_surface import filter_tool_definitions
from navin.utils import helpers


def _schema(name: str, description: str = "x" * 2000) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object"}}}


def test_same_inputs_return_the_same_list_and_compact_copies():
    defs = [_schema("read_file"), _schema("exec"), _schema("web_search")]
    first = filter_tool_definitions(defs, frozenset({"web_search"}), compact=True)
    again = filter_tool_definitions(defs, {"web_search"}, compact=True)
    assert again is first
    assert [s["function"]["name"] for s in first] == ["read_file", "exec"]
    assert len(first[0]["function"]["description"]) < 2000
    assert defs[0]["function"]["description"] == "x" * 2000  # the registry schema is untouched


def test_a_change_in_filters_or_registry_gives_a_fresh_list():
    defs = [_schema("read_file"), _schema("exec")]
    first = filter_tool_definitions(defs, frozenset(), allowed=frozenset({"read_file"}), compact=True)
    widened = filter_tool_definitions(defs, frozenset(), allowed=frozenset({"read_file", "exec"}), compact=True)
    assert [s["function"]["name"] for s in first] == ["read_file"]
    assert [s["function"]["name"] for s in widened] == ["read_file", "exec"]
    registry_changed = [*defs, _schema("grep")]
    assert len(filter_tool_definitions(registry_changed, frozenset(), allowed=None, compact=True)) == 3
    defs.append(_schema("grep"))  # same list object mutated in place
    assert len(filter_tool_definitions(defs, frozenset(), allowed=frozenset({"read_file", "exec", "grep"}), compact=True)) == 3


def test_tool_tokens_are_counted_once_for_a_reused_list(monkeypatch):
    defs = [_schema("read_file"), _schema("exec")]
    tools = filter_tool_definitions(defs, frozenset(), compact=True)
    enc = helpers._get_token_encoding()
    calls = []

    class Counting:
        def encode(self, text, *args, **kwargs):
            calls.append(len(text))
            return enc.encode(text, *args, **kwargs)

    helpers._TOOLS_TOKEN_CACHE.clear()
    counting = Counting()
    first = helpers._estimate_tools_tokens(counting, tools, leading_separator=True)
    second = helpers._estimate_tools_tokens(counting, filter_tool_definitions(defs, frozenset(), compact=True), leading_separator=True)
    assert first == second and len(calls) == 1
