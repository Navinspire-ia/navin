# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""P3-2: Dream consolidation runs on its dedicated preset when configured.

``agents.defaults.dream.model_override`` was documented as pending; it now
resolves through the loop's runtime resolver - preset name first, raw model
slug as fallback - and degrades to the session default rather than skipping
the consolidation.
"""

from __future__ import annotations

import unittest
from unittest import mock

from navin.cli.commands import _dream_runtime_override


def _config(override: str | None) -> mock.Mock:
    config = mock.Mock()
    config.agents.defaults.dream.model_override = override
    return config


class DreamRuntimeOverrideTest(unittest.TestCase):
    def test_no_override_means_session_default(self) -> None:
        agent = mock.Mock()
        self.assertIsNone(_dream_runtime_override(agent, _config(None)))
        self.assertIsNone(_dream_runtime_override(agent, _config("   ")))
        agent.runtime_resolver.resolve_preset.assert_not_called()

    def test_preset_name_wins(self) -> None:
        agent = mock.Mock()
        runtime = object()
        agent.runtime_resolver.resolve_preset.return_value = runtime
        self.assertIs(_dream_runtime_override(agent, _config("dream")), runtime)
        agent.runtime_resolver.resolve_preset.assert_called_once_with("dream")
        agent.runtime_resolver.resolve_override.assert_not_called()

    def test_unknown_preset_falls_back_to_model_slug(self) -> None:
        agent = mock.Mock()
        runtime = object()
        agent.runtime_resolver.resolve_preset.side_effect = KeyError("nope")
        agent.runtime_resolver.resolve_override.return_value = runtime
        self.assertIs(
            _dream_runtime_override(agent, _config("deepseek/deepseek-chat")),
            runtime,
        )
        agent.runtime_resolver.resolve_override.assert_called_once_with(
            model="deepseek/deepseek-chat", model_preset=None
        )

    def test_unresolvable_override_degrades_to_default(self) -> None:
        # A consolidation on the default model beats no consolidation at all.
        agent = mock.Mock()
        agent.runtime_resolver.resolve_preset.side_effect = KeyError("nope")
        agent.runtime_resolver.resolve_override.side_effect = ValueError("media")
        self.assertIsNone(_dream_runtime_override(agent, _config("bad-model")))

    def test_agent_without_resolver_degrades_to_default(self) -> None:
        agent = mock.Mock(spec=[])  # no runtime_resolver attribute
        self.assertIsNone(_dream_runtime_override(agent, _config("dream")))


if __name__ == "__main__":
    unittest.main()
