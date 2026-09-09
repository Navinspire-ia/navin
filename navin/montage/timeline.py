"""Versioned Montage timelines with workspace-safe persistence and rendering."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.montage.assemble import (
    AssembleError,
    AssembleSpec,
    VisualClip,
    classify_visual,
    run_assemble,
    validate_spec,
)
from navin.montage.ffmpeg_runner import run_process
from navin.utils.atomic_io import InterProcessLock, atomic_write_text

TIMELINE_SCHEMA_VERSION = 1
TIMELINES_DIR = f"{WORKSPACE_MONTAGE_DIR}/timelines"
TIMELINE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://navin.ai/schemas/montage-timeline-v1.json",
    "title": "Navin Montage Timeline",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "name",
        "visuals",
        "output",
        "music",
        "voice",
        "subtitles",
        "width",
        "height",
        "fps",
        "music_gain_db",
        "transition",
        "transition_duration",
        "extra_metadata",
    ],
    "properties": {
        "schema_version": {"const": TIMELINE_SCHEMA_VERSION},
        "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"},
        "visuals": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "kind", "duration", "start", "end"],
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "kind": {"enum": ["image", "video"]},
                    "duration": {"type": ["number", "null"]},
                    "start": {"type": ["number", "null"]},
                    "end": {"type": ["number", "null"]},
                },
            },
        },
        "output": {"type": "string", "minLength": 1},
        "music": {"type": ["string", "null"]},
        "voice": {"type": ["string", "null"]},
        "subtitles": {"type": ["string", "null"]},
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1},
        "fps": {"type": "integer", "minimum": 1, "maximum": 120},
        "music_gain_db": {"type": "number", "minimum": -60, "maximum": 12},
        "transition": {"type": "string"},
        "transition_duration": {"type": "number"},
        "extra_metadata": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOP_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "visuals",
        "output",
        "music",
        "voice",
        "subtitles",
        "width",
        "height",
        "fps",
        "music_gain_db",
        "transition",
        "transition_duration",
        "extra_metadata",
    }
)
_VISUAL_KEYS = frozenset({"path", "kind", "duration", "start", "end"})


class TimelineError(ValueError):
    """Raised when a timeline is unsafe, malformed, or unavailable."""


def _root(workspace_root: Path | str) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise TimelineError("workspace root not found")
    return root


def validate_name(name: str) -> str:
    value = str(name or "").strip()
    if not _NAME_RE.fullmatch(value):
        raise TimelineError(
            "invalid timeline name: use 1-64 lowercase letters, numbers, underscores, or hyphens"
        )
    return value


def timelines_dir(workspace_root: Path | str) -> Path:
    return _root(workspace_root) / TIMELINES_DIR


def _timeline_path(workspace_root: Path | str, name: str) -> Path:
    return timelines_dir(workspace_root) / f"{validate_name(name)}.json"


def _safe_path(root: Path, value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TimelineError(f"{field} must be a non-empty path")
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise TimelineError(f"{field} must stay inside the workspace") from exc
    if relative == Path("."):
        raise TimelineError(f"{field} must refer to a file")
    return relative.as_posix()


def _number(
    value: Any,
    field: str,
    *,
    optional: bool = False,
) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimelineError(f"{field} must be a number")
    number = float(value)
    if not number == number or number in (float("inf"), float("-inf")):
        raise TimelineError(f"{field} must be finite")
    return number


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TimelineError(f"{field} must be an integer")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TimelineError(f"{field} has unknown fields: {', '.join(unknown)}")


def _require_fields(data: Mapping[str, Any], required: frozenset[str], field: str) -> None:
    missing = sorted(required - set(data))
    if missing:
        raise TimelineError(f"{field} is missing fields: {', '.join(missing)}")


def validate_timeline(
    document: Mapping[str, Any],
    workspace_root: Path | str,
    *,
    expected_name: str | None = None,
) -> dict[str, Any]:
    """Strictly validate and return the canonical version 1 JSON document."""
    if not isinstance(document, Mapping):
        raise TimelineError("timeline must be a JSON object")
    _reject_unknown(document, _TOP_KEYS, "timeline")
    _require_fields(document, _TOP_KEYS, "timeline")
    if document.get("schema_version") != TIMELINE_SCHEMA_VERSION:
        raise TimelineError(f"schema_version must be {TIMELINE_SCHEMA_VERSION}")
    root = _root(workspace_root)
    name = validate_name(document.get("name", ""))
    if expected_name is not None and name != validate_name(expected_name):
        raise TimelineError("timeline name does not match the route")
    raw_visuals = document.get("visuals")
    if not isinstance(raw_visuals, list) or not raw_visuals:
        raise TimelineError("visuals must be a non-empty array")
    visuals: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_visuals):
        field = f"visuals[{index}]"
        if not isinstance(raw, Mapping):
            raise TimelineError(f"{field} must be an object")
        _reject_unknown(raw, _VISUAL_KEYS, field)
        _require_fields(raw, _VISUAL_KEYS, field)
        path = _safe_path(root, raw.get("path"), f"{field}.path", required=True)
        kind = raw.get("kind")
        if kind not in {"image", "video"}:
            raise TimelineError(f"{field}.kind must be image or video")
        try:
            detected = classify_visual(path or "")
        except AssembleError as exc:
            raise TimelineError(str(exc)) from exc
        if detected != kind:
            raise TimelineError(f"{field}.kind does not match its file extension")
        visuals.append(
            {
                "path": path,
                "kind": kind,
                "duration": _number(raw.get("duration"), f"{field}.duration", optional=True),
                "start": _number(raw.get("start"), f"{field}.start", optional=True),
                "end": _number(raw.get("end"), f"{field}.end", optional=True),
            }
        )
    metadata = document.get("extra_metadata", {})
    if not isinstance(metadata, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise TimelineError("extra_metadata must contain only string keys and values")
    canonical = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "name": name,
        "visuals": visuals,
        "output": _safe_path(root, document.get("output"), "output", required=True),
        "music": _safe_path(root, document.get("music"), "music"),
        "voice": _safe_path(root, document.get("voice"), "voice"),
        "subtitles": _safe_path(root, document.get("subtitles"), "subtitles"),
        "width": _integer(document.get("width"), "width"),
        "height": _integer(document.get("height"), "height"),
        "fps": _integer(document.get("fps"), "fps"),
        "music_gain_db": _number(document.get("music_gain_db"), "music_gain_db"),
        "transition": document.get("transition"),
        "transition_duration": _number(
            document.get("transition_duration"), "transition_duration"
        ),
        "extra_metadata": dict(metadata),
    }
    if not isinstance(canonical["transition"], str):
        raise TimelineError("transition must be a string")
    try:
        validate_spec(timeline_to_spec(canonical, root, validate=False))
    except AssembleError as exc:
        raise TimelineError(str(exc)) from exc
    return canonical


def timeline_from_spec(
    spec: AssembleSpec,
    workspace_root: Path | str,
    *,
    name: str,
) -> dict[str, Any]:
    """Convert an AssembleSpec to canonical, portable timeline JSON."""
    root = _root(workspace_root)
    raw = asdict(spec)
    raw["visuals"] = list(raw["visuals"])
    raw["schema_version"] = TIMELINE_SCHEMA_VERSION
    raw["name"] = validate_name(name)
    return validate_timeline(raw, root)


def timeline_to_spec(
    document: Mapping[str, Any],
    workspace_root: Path | str,
    *,
    validate: bool = True,
    output: str | Path | None = None,
) -> AssembleSpec:
    """Convert timeline JSON to AssembleSpec without dropping any assemble field."""
    root = _root(workspace_root)
    data = validate_timeline(document, root) if validate else dict(document)

    def absolute(value: str | None) -> str | None:
        return str((root / value).resolve(strict=False)) if value else None

    output_value = (
        _safe_path(root, str(output), "output", required=True)
        if output is not None
        else data["output"]
    )
    return AssembleSpec(
        visuals=tuple(
            VisualClip(
                path=absolute(item["path"]) or "",
                kind=item["kind"],
                duration=item["duration"],
                start=item["start"],
                end=item["end"],
            )
            for item in data["visuals"]
        ),
        output=absolute(output_value) or "",
        music=absolute(data.get("music")),
        voice=absolute(data.get("voice")),
        subtitles=absolute(data.get("subtitles")),
        width=data["width"],
        height=data["height"],
        fps=data["fps"],
        music_gain_db=data["music_gain_db"],
        transition=data["transition"],
        transition_duration=data["transition_duration"],
        extra_metadata=dict(data["extra_metadata"]),
    )


def put_timeline(
    workspace_root: Path | str,
    name: str,
    document: Mapping[str, Any],
) -> dict[str, Any]:
    path = _timeline_path(workspace_root, name)
    canonical = validate_timeline(document, workspace_root, expected_name=name)
    with InterProcessLock(path.with_suffix(".lock"), timeout=2.0):
        atomic_write_text(
            path,
            json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
    return canonical


def get_timeline(workspace_root: Path | str, name: str) -> dict[str, Any]:
    path = _timeline_path(workspace_root, name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TimelineError(f"timeline not found: {name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TimelineError(f"timeline is unreadable: {name}") from exc
    return validate_timeline(raw, workspace_root, expected_name=name)


def list_timelines(workspace_root: Path | str) -> list[dict[str, Any]]:
    folder = timelines_dir(workspace_root)
    if not folder.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            timeline = get_timeline(workspace_root, path.stem)
            stat = path.stat()
        except (OSError, TimelineError):
            continue
        rows.append(
            {
                "name": timeline["name"],
                "schema_version": timeline["schema_version"],
                "visuals": len(timeline["visuals"]),
                "output": timeline["output"],
                "mtime": int(stat.st_mtime),
            }
        )
    rows.sort(key=lambda row: (-row["mtime"], row["name"]))
    return rows


def delete_timeline(workspace_root: Path | str, name: str) -> dict[str, Any]:
    path = _timeline_path(workspace_root, name)
    with InterProcessLock(path.with_suffix(".lock"), timeout=2.0):
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise TimelineError(f"timeline not found: {name}") from exc
    return {"ok": True, "name": validate_name(name)}


async def preview_timeline(workspace_root: Path | str, name: str) -> dict[str, Any]:
    """Render a real JPEG thumbnail from the first visual using FFmpeg."""
    root = _root(workspace_root)
    timeline = get_timeline(root, name)
    import asyncio

    from navin.montage import detect

    ffmpeg = await asyncio.to_thread(detect.find_ffmpeg)
    if not ffmpeg:
        raise TimelineError("ffmpeg is not installed: run montage setup")
    first = timeline["visuals"][0]
    source = root / first["path"]
    if not source.is_file():
        raise TimelineError(f"preview source not found: {first['path']}")
    output = root / WORKSPACE_MONTAGE_DIR / "previews" / f"{validate_name(name)}.jpg"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.part.jpg")
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if first["kind"] == "video" and first["start"]:
        args += ["-ss", str(first["start"])]
    args += [
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale='min(960,iw)':-2",
        str(temporary),
    ]
    result = await run_process(args, timeout_s=60.0)
    if not result.ok:
        temporary.unlink(missing_ok=True)
        raise TimelineError(result.stderr or "ffmpeg preview failed")
    os.replace(temporary, output)
    return {
        "ok": True,
        "name": timeline["name"],
        "path": output.relative_to(root).as_posix(),
        "mime": "image/jpeg",
        "size_bytes": output.stat().st_size,
    }


def create_render_job(
    workspace_root: Path | str,
    name: str,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the timeline and persist a pending ``timeline-render`` job."""
    root = _root(workspace_root)
    timeline = get_timeline(root, name)
    spec = timeline_to_spec(timeline, root, output=output)
    from navin.montage.jobs import create_timeline_render_job

    return create_timeline_render_job(root, timeline["name"], spec)


async def render_timeline(
    workspace_root: Path | str,
    name: str,
    *,
    output: str | Path | None = None,
    notify: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Create and run (to completion) a durable assembly job for a saved timeline."""
    root = _root(workspace_root)
    job = create_render_job(root, name, output=output)
    from navin.montage.jobs import run_job

    return await run_job(root, job["id"], {"render": run_timeline_render_step}, notify=notify)


def start_timeline_render(
    workspace_root: Path | str,
    name: str,
    *,
    output: str | Path | None = None,
    notify: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Create the render job and run it in the background; returns the manifest.

    The UI follows the job through ``get_job`` / live ``notify`` updates and
    can cancel it, instead of holding one HTTP request open for the whole
    encode.
    """
    root = _root(workspace_root)
    job = create_render_job(root, name, output=output)
    from navin.montage.jobs import start_job

    return start_job(root, job["id"], {"render": run_timeline_render_step}, notify=notify)


async def run_timeline_render_step(
    payload: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    from navin.montage.jobs import render_hooks

    root = _root(payload["root"])
    timeline = get_timeline(root, str(payload["timeline"]))
    spec = timeline_to_spec(timeline, root, output=payload.get("output"))
    return await run_assemble(spec, **render_hooks(manifest))


def default_timeline(name: str, spec: AssembleSpec, workspace_root: Path | str) -> dict[str, Any]:
    """Compatibility helper for callers that need an explicit canonical document."""
    return timeline_from_spec(spec, workspace_root, name=name)


def import_media(
    workspace_root: Path | str,
    source: str | Path,
    *,
    bucket: str = "creatives",
) -> str:
    """Bring a media file into the project so a timeline can reference it.

    Timelines are portable documents with workspace-relative paths, so a clip
    the agent generated in the shared media directory has to be copied under
    ``marketing/montage/<bucket>/`` first. Files already inside the workspace
    are left where they are. Returns the workspace-relative POSIX path.
    """
    import hashlib
    import shutil

    root = _root(workspace_root)
    origin = Path(source).expanduser()
    if not origin.is_file():
        raise TimelineError(f"media file not found: {source}")
    resolved = origin.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        pass
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", bucket):
        raise TimelineError("invalid import bucket")
    folder = root / WORKSPACE_MONTAGE_DIR / bucket
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / origin.name
    if destination.exists():
        with resolved.open("rb") as source_file:
            digest = hashlib.file_digest(source_file, "sha256").hexdigest()
        if destination.is_file() and destination.stat().st_size == resolved.stat().st_size:
            with destination.open("rb") as imported_file:
                if hashlib.file_digest(imported_file, "sha256").hexdigest() == digest:
                    return destination.relative_to(root).as_posix()
        destination = folder / f"{origin.stem}-{digest[:16]}{origin.suffix}"
    temporary = destination.with_name(f".{destination.name}.part")
    shutil.copy2(resolved, temporary)
    os.replace(temporary, destination)
    return destination.relative_to(root).as_posix()


__all__ = [
    "TIMELINE_JSON_SCHEMA",
    "TIMELINE_SCHEMA_VERSION",
    "TIMELINES_DIR",
    "TimelineError",
    "create_render_job",
    "delete_timeline",
    "get_timeline",
    "import_media",
    "list_timelines",
    "preview_timeline",
    "put_timeline",
    "render_timeline",
    "start_timeline_render",
    "timeline_from_spec",
    "timeline_to_spec",
    "validate_name",
    "validate_timeline",
]
