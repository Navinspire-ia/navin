# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Application-level audio transcription service.

This module owns navin's transcription behavior: config resolution,
legacy channel fallback, upload validation, temporary-file handling, and
dispatch to provider adapters. It deliberately does not know provider-specific
HTTP details; those live in ``navin.providers.transcription``.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.audio.transcription_registry import (
    get_transcription_provider,
    resolve_transcription_provider,
)
from navin.config.paths import get_media_dir
from navin.providers.registry import find_by_name
from navin.utils.media_decode import FileSizeExceeded, save_base64_data_url

TranscriptionProviderName = str

def _default_transcription_provider() -> TranscriptionProviderName:
    from navin.optional_live import live_modules_available

    return "navin" if live_modules_available() else "openrouter"


_DEFAULT_PROVIDER: TranscriptionProviderName = _default_transcription_provider()
_MAX_AUDIO_BYTES_FALLBACK = 25 * 1024 * 1024
_AUDIO_MIME_ALLOWED: frozenset[str] = frozenset({
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
})


@dataclass(frozen=True)
class EffectiveTranscriptionConfig:
    enabled: bool
    provider: TranscriptionProviderName
    model: str
    language: str | None
    api_key: str = field(repr=False)
    api_base: str
    max_duration_sec: int
    max_upload_mb: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class TranscriptionIngressError(Exception):
    """Stable transcription upload error surfaced to WebUI clients."""

    def __init__(self, detail: str, **extra: Any):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


def _as_provider(value: Any) -> TranscriptionProviderName | None:
    spec = resolve_transcription_provider(value)
    return spec.name if spec else None


def _provider_config(config: Any, provider: str) -> Any:
    return getattr(getattr(config, "providers", None), provider, None)


def _provider_default_api_base(provider: str) -> str | None:
    spec = find_by_name(provider)
    return spec.default_api_base if spec else None


def _is_local_transcription_provider(provider: str) -> bool:
    chat_spec = find_by_name(provider)
    return bool(chat_spec and chat_spec.is_local)


def _resolve_transcription_api_key(
    provider: str,
    provider_cfg: Any,
    config: Any = None,
) -> str:
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
    if provider == "siliconflow":
        env_key = os.environ.get("SILICONFLOW_API_KEY")
        if env_key:
            return env_key

    env_key = spec.env_key if spec else ""
    if env_key:
        env_value = os.environ.get(env_key)
        if env_value:
            return env_value
    # Local OpenAI-compat servers (Ollama / vLLM) accept a dummy Bearer token.
    if _is_local_transcription_provider(provider):
        return "local"
    return ""


def _resolve_transcription_api_base(provider: str, provider_cfg: Any) -> str:
    api_base = getattr(provider_cfg, "api_base", None) if provider_cfg else None
    if api_base:
        return api_base
    return _provider_default_api_base(provider) or ""


def _extract_data_url_mime(url: str) -> str | None:
    header, _, _ = url.partition(",")
    if not header.startswith("data:") or ";base64" not in header:
        return None
    return header[5:].split(";", 1)[0].strip().lower() or None


def resolve_transcription_config(config: Any) -> EffectiveTranscriptionConfig:
    """Resolve top-level transcription settings with legacy channel fallback."""
    top = getattr(config, "transcription", None)
    channels = getattr(config, "channels", None)
    provider = (
        _as_provider(getattr(top, "provider", None))
        or _as_provider(getattr(channels, "transcription_provider", None))
        or _DEFAULT_PROVIDER
    )
    spec = get_transcription_provider(provider)
    if spec is None:
        logger.warning("Unknown transcription provider {}; falling back to {}", provider, _DEFAULT_PROVIDER)
        provider = _DEFAULT_PROVIDER
        spec = get_transcription_provider(provider)
    default_model = spec.default_model if spec else ""
    provider_cfg = _provider_config(config, provider)
    api_key = _resolve_transcription_api_key(provider, provider_cfg, config)
    api_base = _resolve_transcription_api_base(provider, provider_cfg)
    model = (getattr(top, "model", None) or default_model).strip()
    # Only legacy automatic settings may fall back to a managed/shared key.
    # Keep an explicit BYOK choice on its provider even when its key is missing.
    explicit_provider = bool(getattr(top, "provider", None))
    if not api_key and not explicit_provider and not _is_local_transcription_provider(provider):
        generic_models = {"whisper-large-v3", "whisper-1", "whisper", ""}
        for fallback in ("navin", "openrouter"):
            if provider == fallback:
                continue
            fb_cfg = _provider_config(config, fallback)
            fb_key = _resolve_transcription_api_key(fallback, fb_cfg, config)
            if not fb_key:
                continue
            fb_spec = get_transcription_provider(fallback)
            provider = fallback
            provider_cfg = fb_cfg
            api_key = fb_key
            api_base = _resolve_transcription_api_base(fallback, fb_cfg)
            if model in generic_models:
                model = (fb_spec.default_model if fb_spec else "") or model
            break
    return EffectiveTranscriptionConfig(
        enabled=bool(getattr(top, "enabled", True)),
        provider=provider,
        model=model,
        language=getattr(top, "language", None) or getattr(channels, "transcription_language", None),
        api_key=api_key,
        api_base=api_base,
        max_duration_sec=int(getattr(top, "max_duration_sec", 120)),
        max_upload_mb=int(getattr(top, "max_upload_mb", 25)),
    )


async def transcribe_audio_data_url(
    data_url: Any,
    config: EffectiveTranscriptionConfig,
    *,
    duration_ms: Any = None,
    partials: list[str] | None = None,
) -> str:
    """Validate, persist, transcribe, and remove a WebUI audio data URL."""
    if not isinstance(data_url, str) or not data_url:
        raise TranscriptionIngressError("missing_audio")
    if not config.enabled:
        raise TranscriptionIngressError("disabled")
    if not config.configured:
        raise TranscriptionIngressError("not_configured", provider=config.provider)
    if (
        isinstance(duration_ms, (int, float))
        and duration_ms > (config.max_duration_sec * 1000 + 1000)
    ):
        raise TranscriptionIngressError("duration")
    if _extract_data_url_mime(data_url) not in _AUDIO_MIME_ALLOWED:
        raise TranscriptionIngressError("mime")

    audio_path: str | None = None
    max_bytes = max(
        1,
        config.max_upload_mb * 1024 * 1024 if config.max_upload_mb else _MAX_AUDIO_BYTES_FALLBACK,
    )
    try:
        audio_path = save_base64_data_url(
            data_url,
            get_media_dir("webui-transcription"),
            max_bytes=max_bytes,
        )
    except FileSizeExceeded as exc:
        raise TranscriptionIngressError("size") from exc
    except Exception as exc:
        logger.warning("transcription audio decode failed: {}", exc)
    if not audio_path:
        raise TranscriptionIngressError("decode")

    try:
        text = await transcribe_audio_file(audio_path, config, partials=partials)
    finally:
        with suppress(OSError):
            Path(audio_path).unlink(missing_ok=True)
    if not text:
        raise TranscriptionIngressError("empty")
    return text


async def transcribe_audio_file(
    file_path: str | Path,
    config: EffectiveTranscriptionConfig,
    *,
    partials: list[str] | None = None,
) -> str:
    """Transcribe *file_path* using the already-resolved transcription config."""
    if not config.enabled or not config.configured:
        return ""
    spec = get_transcription_provider(config.provider)
    if spec is None:
        logger.warning("Unknown transcription provider: {}", config.provider)
        return ""
    provider = spec.load_adapter()(
        api_key=config.api_key,
        api_base=config.api_base or None,
        language=config.language,
        model=config.model,
    )
    stream_method = getattr(provider, "transcribe_with_partials", None)
    if partials is not None and callable(stream_method):
        text, provider_partials = await stream_method(file_path)
        partials.extend(str(value) for value in provider_partials if str(value).strip())
        return text
    return await provider.transcribe(file_path)
