# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama setup helpers used by Settings → Providers."""

from __future__ import annotations

import unittest
from unittest import mock

from navin.providers import ollama_setup as setup


class ModelIdValidationTest(unittest.TestCase):
    def test_accepts_common_ids(self) -> None:
        self.assertEqual(setup._validate_model_id("llama3.2"), "llama3.2")
        self.assertEqual(setup._validate_model_id("qwen2.5-coder:7b"), "qwen2.5-coder:7b")
        self.assertEqual(setup._validate_model_id(" library/foo "), "library/foo")

    def test_rejects_unsafe_ids(self) -> None:
        with self.assertRaises(setup.OllamaSetupError):
            setup._validate_model_id("../etc/passwd")
        with self.assertRaises(setup.OllamaSetupError):
            setup._validate_model_id("model; rm -rf /")
        with self.assertRaises(setup.OllamaSetupError):
            setup._validate_model_id("")


class StatusPayloadTest(unittest.TestCase):
    def test_status_reports_install_phase_without_binary(self) -> None:
        with (
            mock.patch.object(setup, "resolve_ollama_binary", return_value=None),
            mock.patch.object(setup, "_daemon_running", return_value=False),
            mock.patch.object(setup, "_configured_api_base", return_value=None),
            mock.patch.object(setup, "_list_installed_models", return_value=[]),
        ):
            payload = setup.ollama_status_payload()
        self.assertEqual(payload["phase"], "install")
        self.assertFalse(payload["installed"])
        self.assertFalse(payload["binary_available"])
        self.assertFalse(payload["running"])
        self.assertTrue(payload["recommended_models"])

    def test_running_daemon_without_cli_counts_as_installed(self) -> None:
        """WSL often reaches a Windows-hosted Ollama without a local binary."""
        with (
            mock.patch.object(setup, "resolve_ollama_binary", return_value=None),
            mock.patch.object(setup, "_daemon_running", return_value=True),
            mock.patch.object(
                setup, "_configured_api_base", return_value="http://localhost:11434/v1"
            ),
            mock.patch.object(
                setup,
                "_list_installed_models",
                return_value=[{"id": "llama3.2", "size": 1, "modified_at": None}],
            ),
        ):
            payload = setup.ollama_status_payload()
        self.assertTrue(payload["installed"])
        self.assertFalse(payload["binary_available"])
        self.assertTrue(payload["running"])
        self.assertEqual(payload["phase"], "ready")
        self.assertTrue(payload["recommended_models"][0]["installed"])

    def test_status_reports_ready_when_running_and_configured(self) -> None:
        binary = setup.OllamaBinary(path="/usr/bin/ollama", source="path")
        with (
            mock.patch.object(setup, "resolve_ollama_binary", return_value=binary),
            mock.patch.object(setup, "_daemon_running", return_value=True),
            mock.patch.object(
                setup, "_configured_api_base", return_value="http://localhost:11434/v1"
            ),
            mock.patch.object(
                setup,
                "_list_installed_models",
                return_value=[{"id": "llama3.2", "size": 1, "modified_at": None}],
            ),
        ):
            payload = setup.ollama_status_payload()
        self.assertEqual(payload["phase"], "ready")
        self.assertTrue(payload["binary_available"])
        self.assertTrue(payload["recommended_models"][0]["installed"])

    def test_wsl_install_plan_mentions_windows_host(self) -> None:
        with mock.patch.object(setup, "host_platform", return_value="wsl"):
            plan = setup._install_plan("wsl")
        self.assertEqual(plan["method"], "script")
        self.assertTrue(plan["supported"])
        self.assertIn("install.sh", plan["command"])
        self.assertTrue(any("Windows" in step for step in plan["steps"]))


class ConfigureTest(unittest.TestCase):
    def test_configure_writes_api_base_and_preset(self) -> None:
        from navin.config.schema import Config

        config = Config()
        with (
            mock.patch.object(setup, "load_config", return_value=config),
            mock.patch.object(setup, "save_config") as save,
            mock.patch.object(setup, "ollama_status_payload", return_value={"provider": "ollama"}),
        ):
            payload = setup.configure_ollama(model="llama3.2", make_active=True)

        self.assertEqual(config.providers.ollama.api_base, "http://localhost:11434/v1")
        self.assertIn(config.agents.defaults.model_preset, config.model_presets)
        preset = config.model_presets[config.agents.defaults.model_preset]
        self.assertEqual(preset.provider, "ollama")
        self.assertEqual(preset.model, "llama3.2")
        save.assert_called_once()
        self.assertTrue(payload["last_action"]["ok"])
        self.assertNotIn("settings", payload)

    def test_configure_preserves_custom_api_base(self) -> None:
        from navin.config.schema import Config

        config = Config()
        config.providers.ollama.api_base = "http://192.168.1.10:11434/v1"
        with (
            mock.patch.object(setup, "load_config", return_value=config),
            mock.patch.object(setup, "save_config") as save,
            mock.patch.object(setup, "ollama_status_payload", return_value={"provider": "ollama"}),
        ):
            payload = setup.configure_ollama(model="llama3.2", make_active=True)

        self.assertEqual(config.providers.ollama.api_base, "http://192.168.1.10:11434/v1")
        self.assertIn("192.168.1.10", payload["last_action"]["message"])
        save.assert_called_once()  # preset still written


class OllamaApiSurfaceTest(unittest.TestCase):
    def test_configure_action_attaches_settings_payload(self) -> None:
        from navin.webui import ollama_setup_api as api

        with (
            mock.patch.object(
                api,
                "configure_ollama",
                return_value={"provider": "ollama", "last_action": {"ok": True}},
            ),
            mock.patch.object(api, "settings_payload", return_value={"providers": []}),
        ):
            payload = api.ollama_setup_action("configure", {"model": ["llama3.2"]})
        self.assertEqual(payload["settings"], {"providers": []})


if __name__ == "__main__":
    unittest.main()
