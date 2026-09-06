"""HTTP helpers for editor LSP: hover, completion, signature, code actions, rename."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.path import normalize_relative_path


class LspApiError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _resolve_rel(scope: WorkspaceScope, path: str | None) -> str:
    """Turn an absolute or relative editor path into a project-relative key."""
    cleaned = (path or "").strip()
    if not cleaned:
        raise LspApiError(400, "missing path")
    root = scope.project_path.resolve(strict=False)
    candidate = Path(cleaned).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(root).as_posix()
        except ValueError as exc:
            raise LspApiError(400, "path outside project") from exc
    rel = normalize_relative_path(cleaned)
    if not rel or ".." in Path(rel).parts:
        raise LspApiError(400, "invalid path")
    return rel


def _require_position(line: int | None, col: int | None) -> tuple[int, int]:
    try:
        line_n = int(line or 0)
        col_n = int(col or 0)
    except (TypeError, ValueError) as exc:
        raise LspApiError(400, "invalid line/col") from exc
    if line_n < 1 or col_n < 1:
        raise LspApiError(400, "line and col must be 1-based")
    return line_n, col_n


def _identifier_from_content(content: str, line: int, col: int) -> str:
    """Return the identifier under a one-based editor position."""
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return ""
    current = lines[line - 1]
    if not current:
        return ""
    index = max(0, min(col - 1, len(current) - 1))
    if not (current[index].isalnum() or current[index] == "_") and index > 0:
        index -= 1
    start = index
    end = index + 1
    while start > 0 and (current[start - 1].isalnum() or current[start - 1] == "_"):
        start -= 1
    while end < len(current) and (current[end].isalnum() or current[end] == "_"):
        end += 1
    return current[start:end] if start < end else ""


def hover_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    content: str | None = None,
) -> dict[str, Any]:
    """Hover text at a position: LSP first, index signature fallback."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)

    text = ""
    source = "none"
    try:
        from navin.lsp import LspManager

        manager = LspManager.for_root(root)
        kwargs = {"text": content} if content is not None else {}
        text = (manager.hover(rel, line_n, col_n, **kwargs) or "").strip()
        if text:
            source = "lsp"
    except Exception:
        text = ""

    if not text:
        text = _index_hover_fallback(root, rel, line_n, col_n)
        if text:
            source = "index"

    if text and _is_echo_hover(text, root, rel, line_n, col_n):
        text = ""
        source = "none"

    return {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "contents": text,
        "source": source,
    }


def definition_at_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    limit: int = 20,
    content: str | None = None,
) -> dict[str, Any]:
    """Go-to-definition at a caret position (LSP, then index by identifier)."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)
    capped = max(1, min(int(limit or 20), 50))

    items: list[dict[str, Any]] = []
    source = "none"
    try:
        from navin.lsp import LspManager

        manager = LspManager.for_root(root)
        kwargs = {"text": content} if content is not None else {}
        locations = manager.definition(rel, line_n, col_n, **kwargs)
        for loc in locations[:capped]:
            items.append(
                {
                    "name": loc.name or "",
                    "path": loc.path,
                    "line": loc.line,
                    "col": loc.col,
                    "kind": loc.kind or "",
                }
            )
        if items:
            source = "lsp"
    except Exception:
        items = []

    if not items:
        from navin.agent.tools.lsp import _identifier_at
        from navin.webui.symbols_api import goto_definition_payload

        symbol = (
            _identifier_from_content(content, line_n, col_n)
            if content is not None
            else _identifier_at(root / rel, line_n, col_n)
        )
        if symbol:
            fallback = goto_definition_payload(
                scope, symbol=symbol, path=rel, limit=capped
            )
            items = list(fallback.get("items") or [])
            if items:
                source = "index"

    return {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "items": items,
        "total": len(items),
        "source": source,
    }


def references_at_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    limit: int = 60,
    content: str | None = None,
) -> dict[str, Any]:
    """Find references at a caret position (LSP, then index by identifier)."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)
    capped = max(1, min(int(limit or 60), 120))

    from navin.agent.tools.lsp import _identifier_at

    symbol = (
        _identifier_from_content(content, line_n, col_n)
        if content is not None
        else _identifier_at(root / rel, line_n, col_n)
    )
    items: list[dict[str, Any]] = []
    source = "none"
    try:
        from navin.lsp import LspManager

        manager = LspManager.for_root(root)
        kwargs = {"text": content} if content is not None else {}
        locations = manager.references(rel, line_n, col_n, **kwargs)
        for loc in locations[:capped]:
            items.append(
                {
                    "name": loc.name or symbol or "",
                    "path": loc.path,
                    "line": loc.line,
                    "col": loc.col,
                    "kind": loc.kind or "",
                }
            )
        if items:
            source = "lsp"
    except Exception:
        items = []

    if not items and symbol:
        try:
            from navin.index import get_index

            index = get_index(root)
            index.ensure()
            locations, _total = index.references(symbol, limit=capped)
            for loc in locations[:capped]:
                items.append(
                    {
                        "name": symbol,
                        "path": loc.path,
                        "line": loc.line,
                        "col": getattr(loc, "col", 1) or 1,
                        "kind": getattr(loc, "kind", "") or "",
                    }
                )
            if items:
                source = "index"
        except Exception:
            items = []

    return {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "symbol": symbol,
        "items": items,
        "total": len(items),
        "source": source,
    }


def rename_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    new_name: str,
    apply: bool = False,
    content: str | None = None,
) -> dict[str, Any]:
    """Preview or apply a symbol rename via the language server + index guard."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)
    target = (new_name or "").strip()
    if not target:
        raise LspApiError(400, "missing new_name")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
        raise LspApiError(400, "invalid new_name")
    if apply and content is not None:
        try:
            saved = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LspApiError(409, "save the current file before applying rename") from exc
        if saved != content:
            raise LspApiError(
                409,
                "save the current file before applying a workspace rename",
            )

    from navin.agent.tools.lsp import LspTool, _identifier_at
    from navin.lsp import LspManager
    from navin.lsp.client import LspError

    symbol = (
        _identifier_from_content(content, line_n, col_n)
        if content is not None
        else _identifier_at(root / rel, line_n, col_n)
    )
    if not symbol:
        raise LspApiError(400, "no identifier at position")
    if symbol == target:
        return {
            "path": rel,
            "line": line_n,
            "col": col_n,
            "symbol": symbol,
            "new_name": target,
            "edits": {},
            "files": [],
            "total": 0,
            "applied": False,
            "unseen": [],
            "message": "new name matches current symbol",
        }

    tool = LspTool(workspace=str(root), restrict_to_workspace=True)
    manager = LspManager.for_root(root)
    try:
        edits, unseen, server = tool._best_rename(
            manager, root, rel, line_n, col_n, target, symbol, content
        )
    except LspError as exc:
        raise LspApiError(503, str(exc) or "language server unavailable") from exc
    except Exception as exc:
        raise LspApiError(503, f"rename failed: {exc}") from exc

    if not edits:
        raise LspApiError(
            404,
            f"no renameable symbol at {rel}:{line_n}:{col_n}",
        )

    total = sum(len(rows) for rows in edits.values())
    files = sorted(edits)
    payload: dict[str, Any] = {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "symbol": symbol,
        "new_name": target,
        "server": server or "",
        "edits": {
            file_path: [
                {
                    "line": int(row["line"]),
                    "col": int(row["col"]),
                    "end_line": int(row["end_line"]),
                    "end_col": int(row["end_col"]),
                    "new_text": str(row["new_text"]),
                }
                for row in rows
            ]
            for file_path, rows in edits.items()
        },
        "files": files,
        "total": total,
        "unseen": list(unseen),
        "applied": False,
        "message": "",
    }

    if not apply:
        payload["message"] = (
            f"Preview: {total} edit(s) in {len(files)} file(s). "
            "Re-run with apply=true to write."
        )
        return payload

    if unseen:
        raise LspApiError(
            409,
            "rename incomplete: index found uses the server missed "
            f"({', '.join(unseen[:6])}). Preview only; fix remaining sites first.",
        )

    written, problem = tool._apply_rename(edits)
    if problem:
        raise LspApiError(409, problem)
    payload["applied"] = True
    payload["written"] = [p.as_posix() if hasattr(p, "as_posix") else str(p) for p in written]
    # Prefer project-relative paths for the UI.
    rel_written: list[str] = []
    for absolute in written:
        try:
            rel_written.append(Path(absolute).resolve(strict=False).relative_to(root).as_posix())
        except Exception:
            rel_written.append(str(absolute))
    payload["written"] = rel_written
    payload["message"] = f"Applied {total} edit(s) across {len(rel_written)} file(s)."
    return payload


def completion_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    content: str | None = None,
    trigger: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    """Completion items at a caret. Empty list when no server answers."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)
    capped = max(1, min(int(limit or 80), 120))
    items: list[dict[str, Any]] = []
    source = "none"
    try:
        from navin.lsp import LspManager

        manager = LspManager.for_root(root)
        kwargs: dict[str, Any] = {}
        if content is not None:
            kwargs["text"] = content
        if trigger:
            kwargs["trigger"] = trigger
        items = list(manager.completion(rel, line_n, col_n, **kwargs)[:capped])
        if items:
            source = "lsp"
    except Exception:
        items = []
    return {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "items": items,
        "total": len(items),
        "source": source,
    }


def signature_help_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    content: str | None = None,
    trigger: str | None = None,
) -> dict[str, Any]:
    """Parameter hints at a caret. Empty signatures when no server answers."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)
    help_doc: dict[str, Any] = {}
    source = "none"
    try:
        from navin.lsp import LspManager

        manager = LspManager.for_root(root)
        kwargs: dict[str, Any] = {}
        if content is not None:
            kwargs["text"] = content
        if trigger:
            kwargs["trigger"] = trigger
        help_doc = manager.signature_help(rel, line_n, col_n, **kwargs) or {}
        if help_doc.get("signatures"):
            source = "lsp"
    except Exception:
        help_doc = {}
    return {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "source": source,
        "active_signature": int(help_doc.get("active_signature") or 0),
        "active_parameter": int(help_doc.get("active_parameter") or 0),
        "signatures": list(help_doc.get("signatures") or []),
    }


def code_actions_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    line: int,
    col: int,
    end_line: int | None = None,
    end_col: int | None = None,
    content: str | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """Quick-fixes / refactors for a caret or selection."""
    root = scope.project_path
    if not root.is_dir():
        raise LspApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)
    line_n, col_n = _require_position(line, col)
    finish_line, finish_col = line_n, col_n
    if end_line is not None or end_col is not None:
        finish_line, finish_col = _require_position(end_line or line_n, end_col or col_n)
    capped = max(1, min(int(limit or 40), 80))
    items: list[dict[str, Any]] = []
    source = "none"
    try:
        from navin.lsp import LspManager

        manager = LspManager.for_root(root)
        kwargs: dict[str, Any] = {
            "end_line": finish_line,
            "end_col": finish_col,
        }
        if content is not None:
            kwargs["text"] = content
        items = list(manager.code_actions(rel, line_n, col_n, **kwargs)[:capped])
        if items:
            source = "lsp"
    except Exception:
        items = []
    return {
        "path": rel,
        "line": line_n,
        "col": col_n,
        "end_line": finish_line,
        "end_col": finish_col,
        "items": items,
        "total": len(items),
        "source": source,
    }


def _is_echo_hover(text: str, root: Path, rel: str, line: int, col: int) -> bool:
    """True when the hover only repeats the identifier already on the line."""
    try:
        from navin.agent.tools.lsp import _identifier_at

        ident = _identifier_at(root / rel, line, col)
    except Exception:
        return False
    if not ident:
        return False
    body = text.strip()
    if body.startswith("```") and body.endswith("```"):
        inner = body.strip("`")
        lines = [part.strip() for part in inner.splitlines() if part.strip()]
        if lines and lines[0].isalnum():
            lines = lines[1:]
        body = "\n".join(lines).strip()
    body = body.strip("`").strip()
    if body.startswith("**") and body.endswith("**") and len(body) >= 4:
        body = body[2:-2].strip()
    return body == ident


def _index_hover_fallback(root: Path, rel: str, line: int, col: int) -> str:
    """Best-effort hover from the code index when no language server answers."""
    try:
        from navin.agent.tools.lsp import _identifier_at
        from navin.index import get_index

        symbol = _identifier_at(root / rel, line, col)
        if not symbol:
            return ""
        index = get_index(root)
        index.ensure()
        locations = index.definition(symbol)
        # Echoing the word already on the line is not hover info: the editor
        # would paint a grey tooltip that just repeats `requests` under every
        # YAML key. Stay silent until there is a definition to point at.
        if not locations:
            return ""
        loc = locations[0]
        kind = getattr(loc, "kind", "") or ""
        signature = getattr(loc, "signature", "") or ""
        parts = [symbol]
        if kind:
            parts.append(f"({kind})")
        head = " ".join(parts)
        lines = [head, f"{loc.path}:{loc.line}"]
        if signature:
            lines.append(str(signature))
        return "\n".join(lines)
    except Exception:
        return ""
