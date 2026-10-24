"""Turn a screen recording into ordered, deduplicated visual steps."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs
from navin.visual.compare import compare_images

Runner = Callable[[list[str]], Any]
_PTS = re.compile(r"pts_time:([0-9.]+)")
INSTALL_HINT = (
    "ffmpeg not found: install it (apt install ffmpeg, brew install ffmpeg, "
    "or winget install ffmpeg) and make sure it is on PATH"
)


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(INSTALL_HINT)
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **no_window_kwargs())


def _run(runner: Runner, cmd: list[str]) -> Any:
    res = runner(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({res.returncode}): {str(res.stderr)[-500:]}")
    return res


def _extract(runner: Runner, video: Path, out_dir: Path, prefix: str, vf: str, max_frames: int):
    for old in out_dir.glob(f"{prefix}_*.png"):
        old.unlink()
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(video),
        "-vf", f"{vf},showinfo", "-vsync", "vfr", "-frames:v", str(max_frames),
        str(out_dir / f"{prefix}_%04d.png"),
    ]
    res = _run(runner, cmd)
    times = [float(t) for t in _PTS.findall(str(res.stderr or ""))]
    files = sorted(out_dir.glob(f"{prefix}_*.png"))
    return [
        {"frame_path": str(f), "timestamp": times[i] if i < len(times) else None}
        for i, f in enumerate(files)
    ]


def extract_keyframes(
    video: str | Path,
    out_dir: str | Path,
    max_frames: int = 20,
    scene_threshold: float = 0.3,
    runner: Runner | None = None,
) -> list[dict[str, Any]]:
    """Extract keyframes on scene changes; fall back to regular sampling."""
    run = runner or _default_runner
    src = Path(video)
    if not src.is_file():
        raise FileNotFoundError(f"video not found: {src}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = _extract(run, src, out, "scene", f"select='gt(scene,{scene_threshold})'", max_frames)
    if len(frames) >= min(3, max_frames):
        return frames
    probe = _run(run, [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(src),
    ])
    try:
        duration = float(str(probe.stdout).strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned no duration for {src}") from exc
    if duration <= 0:
        raise RuntimeError(f"video has no duration: {src}")
    interval = duration / max_frames
    return _extract(run, src, out, "sample", f"fps=1/{interval:.6f}", max_frames)


def steps_from_frames(frames: list[Any], similarity: float = 0.98) -> list[dict[str, Any]]:
    """Ordered steps, dropping frames nearly identical to the previous kept one."""
    steps: list[dict[str, Any]] = []
    last: str | None = None
    for item in frames:
        if isinstance(item, dict):
            path, ts = str(item["frame_path"]), item.get("timestamp")
        else:
            path, ts = str(item), None
        if last is not None and compare_images(last, path)["score"] >= similarity:
            continue
        steps.append({"index": len(steps), "timestamp": ts, "frame_path": path})
        last = path
    return steps
