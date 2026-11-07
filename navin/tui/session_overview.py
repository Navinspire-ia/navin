# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Fast, session-scoped terminal views for saved prompts and board work."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from navin.session.history_visibility import is_hidden_history_message

PAGE_SIZE = 20
STATUS_FR = {
    "done": "Terminée", "cancelled": "Annulée", "blocked": "Bloquée",
    "fix": "À corriger", "failed": "Échec", "in_progress": "En cours", "review": "En revue",
    "audit": "En audit", "backlog": "En attente", "planned": "Planifiée",
}


def collect_prompt_history(messages: list[Any]) -> list[dict[str, str]]:
    """One pass over the transcript; never copy each prompt's entire remaining tail."""
    prompts: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict) or is_hidden_history_message(message):
            continue
        if message.get("injected_event") or message.get("_channel_delivery") or message.get("_command"):
            continue
        role = message.get("role")
        if role == "user":
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(str(part.get("text")) for part in content
                                    if isinstance(part, dict) and isinstance(part.get("text"), str))
            if not isinstance(content, str) or not content.strip():
                continue
            stamp = str(message.get("timestamp") or "")
            try:
                date = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")
            except ValueError:
                date = "Date inconnue"
            prompts.append({"date": date, "text": content.strip(), "answered": ""})
        elif role == "assistant" and prompts and message.get("content") and not message.get("tool_calls"):
            prompts[-1]["answered"] = "1"
    return prompts


def collect_session_plan(project: Path, key: str) -> list[dict[str, Any]]:
    """Read the board only when the chat actually touched a task there."""
    from navin.board import session_focus
    from navin.board.store import ProjectBoardStore

    ids = session_focus.touched(key)
    if not ids:
        return []
    by_id = {task["id"]: task for task in ProjectBoardStore(project).read_tasks()}
    return [by_id[task_id] for task_id in ids if task_id in by_id]


class SessionOverviewScreen(ModalScreen[None]):
    """Paged, low-cost view: history or plan, with an actionable errors tab."""

    DEFAULT_CSS = """
    SessionOverviewScreen { align: center middle; background: $background; }
    SessionOverviewScreen > Vertical {
        width: 90%; height: 88%; border: solid $primary;
        background: $surface; padding: 1 2;
    }
    SessionOverviewScreen #overview-heading { height: 2; text-style: bold; }
    SessionOverviewScreen #overview-tabs { height: 3; }
    SessionOverviewScreen #overview-tabs Button { margin-right: 1; }
    SessionOverviewScreen #overview-content { height: 1fr; }
    SessionOverviewScreen #overview-footer { height: 3; }
    SessionOverviewScreen #overview-footer Button { margin-right: 1; }
    """
    BINDINGS = [Binding("escape", "close", "Fermer", priority=True),
                Binding("left", "previous", "Précédent"), Binding("right", "next", "Suivant")]

    def __init__(self, mode: str, *, prompts: list[dict[str, str]] | None = None,
                 tasks: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.prompts = prompts or []
        self.tasks = tasks or []
        self.tab = "all"
        self.page = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="overview-heading", markup=False)
            with Horizontal(id="overview-tabs"):
                yield Button("Toutes", id="overview-all")
                yield Button("En cours", id="overview-open")
                yield Button("Échecs / à relancer", id="overview-errors")
            with VerticalScroll(id="overview-content"):
                yield Static(id="overview-text", markup=False)
            with Horizontal(id="overview-footer"):
                yield Button("← Précédent", id="overview-previous")
                yield Button("Suivant →", id="overview-next")
                yield Button("Fermer", id="overview-close")

    def on_mount(self) -> None:
        self.query_one("#overview-open", Button).display = self.mode == "plan"
        self.query_one("#overview-errors", Button).display = self.mode == "plan"
        self._paint()

    def _rows(self) -> list[dict[str, Any]]:
        if self.mode == "history":
            return list(reversed(self.prompts))
        if self.tab == "errors":
            return [task for task in self.tasks if task.get("status") in {"fix", "failed", "blocked", "cancelled"}]
        if self.tab == "open":
            return [task for task in self.tasks if task.get("status") != "done"]
        return self.tasks

    def _paint(self) -> None:
        rows = self._rows()
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, pages - 1)
        heading = "Historique" if self.mode == "history" else "Plan de cette conversation"
        self.query_one("#overview-heading", Static).update(
            f"{heading}  ·  {len(rows)} élément(s)  ·  page {self.page + 1}/{pages}")
        for name in ("all", "open", "errors"):
            self.query_one(f"#overview-{name}", Button).variant = "primary" if name == self.tab else "default"
        current = rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines: list[str] = []
        for index, row in enumerate(current, self.page * PAGE_SIZE + 1):
            if self.mode == "history":
                # This is a saved chat message, not a claim that its task succeeded.
                lines.extend([f"{index}. {row['date']}  ·  {'Réponse enregistrée' if row['answered'] else 'Sans réponse enregistrée'}",
                              row["text"], ""])
            else:
                state = str(row.get("status") or "planned")
                lines.extend([f"{index}. [{STATUS_FR.get(state, state)}] {row.get('title') or row['id']}",
                              str(row.get("description") or "").strip()])
                if state in {"fix", "failed", "blocked", "cancelled"}:
                    lines.append(f"À relancer : ouvrir la conversation et demander de reprendre {row['id']}.")
                lines.append("")
        if not lines:
            lines = ["Aucun message enregistré." if self.mode == "history" else
                     "Aucune tâche pour cette conversation." if self.tab == "all" else
                     "Aucune tâche dans cet onglet."]
        self.query_one("#overview-text", Static).update("\n".join(lines))
        self.query_one("#overview-previous", Button).disabled = self.page == 0
        self.query_one("#overview-next", Button).disabled = self.page + 1 >= pages

    def action_previous(self) -> None:
        if self.page:
            self.page -= 1
            self._paint()

    def action_next(self) -> None:
        if (self.page + 1) * PAGE_SIZE < len(self._rows()):
            self.page += 1
            self._paint()

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        button = str(event.button.id or "").removeprefix("overview-")
        if button in {"all", "open", "errors"}:
            self.tab, self.page = button, 0
            self._paint()
        elif button == "previous":
            self.action_previous()
        elif button == "next":
            self.action_next()
        elif button == "close":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)
