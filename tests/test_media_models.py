"""The same mixed catalog must stay correctly separated in every media picker."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from navin.config.loader import load_config, save_config
from navin.config.schema import Config, ModelPresetConfig
from navin.providers.media_models import MEDIA_MODEL_KINDS, media_model_kinds
from navin.webui.settings_api import WebUISettingsError, provider_models_payload, update_model_route

CASES = json.loads((Path(__file__).parent / "fixtures/media-models.json").read_text())


@pytest.mark.parametrize("row", CASES, ids=[row["id"] for row in CASES])
def test_media_capabilities_match_the_shared_browser_cases(row):
    assert media_model_kinds(row["id"], row) == row["expected"]


@pytest.mark.parametrize("kind", sorted(MEDIA_MODEL_KINDS))
@pytest.mark.parametrize("provider", ["navin", "openrouter", "openai"])
def test_provider_catalog_returns_only_models_for_the_requested_media(kind, provider):
    config = Config()
    getattr(config.providers, provider).api_key = "test-provider-key"
    save_config(config)
    response = httpx.Response(200, json={"data": CASES[:-1]}, request=httpx.Request("GET", "https://test.invalid/models"))
    with patch("navin.webui.settings_api.httpx.get", return_value=response) as fetch:
        result = provider_models_payload({"provider": [provider], "modality": [kind]})
    expected = {row["id"] for row in CASES[:-1] if kind in row["expected"]}
    assert {row["id"] for row in result["models"]} == expected
    assert result["model_count"] == len(expected)
    assert result["provider"] == provider
    assert "test-provider-key" not in str(result)
    if provider in {"navin", "openrouter"}:
        assert fetch.call_args.kwargs["params"] == {"output_modalities": {
            "stt": "transcription", "tts": "speech", "image": "image", "video": "video", "music": "audio",
        }[kind]}


def test_google_media_catalog_uses_native_models_and_follows_pagination():
    config = Config()
    config.providers.gemini.api_key = "test-google-key"
    save_config(config)
    responses = [
        {"models": [{"name": "models/gemini-3.7-flash"}], "nextPageToken": "next"},
        {"models": [{"name": "models/gemini-3.1-flash-tts-preview", "displayName": "Gemini speech"},
                    {"name": "models/veo-3.1-fast"}]},
    ]

    def fetch(url, **kwargs):
        assert url == "https://generativelanguage.googleapis.com/v1beta/models"
        assert kwargs["headers"]["x-goog-api-key"] == "test-google-key"
        assert "Authorization" not in kwargs["headers"]
        if len(responses) == 1:
            assert kwargs["params"]["pageToken"] == "next"
        return httpx.Response(200, json=responses.pop(0), request=httpx.Request("GET", url))

    with patch("navin.webui.settings_api.httpx.get", side_effect=fetch):
        result = provider_models_payload({"provider": ["gemini"], "modality": ["tts"]})
    assert [row["id"] for row in result["models"]] == ["gemini-3.1-flash-tts-preview"]
    assert result["models"][0]["label"] == "Gemini speech"
    assert "Kore" in result["models"][0]["voices"]


@pytest.mark.parametrize("role", ["dev", "vision", "computer"])
def test_media_generator_cannot_be_assigned_to_a_chat_or_desktop_route(role):
    config = Config()
    config.model_presets["speech"] = ModelPresetConfig(provider="openrouter", model="qwen/qwen-audio-3.0-tts-flash")
    save_config(config)
    with pytest.raises(WebUISettingsError, match="chat model"):
        update_model_route({"role": [role], "preset": ["speech"]})
    assert role not in load_config().model_routes


def test_desktop_route_rejects_a_provider_declared_text_only_model():
    config = Config()
    config.model_presets["text"] = ModelPresetConfig(provider="openai", model="gpt-5", input_modalities=["text"])
    save_config(config)
    with pytest.raises(WebUISettingsError, match="vision"):
        update_model_route({"role": ["computer"], "preset": ["text"]})
