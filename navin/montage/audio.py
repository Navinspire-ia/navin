# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Professional audio for montage output: mix, loudness, beats and SFX.

The first assemble pass laid music under narration with a sidechain duck and
stopped there: no loudness target (so one export was whisper quiet and the
next clipped on a phone), raw TTS straight into the mix, music that started
and ended like a light switch, and nothing to cut on the beat. This module is
the audio half of a premium render, ffmpeg only, no new dependency:

- :func:`mix_audio` places narration (one file or one file per scene),
  cleans it (80 Hz high-pass, light de-ess, gentle compression), ducks and
  fades the music bed, lays procedural SFX at given times, and normalizes the
  result with a two-pass ``loudnorm`` to -14 LUFS integrated / -1 dBTP
  (social) or -16 LUFS (web);
- :func:`detect_beats` finds the beat grid of a music track from an energy
  onset envelope (decoded by ffmpeg, analysed in pure Python), and
  :func:`snap_to_beats` moves cut points onto it;
- :func:`generate_sfx` renders the procedural SFX set (soft click, whoosh,
  pop, rise, impact) with ``aevalsrc``/``anoisesrc``;
- :func:`measure_loudness` reads integrated loudness and true peak back with
  ``ebur128``, which is how the result is verified.

Graph builders are pure functions so they can be tested without ffmpeg.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import tempfile
from array import array
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.montage.ffmpeg_runner import run_process

LOUDNESS_SOCIAL = -14.0
LOUDNESS_WEB = -16.0
LOUDNESS_TARGETS = {"social": LOUDNESS_SOCIAL, "web": LOUDNESS_WEB}
DEFAULT_TRUE_PEAK = -1.0
DEFAULT_LRA = 11.0
MIX_SAMPLE_RATE = 48_000

SFX_KINDS = ("click", "whoosh", "pop", "rise", "impact")
#: Length of each procedural effect, used to keep cues inside the timeline.
SFX_DURATIONS = {"click": 0.05, "whoosh": 0.8, "pop": 0.16, "rise": 1.6, "impact": 1.4}

#: Beat analysis decodes at this rate: plenty for kick/snare energy.
_BEAT_RATE = 11_025
_BEAT_HOP = 256
_BEAT_MAX_SECONDS = 240.0


class AudioMixError(ValueError):
    """Invalid audio mix request."""


@dataclass(frozen=True, slots=True)
class VoiceCue:
    """One narration file placed at ``at`` seconds on the timeline."""

    path: str
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class SfxCue:
    """One procedural effect placed at ``at`` seconds."""

    kind: str
    at: float
    gain_db: float = -6.0


@dataclass(frozen=True, slots=True)
class AudioMixOptions:
    """Knobs of :func:`mix_audio`; the defaults are the social premium mix.

    ``loudness_target`` of ``None`` skips loudness normalization entirely.
    """

    loudness_target: float | None = LOUDNESS_SOCIAL
    true_peak: float = DEFAULT_TRUE_PEAK
    lra: float = DEFAULT_LRA
    voice_chain: bool = True
    duck: bool = True
    music_gain_db: float = -16.0
    music_fade_in: float = 1.0
    music_fade_out: float = 1.5
    sfx: tuple[SfxCue, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        if self.loudness_target is not None and not -36.0 <= self.loudness_target <= -5.0:
            raise AudioMixError("loudness target must be between -36 and -5 LUFS")
        if not -9.0 <= self.true_peak <= 0.0:
            raise AudioMixError("true peak must be between -9 and 0 dBTP")
        if not 1.0 <= self.lra <= 50.0:
            raise AudioMixError("loudness range must be between 1 and 50 LU")
        if not -60.0 <= self.music_gain_db <= 12.0:
            raise AudioMixError("music_gain_db must be between -60 and +12 dB")
        if self.music_fade_in < 0 or self.music_fade_out < 0:
            raise AudioMixError("music fades cannot be negative")
        for cue in self.sfx:
            if cue.kind not in SFX_KINDS:
                raise AudioMixError(
                    f"unknown sfx {cue.kind!r}; use one of: " + ", ".join(SFX_KINDS)
                )
            if cue.at < 0:
                raise AudioMixError("sfx times cannot be negative")


def resolve_loudness_target(value: float | str | None) -> float | None:
    """Accept ``social``/``web``/a LUFS number/``None`` (no normalization)."""
    if value is None:
        return None
    if isinstance(value, str):
        key = value.strip().lower()
        if key in ("", "none", "off"):
            return None
        if key in LOUDNESS_TARGETS:
            return LOUDNESS_TARGETS[key]
        try:
            return float(key)
        except ValueError as exc:
            raise AudioMixError(
                f"loudness must be social, web or a LUFS number, got {value!r}"
            ) from exc
    return float(value)


# --------------------------------------------------------------------------
# Filter builders
# --------------------------------------------------------------------------


def voice_chain_filter() -> str:
    """Narration clean-up: rumble cut, light de-ess, gentle leveling."""
    return (
        "highpass=f=80:poles=2,"
        "deesser=i=0.35:m=0.5:f=0.5:s=o,"
        "acompressor=threshold=-20dB:ratio=3:attack=8:release=160:knee=4:makeup=2"
    )


def duck_filter() -> str:
    """Smoothed sidechain duck: slower attack and a long release than the
    legacy duck, so the bed breathes back between sentences instead of pumping."""
    return "sidechaincompress=threshold=0.025:ratio=6:attack=60:release=650:knee=4:makeup=1"


def _fmt() -> str:
    return f"aformat=sample_fmts=fltp:sample_rates={MIX_SAMPLE_RATE}:channel_layouts=stereo"


def _num(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def sfx_chains(kind: str, label: str) -> list[str]:
    """Source-only filter chains rendering effect *kind*, ending on ``[label]``.

    Frequencies sweep with an analytic phase (the integral of the frequency
    curve), so the pitch glides cleanly instead of warbling.
    """
    rate = MIX_SAMPLE_RATE
    if kind == "click":
        return [
            "aevalsrc=exprs='(0.7*sin(2*PI*2400*t)+0.3*sin(2*PI*5200*t))*exp(-t*120)'"
            f":s={rate}:d=0.05,{_fmt()}[{label}]"
        ]
    if kind == "pop":
        return [
            "aevalsrc=exprs='0.9*sin(2*PI*(300*t+24*(1-exp(-25*t))))*exp(-t*22)'"
            f":s={rate}:d=0.16,{_fmt()}[{label}]"
        ]
    if kind == "whoosh":
        return [
            f"anoisesrc=color=pink:amplitude=0.9:sample_rate={rate}:duration=0.8,"
            "highpass=f=250,lowpass=f=4500,"
            "afade=t=in:st=0:d=0.45:curve=exp,afade=t=out:st=0.45:d=0.35:curve=exp,"
            f"{_fmt()}[{label}]"
        ]
    if kind == "rise":
        return [
            "aevalsrc=exprs='(0.35*sin(2*PI*(180*t+225*t*t))+0.2*sin(2*PI*(360*t+450*t*t)))"
            f"*(t/1.6)':s={rate}:d=1.6,{_fmt()}[{label}tone]",
            f"anoisesrc=color=white:amplitude=0.3:sample_rate={rate}:duration=1.6,"
            f"highpass=f=1500,afade=t=in:st=0:d=1.5:curve=exp,{_fmt()}[{label}air]",
            f"[{label}tone][{label}air]amix=inputs=2:normalize=0,"
            f"afade=t=out:st=1.52:d=0.08[{label}]",
        ]
    if kind == "impact":
        return [
            "aevalsrc=exprs='0.95*sin(2*PI*(45*t+9*(1-exp(-10*t))))*exp(-t*4.5)'"
            f":s={rate}:d=1.4,{_fmt()}[{label}boom]",
            f"anoisesrc=color=brown:amplitude=0.6:sample_rate={rate}:duration=0.25,"
            f"afade=t=out:st=0:d=0.25,{_fmt()}[{label}hit]",
            f"[{label}boom][{label}hit]amix=inputs=2:duration=longest:normalize=0,"
            f"alimiter=limit=0.89:level=false[{label}]",
        ]
    raise AudioMixError(f"unknown sfx {kind!r}; use one of: " + ", ".join(SFX_KINDS))


def build_mix_graph(
    *,
    voice_inputs: Sequence[tuple[int, float]],
    music_input: int | None,
    duration: float | None,
    options: AudioMixOptions,
    out_label: str = "mix",
) -> list[str]:
    """Filter chains mixing narration, bed and SFX into ``[out_label]``.

    *voice_inputs* pairs an ffmpeg input index with its timeline position.
    Loudness normalization is not part of this graph: the two-pass caller
    appends ``loudnorm`` to ``[out_label]``.
    """
    options.validate()
    chains: list[str] = []
    mix_labels: list[str] = []
    voice_label: str | None = None
    if voice_inputs:
        cleaned = voice_chain_filter() + "," if options.voice_chain else ""
        parts: list[str] = []
        for position, (index, at) in enumerate(voice_inputs):
            delay = max(0, round(float(at) * 1000))
            label = f"vo{position}"
            chains.append(
                f"[{index}:a]{_fmt()},{cleaned}adelay=delays={delay}:all=1[{label}]"
            )
            parts.append(f"[{label}]")
        if len(parts) == 1:
            voice_label = parts[0]
        else:
            chains.append(
                f"{''.join(parts)}amix=inputs={len(parts)}:duration=longest:normalize=0[voice]"
            )
            voice_label = "[voice]"
    if music_input is not None:
        bed = f"[{music_input}:a]{_fmt()},volume={_num(options.music_gain_db)}dB"
        if voice_label is not None and options.duck:
            chains.append(f"{bed}[bed]")
            chains.append(f"{voice_label}asplit=2[voicemix][sidechain]")
            chains.append(f"[bed][sidechain]{duck_filter()}[bedducked]")
            source = "[bedducked]"
            voice_label = "[voicemix]"
        else:
            chains.append(f"{bed}[bedducked]")
            source = "[bedducked]"
        fades: list[str] = []
        if options.music_fade_in > 0:
            fades.append(f"afade=t=in:st=0:d={_num(options.music_fade_in)}")
        if duration and options.music_fade_out > 0:
            fade_out = min(options.music_fade_out, max(0.0, float(duration) / 2))
            if fade_out > 0:
                fades.append(
                    f"afade=t=out:st={_num(float(duration) - fade_out)}:d={_num(fade_out)}"
                )
        if duration:
            fades.append(f"atrim=0:{_num(duration)}")
        chains.append(f"{source}{','.join(fades) or 'anull'}[music]")
        mix_labels.append("[music]")
    if voice_label is not None:
        mix_labels.append(voice_label)
    for position, cue in enumerate(options.sfx):
        if duration and cue.at >= duration:
            continue
        label = f"sfx{position}"
        chains += sfx_chains(cue.kind, f"{label}src")
        delay = max(0, round(cue.at * 1000))
        chains.append(
            f"[{label}src]volume={_num(cue.gain_db)}dB,adelay=delays={delay}:all=1[{label}]"
        )
        mix_labels.append(f"[{label}]")
    if not mix_labels:
        raise AudioMixError("nothing to mix: pass narration, music or sfx")
    tail = ""
    if duration:
        tail = f",apad,atrim=0:{_num(duration)}"
    if len(mix_labels) == 1:
        chains.append(f"{mix_labels[0]}anull{tail}[{out_label}]")
    else:
        chains.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:"
            f"normalize=0{tail}[{out_label}]"
        )
    return chains


def loudnorm_measure_filter(target: float, true_peak: float, lra: float) -> str:
    return (
        f"loudnorm=I={_num(target)}:TP={_num(true_peak)}:LRA={_num(lra)}:print_format=json"
    )


def loudnorm_apply_filter(
    target: float, true_peak: float, lra: float, measured: dict[str, float]
) -> str:
    """Second pass: feed the first-pass measurements so the gain is linear
    (one static gain, no pumping) whenever the material allows it."""
    return (
        f"loudnorm=I={_num(target)}:TP={_num(true_peak)}:LRA={_num(lra)}:"
        f"measured_I={_num(measured['input_i'])}:measured_TP={_num(measured['input_tp'])}:"
        f"measured_LRA={_num(measured['input_lra'])}:"
        f"measured_thresh={_num(measured['input_thresh'])}:"
        f"offset={_num(measured.get('target_offset', 0.0))}:linear=true:print_format=summary"
    )


def parse_loudnorm_json(stderr: str) -> dict[str, float] | None:
    """The JSON block ``loudnorm`` prints after the first pass, as floats.

    Returns ``None`` when it is missing or the input was silent (``-inf``),
    in which case there is nothing meaningful to normalize.
    """
    text = stderr or ""
    end = text.rfind("}")
    start = text.rfind("{", 0, end + 1)
    if start < 0 or end < 0:
        return None
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return None
    out: dict[str, float] = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        out[key] = value
    return out


_EBU_I_RE = re.compile(r"I:\s*(-?[\d.]+|-inf)\s*LUFS")
_EBU_LRA_RE = re.compile(r"LRA:\s*(-?[\d.]+)\s*LU\b")
_EBU_PEAK_RE = re.compile(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


def parse_ebur128_summary(stderr: str) -> dict[str, float | None]:
    """Integrated loudness, LRA and true peak from an ``ebur128`` summary."""
    text = stderr or ""
    at = text.rfind("Summary:")
    summary = text[at:] if at >= 0 else text

    def grab(pattern: re.Pattern[str]) -> float | None:
        match = pattern.search(summary)
        if match is None or match.group(1) == "-inf":
            return None
        return float(match.group(1))

    return {
        "integrated": grab(_EBU_I_RE),
        "lra": grab(_EBU_LRA_RE),
        "true_peak": grab(_EBU_PEAK_RE),
    }


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------


async def _ffmpeg(ffmpeg: str | None) -> str:
    if ffmpeg:
        return ffmpeg
    from navin.montage import detect

    found = await asyncio.to_thread(detect.find_ffmpeg)
    if not found:
        raise AudioMixError(
            "ffmpeg is not installed. Run the montage tool with action=setup and "
            "package=ffmpeg first."
        )
    return found


async def measure_loudness(
    path: str | Path, ffmpeg: str | None = None, timeout_s: float = 300.0
) -> dict[str, float | None]:
    """Measure *path* with ``ebur128`` (integrated LUFS, LRA, true peak dBTP)."""
    binary = await _ffmpeg(ffmpeg)
    result = await run_process(
        [
            binary,
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        timeout_s=timeout_s,
        capture_stdout=False,
    )
    if not result.ok:
        raise AudioMixError(result.stderr or "loudness measurement failed")
    return parse_ebur128_summary(result.raw_stderr)


async def generate_sfx(
    kind: str,
    output: str | Path,
    *,
    ffmpeg: str | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Render one procedural effect to a 48 kHz stereo WAV at *output*."""
    if kind not in SFX_KINDS:
        raise AudioMixError(f"unknown sfx {kind!r}; use one of: " + ", ".join(SFX_KINDS))
    binary = await _ffmpeg(ffmpeg)
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = ";".join(sfx_chains(kind, "out"))
    result = await run_process(
        [
            binary,
            "-y",
            "-hide_banner",
            "-nostdin",
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        timeout_s=timeout_s,
        capture_stdout=False,
    )
    if not result.ok:
        raise AudioMixError(result.stderr or f"could not render the {kind} sfx")
    return {"ok": True, "kind": kind, "path": str(path), "duration_s": SFX_DURATIONS[kind]}


async def generate_sfx_set(
    directory: str | Path, *, ffmpeg: str | None = None
) -> dict[str, str]:
    """Render every procedural effect into *directory*; returns kind to path."""
    folder = Path(directory).expanduser()
    out: dict[str, str] = {}
    for kind in SFX_KINDS:
        result = await generate_sfx(kind, folder / f"sfx_{kind}.wav", ffmpeg=ffmpeg)
        out[kind] = str(result["path"])
    return out


def _voice_cues(voice: str | VoiceCue | Iterable[Any] | None) -> list[VoiceCue]:
    if voice is None or voice == "":
        return []
    if isinstance(voice, VoiceCue):
        return [voice]
    if isinstance(voice, (str, Path)):
        return [VoiceCue(str(voice), 0.0)]
    cues: list[VoiceCue] = []
    for item in voice:
        if isinstance(item, VoiceCue):
            cues.append(item)
        elif isinstance(item, (str, Path)):
            cues.append(VoiceCue(str(item), 0.0))
        elif isinstance(item, dict):
            cues.append(VoiceCue(str(item["path"]), float(item.get("at", item.get("start", 0.0)))))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            cues.append(VoiceCue(str(item[0]), float(item[1])))
        else:
            raise AudioMixError("voice cues must be paths, (path, at) pairs or VoiceCue")
    for cue in cues:
        if cue.at < 0:
            raise AudioMixError("narration cannot start before zero")
    return cues


def _sfx_cues(sfx: Iterable[Any] | None) -> tuple[SfxCue, ...]:
    out: list[SfxCue] = []
    for item in sfx or ():
        if isinstance(item, SfxCue):
            out.append(item)
        elif isinstance(item, dict):
            out.append(
                SfxCue(
                    str(item.get("kind") or item.get("type") or ""),
                    float(item.get("at", item.get("time", 0.0))),
                    float(item.get("gain_db", -6.0)),
                )
            )
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            out.append(SfxCue(str(item[0]), float(item[1]), *(float(v) for v in item[2:3])))
        else:
            raise AudioMixError("sfx cues must be {kind, at} objects or (kind, at) pairs")
    return tuple(out)


async def _probe_length(path: str, ffmpeg: str) -> float | None:
    from navin.montage.assemble import find_ffprobe, probe_duration, probe_duration_with_ffmpeg

    ffprobe = await asyncio.to_thread(find_ffprobe, ffmpeg)
    if ffprobe:
        return await probe_duration(path, ffprobe)
    return await probe_duration_with_ffmpeg(path, ffmpeg)


async def mix_audio(
    output: str | Path,
    *,
    voice: str | VoiceCue | Iterable[Any] | None = None,
    music: str | None = None,
    sfx: Iterable[Any] | None = None,
    duration: float | None = None,
    options: AudioMixOptions | None = None,
    loudness: float | str | None = "keep",
    ffmpeg: str | None = None,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    """Mix narration, music and SFX into a mastered 48 kHz WAV at *output*.

    ``voice`` is one path, or a list of ``(path, at)`` cues (one per scene).
    ``duration`` fixes the timeline length (music is looped and faded to it);
    without it the mix spans the narration, or the music when there is none.
    ``loudness`` overrides ``options.loudness_target`` (``social``, ``web``,
    a LUFS value, or ``None`` to skip normalization).

    Returns ``{"ok", "output", "duration_s", "measured", "target", ...}``
    where ``measured`` is the ``ebur128`` reading of the written file.
    """
    opts = options or AudioMixOptions()
    if sfx is not None:
        opts = AudioMixOptions(
            loudness_target=opts.loudness_target,
            true_peak=opts.true_peak,
            lra=opts.lra,
            voice_chain=opts.voice_chain,
            duck=opts.duck,
            music_gain_db=opts.music_gain_db,
            music_fade_in=opts.music_fade_in,
            music_fade_out=opts.music_fade_out,
            sfx=opts.sfx + _sfx_cues(sfx),
        )
    target = opts.loudness_target if loudness == "keep" else resolve_loudness_target(loudness)
    opts.validate()
    cues = _voice_cues(voice)
    if not cues and not music and not opts.sfx:
        raise AudioMixError("nothing to mix: pass narration, music or sfx")
    binary = await _ffmpeg(ffmpeg)

    length = float(duration) if duration and duration > 0 else None
    if length is None:
        ends: list[float] = []
        for cue in cues:
            measured = await _probe_length(cue.path, binary)
            if measured:
                ends.append(cue.at + measured)
        if not ends and music:
            measured = await _probe_length(music, binary)
            if measured:
                ends.append(measured)
        for cue in opts.sfx:
            ends.append(cue.at + SFX_DURATIONS.get(cue.kind, 0.0))
        length = max(ends) if ends else None

    inputs: list[str] = []
    voice_inputs: list[tuple[int, float]] = []
    index = 0
    for cue in cues:
        inputs += ["-i", cue.path]
        voice_inputs.append((index, cue.at))
        index += 1
    music_input: int | None = None
    if music:
        if length:
            inputs += ["-stream_loop", "-1"]
        inputs += ["-i", music]
        music_input = index
        index += 1
    chains = build_mix_graph(
        voice_inputs=voice_inputs,
        music_input=music_input,
        duration=length,
        options=opts,
    )
    base = [binary, "-y", "-hide_banner", "-nostdin", "-nostats"] + inputs
    cap = ["-t", _num(length)] if length else []

    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    measured_first: dict[str, float] | None = None
    passes = 0
    if target is not None:
        tp = opts.true_peak
        graph = ";".join(chains + [f"[mix]{loudnorm_measure_filter(target, tp, opts.lra)}[norm]"])
        first = await run_process(
            base + ["-filter_complex", graph, "-map", "[norm]"] + cap + ["-f", "null", "-"],
            timeout_s=timeout_s,
            capture_stdout=False,
        )
        if not first.ok:
            raise AudioMixError(first.stderr or "loudness analysis failed")
        passes = 1
        measured_first = parse_loudnorm_json(first.raw_stderr)
        if measured_first is not None:
            final = f"[mix]{loudnorm_apply_filter(target, tp, opts.lra, measured_first)}"
        else:
            # Silent or unmeasurable mix: a dynamic single pass is the best bet.
            final = f"[mix]{loudnorm_measure_filter(target, tp, opts.lra)}"
        graph = ";".join(chains + [f"{final},aresample={MIX_SAMPLE_RATE}[out]"])
    else:
        graph = ";".join(chains + [f"[mix]aresample={MIX_SAMPLE_RATE}[out]"])
    second = await run_process(
        base
        + ["-filter_complex", graph, "-map", "[out]"]
        + cap
        + ["-c:a", "pcm_s16le", "-ar", str(MIX_SAMPLE_RATE), str(path)],
        timeout_s=timeout_s,
        capture_stdout=False,
    )
    if not second.ok:
        raise AudioMixError(second.stderr or "audio mix failed")
    passes += 1
    measured = await measure_loudness(path, binary)
    return {
        "ok": True,
        "output": str(path),
        "duration_s": round(length, 3) if length else None,
        "target_lufs": target,
        "true_peak_target": opts.true_peak if target is not None else None,
        "passes": passes,
        "first_pass": measured_first,
        "measured": measured,
        "voice_segments": len(cues),
        "has_music": bool(music),
        "sfx": [{"kind": cue.kind, "at": cue.at} for cue in opts.sfx],
    }


# --------------------------------------------------------------------------
# Beats
# --------------------------------------------------------------------------


def onset_envelope(samples: Sequence[int], rate: int = _BEAT_RATE, hop: int = _BEAT_HOP) -> list[float]:
    """Half-wave rectified log-energy flux, with the local mean removed.

    Energy rises at every kick and snare; subtracting a ~0.4 s moving average
    keeps sustained loud passages from reading as one long onset.
    """
    frames = len(samples) // hop
    if frames < 3:
        return []
    energy: list[float] = []
    for frame in range(frames):
        chunk = samples[frame * hop : (frame + 1) * hop]
        power = sum(value * value for value in chunk) / hop
        energy.append(math.log1p(power))
    flux = [0.0] + [max(0.0, energy[i] - energy[i - 1]) for i in range(1, frames)]
    window = max(1, round(0.4 * rate / hop))
    out: list[float] = []
    running = 0.0
    for i, value in enumerate(flux):
        running += value
        if i >= window:
            running -= flux[i - window]
        mean = running / min(i + 1, window)
        out.append(max(0.0, value - mean))
    return out


def estimate_tempo(
    envelope: Sequence[float],
    frame_rate: float,
    *,
    min_bpm: float = 70.0,
    max_bpm: float = 180.0,
) -> float | None:
    """Tempo in BPM from the autocorrelation of *envelope*.

    A log-normal prior centred on 120 BPM breaks ties between a tempo and its
    double or half, which plain autocorrelation cannot tell apart.
    """
    n = len(envelope)
    if n < 8:
        return None
    min_lag = max(1, int(frame_rate * 60.0 / max_bpm))
    max_lag = min(n - 1, int(math.ceil(frame_rate * 60.0 / min_bpm)))
    if max_lag <= min_lag:
        return None
    scores: dict[int, float] = {}
    for lag in range(min_lag, max_lag + 1):
        total = 0.0
        for i in range(n - lag):
            total += envelope[i] * envelope[i + lag]
        bpm = 60.0 * frame_rate / lag
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        scores[lag] = total / (n - lag) * prior
    best = max(scores, key=scores.__getitem__)
    if scores[best] <= 0:
        return None
    # Parabolic interpolation around the best lag recovers sub-frame tempo.
    left, right = scores.get(best - 1), scores.get(best + 1)
    lag = float(best)
    if left is not None and right is not None:
        denom = left - 2 * scores[best] + right
        if denom != 0:
            lag = best + 0.5 * (left - right) / denom
    return 60.0 * frame_rate / lag


def track_beats(envelope: Sequence[float], frame_rate: float, bpm: float) -> list[float]:
    """Beat times (seconds) for a steady *bpm*, phase-locked to the onsets.

    The phase is the offset whose comb sums the most onset energy; each beat
    is then nudged to the strongest onset within 10% of a period, which
    absorbs small drift without letting the grid wander.
    """
    n = len(envelope)
    if n == 0 or bpm <= 0:
        return []
    period = 60.0 * frame_rate / bpm
    best_phase, best_score = 0.0, -1.0
    steps = max(1, int(period))
    for step in range(steps):
        phase = step * period / steps
        score = 0.0
        position = phase
        while position < n:
            score += envelope[int(position)]
            position += period
        if score > best_score:
            best_phase, best_score = phase, score
    radius = max(1, int(period * 0.1))
    beats: list[float] = []
    position = best_phase
    # Extend the grid back to the start of the track (the very first onset has
    # no flux to measure, so the phase search can land one beat late).
    while position - period >= -radius:
        position -= period
    while position < n:
        center = max(0, int(round(position)))
        lo, hi = max(0, center - radius), min(n - 1, center + radius)
        window = range(lo, hi + 1)
        peak = max(window, key=lambda i: envelope[i]) if hi >= lo else center
        chosen = peak if envelope[peak] > 0 else center
        beats.append(round(chosen / frame_rate, 3))
        position += period
    return sorted(set(beats))


async def detect_beats(
    music: str | Path,
    *,
    ffmpeg: str | None = None,
    max_seconds: float = _BEAT_MAX_SECONDS,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Beat grid of *music*: ``{"tempo_bpm", "beats", "duration_s"}``.

    ffmpeg decodes to mono 11 kHz PCM; the analysis is pure Python, so no
    numpy or librosa is needed.
    """
    binary = await _ffmpeg(ffmpeg)
    with tempfile.TemporaryDirectory(prefix="navin-beats-") as tmp:
        raw = Path(tmp) / "music.s16"
        result = await run_process(
            [
                binary,
                "-y",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(music),
                "-t",
                _num(max_seconds),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(_BEAT_RATE),
                "-f",
                "s16le",
                str(raw),
            ],
            timeout_s=timeout_s,
            capture_stdout=False,
        )
        if not result.ok or not raw.is_file():
            raise AudioMixError(result.stderr or "could not decode the music for beat detection")
        samples = array("h")
        samples.frombytes(raw.read_bytes()[: (raw.stat().st_size // 2) * 2])
    import sys

    if sys.byteorder == "big":
        samples.byteswap()
    frame_rate = _BEAT_RATE / _BEAT_HOP
    envelope = await asyncio.to_thread(onset_envelope, samples)
    tempo = await asyncio.to_thread(estimate_tempo, envelope, frame_rate)
    beats = track_beats(envelope, frame_rate, tempo) if tempo else []
    return {
        "tempo_bpm": round(tempo, 2) if tempo else None,
        "beats": beats,
        "duration_s": round(len(samples) / _BEAT_RATE, 3),
    }


def snap_to_beats(
    cut_times: Sequence[float],
    beats: Sequence[float],
    *,
    tolerance: float | None = None,
    min_gap: float = 0.4,
) -> list[float]:
    """Move each cut onto the nearest beat when one is close enough.

    ``tolerance`` defaults to half the median beat interval (capped at
    0.35 s), so a cut never jumps by more than half a beat. Cuts stay in
    order and at least ``min_gap`` apart; a cut that would collide with the
    previous one keeps its original time (pushed to ``min_gap`` after the
    previous cut if needed). The output aligns with the input.
    """
    grid = sorted(float(beat) for beat in beats if beat is not None and beat >= 0)
    cuts = [float(cut) for cut in cut_times]
    if not grid:
        return cuts
    if tolerance is None:
        if len(grid) > 1:
            intervals = sorted(b - a for a, b in zip(grid, grid[1:], strict=False) if b > a)
            median = intervals[len(intervals) // 2] if intervals else 0.5
        else:
            median = 0.5
        tolerance = min(0.35, median / 2)
    import bisect

    out: list[float] = []
    previous: float | None = None
    for cut in cuts:
        at = bisect.bisect_left(grid, cut)
        candidates = [grid[i] for i in (at - 1, at) if 0 <= i < len(grid)]
        nearest = min(candidates, key=lambda beat: abs(beat - cut))
        snapped = nearest if abs(nearest - cut) <= tolerance else cut
        if previous is not None and snapped - previous < min_gap:
            # The beat is taken by the previous cut: keep this cut where it
            # was, and never let it land before (or on) its predecessor.
            snapped = max(cut, previous + min_gap)
        out.append(round(snapped, 3))
        previous = snapped
    return out
