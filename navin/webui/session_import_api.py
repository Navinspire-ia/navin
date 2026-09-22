# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebUI API for importing external chat sessions.

Backs /api/webui/sessions/import/scan (GET) and /api/webui/sessions/import
(POST). Both reuse navin.session.import_sessions so the CLI and the desktop
share one importer core.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from navin.session.import_sessions import (
    discover,
    import_to_workspace,
    load_extra_roots,
    remove_extra_root,
    resolve_sources,
    save_extra_root,
)


class SessionImportError(Exception):
    """Raised with an HTTP status and message for API errors."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


_IMPORT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-import")
_IMPORT_LOCK = Lock()
_IMPORT_JOBS: dict[str, tuple[tuple[Any, ...], Future]] = {}


def session_import_job(
    workspace: Path, *, job_id: str = "", source: str = "auto",
    overwrite: bool = False, limit: int = 0,
) -> dict[str, Any]:
    """Start once and poll with short requests, even for very large histories."""
    scope = str(workspace.expanduser().resolve())
    with _IMPORT_LOCK:
        if job_id:
            job = _IMPORT_JOBS.get(job_id)
            if job is None or job[0][0] != scope:
                raise SessionImportError(404, "Import job not found for this workspace")
            future = job[1]
        else:
            try:
                names = tuple(resolve_sources(source))
            except ValueError as exc:
                raise SessionImportError(400, str(exc)) from exc
            key = (scope, names, overwrite, limit)
            for existing_id, (existing_key, existing_future) in _IMPORT_JOBS.items():
                if existing_key == key and not existing_future.done():
                    return {"job_id": existing_id, "status": "running"}
                if existing_key[0] == scope and not existing_future.done():
                    raise SessionImportError(409, "An import is already running in this workspace")
            if sum(not item[1].done() for item in _IMPORT_JOBS.values()) >= 2:
                raise SessionImportError(409, "Other imports are running. Try again after they finish.")
            for old_id, (_, old_future) in list(_IMPORT_JOBS.items()):
                if len(_IMPORT_JOBS) < 16:
                    break
                if old_future.done():
                    del _IMPORT_JOBS[old_id]
            job_id = uuid4().hex
            future = _IMPORT_POOL.submit(
                run_external_session_import, workspace,
                source=source, overwrite=overwrite, limit=limit,
            )
            _IMPORT_JOBS[job_id] = (key, future)
    if not future.done():
        return {"job_id": job_id, "status": "running"}
    try:
        return {"job_id": job_id, "status": "done", "result": future.result()}
    except Exception as exc:
        raise SessionImportError(500, f"Session import failed: {exc}") from exc


def _stats_payload(report: Any) -> dict[str, Any]:
    return {
        "name": report.name,
        "label": report.label,
        "status": report.status,
        "discovered": len(report.sessions),
        "note": report.note,
    }


def scan_external_sessions() -> dict[str, Any]:
    """Detect external sources and count importable sessions."""
    reports = discover(resolve_sources("auto"))
    return {
        "sources": [_stats_payload(report) for report in reports],
        "saved_roots": load_extra_roots(),
    }


def add_saved_root(source: str, path: str) -> dict[str, Any]:
    """Persist one extra data root (e.g. a mounted Windows/macOS dir)."""
    try:
        save_extra_root(source, path)
    except ValueError as exc:
        raise SessionImportError(400, str(exc)) from exc
    return {"saved_roots": load_extra_roots()}


def delete_saved_root(source: str, path: str) -> dict[str, Any]:
    """Drop one saved extra root."""
    remove_extra_root(source, path)
    return {"saved_roots": load_extra_roots()}


def run_external_session_import(
    workspace: Path,
    *,
    source: str = "auto",
    overwrite: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    """Import detected sessions into *workspace* and return per-source stats."""
    try:
        names = resolve_sources(source)
    except ValueError as exc:
        raise SessionImportError(400, str(exc)) from exc
    reports = discover(names)
    stats = import_to_workspace(
        reports, workspace, overwrite=overwrite, limit=limit
    )
    return {
        "workspace": str(workspace.expanduser().resolve()),
        "sources": [
            {
                "name": stat.name,
                "label": stat.label,
                "status": stat.status,
                "discovered": stat.discovered,
                "imported": stat.imported,
                "skipped_existing": stat.skipped_existing,
                "note": stat.note,
            }
            for stat in stats
        ],
    }
