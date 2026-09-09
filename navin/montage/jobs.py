# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Durable, resumable Montage jobs backed by atomic JSON manifests.

A job is a manifest on disk plus, while it runs in this process, an entry in
the active registry that carries its cancel flag and progress sink. Handlers
fetch that entry with :func:`job_context` so a render can stream ffmpeg
progress into the manifest and stop when the user cancels, without the job
API having to know anything about ffmpeg.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.utils.atomic_io import InterProcessLock, atomic_write_text

StepHandler = Callable[[dict[str, Any], dict[str, Any]], Any | Awaitable[Any]]
NotifyFn = Callable[[dict[str, Any]], None]
_HANDLERS: dict[str, dict[str, StepHandler]] = {}
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TERMINAL = TERMINAL_STATUSES
#: Progress is persisted at most this often so a 2 h render does not rewrite
#: its manifest 400 times a minute; the final report is always written.
_PROGRESS_WRITE_INTERVAL_S = 0.5


class MontageJobError(ValueError):
    """Raised for malformed or unknown Montage jobs."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobContext:
    """Live handle a running step uses to report progress and notice cancels."""

    job_id: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _sink: Callable[[Mapping[str, Any]], None] | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def report_progress(self, progress: Mapping[str, Any]) -> None:
        """Persist a progress report (``fraction`` 0..1, free-form extras)."""
        if self._sink is not None:
            self._sink(progress)


@dataclass
class _ActiveJob:
    context: JobContext
    root: Path
    task: asyncio.Task[Any] | None = None
    notify: NotifyFn | None = None


_ACTIVE: dict[str, _ActiveJob] = {}


def job_context(job_id: str) -> JobContext | None:
    """The live context of *job_id* when it runs in this process, else ``None``."""
    active = _ACTIVE.get(job_id)
    return active.context if active is not None else None


def active_job_ids() -> list[str]:
    return sorted(_ACTIVE)


def is_job_active(job_id: str) -> bool:
    return job_id in _ACTIVE


def _notify(active: _ActiveJob | None, notify: NotifyFn | None, manifest: dict[str, Any]) -> None:
    for fn in (notify, active.notify if active is not None else None):
        if fn is None:
            continue
        try:
            fn(manifest)
        except Exception:  # noqa: BLE001 - a UI listener must never fail the job
            pass


def jobs_dir(root: Path | str) -> Path:
    return Path(root).expanduser().resolve() / WORKSPACE_MONTAGE_DIR / "jobs"


def _manifest_path(root: Path | str, job_id: str) -> Path:
    if not job_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in job_id):
        raise MontageJobError("invalid montage job id")
    return jobs_dir(root) / f"{job_id}.json"


def _write(path: Path, manifest: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        mode=0o600,
    )


def register_job_handler(operation: str, step: str, handler: StepHandler) -> None:
    """Register an in-process handler used when a durable job is resumed."""
    _HANDLERS.setdefault(operation, {})[step] = handler


def create_job(
    root: Path | str,
    operation: str,
    steps: Sequence[str | Mapping[str, Any]],
    *,
    payload: Mapping[str, Any] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Create a pending job manifest and return it."""
    if not operation.strip():
        raise MontageJobError("operation is required")
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(steps):
        row = {"name": raw} if isinstance(raw, str) else dict(raw)
        name = str(row.get("name") or "").strip()
        if not name:
            raise MontageJobError(f"step {position} needs a name")
        normalized.append(
            {
                "name": name,
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"name", "status", "attempts", "result", "error"}
                },
                "status": "pending",
                "attempts": 0,
                "latency_s": 0.0,
                "cost": 0.0,
                "result": None,
                "error": None,
            }
        )
    if not normalized:
        raise MontageJobError("at least one step is required")
    identifier = job_id or uuid.uuid4().hex
    path = _manifest_path(root, identifier)
    if path.exists():
        raise MontageJobError(f"montage job already exists: {identifier}")
    now = _now()
    manifest = {
        "version": 1,
        "id": identifier,
        "operation": operation.strip(),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "payload": dict(payload or {}),
        "steps": normalized,
        "latency_s": 0.0,
        "cost": 0.0,
        "error": None,
    }
    _write(path, manifest)
    return manifest


def get_job(root: Path | str, job_id: str) -> dict[str, Any]:
    path = _manifest_path(root, job_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MontageJobError(f"montage job not found: {job_id}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MontageJobError(f"montage job manifest is unreadable: {job_id}") from exc
    if not isinstance(data, dict) or data.get("id") != job_id:
        raise MontageJobError(f"invalid montage job manifest: {job_id}")
    return data


def list_jobs(
    root: Path | str,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List newest jobs, ignoring temporary or malformed files."""
    folder = jobs_dir(root)
    if not folder.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in folder.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if status and row.get("status") != status:
            continue
        rows.append(row)
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return rows[: max(1, min(int(limit), 500))]


class _JobCancelled(Exception):
    """Raised inside the step loop when the user stopped the job."""


async def run_job(
    root: Path | str,
    job_id: str,
    handlers: Mapping[str, StepHandler] | None = None,
    *,
    notify: NotifyFn | None = None,
) -> dict[str, Any]:
    """Run pending steps in order, safely skipping completed steps.

    ``notify`` is called with the manifest after every persisted change
    (status, progress), which is how the WebUI gets live updates.
    """
    path = _manifest_path(root, job_id)
    lock = InterProcessLock(path.with_suffix(".lock"), timeout=2.0)
    active = _ACTIVE.get(job_id)
    owns_registry = active is None
    if active is None:
        active = _ActiveJob(
            context=JobContext(job_id=job_id),
            root=Path(root).expanduser().resolve(),
            notify=None,
        )
        _ACTIVE[job_id] = active
    context = active.context
    try:
        with lock:
            manifest = get_job(root, job_id)
            if manifest.get("status") == "completed":
                return manifest
            operation = str(manifest.get("operation") or "")
            available = dict(_HANDLERS.get(operation, {}))
            available.update(dict(handlers or {}))
            manifest["status"] = "running"
            manifest["started_at"] = manifest.get("started_at") or _now()
            manifest["completed_at"] = None
            manifest["error"] = None
            manifest["cancel_requested"] = False
            last_progress_write = 0.0

            def persist(progress: Mapping[str, Any]) -> None:
                nonlocal last_progress_write
                report = dict(progress)
                report["updated_at"] = _now()
                manifest["progress"] = report
                now = time.monotonic()
                final = bool(report.get("done")) or report.get("fraction") in (1, 1.0)
                if not final and now - last_progress_write < _PROGRESS_WRITE_INTERVAL_S:
                    return
                last_progress_write = now
                manifest["updated_at"] = report["updated_at"]
                _write(path, manifest)
                _notify(active, notify, manifest)

            context._sink = persist
            for step in manifest.get("steps") or []:
                if step.get("status") in {"running", "cancelled"}:
                    step["status"] = "pending"
                if step.get("status") == "completed":
                    continue
                name = str(step.get("name") or "")
                handler = available.get(name)
                if handler is None:
                    manifest["status"] = "paused"
                    manifest["error"] = f"no resume handler registered for step: {name}"
                    manifest["updated_at"] = _now()
                    _write(path, manifest)
                    _notify(active, notify, manifest)
                    return manifest
                step["status"] = "running"
                step["attempts"] = int(step.get("attempts") or 0) + 1
                step["error"] = None
                manifest["progress"] = None
                manifest["updated_at"] = _now()
                _write(path, manifest)
                _notify(active, notify, manifest)
                started = time.monotonic()
                try:
                    if context.cancelled:
                        raise _JobCancelled()
                    result = handler(dict(manifest.get("payload") or {}), manifest)
                    if inspect.isawaitable(result):
                        result = await result
                    output = dict(result) if isinstance(result, Mapping) else {"value": result}
                    step["result"] = output
                    if output.get("cancelled") or (output.get("ok") is False and context.cancelled):
                        raise _JobCancelled()
                    if output.get("ok") is False:
                        raise MontageJobError(str(output.get("error") or "step failed"))
                    step["cost"] = float(output.get("cost") or output.get("cost_usd") or 0.0)
                    step["status"] = "completed"
                except _JobCancelled:
                    step["status"] = "cancelled"
                    step["error"] = "cancelled"
                    manifest["status"] = "cancelled"
                    manifest["error"] = None
                    manifest["cancel_requested"] = True
                    manifest["completed_at"] = _now()
                except Exception as exc:
                    step["status"] = "failed"
                    step["error"] = str(exc)
                    manifest["status"] = "failed"
                    manifest["error"] = f"{name}: {exc}"
                finally:
                    step["latency_s"] = round(
                        float(step.get("latency_s") or 0.0) + time.monotonic() - started, 3
                    )
                    manifest["latency_s"] = round(
                        sum(float(item.get("latency_s") or 0.0) for item in manifest["steps"]),
                        3,
                    )
                    manifest["cost"] = round(
                        sum(float(item.get("cost") or 0.0) for item in manifest["steps"]), 6
                    )
                    manifest["updated_at"] = _now()
                    _write(path, manifest)
                    _notify(active, notify, manifest)
                if manifest["status"] in {"failed", "cancelled"}:
                    return manifest
            manifest["status"] = "completed"
            manifest["completed_at"] = _now()
            manifest["updated_at"] = manifest["completed_at"]
            _write(path, manifest)
            _notify(active, notify, manifest)
            return manifest
    finally:
        context._sink = None
        if owns_registry:
            _ACTIVE.pop(job_id, None)


def start_job(
    root: Path | str,
    job_id: str,
    handlers: Mapping[str, StepHandler] | None = None,
    *,
    notify: NotifyFn | None = None,
) -> dict[str, Any]:
    """Run *job_id* in the background and return its manifest right away.

    The job keeps running after the HTTP request that started it returns;
    clients follow it through ``get_job`` polling or the ``notify`` hook. A
    job that is already running in this process is not started twice.
    """
    manifest = get_job(root, job_id)
    if job_id in _ACTIVE:
        return manifest
    if manifest.get("status") == "completed":
        return manifest
    active = _ActiveJob(
        context=JobContext(job_id=job_id),
        root=Path(root).expanduser().resolve(),
        notify=notify,
    )
    _ACTIVE[job_id] = active

    async def runner() -> dict[str, Any]:
        try:
            return await run_job(root, job_id, handlers)
        finally:
            _ACTIVE.pop(job_id, None)

    loop = asyncio.get_running_loop()
    active.task = loop.create_task(runner(), name=f"montage-job-{job_id}")
    return manifest


async def wait_for_job(job_id: str, timeout_s: float | None = None) -> dict[str, Any] | None:
    """Await a background job started here; ``None`` when it is not running."""
    active = _ACTIVE.get(job_id)
    if active is None or active.task is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(active.task), timeout=timeout_s)
    except TimeoutError:
        return None


def cancel_job(root: Path | str, job_id: str) -> dict[str, Any]:
    """Stop a running job, or mark a stale pending/running manifest cancelled.

    A job running in this process is asked to stop through its context: the
    ffmpeg child is killed and the runner writes the ``cancelled`` status
    itself (it holds the manifest lock). A manifest left ``running`` by a
    gateway that died is flipped directly.
    """
    manifest = get_job(root, job_id)
    active = _ACTIVE.get(job_id)
    if active is not None:
        active.context.cancel.set()
        manifest["cancel_requested"] = True
        return manifest
    if manifest.get("status") in TERMINAL_STATUSES:
        return manifest
    path = _manifest_path(root, job_id)
    with InterProcessLock(path.with_suffix(".lock"), timeout=2.0):
        manifest = get_job(root, job_id)
        if manifest.get("status") in TERMINAL_STATUSES:
            return manifest
        for step in manifest.get("steps") or []:
            if step.get("status") in {"pending", "running"}:
                step["status"] = "cancelled"
                step["error"] = "cancelled"
        manifest["status"] = "cancelled"
        manifest["cancel_requested"] = True
        manifest["error"] = None
        manifest["completed_at"] = _now()
        manifest["updated_at"] = manifest["completed_at"]
        _write(path, manifest)
    return manifest


def job_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The compact view broadcast to the UI: status, progress, output, error."""
    steps = manifest.get("steps") or []
    last = dict(steps[-1]) if steps and isinstance(steps[-1], Mapping) else {}
    result = last.get("result") if isinstance(last.get("result"), Mapping) else {}
    payload = manifest.get("payload") if isinstance(manifest.get("payload"), Mapping) else {}
    return {
        "id": manifest.get("id"),
        "operation": manifest.get("operation"),
        "status": manifest.get("status"),
        "progress": manifest.get("progress"),
        "error": manifest.get("error"),
        "output": result.get("output") or payload.get("output"),
        "timeline": payload.get("timeline"),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "completed_at": manifest.get("completed_at"),
        "latency_s": manifest.get("latency_s"),
        "cancel_requested": bool(manifest.get("cancel_requested")),
    }


async def resume_job(root: Path | str, job_id: str) -> dict[str, Any]:
    """Resume a job using handlers registered for its operation."""
    manifest = get_job(root, job_id)
    handlers: dict[str, StepHandler] = {}
    if manifest.get("operation") == "assemble":
        handlers["render"] = run_assemble_step
    elif manifest.get("operation") == "timeline-render":
        from navin.montage.timeline import run_timeline_render_step

        handlers["render"] = run_timeline_render_step
    elif manifest.get("operation") == "lipsync":
        handlers["generate"] = run_lipsync_step
    return await run_job(root, job_id, handlers)


def create_assemble_job(root: Path | str, spec: Any) -> dict[str, Any]:
    """Persist an assembly request in a form that can survive a restart."""
    return create_job(
        root,
        "assemble",
        ["render"],
        payload={"spec": asdict(spec)},
    )


def create_timeline_render_job(
    root: Path | str,
    timeline: str,
    spec: Any,
) -> dict[str, Any]:
    """Persist a saved-timeline render with restart-safe output arguments."""
    return create_job(
        root,
        "timeline-render",
        ["render"],
        payload={
            "root": str(Path(root).expanduser().resolve()),
            "timeline": timeline,
            "output": str(spec.output),
        },
    )


def render_hooks(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """``on_progress``/``cancel`` keyword arguments wired to the job's context.

    Handlers pass ``**render_hooks(manifest)`` to :func:`run_assemble` so a
    render started from the UI, the agent, or a resume all stream progress
    and honour cancellation the same way.
    """
    context = job_context(str(manifest.get("id") or ""))
    if context is None:
        return {}

    def on_progress(report: Any) -> None:
        data = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        context.report_progress(data)

    return {"on_progress": on_progress, "cancel": context.cancel}


async def run_assemble_step(payload: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    from navin.montage.assemble import AssembleSpec, VisualClip, run_assemble

    raw = dict(payload.get("spec") or {})
    raw["visuals"] = tuple(VisualClip(**item) for item in raw.get("visuals") or [])
    spec = AssembleSpec(**raw)
    return await run_assemble(spec, **render_hooks(manifest))


def create_lipsync_job(
    root: Path | str,
    *,
    video: str,
    audio: str,
    output: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Create or reuse the durable manifest for one lip-sync request."""
    payload = {
        "root": str(Path(root).expanduser().resolve()),
        "video": video,
        "audio": audio,
        "output": output,
        "model": model,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    try:
        return create_job(
            root,
            "lipsync",
            [{"name": "generate", "requires_credentials": True}],
            payload=payload,
            job_id=f"lipsync-{digest}",
        )
    except MontageJobError as exc:
        if "already exists" not in str(exc):
            raise
        existing = get_job(root, f"lipsync-{digest}")
        if existing.get("status") == "completed" and not Path(output).is_file():
            existing["status"] = "pending"
            existing["completed_at"] = None
            existing["error"] = None
            step = existing["steps"][0]
            step["status"] = "pending"
            step["result"] = None
            step["error"] = None
            existing["updated_at"] = _now()
            _write(_manifest_path(root, existing["id"]), existing)
        return existing


async def run_lipsync_step(
    payload: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Run the configured lip-sync provider and atomically publish its output."""
    import os

    from navin.config.loader import load_config
    from navin.montage.localize import assert_sync_gate_ok
    from navin.providers.lip_sync import LipSyncError, create_lip_sync_provider

    # Lip-sync re-animates the mouth over an audio track; if that track already
    # failed the dubbing sync gate, animating to it just re-encodes a drift.
    # Refuse it the same way dub_video does.
    assert_sync_gate_ok(payload["audio"])

    config = load_config().tools.montage.lip_sync
    api_key = config.api_key or os.environ.get("SYNC_API_KEY")
    provider = create_lip_sync_provider(
        config.provider,
        api_key=api_key,
        api_base=config.api_base,
        timeout_s=config.timeout_s,
        max_wait_s=config.max_wait_s,
        poll_interval_s=config.poll_interval_s,
    )
    root = Path(payload["root"])
    output = Path(payload["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = await provider.generate(
            video=Path(payload["video"]),
            audio=Path(payload["audio"]),
            model=str(payload.get("model") or config.model),
        )
    except LipSyncError:
        raise
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.write_bytes(result.video)
    temporary.replace(output)
    try:
        relative = str(output.relative_to(root)).replace("\\", "/")
    except ValueError:
        relative = str(output)
    return {
        "ok": True,
        "output": relative,
        "provider": result.provider,
        "model": result.model,
        "generation_id": result.generation_id,
        "mime": result.mime,
        "requires_credentials": True,
    }
