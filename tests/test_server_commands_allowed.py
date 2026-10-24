# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Builds, process stops and servers run without the user naming them.

The former opt-in gate refused `npm run build` whenever the request did not
contain a word such as "build" or "restart"; the user asked for it to go.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import ToolCallRequest


class FakeExec(Tool):
    name = "exec"
    description = "Pretend shell."
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def execute(self, **kwargs):
        self.commands.append(kwargs["command"])
        return "Exit code: 0"


async def _run(request: str, commands: list[str]) -> tuple[FakeExec, list[str]]:
    tool = FakeExec()
    registry = ToolRegistry()
    registry.register(tool)
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": request}],
        tools=registry, runtime=SimpleNamespace(),
        max_iterations=20, max_tool_result_chars=2000, loop_guard=AgentLoopGuard(),
    )
    results = []
    for n, command in enumerate(commands):
        result, _, _ = await AgentRunner()._dispatch_tool_call(
            spec, ToolCallRequest(id=f"c{n}", name="exec", arguments={"command": command}), {}, {},
        )
        results.append(result)
    return tool, results


def test_builds_and_servers_run_without_being_asked() -> None:
    commands = ["npm run build", "kill 4242", "npm run dev -- -p 3011", "npx tsc --noEmit"]
    tool, results = asyncio.run(_run("enlève le div sous le titre dans admin-theme.css", commands))
    assert tool.commands == commands
    assert not any("is off for this request" in str(result) for result in results)
