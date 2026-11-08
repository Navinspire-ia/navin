# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The full-detail window of this chat's current plan in the CLI.

Opened by a click on a plan too long to unfold under the chat, and by the
session menu's "Plan en cours". Every step in plan order with its state, its
whole description, its criteria, its validation, its proof (or the reason it
is blocked) and the steps it still waits on. Filtered by state and paged, so a
plan of hundreds of steps still opens at once.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from navin.board.plan import REVIEW_STATUSES
from navin.tui.plan_panel import (
    current_step,
    plan_palette,
    plan_warning,
    read_plan_with_board,
    step_look,
)

PAGE_SIZE = 20
# The window's filters, in the order of the plan's own count line.
FILTERS = (("all", "Toutes"), ("todo", "À faire"), ("blocked", "Bloquées"), ("review", "À valider"))
_VALIDATION_FR = {"test": "tests", "lint": "lint", "verify": "vérification", "manual": "manuelle"}
_PRIORITY_FR = {"critical": "critique", "high": "haute", "medium": "moyenne", "low": "basse"}


def load_plan_detail(project: Path, session_key: str) -> dict[str, Any] | None:
    """This chat's current plan, each step with every detail the board keeps; None without one.

    One read of the board. A step gets its whole description (the panel's is
    cut), its proof, its last update and ``waiting_on``: the dependencies still
    open, judged over the whole board like the plan's own "blocked". The
    mission's constraints come along when the mission is this chat's.
    """
    from navin.board.plan import blocking_dependencies

    plan, tasks, mission = read_plan_with_board(project, session_key)
    if plan is None:
        return None
    by_id = {task["id"]: task for task in tasks}
    blocking = blocking_dependencies(tasks)
    for item in plan["items"]:
        task = by_id.get(item["id"]) or {}
        item["description"] = str(task.get("description") or "").strip()
        item["evidence"] = str(task.get("evidence") or "").strip()
        item["updated_at"] = str(task.get("updated_at") or "")
        item["waiting_on"] = [
            f"{dep} {by_id[dep]['title']}" if dep in by_id else dep
            for dep in blocking.get(item["id"], [])
        ]
    if plan.get("goal") and isinstance(mission, dict):
        plan["constraints"] = [str(rule).strip() for rule in mission.get("constraints") or [] if str(rule).strip()]
    return plan


def step_state(item: dict[str, Any]) -> str:
    """The filter a step falls in: done, cancelled, review, blocked or todo.

    The rule of the plan's own counts: a step handed over is "review", never
    "blocked"; a step someone is on is "todo" even while it waits on a
    dependency.
    """
    status = str(item.get("status") or "")
    if item.get("done"):
        return "done"
    if item.get("cancelled"):
        return "cancelled"
    if status in REVIEW_STATUSES:
        return "review"
    if item.get("blocked") and status != "in_progress":
        return "blocked"
    return "todo"


def _when(stamp: str) -> str:
    """``08/11 à 02:50`` in the reader's time zone; empty for a missing or odd stamp."""
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return moment.astimezone().strftime("%d/%m à %H:%M")


class PlanDetailScreen(ModalScreen[None]):
    """Every step of this chat's current plan with all its detail, filtered by state and paged."""

    DEFAULT_CSS = """
    PlanDetailScreen { align: center middle; background: $background 70%; }
    PlanDetailScreen > Vertical {
        width: 90%; max-width: 150; height: 90%;
        border: round $primary; background: $surface; padding: 1 2;
    }
    PlanDetailScreen #plan-detail-heading { height: auto; }
    PlanDetailScreen #plan-detail-filters { height: 3; margin-top: 1; }
    PlanDetailScreen #plan-detail-filters Button { min-width: 0; margin-right: 1; }
    PlanDetailScreen #plan-detail-steps { height: 1fr; margin-top: 1; }
    PlanDetailScreen .plan-detail-mission { height: auto; padding-bottom: 1; }
    PlanDetailScreen .plan-step-title { height: auto; }
    PlanDetailScreen .plan-step-detail { height: auto; padding: 0 0 1 4; }
    PlanDetailScreen #plan-detail-footer { height: 3; }
    PlanDetailScreen #plan-detail-footer Button { margin-right: 1; }
    PlanDetailScreen #plan-detail-page { width: 1fr; height: 3; content-align: right middle; color: $text-muted; }
    """
    # Priority: the step list keeps the focus for up and down, and must not
    # take left and right away from the pages.
    BINDINGS = [
        Binding("escape", "close", "Fermer", priority=True),
        Binding("left", "previous", "Précédent", priority=True),
        Binding("right", "next", "Suivant", priority=True),
    ]

    def __init__(self, plan: dict[str, Any], *,
                 on_visibility: Callable[[bool], Awaitable[bool]] | None = None) -> None:
        super().__init__()
        self.plan = plan
        # Whether the chat shows the plan, and how to change it; the window
        # reads a hidden plan as well.
        self.hidden = bool(plan.get("hidden"))
        self.on_visibility = on_visibility
        self.filter = "all"
        self.page = 0
        current = current_step(plan)
        self._current_id = current[1]["id"] if current else None
        # Plan order under every filter: a step keeps its number.
        self._steps = list(enumerate(plan.get("items") or [], 1))

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="plan-detail-heading", markup=False)
            with Horizontal(id="plan-detail-filters"):
                for name, label in FILTERS:
                    yield Button(label, id=f"plan-detail-{name}")
            yield VerticalScroll(id="plan-detail-steps")
            with Horizontal(id="plan-detail-footer"):
                yield Button("← Précédent", id="plan-detail-previous")
                yield Button("Suivant →", id="plan-detail-next")
                yield Button("Masquer du chat", id="plan-detail-visibility")
                yield Button("Fermer", id="plan-detail-close")
                yield Static(id="plan-detail-page", markup=False)

    async def on_mount(self) -> None:
        self.query_one("#plan-detail-visibility", Button).display = self.on_visibility is not None
        await self._paint()
        self.query_one("#plan-detail-steps", VerticalScroll).focus()

    def _rows(self, name: str | None = None) -> list[tuple[int, dict[str, Any]]]:
        wanted = name or self.filter
        if wanted == "all":
            return self._steps
        return [(number, item) for number, item in self._steps if step_state(item) == wanted]

    def _paint_heading(self) -> None:
        """Heading, filter counts and the visibility button; the step list stays."""
        ink = plan_palette(self.app.current_theme.dark)
        self.query_one("#plan-detail-heading", Static).update(self._heading(ink))
        for name, label in FILTERS:
            button = self.query_one(f"#plan-detail-{name}", Button)
            button.label = f"{label} ({len(self._rows(name))})"
            button.variant = "primary" if name == self.filter else "default"
        self.query_one("#plan-detail-visibility", Button).label = (
            "Afficher dans le chat" if self.hidden else "Masquer du chat"
        )

    async def _paint(self) -> None:
        ink = plan_palette(self.app.current_theme.dark)
        rows = self._rows()
        pages = max(1, -(-len(rows) // PAGE_SIZE))
        self.page = min(self.page, pages - 1)
        self._paint_heading()
        widgets: list[Static] = []
        mission = self._mission(ink) if self.page == 0 and self.filter == "all" else None
        if mission is not None:
            widgets.append(Static(mission, classes="plan-detail-mission", markup=False))
        for number, item in rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]:
            widgets.append(Static(self._title(number, item, ink), classes="plan-step-title", markup=False))
            widgets.append(Static(self._detail(item, ink), classes="plan-step-detail", markup=False))
        if not rows:
            widgets.append(Static("Aucune étape dans cet onglet.", markup=False))
        steps = self.query_one("#plan-detail-steps", VerticalScroll)
        await steps.remove_children()
        await steps.mount_all(widgets)
        steps.scroll_home(animate=False)
        several = pages > 1
        previous = self.query_one("#plan-detail-previous", Button)
        following = self.query_one("#plan-detail-next", Button)
        previous.display = following.display = several
        previous.disabled = self.page == 0
        following.disabled = self.page + 1 >= pages
        self.query_one("#plan-detail-page", Static).update(f"page {self.page + 1}/{pages}" if several else "")

    def _heading(self, ink: dict[str, str]) -> Text:
        plan = self.plan
        out = Text()
        out.append("Plan en cours", style=f"bold {ink['text']}")
        if self.hidden:
            out.append("  ·  masqué dans le chat", style=ink["muted"])
        done = int(plan.get("done_count") or 0)
        total = int(plan.get("total_count") or len(self._steps))
        out.append("\n")
        if plan.get("complete"):
            out.append(f"✓ Plan terminé  {done}/{total} étapes", style=f"bold {ink['green']}")
        else:
            out.append(f"{done}/{total} terminées", style=f"bold {ink['text']}")
            blocked = int(plan.get("blocked_count") or 0)
            if blocked:
                out.append(f" · {blocked} bloquée{'s' if blocked > 1 else ''}", style=ink["coral"])
            review = int(plan.get("review_count") or 0)
            if review:
                out.append(f" · {review} à valider", style=ink["blue"])
            current = current_step(plan)
            if current is not None:
                out.append("  ·  en cours : ", style=ink["muted"])
                out.append(str(current[1].get("title") or current[1]["id"]), style=ink["text"])
        goal = " ".join(str(plan.get("goal") or "").split())
        if goal:
            out.append("\nObjectif : ", style=f"bold {ink['muted']}")
            out.append(goal, style=ink["text"])
        warning = plan_warning(plan)
        if warning:
            out.append(f"\n! {warning}", style=ink["coral"])
        return out

    def _mission(self, ink: dict[str, str]) -> Text | None:
        """The mission's criteria and constraints, when the mission is this chat's."""
        out = Text()
        for label, values in (("Critères de la mission", self.plan.get("acceptance_criteria") or []),
                              ("Contraintes", self.plan.get("constraints") or [])):
            lines = [" ".join(str(value).split()) for value in values if str(value).strip()]
            if not lines:
                continue
            if out.plain:
                out.append("\n")
            out.append(label, style=f"bold {ink['text']}")
            for line in lines:
                out.append(f"\n  • {line}", style=ink["text"])
        return out if out.plain else None

    def _title(self, number: int, item: dict[str, Any], ink: dict[str, str]) -> Text:
        glyph, tone, state = step_look(item, item["id"] == self._current_id)
        finished = item.get("done") or item.get("cancelled")
        out = Text()
        out.append(f"{glyph} ", style=ink[tone])
        out.append(f"{number}. ", style=ink["muted"])
        out.append(str(item.get("title") or item["id"]), style=ink["muted"] if finished else f"bold {ink['text']}")
        out.append(f"  {state}", style=ink[tone])
        return out

    def _detail(self, item: dict[str, Any], ink: dict[str, str]) -> Text:
        out = Text()
        meta = [str(item["id"])]
        priority = _PRIORITY_FR.get(str(item.get("priority") or ""))
        if priority:
            meta.append(f"priorité {priority}")
        when = _when(str(item.get("updated_at") or ""))
        if when:
            meta.append(f"mis à jour le {when}")
        retries = int(item.get("retry_count") or 0)
        if retries:
            meta.append(f"{retries} reprise{'s' if retries > 1 else ''}")
        out.append(" · ".join(meta), style=ink["muted"])

        def field(label: str, value: str) -> None:
            out.append(f"\n{label} : ", style=f"bold {ink['muted']}")
            out.append(value, style=ink["text"])

        description = str(item.get("description") or "").strip()
        if description:
            out.append(f"\n{description}", style=ink["text"])
        acceptance = str(item.get("acceptance") or "").strip()
        if acceptance:
            field("Critères", acceptance)
        validation = str(item.get("validation") or "").strip()
        if validation and validation != "none":
            field("Validation", _VALIDATION_FR.get(validation, validation))
        evidence = str(item.get("evidence") or "").strip()
        if evidence:
            # A parked step keeps the reason it was parked in its evidence.
            field("Motif du blocage" if item.get("status") == "blocked" else "Preuve", evidence)
        waiting = [str(dep) for dep in item.get("waiting_on") or []]
        if waiting:
            field("En attente de", ", ".join(waiting))
        return out

    @on(Button.Pressed)
    async def _button(self, event: Button.Pressed) -> None:
        button = str(event.button.id or "").removeprefix("plan-detail-")
        if button in {name for name, _label in FILTERS}:
            self.filter, self.page = button, 0
            await self._paint()
        elif button == "previous":
            await self.action_previous()
        elif button == "next":
            await self.action_next()
        elif button == "visibility":
            await self._toggle_visibility()
        elif button == "close":
            self.action_close()

    async def action_previous(self) -> None:
        if self.page:
            self.page -= 1
            await self._paint()

    async def action_next(self) -> None:
        if (self.page + 1) * PAGE_SIZE < len(self._rows()):
            self.page += 1
            await self._paint()

    async def _toggle_visibility(self) -> None:
        """Hide or show the plan in the chat; its tasks and the run go on."""
        if self.on_visibility is None:
            return
        wanted = not self.hidden
        if await self.on_visibility(wanted):
            self.hidden = wanted
        self._paint_heading()

    def action_close(self) -> None:
        self.dismiss(None)
