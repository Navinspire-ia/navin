# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A guest sees the model and the context fill of the shared session."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.session.context_usage_meta import LAST_CONTEXT_USAGE_KEY
from navin.share.context import session_context


class ShareUsageContextTest(unittest.TestCase):
    def _session(self, metadata: dict) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        rows = [
            {"_type": "metadata", "metadata": metadata, "created_at": "", "updated_at": ""},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        handle.write("\n".join(json.dumps(row) for row in rows))
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_model_and_fill_from_the_last_turn(self) -> None:
        path = self._session({
            LAST_CONTEXT_USAGE_KEY: {
                "prompt_tokens": 50_000,
                "peak_prompt_tokens": 50_000,
                "context_window": 1_000_000,
                "model": "moonshotai/kimi-k3",
            },
        })
        context = session_context(path, None)
        usage = context["usage"]
        self.assertEqual(usage["model"], "moonshotai/kimi-k3")
        self.assertEqual(usage["context_window"], 1_000_000)
        self.assertGreater(usage["tokens"], 0)
        self.assertGreaterEqual(usage["percent"], 0)
        self.assertEqual(context["conversation"]["model"], "moonshotai/kimi-k3")

    def test_session_without_usage(self) -> None:
        usage = session_context(self._session({}), None)["usage"]
        self.assertEqual(usage["model"], "")


class ShareSelectedModelTest(unittest.TestCase):
    """The guest sees the model the host picked, not the one of the last turn."""

    _session = ShareUsageContextTest._session

    def setUp(self) -> None:
        from navin.config.loader import get_config_path, save_config, set_config_path
        from navin.config.schema import Config, ModelPresetConfig

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        previous = get_config_path()
        self.addCleanup(set_config_path, previous)
        set_config_path(Path(tmp.name) / "config.json")
        save_config(Config(model_presets={
            "claude-opus-5-5": ModelPresetConfig(model="anthropic/claude-opus-5.5"),
        }))

    def _switched(self, pin: str) -> Path:
        # The last turn ran on Sonnet, then the host picked another model.
        return self._session({
            "_last_model_preset": pin,
            LAST_CONTEXT_USAGE_KEY: {
                "prompt_tokens": 264_000, "context_window": 1_000_000, "model": "anthropic/claude-sonnet-5.5",
            },
        })

    def test_the_pick_wins_over_the_last_turn(self) -> None:
        context = session_context(self._switched("claude-opus-5-5"), None)
        self.assertEqual(context["usage"]["model"], "anthropic/claude-opus-5.5")
        self.assertEqual(context["conversation"]["model"], "anthropic/claude-opus-5.5")

    def test_a_deleted_or_auto_pick_keeps_the_last_turn(self) -> None:
        for pin in ("gone-preset", "auto"):
            with self.subTest(pin=pin):
                usage = session_context(self._switched(pin), None)["usage"]
                self.assertEqual(usage["model"], "anthropic/claude-sonnet-5.5")

    def test_the_model_the_host_runs_now_wins(self) -> None:
        context = session_context(self._switched("auto"), None, "openai/gpt-6-luna")
        self.assertEqual(context["usage"]["model"], "openai/gpt-6-luna")
        self.assertEqual(context["conversation"]["model"], "openai/gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
