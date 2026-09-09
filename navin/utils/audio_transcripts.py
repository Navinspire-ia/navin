# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn audio attachments, and the soundtrack of video attachments, into text.

No chat provider takes an mp3 on the content format used everywhere here, so
an audio file attached in the chat was refused at the door, and a video only
reached the model as a handful of stills with the explicit warning that its
audio was not available. Both gaps close the same way: run the sound through
the speech-to-text provider the voice mode already uses and append the
transcript to the turn as text the model can quote.

Every failure degrades to a one-line note that says what happened, so the
model never claims to have listened to something it did not receive.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

# ``.webm`` is deliberately absent: it is also a video container, and an
# ambiguous file goes through the video path whose soundtrack step transcribes
# it anyway.
AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".mpga", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".weba"}
)

# A transcript is text the model reads on every call of the turn; a long
# recording is summarized by the model from this bounded excerpt.
MAX_TRANSCRIPT_CHARS = 20_000
# The soundtrack of a video attachment is capped so a long recording cannot
# turn one message into a very long extraction and STT upload.
SOUNDTRACK_MAX_SECONDS = 600

_EXTRACT_TIMEOUT_S = 90
_TRANSCRIBE_TIMEOUT_S = 240

# ffmpeg's own words for "this container has no audio stream".
_NO_AUDIO_MARKERS = (
    "does not contain any stream",
    "matches no streams",
    "output file is empty",
)
# Channels that transcribe voice notes themselves leave this marker in the
# text (Telegram, Matrix); the attachment must not be transcribed twice.
_INLINE_TRANSCRIPTION_RE = re.compile(r"\[transcription:", re.IGNORECASE)


@dataclass(slots=True)
class AudioTranscript:
    """Result of transcribing one sound source.

    ``text`` is empty when nothing could be transcribed; ``reason`` then says
    why, in a form safe to show the model.
    """

    source: str
    text: str = ""
    provider: str | None = None
    model: str | None = None
    language: str | None = None
    truncated: bool = False
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text)


def is_audio_path(path: str | Path) -> bool:
    """True when *path* looks like an audio file by extension."""
    text = str(path).split("?", 1)[0].split("#", 1)[0]
    return Path(text).suffix.lower() in AUDIO_EXTENSIONS


def transcripts_cache_root() -> Path:
    """Where transcripts are cached, next to the sampled video frames."""
    return Path.home() / ".navin" / "cache" / "transcripts"


def _cache_file(source: Path, base_dir: Path | None) -> Path | None:
    try:
        stat = source.stat()
    except OSError:
        return None
    fingerprint = f"{source.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
    root = base_dir if base_dir is not None else transcripts_cache_root()
    return root / f"{digest}.json"


def _read_cache(cache: Path | None, source: str) -> AudioTranscript | None:
    if cache is None or not cache.is_file():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None
    return AudioTranscript(
        source=source,
        text=text,
        provider=data.get("provider") or None,
        model=data.get("model") or None,
        language=data.get("language") or None,
        truncated=bool(data.get("truncated")),
    )


def _write_cache(cache: Path | None, result: AudioTranscript) -> None:
    if cache is None or not result.ok:
        return
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "text": result.text,
                    "provider": result.provider,
                    "model": result.model,
                    "language": result.language,
                    "truncated": result.truncated,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("transcript cache write failed for {}: {}", result.source, exc)


def _find_ffmpeg() -> str | None:
    try:
        from navin.montage.detect import find_ffmpeg
    except Exception:  # pragma: no cover - montage package always ships
        return None
    try:
        return find_ffmpeg()
    except Exception:
        return None


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    from navin.utils.proc import no_window_kwargs

    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )


def _transcription_config() -> Any:
    """The resolved speech-to-text config, ``None`` when nothing can transcribe."""
    try:
        from navin.audio.transcription import resolve_transcription_config
        from navin.config.loader import load_config

        config = resolve_transcription_config(load_config())
    except Exception as exc:
        logger.debug("transcription config unavailable: {}", exc)
        return None
    if not getattr(config, "enabled", False) or not getattr(config, "configured", False):
        return None
    return config


def _bounded(text: str) -> tuple[str, bool]:
    cleaned = text.strip()
    if len(cleaned) <= MAX_TRANSCRIPT_CHARS:
        return cleaned, False
    return cleaned[:MAX_TRANSCRIPT_CHARS].rstrip() + " [...]", True


async def _transcribe_file(path: Path, result: AudioTranscript) -> AudioTranscript:
    """Fill *result* from the configured provider. Never raises."""
    config = _transcription_config()
    if config is None:
        result.reason = (
            "no speech-to-text provider is configured (transcription settings)"
        )
        return result
    from navin.audio.transcription import transcribe_audio_file

    try:
        text = await asyncio.wait_for(
            transcribe_audio_file(path, config), timeout=_TRANSCRIBE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        result.reason = "transcription timed out"
        return result
    except Exception as exc:
        logger.warning("transcription failed for {}: {}", path, exc)
        result.reason = f"transcription failed: {exc}"
        return result
    text, truncated = _bounded(text or "")
    if not text:
        result.reason = "no speech detected"
        return result
    result.text = text
    result.truncated = truncated
    result.provider = str(getattr(config, "provider", "") or "") or None
    result.model = str(getattr(config, "model", "") or "") or None
    result.language = getattr(config, "language", None) or None
    return result


async def transcribe_audio_attachment(
    path: str | Path, *, cache_dir: Path | None = None
) -> AudioTranscript:
    """Transcribe one audio file with the configured provider. Never raises."""
    source = Path(path)
    result = AudioTranscript(source=str(source))
    if not source.is_file():
        result.reason = "file not found"
        return result
    cache = _cache_file(source, cache_dir)
    cached = _read_cache(cache, str(source))
    if cached is not None:
        return cached
    result = await _transcribe_file(source, result)
    _write_cache(cache, result)
    return result


def _extract_soundtrack(ffmpeg: str, video: Path, target: Path) -> str | None:
    """Write the audio of *video* as 16 kHz mono WAV. Returns a reason on failure."""
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-t",
        str(SOUNDTRACK_MAX_SECONDS),
        str(target),
    ]
    try:
        proc = _run(args, _EXTRACT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "audio extraction timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"audio extraction failed: {exc}"
    stderr = (proc.stderr or "").lower()
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        if any(marker in stderr for marker in _NO_AUDIO_MARKERS):
            return "no audio track"
        return "audio extraction failed"
    return None


async def transcribe_video_soundtrack(
    path: str | Path, *, cache_dir: Path | None = None
) -> AudioTranscript:
    """Extract the audio track of a video with ffmpeg and transcribe it. Never raises."""
    video = Path(path)
    result = AudioTranscript(source=str(video))
    if not video.is_file():
        result.reason = "file not found"
        return result
    cache = _cache_file(video, cache_dir)
    cached = _read_cache(cache, str(video))
    if cached is not None:
        return cached
    # Check the provider before paying for an extraction that nothing could use.
    if _transcription_config() is None:
        result.reason = (
            "no speech-to-text provider is configured (transcription settings)"
        )
        return result
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        result.reason = "ffmpeg is not installed, soundtrack unavailable"
        return result
    with tempfile.TemporaryDirectory(prefix="navin-soundtrack-") as tmp:
        wav = Path(tmp) / "soundtrack.wav"
        failure = await asyncio.to_thread(_extract_soundtrack, ffmpeg, video, wav)
        if failure:
            result.reason = failure
            return result
        result = await _transcribe_file(wav, result)
    _write_cache(cache, result)
    return result


def describe_transcript(result: AudioTranscript, *, kind: str = "audio") -> str:
    """Note handed to the model: the transcript, or the reason there is none."""
    name = Path(result.source).name
    if not result.ok:
        return f"[{kind}: {name} - not transcribed: {result.reason}]"
    engine = " ".join(part for part in (result.model, result.provider) if part)
    details = [f"transcript via {engine}" if engine else "transcript"]
    if result.language:
        details.append(f"language {result.language}")
    if result.truncated:
        details.append(f"first {MAX_TRANSCRIPT_CHARS} characters")
    if kind != "audio":
        details.append("speech only, not what is shown on screen")
    return f"[{kind}: {name} - {', '.join(details)}]\n{result.text}\n[end of {kind}: {name}]"


async def expand_audio_attachments(
    text: str, media: list[str], *, cache_dir: Path | None = None
) -> tuple[str, list[str]]:
    """Replace audio paths in *media* with their transcript appended to *text*.

    Non-audio entries pass through untouched and keep their order. When the
    channel already transcribed its voice note inline, the audio path is
    dropped without a second transcription.
    """
    if not media:
        return text, media

    rewritten: list[str] = []
    notes: list[str] = []
    already_inline = bool(_INLINE_TRANSCRIPTION_RE.search(text or ""))
    for item in media:
        if not isinstance(item, str) or not is_audio_path(item):
            rewritten.append(item)
            continue
        if already_inline:
            continue
        result = await transcribe_audio_attachment(item, cache_dir=cache_dir)
        notes.append(describe_transcript(result, kind="audio"))

    if notes:
        suffix = "\n\n".join(notes)
        text = f"{text}\n\n{suffix}" if text else suffix
    return text, rewritten


async def append_video_soundtracks(
    text: str, videos: list[str], *, cache_dir: Path | None = None
) -> str:
    """Append the transcript of each video's soundtrack to *text*."""
    notes: list[str] = []
    for item in videos:
        if not isinstance(item, str) or not item:
            continue
        result = await transcribe_video_soundtrack(item, cache_dir=cache_dir)
        notes.append(describe_transcript(result, kind="video audio"))
    if not notes:
        return text
    suffix = "\n\n".join(notes)
    return f"{text}\n\n{suffix}" if text else suffix
