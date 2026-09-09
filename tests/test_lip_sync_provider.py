# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from navin.providers.lip_sync import LipSyncError, SyncLabsLipSyncProvider


def _mp4() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24


@pytest.mark.asyncio
async def test_sync_labs_multipart_poll_and_download(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    audio = tmp_path / "voice.wav"
    video.write_bytes(_mp4())
    audio.write_bytes(b"RIFFfake")
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.host == "api.sync.so":
            assert request.headers["x-api-key"] == "secret"
        if request.method == "POST":
            body = request.content
            assert b'name="model"' in body
            assert b"lipsync-2" in body
            assert b'name="input"' in body
            assert b'[{"type":"video"},{"type":"audio"}]' in body
            assert b'name="video"; filename="source.mp4"' in body
            assert b'name="audio"; filename="voice.wav"' in body
            return httpx.Response(200, json={"id": "gen-1"})
        if request.url.path.endswith("/generate/gen-1"):
            polls += 1
            if polls == 1:
                return httpx.Response(200, json={"status": "PROCESSING"})
            return httpx.Response(
                200,
                json={"status": "COMPLETED", "output": {"url": "https://cdn.test/out.mp4"}},
            )
        return httpx.Response(
            200,
            content=_mp4(),
            headers={"content-type": "video/mp4"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SyncLabsLipSyncProvider(
            api_key="secret",
            client=client,
            poll_interval_s=0.001,
            max_wait_s=1,
        )
        result = await provider.generate(video=video, audio=audio, model="lipsync-2")

    assert result.generation_id == "gen-1"
    assert result.mime == "video/mp4"
    assert polls == 2


@pytest.mark.asyncio
async def test_sync_labs_rejects_terminal_failure_and_redacts_key(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    audio = tmp_path / "voice.wav"
    video.write_bytes(_mp4())
    audio.write_bytes(b"RIFFfake")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "gen-2"})
        return httpx.Response(
            200,
            content=json.dumps(
                {"status": "FAILED", "error": "key secret-key was rejected"}
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SyncLabsLipSyncProvider(
            api_key="secret-key",
            client=client,
            poll_interval_s=0.001,
        )
        with pytest.raises(LipSyncError) as raised:
            await provider.generate(video=video, audio=audio)
    assert "secret-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_sync_labs_validates_downloaded_video(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    audio = tmp_path / "voice.wav"
    video.write_bytes(_mp4())
    audio.write_bytes(b"RIFFfake")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "gen-3"})
        if request.url.path.endswith("/generate/gen-3"):
            return httpx.Response(
                200,
                json={"status": "COMPLETED", "outputUrl": "https://cdn.test/out.mp4"},
            )
        return httpx.Response(200, content=b"<html>bad</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SyncLabsLipSyncProvider(api_key="secret", client=client)
        with pytest.raises(LipSyncError, match="not a supported video"):
            await provider.generate(video=video, audio=audio)
