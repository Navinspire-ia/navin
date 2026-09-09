# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Format an editor buffer with the project's formatter.

VS Code and Cursor route Format Document to a real formatter (Prettier, ruff,
gofmt, rustfmt), not to the language server: the servers people actually run
(pyright, most of the Open VSX set) do not implement formatting at all. This
does the same. The buffer content travels with the request, so unsaved edits
format without being written to disk first; the caller decides what to do with
the result.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.proc import no_window_kwargs
from navin.webui.lsp_api import _resolve_rel

_TIMEOUT_S = 15.0

_PRETTIER_SUFFIXES = {
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".css",
    ".scss",
    ".less",
    ".json",
    ".jsonc",
    ".md",
    ".mdx",
    ".html",
    ".vue",
    ".yaml",
    ".yml",
    ".graphql",
}


class FormatApiError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _prettier_argv(root: Path) -> list[str] | None:
    """The project's own Prettier first, then a global one, then npx."""
    for name in ("prettier.cmd", "prettier"):
        local = root / "node_modules" / ".bin" / name
        if local.exists():
            return [str(local)]
    found = shutil.which("prettier")
    if found:
        return [found]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--no-install", "prettier"]
    return None


def _ruff_argv() -> list[str]:
    found = shutil.which("ruff")
    if found:
        return [found]
    # navin itself ships with ruff; the module entry point works even when the
    # console script is not on PATH (bundled installs).
    return [sys.executable, "-m", "ruff"]


def formatter_argv(rel: str, root: Path) -> tuple[list[str], str] | None:
    """Formatter command reading the buffer on stdin, or None if unsupported."""
    suffix = Path(rel).suffix.lower()
    if suffix in {".py", ".pyi"}:
        return [*_ruff_argv(), "format", "--stdin-filename", rel, "-"], "ruff"
    if suffix in _PRETTIER_SUFFIXES:
        prettier = _prettier_argv(root)
        if prettier is None:
            return None
        return [*prettier, "--stdin-filepath", rel], "prettier"
    if suffix == ".go":
        gofmt = shutil.which("gofmt")
        if gofmt is None:
            return None
        return [gofmt], "gofmt"
    if suffix == ".rs":
        rustfmt = shutil.which("rustfmt")
        if rustfmt is None:
            return None
        return [rustfmt, "--edition", "2021"], "rustfmt"
    return None


def format_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    content: str,
) -> dict[str, Any]:
    """Run the right formatter over *content* and return the result."""
    root = scope.project_path
    if not root.is_dir():
        raise FormatApiError(404, "project directory not found")
    rel = _resolve_rel(scope, path)

    picked = formatter_argv(rel, root)
    if picked is None:
        suffix = Path(rel).suffix or rel
        raise FormatApiError(422, f"no formatter available for {suffix}")
    argv, tool = picked

    try:
        completed = subprocess.run(
            argv,
            input=content.encode("utf-8"),
            capture_output=True,
            cwd=root,
            timeout=_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FormatApiError(503, f"{tool} timed out") from exc
    except OSError as exc:
        raise FormatApiError(503, f"{tool} failed to start: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        # Syntax errors are the common case: the formatter refuses the buffer
        # rather than reformatting broken code. Surface its own message.
        raise FormatApiError(422, detail[:500] or f"{tool} failed")

    formatted = completed.stdout.decode("utf-8", "replace")
    return {
        "path": rel,
        "tool": tool,
        "formatted": formatted,
        "changed": formatted != content,
    }
