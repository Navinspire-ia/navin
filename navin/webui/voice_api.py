# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Preview a configured speech provider without starting a microphone session."""

from __future__ import annotations

import base64
import dataclasses
from typing import Any

from navin.audio.models import known_voices, teammate_voice, voice_profile
from navin.audio.tts import TtsError, resolve_tts_config, synthesize_speech_with_config
from navin.audio.tts_registry import resolve_tts_provider
from navin.config.loader import load_config
from navin.webui.http_utils import query_first
from navin.webui.settings_api import WebUISettingsError

# One spoken clip of a teammate (the Live page splits longer text).
TEAMMATE_SPEECH_MAX_CHARS = 1200


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


def teammate_voice_for(model: str, main_voice: str, *, chosen: str, gender: str, language: str, key: str) -> str:
    """The voice a teammate speaks with on *model*.

    Its own voice when the model has it (case as the provider spells it),
    else one of its gender, else the lead's voice.
    """
    known = known_voices(model)
    # No language given (the CLI): the one the lead's own voice speaks.
    language = language or voice_profile(main_voice)[1]
    if chosen:
        if not known:
            return chosen
        match = next((voice for voice in known if voice.lower() == chosen.lower()), "")
        if match:
            return match
    if gender in {"female", "male"}:
        picked = teammate_voice(model, gender, language, key=key, avoid=main_voice)
        if picked:
            return picked
    return main_voice


async def teammate_speech_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    """Live: a teammate's words in its own voice, with the saved speech settings.

    Nothing is saved: the voice is only that clip's. The answer says which
    voice spoke, so the page can show it.
    """
    text = (query_first(query, "text") or "").strip()
    chosen = (query_first(query, "voice") or "").strip()
    gender = (query_first(query, "gender") or "").strip().lower()
    language = (query_first(query, "lang") or "").strip()[:12]
    key = (query_first(query, "key") or "").strip()[:48]
    name = (query_first(query, "name") or "").strip()[:40]
    if not text or len(text) > TEAMMATE_SPEECH_MAX_CHARS or len(chosen) > 80:
        raise WebUISettingsError("Invalid teammate speech")
    effective = resolve_tts_config(load_config())
    if not effective.configured:
        raise WebUISettingsError("Configure a speech provider in Settings > Live", status=409)
    voice = teammate_voice_for(
        effective.model, effective.voice, chosen=chosen, gender=gender, language=language, key=key,
    )
    if voice and voice != effective.voice:
        effective = dataclasses.replace(effective, voice=voice)
    elif name:
        # The speech model has no other voice of its gender: it sounds like
        # Navin, so its name comes first and the user knows who speaks.
        text = f"{name}: {text}"
    try:
        audio = await synthesize_speech_with_config(text, effective)
    except TtsError as exc:
        raise WebUISettingsError(f"Teammate voice failed: {exc.detail}", status=502) from exc
    return {
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "mime": "audio/wav" if effective.response_format == "wav" else "audio/mpeg",
        "voice": effective.voice,
    }
