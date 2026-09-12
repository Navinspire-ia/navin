# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persistent developer activity for CLI sessions without the full-screen TUI."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from navin.utils.file_edit_events import file_edit_details
from navin.utils.tool_hints import (
    MAX_TRANSCRIPT_LINES,
    activity_head_text,
    activity_label,
    file_operation_label,
    format_tool_preview_markup,
    tool_cluster_kind,
    tool_verb,
)


class ActivityPrinter:
    """Render structured events once, preserving full commands and real diffs."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._calls: dict[str, dict[str, Any]] = {}
        self._file_calls: set[str] = set()
        self._seen_files: set[tuple[str, str, str]] = set()
        self._group = ""

    def break_group(self) -> None:
        self._group = ""

    def consume(
        self, *, tool_events: list[dict[str, Any]] | None = None,
        file_edit_events: list[dict[str, Any]] | None = None,
    ) -> None:
        for event in tool_events or []:
            if isinstance(event, dict):
                self._tool(event)
        edits = []
        for payload in file_edit_events or []:
            if not isinstance(payload, dict):
                continue
            edit = file_edit_details(payload)
            if edit["phase"] == "start" or not edit["path"]:
                continue
            key = (edit["call_id"], edit["path"], edit["phase"])
            if key in self._seen_files:
                continue
            self._seen_files.add(key)
            self._file_calls.add(edit["call_id"])
            edits.append(edit)
        if edits:
            self._files(edits)

    def _head(self, label: str) -> None:
        self.console.print(activity_head_text(label), highlight=False)

    def _tool(self, event: dict[str, Any]) -> None:
        key = str(event.get("call_id") or "")
        state = self._calls.setdefault(key, {"output": "", "done": False, "printed": False})
        state["name"] = event.get("name") or state.get("name") or "tool"
        if event.get("arguments"):
            state["arguments"] = event["arguments"]
        phase = event.get("phase") or "start"
        if state["done"]:
            return
        if phase in {"start", "output"} and tool_verb(state["name"]) == "run" and not state["printed"]:
            self.break_group()
            self._head("• " + activity_label(state["name"], state.get("arguments"), phase="start").replace("\n", "\n  │ "))
            state["printed"] = True
        if phase == "start":
            return
        if phase == "output":
            if isinstance(event.get("output"), str):
                chunk = event["output"]
                state["output"] += chunk
                if state["printed"]:
                    self.console.print(Text.from_ansi(chunk), end="", highlight=False)
            return
        state["done"] = True
        if key in self._file_calls:
            return
        name = state["name"]
        args = state.get("arguments") or {}
        family = tool_cluster_kind(name)
        label = activity_label(name, args, phase=phase)
        result = event.get("result")
        error = event.get("error")
        if state["printed"]:
            if state["output"] and not state["output"].endswith("\n"):
                self.console.print()
            self._head("  └ " + ("Completed" if phase == "end" else "Cancelled" if phase == "cancelled" else "Failed"))
            # The terminal already contains streamed stdout. Print only any
            # additional completion metadata, not a second copy of the run.
            output = state["output"].strip()
            if output:
                if isinstance(result, str):
                    result = result.replace(output, "", 1).strip()
                elif isinstance(result, dict):
                    result = dict(result)
                    for field in ("output", "stdout", "stderr", "text", "content"):
                        if isinstance(result.get(field), str):
                            result[field] = result[field].replace(output, "", 1).strip()
                if isinstance(error, str):
                    error = error.replace(output, "", 1).strip()
            self._body(name, args, result=result, error=error)
            return
        if family == "explore":
            if self._group != "explore":
                self._head("• Explored")
            self._head(f"  └ {label}")
            self._group = "explore"
            if phase not in {"error", "cancelled"}:
                return
        else:
            self.break_group()
            self._head(f"{'×' if phase in {'error', 'cancelled'} else '•'} {label}".replace("\n", "\n  │ "))
        self._body(
            name, args, result=result, error=error,
            output_lines=state["output"].splitlines(),
        )

    def _files(self, edits: list[dict[str, Any]]) -> None:
        self.break_group()
        if len(edits) > 1:
            done = [edit for edit in edits if edit["phase"] == "end"]
            operations = {file_operation_label(edit["kind"]) for edit in done}
            title = next(iter(operations)) if len(operations) == 1 else "Edited" if done else "Edits"
            count = len(done or edits)
            noun = "file" if count == 1 else "files"
            counted = [edit for edit in done if not edit["binary"]]
            suffix = f" (+{sum(e['added'] for e in counted)} -{sum(e['removed'] for e in counted)})" if counted else ""
            if len(counted) < len(done):
                suffix += " · some line counts unavailable"
            self._head(f"• {title} {count} {noun}{suffix}")
        for edit in edits:
            label = activity_label(
                edit["tool"], {}, path=edit["path"], phase=edit["phase"],
                operation=edit["kind"] if edit["phase"] == "end" else "",
                added=edit["added"], removed=edit["removed"],
                counts_known=not edit["binary"],
            )
            self._head(("  └ " if len(edits) > 1 else "• ") + label)
            self._body(
                edit["tool"], {"path": edit["path"]}, diff_text=edit["diff"] if edit["phase"] == "end" else "",
                error=edit["error"],
            )
            if edit["truncated"]:
                self.console.print(Text("  Diff truncated by source. Counts cover the whole change.", style="dim"))
            elif edit["binary"]:
                self.console.print(Text("  Preview and line counts unavailable (binary, large or unreadable file).", style="dim"))

    def _body(self, name: str, arguments: dict, **kwargs: Any) -> None:
        body = format_tool_preview_markup(
            name, arguments, limit=MAX_TRANSCRIPT_LINES,
            width=max(1, self.console.width - 2), **kwargs,
        )
        if body:
            self.console.print(Text.from_markup(body), highlight=False)
