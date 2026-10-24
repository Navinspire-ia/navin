# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live browser demo footage → social-ready exports for Montage."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.montage.ffmpeg_runner import run_process_sync

_FFMPEG_TIMEOUT_S = 600
# A slow-preset encode of a long 1080p demo can outlast the generic budget.
_DEMO_ENCODE_TIMEOUT_S = 1_800
_VIDEO_EXTS = {".webm", ".mp4", ".mov", ".mkv"}

# Demo masters: constant frame rate (browser captures are variable), visually
# lossless x264. Editors and social platforms stutter on VFR input.
DEMO_FPS = 30
DEMO_CRF = 16
DEMO_PRESET = "slow"


def demo_fps(fps: int | float | None = None) -> int:
    """Clamp a requested rate to the two supported outputs, 30 or 60 fps."""
    try:
        value = float(fps if fps is not None else DEMO_FPS)
    except (TypeError, ValueError):
        value = DEMO_FPS
    return 60 if value >= 45 else 30


def cfr_video_args(fps: int | float | None = None) -> list[str]:
    """ffmpeg output args: CFR at 30/60 fps, libx264 crf 16 preset slow."""
    rate = demo_fps(fps)
    return [
        "-vf",
        f"fps={rate},scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-r",
        str(rate),
        "-c:v",
        "libx264",
        "-preset",
        DEMO_PRESET,
        "-crf",
        str(DEMO_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]


def cfr_transcode_args(ffmpeg: str, source: Path, dest: Path, *, fps: int | float | None = None) -> list[str]:
    """Full argv turning a (VFR) recording into a CFR MP4 master."""
    return [ffmpeg, "-y", "-i", str(source), *cfr_video_args(fps), "-c:a", "aac", str(dest)]


def frames_encode_args(ffmpeg: str, concat_list: Path, dest: Path, *, fps: int | float | None = None) -> list[str]:
    """Full argv encoding a timed frame list (ffconcat) into a CFR MP4 master."""
    return [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        *cfr_video_args(fps),
        "-an",
        str(dest),
    ]


def write_frame_concat(frames: list[tuple[Path, float]], end_time: float, list_path: Path) -> Path:
    """Write an ffconcat list from (frame, seconds_from_start) pairs.

    Each frame stays on screen until the next one arrived (screencast frames
    only come when the page changes), the last one until ``end_time``. The
    first frame is shown from t=0 so the video timeline matches the action log.
    """
    lines = ["ffconcat version 1.0"]
    ordered = sorted(frames, key=lambda item: item[1])
    for index, (path, start) in enumerate(ordered):
        begin = 0.0 if index == 0 else start
        stop = ordered[index + 1][1] if index + 1 < len(ordered) else end_time
        duration = max(0.001, stop - begin)
        quoted = str(path).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{quoted}'")
        lines.append(f"duration {duration:.6f}")
    if ordered:
        # The concat demuxer ignores the last duration unless the file repeats.
        quoted = str(ordered[-1][0]).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{quoted}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def encode_frames_to_mp4(
    frames: list[tuple[Path, float]],
    dest: Path,
    *,
    end_time: float,
    fps: int | float | None = None,
) -> Path | None:
    """Encode timed screencast frames into a CFR MP4. None when ffmpeg is missing."""
    from navin.montage.detect import find_ffmpeg

    if not frames:
        return None
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    list_path = write_frame_concat(frames, end_time, dest.with_suffix(".frames.txt"))
    try:
        completed = run_process_sync(
            frames_encode_args(ffmpeg, list_path, dest, fps=fps),
            timeout_s=_DEMO_ENCODE_TIMEOUT_S,
        )
    finally:
        list_path.unlink(missing_ok=True)
    if not completed.ok or not dest.is_file():
        return None
    return dest


def demos_dir(root: Path) -> Path:
    path = root / WORKSPACE_MONTAGE_DIR / "demos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir(root: Path) -> Path:
    path = root / WORKSPACE_MONTAGE_DIR / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def transcode_to_mp4(source: Path, *, fps: int | float | None = None) -> Path | None:
    """Best-effort WebM/MOV → CFR MP4 master via ffmpeg. None if ffmpeg missing."""
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
        cfr_transcode_args(ffmpeg, source, dest, fps=fps),
        timeout_s=_DEMO_ENCODE_TIMEOUT_S,
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
    # Keep the recorder's actions.json next to the copy: package and
    # footage_polish use it for camera follow, cursor and dead-time trim.
    from navin.montage.footage import find_actions_sidecar

    sidecar = find_actions_sidecar(src)
    if sidecar is not None:
        try:
            shutil.copy2(sidecar, dest.with_name(f"{dest.stem}.actions.json"))
        except OSError:
            pass

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


def _prepare_polish(
    root: Path,
    src: Path,
    work: Path,
    actions: str | None,
    footage: dict[str, Any] | None,
) -> tuple[Any, str]:
    """Footage plan when an actions.json exists; ``(None, note)`` otherwise."""
    from navin.montage.footage import (
        FootageError,
        FootageOptions,
        find_actions_sidecar,
        prepare_footage,
    )

    sidecar: Path | None
    if (actions or "").strip():
        sidecar = Path(actions or "").expanduser()
        if not sidecar.is_absolute():
            sidecar = (root / sidecar).resolve()
        if not sidecar.is_file():
            return None, f"actions file not found: {actions} - plain crop used"
    else:
        sidecar = find_actions_sidecar(src) or find_actions_sidecar(work)
    if sidecar is None:
        return None, ""
    try:
        return prepare_footage(work, sidecar, FootageOptions.from_mapping(footage)), ""
    except (FootageError, OSError, ValueError) as exc:
        return None, f"polish skipped ({exc}) - plain crop used"


def _render_polished_profile(
    root: Path,
    plan: Any,
    profile: Any,
    dest: Path,
    *,
    title: str,
    burn_srt: str | None,
    layout: str | None,
) -> str:
    """Render one profile from the footage plan. Empty string on success."""
    from navin.montage.footage import FootageError, render_footage

    srt: Path | None = None
    if burn_srt:
        srt = Path(burn_srt).expanduser()
        if not srt.is_absolute():
            srt = (root / srt).resolve()
        if not srt.is_file():
            srt = None
    wide = abs(profile.width / profile.height - plan.src_w / plan.src_h) < 0.05
    framed = (layout or "").strip().lower() == "framed" and not wide
    try:
        render_footage(
            plan,
            dest,
            width=profile.width,
            height=profile.height,
            layout="framed" if framed else "crop",
            title=title if framed else "",
            subtitles=srt,
        )
    except (FootageError, OSError) as exc:
        return f"polished {profile.id} failed ({exc}) - plain crop used"
    return ""


def package_social(
    root: Path,
    demo_path: str,
    *,
    title: str = "Product demo",
    burn_srt: str | None = None,
    profiles: str | None = None,
    layout: str | None = None,
    actions: str | None = None,
    footage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce platform-profile exports from a demo file (YouTube, IG, TikTok, …).

    When the recorder's ``actions.json`` sits next to the demo (or ``actions``
    names one), every profile is rendered as polished footage instead: camera
    follow, drawn cursor, dead-time trim, and a smart crop that follows the
    action for 9:16 / 1:1. ``layout="framed"`` puts the 16:9 footage inside a
    branded background (title above, captions below) for non-16:9 profiles.
    """
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

    footage_plan, footage_note = _prepare_polish(root, src, work, actions, footage)
    polished: dict[str, str] = {}
    exports: dict[str, str] = {}
    for profile in selected:
        dest = out_dir / f"{base}-{profile.id}.mp4"
        if footage_plan is not None:
            note = _render_polished_profile(
                root, footage_plan, profile, dest, title=title, burn_srt=burn_srt, layout=layout
            )
            if not note:
                exports[profile.id] = str(dest.relative_to(root)).replace("\\", "/")
                polished[profile.id] = exports[profile.id]
                continue
            footage_note = note
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
    actions_rel = ""
    if footage_plan is not None and polished:
        from navin.montage.footage import remapped_actions_payload

        actions_out = out_dir / f"{base}.actions.json"
        actions_out.write_text(
            json.dumps(remapped_actions_payload(footage_plan), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        actions_rel = str(actions_out.relative_to(root)).replace("\\", "/")
        removed = footage_plan.remap.removed
        variant_lines += [
            "",
            "Polished footage (camera follow, drawn cursor, smart crop): "
            + ", ".join(sorted(polished)),
            f"Dead time removed: {removed:.1f}s - remapped actions: `{actions_rel}`",
        ]
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
        result: dict[str, Any] = {
            "ok": True,
            "title": title,
            "source": demo_path,
            "exports": exports,
            "profiles": [p.id for p in selected],
            "brief": str(brief.relative_to(root)).replace("\\", "/"),
            "master": master_rel,
            "fix": "",
        }
        if polished:
            result["polished"] = sorted(polished)
            result["actions"] = actions_rel
        if footage_note:
            result["polish_note"] = footage_note
        return result

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
    if result.get("polished"):
        lines.append(
            "  polished: camera follow + cursor + dead-time trim "
            f"({', '.join(result['polished'])})"
        )
        if result.get("actions"):
            lines.append(f"  actions: {result['actions']}")
    if result.get("polish_note"):
        lines.append(f"  polish note: {result['polish_note']}")
    if result.get("partial"):
        lines.append("  status: partial (ratios present; ratio exports missing)")
    if result.get("fix"):
        lines.append(f"  note: {result['fix']}")
    else:
        lines.append("Ready to propose for social - never auto-publish.")
    return "\n".join(lines)
