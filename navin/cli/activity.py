# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persistent developer activity for CLI sessions without the full-screen TUI."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.text import Text

from navin.utils.command_output import (
    GIT_PREVIEW_LINES,
    command_exit_code,
    compact_command_rows,
    is_exit_line,
    is_git_command,
)
from navin.utils.file_edit_events import file_edit_details
from navin.utils.task_progress import parse_progress_from_output, progress_bar
from navin.utils.tool_hints import (
    MAX_TRANSCRIPT_LINES,
    activity_head_text,
    activity_label,
    activity_palette,
    file_operation_label,
    format_tool_preview_markup,
    humanize_tool_error,
    is_validation_pending,
    preview_rows,
    redact_command,
    result_summary,
    tool_cluster_kind,
    tool_verb,
)

# Test totals survive the tail of a finished command (same rule as the TUI).
_TEST_TOTALS = re.compile(r"\b\d+ (?:passed|failed|tests?|errors?)\b", re.IGNORECASE)
_EXIT_RE = re.compile(r"^Exit code: (-?\d+)\s*$", re.MULTILINE)


def _poll_group(name: str, args: dict[str, Any]) -> str:
    """The group of a plain check on a running command, else ""."""
    if name != "write_stdin" or args.get("chars") or args.get("terminate"):
        return ""
    return f"poll:{args.get('session_id') or ''}"


def _is_command(name: str) -> bool:
    return tool_verb(name) == "run" or name == "test_run"


def status_glyph(name: str, phase: str, result: Any = None, error: str | None = None) -> str:
    """Claude Code style: green check when done, red cross when it failed."""
    if phase == "error" and is_validation_pending(name, error):
        return "!"
    if phase in {"cancelled", "interrupted"}:
        return "•"
    code = command_exit_code(result) if _is_command(name) else None
    if phase == "error" or code:
        return "×"
    return "✓"


class ActivityPrinter:
    """Render structured events once, preserving full commands and real diffs."""

    def __init__(
        self, console: Console, on_status: Callable[[str], None] | None = None,
        on_step_end: Callable[[], None] | None = None,
    ) -> None:
        self.console = console
        # Live status for the spinner ("Running pytest", "Read a.py"): what
        # is happening now, without printing a line per update.
        self.on_status = on_status
        # Told when a call finishes: the next model call starts from there.
        self.on_step_end = on_step_end
        self._calls: dict[str, dict[str, Any]] = {}
        self._anonymous = 0
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
            # A failed edit changed nothing: the agent retries, the final
            # message reports a real failure. No "Failed" row.
            if edit["phase"] in {"start", "error"} or not edit["path"]:
                continue
            key = (edit["call_id"], edit["path"], edit["phase"])
            if key in self._seen_files:
                continue
            self._seen_files.add(key)
            self._file_calls.add(edit["call_id"])
            edits.append(edit)
        if edits:
            self._files(edits)

    def _head(self, label: str, summary: str = "") -> None:
        head = activity_head_text(label)
        if summary:
            head.append(f"  {summary}", style=activity_palette()["muted"])
        self.console.print(head, highlight=False)

    def _outcome(self, summary: str, *, first: bool = True) -> None:
        if summary:
            lead = "  └ " if first else "    "
            self.console.print(Text(f"{lead}{summary}", style=activity_palette()["muted"]), highlight=False)

    def _status(self, text: str) -> None:
        if self.on_status is not None and text:
            try:
                self.on_status(text)
            except Exception:  # noqa: BLE001 - a status line must never break output
                pass

    def _compact_run(self, name: str) -> bool:
        return self.console.is_terminal and (tool_verb(name) == "run" or name == "test_run")

    def _call_key(self, event: dict[str, Any]) -> str:
        """The call id, else a key of its own for each call without one.

        Parallel calls without ids must not share a row: a start opens a new
        key, a later event joins the running call with the same arguments,
        else the oldest running call of that tool.
        """
        call_id = str(event.get("call_id") or "")
        if call_id:
            return call_id
        name = event.get("name") or "tool"
        phase = event.get("phase") or "start"
        if phase != "start":
            running = [
                (key, state) for key, state in self._calls.items()
                if key.startswith("\x00") and state.get("name") == name and not state["done"]
            ]
            arguments = event.get("arguments")
            same = [key for key, state in running if arguments and state.get("arguments") == arguments]
            if same or running:
                return (same or [running[0][0]])[0]
        self._anonymous += 1
        return f"\x00{name}:{self._anonymous}"

    def _running_label(self) -> str:
        """The newest call still running, for the live status."""
        for state in reversed(self._calls.values()):
            if state.get("announced") and not state["done"]:
                label = activity_label(state["name"], state.get("arguments"), phase="start").splitlines()[0]
                return label + ("  " + state["bar"] if state.get("bar") else "")
        return ""

    def _tool(self, event: dict[str, Any]) -> None:
        key = self._call_key(event)
        state = self._calls.setdefault(key, {"output": "", "done": False, "printed": False})
        state["name"] = event.get("name") or state.get("name") or "tool"
        if event.get("arguments"):
            state["arguments"] = event["arguments"]
        phase = event.get("phase") or "start"
        if state["done"]:
            return
        name = state["name"]
        if phase in {"start", "output"} and not state.get("announced"):
            state["announced"] = True
            self._status(activity_label(name, state.get("arguments"), phase="start").splitlines()[0])
        if phase in {"start", "output"} and _is_command(name) and not state["printed"] and not self._compact_run(name):
            # A pipe gets the raw stream: say once what it belongs to.
            self.break_group()
            self._head("• " + activity_label(name, state.get("arguments"), phase="start").replace("\n", "\n  │ "))
            # A pipe or log keeps the exact command under its short heading.
            raw = (state.get("arguments") or {}).get("command") or (state.get("arguments") or {}).get("cmd")
            if isinstance(raw, str) and raw.strip():
                self._head("  $ " + redact_command(raw.strip()).replace("\n", "\n  │ "))
            state["printed"] = True
        if phase == "start":
            return
        if phase == "output":
            if isinstance(event.get("output"), str):
                chunk = event["output"]
                if event.get("output_mode") == "snapshot":
                    previous = state["output"]
                    state["output"] = chunk
                    if chunk.startswith(previous):
                        chunk = chunk[len(previous):]
                    else:
                        # A bounded tail may have dropped its oldest lines.
                        for overlap in range(min(len(previous), len(chunk)), 0, -1):
                            if previous.endswith(chunk[:overlap]):
                                chunk = chunk[overlap:]
                                break
                else:
                    state["output"] += chunk
                if state["printed"] and chunk:
                    # Text.from_ansi drops a trailing newline: keep the line
                    # breaks of the stream, or a pipe reads "step 1step 2".
                    self.console.print(Text.from_ansi(chunk), end="\n" if chunk.endswith("\n") else "", highlight=False)
            percent = event.get("percent")
            if not isinstance(percent, (int, float)):
                percent = parse_progress_from_output(state["output"][-4000:]).get("percent")
            bar = progress_bar(percent)
            if bar and bar != state.get("bar"):
                # Progress moves the live status; it never prints a new line.
                state["bar"] = bar
                self._status(activity_label(name, state.get("arguments"), phase="start").splitlines()[0] + "  " + bar)
            return
        state["done"] = True
        # The live status moves to the newest call still running, else the
        # turn simply goes on.
        self._status(self._running_label() or "Working…")
        if self.on_step_end is not None:
            self.on_step_end()
        if str(event.get("call_id") or "") in self._file_calls:
            return
        args = state.get("arguments") or {}
        poll_group = _poll_group(name, args)
        if poll_group and poll_group == self._group:
            # The same running command checked again: one row, not one per
            # check. Only its end is worth a line.
            self._fold_poll(event.get("result"), event.get("error"))
            return
        self._end_row(name, args, phase, event, state)
        if poll_group:
            self._group = poll_group

    def _fold_poll(self, result: Any, error: Any) -> None:
        if error:
            self._outcome(humanize_tool_error(error))
            return
        text = result if isinstance(result, str) else ""
        found = _EXIT_RE.search(text)
        if found is None:
            return
        code = int(found.group(1))
        totals = [
            Text.from_ansi(line).plain.strip() for line in text.splitlines()
            if _TEST_TOTALS.search(line)
        ]
        summary = f"{'✓' if code == 0 else '×'} Finished, exit {code}"
        if totals:
            summary += f" · {' '.join(totals[-1].split())}"
        self._outcome(summary)

    def _end_row(
        self, name: str, args: dict[str, Any], phase: str,
        event: dict[str, Any], state: dict[str, Any],
    ) -> None:
        family = tool_cluster_kind(name)
        label = activity_label(name, args, phase=phase)
        result = event.get("result")
        error = event.get("error")
        glyph = status_glyph(name, phase, result, error)
        output_lines = state["output"].splitlines()
        summary = result_summary(name, args, result, output_lines, phase=phase, error=error)
        if self._compact_run(name):
            code = command_exit_code(result)
            if state.get("bar") and code == 0 and phase == "end":
                # A bar only for a command that reported its progress.
                label += "  " + progress_bar(100)
            self.break_group()
            self._head(f"{glyph} {label}")
            self._outcome(summary)
            self._body(name, args, result=result, error=error, output_lines=output_lines, tail=True)
            return
        if state["printed"]:
            if state["output"] and not state["output"].endswith("\n"):
                self.console.print()
            # The terminal already contains the streamed stdout. Print only
            # any additional completion output, then the row's outcome.
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
            self._head(f"  └ {glyph} {summary or ('Cancelled' if phase == 'cancelled' else 'Done')}")
            return
        if phase == "error" and not is_validation_pending(name, error):
            # A failed step: a red cross and a short reason, never the raw
            # error. The agent may retry; the final message says the outcome.
            self.break_group()
            self._head(f"{glyph} {label}".replace("\n", "\n  │ "))
            self._outcome(summary or humanize_tool_error(error))
            return
        if family == "explore":
            if self._group != "explore":
                self._head("• Explored")
            self._head(f"  └ {glyph} {label}", summary)
            self._group = "explore"
            if phase != "cancelled":
                return
        else:
            self.break_group()
            self._head(f"{glyph} {label}".replace("\n", "\n  │ "))
            rows = preview_rows(name, args, result=result, error=error, output_lines=output_lines, limit=3)
            if len(rows) <= 2 and not any(row[1] in {"add", "del"} for row in rows):
                # A short answer reads as the row's outcome, not a numbered listing.
                for index, row in enumerate(rows[:2]):
                    self._outcome(row[2].strip(), first=not index)
                if not rows:
                    self._outcome(summary)
                return
        printed = self._body(
            name, args, result=result, error=error,
            output_lines=output_lines,
        )
        if not printed and family != "explore":
            self._outcome(summary)

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
            self._head(f"{'✓' if done else '•'} {title} {count} {noun}{suffix}")
        for edit in edits:
            label = activity_label(
                edit["tool"], {}, path=edit["path"], phase=edit["phase"],
                operation=edit["kind"] if edit["phase"] == "end" else "",
                added=edit["added"], removed=edit["removed"],
                counts_known=not edit["binary"],
            )
            glyph = status_glyph(edit["tool"], edit["phase"])
            self._head(("  └ " if len(edits) > 1 else "") + f"{glyph} {label}")
            self._body(
                edit["tool"], {"path": edit["path"]}, diff_text=edit["diff"] if edit["phase"] == "end" else "",
                error=edit["error"],
            )
            if edit["truncated"]:
                self.console.print(Text("  Diff truncated by source. Counts cover the whole change.", style="dim"))
            elif edit["binary"] and edit["phase"] == "end":
                self.console.print(Text("  No text preview.", style="dim"))

    def _body(self, name: str, arguments: dict, *, tail: bool = False, **kwargs: Any) -> bool:
        compact = self._compact_run(name)
        command = compact or (self.console.is_terminal and name == "git")
        omitted = 0
        if command:
            rows = preview_rows(name, arguments, include_run_output=True, **kwargs)
            compact = compact and not is_git_command(name, arguments) and not any(row[1] in {"add", "del"} for row in rows)
            summary = compact_command_rows(rows) if compact else rows[:GIT_PREVIEW_LINES]
            if compact and tail:
                # The outcome line carries the exit code: keep the error or
                # test totals, else the last line or two of output.
                errors = [row for row in summary if row[1] == "failure" and not is_exit_line(row[2])]
                totals = [row for row in summary if _TEST_TOTALS.search(row[2]) and row not in errors]
                kept = [*errors[-1:], *totals[-1:]] or [
                    row for row in summary if row[2].strip() and not is_exit_line(row[2])
                ][-2:]
                summary = [(index, row[1], row[2]) for index, row in enumerate(kept[-2:], 1)]
                omitted = 0
            else:
                omitted = len(rows) - len(summary)
            if not summary:
                return False
            kwargs["rows"] = summary
        body = format_tool_preview_markup(
            name, arguments, limit=MAX_TRANSCRIPT_LINES,
            width=max(1, self.console.width - 2), compact=compact, **kwargs,
        )
        if body:
            self.console.print(Text.from_markup(body), highlight=False)
        if omitted:
            self.console.print(Text(f"  … {omitted} lines omitted from preview", style="dim"))
        return bool(body)
