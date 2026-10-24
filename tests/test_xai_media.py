import asyncio
import base64
import json

import httpx
import pytest

import navin.audio  # noqa: F401 - app import order
from navin.live.realtime import XaiTranscripts, resolve_realtime_target
from navin.providers import image_generation, transcription, tts, video_generation
from navin.providers.media_catalog import static_media_rows


def _mock(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("trust_env", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def test_tts_uses_xai_format(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3audio")

    _mock(monkeypatch, tts, handler)
    audio = asyncio.run(tts.XaiTtsProvider(api_key="k", voice="ara").synthesize("Bonjour"))
    assert audio == b"ID3audio"
    assert seen["url"] == "https://api.x.ai/v1/tts"
    assert seen["body"] == {"text": "Bonjour", "language": "auto", "voice_id": "ara",
                            "output_format": {"codec": "mp3", "sample_rate": 24000}}


def test_stt_multipart(monkeypatch, tmp_path):
    take = tmp_path / "take.webm"
    take.write_bytes(b"webm")

    def handler(request):
        assert str(request.url) == "https://api.x.ai/v1/stt"
        assert b'name="model"' in request.content and b"grok-voice-transcribe-2.0" in request.content
        return httpx.Response(200, json={"text": " bonjour ", "language": "fr"})

    _mock(monkeypatch, transcription, handler)
    assert asyncio.run(transcription.XaiTranscriptionProvider(api_key="k").transcribe(take)) == "bonjour"


def test_image_generation(monkeypatch):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/images/generations"
        assert body["model"] == "grok-imagine-image-2.0" and body["aspect_ratio"] == "16:9"
        assert "size" not in body
        return httpx.Response(200, json={"data": [{"b64_json": png}]})

    _mock(monkeypatch, image_generation, handler)
    client = image_generation.XaiImageGenerationClient(api_key="k")
    result = asyncio.run(client.generate(prompt="a cat", model="grok-imagine-image-2.0", aspect_ratio="16:9"))
    assert result.images


def test_video_job_poll_and_download(monkeypatch):
    states = iter(["pending", "done"])

    def handler(request):
        if request.method == "POST":
            assert request.url.path == "/v1/videos/generations"
            assert json.loads(request.content)["duration"] == 6
            return httpx.Response(200, json={"request_id": "r1"})
        if request.url.path == "/v1/videos/r1":
            state = next(states)
            return httpx.Response(200, json={"status": state, "video": {"url": "https://cdn.x.ai/v.mp4"} if state == "done" else None})
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42video")

    _mock(monkeypatch, video_generation, handler)
    client = video_generation.XaiVideoGenerationClient(api_key="k", poll_interval=0.01)
    result = asyncio.run(client.generate(prompt="waves", model="grok-imagine-video-1.5", duration_seconds=6))
    assert result.mime == "video/mp4"


def test_live_target_and_transcripts(monkeypatch):
    from navin.config.schema import Config

    config = Config()
    config.providers.xai.api_key = "k"
    config.voice.live_realtime_provider = "xai"
    config.voice.live_realtime_model = "grok-voice-latest"
    target = resolve_realtime_target(config)
    assert target.url == "wss://api.x.ai/v1/realtime?model=grok-voice-latest"
    assert target.headers == {"Authorization": "Bearer k"}

    t = XaiTranscripts()
    assert t.events(json.dumps({"type": "conversation.item.input_audio_transcription.updated", "item_id": "i", "transcript": "bon"})) == []
    t.events(json.dumps({"type": "conversation.item.input_audio_transcription.updated", "item_id": "i", "transcript": "bonjour"}))
    out = [json.loads(m) for m in t.events(json.dumps({"type": "response.created"}))]
    assert out[0] == {"type": "conversation.item.input_audio_transcription.completed", "item_id": "i", "transcript": "bonjour"}
    assert out[1]["type"] == "response.created"


@pytest.mark.parametrize("kind", ["tts", "stt", "live", "image", "video"])
def test_pickers_list_grok_models(kind):
    assert static_media_rows("xai", kind)
