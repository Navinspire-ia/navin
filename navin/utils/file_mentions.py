# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""File and folder ``@`` mentions selected in the composer.

Typing ``@src/lib/api.ts`` is how a user frames the context of a turn, which is
otherwise left entirely to the agent's own search. The selected paths are
announced to the model as runtime context rather than inlined: a mentioned
folder or a large file would otherwise displace the conversation itself, and
the agent already has ``read_file`` and ``list_dir`` to pull in what it needs.

Paths arrive from the client, so they are validated twice: their shape is
checked on receipt, and their resolved location is confined to the active
workspace when the turn is annotated.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from navin.index.symbols import language_for
from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext


FILE_MENTION_METADATA_KEY = "file_mentions"
_MAX_MENTIONS = 12
_MAX_PATH_CHARS = 400
_MAX_COUNTED_BYTES = 2_000_000
_MAX_SYMBOL_CHARS = 200
_MAX_LINE = 10_000_000
_KINDS = ("file", "directory", "symbol")


def normalize_file_mentions(raw: Any) -> list[dict[str, str]]:
    """Sanitize structured file mentions sent by the WebUI.

    Only workspace-relative paths survive: absolute paths and ``..`` segments
    are dropped here so no later stage has to reason about escaping the project.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw[:_MAX_MENTIONS]:
        if not isinstance(item, Mapping):
            continue
        cleaned = _clean_relative_path(item.get("path"))
        if cleaned is None:
            continue
        kind = item.get("kind")
        kind = kind if kind in _KINDS else "file"
        name = _clean_symbol_name(item.get("name")) if kind == "symbol" else ""
        if kind == "symbol" and not name:
            # A symbol mention without an identifier is just its file.
            kind = "file"
        # Two symbols defined in one file are two distinct mentions, so the
        # identifier is part of what makes a mention unique.
        key = (cleaned, kind, name)
        if key in seen:
            continue
        seen.add(key)
        mention = {"path": cleaned, "kind": kind}
        if kind == "symbol":
            mention["name"] = name
            mention["line"] = str(_clean_line(item.get("line")))
        out.append(mention)
    return out


def _clean_symbol_name(value: Any) -> str:
    """An identifier, possibly qualified as ``Class.method``, or empty."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > _MAX_SYMBOL_CHARS:
        return ""
    return text if all(ch.isalnum() or ch in "_.$" for ch in text) else ""


def _clean_line(value: Any) -> int:
    try:
        line = int(value)
    except (TypeError, ValueError):
        return 0
    return line if 0 < line <= _MAX_LINE else 0


def _clean_relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/")
    if not text or len(text) > _MAX_PATH_CHARS:
        return None
    # Rejected rather than rewritten: stripping the leading slash off
    # "/etc/passwd" would silently point the mention at a different file.
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    if not text:
        return None
    parts = [part for part in text.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _resolve_in_workspace(rel_path: str, workspace: Path) -> Path | None:
    """Resolve inside ``workspace``, or None when the path escapes it."""
    try:
        root = workspace.resolve(strict=False)
        target = (root / rel_path).resolve(strict=False)
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _describe_file(target: Path, rel_path: str) -> str:
    details: list[str] = []
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    if 0 < size <= _MAX_COUNTED_BYTES:
        try:
            raw = target.read_bytes()
        except OSError:
            raw = b""
        if raw:
            lines = raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)
            details.append(f"{lines} lines")
    elif size > _MAX_COUNTED_BYTES:
        details.append(f"{size // 1024} KB")
    language = language_for(rel_path)
    if language:
        details.append(language)
    suffix = f" ({', '.join(details)})" if details else ""
    return (
        f"File Attachment: '{rel_path}'{suffix}. The user pointed at this exact "
        "path. Read it with read_file before searching for it, and treat it as "
        "the intended subject of the request."
    )


def file_mention_runtime_lines(
    metadata: Mapping[str, Any] | None,
    *,
    workspace: Path | None,
) -> list[str]:
    """Build model-visible annotations for the paths the user attached."""
    raw = metadata.get(FILE_MENTION_METADATA_KEY) if isinstance(metadata, Mapping) else None
    mentions = normalize_file_mentions(raw)
    if not mentions or workspace is None:
        return []

    lines: list[str] = []
    for mention in mentions:
        rel_path = mention["path"]
        target = _resolve_in_workspace(rel_path, workspace)
        if target is None or not target.exists():
            lines.append(
                f"Attachment Error: '{rel_path}' was mentioned by the user but "
                "does not exist in the workspace. Say so instead of guessing at "
                "a similarly named file."
            )
            continue
        if mention["kind"] == "symbol" and not target.is_dir():
            lines.append(_describe_symbol(mention, rel_path))
        elif target.is_dir():
            lines.append(
                f"Folder Attachment: '{rel_path}'. The user pointed at this "
                "exact folder. Inspect it with list_dir, and scope the work to "
                "it unless told otherwise."
            )
        else:
            lines.append(_describe_file(target, rel_path))
    return lines


def _describe_symbol(mention: Mapping[str, str], rel_path: str) -> str:
    """Announce a symbol by its definition site, not by inlining its body.

    The location is what the agent cannot cheaply recover: a name alone sends it
    searching, and two projects in three have the same name in several files.
    """
    name = mention.get("name", "")
    line = mention.get("line", "0")
    where = f"{rel_path}:{line}" if line and line != "0" else rel_path
    return (
        f"Symbol Attachment: '{name}' defined at {where}. The user pointed at "
        "this exact definition. Read it there, and use code_index or lsp "
        "action=references to reach its callers before changing it."
    )


async def file_mention_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Annotate one agent turn with the files and folders the user attached."""
    content = wrap_runtime_context_lines(
        file_mention_runtime_lines(
            request.metadata,
            workspace=request.workspace,
        )
    )
    if not content:
        return None
    return RuntimeContextBlock(source="file_mentions", content=content)
