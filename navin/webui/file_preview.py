"""Workspace-scoped source preview payloads for the WebUI."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from navin.security.workspace_access import WorkspaceScope
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

MAX_FILE_PREVIEW_BYTES = 384 * 1024
MAX_IMAGE_PREVIEW_BYTES = 8 * 1024 * 1024
# Saves travel as chunked base64 request headers (the gateway's HTTP layer has
# no request bodies), so the cap must stay well under header-count limits.
MAX_FILE_SAVE_BYTES = 400 * 1024
_FILE_BODY_HEADER_PREFIX = "x-navin-file-body-"
_MAX_FILE_BODY_CHUNKS = 120

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


class WebUIFilePreviewError(ValueError):
    """Raised when a file cannot be previewed through the WebUI."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def file_preview_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_bytes: int = MAX_FILE_PREVIEW_BYTES,
    allow_any: bool = False,
) -> dict[str, Any]:
    """Return a preview for a file allowed by the session workspace scope.

    With ``allow_any`` (Dev mode editor), images are returned as data URLs and
    other binary files as metadata instead of a 415 error.
    """

    resolved = _resolve_preview_path(raw_path, scope=scope)
    display_path = _display_path(resolved, scope.project_path)
    base = {
        "path": str(resolved),
        "display_path": display_path,
        "project_path": str(scope.project_path),
    }

    try:
        size = resolved.stat().st_size
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    image_mime = _IMAGE_MIME_BY_EXT.get(resolved.suffix.lower())
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

    try:
        with open(resolved, "rb") as f:
            raw = f.read(max_bytes + 1)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    if b"\0" in raw[:4096]:
        if allow_any:
            return {**base, "kind": "binary", "language": "binary", "size": size,
                    "content": "", "truncated": False}
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")

    truncated = len(raw) > max_bytes
    preview_bytes = raw[:max_bytes]
    try:
        content = preview_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = preview_bytes.decode("utf-8", errors="replace")

    return {
        **base,
        "kind": "text",
        "language": _language_for_path(resolved),
        "content": content,
        "size": size,
        "truncated": truncated,
    }


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


def file_preview_availability_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
) -> dict[str, bool]:
    """Confirm that a path is a readable text preview candidate without loading it fully."""

    resolved = _resolve_preview_path(raw_path, scope=scope)
    try:
        with open(resolved, "rb") as f:
            prefix = f.read(4096)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e
    if b"\0" in prefix:
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")
    return {"available": True}


def _resolve_preview_path(raw_path: str | None, *, scope: WorkspaceScope) -> Path:
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
        raise WebUIFilePreviewError(404, "file not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "file is outside the current workspace") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if not resolved.is_file():
        raise WebUIFilePreviewError(404, "file not found")
    return resolved


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
    return value


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _language_for_path(path: Path) -> str:
    name = path.name.lower()
    ext = path.suffix.lower().lstrip(".")
    if name == "dockerfile":
        return "dockerfile"
    return {
        "cjs": "javascript",
        "css": "css",
        "cts": "typescript",
        "html": "html",
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
        "yaml": "yaml",
        "yml": "yaml",
    }.get(ext, ext or "text")
