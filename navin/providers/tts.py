# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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
    if provider == "qwen":
        return "wav"  # Qwen TTS answers WAV files
    if provider == "bedrock":
        # Polly delivers MP3, Ogg Vorbis or raw PCM (wrapped as WAV).
        fmt = response_format.strip().lower()
        return "ogg" if fmt in {"ogg", "opus"} else ("wav" if fmt in {"pcm", "wav"} else "mp3")
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
    """TTS via the managed Navin key (Grok Voice, etc.).

    On the Navin gateway (LiteLLM) it is the plain OpenAI-compatible
    ``/audio/speech`` client, without OpenRouter's extras.
    """

    def __new__(
        cls,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        from navin.providers.gateway import navin_managed_base

        # Gateway only, never OpenRouter (see navin.providers.gateway).
        api_base = navin_managed_base(api_base)
        return OpenAICompatibleTtsProvider(
            api_key=api_key,
            api_base=api_base,
            model=model or NAVIN_TTS_MODEL,
            voice=voice or NAVIN_TTS_VOICE,
            response_format=response_format,
            provider_label="Navin",
            default_api_base=api_base or "",
            # Never fall back to the user's own OPENAI_API_KEY on our gateway.
            env_key="",
            default_model=NAVIN_TTS_MODEL,
            default_voice=NAVIN_TTS_VOICE,
        )

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


class BedrockPollyTtsProvider:
    """Speech with the user's own AWS credentials: Amazon Polly (neural or
    generative voices), in the region that serves the engine
    (navin/providers/bedrock_media.py).

    Polly is its own AWS service: it needs ``polly:SynthesizeSpeech`` and IAM
    credentials (an AWS profile or access keys). A Bedrock API key alone only
    opens Bedrock.
    """

    DEFAULT_MODEL = "polly-neural"
    DEFAULT_VOICE = "Lea"
    # Polly bills and caps each request at 3,000 characters.
    MAX_CHARS = 2800

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        from navin.providers.bedrock_media import decode_target, model_region

        self.api_key = api_key
        self.model = (model or self.DEFAULT_MODEL).strip()
        self.engine = "generative" if "generative" in self.model else "neural"
        chosen = (voice or "").strip()
        self.voice = chosen if chosen and chosen.lower() not in {"auto", "alloy"} else self.DEFAULT_VOICE
        self.response_format = (response_format or "mp3").strip().lower()
        default_region, self.profile = decode_target(api_base)
        self.region = model_region(self.model, default_region)

    def _chunks(self, text: str) -> list[str]:
        clean = " ".join(text.split())
        if len(clean) <= self.MAX_CHARS:
            return [clean]
        sentences = re.split(r"(?<=[.!?])\s+", clean)
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            for piece in textwrap.wrap(sentence, self.MAX_CHARS) or [""]:
                if current and len(current) + 1 + len(piece) > self.MAX_CHARS:
                    chunks.append(current)
                    current = piece
                else:
                    current = f"{current} {piece}".strip()
        if current:
            chunks.append(current)
        return chunks

    def _synthesize(self, text: str) -> bytes:
        from navin.providers.bedrock_media import boto_client

        client = boto_client("polly", self.region, self.profile)
        pcm = self.response_format in {"pcm", "wav"}
        out = bytearray()
        for chunk in self._chunks(text):
            response = client.synthesize_speech(
                Text=chunk,
                VoiceId=self.voice,
                Engine=self.engine,
                OutputFormat="pcm" if pcm else ("ogg_vorbis" if self.response_format in {"ogg", "opus"} else "mp3"),
                **({"SampleRate": "16000"} if pcm else {}),
            )
            out.extend(response["AudioStream"].read())
        audio = bytes(out)
        if self.response_format == "wav":
            return pcm16_to_wav(audio, sample_rate=16000, channels=1, bits_per_sample=16)
        return audio

    async def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            return b""
        try:
            import asyncio

            return await asyncio.to_thread(self._synthesize, text)
        except Exception as exc:  # noqa: BLE001 - surfaced as "synthesis failed" by the caller
            logger.warning("Amazon Polly speech failed ({} {} in {}): {}", self.engine, self.voice, self.region, str(exc)[:300])
            return b""


class XaiTtsProvider:
    """Grok voice with the user's own xAI key: ``POST /v1/tts`` (xAI's own
    format: no model id, ``voice_id``, the language detected from the text)."""

    DEFAULT_API_BASE = "https://api.x.ai/v1"
    DEFAULT_VOICE = "eve"
    _CODECS = {"mp3": "mp3", "wav": "wav", "pcm": "pcm"}

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        self.api_base = (api_base or self.DEFAULT_API_BASE).rstrip("/")
        self.model = model or "grok-voice-tts"
        chosen = (voice or "").strip()
        self.voice = chosen if chosen and chosen.lower() not in {"auto", "alloy"} else self.DEFAULT_VOICE
        self.codec = self._CODECS.get((response_format or "mp3").strip().lower(), "mp3")

    async def synthesize(self, text: str) -> bytes:
        if not self.api_key or not text or not text.strip():
            return b""
        body = {
            "text": text.strip(),
            "language": "auto",
            "voice_id": self.voice,
            "output_format": {"codec": self.codec, "sample_rate": 24000},
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.api_base}/tts",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            logger.warning("xAI speech request failed: {}", exc)
            return b""
        if response.status_code >= 400:
            logger.warning("xAI speech failed (HTTP {}): {}", response.status_code, response.text[:300])
            return b""
        return response.content


class OmniRouteTtsProvider(LocalOpenAICompatibleTtsProvider):
    """TTS through the local OmniRoute gateway (``/v1/audio/speech``; models
    are ``provider/model``, e.g. ``openai/tts-1``)."""

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
            provider_label="OmniRoute",
            default_api_base="http://localhost:20128/v1",
            env_key="OMNIROUTE_API_KEY",
            default_model="openai/tts-1",
            default_voice="alloy",
        )


# ---------------------------------------------------------------------------
# Word timestamps for narration (montage captions)
# ---------------------------------------------------------------------------
#
# Captions highlight the word being spoken, so narration needs word timings.
# ElevenLabs returns a character alignment with the audio; OpenAI-compatible
# speech endpoints return none, but their transcription endpoint does
# (``verbose_json`` with word granularity), so the generated clip can be read
# back. Both helpers return ``[{"text", "start", "end"}]`` in seconds and never
# raise for a provider failure: the caller falls back to an estimate.

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
ELEVENLABS_NARRATION_MODEL = "eleven_multilingual_v2"
#: Transcription model with word timestamps per OpenAI-compatible provider.
WORD_TIMESTAMP_MODELS: dict[str, str] = {
    "openai": "whisper-1",
    "groq": "whisper-large-v3-turbo",
}


def is_elevenlabs_base(api_base: str | None) -> bool:
    """True when *api_base* points at the ElevenLabs API."""
    host = urlsplit(api_base or "").hostname or ""
    return host == "elevenlabs.io" or host.endswith(".elevenlabs.io")


def words_from_elevenlabs_alignment(alignment: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Group an ElevenLabs character alignment into words.

    The payload carries ``characters`` with parallel
    ``character_start_times_seconds`` / ``character_end_times_seconds``.
    """
    if not isinstance(alignment, dict):
        return []
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict[str, Any]] = []
    current = ""
    begin = end = 0.0
    for index, char in enumerate(chars):
        if index >= len(starts) or index >= len(ends):
            break
        if str(char).isspace():
            if current:
                words.append({"text": current, "start": round(begin, 3), "end": round(end, 3)})
                current = ""
            continue
        if not current:
            begin = float(starts[index])
        current += str(char)
        end = float(ends[index])
    if current:
        words.append({"text": current, "start": round(begin, 3), "end": round(end, 3)})
    return words


def words_from_verbose_transcription(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Words of an OpenAI-style ``verbose_json`` transcription.

    Top-level ``words`` (OpenAI, Groq) wins; some servers only nest them in
    ``segments[].words``.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("words")
    if not raw:
        raw = [
            word
            for segment in payload.get("segments") or []
            if isinstance(segment, dict)
            for word in segment.get("words") or []
        ]
    words: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("word") or item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        words.append({"text": text, "start": round(start, 3), "end": round(max(start, end), 3)})
    return words


async def elevenlabs_synthesize_with_timestamps(
    text: str,
    *,
    api_key: str,
    voice_id: str,
    model: str | None = None,
    api_base: str | None = None,
    output_format: str = "mp3_44100_128",
) -> tuple[bytes, list[dict[str, Any]]]:
    """Speech plus word timings from ElevenLabs ``/with-timestamps``.

    Returns ``(b"", [])`` on any failure.
    """
    if not api_key or not voice_id or not (text or "").strip():
        return b"", []
    base = (api_base or ELEVENLABS_API_BASE).rstrip("/")
    url = f"{base}/text-to-speech/{voice_id}/with-timestamps"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                params={"output_format": output_format},
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={"text": text.strip(), "model_id": model or ELEVENLABS_NARRATION_MODEL},
            )
        if response.status_code >= 400:
            logger.warning(
                "ElevenLabs timestamped speech failed (HTTP {}): {}",
                response.status_code,
                response.text[:300],
            )
            return b"", []
        payload = response.json()
        audio = base64.b64decode(payload.get("audio_base64") or "", validate=False)
        alignment = payload.get("normalized_alignment") or payload.get("alignment")
        return audio, words_from_elevenlabs_alignment(alignment)
    except Exception as exc:  # noqa: BLE001 - the caller falls back to an estimate
        logger.warning("ElevenLabs timestamped speech failed: {}", type(exc).__name__)
        return b"", []


async def transcribe_word_timestamps(
    audio: bytes,
    *,
    api_key: str,
    api_base: str,
    model: str = "whisper-1",
    filename: str = "speech.mp3",
    language: str | None = None,
    provider_label: str = "OpenAI",
) -> list[dict[str, Any]]:
    """Word timings of *audio* via an OpenAI-compatible transcription endpoint.

    Used as forced alignment for narration we just synthesized: the script is
    known, the transcription only supplies timings. Returns ``[]`` on failure.
    """
    if not audio or not api_key or not api_base:
        return []
    base = api_base.rstrip("/")
    if base.endswith(_SPEECH_PATH):
        base = base[: -len(_SPEECH_PATH)].rstrip("/")
    url = f"{base}/audio/transcriptions"
    data: dict[str, str] = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language:
        data["language"] = language.split("-")[0]
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": (filename, audio)},
            )
        if response.status_code >= 400:
            logger.warning(
                "{} word timestamps failed (HTTP {}): {}",
                provider_label,
                response.status_code,
                response.text[:300],
            )
            return []
        return words_from_verbose_transcription(response.json())
    except Exception as exc:  # noqa: BLE001 - the caller falls back to an estimate
        logger.warning("{} word timestamps failed: {}", provider_label, type(exc).__name__)
        return []
