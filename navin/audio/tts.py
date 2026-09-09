"""Application-level text-to-speech service.

Resolves voice/TTS config, API credentials, and dispatches to provider adapters.
Provider HTTP details live in ``navin.providers.tts``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from navin.audio.models import default_voice
from navin.audio.tts_registry import (
    get_tts_provider,
    resolve_tts_provider,
)
from navin.providers.registry import find_by_name
from navin.providers.tts import delivered_response_format, wire_voice

TtsProviderName = str

def _default_tts_provider() -> TtsProviderName:
    from navin.optional_live import live_modules_available

    return "navin" if live_modules_available() else "openrouter"


_DEFAULT_PROVIDER: TtsProviderName = _default_tts_provider()
_REALTIME_VOICE_PLANS = frozenset({"pro", "ultra", "team"})


@dataclass(frozen=True)
class EffectiveTtsConfig:
    provider: TtsProviderName
    model: str
    voice: str
    auto_speak: bool
    api_key: str = field(repr=False)
    api_base: str
    response_format: str = "mp3"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class TtsError(Exception):
    """Stable TTS error surfaced to WebUI clients."""

    def __init__(self, detail: str, **extra: Any):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


def _as_provider(value: Any) -> TtsProviderName | None:
    spec = resolve_tts_provider(value)
    return spec.name if spec else None


def _provider_config(config: Any, provider: str) -> Any:
    return getattr(getattr(config, "providers", None), provider, None)


def _is_local_tts_provider(provider: str) -> bool:
    chat_spec = find_by_name(provider)
    return bool(chat_spec and chat_spec.is_local)


def _resolve_tts_api_key(provider: str, provider_cfg: Any, config: Any = None) -> str:
    api_key = getattr(provider_cfg, "api_key", None) if provider_cfg else None
    if api_key:
        return api_key
    # Plan Plus+ : la clé managée vit dans license.managed_api_key même si le
    # slot providers.navin n'a pas encore été synchronisé sur ce device.
    if provider == "navin" and config is not None:
        managed = getattr(getattr(config, "license", None), "managed_api_key", "") or ""
        if managed.strip():
            return managed.strip()
    spec = find_by_name(provider)
    env_key = spec.env_key if spec else ""
    if env_key:
        env_value = os.environ.get(env_key)
        if env_value:
            return env_value
    # Local OpenAI-compat servers (Ollama / vLLM) accept a dummy Bearer token.
    if _is_local_tts_provider(provider):
        return "local"
    return ""


def _resolve_tts_api_base(provider: str, provider_cfg: Any) -> str:
    api_base = getattr(provider_cfg, "api_base", None) if provider_cfg else None
    if api_base:
        return api_base
    spec = find_by_name(provider)
    return (spec.default_api_base if spec else "") or ""


def resolve_tts_config(config: Any) -> EffectiveTtsConfig:
    """Resolve top-level voice TTS settings with sensible defaults."""
    voice_cfg = getattr(config, "voice", None)
    provider = (
        _as_provider(getattr(voice_cfg, "tts_provider", None))
        or _DEFAULT_PROVIDER
    )
    spec = get_tts_provider(provider)
    if spec is None:
        logger.warning("Unknown TTS provider {}; falling back to {}", provider, _DEFAULT_PROVIDER)
        provider = _DEFAULT_PROVIDER
        spec = get_tts_provider(provider)
    assert spec is not None
    provider_cfg = _provider_config(config, provider)
    model = (getattr(voice_cfg, "tts_model", None) or spec.default_model).strip()
    voice = (getattr(voice_cfg, "voice", None) or spec.default_voice).strip() or spec.default_voice
    api_key = _resolve_tts_api_key(provider, provider_cfg, config)
    api_base = _resolve_tts_api_base(provider, provider_cfg)
    # Resolve legacy automatic settings, but honor an explicit provider choice.
    # A missing BYOK key must lead to setup, not send speech to another account.
    explicit_provider = bool(getattr(voice_cfg, "tts_provider", None))
    if not api_key and not explicit_provider and not _is_local_tts_provider(provider):
        for fallback in ("navin", "openrouter"):
            if provider == fallback:
                continue
            fb_cfg = _provider_config(config, fallback)
            fb_key = _resolve_tts_api_key(fallback, fb_cfg, config)
            if not fb_key:
                continue
            fb_spec = get_tts_provider(fallback)
            if fb_spec is None:
                continue
            if model == spec.default_model or not model:
                model = fb_spec.default_model
                voice = (getattr(voice_cfg, "voice", None) or fb_spec.default_voice).strip() or fb_spec.default_voice
            provider = fallback
            spec = fb_spec
            api_key = fb_key
            api_base = _resolve_tts_api_base(fallback, fb_cfg)
            break
    requested_format = (getattr(voice_cfg, "response_format", None) or "mp3").strip() or "mp3"
    if voice.lower() == "auto":
        voice = default_voice(model) or spec.default_voice
    voice = wire_voice(model, voice)
    return EffectiveTtsConfig(
        provider=provider,
        model=model,
        voice=voice,
        auto_speak=bool(getattr(voice_cfg, "auto_speak", False)),
        api_key=api_key,
        api_base=api_base,
        # Announce the container the provider adapter actually delivers.
        response_format=delivered_response_format(model, requested_format, provider=provider),
    )


def voice_realtime_allowed(config: Any) -> bool:
    """Live is available with Navin Pro+/Team or configured BYOK speech engines."""
    voice_cfg = getattr(config, "voice", None)
    flag = getattr(voice_cfg, "realtime_enabled", None)
    if flag is True:
        return True
    if flag is False:
        return False
    plan = (getattr(getattr(config, "license", None), "plan", "") or "").strip().lower()
    if plan in _REALTIME_VOICE_PLANS:
        return True
    # BYOK and local speech do not consume a Navin subscription. Both sides
    # must be selected and configured; a chat API key alone is not a voice setup.
    from navin.audio.transcription import resolve_transcription_config

    stt = resolve_transcription_config(config)
    tts = resolve_tts_config(config)
    return bool(getattr(getattr(config, "transcription", None), "provider", None)
                and getattr(voice_cfg, "tts_provider", None)
                and stt.enabled and stt.configured and tts.configured
                and stt.provider != "navin" and tts.provider != "navin")


def live_voice_status(config: Any, *, stt: Any = None, tts: Any = None) -> dict[str, Any]:
    """One readiness contract for the Live button, Settings and WebSocket start."""
    from navin.audio.transcription import resolve_transcription_config

    stt = stt if stt is not None else resolve_transcription_config(config)
    tts = tts if tts is not None else resolve_tts_config(config)
    stt_selected = bool(getattr(getattr(config, "transcription", None), "provider", None))
    tts_selected = bool(getattr(getattr(config, "voice", None), "tts_provider", None))
    # Managed defaults may be resolved before catalog sync writes the fields.
    stt_ready = stt.configured and (stt_selected or stt.provider == "navin")
    tts_ready = tts.configured and (tts_selected or tts.provider == "navin")
    missing: list[str] = []
    if not stt.enabled:
        missing.append("stt_disabled")
    elif not stt_ready:
        missing.append("stt_not_configured")
    if not tts_ready:
        missing.append("tts_not_configured")
    allowed = voice_realtime_allowed(config)
    if getattr(getattr(config, "voice", None), "realtime_enabled", None) is False:
        reason = "voice_disabled"
    elif missing:
        reason = missing[0]
    elif not allowed:
        reason = "plan_required"
    else:
        reason = None
    return {
        "ready": reason is None,
        "reason": reason,
        "missing": missing,
        "mode": "navin" if "navin" in {stt.provider, tts.provider} else "byok",
        "settings_section": "voice",
        "stt": {"provider": stt.provider, "model": getattr(stt, "model", ""),
                "configured": stt_ready, "enabled": stt.enabled},
        "tts": {"provider": tts.provider, "model": tts.model,
                "configured": tts_ready, "voice": tts.voice},
    }


def should_cancel_tts(*, playing: bool, user_speaking: bool) -> bool:
    """Pure barge-in helper: cancel TTS playback when the user starts speaking."""
    return bool(playing) and bool(user_speaking)


async def synthesize_speech(
    text: str,
    provider: str,
    voice: str,
    api_key: str,
    *,
    api_base: str | None = None,
    model: str | None = None,
    response_format: str = "mp3",
    extra_fields: dict[str, Any] | None = None,
) -> bytes:
    """Synthesize *text* to audio bytes (mp3/wav) via an OpenAI-compatible TTS provider."""
    if not isinstance(text, str) or not text.strip():
        raise TtsError("empty_text")
    if not api_key:
        raise TtsError("not_configured", provider=provider)
    spec = resolve_tts_provider(provider) or get_tts_provider(_DEFAULT_PROVIDER)
    if spec is None:
        raise TtsError("unknown_provider", provider=provider)
    adapter = spec.load_adapter()(
        api_key=api_key,
        api_base=api_base or None,
        model=model or spec.default_model,
        voice=voice or spec.default_voice,
        response_format=response_format or "mp3",
    )
    # Clone extras stay off the constructor so existing adapters keep their
    # signature. Only the HTTP helper reads this attribute.
    if extra_fields:
        adapter.extra_fields = extra_fields
    audio = await adapter.synthesize(text)
    if not audio:
        raise TtsError("synthesis_failed", provider=spec.name)
    return audio


async def synthesize_speech_with_config(
    text: str,
    config: EffectiveTtsConfig,
    *,
    extra_fields: dict[str, Any] | None = None,
) -> bytes:
    """Synthesize using an already-resolved TTS config."""
    return await synthesize_speech(
        text,
        config.provider,
        config.voice,
        config.api_key,
        api_base=config.api_base or None,
        model=config.model,
        response_format=config.response_format,
        extra_fields=extra_fields,
    )
