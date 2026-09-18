# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Full TUI + engine integration for the queue "Send now" flow.

The real NavinApp runs on a real TuiRuntime with a real AgentLoop behind a
scripted provider. A prompt queued while the first turn runs is sent with
"Send now": the engine must inject it into the running turn (the next model
call sees it) and the transcript must keep showing it.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

from navin.config.loader import get_config_path, set_config_path
from navin.config.schema import Config
from navin.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from navin.providers.factory import ProviderSnapshot
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.widgets import PromptQueue, QueuedPromptRow, UserMessage


class ScriptedProvider(LLMProvider):
    def __init__(self, responses, gate=None):
        super().__init__()
        self.responses = deque(responses)
        self.calls: list[list[dict]] = []
        self.gate = gate

    def get_default_model(self):
        return "test-send-now-tui"

    async def chat(self, **kwargs):
        if self.gate is not None and len(self.calls) == 0:
            await self.gate.wait()
        self.calls.append(list(kwargs.get("messages") or []))
        return self.responses.popleft()

    async def chat_with_retry(self, **kwargs):
        return await self.chat(**kwargs)


def tool_call(name, **arguments):
    return LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
        ToolCallRequest(id="call", name=name, arguments=arguments),
    ])


class NoopHook:
    """Stand-in for the usage/cron hooks in integration tests."""

    def __init__(self, *args, **kwargs):
        pass

    accounts_for_usage = False

    async def before_run(self, context):
        pass

    async def after_run(self, context):
        pass

    async def on_error(self, context):
        pass

    async def on_finally(self, context):
        pass


def make_config(workspace: Path) -> Config:
    return Config.model_validate({
        "agents": {
            "defaults": {
                "workspace": str(workspace),
                "provider": "openai",
                "model": "test-send-now-tui",
            }
        },
        "providers": {"openai": {"apiKey": "sk-test"}},
    })


def test_send_now_reaches_the_running_turn_end_to_end(tmp_path):
    async def run():
        (tmp_path / "a.json").write_text("{}\n")
        provider = ScriptedProvider([
            LLMResponse(content="First answer, follow-up noted."),
            LLMResponse(content="Follow-up handled."),
        ], gate=asyncio.Event())
        config = make_config(tmp_path)
        old_config_path = get_config_path()
        set_config_path(tmp_path / "config.json")
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = NavinApp(config, prefs=prefs)

        def fake_snapshot(*args, **kwargs):
            return ProviderSnapshot(provider, "test-send-now-tui", 128_000, ("test-send-now-tui",))

        with (
            patch(
                "navin.providers.factory.build_provider_snapshot_allowing_unconfigured",
                fake_snapshot,
            ),
            patch(
                "navin.providers.factory.load_provider_snapshot_allowing_unconfigured",
                fake_snapshot,
            ),
            patch("navin.agent.skills._home_skill_dirs", return_value=[]),
            patch("navin.agent.loop.AgentLoop._connect_mcp", new_callable=AsyncMock),
            patch("navin.optional_live.live_modules_available", return_value=False),
            patch("navin.tui.app.live_modules_available", return_value=False),
            patch("navin.cron.service.CronService"),
            patch("navin.cron.spend.CronSpendHook", NoopHook),
            patch("navin.webui.token_usage.TokenUsageHook", NoopHook),
        ):
            try:
                async with app.run_test(size=(100, 32)) as pilot:
                    app._engine_ready = True
                    await app._render_history()

                    # Turn 1 starts; the provider holds the first answer until
                    # the gate opens so the turn stays active.
                    await app.submit_text("first prompt")
                    for _ in range(300):
                        if app.runtime.turn_active:
                            break
                        await asyncio.sleep(0.01)
                    assert app.runtime.turn_active, "the first turn never started"

                    # Queue a prompt while the turn runs, then Send now.
                    await app.submit_text("urgent follow-up")
                    await pilot.pause()
                    assert len(app.query(QueuedPromptRow)) == 1
                    row = app.query(QueuedPromptRow).first()
                    await pilot.click(row.query_one(".queue-send"))
                    await pilot.pause()
                    provider.gate.set()

                    # The engine must inject it: the next model call sees it.
                    for _ in range(300):
                        if len(provider.calls) >= 2:
                            break
                        await asyncio.sleep(0.01)
                    assert len(provider.calls) >= 2, "the follow-up never reached the model"
                    second_call = provider.calls[1]
                    user_texts = [
                        str(m.get("content")) for m in second_call
                        if m.get("role") == "user"
                    ]
                    assert any("urgent follow-up" in t for t in user_texts), user_texts

                    # The turn ends; the transcript keeps both user messages.
                    await pilot.pause()
                    texts = [r.raw_text for r in app.query(UserMessage)]
                    assert texts == ["first prompt", "urgent follow-up"], texts
                    assert not app.query_one(PromptQueue).display
            finally:
                if app.runtime.agent_loop is not None:
                    app.runtime.agent_loop.stop()
                if app.runtime._loop_task is not None:
                    app.runtime._loop_task.cancel()
                if app.runtime._consumer_task is not None:
                    app.runtime._consumer_task.cancel()
                await asyncio.gather(
                    app.runtime._loop_task or asyncio.sleep(0),
                    app.runtime._consumer_task or asyncio.sleep(0),
                    return_exceptions=True,
                )
                await app.runtime.close()
        set_config_path(old_config_path)
    asyncio.run(run())
