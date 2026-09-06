"""Provider-specific text-to-speech adapters.

OpenAI-compatible ``/audio/speech`` endpoints (OpenAI, Groq, OpenRouter).
Product-level config resolution lives in ``navin.audio.tts``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger

_SPEECH_PATH = "audio/speech"


def _resolve_speech_url(api_base: str | None, default_url: str) -> str:
    if not api_base:
        return default_url
    base = api_base.rstrip("/")
    if base.endswith(_SPEECH_PATH):
        return base
    return f"{base}/{_SPEECH_PATH}"


async def _post_speech(
    url: str,
    *,
    api_key: str,
    model: str,
    voice: str,
    text: str,
    response_format: str,
    provider_label: str,
    extra_headers: dict[str, str] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> bytes:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    # Optional clone/reference fields. Core keys stay owned by this function
    # so an extra cannot silently change the model or the spoken text.
    if extra_fields:
        reserved = {"model", "input", "voice", "response_format"}
        payload.update(
            {key: value for key, value in extra_fields.items() if key not in reserved}
        )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=60.0)
    except Exception as exc:
        logger.exception("{} TTS request failed: {}", provider_label, exc)
        return b""

    if response.status_code >= 400:
        logger.error(
            "{} TTS error {}: {}",
            provider_label,
            response.status_code,
            response.text[:300],
        )
        return b""
    return bytes(response.content)


class OpenAICompatibleTtsProvider:
    """TTS via OpenAI-compatible ``/v1/audio/speech`` (OpenAI, Groq, etc.)."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
        *,
        provider_label: str = "OpenAI",
        default_api_base: str = "https://api.openai.com/v1",
        env_key: str = "OPENAI_API_KEY",
        default_model: str = "tts-1",
        default_voice: str = "alloy",
    ):
        self.api_key = api_key or os.environ.get(env_key)
        self.api_url = _resolve_speech_url(
            api_base,
            f"{default_api_base.rstrip('/')}/{_SPEECH_PATH}",
        )
        self.model = model or default_model
        self.voice = voice or default_voice
        self.response_format = response_format or "mp3"
        self.provider_label = provider_label

    async def synthesize(self, text: str) -> bytes:
        if not self.api_key:
            logger.warning("{} API key not configured for TTS", self.provider_label)
            return b""
        if not text or not text.strip():
            return b""
        return await _post_speech(
            self.api_url,
            api_key=self.api_key,
            model=self.model,
            voice=self.voice,
            text=text.strip(),
            response_format=self.response_format,
            provider_label=self.provider_label,
            extra_fields=getattr(self, "extra_fields", None),
        )


class GroqTtsProvider(OpenAICompatibleTtsProvider):
    """TTS via Groq's OpenAI-compatible speech endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label="Groq",
            default_api_base="https://api.groq.com/openai/v1",
            env_key="GROQ_API_KEY",
            default_model="playai-tts",
            default_voice="Fritz-PlayAI",
        )


class OpenRouterTtsProvider(OpenAICompatibleTtsProvider):
    """TTS via OpenRouter's OpenAI-compatible speech endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
        *,
        provider_label: str = "TTS",
        env_key: str = "OPENROUTER_API_KEY",
        default_model: str = "openai/gpt-4o-mini-tts",
        default_voice: str = "alloy",
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label=provider_label,
            default_api_base="https://openrouter.ai/api/v1",
            env_key=env_key,
            default_model=default_model,
            default_voice=default_voice,
        )

    async def synthesize(self, text: str) -> bytes:
        if not self.api_key:
            logger.warning("{} API key not configured for TTS", self.provider_label)
            return b""
        if not text or not text.strip():
            return b""
        return await _post_speech(
            self.api_url,
            api_key=self.api_key,
            model=self.model,
            voice=self.voice,
            text=text.strip(),
            response_format=self.response_format,
            provider_label=self.provider_label,
            extra_headers={
                "HTTP-Referer": "https://navin.live",
                "X-Title": "Navin",
            },
            extra_fields=getattr(self, "extra_fields", None),
        )


class GeminiTtsProvider(OpenAICompatibleTtsProvider):
    """TTS via Google's Gemini OpenAI-compatible speech endpoint (BYOK Google)."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        super().__init__(
            api_key=api_key or os.environ.get("GOOGLE_API_KEY"),
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label="Gemini",
            default_api_base="https://generativelanguage.googleapis.com/v1beta/openai",
            env_key="GEMINI_API_KEY",
            default_model="gemini-3.1-flash-tts-preview",
            default_voice="Kore",
        )


class LocalOpenAICompatibleTtsProvider(OpenAICompatibleTtsProvider):
    """TTS against a local OpenAI-compatible ``/audio/speech`` server.

    Local stacks (Ollama, vLLM, Speaches, …) rarely require a real API key.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
        *,
        provider_label: str = "Local",
        default_api_base: str = "http://localhost:8000/v1",
        env_key: str = "",
        default_model: str = "tts-1",
        default_voice: str = "alloy",
    ):
        super().__init__(
            api_key=api_key or "local",
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label=provider_label,
            default_api_base=default_api_base,
            env_key=env_key,
            default_model=default_model,
            default_voice=default_voice,
        )


class OllamaTtsProvider(LocalOpenAICompatibleTtsProvider):
    """TTS via Ollama's OpenAI-compatible speech endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label="Ollama",
            default_api_base="http://localhost:11434/v1",
            env_key="OLLAMA_API_KEY",
            default_model="tts",
            default_voice="alloy",
        )


class VllmTtsProvider(LocalOpenAICompatibleTtsProvider):
    """TTS via a vLLM (or similar) OpenAI-compatible speech endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label="vLLM",
            default_api_base="http://localhost:8000/v1",
            env_key="VLLM_API_KEY",
            default_model="tts-1",
            default_voice="alloy",
        )


class LmStudioTtsProvider(LocalOpenAICompatibleTtsProvider):
    """TTS via LM Studio's OpenAI-compatible speech endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label="LM Studio",
            default_api_base="http://localhost:1234/v1",
            env_key="LM_STUDIO_API_KEY",
            default_model="tts-1",
            default_voice="alloy",
        )


class NavinTtsProvider(OpenRouterTtsProvider):
    """TTS via the managed Navin OpenRouter key (Grok Voice, etc.)."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            model=model,
            voice=voice,
            response_format=response_format,
            provider_label="Navin",
            env_key="",
            default_model="google/gemini-3.1-flash-tts-preview",
            default_voice="eve",
        )
