"""Voice over generation: the missing layer of a full video.

The TTS engine already existed for the realtime voice session, but no tool
exposed it, so "une video avec voix off" could never be fulfilled. These tests
cover the tool contract, the artifact on disk, and the failure modes that must
stay readable rather than raise.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from navin.agent.tools.speech_generation import (
    MAX_SPEECH_CHARS,
    SpeechGenerationTool,
    SpeechGenerationToolConfig,
    resolve_speech_mime,
    validate_speech_text,
)
from navin.audio.tts import EffectiveTtsConfig, TtsError
from navin.utils.artifacts import (
    ArtifactError,
    generated_speech_tool_result,
    store_generated_speech_artifact,
)


def _tts(configured: bool = True, response_format: str = "mp3") -> EffectiveTtsConfig:
    return EffectiveTtsConfig(
        provider="navin",
        model="tts-1",
        voice="alloy",
        auto_speak=False,
        api_key="sk-test" if configured else "",
        api_base="https://api.example.test/v1",
        response_format=response_format,
    )


def _tool(**overrides) -> SpeechGenerationTool:
    return SpeechGenerationTool(
        workspace="/tmp",
        config=SpeechGenerationToolConfig(**overrides),
    )


def _run(coro):
    return asyncio.run(coro)


class TestTextValidation:
    def test_the_script_is_returned_trimmed(self):
        assert validate_speech_text("  bonjour le monde  ") == "bonjour le monde"

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_empty_scripts_are_refused(self, value):
        with pytest.raises(ValueError, match="required"):
            validate_speech_text(value)

    def test_a_runaway_script_is_refused_before_burning_quota(self):
        with pytest.raises(ValueError, match="over the"):
            validate_speech_text("a" * (MAX_SPEECH_CHARS + 1))

    def test_the_limit_itself_is_allowed(self):
        assert len(validate_speech_text("a" * MAX_SPEECH_CHARS)) == MAX_SPEECH_CHARS


class TestMimeMapping:
    @pytest.mark.parametrize(
        ("fmt", "mime"),
        [
            ("mp3", "audio/mpeg"),
            ("wav", "audio/wav"),
            ("opus", "audio/opus"),
            ("flac", "audio/flac"),
            ("WAV", "audio/wav"),
        ],
    )
    def test_known_formats(self, fmt, mime):
        assert resolve_speech_mime(fmt) == mime

    @pytest.mark.parametrize("fmt", ["", None, "weird"])
    def test_unknown_formats_fall_back_to_mp3(self, fmt):
        assert resolve_speech_mime(fmt) == "audio/mpeg"


class TestArtifact:
    def test_a_speech_artifact_lands_on_disk_with_its_sidecar(self):
        meta = store_generated_speech_artifact(
            b"ID3fake-audio",
            mime="audio/mpeg",
            text="bonjour",
            model="tts-1",
            voice="alloy",
            language="fr-FR",
        )
        assert meta["id"].startswith("spk_")
        assert os.path.isfile(meta["path"])
        assert meta["kind"] == "speech"
        assert meta["language"] == "fr-FR"
        assert meta["size_bytes"] == len(b"ID3fake-audio")
        sidecar = meta["path"].rsplit(".", 1)[0] + ".json"
        assert json.loads(open(sidecar, encoding="utf-8").read())["voice"] == "alloy"

    def test_speech_is_stored_apart_from_music(self):
        # The montage step picks the spoken track for ducking, so it must not have
        # to guess which audio artifact is narration.
        meta = store_generated_speech_artifact(
            b"x", mime="audio/mpeg", text="hi", model="tts-1", voice="alloy"
        )
        assert "generated-speech" in meta["path"]

    def test_an_unknown_mime_still_produces_a_playable_file(self):
        meta = store_generated_speech_artifact(
            b"x", mime="audio/unknown-thing", text="hi", model="tts-1", voice="alloy"
        )
        assert meta["path"].endswith(".mp3")
        assert meta["mime"] == "audio/mpeg"

    def test_an_empty_payload_is_refused(self):
        with pytest.raises(ArtifactError):
            store_generated_speech_artifact(
                b"", mime="audio/mpeg", text="hi", model="tts-1", voice="alloy"
            )

    def test_the_tool_result_points_the_model_at_delivery_and_montage(self):
        payload = json.loads(generated_speech_tool_result([{"id": "spk_1"}]))
        assert payload["artifacts"] == [{"id": "spk_1"}]
        assert "message tool" in payload["next_step"]
        assert "montage" in payload["next_step"]


class TestExecute:
    def test_the_happy_path_stores_the_narration(self, monkeypatch):
        calls: list[tuple[str, str, str]] = []

        async def fake_synth(text, config):
            calls.append((text, config.voice, config.model))
            return b"ID3spoken"

        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config", lambda _config: _tts()
        )
        monkeypatch.setattr(
            "navin.audio.tts.synthesize_speech_with_config", fake_synth
        )

        out = _run(_tool().execute(text="Bonjour et bienvenue"))
        payload = json.loads(str(out))
        assert calls == [("Bonjour et bienvenue", "alloy", "tts-1")]
        assert payload["artifacts"][0]["text"] == "Bonjour et bienvenue"
        assert os.path.isfile(payload["artifacts"][0]["path"])

    def test_a_voice_argument_overrides_the_configured_voice(self, monkeypatch):
        seen: list[str] = []

        async def fake_synth(text, config):
            seen.append(config.voice)
            return b"ID3spoken"

        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config", lambda _config: _tts()
        )
        monkeypatch.setattr(
            "navin.audio.tts.synthesize_speech_with_config", fake_synth
        )

        _run(_tool().execute(text="salut", voice="nova"))
        assert seen == ["nova"]

    def test_the_configured_voice_wins_over_the_realtime_default(self, monkeypatch):
        seen: list[str] = []

        async def fake_synth(text, config):
            seen.append(config.voice)
            return b"ID3spoken"

        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config", lambda _config: _tts()
        )
        monkeypatch.setattr(
            "navin.audio.tts.synthesize_speech_with_config", fake_synth
        )

        _run(_tool(voice="shimmer").execute(text="salut"))
        assert seen == ["shimmer"]

    def test_a_missing_credential_reads_as_a_setup_problem(self, monkeypatch):
        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config",
            lambda _config: _tts(configured=False),
        )
        out = str(_run(_tool().execute(text="salut")))
        assert "text-to-speech" in out
        assert "Error" in out

    def test_an_empty_script_never_reaches_the_provider(self, monkeypatch):
        called = False

        async def fake_synth(text, config):
            nonlocal called
            called = True
            return b"x"

        monkeypatch.setattr(
            "navin.audio.tts.synthesize_speech_with_config", fake_synth
        )
        out = str(_run(_tool().execute(text="   ")))
        assert "Error" in out
        assert called is False

    def test_a_provider_failure_is_reported_not_raised(self, monkeypatch):
        async def boom(text, config):
            raise TtsError("synthesis_failed", provider="navin")

        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config", lambda _config: _tts()
        )
        monkeypatch.setattr("navin.audio.tts.synthesize_speech_with_config", boom)

        out = str(_run(_tool().execute(text="salut")))
        assert "Error" in out

    def test_the_response_format_drives_the_stored_mime(self, monkeypatch):
        async def fake_synth(text, config):
            return b"RIFFfake"

        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config",
            lambda _config: _tts(response_format="wav"),
        )
        monkeypatch.setattr(
            "navin.audio.tts.synthesize_speech_with_config", fake_synth
        )

        payload = json.loads(str(_run(_tool().execute(text="salut"))))
        assert payload["artifacts"][0]["mime"] == "audio/wav"
        assert payload["artifacts"][0]["path"].endswith(".wav")


class TestToolSurface:
    def test_the_tool_is_discovered_by_the_loader(self):
        from navin.agent.tools.loader import ToolLoader

        names = {cls.__name__ for cls in ToolLoader().discover()}
        assert "SpeechGenerationTool" in names

    def test_config_key_matches_the_schema_field(self):
        from navin.config.schema import ToolsConfig

        assert SpeechGenerationTool.config_key == "speech_generation"
        assert hasattr(ToolsConfig(), "speech_generation")

    def test_an_explicit_disable_is_respected(self):
        class Ctx:
            config = type(
                "T", (), {"speech_generation": SpeechGenerationToolConfig(enabled=False)}
            )()

        assert SpeechGenerationTool.enabled(Ctx()) is False

    def test_an_explicit_enable_skips_credential_probing(self):
        class Ctx:
            config = type(
                "T", (), {"speech_generation": SpeechGenerationToolConfig(enabled=True)}
            )()

        assert SpeechGenerationTool.enabled(Ctx()) is True

    def test_auto_mode_follows_the_voice_credential(self, monkeypatch):
        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config", lambda _config: _tts()
        )

        class Ctx:
            config = type(
                "T", (), {"speech_generation": SpeechGenerationToolConfig()}
            )()

        assert SpeechGenerationTool.enabled(Ctx()) is True

        monkeypatch.setattr(
            "navin.audio.tts.resolve_tts_config",
            lambda _config: _tts(configured=False),
        )
        assert SpeechGenerationTool.enabled(Ctx()) is False
