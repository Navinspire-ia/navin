"""Provider-specific text-to-speech adapters.

OpenAI-compatible ``/audio/speech`` endpoints (OpenAI, Groq, OpenRouter).
Product-level config resolution lives in ``navin.audio.tts``.
"""

from __future__ import annotations

import base64
import io
import os
import re
import struct
import textwrap
import wave
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger

from navin.audio.models import (
    GROQ_TTS_MODEL,
    GROQ_TTS_VOICE,
    NAVIN_TTS_MODEL,
    NAVIN_TTS_VOICE,
    ORPHEUS_VOICES,
    QWEN_SPEECH_VOICES,
)

_SPEECH_PATH = "audio/speech"

# Gemini TTS models (BYOK Google or through OpenRouter / the managed Navin key)
# only speak raw PCM: asking for mp3 is a 400. We request pcm and wrap it in a
# WAV container the browser and every downstream tool can play.
_GEMINI_TTS_MODEL_RE = re.compile(r"(?:^|/)gemini-[\w.-]*tts", re.IGNORECASE)
GEMINI_TTS_PCM_SAMPLE_RATE = 24_000
GEMINI_TTS_PCM_CHANNELS = 1
GEMINI_TTS_PCM_BITS = 16


# Prebuilt Gemini TTS voices (case-insensitive on the wire, canonical case kept).
GEMINI_TTS_VOICES: tuple[str, ...] = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
_GEMINI_VOICE_BY_LOWER = {name.lower(): name for name in GEMINI_TTS_VOICES}
GEMINI_TTS_DEFAULT_VOICE = "Kore"
# The product voice picker lists Grok / OpenAI names (eve, ara, ...). When the
# model is Gemini TTS those names are rejected upstream, so map each one to the
# closest Gemini voice instead of failing the whole synthesis.
_GEMINI_VOICE_ALIASES: dict[str, str] = {
    "eve": "Kore",
    "ara": "Aoede",
    "rex": "Charon",
    "sal": "Puck",
    "leo": "Fenrir",
    "alloy": "Kore",
    "ash": "Charon",
    "ballad": "Enceladus",
    "coral": "Leda",
    "echo": "Orus",
    "fable": "Zephyr",
    "nova": "Aoede",
    "onyx": "Fenrir",
    "sage": "Callirrhoe",
    "shimmer": "Puck",
}


def is_gemini_tts_model(model: str | None) -> bool:
    """True for ``gemini-*-tts*`` slugs, with or without a ``google/`` prefix."""
    return bool(model) and _GEMINI_TTS_MODEL_RE.search(str(model).strip()) is not None


def gemini_voice_for(voice: str | None) -> str:
    """Voice name Gemini TTS accepts for *voice* (native, aliased, or the default)."""
    key = (voice or "").strip().lower()
    if not key:
        return GEMINI_TTS_DEFAULT_VOICE
    native = _GEMINI_VOICE_BY_LOWER.get(key)
    if native:
        return native
    return _GEMINI_VOICE_ALIASES.get(key, GEMINI_TTS_DEFAULT_VOICE)


def wire_voice(model: str | None, voice: str) -> str:
    """Voice actually sent to the provider for *model*."""
    if is_gemini_tts_model(model):
        return gemini_voice_for(voice)
    slug = (model or "").lower().rsplit("/", 1)[-1]
    for prefix, voices in QWEN_SPEECH_VOICES.items():
        if slug.startswith(prefix):
            native = next((v for v in voices if v.lower() == voice.lower()), None)
            # An old Grok/Gemini voice must not make a new Qwen default fail.
            return native or voices[0]
    return voice


def wire_response_format(model: str | None, response_format: str) -> str:
    """Format actually sent to the provider (Gemini TTS only accepts pcm)."""
    return "pcm" if is_gemini_tts_model(model) else response_format


def delivered_response_format(model: str | None, response_format: str, *, provider: str = "") -> str:
    """Format the caller receives: Gemini PCM is delivered as a WAV file."""
    if is_gemini_tts_model(model) and response_format.strip().lower() != "pcm":
        return "wav"
    if provider == "groq" and (model or "").rsplit("/", 1)[-1] in ORPHEUS_VOICES:
        return "wav"
    return response_format


def pcm16_to_wav(
    pcm: bytes,
    *,
    sample_rate: int = GEMINI_TTS_PCM_SAMPLE_RATE,
    channels: int = GEMINI_TTS_PCM_CHANNELS,
    bits_per_sample: int = GEMINI_TTS_PCM_BITS,
) -> bytes:
    """Prepend a canonical 44-byte RIFF/WAVE header to raw little-endian PCM."""
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        len(pcm),
    )
    return header + pcm


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
    requested_format = (response_format or "mp3").strip().lower() or "mp3"
    wrap_pcm = is_gemini_tts_model(model) and requested_format != "pcm"
    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "voice": wire_voice(model, voice),
        "response_format": wire_response_format(model, requested_format),
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
    content = bytes(response.content)
    if wrap_pcm and content and not content.startswith(b"RIFF"):
        content = pcm16_to_wav(content)
    return content


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
            default_model=GROQ_TTS_MODEL,
            default_voice=GROQ_TTS_VOICE,
        )
        if self.model.rsplit("/", 1)[-1] in ORPHEUS_VOICES:
            self.response_format = "wav"

    async def synthesize(self, text: str) -> bytes:
        if self.model.rsplit("/", 1)[-1] not in ORPHEUS_VOICES or len(text.strip()) <= 200:
            return await super().synthesize(text)
        # Groq Orpheus accepts at most 200 characters. Join PCM frames in a
        # single WAV container so previews and longer Live replies play fully.
        chunks = textwrap.wrap(text, width=200, break_on_hyphens=False)
        frames: list[bytes] = []
        audio_format: tuple[int, int, int] | None = None
        for chunk in chunks:
            audio = await super().synthesize(chunk)
            if not audio:
                return b""
            with wave.open(io.BytesIO(audio), "rb") as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if audio_format is not None and current != audio_format:
                    raise ValueError("Speech chunks have inconsistent audio formats")
                audio_format = current
                frames.append(source.readframes(source.getnframes()))
        if audio_format is None:
            return b""
        result = io.BytesIO()
        with wave.open(result, "wb") as output:
            output.setnchannels(audio_format[0])
            output.setsampwidth(audio_format[1])
            output.setframerate(audio_format[2])
            output.writeframes(b"".join(frames))
        return result.getvalue()


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
        default_model: str = NAVIN_TTS_MODEL,
        default_voice: str = NAVIN_TTS_VOICE,
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
    """Native Gemini speech, with OpenAI-compatible support for custom gateways."""

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

    async def synthesize(self, text: str) -> bytes:
        endpoint = urlsplit(self.api_url)
        if endpoint.hostname != "generativelanguage.googleapis.com":
            return await super().synthesize(text)
        if not self.api_key or not text or not text.strip():
            return b""
        version = re.search(r"/(v1(?:beta|alpha)?)(?:/|$)", endpoint.path)
        api_version = version.group(1) if version else "v1beta"
        url = f"{endpoint.scheme}://{endpoint.netloc}/{api_version}/interactions"
        payload = {
            "model": self.model.removeprefix("google/").removeprefix("models/"),
            "input": text.strip(),
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": gemini_voice_for(self.voice)}]},
            "store": False,
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=60.0,
                )
            if response.status_code >= 400:
                logger.error("Gemini TTS error {}: {}", response.status_code, response.text[:300])
                return b""
            result = response.json()
            parts = [
                part
                for step in result.get("steps", [])
                if step.get("type") == "model_output"
                for part in step.get("content", [])
            ]
            # Earlier Interactions revisions exposed the same content blocks
            # under outputs. Accept both during Google's API rollout.
            if not parts:
                parts = result.get("outputs", [])
            pcm = b"".join(
                base64.b64decode(part["data"], validate=True)
                for part in parts
                if part.get("type") == "audio" and part.get("data")
            )
            if not pcm or self.response_format == "pcm" or pcm.startswith(b"RIFF"):
                return pcm
            return pcm16_to_wav(pcm)
        except Exception as exc:
            logger.warning("Gemini TTS request failed: {}", type(exc).__name__)
            return b""


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
            default_model=NAVIN_TTS_MODEL,
            default_voice=NAVIN_TTS_VOICE,
        )
