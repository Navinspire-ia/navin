# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The active model is chosen by the operator, never guessed from a vendor default."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.agent.model_runtime import ModelRuntimeResolver
from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import Config
from navin.providers.factory import (
    build_provider_snapshot,
    build_provider_snapshot_allowing_unconfigured,
    provider_signature,
    unconfigured_provider_snapshot,
)
from navin.providers.unconfigured import UnconfiguredProvider
from navin.utils.llm_runtime import runtime_from_provider_snapshot


def _config(payload: dict) -> Config:
    return Config.model_validate(payload)


class DefaultModelTest(unittest.TestCase):
    def test_a_fresh_config_names_no_model(self):
        self.assertEqual(Config().agents.defaults.model, "")

    def test_an_unset_model_is_a_setup_error(self):
        with self.assertRaises(ValueError) as caught:
            build_provider_snapshot(Config())
        self.assertIn("No model is configured", str(caught.exception))

    def test_a_lone_api_key_does_not_borrow_another_vendors_model(self):
        # Auto-detection falls back to the only configured key; pairing it with a
        # model nobody chose used to send Anthropic names to unrelated APIs.
        config = _config({"providers": {"mistral": {"apiKey": "sk-test"}}})
        snapshot = build_provider_snapshot_allowing_unconfigured(config)
        self.assertIsInstance(snapshot.provider, UnconfiguredProvider)

    def test_a_chosen_model_reaches_its_provider(self):
        config = _config(
            {
                "providers": {"mistral": {"apiKey": "sk-test"}},
                "agents": {"defaults": {"model": "mistral-large-latest"}},
            }
        )
        snapshot = build_provider_snapshot(config)
        self.assertNotIsInstance(snapshot.provider, UnconfiguredProvider)
        self.assertEqual(snapshot.model, "mistral-large-latest")
        self.assertEqual(config.get_provider_name(snapshot.model), "mistral")


class RuntimeRefreshTest(unittest.TestCase):
    """A working session must never demote itself to the setup placeholder.

    The runtime refresh reloads the config file before every turn. When that
    read fails transiently (Settings rewriting the file, an env var briefly
    unresolved), the tolerant loader hands back the "please configure a
    provider" stand-in - which used to hijack the running task with a setup
    message instead of continuing on the already working model.
    """

    def _configured_runtime(self):
        config = _config(
            {
                "providers": {"mistral": {"apiKey": "sk-test"}},
                "agents": {"defaults": {"model": "mistral-large-latest"}},
            }
        )
        return runtime_from_provider_snapshot(build_provider_snapshot(config))

    def test_transient_config_failure_keeps_the_working_model(self):
        resolver = ModelRuntimeResolver(
            self._configured_runtime(),
            provider_snapshot_loader=lambda: unconfigured_provider_snapshot("boom"),
        )
        self.assertIsNone(resolver.refresh())
        self.assertNotIsInstance(resolver.runtime.provider, UnconfiguredProvider)
        self.assertEqual(resolver.runtime.model, "mistral-large-latest")

    def test_recovered_loader_updates_the_default_again(self):
        config = _config(
            {
                "providers": {"mistral": {"apiKey": "sk-test"}},
                "agents": {"defaults": {"model": "mistral-medium-latest"}},
            }
        )
        recovered = build_provider_snapshot(config)
        snapshots = iter([unconfigured_provider_snapshot("boom"), recovered])
        resolver = ModelRuntimeResolver(
            self._configured_runtime(),
            provider_snapshot_loader=lambda: next(snapshots),
        )
        self.assertIsNone(resolver.refresh())
        replaced = resolver.refresh()
        self.assertIsNotNone(replaced)
        self.assertEqual(resolver.runtime.model, "mistral-medium-latest")

    def test_boot_before_setup_still_reports_unconfigured(self):
        placeholder = runtime_from_provider_snapshot(unconfigured_provider_snapshot())
        resolver = ModelRuntimeResolver(
            placeholder,
            provider_snapshot_loader=lambda: unconfigured_provider_snapshot(),
        )
        resolver.refresh()
        self.assertIsInstance(resolver.runtime.provider, UnconfiguredProvider)


class PresetProviderReuseTest(unittest.TestCase):
    """A preset resolved on every turn runs on one provider and its connections.

    Each turn of a chat or subagent pinned to a preset built a new provider
    (OpenAI client, SSL context, TLS connection) and dropped it unclosed: the
    desktop engine held 148 connections to its LLM gateway after three days.
    """

    @staticmethod
    def _config(key: str = "sk-test") -> Config:
        return _config(
            {
                "providers": {"mistral": {"apiKey": key}},
                "agents": {"defaults": {"model": "mistral-large-latest"}},
                "modelPresets": {"fast": {"model": "mistral-small-latest", "provider": "mistral"}},
            }
        )

    def _resolver(self, current: dict[str, Config]) -> ModelRuntimeResolver:
        return ModelRuntimeResolver(
            runtime_from_provider_snapshot(build_provider_snapshot(current["config"])),
            model_presets=dict(current["config"].model_presets),
            preset_snapshot_loader=lambda name: build_provider_snapshot(current["config"], preset_name=name),
        )

    def test_a_pinned_preset_reuses_its_provider_on_every_turn(self):
        resolver = self._resolver({"config": self._config()})
        first = resolver.resolve_preset("fast")
        second = resolver.resolve_preset("fast")
        self.assertIs(first.provider, second.provider)
        self.assertEqual(second.model, "mistral-small-latest")
        self.assertIsNot(first.provider, resolver.runtime.provider, "another model keeps its own provider")

    def test_a_changed_preset_gets_a_new_provider(self):
        current = {"config": self._config()}
        resolver = self._resolver(current)
        before = resolver.resolve_preset("fast")
        current["config"] = self._config(key="sk-rotated")
        after = resolver.resolve_preset("fast")
        self.assertIsNot(before.provider, after.provider)
        self.assertNotEqual(before.snapshot_signature, after.snapshot_signature)
        self.assertIs(resolver.resolve_preset("fast").provider, after.provider)

    def test_a_preset_riding_on_the_default_provider_follows_the_default(self):
        # Without a preset loader a preset runs on the default provider, and its
        # signature names the preset only: shared, it stayed on the default
        # provider of its first turn after the key or the endpoint changed.
        first = build_provider_snapshot(self._config())
        resolver = ModelRuntimeResolver(
            runtime_from_provider_snapshot(first), model_presets=dict(self._config().model_presets),
        )
        self.assertIs(resolver.resolve_preset("fast").provider, first.provider)
        rotated = build_provider_snapshot(self._config(key="sk-rotated"))
        resolver.adopt_snapshot(rotated)
        self.assertIs(resolver.resolve_preset("fast").provider, rotated.provider)

    def test_a_fallback_trusting_another_ca_changes_the_provider_signature(self):
        # The signature decides which provider is reused: a fallback's CA bundle
        # is part of what that provider is built from.
        def config(ca_bundle: str) -> Config:
            return _config(
                {
                    "providers": {
                        "mistral": {"apiKey": "sk-test"},
                        "openai": {"apiKey": "sk-backup", "caBundle": ca_bundle},
                    },
                    "agents": {"defaults": {"model": "mistral-large-latest"}},
                    "modelPresets": {"backup": {"model": "gpt-4o", "provider": "openai"}},
                }
            )

        first = provider_signature(config("/etc/ssl/bank-a.pem"))
        second = provider_signature(config("/etc/ssl/bank-b.pem"))
        self.assertEqual(first[:-1], second[:-1], "the primary is the same")
        self.assertTrue(first[-1], "the other preset is the fallback")
        self.assertNotEqual(first[-1], second[-1])


class SavedConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self):
        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_an_unchosen_model_is_not_written_to_disk(self):
        save_config(load_config())
        written = json.loads(get_config_path().read_text(encoding="utf-8"))
        self.assertNotIn("model", written.get("agents", {}).get("defaults", {}))

    def test_a_chosen_model_survives_a_round_trip(self):
        config = load_config()
        config.agents.defaults.model = "mistral-large-latest"
        save_config(config)
        self.assertEqual(load_config().agents.defaults.model, "mistral-large-latest")
