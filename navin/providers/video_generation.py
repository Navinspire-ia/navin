# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Video generation provider helpers.

Mirrors the image generation provider architecture: a small registry of
async clients, one per provider, all returning raw video bytes that the
tool layer persists as artifacts.

Supported providers:
- ``gemini``  - Google Veo via the Generative Language API (predictLongRunning)
- ``openai``  - OpenAI Sora via the ``/v1/videos`` API
- ``minimax`` - MiniMax Hailuo via ``/v1/video_generation`` task polling
- ``openrouter`` / ``navin`` - OpenRouter ``/v1/videos`` (Seedance, Veo, …)
- ``ollama`` / ``vllm`` / ``lm_studio`` - local OpenAI-compatible ``/v1/videos`` servers
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from navin.providers.image_generation import (
    image_path_to_data_url,
    image_path_to_inline_data,
)
from navin.providers.registry import find_by_name

_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_POLL_INTERVAL_S = 5.0
_DEFAULT_MAX_WAIT_S = 600.0

_OPENROUTER_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/Navinspire-ia/navin",
    "X-OpenRouter-Title": "navin",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}

_VEO_ASPECT_RATIOS = {"16:9", "9:16"}
_SORA_ASPECT_RATIO_SIZES = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "1:1": "720x720",
}
_MINIMAX_RESOLUTIONS = {"512P", "768P", "1080P"}


class VideoGenerationError(RuntimeError):
    """Raised when the video generation provider cannot return a video."""


@dataclass(frozen=True)
class GeneratedVideoResponse:
    """A generated video returned by the provider."""

    video: bytes
    mime: str = "video/mp4"
    content: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_VIDEO_GEN_PROVIDERS: dict[str, type[VideoGenerationProvider]] = {}


def register_video_gen_provider(cls: type[VideoGenerationProvider]) -> None:
    """Register a video provider at import time only."""
    name = cls.provider_name
    if not name:
        raise ValueError(f"{cls.__name__} must set provider_name")
    _VIDEO_GEN_PROVIDERS[name] = cls


def get_video_gen_provider(name: str) -> type[VideoGenerationProvider] | None:
    return _VIDEO_GEN_PROVIDERS.get(name)


def video_gen_provider_names() -> tuple[str, ...]:
    """Return registered video generation provider names in registry order."""
    return tuple(_VIDEO_GEN_PROVIDERS)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class VideoGenerationProvider(ABC):
    """Base class for video generation provider clients."""

    provider_name: str = ""
    missing_key_message: str = ""
    default_timeout: float = _DEFAULT_TIMEOUT_S

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
        max_wait: float | None = None,
        poll_interval: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = self._resolve_base_url(api_base)
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.proxy = proxy or None
        self.timeout = timeout if timeout is not None else self.default_timeout
        self.max_wait = max_wait if max_wait is not None else _DEFAULT_MAX_WAIT_S
        self.poll_interval = (
            poll_interval if poll_interval is not None else _DEFAULT_POLL_INTERVAL_S
        )
        self._client = client

    def _resolve_base_url(self, api_base: str | None) -> str:
        if api_base:
            return api_base.rstrip("/")
        spec = find_by_name(self.provider_name)
        if spec and spec.default_api_base:
            return spec.default_api_base.rstrip("/")
        return self._default_base_url()

    def _default_base_url(self) -> str:
        return ""

    @abstractmethod
    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
    ) -> GeneratedVideoResponse: ...

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            kwargs["proxy"] = self.proxy
            kwargs["trust_env"] = False
        return kwargs

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        error_label: str,
    ) -> httpx.Response:
        try:
            response = await client.request(method, url, headers=headers, json=json_body)
        except httpx.TimeoutException as exc:
            raise VideoGenerationError(f"{error_label} timed out") from exc
        except httpx.RequestError as exc:
            raise VideoGenerationError(f"{error_label} request failed: {exc}") from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:800]
            raise VideoGenerationError(
                f"{error_label} failed (HTTP {response.status_code}): {detail}"
            ) from exc
        return response

    async def _poll(self, check, *, error_label: str):
        """Poll ``check()`` until it returns a non-None value or max_wait elapses."""
        waited = 0.0
        while True:
            result = await check()
            if result is not None:
                return result
            if waited >= self.max_wait:
                raise VideoGenerationError(
                    f"{error_label} did not complete within {int(self.max_wait)}s"
                )
            await asyncio.sleep(self.poll_interval)
            waited += self.poll_interval


def _detect_video_mime(raw: bytes, fallback: str = "video/mp4") -> str:
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        return "video/mp4"
    if raw.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    return fallback


# ---------------------------------------------------------------------------
# Gemini / Veo
# ---------------------------------------------------------------------------


class GeminiVideoGenerationClient(VideoGenerationProvider):
    """Google Veo video generation via the Generative Language API."""

    provider_name = "gemini"
    missing_key_message = (
        "Gemini API key is not configured. Set providers.gemini.apiKey."
    )

    def _default_base_url(self) -> str:
        return "https://generativelanguage.googleapis.com/v1beta"

    def _resolve_base_url(self, api_base: str | None) -> str:
        # Video generation must hit the native Generative Language API, so we
        # intentionally bypass the shared registry lookup (which points at the
        # OpenAI-compatible chat shim).
        if api_base:
            return api_base.rstrip("/")
        return self._default_base_url()

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
    ) -> GeneratedVideoResponse:
        if not self.api_key:
            raise VideoGenerationError(self.missing_key_message)

        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        instance: dict[str, Any] = {"prompt": prompt}
        if reference_image:
            inline = image_path_to_inline_data(reference_image)
            instance["image"] = {
                "bytesBase64Encoded": inline["data"],
                "mimeType": inline["mimeType"],
            }

        parameters: dict[str, Any] = {}
        if aspect_ratio in _VEO_ASPECT_RATIOS:
            parameters["aspectRatio"] = aspect_ratio
        if resolution:
            parameters["resolution"] = resolution
        if duration_seconds:
            parameters["durationSeconds"] = int(duration_seconds)

        body: dict[str, Any] = {"instances": [instance]}
        if parameters:
            body["parameters"] = parameters
        body.update(self.extra_body)

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            start = await self._request(
                client,
                "POST",
                f"{self.api_base}/models/{model}:predictLongRunning",
                headers=headers,
                json_body=body,
                error_label="Veo video generation",
            )
            operation = start.json()
            op_name = operation.get("name")
            if not op_name:
                raise VideoGenerationError(
                    f"Veo did not return an operation name: {str(operation)[:500]}"
                )

            async def check():
                status = await self._request(
                    client,
                    "GET",
                    f"{self.api_base}/{op_name}",
                    headers=headers,
                    error_label="Veo operation polling",
                )
                data = status.json()
                if data.get("done"):
                    return data
                return None

            data = await self._poll(check, error_label="Veo video generation")

        error = data.get("error")
        if error:
            raise VideoGenerationError(f"Veo video generation failed: {error}")

        uri = _veo_video_uri(data)
        if not uri:
            raise VideoGenerationError(
                f"Veo returned no video in the operation response: {str(data)[:500]}"
            )

        raw = await self._download(uri, headers={"x-goog-api-key": self.api_key})
        return GeneratedVideoResponse(video=raw, mime=_detect_video_mime(raw), raw=data)

    async def _download(self, uri: str, *, headers: dict[str, str]) -> bytes:
        async with httpx.AsyncClient(
            **{**self._client_kwargs(), "follow_redirects": True}
        ) as client:
            response = await client.get(uri, headers=headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise VideoGenerationError(
                    f"failed to download Veo video: {response.text[:300]}"
                ) from exc
            return response.content


def _veo_video_uri(operation: dict[str, Any]) -> str | None:
    response = operation.get("response") or {}
    gen = response.get("generateVideoResponse") or response
    samples = gen.get("generatedSamples") or gen.get("generated_videos") or []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        video = sample.get("video") or {}
        uri = video.get("uri") if isinstance(video, dict) else None
        if isinstance(uri, str) and uri:
            return uri
    return None


# ---------------------------------------------------------------------------
# OpenAI / Sora
# ---------------------------------------------------------------------------


class OpenAIVideoGenerationClient(VideoGenerationProvider):
    """OpenAI Sora video generation via the ``/videos`` API."""

    provider_name = "openai"
    missing_key_message = (
        "OpenAI API key is not configured. Set providers.openai.apiKey."
    )

    def _default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
    ) -> GeneratedVideoResponse:
        if not self.api_key:
            raise VideoGenerationError(self.missing_key_message)
        if reference_image:
            logger.warning(
                "Sora reference images are not supported by this client; "
                "ignoring reference image {}",
                reference_image,
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        body: dict[str, Any] = {"model": model, "prompt": prompt}
        size = _sora_size(aspect_ratio, resolution)
        if size:
            body["size"] = size
        if duration_seconds:
            body["seconds"] = str(int(duration_seconds))
        body.update(self.extra_body)

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            start = await self._request(
                client,
                "POST",
                f"{self.api_base}/videos",
                headers=headers,
                json_body=body,
                error_label="Sora video generation",
            )
            job = start.json()
            video_id = job.get("id")
            if not video_id:
                raise VideoGenerationError(
                    f"Sora did not return a video id: {str(job)[:500]}"
                )

            async def check():
                status = await self._request(
                    client,
                    "GET",
                    f"{self.api_base}/videos/{video_id}",
                    headers=headers,
                    error_label="Sora status polling",
                )
                data = status.json()
                state = data.get("status")
                if state == "completed":
                    return data
                if state in {"failed", "cancelled"}:
                    detail = data.get("error") or state
                    raise VideoGenerationError(f"Sora video generation failed: {detail}")
                return None

            data = await self._poll(check, error_label="Sora video generation")

            content = await self._request(
                client,
                "GET",
                f"{self.api_base}/videos/{video_id}/content",
                headers={"Authorization": f"Bearer {self.api_key}", **self.extra_headers},
                error_label="Sora video download",
            )
            raw = content.content

        return GeneratedVideoResponse(video=raw, mime=_detect_video_mime(raw), raw=data)


def _sora_size(aspect_ratio: str | None, resolution: str | None) -> str | None:
    if resolution and "x" in resolution.lower():
        return resolution
    if aspect_ratio and aspect_ratio in _SORA_ASPECT_RATIO_SIZES:
        return _SORA_ASPECT_RATIO_SIZES[aspect_ratio]
    return None


# ---------------------------------------------------------------------------
# MiniMax / Hailuo
# ---------------------------------------------------------------------------


class MiniMaxVideoGenerationClient(VideoGenerationProvider):
    """MiniMax Hailuo video generation via the task-based API."""

    provider_name = "minimax"
    missing_key_message = (
        "MiniMax API key is not configured. Set providers.minimax.apiKey."
    )

    def _default_base_url(self) -> str:
        return "https://api.minimaxi.com/v1"

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
    ) -> GeneratedVideoResponse:
        if not self.api_key:
            raise VideoGenerationError(self.missing_key_message)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        body: dict[str, Any] = {"model": model, "prompt": prompt}
        if duration_seconds:
            body["duration"] = int(duration_seconds)
        if resolution and resolution.upper() in _MINIMAX_RESOLUTIONS:
            body["resolution"] = resolution.upper()
        if reference_image:
            from navin.providers.image_generation import image_path_to_data_url

            body["first_frame_image"] = image_path_to_data_url(reference_image)
        body.update(self.extra_body)

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            start = await self._request(
                client,
                "POST",
                f"{self.api_base}/video_generation",
                headers=headers,
                json_body=body,
                error_label="MiniMax video generation",
            )
            job = start.json()
            task_id = job.get("task_id")
            if not task_id:
                raise VideoGenerationError(
                    f"MiniMax did not return a task id: {str(job)[:500]}"
                )

            async def check():
                status = await self._request(
                    client,
                    "GET",
                    f"{self.api_base}/query/video_generation?task_id={task_id}",
                    headers=headers,
                    error_label="MiniMax status polling",
                )
                data = status.json()
                state = str(data.get("status", "")).lower()
                if state == "success":
                    return data
                if state == "fail":
                    detail = (data.get("base_resp") or {}).get("status_msg") or "unknown error"
                    raise VideoGenerationError(f"MiniMax video generation failed: {detail}")
                return None

            data = await self._poll(check, error_label="MiniMax video generation")

            file_id = data.get("file_id")
            if not file_id:
                raise VideoGenerationError(
                    f"MiniMax returned no file id: {str(data)[:500]}"
                )
            retrieve = await self._request(
                client,
                "GET",
                f"{self.api_base}/files/retrieve?file_id={file_id}",
                headers=headers,
                error_label="MiniMax file retrieval",
            )
            file_info = retrieve.json()
            download_url = ((file_info.get("file") or {}).get("download_url")) or ""
            if not download_url:
                raise VideoGenerationError(
                    f"MiniMax returned no download URL: {str(file_info)[:500]}"
                )
            download = await client.get(download_url, follow_redirects=True)
            try:
                download.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise VideoGenerationError(
                    f"failed to download MiniMax video: {download.text[:300]}"
                ) from exc
            raw = download.content

        return GeneratedVideoResponse(video=raw, mime=_detect_video_mime(raw), raw=data)


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

register_video_gen_provider(GeminiVideoGenerationClient)
register_video_gen_provider(MiniMaxVideoGenerationClient)
register_video_gen_provider(OpenAIVideoGenerationClient)


class OpenRouterVideoGenerationClient(VideoGenerationProvider):
    """OpenRouter async video API (``POST /api/v1/videos`` + poll)."""

    provider_name = "openrouter"
    missing_key_message = (
        "Video generation API key is not configured. "
        "Add a key in Settings → Video, or connect a paid Navin plan."
    )
    default_timeout = 120.0

    def _default_base_url(self) -> str:
        return "https://openrouter.ai/api/v1"

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
    ) -> GeneratedVideoResponse:
        if not self.api_key:
            raise VideoGenerationError(self.missing_key_message)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **_OPENROUTER_ATTRIBUTION_HEADERS,
            **self.extra_headers,
        }
        body: dict[str, Any] = {"model": model, "prompt": prompt}
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if duration_seconds:
            body["duration"] = int(duration_seconds)
        if resolution:
            body["resolution"] = resolution
        if reference_image:
            body["frame_images"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": image_path_to_data_url(reference_image)},
                    "frame_type": "first_frame",
                }
            ]
        body.update(self.extra_body)

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            start = await self._request(
                client,
                "POST",
                f"{self.api_base}/videos",
                headers=headers,
                json_body=body,
                error_label="Video generation",
            )
            job = start.json()
            polling_url = job.get("polling_url")
            video_id = job.get("id")
            if not polling_url and video_id:
                polling_url = f"{self.api_base}/videos/{video_id}"
            if not polling_url:
                raise VideoGenerationError(
                    f"Video provider did not return a polling URL: {str(job)[:500]}"
                )

            async def check():
                status = await self._request(
                    client,
                    "GET",
                    str(polling_url),
                    headers=headers,
                    error_label="Video status",
                )
                data = status.json()
                state = str(data.get("status") or "").lower()
                if state in {"completed", "complete", "succeeded", "success"}:
                    return data
                if state in {"failed", "error", "cancelled", "canceled"}:
                    detail = data.get("error") or state
                    raise VideoGenerationError(
                        f"Video generation failed: {detail}"
                    )
                return None

            data = await self._poll(check, error_label="Video generation")

            job_id = str(data.get("id") or video_id or "").strip()
            urls: list[str] = []
            for key in ("unsigned_urls", "urls", "signed_urls"):
                raw_urls = data.get(key)
                if isinstance(raw_urls, list):
                    urls.extend(u for u in raw_urls if isinstance(u, str) and u)

            # Prefer the configured API base + job id. Provider "unsigned_urls"
            # often point at /videos/{id}/content and still require Bearer auth;
            # a bare GET returns 401 ("auth cookie").
            download_headers = {
                "Authorization": f"Bearer {self.api_key}",
                **_OPENROUTER_ATTRIBUTION_HEADERS,
                **self.extra_headers,
            }
            content_url = self._resolve_video_content_url(job_id, urls)
            if not content_url:
                raise VideoGenerationError(
                    f"Video generation completed without a download URL: {str(data)[:500]}"
                )

            content = await self._request(
                client,
                "GET",
                content_url,
                headers=download_headers,
                error_label="Video download",
            )
            raw = content.content

        return GeneratedVideoResponse(
            video=raw, mime=_detect_video_mime(raw), raw=data
        )

    def _resolve_video_content_url(self, job_id: str, urls: list[str]) -> str:
        """Build an authenticated content URL on our configured API base."""
        if job_id:
            return f"{self.api_base.rstrip('/')}/videos/{job_id}/content"
        for url in urls:
            # Rewrite absolute provider content URLs onto api_base so managed
            # keys always hit the same host that created the job.
            marker = "/videos/"
            if marker in url and "/content" in url:
                tail = url.split(marker, 1)[1]
                return f"{self.api_base.rstrip('/')}{marker}{tail}"
            if url.startswith(self.api_base):
                return url
        return urls[0] if urls else ""


class NavinVideoGenerationClient(OpenRouterVideoGenerationClient):
    """Video generation via the managed Navin OpenRouter key (Seedance, etc.)."""

    provider_name = "navin"
    missing_key_message = (
        "Navin managed key is not configured. Connect a paid plan, "
        "or switch Video settings to your own provider."
    )


register_video_gen_provider(OpenRouterVideoGenerationClient)
from navin.optional_live import live_modules_available

if live_modules_available():
    register_video_gen_provider(NavinVideoGenerationClient)


class LocalOpenAICompatibleVideoGenerationClient(OpenAIVideoGenerationClient):
    """OpenAI-compatible ``/videos`` API for local servers (Ollama, vLLM, …).

    API key is optional: many local stacks accept a dummy Bearer token or none.
    """

    missing_key_message = ""
    _local_api_key = "local"

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
    ) -> GeneratedVideoResponse:
        if not self.api_base:
            raise VideoGenerationError(
                f"{self.provider_name} video API base is not configured. "
                f"Set providers.{self.provider_name}.apiBase."
            )
        # Reuse the OpenAI videos client with a local placeholder key when unset.
        previous = self.api_key
        if not previous:
            self.api_key = self._local_api_key
        try:
            return await super().generate(
                prompt=prompt,
                model=model,
                reference_image=reference_image,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
                resolution=resolution,
            )
        finally:
            self.api_key = previous


class OllamaVideoGenerationClient(LocalOpenAICompatibleVideoGenerationClient):
    """Video generation via Ollama's OpenAI-compatible ``/v1/videos`` surface."""

    provider_name = "ollama"

    def _default_base_url(self) -> str:
        return "http://localhost:11434/v1"


class VllmVideoGenerationClient(LocalOpenAICompatibleVideoGenerationClient):
    """Video generation via a vLLM (or similar) OpenAI-compatible ``/videos`` API."""

    provider_name = "vllm"

    def _default_base_url(self) -> str:
        return "http://localhost:8000/v1"


class LmStudioVideoGenerationClient(LocalOpenAICompatibleVideoGenerationClient):
    """Video generation via LM Studio's OpenAI-compatible ``/v1/videos`` surface."""

    provider_name = "lm_studio"

    def _default_base_url(self) -> str:
        return "http://localhost:1234/v1"


register_video_gen_provider(OllamaVideoGenerationClient)
register_video_gen_provider(VllmVideoGenerationClient)
register_video_gen_provider(LmStudioVideoGenerationClient)
