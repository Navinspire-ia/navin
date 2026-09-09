# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Audio attachments and video soundtracks must reach the model as text, or say why not."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from navin.utils import audio_transcripts
from navin.utils.audio_transcripts import (
    MAX_TRANSCRIPT_CHARS,
    AudioTranscript,
    append_video_soundtracks,
    describe_transcript,
    expand_audio_attachments,
    is_audio_path,
    transcribe_audio_attachment,
    transcribe_video_soundtrack,
)


@dataclass
class _Config:
    provider: str = "groq"
    model: str = "whisper-large-v3"
    language: str | None = "fr"
    enabled: bool = True
    configured: bool = True


def _run(coro):
    return asyncio.run(coro)


class TestIsAudioPath:
    def test_recognizes_common_containers(self):
        for name in ("note.mp3", "a.WAV", "rec.m4a", "x.ogg", "y.flac", "z.opus"):
            assert is_audio_path(name), name

    def test_rejects_video_images_and_documents(self):
        for name in ("clip.mp4", "clip.webm", "shot.png", "doc.pdf", "notes.md"):
            assert not is_audio_path(name), name

    def test_ignores_query_and_fragment(self):
        assert is_audio_path("https://cdn/voice.mp3?sig=abc#t=10")


class TestTranscribeAudioAttachment:
    def test_missing_file_is_reported_not_raised(self, tmp_path: Path):
        result = _run(transcribe_audio_attachment(tmp_path / "nope.mp3", cache_dir=tmp_path / "c"))
        assert not result.ok
        assert result.reason == "file not found"

    def test_no_provider_is_a_reason_the_model_can_read(self, tmp_path: Path, monkeypatch):
        audio = tmp_path / "note.mp3"
        audio.write_bytes(b"ID3" + b"\x00" * 16)
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: None)
        result = _run(transcribe_audio_attachment(audio, cache_dir=tmp_path / "c"))
        assert not result.ok
        assert "no speech-to-text provider" in (result.reason or "")

    def test_transcript_is_bounded_and_cached(self, tmp_path: Path, monkeypatch):
        audio = tmp_path / "note.mp3"
        audio.write_bytes(b"ID3" + b"\x00" * 16)
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())
        calls = {"count": 0}

        async def fake_transcribe(path, config, **_kw):
            calls["count"] += 1
            return "bonjour " * 10_000

        monkeypatch.setattr(
            "navin.audio.transcription.transcribe_audio_file", fake_transcribe
        )
        cache = tmp_path / "cache"
        first = _run(transcribe_audio_attachment(audio, cache_dir=cache))
        assert first.ok
        assert first.truncated
        assert len(first.text) <= MAX_TRANSCRIPT_CHARS + len(" [...]")
        assert first.provider == "groq"
        assert first.model == "whisper-large-v3"
        assert first.language == "fr"

        second = _run(transcribe_audio_attachment(audio, cache_dir=cache))
        assert second.text == first.text
        # Re-sending the same file must not pay the provider twice.
        assert calls["count"] == 1

    def test_provider_failure_becomes_a_reason(self, tmp_path: Path, monkeypatch):
        audio = tmp_path / "note.wav"
        audio.write_bytes(b"RIFF" + b"\x00" * 16)
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())

        async def boom(path, config, **_kw):
            raise RuntimeError("401 unauthorized")

        monkeypatch.setattr("navin.audio.transcription.transcribe_audio_file", boom)
        result = _run(transcribe_audio_attachment(audio, cache_dir=tmp_path / "c"))
        assert not result.ok
        assert "401 unauthorized" in (result.reason or "")

    def test_silence_is_reported(self, tmp_path: Path, monkeypatch):
        audio = tmp_path / "note.wav"
        audio.write_bytes(b"RIFF" + b"\x00" * 16)
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())

        async def silent(path, config, **_kw):
            return "   "

        monkeypatch.setattr("navin.audio.transcription.transcribe_audio_file", silent)
        result = _run(transcribe_audio_attachment(audio, cache_dir=tmp_path / "c"))
        assert not result.ok
        assert result.reason == "no speech detected"


class TestTranscribeVideoSoundtrack:
    def _video(self, tmp_path: Path) -> Path:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return video

    def test_missing_ffmpeg_is_reported(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())
        monkeypatch.setattr(audio_transcripts, "_find_ffmpeg", lambda: None)
        result = _run(transcribe_video_soundtrack(self._video(tmp_path), cache_dir=tmp_path / "c"))
        assert not result.ok
        assert "ffmpeg" in (result.reason or "")

    def test_provider_is_checked_before_extraction(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: None)
        calls = {"ffmpeg": 0}

        def find():
            calls["ffmpeg"] += 1
            return "/usr/bin/ffmpeg"

        monkeypatch.setattr(audio_transcripts, "_find_ffmpeg", find)
        result = _run(transcribe_video_soundtrack(self._video(tmp_path), cache_dir=tmp_path / "c"))
        assert "no speech-to-text provider" in (result.reason or "")
        assert calls["ffmpeg"] == 0

    def test_a_silent_video_says_no_audio_track(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())
        monkeypatch.setattr(audio_transcripts, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            audio_transcripts,
            "_run",
            lambda args, timeout: type(
                "Proc",
                (),
                {"returncode": 1, "stderr": "Output file #0 does not contain any stream"},
            )(),
        )
        result = _run(transcribe_video_soundtrack(self._video(tmp_path), cache_dir=tmp_path / "c"))
        assert not result.ok
        assert result.reason == "no audio track"

    def test_extraction_timeout_is_reported(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())
        monkeypatch.setattr(audio_transcripts, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")

        def slow(args, timeout):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

        monkeypatch.setattr(audio_transcripts, "_run", slow)
        result = _run(transcribe_video_soundtrack(self._video(tmp_path), cache_dir=tmp_path / "c"))
        assert result.reason == "audio extraction timed out"

    def test_extracted_audio_is_transcribed_with_a_duration_cap(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(audio_transcripts, "_transcription_config", lambda: _Config())
        monkeypatch.setattr(audio_transcripts, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        seen: dict[str, list[str]] = {}

        def fake_run(args, timeout):
            seen["args"] = args
            Path(args[-1]).write_bytes(b"RIFF" + b"\x00" * 64)
            return type("Proc", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(audio_transcripts, "_run", fake_run)

        async def fake_transcribe(path, config, **_kw):
            assert Path(path).suffix == ".wav"
            return "Et voila la demo."

        monkeypatch.setattr("navin.audio.transcription.transcribe_audio_file", fake_transcribe)
        result = _run(transcribe_video_soundtrack(self._video(tmp_path), cache_dir=tmp_path / "c"))

        assert result.ok
        assert result.text == "Et voila la demo."
        args = seen["args"]
        assert "-vn" in args
        assert args[args.index("-t") + 1] == str(audio_transcripts.SOUNDTRACK_MAX_SECONDS)


class TestDescribeTranscript:
    def test_success_note_quotes_the_text_and_its_origin(self):
        note = describe_transcript(
            AudioTranscript(
                source="/tmp/memo.mp3",
                text="Rappelle le client demain.",
                provider="groq",
                model="whisper-large-v3",
                language="fr",
            )
        )
        assert note.startswith("[audio: memo.mp3 - transcript via whisper-large-v3 groq, language fr]")
        assert "Rappelle le client demain." in note
        assert note.endswith("[end of audio: memo.mp3]")

    def test_video_note_says_it_is_speech_only(self):
        note = describe_transcript(
            AudioTranscript(source="/tmp/demo.mp4", text="hello"), kind="video audio"
        )
        assert note.startswith("[video audio: demo.mp4 - transcript, speech only")

    def test_failure_note_names_the_reason(self):
        note = describe_transcript(AudioTranscript(source="/tmp/x.mp3", reason="no audio track"))
        assert note == "[audio: x.mp3 - not transcribed: no audio track]"


class TestExpandAudioAttachments:
    def test_non_audio_media_passes_through_untouched(self):
        media = ["/tmp/a.png", "/tmp/notes.pdf", "/tmp/clip.mp4"]
        text, out = _run(expand_audio_attachments("hello", media))
        assert text == "hello"
        assert out == media

    def test_audio_becomes_a_note_and_leaves_the_media_list(self, monkeypatch):
        async def fake(path, **_kw):
            return AudioTranscript(source=path, text="ok then", provider="navin", model="whisper-1")

        monkeypatch.setattr(audio_transcripts, "transcribe_audio_attachment", fake)
        text, media = _run(expand_audio_attachments("listen", ["/tmp/a.png", "/tmp/memo.mp3"]))
        assert media == ["/tmp/a.png"]
        assert "listen" in text
        assert "[audio: memo.mp3 - transcript via whisper-1 navin]" in text
        assert "ok then" in text

    def test_channel_inline_transcription_is_not_redone(self, monkeypatch):
        calls = {"count": 0}

        async def fake(path, **_kw):
            calls["count"] += 1
            return AudioTranscript(source=path, text="again")

        monkeypatch.setattr(audio_transcripts, "transcribe_audio_attachment", fake)
        text, media = _run(
            expand_audio_attachments("[transcription: hello there]", ["/tmp/voice.ogg"])
        )
        assert calls["count"] == 0
        assert media == []
        assert text == "[transcription: hello there]"

    def test_empty_media_is_a_noop(self):
        assert _run(expand_audio_attachments("hi", [])) == ("hi", [])


class TestAppendVideoSoundtracks:
    def test_notes_follow_the_text(self, monkeypatch):
        async def fake(path, **_kw):
            return AudioTranscript(source=path, reason="no audio track")

        monkeypatch.setattr(audio_transcripts, "transcribe_video_soundtrack", fake)
        text = _run(append_video_soundtracks("watch", ["/tmp/clip.mp4"]))
        assert text == "watch\n\n[video audio: clip.mp4 - not transcribed: no audio track]"

    def test_no_videos_is_a_noop(self):
        assert _run(append_video_soundtracks("watch", [])) == "watch"


class TestPipelineOrder:
    """Transcript notes must survive the document step that runs next."""

    def test_audio_note_reaches_the_model_through_extract_documents(self, tmp_path: Path, monkeypatch):
        from navin.utils.document import extract_documents

        async def fake(path, **_kw):
            return AudioTranscript(source=path, text="deploy on friday")

        monkeypatch.setattr(audio_transcripts, "transcribe_audio_attachment", fake)
        memo = tmp_path / "memo.mp3"
        memo.write_bytes(b"ID3" + b"\x00" * 16)

        text, media = _run(expand_audio_attachments("plan", [str(memo)]))
        text, images = extract_documents(text, media)

        assert images == []
        assert "deploy on friday" in text
        assert "[Attachment:" not in text


@pytest.mark.parametrize("marker", ["Output file #0 does not contain any stream", "matches no streams"])
def test_no_audio_markers_are_recognized(marker: str):
    assert any(m in marker.lower() for m in audio_transcripts._NO_AUDIO_MARKERS)
