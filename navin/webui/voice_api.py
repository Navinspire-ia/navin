"""Preview a configured speech provider without starting a microphone session."""

from __future__ import annotations

import base64
from typing import Any

from navin.audio.tts import TtsError, resolve_tts_config, synthesize_speech_with_config
from navin.audio.tts_registry import resolve_tts_provider
from navin.config.loader import load_config
from navin.webui.http_utils import query_first
from navin.webui.settings_api import WebUISettingsError


async def voice_preview_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    provider = (query_first(query, "provider") or "").strip()
    spec = resolve_tts_provider(provider)
    if spec is None:
        raise WebUISettingsError("Select a speech provider")
    config = load_config()
    text = (query_first(query, "text") or "").strip()
    model = (query_first(query, "model") or "").strip()
    voice = (query_first(query, "voice") or "auto").strip()
    if not text or len(text) > 600 or len(model) > 200 or len(voice) > 80:
        raise WebUISettingsError("Invalid voice preview")
    # This copy is never saved: auditioning a voice must not alter the chat,
    # its subscription, or the speech settings another window is editing.
    config = config.model_copy(deep=True)
    config.voice.tts_provider = spec.name
    config.voice.tts_model = model or spec.default_model
    config.voice.voice = voice
    config.voice.response_format = "mp3"
    effective = resolve_tts_config(config)
    if effective.provider != spec.name or not effective.configured:
        raise WebUISettingsError("Configure the selected speech provider")
    try:
        audio = await synthesize_speech_with_config(text, effective)
    except TtsError as exc:
        raise WebUISettingsError(f"Voice preview failed: {exc.detail}") from exc
    return {
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "mime": "audio/wav" if effective.response_format == "wav" else "audio/mpeg",
        "provider": effective.provider, "model": effective.model, "voice": effective.voice,
    }
