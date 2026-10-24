# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Known browser actions run back to back in one call, not one model turn each."""

from __future__ import annotations

import asyncio

from navin.agent.tools.base import ToolResult
from navin.agent.tools.browser import BrowserTool


def _tool(results: dict[str, object]) -> tuple[BrowserTool, list[str]]:
    tool = BrowserTool.__new__(BrowserTool)
    ran: list[str] = []
    original = BrowserTool.execute

    async def fake_execute(self, **kwargs):  # noqa: ANN001 - test double
        if kwargs.get("action") == "sequence":
            return await original(self, **kwargs)
        ran.append(kwargs["action"])
        return results.get(kwargs["action"], "ok")

    tool.execute = fake_execute.__get__(tool)  # type: ignore[method-assign]
    return tool, ran


def test_steps_run_in_order_and_the_last_result_comes_back_whole() -> None:
    tool, ran = _tool({"snapshot": "page: Login\n[1] button Sign in"})
    out = asyncio.run(tool._run_sequence([
        {"action": "navigate", "url": "http://127.0.0.1:5182/"},
        {"action": "type", "selector": "#email", "text": "a@b.c"},
        {"action": "click", "selector": "button[type=submit]"},
        {"action": "snapshot"},
    ]))
    assert ran == ["navigate", "type", "click", "snapshot"]
    assert "1. navigate: ok" in out and "4. snapshot: ok" in out
    assert out.endswith("[1] button Sign in")


def test_a_failing_step_stops_the_sequence() -> None:
    tool, ran = _tool({"click": ToolResult.error("Error: no element matches #missing")})
    out = asyncio.run(tool._run_sequence([
        {"action": "click", "selector": "#missing"},
        {"action": "snapshot"},
    ]))
    assert ran == ["click"]
    assert isinstance(out, ToolResult) and out.is_error
    assert "Stopped at step 1" in out


def test_nested_or_empty_sequences_are_refused() -> None:
    tool, _ = _tool({})
    assert asyncio.run(tool._run_sequence([])).is_error
    assert asyncio.run(tool._run_sequence([{"action": "sequence", "steps": []}])).is_error
