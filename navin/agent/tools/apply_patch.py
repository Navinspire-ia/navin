# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Apply file edits by providing structured edit instructions."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.agent.tools.atomic_write import write_all
from navin.agent.tools.base import ToolResult, tool_parameters
from navin.agent.tools.filesystem import _best_window, _find_matches, _FsTool
from navin.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.utils import text_decode
from navin.utils.git_state import uncommitted_note

_EDIT_ACTIONS = ("replace", "add")


@dataclass(slots=True)
class _PatchSummary:
    action: str
    path: str
    added: int = 0
    deleted: int = 0


class _PatchError(ValueError):
    pass


def _validate_patch_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise _PatchError("patch path cannot be empty")
    if "\0" in normalized:
        raise _PatchError(f"patch path contains a null byte: {path!r}")
    return normalized


def _lines_to_text(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _text_line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _line_diff_stats(before: str, after: str) -> tuple[int, int]:
    before_lines = before.replace("\r\n", "\n").splitlines()
    after_lines = after.replace("\r\n", "\n").splitlines()
    added = 0
    deleted = 0
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


def _append_text(content: str, addition: str) -> str:
    """Append text without merging it into an unterminated final line.

    A file that deliberately ends without a newline keeps that shape: a
    separator is still inserted so the addition starts on its own line, but the
    result is not terminated on the file's behalf.
    """
    base = content.replace("\r\n", "\n")
    extra = addition.replace("\r\n", "\n")
    terminated = not base or base.endswith("\n")
    if base and extra and not terminated and not extra.startswith("\n"):
        base += "\n"
    combined = base + extra
    if combined and terminated and not combined.endswith("\n"):
        combined += "\n"
    return combined


def _exact_positions(content: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx < 0:
            return positions
        positions.append(idx)
        start = idx + max(1, len(needle))


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _preview_lines(lines: list[int]) -> str:
    shown = ", ".join(f"line {number}" for number in lines[:5])
    return f"{shown}, ..." if len(lines) > 5 else shown


def _not_found_detail(old_text: str, content: str, path: str) -> str:
    """Say why the text was close but not equal, instead of only that it missed.

    apply_patch stays exact on purpose, so a near miss has to be reported
    precisely enough for the next call to succeed without another read.
    """
    loose = _find_matches(content, old_text)
    if loose:
        lines = [match.line for match in loose]
        return (
            f"old_text not found in {path}, but {len(loose)} near match(es) exist at "
            f"{_preview_lines(lines)}. The text differs in indentation, whitespace or "
            "quote style - copy it verbatim from read_file."
        )

    ratio, start, window, hints = _best_window(old_text, content)
    if ratio > 0.5:
        diff = "\n".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                window,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {start + 1})",
                lineterm="",
            )
        )
        cause = f" Possible cause: {', '.join(hints)}." if hints else ""
        return (
            f"old_text not found in {path}.{cause}\n"
            f"Closest match ({ratio:.0%} similar) at line {start + 1}:\n{diff}"
        )
    if hints:
        return (
            f"old_text not found in {path}. Possible cause: {', '.join(hints)}. "
            "Copy the exact text from read_file and try again."
        )
    return f"old_text not found in {path}. No similar text nearby - re-read the file."


def _read_source(
    source: Path, path: str, encodings: dict[Path, text_decode.DecodedText]
) -> str:
    """Read a file for patching, remembering how to write it back.

    The returned text keeps the file's original line endings, because the patch
    logic downstream matches `old_text` against it and then writes it verbatim.
    """
    decoded = text_decode.decode(source.read_bytes())
    if decoded is None:
        raise _PatchError(f"file is not text: {path}")
    encodings[source] = decoded
    return decoded.text.replace("\n", "\r\n") if decoded.crlf else decoded.text


def _prefix_warnings(stale: dict[str, str], body: str) -> str:
    """Lead with any read-before-write warnings, keeping the patch result intact."""
    if not stale:
        return body
    lines = [f"{path}: {warning}" for path, warning in stale.items()]
    return "\n".join(lines) + "\n\n" + body


def _format_summary(summary: _PatchSummary) -> str:
    stats = ""
    if summary.added or summary.deleted:
        stats = f" (+{summary.added}/-{summary.deleted})"
    return f"- {summary.action} {summary.path}{stats}"


@tool_parameters(
    tool_parameters_schema(
        edits=ArraySchema(
            items=ObjectSchema(
                path=StringSchema(
                    "Path to the file to edit. Relative paths resolve against the "
                    "workspace; absolute paths and '..' obey the workspace access policy."
                ),
                action=StringSchema(
                    "Operation type: replace or add.",
                    enum=list(_EDIT_ACTIONS),
                ),
                old_text=StringSchema(
                    "Exact text to search for in the file. Required for replace.",
                    nullable=True,
                ),
                new_text=StringSchema(
                    "Text to replace with or append. Required for replace and add.",
                    nullable=True,
                ),
                occurrence=IntegerSchema(
                    1,
                    description=(
                        "Which match to replace, 1-based, when old_text appears more "
                        "than once. Without it a repeated old_text is refused rather "
                        "than guessed."
                    ),
                    minimum=1,
                    nullable=True,
                ),
                required=["path", "action"],
            ),
            description="List of edits to apply. Each edit specifies a file and the change to make.",
            min_items=1,
            max_items=20,
        ),
        dry_run=BooleanSchema(
            description="Validate and summarize the patch without writing files.",
            default=False,
        ),
        required=["edits"],
    )
)
class ApplyPatchTool(_FsTool):
    """Apply file edits by providing structured edit instructions."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return (
            "Default tool for code edits and new-file creation on Agent Code turns. "
            "Supports multi-file changes in a single call. Provide a list of "
            "structured edits, each specifying a file path, action (replace/add), "
            "and the exact text to change. For a brand-new file use "
            'action=add with new_text (no old_text). For edits use action=replace '
            "with old_text that matches byte for byte and is unique; set "
            "occurrence to choose between repeated matches. Line endings and a "
            "missing final newline are preserved. "
            "Paths are resolved by the current workspace access policy. "
            "Set dry_run=true to validate and preview without writing files. "
            "Prefer this over write_file for small/partial code edits."
        )

    async def execute(
        self,
        edits: list[dict] | None = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            if not edits:
                raise _PatchError("must provide edits")

            writes: dict[Path, str] = {}
            summaries: list[_PatchSummary] = []
            stale: dict[str, str] = {}
            encodings: dict[Path, text_decode.DecodedText] = {}

            for edit in edits:
                if not isinstance(edit, dict):
                    raise _PatchError("each edit must be an object")
                raw_path = edit.get("path")
                if not isinstance(raw_path, str):
                    raise _PatchError("path required for edit")
                path = _validate_patch_path(raw_path)
                action = edit.get("action")
                if not isinstance(action, str):
                    raise _PatchError(f"action required for edit: {path}")
                source = await self._bound_path(path, write=True)

                # Read-before-write, matching edit_file. Only existing files are
                # checked, and only once per file: an append via "add" has no
                # old_text to anchor it, so nothing else would catch the case
                # where the file moved on since the agent last looked at it.
                if source not in writes and source.exists() and path not in stale:
                    warning = self._file_states.check_read(source)
                    if warning:
                        root = self._display_workspace()
                        note = uncommitted_note(root, source) if root else ""
                        stale[path] = f"{warning}\n{note}" if note else warning

                if action == "add":
                    new_text = edit.get("new_text")
                    if new_text is None:
                        raise _PatchError(f"new_text required for add: {path}")

                    pending = writes.get(source)
                    if pending is not None:
                        content = pending
                        exists = True
                    elif source.exists():
                        content = _read_source(source, path, encodings)
                        exists = True
                    else:
                        content = ""
                        exists = False

                    if exists:
                        uses_crlf = "\r\n" in content
                        new_norm = _append_text(content, new_text)
                        if uses_crlf:
                            new_norm = new_norm.replace("\n", "\r\n")
                        writes[source] = new_norm
                        added, deleted = _line_diff_stats(content, new_norm)
                        action_name = "update"
                    else:
                        new_norm = new_text.replace("\r\n", "\n")
                        if new_norm and not new_norm.endswith("\n"):
                            new_norm += "\n"
                        writes[source] = new_norm
                        added = _text_line_count(new_norm)
                        deleted = 0
                        action_name = "add"

                    summaries.append(
                        _PatchSummary(
                            action=action_name, path=path, added=added, deleted=deleted
                        )
                    )

                elif action == "replace":
                    old_text = edit.get("old_text") or ""
                    if not old_text:
                        raise _PatchError(f"old_text required for replace: {path}")
                    new_text = edit.get("new_text")
                    if new_text is None:
                        raise _PatchError(f"new_text required for replace: {path}")

                    pending = writes.get(source)
                    if pending is not None:
                        content = pending
                    elif source.exists():
                        content = _read_source(source, path, encodings)
                    else:
                        raise _PatchError(f"file to update does not exist: {path}")

                    if pending is None and not source.is_file():
                        raise _PatchError(f"path to update is not a file: {path}")

                    uses_crlf = "\r\n" in content
                    norm_content = content.replace("\r\n", "\n")
                    norm_old = old_text.replace("\r\n", "\n")
                    terminated = norm_content.endswith("\n")

                    positions = _exact_positions(norm_content, norm_old)
                    if not positions:
                        raise _PatchError(_not_found_detail(norm_old, norm_content, path))

                    occurrence = edit.get("occurrence")
                    if occurrence is not None and not isinstance(occurrence, int):
                        raise _PatchError(f"occurrence must be an integer: {path}")
                    if occurrence is not None and not 1 <= occurrence <= len(positions):
                        raise _PatchError(
                            f"occurrence {occurrence} is out of range in {path}: "
                            f"old_text appears {len(positions)} time(s)"
                        )
                    if len(positions) > 1 and occurrence is None:
                        lines = [_line_of(norm_content, offset) for offset in positions]
                        raise _PatchError(
                            f"old_text appears {len(positions)} times in {path} at "
                            f"{_preview_lines(lines)}. Extend old_text with surrounding "
                            "context, or set occurrence to pick one of them."
                        )

                    pos = positions[(occurrence or 1) - 1]
                    new_norm = (
                        norm_content[:pos]
                        + new_text.replace("\r\n", "\n")
                        + norm_content[pos + len(norm_old) :]
                    )
                    # A file that had no final newline keeps none: adding one
                    # would change a byte the caller never mentioned.
                    if new_norm and terminated and not new_norm.endswith("\n"):
                        new_norm += "\n"
                    if uses_crlf:
                        new_norm = new_norm.replace("\n", "\r\n")

                    writes[source] = new_norm
                    added, deleted = _line_diff_stats(content, new_norm)
                    summaries.append(
                        _PatchSummary(
                            action="update", path=path, added=added, deleted=deleted
                        )
                    )

                else:
                    raise _PatchError(
                        f"unknown action '{action}' for {path}. "
                        f"Valid actions: {', '.join(_EDIT_ACTIONS)}."
                    )

            if dry_run:
                return _prefix_warnings(
                    stale,
                    "Patch dry-run succeeded:\n"
                    + "\n".join(_format_summary(summary) for summary in summaries),
                )

            write_all(writes, encodings=encodings, file_states=self._file_states)
            applied = _prefix_warnings(
                stale,
                "Patch applied:\n"
                + "\n".join(_format_summary(summary) for summary in summaries),
            )
            return await self._with_diagnostics(applied, list(writes))
        except PermissionError as exc:
            return ToolResult.error(f"Error: {exc}")
        except _PatchError as exc:
            return ToolResult.error(f"Error applying patch: {exc}")
        except Exception as exc:
            return ToolResult.error(f"Error applying patch: {exc}")
