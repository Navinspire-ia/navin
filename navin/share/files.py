# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The shared project's files, for a guest allowed to download.

A guest picks one file (the PDF report, the spreadsheet, the deck...) instead
of the whole session. Only files inside the chat's project, never a secret
(the same rules as the session export: navin.session.bundle.is_secret_path),
never through a link that leaves the project, at most MAX_FILE_BYTES each.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from navin.session.bundle import is_secret_path

MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_LISTED = 5000
# Tooling and caches: never the work a guest came for.
SKIPPED_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next", ".nuxt", "dist", "build",
    ".cache", ".turbo", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target", ".idea", ".vscode",
    ".navin", ".gradle", "coverage",
})


def list_project_files(root: Path) -> dict[str, Any]:
    """``{"files": [{path, size, modified}], "truncated": bool}``, newest first."""
    files: list[dict[str, Any]] = []
    truncated = False
    root = root.resolve()
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith(".") or d in {".github"})
        for name in names:
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if is_secret_path(rel) or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append({"path": rel, "size": stat.st_size, "modified": stat.st_mtime})
            if len(files) >= MAX_LISTED:
                truncated = True
                break
        if truncated:
            break
    files.sort(key=lambda item: item["modified"], reverse=True)
    return {"files": files, "truncated": truncated}


def _shared_path(root: Path, rel: str) -> Path:
    """The project file a guest may read, or ValueError with a reason code."""
    if not rel or rel.startswith("/") or "\\" in rel or ".." in Path(rel).parts:
        raise ValueError("not_found")
    root = root.resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("not_found")
    if is_secret_path(path.relative_to(root).as_posix()) or any(part in SKIPPED_DIRS for part in Path(rel).parts):
        raise ValueError("not_allowed")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("too_large")
    return path


def read_project_file(root: Path, rel: str) -> tuple[bytes, str, str]:
    """(bytes, mime type, file name). Raises ValueError with a reason code."""
    path = _shared_path(root, rel)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path.read_bytes(), mime, path.name


# The guest viewer's own types: an Excel grid read here, an Office file
# rendered to PDF. Everything else previews from the file's own bytes.
SHEETS_MIME = "application/vnd.navin.sheets+json"
_SHEET_EXTS = frozenset({".xlsx", ".xlsm"})
_OFFICE_PDF_EXTS = frozenset({".docx", ".doc", ".odt", ".pptx", ".ppt", ".odp", ".xls", ".ods"})


def preview_project_file(root: Path, rel: str) -> tuple[bytes, str, str]:
    """What the guest viewer shows for a file: (bytes, mime type, file name).

    Excel workbooks come as a JSON grid (no LibreOffice needed), Word,
    PowerPoint and older spreadsheets as a PDF rendered by LibreOffice. A
    workbook openpyxl cannot read falls back to the PDF too. ValueError
    "no_preview" when this machine cannot render it: the guest downloads it.
    """
    from navin.webui.file_preview import (
        MAX_OFFICE_RENDER_BYTES,
        WebUIFilePreviewError,
        render_office_pdf,
        spreadsheet_sheets,
    )

    path = _shared_path(root, rel)
    suffix = path.suffix.lower()
    if suffix in _SHEET_EXTS:
        sheets = spreadsheet_sheets(path)
        if sheets is not None:
            body = json.dumps({"sheets": sheets}, ensure_ascii=False).encode("utf-8")
            return body, SHEETS_MIME, path.name
    if suffix in _OFFICE_PDF_EXTS or suffix in _SHEET_EXTS:
        if path.stat().st_size > MAX_OFFICE_RENDER_BYTES:
            raise ValueError("too_large")
        try:
            return render_office_pdf(path), "application/pdf", f"{path.stem}.pdf"
        except WebUIFilePreviewError as exc:
            raise ValueError("no_preview") from exc
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path.read_bytes(), mime, path.name
