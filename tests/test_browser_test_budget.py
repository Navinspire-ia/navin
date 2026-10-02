# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""At most three browser test runs per request.

A CLI session asked to restyle a sidebar and navbar ran Playwright scripts in a
loop, installed Chromium twice and edited another feature's browser test to get
a green run. Three runs show the change or show why it cannot be shown here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from navin.agent.runner import (
    AgentLoopGuard,
    AgentRunner,
    AgentRunSpec,
    is_browser_test_command,
    opens_local_app,
)
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


@pytest.mark.parametrize(("command", "expected"), [
    ("cd frontend && python3 tests/browser/workspace-scope-menu.py 2>&1 | tail -12", True),
    ("PLAYWRIGHT_BROWSERS_PATH=/h/.cache/ms-playwright python3 tests/browser/a.py", True),
    ("python3 -m playwright install chromium --with-deps", True),
    ("npx playwright test", True),
    ("npm run test:e2e", True),
    ("ls ~/.cache/ms-playwright/", False),
    ("grep -rn playwright frontend/tests", False),
    ("cat tests/browser/a.py", False),
    ("npx vitest run", False),
    ("python -m pytest backend/tests", False),
])
def test_what_counts_as_a_browser_run(command: str, expected: bool) -> None:
    assert is_browser_test_command(command) is expected


def test_a_script_counts_when_it_imports_a_browser_driver(tmp_path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "visual-button-check.py").write_text(
        "import asyncio\nfrom playwright.async_api import async_playwright\n"
    )
    (build / "make_icons.py").write_text("print('icons')\n")
    front = tmp_path / "frontend"
    front.mkdir()
    assert is_browser_test_command("python3 build/visual-button-check.py 2>&1 | tail -20", tmp_path)
    assert is_browser_test_command(
        f"cd {front} && python3 ../build/visual-button-check.py 2>&1 | tail", tmp_path,
    )
    assert is_browser_test_command(
        "PLAYWRIGHT_BROWSERS_PATH=/h/.cache/ms-playwright python3 build/make_icons.py", tmp_path,
    )
    assert not is_browser_test_command("python3 build/make_icons.py", tmp_path)


@pytest.mark.parametrize("shared_guard", [True, False])
def test_the_fourth_browser_run_is_refused_but_other_commands_still_work(shared_guard: bool) -> None:
    async def check() -> None:
        tool = FakeExec()
        registry = ToolRegistry()
        registry.register(tool)
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "check the navbar with playwright"}],
            tools=registry, runtime=SimpleNamespace(),
            max_iterations=20, max_tool_result_chars=2000,
            loop_guard=AgentLoopGuard() if shared_guard else None,
        )
        runner = AgentRunner()

        async def run(command: str, n: int):
            return await runner._dispatch_tool_call(
                spec, ToolCallRequest(id=f"c{n}", name="exec", arguments={"command": command}), {}, {},
            )

        for n in range(3):
            _, event, _ = await run(f"python3 tests/browser/run{n}.py", n)
            assert event["status"] == "ok"
        result, event, fatal = await run("python3 tests/browser/run3.py", 3)
        assert event["status"] == "error" and fatal is None
        assert "browser test budget" in result
        _, event, _ = await run("npx tsc --noEmit", 4)
        assert event["status"] == "ok"
        assert tool.commands == [f"python3 tests/browser/run{n}.py" for n in range(3)] + ["npx tsc --noEmit"]

    asyncio.run(check())


def test_no_browser_run_unless_the_request_asks_for_one() -> None:
    async def check() -> None:
        tool = FakeExec()
        registry = ToolRegistry()
        registry.register(tool)
        spec = AgentRunSpec(
            initial_messages=[
                {"role": "user", "content": "change le style de la sidebar et de la navbar"},
                {"role": "user", "content": "[Background command finished] playwright ok"},
            ],
            tools=registry, runtime=SimpleNamespace(),
            max_iterations=20, max_tool_result_chars=2000, loop_guard=AgentLoopGuard(),
        )
        result, event, _ = await AgentRunner()._dispatch_tool_call(
            spec, ToolCallRequest(id="c", name="exec", arguments={"command": "python3 tests/browser/a.py"}),
            {}, {},
        )
        assert event["status"] == "error" and "browser tests are off" in result
        assert tool.commands == []

    asyncio.run(check())


def test_the_browser_tool_on_the_local_app_is_a_browser_test() -> None:
    assert opens_local_app({"action": "navigate", "url": "http://127.0.0.1:3010/admin"})
    assert opens_local_app({"action": "navigate", "url": "localhost:5173"})
    assert not opens_local_app({"action": "navigate", "url": "https://docs.python.org/3/"})
    assert not opens_local_app({"action": "snapshot"})


def _browser_spec(request: str) -> AgentRunSpec:
    class FakeBrowser(Tool):
        name = "browser"
        description = "Pretend browser."
        parameters = {"type": "object", "properties": {"action": {"type": "string"}, "url": {"type": "string"}}}

        async def execute(self, **kwargs):
            return "page"

    class FakeScrape(FakeBrowser):
        name = "scrape"

    registry = ToolRegistry()
    registry.register(FakeBrowser())
    registry.register(FakeScrape())
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": request}],
        tools=registry, runtime=SimpleNamespace(),
        max_iterations=20, max_tool_result_chars=2000, loop_guard=AgentLoopGuard(),
    )


async def _call(spec: AgentRunSpec, name: str, args: dict, n: int = 0):
    return await AgentRunner()._dispatch_tool_call(spec, ToolCallRequest(id=f"b{n}", name=name, arguments=args), {}, {})


@pytest.mark.parametrize(("request_text", "allowed"), [
    ("supprime la phrase sous le titre de la home", False),
    ("quelle est la dernière version de Next.js ?", False),
    ("scrape les offres de ce site en CSV", True),
    ("récupère les appels d'offres du portail", True),
    ("ouvre le navigateur et fais une capture d'écran", True),
])
def test_the_browser_is_off_unless_asked_or_scraping(request_text: str, allowed: bool) -> None:
    async def check() -> None:
        spec = _browser_spec(request_text)
        result, event, _ = await _call(spec, "browser", {"action": "navigate", "url": "https://nextjs.org/blog"})
        assert (event["status"] == "ok") is allowed
        if not allowed:
            assert "browser is off" in result

    asyncio.run(check())


def test_a_scrape_call_unlocks_the_browser_escalation() -> None:
    async def check() -> None:
        spec = _browser_spec("trouve les horaires de la mairie")
        _, event, _ = await _call(spec, "browser", {"action": "snapshot"}, 0)
        assert event["status"] == "error"
        _, event, _ = await _call(spec, "scrape", {"action": "fetch", "url": "https://example.org"}, 1)
        assert event["status"] == "ok"
        _, event, _ = await _call(spec, "browser", {"action": "navigate", "url": "https://example.org"}, 2)
        assert event["status"] == "ok"

    asyncio.run(check())


def test_continue_after_a_handoff_keeps_the_open_page() -> None:
    from unittest.mock import patch

    from navin.agent.tools import browser

    async def check() -> None:
        spec = _browser_spec("continue")
        spec.session_key = "websocket:live"
        session = browser._BrowserSession(browser.BrowserToolConfig())
        session.page = SimpleNamespace()
        with patch.dict(browser._SESSIONS, {"websocket:live": session}, clear=True):
            _, event, _ = await _call(spec, "browser", {"action": "snapshot"})
        assert event["status"] == "ok"

    asyncio.run(check())
