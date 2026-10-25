# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Scrollable inspector for the agents of the conversation, with recent action details."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static


def _event_lines(event: dict[str, Any]) -> list[str]:
    payload = event.get("payload", {})
    kind = event.get("kind", "")
    label = payload.get("name") or payload.get("role") or kind
    lines = [f"[{event.get('seq', '')}] {label}"]
    if isinstance(payload.get("content"), str):
        lines.append(payload["content"])
    elif kind in {"tool_start", "tool_done"}:
        if payload.get("arguments"):
            lines.append(json.dumps(payload["arguments"], ensure_ascii=False, indent=2))
        if "result" in payload:
            lines.append(str(payload["result"]))
    elif kind == "phase":
        lines.append(str(payload.get("status_line") or payload.get("phase", "")))
    else:
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("tool_calls"):
        lines.append(json.dumps(payload["tool_calls"], ensure_ascii=False, indent=2))
    return lines


# How an agent ended, read the same way as the desktop journal (subagentState).
_STATE_LABELS = {
    "running": "Running", "succeeded": "Succeeded", "failed": "Failed",
    "cancelled": "Cancelled", "interrupted": "Interrupted",
}
# The state filters, in button order: (id suffix, label, states kept).
_FILTERS: tuple[tuple[str, str, frozenset[str] | None], ...] = (
    ("all", "All", None),
    ("running", "Running", frozenset({"running"})),
    ("succeeded", "Succeeded", frozenset({"succeeded"})),
    # Everything that did not finish well, so it can be resumed or relaunched.
    ("errors", "Errors", frozenset({"failed", "cancelled", "interrupted"})),
)


def agent_state(row: dict[str, Any]) -> str:
    """running, succeeded, failed, cancelled or interrupted.

    ``row`` mixes the saved record (``done``, ``error`` text) and the live
    panel row (``done_at`` once finished, ``error`` as a flag).
    """
    if row.get("phase") in {"interrupted", "interrupted_tool"}:
        return "interrupted"
    if not (row.get("done") or "done_at" in row):
        return "running"
    error = row.get("error")
    if isinstance(error, str) and error.strip().lower() in {"cancelled", "canceled"}:
        return "cancelled"
    return "failed" if row.get("phase") == "error" or error else "succeeded"


# Every widget the refresh, detail and operation paths update.
_UI_SELECTOR = ", ".join(f"#{name}" for name in (
    "agents-heading", "agent-filter", "agent-table", "agent-detail",
    "agents-more", "agents-resume", "agents-relaunch",
    "agents-show-all", "agents-show-running", "agents-show-succeeded", "agents-show-errors",
))
_UI_COUNT = _UI_SELECTOR.count("#")


class AgentsScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    AgentsScreen { align: center middle; background: $background; }
    AgentsScreen > Vertical {
        width: 96%; height: 94%; border: solid $primary;
        background: $surface; padding: 1 2;
    }
    AgentsScreen #agents-heading { height: 1; }
    AgentsScreen Input { height: 3; }
    AgentsScreen #agents-filters { height: 3; }
    AgentsScreen #agents-filters Button { margin-right: 1; min-width: 12; }
    AgentsScreen DataTable { height: 2fr; min-height: 4; }
    AgentsScreen #agent-detail-scroll { height: 1fr; min-height: 4; }
    AgentsScreen #agent-detail { height: auto; }
    AgentsScreen #agents-actions { height: 3; }
    AgentsScreen #agents-actions Button { margin-right: 1; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        # The filter field keeps the focus: a plain letter would be typed there.
        Binding("ctrl+r", "restart_selected", "Resume or relaunch", priority=True),
    ]

    def __init__(
        self, snapshot: Callable[[], dict[str, dict[str, Any]]], *,
        list_loader: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        history_loader: Callable[[str, int], Awaitable[dict[str, Any]]] | None = None,
        operation_loader: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._rows: dict[str, dict[str, Any]] = {}
        self._signature: str | None = None
        self._list_loader = list_loader
        self._history_loader = history_loader
        self._operation_loader = operation_loader
        self._durable_rows: dict[str, dict[str, Any]] = {}
        self._selected: str | None = None
        self._events: list[dict[str, Any]] = []
        self._cursor = 0
        self._has_more = False
        self._history_error = ""
        self._state_filter = "all"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Agents - Escape to close", id="agents-heading", markup=False)
            yield Input(placeholder="Filter by name, task or ID", id="agent-filter")
            with Horizontal(id="agents-filters"):
                for suffix, label, _states in _FILTERS:
                    yield Button(label, id=f"agents-show-{suffix}",
                                 variant="primary" if suffix == self._state_filter else "default")
            yield DataTable(id="agent-table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="agent-detail-scroll"):
                yield Static("Select an agent to inspect its recent actions.", id="agent-detail", markup=False)
            with Horizontal(id="agents-actions"):
                yield Button("Back to chat", id="agents-back", variant="primary")
                yield Button("Load more actions", id="agents-more")
                yield Button("Resume agent", id="agents-resume")
                yield Button("Relaunch agent", id="agents-relaunch")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("ID", "Agent", "State", "Current action", "Tokens")
        self._refresh_rows()
        self.set_interval(0.5, self._refresh_rows)
        self.query_one(Input).focus()
        self.query_one("#agents-more", Button).display = False
        self.query_one("#agents-resume", Button).display = False
        self.query_one("#agents-relaunch", Button).display = False
        if self._list_loader is not None:
            self._reload_durable()
            self.set_interval(2, self._reload_durable)

    def _leaving(self) -> bool:
        """True once Textual is taking this screen down (closed, or the app quitting).

        Textual removes the widgets one by one before the screen stops its
        timers and workers. A saved-agents load or a refresh that landed in
        that window found the table already gone and crashed the whole CLI
        (``NoMatches: No nodes match 'DataTable' on AgentsScreen()``). The UI
        methods are synchronous, so widgets present when they start stay
        there until they return.
        """
        return not self.is_attached or len(self.query(_UI_SELECTOR)) != _UI_COUNT

    def _reload_durable(self) -> None:
        if self._leaving():
            return
        self.run_worker(self._load_durable, group="agent-list", exclusive=True)

    async def _load_durable(self) -> None:
        if self._list_loader is None:
            return
        try:
            agents = await self._list_loader()
        except Exception as exc:
            if not self._leaving():
                self.query_one("#agents-heading", Static).update(f"Could not load saved agents: {exc}")
            return
        self._durable_rows = {
            row["task_id"]: {**row, "task": row.get("task_description", ""),
                             "status": row.get("status_line", row.get("phase", ""))}
            for row in agents
        }
        self._refresh_rows()
        if self._selected and not self._has_more:
            self._request_history()

    @on(Input.Changed, "#agent-filter")
    def _filter_changed(self) -> None:
        self._refresh_rows()

    @on(Button.Pressed)
    def _state_filter_pressed(self, event: Button.Pressed) -> None:
        button_id = str(event.button.id or "")
        if button_id.startswith("agents-show-"):
            self.show_state(button_id.removeprefix("agents-show-"))

    def show_state(self, suffix: str) -> None:
        """Keep the agents in one state group: all, running, succeeded or errors."""
        if suffix not in {name for name, _label, _states in _FILTERS} or self._leaving():
            return
        self._state_filter = suffix
        for name, _label, _states in _FILTERS:
            self.query_one(f"#agents-show-{name}", Button).variant = "primary" if name == suffix else "default"
        self._signature = None
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        if self._leaving():
            return
        self._rows = {key: dict(row) for key, row in self._durable_rows.items()}
        for key, row in self._snapshot().items():
            self._rows[key] = {**self._rows.get(key, {}), **row}
        needle = self.query_one(Input).value.strip().casefold()
        keep = next(states for name, _label, states in _FILTERS if name == self._state_filter)
        states = {key: agent_state(row) for key, row in self._rows.items()}
        rows = [(key, row) for key, row in self._rows.items()
                if (keep is None or states[key] in keep)
                and (not needle or needle in f"{key} {row.get('label', '')} {row.get('task', '')}".casefold())]
        signature = repr((rows, [states[key] for key, _row in rows]))
        if signature == self._signature:
            return
        self._signature = signature
        table = self.query_one(DataTable)
        selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value if table.row_count else None
        table.clear()
        for key, row in rows:
            table.add_row(Text(key), Text(str(row.get("label", key))),
                          Text(_STATE_LABELS[states[key]]), Text(str(row.get("status", ""))),
                          str(row.get("tokens", 0)), key=key)
        if selected in table.rows:
            table.move_cursor(row=table.get_row_index(selected))
        counts = {name: sum(1 for state in states.values() if state in group)
                  for name, _label, group in _FILTERS if group is not None}
        self.query_one("#agents-heading", Static).update(
            f"Agents of this conversation: {len(rows)} shown / {len(self._rows)} - "
            f"{counts['running']} running, {counts['succeeded']} succeeded, {counts['errors']} with errors - "
            "Ctrl+R resume or relaunch, Escape to close"
        )
        if rows:
            self._show_detail(str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value))
        else:
            self._selected = None
            for button in ("agents-more", "agents-resume", "agents-relaunch"):
                self.query_one(f"#{button}", Button).display = False
            self.query_one("#agent-detail", Static).update("No matching agents.")

    @on(DataTable.RowHighlighted)
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value is not None:
            self._show_detail(str(event.row_key.value))

    def _show_detail(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or self._leaving():
            return
        if self._selected != key:
            self._selected = key
            self._events = []
            self._cursor = 0
            self._has_more = False
            self._history_error = ""
            self._request_history()
        lines = [f"{row.get('label', key)} [{key}] - {_STATE_LABELS[agent_state(row)]}", str(row.get("task", "")),
                 f"Model: {row.get('model') or '-'}", str(row.get("status", "")), "Recent actions:"]
        for step in row.get("steps", []):
            lines.append(f"[{step.get('status', '')}] {step.get('verb', '')} {step.get('target', '')}".strip())
        if self._events:
            lines.extend(["", "Saved conversation and actions:"])
            for event in self._events:
                lines.extend(_event_lines(event))
        elif self._history_loader is not None:
            lines.extend(["", self._history_error or "Loading saved actions..."])
        self.query_one("#agent-detail", Static).update("\n".join(lines))
        self.query_one("#agents-more", Button).display = self._has_more
        self.query_one("#agents-resume", Button).display = bool(self._operation_loader and row.get("recoverable"))
        self.query_one("#agents-relaunch", Button).display = bool(self._operation_loader and row.get("requires_relaunch"))

    def _request_history(self) -> None:
        if self._history_loader is not None and self._selected is not None and not self._leaving():
            self.run_worker(partial(self._load_history, self._selected, self._cursor),
                            group="agent-history", exclusive=True)

    async def _load_history(self, task_id: str, after: int) -> None:
        assert self._history_loader is not None
        try:
            page = await self._history_loader(task_id, after)
        except Exception as exc:
            if self._selected == task_id:
                self._history_error = f"Could not load saved actions: {exc}"
                self._show_detail(task_id)
            return
        if self._selected != task_id:
            return
        agent = page.get("agent")
        if isinstance(agent, dict):
            saved = {**agent, "task": agent.get("task_description", ""),
                     "status": agent.get("status_line", agent.get("phase", ""))}
            self._durable_rows[task_id] = saved
            self._rows[task_id] = {**self._rows.get(task_id, {}), **saved}
        seen = {event["seq"] for event in self._events}
        self._events.extend(event for event in page.get("events", []) if event["seq"] not in seen)
        self._cursor = int(page.get("next_cursor", after))
        self._has_more = bool(page.get("has_more"))
        self._history_error = "No saved actions yet." if not self._events else ""
        self._show_detail(task_id)

    @on(Button.Pressed, "#agents-more")
    def _more(self) -> None:
        self._request_history()

    @on(Button.Pressed, "#agents-back")
    def _back(self) -> None:
        self.action_close()

    @on(Button.Pressed, "#agents-resume")
    def _resume(self) -> None:
        self.run_worker(partial(self._operate, "recover"), group="agent-operation", exclusive=True)

    @on(Button.Pressed, "#agents-relaunch")
    def _relaunch(self) -> None:
        self.run_worker(partial(self._operate, "relaunch"), group="agent-operation", exclusive=True)

    def action_restart_selected(self) -> None:
        """Ctrl+R: resume the selected agent where it stopped, else relaunch it.

        Offered on the same agents as the desktop journal: an interrupted one
        that can continue, or a failed or ambiguous one to start again.
        """
        row = self._rows.get(self._selected or "")
        if self._operation_loader is None or row is None or self._leaving():
            return
        if row.get("recoverable"):
            self._resume()
        elif row.get("requires_relaunch"):
            self._relaunch()
        else:
            self.query_one("#agents-heading", Static).update(
                f"{row.get('label', self._selected)} is {_STATE_LABELS[agent_state(row)].lower()}: "
                "nothing to resume or relaunch."
            )

    async def _operate(self, operation: str) -> None:
        if self._operation_loader is None or self._selected is None:
            return
        for button in ("agents-resume", "agents-relaunch"):
            self.query_one(f"#{button}", Button).disabled = True
        try:
            result = await self._operation_loader(self._selected, operation)
        except Exception as exc:
            result = {"error": f"Could not {operation} agent: {exc}"}
        if self._leaving():
            return
        detail = str(result.get("detail") or result.get("error") or result.get("result") or result)
        self.query_one("#agents-heading", Static).update(detail)
        for button in ("agents-resume", "agents-relaunch"):
            self.query_one(f"#{button}", Button).disabled = False
        if result.get("ok"):
            self._signature = None
            self._request_history()
            self._reload_durable()

    def action_close(self) -> None:
        self.dismiss(None)
