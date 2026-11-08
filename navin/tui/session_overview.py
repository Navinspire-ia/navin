# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Fast, session-scoped terminal views for saved prompts and board work."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

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
    "fix": "À corriger", "failed": "Échec", "in_progress": "En cours", "review": "À valider",
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


CURRENT_PLAN_LABEL = "Plan en cours"


def collect_session_plans(project: Path, key: str) -> dict[str, Any]:
    """This chat's plans, kept apart: the current one, then the earlier ones, newest first.

    Each task carries its plan's name under ``_plan``. ``hidden`` tells whether
    the user hid the current plan from the chat; ``has_current`` whether there
    is a current plan at all. The board is read only when the chat has a plan.
    """
    from navin.board import session_focus
    from navin.board.store import ProjectBoardStore

    current_ids = session_focus.touched(key)
    earlier = session_focus.earlier_plans(key)
    if not current_ids and not earlier:
        return {"tasks": [], "hidden": False, "has_current": False}
    by_id = {task["id"]: task for task in ProjectBoardStore(project).read_tasks()}
    tasks = [{**by_id[tid], "_plan": CURRENT_PLAN_LABEL} for tid in current_ids if tid in by_id]
    has_current = bool(tasks)
    seen = {task["id"] for task in tasks}
    for number, ids in enumerate(earlier, 1):
        for tid in ids:
            if tid in by_id and tid not in seen:
                seen.add(tid)
                tasks.append({**by_id[tid], "_plan": f"Plan précédent {number}"})
    return {
        "tasks": tasks,
        "hidden": has_current and session_focus.plan_hidden(key),
        "has_current": has_current,
    }


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
                 tasks: list[dict[str, Any]] | None = None, hidden: bool | None = None,
                 on_visibility: Callable[[bool], Awaitable[bool]] | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.prompts = prompts or []
        self.tasks = tasks or []
        # The current plan's visibility in the chat, and how to change it;
        # None when there is no current plan.
        self.hidden = hidden
        self.on_visibility = on_visibility
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
                yield Button("Masquer du chat", id="overview-visibility")
                yield Button("Fermer", id="overview-close")

    def on_mount(self) -> None:
        self.query_one("#overview-open", Button).display = self.mode == "plan"
        self.query_one("#overview-errors", Button).display = self.mode == "plan"
        self.query_one("#overview-visibility", Button).display = (
            self.mode == "plan" and self.hidden is not None and self.on_visibility is not None
        )
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
        heading = "Historique" if self.mode == "history" else "Plans de cette conversation"
        self.query_one("#overview-heading", Static).update(
            f"{heading}  ·  {len(rows)} élément(s)  ·  page {self.page + 1}/{pages}")
        for name in ("all", "open", "errors"):
            self.query_one(f"#overview-{name}", Button).variant = "primary" if name == self.tab else "default"
        self.query_one("#overview-visibility", Button).label = (
            "Afficher dans le chat" if self.hidden else "Masquer du chat"
        )
        current = rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines: list[str] = []
        group = None
        for index, row in enumerate(current, self.page * PAGE_SIZE + 1):
            if self.mode == "history":
                # This is a saved chat message, not a claim that its task succeeded.
                lines.extend([f"{index}. {row['date']}  ·  {'Réponse enregistrée' if row['answered'] else 'Sans réponse enregistrée'}",
                              row["text"], ""])
            else:
                # Plans stay apart: each one opens with its own name.
                if row.get("_plan") and row["_plan"] != group:
                    group = row["_plan"]
                    hidden = self.hidden and group == CURRENT_PLAN_LABEL
                    lines.extend([f"{group}{' (masqué dans le chat)' if hidden else ''}", ""])
                state = str(row.get("status") or "planned")
                lines.extend([f"{index}. [{STATUS_FR.get(state, state)}] {row.get('title') or row['id']}",
                              str(row.get("description") or "").strip()])
                # Every detail of the step, like the plan's own window.
                acceptance = str(row.get("acceptance") or "").strip()
                if acceptance:
                    lines.append(f"Critères : {acceptance}")
                validation = str(row.get("validation") or "").strip()
                if validation and validation != "none":
                    lines.append(f"Validation : {validation}")
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
    async def _button(self, event: Button.Pressed) -> None:
        button = str(event.button.id or "").removeprefix("overview-")
        if button in {"all", "open", "errors"}:
            self.tab, self.page = button, 0
            self._paint()
        elif button == "previous":
            self.action_previous()
        elif button == "next":
            self.action_next()
        elif button == "visibility":
            await self._toggle_visibility()
        elif button == "close":
            self.action_close()

    async def _toggle_visibility(self) -> None:
        """Hide or show the current plan in the chat; its tasks and the run go on."""
        if self.on_visibility is None or self.hidden is None:
            return
        wanted = not self.hidden
        if await self.on_visibility(wanted):
            self.hidden = wanted
        self._paint()

    def action_close(self) -> None:
        self.dismiss(None)
