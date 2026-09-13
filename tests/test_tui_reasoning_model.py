# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Changing the reasoning effort must not drop a non-default model pick.

Regression: on the CLI, picking a reasoning level (ctrl+shift+r) while a
non-default model preset is selected sent the turn back to the default model.
The runtime keeps a per-chat reasoning choice keyed by preset; the loop must
still resolve the chat's pinned/selected preset for the next turn.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from navin.agent.loop import AgentLoop
from navin.agent.model_runtime import ModelRuntimeResolver
from navin.config.loader import save_config
from navin.config.schema import Config
from navin.providers.factory import build_provider_snapshot
from navin.session.manager import SessionManager
from navin.tui.runtime import TuiRuntime
from navin.utils.llm_runtime import runtime_from_provider_snapshot


def reasoning_config(workspace: Path) -> Config:
    return Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "workspace": str(workspace),
                    "provider": "openai",
                    "model": "gpt-5.4",
                }
            },
            "providers": {
                "openai": {"apiKey": "sk-test"},
                "anthropic": {"apiKey": "sk-test"},
            },
            "modelPresets": {
                "claude": {
                    "model": "claude-sonnet-4-6",
                    "provider": "anthropic",
                    "label": "Sonnet",
                },
                "gpt": {"model": "gpt-5.4", "provider": "openai", "label": "GPT"},
            },
        }
    )


def stub_loop(config: Config, workspace: Path) -> "LoopStub":
    """A loop-shaped stub with the real resolver and session plumbing."""
    default_snapshot = build_provider_snapshot(config)
    resolver = ModelRuntimeResolver(
        runtime_from_provider_snapshot(default_snapshot),
        model_presets=dict(config.model_presets),
        provider_snapshot_loader=lambda: build_provider_snapshot(config),
        preset_snapshot_loader=lambda name: build_provider_snapshot(config, preset_name=name),
    )
    loop = LoopStub()
    loop.runtime_resolver = resolver
    loop.sessions = SessionManager(config.workspace_path)
    return loop


class LoopStub:
    """AgentLoop surface used by the TUI and runtime_for_inbound."""

    def __init__(self) -> None:
        self.runtime_resolver: ModelRuntimeResolver | None = None
        self.sessions: SessionManager | None = None
        self.session = None
        self._unified_session = False

    @property
    def model_presets(self) -> dict:
        return dict(self.runtime_resolver.model_presets)

    @property
    def model_preset(self) -> str | None:
        return self.runtime_resolver.model_preset

    @model_preset.setter
    def model_preset(self, name: str | None) -> None:
        self.runtime_resolver.select_preset(name)

    @property
    def model(self) -> str:
        return self.runtime_resolver.runtime.model

    def _effective_session_key(self, msg) -> str:
        return msg.session_key

    def _session_pinned_preset(self, msg) -> str | None:
        return AgentLoop._session_pinned_preset(self, msg)

    def llm_runtime(self):
        return AgentLoop.llm_runtime(self)


def tui_runtime(config: Config, loop: SimpleNamespace) -> TuiRuntime:
    runtime = TuiRuntime(config, on_event=AsyncMock())
    runtime.agent_loop = loop
    return runtime


class ReasoningKeepsModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.config = reasoning_config(self.workspace)
        save_config(self.config, self.workspace / "config.json")

    async def test_reasoning_change_keeps_the_selected_model(self) -> None:
        loop = stub_loop(self.config, self.workspace)
        runtime = tui_runtime(self.config, loop)
        runtime.set_model_preset("claude")
        self.assertEqual(loop.runtime_resolver.model_preset, "claude")

        runtime.set_reasoning_effort("high")
        self.assertEqual(
            loop.runtime_resolver.model_preset, "claude",
            "the reasoning pick must not reset the selected model",
        )
        runtime._refresh_status()
        self.assertEqual(runtime.status.model_preset, "claude")

    async def test_turn_after_reasoning_change_runs_the_selected_model(self) -> None:
        loop = stub_loop(self.config, self.workspace)
        runtime = tui_runtime(self.config, loop)
        runtime.set_model_preset("claude")
        runtime.set_reasoning_effort("high")

        runtime.bus = SimpleNamespace(publish_inbound=AsyncMock())
        runtime._ensure_tasks = lambda: None
        await runtime.send("salut")
        message = runtime.bus.publish_inbound.call_args.args[0]
        self.assertEqual(message.metadata.get("reasoning_effort"), "high")

        turn_runtime = AgentLoop.runtime_for_inbound(loop, message)
        self.assertEqual(
            turn_runtime.model_preset, "claude",
            "the turn after a reasoning change must still run the selected preset",
        )
        self.assertEqual(
            loop.runtime_resolver.model_preset, "claude",
            "resolving the turn must not reset the loop-level selection either",
        )
