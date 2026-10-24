# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Look and delivery for montage output: fill, Ken Burns, grade and encode.

The first assemble pass letterboxed every mismatched visual with black bars,
showed stills frozen, and encoded with one middle-of-the-road x264 setting.
That reads as a slideshow, not a marketing cut. This module holds the pure
filter and argv builders that fix it, so :mod:`navin.montage.assemble` only
decides when to use them:

- ``fill``: ``pad`` keeps the legacy letterbox, ``blur`` fills the bars with a
  blurred, darkened copy of the same frame, ``color`` fills them with a brand
  color instead of black;
- Ken Burns: stills get a slow eased zoom. ``zoompan`` rounds its crop window
  to whole pixels, which jitters at output size, so the frame is upscaled
  first and zoomed at that higher internal resolution;
- grade: a light ``eq`` contrast/saturation lift, ``unsharp`` and an optional
  ``.cube`` LUT through ``lut3d``;
- encode presets: ``social``, ``master``, ``master_prores`` and ``web`` on top
  of the legacy ``default``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FILL_PAD = "pad"
FILL_BLUR = "blur"
FILL_COLOR = "color"
FILL_MODES = frozenset({FILL_PAD, FILL_BLUR, FILL_COLOR})

#: Upscale factor before zoompan. 2x already removes visible stepping on a
#: 1080p canvas; 3x is smoother on slow pushes and still cheap for short stills.
KEN_BURNS_OVERSCAN = 3
#: Cap on the internal width so a 4K canvas does not explode memory.
KEN_BURNS_MAX_INTERNAL = 6480
#: Final zoom of a Ken Burns move (1.0 = no zoom).
KEN_BURNS_ZOOM = 1.12

ENCODE_DEFAULT = "default"
ENCODE_SOCIAL = "social"
ENCODE_MASTER = "master"
ENCODE_MASTER_PRORES = "master_prores"
ENCODE_WEB = "web"
ENCODE_PRESETS = frozenset(
    {ENCODE_DEFAULT, ENCODE_SOCIAL, ENCODE_MASTER, ENCODE_MASTER_PRORES, ENCODE_WEB}
)

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_NAMED_COLORS = frozenset(
    {"black", "white", "gray", "grey", "red", "green", "blue", "yellow", "navy", "orange"}
)


class GradeError(ValueError):
    """Invalid look or encode option."""


@dataclass(frozen=True, slots=True)
class GradeOptions:
    """A light, safe grade: values near identity unless asked otherwise.

    ``lut_path`` points at a ``.cube`` file applied with ``lut3d`` after the
    ``eq`` stage. ``sharpen`` is the ``unsharp`` luma amount (0 disables it).
    """

    contrast: float = 1.06
    saturation: float = 1.12
    brightness: float = 0.0
    gamma: float = 1.0
    sharpen: float = 0.35
    lut_path: str | None = None

    def validate(self) -> None:
        if not 0.5 <= self.contrast <= 2.0:
            raise GradeError("grade contrast must be between 0.5 and 2.0")
        if not 0.0 <= self.saturation <= 3.0:
            raise GradeError("grade saturation must be between 0 and 3")
        if not -0.5 <= self.brightness <= 0.5:
            raise GradeError("grade brightness must be between -0.5 and 0.5")
        if not 0.5 <= self.gamma <= 2.0:
            raise GradeError("grade gamma must be between 0.5 and 2.0")
        if not 0.0 <= self.sharpen <= 2.0:
            raise GradeError("grade sharpen must be between 0 and 2")
        if self.lut_path and not str(self.lut_path).lower().endswith((".cube", ".3dl")):
            raise GradeError("grade LUT must be a .cube or .3dl file")


def filter_path(path: str) -> str:
    """Escape *path* for use as a quoted filter option value.

    Backslashes become forward slashes (Windows), and the colon of a drive
    letter is escaped so it does not end the option.
    """
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def ffmpeg_color(value: str | None, default: str = "black") -> str:
    """Normalize ``#RRGGBB``, ``RRGGBB`` or a basic color name for ffmpeg."""
    raw = (value or "").strip()
    if not raw:
        return default
    match = _HEX_RE.match(raw)
    if match:
        return f"0x{match.group(1).upper()}"
    if raw.lower() in _NAMED_COLORS:
        return raw.lower()
    if raw.lower().startswith("0x") and _HEX_RE.match(raw[2:]):
        return f"0x{raw[2:].upper()}"
    raise GradeError(f"unsupported fill color {value!r}; use #RRGGBB")


def grade_filter(options: GradeOptions | None) -> str:
    """Comma separated filter chain for *options*, or ``""`` for no grade."""
    if options is None:
        return ""
    options.validate()
    parts = [
        "eq="
        f"contrast={_num(options.contrast)}:saturation={_num(options.saturation)}:"
        f"brightness={_num(options.brightness)}:gamma={_num(options.gamma)}"
    ]
    if options.sharpen > 0:
        parts.append(f"unsharp=5:5:{_num(options.sharpen)}:5:5:0")
    if options.lut_path:
        parts.append(f"lut3d=file='{filter_path(options.lut_path)}':interp=tetrahedral")
    return ",".join(parts)


def fill_filter(
    width: int, height: int, *, mode: str = FILL_PAD, color: str | None = None
) -> str:
    """Single-chain fit for ``pad`` (black bars) or ``color`` (brand bars).

    ``blur`` needs a split graph, see :func:`fill_chains`.
    """
    fit = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    pad = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    if mode == FILL_PAD:
        return f"{fit},{pad}"
    if mode == FILL_COLOR:
        return f"{fit},{pad}:color={ffmpeg_color(color)}"
    raise GradeError(f"fill mode {mode!r} needs fill_chains")


def fill_chains(
    source: str,
    label: str,
    width: int,
    height: int,
    *,
    mode: str = FILL_PAD,
    color: str | None = None,
) -> list[str]:
    """Chains that fit ``[source]`` into ``width x height`` and end on ``[label]``.

    ``pad`` keeps the historical graph byte for byte (black bars) so existing
    renders and tests do not change. ``blur`` builds the background from the
    same frame at quarter resolution, which keeps the blur cheap.
    """
    if mode not in FILL_MODES:
        raise GradeError(f"unknown fill mode {mode!r}; use pad, blur or color")
    if mode != FILL_BLUR:
        return [f"{source}{fill_filter(width, height, mode=mode, color=color)}[{label}]"]
    fit = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    small_w = _even(max(2, width // 4))
    small_h = _even(max(2, height // 4))
    # boxblur refuses a radius over half the (chroma) plane, which tiny
    # canvases hit; 12 is plenty at quarter resolution.
    radius = max(1, min(12, min(small_w, small_h) // 4 - 1))
    return [
        f"{source}split=2[{label}bgsrc][{label}fgsrc]",
        f"[{label}bgsrc]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
        f"crop={small_w}:{small_h},boxblur={radius}:3,eq=brightness=-0.06:saturation=1.1,"
        f"scale={width}:{height},setsar=1[{label}bg]",
        f"[{label}fgsrc]{fit}[{label}fg]",
        f"[{label}bg][{label}fg]overlay=(W-w)/2:(H-h)/2[{label}]",
    ]


def ken_burns_filter(
    width: int,
    height: int,
    fps: int,
    duration: float,
    *,
    zoom_in: bool = True,
    zoom: float = KEN_BURNS_ZOOM,
) -> str:
    """Eased zoom on an already filled ``width x height`` still.

    The input must be a looped still (one input frame per output frame), so
    ``d=1`` and the zoom is computed from the output frame number ``on``: a
    formula of ``on`` never resets between input frames the way ``zoom`` does.
    """
    frames = max(1, round(float(duration) * fps))
    factor = KEN_BURNS_OVERSCAN
    while factor > 1 and width * factor > KEN_BURNS_MAX_INTERNAL:
        factor -= 1
    big_w, big_h = _even(width * factor), _even(height * factor)
    span = max(1, frames - 1)
    # Smoothstep easing: starts and lands softly instead of a linear crawl.
    # The whole expression is single quoted in the graph, so its commas are safe.
    ease = f"(3*pow(min(on/{span},1),2)-2*pow(min(on/{span},1),3))"
    delta = _num(max(0.0, zoom - 1.0))
    z = f"1+{delta}*{ease}" if zoom_in else f"{_num(zoom)}-{delta}*{ease}"
    return (
        f"scale={big_w}:{big_h}:flags=lanczos,"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={width}x{height}:fps={fps}"
    )


def encode_args(preset: str, fps: int, *, has_audio: bool) -> list[str]:
    """Codec flags for *preset*; ``default`` is the historical encode."""
    if preset not in ENCODE_PRESETS:
        raise GradeError(
            f"unknown encode preset {preset!r}; use one of: " + ", ".join(sorted(ENCODE_PRESETS))
        )
    audio: list[str] = []
    if preset == ENCODE_MASTER_PRORES:
        if has_audio:
            audio = ["-c:a", "pcm_s16le", "-ar", "48000"]
        return audio + [
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-pix_fmt",
            "yuv422p10le",
            "-r",
            str(fps),
            "-fps_mode",
            "cfr",
        ]
    if has_audio:
        bitrate = {ENCODE_MASTER: "320k", ENCODE_WEB: "160k"}.get(preset, "192k")
        audio = ["-c:a", "aac", "-b:a", bitrate]
        if preset != ENCODE_DEFAULT:
            audio += ["-ar", "48000"]
    if preset == ENCODE_DEFAULT:
        return audio + [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-movflags",
            "+faststart",
        ]
    crf, speed = {
        ENCODE_SOCIAL: ("18", "slow"),
        ENCODE_MASTER: ("14", "slow"),
        ENCODE_WEB: ("22", "medium"),
    }[preset]
    return audio + [
        "-c:v",
        "libx264",
        "-preset",
        speed,
        "-crf",
        crf,
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-g",
        str(max(1, fps * 2)),
        "-movflags",
        "+faststart",
    ]


def preset_wants_mov(preset: str) -> bool:
    """ProRes belongs in a QuickTime container."""
    return preset == ENCODE_MASTER_PRORES


def _even(value: int) -> int:
    return value if value % 2 == 0 else value + 1


def _num(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"
