# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Lip-sync provider registry and Sync Labs REST v2 adapter."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_MAX_DIRECT_FILE_BYTES = 20 * 1024 * 1024


class LipSyncError(RuntimeError):
    """Raised when a lip-sync request cannot produce a validated video."""


@dataclass(frozen=True)
class LipSyncResult:
    video: bytes
    mime: str
    generation_id: str
    provider: str
    model: str
    raw: dict[str, Any]


class LipSyncProvider(ABC):
    name: str
    requires_credentials: bool = True

    @abstractmethod
    async def generate(self, *, video: Path, audio: Path, model: str) -> LipSyncResult:
        """Generate one lip-synced video from exactly one video and one audio."""


def _safe_detail(response: httpx.Response, api_key: str) -> str:
    return response.text[:800].replace(api_key, "<redacted>") if api_key else response.text[:800]


def _output_url(data: dict[str, Any]) -> str | None:
    output = data.get("output")
    if isinstance(output, dict):
        for key in ("url", "outputUrl", "output_url"):
            if isinstance(output.get(key), str) and output[key]:
                return output[key]
    for key in ("outputUrl", "output_url", "url"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key]
    return None


def _validate_video(raw: bytes, content_type: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    is_mp4 = len(raw) >= 12 and raw[4:8] == b"ftyp"
    is_webm = raw.startswith(b"\x1aE\xdf\xa3")
    if not raw or not (is_mp4 or is_webm):
        raise LipSyncError("lip-sync output is empty or is not a supported video container")
    if is_mp4:
        return "video/mp4"
    if is_webm:
        return "video/webm"
    if mime.startswith("video/"):  # pragma: no cover - guarded by signatures
        return mime
    raise LipSyncError("lip-sync output has an invalid content type")


class SyncLabsLipSyncProvider(LipSyncProvider):
    """Sync Labs generate API using local multipart file inputs."""

    name = "sync_labs"

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str = "https://api.sync.so/v2",
        timeout_s: float = 60.0,
        max_wait_s: float = 1800.0,
        poll_interval_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = (api_key or os.environ.get("SYNC_API_KEY") or "").strip()
        self.api_base = api_base.rstrip("/")
        self.timeout_s = timeout_s
        self.max_wait_s = max_wait_s
        self.poll_interval_s = poll_interval_s
        self._client = client

    async def _checked(self, request, label: str) -> httpx.Response:
        try:
            response = await request
        except httpx.TimeoutException as exc:
            raise LipSyncError(f"{label} timed out") from exc
        except httpx.RequestError as exc:
            message = str(exc).replace(self.api_key, "<redacted>")
            raise LipSyncError(f"{label} request failed: {message}") from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LipSyncError(
                f"{label} failed (HTTP {response.status_code}): "
                f"{_safe_detail(response, self.api_key)}"
            ) from exc
        return response

    async def generate(self, *, video: Path, audio: Path, model: str = "lipsync-2") -> LipSyncResult:
        if not self.api_key:
            raise LipSyncError(
                "Sync Labs API key is not configured. Set tools.montage.lipSync.apiKey "
                "or SYNC_API_KEY."
            )
        if not video.is_file() or not audio.is_file():
            raise LipSyncError("lip-sync requires one existing video and one existing audio file")
        for path in (video, audio):
            if path.stat().st_size > _MAX_DIRECT_FILE_BYTES:
                raise LipSyncError(
                    f"Sync Labs direct upload is limited to 20 MB per file: {path.name}"
                )

        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        )
        try:
            with video.open("rb") as video_file, audio.open("rb") as audio_file:
                files = [
                    (
                        "video",
                        (
                            video.name,
                            video_file,
                            mimetypes.guess_type(video.name)[0] or "video/mp4",
                        ),
                    ),
                    (
                        "audio",
                        (
                            audio.name,
                            audio_file,
                            mimetypes.guess_type(audio.name)[0] or "audio/wav",
                        ),
                    ),
                ]
                form = {
                    "model": model or "lipsync-2",
                    "input": json.dumps(
                        [{"type": "video"}, {"type": "audio"}],
                        separators=(",", ":"),
                    ),
                }
                started = await self._checked(
                    client.post(
                        f"{self.api_base}/generate",
                        headers=headers,
                        data=form,
                        files=files,
                    ),
                    "Sync Labs generation",
                )
            try:
                data = started.json()
            except json.JSONDecodeError as exc:
                raise LipSyncError("Sync Labs generation returned invalid JSON") from exc
            generation_id = str(data.get("id") or data.get("generationId") or "").strip()
            if not generation_id:
                raise LipSyncError("Sync Labs generation returned no id")

            deadline = time.monotonic() + self.max_wait_s
            while True:
                polled = await self._checked(
                    client.get(f"{self.api_base}/generate/{generation_id}", headers=headers),
                    "Sync Labs polling",
                )
                try:
                    data = polled.json()
                except json.JSONDecodeError as exc:
                    raise LipSyncError("Sync Labs polling returned invalid JSON") from exc
                status = str(data.get("status") or "").upper()
                if status == "COMPLETED":
                    break
                if status in {"FAILED", "REJECTED"}:
                    detail = str(data.get("error") or data.get("message") or status).replace(
                        self.api_key, "<redacted>"
                    )
                    raise LipSyncError(f"Sync Labs generation {status.lower()}: {detail}")
                if time.monotonic() >= deadline:
                    raise LipSyncError(
                        f"Sync Labs generation did not complete within {int(self.max_wait_s)}s"
                    )
                await asyncio.sleep(min(self.poll_interval_s, max(0.0, deadline - time.monotonic())))

            url = _output_url(data)
            if not url:
                raise LipSyncError("Sync Labs completed without an output URL")
            download = await self._checked(client.get(url), "Sync Labs output download")
            mime = _validate_video(download.content, download.headers.get("content-type", ""))
            return LipSyncResult(
                video=download.content,
                mime=mime,
                generation_id=generation_id,
                provider=self.name,
                model=model or "lipsync-2",
                raw=data,
            )
        finally:
            if owns_client:
                await client.aclose()


_PROVIDERS: dict[str, type[LipSyncProvider]] = {
    "sync_labs": SyncLabsLipSyncProvider,
    "sync-labs": SyncLabsLipSyncProvider,
    "sync": SyncLabsLipSyncProvider,
}


def lip_sync_provider_names() -> tuple[str, ...]:
    return ("sync_labs",)


def create_lip_sync_provider(name: str, **kwargs: Any) -> LipSyncProvider:
    provider = _PROVIDERS.get((name or "").strip().lower())
    if provider is None:
        raise LipSyncError(f"unknown lip-sync provider: {name}")
    return provider(**kwargs)
