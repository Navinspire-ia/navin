import base64
import json
from types import SimpleNamespace

from navin.live.nova_sonic import NovaSonicTranslator
from navin.providers.bedrock_media import (
    AWS_CREDENTIALS_MARKER,
    decode_target,
    encode_target,
    media_rows,
    model_region,
    provider_credentials,
)


def test_media_rows_per_use_and_region():
    assert [r["id"] for r in media_rows("stt")] == ["mistral.voxtral-small-24b-2507", "mistral.voxtral-mini-3b-2507"]
    assert [r["id"] for r in media_rows("live")] == ["amazon.nova-2-5-sonic", "amazon.nova-2-sonic-v1:0"]
    assert media_rows("live")[0]["owned_by"] == "AWS Bedrock · eu-north-1, us-east-1, us-west-2"
    assert media_rows("image") == [] and media_rows("video") == []
    assert model_region("mistral.voxtral-small-24b-2507", "eu-west-3") == "eu-west-1"
    assert model_region("amazon.nova-2-sonic-v1:0", "eu-west-3") == "eu-north-1"
    assert model_region("polly-neural", "eu-west-3") == "eu-west-3"


def test_nova_sonic_runs_in_a_region_that_serves_it_never_in_ohio():
    from navin.live.nova_sonic import DEFAULT_MODEL
    from navin.providers.bedrock_media import model_regions

    assert DEFAULT_MODEL == "amazon.nova-2-5-sonic"
    # In-Region only: the provider's region when it serves Sonic, else Stockholm.
    assert model_region(DEFAULT_MODEL, "us-east-1") == "us-east-1"
    assert model_region(DEFAULT_MODEL, "us-east-2") == "eu-north-1"
    assert model_region(DEFAULT_MODEL, None) == "eu-north-1"
    assert model_regions(DEFAULT_MODEL, "us-west-2") == ("us-west-2", "eu-north-1", "us-east-1")
    assert model_regions(DEFAULT_MODEL, "us-east-2") == ("eu-north-1", "us-east-1", "us-west-2")
    assert model_regions("mistral.voxtral-mini-3b-2507", "eu-west-3") == ("eu-west-1",)
    assert model_regions("polly-neural", None) == ("us-east-2",)


class _Page:
    """The browser side of a Live call: sends nothing, records what it gets."""

    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send(self, frame):
        self.sent.append(frame)

    async def close(self, *_):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _Stream:
    """A Nova Sonic stream that opened and ends at once."""

    def __init__(self):
        self.input_stream = self
        self.sent: list = []

    async def send(self, chunk):
        self.sent.append(chunk)

    async def close(self):
        return None

    async def await_output(self):
        return None, self

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def test_live_with_an_own_aws_key_moves_to_the_next_region_when_one_refuses(monkeypatch):
    import asyncio

    from navin.config.schema import Config
    from navin.live import nova_sonic
    from navin.live.realtime import bridge

    cfg = Config()
    cfg.voice.live_realtime_provider = "bedrock"
    cfg.providers.bedrock.profile = "own"
    cfg.providers.bedrock.region = "us-east-2"  # Ohio: no Nova Sonic there
    tried: list[str] = []
    refusing: set[str] = {"eu-north-1"}

    async def open_stream(model, region, profile):
        assert (model, profile) == ("amazon.nova-2-5-sonic", "own")
        tried.append(region)
        if region in refusing:
            raise RuntimeError(f"AccessDeniedException in {region}")
        return _Stream()

    monkeypatch.setattr(nova_sonic, "_open_stream", open_stream)
    page = _Page()
    asyncio.run(bridge(page, cfg))
    assert tried == ["eu-north-1", "us-east-1"]
    assert json.loads(page.sent[0]) == {"type": "navin.ready", "provider": "bedrock", "model": "amazon.nova-2-5-sonic"}

    # Every region refuses: the page is told once, with the last refusal, and falls back.
    tried.clear()
    refusing.update({"us-east-1", "us-west-2"})
    page = _Page()
    asyncio.run(bridge(page, cfg))
    assert tried == ["eu-north-1", "us-east-1", "us-west-2"]
    error = json.loads(page.sent[0])["error"]
    assert error["code"] == "realtime_unreachable" and "us-west-2" in error["message"]
    assert page.closed

    # No AWS credentials: no other region would sign any better.
    async def no_credentials(model, region, profile):
        tried.append(region)
        raise PermissionError("Live with Nova Sonic needs AWS access keys or an AWS profile")

    tried.clear()
    monkeypatch.setattr(nova_sonic, "_open_stream", no_credentials)
    page = _Page()
    asyncio.run(bridge(page, cfg))
    assert tried == ["eu-north-1"]
    assert json.loads(page.sent[0])["error"]["code"] == "realtime_unreachable"


def test_credentials_and_target(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(name, raising=False)
    assert provider_credentials(SimpleNamespace(api_key="", profile="", region="")) == ("", "")
    key, base = provider_credentials(SimpleNamespace(api_key="", profile="bedrock-eu", region="eu-west-3"))
    assert key == AWS_CREDENTIALS_MARKER and decode_target(base) == ("eu-west-3", "bedrock-eu")
    key, base = provider_credentials(SimpleNamespace(api_key="ABSK123", profile="", region=""))
    assert key == "ABSK123" and decode_target(base) == ("us-east-2", None)
    assert decode_target(encode_target("eu-north-1", None)) == ("eu-north-1", None)


def _pcm24(ms: int) -> str:
    return base64.b64encode(b"\x01\x00" * (24 * ms)).decode()


def test_sonic_session_start_and_audio():
    t = NovaSonicTranslator()
    assert t.from_page({"type": "input_audio_buffer.append", "audio": _pcm24(10)}) == []  # before setup
    events = t.from_page({"type": "session.update", "session": {
        "instructions": "Speak French.", "tools": [{"type": "function", "name": "run", "parameters": {"type": "object"}}]}})
    names = [next(iter(e)) for e in events]
    assert names[:2] == ["sessionStart", "promptStart"]
    assert events[1]["promptStart"]["audioOutputConfiguration"]["voiceId"] == "ambre"
    assert events[1]["promptStart"]["toolConfiguration"]["tools"][0]["toolSpec"]["name"] == "run"
    assert names[-1] == "contentStart" and events[-1]["contentStart"]["type"] == "AUDIO"
    audio = t.from_page({"type": "input_audio_buffer.append", "audio": _pcm24(30)})
    pcm16 = base64.b64decode(audio[0]["audioInput"]["content"])
    assert len(pcm16) == 2 * 16 * 30  # 24 kHz -> 16 kHz
    assert [next(iter(e)) for e in t.closing()] == ["contentEnd", "promptEnd", "sessionEnd"]


def test_sonic_output_to_page():
    t = NovaSonicTranslator()
    t.from_page({"type": "session.update", "session": {}})
    out = t.from_sonic({"textOutput": {"role": "USER", "content": "bonjour", "contentId": "u"}})
    assert out == [{"type": "conversation.item.input_audio_transcription.completed", "transcript": "bonjour"}]
    t.from_sonic({"contentStart": {"contentId": "s", "role": "ASSISTANT",
                                   "additionalModelFields": json.dumps({"generationStage": "SPECULATIVE"})}})
    t.from_sonic({"textOutput": {"role": "ASSISTANT", "content": "Salut !", "contentId": "s"}})
    t.from_sonic({"contentStart": {"contentId": "f", "role": "ASSISTANT",
                                   "additionalModelFields": json.dumps({"generationStage": "FINAL"})}})
    t.from_sonic({"textOutput": {"role": "ASSISTANT", "content": "Salut !", "contentId": "f"}})  # repeat, ignored
    out = t.from_sonic({"audioOutput": {"content": "AAA="}})
    assert [e["type"] for e in out] == ["response.created", "response.audio.delta"]
    out = t.from_sonic({"contentEnd": {"type": "AUDIO", "stopReason": "END_TURN"}})
    assert out[0] == {"type": "response.audio_transcript.done", "transcript": "Salut !"}
    assert out[1]["type"] == "response.done"
    assert t.from_sonic({"textOutput": {"role": "ASSISTANT", "content": '{ "interrupted" : true }'}}) == [
        {"type": "input_audio_buffer.speech_started"}]
    tool = t.from_sonic({"toolUse": {"toolUseId": "c1", "toolName": "run", "content": '{"a":1}'}})
    assert tool[-1] == {"type": "response.function_call_arguments.done", "call_id": "c1", "name": "run", "arguments": '{"a":1}'}
    result = t.from_page({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": "c1", "output": "ok"}})
    assert result[0]["contentStart"]["toolResultInputConfiguration"]["toolUseId"] == "c1"


def test_bedrock_is_a_live_and_media_provider(monkeypatch):
    from navin.audio.transcription_registry import resolve_transcription_provider
    from navin.audio.tts_registry import resolve_tts_provider
    from navin.providers.media_choice import LIVE_PROVIDERS

    assert "bedrock" in LIVE_PROVIDERS
    assert resolve_tts_provider("bedrock").default_voice == "Lea"
    assert resolve_transcription_provider("bedrock").default_model.startswith("mistral.voxtral")


def test_aws_session_assumes_the_central_inference_role(monkeypatch):
    import datetime

    import boto3

    from navin.providers.bedrock_media import aws_session

    calls = []

    class FakeSts:
        def assume_role(self, **kwargs):
            calls.append(kwargs)
            return {"Credentials": {
                "AccessKeyId": "AKIA", "SecretAccessKey": "s", "SessionToken": "t",
                "Expiration": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
            }}

    class FakeBase:
        def client(self, service):
            assert service == "sts"
            return FakeSts()

    monkeypatch.setattr(boto3, "Session", lambda **kw: kw.get("botocore_session") or FakeBase())
    role = "arn:aws:iam::712493797892:role/navin-gateway-bedrock-workloads"
    session = aws_session(None, role)
    creds = session.get_credentials()
    assert creds.access_key == "AKIA" and creds.token == "t"
    assert calls == [{"RoleArn": role, "RoleSessionName": "navin-bedrock"}]


def test_aws_session_without_role_keeps_local_credentials(monkeypatch):
    import boto3

    from navin.providers.bedrock_media import aws_session

    monkeypatch.delenv("NAVIN_BEDROCK_ROLE_ARN", raising=False)
    monkeypatch.setattr(boto3, "Session", lambda **kw: ("base", kw))
    assert aws_session("prod-01") == ("base", {"profile_name": "prod-01"})
