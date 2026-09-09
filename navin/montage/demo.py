# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live browser demo footage → social-ready exports for Montage."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.montage.ffmpeg_runner import run_process_sync

_FFMPEG_TIMEOUT_S = 600
_VIDEO_EXTS = {".webm", ".mp4", ".mov", ".mkv"}


def demos_dir(root: Path) -> Path:
    path = root / WORKSPACE_MONTAGE_DIR / "demos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir(root: Path) -> Path:
    path = root / WORKSPACE_MONTAGE_DIR / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def transcode_to_mp4(source: Path) -> Path | None:
    """Best-effort WebM/MOV → MP4 via ffmpeg. Returns None if ffmpeg missing."""
    from navin.montage.detect import find_ffmpeg

    source = source.expanduser().resolve()
    if not source.is_file():
        return None
    if source.suffix.lower() == ".mp4":
        return source
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    dest = source.with_suffix(".mp4")
    completed = run_process_sync(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout_s=_FFMPEG_TIMEOUT_S,
    )
    if not completed.ok or not dest.is_file():
        return None
    return dest


def register_demo(root: Path, path: str, *, name: str | None = None) -> dict[str, Any]:
    """Copy a recorded demo into marketing/montage/demos/."""
    root = root.resolve()
    src = Path(path).expanduser()
    if not src.is_absolute():
        src = (root / src).resolve()
    else:
        src = src.resolve()
    try:
        src.relative_to(root)
    except ValueError:
        # Allow media dir artifacts outside workspace root when under home media
        pass
    if not src.is_file():
        return {"ok": False, "error": "not_found", "fix": f"file not found: {path}"}
    if src.suffix.lower() not in _VIDEO_EXTS:
        return {
            "ok": False,
            "error": "bad_type",
            "fix": f"expected video ({', '.join(sorted(_VIDEO_EXTS))})",
        }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stem = (name or src.stem or "demo").strip().replace(" ", "-")[:64] or "demo"
    dest = demos_dir(root) / f"{stem}-{stamp}{src.suffix.lower()}"
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        return {"ok": False, "error": "copy_failed", "fix": str(exc)}

    mp4 = transcode_to_mp4(dest)
    rel = str(dest.relative_to(root)).replace("\\", "/")
    out: dict[str, Any] = {
        "ok": True,
        "demo": rel,
        "mp4": "",
    }
    if mp4 and mp4.is_file():
        try:
            out["mp4"] = str(mp4.relative_to(root)).replace("\\", "/")
        except ValueError:
            out["mp4"] = str(mp4)
    return out


def _ffmpeg_crop(source: Path, dest: Path, vf: str) -> bool:
    from navin.montage.detect import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    completed = run_process_sync(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout_s=_FFMPEG_TIMEOUT_S,
    )
    return completed.ok and dest.is_file()


def package_social(
    root: Path,
    demo_path: str,
    *,
    title: str = "Product demo",
    burn_srt: str | None = None,
    profiles: str | None = None,
) -> dict[str, Any]:
    """Produce platform-profile exports from a demo file (YouTube, IG, TikTok, …)."""
    from navin.montage.profiles import resolve_package_profiles

    root = root.resolve()
    src = Path(demo_path).expanduser()
    if not src.is_absolute():
        src = (root / src).resolve()
    else:
        src = src.resolve()
    if not src.is_file():
        return {"ok": False, "error": "not_found", "fix": f"demo not found: {demo_path}"}

    selected = resolve_package_profiles(profiles)
    # Prefer mp4 source
    work = transcode_to_mp4(src) or src
    out_dir = exports_dir(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"demo-{stamp}"

    from navin.montage.detect import find_ffmpeg

    if not find_ffmpeg():
        master = out_dir / f"{base}-master{work.suffix.lower()}"
        try:
            shutil.copy2(work, master)
        except OSError as exc:
            return {"ok": False, "error": "ffmpeg_missing", "fix": str(exc)}
        master_rel = str(master.relative_to(root)).replace("\\", "/")
        # The brief is part of the deliverable even without ffmpeg: it names
        # the master copy and says why the platform profiles are missing.
        brief = out_dir / f"{base}-brief.md"
        brief.write_text(
            "\n".join(
                [
                    f"# Demo export - {title}",
                    "",
                    f"Source: `{demo_path}`",
                    "",
                    "## Platform profiles",
                    "",
                    "- (none - ffmpeg missing; only the master was copied)",
                    "",
                    f"Master: `{master_rel}`",
                    "",
                    "Status: proposed for publish - do not auto-upload.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "partial": True,
            "title": title,
            "master": master_rel,
            "exports": {},
            "profiles": [p.id for p in selected],
            "brief": str(brief.relative_to(root)).replace("\\", "/"),
            "fix": (
                "ffmpeg missing - only master copied. Install ffmpeg then "
                "montage(action=package) again for platform profiles."
            ),
        }

    exports: dict[str, str] = {}
    for profile in selected:
        dest = out_dir / f"{base}-{profile.id}.mp4"
        filter_chain = profile.ffmpeg_vf
        if burn_srt:
            srt = Path(burn_srt).expanduser()
            if not srt.is_absolute():
                srt = (root / srt).resolve()
            if srt.is_file():
                srt_esc = str(srt).replace("\\", "/").replace(":", "\\:")
                filter_chain = f"{profile.ffmpeg_vf},subtitles='{srt_esc}'"
        if _ffmpeg_crop(work, dest, filter_chain):
            exports[profile.id] = str(dest.relative_to(root)).replace("\\", "/")

    master_rel = ""
    if not exports:
        master = out_dir / f"{base}-master{work.suffix.lower()}"
        try:
            shutil.copy2(work, master)
            master_rel = str(master.relative_to(root)).replace("\\", "/")
        except OSError:
            master_rel = ""

    brief = out_dir / f"{base}-brief.md"
    variant_lines = [
        f"- **{pid}** ({next((p.label for p in selected if p.id == pid), pid)}): `{path}`"
        for pid, path in exports.items()
    ] or ["- (none - ffmpeg could not re-encode; see master copy)"]
    brief.write_text(
        "\n".join(
            [
                f"# Demo export - {title}",
                "",
                f"Source: `{demo_path}`",
                "",
                "## Platform profiles",
                "",
                *variant_lines,
                "",
                f"Master: `{master_rel}`" if master_rel else "Master: (unavailable)",
                "",
                "Status: proposed for publish - do not auto-upload.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    if exports:
        return {
            "ok": True,
            "title": title,
            "source": demo_path,
            "exports": exports,
            "profiles": [p.id for p in selected],
            "brief": str(brief.relative_to(root)).replace("\\", "/"),
            "master": master_rel,
            "fix": "",
        }

    if master_rel:
        return {
            "ok": True,
            "partial": True,
            "title": title,
            "source": demo_path,
            "exports": {},
            "profiles": [p.id for p in selected],
            "brief": str(brief.relative_to(root)).replace("\\", "/"),
            "master": master_rel,
            "error": "export_reencode_failed",
            "fix": (
                "ffmpeg could not build platform profiles from this source "
                "(corrupt/incomplete demo?). Master copy kept - re-record or "
                "transcode manually."
            ),
        }

    return {
        "ok": False,
        "error": "export_failed",
        "title": title,
        "source": demo_path,
        "exports": {},
        "profiles": [p.id for p in selected],
        "brief": str(brief.relative_to(root)).replace("\\", "/"),
        "fix": "ffmpeg failed to produce exports and master copy - check the source demo.",
    }


def render_register_result(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"Demo register failed: {result.get('error')} - Fix: {result.get('fix')}"
    lines = [
        "Demo registered:",
        f"  demo: {result.get('demo')}",
    ]
    if result.get("mp4"):
        lines.append(f"  mp4: {result['mp4']}")
    lines.append("Next: analyze the product flow, then montage(action=package, path=...).")
    return "\n".join(lines)


def render_package_result(result: dict[str, Any]) -> str:
    if not result.get("ok") and not result.get("partial"):
        err = result.get("error") or "export_failed"
        fix = result.get("fix") or "Check the demo source and ffmpeg."
        return f"Package failed: {err} - Fix: {fix}"
    lines = [
        f"Social demo package - {result.get('title')}:",
        f"  brief: {result.get('brief') or '(none)'}",
    ]
    if result.get("master"):
        lines.append(f"  master: {result['master']}")
    for ratio, path in (result.get("exports") or {}).items():
        lines.append(f"  {ratio}: {path}")
    if result.get("partial"):
        lines.append("  status: partial (ratios present; ratio exports missing)")
    if result.get("fix"):
        lines.append(f"  note: {result['fix']}")
    else:
        lines.append("Ready to propose for social - never auto-publish.")
    return "\n".join(lines)
