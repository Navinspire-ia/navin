# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP helpers for the Code workbench DAP debugger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navin.dap.manager import get_debug_manager
from navin.dap.session import DebugSessionError
from navin.security.workspace_access import WorkspaceScope


def _session(scope: WorkspaceScope):
    root = Path(scope.project_path).expanduser()
    if not root.is_dir():
        raise DebugSessionError(f"project not found: {root}", status=404)
    return get_debug_manager().session_for(root)


def debug_dispatch(
    scope: WorkspaceScope,
    *,
    op: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one debug op and return a JSON-serialisable payload."""
    session = _session(scope)
    data = params if isinstance(params, dict) else {}
    action = (op or "state").strip().lower()

    if action in {"state", "status"}:
        return {"ok": True, **session.snapshot()}

    if action == "setbreakpoints":
        path = str(data.get("path") or "")
        lines_raw = data.get("lines") or []
        if isinstance(lines_raw, str):
            try:
                lines_raw = json.loads(lines_raw)
            except json.JSONDecodeError:
                lines_raw = [int(x) for x in lines_raw.split(",") if x.strip().isdigit()]
        lines = [int(x) for x in lines_raw if str(x).strip().lstrip("-").isdigit()]
        result = session.set_breakpoints(path, lines)
        return {"ok": True, **result, **session.snapshot()}

    if action == "start":
        args = data.get("args") or []
        if isinstance(args, str):
            args = [a for a in args.split(" ") if a]
        runtime_raw = data.get("runtime")
        runtime = str(runtime_raw).strip() if runtime_raw not in (None, "") else None
        result = session.start(
            program=str(data.get("program") or "") or None,
            pytest_node=str(data.get("pytest") or data.get("pytest_node") or "") or None,
            args=[str(a) for a in args] if isinstance(args, list) else None,
            runtime=runtime,
        )
        return {"ok": True, **result}

    if action == "stop":
        return {"ok": True, **session.stop()}

    if action == "continue":
        return {"ok": True, **session.continue_()}

    if action == "step":
        return {"ok": True, **session.step(str(data.get("kind") or "over"))}

    if action == "stack":
        return {"ok": True, **session.stack()}

    if action == "scopes":
        frame_id = int(data.get("frameId") or data.get("frame_id") or 0)
        return {"ok": True, **session.scopes(frame_id), **session.snapshot()}

    if action == "variables":
        ref = int(data.get("variablesReference") or data.get("ref") or 0)
        return {"ok": True, **session.variables(ref), **session.snapshot()}

    if action == "evaluate":
        frame = data.get("frameId") or data.get("frame_id")
        return {
            "ok": True,
            **session.evaluate(
                str(data.get("expression") or data.get("expr") or ""),
                frame_id=int(frame) if frame is not None else None,
            ),
        }

    raise DebugSessionError(f"unknown debug op: {action}", status=400)
