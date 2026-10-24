# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Video QA for Marketing demo videos.

Two layers, mirroring the still-image gate in ``visual_qa``:

* objective checks measured with ffprobe / ffmpeg filters (resolution, fps,
  duration, blackdetect, freezedetect, cropdetect for bars, silencedetect,
  ebur128 loudness), and
* a vision rubric over frames sampled at every scene boundary and every N
  seconds, plus a contact sheet (composition, text legibility at mobile size,
  brand consistency, black bars, visible secrets, spinners / blank frames,
  pacing).

Every finding carries the scene index and timestamp it applies to, which is
what lets the demo-video pipeline re-render only the scenes that failed.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from navin.utils.atomic_io import atomic_write_text

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - readiness reports a missing Pillow
    Image = ImageDraw = None  # type: ignore[assignment]

_STATUS_RANK = {"PASS": 0, "WARN": 1, "BLOCK": 2}
_FFMPEG_TIMEOUT_S = 600.0


@dataclass(frozen=True, slots=True)
class VideoQAPolicy:
    """Thresholds for the objective checks and the sampling plan."""

    pass_score: float = 85.0
    warn_score: float = 65.0
    sample_every_s: float = 2.0
    max_vision_frames: int = 16
    frame_long_edge: int = 640
    black_min_s: float = 0.25
    black_pixel_threshold: float = 0.10
    freeze_min_s: float = 1.5
    freeze_noise_db: float = -60.0
    silence_min_s: float = 1.5
    silence_noise_db: float = -50.0
    target_lufs: float = -14.0
    lufs_tolerance: float = 2.0
    max_true_peak_db: float = -1.0
    bar_tolerance: float = 0.02
    fps_tolerance: float = 0.05
    duration_tolerance_s: float = 0.5
    deterministic_weight: float = 0.5
    vision_weight: float = 0.5
    block_on_vision_failure: bool = False


def finding(
    check: str,
    status: str,
    score: float,
    evidence: list[str],
    recommendation: str = "",
    *,
    source: str = "objective",
    scene_index: int | None = None,
    timestamp: float | None = None,
    end: float | None = None,
) -> dict[str, Any]:
    return {
        "check": check,
        "source": source,
        "status": status,
        "score": round(max(0.0, min(100.0, float(score))), 2),
        "evidence": evidence,
        "recommendation": recommendation,
        "scene_index": scene_index,
        "timestamp": round(timestamp, 3) if timestamp is not None else None,
        "end": round(end, 3) if end is not None else None,
    }


# ---------------------------------------------------------------------------
# Scene map
# ---------------------------------------------------------------------------


def scene_starts(scenes: list[dict[str, Any]] | None) -> list[float]:
    """Start time of each storyboard scene from its ``start`` or the running sum."""
    starts: list[float] = []
    cursor = 0.0
    for scene in scenes or []:
        start = scene.get("start")
        if isinstance(start, int | float) and not isinstance(start, bool):
            cursor = float(start)
        starts.append(round(cursor, 3))
        cursor += float(scene.get("duration") or 0)
    return starts


def scene_at(starts: list[float], timestamp: float | None, *, tolerance: float = 0.1) -> int | None:
    """Scene holding *timestamp*.

    Detectors report an event from the last frame before it shows (a freeze
    starts on the final moving frame), so an event a few frames before a cut
    belongs to the scene after it.
    """
    if timestamp is None or not starts:
        return None
    index = 0
    for position, start in enumerate(starts):
        if timestamp + tolerance >= start:
            index = position
    return index


def sample_timestamps(
    duration: float,
    starts: list[float],
    *,
    every_s: float,
) -> list[float]:
    """Scene boundary frames first, then a frame every ``every_s`` seconds."""
    if duration <= 0:
        return []
    last = max(0.0, duration - 0.05)
    wanted: list[float] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else duration
        # Just after the cut: the first frame viewers see of the new scene.
        wanted.append(min(last, start + min(0.25, max(0.0, (end - start) / 4))))
    step = max(0.25, float(every_s))
    cursor = step / 2
    while cursor < duration:
        wanted.append(min(last, cursor))
        cursor += step
    out: list[float] = []
    for value in sorted(wanted):
        if not out or value - out[-1] >= 0.3:
            out.append(round(value, 3))
    return out


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _tools() -> tuple[str | None, str | None]:
    from navin.montage.assemble import find_ffprobe
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    return ffmpeg, find_ffprobe(ffmpeg)


async def _run(argv: list[str]) -> Any:
    from navin.montage.ffmpeg_runner import run_process

    return await run_process(argv, timeout_s=_FFMPEG_TIMEOUT_S)


def _fraction(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            return float(num) / float(den) if float(den) else None
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


async def probe_video(path: Path, ffprobe: str) -> dict[str, Any]:
    result = await _run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    if not result.ok:
        raise RuntimeError(f"ffprobe could not read {path.name}")
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = _fraction((payload.get("format") or {}).get("duration")) or _fraction(video.get("duration"))
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")),
        "duration": duration or 0.0,
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
    }


_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")
_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
_LUFS = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.M)
_LRA = re.compile(r"^\s*LRA:\s*(-?[\d.]+)\s*LU", re.M)
_PEAK = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.M)


def parse_filter_log(stderr: str, *, duration: float) -> dict[str, Any]:
    """Pull detector events out of one ffmpeg analysis pass."""
    black = [
        {"start": float(a), "end": float(b), "duration": float(c)}
        for a, b, c in _BLACK.findall(stderr)
    ]
    freezes: list[dict[str, float]] = []
    starts = [float(value) for value in _FREEZE_START.findall(stderr)]
    ends = [float(value) for value in _FREEZE_END.findall(stderr)]
    for position, start in enumerate(starts):
        end = ends[position] if position < len(ends) else duration
        freezes.append({"start": start, "end": end, "duration": max(0.0, end - start)})
    silences: list[dict[str, float]] = []
    s_starts = [max(0.0, float(value)) for value in _SILENCE_START.findall(stderr)]
    s_ends = [float(value) for value in _SILENCE_END.findall(stderr)]
    for position, start in enumerate(s_starts):
        end = s_ends[position] if position < len(s_ends) else duration
        silences.append({"start": start, "end": end, "duration": max(0.0, end - start)})
    crops = Counter(tuple(int(part) for part in match) for match in _CROP.findall(stderr))
    crop = list(crops.most_common(1)[0][0]) if crops else None
    summary = stderr[stderr.rfind("Summary:") :] if "Summary:" in stderr else ""

    def _level(pattern: re.Pattern[str]) -> float | None:
        match = pattern.search(summary)
        if not match:
            return None
        raw = match.group(1)
        return float("-inf") if raw == "-inf" else float(raw)

    return {
        "black": black,
        "freezes": freezes,
        "silences": silences,
        "crop": crop,
        "loudness": {"integrated": _level(_LUFS), "lra": _level(_LRA), "true_peak": _level(_PEAK)},
    }


async def analyze_streams(
    path: Path,
    ffmpeg: str,
    *,
    duration: float,
    has_audio: bool,
    policy: VideoQAPolicy,
) -> dict[str, Any]:
    """One decode pass with every detector attached."""
    video_filters = (
        f"blackdetect=d={policy.black_min_s}:pix_th={policy.black_pixel_threshold},"
        f"freezedetect=n={policy.freeze_noise_db}dB:d={policy.freeze_min_s},"
        "cropdetect=limit=24:round=2:reset=0"
    )
    argv = [ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-vf", video_filters]
    if has_audio:
        argv += [
            "-af",
            f"silencedetect=n={policy.silence_noise_db}dB:d={policy.silence_min_s},ebur128=peak=true",
        ]
    argv += ["-f", "null", "-"]
    result = await _run(argv)
    if not result.ok and not result.raw_stderr:
        raise RuntimeError(f"ffmpeg could not analyze {path.name}")
    return parse_filter_log(result.raw_stderr or "", duration=duration)


# ---------------------------------------------------------------------------
# Objective findings
# ---------------------------------------------------------------------------


def _ratio(value: str | None) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", str(value or ""))
    if not match or float(match.group(2)) <= 0:
        return None
    return float(match.group(1)) / float(match.group(2))


def objective_findings(
    info: dict[str, Any],
    events: dict[str, Any],
    *,
    requirements: dict[str, Any] | None,
    starts: list[float],
    policy: VideoQAPolicy,
) -> list[dict[str, Any]]:
    req = dict(requirements or {})
    out: list[dict[str, Any]] = []
    width, height = int(info.get("width") or 0), int(info.get("height") or 0)
    exp_w, exp_h = req.get("width"), req.get("height")
    dims_ok = width > 0 and height > 0 and exp_w in (None, width) and exp_h in (None, height)
    ratio = _ratio(req.get("aspect_ratio") or req.get("format"))
    if ratio and height:
        dims_ok = dims_ok and abs((width / height) / ratio - 1) <= 0.02
    out.append(
        finding(
            "resolution",
            "PASS" if dims_ok else "BLOCK",
            100 if dims_ok else 0,
            [f"observed {width}x{height}; expected {exp_w or '*'}x{exp_h or '*'} {req.get('format') or ''}".strip()],
            "" if dims_ok else "Re-render at the deliverable canvas instead of scaling.",
        )
    )
    fps = info.get("fps")
    exp_fps = req.get("fps")
    fps_ok = bool(fps) and (exp_fps is None or abs(float(fps) - float(exp_fps)) <= policy.fps_tolerance * float(exp_fps))
    out.append(
        finding(
            "fps",
            "PASS" if fps_ok else "WARN",
            100 if fps_ok else 40,
            [f"observed {fps or 0:.3f} fps; expected {exp_fps or 'any'}"],
            "" if fps_ok else "Render every scene at the storyboard fps.",
        )
    )
    duration = float(info.get("duration") or 0)
    lo, hi = req.get("min_duration"), req.get("max_duration")
    exp_d = req.get("duration")
    dur_ok = duration > 0
    if exp_d is not None:
        dur_ok = dur_ok and abs(duration - float(exp_d)) <= max(policy.duration_tolerance_s, float(exp_d) * 0.05)
    if lo is not None:
        dur_ok = dur_ok and duration >= float(lo)
    if hi is not None:
        dur_ok = dur_ok and duration <= float(hi)
    out.append(
        finding(
            "duration",
            "PASS" if dur_ok else "WARN",
            100 if dur_ok else 50,
            [f"observed {duration:.2f}s; expected {exp_d or '*'}s within [{lo or 0}, {hi or 'inf'}]"],
            "" if dur_ok else "Adjust scene durations to the storyboard total.",
        )
    )
    for item in events.get("black") or []:
        # A fade through black at a cut is a transition; long black is a defect.
        long_black = item["duration"] >= 1.0 or item["start"] <= 0.05
        out.append(
            finding(
                "black_frames",
                "BLOCK" if long_black else "WARN",
                10 if long_black else 60,
                [f"black from {item['start']:.2f}s to {item['end']:.2f}s ({item['duration']:.2f}s)"],
                "Re-render this scene; never open or hold on a black frame.",
                scene_index=scene_at(starts, item["start"]),
                timestamp=item["start"],
                end=item["end"],
            )
        )
    for item in events.get("freezes") or []:
        out.append(
            finding(
                "frozen_frames",
                "BLOCK" if item["duration"] >= 3.0 else "WARN",
                20 if item["duration"] >= 3.0 else 60,
                [f"no motion from {item['start']:.2f}s to {item['end']:.2f}s ({item['duration']:.2f}s)"],
                "Add motion (zoom, pan, kinetic type) or shorten the hold.",
                scene_index=scene_at(starts, item["start"]),
                timestamp=item["start"],
                end=item["end"],
            )
        )
    crop = events.get("crop")
    if crop and width and height:
        cw, ch, cx, cy = crop
        bars = (width - cw) / width > policy.bar_tolerance or (height - ch) / height > policy.bar_tolerance
        out.append(
            finding(
                "black_bars",
                "WARN" if bars else "PASS",
                45 if bars else 100,
                [f"active picture {cw}x{ch} at {cx},{cy} inside {width}x{height}"],
                "Smart-crop or re-frame the source to fill the canvas." if bars else "",
            )
        )
    if not info.get("has_audio"):
        out.append(
            finding(
                "audio",
                "WARN",
                50,
                ["no audio stream"],
                "Add a music bed and narration in the premium assemble step.",
            )
        )
    else:
        for item in events.get("silences") or []:
            whole = item["duration"] >= max(1.0, duration - 0.5)
            out.append(
                finding(
                    "silence",
                    "BLOCK" if whole else "WARN",
                    15 if whole else 65,
                    [f"silence from {item['start']:.2f}s to {item['end']:.2f}s ({item['duration']:.2f}s)"],
                    "Extend the music bed or narration over this gap.",
                    scene_index=None if whole else scene_at(starts, item["start"]),
                    timestamp=item["start"],
                    end=item["end"],
                )
            )
        loud = events.get("loudness") or {}
        integrated, peak = loud.get("integrated"), loud.get("true_peak")
        if integrated is not None:
            off = abs(integrated - policy.target_lufs) if integrated != float("-inf") else 99.0
            loud_ok = off <= policy.lufs_tolerance
            out.append(
                finding(
                    "loudness",
                    "PASS" if loud_ok else "WARN",
                    100 if loud_ok else max(20, 100 - off * 8),
                    [f"integrated {integrated} LUFS (target {policy.target_lufs} +/- {policy.lufs_tolerance}); LRA {loud.get('lra')} LU"],
                    "" if loud_ok else "Normalize the mix to the social loudness target.",
                )
            )
        if peak is not None and peak != float("-inf"):
            peak_ok = peak <= policy.max_true_peak_db
            out.append(
                finding(
                    "true_peak",
                    "PASS" if peak_ok else "WARN",
                    100 if peak_ok else 60,
                    [f"peak {peak} dBFS; ceiling {policy.max_true_peak_db}"],
                    "" if peak_ok else "Apply a limiter below -1 dBTP.",
                )
            )
    return out


# ---------------------------------------------------------------------------
# Frames and contact sheet
# ---------------------------------------------------------------------------


async def extract_frames(
    path: Path,
    ffmpeg: str,
    timestamps: list[float],
    out_dir: Path,
    *,
    long_edge: int,
    starts: list[float],
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = f"scale='if(gt(iw,ih),{long_edge},-2)':'if(gt(iw,ih),-2,{long_edge})'"
    frames: list[dict[str, Any]] = []
    for number, stamp in enumerate(timestamps, start=1):
        target = out_dir / f"frame-{number:03d}-{stamp:07.2f}.jpg"
        result = await _run(
            [
                ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{stamp:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", scale, "-q:v", "3", str(target),
            ]
        )
        if result.ok and target.is_file() and target.stat().st_size > 0:
            frames.append(
                {"path": str(target), "timestamp": stamp, "scene_index": scene_at(starts, stamp, tolerance=0.0)}
            )
    return frames


def build_contact_sheet(frames: list[dict[str, Any]], target: Path, *, columns: int = 4) -> Path | None:
    if Image is None or not frames:
        return None
    thumbs = []
    for frame in frames:
        with Image.open(frame["path"]) as opened:
            image = opened.convert("RGB")
        image.thumbnail((320, 320))
        thumbs.append((image, frame))
    cell_w = max(image.width for image, _frame in thumbs)
    cell_h = max(image.height for image, _frame in thumbs) + 18
    cols = max(1, min(columns, len(thumbs)))
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 18, 22))
    draw = ImageDraw.Draw(sheet)
    for position, (image, frame) in enumerate(thumbs):
        x, y = (position % cols) * cell_w, (position // cols) * cell_h
        sheet.paste(image, (x, y))
        label = f"#{position + 1} {frame['timestamp']:.1f}s s{frame.get('scene_index')}"
        draw.text((x + 4, y + image.height + 3), label, fill=(230, 230, 235))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, "JPEG", quality=85)
    return target


def _pick_vision_frames(frames: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(frames) <= limit:
        return frames
    # Keep the first frame of each scene, then spread the rest evenly.
    firsts: dict[Any, dict[str, Any]] = {}
    for frame in frames:
        firsts.setdefault(frame.get("scene_index"), frame)
    chosen = list(firsts.values())[:limit]
    rest = [frame for frame in frames if frame not in chosen]
    room = limit - len(chosen)
    if room > 0 and rest:
        step = len(rest) / room
        chosen += [rest[int(position * step)] for position in range(room)]
    return sorted(chosen, key=lambda frame: frame["timestamp"])


def vision_findings(analysis: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, item in (analysis.get("dimensions") or {}).items():
        if isinstance(item, dict):
            out.append(
                finding(
                    name,
                    str(item["status"]),
                    float(item["score"]),
                    list(item["evidence"]),
                    str(item.get("recommendation") or ""),
                    source="vision",
                )
            )
    for issue in analysis.get("issues") or []:
        frame = frames[int(issue["frame"]) - 1]
        out.append(
            finding(
                str(issue["check"]),
                str(issue["status"]),
                30 if issue["status"] == "BLOCK" else 65,
                [str(issue["evidence"])],
                str(issue.get("recommendation") or ""),
                source="vision",
                scene_index=frame.get("scene_index"),
                timestamp=float(frame["timestamp"]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _average(findings: list[dict[str, Any]]) -> float:
    scores = [float(item["score"]) for item in findings]
    return sum(scores) / len(scores) if scores else 0.0


def verdict_for(score: float, findings: list[dict[str, Any]], policy: VideoQAPolicy) -> str:
    strongest = max(
        (str(item.get("status") or "BLOCK") for item in findings),
        key=lambda status: _STATUS_RANK.get(status, 2),
        default="BLOCK",
    )
    if strongest == "BLOCK" or score < policy.warn_score:
        return "BLOCK"
    if strongest == "WARN" or score < policy.pass_score:
        return "WARN"
    return "PASS"


def failing_scenes(report: dict[str, Any], *, include_warn: bool = True) -> dict[int, list[dict[str, Any]]]:
    """Group scene-pinned findings that should trigger a targeted re-render."""
    wanted = {"BLOCK", "WARN"} if include_warn else {"BLOCK"}
    out: dict[int, list[dict[str, Any]]] = {}
    for item in report.get("findings") or []:
        index = item.get("scene_index")
        if isinstance(index, int) and item.get("status") in wanted:
            out.setdefault(index, []).append(item)
    return out


async def run_video_qa(
    video: str | Path,
    *,
    scenes: list[dict[str, Any]] | None = None,
    requirements: dict[str, Any] | None = None,
    provider: Any | None = None,
    policy: VideoQAPolicy | None = None,
    work_dir: str | Path | None = None,
    context: dict[str, Any] | None = None,
    vision: bool = True,
) -> dict[str, Any]:
    """Objective + vision QA for one rendered video."""
    active = policy or VideoQAPolicy()
    path = Path(video).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"video not found: {video}")
    ffmpeg, ffprobe = await asyncio.to_thread(_tools)
    if not ffmpeg or not ffprobe:
        return {
            "schema_version": 1,
            "verdict": "SKIPPED",
            "reason": "ffmpeg/ffprobe are not installed",
            "findings": [],
            "pass": False,
        }
    starts = scene_starts(scenes)
    info = await probe_video(path, ffprobe)
    events = await analyze_streams(
        path, ffmpeg, duration=info["duration"], has_audio=info["has_audio"], policy=active
    )
    objective = objective_findings(info, events, requirements=requirements, starts=starts, policy=active)
    out_dir = Path(work_dir) if work_dir else path.parent / f"{path.stem}-qa"
    timestamps = sample_timestamps(info["duration"], starts, every_s=active.sample_every_s)
    frames = await extract_frames(
        path, ffmpeg, timestamps, out_dir / "frames", long_edge=active.frame_long_edge, starts=starts
    )
    sheet = await asyncio.to_thread(build_contact_sheet, frames, out_dir / "contact-sheet.jpg")
    vision_rows: list[dict[str, Any]] = []
    provider_meta: dict[str, Any] = {"provider": None, "model": None, "usage": {}, "cost_usd": None}
    analyze = getattr(provider, "analyze_video", None) if provider is not None else None
    if vision and frames:
        if not callable(analyze):
            vision_rows.append(
                finding(
                    "vision_analysis",
                    "BLOCK" if active.block_on_vision_failure else "WARN",
                    0 if active.block_on_vision_failure else 70,
                    ["no video-capable visual QA provider was available"],
                    "Configure a multimodal chat provider for the vision rubric.",
                    source="gate",
                )
            )
        else:
            picked = _pick_vision_frames(frames, active.max_vision_frames)
            try:
                result = await analyze(
                    frames=picked,
                    contact_sheet=sheet,
                    context={**dict(context or {}), "requirements": dict(requirements or {}),
                             "scenes": [
                                 {"index": i, "template": s.get("template"), "start": starts[i],
                                  "duration": s.get("duration")}
                                 for i, s in enumerate(scenes or [])
                             ]},
                )
                vision_rows = vision_findings(result.analysis, picked)
                provider_meta = {
                    "provider": result.provider,
                    "model": result.model,
                    "usage": result.usage,
                    "cost_usd": result.cost_usd,
                }
            except Exception as exc:  # noqa: BLE001 - an unusable answer must not approve
                vision_rows.append(
                    finding(
                        "vision_analysis",
                        "BLOCK" if active.block_on_vision_failure else "WARN",
                        0 if active.block_on_vision_failure else 60,
                        [f"provider response was unusable: {str(exc)[:200]}"],
                        "Rerun with a healthy multimodal provider. Do not approve automatically.",
                        source="gate",
                    )
                )
    objective_score = _average(objective)
    vision_score = _average(vision_rows) if vision_rows else objective_score
    score = objective_score * active.deterministic_weight + vision_score * active.vision_weight
    findings = objective + vision_rows
    verdict = verdict_for(score, findings, active)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "video": {**info, "path": str(path)},
        "requirements": dict(requirements or {}),
        "policy": asdict(active),
        "provider": provider_meta,
        "events": events,
        "frames": frames,
        "contact_sheet": str(sheet) if sheet else "",
        "scores": {
            "objective": round(objective_score, 2),
            "vision": round(vision_score, 2),
            "overall": round(score, 2),
        },
        "verdict": verdict,
        "findings": findings,
        "pass": verdict == "PASS",
    }
    report["scenes_to_fix"] = {str(key): len(value) for key, value in failing_scenes(report).items()}
    atomic_write_text(
        out_dir / "video-qa.json",
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
    )
    return report


def video_qa_ready() -> bool:
    return bool(shutil.which("ffmpeg") or _tools()[0])
