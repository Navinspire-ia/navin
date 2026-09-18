# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Lossless compatibility for file tool arguments emitted by different models."""

from __future__ import annotations

import json
from typing import Any

_PATH_ALIASES = {"file_path": "path", "filepath": "path"}
_EDIT_ALIASES = {
    **_PATH_ALIASES,
    "old_string": "old_text", "new_string": "new_text",
    "search": "old_text", "replace": "new_text",
}
_FILE_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "apply_patch", "manage_files", "list_dir",
})


def _aliases(params: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    result = dict(params)
    for alias, canonical in aliases.items():
        if alias not in result:
            continue
        value = result.pop(alias)
        if canonical in result and result[canonical] != value:
            raise ValueError(f"conflicting '{canonical}' and '{alias}' values")
        result[canonical] = value
    return result


def _decode_container(value: Any) -> Any:
    """Decode complete JSON containers, never repair or trim file contents."""
    for _ in range(2):
        if not isinstance(value, str):
            break
        candidate = value.strip()
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[0] in {"```", "```json"} and lines[-1] == "```":
            candidate = "\n".join(lines[1:-1])
        try:
            decoded = json.loads(candidate, strict=False)
        except (ValueError, TypeError):
            break
        if not isinstance(decoded, (dict, list, str)):
            break
        value = decoded
    return value


def _text_patch(patch: str) -> list[dict[str, Any]]:
    """Translate complete Add/Update patches to the existing atomic edit path.

    Unsupported operations are rejected as a whole; manage_files handles
    deletion and moves with their own permission and checkpoint checks.
    """
    lines = patch.strip().replace("\r\n", "\n").split("\n")
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("text patch must include complete Begin Patch and End Patch markers")
    edits: list[dict[str, Any]] = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        if header.startswith("*** Add File: "):
            path = header.removeprefix("*** Add File: ")
            content: list[str] = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                line = lines[index]
                if not line.startswith("+"):
                    raise ValueError("each Add File content line must start with '+'")
                content.append(line[1:])
                index += 1
            edits.append({
                "path": path, "action": "add", "create_only": True,
                "new_text": "\n".join(content) + ("\n" if content else ""),
            })
        elif header.startswith("*** Update File: "):
            path = header.removeprefix("*** Update File: ")
            file_start = len(edits)
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                if not (lines[index] == "@@" or lines[index].startswith("@@ ")):
                    raise ValueError("each Update File hunk must start with '@@'")
                index += 1
                old: list[str] = []
                new: list[str] = []
                changed = False
                while index < len(lines) - 1:
                    line = lines[index]
                    if line.startswith(("@@", "*** ")):
                        break
                    if not line or line[0] not in " +-":
                        raise ValueError("patch hunk lines must start with space, '+' or '-'")
                    if line[0] in " -":
                        old.append(line[1:])
                    if line[0] in " +":
                        new.append(line[1:])
                    changed |= line[0] in "+-"
                    index += 1
                if not old or not changed:
                    raise ValueError("update hunk needs original lines and a change; use edit_file for unanchored edits")
                edits.append({
                    "path": path, "action": "replace", "match_lines": True,
                    "old_text": "\n".join(old) + "\n",
                    "new_text": "\n".join(new) + ("\n" if new else ""),
                })
                if lines[index] == "*** End of File":
                    edits[-1]["at_eof"] = True
                    index += 1
                    break
            if len(edits) == file_start:
                raise ValueError("Update File requires at least one complete hunk")
        else:
            raise ValueError(
                "unsupported text patch operation; use structured edits for add/replace "
                "and manage_files for delete/move"
            )
    if not edits:
        raise ValueError("patch requires at least one edit")
    return edits


def normalize_file_arguments(name: str, params: Any) -> Any:
    if name not in _FILE_TOOLS:
        return params
    params = _decode_container(params)
    if isinstance(params, dict) and set(params) == {"arguments"}:
        params = _decode_container(params["arguments"])
    if name == "apply_patch":
        if isinstance(params, str) and params.lstrip().startswith("*** Begin Patch"):
            return {"edits": _text_patch(params)}
        if isinstance(params, list):
            params = {"edits": params}
        if not isinstance(params, dict):
            return params
        result = _aliases(params, {"patch": "patch_text", "input": "patch_text"})
        if "patch_text" in result:
            patch = result.pop("patch_text")
            if "edits" in result or set(result) - {"dry_run"}:
                raise ValueError("provide either a text patch or structured edits, not both")
            if not isinstance(patch, str):
                raise ValueError("patch text must be a string")
            return {**result, "edits": _text_patch(patch)}
        if "edits" not in result and any(key in result for key in ("path", "file_path", "filepath")):
            dry_run = {"dry_run": result.pop("dry_run")} if "dry_run" in result else {}
            result = {**dry_run, "edits": [result]}
        edits = _decode_container(result.get("edits"))
        if isinstance(edits, dict):
            edits = [edits]
        if isinstance(edits, list):
            normalized = []
            for edit in edits:
                edit = _decode_container(edit)
                if isinstance(edit, dict):
                    edit = _aliases(edit, {**_EDIT_ALIASES, "operation": "action", "op": "action"})
                    if "action" not in edit and "old_text" in edit and "new_text" in edit:
                        edit["action"] = "replace"
                normalized.append(edit)
            result["edits"] = normalized
        return result
    if not isinstance(params, dict):
        return params
    if name == "edit_file":
        return _aliases(params, _EDIT_ALIASES)
    if name == "manage_files":
        result = _aliases(params, {
            **_PATH_ALIASES, "source": "path", "source_path": "path",
            "dest": "destination", "destination_path": "destination",
            "operation": "action",
        })
        if result.get("action") == "rename":
            result["action"] = "move"
        if result.get("action") == "delete" and "path" in result:
            result = _aliases({**result, "path": [result["path"]]}, {"path": "paths"})
        if "paths" in result:
            result["paths"] = _decode_container(result["paths"])
            if isinstance(result["paths"], str):
                result["paths"] = [result["paths"]]
        return result
    return _aliases(params, _PATH_ALIASES)


def file_argument_guidance(name: str) -> str:
    return {
        "apply_patch": (
            'Shape for an edit: {"edits":[{"path":"FILE","action":"replace",'
            '"old_text":"EXACT ORIGINAL","new_text":"REPLACEMENT"}]}. '
            'For a new file: {"edits":[{"path":"FILE","action":"add","new_text":"CONTENT"}]}. '
            "If patch formatting fails, use read_file then edit_file(path, old_text, new_text); "
            "use write_file(path, content) for new files and manage_files for delete/move/copy/mkdir."
        ),
        "edit_file": 'Shape: {"path":"FILE","old_text":"EXACT ORIGINAL","new_text":"REPLACEMENT"}.',
        "write_file": 'Shape: {"path":"FILE","content":"COMPLETE CONTENT"}.',
        "manage_files": (
            'Shapes: {"action":"delete","paths":["FILE"]}, '
            '{"action":"move","path":"SOURCE","destination":"TARGET"}, '
            '{"action":"copy","path":"SOURCE","destination":"TARGET"}, '
            '{"action":"mkdir","path":"DIRECTORY"}.'
        ),
    }.get(name, "")
