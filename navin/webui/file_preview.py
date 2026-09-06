"""Workspace-scoped source preview payloads for the WebUI."""

from __future__ import annotations

import base64
import datetime
import mimetypes
import os
import re
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from navin.config.secrets import RUNTIME_CONFIG_DENIED, is_runtime_secret_path
from navin.security.workspace_access import WorkspaceScope
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from navin.utils import wsl

MAX_FILE_PREVIEW_BYTES = 384 * 1024
MAX_IMAGE_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_FILE_DOWNLOAD_BYTES = 64 * 1024 * 1024
# Saves travel as chunked base64 request headers (the gateway's HTTP layer has
# no request bodies), so the cap must stay under the header-count limit raised
# in navin.channels.websocket (640 headers) and under the Vite dev proxy's
# --max-http-header-size. 2 MB of UTF-8 is ~467 chunks of 6000 base64 chars.
MAX_FILE_SAVE_BYTES = 2 * 1024 * 1024
_FILE_BODY_HEADER_PREFIX = "x-navin-file-body-"
_MAX_FILE_BODY_CHUNKS = 480

_IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}

_AUDIO_MIME_BY_EXT = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}

_VIDEO_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".ogv": "video/ogg",
}

_SKIP_BASENAME_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".navin",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".next",
        ".turbo",
        ".cache",
        "dist",
        "build",
        "coverage",
        "target",
    }
)
_MAX_BASENAME_WALK_DIRS = 4000
_MAX_BASENAME_MATCHES = 12
# A hit stays cached while the file is still there (checked on every read).
# A miss is cached only briefly: the agent usually writes the file the chip
# names a moment after the chip is rendered, and "Retry" must then succeed.
_BASENAME_WALK_TTL_S = 20.0
_BASENAME_MISS_TTL_S = 2.0
_BASENAME_WALK_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}
_OPENABLE_BINARY_EXTS = frozenset(
    {
        ".pdf",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".zip",
        ".rar",
        ".7z",
        ".gz",
        ".tgz",
        ".tar",
        ".bz2",
        ".xz",
    }
) | frozenset(_IMAGE_MIME_BY_EXT) | frozenset(_AUDIO_MIME_BY_EXT) | frozenset(
    _VIDEO_MIME_BY_EXT
)


_OFFICE_RENDER_EXTS = frozenset(
    {".docx", ".doc", ".odt", ".pptx", ".ppt", ".odp", ".xlsx", ".xlsm", ".xls", ".ods"}
)
MAX_OFFICE_RENDER_BYTES = 60 * 1024 * 1024
_OFFICE_RENDER_TIMEOUT_S = 120

# Excel workbooks the gateway reads itself (openpyxl) so the viewer shows a
# table at once, on any machine, LibreOffice or not. The grid is capped: the
# viewer is for reading a deliverable, not for browsing a million rows.
_SPREADSHEET_NATIVE_EXTS = frozenset({".xlsx", ".xlsm"})
MAX_SPREADSHEET_BYTES = 25 * 1024 * 1024
_SPREADSHEET_MAX_SHEETS = 12
_SPREADSHEET_MAX_ROWS = 1000
_SPREADSHEET_MAX_COLS = 100


class WebUIFilePreviewError(ValueError):
    """Raised when a file cannot be previewed through the WebUI."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def office_render_availability() -> tuple[bool, str]:
    """Whether LibreOffice is here to render Office files, and how to get it."""
    try:
        from navin.documents._office import find_soffice, install_hint
    except Exception:  # pragma: no cover - documents package missing
        return False, "LibreOffice is required to preview Office files."
    if find_soffice() is not None:
        return True, ""
    return False, install_hint()


def _office_render_cache_dir() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("cache") / "office-preview"


def office_render_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    extra_roots: list[Path] | None = None,
) -> tuple[bytes, str, str]:
    """Render a Word / PowerPoint / Excel file to PDF bytes for the viewer.

    The PDF is cached under the runtime home, keyed by path, size and mtime, so
    reopening a file the agent did not touch costs a read; a new version of
    the file renders again. Returns ``(pdf_bytes, content_type, filename)``.
    """
    resolved = _resolve_preview_path(
        raw_path, scope=scope, extra_roots=extra_roots, cached=False
    )
    _deny_runtime_secret(resolved)
    suffix = resolved.suffix.lower()
    if suffix not in _OFFICE_RENDER_EXTS:
        raise WebUIFilePreviewError(415, "only Word, PowerPoint and Excel files render here")
    try:
        stat = resolved.stat()
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e
    if stat.st_size > MAX_OFFICE_RENDER_BYTES:
        raise WebUIFilePreviewError(
            413, f"file is too large to render (max {MAX_OFFICE_RENDER_BYTES // (1024 * 1024)} MiB)"
        )
    available, hint = office_render_availability()
    if not available:
        raise WebUIFilePreviewError(503, hint)

    import hashlib

    digest = hashlib.sha1(
        f"{resolved}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8", "replace")
    ).hexdigest()
    cache_dir = _office_render_cache_dir()
    cached = cache_dir / f"{digest}.pdf"
    if cached.is_file():
        try:
            return cached.read_bytes(), "application/pdf", f"{resolved.stem}.pdf"
        except OSError:
            pass

    import shutil
    import tempfile

    from navin.documents._office import OfficeUnavailableError, to_pdf

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="navin-office-render-") as tmp:
            pdf = to_pdf(resolved, Path(tmp), timeout=_OFFICE_RENDER_TIMEOUT_S)
            body = pdf.read_bytes()
            shutil.copyfile(pdf, cached)
    except OfficeUnavailableError as e:
        raise WebUIFilePreviewError(503, str(e)) from e
    except Exception as e:  # conversion failed: the file is the reason, say so
        raise WebUIFilePreviewError(500, f"LibreOffice could not render this file: {e}") from e
    _trim_office_render_cache(cache_dir)
    return body, "application/pdf", f"{resolved.stem}.pdf"


def _spreadsheet_cell_text(value: Any) -> str:
    """Cell value as the text a spreadsheet would show (no ``None``, no ``1.0``)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    if isinstance(value, datetime.datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.isoformat(timespec="seconds")
    return str(value)


def spreadsheet_sheets(path: Path) -> list[dict[str, Any]] | None:
    """Worksheets of an Excel file as text grids, or None when it cannot be read here.

    Read-only, values only (formulas show their cached result, as in Excel).
    Trailing empty rows and columns are dropped; every row of a sheet has the
    same width so the table lines up.
    """
    try:
        import openpyxl
    except Exception:
        return None
    try:
        if path.stat().st_size > MAX_SPREADSHEET_BYTES:
            return None
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return None

    sheets: list[dict[str, Any]] = []
    try:
        worksheets = list(workbook.worksheets)
        for sheet in worksheets[:_SPREADSHEET_MAX_SHEETS]:
            rows: list[list[str]] = []
            truncated = False
            try:
                iterator = sheet.iter_rows(
                    max_row=_SPREADSHEET_MAX_ROWS + 1,
                    max_col=_SPREADSHEET_MAX_COLS + 1,
                    values_only=True,
                )
                for raw in iterator:
                    if len(rows) >= _SPREADSHEET_MAX_ROWS:
                        truncated = True
                        break
                    cells = [_spreadsheet_cell_text(v) for v in raw]
                    if len(cells) > _SPREADSHEET_MAX_COLS:
                        if any(cells[_SPREADSHEET_MAX_COLS:]):
                            truncated = True
                        cells = cells[:_SPREADSHEET_MAX_COLS]
                    while cells and not cells[-1]:
                        cells.pop()
                    rows.append(cells)
            except Exception:
                return None
            while rows and not rows[-1]:
                rows.pop()
            width = max((len(r) for r in rows), default=0)
            for r in rows:
                r.extend([""] * (width - len(r)))
            sheets.append(
                {
                    "name": str(sheet.title),
                    "rows": rows,
                    "truncated": truncated,
                    "hidden": getattr(sheet, "sheet_state", "visible") != "visible",
                }
            )
        if len(worksheets) > _SPREADSHEET_MAX_SHEETS and sheets:
            sheets[-1]["truncated"] = True
    finally:
        try:
            workbook.close()
        except Exception:
            pass
    return sheets


def _trim_office_render_cache(cache_dir: Path, keep: int = 60) -> None:
    try:
        entries = sorted(
            (p for p in cache_dir.glob("*.pdf") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in entries[keep:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _deny_runtime_secret(resolved: Path) -> None:
    if is_runtime_secret_path(resolved):
        raise WebUIFilePreviewError(403, RUNTIME_CONFIG_DENIED)


def file_preview_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_bytes: int = MAX_FILE_PREVIEW_BYTES,
    allow_any: bool = False,
    extra_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Return a preview for a file allowed by the session workspace scope.

    With ``allow_any`` (Dev mode editor / chat panel), images are returned as
    data URLs and other binary files as metadata instead of a 415 error.
    """

    resolved = _resolve_preview_path(
        raw_path, scope=scope, extra_roots=extra_roots, cached=False
    )
    _deny_runtime_secret(resolved)
    display_path = _display_path(resolved, scope.project_path)
    suffix = resolved.suffix.lower()
    base = {
        "path": str(resolved),
        "display_path": display_path,
        "project_path": str(scope.project_path),
        "name": resolved.name,
        "mime": _mime_for_path(resolved),
    }

    try:
        size = resolved.stat().st_size
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    image_mime = _IMAGE_MIME_BY_EXT.get(suffix)
    if allow_any and image_mime and image_mime != "image/svg+xml":
        if size > MAX_IMAGE_PREVIEW_BYTES:
            return {**base, "kind": "binary", "language": "image", "size": size,
                    "content": "", "truncated": True}
        try:
            raw = resolved.read_bytes()
        except OSError as e:
            raise WebUIFilePreviewError(500, "failed to read file") from e
        data_url = f"data:{image_mime};base64,{base64.b64encode(raw).decode('ascii')}"
        return {**base, "kind": "image", "language": "image", "size": size,
                "content": "", "data_url": data_url, "truncated": False}

    audio_mime = _AUDIO_MIME_BY_EXT.get(suffix)
    if allow_any and audio_mime and suffix != ".webm":
        return {**base, "kind": "audio", "language": "audio", "size": size,
                "content": "", "truncated": False}

    video_mime = _VIDEO_MIME_BY_EXT.get(suffix)
    if allow_any and video_mime:
        return {**base, "kind": "video", "language": "video", "size": size,
                "content": "", "truncated": False}

    # No body here, like audio and video: the client fetches the bytes through
    # the download route and renders them from a blob URL. Inlining a PDF as a
    # data URL does not work - browsers refuse to hand data: documents to the
    # PDF viewer - and serving it inline from our own origin would mean
    # relaxing Content-Disposition on a route that also serves HTML.
    if allow_any and suffix == ".pdf":
        return {**base, "kind": "pdf", "language": "pdf", "size": size,
                "content": "", "truncated": False}

    # Excel workbooks open as a table straight away: the gateway reads the
    # cells itself. The LibreOffice PDF rendering stays available as the
    # "layout" view when it is installed.
    if allow_any and suffix in _SPREADSHEET_NATIVE_EXTS:
        sheets = spreadsheet_sheets(resolved)
        if sheets is not None:
            available, hint = office_render_availability()
            return {
                **base,
                "kind": "spreadsheet",
                "language": suffix[1:],
                "size": size,
                "content": "",
                "truncated": any(bool(sheet.get("truncated")) for sheet in sheets),
                "sheets": sheets,
                "render_available": available and size <= MAX_OFFICE_RENDER_BYTES,
                "render_hint": "" if available else hint,
            }

    # Word, PowerPoint and Excel files render through LibreOffice into a PDF
    # the client shows in the same viewer as a .pdf (file-render route). The
    # payload says whether that render exists so the client can show the
    # install hint instead of a spinner when it does not.
    if allow_any and suffix in _OFFICE_RENDER_EXTS:
        available, hint = office_render_availability()
        return {**base, "kind": "office", "language": suffix[1:], "size": size,
                "content": "", "truncated": False,
                "render_available": available and size <= MAX_OFFICE_RENDER_BYTES,
                "render_hint": "" if available else hint}

    try:
        with open(resolved, "rb") as f:
            raw = f.read(max_bytes + 1)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    if b"\0" in raw[:4096]:
        if allow_any:
            return {**base, "kind": "binary", "language": _language_for_path(resolved),
                    "size": size, "content": "", "truncated": False}
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")

    truncated = len(raw) > max_bytes
    preview_bytes = raw[:max_bytes]
    try:
        content = preview_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = preview_bytes.decode("utf-8", errors="replace")

    language = _language_for_path(resolved)
    kind = "text"
    if language == "csv":
        kind = "csv"
    elif language == "html":
        kind = "html"
    elif language in {"markdown", "md"}:
        kind = "markdown"
    elif language in {"json", "xml", "yaml"}:
        kind = language

    return {
        **base,
        "kind": kind,
        "language": language,
        "content": content,
        "size": size,
        "truncated": truncated,
    }


def file_download_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_bytes: int = MAX_FILE_DOWNLOAD_BYTES,
    extra_roots: list[Path] | None = None,
) -> tuple[bytes, str, str, dict[str, Any]]:
    """Load a workspace file for download.

    Returns ``(body, content_type, filename, meta)``.
    """

    resolved = _resolve_preview_path(
        raw_path, scope=scope, extra_roots=extra_roots, cached=False
    )
    _deny_runtime_secret(resolved)
    try:
        size = resolved.stat().st_size
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e
    if size > max_bytes:
        raise WebUIFilePreviewError(
            413,
            f"file is too large to download (max {max_bytes // (1024 * 1024)} MiB)",
        )
    try:
        body = resolved.read_bytes()
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e
    content_type = _mime_for_path(resolved)
    meta = {
        "path": str(resolved),
        "display_path": _display_path(resolved, scope.project_path),
        "size": size,
        "name": resolved.name,
    }
    return body, content_type, resolved.name, meta


def content_disposition_attachment(filename: str) -> str:
    """Build a Content-Disposition header value safe for ASCII and Unicode names."""

    safe = re.sub(r'[\r\n"]', "_", filename) or "download"
    ascii_name = safe.encode("ascii", errors="ignore").decode("ascii") or "download"
    encoded = quote(safe, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def file_body_from_headers(headers: Any) -> str:
    """Reassemble file content sent as chunked base64 request headers."""

    chunks: list[str] = []
    for index in range(_MAX_FILE_BODY_CHUNKS):
        raw = None
        name = f"{_FILE_BODY_HEADER_PREFIX}{index}"
        try:
            raw = headers.get(name)
        except (AttributeError, TypeError):
            raw = None
        if raw is None and hasattr(headers, "get"):
            raw = headers.get(name.title()) or headers.get(name.upper())
        if raw is None:
            break
        chunks.append(str(raw))
    if not chunks:
        raise WebUIFilePreviewError(400, "missing file content")
    try:
        return base64.b64decode("".join(chunks), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        raise WebUIFilePreviewError(400, "invalid file content encoding") from e


def file_save_payload(
    raw_path: str | None,
    content: str,
    *,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """Write text content to a file inside the session workspace scope."""

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_SAVE_BYTES:
        raise WebUIFilePreviewError(413, "file content is too large to save")

    path = _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")

    allowed_root = scope.project_path if scope.restrict_to_workspace else None
    try:
        resolved = resolve_allowed_path(
            path,
            workspace=scope.project_path,
            allowed_root=allowed_root,
            strict=True,
        )
    except FileNotFoundError:
        # New file: resolve the parent directory instead.
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = scope.project_path / candidate
        try:
            parent = resolve_allowed_path(
                str(candidate.parent),
                workspace=scope.project_path,
                allowed_root=allowed_root,
                strict=True,
            )
        except (FileNotFoundError, OSError) as e:
            raise WebUIFilePreviewError(404, "parent directory not found") from e
        except WorkspaceBoundaryError as e:
            raise WebUIFilePreviewError(403, "file is outside the current workspace") from e
        name = candidate.name
        if not name or name in {".", ".."}:
            raise WebUIFilePreviewError(400, "invalid file name")
        resolved = parent / name
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "file is outside the current workspace") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if resolved.exists() and not resolved.is_file():
        raise WebUIFilePreviewError(400, "path is not a file")
    _deny_runtime_secret(resolved)

    try:
        resolved.write_bytes(encoded)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to write file") from e

    return {
        "path": str(resolved),
        "display_path": _display_path(resolved, scope.project_path),
        "size": len(encoded),
        "saved": True,
    }


def file_create_payload(
    raw_path: str | None,
    *,
    kind: str,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """Create an empty file or a directory inside the session workspace scope.

    Missing parents are created, so one entry of ``src/components/Button.tsx``
    does what the user meant instead of failing on a folder they would then have
    to create by hand, one level at a time.
    """

    if kind not in {"file", "directory"}:
        raise WebUIFilePreviewError(400, "kind must be 'file' or 'directory'")

    path = _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    target = _resolve_new_path(path, scope=scope)
    if target.exists():
        raise WebUIFilePreviewError(409, "a file or folder with that name already exists")

    try:
        if kind == "directory":
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # x mode: two tabs racing on the same name must not clobber content.
            with open(target, "x", encoding="utf-8"):
                pass
    except FileExistsError as e:
        raise WebUIFilePreviewError(409, "a file or folder with that name already exists") from e
    except OSError as e:
        raise WebUIFilePreviewError(500, f"failed to create {kind}") from e

    return {
        "path": str(target),
        "display_path": _display_path(target, scope.project_path),
        "kind": kind,
        "created": True,
    }


def file_delete_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """Delete a file, or a directory and everything in it.

    There is no trash to fall back on, so the destructive shape of this is the
    caller's problem to confirm; what is enforced here is that the target is
    inside the project and is not the project itself.
    """

    resolved = _resolve_existing_path(raw_path, scope=scope)
    root = scope.project_path.resolve(strict=False)
    if resolved == root:
        raise WebUIFilePreviewError(403, "the project folder cannot be deleted")

    is_dir = resolved.is_dir() and not resolved.is_symlink()
    try:
        if is_dir:
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to delete") from e

    return {
        "path": str(resolved),
        "display_path": _display_path(resolved, scope.project_path),
        "kind": "directory" if is_dir else "file",
        "deleted": True,
    }


def file_rename_payload(
    raw_path: str | None,
    new_name: str | None,
    *,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """Rename or move an entry within the project.

    ``new_name`` is normally a bare name, but a path moves the entry: renaming
    to ``lib/util.ts`` is how the explorer offers "move" without a second verb,
    matching what editors do with their rename box.
    """

    source = _resolve_existing_path(raw_path, scope=scope)
    root = scope.project_path.resolve(strict=False)
    if source == root:
        raise WebUIFilePreviewError(403, "the project folder cannot be renamed")

    wanted = _clean_preview_path(new_name)
    if not wanted:
        raise WebUIFilePreviewError(400, "missing new name")
    if len(wanted) > 4096:
        raise WebUIFilePreviewError(400, "name is too long")

    # A bare name stays put; anything with a separator is relative to the
    # project, so the box doubles as a move.
    if "/" in wanted or "\\" in wanted:
        intended = scope.project_path / Path(wanted)
    else:
        intended = source.parent / wanted
    # Answered before resolving, because resolving a path that already exists
    # is how you create something new - and "rename to the same name" would
    # come back as a name clash with itself.
    if intended.resolve(strict=False) == source:
        return {
            "path": str(source),
            "display_path": _display_path(source, scope.project_path),
            "renamed": False,
        }

    target = _resolve_new_path(str(intended), scope=scope)
    if target.exists():
        raise WebUIFilePreviewError(409, "a file or folder with that name already exists")
    if source in target.parents:
        raise WebUIFilePreviewError(400, "a folder cannot be moved inside itself")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to rename") from e

    return {
        "path": str(target),
        "display_path": _display_path(target, scope.project_path),
        "previous_path": str(source),
        "renamed": True,
    }


def file_paste_payload(
    raw_path: str | None,
    raw_parent: str | None,
    *,
    move: bool,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """Copy or move an entry into ``raw_parent``, suffixing on a name clash.

    Serves paste and duplicate alike: pasting into the folder something already
    sits in is a duplicate, which is why a clash suffixes instead of failing.
    An empty ``raw_parent`` means the project root.
    """

    source = _resolve_existing_path(raw_path, scope=scope)
    root = scope.project_path.resolve(strict=False)
    if source == root:
        raise WebUIFilePreviewError(403, "the project folder cannot be copied")

    cleaned_parent = _clean_preview_path(raw_parent)
    if cleaned_parent:
        destination = _resolve_existing_path(cleaned_parent, scope=scope)
    else:
        destination = root
    if not destination.is_dir():
        raise WebUIFilePreviewError(400, "destination is not a directory")

    is_dir = source.is_dir() and not source.is_symlink()
    # Checked before the suffix dance, because the suffixed name would still
    # land inside the tree being copied and recurse until the disk gave up.
    if is_dir and (destination == source or source in destination.parents):
        raise WebUIFilePreviewError(400, "a folder cannot be copied inside itself")
    if move and destination == source.parent:
        return {
            "path": str(source),
            "display_path": _display_path(source, scope.project_path),
            "kind": "directory" if is_dir else "file",
            "pasted": False,
        }

    target = _free_name(destination, source.name)
    try:
        if move:
            source.rename(target)
        elif is_dir:
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to move" if move else "failed to copy") from e

    return {
        "path": str(target),
        "display_path": _display_path(target, scope.project_path),
        "kind": "directory" if is_dir else "file",
        "previous_path": str(source) if move else None,
        "pasted": True,
    }


def _free_name(parent: Path, name: str) -> Path:
    """Return ``parent/name``, or the first free "name copy N" beside it.

    Matches the editor convention of growing a suffix rather than refusing the
    paste or silently overwriting what is already there.
    """

    candidate = parent / name
    if not candidate.exists():
        return candidate

    # Split on the first dot so ".tar.gz" survives, but leave a leading dot
    # alone: ".gitignore" is a name, not an extension.
    stem, dot, suffix = name.lstrip(".").partition(".")
    stem = name[: len(name) - len(name.lstrip("."))] + stem
    suffix = f"{dot}{suffix}" if dot else ""

    for attempt in range(1, 1000):
        label = "copy" if attempt == 1 else f"copy {attempt}"
        candidate = parent / f"{stem} {label}{suffix}"
        if not candidate.exists():
            return candidate
    raise WebUIFilePreviewError(409, "too many copies of that name already exist")


def _resolve_existing_path(raw_path: str | None, *, scope: WorkspaceScope) -> Path:
    """Resolve a path that must already exist, refusing anything outside."""

    path = _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")
    try:
        resolved = resolve_allowed_path(
            path,
            workspace=scope.project_path,
            allowed_root=scope.project_path if scope.restrict_to_workspace else None,
            strict=True,
        )
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "path is outside the current workspace") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e
    _deny_runtime_secret(resolved)
    return resolved


def _resolve_new_path(path: str, *, scope: WorkspaceScope) -> Path:
    """Resolve a path that does not exist yet, without leaving the workspace.

    ``resolve_allowed_path`` needs the path to exist, so the nearest existing
    ancestor is resolved instead - that is what makes a symlinked parent
    pointing outside the project fail here rather than after the write - and the
    segments below it are checked by hand.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = scope.project_path / candidate

    trailing: list[str] = []
    existing = candidate
    while True:
        try:
            if existing.exists():
                break
        except OSError as e:
            raise WebUIFilePreviewError(400, "invalid path") from e
        parent = existing.parent
        if parent == existing:
            raise WebUIFilePreviewError(400, "invalid path")
        name = existing.name
        if name in {"", ".", ".."}:
            raise WebUIFilePreviewError(400, "invalid file name")
        if "\x00" in name:
            raise WebUIFilePreviewError(400, "invalid file name")
        trailing.append(name)
        existing = parent

    if not trailing:
        raise WebUIFilePreviewError(409, "a file or folder with that name already exists")

    allowed_root = scope.project_path if scope.restrict_to_workspace else None
    try:
        base = resolve_allowed_path(
            str(existing),
            workspace=scope.project_path,
            allowed_root=allowed_root,
            strict=True,
        )
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "parent directory not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "path is outside the current workspace") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if not base.is_dir():
        raise WebUIFilePreviewError(400, "parent path is not a directory")

    target = base.joinpath(*reversed(trailing))
    if allowed_root is not None:
        try:
            target.relative_to(allowed_root.resolve())
        except ValueError as e:
            raise WebUIFilePreviewError(403, "path is outside the current workspace") from e
    _deny_runtime_secret(target)
    return target


def file_preview_availability_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    extra_roots: list[Path] | None = None,
) -> dict[str, bool]:
    """Confirm that a path can open in the preview panel (text, media, or download)."""

    resolved = _resolve_preview_path(raw_path, scope=scope, extra_roots=extra_roots)
    _deny_runtime_secret(resolved)
    suffix = resolved.suffix.lower()
    if suffix in _OPENABLE_BINARY_EXTS:
        return {"available": True}
    try:
        with open(resolved, "rb") as f:
            prefix = f.read(4096)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e
    if b"\0" in prefix:
        # Still openable for download via the panel.
        return {"available": True}
    return {"available": True}


def _resolve_preview_path(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    extra_roots: list[Path] | None = None,
    cached: bool = True,
) -> Path:
    path = _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    # The open Code project is tried first: the chat session may still be
    # scoped to another folder while the editor is looking at this one.
    roots: list[Path] = []
    for candidate in list(extra_roots or []) + [scope.project_path]:
        try:
            resolved_root = Path(candidate).expanduser().resolve(strict=False)
        except OSError:
            continue
        if resolved_root in roots:
            continue
        roots.append(resolved_root)
    if not roots:
        roots.append(Path(scope.project_path))

    last_boundary: WorkspaceBoundaryError | None = None
    last_missing: Exception | None = None
    for root in roots:
        scoped = replace(scope, project_path=root)
        try:
            resolved = resolve_allowed_path(
                path,
                workspace=root,
                allowed_root=root if scoped.restrict_to_workspace else None,
                strict=True,
            )
        except FileNotFoundError as e:
            last_missing = e
            fallback = _resolve_unique_basename(path, scope=scoped, cached=cached)
            if fallback is not None:
                _deny_runtime_secret(fallback)
                return fallback
            continue
        except WorkspaceBoundaryError as e:
            last_boundary = e
            fallback = _resolve_unique_basename(path, scope=scoped, cached=cached)
            if fallback is not None:
                _deny_runtime_secret(fallback)
                return fallback
            continue
        except OSError as e:
            last_missing = e
            continue

        if resolved.is_file():
            _deny_runtime_secret(resolved)
            return resolved
        fallback = _resolve_unique_basename(path, scope=scoped, cached=cached)
        if fallback is not None:
            _deny_runtime_secret(fallback)
            return fallback

    if last_boundary is not None and last_missing is None:
        raise WebUIFilePreviewError(403, "file is outside the current workspace") from last_boundary
    raise WebUIFilePreviewError(404, "file not found") from last_missing


def _is_bare_file_name(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or "/" in normalized:
        return False
    return normalized not in {".", ".."}


def _resolve_unique_basename(
    raw_path: str,
    *,
    scope: WorkspaceScope,
    cached: bool = True,
) -> Path | None:
    """Locate ``auth.py`` under ``src/`` when the chip only carries the name.

    Chat chips often cite a basename. Opening must still work if that file
    exists in the workspace (Mac, Linux, and Windows). When several files
    share the name, the most recently written one opens: the chip sits next
    to the edit that produced it, and the tab shows the full path so a wrong
    guess is visible and one click away from the right file. A dead
    "not on disk" for a file the agent just wrote is the worse outcome.
    """

    if not _is_bare_file_name(raw_path):
        return None
    root = scope.project_path
    try:
        root_resolved = root.resolve()
    except OSError:
        return None
    if not root_resolved.is_dir():
        return None

    wanted_name = Path(raw_path.replace("\\", "/")).name
    wanted = wanted_name.casefold()

    # The bounded, cached walk decides. Well-known locations are consulted
    # only when it saw nothing (absent, or a tree larger than the walk budget).
    found, walk_saw_everything = _walk_unique_basename(
        root_resolved, wanted, cached=cached
    )
    if found is not None or walk_saw_everything:
        return found

    guessed: list[Path] = [Path(wanted_name)]
    if wanted_name.startswith("test_") and wanted_name.endswith(".py"):
        guessed.extend((Path("tests") / wanted_name, Path("test") / wanted_name))
    guessed.extend(
        (
            Path("src") / wanted_name,
            Path("webui") / "src" / wanted_name,
            Path("navin") / "documents" / wanted_name,
            Path("documents") / wanted_name,
        )
    )
    for rel in guessed:
        candidate = root_resolved / rel
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _walk_unique_basename(
    root_resolved: Path,
    wanted: str,
    *,
    cached: bool = True,
) -> tuple[Path | None, bool]:
    """(the file named ``wanted`` under the root or None, walk was complete).

    With several candidates the most recently modified one wins (ties go to
    the shallowest path). The second value is False when the tree exceeded
    the walk budget, so a caller knows a None means "not seen" rather than
    "not there". The cache absorbs the burst of chip probes a rendered
    answer triggers; ``cached=False`` (a user opening or downloading a file)
    always walks: the chip was often probed before the agent finished writing
    the file, and the newest of two same-named files may have changed.
    """
    cache_key = (str(root_resolved), wanted)
    now = time.monotonic()
    entry = _BASENAME_WALK_CACHE.get(cache_key) if cached else None
    if entry is not None:
        cached_at, cached_path = entry
        if cached_path:
            # A hit is reused only while the file is still there; a moved or
            # deleted file must not be reported present for 20 seconds.
            if now - cached_at < _BASENAME_WALK_TTL_S and Path(cached_path).is_file():
                return Path(cached_path), True
        elif now - cached_at < _BASENAME_MISS_TTL_S:
            return None, True

    matches: list[Path] = []
    dirs_seen = 0
    complete = True
    for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
        dirs_seen += 1
        if dirs_seen > _MAX_BASENAME_WALK_DIRS:
            complete = False
            break
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _SKIP_BASENAME_DIRS and not name.startswith(".")
        ]
        for filename in filenames:
            if filename.casefold() != wanted:
                continue
            candidate = Path(dirpath) / filename
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            if is_runtime_secret_path(resolved):
                continue
            matches.append(resolved)
        if len(matches) >= _MAX_BASENAME_MATCHES:
            complete = False
            break
    found = _newest_path(matches)
    if complete or found is not None:
        _BASENAME_WALK_CACHE[cache_key] = (now, str(found) if found else None)
    return found, complete


def _newest_path(paths: list[Path]) -> Path | None:
    """Most recently modified of ``paths``; ties go to the shallowest path."""
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]

    def key(path: Path) -> tuple[float, int, str]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (mtime, -len(path.parts), path.as_posix())

    return max(paths, key=key)


def _clean_preview_path(raw_path: str | None) -> str:
    if raw_path is None:
        return ""
    value = raw_path.strip()
    if not value:
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:[\\/]", value):
            value = value[1:]
    else:
        value = unquote(value)
    value = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not re.match(r"^[A-Za-z]:[\\/]", value):
        value = re.sub(r":\d+(?::\d+)?$", "", value)
    # A chip may carry the Windows spelling of a WSL file
    # (\\wsl.localhost\Ubuntu\home\...). This gateway runs inside the
    # distribution, where the same file is simply /home/...
    if sys.platform != "win32":
        candidate = value
        # Lost leading slashes ("wsl.localhost/Ubuntu/...") still identify
        # the redirector; give parse_unc the shape it expects.
        if re.match(r"^[\\/]?wsl(?:\$|\.localhost)[\\/]", candidate, re.IGNORECASE):
            candidate = "//" + candidate.lstrip("\\/")
        location = wsl.parse_unc(candidate)
        if location is not None:
            value = location.posix
    return value


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _mime_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    for table in (_IMAGE_MIME_BY_EXT, _AUDIO_MIME_BY_EXT, _VIDEO_MIME_BY_EXT):
        mime = table.get(suffix)
        if mime:
            return mime
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _language_for_path(path: Path) -> str:
    name = path.name.lower()
    ext = path.suffix.lower().lstrip(".")
    if name == "dockerfile":
        return "dockerfile"
    return {
        "cjs": "javascript",
        "css": "css",
        "csv": "csv",
        "cts": "typescript",
        "html": "html",
        "htm": "html",
        "js": "javascript",
        "json": "json",
        "jsonl": "json",
        "jsx": "jsx",
        "md": "markdown",
        "mdx": "markdown",
        "mjs": "javascript",
        "mts": "typescript",
        "py": "python",
        "pyi": "python",
        "scss": "scss",
        "sh": "bash",
        "toml": "toml",
        "ts": "typescript",
        "tsx": "tsx",
        "txt": "text",
        "xml": "xml",
        "yaml": "yaml",
        "yml": "yaml",
        "xlsx": "excel",
        "xls": "excel",
        "pdf": "pdf",
        "zip": "zip",
        "rar": "rar",
    }.get(ext, ext or "text")
