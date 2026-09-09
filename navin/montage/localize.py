# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Video localization: timed transcription (SRT) and voice-track dubbing.

Full localization pipeline built only on what navin already ships: ffmpeg
extracts and segments the audio by silence, the configured STT provider
transcribes each speech segment, and the result lands as a timed ``.srt``
plus a plain transcript. Dubbing muxes a generated voice track back into
the original video, optionally keeping the original audio as a low bed
and burning subtitles. Translation itself is the agent's job (it is the
LLM); these two stages are the ones that need real tooling.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from navin.montage.ffmpeg_runner import run_process

#: silencedetect tuning: -33dB / 0.45s splits normal speech into sentence-ish
#: chunks without cutting inside words on consumer-grade recordings.
_SILENCE_NOISE = "-33dB"
_SILENCE_MIN_S = 0.45
#: STT providers behave best under ~15s per request; longer speech runs are
#: split evenly so subtitle cues stay readable.
_MAX_SEGMENT_S = 14.0
_MIN_SEGMENT_S = 0.25
_MAX_SEGMENTS = 400


class LocalizeError(Exception):
    """Raised when transcription or dubbing cannot proceed."""


async def _run_ffmpeg(args: list[str], timeout_s: float = 900.0) -> str:
    """Run ffmpeg and return its stderr text (ffmpeg logs everything there)."""
    result = await run_process(args, timeout_s=timeout_s, capture_stdout=False)
    if result.timed_out:
        raise LocalizeError(f"ffmpeg timed out after {timeout_s:.0f}s") from None
    if not result.ok:
        raise LocalizeError(
            f"ffmpeg failed (exit {result.exit_code}):\n{result.stderr or 'no diagnostic output'}"
        )
    return result.raw_stderr


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """Read silencedetect start/end pairs from ffmpeg stderr."""
    starts = [float(v) for v in re.findall(r"silence_start:\s*(-?[0-9.]+)", stderr)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*(-?[0-9.]+)", stderr)]
    pairs: list[tuple[float, float]] = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else None
        if end is not None and end > start:
            pairs.append((max(0.0, start), end))
        elif end is None:
            # Trailing silence that runs to the end of the file.
            pairs.append((max(0.0, start), float("inf")))
    return pairs


def speech_segments(
    silences: list[tuple[float, float]], duration: float
) -> list[tuple[float, float]]:
    """Invert silence windows into speech segments, split overlong runs."""
    raw: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor:
            raw.append((cursor, min(start, duration)))
        cursor = max(cursor, end)
        if cursor >= duration:
            break
    if cursor < duration:
        raw.append((cursor, duration))
    if not raw and duration > 0:
        raw = [(0.0, duration)]

    segments: list[tuple[float, float]] = []
    for start, end in raw:
        length = end - start
        if length < _MIN_SEGMENT_S:
            continue
        if length <= _MAX_SEGMENT_S:
            segments.append((start, end))
            continue
        pieces = int(length // _MAX_SEGMENT_S) + 1
        step = length / pieces
        for index in range(pieces):
            segments.append((start + index * step, start + (index + 1) * step))
    return segments


def _srt_timestamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(cues: list[tuple[float, float, str]]) -> str:
    blocks: list[str] = []
    for index, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n")
    return "\n".join(blocks)


@dataclass(frozen=True)
class Cue:
    """One subtitle cue, with its window on the source timeline."""

    index: int
    start_ms: int
    end_ms: int
    text: str


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _clock_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(millis.ljust(3, "0"))
    )


def parse_srt(text: str) -> list[Cue]:
    """Read cues out of an SRT, tolerating the shapes translators hand back.

    Numbering is renumbered from the order encountered rather than trusted:
    a translated file often loses or duplicates indices, and the timeline is
    what matters here, not the label.
    """
    cues: list[Cue] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timed = next((i for i, line in enumerate(lines) if _SRT_TIME.search(line)), None)
        if timed is None:
            continue
        match = _SRT_TIME.search(lines[timed])
        if match is None:  # pragma: no cover - guarded by the search above
            continue
        start = _clock_ms(*match.group(1, 2, 3, 4))
        end = _clock_ms(*match.group(5, 6, 7, 8))
        body = " ".join(lines[timed + 1 :]).strip()
        if not body or end <= start:
            continue
        cues.append(Cue(len(cues) + 1, start, end, body))
    return cues


def atempo_chain(ratio: float) -> list[str]:
    """Express *ratio* as atempo filters, which each accept only 0.5 to 2.0."""
    if ratio <= 0:
        raise ValueError("tempo ratio must be positive")
    if abs(ratio - 1.0) < 1e-6:
        return []
    steps: list[str] = []
    remaining = ratio
    while remaining > 2.0:
        steps.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        steps.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-6:
        steps.append(f"atempo={remaining:.4f}".rstrip("0").rstrip("."))
    return steps


@dataclass(frozen=True)
class Placement:
    """A rendered clip, where it starts and how much it must be sped up."""

    cue_index: int
    start_ms: int
    tempo: float
    #: What the clip lasts once *tempo* is applied.
    played_ms: int
    #: True when the clip still runs past the next cue despite the speed cap.
    overruns: bool
    budget_ms: int


def plan_placements(
    cues: list[Cue],
    clip_ms: list[int],
    *,
    max_tempo: float = 1.35,
    tail_ms: int = 60,
) -> list[Placement]:
    """Fit each synthesized clip into the window its cue occupies.

    A translation rarely lands on the same duration as the original, and
    Arabic in particular tends to run longer than French or English. Rather
    than let the track drift, each clip keeps its cue's start time and is
    sped up just enough to be out of the way before the next one begins.

    The budget is not the cue itself but the distance to the next cue, since
    the silence after a sentence is free to use. Speed-up is capped so the
    voice stays natural: past the cap the clip is allowed to overrun and is
    reported, which is a local overlap instead of a drift that accumulates
    over the whole video.
    """
    if len(clip_ms) != len(cues):
        raise ValueError("expected one clip duration per cue")
    if max_tempo < 1.0:
        raise ValueError("max_tempo must be at least 1.0")

    placements: list[Placement] = []
    for position, (cue, raw_ms) in enumerate(zip(cues, clip_ms, strict=True)):
        if raw_ms <= 0:
            continue
        following = cues[position + 1].start_ms if position + 1 < len(cues) else None
        if following is None:
            budget = max(raw_ms, cue.end_ms - cue.start_ms)
        else:
            budget = max(1, following - cue.start_ms - tail_ms)
        tempo = 1.0
        if raw_ms > budget:
            tempo = min(max_tempo, raw_ms / budget)
        played = int(round(raw_ms / tempo))
        placements.append(
            Placement(
                cue_index=cue.index,
                start_ms=cue.start_ms,
                tempo=tempo,
                played_ms=played,
                overruns=played > budget,
                budget_ms=budget,
            )
        )
    return placements


def build_sync_report(
    placements: list[Placement],
    *,
    max_tempo: float,
    threshold_ms: int = 120,
) -> dict[str, object]:
    """Return a stable delivery gate report for a rendered voice track."""
    overlaps: list[dict[str, int]] = []
    overruns: list[dict[str, int]] = []
    for position, placement in enumerate(placements):
        overrun_ms = max(0, placement.played_ms - placement.budget_ms)
        if overrun_ms:
            overruns.append({"cue": placement.cue_index, "overrun_ms": overrun_ms})
        if position + 1 < len(placements):
            next_start = placements[position + 1].start_ms
            overlap_ms = max(0, placement.start_ms + placement.played_ms - next_start)
            if overlap_ms:
                overlaps.append(
                    {
                        "cue": placement.cue_index,
                        "next_cue": placements[position + 1].cue_index,
                        "overlap_ms": overlap_ms,
                    }
                )
    drift_ms = max(
        [item["overlap_ms"] for item in overlaps]
        + [item["overrun_ms"] for item in overruns]
        + [0]
    )
    return {
        "drift_ms": drift_ms,
        "overlaps": overlaps,
        "overruns": overruns,
        "max_tempo": max_tempo,
        "threshold_ms": threshold_ms,
        "gate": {
            "passed": drift_ms <= threshold_ms,
            "reason": None if drift_ms <= threshold_ms else "sync_threshold_exceeded",
        },
    }


def split_cue_text(text: str, *, max_chars: int = 280) -> list[str]:
    """Split long TTS input at spoken boundaries without rewriting the SRT."""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return [clean] if clean else []
    sentences = re.split(r"(?<=[.!?;:])\s+", clean)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        words = sentence.split()
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = word
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_voice_filter(placements: list[Placement], *, sample_rate: int = 48000) -> str:
    """Lay the placed clips onto one silent track as an ffmpeg filtergraph."""
    if not placements:
        raise ValueError("no placements to lay out")
    chains: list[str] = []
    labels: list[str] = []
    for position, placement in enumerate(placements):
        label = f"s{position}"
        steps = [f"aresample={sample_rate}"]
        steps += atempo_chain(placement.tempo)
        # ``all=1`` delays every channel, so mono and stereo clips behave the
        # same without knowing the layout the provider returned.
        steps.append(f"adelay={max(0, placement.start_ms)}:all=1")
        chains.append(f"[{position}:a]{','.join(steps)}[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        return f"{chains[0]};{labels[0]}anull[out]"
    mixed = "".join(labels)
    chains.append(f"{mixed}amix=inputs={len(labels)}:duration=longest:normalize=0[out]")
    return ";".join(chains)


def _localization_dir(root: Path, source: Path) -> Path:
    out = root / "marketing" / "montage" / "localization" / source.stem
    out.mkdir(parents=True, exist_ok=True)
    return out


async def transcribe_video(
    root: Path,
    source: str,
    *,
    language: str | None = None,
) -> dict[str, object]:
    """Extract audio, segment by silence, transcribe, write .srt + .txt."""
    from navin.audio.transcription import (
        resolve_transcription_config,
        transcribe_audio_file,
    )
    from navin.config.loader import load_config
    from navin.montage.assemble import parse_ffmpeg_duration
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise LocalizeError(
            "ffmpeg is not installed. Run montage(action=setup, package=ffmpeg) first."
        )
    config = resolve_transcription_config(load_config())
    if not config.enabled or not config.configured:
        raise LocalizeError(
            "no speech-to-text provider is configured. Configure transcription "
            "in settings (e.g. the managed provider or an STT API key) first."
        )
    if language:
        config = replace(config, language=language)

    src = Path(source)
    out_dir = _localization_dir(root, src)

    with tempfile.TemporaryDirectory(prefix="navin-localize-") as tmp:
        wav = Path(tmp) / "audio.wav"
        await _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav),
            ]
        )
        silence_log = await _run_ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(wav),
                "-af",
                f"silencedetect=noise={_SILENCE_NOISE}:d={_SILENCE_MIN_S}",
                "-f",
                "null",
                "-",
            ]
        )
        duration = parse_ffmpeg_duration(silence_log)
        if not duration or duration <= 0:
            raise LocalizeError("could not measure audio duration")
        segments = speech_segments(parse_silences(silence_log), duration)
        if not segments:
            raise LocalizeError("no speech detected in the source audio")
        if len(segments) > _MAX_SEGMENTS:
            raise LocalizeError(
                f"This video needs {len(segments)} speech segments; one transcription supports "
                f"{_MAX_SEGMENTS}. Split the video into shorter parts before transcribing. "
                "No partial subtitles were generated."
            )

        cues: list[tuple[float, float, str]] = []
        for index, (start, end) in enumerate(segments):
            chunk = Path(tmp) / f"chunk-{index:04d}.wav"
            await _run_ffmpeg(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-nostdin",
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{end - start:.3f}",
                    "-i",
                    str(wav),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(chunk),
                ]
            )
            text = (await transcribe_audio_file(chunk, config)).strip()
            if text:
                cues.append((start, end, text))

    if not cues:
        raise LocalizeError("transcription returned no text (STT provider may be misconfigured)")

    srt_path = out_dir / "source.srt"
    txt_path = out_dir / "transcript.txt"
    srt_path.write_text(build_srt(cues), encoding="utf-8")
    txt_path.write_text("\n".join(text for _s, _e, text in cues) + "\n", encoding="utf-8")

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(p)

    return {
        "ok": True,
        "srt": rel(srt_path),
        "transcript": rel(txt_path),
        "segments": len(cues),
        "duration_s": round(duration, 2),
        "provider": config.provider,
        "language": config.language or "auto",
        "next_step": (
            "Translate the .srt cues (keep numbering and timing untouched), save "
            "as translated.srt next to source.srt, then montage(action=voicetrack, "
            "srt=translated.srt) to synthesize each cue at its own timecode, and "
            "montage(action=dub, path=..., voice=<voice_track>). Do not synthesize "
            "the whole translation in one generate_speech call: a single block "
            "ignores the pauses in the source and drifts out of sync."
        ),
    }


async def _clip_duration_ms(path: Path, ffprobe: str | None, ffmpeg: str) -> int:
    from navin.montage.assemble import probe_duration, probe_duration_with_ffmpeg

    seconds = None
    if ffprobe:
        seconds = await probe_duration(str(path), ffprobe)
    if seconds is None:
        seconds = await probe_duration_with_ffmpeg(str(path), ffmpeg)
    if seconds is None or seconds <= 0:
        return 0
    return int(round(seconds * 1000))


_REFERENCE_MAX_S = 15.0
_REFERENCE_MIN_S = 4.0


async def extract_voice_sample(
    root: Path,
    source: str,
    *,
    seconds: float = 12.0,
    output: str | None = None,
) -> dict[str, object]:
    """Cut a short speech clip from *source* to use as a TTS clone reference.

    Uses the same silence detector as transcription, so the sample starts on
    actual speech rather than a music bed or a fade-in. ffmpeg only: no new
    binary, no ML stack.
    """
    from navin.montage.assemble import parse_ffmpeg_duration
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise LocalizeError(
            "ffmpeg is not installed. Run montage(action=setup, package=ffmpeg) first."
        )
    src = Path(source)
    if not src.is_file():
        raise LocalizeError(f"source file not found: {source}")
    length = max(_REFERENCE_MIN_S, min(_REFERENCE_MAX_S, float(seconds)))
    dest = Path(output) if output else _localization_dir(root, src) / "voice-reference.wav"
    if not dest.is_absolute():
        dest = root / dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="navin-voice-ref-") as tmp:
        wav = Path(tmp) / "audio.wav"
        await _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav),
            ]
        )
        silence_log = await _run_ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(wav),
                "-af",
                f"silencedetect=noise={_SILENCE_NOISE}:d={_SILENCE_MIN_S}",
                "-f",
                "null",
                "-",
            ]
        )
        duration = parse_ffmpeg_duration(silence_log) or 0.0
        segments = speech_segments(parse_silences(silence_log), duration) if duration else []
        start = segments[0][0] if segments else 0.0
        available = (duration - start) if duration > start else length
        take = min(length, max(_REFERENCE_MIN_S, available) if available else length)
        await _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-nostdin",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{take:.3f}",
                "-i",
                str(wav),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(dest),
            ]
        )

    if not dest.is_file() or dest.stat().st_size == 0:
        raise LocalizeError("could not extract a voice sample from the source")
    try:
        rel = str(dest.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(dest)
    return {
        "ok": True,
        "reference": rel,
        "duration_s": round(take, 2),
        "offset_s": round(start, 2),
        "next_step": (
            "Pass this file as reference= to montage(action=voicetrack, "
            "srt=translated.srt, reference=<reference>). The configured TTS "
            "model must accept a reference clip (Fish Audio, ElevenLabs). "
            "Catalogue voices (OpenAI, Groq, Gemini, local) cannot clone."
        ),
    }


async def build_dub_track(
    root: Path,
    srt: str,
    *,
    output: str | None = None,
    max_tempo: float = 1.35,
    voice: str | None = None,
    reference: str | None = None,
    sync_threshold_ms: int = 120,
) -> dict[str, object]:
    """Synthesize a translated SRT into one voice track that holds its timing.

    Synthesizing the whole translation in a single call, which is what the
    pipeline did before, produces an unbroken block of speech: it starts at
    zero and runs straight through, while the original speech was spread out
    with pauses. The gap widens for the length of the video. Here each cue is
    synthesized on its own and pinned back to the timecode it came from, so
    the track can only be as wrong as one sentence.
    """
    from navin.audio.tts import resolve_tts_config, synthesize_speech_with_config
    from navin.config.loader import load_config
    from navin.montage.assemble import find_ffprobe
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise LocalizeError(
            "ffmpeg is not installed. Run montage(action=setup, package=ffmpeg) first."
        )

    srt_path = Path(srt)
    if not srt_path.is_absolute():
        srt_path = root / srt_path
    if not srt_path.is_file():
        raise LocalizeError(f"subtitle file not found: {srt}")
    cues = parse_srt(srt_path.read_text(encoding="utf-8", errors="replace"))
    if not cues:
        raise LocalizeError(f"no usable cues in {srt}")
    if len(cues) > _MAX_SEGMENTS:
        raise LocalizeError(f"{len(cues)} cues exceeds the {_MAX_SEGMENTS} supported in one track")

    config = resolve_tts_config(load_config())
    if not config.configured:
        raise LocalizeError(
            "no text-to-speech provider is configured. Configure a voice in "
            "settings (managed provider or a TTS API key) first."
        )
    if voice:
        config = replace(config, voice=voice)

    extra_fields: dict[str, object] | None = None
    if reference:
        from navin.audio.tts_clone import model_supports_reference, reference_extra_fields

        if not model_supports_reference(config.model, config.provider):
            raise LocalizeError(
                "The configured TTS model cannot clone a voice from a reference "
                f"clip ({config.provider}/{config.model}). Pick fish-audio/s2.1-pro "
                "(or another clone-capable model) in Settings > Voice, or omit "
                "reference= to keep the catalogue voice."
            )
        ref_path = Path(reference)
        if not ref_path.is_absolute():
            ref_path = root / ref_path
        if not ref_path.is_file():
            raise LocalizeError(f"reference audio not found: {reference}")
        extra_fields = reference_extra_fields(ref_path.read_bytes())

    dest = Path(output) if output else srt_path.with_name(f"{srt_path.stem}-voice.wav")
    if not dest.is_absolute():
        dest = root / dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    ffprobe = find_ffprobe(ffmpeg)
    suffix = (config.response_format or "mp3").strip() or "mp3"

    cache_dir = dest.with_name(f".{dest.stem}-cues")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="navin-dubtrack-") as tmp:
        clips: list[Path] = []
        durations: list[int] = []
        kept: list[Cue] = []
        for cue in cues:
            parts = split_cue_text(cue.text)
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "text": parts,
                        "provider": config.provider,
                        "model": config.model,
                        "voice": config.voice,
                        "reference": bool(extra_fields),
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()[:20]
            clip = cache_dir / f"cue-{cue.index:04d}-{fingerprint}.{suffix}"
            if not clip.is_file() or clip.stat().st_size == 0:
                rendered = [
                    await synthesize_speech_with_config(
                        part, config, extra_fields=extra_fields
                    )
                    for part in parts
                ]
                if len(rendered) == 1:
                    clip.write_bytes(rendered[0])
                else:
                    part_paths: list[Path] = []
                    for part_index, audio in enumerate(rendered):
                        part_path = Path(tmp) / f"cue-{cue.index:04d}-{part_index:03d}.{suffix}"
                        part_path.write_bytes(audio)
                        part_paths.append(part_path)
                    concat = Path(tmp) / f"cue-{cue.index:04d}-concat.txt"
                    concat.write_text(
                        "\n".join(f"file '{path.as_posix()}'" for path in part_paths) + "\n",
                        encoding="utf-8",
                    )
                    await _run_ffmpeg(
                        [
                            ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-nostdin",
                            "-f",
                            "concat",
                            "-safe",
                            "0",
                            "-i",
                            str(concat),
                            "-c",
                            "copy",
                            str(clip),
                        ]
                    )
            measured = await _clip_duration_ms(clip, ffprobe, ffmpeg)
            if measured <= 0:
                continue
            clips.append(clip)
            durations.append(measured)
            kept.append(cue)

        if not clips:
            raise LocalizeError("text-to-speech returned no usable audio")

        placements = plan_placements(kept, durations, max_tempo=max_tempo)
        args = [ffmpeg, "-y", "-hide_banner", "-nostdin"]
        for clip in clips:
            args += ["-i", str(clip)]
        args += [
            "-filter_complex",
            build_voice_filter(placements),
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
        await _run_ffmpeg(args)

    if not dest.is_file():
        raise LocalizeError("voice track rendering produced no file")

    stretched = [p for p in placements if p.tempo > 1.0]
    sync_report = build_sync_report(
        placements,
        max_tempo=max_tempo,
        threshold_ms=max(0, int(sync_threshold_ms)),
    )
    report_path = dest.with_suffix(dest.suffix + ".sync.json")
    report_path.write_text(
        json.dumps(sync_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        rel = str(dest.relative_to(root)).replace("\\", "/")
        report_rel = str(report_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(dest)
        report_rel = str(report_path)
    return {
        "ok": bool(sync_report["gate"]["passed"]),
        "voice_track": rel,
        "cues": len(placements),
        "sped_up": len(stretched),
        "sync": sync_report,
        "sync_report": report_rel,
        "provider": config.provider,
        "voice": config.voice,
        "cloned": extra_fields is not None,
        "next_step": (
            "Pass this voice track to montage(action=dub, path=<video>, "
            "voice=<voice_track>) only when sync.gate.passed is true."
        ),
    }


def assert_sync_gate_ok(audio: str | Path) -> None:
    """Refuse to use a voice track whose sibling ``.sync.json`` failed the gate.

    Both dubbing and lip-sync call this so a track that drifted past the sync
    threshold can never reach a deliverable, whichever path consumes it. A track
    with no report (a hand-supplied WAV) is allowed - the gate only blocks a
    track we ourselves flagged as out of sync.
    """
    audio_path = Path(audio)
    report_path = audio_path.with_suffix(audio_path.suffix + ".sync.json")
    if not report_path.is_file():
        return
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalizeError(f"sync report is unreadable: {report_path}") from exc
    if not bool((report.get("gate") or {}).get("passed")):
        raise LocalizeError(
            "delivery blocked: voice track exceeds the configured sync threshold"
        )


async def dub_video(
    root: Path,
    source: str,
    voice: str,
    *,
    srt: str | None = None,
    original_gain_db: float | None = None,
    output: str | None = None,
) -> dict[str, object]:
    """Replace the audio track of *source* with *voice*, optionally burn subs."""
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise LocalizeError(
            "ffmpeg is not installed. Run montage(action=setup, package=ffmpeg) first."
        )
    assert_sync_gate_ok(voice)

    src = Path(source)
    target = (output or "").strip()
    if target:
        dest = Path(target)
        if not dest.is_absolute():
            dest = root / dest
    else:
        dest = _localization_dir(root, src) / f"{src.stem}-dubbed.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(src),
        "-i",
        str(voice),
    ]

    filters: list[str] = []
    if original_gain_db is not None:
        # Keep the original track as an ambience bed under the new voice, then pad
        # with silence so the audio is never shorter than the video.
        filters.append(
            f"[0:a]volume={original_gain_db}dB[bed];"
            "[1:a][bed]amix=inputs=2:duration=first:dropout_transition=0,"
            "apad[aout]"
        )
        audio_map = "[aout]"
    else:
        # apad extends the voice with trailing silence; combined with -shortest
        # below the output always matches the full video length (the voice is
        # padded when short and clipped when long, but the image is never cut).
        filters.append("[1:a]apad[aout]")
        audio_map = "[aout]"

    if srt:
        escaped = str(srt).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        filters.append(f"[0:v]subtitles='{escaped}'[vout]")
        video_map, video_codec = (
            "[vout]",
            ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p"],
        )
    else:
        video_map, video_codec = "0:v", ["-c:v", "copy"]

    if filters:
        args += ["-filter_complex", ";".join(filters)]
    args += ["-map", video_map, "-map", audio_map]
    args += video_codec
    args += ["-c:a", "aac", "-b:a", "192k", "-shortest", str(dest)]

    await _run_ffmpeg(args)
    if not dest.is_file():
        raise LocalizeError("dub produced no output file")

    try:
        rel = str(dest.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(dest)
    return {
        "ok": True,
        "output": rel,
        "burned_subtitles": bool(srt),
        "kept_original_audio": original_gain_db is not None,
        "next_step": (
            "Call the message tool with this output path in the media parameter "
            "to deliver the dubbed video to the user."
        ),
    }
