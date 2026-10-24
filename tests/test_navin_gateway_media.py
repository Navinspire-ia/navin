# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Every modality follows the managed key to the Navin gateway (LiteLLM)."""

from __future__ import annotations

import pytest

from navin.providers.gateway import is_navin_gateway

GATEWAY = "https://llm.navin.live/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
KEY = "sk-navin-virtual"


def test_the_gateway_is_any_managed_base_but_openrouter():
    assert is_navin_gateway(GATEWAY)
    assert not is_navin_gateway(OPENROUTER)
    assert not is_navin_gateway("")
    assert not is_navin_gateway(None)


def test_tts_and_stt_speak_the_standard_audio_api_on_the_gateway(monkeypatch):
    import navin.audio.tts  # noqa: F401 - the app's import order (providers.tts alone cycles)
    from navin.providers.transcription import (
        NavinTranscriptionProvider,
        OpenAITranscriptionProvider,
    )
    from navin.providers.tts import NavinTtsProvider, OpenAICompatibleTtsProvider

    # The user's own OpenAI key must never be sent to our gateway.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-users-own-openai")
    tts = NavinTtsProvider(api_key=KEY, api_base=GATEWAY, model="gemini/gemini-2.5-flash-tts")
    assert type(tts) is OpenAICompatibleTtsProvider
    assert tts.api_key == KEY
    stt = NavinTranscriptionProvider(api_key=KEY, api_base=GATEWAY, model="qwen/paraformer-v2")
    assert type(stt) is OpenAITranscriptionProvider
    assert stt.api_key == KEY
    assert "llm.navin.live" in str(getattr(stt, "api_url", getattr(stt, "api_base", "")))

    # The subscription never falls back to OpenRouter: an OpenRouter or
    # missing base lands on the gateway too.
    for base in (OPENROUTER, None):
        tts_any = NavinTtsProvider(api_key=KEY, api_base=base)
        assert type(tts_any) is OpenAICompatibleTtsProvider
        assert tts_any.api_url.startswith("https://llm.navin.live")
        stt_any = NavinTranscriptionProvider(api_key=KEY, api_base=base)
        assert type(stt_any) is OpenAITranscriptionProvider
        assert "llm.navin.live" in str(getattr(stt_any, "api_url", getattr(stt_any, "api_base", "")))


@pytest.mark.parametrize("module,navin_cls,openai_cls", [
    ("navin.providers.image_generation", "NavinImageGenerationClient", "OpenAIImageGenerationClient"),
    ("navin.providers.video_generation", "NavinVideoGenerationClient", "OpenAIVideoGenerationClient"),
])
def test_image_and_video_speak_the_standard_api_on_the_gateway(module, navin_cls, openai_cls):
    import importlib

    mod = importlib.import_module(module)
    navin = getattr(mod, navin_cls)
    on_gateway = navin(api_key=KEY, api_base=GATEWAY)
    assert type(on_gateway) is getattr(mod, openai_cls)
    assert on_gateway.provider_name == "navin"
    assert on_gateway.api_key == KEY
    assert on_gateway.api_base.startswith("https://llm.navin.live")
    for base in (OPENROUTER, None):
        fallback = navin(api_key=KEY, api_base=base)
        assert type(fallback) is getattr(mod, openai_cls)
        assert fallback.api_base.startswith("https://llm.navin.live")


def test_the_navin_chat_provider_never_targets_openrouter():
    from navin.providers.openai_compat_provider import OpenAICompatProvider
    from navin.providers.registry import find_by_name

    spec = find_by_name("navin")
    assert spec.default_api_base == GATEWAY
    for base in (None, OPENROUTER, "https://openrouter.ai/api/v1/"):
        provider = OpenAICompatProvider(api_key=KEY, api_base=base, spec=spec)
        assert provider._effective_base == GATEWAY
        assert not provider._is_openrouter
    staging = "https://llm-staging.navin.live/v1"
    assert OpenAICompatProvider(api_key=KEY, api_base=staging, spec=spec)._effective_base == staging


def test_music_follows_the_gateway():
    import navin.audio  # noqa: F401 - app import order
    from navin.providers.music_generation import NavinMusicGenerationClient

    for base in (None, OPENROUTER):
        assert NavinMusicGenerationClient(api_key=KEY, api_base=base).api_base == GATEWAY


def test_missing_media_buckets_leave_no_openrouter_default():
    import navin.audio  # noqa: F401 - app import order
    from navin.config.schema import Config
    from navin.providers.managed_catalog import apply_catalog, parse_catalog

    config = Config()
    config.license.plan = "pro"
    config.license.managed_api_key = KEY
    config.providers.navin.api_key = KEY
    payload = {
        "provider": "openrouter",  # whatever the site says, no OpenRouter fallback
        "defaultModel": "anthropic/claude-sonnet-4.5",
        "models": [{"slug": "anthropic/claude-sonnet-4.5", "tier": "main"}],
        "media": {},
    }
    apply_catalog(config, parse_catalog(payload, plan="pro"), force_managed=True, steer_tools=True)
    assert config.tools.image_generation.model == ""
    assert config.tools.video_generation.model == ""
    assert config.tools.music_generation.model == ""


def test_unreachable_catalog_keeps_a_subscription_off_the_embedded_openrouter_models(monkeypatch):
    import httpx

    import navin.audio  # noqa: F401 - app import order
    from navin.config.schema import Config
    from navin.providers import managed_catalog

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(managed_catalog.httpx, "get", boom)
    monkeypatch.setattr(managed_catalog, "save_config", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("navin.optional_live.live_modules_available", lambda: True)
    config = Config()
    config.model_catalog.enabled = True
    config.license.plan = "pro"
    before = dict(config.model_presets)
    assert managed_catalog.sync_managed_catalog(config, force=True) is False
    assert config.model_presets == before
