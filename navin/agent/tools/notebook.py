# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Jupyter notebooks, cell by cell.

``read_file`` renders an ``.ipynb`` as numbered cells with their outputs
summarised, instead of the raw JSON (where one base64 PNG can be 200 KB of
noise and every source line is a quoted string). ``notebook_edit`` replaces,
inserts or deletes a cell by the number shown in that rendering, writes the
notebook back in Jupyter's own JSON layout and clears stale outputs on the
cells it touches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navin.agent.checkpoints import record_file_before
from navin.agent.tools.base import ToolResult
from navin.agent.tools.filesystem import _FsTool

# One output can be a whole DataFrame dump; the model needs its shape, not
# every row. Images and widgets are named, never inlined.
_MAX_OUTPUT_CHARS = 1_500
_TEXT_MIMES = ("text/plain", "text/markdown", "text/html", "application/json")
_ACTIONS = ("replace", "insert_after", "insert_before", "delete")


def _join_source(source: Any) -> str:
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source or "")


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + f"\n... ({len(text) - _MAX_OUTPUT_CHARS} more chars)"


def _render_output(output: dict[str, Any]) -> str:
    kind = str(output.get("output_type") or "output")
    if kind == "stream":
        name = str(output.get("name") or "stdout")
        return f"--- output: {name}\n{_clip(_join_source(output.get('text')).rstrip())}"
    if kind == "error":
        ename = str(output.get("ename") or "Error")
        evalue = str(output.get("evalue") or "").strip()
        return f"--- output: error {ename}: {evalue}"
    data = output.get("data") or {}
    if not isinstance(data, dict):
        return f"--- output: {kind}"
    for mime in _TEXT_MIMES:
        if mime in data:
            return f"--- output: {kind} ({mime})\n{_clip(_join_source(data[mime]).rstrip())}"
    others = sorted(str(mime) for mime in data)
    if others:
        return f"--- output: {kind} ({', '.join(others)} omitted)"
    return f"--- output: {kind}"


def render_notebook(text: str) -> str | None:
    """Cells and outputs as text, or None when ``text`` is not a notebook."""
    try:
        nb = json.loads(text)
    except ValueError:
        return None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        return None
    cells: list[dict[str, Any]] = [c for c in nb["cells"] if isinstance(c, dict)]
    kernel = ((nb.get("metadata") or {}).get("kernelspec") or {}).get("name")
    header = f"Notebook: {len(cells)} cells" + (f" (kernel: {kernel})" if kernel else "")
    header += ". Cells are numbered for notebook_edit; edit_file does not apply here."
    parts = [header]
    for index, cell in enumerate(cells, start=1):
        cell_type = str(cell.get("cell_type") or "raw")
        label = f"[cell {index}] {cell_type}"
        if cell_type == "code":
            count = cell.get("execution_count")
            if count is not None:
                label += f" (in [{count}])"
        parts.append("")
        parts.append(label)
        parts.append(_join_source(cell.get("source")).rstrip("\n"))
        if cell_type == "code":
            for output in cell.get("outputs") or []:
                if isinstance(output, dict):
                    parts.append(_render_output(output))
    return "\n".join(parts) + "\n"


def _source_as_stored(text: str, like: Any) -> Any:
    """Store ``text`` the way the notebook already stores sources (list or string)."""
    if isinstance(like, str):
        return text
    return text.splitlines(keepends=True)


def _new_cell(cell_type: str, source: str, like: Any) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": _source_as_stored(source, like),
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


class NotebookEditTool(_FsTool):
    """Replace, insert or delete one cell of a Jupyter notebook."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "notebook_edit"

    @property
    def description(self) -> str:
        return (
            "Edit one cell of a Jupyter notebook (.ipynb) by the cell number "
            "read_file shows. replace: new source for that cell (outputs are "
            "cleared); insert_after / insert_before: add a cell next to it "
            "(cell=0 with insert_after appends at the top); delete: remove it. "
            "Use this instead of edit_file for notebooks."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Notebook path (.ipynb)"},
                "cell": {
                    "type": "integer",
                    "description": "1-based cell number as shown by read_file",
                    "minimum": 0,
                },
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "replace (default), insert_after, insert_before or delete",
                },
                "source": {
                    "type": "string",
                    "description": "New cell source (replace / insert)",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown", "raw"],
                    "description": "Cell type for inserts (default code); replace keeps the current type unless given",
                },
            },
            "required": ["path", "cell"],
        }

    async def execute(
        self,
        path: str,
        cell: int,
        action: str = "replace",
        source: str | None = None,
        cell_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            if action not in _ACTIONS:
                return ToolResult.error(f"Error: action must be one of {', '.join(_ACTIONS)}")
            fp = await self._bound_path(path, write=True)
            if not fp.exists():
                return self._missing_path_msg("Notebook", path, fp)
            try:
                nb = json.loads(fp.read_text(encoding="utf-8"))
            except ValueError as e:
                return ToolResult.error(f"Error: {path} is not valid notebook JSON: {e}")
            if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
                return ToolResult.error(f"Error: {path} has no cells array; not a Jupyter notebook")
            cells: list[Any] = nb["cells"]
            total = len(cells)
            like = cells[0].get("source") if cells and isinstance(cells[0], dict) else []

            if action in ("replace", "insert_before", "delete") and not 1 <= cell <= total:
                return ToolResult.error(
                    f"Error: cell {cell} does not exist ({total} cells in {path})"
                )
            if action == "insert_after" and not 0 <= cell <= total:
                return ToolResult.error(
                    f"Error: cell {cell} does not exist ({total} cells in {path}); use 0 to append at the top"
                )
            if action != "delete" and source is None:
                return ToolResult.error(f"Error: source is required for {action}")

            record_file_before(fp)
            if action == "delete":
                removed = cells.pop(cell - 1)
                kind = removed.get("cell_type") if isinstance(removed, dict) else "cell"
                summary = f"Deleted {kind} cell {cell} from {fp} ({len(cells)} cells left)"
            elif action == "replace":
                current = cells[cell - 1] if isinstance(cells[cell - 1], dict) else {}
                new_type = cell_type or str(current.get("cell_type") or "code")
                updated = _new_cell(new_type, source or "", current.get("source", like))
                for key in ("id", "metadata"):
                    if key in current:
                        updated[key] = current[key]
                cells[cell - 1] = updated
                summary = f"Replaced cell {cell} ({new_type}) in {fp}"
            else:
                new_type = cell_type or "code"
                position = cell if action == "insert_after" else cell - 1
                cells.insert(position, _new_cell(new_type, source or "", like))
                summary = f"Inserted {new_type} cell as cell {position + 1} in {fp} ({len(cells)} cells now)"

            fp.write_text(
                json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
            )
            self._file_states.record_write(fp)
            return summary
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error editing notebook: {e}")


def is_notebook(path: Path) -> bool:
    return path.suffix.lower() == ".ipynb"


__all__ = ["NotebookEditTool", "is_notebook", "render_notebook"]
