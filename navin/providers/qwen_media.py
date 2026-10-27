# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Alibaba Model Studio (Qwen / Wan) for the media uses with the user's own key.

The Qwen provider stores the OpenAI-compatible base
(``https://dashscope-intl.aliyuncs.com/compatible-mode/v1``, or a workspace
host ``https://<id>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1``).
The media APIs live on the same host under ``/api/v1`` (native DashScope) and
``/api-ws/v1/realtime``; :func:`native_host` derives it, so the region or
workspace chosen for chat is the one used for voice, image and video.

Generated audio, images and videos come back as URLs valid for 24 hours: they
are downloaded at once.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import wave
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from loguru import logger

DEFAULT_HOST = "https://dashscope-intl.aliyuncs.com"
#: Qwen TTS reads at most 600 characters per request.
TTS_MAX_CHARS = 550
QWEN_TTS_VOICES = (
    "Cherry", "Ethan", "Serena", "Chelsie", "Jennifer", "Ryan", "Aiden", "Katerina",
    "Emilien", "Andre", "Vivian", "Bella", "Neil", "Elias", "Arthur", "Kai", "Maia",
)


def native_host(api_base: str | None) -> str:
    """``https://host`` of the DashScope native APIs for the configured base."""
    text = (api_base or "").strip()
    if not text:
        return DEFAULT_HOST
    parts = urlsplit(text if "://" in text else f"https://{text}")
    if not parts.netloc:
        return DEFAULT_HOST
    return f"{parts.scheme or 'https'}://{parts.netloc}"


def api_key_or_env(api_key: str | None) -> str:
    return (api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")
            or os.environ.get("ALIBABA_API_KEY") or "").strip()


def _join_wav(parts: list[bytes]) -> bytes:
    """One WAV from several WAV chunks of the same format."""
    if len(parts) == 1:
        return parts[0]
    frames = bytearray()
    params = None
    for part in parts:
        with wave.open(io.BytesIO(part), "rb") as reader:
            params = params or reader.getparams()
            frames.extend(reader.readframes(reader.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        assert params is not None
        writer.setparams(params)
        writer.writeframes(bytes(frames))
    return out.getvalue()


def _chunks(text: str, limit: int = TTS_MAX_CHARS) -> list[str]:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return [clean]
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", clean):
        while len(sentence) > limit:
            chunks.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + 1 + len(sentence) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


class QwenTtsProvider:
    """Qwen TTS (``qwen3-tts-flash``...) on the multimodal-generation API.
    The answer is a WAV URL per request; long texts are cut at sentence ends
    and the pieces joined back into one WAV."""

    DEFAULT_MODEL = "qwen3-tts-flash"
    DEFAULT_VOICE = "Cherry"

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ):
        self.api_key = api_key_or_env(api_key)
        self.host = native_host(api_base)
        self.model = (model or self.DEFAULT_MODEL).removeprefix("qwen/")
        chosen = (voice or "").strip()
        self.voice = chosen if chosen and chosen.lower() not in {"auto", "alloy"} else self.DEFAULT_VOICE
        self.response_format = "wav"

    async def synthesize(self, text: str) -> bytes:
        if not self.api_key or not text or not text.strip():
            return b""
        url = f"{self.host}/api/v1/services/aigc/multimodal-generation/generation"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        parts: list[bytes] = []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                for chunk in _chunks(text):
                    body = {"model": self.model, "input": {"text": chunk, "voice": self.voice, "language_type": "Auto"}}
                    response = await client.post(url, headers=headers, json=body)
                    if response.status_code >= 400:
                        logger.warning("Qwen speech failed (HTTP {}): {}", response.status_code, response.text[:300])
                        return b""
                    audio = ((response.json().get("output") or {}).get("audio") or {})
                    if audio.get("data"):
                        parts.append(base64.b64decode(audio["data"]))
                        continue
                    audio_url = str(audio.get("url") or "")
                    if not audio_url:
                        logger.warning("Qwen speech returned no audio: {}", response.text[:300])
                        return b""
                    download = await client.get(audio_url)
                    download.raise_for_status()
                    parts.append(download.content)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Qwen speech request failed: {}", exc)
            return b""
        try:
            return _join_wav(parts)
        except (wave.Error, EOFError):
            return b"".join(parts)


class QwenTranscriptionProvider:
    """Qwen3-ASR on the OpenAI-compatible chat API with ``input_audio``
    (a base64 data URL; up to 10 MB and 5 minutes per take)."""

    DEFAULT_MODEL = "qwen3-asr-flash"
    _MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg",
             ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".flac": "audio/flac", ".aac": "audio/aac"}

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key_or_env(api_key)
        self.host = native_host(api_base)
        self.language = language or None
        self.model = (model or self.DEFAULT_MODEL).removeprefix("qwen/")

    async def transcribe(self, file_path: str | Path) -> str:
        if not self.api_key:
            logger.warning("Qwen (Alibaba) API key not configured for transcription")
            return ""
        path = Path(file_path)
        if not path.exists():
            logger.error("Audio file not found: {}", file_path)
            return ""
        mime = self._MIME.get(path.suffix.lower(), "audio/wav")
        audio_bytes = await asyncio.to_thread(path.read_bytes)
        data_url = f"data:{mime};base64,{base64.b64encode(audio_bytes).decode('ascii')}"
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data_url}}]}],
            "stream": False,
            "asr_options": {"enable_itn": False, **({"language": self.language[:2]} if self.language else {})},
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.host}/compatible-mode/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            logger.warning("Qwen transcription request failed: {}", exc)
            return ""
        if response.status_code >= 400:
            logger.warning("Qwen transcription failed (HTTP {}): {}", response.status_code, response.text[:300])
            return ""
        try:
            message = ((response.json().get("choices") or [{}])[0].get("message") or {})
        except (ValueError, IndexError, AttributeError):
            return ""
        content = message.get("content")
        if isinstance(content, list):
            return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict)).strip()
        return str(content or "").strip()
