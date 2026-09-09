"""Music generation via OpenRouter Lyria (chat completions + audio output)."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

_DEFAULT_TIMEOUT_S = 180.0
# Hard wall-clock cap for one generation (stream + fallback). Without it a
# provider that keeps the SSE stream alive with keep-alives can hang forever,
# because the httpx timeout only bounds each individual read.
_DEFAULT_MAX_WAIT_S = 420.0
_OPENROUTER_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/Navinspire-ia/navin",
    "X-OpenRouter-Title": "navin",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}
_DATA_AUDIO_RE = re.compile(
    r"data:(audio/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)

_MUSIC_GEN_PROVIDERS: dict[str, type[MusicGenerationProvider]] = {}


class MusicGenerationError(RuntimeError):
    """Raised when the music generation provider cannot return audio."""


@dataclass(frozen=True)
class GeneratedMusicResponse:
    audio: bytes
    mime: str = "audio/mpeg"
    content: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def register_music_gen_provider(cls: type[MusicGenerationProvider]) -> None:
    name = cls.provider_name
    if not name:
        raise ValueError(f"{cls.__name__} must set provider_name")
    _MUSIC_GEN_PROVIDERS[name] = cls


def get_music_gen_provider(name: str) -> type[MusicGenerationProvider] | None:
    return _MUSIC_GEN_PROVIDERS.get(name)


def music_gen_provider_names() -> tuple[str, ...]:
    return tuple(_MUSIC_GEN_PROVIDERS)


class MusicGenerationProvider(ABC):
    provider_name: str = ""
    missing_key_message: str = ""
    default_timeout: float = _DEFAULT_TIMEOUT_S
    default_max_wait: float = _DEFAULT_MAX_WAIT_S

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.api_base = (api_base or "https://openrouter.ai/api/v1").rstrip("/")
        self.extra_headers = dict(extra_headers or {})
        self.extra_body = dict(extra_body or {})
        self.proxy = proxy

    @abstractmethod
    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
    ) -> GeneratedMusicResponse: ...


def _detect_audio_mime(raw: bytes) -> str:
    if raw[:3] == b"ID3" or (len(raw) > 1 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return "audio/wav"
    if raw[:4] == b"OggS":
        return "audio/ogg"
    if raw[:4] == b"fLaC":
        return "audio/flac"
    return "audio/mpeg"


def _decode_b64_audio(value: str) -> bytes | None:
    try:
        return base64.b64decode("".join(value.split()), validate=False)
    except Exception:
        return None


def _extract_audio_from_payload(data: dict[str, Any]) -> tuple[bytes, str, str] | None:
    """Return (audio_bytes, mime, transcript) when present in a chat payload."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice0 = choices[0] if isinstance(choices[0], dict) else {}
    message = choice0.get("message") if isinstance(choice0, dict) else None
    if not isinstance(message, dict):
        message = choice0.get("delta") if isinstance(choice0, dict) else None
    if not isinstance(message, dict):
        return None

    transcript_parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        transcript_parts.append(content.strip())
        match = _DATA_AUDIO_RE.search(content)
        if match:
            raw = _decode_b64_audio(match.group(2))
            if raw:
                return raw, match.group(1).lower(), " ".join(transcript_parts)

    audio = message.get("audio")
    if isinstance(audio, dict):
        if isinstance(audio.get("transcript"), str) and audio["transcript"].strip():
            transcript_parts.append(audio["transcript"].strip())
        data_b64 = audio.get("data")
        if isinstance(data_b64, str) and data_b64:
            raw = _decode_b64_audio(data_b64)
            if raw:
                fmt = str(audio.get("format") or "mp3").lower()
                mime = "audio/wav" if fmt == "wav" else "audio/mpeg"
                return raw, mime, " ".join(transcript_parts)

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                transcript_parts.append(part["text"].strip())
            if part.get("type") in {"input_audio", "audio", "output_audio"}:
                nested = part.get("input_audio") or part.get("audio") or part
                if isinstance(nested, dict):
                    data_b64 = nested.get("data")
                    if isinstance(data_b64, str) and data_b64:
                        raw = _decode_b64_audio(data_b64)
                        if raw:
                            fmt = str(nested.get("format") or "mp3").lower()
                            mime = "audio/wav" if fmt == "wav" else "audio/mpeg"
                            return raw, mime, " ".join(transcript_parts)

    return None


class OpenRouterMusicGenerationClient(MusicGenerationProvider):
    """Lyria via OpenRouter chat completions (audio modalities)."""

    provider_name = "openrouter"
    missing_key_message = (
        "OpenRouter API key is not configured. Add it under Providers, "
        "or switch Music settings to Navin."
    )

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
    ) -> GeneratedMusicResponse:
        if not self.api_key:
            raise MusicGenerationError(self.missing_key_message)
        try:
            async with asyncio.timeout(self.default_max_wait):
                return await self._generate(
                    prompt=prompt, model=model, reference_image=reference_image
                )
        except TimeoutError:
            raise MusicGenerationError(
                "Music generation did not complete within "
                f"{int(self.default_max_wait)}s. Try again, shorten the request, "
                "or switch the Music model."
            ) from None

    async def _generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
    ) -> GeneratedMusicResponse:
        content: list[dict[str, Any]] | str
        if reference_image:
            from navin.providers.image_generation import image_path_to_data_url

            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_path_to_data_url(reference_image)},
                },
            ]
        else:
            content = prompt

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["text", "audio"],
            "stream": True,
            **self.extra_body,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **_OPENROUTER_ATTRIBUTION_HEADERS,
            **self.extra_headers,
        }
        url = f"{self.api_base}/chat/completions"

        audio_chunks: list[str] = []
        transcript_chunks: list[str] = []
        last_payload: dict[str, Any] = {}

        async with httpx.AsyncClient(
            timeout=self.default_timeout,
            proxy=self.proxy or None,
        ) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code >= 400:
                    text = (await response.aread()).decode("utf-8", errors="replace")[:800]
                    raise MusicGenerationError(
                        f"Music generation failed ({response.status_code}): {text}"
                    )
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    last_payload = chunk
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                    if not isinstance(delta, dict):
                        continue
                    audio = delta.get("audio")
                    if isinstance(audio, dict):
                        if isinstance(audio.get("data"), str) and audio["data"]:
                            audio_chunks.append(audio["data"])
                        if isinstance(audio.get("transcript"), str) and audio["transcript"]:
                            transcript_chunks.append(audio["transcript"])
                    extracted = _extract_audio_from_payload(
                        {"choices": [{"message": delta}]}
                    )
                    if extracted and not audio_chunks:
                        raw, mime, text = extracted
                        return GeneratedMusicResponse(
                            audio=raw,
                            mime=mime or _detect_audio_mime(raw),
                            content=text or " ".join(transcript_chunks),
                            raw=last_payload,
                        )

        if audio_chunks:
            raw = _decode_b64_audio("".join(audio_chunks))
            if raw:
                return GeneratedMusicResponse(
                    audio=raw,
                    mime=_detect_audio_mime(raw),
                    content="".join(transcript_chunks),
                    raw=last_payload,
                )

        # Non-stream fallback for providers that ignore stream audio.
        body_ns = {**body, "stream": False}
        async with httpx.AsyncClient(
            timeout=self.default_timeout,
            proxy=self.proxy or None,
        ) as client:
            response = await client.post(url, headers=headers, json=body_ns)
            if response.status_code >= 400:
                raise MusicGenerationError(
                    f"Music generation failed ({response.status_code}): "
                    f"{response.text[:800]}"
                )
            try:
                data = response.json()
            except Exception as exc:
                raise MusicGenerationError("Music generation returned invalid JSON") from exc
            if not isinstance(data, dict):
                raise MusicGenerationError("Music generation returned unexpected payload")
            extracted = _extract_audio_from_payload(data)
            if extracted:
                raw, mime, text = extracted
                return GeneratedMusicResponse(
                    audio=raw,
                    mime=mime or _detect_audio_mime(raw),
                    content=text,
                    raw=data,
                )
            logger.error("Music generation payload without audio: {}", str(data)[:500])
            raise MusicGenerationError(
                "Music generation completed without audio bytes. "
                "Check the Lyria model id and OpenRouter account access."
            )


class NavinMusicGenerationClient(OpenRouterMusicGenerationClient):
    """Music generation via the managed Navin OpenRouter key (Lyria)."""

    provider_name = "navin"
    missing_key_message = (
        "Navin managed key is not configured. Connect a paid plan, "
        "or switch Music settings to your own provider."
    )


register_music_gen_provider(OpenRouterMusicGenerationClient)
from navin.optional_live import live_modules_available

if live_modules_available():
    register_music_gen_provider(NavinMusicGenerationClient)
