# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Assemble a finished video from generated parts.

Navin could already generate footage, stills, music and (now) narration, but it
had no way to hand back one playable file: the montage toolchain only rendered
HyperFrames HTML and packaged existing exports. This module is the missing step.
It takes the artifacts a turn produced and muxes them into a single MP4:

- visuals are normalized to one canvas, so ``concat`` never chokes on mismatched
  resolution or sample aspect ratio, and stills get an explicit duration;
- video clips can be trimmed to a source ``start``/``end`` window, so a good
  take does not have to be re-generated just to lose a slow intro;
- clips can be joined with ``xfade`` transitions instead of a hard cut, which
  needs every clip length to compute the fade offsets;
- music is looped and laid under the narration at a bed level, with the bed
  gain adjustable per request;
- when narration exists, the music is ducked with ``sidechaincompress`` instead
  of a fixed volume cut, so speech stays intelligible without killing the track;
- the output is cut to the length of the visuals, never the looped music.

Premium options (all off by default, all on with ``quality="premium"``):

- ``fill="blur"``/``"color"`` replaces the black letterbox bars with a
  blurred copy of the frame or a brand color (:mod:`navin.montage.grade`);
- ``ken_burns`` gives stills a slow eased zoom rendered at high internal
  resolution, so it does not jitter;
- ``grade`` applies a light ``eq``/``unsharp`` look and an optional ``.cube``
  LUT before captions are burned (captions are never graded);
- ``encode`` picks a delivery preset: ``social``, ``master``,
  ``master_prores`` or ``web`` (``default`` is the historical encode);
- ``audio_mix`` turns on the mastered mix of :mod:`navin.montage.audio`
  (voice clean-up, smoothed duck, music fades, SFX, two-pass loudnorm);
- ``voice_cues`` places one narration file per scene;
- ASS captions from :mod:`navin.montage.captions` are burned with libass,
  ``fonts_dir`` pointing at the brand fonts.

The command construction is a pure function so the filter graph can be tested
without ffmpeg present, which is the part that actually breaks.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from navin.montage.audio import (
    AudioMixError,
    AudioMixOptions,
    SfxCue,
    VoiceCue,
    build_mix_graph,
    loudnorm_measure_filter,
    mix_audio,
    resolve_loudness_target,
    snap_to_beats,  # noqa: F401 - public API: callers snap cuts via assemble
)
from navin.montage.captions import build_captions
from navin.montage.ffmpeg_runner import run_process, useful_stderr
from navin.montage.grade import (
    ENCODE_DEFAULT,
    ENCODE_PRESETS,
    ENCODE_SOCIAL,
    FILL_BLUR,
    FILL_COLOR,
    FILL_MODES,
    FILL_PAD,
    GradeError,
    GradeOptions,
    encode_args,
    fill_chains,
    fill_filter,
    filter_path,
    grade_filter,
    ken_burns_filter,
    preset_wants_mov,
)

QUALITY_STANDARD = "standard"
QUALITY_PREMIUM = "premium"
QUALITIES = frozenset({QUALITY_STANDARD, QUALITY_PREMIUM})

KIND_IMAGE = "image"
KIND_VIDEO = "video"

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})
#: Animated GIFs decode as a silent video stream, so they cut and fade like clips.
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".gif"})

#: Stills need a duration; this is what a still gets when the caller omits one.
DEFAULT_IMAGE_DURATION = 3.0
#: Music sits this far under unity so narration and effects have headroom.
DEFAULT_MUSIC_GAIN_DB = -16.0
#: Long enough to read as a fade, short enough not to eat the clips.
DEFAULT_TRANSITION_DURATION = 0.5

#: ``xfade`` styles worth offering; the filter accepts more, but these render
#: predictably on every ffmpeg the montage installer ships.
TRANSITIONS = frozenset(
    {
        "fade",
        "fadeblack",
        "fadewhite",
        "dissolve",
        "wipeleft",
        "wiperight",
        "wipeup",
        "wipedown",
        "slideleft",
        "slideright",
        "slideup",
        "slidedown",
        "circleopen",
        "circleclose",
        "radial",
        "smoothleft",
        "smoothright",
        "pixelize",
        "hblur",
        "distance",
        "zoomin",
    }
)


class AssembleError(ValueError):
    """Invalid assemble request, surfaced to the model as a readable error."""


def classify_visual(path: str | Path) -> str:
    """Return ``image`` or ``video`` for *path* based on its suffix."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return KIND_IMAGE
    if suffix in VIDEO_SUFFIXES:
        return KIND_VIDEO
    raise AssembleError(f"unsupported visual format: {path}")


@dataclass(frozen=True, slots=True)
class VisualClip:
    """One visual input: a video clip, or a still shown for ``duration`` seconds.

    ``start``/``end`` are source trim points in seconds for video clips: the
    part of the file that plays, not timeline positions. ``duration`` remains
    the still display time, or a caller-declared source length for a video.
    """

    path: str
    kind: str
    duration: float | None = None
    start: float | None = None
    end: float | None = None

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        duration: float | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> VisualClip:
        kind = classify_visual(path)
        if kind == KIND_IMAGE and (duration is None or duration <= 0):
            duration = DEFAULT_IMAGE_DURATION
        return cls(path=str(path), kind=kind, duration=duration, start=start, end=end)


@dataclass(frozen=True, slots=True)
class AssembleSpec:
    """Everything needed to mux one deliverable."""

    visuals: tuple[VisualClip, ...]
    output: str
    music: str | None = None
    voice: str | None = None
    subtitles: str | None = None
    width: int = 1080
    height: int = 1920
    fps: int = 30
    music_gain_db: float = DEFAULT_MUSIC_GAIN_DB
    transition: str = "none"
    transition_duration: float = DEFAULT_TRANSITION_DURATION
    extra_metadata: dict[str, str] = field(default_factory=dict)

    # Premium knobs are real fields only on PremiumAssembleSpec: the
    # historical spec keeps its exact field set, because saved timelines are
    # built from ``asdict(spec)`` and reject unknown keys. Here they read as
    # their "off" defaults so every builder can use them uniformly.
    @property
    def fill(self) -> str:
        return FILL_PAD

    @property
    def fill_color(self) -> str | None:
        return None

    @property
    def ken_burns(self) -> bool:
        return False

    @property
    def grade(self) -> GradeOptions | None:
        return None

    @property
    def encode(self) -> str:
        return ENCODE_DEFAULT

    @property
    def fonts_dir(self) -> str | None:
        return None

    @property
    def voice_cues(self) -> tuple[VoiceCue, ...]:
        return ()

    @property
    def audio_mix(self) -> AudioMixOptions | None:
        return None

    @property
    def premixed_audio(self) -> str | None:
        return None

    @property
    def has_audio(self) -> bool:
        return bool(
            self.music
            or self.voice
            or self.voice_cues
            or self.premixed_audio
            or (self.audio_mix is not None and self.audio_mix.sfx)
        )

    @property
    def uses_mastered_mix(self) -> bool:
        """The audio goes through :mod:`navin.montage.audio` rather than the
        historical two-input mix."""
        return self.premixed_audio is None and (
            self.audio_mix is not None or bool(self.voice_cues)
        )

    @property
    def has_transitions(self) -> bool:
        """Crossfades only exist between clips, so one visual means none."""
        return self.transition not in ("", "none") and len(self.visuals) > 1


@dataclass(frozen=True, slots=True)
class PremiumAssembleSpec(AssembleSpec):
    """An :class:`AssembleSpec` carrying the premium look/audio options.

    :func:`spec_from_paths` only returns this class when at least one option
    differs from the historical render.
    """

    #: ``pad`` (black bars, historical), ``blur`` or ``color``.
    fill: str = FILL_PAD
    fill_color: str | None = None
    ken_burns: bool = False
    grade: GradeOptions | None = None
    encode: str = ENCODE_DEFAULT
    #: Brand font folder handed to libass when burning captions.
    fonts_dir: str | None = None
    #: One narration file per scene, placed on the timeline.
    voice_cues: tuple[VoiceCue, ...] = ()
    #: Mastered mix options; ``None`` keeps the historical mix.
    audio_mix: AudioMixOptions | None = None
    #: A finished audio track (from :func:`navin.montage.audio.mix_audio`)
    #: that replaces music/voice mixing entirely.
    premixed_audio: str | None = None


#: Values of the premium fields that reproduce the historical render.
_PREMIUM_DEFAULTS: dict[str, Any] = {
    "fill": FILL_PAD,
    "fill_color": None,
    "ken_burns": False,
    "grade": None,
    "encode": ENCODE_DEFAULT,
    "fonts_dir": None,
    "voice_cues": (),
    "audio_mix": None,
    "premixed_audio": None,
}


def validate_spec(spec: AssembleSpec) -> None:
    """Raise :class:`AssembleError` when the request cannot produce a video."""
    if not spec.visuals:
        raise AssembleError("at least one visual is required: pass generated clips or images")
    if not spec.output:
        raise AssembleError("output path is required")
    if spec.width <= 0 or spec.height <= 0:
        raise AssembleError("width and height must be positive")
    if not 1 <= spec.fps <= 120:
        raise AssembleError("fps must be between 1 and 120")
    for clip in spec.visuals:
        if clip.kind not in {KIND_IMAGE, KIND_VIDEO}:
            raise AssembleError(f"unknown visual kind: {clip.kind}")
        if clip.kind == KIND_IMAGE and (clip.duration is None or clip.duration <= 0):
            raise AssembleError(f"image needs a positive duration: {clip.path}")
        if clip.kind == KIND_IMAGE and (clip.start or clip.end):
            raise AssembleError(f"images cannot be trimmed, set its duration instead: {clip.path}")
        if clip.start is not None and clip.start < 0:
            raise AssembleError(f"trim start cannot be negative: {clip.path}")
        if clip.end is not None and clip.end <= (clip.start or 0.0):
            raise AssembleError(f"trim end must be after trim start: {clip.path}")
    if spec.transition not in ("", "none"):
        if spec.transition not in TRANSITIONS:
            raise AssembleError(
                f"unknown transition {spec.transition!r}; use none or one of: "
                + ", ".join(sorted(TRANSITIONS))
            )
        if not 0 < spec.transition_duration <= 5:
            raise AssembleError("transition_duration must be between 0 and 5 seconds")
    if not -60 <= spec.music_gain_db <= 12:
        raise AssembleError("music_gain_db must be between -60 and +12 dB")
    if spec.fill not in FILL_MODES:
        raise AssembleError(f"unknown fill {spec.fill!r}; use pad, blur or color")
    if spec.encode not in ENCODE_PRESETS:
        raise AssembleError(
            f"unknown encode preset {spec.encode!r}; use one of: "
            + ", ".join(sorted(ENCODE_PRESETS))
        )
    if preset_wants_mov(spec.encode) and Path(spec.output).suffix.lower() != ".mov":
        raise AssembleError("the ProRes master needs a .mov output path")
    try:
        if spec.fill == FILL_COLOR:
            fill_filter(spec.width, spec.height, mode=FILL_COLOR, color=spec.fill_color)
        if spec.grade is not None:
            spec.grade.validate()
        if spec.audio_mix is not None:
            spec.audio_mix.validate()
    except (GradeError, AudioMixError) as exc:
        raise AssembleError(str(exc)) from exc
    for cue in spec.voice_cues:
        if cue.at < 0:
            raise AssembleError(f"narration cannot start before zero: {cue.path}")
    collision = output_input_collision(spec)
    if collision is not None:
        raise AssembleError(
            f"the output path is also an input ({collision}); ffmpeg cannot edit a file "
            "in place, so choose another output name or remove that clip"
        )


def output_input_collision(spec: AssembleSpec) -> str | None:
    """Return the input that *spec.output* would overwrite, or ``None``.

    Rendering a previous export back into itself looks like a legitimate
    re-render from the UI (the export shows up in the asset library), but
    ffmpeg refuses it and any cleanup afterwards would destroy the source.
    """
    target = _comparable_path(spec.output)
    if target is None:
        return None
    inputs = (
        [clip.path for clip in spec.visuals]
        + [
            path
            for path in (spec.music, spec.voice, spec.subtitles, spec.premixed_audio)
            if path
        ]
        + [cue.path for cue in spec.voice_cues]
    )
    for path in inputs:
        if _comparable_path(path) == target:
            return path
    return None


def _comparable_path(raw: str) -> Path | None:
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _input_args(spec: AssembleSpec, bounded: bool = False) -> tuple[list[str], dict[str, int]]:
    """Build the ffmpeg input flags and remember each stream's index.

    ``bounded`` says whether the caller will cap the output with ``-t``. Only then
    is it safe to loop the music bed: an endless input with no cap never reaches
    end of stream, and the render never terminates.
    """
    args: list[str] = []
    index = 0
    indices: dict[str, int] = {}
    for position, clip in enumerate(spec.visuals):
        if clip.kind == KIND_IMAGE:
            if spec.ken_burns:
                # zoompan emits one frame per input frame (d=1), so the looped
                # still must already run at the output rate.
                args += ["-framerate", str(spec.fps)]
            args += ["-loop", "1", "-t", _seconds(clip.duration)]
        else:
            # Input-side seeking: with a re-encode it is frame accurate, and it
            # avoids decoding the part of the take that was cut anyway. -t is
            # used instead of -to so trims work on every ffmpeg in the field.
            if clip.start:
                args += ["-ss", _seconds(clip.start)]
            if clip.end is not None:
                args += ["-t", _seconds(clip.end - (clip.start or 0.0))]
        args += ["-i", clip.path]
        indices[f"visual:{position}"] = index
        index += 1
    if spec.music:
        if bounded:
            # A short track still covers a long edit; -t ends the file.
            args += ["-stream_loop", "-1"]
        args += ["-i", spec.music]
        indices["music"] = index
        index += 1
    if spec.voice:
        args += ["-i", spec.voice]
        indices["voice"] = index
        index += 1
    if spec.premixed_audio:
        args += ["-i", spec.premixed_audio]
        indices["premix"] = index
        index += 1
    for position, cue in enumerate(spec.voice_cues):
        args += ["-i", cue.path]
        indices[f"voice:{position}"] = index
        index += 1
    return args, indices


def _seconds(value: float | None) -> str:
    return f"{float(value or 0):.3f}".rstrip("0").rstrip(".") or "0"


def build_video_filters(
    spec: AssembleSpec,
    indices: dict[str, int],
    clip_durations: list[float | None] | None = None,
) -> list[str]:
    """Normalize every visual to the same canvas, then join them.

    A hard cut uses ``concat``. With a transition, clips are chained through
    ``xfade``, which needs each clip's played length (*clip_durations*) to place
    the fade offsets: a wrong offset fades over a frozen last frame.
    """
    transitions = spec.has_transitions
    # xfade refuses inputs whose timebases differ, and a decoded MP4 and a
    # looped still rarely agree, so pin one timebase when fading.
    settb = ",settb=AVTB" if transitions else ""
    chains: list[str] = []
    labels: list[str] = []
    for position, clip in enumerate(spec.visuals):
        source = indices[f"visual:{position}"]
        label = f"v{position}"
        labels.append(f"[{label}]")
        if spec.fill == FILL_BLUR:
            chains += fill_chains(
                f"[{source}:v]", f"f{position}", spec.width, spec.height, mode=FILL_BLUR
            )
            head = f"[f{position}]"
        else:
            # pad keeps the historical chain byte for byte.
            head = (
                f"[{source}:v]"
                + fill_filter(spec.width, spec.height, mode=spec.fill, color=spec.fill_color)
                + ","
            )
        motion = ""
        if spec.ken_burns and clip.kind == KIND_IMAGE:
            motion = (
                ken_burns_filter(
                    spec.width,
                    spec.height,
                    spec.fps,
                    float(clip.duration or DEFAULT_IMAGE_DURATION),
                    zoom_in=position % 2 == 0,
                )
                + ","
            )
        chains.append(
            f"{head}{motion}setsar=1,fps={spec.fps},format=yuv420p{settb}[{label}]"
        )
    if len(labels) == 1:
        chains.append(f"{labels[0]}null[vcat]")
    elif transitions:
        chains += _xfade_chains(spec, labels, clip_durations)
    else:
        chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vcat]")
    # Grade first, captions last: burned text must keep the brand colors.
    post: list[str] = []
    look = grade_filter(spec.grade)
    if look:
        post.append(look)
    if spec.subtitles:
        burn = f"subtitles='{filter_path(spec.subtitles)}'"
        if spec.fonts_dir:
            burn += f":fontsdir='{filter_path(spec.fonts_dir)}'"
        post.append(burn)
    chains.append(f"[vcat]{','.join(post) or 'null'}[vout]")
    return chains


def _xfade_chains(
    spec: AssembleSpec,
    labels: list[str],
    clip_durations: list[float | None] | None,
) -> list[str]:
    """Chain every clip pair through xfade, ending on the ``[vcat]`` label."""
    if (
        clip_durations is None
        or len(clip_durations) != len(labels)
        or any(duration is None or duration <= 0 for duration in clip_durations)
    ):
        raise AssembleError(
            "transitions need the length of every clip to place the fades; "
            "a clip could not be measured. Pass durations= for it, trim it "
            "with an explicit end, or use transition=none."
        )
    fade = spec.transition_duration
    shortest = min(float(duration or 0.0) for duration in clip_durations)
    if fade >= shortest:
        raise AssembleError(
            f"transition_duration={_seconds(fade)}s must be shorter than the "
            f"shortest clip ({_seconds(shortest)}s), or the fades overlap"
        )
    chains: list[str] = []
    current = labels[0]
    offset = 0.0
    for position in range(1, len(labels)):
        offset += float(clip_durations[position - 1] or 0.0) - fade
        target = "[vcat]" if position == len(labels) - 1 else f"[x{position}]"
        chains.append(
            f"{current}{labels[position]}"
            f"xfade=transition={spec.transition}:duration={_seconds(fade)}:"
            f"offset={_seconds(offset)}{target}"
        )
        current = target
    return chains


def build_audio_filters(
    spec: AssembleSpec,
    indices: dict[str, int],
    bounded: bool = False,
    total_duration: float | None = None,
) -> list[str]:
    """Mix the bed and the narration, ducking the bed under speech.

    ``apad`` makes a track span the whole edit instead of cutting to silence
    mid-video, but it also makes the stream endless. It is therefore only used
    when ``bounded`` says a ``-t`` cap will end the output.

    A premixed track is only resampled. The mastered mix (``audio_mix`` or
    ``voice_cues``) is built in-graph with a single dynamic ``loudnorm``
    pass; :func:`run_assemble` replaces it with a two-pass premix first.
    """
    if not spec.has_audio:
        return []
    pad = ",apad" if bounded else ""
    if spec.premixed_audio:
        return [f"[{indices['premix']}:a]aresample=48000{pad}[aout]"]
    if spec.uses_mastered_mix:
        return _mastered_audio_filters(spec, indices, total_duration if bounded else None)
    music_index = indices.get("music")
    voice_index = indices.get("voice")

    if music_index is not None and voice_index is None:
        return [f"[{music_index}:a]volume={spec.music_gain_db}dB,aresample=async=1[aout]"]

    if voice_index is not None and music_index is None:
        return [f"[{voice_index}:a]aresample=async=1{pad}[aout]"]

    return [
        f"[{music_index}:a]volume={spec.music_gain_db}dB,aresample=async=1[bed]",
        f"[{voice_index}:a]aresample=async=1{pad}[speech]",
        "[speech]asplit=2[speechmix][sidechain]",
        "[bed][sidechain]sidechaincompress="
        "threshold=0.03:ratio=8:attack=20:release=400:makeup=1[bedducked]",
        "[bedducked][speechmix]amix=inputs=2:duration=longest:normalize=0[aout]",
    ]


def mix_options_for(spec: AssembleSpec) -> AudioMixOptions:
    """The mastered-mix options of *spec*, with the bed level from the spec."""
    base = spec.audio_mix or AudioMixOptions(loudness_target=None)
    return replace(base, music_gain_db=spec.music_gain_db)


def voice_cues_for(spec: AssembleSpec) -> list[VoiceCue]:
    """Every narration placement: the single ``voice`` at 0, then the cues."""
    cues = [VoiceCue(spec.voice, 0.0)] if spec.voice else []
    return cues + list(spec.voice_cues)


def _mastered_audio_filters(
    spec: AssembleSpec, indices: dict[str, int], duration: float | None
) -> list[str]:
    options = mix_options_for(spec)
    voice_inputs: list[tuple[int, float]] = []
    if spec.voice:
        voice_inputs.append((indices["voice"], 0.0))
    for position, cue in enumerate(spec.voice_cues):
        voice_inputs.append((indices[f"voice:{position}"], cue.at))
    try:
        chains = build_mix_graph(
            voice_inputs=voice_inputs,
            music_input=indices.get("music"),
            duration=duration,
            options=options,
        )
    except AudioMixError as exc:
        raise AssembleError(str(exc)) from exc
    if options.loudness_target is not None:
        chains.append(
            f"[mix]{loudnorm_measure_filter(options.loudness_target, options.true_peak, options.lra)}"
            ",aresample=48000[aout]"
        )
    else:
        chains.append("[mix]anull[aout]")
    return chains


def build_filter_complex(
    spec: AssembleSpec,
    indices: dict[str, int],
    bounded: bool = False,
    clip_durations: list[float | None] | None = None,
    total_duration: float | None = None,
) -> str:
    return ";".join(
        build_video_filters(spec, indices, clip_durations)
        + build_audio_filters(spec, indices, bounded, total_duration)
    )


def clip_play_duration(
    clip: VisualClip, probe: Callable[[str], float | None] | None = None
) -> float | None:
    """Seconds *clip* occupies on the timeline after any trim, or ``None``.

    A still is its display duration. A video is its source length (declared via
    ``duration`` or probed) minus what the trim cuts; an ``end`` past the real
    source length is clamped when the source length is known.
    """
    if clip.kind == KIND_IMAGE:
        return float(clip.duration) if clip.duration and clip.duration > 0 else None
    start = float(clip.start or 0.0)
    source: float | None = None
    if clip.duration and clip.duration > 0:
        source = float(clip.duration)
    elif probe is not None:
        probed = probe(clip.path)
        if probed and probed > 0:
            source = float(probed)
    if clip.end is not None:
        end = float(clip.end) if source is None else min(float(clip.end), source)
        played = end - start
        return played if played > 0 else None
    if source is None:
        return None
    played = source - start
    return played if played > 0 else None


def resolve_clip_durations(
    spec: AssembleSpec,
    probe: Callable[[str], float | None] | None = None,
) -> list[float | None]:
    """Played duration of each visual, aligned with ``spec.visuals``."""
    return [clip_play_duration(clip, probe) for clip in spec.visuals]


def resolve_total_duration(
    spec: AssembleSpec,
    probe: Callable[[str], float | None] | None = None,
) -> float | None:
    """Total length of the visuals, or ``None`` when a clip length is unknown.

    Stills carry their own duration. Video clips need probing, and *probe* is
    injected so the arithmetic can be tested without ffprobe on the box.
    Crossfades overlap the clips, so each transition shortens the timeline.
    """
    total = 0.0
    for duration in resolve_clip_durations(spec, probe):
        if duration is None:
            return None
        total += duration
    if spec.has_transitions:
        total -= spec.transition_duration * (len(spec.visuals) - 1)
    return total if total > 0 else None


def build_ffmpeg_args(
    spec: AssembleSpec,
    ffmpeg: str = "ffmpeg",
    total_duration: float | None = None,
    clip_durations: list[float | None] | None = None,
    *,
    progress: bool = False,
) -> list[str]:
    """Return the full ffmpeg argv for *spec*.

    ``progress`` asks ffmpeg for machine-readable ``key=value`` progress blocks
    on stdout (``-progress pipe:1``) instead of the human status line, which
    is what :class:`ProgressParser` turns into a percentage.
    """
    validate_spec(spec)
    bounded = bool(total_duration and total_duration > 0)
    inputs, indices = _input_args(spec, bounded)
    args = [ffmpeg, "-y", "-hide_banner", "-nostdin"]
    if progress:
        args += ["-nostats", "-progress", "pipe:1"]
    args += inputs
    args += [
        "-filter_complex",
        build_filter_complex(spec, indices, bounded, clip_durations, total_duration),
    ]
    args += ["-map", "[vout]"]
    if spec.has_audio:
        args += ["-map", "[aout]"]
    else:
        args += ["-an"]
    # The default preset is the historical aac 192k + x264 medium crf 20.
    args += encode_args(spec.encode, spec.fps, has_audio=spec.has_audio)
    # Something must bound the output. A measured -t is the reliable bound, and it
    # is what lets the bed loop. Without a measurement every input is left finite
    # so -shortest has a real end of stream to latch onto; -shortest on its own
    # with an endless bed renders forever.
    if bounded:
        args += ["-t", _seconds(total_duration)]
    elif spec.has_audio:
        args += ["-shortest"]
    for key, value in sorted(spec.extra_metadata.items()):
        args += ["-metadata", f"{key}={value}"]
    args.append(spec.output)
    return args


def parse_path_list(value: str | None) -> list[str]:
    """Split a comma separated tool argument into clean paths."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_durations(value: str | None) -> list[float]:
    """Parse comma separated seconds, refusing anything that is not a number."""
    out: list[float] = []
    for chunk in parse_path_list(value):
        try:
            out.append(float(chunk))
        except ValueError as exc:
            raise AssembleError(f"durations must be numbers in seconds, got {chunk!r}") from exc
    return out


def _parse_trim_point(raw: str, chunk: str) -> float | None:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise AssembleError(
            f"trims must look like start-end in seconds "
            f"(e.g. 2-8, 3-, -5, or - for the whole clip), got {chunk!r}"
        ) from exc


def parse_trims(value: str | None) -> list[tuple[float | None, float | None]]:
    """Parse per-visual source trims: ``2-8``, ``3-``, ``-5``, or ``-``/empty.

    Entries are positional, aligned with the visuals list, so empty entries are
    kept (they mean "play the whole clip") instead of being dropped.
    """
    if not (value or "").strip():
        return []
    out: list[tuple[float | None, float | None]] = []
    for chunk in (value or "").split(","):
        chunk = chunk.strip()
        if chunk in ("", "-"):
            out.append((None, None))
            continue
        head, separator, tail = chunk.partition("-")
        if not separator:
            raise AssembleError(
                f"trims must look like start-end in seconds "
                f"(e.g. 2-8, 3-, -5, or - for the whole clip), got {chunk!r}"
            )
        out.append((_parse_trim_point(head.strip(), chunk), _parse_trim_point(tail.strip(), chunk)))
    return out


def spec_from_paths(
    *,
    visuals: list[str],
    output: str,
    durations: list[float] | None = None,
    trims: list[tuple[float | None, float | None]] | None = None,
    music: str | None = None,
    voice: str | None = None,
    subtitles: str | None = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    transition: str | None = None,
    transition_duration: float | None = None,
    music_gain_db: float | None = None,
    quality: str | None = None,
    fill: str | None = None,
    fill_color: str | None = None,
    ken_burns: bool | None = None,
    grade: GradeOptions | Mapping[str, Any] | bool | None = None,
    lut: str | None = None,
    encode: str | None = None,
    loudness: float | str | None = "auto",
    sfx: Iterable[Any] | None = None,
    voice_cues: Iterable[Any] | None = None,
    fonts_dir: str | None = None,
    brand: Mapping[str, Any] | None = None,
) -> AssembleSpec:
    """Build a spec from plain paths, applying a still duration where needed.

    Every option after ``music_gain_db`` is a premium knob; left unset they
    keep the historical render. ``quality="premium"`` turns them all on
    (blur fill, Ken Burns, light grade, social encode, mastered mix at
    -14 LUFS); any explicit knob still wins over the preset.
    """
    if durations and len(durations) not in (0, len(visuals)):
        raise AssembleError(f"durations has {len(durations)} entries for {len(visuals)} visuals")
    if trims and len(trims) not in (0, len(visuals)):
        raise AssembleError(f"trims has {len(trims)} entries for {len(visuals)} visuals")
    clips = tuple(
        VisualClip.from_path(
            path,
            durations[position] if durations and position < len(durations) else None,
            *(trims[position] if trims and position < len(trims) else (None, None)),
        )
        for position, path in enumerate(visuals)
    )
    style = (transition or "none").strip().lower() or "none"
    if style == "crossfade":  # the name people actually type for xfade's "fade"
        style = "fade"
    extras = premium_options(
        quality=quality,
        fill=fill,
        fill_color=fill_color,
        ken_burns=ken_burns,
        grade=grade,
        lut=lut,
        encode=encode,
        loudness=loudness,
        sfx=sfx,
        voice_cues=voice_cues,
        fonts_dir=fonts_dir,
        brand=brand,
    )
    premium = any(extras.get(key, value) != value for key, value in _PREMIUM_DEFAULTS.items())
    spec_cls = PremiumAssembleSpec if premium else AssembleSpec
    return spec_cls(
        **(extras if premium else {}),
        visuals=clips,
        output=output,
        music=music or None,
        voice=voice or None,
        subtitles=subtitles or None,
        width=width,
        height=height,
        fps=fps,
        transition=style,
        transition_duration=(
            float(transition_duration)
            if transition_duration and transition_duration > 0
            else DEFAULT_TRANSITION_DURATION
        ),
        music_gain_db=(
            float(music_gain_db) if music_gain_db is not None else DEFAULT_MUSIC_GAIN_DB
        ),
    )


def resolve_grade(
    grade: GradeOptions | Mapping[str, Any] | bool | None, lut: str | None = None
) -> GradeOptions | None:
    """``True`` for the default light grade, a dict of :class:`GradeOptions`
    fields, an instance, or ``None``/``False``; *lut* adds a ``.cube`` file."""
    if isinstance(grade, GradeOptions):
        resolved: GradeOptions | None = grade
    elif isinstance(grade, Mapping):
        allowed = set(GradeOptions.__dataclass_fields__)
        unknown = sorted(set(grade) - allowed)
        if unknown:
            raise AssembleError("unknown grade option(s): " + ", ".join(unknown))
        resolved = GradeOptions(**dict(grade))
    elif grade:
        resolved = GradeOptions()
    else:
        resolved = None
    if lut:
        resolved = replace(resolved or GradeOptions(), lut_path=str(lut))
    return resolved


def _voice_cue_list(voice_cues: Iterable[Any] | None) -> tuple[VoiceCue, ...]:
    out: list[VoiceCue] = []
    for item in voice_cues or ():
        if isinstance(item, VoiceCue):
            out.append(item)
        elif isinstance(item, Mapping):
            path = item.get("path")
            if not path:
                raise AssembleError("each narration segment needs a path")
            out.append(VoiceCue(str(path), float(item.get("at", item.get("start", 0.0)) or 0.0)))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            out.append(VoiceCue(str(item[0]), float(item[1])))
        else:
            raise AssembleError("narration segments must be {path, at} objects or (path, at) pairs")
    return tuple(out)


def _sfx_list(sfx: Iterable[Any] | None) -> tuple[SfxCue, ...]:
    out: list[SfxCue] = []
    for item in sfx or ():
        if isinstance(item, SfxCue):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(
                SfxCue(
                    str(item.get("kind") or item.get("type") or ""),
                    float(item.get("at", item.get("time", 0.0)) or 0.0),
                    float(item.get("gain_db", -6.0)),
                )
            )
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            out.append(SfxCue(str(item[0]), float(item[1]), *(float(v) for v in item[2:3])))
        else:
            raise AssembleError("sfx cues must be {kind, at} objects or (kind, at) pairs")
    return tuple(out)


def premium_options(
    *,
    quality: str | None = None,
    fill: str | None = None,
    fill_color: str | None = None,
    ken_burns: bool | None = None,
    grade: GradeOptions | Mapping[str, Any] | bool | None = None,
    lut: str | None = None,
    encode: str | None = None,
    loudness: float | str | None = "auto",
    sfx: Iterable[Any] | None = None,
    voice_cues: Iterable[Any] | None = None,
    fonts_dir: str | None = None,
    brand: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the premium knobs into :class:`AssembleSpec` fields.

    ``quality="premium"`` supplies the defaults (blur fill, Ken Burns, light
    grade, social encode, mastered mix at -14 LUFS, or -16 with the web
    encode); explicit arguments override it. ``loudness="auto"`` means
    "follow the quality", ``None`` disables normalization. A brand kit
    supplies the fill color for ``fill="color"``.
    """
    level = (quality or QUALITY_STANDARD).strip().lower() or QUALITY_STANDARD
    if level not in QUALITIES:
        raise AssembleError(f"unknown quality {quality!r}; use standard or premium")
    premium = level == QUALITY_PREMIUM
    chosen_fill = (fill or (FILL_BLUR if premium else FILL_PAD)).strip().lower()
    color = fill_color
    if color is None and isinstance(brand, Mapping):
        colors = brand.get("colors") if isinstance(brand.get("colors"), Mapping) else {}
        color = colors.get("background") or colors.get("primary")
    chosen_encode = (encode or (ENCODE_SOCIAL if premium else ENCODE_DEFAULT)).strip().lower()
    resolved_grade = resolve_grade(grade if grade is not None else premium, lut)
    if isinstance(loudness, str) and loudness.strip().lower() == "auto":
        target = (
            (-16.0 if chosen_encode == "web" else -14.0) if premium else None
        )
        explicit_loudness = premium
    else:
        try:
            target = resolve_loudness_target(loudness)
        except AudioMixError as exc:
            raise AssembleError(str(exc)) from exc
        explicit_loudness = True
    cues = _voice_cue_list(voice_cues)
    effects = _sfx_list(sfx)
    audio_mix: AudioMixOptions | None = None
    if explicit_loudness and (target is not None or premium) or effects or cues:
        audio_mix = AudioMixOptions(
            loudness_target=target,
            voice_chain=premium or target is not None,
            sfx=effects,
        )
    return {
        "fill": chosen_fill,
        "fill_color": color if chosen_fill == FILL_COLOR else None,
        "ken_burns": bool(premium if ken_burns is None else ken_burns),
        "grade": resolved_grade,
        "encode": chosen_encode,
        "audio_mix": audio_mix,
        "voice_cues": cues,
        "fonts_dir": fonts_dir or None,
    }


def find_ffprobe(ffmpeg: str | None = None) -> str | None:
    """Locate ffprobe: sibling of the ffmpeg in use, then PATH, then the bundle.

    The bundled copy comes last so that when an operator pins a specific ffmpeg
    on PATH, its own ffprobe (same version, same builds) is what measures the
    streams; the bundle still covers a PATH ffmpeg that travels alone.
    """
    import shutil

    if ffmpeg:
        candidate = Path(ffmpeg)
        sibling = candidate.with_name(candidate.name.replace("ffmpeg", "ffprobe"))
        if sibling.is_file():
            return str(sibling)
    which = shutil.which("ffprobe")
    if which:
        return which
    from navin.montage.detect import ffprobe_bundled_bin

    return ffprobe_bundled_bin()


async def probe_duration(path: str, ffprobe: str, timeout_s: float = 20.0) -> float | None:
    """Return the duration of *path* in seconds, or ``None`` when unreadable."""
    result = await run_process(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        timeout_s=timeout_s,
    )
    if not result.ok:
        return None
    try:
        value = float(result.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def parse_ffmpeg_duration(stderr: str) -> float | None:
    """Read the ``Duration:`` line ffmpeg prints when it opens a file."""
    match = _DURATION_RE.search(stderr or "")
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return total if total > 0 else None


async def probe_duration_with_ffmpeg(
    path: str, ffmpeg: str, timeout_s: float = 20.0
) -> float | None:
    """Measure a clip using ffmpeg alone, for hosts that ship no ffprobe.

    The montage installer only ever placed an ``ffmpeg`` binary in
    ``~/.navin/montage/bin``, so assuming ffprobe exists would leave assembly
    unbounded on exactly the machines Navin set up itself. Asking ffmpeg to open a
    file with no output makes it print the container header and exit non-zero,
    which costs no decoding.
    """
    result = await run_process(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", path],
        timeout_s=timeout_s,
        capture_stdout=False,
    )
    return parse_ffmpeg_duration(result.raw_stderr)


async def probe_video_lengths(
    spec: AssembleSpec, ffmpeg: str | None = None
) -> dict[str, float | None]:
    """Source length of every video clip that carries no declared duration."""
    ffprobe = await asyncio.to_thread(find_ffprobe, ffmpeg)
    probed: dict[str, float | None] = {}
    for clip in spec.visuals:
        if clip.kind != KIND_VIDEO or (clip.duration and clip.duration > 0):
            continue
        if clip.path in probed:
            continue
        if ffprobe:
            probed[clip.path] = await probe_duration(clip.path, ffprobe)
        elif ffmpeg:
            probed[clip.path] = await probe_duration_with_ffmpeg(clip.path, ffmpeg)
        else:
            probed[clip.path] = None
    return probed


async def measure_total_duration(spec: AssembleSpec, ffmpeg: str | None = None) -> float | None:
    """Measure the visual timeline, probing video clips that carry no duration."""
    probed = await probe_video_lengths(spec, ffmpeg)
    return resolve_total_duration(spec, probed.get)


@dataclass(frozen=True, slots=True)
class RenderProgress:
    """One ffmpeg progress report, normalized for the UI.

    ``fraction`` is ``None`` when the total length is unknown (an unmeasured
    clip); the UI then shows the elapsed output time instead of a percentage.
    """

    out_time_s: float
    fraction: float | None = None
    frame: int | None = None
    fps: float | None = None
    speed: float | None = None
    done: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "out_time_s": round(self.out_time_s, 3),
            "fraction": None if self.fraction is None else round(self.fraction, 4),
            "frame": self.frame,
            "fps": self.fps,
            "speed": self.speed,
            "done": self.done,
        }


_OUT_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$")


def _parse_out_time(value: str) -> float | None:
    match = _OUT_TIME_RE.match(value.strip())
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


class ProgressParser:
    """Assemble ffmpeg ``-progress`` key=value lines into progress reports.

    ffmpeg prints one block of ``key=value`` lines per update and closes it
    with ``progress=continue`` or ``progress=end``. Only a closed block is
    reported, so a half-written block never yields a bogus percentage.
    """

    def __init__(self, total_duration: float | None) -> None:
        self.total = float(total_duration) if total_duration and total_duration > 0 else None
        self._block: dict[str, str] = {}

    def feed(self, line: str) -> RenderProgress | None:
        key, separator, value = line.partition("=")
        if not separator:
            return None
        key = key.strip()
        value = value.strip()
        if key != "progress":
            self._block[key] = value
            return None
        block, self._block = self._block, {}
        out_time = self._out_time(block)
        done = value == "end"
        fraction: float | None = None
        if self.total:
            fraction = 1.0 if done else max(0.0, min(0.999, out_time / self.total))
        return RenderProgress(
            out_time_s=out_time,
            fraction=fraction,
            frame=_int_or_none(block.get("frame")),
            fps=_float_or_none(block.get("fps")),
            speed=_float_or_none((block.get("speed") or "").rstrip("x")),
            done=done,
        )

    @staticmethod
    def _out_time(block: dict[str, str]) -> float:
        # out_time_us and out_time_ms are both microseconds (a long-standing
        # ffmpeg quirk); out_time is the human HH:MM:SS.micro form.
        for key in ("out_time_us", "out_time_ms"):
            raw = block.get(key)
            if raw and raw.lstrip("-").isdigit():
                return max(0.0, int(raw) / 1_000_000)
        parsed = _parse_out_time(block.get("out_time") or "")
        return max(0.0, parsed or 0.0)


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        return None


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(str(value).strip()) if value not in (None, "", "N/A") else None
    except ValueError:
        return None


ProgressCallback = Callable[[RenderProgress], None]


async def run_assemble(
    spec: AssembleSpec,
    *,
    ffmpeg: str | None = None,
    timeout_s: float = 900.0,
    on_progress: ProgressCallback | None = None,
    cancel: asyncio.Event | None = None,
) -> dict[str, object]:
    """Run ffmpeg for *spec* and report the outcome.

    ``on_progress`` receives live :class:`RenderProgress` reports; ``cancel``
    stops the render, removes the partial file and reports ``cancelled``.
    """
    from navin.montage import detect

    # PATH scans can take seconds on WSL; keep them off the gateway loop.
    binary = ffmpeg or await asyncio.to_thread(detect.find_ffmpeg)
    if not binary:
        return {
            "ok": False,
            "error": (
                "ffmpeg is not installed. Run the montage tool with "
                "action=setup and package=ffmpeg first."
            ),
        }
    live = on_progress is not None or cancel is not None
    output = Path(spec.output).expanduser()
    # ffmpeg writes into a hidden sibling and the real path is only replaced
    # once the encode succeeded: a failed or cancelled render leaves the
    # previous export (and anything else living at that path) untouched.
    staging = staging_output_path(output)
    try:
        validate_spec(spec)
        probed = await probe_video_lengths(spec, binary)
        clip_durations = resolve_clip_durations(spec, probed.get)
        total = resolve_total_duration(spec, probed.get)
        args = build_ffmpeg_args(
            replace(spec, output=str(staging)), binary, total, clip_durations, progress=live
        )
    except AssembleError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"cannot create the output folder: {exc}"}
    if cancel is not None and cancel.is_set():
        return {"ok": False, "cancelled": True, "error": "render cancelled"}
    premix: Path | None = None
    mix_report: dict[str, Any] | None = None
    if spec.uses_mastered_mix and spec.has_audio and total:
        # Two-pass loudnorm needs the finished mix to measure, so the audio is
        # mastered into a hidden WAV first and the video pass only muxes it.
        premix = output.with_name(f".{output.stem}.mix-{secrets.token_hex(4)}.wav")
        try:
            mix_report = await mix_audio(
                premix,
                voice=[(cue.path, cue.at) for cue in voice_cues_for(spec)],
                music=spec.music,
                duration=total,
                options=mix_options_for(spec),
                ffmpeg=binary,
                timeout_s=timeout_s,
            )
            mastered = replace(
                spec,
                output=str(staging),
                premixed_audio=str(premix),
                music=None,
                voice=None,
                voice_cues=(),
                audio_mix=None,
            )
            args = build_ffmpeg_args(mastered, binary, total, clip_durations, progress=live)
        except (AudioMixError, AssembleError) as exc:
            _discard_partial_output(premix)
            return {"ok": False, "error": f"audio mix failed: {exc}"}
    if cancel is not None and cancel.is_set():
        if premix is not None:
            _discard_partial_output(premix)
        return {"ok": False, "cancelled": True, "error": "render cancelled"}
    on_line: Callable[[str], None] | None = None
    if live:
        parser = ProgressParser(total)

        def on_line(line: str) -> None:
            report = parser.feed(line)
            if report is not None and on_progress is not None:
                on_progress(report)

    try:
        process_result = await run_process(
            args, timeout_s=timeout_s, on_stdout_line=on_line, cancel=cancel
        )
    finally:
        if premix is not None:
            _discard_partial_output(premix)
    if process_result.cancelled:
        _discard_partial_output(staging)
        return {
            "ok": False,
            "cancelled": True,
            "error": "render cancelled",
            "command": process_result.command,
        }
    if process_result.timed_out:
        _discard_partial_output(staging)
        return {
            "ok": False,
            "error": f"ffmpeg timed out after {timeout_s:.0f}s",
            "command": process_result.command,
            "process": process_result.to_dict(),
        }
    if not process_result.ok:
        _discard_partial_output(staging)
        return {
            "ok": False,
            "error": process_result.stderr
            or (process_result.error.message if process_result.error else "ffmpeg failed"),
            "command": process_result.command,
            "process": process_result.to_dict(),
        }
    try:
        os.replace(staging, output)
    except OSError as exc:
        _discard_partial_output(staging)
        return {
            "ok": False,
            "error": f"render finished but the file could not be moved into place: {exc}",
            "command": process_result.command,
        }
    result: dict[str, object] = {
        "ok": True,
        "output": str(output),
        "size_bytes": output.stat().st_size if output.is_file() else 0,
        "visuals": len(spec.visuals),
        "duration_s": round(total, 3) if total else None,
        "width": spec.width,
        "height": spec.height,
        "fps": spec.fps,
        "has_music": bool(spec.music),
        "has_voice": bool(spec.voice or spec.voice_cues),
        "has_subtitles": bool(spec.subtitles),
        "transition": spec.transition if spec.has_transitions else None,
    }
    if spec.encode != ENCODE_DEFAULT:
        result["encode"] = spec.encode
    if spec.fill != FILL_PAD:
        result["fill"] = spec.fill
    if spec.ken_burns:
        result["ken_burns"] = True
    if spec.grade is not None:
        result["grade"] = True
    if mix_report is not None:
        result["audio"] = {
            "target_lufs": mix_report.get("target_lufs"),
            "measured": mix_report.get("measured"),
            "voice_segments": mix_report.get("voice_segments"),
            "sfx": mix_report.get("sfx"),
        }
    return result


def staging_output_path(output: Path) -> Path:
    """Hidden sibling ffmpeg renders into before the real path is replaced.

    The suffix is kept so ffmpeg still infers the container from it, and the
    leading dot keeps the file out of the asset library while it is written.
    """
    token = secrets.token_hex(4)
    return output.with_name(f".{output.stem}.part-{token}{output.suffix}")


def _discard_partial_output(staging: Path) -> None:
    """A killed or failed ffmpeg leaves a truncated MP4; never ship it."""
    try:
        if staging.is_file():
            staging.unlink()
    except OSError:
        pass


def _last_ffmpeg_error(stderr: str, max_lines: int = 6) -> str:
    """ffmpeg puts the real cause at the end; the banner above it is noise."""
    return useful_stderr(stderr, max_lines=max_lines) or "ffmpeg failed"


async def assemble(
    visuals: list[str],
    output: str,
    *,
    durations: list[float] | None = None,
    trims: list[tuple[float | None, float | None]] | None = None,
    music: str | None = None,
    voice: str | None = None,
    subtitles: str | None = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    transition: str | None = None,
    transition_duration: float | None = None,
    music_gain_db: float | None = None,
    quality: str | None = None,
    fill: str | None = None,
    fill_color: str | None = None,
    ken_burns: bool | None = None,
    grade: GradeOptions | Mapping[str, Any] | bool | None = None,
    lut: str | None = None,
    encode: str | None = None,
    loudness: float | str | None = "auto",
    sfx: Iterable[Any] | None = None,
    voice_segments: Iterable[Any] | None = None,
    captions: Any = None,
    caption_mode: str = "highlight",
    caption_uppercase: bool = False,
    brand: Mapping[str, Any] | None = None,
    ffmpeg: str | None = None,
    timeout_s: float = 900.0,
    on_progress: ProgressCallback | None = None,
    cancel: asyncio.Event | None = None,
) -> dict[str, object]:
    """One call from generated parts to a finished deliverable.

    The first block of arguments is the historical :func:`spec_from_paths`
    surface. The premium block (see :func:`premium_options`) adds fill, Ken
    Burns, grade, LUT, encode preset, loudness target and SFX cues, and
    ``quality="premium"`` turns them all on.

    ``voice_segments`` places one narration file per scene: dicts with
    ``path`` and ``at``/``start`` (seconds), optionally ``text``, ``words``
    and ``words_relative`` as returned by ``generate_speech(segments=...)``.

    ``captions`` burns word-by-word captions: ``True`` builds them from
    ``voice_segments`` (word timings when present, syllable estimate
    otherwise), a list of caption segments is passed to
    :func:`navin.montage.captions.build_captions`, and a ``CaptionResult``
    (or a path to an ``.ass`` file) is burned as is. Brand fonts and colors
    come from *brand* (the shared brand kit contract).
    """
    from navin.montage.captions import CaptionError, CaptionResult

    cues = list(voice_segments or ())
    caption_info: dict[str, Any] | None = None
    fonts_dir: str | None = None
    try:
        if captions is not None and captions is not False:
            if isinstance(captions, CaptionResult):
                subtitles, fonts_dir = captions.path, captions.fonts_dir
                caption_info = captions.to_dict()
            elif isinstance(captions, (str, Path)):
                subtitles = str(captions)
            else:
                source = _caption_source(captions, cues)
                target = Path(output).expanduser()
                built = build_captions(
                    source,
                    target.with_name(f"{target.stem}.captions.ass"),
                    width=width,
                    height=height,
                    brand=brand,
                    mode=caption_mode,
                    uppercase=caption_uppercase,
                )
                subtitles, fonts_dir = built.path, built.fonts_dir
                caption_info = built.to_dict()
        spec = spec_from_paths(
            visuals=visuals,
            output=output,
            durations=durations,
            trims=trims,
            music=music,
            voice=voice,
            subtitles=subtitles,
            width=width,
            height=height,
            fps=fps,
            transition=transition,
            transition_duration=transition_duration,
            music_gain_db=music_gain_db,
            quality=quality,
            fill=fill,
            fill_color=fill_color,
            ken_burns=ken_burns,
            grade=grade,
            lut=lut,
            encode=encode,
            loudness=loudness,
            sfx=sfx,
            voice_cues=[
                {"path": item["path"], "at": item.get("at", item.get("start", 0.0))}
                if isinstance(item, Mapping)
                else item
                for item in cues
            ],
            fonts_dir=fonts_dir,
            brand=brand,
        )
    except (AssembleError, CaptionError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    result = await run_assemble(
        spec, ffmpeg=ffmpeg, timeout_s=timeout_s, on_progress=on_progress, cancel=cancel
    )
    if caption_info is not None:
        result["captions"] = caption_info
    return result


def _caption_source(captions: Any, cues: list[Any]) -> list[Any]:
    """Caption segments for ``assemble(captions=...)``."""
    if captions is True:
        segments: list[Any] = []
        for item in cues:
            if not isinstance(item, Mapping) or not item.get("text"):
                continue
            start = float(item.get("at", item.get("start", 0.0)) or 0.0)
            segment = {
                "text": item["text"],
                "start": start,
                "words": item.get("words") or [],
                "words_relative": bool(item.get("words_relative", True)),
            }
            if item.get("end") is not None:
                segment["end"] = float(item["end"])
            else:
                segment["duration_s"] = item.get("duration_s") or item.get("duration")
            segments.append(segment)
        if not segments:
            raise AssembleError(
                "captions=True needs voice_segments with their text and duration_s"
            )
        return segments
    if isinstance(captions, (list, tuple)):
        return list(captions)
    raise AssembleError("captions must be True, a list of segments, a CaptionResult or a path")

