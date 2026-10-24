# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live realtime engine: Nova Sonic (Navin gateway), GPT Realtime and Gemini Live (BYOK)."""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import patch

from navin.config.loader import save_config
from navin.config.schema import Config, ModelPresetConfig
from navin.live.docs_search import search_docs
from navin.live.gemini_live import GeminiLiveTranslator, resample_pcm16_24k_to_16k
from navin.live.realtime import NovaGatewayFrames, bridge, realtime_status, resolve_realtime_target
from navin.providers.media_models import is_realtime_voice_model
from navin.webui.settings_api import (
    connected_media_providers,
    media_model_choices,
    provider_models_payload,
    update_live_voice_settings,
)


def _navin_config() -> Config:
    cfg = Config()
    cfg.license.managed_api_base = "https://llm.navin.live/v1"
    cfg.providers.navin.api_key = "sk-managed"
    cfg.voice.live_realtime_provider = "navin"
    cfg.voice.live_realtime_model = "amazon/nova-2-sonic"
    return cfg


def test_subscribers_get_nova_sonic_through_the_gateway_with_the_managed_key():
    target = resolve_realtime_target(_navin_config())
    assert target is not None and target.provider == "navin"
    assert target.url == "wss://llm.navin.live/v1/realtime?model=amazon/nova-2-sonic"
    assert target.headers == {"Authorization": "Bearer sk-managed"}


def test_byok_gpt_realtime_and_gemini_live_use_the_users_own_keys():
    cfg = Config()
    cfg.providers.openai.api_key = "sk-user"
    # A key alone does not pick a Live model for the user.
    assert resolve_realtime_target(cfg) is None
    cfg.voice.live_realtime_provider = "openai"
    cfg.voice.live_realtime_model = "gpt-realtime"
    target = resolve_realtime_target(cfg)
    assert target.provider == "openai" and "api.openai.com/v1/realtime?model=gpt-realtime" in target.url

    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    cfg.voice.live_realtime_provider = "gemini"
    cfg.voice.live_realtime_model = "models/gemini-3.8-live"
    target = resolve_realtime_target(cfg)
    assert target.provider == "gemini" and target.headers == {"x-goog-api-key": "AIza-user"}


def test_pipeline_choice_or_nothing_configured_means_no_realtime():
    cfg = _navin_config()
    cfg.voice.live_engine = "pipeline"
    assert resolve_realtime_target(cfg) is None
    status = realtime_status(Config())
    assert not status["available"] and status["engine"] == "auto"
    assert not status["pipeline_ready"]


def test_managed_live_can_be_ready_when_pipeline_requires_a_different_plan():
    cfg = _navin_config()
    cfg.license.plan = "plus"
    cfg.license.managed_api_key = "sk-managed"
    cfg.transcription.provider = "navin"
    cfg.voice.tts_provider = "navin"
    status = realtime_status(cfg)
    assert status["available"] and status["model"] == "amazon/nova-2-sonic"
    assert status["pipeline_ready"] is False
    assert status["pipeline_reason"] == "plan_required"


def test_disabling_voice_also_disables_the_realtime_bridge():
    cfg = _navin_config()
    cfg.voice.realtime_enabled = False
    assert resolve_realtime_target(cfg) is None
    status = realtime_status(cfg)
    assert status["available"] is False and status["pipeline_ready"] is False
    assert status["pipeline_reason"] == "voice_disabled"


class _Socket:
    """In-memory stand-in for a websocket (page side or upstream)."""

    def __init__(self, incoming=None):
        self.sent: list[str] = []
        self.incoming = list(incoming or [])
        self.closed = False

    async def send(self, frame):
        self.sent.append(frame)

    async def close(self, *_):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)


def test_the_bridge_relays_both_ways_and_never_shows_the_key_to_the_page():
    page = _Socket([json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(bytes(480)).decode()})])
    upstream = _Socket([json.dumps({"type": "response.audio.delta", "delta": "BBBB"})])

    async def connect(target):
        assert target.headers["Authorization"] == "Bearer sk-managed"
        return upstream

    asyncio.run(bridge(page, _navin_config(), connect=connect))
    assert json.loads(page.sent[0]) == {"type": "navin.ready", "provider": "navin", "model": "amazon/nova-2-sonic"}
    assert any("BBBB" in frame for frame in page.sent)
    assert "sk-managed" not in "".join(page.sent)
    assert upstream.sent and json.loads(upstream.sent[0])["type"] == "input_audio_buffer.append"


def test_an_unavailable_model_tells_the_page_to_fall_back():
    page = _Socket()
    asyncio.run(bridge(page, Config()))
    assert json.loads(page.sent[0])["error"]["code"] == "realtime_unavailable" and page.closed


def test_a_transient_upstream_failure_is_retried_once_but_a_bad_key_is_not(monkeypatch):
    import navin.live.realtime as realtime

    monkeypatch.setattr(realtime, "CONNECT_RETRY_DELAY_S", 0)
    attempts: list[int] = []
    upstream = _Socket()

    async def flaky(target):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("connection reset")
        return upstream

    page = _Socket()
    asyncio.run(bridge(page, _navin_config(), connect=flaky))
    assert len(attempts) == 2
    assert json.loads(page.sent[0])["type"] == "navin.ready"

    class _Response:
        status_code = 401

    class _RefusedError(Exception):
        response = _Response()

    attempts.clear()

    async def unauthorized(target):
        attempts.append(1)
        raise _RefusedError("HTTP 401")

    page = _Socket()
    asyncio.run(bridge(page, _navin_config(), connect=unauthorized))
    assert len(attempts) == 1
    error = json.loads(page.sent[0])["error"]
    assert error["code"] == "realtime_unreachable" and "401" in error["message"] and page.closed


def test_the_upstream_is_closed_when_the_page_leaves_before_ready():
    class _GonePage(_Socket):
        async def send(self, frame):
            raise ConnectionError("page closed")

    upstream = _Socket()

    async def connect(target):
        return upstream

    asyncio.run(bridge(_GonePage(), _navin_config(), connect=connect))
    assert upstream.closed


def test_an_abnormal_upstream_close_is_explained_to_the_page():
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    class _DyingUpstream(_Socket):
        async def __anext__(self):
            await asyncio.sleep(0)
            raise ConnectionClosedError(Close(1008, "invalid_api_key"), None)

    class _PatientPage(_Socket):
        async def __anext__(self):
            await asyncio.sleep(1)
            raise StopAsyncIteration

    async def connect(target):
        return _DyingUpstream()

    page = _PatientPage()
    asyncio.run(bridge(page, _navin_config(), connect=connect))
    errors = [json.loads(f)["error"] for f in page.sent if json.loads(f).get("type") == "error"]
    assert errors and errors[0]["code"] == "realtime_dropped"
    assert errors[0]["message"] == "upstream closed (1008: invalid_api_key)"
    assert page.closed

    # A normal end of session is not reported as a failure.
    class _PoliteUpstream(_Socket):
        async def __anext__(self):
            await asyncio.sleep(0)
            raise ConnectionClosedError(Close(1000, ""), None)

    async def connect_polite(target):
        return _PoliteUpstream()

    page = _PatientPage()
    asyncio.run(bridge(page, _navin_config(), connect=connect_polite))
    assert not any(json.loads(f).get("type") == "error" for f in page.sent)


def test_managed_nova_bridge_preserves_tool_names_for_bedrock():
    tool = {"type": "function", "name": "ask_agent", "description": "Delegate work",
            "parameters": {"type": "object", "properties": {"request": {"type": "string"}}}}
    update = json.dumps({"type": "session.update", "session": {
        "voice": "ambre", "tools": [tool], "input_audio_format": "pcm16",
    }})

    async def run():
        page = _Socket([update])
        upstream = _Socket()

        async def connect(target):
            return upstream

        await bridge(page, _navin_config(), connect=connect)
        forwarded = json.loads(upstream.sent[0])["session"]
        assert forwarded["tools"][0]["function"]["name"] == "ask_agent"
        assert forwarded["tools"][0]["function"]["parameters"] == tool["parameters"]
        assert forwarded["voice"] == "ambre"
        cfg = _navin_config()
        cfg.voice.live_realtime_provider = "openai"
        cfg.voice.live_realtime_model = "gpt-realtime"
        cfg.providers.openai.api_key = "test-key"
        upstream.sent.clear()
        await bridge(_Socket([update]), cfg, connect=connect)
        assert json.loads(upstream.sent[0])["session"]["tools"] == [tool]

    asyncio.run(run())


def test_managed_nova_automatic_voice_matches_the_female_avatar_even_without_tools():
    adapter = NovaGatewayFrames()
    for voice, instructions, expected in (
        (None, "The interface language is fr-FR", "ambre"),
        ("auto", "Speak French", "ambre"),
        ("", "Speak English", "tiffany"),
        ("florian", "Speak French", "florian"),
    ):
        event = {"type": "session.update", "session": {"instructions": instructions}}
        if voice is not None:
            event["session"]["voice"] = voice
        converted = json.loads(adapter.from_page(json.dumps(event)))
        assert converted["session"]["voice"] == expected


def test_managed_nova_tool_results_are_json_without_changing_other_providers():
    outputs = ["Sent to the agent.", "error: empty request", "", '{"agentWorking":false}', '["done"]', "Voix prête : café"]

    async def run():
        for output in outputs:
            frame = json.dumps({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": "tool-1", "output": output,
            }})
            upstream = _Socket()

            async def connect(target):
                return upstream

            await bridge(_Socket([frame]), _navin_config(), connect=connect)
            item = json.loads(upstream.sent[0])["item"]
            assert item["call_id"] == "tool-1"
            parsed = json.loads(item["output"])
            if output.startswith(("{", "[")):
                assert parsed == json.loads(output)
            else:
                assert parsed == {"result": output}
            cfg = _navin_config()
            cfg.voice.live_realtime_provider = "openai"
            cfg.providers.openai.api_key = "test-key"
            upstream.sent.clear()
            await bridge(_Socket([frame]), cfg, connect=connect)
            assert json.loads(upstream.sent[0])["item"]["output"] == output

    asyncio.run(run())


def test_managed_nova_microphone_is_resampled_for_the_gateway():
    from navin.live.realtime import NovaGatewayFrames

    frames = NovaGatewayFrames()
    raw = base64.b64encode(bytes(4800 * 2)).decode()
    converted = json.loads(frames.from_page(json.dumps({"type": "input_audio_buffer.append", "audio": raw})))
    assert len(base64.b64decode(converted["audio"])) == 3200 * 2
    assert frames.from_page('{"type":"input_audio_buffer.append","audio":"!"}') is None


def test_gemini_live_is_translated_from_and_to_the_realtime_protocol():
    tr = GeminiLiveTranslator("models/gemini-3.8-live")
    setup = tr.from_page({"type": "session.update", "session": {
        "instructions": "Be Navin", "voice": "Kore",
        "tools": [{"type": "function", "name": "ask_agent", "description": "d",
                   "parameters": {"type": "object", "properties": {"request": {"type": "string"}}}}],
    }})[0]["setup"]
    assert setup["model"] == "models/gemini-3.8-live"
    assert setup["systemInstruction"]["parts"][0]["text"] == "Be Navin"
    assert setup["tools"][0]["functionDeclarations"][0]["name"] == "ask_agent"
    assert setup["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"

    pcm24 = (b"\x10\x00" * 2400)  # 100 ms at 24 kHz
    audio = tr.from_page({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm24).decode()})[0]
    assert audio["realtimeInput"]["audio"]["mimeType"] == "audio/pcm;rate=16000"
    assert len(base64.b64decode(audio["realtimeInput"]["audio"]["data"])) == 1600 * 2

    events = tr.from_gemini({"toolCall": {"functionCalls": [{"id": "c1", "name": "ask_agent", "args": {"request": "run tests"}}]}})
    assert events[0]["type"] == "response.function_call_arguments.done" and json.loads(events[0]["arguments"]) == {"request": "run tests"}
    reply = tr.from_page({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": "c1", "output": "sent"}})[0]
    assert reply["toolResponse"]["functionResponses"][0] == {"id": "c1", "name": "ask_agent", "response": {"result": "sent"}}

    events = tr.from_gemini({"serverContent": {"inputTranscription": {"text": "lance les tests"},
                                               "modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": "QUJD"}}]}}})
    kinds = [e["type"] for e in events]
    assert kinds == ["conversation.item.input_audio_transcription.completed", "response.created", "response.audio.delta"]
    assert tr.from_gemini({"serverContent": {"interrupted": True}})[0]["type"] == "input_audio_buffer.speech_started"
    assert tr.from_gemini({"serverContent": {"turnComplete": True}})[-1]["type"] == "response.done"
    text = tr.from_page({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                       "content": [{"type": "input_text", "text": "[Navin agent] done"}]}})[0]
    assert text["clientContent"]["turns"][0]["parts"][0]["text"] == "[Navin agent] done"
    assert len(resample_pcm16_24k_to_16k(b"\x00\x00" * 3)) == 4


def test_one_model_list_per_use_and_picking_one_sets_its_provider():
    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    cfg.model_presets = {
        "live": ModelPresetConfig(label="Gemini Live", model="models/gemini-3.8-live", provider="gemini"),
        "tts": ModelPresetConfig(label="Gemini TTS", model="models/gemini-3.8-flash-tts", provider="gemini"),
        "chat": ModelPresetConfig(label="Gemini Flash", model="models/gemini-3.8-flash", provider="gemini"),
    }
    save_config(cfg)
    choices = media_model_choices(cfg)
    assert [o["value"] for o in choices["live"]["options"]] == ["gemini::gemini-3.8-live"]
    assert [o["value"] for o in choices["tts"]["options"]] == ["gemini::gemini-3.8-flash-tts"]
    assert all(o["model"] != "models/gemini-3.8-flash" for kind in choices.values() for o in kind["options"])

    with patch("navin.webui.settings_api.settings_payload", return_value={}):
        update_live_voice_settings({"live_engine": ["realtime"], "live_realtime": ["gemini::models/gemini-3.8-live"],
                                    "live_realtime_voice": ["Kore"]})
    from navin.config.loader import load_config

    voice = load_config().voice
    assert (voice.live_engine, voice.live_realtime_provider, voice.live_realtime_model, voice.live_realtime_voice) == (
        "realtime", "gemini", "gemini-3.8-live", "Kore")


def test_pickers_list_the_connected_providers_models_without_adding_them_first():
    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    save_config(cfg)
    assert [p["provider"] for p in connected_media_providers(cfg)] == ["gemini"]

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": f"models/{m}"} for m in (
                "gemini-3.1-flash-live-preview", "gemini-2.5-flash-native-audio-latest",
                "gemini-3.5-transcribe-live", "gemini-3.8-flash-tts", "gemini-3.8-flash")]}

    with patch("navin.webui.settings_api.httpx.get", return_value=Reply()):
        live = provider_models_payload({"provider": ["gemini"], "modality": ["live"]})
    assert sorted(m["id"] for m in live["models"]) == ["gemini-2.5-flash-native-audio-latest", "gemini-3.1-flash-live-preview"]
    assert not is_realtime_voice_model("gemini-3.5-live-translate-preview")


def test_the_live_can_answer_from_navins_docs():
    results = search_docs("configure ollama local model")
    assert results and "ollama" in (results[0]["title"] + results[0]["excerpt"]).lower()
    assert results[0]["url"].startswith("https://navin.live/en/docs/")
    assert search_docs("") == []


def test_the_gateways_live_model_follows_the_catalog_unless_the_user_chose_byok():
    from navin.providers.managed_catalog import _apply_live_realtime_default, parse_catalog

    catalog = parse_catalog({"defaultModel": "anthropic/claude-sonnet-5.5", "models": [
        {"slug": "anthropic/claude-sonnet-5.5", "name": "Claude Sonnet 5.5", "tier": "main"}],
        "media": {"live": {"defaultModel": "amazon/nova-2-sonic", "models": [
        {"slug": "amazon/nova-2-sonic", "name": "Nova 2 Sonic"}]}}})
    cfg = Config()
    assert _apply_live_realtime_default(cfg, catalog) is True
    assert (cfg.voice.live_realtime_provider, cfg.voice.live_realtime_model) == ("navin", "amazon/nova-2-sonic")
    cfg.voice.live_realtime_provider = "gemini"
    cfg.voice.live_realtime_model = "models/gemini-3.8-live"
    assert _apply_live_realtime_default(cfg, catalog) is False
    assert cfg.voice.live_realtime_model == "models/gemini-3.8-live"


def test_switching_the_gateways_live_model_drops_the_old_models_voice():
    from navin.providers.managed_catalog import _apply_live_realtime_default, parse_catalog

    def catalog(slug: str):
        return parse_catalog({"defaultModel": "anthropic/claude-sonnet-5.5", "models": [
            {"slug": "anthropic/claude-sonnet-5.5", "name": "Claude Sonnet 5.5", "tier": "main"}],
            "media": {"live": {"defaultModel": slug, "models": [{"slug": slug, "name": slug}]}}})

    cfg = Config()
    _apply_live_realtime_default(cfg, catalog("amazon/nova-2-sonic"))
    cfg.voice.live_realtime_voice = "tiffany"
    assert _apply_live_realtime_default(cfg, catalog("amazon/nova-2-sonic")) is False
    assert cfg.voice.live_realtime_voice == "tiffany"  # same model: the user's voice stays
    assert _apply_live_realtime_default(cfg, catalog("gemini/gemini-live-2.5-flash")) is True
    assert cfg.voice.live_realtime_model == "gemini/gemini-live-2.5-flash"
    assert cfg.voice.live_realtime_voice == ""  # the new model's own default voice


def test_gemini_listening_saves_and_live_runs_on_the_realtime_model():
    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    save_config(cfg)
    query = {
        "enabled": ["true"], "provider": ["gemini"], "model": ["gemini-3.5-transcribe"], "language": [""],
        "max_duration_sec": ["120"], "max_upload_mb": ["25"], "tts_provider": ["gemini"],
        "tts_model": ["models/gemini-2.5-flash-preview-tts"], "voice": ["auto"], "auto_speak": ["false"],
        "response_format": ["wav"], "realtime_enabled": ["auto"], "live_engine": ["auto"],
        "live_realtime": ["gemini::models/gemini-3.8-live"], "live_realtime_voice": [""],
    }
    with patch("navin.webui.settings_api.settings_payload", return_value={}):
        update_live_voice_settings(query)
    from navin.audio.tts import live_voice_status
    from navin.config.loader import load_config

    saved = load_config()
    assert (saved.transcription.provider, saved.transcription.model) == ("gemini", "gemini-3.5-transcribe")
    status = live_voice_status(saved, include_realtime=True)
    assert status["ready"] and status["engine"] == "realtime"
    assert status["pipeline_ready"]
    # The CLI and the pipeline socket only look at listening + voice.
    assert live_voice_status(saved)["engine"] == "pipeline"


def test_live_is_ready_on_the_realtime_model_even_without_listening_and_voice():
    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    cfg.voice.live_realtime_provider = "gemini"
    cfg.voice.live_realtime_model = "gemini-3.8-live"
    cfg.transcription.provider = "navin"
    cfg.voice.tts_provider = "navin"
    from navin.audio.tts import live_voice_status

    status = live_voice_status(cfg, include_realtime=True)
    assert (status["ready"], status["engine"], status["pipeline_ready"]) == (True, "realtime", False)
    cfg.voice.live_engine = "pipeline"
    assert live_voice_status(cfg, include_realtime=True)["ready"] is False


def test_signed_out_hides_navin_models_and_gemini_ids_are_not_doubled():
    cfg = Config()
    cfg.providers.gemini.api_key = "AIza-user"
    cfg.voice.tts_provider = "navin"
    cfg.voice.tts_model = "google/gemini-3.1-flash-tts-preview"
    cfg.model_presets = {
        "img": ModelPresetConfig(label="Img", model="models/gemini-2.5-flash-image", provider="gemini"),
        "nav": ModelPresetConfig(label="Navin img", model="google/gemini-3.1-flash-image", provider="navin",
                                 modality="image"),
    }
    choices = media_model_choices(cfg)
    assert [o["value"] for o in choices["image"]["options"]] == ["gemini::gemini-2.5-flash-image"]
    assert choices["tts"] == {"selected": "", "options": []}
    assert [p["provider"] for p in connected_media_providers(cfg)] == ["gemini"]


def test_gemini_transcription_reads_both_answer_shapes(tmp_path):
    from navin.providers.transcription import GeminiTranscriptionProvider

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000WAVE")
    provider = GeminiTranscriptionProvider(
        api_key="k", api_base="https://generativelanguage.googleapis.com/v1beta/openai/", model="models/gemini-3.5-transcribe")
    assert provider.api_base == "https://generativelanguage.googleapis.com/v1beta"
    assert provider.model == "gemini-3.5-transcribe"
    shapes = [
        {"candidates": [{"content": {"parts": [{"audioTranscription": {"text": "Bonjour"}}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "pense", "thought": True}, {"text": "Bonjour"}]}}]},
    ]
    for shape in shapes:
        with patch("navin.providers.transcription._request_json_with_retry", return_value=shape) as call:
            assert asyncio.run(provider.transcribe(audio)) == "Bonjour"
        assert call.call_args.args[2].endswith("/models/gemini-3.5-transcribe:generateContent")


def test_the_calls_opening_line_reaches_gemini_live_as_a_turn():
    translator = GeminiLiveTranslator("gemini-3.8-live")
    greeting = translator.from_page({"type": "response.create", "response": {"instructions": 'Say exactly: "Salut Aymen"'}})
    assert greeting == [{"clientContent": {"turns": [{"role": "user", "parts": [{"text": 'Say exactly: "Salut Aymen"'}]}],
                                           "turnComplete": True}}]
    assert translator.from_page({"type": "response.create"}) == []
