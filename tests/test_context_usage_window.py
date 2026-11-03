"""The context meter shows the configured window, not the last turn's."""

from __future__ import annotations

from unittest.mock import patch

from navin.config.schema import Config, ModelPresetConfig
from navin.webui.context_usage import (
    apply_context_window,
    context_usage_base,
    context_usage_payload,
)


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


def _route_session(tokens: int) -> dict:
    return {
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {
            "_last_context_usage": {
                "prompt_tokens": tokens,
                "peak_prompt_tokens": tokens,
                "context_window": 200_000,
                "source": "provider",
            }
        },
    }


def test_the_window_is_applied_to_a_cached_base_at_every_call(tmp_path) -> None:
    base = context_usage_base(_route_session(100_000))
    assert "context_window" not in base and "percent" not in base
    with patch("navin.config.loader.get_config_path", return_value=tmp_path / "config.json"):
        with patch("navin.config.loader.load_config", return_value=_config(500_000)):
            first = apply_context_window(base)
            whole = context_usage_payload(_route_session(100_000))
        with patch("navin.config.loader.load_config", return_value=_config(1_000_000)):
            second = apply_context_window(base)
    assert (first["context_window"], first["percent"]) == (500_000, 20.0)
    assert (second["context_window"], second["percent"]) == (1_000_000, 10.0)
    assert not any(key.startswith("_") for key in first), "inputs of the window step stay server-side"
    assert first == whole, "same payload as the one-step call"


def test_the_route_rebuilds_per_chat_not_per_config_write(tmp_path) -> None:
    """A config write used to send every open chat back to a cold rebuild."""
    import asyncio
    import json
    from types import SimpleNamespace

    from navin.webui.route_cache import CoalescingCache
    from navin.webui.ws_http import GatewayHTTPHandler

    reads: list[str] = []

    def read_session_file(key: str) -> dict:
        reads.append(key)
        return _route_session(100_000)

    async def session_scope(_key: str) -> SimpleNamespace:
        return SimpleNamespace(project_path="")

    handler = SimpleNamespace(
        route_cache=CoalescingCache(),
        check_api_token=lambda _request: True,
        session_manager=SimpleNamespace(read_session_file=read_session_file),
        _session_scope=session_scope,
    )
    route = GatewayHTTPHandler._handle_context_usage.__get__(handler)
    config_path = tmp_path / "config.json"

    async def poll() -> dict:
        response = await route(SimpleNamespace(path="/x"), "websocket%3Achat-1")
        return json.loads(response.body)

    with patch("navin.config.loader.get_config_path", return_value=config_path):
        config_path.write_text("{}", encoding="utf-8")
        with patch("navin.config.loader.load_config", return_value=_config(500_000)):
            first = asyncio.run(poll())
        # Settings saves a new context size: a new config file version.
        config_path.write_text('{"agents": {}}', encoding="utf-8")
        with patch("navin.config.loader.load_config", return_value=_config(1_000_000)):
            second = asyncio.run(poll())
    assert reads == ["websocket:chat-1"], "one session read for both polls"
    assert (first["context_window"], first["percent"]) == (500_000, 20.0)
    assert (second["context_window"], second["percent"]) == (1_000_000, 10.0), "the new window shows at once"
