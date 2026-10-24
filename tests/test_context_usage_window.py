"""The context meter shows the configured window, not the last turn's."""

from __future__ import annotations

from unittest.mock import patch

from navin.config.schema import Config, ModelPresetConfig
from navin.webui.context_usage import context_usage_payload


def _config(window: int) -> Config:
    config = Config()
    config.agents.defaults.context_window_tokens = window
    return config


def test_changed_window_shows_before_the_next_turn() -> None:
    session = {
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {
            "_last_context_usage": {
                "prompt_tokens": 100_000,
                "peak_prompt_tokens": 100_000,
                "context_window": 200_000,
                "source": "provider",
            }
        },
    }
    with patch("navin.config.loader.load_config", return_value=_config(500_000)):
        payload = context_usage_payload(session)
    assert payload["context_window"] == 500_000
    assert payload["percent"] == 20.0


def test_persisted_window_is_the_fallback_when_config_is_unreadable() -> None:
    session = {
        "messages": [],
        "metadata": {
            "_last_context_usage": {
                "prompt_tokens": 50_000,
                "peak_prompt_tokens": 50_000,
                "context_window": 200_000,
                "source": "provider",
            }
        },
    }
    with patch("navin.config.loader.load_config", side_effect=RuntimeError("boom")):
        payload = context_usage_payload(session)
    assert payload["context_window"] == 200_000


def test_pinned_1m_model_shows_1m_despite_smaller_models_configured() -> None:
    config = Config()
    config.model_presets = {
        "big": ModelPresetConfig(model="acme/big", provider="navin", context_window_tokens=1_000_000),
        "small": ModelPresetConfig(model="acme/small", provider="navin", context_window_tokens=200_000),
    }
    session = {
        "messages": [],
        "metadata": {
            "_last_model_preset": "big",
            "_last_context_usage": {
                "prompt_tokens": 100_000,
                "peak_prompt_tokens": 100_000,
                "context_window": 200_000,
                "source": "provider",
            },
        },
    }
    with patch("navin.config.loader.load_config", return_value=config):
        payload = context_usage_payload(session)
    assert payload["context_window"] == 1_000_000
    assert payload["percent"] == 10.0


def test_shared_bundle_keeps_its_recorded_window() -> None:
    session = {
        "messages": [],
        "metadata": {
            "_last_context_usage": {
                "prompt_tokens": 50_000,
                "peak_prompt_tokens": 50_000,
                "context_window": 200_000,
                "source": "provider",
            }
        },
    }
    with patch("navin.config.loader.load_config", return_value=_config(1_000_000)):
        payload = context_usage_payload(session, prefer_persisted_window=True)
    assert payload["context_window"] == 200_000
