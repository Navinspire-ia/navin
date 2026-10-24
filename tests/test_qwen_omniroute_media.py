import asyncio
import base64
import io
import json
import wave

import httpx

import navin.audio  # noqa: F401 - app import order
from navin.live.realtime import QwenRealtimeFrames, resolve_realtime_target
from navin.providers import image_generation, qwen_media, video_generation
from navin.providers.media_models import media_model_kinds


def _mock(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("trust_env", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def _wav(frames: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(frames)
    return out.getvalue()


def test_native_host_follows_the_qwen_provider():
    assert qwen_media.native_host("") == "https://dashscope-intl.aliyuncs.com"
    assert qwen_media.native_host("https://ws1.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1") == \
        "https://ws1.ap-southeast-1.maas.aliyuncs.com"


def test_tts_long_text_is_cut_and_joined(monkeypatch):
    calls = []

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            calls.append(body["input"]["text"])
            assert request.url.path == "/api/v1/services/aigc/multimodal-generation/generation"
            return httpx.Response(200, json={"output": {"audio": {"url": f"https://oss/a{len(calls)}.wav"}}})
        return httpx.Response(200, content=_wav(b"\x01\x00" * 100))

    _mock(monkeypatch, qwen_media, handler)
    text = "Bonjour tout le monde. " * 60  # > 600 characters
    audio = asyncio.run(qwen_media.QwenTtsProvider(api_key="k").synthesize(text))
    assert len(calls) >= 2 and all(len(c) <= qwen_media.TTS_MAX_CHARS for c in calls)
    with wave.open(io.BytesIO(audio), "rb") as reader:
        assert reader.getnframes() == 100 * len(calls)


def test_asr_sends_a_data_url(monkeypatch, tmp_path):
    take = tmp_path / "take.webm"
    take.write_bytes(b"webm")

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        data = body["messages"][0]["content"][0]["input_audio"]["data"]
        assert data.startswith("data:audio/webm;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": "bonjour"}}]})

    _mock(monkeypatch, qwen_media, handler)
    assert asyncio.run(qwen_media.QwenTranscriptionProvider(api_key="k", language="fr").transcribe(take)) == "bonjour"


def test_image_downloads_the_result(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["parameters"]["size"] == "1664*928"
            return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": "https://oss/i.png"}]}}]}})
        return httpx.Response(200, content=png)

    _mock(monkeypatch, image_generation, handler)
    client = image_generation.QwenImageGenerationClient(api_key="k")
    result = asyncio.run(client.generate(prompt="a cat", model="qwen-image-plus", aspect_ratio="16:9"))
    assert result.images[0].startswith("data:image/png;base64,")


def test_video_async_task(monkeypatch):
    states = iter(["RUNNING", "SUCCEEDED"])

    def handler(request):
        if request.method == "POST":
            assert request.headers["X-DashScope-Async"] == "enable"
            assert json.loads(request.content)["parameters"]["ratio"] == "9:16"
            return httpx.Response(200, json={"output": {"task_id": "t1", "task_status": "PENDING"}})
        if request.url.path == "/api/v1/tasks/t1":
            state = next(states)
            return httpx.Response(200, json={"output": {"task_status": state, "video_url": "https://oss/v.mp4"}})
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42video")

    _mock(monkeypatch, video_generation, handler)
    client = video_generation.QwenVideoGenerationClient(api_key="k", poll_interval=0.01)
    result = asyncio.run(client.generate(prompt="waves", model="wan2.7-t2v-2026-06-12", aspect_ratio="9:16"))
    assert result.mime == "video/mp4"


def test_qwen_live_target_and_frames():
    from navin.config.schema import Config

    config = Config()
    config.providers.qwen.api_key = "k"
    config.voice.live_realtime_provider = "qwen"
    config.voice.live_realtime_model = "qwen3.8-omni-flash-realtime"
    target = resolve_realtime_target(config)
    assert target.url == "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime"

    frames = QwenRealtimeFrames()
    session = json.loads(frames.from_page(json.dumps({"type": "session.update", "session": {"voice": "alloy", "instructions": "hi"}})))
    assert session["session"]["voice"] == "Cherry" and session["session"]["input_audio_format"] == "pcm"
    audio24 = base64.b64encode(b"\x01\x00" * 2400).decode()
    out = json.loads(frames.from_page(json.dumps({"type": "input_audio_buffer.append", "audio": audio24})))
    assert len(base64.b64decode(out["audio"])) == 2 * 1600  # 24 kHz -> 16 kHz


def test_omniroute_catalog_types():
    assert media_model_kinds("openai/tts-1", {"type": "audio", "subtype": "speech"}) == ["tts"]
    assert media_model_kinds("deepgram/nova-3", {"type": "audio", "subtype": "transcription"}) == ["stt"]
    assert media_model_kinds("together/flux", {"type": "image"}) == ["image"]
    assert media_model_kinds("kie/veo", {"type": "video"}) == ["video"]
