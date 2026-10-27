# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One CLI, several projects working at once, on real engines.

The real NavinApp runs a real TuiRuntime and real AgentLoops behind a scripted
provider, on two project folders. A turn of the first project keeps running
while the second one is open, its answer is saved in its own project, and
coming back shows it as it is: the same engine, its draft, what the turn did
meanwhile, then the rest live.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

from navin.agent.hook import AgentHook
from navin.config.loader import get_config_path, set_config_path
from navin.providers.base import LLMProvider, LLMResponse
from navin.providers.factory import ProviderSnapshot
from navin.session.manager import SessionManager
from navin.tui import projects
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.widgets import AssistantMessage, ToolCall, UserMessage
from tests.test_tui_send_now_integration import _drain_runtime, make_config, tool_call


class QuietHook(AgentHook):
    """Stand-in for the usage and cron hooks: no disk, no network."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()


class ProjectProvider(LLMProvider):
    """Answers each job from its own script; a step may wait on a gate."""

    def __init__(self, scripts: dict[str, list[tuple[asyncio.Event | None, LLMResponse]]]) -> None:
        super().__init__()
        self.scripts = {job: deque(steps) for job, steps in scripts.items()}
        self.started: list[str] = []

    def get_default_model(self):
        return "test-multi-project"

    def _job(self, messages) -> str:
        for message in reversed(messages or []):
            if message.get("role") == "user":
                text = str(message.get("content"))
                job = next((name for name in self.scripts if name in text), None)
                if job:
                    return job
        return ""

    async def chat(self, **kwargs):
        job = self._job(kwargs.get("messages"))
        steps = self.scripts.get(job)
        if not steps:
            return LLMResponse(content="ok")
        self.started.append(job)
        gate, response = steps.popleft()
        if gate is not None:
            await gate.wait()
        return response

    async def chat_with_retry(self, **kwargs):
        return await self.chat(**kwargs)


async def wait_for(check, what: str, timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not check():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


class LoopWatch:
    """Longest time the event loop went without running a 10 ms ticker.

    The engine of the new project is built on a worker thread: a switch that
    did that work on the loop would freeze the screen and every other project.
    """

    def __init__(self) -> None:
        self.longest = 0.0
        self._task: asyncio.Task | None = None

    async def _tick(self) -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            await asyncio.sleep(0.01)
            now = loop.time()
            self.longest = max(self.longest, now - last)
            last = now

    def __enter__(self) -> LoopWatch:
        self._task = asyncio.get_running_loop().create_task(self._tick())
        return self

    def __exit__(self, *exc) -> None:
        if self._task is not None:
            self._task.cancel()


def answers(app) -> list[str]:
    return [block.copy_text() for block in app.query(AssistantMessage)]


def run_two_projects(tmp_path, scripts, body) -> None:
    async def run():
        alpha, beta = tmp_path / "alpha", tmp_path / "beta"
        alpha.mkdir()
        beta.mkdir()
        (alpha / "a.json").write_text("{}\n")
        provider = ProjectProvider(scripts)
        old_config_path, cwd = get_config_path(), os.getcwd()
        set_config_path(tmp_path / "config.json")
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = NavinApp(make_config(alpha), prefs=prefs)
        state: dict = {}

        def snapshot(*args, **kwargs):
            return ProviderSnapshot(provider, "test-multi-project", 128_000, ("test-multi-project",))

        with (
            patch("navin.providers.factory.build_provider_snapshot_allowing_unconfigured", snapshot),
            patch("navin.providers.factory.load_provider_snapshot_allowing_unconfigured", snapshot),
            patch("navin.agent.skills._home_skill_dirs", return_value=[]),
            patch("navin.agent.loop.AgentLoop._connect_mcp", new_callable=AsyncMock),
            patch("navin.optional_live.live_modules_available", return_value=False),
            patch("navin.tui.app.live_modules_available", return_value=False),
            patch("navin.cron.service.CronService"),
            patch("navin.cron.spend.CronSpendHook", QuietHook),
            patch("navin.webui.token_usage.TokenUsageHook", QuietHook),
            # The real update server would add an "update available" row mid-test.
            patch("navin.update.notice.update_notice", return_value=""),
            # The desktop's project list lives in the user's home: kept in memory here.
            patch.object(projects, "read_state", lambda: json.loads(json.dumps(state))),
            patch.object(projects, "_write_state", state.update),
        ):
            try:
                async with app.run_test(size=(140, 40)) as pilot:
                    await wait_for(lambda: app._engine_ready, "the engine")
                    await body(app, pilot, provider, alpha, beta)
            finally:
                await _drain_runtime(app)
                os.chdir(cwd)
                set_config_path(old_config_path)

    asyncio.run(run())


def test_a_turn_finishes_in_its_own_project_while_another_one_is_used(tmp_path):
    gate = asyncio.Event()

    async def body(app, pilot, provider, alpha, beta):
        flashes: list[str] = []
        flash = app._flash

        async def recording_flash(text, *args, **kwargs):
            flashes.append(str(text))
            return await flash(text, *args, **kwargs)

        app._flash = recording_flash
        alpha_key, alpha_loop = app.runtime.session_key, app.runtime.agent_loop
        await app.submit_text("job-alpha: refactor the parser")
        await wait_for(lambda: "job-alpha" in provider.started, "the alpha turn")
        app.composer.set_text("draft kept in alpha")
        await pilot.pause()

        # Another project opens: no question, nothing stops, the screen never freezes.
        with LoopWatch() as watch:
            await app._open_project(str(beta), "cli:beta")
        assert watch.longest < 1.0, f"the loop froze {watch.longest:.2f}s while opening a new project"
        assert Path(app.runtime.workspace) == beta.resolve()
        assert app.runtime.agent_loop is not alpha_loop
        assert [Path(p) for p in app.runtime.background_projects()] == [alpha.resolve()]
        busy = {Path(path): keys for path, keys in app.runtime.running_sessions().items()}
        assert alpha_key in busy[alpha.resolve()]
        assert app.composer.text == ""  # beta has its own draft
        assert any("keeps working in the background" in text for text in flashes)

        # Beta works normally meanwhile.
        await app.submit_text("job-beta: quick question")
        await wait_for(lambda: any("beta answer" in text for text in answers(app)), "the beta answer")

        # Alpha finishes in the background, saved in alpha only.
        gate.set()
        await wait_for(lambda: alpha.resolve() not in {Path(p) for p in app.runtime.running_sessions()}, "alpha to finish")

        def alpha_saved() -> bool:
            saved = SessionManager(alpha).get_or_create(alpha_key)
            return any("alpha is done" in str(m.get("content")) for m in saved.messages)

        await wait_for(alpha_saved, "the alpha answer on disk")
        await wait_for(lambda: any("finished" in text for text in flashes), "the finished note")
        assert not any("alpha is done" in text for text in answers(app))
        beta_saved = SessionManager(beta).get_or_create("cli:beta").messages
        assert not any("alpha" in str(m.get("content")) and m.get("role") == "assistant" for m in beta_saved)

        # Back on alpha: the same engine, its draft, its answer.
        with LoopWatch() as watch:
            await app._open_project(str(alpha), alpha_key)
        assert watch.longest < 1.0, f"the loop froze {watch.longest:.2f}s while coming back to a project"
        assert app.runtime.agent_loop is alpha_loop
        assert app.runtime.background_projects() == []  # beta was idle: closed
        assert app.composer.text == "draft kept in alpha"
        await wait_for(lambda: any("alpha is done" in text for text in answers(app)), "the alpha answer on screen")
        assert not app.runtime.turn_active

    run_two_projects(tmp_path, {
        "job-alpha": [(gate, LLMResponse(content="alpha is done"))],
        "job-beta": [(None, LLMResponse(content="beta answer"))],
    }, body)


def test_coming_back_mid_turn_shows_what_the_turn_did_then_goes_on_live(tmp_path):
    first, second = asyncio.Event(), asyncio.Event()

    async def body(app, pilot, provider, alpha, beta):
        alpha_key = app.runtime.session_key
        await app.submit_text("job-alpha: read the config")
        await wait_for(lambda: provider.started.count("job-alpha") == 1, "the alpha turn")
        await pilot.pause()
        shown_before = [row.tool_name for row in app.query(ToolCall)]
        await app._open_project(str(beta), "cli:beta")
        assert not list(app.query(ToolCall))  # beta's own screen

        # The tool runs while beta is on screen; the next model call waits.
        first.set()
        await wait_for(lambda: provider.started.count("job-alpha") == 2, "the alpha tool step")

        await app._open_project(str(alpha), alpha_key)
        await pilot.pause()
        assert app.runtime.turn_active  # still working, shown as such
        # what the turn showed before leaving, then what it did meanwhile, once each
        assert [row.tool_name for row in app.query(ToolCall)] == [*shown_before, "read_file"]
        assert [row.raw_text for row in app.query(UserMessage)] == ["job-alpha: read the config"]

        # The rest arrives live, once.
        second.set()
        await wait_for(lambda: not app.runtime.turn_active, "the alpha turn to end")
        await wait_for(lambda: any("alpha final" in text for text in answers(app)), "the final answer")
        assert sum("alpha final" in text for text in answers(app)) == 1
        assert [row.raw_text for row in app.query(UserMessage)] == ["job-alpha: read the config"]

    run_two_projects(tmp_path, {
        "job-alpha": [
            (first, tool_call("read_file", path="a.json")),
            (second, LLMResponse(content="alpha final")),
        ],
        "job-beta": [(None, LLMResponse(content="beta answer"))],
    }, body)
