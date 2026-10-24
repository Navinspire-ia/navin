# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The model chosen for each media use (image, video, listening, voice, Live):
where it is stored, and whether its provider can serve it now."""

from __future__ import annotations

from typing import Any

MEDIA_USES = ("image", "video", "tts", "stt", "live")
# The Live realtime bridge speaks to these upstreams only (navin/live/realtime.py).
LIVE_PROVIDERS = frozenset({"navin", "openai", "gemini", "bedrock", "xai", "qwen"})


def media_kind_of_preset(preset: Any) -> str | None:
    """image / video / tts / stt / live for a model preset, else None."""
    from navin.providers.media_models import is_realtime_voice_model, media_model_kinds

    modality = (getattr(preset, "modality", None) or "").strip().lower()
    if modality == "audio":
        return "tts"
    if modality in {"image", "video", "stt", "live"}:
        return modality
    model = str(getattr(preset, "model", "") or "")
    if is_realtime_voice_model(model):
        return "live"
    kinds = [kind for kind in media_model_kinds(model) if kind in MEDIA_USES]
    return kinds[0] if kinds else None


def provider_usable(config: Any, provider: str) -> bool:
    """The provider can serve a request now: Navin with a plan key, others with their key (or local base)."""
    provider = (provider or "").strip().lower()
    if not provider:
        return False
    if provider == "navin":
        navin = getattr(config.providers, "navin", None)
        return bool(
            (getattr(config.license, "managed_api_key", "") or "").strip()
            or (getattr(navin, "api_key", "") or "").strip()
        )
    from navin.providers.media_credentials import media_credentials_ready

    slot = getattr(config.providers, provider, None)
    if slot is None:
        slot = (getattr(config.providers, "model_extra", None) or {}).get(provider)
    return media_credentials_ready(provider, slot)


def media_selected(config: Any, kind: str) -> tuple[str, str]:
    if kind == "image":
        return (config.tools.image_generation.provider or ""), (config.tools.image_generation.model or "")
    if kind == "video":
        return (config.tools.video_generation.provider or ""), (config.tools.video_generation.model or "")
    if kind == "tts":
        return (config.voice.tts_provider or ""), (config.voice.tts_model or "")
    if kind == "stt":
        return (config.transcription.provider or ""), (config.transcription.model or "")
    return (config.voice.live_realtime_provider or ""), (config.voice.live_realtime_model or "")


def normalize_media_model(kind: str, provider: str, model: str) -> str:
    """Gemini's native ids carry ``models/``; the adapters (and the Live
    translator) add it themselves, so one model has one id everywhere."""
    if provider == "gemini":
        return model.removeprefix("models/")
    return model


def set_media(config: Any, kind: str, provider: str, model: str) -> None:
    model = normalize_media_model(kind, provider, model)
    if kind == "image":
        config.tools.image_generation.provider = provider
        config.tools.image_generation.model = model
    elif kind == "video":
        config.tools.video_generation.provider = provider
        config.tools.video_generation.model = model
    elif kind == "tts":
        if config.voice.tts_provider != provider:
            # A voice name belongs to its provider (Kore is Gemini's, alloy OpenAI's).
            config.voice.voice = "auto"
        config.voice.tts_provider = provider
        config.voice.tts_model = model
    elif kind == "stt":
        config.transcription.provider = provider
        config.transcription.model = model
    else:
        config.voice.live_realtime_provider = provider
        config.voice.live_realtime_model = model
