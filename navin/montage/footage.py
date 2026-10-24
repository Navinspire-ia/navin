# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Polished demo footage from raw screen recordings (Screen Studio style).

Input contract: an MP4 recorded by the browser recorder plus a sidecar
``<recording>.actions.json``::

    {"version": 1, "viewport": {"width": W, "height": H},
     "device_scale_factor": D, "fps": F, "duration": S,
     "events": [{"t": sec, "type": "click"|"type"|"scroll"|"navigate"|"hover"
                 |"wait"|"key", "x": css_px|null, "y": css_px|null,
                 "bbox": [x, y, w, h]|null, "label": str,
                 "path": [[t, x, y], ...]}]}

Coordinates are CSS pixels of the viewport; video pixels are ``css * D``
(measured from the real frame size, so a recorder that downscaled still maps).

Pipeline (ffmpeg decode -> Pillow sub-pixel camera -> ffmpeg encode):

1. Dead-time trim: ``freezedetect`` static spans minus the windows around
   actions are sped up (smooth rate ramp) or cut. :class:`TimeRemap` is the
   exact time warp; it drives ``setpts`` and remaps every event timestamp.
2. Camera: click/type clusters become zoom targets (1.6x-2.2x at intensity 1),
   navigate/scroll zoom back out; a critically damped spring smooths
   center + log-zoom per frame and the view is clamped to the frame. Each
   decoded frame is cropped at a float rectangle and Lanczos-resampled by
   Pillow (``crop`` + ``sendcmd`` resizing is unstable in ffmpeg 6 and
   ``zoompan`` snaps to whole pixels, which reads as jitter).
3. Cursor: a vector-rendered arrow (light/dark) follows a Hermite path through
   the events (Catmull-Rom through recorded path samples), shrinks slightly on
   click and emits a ripple. Sprites are a PNG sequence; their position is an
   exact per-frame ``overlay`` expression.
4. Layouts: ``crop`` follows the camera for any aspect (smart 9:16 / 1:1
   crop); ``framed`` puts the 16:9 footage inside a branded background with a
   title above and captions below.
"""

from __future__ import annotations

import io
import json
import math
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from navin.montage.ffmpeg_runner import run_process_sync

FOCUS_TYPES = frozenset({"click", "type"})
RESET_TYPES = frozenset({"navigate", "scroll"})
CURSOR_TYPES = frozenset({"click", "type", "hover", "scroll"})
EVENT_TYPES = frozenset({"click", "type", "scroll", "navigate", "hover", "wait", "key"})

INTENSITY_PRESETS = {"off": 0.0, "none": 0.0, "subtle": 0.6, "normal": 1.0, "strong": 1.3}
_DEFAULT_POINT_BOX = (160.0, 64.0)  # css px box around a bare point


class FootageError(RuntimeError):
    """Raised for unusable inputs (missing file, bad actions document)."""


# --------------------------------------------------------------------------
# Actions document
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionEvent:
    t: float
    type: str
    x: float | None = None
    y: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    label: str = ""
    path: tuple[tuple[float, float, float], ...] = ()
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def point(self) -> tuple[float, float] | None:
        if self.x is not None and self.y is not None:
            return float(self.x), float(self.y)
        if self.bbox is not None:
            bx, by, bw, bh = self.bbox
            return bx + bw / 2.0, by + bh / 2.0
        return None

    def rect(self) -> tuple[float, float, float, float] | None:
        """Focus rectangle in css px (bbox, else a default box around the point)."""
        if self.bbox is not None and self.bbox[2] > 0 and self.bbox[3] > 0:
            return self.bbox
        pt = self.point()
        if pt is None:
            return None
        w, h = _DEFAULT_POINT_BOX
        return pt[0] - w / 2.0, pt[1] - h / 2.0, w, h

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.extra)
        out.update(
            {
                "t": round(self.t, 4),
                "type": self.type,
                "x": self.x,
                "y": self.y,
                "bbox": list(self.bbox) if self.bbox is not None else None,
                "label": self.label,
            }
        )
        if self.path:
            out["path"] = [[round(t, 4), x, y] for t, x, y in self.path]
        return out


@dataclass(frozen=True)
class ActionsDoc:
    viewport_width: float
    viewport_height: float
    device_scale_factor: float = 1.0
    fps: float = 30.0
    duration: float = 0.0
    events: tuple[ActionEvent, ...] = ()
    version: int = 1
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.extra)
        out.update(
            {
                "version": self.version,
                "viewport": {"width": self.viewport_width, "height": self.viewport_height},
                "device_scale_factor": self.device_scale_factor,
                "fps": self.fps,
                "duration": round(self.duration, 4),
                "events": [e.to_dict() for e in self.events],
            }
        )
        return out


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def actions_from_dict(data: Any) -> ActionsDoc:
    if not isinstance(data, dict):
        raise FootageError("actions document must be a JSON object")
    viewport = data.get("viewport") if isinstance(data.get("viewport"), dict) else {}
    vw = _num(viewport.get("width")) or 0.0
    vh = _num(viewport.get("height")) or 0.0
    dsf = _num(data.get("device_scale_factor")) or 1.0
    events: list[ActionEvent] = []
    for row in data.get("events") or []:
        if not isinstance(row, dict):
            continue
        t = _num(row.get("t"))
        kind = str(row.get("type") or "").strip().lower()
        if t is None or kind not in EVENT_TYPES:
            continue
        bbox_raw = row.get("bbox")
        bbox: tuple[float, float, float, float] | None = None
        if isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
            vals = [_num(v) for v in bbox_raw]
            if all(v is not None for v in vals):
                bbox = (vals[0], vals[1], vals[2], vals[3])  # type: ignore[assignment]
        path: list[tuple[float, float, float]] = []
        for sample in row.get("path") or []:
            if isinstance(sample, (list, tuple)) and len(sample) >= 3:
                st, sx, sy = (_num(sample[0]), _num(sample[1]), _num(sample[2]))
                if st is not None and sx is not None and sy is not None:
                    path.append((st, sx, sy))
        path.sort(key=lambda s: s[0])
        known = {"t", "type", "x", "y", "bbox", "label", "path"}
        events.append(
            ActionEvent(
                t=max(0.0, t),
                type=kind,
                x=_num(row.get("x")),
                y=_num(row.get("y")),
                bbox=bbox,
                label=str(row.get("label") or ""),
                path=tuple(path),
                extra={k: v for k, v in row.items() if k not in known},
            )
        )
    events.sort(key=lambda e: e.t)
    known_top = {"version", "viewport", "device_scale_factor", "fps", "duration", "events"}
    return ActionsDoc(
        viewport_width=vw,
        viewport_height=vh,
        device_scale_factor=dsf,
        fps=_num(data.get("fps")) or 30.0,
        duration=_num(data.get("duration")) or 0.0,
        events=tuple(events),
        version=int(_num(data.get("version")) or 1),
        extra={k: v for k, v in data.items() if k not in known_top},
    )


def load_actions(path: str | Path) -> ActionsDoc:
    target = Path(path).expanduser()
    if not target.is_file():
        raise FootageError(f"actions file not found: {path}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FootageError(f"actions file is not valid JSON: {exc}") from exc
    return actions_from_dict(data)


def find_actions_sidecar(video: str | Path) -> Path | None:
    """``clip.actions.json`` (or ``clip.mp4.actions.json``) next to *video*."""
    src = Path(video)
    for candidate in (
        src.with_name(f"{src.stem}.actions.json"),
        src.with_name(f"{src.name}.actions.json"),
    ):
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------
# Dead-time trimming
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadSegment:
    """Input span ``[start, end]`` played at ``1/speed`` rate (``speed=inf`` cuts)."""

    start: float
    end: float
    speed: float
    ramp: float = 0.0

    @property
    def keep_rate(self) -> float:
        return 0.0 if math.isinf(self.speed) else 1.0 / self.speed

    @property
    def loss(self) -> float:
        k = 1.0 - self.keep_rate
        return k * (self.end - self.start - self.ramp)

    def loss_at(self, t: float) -> float:
        a, b, r = self.start, self.end, self.ramp
        k = 1.0 - self.keep_rate
        if t <= a:
            return 0.0
        if t >= b:
            return self.loss
        if r > 0 and t < a + r:
            return k * (t - a) ** 2 / (2 * r)
        if t <= b - r:
            return k * r / 2 + k * (t - a - r)
        d = t - (b - r)
        return k * r / 2 + k * (b - a - 2 * r) + k * (d - d * d / (2 * r))

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 4),
            "end": round(self.end, 4),
            "speed": "cut" if math.isinf(self.speed) else round(self.speed, 3),
            "ramp": round(self.ramp, 4),
        }


@dataclass(frozen=True)
class TimeRemap:
    segments: tuple[DeadSegment, ...] = ()

    def map(self, t: float) -> float:
        return t - sum(seg.loss_at(t) for seg in self.segments)

    def output_duration(self, duration: float) -> float:
        return max(0.0, self.map(duration))

    @property
    def removed(self) -> float:
        return sum(seg.loss for seg in self.segments)

    def setpts_expr(self) -> str:
        """ffmpeg expression of :meth:`map` over the input time ``T``."""
        if not self.segments:
            return "T"
        parts = ["T"]
        for seg in self.segments:
            a, b, r = seg.start, seg.end, seg.ramp
            k = 1.0 - seg.keep_rate
            loss = f"{seg.loss:.6f}"
            core = f"({k * r / 2:.6f}+{k:.6f}*(T-{a + r:.6f}))"
            if r > 0:
                ramp_in = f"({k:.6f}*(T-{a:.6f})*(T-{a:.6f})/{2 * r:.6f})"
                tail = f"(T-{b - r:.6f})"
                ramp_out = (
                    f"({k * r / 2 + k * (b - a - 2 * r):.6f}"
                    f"+{k:.6f}*({tail}-{tail}*{tail}/{2 * r:.6f}))"
                )
                inner = f"if(lt(T,{a + r:.6f}),{ramp_in},if(lte(T,{b - r:.6f}),{core},{ramp_out}))"
            else:
                inner = core
            parts.append(f"if(lte(T,{a:.6f}),0,if(gte(T,{b:.6f}),{loss},{inner}))")
        return parts[0] + "".join(f"-{p}" for p in parts[1:])

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [seg.to_dict() for seg in self.segments],
            "removed_seconds": round(self.removed, 4),
        }


_FREEZE_RE = re.compile(r"lavfi\.freezedetect\.freeze_(start|end):\s*([0-9.]+)")


def detect_freezes(
    source: str | Path,
    *,
    min_duration: float = 1.0,
    noise_db: float = -50.0,
    duration: float | None = None,
    ffmpeg: str | None = None,
) -> list[tuple[float, float]]:
    """Static spans via ``freezedetect`` on a cheap downscaled decode."""
    from navin.montage.detect import find_ffmpeg

    binary = ffmpeg or find_ffmpeg()
    if not binary:
        return []
    completed = run_process_sync(
        [
            binary,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(source),
            "-an",
            "-vf",
            f"scale=480:-2,freezedetect=n={noise_db}dB:d={max(0.2, min_duration):.3f}",
            "-f",
            "null",
            "-",
        ],
        timeout_s=max(300.0, (duration or 60.0) * 4),
        capture_stdout=False,
    )
    spans: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in _FREEZE_RE.findall(completed.raw_stderr or completed.stderr):
        if kind == "start":
            start = float(value)
        elif start is not None:
            spans.append((start, float(value)))
            start = None
    if start is not None and duration:
        spans.append((start, float(duration)))
    return spans


def plan_dead_segments(
    freezes: Iterable[tuple[float, float]],
    events: Iterable[ActionEvent],
    *,
    threshold: float = 1.2,
    speed: float = 6.0,
    mode: str = "speed",
    margin: float = 0.3,
    guard_before: float = 0.4,
    guard_after: float = 0.8,
    ramp: float = 0.3,
) -> TimeRemap:
    """Static spans not covered by an action window become :class:`DeadSegment`."""
    guards = sorted((e.t - guard_before, e.t + guard_after) for e in events if e.type != "wait")
    segments: list[DeadSegment] = []
    for a, b in sorted(freezes):
        pieces = [(a, b)]
        for g0, g1 in guards:
            nxt: list[tuple[float, float]] = []
            for p0, p1 in pieces:
                if g1 <= p0 or g0 >= p1:
                    nxt.append((p0, p1))
                    continue
                if g0 > p0:
                    nxt.append((p0, g0))
                if g1 < p1:
                    nxt.append((g1, p1))
            pieces = nxt
        for p0, p1 in pieces:
            if p1 - p0 < threshold:
                continue
            s0, s1 = p0 + margin, p1 - margin
            if s1 - s0 < 0.3:
                continue
            if mode == "cut":
                segments.append(DeadSegment(s0, s1, math.inf, 0.0))
            else:
                r = min(ramp, (s1 - s0) / 3.0)
                segments.append(DeadSegment(s0, s1, max(1.5, float(speed)), r))
    segments.sort(key=lambda s: s.start)
    merged: list[DeadSegment] = []
    for seg in segments:
        if merged and seg.start < merged[-1].end:
            continue
        merged.append(seg)
    return TimeRemap(tuple(merged))


def remap_actions(doc: ActionsDoc, remap: TimeRemap, duration: float) -> ActionsDoc:
    if not remap.segments:
        return replace(doc, duration=duration)
    events = tuple(
        replace(
            e,
            t=remap.map(e.t),
            path=tuple((remap.map(t), x, y) for t, x, y in e.path),
        )
        for e in doc.events
    )
    return replace(doc, events=events, duration=remap.output_duration(duration))


_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _fmt_srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_srt_time(match: re.Match[str]) -> float:
    h, m, s, ms = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for idx, line in enumerate(lines):
            if "-->" in line:
                found = list(_SRT_TIME.finditer(line))
                if len(found) >= 2:
                    body = " ".join(lines[idx + 1 :]).strip()
                    cues.append((_parse_srt_time(found[0]), _parse_srt_time(found[1]), body))
                break
    return cues


def remap_srt(text: str, remap: TimeRemap) -> str:
    out: list[str] = []
    for idx, (a, b, body) in enumerate(parse_srt(text), start=1):
        ra, rb = remap.map(a), remap.map(b)
        if rb - ra < 0.05:
            continue
        out.append(f"{idx}\n{_fmt_srt_time(ra)} --> {_fmt_srt_time(rb)}\n{body}\n")
    return "\n".join(out)


def captions_from_actions(doc: ActionsDoc, *, hold: float = 2.2) -> list[tuple[float, float, str]]:
    """Short captions from event labels (click/type/navigate)."""
    cues: list[tuple[float, float, str]] = []
    labelled = [
        e for e in doc.events if e.label.strip() and e.type in {"click", "type", "navigate"}
    ]
    for idx, event in enumerate(labelled):
        start = max(0.0, event.t - 0.2)
        end = event.t + hold
        if idx + 1 < len(labelled):
            end = min(end, labelled[idx + 1].t - 0.25)
        if end - start >= 0.4:
            cues.append((start, end, event.label.strip()))
    return cues


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraOptions:
    intensity: float = 1.0
    lead: float = 0.6
    hold: float = 1.4
    merge_gap: float = 1.6
    bridge_gap: float = 0.9
    settle: float = 0.7
    fill: float = 0.45


@dataclass
class CameraPath:
    fps: float
    src_w: int
    src_h: int
    base_w: float
    base_h: float
    cx: list[float]
    cy: list[float]
    zoom: list[float]

    def __len__(self) -> int:
        return len(self.cx)

    def region(self, i: int) -> tuple[float, float, float, float]:
        z = self.zoom[i]
        w, h = self.base_w / z, self.base_h / z
        return self.cx[i] - w / 2.0, self.cy[i] - h / 2.0, w, h

    def box(self, i: int) -> tuple[float, float, float, float]:
        """Sub-pixel source rectangle ``(x0, y0, x1, y1)`` kept inside the frame."""
        _, _, w, h = self.region(i)
        w = min(float(self.src_w), w)
        h = min(float(self.src_h), h)
        x = min(max(self.cx[i] - w / 2.0, 0.0), self.src_w - w)
        y = min(max(self.cy[i] - h / 2.0, 0.0), self.src_h - h)
        return x, y, x + w, y + h

    def to_output(
        self, i: int, px: float, py: float, out_w: int, out_h: int
    ) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box(i)
        return (px - x0) * out_w / (x1 - x0), (py - y0) * out_h / (y1 - y0)

    def max_step(self) -> dict[str, float]:
        """Largest per-frame change of center (px) and zoom (ratio)."""
        dc = dz = 0.0
        for i in range(1, len(self.cx)):
            dc = max(dc, math.hypot(self.cx[i] - self.cx[i - 1], self.cy[i] - self.cy[i - 1]))
            dz = max(dz, abs(math.log(self.zoom[i] / self.zoom[i - 1])))
        return {"center_px": dc, "log_zoom": dz}

    def fast_intervals(self, out_w: int) -> list[tuple[float, float]]:
        """Spans where the view moves fast enough to deserve motion blur."""
        flagged: list[int] = []
        for i in range(1, len(self.cx)):
            dlz = abs(math.log(self.zoom[i] / self.zoom[i - 1])) * self.fps
            _, _, w, _ = self.region(i)
            pan = math.hypot(self.cx[i] - self.cx[i - 1], self.cy[i] - self.cy[i - 1])
            pan_out = pan * out_w / max(1.0, w)
            if dlz > 0.9 or pan_out > out_w * 0.012:
                flagged.append(i)
        spans: list[tuple[float, float]] = []
        for i in flagged:
            t0, t1 = (i - 1) / self.fps, (i + 1) / self.fps
            if spans and t0 <= spans[-1][1] + 1e-6:
                spans[-1] = (spans[-1][0], t1)
            else:
                spans.append((t0, t1))
        return spans


def resolve_intensity(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in INTENSITY_PRESETS:
            return INTENSITY_PRESETS[text]
        try:
            value = float(text)
        except ValueError:
            return 1.0
    num = _num(value)
    if num is None:
        return 1.0
    return max(0.0, min(1.5, num))


def _base_region(src_w: int, src_h: int, aspect: float) -> tuple[float, float]:
    if aspect >= src_w / src_h:
        return float(src_w), src_w / aspect
    return src_h * aspect, float(src_h)


def _clamp_center(
    cx: float, cy: float, w: float, h: float, src_w: int, src_h: int
) -> tuple[float, float]:
    cx = min(max(cx, w / 2.0), src_w - w / 2.0) if w < src_w else src_w / 2.0
    cy = min(max(cy, h / 2.0), src_h - h / 2.0) if h < src_h else src_h / 2.0
    return cx, cy


def plan_camera(
    events: Iterable[ActionEvent],
    *,
    src_w: int,
    src_h: int,
    scale_x: float,
    scale_y: float,
    fps: float,
    n_frames: int,
    aspect: float | None = None,
    options: CameraOptions | None = None,
) -> CameraPath:
    """Virtual camera (center + zoom per frame) in source pixels."""
    opts = options or CameraOptions()
    aspect = aspect or (src_w / src_h)
    base_w, base_h = _base_region(src_w, src_h, aspect)
    portrait = aspect < (src_w / src_h) * 0.8
    intensity = max(0.0, opts.intensity)
    damp = 0.4 if portrait else 1.0
    z_lo = 1.0 + 0.6 * intensity * damp
    z_hi = 1.0 + 1.2 * intensity * damp
    ordered = sorted(events, key=lambda e: e.t)

    # 1. Clusters of nearby focus events (one zoom target each).
    clusters: list[dict[str, Any]] = []
    for event in ordered:
        if event.type in RESET_TYPES:
            if clusters:
                clusters[-1]["closed"] = True
                clusters[-1]["reset"] = event.t
            continue
        if event.type not in FOCUS_TYPES:
            continue
        css = event.rect()
        if css is None:
            continue
        rect = (css[0] * scale_x, css[1] * scale_y, css[2] * scale_x, css[3] * scale_y)
        cur = clusters[-1] if clusters else None
        if cur is not None and not cur.get("closed") and event.t - cur["t1"] <= opts.merge_gap:
            ux0 = min(cur["rect"][0], rect[0])
            uy0 = min(cur["rect"][1], rect[1])
            ux1 = max(cur["rect"][0] + cur["rect"][2], rect[0] + rect[2])
            uy1 = max(cur["rect"][1] + cur["rect"][3], rect[1] + rect[3])
            if (ux1 - ux0) <= base_w / z_lo * 0.9 and (uy1 - uy0) <= base_h / z_lo * 0.9:
                cur["rect"] = (ux0, uy0, ux1 - ux0, uy1 - uy0)
                cur["t1"] = event.t
                continue
        clusters.append({"t0": event.t, "t1": event.t, "rect": rect})

    # 2. Focus windows on the timeline.
    windows: list[list[Any]] = []
    for cl in clusters:
        rx, ry, rw, rh = cl["rect"]
        if intensity <= 0:
            z = 1.0
        else:
            z = min(base_w * opts.fill / max(rw, 1.0), base_h * opts.fill / max(rh, 1.0))
            z = min(max(z, z_lo), z_hi)
        w, h = base_w / z, base_h / z
        cx, cy = _clamp_center(rx + rw / 2.0, ry + rh / 2.0, w, h, src_w, src_h)
        start = max(0.0, cl["t0"] - opts.lead)
        end = cl["t1"] + opts.hold
        if cl.get("reset") is not None:
            end = min(end, cl["reset"])
        windows.append([start, end, (cx, cy, z)])
    for i in range(len(windows) - 1):
        if windows[i + 1][0] - windows[i][1] < opts.bridge_gap:
            windows[i][1] = windows[i + 1][0]

    def idle_target(last_cx: float | None) -> tuple[float, float, float]:
        if portrait and last_cx is not None:
            cx, cy = _clamp_center(last_cx, src_h / 2.0, base_w, base_h, src_w, src_h)
            return cx, cy, 1.0
        return src_w / 2.0, src_h / 2.0, 1.0

    # 3. Spring-follow the step targets (exact critically damped update).
    omega = 4.74 / max(0.1, opts.settle)
    dt = 1.0 / fps
    decay = math.exp(-omega * dt)
    targets: list[tuple[float, float, float]] = []
    last_cx: float | None = None
    wi = 0
    for i in range(max(1, n_frames)):
        t = i * dt
        while wi < len(windows) and windows[wi][1] <= t:
            last_cx = windows[wi][2][0]
            wi += 1
        if wi < len(windows) and windows[wi][0] <= t < windows[wi][1]:
            targets.append(windows[wi][2])
        else:
            targets.append(idle_target(last_cx))

    state = [targets[0][0], targets[0][1], math.log(targets[0][2])]
    vel = [0.0, 0.0, 0.0]
    cxs: list[float] = []
    cys: list[float] = []
    zs: list[float] = []
    for target in targets:
        goal = (target[0], target[1], math.log(target[2]))
        for k in range(3):
            d = state[k] - goal[k]
            v = vel[k]
            c = v + omega * d
            state[k] = goal[k] + (d + c * dt) * decay
            vel[k] = (v - omega * c * dt) * decay
        z = min(max(math.exp(state[2]), 1.0), max(1.0, z_hi))
        cx, cy = _clamp_center(state[0], state[1], base_w / z, base_h / z, src_w, src_h)
        cxs.append(cx)
        cys.append(cy)
        zs.append(z)
    return CameraPath(
        fps=fps, src_w=src_w, src_h=src_h, base_w=base_w, base_h=base_h, cx=cxs, cy=cys, zoom=zs
    )


# --------------------------------------------------------------------------
# Cursor
# --------------------------------------------------------------------------


@dataclass
class CursorTrack:
    x: list[float]
    y: list[float]
    scale: list[float]
    ripples: list[tuple[float, ...]]


_CLICK_PRESS = 0.06
_CLICK_RELEASE = 0.22
_RIPPLE = 0.55


def _cursor_keys(
    events: Iterable[ActionEvent], scale_x: float, scale_y: float, diag: float
) -> list[tuple[float, float, float, bool]]:
    keys: list[tuple[float, float, float, bool]] = []  # t, x, y, rest
    for event in sorted(events, key=lambda e: e.t):
        if event.type not in CURSOR_TYPES:
            continue
        pt = event.point()
        if pt is None:
            continue
        x, y = pt[0] * scale_x, pt[1] * scale_y
        samples = [(t, sx * scale_x, sy * scale_y) for t, sx, sy in event.path if t < event.t]
        if keys:
            samples = [s for s in samples if s[0] > keys[-1][0] + 1e-3]
        if samples:
            if not keys:
                keys.append((samples[0][0], samples[0][1], samples[0][2], True))
                samples = samples[1:]
            keys.extend((t, sx, sy, False) for t, sx, sy in samples)
            keys.append((max(event.t, keys[-1][0] + 1e-3), x, y, True))
            continue
        if not keys:
            keys.append((0.0, x, y, True))
            if event.t > 0:
                keys.append((event.t, x, y, True))
            continue
        pt_, px, py, _ = keys[-1]
        dist = math.hypot(x - px, y - py)
        if dist < 0.5:
            continue
        travel = min(0.85, max(0.3, 0.3 + 0.45 * dist / max(1.0, diag)))
        arrive = event.t - (0.08 if event.type == "click" else 0.0)
        arrive = max(arrive, pt_ + 0.05)
        depart = max(pt_, arrive - travel)
        if depart > pt_ + 1e-3:
            keys.append((depart, px, py, True))
        keys.append((arrive, x, y, True))
    return keys


def _eval_keys(keys: list[tuple[float, float, float, bool]], t: float) -> tuple[float, float]:
    if t <= keys[0][0]:
        return keys[0][1], keys[0][2]
    if t >= keys[-1][0]:
        return keys[-1][1], keys[-1][2]
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    t0, x0, y0, r0 = keys[lo]
    t1, x1, y1, r1 = keys[hi]
    span = max(1e-6, t1 - t0)
    u = (t - t0) / span

    def tangent(i: int) -> tuple[float, float]:
        if keys[i][3]:
            return 0.0, 0.0
        a = keys[max(0, i - 1)]
        b = keys[min(len(keys) - 1, i + 1)]
        dt = max(1e-6, b[0] - a[0])
        return (b[1] - a[1]) / dt, (b[2] - a[2]) / dt

    m0x, m0y = tangent(lo)
    m1x, m1y = tangent(hi)
    h00 = 2 * u**3 - 3 * u**2 + 1
    h10 = u**3 - 2 * u**2 + u
    h01 = -2 * u**3 + 3 * u**2
    h11 = u**3 - u**2
    x = h00 * x0 + h10 * span * m0x + h01 * x1 + h11 * span * m1x
    y = h00 * y0 + h10 * span * m0y + h01 * y1 + h11 * span * m1y
    if r0 and r1:
        # Gentle arc on point-to-point moves reads as a hand, not a robot.
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        if dist > 1.0:
            nx, ny = -dy / dist, dx / dist
            if ny > 0:
                nx, ny = -nx, -ny
            bend = 0.06 * dist * math.sin(math.pi * u)
            x += nx * bend
            y += ny * bend
    return x, y


def plan_cursor(
    events: Iterable[ActionEvent],
    *,
    scale_x: float,
    scale_y: float,
    src_w: int,
    src_h: int,
    fps: float,
    n_frames: int,
) -> CursorTrack | None:
    events = list(events)
    keys = _cursor_keys(events, scale_x, scale_y, math.hypot(src_w, src_h))
    if not keys:
        return None
    clicks = sorted(e.t for e in events if e.type == "click" and e.point() is not None)
    xs: list[float] = []
    ys: list[float] = []
    scales: list[float] = []
    ripples: list[tuple[float, ...]] = []
    for i in range(max(1, n_frames)):
        t = i / fps
        x, y = _eval_keys(keys, t)
        xs.append(min(max(x, 0.0), src_w - 1.0))
        ys.append(min(max(y, 0.0), src_h - 1.0))
        press = 0.0
        active: list[float] = []
        for tc in clicks:
            d = t - tc
            if -_CLICK_PRESS <= d < 0:
                u = (d + _CLICK_PRESS) / _CLICK_PRESS
                press = max(press, u * u * (3 - 2 * u))
            elif 0 <= d < _CLICK_RELEASE:
                u = d / _CLICK_RELEASE
                press = max(press, 1 - u * u * (3 - 2 * u))
            if 0 <= d < _RIPPLE:
                active.append(round(d / _RIPPLE, 3))
        scales.append(round(1.0 - 0.16 * press, 3))
        ripples.append(tuple(active))
    return CursorTrack(x=xs, y=ys, scale=scales, ripples=ripples)


def _delta_expr(values: list[int], fps: float) -> str:
    """Frame-exact piecewise-constant expression over ``t`` (delta encoded)."""
    if not values:
        return "0"
    parts = [str(values[0])]
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        if delta:
            parts.append(f"gte(t,{(i - 0.5) / fps:.6f})*({delta})")
    return "+".join(parts)


# --------------------------------------------------------------------------
# Plan + render
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FootageOptions:
    intensity: float = 1.0
    trim: bool = True
    trim_threshold: float = 1.2
    trim_speed: float = 6.0
    trim_mode: str = "speed"  # speed | cut
    cursor: str = "auto"  # auto | light | dark | none
    cursor_size: float = 22.0  # css px, arrow height
    motion_blur: bool = False
    accent: str = "#4F8CFF"
    background: str = "#0B1020"
    layout: str = "crop"  # crop | framed
    title: str = ""
    captions: str = ""  # srt path, or "actions" for labels
    crf: int = 16
    preset: str = "slow"
    fps: float | None = None
    camera: CameraOptions = field(default_factory=CameraOptions)
    keep_temp: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "FootageOptions":
        data = dict(data or {})
        opts = cls()
        kwargs: dict[str, Any] = {}
        if "intensity" in data and data["intensity"] is not None:
            kwargs["intensity"] = resolve_intensity(data["intensity"])
        for key in ("trim", "motion_blur", "keep_temp"):
            if key in data and data[key] is not None:
                kwargs[key] = _truthy(data[key])
        for key in ("trim_threshold", "trim_speed", "cursor_size", "fps"):
            num = _num(data.get(key))
            if num is not None and num > 0:
                kwargs[key] = num
        if _num(data.get("crf")) is not None:
            kwargs["crf"] = int(min(max(_num(data["crf"]) or 16, 0), 40))
        for key in (
            "trim_mode",
            "cursor",
            "accent",
            "background",
            "layout",
            "title",
            "captions",
            "preset",
        ):
            if data.get(key) not in (None, ""):
                kwargs[key] = str(data[key]).strip()
        if kwargs.get("trim_mode") not in (None, "speed", "cut"):
            kwargs["trim_mode"] = "speed"
        if kwargs.get("layout") not in (None, "crop", "framed"):
            kwargs["layout"] = "crop"
        if kwargs.get("cursor") not in (None, "auto", "light", "dark", "none"):
            kwargs["cursor"] = "auto"
        return replace(opts, **kwargs)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass
class FootagePlan:
    source: Path
    src_w: int
    src_h: int
    src_duration: float
    has_audio: bool
    fps: Fraction
    scale_x: float
    scale_y: float
    original: ActionsDoc
    doc: ActionsDoc
    remap: TimeRemap
    options: FootageOptions

    @property
    def duration(self) -> float:
        return self.doc.duration

    @property
    def n_frames(self) -> int:
        return max(1, int(round(self.duration * float(self.fps))))


def _probe(source: Path) -> dict[str, Any]:
    from navin.montage.detect import find_ffmpeg
    from navin.montage.probe import find_ffprobe

    ffprobe = find_ffprobe(find_ffmpeg())
    if not ffprobe:
        raise FootageError(
            "ffprobe not found - install ffmpeg (montage action=setup package=ffmpeg)"
        )
    completed = run_process_sync(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source),
        ],
        timeout_s=60,
    )
    if not completed.ok:
        raise FootageError(f"could not read the clip: {completed.stderr.strip()[:200]}")
    data = json.loads(completed.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise FootageError("clip has no video stream")
    rate = video.get("r_frame_rate") or video.get("avg_frame_rate") or "30/1"
    try:
        fps = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        fps = Fraction(30)
    if fps <= 0 or fps > 240:
        fps = Fraction(30)
    duration = (
        _num((data.get("format") or {}).get("duration")) or _num(video.get("duration")) or 0.0
    )
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps.limit_denominator(1001),
        "duration": duration,
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def prepare_footage(
    source: str | Path,
    actions: str | Path | ActionsDoc | None = None,
    options: FootageOptions | None = None,
) -> FootagePlan:
    """Probe, load actions, detect dead time and remap timestamps."""
    opts = options or FootageOptions()
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise FootageError(f"clip not found: {source}")
    info = _probe(src)
    if info["width"] <= 0 or info["height"] <= 0:
        raise FootageError("could not read the clip size")
    if isinstance(actions, ActionsDoc):
        doc = actions
    else:
        sidecar = Path(actions).expanduser() if actions else find_actions_sidecar(src)
        if sidecar is not None and actions and not sidecar.is_file():
            raise FootageError(f"actions file not found: {actions}")
        doc = load_actions(sidecar) if sidecar is not None else ActionsDoc(0.0, 0.0)
    src_w, src_h = info["width"], info["height"]
    dsf = doc.device_scale_factor or 1.0
    scale_x = src_w / doc.viewport_width if doc.viewport_width > 0 else dsf
    scale_y = src_h / doc.viewport_height if doc.viewport_height > 0 else dsf
    duration = info["duration"] or doc.duration
    if duration <= 0:
        raise FootageError("clip duration is unknown")
    fps = Fraction(opts.fps).limit_denominator(1001) if opts.fps else info["fps"]
    remap = TimeRemap()
    if opts.trim:
        freezes = detect_freezes(src, min_duration=opts.trim_threshold, duration=duration)
        remap = plan_dead_segments(
            freezes,
            doc.events,
            threshold=opts.trim_threshold,
            speed=opts.trim_speed,
            mode=opts.trim_mode,
        )
    remapped = remap_actions(doc, remap, duration)
    return FootagePlan(
        source=src,
        src_w=src_w,
        src_h=src_h,
        src_duration=duration,
        has_audio=bool(info["has_audio"]),
        fps=fps,
        scale_x=scale_x,
        scale_y=scale_y,
        original=doc,
        doc=remapped,
        remap=remap,
        options=opts,
    )


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _auto_cursor_theme(source: Path, at: float, tmp: Path) -> str:
    from PIL import Image, ImageStat

    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    probe_png = tmp / "luma.png"
    if ffmpeg:
        run_process_sync(
            [
                ffmpeg,
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-ss",
                f"{max(0.0, at):.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=64:-2",
                str(probe_png),
            ],
            timeout_s=60,
        )
    if not probe_png.is_file():
        return "light"
    with Image.open(probe_png) as img:
        mean = ImageStat.Stat(img.convert("L")).mean[0]
    return "dark" if mean > 150 else "light"


_FFMPEG_MAJOR: int | None = None


def _ffmpeg_major(ffmpeg: str) -> int:
    global _FFMPEG_MAJOR
    if _FFMPEG_MAJOR is None:
        completed = run_process_sync([ffmpeg, "-hide_banner", "-version"], timeout_s=20)
        text = completed.stdout or ""
        match = re.search(r"ffmpeg version n?(\d+)\.", text)
        if match:
            _FFMPEG_MAJOR = int(match.group(1))
        else:
            # Git snapshots ("version N-1234-g...") are newer than any release.
            _FFMPEG_MAJOR = 99 if re.search(r"ffmpeg version N-", text) else 6
    return _FFMPEG_MAJOR


def _graph_args(ffmpeg: str, script: Path) -> list[str]:
    if _ffmpeg_major(ffmpeg) >= 7:
        return ["-/filter_complex", str(script)]
    return ["-filter_complex_script", str(script)]


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def _log_tail(path: Path, limit: int = 400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:]


def _pump_camera(
    dec_argv: list[str],
    enc_argv: list[str],
    camera: CameraPath,
    *,
    out_w: int,
    out_h: int,
    n_frames: int,
    timeout_s: float,
    logdir: Path,
) -> None:
    """Decoder -> sub-pixel Lanczos camera crop (Pillow) -> encoder.

    ``Image.resize(box=...)`` takes a float source rectangle, so slow zooms and
    pans move by fractions of a pixel instead of snapping like ``crop`` /
    ``zoompan``. Pillow releases the GIL while resampling, so a small thread
    pool keeps every core busy.
    """
    import os
    import subprocess
    import time
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image

    from navin.utils.proc import no_window_kwargs

    src_size = (camera.src_w, camera.src_h)
    frame_bytes = camera.src_w * camera.src_h * 3
    dec_log_path, enc_log_path = logdir / "decode.log", logdir / "encode.log"
    deadline = time.monotonic() + timeout_s
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))

    def warp(buf: bytearray, i: int) -> bytes:
        img = Image.frombuffer("RGB", src_size, buf, "raw", "RGB", 0, 1)
        return img.resize((out_w, out_h), Image.Resampling.LANCZOS, box=camera.box(i)).tobytes()

    with open(dec_log_path, "wb") as dec_log, open(enc_log_path, "wb") as enc_log:
        dec = subprocess.Popen(
            dec_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=dec_log,
            **no_window_kwargs(),
        )
        try:
            enc = subprocess.Popen(
                enc_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=enc_log,
                **no_window_kwargs(),
            )
        except OSError:
            dec.kill()
            raise
        written = 0
        last: bytes | None = None
        try:
            assert dec.stdout is not None and enc.stdin is not None
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending: deque[Any] = deque()

                def flush_one() -> None:
                    nonlocal written, last
                    data = pending.popleft().result()
                    enc.stdin.write(data)  # type: ignore[union-attr]
                    last = data
                    written += 1

                for i in range(n_frames):
                    if time.monotonic() > deadline:
                        raise FootageError("render timed out")
                    buf = bytearray(frame_bytes)
                    view = memoryview(buf)
                    got = 0
                    while got < frame_bytes:
                        n = dec.stdout.readinto(view[got:])
                        if not n:
                            break
                        got += n
                    if got < frame_bytes:
                        break
                    pending.append(pool.submit(warp, buf, i))
                    while len(pending) > workers * 2:
                        flush_one()
                while pending:
                    flush_one()
            if last is None:
                dec.wait(timeout=30)
                raise FootageError(
                    f"decoder produced no frames: {_log_tail(dec_log_path) or 'ffmpeg error'}"
                )
            while written < n_frames:
                enc.stdin.write(last)
                written += 1
            enc.stdin.close()
            remaining = max(60.0, deadline - time.monotonic())
            enc_rc = enc.wait(timeout=remaining)
            dec.stdout.close()
            try:
                dec.wait(timeout=30)
            except subprocess.TimeoutExpired:
                dec.kill()
            if enc_rc != 0:
                raise FootageError(f"encode failed: {_log_tail(enc_log_path) or 'ffmpeg error'}")
        except (BrokenPipeError, OSError) as exc:
            raise FootageError(
                f"encode failed: {_log_tail(enc_log_path) or _log_tail(dec_log_path) or exc}"
            ) from exc
        finally:
            for proc in (dec, enc):
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass


def render_footage(
    plan: FootagePlan,
    dest: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    layout: str | None = None,
    title: str | None = None,
    captions: str | None = None,
    subtitles: str | Path | None = None,
) -> dict[str, Any]:
    """Encode one deliverable from *plan* at ``width x height``.

    ``layout="crop"`` follows the camera for any aspect ratio (smart crop);
    ``layout="framed"`` fits the 16:9 footage inside a branded background.
    ``subtitles`` burns an SRT written against the source timeline (it is
    remapped through the dead-time trim here).
    """
    from navin.montage import footage_art as art
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FootageError("ffmpeg not found - install it with montage action=setup package=ffmpeg")
    opts = plan.options
    layout = (layout or opts.layout or "crop").strip().lower()
    title = opts.title if title is None else title
    captions = opts.captions if captions is None else captions
    out_w, out_h = _even(width), _even(height)
    fps = plan.fps
    fps_f = float(fps)
    fps_s = f"{fps.numerator}/{fps.denominator}"
    n_frames = plan.n_frames
    dest = Path(dest).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    inner_x = inner_y = 0
    if layout == "framed":
        src_aspect = plan.src_w / plan.src_h
        margin = round(out_w * 0.045)
        inner_w = _even(out_w - 2 * margin)
        inner_h = _even(inner_w / src_aspect)
        if inner_h > out_h * 0.62:
            inner_h = _even(out_h * 0.62)
            inner_w = _even(inner_h * src_aspect)
        inner_x = (out_w - inner_w) // 2
        center_y = 0.47 if out_h > out_w else 0.53
        inner_y = _even(out_h * center_y - inner_h / 2)
        cam_w, cam_h = inner_w, inner_h
        cam_aspect = src_aspect
    else:
        cam_w, cam_h = out_w, out_h
        cam_aspect = out_w / out_h

    camera = plan_camera(
        plan.doc.events,
        src_w=plan.src_w,
        src_h=plan.src_h,
        scale_x=plan.scale_x,
        scale_y=plan.scale_y,
        fps=fps_f,
        n_frames=n_frames,
        aspect=cam_aspect,
        options=replace(opts.camera, intensity=opts.intensity),
    )

    tmp = Path(tempfile.mkdtemp(prefix="navin-footage-"))
    try:
        # ---- decoder: time warp, CFR, cursor overlay in source pixels ----
        dec_inputs: list[str] = ["-i", str(plan.source)]
        pre = "[0:v]setpts=PTS-STARTPTS"
        if plan.remap.segments:
            pre += f",setpts='({plan.remap.setpts_expr()})/TB'"
        pre += f",fps={fps_s}"
        theme = (opts.cursor or "auto").lower()
        cursor = None
        if theme != "none":
            cursor = plan_cursor(
                plan.doc.events,
                scale_x=plan.scale_x,
                scale_y=plan.scale_y,
                src_w=plan.src_w,
                src_h=plan.src_h,
                fps=fps_f,
                n_frames=n_frames,
            )
        dec_graph: list[str] = []
        # Full chroma interpolation keeps colored UI text crisp; accurate_rnd
        # costs 3x at 4K for no visible gain here.
        to_rgb = "scale=flags=full_chroma_int,format=rgb24"
        if cursor is not None:
            if theme == "auto":
                theme = _auto_cursor_theme(plan.source, plan.src_duration / 2.0, tmp)
            accent = art.parse_color(opts.accent, (79, 140, 255))
            spec = art.sprite_spec(
                round(opts.cursor_size * 1.45 * plan.scale_y), theme=theme, accent=accent
            )
            sprite_dir = tmp / "cursor"
            sprite_dir.mkdir()
            cache: dict[tuple[float, tuple[float, ...]], bytes] = {}
            for i in range(n_frames):
                key = (cursor.scale[i], cursor.ripples[i])
                blob = cache.get(key)
                if blob is None:
                    buf = io.BytesIO()
                    art.render_sprite(spec, key[0], key[1]).save(
                        buf, format="PNG", compress_level=1
                    )
                    blob = buf.getvalue()
                    cache[key] = blob
                (sprite_dir / f"{i:06d}.png").write_bytes(blob)
            half = spec.size // 2
            xs = [int(round(v)) - half for v in cursor.x]
            ys = [int(round(v)) - half for v in cursor.y]
            dec_inputs += [
                "-framerate",
                fps_s,
                "-start_number",
                "0",
                "-i",
                str(sprite_dir / "%06d.png"),
            ]
            # Overlay in RGB: exact pixel positions (no chroma rounding) and the
            # conversion happens once, before the sprite is blended.
            dec_graph.append(pre + f",{to_rgb}[base]")
            dec_graph.append("[1:v]format=rgba,setpts=PTS-STARTPTS[spr]")
            dec_graph.append(
                f"[base][spr]overlay=x='{_delta_expr(xs, fps_f)}':y='{_delta_expr(ys, fps_f)}'"
                ":format=rgb:eof_action=pass,format=rgb24[d]"
            )
        else:
            dec_graph.append(pre + f",{to_rgb}[d]")
        dec_script = tmp / "decode.txt"
        dec_script.write_text(";\n".join(dec_graph), encoding="utf-8")
        dec_argv = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *dec_inputs,
            *_graph_args(ffmpeg, dec_script),
            "-map",
            "[d]",
            "-frames:v",
            str(n_frames),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]

        # ---- encoder: blur, layout, captions, final pixel format ----
        enc_inputs: list[str] = [
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{cam_w}x{cam_h}",
            "-framerate",
            fps_s,
            "-i",
            "pipe:0",
        ]
        n_inputs = 1
        fg = "[0:v]setsar=1"
        blur_spans: list[tuple[float, float]] = []
        if opts.motion_blur:
            blur_spans = camera.fast_intervals(cam_w)
            if blur_spans:
                enable = "+".join(f"between(t,{a:.4f},{b:.4f})" for a, b in blur_spans)
                fg += f",tmix=frames=3:weights='1 2 1':enable='{enable}'"

        srt_text = ""
        if subtitles:
            srt_path = Path(subtitles).expanduser()
            if srt_path.is_file():
                srt_text = remap_srt(
                    srt_path.read_text(encoding="utf-8", errors="replace"), plan.remap
                )
        to_yuv = (
            "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,"
            "format=yuv420p,setsar=1"
        )
        enc_graph: list[str] = []
        if layout == "framed":
            caption_cues: list[tuple[float, float, str]] = []
            cap_key = (captions or "").strip()
            if cap_key.lower() == "actions":
                caption_cues = captions_from_actions(plan.doc)
            elif cap_key and Path(cap_key).expanduser().is_file():
                caption_cues = parse_srt(
                    remap_srt(
                        Path(cap_key).expanduser().read_text(encoding="utf-8", errors="replace"),
                        plan.remap,
                    )
                )
            elif srt_text:
                caption_cues = parse_srt(srt_text)
            bg_rgb = art.parse_color(opts.background, (11, 16, 32))
            accent = art.parse_color(opts.accent, (79, 140, 255))
            overlay_png = tmp / "frame.png"
            art.render_framed_overlay(
                out_w,
                out_h,
                (inner_x, inner_y, cam_w, cam_h),
                title=title or "",
                background=bg_rgb,
                accent=accent,
            ).save(overlay_png)
            enc_inputs += ["-loop", "1", "-framerate", fps_s, "-i", str(overlay_png)]
            mask_idx = n_inputs
            n_inputs += 1
            enc_graph.append(fg + "[fg]")
            enc_graph.append(
                "color=c=0x%02x%02x%02x:s=%dx%d:r=%s[bgc]" % (*bg_rgb, out_w, out_h, fps_s)
            )
            enc_graph.append(f"[bgc][fg]overlay=x={inner_x}:y={inner_y}:eof_action=pass[l1]")
            enc_graph.append(f"[{mask_idx}:v]format=rgba[mask]")
            enc_graph.append("[l1][mask]overlay=0:0:eof_action=pass[l2]")
            last = "[l2]"
            if caption_cues:
                band_y = inner_y + cam_h + round(out_h * 0.025)
                band_h = _even(max(64, out_h - band_y - round(out_h * 0.06)))
                cap_dir = tmp / "captions"
                cap_dir.mkdir()
                blobs: dict[int, bytes] = {}
                for i in range(n_frames):
                    t = i / fps_f
                    cue = next((k for k, c in enumerate(caption_cues) if c[0] <= t < c[1]), -1)
                    blob = blobs.get(cue)
                    if blob is None:
                        buf = io.BytesIO()
                        text = caption_cues[cue][2] if cue >= 0 else ""
                        art.render_caption(text, out_w, band_h).save(
                            buf, format="PNG", compress_level=1
                        )
                        blob = buf.getvalue()
                        blobs[cue] = blob
                    (cap_dir / f"{i:06d}.png").write_bytes(blob)
                enc_inputs += [
                    "-framerate",
                    fps_s,
                    "-start_number",
                    "0",
                    "-i",
                    str(cap_dir / "%06d.png"),
                ]
                cap_idx = n_inputs
                n_inputs += 1
                enc_graph.append(f"[{cap_idx}:v]format=rgba,setpts=PTS-STARTPTS[cap]")
                enc_graph.append(f"{last}[cap]overlay=0:{band_y}:eof_action=pass[l3]")
                last = "[l3]"
            enc_graph.append(f"{last}{to_yuv}[v]")
        else:
            chain = fg
            if srt_text:
                srt_tmp = tmp / "captions.srt"
                srt_tmp.write_text(srt_text, encoding="utf-8")
                chain += f",subtitles='{_escape_filter_path(srt_tmp)}'"
            enc_graph.append(f"{chain},{to_yuv}[v]")

        audio: list[str] = ["-an"]
        if plan.has_audio and not plan.remap.segments:
            enc_inputs += ["-i", str(plan.source)]
            audio = ["-map", f"{n_inputs}:a:0?", "-c:a", "aac", "-b:a", "192k"]
            n_inputs += 1
        enc_script = tmp / "encode.txt"
        enc_script.write_text(";\n".join(enc_graph), encoding="utf-8")
        enc_argv = [
            ffmpeg,
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *enc_inputs,
            *_graph_args(ffmpeg, enc_script),
            "-map",
            "[v]",
            *audio,
            "-frames:v",
            str(n_frames),
            "-c:v",
            "libx264",
            "-preset",
            opts.preset or "slow",
            "-crf",
            str(opts.crf),
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-r",
            fps_s,
            "-fps_mode",
            "cfr",
            "-movflags",
            "+faststart",
            str(dest),
        ]
        _pump_camera(
            dec_argv,
            enc_argv,
            camera,
            out_w=cam_w,
            out_h=cam_h,
            n_frames=n_frames,
            timeout_s=max(900.0, plan.src_duration * 60),
            logdir=tmp,
        )
        if not dest.is_file():
            raise FootageError("render failed: no output written")

        event_frames: list[dict[str, Any]] = []
        for event in plan.doc.events:
            pt = event.point()
            if pt is None:
                continue
            i = min(n_frames - 1, max(0, int(round(event.t * fps_f))))
            ox, oy = camera.to_output(i, pt[0] * plan.scale_x, pt[1] * plan.scale_y, cam_w, cam_h)
            event_frames.append(
                {
                    "t": round(event.t, 4),
                    "type": event.type,
                    "frame": i,
                    "x": round(ox + inner_x, 1),
                    "y": round(oy + inner_y, 1),
                }
            )
        steps = camera.max_step()
        return {
            "ok": True,
            "output": str(dest),
            "width": out_w,
            "height": out_h,
            "fps": fps_f,
            "frames": n_frames,
            "duration": round(n_frames / fps_f, 4),
            "layout": layout,
            "cursor": theme if cursor is not None else "none",
            "motion_blur_spans": [[round(a, 3), round(b, 3)] for a, b in blur_spans],
            "camera": {
                "max_zoom": round(max(camera.zoom), 3),
                "max_center_step_px": round(steps["center_px"], 2),
                "max_log_zoom_step": round(steps["log_zoom"], 4),
            },
            "event_frames": event_frames,
        }
    finally:
        if not opts.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


def remapped_actions_payload(
    plan: FootagePlan, render: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = plan.doc.to_dict()
    payload["fps"] = float(plan.fps)
    payload["duration"] = round(plan.n_frames / float(plan.fps), 4)
    polish: dict[str, Any] = {
        "source": str(plan.source),
        "source_duration": round(plan.src_duration, 4),
        "coordinates": "css px of the source viewport",
        "dead_time": plan.remap.to_dict(),
        "options": {
            k: v
            for k, v in asdict(plan.options).items()
            if k
            in {
                "intensity",
                "trim",
                "trim_threshold",
                "trim_speed",
                "trim_mode",
                "cursor",
                "layout",
            }
        },
    }
    if render:
        polish["output"] = render.get("output")
        polish["output_size"] = [render.get("width"), render.get("height")]
        polish["event_frames"] = render.get("event_frames", [])
    payload["polish"] = polish
    return payload


def polish_footage(
    source: str | Path,
    actions: str | Path | None = None,
    output: str | Path | None = None,
    *,
    options: FootageOptions | None = None,
    width: int = 1920,
    height: int = 1080,
    subtitles: str | Path | None = None,
) -> dict[str, Any]:
    """Polish one clip: trim, camera, cursor -> MP4 + remapped actions.json."""
    plan = prepare_footage(source, actions, options)
    dest = (
        Path(output).expanduser()
        if output
        else plan.source.with_name(f"{plan.source.stem}-polished.mp4")
    )
    result = render_footage(plan, dest, width=width, height=height, subtitles=subtitles)
    actions_out = Path(result["output"]).with_name(f"{Path(result['output']).stem}.actions.json")
    actions_out.write_text(
        json.dumps(remapped_actions_payload(plan, result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    result["actions"] = str(actions_out)
    result["source_duration"] = round(plan.src_duration, 4)
    result["dead_time"] = plan.remap.to_dict()
    result["events"] = len(plan.doc.events)
    return result
