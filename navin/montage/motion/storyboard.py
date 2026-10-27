# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Storyboard: motion scenes + footage + transitions + audio -> one video.

Contract (shared with the other marketing agents)::

    {
      "version": 1,
      "format": "16:9" | "9:16" | "1:1",          # optional "resolution": "4k" (16:9 only)
                                                   # optional "size": [w, h] for drafts
      "fps": 30 | 60,
      "brand": {brand kit} | "path/to/brand.json", # optional; marketing desk otherwise
      "scenes": [
        {"template": "hero_intro", "duration": 4, "props": {...},
         "transition": "cut" | "fade" | "slide" | "zoom" | "whip",
         "transition_duration": 0.5},               # optional
        {"type": "footage", "path": "clip.mp4", "duration": 5,
         "start": 2.0, "fit": "cover" | "contain", "transition": "fade"}
      ],
      "audio": {"voiceover": "vo.mp3", "music": "bed.mp3",
                "sfx": [{"kind": "whoosh", "at": 3.5}],
                "voice_segments": [{"path": "s1.mp3", "at": 0.0}]}
    }

A scene's ``transition`` is how it *enters* (the first scene's is ignored).
Transitions overlap the two scenes (ffmpeg ``xfade``), so the total length is
``sum(durations) - sum(overlaps)``; ``cut`` overlaps nothing. Footage scenes
reference a processed clip (for example from the footage polish step) and are
trimmed, scaled and padded or looped to their declared duration.

Each motion scene renders through :func:`~navin.montage.motion.renderer.render_motion`
to a near-lossless intermediate; one ffmpeg pass joins everything. Audio
mixing is delegated to :func:`navin.montage.assemble.assemble`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.montage.motion.brand import BrandKit, load_brand_kit
from navin.montage.motion.catalog import get_template
from navin.montage.motion.media import VIDEO_SUFFIXES, PathResolver, default_resolver
from navin.montage.motion.renderer import (
    FORMATS,
    MotionProgress,
    RenderSettings,
    render_motion,
)
from navin.utils.proc import no_window_kwargs

TRANSITIONS: dict[str, tuple[str, float]] = {
    "fade": ("fade", 0.5),
    "slide": ("slideleft", 0.5),
    "zoom": ("zoomin", 0.6),
    "whip": ("smoothleft", 0.3),
}
FOOTAGE = "footage"
_INTERMEDIATE_CRF = 10
_FINAL_CRF = 16
_MAX_SCENES = 60
_MAX_TOTAL_S = 600.0

ProgressFn = Callable[[dict[str, Any]], None]


class StoryboardError(ValueError):
    """The storyboard does not follow the contract."""


@dataclass(slots=True)
class Scene:
    kind: str  # "motion" or "footage"
    duration: float
    template: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    path: str = ""
    start: float = 0.0
    fit: str = "cover"
    transition: str = "cut"
    transition_duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"duration": self.duration, "transition": self.transition}
        if self.kind == FOOTAGE:
            row.update({"type": FOOTAGE, "path": self.path, "start": self.start, "fit": self.fit})
        else:
            row.update({"template": self.template, "props": self.props})
        if self.transition != "cut":
            row["transition_duration"] = self.transition_duration
        return row


@dataclass(slots=True)
class Storyboard:
    format: str
    fps: int
    width: int
    height: int
    scenes: list[Scene]
    brand: BrandKit
    audio: dict[str, Any] = field(default_factory=dict)
    encode: str | None = None

    @property
    def overlaps(self) -> list[float]:
        """Seconds each scene overlaps the previous one (0 for the first/cuts)."""
        return [0.0] + [
            s.transition_duration if s.transition != "cut" else 0.0 for s in self.scenes[1:]
        ]

    @property
    def total_duration(self) -> float:
        return round(sum(s.duration for s in self.scenes) - sum(self.overlaps), 6)


def _number(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StoryboardError(f"{name} must be a number") from exc


def load_storyboard_json(value: Any, *, base_dir: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """Raw storyboard dict from a dict, a JSON string or a JSON file path."""
    if isinstance(value, dict):
        return value, base_dir
    text = str(value or "").strip()
    if not text:
        raise StoryboardError("storyboard is empty")
    if text.startswith("{"):
        try:
            return json.loads(text), base_dir
        except json.JSONDecodeError as exc:
            raise StoryboardError(f"storyboard JSON is invalid: {exc}") from exc
    path = Path(text).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        raise StoryboardError(f"storyboard file not found: {text}")
    try:
        return json.loads(path.read_text(encoding="utf-8")), path.parent
    except (OSError, json.JSONDecodeError) as exc:
        raise StoryboardError(f"storyboard file is not valid JSON: {exc}") from exc


def parse_storyboard(
    raw: dict[str, Any],
    *,
    base_dir: Path | None = None,
    use_marketing_store: bool = True,
) -> Storyboard:
    """Validate a storyboard dict against the contract."""
    if not isinstance(raw, dict):
        raise StoryboardError("storyboard must be a JSON object")
    version = raw.get("version", 1)
    if version not in (1, "1"):
        raise StoryboardError(f"unsupported storyboard version {version!r}; use 1")
    fmt = str(raw.get("format") or "16:9").strip().lower()
    if fmt not in ("16:9", "9:16", "1:1"):
        raise StoryboardError("format must be 16:9, 9:16 or 1:1")
    width, height = FORMATS[fmt]
    resolution = str(raw.get("resolution") or "").strip().lower()
    if resolution in ("4k", "2160p", "uhd"):
        if fmt != "16:9":
            raise StoryboardError("resolution 4k is available for format 16:9 only")
        width, height = FORMATS["4k"]
    elif resolution not in ("", "1080p", "hd", "fhd"):
        raise StoryboardError("resolution must be 1080p or 4k")
    size = raw.get("size")
    if size is not None:
        # Draft / preview override: explicit [width, height] (even numbers).
        if not (isinstance(size, list) and len(size) == 2):
            raise StoryboardError("size must be [width, height]")
        width, height = (int(_number(v, "size")) for v in size)
        if not (64 <= width <= 3840 and 64 <= height <= 3840) or width % 2 or height % 2:
            raise StoryboardError("size must be even numbers between 64 and 3840")
    fps = int(_number(raw.get("fps", 30), "fps"))
    if fps not in (24, 25, 30, 50, 60):
        raise StoryboardError("fps must be 30 or 60 (24, 25 and 50 are accepted too)")
    scenes_raw = raw.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise StoryboardError("scenes must be a non-empty list")
    if len(scenes_raw) > _MAX_SCENES:
        raise StoryboardError(f"at most {_MAX_SCENES} scenes per storyboard")
    scenes: list[Scene] = []
    for index, item in enumerate(scenes_raw):
        where = f"scenes[{index}]"
        if not isinstance(item, dict):
            raise StoryboardError(f"{where} must be an object")
        props = item.get("props") or {}
        if not isinstance(props, dict):
            raise StoryboardError(f"{where}.props must be an object")
        kind_raw = str(item.get("type") or item.get("template") or "").strip().lower()
        transition = str(item.get("transition") or "cut").strip().lower()
        if transition not in TRANSITIONS and transition not in ("cut", "none"):
            raise StoryboardError(
                f"{where}.transition must be cut, fade, slide, zoom or whip"
            )
        if transition == "none":
            transition = "cut"
        t_default = TRANSITIONS.get(transition, ("", 0.0))[1]
        t_dur = _number(item.get("transition_duration", t_default), f"{where}.transition_duration")
        if kind_raw == FOOTAGE:
            path = str(item.get("path") or props.get("path") or props.get("clip") or "").strip()
            if not path:
                raise StoryboardError(f"{where} is footage but has no path")
            duration = item.get("duration", props.get("duration"))
            if duration is None:
                raise StoryboardError(f"{where}.duration is required")
            fit = str(item.get("fit") or props.get("fit") or "cover").strip().lower()
            if fit not in ("cover", "contain"):
                raise StoryboardError(f"{where}.fit must be cover or contain")
            scene = Scene(
                kind=FOOTAGE,
                duration=_number(duration, f"{where}.duration"),
                path=path,
                start=max(0.0, _number(item.get("start", props.get("start", 0)) or 0, f"{where}.start")),
                fit=fit,
            )
        else:
            info = get_template(kind_raw)
            if info is None:
                raise StoryboardError(
                    f"{where}.template {kind_raw!r} is unknown; use montage(action=motion_templates)"
                )
            duration = item.get("duration", info.default_duration)
            scene = Scene(
                kind="motion",
                duration=_number(duration, f"{where}.duration"),
                template=info.name,
                props=props,
            )
            if scene.duration < info.min_duration * 0.5:
                raise StoryboardError(
                    f"{where}: {info.name} needs at least {info.min_duration * 0.5:g}s"
                )
        if not 0.2 <= scene.duration <= 300:
            raise StoryboardError(f"{where}.duration must be between 0.2 and 300 seconds")
        scene.transition = transition if index > 0 else "cut"
        scene.transition_duration = t_dur if scene.transition != "cut" else 0.0
        scenes.append(scene)
    for index in range(1, len(scenes)):
        scene = scenes[index]
        if scene.transition == "cut":
            continue
        limit = min(scenes[index - 1].duration, scene.duration) * 0.5
        if not 0.05 <= scene.transition_duration <= max(0.05, limit):
            raise StoryboardError(
                f"scenes[{index}].transition_duration must be between 0.05 and {limit:.2f}s "
                "(half of the shorter neighbouring scene)"
            )
    audio = raw.get("audio") or {}
    if not isinstance(audio, dict):
        raise StoryboardError("audio must be an object")
    brand = load_brand_kit(raw.get("brand"), base_dir=base_dir, use_marketing_store=use_marketing_store)
    board = Storyboard(
        format=fmt,
        fps=fps,
        width=width,
        height=height,
        scenes=scenes,
        brand=brand,
        audio=dict(audio),
        encode=(str(raw["encode"]).strip().lower() if raw.get("encode") else None),
    )
    if board.total_duration > _MAX_TOTAL_S:
        raise StoryboardError(f"storyboard is longer than {_MAX_TOTAL_S:.0f}s")
    return board


def build_join_args(
    board: Storyboard,
    inputs: list[str],
    output: str,
    ffmpeg: str,
    *,
    crf: int = _FINAL_CRF,
    preset: str = "slow",
) -> list[str]:
    """ffmpeg argv that normalizes every scene and chains cuts / xfades."""
    w, h, fps = board.width, board.height, board.fps
    pad_color = board.brand.colors.get("background", "#000000")
    if not pad_color.startswith("#"):
        pad_color = "#000000"
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for scene, path in zip(board.scenes, inputs, strict=True):
        if scene.kind == FOOTAGE and scene.start > 0:
            argv += ["-ss", f"{scene.start:.3f}"]
        argv += ["-i", path]
    chains: list[str] = []
    for index, scene in enumerate(board.scenes):
        steps = []
        if scene.kind == FOOTAGE:
            if scene.fit == "contain":
                steps.append(
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"
                )
            else:
                steps.append(
                    f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"
                )
            steps.append("setsar=1")
        steps += [
            f"fps={fps}",
            # Footage shorter than its slot holds its last frame.
            f"tpad=stop_mode=clone:stop_duration={scene.duration:.3f}",
            f"trim=duration={scene.duration:.6f}",
            "setpts=PTS-STARTPTS",
            "format=yuv420p",
            "settb=AVTB",
        ]
        chains.append(f"[{index}:v]{','.join(steps)}[s{index}]")
    current = "[s0]"
    length = board.scenes[0].duration
    for index in range(1, len(board.scenes)):
        scene = board.scenes[index]
        label = f"[j{index}]"
        if scene.transition == "cut":
            chains.append(f"{current}[s{index}]concat=n=2:v=1:a=0{label}")
            length += scene.duration
        else:
            name = TRANSITIONS[scene.transition][0]
            offset = length - scene.transition_duration
            chains.append(
                f"{current}[s{index}]xfade=transition={name}:"
                f"duration={scene.transition_duration:.6f}:offset={offset:.6f}{label}"
            )
            length += scene.duration - scene.transition_duration
        current = label
    chains.append(
        f"{current}scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[out]"
    )
    argv += [
        "-filter_complex",
        ";".join(chains),
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-r",
        str(fps),
        "-t",
        f"{length:.6f}",
        "-movflags",
        "+faststart",
        output,
    ]
    return argv


async def _run(argv: list[str], timeout_s: float = 3600.0) -> None:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise StoryboardError("joining the scenes timed out") from exc
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-6:]
        raise StoryboardError("joining the scenes failed: " + " | ".join(tail))


def _has_audio(audio: dict[str, Any]) -> bool:
    return bool(
        audio.get("voiceover") or audio.get("voice") or audio.get("music")
        or audio.get("sfx") or audio.get("voice_segments")
    )


async def _mix_audio(
    board: Storyboard,
    video: Path,
    output: Path,
    resolve: PathResolver,
    ffmpeg: str | None,
) -> dict[str, Any]:
    """Hand the silent edit to montage assemble for the audio mix."""
    from navin.montage import assemble as assemble_module

    audio = board.audio

    def path_of(value: Any, name: str) -> str | None:
        if not value:
            return None
        found = resolve(str(value))
        if found is None:
            raise StoryboardError(f"audio.{name} not found or not allowed: {value}")
        return str(found)

    segments = []
    for item in audio.get("voice_segments") or []:
        if isinstance(item, dict) and item.get("path"):
            segments.append({**item, "path": path_of(item["path"], "voice_segments")})
    kwargs: dict[str, Any] = {
        "visuals": [str(video)],
        "output": str(output),
        "durations": [board.total_duration],
        "music": path_of(audio.get("music"), "music"),
        "voice": path_of(audio.get("voiceover") or audio.get("voice"), "voiceover"),
        "width": board.width,
        "height": board.height,
        "fps": board.fps,
        "music_gain_db": audio.get("music_gain_db"),
        "sfx": audio.get("sfx") or None,
        "voice_segments": segments or None,
        "encode": board.encode or "master",
        "brand": board.brand.to_dict(),
        "ffmpeg": ffmpeg,
    }
    public = getattr(assemble_module, "assemble", None)
    if public is not None:
        accepted = inspect.signature(public).parameters
        call = {k: v for k, v in kwargs.items() if k in accepted and v is not None}
        dropped = sorted(k for k, v in kwargs.items() if v is not None and k not in accepted)
        result = dict(await public(**call))
        if dropped:
            result["ignored_audio_options"] = dropped
        return result
    # Older assemble without the one-call helper.
    spec = assemble_module.spec_from_paths(
        visuals=kwargs["visuals"],
        output=kwargs["output"],
        durations=kwargs["durations"],
        music=kwargs["music"],
        voice=kwargs["voice"],
        width=board.width,
        height=board.height,
        fps=board.fps,
        music_gain_db=kwargs["music_gain_db"],
    )
    result = dict(await assemble_module.run_assemble(spec, ffmpeg=ffmpeg))
    if kwargs["sfx"] or segments:
        result["ignored_audio_options"] = ["sfx", "voice_segments"]
    return result


async def render_storyboard(
    storyboard: Any,
    output: str | Path,
    *,
    base_dir: Path | None = None,
    resolve: PathResolver | None = None,
    on_progress: ProgressFn | None = None,
    cancel: asyncio.Event | None = None,
    workers: int | None = None,
    use_marketing_store: bool = True,
) -> dict[str, Any]:
    """Render every scene, join them with transitions, then mix audio.

    *storyboard* is a dict, a JSON string, a JSON file path or a parsed
    :class:`Storyboard`. Returns a JSON-ready dict with ``ok``, ``output``,
    ``duration_s`` and per-scene details; never raises for render failures.
    """
    started = time.monotonic()
    try:
        if isinstance(storyboard, Storyboard):
            board, root = storyboard, base_dir
        else:
            raw, root = load_storyboard_json(storyboard, base_dir=base_dir)
            board = parse_storyboard(raw, base_dir=root, use_marketing_store=use_marketing_store)
    except StoryboardError as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:  # brand kit errors
        return {"ok": False, "error": str(exc)}
    resolver = resolve or default_resolver(root)
    from navin.montage.detect import find_ffmpeg

    ffmpeg = await asyncio.to_thread(find_ffmpeg)
    if not ffmpeg:
        return {
            "ok": False,
            "error": "ffmpeg is not installed. Run montage(action=setup, package=ffmpeg) first.",
        }
    out_path = Path(output).expanduser()
    if out_path.suffix.lower() != ".mp4":
        out_path = out_path.with_suffix(".mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=".navin-storyboard-", dir=str(out_path.parent)))
    motion_frames = sum(round(s.duration * board.fps) for s in board.scenes if s.kind != FOOTAGE)
    done_frames = 0
    scene_rows: list[dict[str, Any]] = []

    def report(phase: str, extra: dict[str, Any] | None = None) -> None:
        if on_progress is None:
            return
        row = {
            "phase": phase,
            "fraction": round(done_frames / motion_frames * 0.9, 4) if motion_frames else None,
        }
        row.update(extra or {})
        try:
            on_progress(row)
        except Exception:  # noqa: BLE001
            pass

    try:
        inputs: list[str] = []
        for index, scene in enumerate(board.scenes):
            if cancel is not None and cancel.is_set():
                return {"ok": False, "cancelled": True, "error": "render cancelled"}
            if scene.kind == FOOTAGE:
                found = resolver(scene.path)
                if found is None or found.suffix.lower() not in VIDEO_SUFFIXES | {".gif"}:
                    raise StoryboardError(f"scenes[{index}] footage not found or not a video: {scene.path}")
                inputs.append(str(found))
                scene_rows.append({"index": index, "type": FOOTAGE, "path": str(found), "duration_s": scene.duration})
                continue
            target = workdir / f"scene{index:02d}.mp4"
            base_frames = done_frames

            def on_scene(progress: MotionProgress, _index: int = index, _base: int = base_frames) -> None:
                nonlocal done_frames
                done_frames = _base + progress.frames_done
                report("rendering", {"scene": _index, "scene_phase": progress.phase})

            result = await render_motion(
                output=target,
                template=scene.template,
                props=scene.props,
                brand=board.brand,
                settings=RenderSettings(
                    width=board.width,
                    height=board.height,
                    fps=board.fps,
                    duration=scene.duration,
                    crf=_INTERMEDIATE_CRF,
                    preset="medium",
                    workers=workers,
                ),
                resolve=resolver,
                on_progress=on_scene,
                cancel=cancel,
                ffmpeg=ffmpeg,
            )
            if not result.get("ok"):
                if result.get("cancelled"):
                    return result
                return {
                    "ok": False,
                    "error": f"scenes[{index}] ({scene.template}) failed: {result.get('error')}",
                    "console_errors": result.get("console_errors") or [],
                    "scenes": scene_rows,
                }
            done_frames = base_frames + round(scene.duration * board.fps)
            inputs.append(str(target))
            scene_rows.append(
                {
                    "index": index,
                    "template": scene.template,
                    "duration_s": scene.duration,
                    "console_errors": result.get("console_errors") or [],
                }
            )

        report("joining")
        audio = _has_audio(board.audio)
        joined = workdir / "joined.mp4" if audio else out_path.with_name(f".{out_path.stem}.part.mp4")
        await _run(
            build_join_args(
                board,
                inputs,
                str(joined),
                ffmpeg,
                crf=_INTERMEDIATE_CRF if audio else _FINAL_CRF,
                preset="medium" if audio else "slow",
            )
        )
        mix: dict[str, Any] | None = None
        if audio:
            report("mixing")
            mix = await _mix_audio(board, joined, out_path, resolver, ffmpeg)
            if not mix.get("ok"):
                return {
                    "ok": False,
                    "error": f"audio mix failed: {mix.get('error')}",
                    "scenes": scene_rows,
                }
        else:
            joined.replace(out_path)
        report("done", {"fraction": 1.0})
        summary: dict[str, Any] = {
            "ok": True,
            "output": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "duration_s": board.total_duration,
            "format": board.format,
            "width": board.width,
            "height": board.height,
            "fps": board.fps,
            "scenes": scene_rows,
            "transitions": [s.transition for s in board.scenes[1:]],
            "has_audio": audio,
            "elapsed_s": round(time.monotonic() - started, 2),
        }
        if mix is not None:
            for key in ("has_music", "has_voice", "ignored_audio_options", "loudness", "audio"):
                if key in mix:
                    summary[key] = mix[key]
        return summary
    except StoryboardError as exc:
        return {"ok": False, "error": str(exc), "scenes": scene_rows}
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)


__all__ = [
    "FOOTAGE",
    "TRANSITIONS",
    "Scene",
    "Storyboard",
    "StoryboardError",
    "build_join_args",
    "load_storyboard_json",
    "parse_storyboard",
    "render_storyboard",
]
