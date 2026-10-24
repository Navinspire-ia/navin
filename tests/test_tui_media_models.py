# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Terminal settings pick media models from lists, like the desktop."""

from __future__ import annotations

from unittest.mock import patch

from navin.config.loader import save_config
from navin.config.schema import Config, ModelPresetConfig
from navin.tui.media_models import NONE_ID, apply_media_model, describe, media_model_items
from navin.tui.settings import _set


def test_lists_added_models_and_the_connected_providers_catalog():
    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    cfg.model_presets = {"live": ModelPresetConfig(label="Gemini Live", model="models/gemini-3.8-live", provider="gemini")}
    save_config(cfg)
    payload = {"status": "available", "models": [{"id": "gemini-3.1-flash-live-preview"}, {"id": "gemini-3.8-live"}]}
    with patch("navin.webui.settings_api.provider_models_payload", return_value=payload):
        items = media_model_items("live")
    assert sorted(item.id for item in items) == ["gemini::gemini-3.1-flash-live-preview", "gemini::gemini-3.8-live"]
    assert {item.group for item in items} == {"Gemini"}


def test_picking_sets_provider_and_model_and_live_can_be_cleared():
    data: dict = {"voice": {"ttsProvider": "navin", "voice": "Kore"}}
    apply_media_model(data, "tts", "gemini::models/gemini-3.8-flash-tts", _set)
    assert data["voice"]["ttsProvider"] == "gemini"
    assert data["voice"]["ttsModel"] == "gemini-3.8-flash-tts"
    assert data["voice"]["voice"] == "auto"
    assert describe(data, "tts") == "gemini-3.8-flash-tts  ·  gemini"

    apply_media_model(data, "stt", "gemini::gemini-3.5-transcribe", _set)
    assert data["transcription"] == {"provider": "gemini", "model": "gemini-3.5-transcribe",
                                     "selectionExplicit": True, "enabled": True}

    apply_media_model(data, "live", "gemini::gemini-3.8-live", _set)
    apply_media_model(data, "live", NONE_ID, _set)
    assert (data["voice"]["liveRealtimeProvider"], data["voice"]["liveRealtimeModel"]) == ("", "")
