# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebUI API for importing external chat sessions.

Backs /api/webui/sessions/import/scan (GET) and /api/webui/sessions/import
(POST). Both reuse navin.session.import_sessions so the CLI and the desktop
share one importer core.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
