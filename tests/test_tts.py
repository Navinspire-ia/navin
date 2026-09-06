"""Tests for TTS synthesis and barge-in helpers."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from navin.audio.tts import (
    TtsError,
    should_cancel_tts,
    synthesize_speech,
    voice_realtime_allowed,
)
from navin.audio.tts_registry import resolve_tts_provider, tts_provider_names
from navin.config.schema import Config, LicenseConfig, VoiceConfig
from navin.providers import tts as tts_providers
from navin.webui.voice_session_ws import (
    clear_voice_sessions_for_tests,
    webui_voice_session_events,
)


def test_tts_provider_registry_includes_openai_compatible():
    names = tts_provider_names()
    assert "navin" in names
    assert "openai" in names
    assert "openrouter" in names
    assert "groq" in names
    assert "gemini" in names
    assert "ollama" in names
    assert "vllm" in names
    assert "lm_studio" in names
    assert resolve_tts_provider("navin") is not None
    assert resolve_tts_provider("OpenAI") is not None
    assert resolve_tts_provider("google") is not None
    assert resolve_tts_provider("ollama") is not None
    assert resolve_tts_provider("vllm") is not None
    assert resolve_tts_provider("lm_studio") is not None
    assert resolve_tts_provider("lmstudio") is not None
    assert resolve_tts_provider("unknown-provider") is None


def test_should_cancel_tts_barge_in():
    assert should_cancel_tts(playing=True, user_speaking=True) is True
    assert should_cancel_tts(playing=True, user_speaking=False) is False
    assert should_cancel_tts(playing=False, user_speaking=True) is False
    assert should_cancel_tts(playing=False, user_speaking=False) is False


@pytest.mark.parametrize(
    ("plan", "flag", "expected"),
    [
        ("pro", None, True),
        ("ultra", None, True),
        ("team", None, True),
        ("plus", None, False),
        ("free", None, False),
        ("", None, False),
        ("free", True, True),
        ("pro", False, False),
    ],
)
def test_voice_realtime_allowed_plan_gate(plan: str, flag: bool | None, expected: bool):
    config = Config(
        license=LicenseConfig(plan=plan),
        voice=VoiceConfig(realtime_enabled=flag),
    )
    assert voice_realtime_allowed(config) is expected


@pytest.mark.asyncio
async def test_synthesize_speech_mocked_http():
    audio_bytes = b"ID3fake-mp3-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/audio/speech")
        body = request.read()
        assert b"Hello Navin" in body
        assert b"alloy" in body
        return httpx.Response(200, content=audio_bytes)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    with patch.object(tts_providers.httpx, "AsyncClient", side_effect=client_factory):
        result = await synthesize_speech(
            "Hello Navin",
            "openai",
            "alloy",
            "sk-test",
            api_base="https://api.openai.com/v1",
            model="tts-1",
        )
    assert result == audio_bytes


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_empty_and_missing_key():
    with pytest.raises(TtsError) as empty:
        await synthesize_speech("  ", "openai", "alloy", "sk-test")
    assert empty.value.detail == "empty_text"

    with pytest.raises(TtsError) as missing:
        await synthesize_speech("hi", "openai", "alloy", "")
    assert missing.value.detail == "not_configured"


@pytest.mark.asyncio
async def test_voice_session_start_plan_required():
    clear_voice_sessions_for_tests()
    config = Config(
        license=LicenseConfig(plan="free"),
        voice=VoiceConfig(realtime_enabled=None),
    )
    with patch("navin.webui.voice_session_ws.load_config", return_value=config):
        events = await webui_voice_session_events(
            {"type": "voice_session_start", "request_id": "req-1"}
        )
    assert len(events) == 1
    event, payload = events[0]
    assert event == "voice_session_error"
    assert payload["detail"] == "plan_required"


@pytest.mark.asyncio
async def test_voice_session_start_and_tts_chunk():
    clear_voice_sessions_for_tests()
    config = Config(
        license=LicenseConfig(plan="pro"),
        voice=VoiceConfig(
            realtime_enabled=True,
            tts_provider="openai",
            voice="alloy",
            auto_speak=True,
        ),
    )
    # Mark STT as configured via resolve helpers.
    stt_cfg = SimpleNamespace(
        enabled=True,
        configured=True,
        provider="groq",
    )
    tts_cfg = SimpleNamespace(
        provider="openai",
        model="tts-1",
        voice="alloy",
        auto_speak=True,
        configured=True,
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        response_format="mp3",
    )

    with (
        patch("navin.webui.voice_session_ws.load_config", return_value=config),
        patch(
            "navin.webui.voice_session_ws.resolve_transcription_config",
            return_value=stt_cfg,
        ),
        patch("navin.webui.voice_session_ws.resolve_tts_config", return_value=tts_cfg),
        patch(
            "navin.webui.voice_session_ws.synthesize_speech_with_config",
            new=AsyncMock(return_value=b"\xff\xfbaudio"),
        ),
    ):
        started = await webui_voice_session_events(
            {"type": "voice_session_start", "request_id": "req-start"}
        )
        assert started[0][0] == "voice_session_started"
        session_id = started[0][1]["session_id"]

        with patch(
            "navin.webui.voice_session_ws.transcribe_audio_data_url",
            new=AsyncMock(return_value="hello there"),
        ):
            chunk_events = await webui_voice_session_events(
                {
                    "type": "voice_audio_chunk",
                    "request_id": "req-chunk",
                    "session_id": session_id,
                    "data_url": "data:audio/wav;base64,AAAA",
                    "speak_text": "Reply aloud",
                }
            )

    kinds = [event for event, _ in chunk_events]
    assert "transcript_partial" in kinds
    assert "tts_audio" in kinds
    tts_payload = next(payload for event, payload in chunk_events if event == "tts_audio")
    assert base64.b64decode(tts_payload["audio_base64"]) == b"\xff\xfbaudio"


@pytest.mark.asyncio
async def test_voice_session_barge_in_cancels_tts():
    clear_voice_sessions_for_tests()
    config = Config(license=LicenseConfig(plan="pro"), voice=VoiceConfig(realtime_enabled=True))
    stt_cfg = SimpleNamespace(enabled=True, configured=True, provider="groq")
    tts_cfg = SimpleNamespace(
        provider="openai",
        model="tts-1",
        voice="alloy",
        auto_speak=True,
        configured=True,
        api_key="sk-test",
        api_base="",
        response_format="mp3",
    )
    synthesize = AsyncMock(return_value=b"audio")

    with (
        patch("navin.webui.voice_session_ws.load_config", return_value=config),
        patch(
            "navin.webui.voice_session_ws.resolve_transcription_config",
            return_value=stt_cfg,
        ),
        patch("navin.webui.voice_session_ws.resolve_tts_config", return_value=tts_cfg),
        patch(
            "navin.webui.voice_session_ws.synthesize_speech_with_config",
            new=synthesize,
        ),
    ):
        started = await webui_voice_session_events(
            {"type": "voice_session_start", "request_id": "r1"}
        )
        session_id = started[0][1]["session_id"]
        events = await webui_voice_session_events(
            {
                "type": "voice_audio_chunk",
                "request_id": "r2",
                "session_id": session_id,
                "speak_text": "should not play",
                "barge_in": True,
            }
        )

    assert synthesize.await_count == 0
    assert events[0][0] == "tts_cancelled"


@pytest.mark.asyncio
async def test_openrouter_tts_sends_extra_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k: v for k, v in request.headers.items()})
        return httpx.Response(200, content=b"mp3")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    with patch.object(tts_providers.httpx, "AsyncClient", side_effect=client_factory):
        audio = await synthesize_speech(
            "OpenRouter hello",
            "openrouter",
            "alloy",
            "or-key",
        )
    assert audio == b"mp3"
    assert seen.get("authorization") == "Bearer or-key"
    assert seen.get("x-title") == "Navin"
