# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI tool counter reports schemas the model sees, not the registry."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from navin.session.context_usage_meta import LAST_CONTEXT_USAGE_KEY
from navin.tui.runtime import TuiRuntime, sent_tool_count, tools_label


def _definition(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _Registry:
    def __init__(self, names: list[str]) -> None:
        self._defs = [_definition(n) for n in names]

    def get_definitions(self) -> list[dict]:
        return list(self._defs)


class _Sessions:
    def __init__(self, metadata: dict) -> None:
        self.session = SimpleNamespace(metadata=metadata, messages=[])

    def get_or_create(self, _key: str) -> SimpleNamespace:
        return self.session


def _fake_loop(names: list[str], metadata: dict, workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        model="test/model",
        model_preset="default",
        model_presets={},
        context_window_tokens=200_000,
        tools=_Registry(names),
        sessions=_Sessions(metadata),
        workspace=workspace,
    )


class ToolsLabelTest(unittest.TestCase):
    def test_shows_sent_against_loaded_when_gated(self) -> None:
        self.assertEqual(tools_label(52, 63), "52 of 63 tools in prompt")

    def test_collapses_when_nothing_is_withheld(self) -> None:
        self.assertEqual(tools_label(63, 63), "63 tools in prompt")

    def test_falls_back_to_loaded_before_any_measure(self) -> None:
        self.assertEqual(tools_label(0, 63), "63 tools in prompt")


class SentToolCountTest(unittest.TestCase):
    NAMES = ["read_file", "exec", "tenders", "career", "leads", "board", "cron", "mobile"]

    def test_plain_turn_withholds_desks_and_on_demand_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = _fake_loop(self.NAMES, {}, Path(tmp))
            self.assertEqual(sent_tool_count(loop, {}), 2)  # read_file, exec

    def test_open_desk_in_session_comes_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = _fake_loop(self.NAMES, {}, Path(tmp))
            count = sent_tool_count(loop, {"active_desk_tools": ["tenders"]})
            self.assertEqual(count, 3)

    def test_repository_facts_bring_board_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".navin" / "board").mkdir(parents=True)
            from navin.agent.tool_demand import clear_project_facts_cache

            clear_project_facts_cache()
            loop = _fake_loop(self.NAMES, {}, Path(tmp))
            self.assertEqual(sent_tool_count(loop, {}), 3)  # + board
            clear_project_facts_cache()


class RefreshStatusTest(unittest.TestCase):
    NAMES = ["read_file", "exec", "tenders", "career", "leads", "board", "cron", "mobile"]

    def _runtime(self, loop: SimpleNamespace) -> TuiRuntime:
        config = SimpleNamespace(
            workspace_path=Path(loop.workspace),
            resolve_preset=lambda _name: SimpleNamespace(provider="test"),
        )
        runtime = TuiRuntime(config, on_event=lambda _e: None)
        runtime.agent_loop = loop
        return runtime

    def test_prefers_the_last_turn_real_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metadata = {
                LAST_CONTEXT_USAGE_KEY: {
                    "prompt_tokens": 25_474,
                    "billed_tokens_session": 25_701,
                    "tool_count": 52,
                }
            }
            runtime = self._runtime(_fake_loop(self.NAMES, metadata, Path(tmp)))
            runtime._refresh_status()
            self.assertEqual(runtime.status.tool_count, 52)
            self.assertEqual(runtime.status.tool_registry_count, len(self.NAMES))
            self.assertEqual(runtime.status.billed_tokens_session, 25_701)

    def test_estimates_a_plain_turn_before_the_first_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(_fake_loop(self.NAMES, {}, Path(tmp)))
            runtime._refresh_status()
            self.assertEqual(runtime.status.tool_registry_count, len(self.NAMES))
            self.assertEqual(runtime.status.tool_count, 2)
            self.assertLess(runtime.status.tool_count, runtime.status.tool_registry_count)

    def test_provider_follows_the_preset_not_the_model_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = _fake_loop(self.NAMES, {}, Path(tmp))
            loop.model = "z-ai/glm-5.3-flash"
            runtime = self._runtime(loop)
            runtime._refresh_status()
            self.assertEqual(runtime.status.provider, "test")
            self.assertEqual(runtime.status.model, "z-ai/glm-5.3-flash")

    def test_reload_from_disk_refreshes_the_resolver(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            loop = _fake_loop(self.NAMES, {}, Path(tmp))
            runtime = self._runtime(loop)
            called: list[bool] = []

            def current(*, refresh: bool = False) -> None:
                called.append(refresh)

            loop.runtime_resolver = SimpleNamespace(current=current)
            fresh = SimpleNamespace(
                workspace_path=Path(tmp),
                resolve_preset=lambda _name: SimpleNamespace(provider="z-ai"),
            )
            with mock.patch("navin.config.loader.load_config", return_value=fresh):
                runtime.reload_from_disk()
            self.assertEqual(called, [True])
            self.assertIs(runtime.config, fresh)


if __name__ == "__main__":
    unittest.main()
