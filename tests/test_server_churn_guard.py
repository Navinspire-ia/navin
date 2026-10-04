# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""No production build, server kill or second server unless the user asks.

Measured in a CLI session: a CSS tweak in a Next app ran `npm run build` under
the live `next dev` (rewriting `.next` beneath it), killed the server on :3011,
then started dev, start, and a second server on :3012.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec, is_server_churn_command
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import ToolCallRequest


@pytest.mark.parametrize(("command", "expected"), [
    ("cd frontend && npm run build 2>&1 | tail -6", "build"),
    ("npx next build", "build"),
    ("pnpm build", "build"),
    ("vite build --mode production", "build"),
    ("PID=$(ss -ltnp | grep ':3011' | grep -oP 'pid=\\K[0-9]+'); [ -n \"$PID\" ] && kill \"$PID\"", "kill"),
    ("lsof -ti:3000 | xargs kill -9", "kill"),
    ("fuser -k 3011/tcp", "kill"),
    ("pkill -f 'next dev'", "kill"),
    ("cd frontend && npm run dev -- -p 3011", "serve"),
    ("npm run start -- -p 3012", "serve"),
    ("yarn dev", "serve"),
    ("npx vite", "serve"),
    ("nohup npx next start -p 3011 &", "serve"),
    # Reading and checks stay free.
    ("cat package.json | grep build", ""),
    ("curl -s -o /dev/null -w '%{http_code}' http://localhost:3011/admin", ""),
    ("npx tsc --noEmit", ""),
    ("npm run lint", ""),
    ("npm test", ""),
    ("npm install framer-motion", ""),
    ("next lint", ""),
    ("ls build/", ""),
])
def test_what_counts_as_server_churn(command: str, expected: str) -> None:
    assert is_server_churn_command(command) == expected


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


def test_a_style_tweak_cannot_build_or_restart_the_server() -> None:
    tool, results = asyncio.run(_run(
        "enlève le div sous le titre dans admin-theme.css",
        ["npm run build", "kill 4242", "npm run dev -- -p 3011", "npx tsc --noEmit"],
    ))
    assert tool.commands == ["npx tsc --noEmit"]
    assert "a production build is off" in results[0]
    assert "stopping a process is off" in results[1]
    assert "starting a server is off" in results[2]


@pytest.mark.parametrize("request_text", [
    "fais un build de prod et vérifie qu'il passe",
    "redémarre le serveur, il est bloqué",
    "deploy the frontend",
    "lance npm run dev sur le port 3011",
])
def test_an_explicit_request_unlocks_builds_and_servers(request_text: str) -> None:
    tool, _ = asyncio.run(_run(request_text, ["npm run build", "npm run dev"]))
    assert tool.commands == ["npm run build", "npm run dev"]


def test_a_workflow_turn_reads_the_users_words_not_the_brief() -> None:
    # "/forge fais un build de prod" arrives as "[Build mode] (/forge) ... Focus
    # / target given by the user: ...". Skipping that whole message read the
    # previous one, and the build the user just asked for was refused.
    from navin.agent.runner import _SERVER_REQUEST_MARKERS, _request_asks_for_browser

    def asks(text: str) -> bool:
        return _request_asks_for_browser(
            [{"role": "user", "content": "corrige le titre"}, {"role": "user", "content": text}],
            _SERVER_REQUEST_MARKERS,
        )

    brief = "[Build mode] (/forge)\nDELIVERY GATE: start the app, build, verify.\n"
    assert asks(brief + "Focus / target given by the user: fais un build de prod")
    # The brief's own words never count as the user's request.
    assert not asks(brief + "Focus / target given by the user: change la couleur du bouton")
    assert not asks("[Background command finished] npm run build")
