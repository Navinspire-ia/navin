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

The command construction is a pure function so the filter graph can be tested
without ffmpeg present, which is the part that actually breaks.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from navin.montage.ffmpeg_runner import run_process, useful_stderr

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

    @property
    def has_audio(self) -> bool:
        return bool(self.music or self.voice)

    @property
    def has_transitions(self) -> bool:
        """Crossfades only exist between clips, so one visual means none."""
        return self.transition not in ("", "none") and len(self.visuals) > 1


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
    inputs = [clip.path for clip in spec.visuals] + [
        path for path in (spec.music, spec.voice, spec.subtitles) if path
    ]
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
    for position, _clip in enumerate(spec.visuals):
        source = indices[f"visual:{position}"]
        label = f"v{position}"
        labels.append(f"[{label}]")
        chains.append(
            f"[{source}:v]"
            f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease,"
            f"pad={spec.width}:{spec.height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={spec.fps},format=yuv420p{settb}"
            f"[{label}]"
        )
    if len(labels) == 1:
        chains.append(f"{labels[0]}null[vcat]")
    elif transitions:
        chains += _xfade_chains(spec, labels, clip_durations)
    else:
        chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vcat]")
    if spec.subtitles:
        escaped = str(spec.subtitles).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        chains.append(f"[vcat]subtitles='{escaped}'[vout]")
    else:
        chains.append("[vcat]null[vout]")
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
    spec: AssembleSpec, indices: dict[str, int], bounded: bool = False
) -> list[str]:
    """Mix the bed and the narration, ducking the bed under speech.

    ``apad`` makes a track span the whole edit instead of cutting to silence
    mid-video, but it also makes the stream endless. It is therefore only used
    when ``bounded`` says a ``-t`` cap will end the output.
    """
    if not spec.has_audio:
        return []
    music_index = indices.get("music")
    voice_index = indices.get("voice")
    pad = ",apad" if bounded else ""

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


def build_filter_complex(
    spec: AssembleSpec,
    indices: dict[str, int],
    bounded: bool = False,
    clip_durations: list[float | None] | None = None,
) -> str:
    return ";".join(
        build_video_filters(spec, indices, clip_durations)
        + build_audio_filters(spec, indices, bounded)
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
        build_filter_complex(spec, indices, bounded, clip_durations),
    ]
    args += ["-map", "[vout]"]
    if spec.has_audio:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(spec.fps),
        "-movflags",
        "+faststart",
    ]
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
) -> AssembleSpec:
    """Build a spec from plain paths, applying a still duration where needed."""
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
    return AssembleSpec(
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
    on_line: Callable[[str], None] | None = None
    if live:
        parser = ProgressParser(total)

        def on_line(line: str) -> None:
            report = parser.feed(line)
            if report is not None and on_progress is not None:
                on_progress(report)

    process_result = await run_process(
        args, timeout_s=timeout_s, on_stdout_line=on_line, cancel=cancel
    )
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
    return {
        "ok": True,
        "output": str(output),
        "size_bytes": output.stat().st_size if output.is_file() else 0,
        "visuals": len(spec.visuals),
        "duration_s": round(total, 3) if total else None,
        "width": spec.width,
        "height": spec.height,
        "fps": spec.fps,
        "has_music": bool(spec.music),
        "has_voice": bool(spec.voice),
        "has_subtitles": bool(spec.subtitles),
        "transition": spec.transition if spec.has_transitions else None,
    }


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
