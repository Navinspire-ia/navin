"""WebUI Montage studio status, assets listing, and HyperFrames setup.

Avoids OpenMontage-class pitfalls:
- readiness is proven (binaries run ``--version``, providers resolve a real key)
- no hardcoded model slugs for "ready" - catalog / config defaults only
- heavy deps (HyperFrames) stay lazy via ``run_setup``
- workspace paths are validated under the project root
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.montage.assemble import IMAGE_SUFFIXES, VIDEO_SUFFIXES, classify_visual
from navin.montage.detect import hyperframes_bin
from navin.montage.doctor import run_doctor
from navin.montage.install import ffmpeg_install_plan, remotion_bin
from navin.montage.packages import ALL_PACKAGES, packages_by_category
from navin.montage.probe import AUDIO_SUFFIXES, cached_media_info
from navin.montage.profiles import list_profiles
from navin.montage.stock import stock_status
from navin.providers.media_credentials import media_credentials_ready

NotifyFn = Callable[[dict[str, Any]], None]

_ASSET_DIRS = (
    ("demos", "demo"),
    ("exports", "export"),
    ("renders", "render"),
    ("captures", "capture"),
    ("creatives", "creative"),
    ("compositions", "composition"),
    ("localization", "localization"),
)

# The gallery classifies with the same suffix tables the assembler accepts, so
# every asset shown as image/video can actually be dropped on the timeline.
# Display kind and timeline kind differ for two formats: .svg previews as an
# image but is not a render input, and .gif previews as an image (an <img>
# plays it) while the assembler cuts it like a silent video clip.
_VIDEO_EXT = set(VIDEO_SUFFIXES) - {".gif"}
_IMAGE_EXT = set(IMAGE_SUFFIXES) | {".svg", ".gif"}
_AUDIO_EXT = set(AUDIO_SUFFIXES)
_DOC_EXT = {".md", ".markdown", ".html", ".htm", ".json", ".csv", ".srt", ".vtt", ".ass", ".txt"}

_MAX_ASSETS = 200


class MontageApiError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def list_montage_timelines(workspace_root: Path | str | None) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.timeline import list_timelines

    timelines = list_timelines(root)
    return {"count": len(timelines), "timelines": timelines}


def get_montage_timeline(
    workspace_root: Path | str | None, name: str
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.timeline import TimelineError, get_timeline

    try:
        return get_timeline(root, name)
    except TimelineError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise MontageApiError(str(exc), status=status) from exc


def put_montage_timeline(
    workspace_root: Path | str | None,
    name: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.timeline import TimelineError, put_timeline

    try:
        return put_timeline(root, name, document)
    except TimelineError as exc:
        raise MontageApiError(str(exc), status=400) from exc


def delete_montage_timeline(
    workspace_root: Path | str | None, name: str
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.timeline import TimelineError, delete_timeline

    try:
        return delete_timeline(root, name)
    except TimelineError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise MontageApiError(str(exc), status=status) from exc


async def preview_montage_timeline(
    workspace_root: Path | str | None, name: str
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.timeline import TimelineError, preview_timeline

    try:
        return await preview_timeline(root, name)
    except TimelineError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise MontageApiError(str(exc), status=status) from exc


async def render_montage_timeline(
    workspace_root: Path | str | None,
    name: str,
    *,
    output: str | None = None,
    notify: NotifyFn | None = None,
) -> dict[str, Any]:
    """Render a saved timeline and wait for the job to finish (``?wait=1``)."""
    root = _require_workspace(workspace_root)
    from navin.montage.timeline import TimelineError, render_timeline

    try:
        return await render_timeline(root, name, output=output, notify=notify)
    except TimelineError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise MontageApiError(str(exc), status=status) from exc


def start_montage_timeline_render(
    workspace_root: Path | str | None,
    name: str,
    *,
    output: str | None = None,
    notify: NotifyFn | None = None,
) -> dict[str, Any]:
    """Start rendering in the background; the manifest returned is followed via jobs."""
    root = _require_workspace(workspace_root)
    from navin.montage.jobs import MontageJobError
    from navin.montage.timeline import TimelineError, start_timeline_render

    try:
        return start_timeline_render(root, name, output=output, notify=notify)
    except TimelineError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise MontageApiError(str(exc), status=status) from exc
    except MontageJobError as exc:
        raise MontageApiError(str(exc), status=400) from exc


def list_montage_jobs(
    workspace_root: Path | str | None,
    *,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return durable Montage jobs for one workspace."""
    root = _require_workspace(workspace_root)
    from navin.montage.jobs import is_job_active, list_jobs

    jobs = list_jobs(root, status=status, limit=limit)
    for row in jobs:
        row["active"] = is_job_active(str(row.get("id") or ""))
    return {"count": len(jobs), "jobs": jobs}


def get_montage_job(workspace_root: Path | str | None, job_id: str) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.jobs import MontageJobError, get_job, is_job_active

    try:
        manifest = get_job(root, job_id)
    except MontageJobError as exc:
        raise MontageApiError(str(exc), status=404) from exc
    manifest["active"] = is_job_active(job_id)
    return manifest


async def resume_montage_job(
    workspace_root: Path | str | None,
    job_id: str,
    *,
    wait: bool = True,
    notify: NotifyFn | None = None,
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.montage.jobs import MontageJobError, get_job, resume_job, start_job

    try:
        if wait:
            return await resume_job(root, job_id)
        manifest = get_job(root, job_id)
        handlers = _resume_handlers(str(manifest.get("operation") or ""))
        return start_job(root, job_id, handlers, notify=notify)
    except MontageJobError as exc:
        raise MontageApiError(str(exc), status=404) from exc


def _resume_handlers(operation: str) -> dict[str, Any]:
    from navin.montage.jobs import run_assemble_step, run_lipsync_step

    if operation == "assemble":
        return {"render": run_assemble_step}
    if operation == "timeline-render":
        from navin.montage.timeline import run_timeline_render_step

        return {"render": run_timeline_render_step}
    if operation == "lipsync":
        return {"generate": run_lipsync_step}
    return {}


def cancel_montage_job(workspace_root: Path | str | None, job_id: str) -> dict[str, Any]:
    """Stop a running render (kills ffmpeg) or mark a stale job cancelled."""
    root = _require_workspace(workspace_root)
    from navin.montage.jobs import MontageJobError, cancel_job, is_job_active

    try:
        manifest = cancel_job(root, job_id)
    except MontageJobError as exc:
        raise MontageApiError(str(exc), status=404) from exc
    manifest["active"] = is_job_active(job_id)
    return manifest


async def probe_montage_media(
    workspace_root: Path | str | None, path: str | None
) -> dict[str, Any]:
    """Measure one workspace media file (duration, dimensions, fps, streams)."""
    root = _require_workspace(workspace_root)
    raw = (path or "").strip()
    if not raw:
        raise MontageApiError("path is required", status=400)
    candidate = Path(raw).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    if not _safe_under_root(root, target):
        raise MontageApiError("path must stay inside the workspace", status=400)
    if not target.is_file():
        raise MontageApiError(f"media file not found: {raw}", status=404)
    from navin.montage.probe import ProbeError, probe_media

    try:
        info = await probe_media(target)
    except ProbeError as exc:
        raise MontageApiError(str(exc), status=404) from exc
    info["path"] = target.resolve().relative_to(root.resolve()).as_posix()
    return info


def _require_workspace(workspace_root: Path | str | None) -> Path:
    if not workspace_root:
        raise MontageApiError("workspace root is required", status=400)
    root = Path(workspace_root).expanduser()
    if not root.is_dir():
        raise MontageApiError("workspace root not found", status=404)
    return root


def _managed_navin_active(config: Any) -> bool:
    plan = str(getattr(config.license, "plan", "") or "").strip().lower()
    return bool(
        getattr(config.license, "managed_api_key", None) and plan and plan not in {"", "free"}
    )


def _provider_ready(config: Any, provider_name: str) -> bool:
    name = (provider_name or "").strip().lower()
    if not name:
        return False
    if name == "navin" and _managed_navin_active(config):
        return True
    provider_config = getattr(config.providers, name, None)
    if name == "navin" and bool(getattr(provider_config, "api_key", None)):
        return True
    return media_credentials_ready(name, provider_config)


def _media_tool_status(
    config: Any,
    *,
    tool: str,
    provider: str,
    model: str,
    enabled: bool,
) -> dict[str, Any]:
    ready = bool(enabled) and _provider_ready(config, provider)
    return {
        "tool": tool,
        "provider": provider or "",
        "model": model or "",
        "enabled": bool(enabled),
        "ready": ready,
        "managed": (provider or "").strip().lower() == "navin" and _managed_navin_active(config),
    }


def media_readiness(config: Any) -> dict[str, Any]:
    """Real credential readiness for Montage AI tools (never 'key present' alone)."""
    img = config.tools.image_generation
    vid = config.tools.video_generation
    music = config.tools.music_generation
    transcription = config.transcription
    voice = config.voice

    # Match settings_api resolve_media_tool_enabled semantics for explicit flags.
    from navin.providers.media_credentials import resolve_media_tool_enabled

    img_enabled = resolve_media_tool_enabled(
        getattr(img, "enabled", None),
        _provider_ready(config, img.provider),
    )
    vid_enabled = resolve_media_tool_enabled(
        getattr(vid, "enabled", None),
        _provider_ready(config, vid.provider),
    )
    music_enabled = resolve_media_tool_enabled(
        getattr(music, "enabled", None),
        _provider_ready(config, music.provider),
    )
    stt_enabled = bool(getattr(transcription, "enabled", True))
    tts_enabled = True

    tools = {
        "image": _media_tool_status(
            config,
            tool="image",
            provider=img.provider,
            model=img.model,
            enabled=img_enabled,
        ),
        "video": _media_tool_status(
            config,
            tool="video",
            provider=vid.provider,
            model=vid.model,
            enabled=vid_enabled,
        ),
        "music": _media_tool_status(
            config,
            tool="music",
            provider=music.provider,
            model=music.model,
            enabled=music_enabled,
        ),
        "stt": _media_tool_status(
            config,
            tool="stt",
            provider=transcription.provider,
            model=transcription.model,
            enabled=stt_enabled,
        ),
        "tts": _media_tool_status(
            config,
            tool="tts",
            provider=voice.tts_provider,
            model=voice.tts_model,
            enabled=tts_enabled,
        ),
    }
    return {
        "plan": str(getattr(config.license, "plan", "") or "").strip().lower() or "free",
        "managed_key_active": _managed_navin_active(config),
        "tools": tools,
        "ready_for_ai": any(
            row["ready"] for row in tools.values() if row["tool"] in {"image", "video", "music"}
        ),
    }


def _kind_for_path(path: Path, bucket: str) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    if ext in _DOC_EXT:
        return "document"
    if bucket == "composition":
        return "document"
    return "file"


def _safe_under_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _timeline_visual_kind(path: Path) -> str | None:
    """``image``/``video`` when the assembler accepts the file, else ``None``."""
    try:
        return classify_visual(path)
    except ValueError:
        return None


def _asset_row(root: Path, path: Path, bucket: str, st: Any) -> dict[str, Any]:
    kind = _kind_for_path(path, bucket)
    row: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "kind": kind,
        "visual": _timeline_visual_kind(path) if kind in {"image", "video"} else None,
        "bucket": bucket,
        "size": st.st_size,
        "mtime": int(st.st_mtime),
    }
    if kind in {"video", "audio", "image"}:
        # Only what has already been measured: listing never spawns ffprobe.
        probed = cached_media_info(path)
        if probed:
            row["duration"] = probed.get("duration")
            row["width"] = probed.get("width")
            row["height"] = probed.get("height")
            row["fps"] = probed.get("fps")
            row["has_audio"] = probed.get("has_audio")
    return row


def list_montage_assets(workspace_root: Path | str | None) -> dict[str, Any]:
    """List montage artefacts under ``marketing/montage/`` (validated paths)."""
    root = _require_workspace(workspace_root)

    montage_dir = root / WORKSPACE_MONTAGE_DIR
    assets: list[dict[str, Any]] = []
    kit_path = montage_dir / "project-kit.md"
    if kit_path.is_file() and _safe_under_root(root, kit_path):
        assets.append(
            {
                "path": kit_path.relative_to(root).as_posix(),
                "name": kit_path.name,
                "kind": "document",
                "bucket": "kit",
                "size": kit_path.stat().st_size,
                "mtime": int(kit_path.stat().st_mtime),
            }
        )

    if montage_dir.is_dir():
        for report in sorted(montage_dir.glob("montage-report-*.html")):
            if not report.is_file() or not _safe_under_root(root, report):
                continue
            assets.append(
                {
                    "path": report.relative_to(root).as_posix(),
                    "name": report.name,
                    "kind": "document",
                    "bucket": "report",
                    "size": report.stat().st_size,
                    "mtime": int(report.stat().st_mtime),
                }
            )

    for dirname, bucket in _ASSET_DIRS:
        folder = montage_dir / dirname
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if not _safe_under_root(root, path):
                continue
            if path.name.startswith("."):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            assets.append(_asset_row(root, path, bucket, st))
            if len(assets) >= _MAX_ASSETS:
                break
        if len(assets) >= _MAX_ASSETS:
            break

    assets.sort(key=lambda row: row.get("mtime") or 0, reverse=True)
    return {
        "root": str(root),
        "montage_dir": WORKSPACE_MONTAGE_DIR,
        "exists": montage_dir.is_dir(),
        "count": len(assets),
        "assets": assets[:_MAX_ASSETS],
        "truncated": len(assets) >= _MAX_ASSETS,
    }


def packages_status(config: Any) -> dict[str, Any]:
    """Catalog + live readiness for builtin / system / lazy packages."""
    stock = stock_status(config, probe=False)
    ff_plan = ffmpeg_install_plan()
    hf = hyperframes_bin()
    remotion = remotion_bin()
    # find_chromium raises when nothing is found; this wrapper is the one that
    # answers None, which is what a status row needs.
    from navin.agent.tools.browser import _installed_chromium

    chromium = _installed_chromium()
    rows: list[dict[str, Any]] = []
    for package in ALL_PACKAGES:
        row = package.as_dict()
        # Stock clients stay builtin + env/config keys - never shown in Studio UI.
        ui_hidden = package.tier == "builtin" and package.id.startswith("stock-")
        if ui_hidden:
            provider = package.id.removeprefix("stock-")
            info = (stock.get("providers") or {}).get(provider) or {}
            row["installed"] = True
            row["ready"] = True
            row["detail"] = info.get("detail") or "builtin stock client"
            row["installable"] = False
            row["ui_hidden"] = True
            row["needs_action"] = False
            rows.append(row)
            continue
        if package.id == "ffmpeg":
            selected = ff_plan.get("selected") or {}
            row["installed"] = bool(ff_plan.get("present"))
            row["ready"] = bool(ff_plan.get("present"))
            if ff_plan.get("present"):
                row["detail"] = ff_plan.get("path") or "found"
            else:
                kind = selected.get("kind") or "manual"
                if kind == "user-local":
                    row["detail"] = "not on PATH · Install → ~/.navin/montage/bin"
                elif selected.get("runnable"):
                    row["detail"] = f"not on PATH · Install via {kind}"
                elif selected.get("manager_available"):
                    row["detail"] = (
                        f"not on PATH · Install via {kind} "
                        "(or user-local if sudo password required)"
                    )
                else:
                    row["detail"] = "not on PATH"
            # Always offer Install when missing if any recipe can run (incl. user-local).
            row["installable"] = (not bool(ff_plan.get("present"))) and bool(
                ff_plan.get("installable")
                or any(o.get("runnable") for o in (ff_plan.get("options") or []))
            )
            row["install_hint"] = selected.get("manual") or (
                (ff_plan.get("options") or [{}])[0].get("manual") if ff_plan.get("options") else ""
            )
        elif package.id == "chromium":
            row["installed"] = chromium is not None
            row["ready"] = chromium is not None
            row["detail"] = (
                str(chromium) if chromium else "not found · Install → Playwright Chromium (~150 MB)"
            )
            row["installable"] = chromium is None
            row["install_hint"] = "playwright install chromium"
        elif package.id == "hyperframes":
            row["installed"] = hf is not None
            row["ready"] = hf is not None
            row["detail"] = str(hf) if hf else "lazy npm under ~/.navin/montage"
            row["installable"] = True
        elif package.id == "remotion":
            row["installed"] = remotion is not None
            row["ready"] = remotion is not None
            row["detail"] = (
                str(remotion) if remotion else "optional lazy npm under ~/.navin/montage/remotion"
            )
            row["installable"] = True
        else:
            row["installed"] = False
            row["ready"] = False
            row["installable"] = False
        row["ui_hidden"] = False
        # Studio only lists packages that still need Install (or config).
        row["needs_action"] = (not bool(row.get("ready"))) and bool(
            row.get("installable") or package.tier in {"system", "lazy"}
        )
        rows.append(row)
    return {
        "categories": packages_by_category(),
        "items": rows,
        "stock": stock,
        "ffmpeg": {
            "present": bool(ff_plan.get("present")),
            "path": ff_plan.get("path"),
            "installable": (not bool(ff_plan.get("present"))) and bool(ff_plan.get("installable")),
            "which": ff_plan.get("path"),
            "selected": (ff_plan.get("selected") or {}).get("kind"),
        },
        "hyperframes": {"present": hf is not None, "path": str(hf) if hf else None},
        "remotion": {
            "present": remotion is not None,
            "path": str(remotion) if remotion else None,
            "optional": True,
        },
    }


def montage_status_payload(
    config: Any,
    *,
    workspace_root: Path | str | None = None,
) -> dict[str, Any]:
    """Doctor + media readiness + profiles for the Montage workbench."""
    report = run_doctor(config=config)
    media = media_readiness(config)
    packages = packages_status(config)
    assets_summary: dict[str, Any] | None = None
    if workspace_root:
        try:
            listed = list_montage_assets(workspace_root)
            assets_summary = {
                "exists": listed["exists"],
                "count": listed["count"],
                "montage_dir": listed["montage_dir"],
            }
        except MontageApiError:
            assets_summary = None

    return {
        "doctor": report.to_dict(),
        "ready_for_composition": report.ready_for_composition,
        "ready_for_ai": media["ready_for_ai"],
        "ready_for_package": any(
            c.name == "ffmpeg" and c.status != "missing" for c in report.checks
        ),
        "media": media,
        "packages": packages,
        "profiles": list_profiles(),
        "assets": assets_summary,
        "workspace": str(workspace_root) if workspace_root else None,
        "montage_home": report.toolchain.montage_home,
    }
