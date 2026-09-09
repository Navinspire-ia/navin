# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Render a HyperFrames HTML composition to MP4 (requires lazy setup)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.montage.detect import find_chrome, hyperframes_bin
from navin.montage.doctor import run_doctor
from navin.utils.proc import no_window_kwargs

_RENDER_TIMEOUT_S = 1800


def _resolve_composition(root: Path, composition: str) -> Path | None:
    raw = (composition or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if path.is_file() and path.suffix.lower() in {".html", ".htm"}:
        return path
    return None


async def render_composition(
    root: Path,
    *,
    composition: str,
    output: str | None = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    profile: str | None = None,
) -> dict[str, Any]:
    """Invoke HyperFrames CLI against a workspace HTML composition."""
    if profile:
        from navin.montage.profiles import get_profile

        resolved = get_profile(profile)
        if resolved is None:
            return {
                "ok": False,
                "error": "bad_profile",
                "fix": (
                    f"Unknown profile={profile!r}. "
                    "Use montage(action=profiles) for the built-in list."
                ),
            }
        width = resolved.width
        height = resolved.height

    doctor = run_doctor()
    if not doctor.ready_for_composition:
        missing = [c.name for c in doctor.checks if c.status == "missing"]
        return {
            "ok": False,
            "error": "not_ready",
            "fix": (
                "Resolve doctor blockers then montage(action=setup). "
                f"Missing: {', '.join(missing) or 'unknown'}."
            ),
            "doctor": doctor.render(),
        }

    html = _resolve_composition(root, composition)
    if html is None:
        return {
            "ok": False,
            "error": "bad_composition",
            "fix": (
                "Pass composition= as a workspace-relative .html path "
                f"(e.g. {WORKSPACE_MONTAGE_DIR}/compositions/reel.html)."
            ),
        }

    out_dir = root / WORKSPACE_MONTAGE_DIR / "renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    if output:
        out_path = Path(output).expanduser()
        if not out_path.is_absolute():
            out_path = (root / out_path).resolve()
        try:
            out_path.relative_to(root.resolve())
        except ValueError:
            return {
                "ok": False,
                "error": "bad_output",
                "fix": "output must stay inside the workspace.",
            }
    else:
        out_path = out_dir / f"{html.stem}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    hf = hyperframes_bin()
    assert hf is not None  # doctor.ready_for_composition
    chrome = find_chrome()
    env = os.environ.copy()
    if chrome:
        env["PUPPETEER_EXECUTABLE_PATH"] = chrome
        env["PUPPETEER_SKIP_DOWNLOAD"] = "1"

    # Prefer `hyperframes render <html> -o <mp4>` when CLI supports it;
    # fall back to npx from montage home.
    cmd: list[str]
    if hf.suffix == ".js":
        node = shutil.which("node")
        if not node:
            return {
                "ok": False,
                "error": "node_missing",
                "fix": "Node.js required to run HyperFrames.",
            }
        cmd = [node, str(hf), "render", str(html), "-o", str(out_path)]
    else:
        cmd = [str(hf), "render", str(html), "-o", str(out_path)]

    # Optional size hints when CLI accepts them (ignored if unsupported).
    cmd.extend(["--width", str(width), "--height", str(height), "--fps", str(fps)])

    try:
        completed = await asyncio.to_thread(
            subprocess.run,  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RENDER_TIMEOUT_S,
            env=env,
            cwd=str(html.parent),
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "timeout",
            "fix": "Render timed out. Lower quality/fps or free disk (≥2 GB scratch).",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": "exec_failed",
            "fix": str(exc),
        }

    log_tail = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-3000:]
    if completed.returncode != 0 or not out_path.is_file():
        # Retry without optional flags (older CLIs).
        simple = cmd[: cmd.index("--width")] if "--width" in cmd else cmd
        if simple != cmd:
            try:
                completed = await asyncio.to_thread(
                    subprocess.run,  # noqa: S603
                    simple,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_RENDER_TIMEOUT_S,
                    env=env,
                    cwd=str(html.parent),
                    **no_window_kwargs(),
                )
                log_tail = (
                    (completed.stdout or "") + "\n" + (completed.stderr or "")
                )[-3000:]
            except (OSError, subprocess.SubprocessError) as exc:
                return {
                    "ok": False,
                    "error": "render_failed",
                    "fix": str(exc),
                    "log": log_tail,
                }

    if completed.returncode != 0 or not out_path.is_file():
        return {
            "ok": False,
            "error": "render_failed",
            "fix": (
                "HyperFrames render failed. Lint the composition HTML, run "
                "montage(action=doctor), check logs."
            ),
            "log": log_tail,
            "cmd": cmd,
        }

    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    return {
        "ok": True,
        "output": rel,
        "composition": str(html.relative_to(root)),
        "log": log_tail,
    }


def render_result_text(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return "\n".join(
            [
                "HyperFrames render OK:",
                f"  composition: {result.get('composition')}",
                f"  output: {result.get('output')}",
            ]
        )
    lines = [
        "HyperFrames render failed:",
        f"  error: {result.get('error')}",
        f"  Fix: {result.get('fix')}",
    ]
    if result.get("doctor"):
        lines.extend(["", str(result["doctor"])])
    if result.get("log"):
        lines.extend(["", "Log tail:", str(result["log"])])
    return "\n".join(lines)
