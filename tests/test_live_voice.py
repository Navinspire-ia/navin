# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live must use configured speech engines and survive real asynchronous turns."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from navin.audio.models import NAVIN_STT_MODEL, NAVIN_TTS_MODEL, NAVIN_TTS_VOICE
from navin.audio.transcription import resolve_transcription_config
from navin.audio.tts import live_voice_status, resolve_tts_config, synthesize_speech
from navin.config.loader import load_config, save_config
from navin.config.schema import Config
from navin.providers import tts as adapters
from navin.webui.settings_api import (
    WebUISettingsError,
    provider_models_payload,
    settings_payload,
    update_live_voice_settings,
    update_transcription_settings,
    update_voice_settings,
)
from navin.webui.voice_api import voice_preview_payload
from navin.webui.voice_session_ws import (
    clear_voice_sessions_for_tests,
    close_voice_sessions,
    webui_voice_session_events,
)


@pytest.fixture
def byok(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "NAVIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = Config()
    config.license.plan = "free"
    config.transcription.provider = "groq"
    config.voice.tts_provider = "openai"
    config.providers.groq.api_key = "test-stt-key"
    config.providers.openai.api_key = "test-tts-key"
    save_config(config)
    clear_voice_sessions_for_tests()
    yield config
    clear_voice_sessions_for_tests()


def test_free_chat_key_alone_requires_both_speech_choices(byok):
    byok.transcription.provider = ""
    byok.voice.tts_provider = ""
    byok.providers.openrouter.api_key = "chat-key"
    status = live_voice_status(byok)
    assert status["ready"] is False
    assert status["missing"] == ["stt_not_configured", "tts_not_configured"]
    assert status["settings_section"] == "voice"
    assert "chat-key" not in str(status)


def test_legacy_byok_fallback_is_not_displayed_as_an_already_saved_live_choice(byok):
    byok.transcription.provider = ""
    byok.voice.tts_provider = ""
    byok.providers.openrouter.api_key = "chat-key"
    save_config(byok)
    payload = settings_payload()
    assert payload["transcription"]["provider"] == ""
    assert payload["transcription"]["model"] == ""
    assert payload["voice"]["tts_provider"] == ""
    assert payload["voice"]["tts_model"] == ""
    assert payload["voice"]["live"]["ready"] is False
    # Choosing the available provider now differs from the displayed baseline
    # and can be saved without changing an unrelated option as a workaround.
    update_transcription_settings({"provider": ["groq"], "model": ["whisper-large-v3"]})
    update_voice_settings({"tts_provider": ["openai"], "tts_model": ["gpt-4o-mini-tts"]})
    assert settings_payload()["voice"]["live"]["ready"] is True


@pytest.mark.parametrize("chat_provider", ["navin", "anthropic", "openai", "gemini", "zai", "xai"])
def test_live_byok_is_independent_of_subscription_and_chat_model(byok, chat_provider):
    byok.agents.defaults.provider = chat_provider
    status = live_voice_status(byok)
    assert status["ready"] is True
    assert status["mode"] == "byok"
    assert status["stt"]["provider"] == "groq"
    assert status["tts"]["provider"] == "openai"
    assert byok.voice.realtime_enabled is None


@pytest.mark.parametrize("missing", ["stt", "tts"])
def test_live_requires_both_engines_even_on_a_paid_plan(byok, missing):
    byok.license.plan = "pro"
    if missing == "stt":
        byok.providers.groq.api_key = ""
    else:
        byok.providers.openai.api_key = ""
    status = live_voice_status(byok)
    assert status["ready"] is False
    assert status["missing"] == [f"{missing}_not_configured"]


def test_navin_qwen_defaults_resolve_from_the_managed_key(byok):
    byok.license.plan = "pro"
    byok.license.managed_api_key = "managed-test-key"
    byok.transcription.provider = "navin"
    byok.voice.tts_provider = "navin"
    status = live_voice_status(byok)
    assert status["ready"] is True
    assert status["stt"]["model"] == NAVIN_STT_MODEL
    assert (status["tts"]["model"], status["tts"]["voice"]) == (NAVIN_TTS_MODEL, NAVIN_TTS_VOICE)
    assert "managed-test-key" not in str(status)


@pytest.fixture
def subscriber(byok):
    config = Config()
    config.license.plan = "pro"
    config.license.managed_api_key = "managed-test-key"
    config.model_catalog.enabled = False
    save_config(config)
    return config


def test_first_subscriber_setup_is_ready_without_catalog_or_manual_choices(subscriber):
    payload = settings_payload()
    assert payload["voice"]["live"]["ready"] is True
    assert payload["transcription"]["provider"] == "navin"
    assert payload["transcription"]["model"] == NAVIN_STT_MODEL
    assert payload["voice"]["tts_model"] == NAVIN_TTS_MODEL
    assert payload["voice"]["voice"] == NAVIN_TTS_VOICE
    assert settings_payload()["voice"]["live"] == payload["voice"]["live"]


@pytest.mark.asyncio
async def test_first_subscriber_can_start_live_before_visiting_settings(subscriber):
    events = await webui_voice_session_events({"type": "voice_session_start", "request_id": "first"})
    assert events[0][0] == "voice_session_started"
    assert events[0][1]["tts_model"] == NAVIN_TTS_MODEL
    assert events[0][1]["stt_model"] == NAVIN_STT_MODEL
    assert live_voice_status(load_config())["ready"] is True


def test_new_speech_choices_survive_settings_and_an_older_managed_catalog(subscriber):
    from navin.providers.managed_catalog import (
        FALLBACK_CATALOG_PAYLOAD,
        apply_catalog,
        parse_catalog,
    )

    chosen_stt = "provider/new-transcription-model"
    chosen_tts = "google/gemini-3.1-flash-tts-preview"
    payload = update_live_voice_settings({
        "provider": ["navin"], "model": [chosen_stt], "enabled": ["true"],
        "tts_provider": ["navin"], "tts_model": [chosen_tts], "voice": ["Kore"],
    })
    assert payload["transcription"]["model"] == chosen_stt
    assert payload["voice"]["tts_model"] == chosen_tts
    config = load_config()
    apply_catalog(config, parse_catalog(FALLBACK_CATALOG_PAYLOAD), force_managed=True, steer_tools=True)
    save_config(config)
    refreshed = settings_payload()
    assert refreshed["transcription"]["model"] == chosen_stt
    assert (refreshed["voice"]["tts_model"], refreshed["voice"]["voice"]) == (chosen_tts, "Kore")
    assert refreshed["voice"]["live"]["ready"] is True


def test_explicit_byok_setup_with_missing_keys_is_not_overwritten_for_subscribers(subscriber):
    payload = update_live_voice_settings({
        "provider": ["groq"], "model": ["whisper-large-v3"],
        "tts_provider": ["openai"], "tts_model": ["gpt-4o-mini-tts"],
    })
    assert payload["transcription"]["provider"] == "groq"
    assert payload["voice"]["tts_provider"] == "openai"
    assert payload["voice"]["live"]["missing"] == ["stt_not_configured", "tts_not_configured"]


def test_live_save_validates_both_engines_before_writing_either(byok):
    before = load_config().model_dump()
    with pytest.raises(WebUISettingsError):
        update_live_voice_settings({"enabled": ["false"], "model": ["another-stt"], "response_format": ["invalid"]})
    assert load_config().model_dump() == before


def test_saved_listening_and_live_disable_are_preserved_on_subscriber_refresh(subscriber):
    update_live_voice_settings({
        "provider": ["navin"], "model": [NAVIN_STT_MODEL], "enabled": ["false"],
        "tts_provider": ["navin"], "tts_model": [NAVIN_TTS_MODEL], "realtime_enabled": ["false"],
    })
    payload = settings_payload()
    assert payload["transcription"]["enabled"] is False
    assert payload["voice"]["live"]["reason"] == "voice_disabled"


def test_missing_explicit_byok_key_does_not_switch_accounts(byok):
    byok.license.plan = "pro"
    byok.license.managed_api_key = "managed-test-key"
    byok.providers.groq.api_key = ""
    byok.providers.openai.api_key = ""
    stt = resolve_transcription_config(byok)
    tts = resolve_tts_config(byok)
    assert (stt.provider, stt.configured) == ("groq", False)
    assert (tts.provider, tts.configured) == ("openai", False)


def test_provider_change_resets_incompatible_models_and_voice_without_restart(byok):
    byok.voice.tts_model = "gemini-3.1-flash-tts-preview"
    byok.voice.voice = "Kore"
    byok.transcription.model = "whisper-large-v3"
    save_config(byok)
    with patch("navin.webui.settings_api.settings_payload", return_value={"requires_restart": False}):
        result = update_voice_settings({"tts_provider": ["openrouter"]})
        update_transcription_settings({"provider": ["openrouter"]})
    saved = load_config()
    assert saved.voice.tts_model == ""
    assert saved.voice.voice == "auto"
    assert saved.transcription.model == ""
    assert result["requires_restart"] is False


@pytest.mark.parametrize(("kind", "modality", "wanted"), [
    ("tts", "speech", NAVIN_TTS_MODEL), ("stt", "transcription", NAVIN_STT_MODEL),
])
def test_catalog_fetches_the_speech_endpoint_and_keeps_its_actual_voices(byok, kind, modality, wanted):
    byok.providers.openrouter.api_key = "catalog-key"
    save_config(byok)
    rows = [
        {"id": NAVIN_TTS_MODEL, "architecture": {"output_modalities": ["speech"]},
         "supported_voices": ["provider-new-voice", "loongjohn"]},
        {"id": NAVIN_STT_MODEL, "architecture": {"output_modalities": ["transcription"]}},
        {"id": "vendor/audio-chat", "architecture": {"output_modalities": ["text", "audio"]}},
        {"id": "vendor/music", "architecture": {"output_modalities": ["music"]}},
        {"id": "vendor/fake-tts", "architecture": {"output_modalities": ["text"]}},
    ]
    response = httpx.Response(200, json={"data": rows}, request=httpx.Request("GET", "https://catalog.test"))
    with patch("navin.webui.settings_api.httpx.get", return_value=response) as request:
        result = provider_models_payload({"provider": ["openrouter"], "modality": [kind]})
    assert request.call_args.kwargs["params"] == {"output_modalities": modality}
    assert [row["id"] for row in result["models"]] == [wanted]
    if kind == "tts":
        assert result["models"][0]["voices"] == ["provider-new-voice", "loongjohn"]
    assert "catalog-key" not in str(result)


@pytest.mark.asyncio
async def test_preview_uses_the_selected_voice_without_saving_or_opening_a_session(byok):
    original = load_config().model_dump()
    with patch("navin.webui.voice_api.synthesize_speech_with_config", new=AsyncMock(return_value=b"ID3test")) as synth:
        preview = await voice_preview_payload({
            "provider": ["openai"], "model": ["gpt-4o-mini-tts"], "voice": ["cedar"], "text": ["Bonjour."],
        })
    assert synth.call_args.args[0] == "Bonjour."
    effective = synth.call_args.args[1]
    assert (effective.provider, effective.model, effective.voice) == ("openai", "gpt-4o-mini-tts", "cedar")
    assert base64.b64decode(preview["audio_base64"]) == b"ID3test"
    assert load_config().model_dump() == original


@pytest.mark.asyncio
async def test_preview_requires_the_selected_provider_key(byok):
    byok.providers.openai.api_key = ""
    byok.license.managed_api_key = "managed-test-key"
    save_config(byok)
    with patch("navin.webui.voice_api.synthesize_speech_with_config") as synth, pytest.raises(WebUISettingsError):
        await voice_preview_payload({"provider": ["openai"], "text": ["Bonjour."]})
    synth.assert_not_called()


@pytest.mark.asyncio
async def test_live_start_checks_setup_and_explicit_disable_before_allocating_session(byok):
    started = await webui_voice_session_events({"type": "voice_session_start"})
    assert started[0][0] == "voice_session_started"
    byok.voice.realtime_enabled = False
    save_config(byok)
    rejected = await webui_voice_session_events({"type": "voice_session_start"})
    assert rejected[0][1]["detail"] == "voice_disabled"
    assert "session_id" not in rejected[0][1]


@pytest.mark.asyncio
async def test_sessions_belong_to_the_connection_that_started_them(byok):
    owner, other = object(), object()
    started = await webui_voice_session_events({"type": "voice_session_start", "session_id": "call"}, owner=owner)
    assert started[0][0] == "voice_session_started"
    for kind in ("voice_session_start", "voice_audio_chunk", "voice_prompt", "voice_session_end"):
        rejected = await webui_voice_session_events({"type": kind, "session_id": "call"}, owner=other)
        assert rejected[0][1]["detail"] == "invalid_session"
    close_voice_sessions(owner)
    ended = await webui_voice_session_events({"type": "voice_audio_chunk", "session_id": "call"}, owner=owner)
    assert ended[0][1]["detail"] == "invalid_session"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["barge_in", "disconnect"])
async def test_interrupt_cancels_inflight_synthesis_and_queued_chunks(byok, operation):
    owner = object()
    await webui_voice_session_events({"type": "voice_session_start", "session_id": "call"}, owner=owner)
    running = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_speech(*args):
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with patch("navin.webui.voice_session_ws.synthesize_speech_with_config", side_effect=slow_speech):
        tasks = [asyncio.create_task(webui_voice_session_events({
            "type": "voice_audio_chunk", "session_id": "call", "request_id": f"part-{index}",
            "speak_text": "Une longue explication.",
        }, owner=owner)) for index in range(5)]
        await asyncio.wait_for(running.wait(), 1)
        if operation == "barge_in":
            result = await webui_voice_session_events({
                "type": "voice_audio_chunk", "session_id": "call", "barge_in": True,
            }, owner=owner)
            assert result[0][0] == "tts_cancelled"
        else:
            close_voice_sessions(owner)
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
    assert cancelled.is_set()
    assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes)


@pytest.mark.asyncio
async def test_late_speech_cannot_reappear_when_the_same_session_id_is_reused(byok):
    owner = object()
    await webui_voice_session_events({"type": "voice_session_start", "session_id": "call"}, owner=owner)
    running, released = asyncio.Event(), asyncio.Event()

    async def ignores_cancel(*args):
        running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await released.wait()
        return b"stale audio"

    with patch("navin.webui.voice_session_ws.synthesize_speech_with_config", side_effect=ignores_cancel):
        task = asyncio.create_task(webui_voice_session_events({
            "type": "voice_audio_chunk", "session_id": "call", "speak_text": "Ancienne reponse.",
        }, owner=owner))
        await asyncio.wait_for(running.wait(), 1)
        close_voice_sessions(owner)
        await webui_voice_session_events({"type": "voice_session_start", "session_id": "call"}, owner=owner)
        released.set()
        assert await asyncio.wait_for(task, 1) == []


@pytest.mark.asyncio
async def test_google_byok_uses_native_speech_auth_voice_and_playable_wav():
    pcm = b"\x01\x00\x02\x00" * 100

    def handler(request):
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == "test-google-key"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["input"] == "Bonjour."
        assert body["store"] is False
        assert body["generation_config"]["speech_config"] == [{"voice": "Puck"}]
        return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [{
            "type": "audio", "data": base64.b64encode(pcm).decode(), "mime_type": "audio/pcm",
        }]}]})

    client = httpx.AsyncClient
    with patch.object(adapters.httpx, "AsyncClient", side_effect=lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
        audio = await synthesize_speech("Bonjour.", "gemini", "Puck", "test-google-key", model="gemini-3.1-flash-tts-preview")
    assert audio.startswith(b"RIFF")
    assert audio[44:] == pcm


@pytest.mark.asyncio
async def test_groq_speech_respects_character_limit_and_delivers_the_complete_recording(byok):
    from navin.audio.models import GROQ_TTS_MODEL, GROQ_TTS_VOICE, known_voices

    byok.voice.tts_provider = "groq"
    byok.voice.tts_model = ""
    byok.voice.voice = "auto"
    effective = resolve_tts_config(byok)
    assert (effective.model, effective.voice, effective.response_format) == (GROQ_TTS_MODEL, GROQ_TTS_VOICE, "wav")
    assert "hannah" in known_voices(effective.model)
    assert "noura" in known_voices("canopylabs/orpheus-arabic-saudi")
    text = "I will check the project, explain the result, and answer your questions. " * 9
    received = []
    pcm = b"\x01\x00\x02\x00" * 100

    def handler(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/audio/speech"
        body = json.loads(request.content)
        assert body["model"] == GROQ_TTS_MODEL
        assert body["voice"] == GROQ_TTS_VOICE
        assert body["response_format"] == "wav"
        assert len(body["input"]) <= 200
        received.append(body["input"])
        return httpx.Response(200, content=adapters.pcm16_to_wav(pcm), headers={"content-type": "audio/wav"})

    client = httpx.AsyncClient
    with patch.object(adapters.httpx, "AsyncClient", side_effect=lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
        audio = await synthesize_speech(text, "groq", effective.voice, effective.api_key, model=effective.model)
    assert " ".join(received) == text.strip()
    with wave.open(io.BytesIO(audio), "rb") as recording:
        assert recording.getframerate() == 24000
        assert recording.readframes(recording.getnframes()) == pcm * len(received)
