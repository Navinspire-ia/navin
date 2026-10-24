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
    assert media_rows("live")[0]["id"] == "amazon.nova-2-sonic-v1:0"
    assert media_rows("image") == [] and media_rows("video") == []
    assert model_region("mistral.voxtral-small-24b-2507", "eu-west-3") == "eu-west-1"
    assert model_region("amazon.nova-2-sonic-v1:0", "eu-west-3") == "eu-north-1"
    assert model_region("polly-neural", "eu-west-3") == "eu-west-3"


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
