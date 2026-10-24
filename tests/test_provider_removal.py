# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A disconnected provider leaves nothing behind: models and picks move on."""

from __future__ import annotations

from navin.config.schema import Config, ModelPresetConfig
from navin.providers.media_choice import media_selected, set_media
from navin.providers.provider_removal import forget_provider


def _config() -> Config:
    config = Config()
    config.model_presets["sonnet-bedrock"] = ModelPresetConfig(provider="bedrock", model="eu.anthropic.claude-sonnet-5-5")
    config.model_presets["gpt"] = ModelPresetConfig(provider="openai", model="gpt-6.1")
    config.agents.defaults.model_preset = "sonnet-bedrock"
    config.agents.defaults.provider = "bedrock"
    set_media(config, "tts", "bedrock", "neural")
    set_media(config, "live", "bedrock", "amazon.nova-2-sonic-v1:0")
    set_media(config, "image", "openai", "gpt-image-2")
    return config


def test_the_provider_models_and_picks_leave_and_another_model_takes_over():
    config = _config()
    assert forget_provider(config, "bedrock") is True
    assert "sonnet-bedrock" not in config.model_presets and "gpt" in config.model_presets
    # Without a subscription: another connected provider's model becomes the default.
    assert config.agents.defaults.model_preset == "gpt"
    assert config.agents.defaults.provider == "auto"
    assert media_selected(config, "tts") == ("", "")
    assert media_selected(config, "live") == ("", "")
    # Picks on other providers are left alone.
    assert media_selected(config, "image") == ("openai", "gpt-image-2")


def test_nothing_changes_for_a_provider_not_in_use():
    config = _config()
    assert forget_provider(config, "mistral") is False
    assert config.agents.defaults.model_preset == "sonnet-bedrock"


def test_leftovers_of_an_earlier_disconnection_are_cleaned(monkeypatch):
    from navin.providers.provider_removal import prune_disconnected_providers

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = _config()
    # Bedrock still connected (a region), OpenAI disconnected earlier (no key).
    config.providers.bedrock.region = "eu-west-3"
    assert prune_disconnected_providers(config) is True
    assert "gpt" not in config.model_presets and "sonnet-bedrock" in config.model_presets
    # A media pick may wait for its key: only Disconnect clears it.
    assert media_selected(config, "image") == ("openai", "gpt-image-2")
