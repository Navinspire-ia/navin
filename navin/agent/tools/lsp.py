# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Language server tool: types, type-resolved navigation, and safe renames.

Complements ``code_index``. The index is instant and always available but works
from syntax, so it can confuse same-named symbols. A language server resolves
types properly: it knows which ``save`` you mean, what a variable's type is, and
which call sites a rename really touches. Use the index to explore, this to be
certain before changing something.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.agent.tools.atomic_write import write_all
from navin.agent.tools.base import ToolResult
from navin.agent.tools.quality import _QualityTool
from navin.utils import text_decode


def _utf16_index(text: str, units: int) -> int:
    """Map an LSP column, counted in UTF-16 code units, onto a string index.

    The protocol's default position encoding is UTF-16 and the client does not
    negotiate anything else, so a line containing an emoji or an accented word
    before the symbol reports a column that is not the character offset. Slicing
    on the raw number would then cut the line in the wrong place.
    """
    if units <= 0:
        return 0
    consumed = 0
    for index, char in enumerate(text):
        if consumed >= units:
            return index
        consumed += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def _identifier_at(path: Path, line: int, col: int) -> str:
    """The identifier surrounding a 1-based position, or an empty string."""
    try:
        decoded = text_decode.decode(path.read_bytes())
    except OSError:
        return ""
    if decoded is None:
        return ""
    lines = decoded.text.split("\n")
    if not 1 <= line <= len(lines):
        return ""
    row = lines[line - 1]
    index = _utf16_index(row, col - 1)
    if index >= len(row) or not (row[index].isalnum() or row[index] == "_"):
        return ""
    start = index
    while start > 0 and (row[start - 1].isalnum() or row[start - 1] == "_"):
        start -= 1
    end = index
    while end < len(row) and (row[end].isalnum() or row[end] == "_"):
        end += 1
    return row[start:end]


def _apply_edits(text: str, rows: list[dict[str, Any]]) -> str:
    """Apply one file's rename edits, last position first.

    Working backwards keeps every not-yet-applied position valid: replacing a
    shorter name with a longer one shifts everything after it on the line.
    """
    lines = text.split("\n")
    ordered = sorted(
        rows,
        key=lambda row: (int(row["line"]), int(row["col"])),
        reverse=True,
    )
    for row in ordered:
        start_line = int(row["line"]) - 1
        end_line = int(row["end_line"]) - 1
        if not (0 <= start_line <= end_line < len(lines)):
            raise ValueError(
                f"the server reported an edit at line {row['line']} but the file "
                f"has {len(lines)} lines; it may have changed since it was opened"
            )
        start = _utf16_index(lines[start_line], int(row["col"]) - 1)
        end = _utf16_index(lines[end_line], int(row["end_col"]) - 1)
        head = lines[start_line][:start]
        tail = lines[end_line][end:]
        lines[start_line : end_line + 1] = [head + str(row["new_text"]) + tail]
    return "\n".join(lines)


_MAX_SHOWN = 60
_ACTIONS = (
    "hover",
    "definition",
    "references",
    "diagnostics",
    "rename",
    "symbols",
    "servers",
)


class LspTool(_QualityTool):
    """Semantic queries answered by a real language server."""

    @property
    def name(self) -> str:
        return "lsp"

    @property
    def description(self) -> str:
        return (
            "Ask a language server type-aware questions about code. 'hover' "
            "gives the resolved type or signature at a position; 'definition' "
            "and 'references' are type-resolved, so unlike grep or code_index "
            "they do not confuse same-named symbols; 'diagnostics' returns "
            "semantic errors a linter cannot see (wrong types, unknown "
            "attributes); 'rename' lists every edit a rename would make across "
            "files, and with apply=true performs them itself, which is safer "
            "than reproducing them by hand - it is refused if the index knows a "
            "use the server missed; 'symbols' outlines a file "
            "or searches the workspace. Always use 'references' before changing "
            "or removing a shared function, so you see the callers you would "
            "break. Positions are 1-based line and column. Use 'servers' to see "
            "which language servers are installed; when none is, fall back to "
            "code_index."
        )

    @property
    def read_only(self) -> bool:
        """Every action queries, except a rename asked to apply itself."""
        return True

    def call_concurrency_safe(self, arguments: Any) -> bool:
        """Keep an applying rename out of a parallel batch.

        The queries stay batchable, which matters because each one waits on a
        server round-trip; a call that writes files must not run beside another
        tool that might be reading or writing the same ones.
        """
        if isinstance(arguments, dict) and arguments.get("apply"):
            return False
        return super().call_concurrency_safe(arguments)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "Query to run",
                },
                "path": {
                    "type": "string",
                    "description": "Project-relative file path",
                },
                "line": {
                    "type": "integer",
                    "description": "1-based line number of the symbol",
                },
                "col": {
                    "type": "integer",
                    "description": "1-based column, inside the symbol name",
                },
                "new_name": {
                    "type": "string",
                    "description": "Replacement identifier (for action=rename)",
                },
                "apply": {
                    "type": "boolean",
                    "description": (
                        "For action=rename: write the server's own edits to disk, "
                        "all files or none. Without it the rename is only "
                        "previewed. Prefer this over reproducing the edits with "
                        "apply_patch, which risks missing a call site."
                    ),
                    "default": False,
                },
                "query": {
                    "type": "string",
                    "description": (
                        "For action=symbols: search the workspace for this name. "
                        "Omit to outline the file at 'path'."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        path: str | None = None,
        line: int | None = None,
        col: int | None = None,
        new_name: str | None = None,
        query: str | None = None,
        apply: bool = False,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        from navin.lsp import LspManager, available_servers
        from navin.lsp.client import LspError

        if action == "servers":
            rows = available_servers(root)
            lines = ["Language servers:"]
            for row in rows:
                mark = "yes" if row["available"] else "no "
                detail = row["path"] if row["available"] else row["reason"]
                lines.append(
                    f"  [{mark}] {row['server']} "
                    f"({', '.join(row['languages'])}) - {detail}"
                )
            if not any(row["available"] for row in rows):
                lines.append("")
                lines.append(
                    "None installed. Use code_index for navigation, or install "
                    "one (pyright, typescript-language-server, gopls, "
                    "rust-analyzer, python-lsp-server)."
                )
            return "\n".join(lines)

        if action not in _ACTIONS:
            return self.unknown_action(action)

        # Accepts the absolute spelling of a workspace file, since that is what
        # read_file just answered with; an absolute path outside the project is
        # refused with the root named rather than reported as "not found".
        rel, path_error = self._project_relative(path, root)
        needs_path = action != "symbols" or not (query or "").strip()
        if needs_path:
            if path_error:
                return path_error
            if not rel:
                return ToolResult.error(f"Error: action={action} requires 'path'")
            if not (root / rel).is_file():
                return self._missing_project_file(rel, root)

        needs_position = action in {"hover", "definition", "references", "rename"}
        if needs_position and (line is None or col is None):
            return ToolResult.error(
                f"Error: action={action} requires 'line' and 'col' (1-based, "
                "pointing inside the symbol name)"
            )

        manager = LspManager.for_root(root)
        try:
            return await asyncio.to_thread(
                self._run,
                manager,
                root,
                action,
                rel,
                line,
                col,
                new_name,
                query,
                bool(apply),
            )
        except LspError as exc:
            return ToolResult.error(
                f"Error: {exc}\nFall back to code_index for syntax-level navigation."
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResult.error(f"Error querying language server: {exc}")

    def _unseen_users(
        self, root: Path, symbol: str, touched: set[str]
    ) -> list[str]:
        """Project files that use ``symbol`` but are absent from the edit set.

        Servers disagree on how much of a project they analyse. Pyright, asked to
        rename a function, answers with the one file it has loaded and says nothing
        about the module importing it; pylsp with rope reports both. Applying the
        narrow answer silently breaks every caller it omitted, so the index - which
        parses the whole project - gets a veto.
        """
        if not symbol:
            return []
        try:
            from navin.index import get_index

            index = get_index(root)
            index.ensure()
            locations, _total = index.references(symbol)
            users = {
                location.path for location in locations if location.path not in touched
            }
        except Exception:
            # The index is an extra safety net, never a prerequisite.
            return []
        return sorted(users)

    def _best_rename(
        self,
        manager: Any,
        root: Path,
        rel: str,
        line: int,
        col: int,
        new_name: str,
        symbol: str,
        text: str | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str], str]:
        """The first server answer the index does not contradict.

        Several servers can claim the same file, and they are not equally good at
        this question: pyright answers a rename from the files it has finished
        loading, so a caller in a module it has not reached yet is simply missing,
        while pylsp's rope backend walks the project. Rather than pick a winner for
        every project, ask them in priority order and keep the first answer whose
        file set the index agrees is complete.
        """
        try:
            candidates = manager.installed_servers_for(rel) or [""]
        except Exception:
            candidates = [""]
        best: tuple[dict[str, list[dict[str, Any]]], list[str], str] | None = None
        for attempt, name in enumerate(candidates):
            rename_kwargs: dict[str, Any] = {"exclude": candidates[:attempt]}
            if text is not None:
                rename_kwargs["text"] = text
            edits = manager.rename(rel, line, col, new_name, **rename_kwargs)
            unseen = self._unseen_users(root, symbol, set(edits))
            if edits and not unseen:
                return edits, [], name
            # Keep the first answer as the fallback: it came from the best server.
            if best is None or (edits and not best[0]):
                best = (edits, unseen, name)
        return best or ({}, [], "")

    def _apply_rename(
        self, edits: dict[str, list[dict[str, Any]]]
    ) -> tuple[list[Path], str | None]:
        """Turn the server's edit set into files on disk, or change nothing.

        Reproducing these edits by hand is what the agent had to do before, and a
        rename is the one refactor where that is both purely mechanical and
        unforgiving: one missed call site and the project no longer imports.

        Returns the files written, or a message explaining the refusal.
        """
        writes: dict[Path, str] = {}
        encodings: dict[Path, text_decode.DecodedText] = {}
        for rel_path, rows in edits.items():
            try:
                target = self._resolve_write(rel_path)
            except PermissionError:
                # Servers report stubs and vendored dependencies too. Writing
                # part of a rename would be worse than writing none of it.
                return [], (
                    f"Refused: the server wants to edit {rel_path}, which is "
                    "outside the project. Rename the symbol in your own code "
                    "first, or narrow the change."
                )
            if not target.is_file():
                return [], f"Refused: {rel_path} is not a file on disk."
            decoded = text_decode.decode(target.read_bytes())
            if decoded is None:
                return [], f"Refused: {rel_path} is not text."
            encodings[target] = decoded
            try:
                writes[target] = _apply_edits(decoded.text, rows)
            except ValueError as exc:
                return [], f"Refused: {rel_path}: {exc}"
        if not writes:
            return [], "Refused: the server returned no applicable edit."
        write_all(writes, encodings=encodings, file_states=self._file_states)
        return list(writes), None

    def _run(
        self,
        manager: Any,
        root: Path,
        action: str,
        rel: str,
        line: int | None,
        col: int | None,
        new_name: str | None,
        query: str | None,
        apply: bool = False,
    ) -> str:
        if action == "hover":
            text = manager.hover(rel, int(line or 1), int(col or 1))
            if not text:
                return f"No type information at {rel}:{line}:{col}."
            return f"{rel}:{line}:{col}\n\n{text}"

        if action == "definition":
            locations = manager.definition(rel, int(line or 1), int(col or 1))
            if not locations:
                return f"No definition found at {rel}:{line}:{col}."
            lines = [f"Definition of the symbol at {rel}:{line}:{col}:"]
            lines.extend(f"  {loc.render()}" for loc in locations[:_MAX_SHOWN])
            return "\n".join(lines)

        if action == "references":
            locations = manager.references(rel, int(line or 1), int(col or 1))
            if not locations:
                return (
                    f"No references found at {rel}:{line}:{col}. The symbol may be "
                    "unused, or the server may still be indexing."
                )
            by_file: dict[str, int] = {}
            for loc in locations:
                by_file[loc.path] = by_file.get(loc.path, 0) + 1
            lines = [
                f"{len(locations)} reference(s) across {len(by_file)} file(s) - "
                "these are the call sites a change here would affect:"
            ]
            lines.extend(f"  {loc.render()}" for loc in locations[:_MAX_SHOWN])
            if len(locations) > _MAX_SHOWN:
                lines.append(f"  … {len(locations) - _MAX_SHOWN} more")
            return "\n".join(lines)

        if action == "diagnostics":
            rows = manager.diagnostics(rel)
            if not rows:
                return f"No semantic diagnostics for {rel}."
            errors = [r for r in rows if r["severity"] == "error"]
            lines = [
                f"{len(errors)} error(s), {len(rows) - len(errors)} other "
                f"diagnostic(s) in {rel}:"
            ]
            for row in rows[:_MAX_SHOWN]:
                code = f" [{row['code']}]" if row["code"] else ""
                lines.append(
                    f"  {rel}:{row['line']}:{row['col']} {row['severity']}"
                    f"{code} {row['message']}"
                )
            return "\n".join(lines)

        if action == "rename":
            target = (new_name or "").strip()
            if not target:
                return ToolResult.error("Error: action=rename requires 'new_name'")
            symbol = _identifier_at(root / rel, int(line or 1), int(col or 1))
            edits, unseen, server = self._best_rename(
                manager, root, rel, int(line or 1), int(col or 1), target, symbol
            )
            if not edits:
                return (
                    f"The server produced no rename for {rel}:{line}:{col} - the "
                    "position may not be a renameable symbol."
                )
            total = sum(len(rows) for rows in edits.values())
            lines = [
                f"Rename to '{target}' touches {total} location(s) in "
                f"{len(edits)} file(s):"
            ]
            for file_path in sorted(edits):
                rows = edits[file_path]
                positions = ", ".join(
                    f"{row['line']}:{row['col']}" for row in rows[:8]
                )
                more = f" (+{len(rows) - 8} more)" if len(rows) > 8 else ""
                lines.append(f"  {file_path} - {len(rows)} edit(s) at {positions}{more}")
            lines.append("")
            if unseen:
                lines.append(
                    f"Warning: {server} covered {len(edits)} file(s) but the index "
                    f"finds '{symbol}' also used in: {', '.join(unseen[:6])}"
                    + (" …" if len(unseen) > 6 else "")
                )
                lines.append(
                    "A server that has not finished analysing the project answers "
                    "only for the files it loaded. Inspect the rest with code_index "
                    f"action=references name={symbol}."
                )
                lines.append("")
            if not apply:
                lines.append(
                    "Nothing was written. Re-run with apply=true to let the server's "
                    "own edits be applied, which is safer than reproducing them by "
                    "hand, then confirm with verify action=check."
                )
                return "\n".join(lines)
            if unseen:
                return ToolResult.error(
                    "\n".join(lines)
                    + "\nError: applying this would leave those uses pointing at a "
                    "name that no longer exists. Use apply=false to inspect, or edit "
                    "the remaining files yourself with apply_patch."
                )
            written, problem = self._apply_rename(edits)
            if problem:
                lines.append(problem)
                return ToolResult.error("\n".join(lines))
            lines.append(f"Applied {total} edit(s) across {len(written)} file(s).")
            lines.append("Confirm with verify action=check.")
            message = "\n".join(lines)
            # write_file and edit_file attach what the linters think of the
            # result; a rename that breaks an import must not be the one write
            # that reports nothing. ``_run`` already executes off the event
            # loop (execute wraps it in asyncio.to_thread), so the linter
            # subprocesses cannot stall other sessions from here.
            if self._lint_after_edit:
                from navin.agent.tools.edit_feedback import diagnostics_after_write

                report = diagnostics_after_write(written, workspace=root)
                if report:
                    return f"{message}\n\n{report}"
            return message

        if action == "symbols":
            search = (query or "").strip()
            if search:
                locations = manager.workspace_symbols(search, rel)
                if not locations:
                    return f"No workspace symbol matches '{search}'."
                lines = [f"Workspace symbols matching '{search}':"]
            else:
                locations = manager.document_symbols(rel)
                if not locations:
                    return f"No symbols reported for {rel}."
                lines = [f"Symbols in {rel}:"]
            lines.extend(f"  {loc.render()}" for loc in locations[:_MAX_SHOWN])
            if len(locations) > _MAX_SHOWN:
                lines.append(f"  … {len(locations) - _MAX_SHOWN} more")
            return "\n".join(lines)

        raise ValueError(f"unhandled action: {action}")  # pragma: no cover - validated upstream
