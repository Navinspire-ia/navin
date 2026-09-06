"""Voice-clone capability: model detection and payload extras.

The HTTP call itself stays the existing TTS path. These tests lock the
rules so a BYOK user typing a Fish Audio slug gets cloning, and a Groq
or local setup never silently sends a reference the provider cannot use.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import httpx
import pytest

from navin.audio.tts import synthesize_speech
from navin.audio.tts_clone import model_supports_reference, reference_extra_fields
from navin.montage.localize import LocalizeError, build_dub_track
from navin.providers import tts as tts_providers


class TestModelSupportsReference:
    def test_fish_audio_slug_on_navin(self) -> None:
        assert model_supports_reference("fish-audio/s2.1-pro", "navin") is True
        assert model_supports_reference("fish-audio/s2.1-pro-free:free", "openrouter") is True

    def test_byok_user_types_the_slug_in_the_free_field(self) -> None:
        assert model_supports_reference("fish-audio/s2.1-pro", "openrouter") is True
        assert model_supports_reference("elevenlabs/eleven-multilingual-v2") is True

    def test_catalogue_providers_never_clone_even_with_a_fancy_slug(self) -> None:
        assert model_supports_reference("fish-audio/s2.1-pro", "openai") is False
        assert model_supports_reference("fish-audio/s2.1-pro", "groq") is False
        assert model_supports_reference("fish-audio/s2.1-pro", "gemini") is False
        assert model_supports_reference("fish-audio/s2.1-pro", "ollama") is False
        assert model_supports_reference("fish-audio/s2.1-pro", "lm_studio") is False

    def test_default_navin_gemini_tts_does_not_clone(self) -> None:
        assert (
            model_supports_reference("google/gemini-3.1-flash-tts-preview", "navin")
            is False
        )

    def test_empty_is_safe(self) -> None:
        assert model_supports_reference("", "navin") is False
        assert model_supports_reference(None, None) is False


class TestReferencePayload:
    def test_wraps_bytes_as_base64_and_leaves_core_keys_out(self) -> None:
        extra = reference_extra_fields(b"fake-wav")
        assert set(extra) == {"references"}
        encoded = extra["references"][0]["audio"]
        assert base64.b64decode(encoded) == b"fake-wav"
        assert extra["references"][0]["text"] == ""

    def test_empty_audio_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            reference_extra_fields(b"")


@pytest.mark.asyncio
async def test_synthesize_without_extras_keeps_the_four_field_payload() -> None:
    """Existing callers must see the same JSON body they always did."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {
            "model": "tts-1",
            "input": "Hello Navin",
            "voice": "alloy",
            "response_format": "mp3",
        }
        return httpx.Response(200, content=b"ID3ok")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with patch.object(tts_providers.httpx, "AsyncClient", side_effect=factory):
        audio = await synthesize_speech(
            "Hello Navin",
            "openai",
            "alloy",
            "sk-test",
            api_base="https://api.openai.com/v1",
            model="tts-1",
        )
    assert audio == b"ID3ok"


@pytest.mark.asyncio
async def test_synthesize_merges_reference_without_overwriting_core_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "fish-audio/s2.1-pro"
        assert body["input"] == "Salam"
        assert body["voice"] == "eve"
        assert "references" in body
        # A hostile extra cannot replace the spoken text.
        assert body["input"] != "hijack"
        return httpx.Response(200, content=b"ID3clone")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    extras = reference_extra_fields(b"sample")
    extras["input"] = "hijack"
    extras["model"] = "should-not-win"
    with patch.object(tts_providers.httpx, "AsyncClient", side_effect=factory):
        audio = await synthesize_speech(
            "Salam",
            "openrouter",
            "eve",
            "sk-or",
            api_base="https://openrouter.ai/api/v1",
            model="fish-audio/s2.1-pro",
            extra_fields=extras,
        )
    assert audio == b"ID3clone"


@pytest.mark.asyncio
async def test_voicetrack_refuses_a_reference_on_a_catalogue_model(
    tmp_path,
) -> None:
    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")

    fake_config = type(
        "Cfg",
        (),
        {
            "configured": True,
            "model": "tts-1",
            "provider": "openai",
            "voice": "alloy",
            "response_format": "mp3",
        },
    )()

    with (
        patch("navin.montage.localize.find_ffmpeg", create=True),
        patch(
            "navin.audio.tts.resolve_tts_config",
            return_value=fake_config,
        ),
        patch("navin.config.loader.load_config", return_value=object()),
        patch("navin.montage.detect.find_ffmpeg", return_value="/bin/ffmpeg"),
    ):
        with pytest.raises(LocalizeError, match="cannot clone"):
            await build_dub_track(tmp_path, str(srt), reference=str(ref))
