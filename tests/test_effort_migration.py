# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A saved ``high`` reasoning effort returns to the default (low), once."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.config.loader import load_config, save_config


class EffortMigrationTest(unittest.TestCase):
    def _write(self, directory: str, data: dict) -> Path:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_a_saved_high_goes_back_to_low_in_defaults_and_presets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                "agents": {"defaults": {"reasoningEffort": "high"}},
                "modelPresets": {"fast": {"model": "m", "provider": "navin", "reasoningEffort": "high"}},
            })
            config = load_config(path)
            self.assertEqual(config.agents.defaults.reasoning_effort, "low")
            self.assertNotEqual(config.model_presets["fast"].reasoning_effort, "high")

    def test_other_values_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"agents": {"defaults": {"reasoningEffort": "medium"}}})
            self.assertEqual(load_config(path).agents.defaults.reasoning_effort, "medium")

    def test_a_high_chosen_after_the_migration_stays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"agents": {"defaults": {"reasoningEffort": "high"}}})
            config = load_config(path)
            config.agents.defaults.reasoning_effort = "high"
            save_config(config, path)
            self.assertEqual(load_config(path).agents.defaults.reasoning_effort, "high")
            self.assertEqual(load_config(path).agents.defaults.reasoning_effort, "high")


if __name__ == "__main__":
    unittest.main()
