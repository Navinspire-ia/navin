# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The plan of this chat under the CLI transcript: its steps, the current one, progress.

The same narrow plan as the desktop thread panel (``navin.board.plan.session_plan``):
only the board tasks this conversation touched, in the order it touched them. A
plan longer than ``PlanPanel.INLINE_STEPS`` steps stays on one line and opens in
its full-detail window (``navin.tui.plan_detail``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Static

from navin.board.plan import REVIEW_STATUSES


def read_plan_with_board(
    project: Path, session_key: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """This session's plan with the board tasks and the mission it was read from.

    One read of the board file, shared by the panel and its detail window;
    nothing is written. The plan is None when the session has not touched the
    board: no panel, not an empty one. ``hidden`` is set when the user hid it
    (``/plan hide``, or Hide in the desktop): the panel then stays out of the
    way. Raises the board's own errors (unreadable or invalid files).
    """
    from navin.board import session_focus
    from navin.board.ledger import MissionLedgerStore
    from navin.board.plan import session_plan
    from navin.board.store import ProjectBoardStore

    focus = session_focus.touched(session_key)
    if not focus:
        return None, [], None
    ledger = MissionLedgerStore(project)
    mission = ledger.load() if ledger.exists() else None
    tasks = ProjectBoardStore(project).read_tasks()
    plan = session_plan(tasks, focus, mission=mission)
    if plan is not None:
        plan["hidden"] = session_focus.plan_hidden(session_key)
    return plan, tasks, mission


def load_cli_plan(project: Path, session_key: str) -> dict[str, Any] | None:
    """This session's plan, read from the project board (``read_plan_with_board``)."""
    return read_plan_with_board(project, session_key)[0]


def plan_palette(dark: bool) -> dict[str, str]:
    """The plan's inks, readable on the dark and on the light terminal themes."""
    if dark:
        return {"blue": "#5EA8FF", "green": "#8FBC8F", "coral": "#E39B91", "muted": "#8A8A8A", "text": "#E6E6E6"}
    return {"blue": "#3D82FF", "green": "#2D6A4F", "coral": "#914747", "muted": "#626262", "text": "#20242A"}


def current_step(plan: dict[str, Any] | None) -> tuple[int, dict[str, Any]] | None:
    """The step being worked on, or the next one that can start; None otherwise.

    A step handed over for review, a blocked one, a finished or a cancelled
    one is not "en cours": naming it kept a step finished at dawn on the
    working line all day while the run went on with the next ones.
    """
    if not plan or plan.get("complete"):
        return None
    items = plan.get("items") or []
    index = next((i for i, item in enumerate(items) if item["id"] == plan.get("current_id")), None)
    if index is None:
        return None
    item = items[index]
    if item.get("status") == "in_progress":
        return index, item
    if (item.get("done") or item.get("cancelled") or item.get("blocked")
            or item.get("status") in REVIEW_STATUSES):
        return None
    return index, item


def plan_warning(plan: dict[str, Any]) -> str:
    """Why the plan is held, when it is: paused, looping or under revision."""
    if plan.get("pause_reason"):
        return f"En pause : {plan['pause_reason']}"
    if plan.get("loop_detected"):
        return "Boucle détectée, révision du plan"
    return "Révision du plan" if plan.get("replan_needed") else ""


# The normal course of a step: the panel names only the other states.
PLAIN_STATES = frozenset({"Terminée", "En cours", "À suivre", "Planifiée", "En attente"})


def step_look(item: dict[str, Any], current: bool) -> tuple[str, str, str]:
    """Glyph, ink and French state of one step, the same in the panel and in its window.

    A step someone is on reads "En cours" even while a dependency is open,
    like the plan's own counts; the current step that has not started yet
    reads "À suivre".
    """
    status = str(item.get("status") or "")
    if item.get("done"):
        return "✓", "green", "Terminée"
    if item.get("cancelled"):
        return "-", "muted", "Annulée"
    if status in REVIEW_STATUSES:
        # Handed over: the run is past it, the board waits for a check.
        return "◐", "blue", "En audit" if status == "audit" else "À valider"
    if status == "in_progress":
        return "◉", "blue", "En cours"
    if item.get("blocked"):
        return "!", "coral", "Bloquée"
    if status == "fix":
        return "!", "coral", "À corriger"
    if current:
        return "◉", "blue", "À suivre"
    return "○", "muted", "Planifiée" if status == "planned" else "En attente"


class PlanPanel(Static):
    """``▸ Plan  1/3 terminées`` and, unfolded, one row per step, the current one marked.

    A click unfolds a plan of up to ``INLINE_STEPS`` steps under its first
    line; a longer one stays on that line and the click asks for its
    full-detail window instead (``DetailRequested``). A finished plan between
    turns shows one line on its own; the reader's choice wins until the plan
    changes. A plan the user hid is not shown at all, nor named on the working
    line. The ``✕ masquer`` of the first line hides it from the chat (its
    tasks go on).
    """

    class Dismissed(Message):
        """The reader clicked the panel's ``✕ masquer``."""

    class DetailRequested(Message):
        """The reader clicked a plan too long to unfold under the chat."""

    DEFAULT_CSS = """
    PlanPanel {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        background: $background;
        display: none;
    }
    """

    # Past this many steps a click opens the detail window, not a long list.
    INLINE_STEPS = 5
    DISMISS_LABEL = "  ✕ masquer"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, markup=False, **kwargs)
        self.plan: dict[str, Any] | None = None
        self.error = ""
        self.turn_active = False
        self._user_folded: bool | None = None

    def apply(self, plan: dict[str, Any] | None, *, turn_active: bool, error: str = "") -> None:
        def shape(value: dict[str, Any] | None) -> list[tuple[str, str]]:
            return [(item["id"], item["title"]) for item in (value or {}).get("items") or []]

        if shape(plan) != shape(self.plan):
            self._user_folded = None
        self.plan, self.error, self.turn_active = plan, error, turn_active
        self.repaint()

    @property
    def folded(self) -> bool:
        if self.opens_detail():
            return True
        if self._user_folded is not None:
            return self._user_folded
        return bool(self.plan)

    def opens_detail(self) -> bool:
        """Too many steps to unfold under the chat: a click opens the detail window."""
        return len((self.plan or {}).get("items") or []) > self.INLINE_STEPS

    def on_click(self, event: events.Click) -> None:
        event.stop()
        if self.plan is None:
            return
        if (event.style.meta or {}).get("plan_dismiss"):
            self.post_message(self.Dismissed())
            return
        if self.opens_detail():
            self.post_message(self.DetailRequested())
            return
        self._user_folded = not self.folded
        self.repaint()

    def _current(self) -> tuple[int, dict[str, Any]] | None:
        """The plan's current step (``current_step``); none for a plan hidden from the chat."""
        if self.plan and self.plan.get("hidden"):
            return None
        return current_step(self.plan)

    def step_label(self) -> str:
        """``Étape 22/40: Add the tests`` for the working line; empty without a current step.

        Numbered by progress, like the panel's "21/40 terminées": the steps are
        listed in the order the chat touched them, so a step taken up again
        after a stop can sit first in that list. Numbered by its place, the
        line read "Étape 1/40" with 21 steps done and looked like the plan
        had started over. The steps handed over for review are behind the run
        too: 24 of them still read "Étape 1/40".
        """
        current = self._current()
        if current is None:
            return ""
        plan = self.plan or {}
        total = int(plan.get("total_count") or len(plan.get("items") or []))
        behind = int(plan.get("done_count") or 0) + int(plan.get("review_count") or 0)
        return f"Étape {min(behind + 1, total)}/{total}: {current[1]['title']}"

    def repaint(self) -> None:
        hidden = bool(self.plan and self.plan.get("hidden"))
        if (self.plan is None or hidden) and not self.error:
            if self.display:
                self.display = False
                self.update("")
            return
        self.display = True
        self.update(self._render_plan())

    def _palette(self) -> dict[str, str]:
        return plan_palette(self.app.current_theme.dark)

    def _render_plan(self) -> Text:
        ink = self._palette()
        out = Text(no_wrap=True, overflow="ellipsis")
        plan = self.plan
        if plan is None:
            out.append("! ", style=ink["coral"])
            out.append(f"Plan indisponible : {self.error}", style=ink["muted"])
            return out
        items = plan.get("items") or []
        done, total = int(plan.get("done_count") or 0), int(plan.get("total_count") or len(items))
        folded = self.folded
        current = self._current()
        out.append("▸ " if folded else "▾ ", style=ink["muted"])
        # Before the current step, so a long title cannot cut it off.
        dismiss = Style(color=ink["muted"], meta={"plan_dismiss": True})
        if plan.get("complete"):
            out.append("✓ Plan terminé", style=f"bold {ink['green']}")
            out.append(f"  {done}/{total} étapes", style=ink["muted"])
            out.append(self.DISMISS_LABEL, style=dismiss)
        else:
            out.append("Plan", style=f"bold {ink['blue']}")
            out.append(f"  {done}/{total} terminées", style=ink["muted"])
            # What holds the run, then what it handed over for a check:
            # without them a run that ends its steps in review reads "0/40"
            # all day.
            blocked = int(plan.get("blocked_count") or 0)
            if blocked:
                out.append(f" · {blocked} bloquée{'s' if blocked > 1 else ''}", style=ink["coral"])
            review = int(plan.get("review_count") or 0)
            if review:
                out.append(f" · {review} à valider", style=ink["blue"])
            out.append(self.DISMISS_LABEL, style=dismiss)
            if folded and current is not None:
                out.append(" · en cours : ", style=ink["muted"])
                out.append(current[1]["title"], style=ink["text"])
        goal = " ".join(str(plan.get("goal") or "").split())
        if goal and not folded:
            out.append(f" · {goal}", style=ink["muted"])
        warning = plan_warning(plan)
        if warning:
            out.append("\n  ! ", style=ink["coral"])
            out.append(warning, style=ink["coral"])
        if folded:
            return out
        # Unfolded only up to INLINE_STEPS steps: every one has its row.
        current_id = current[1]["id"] if current else None
        for item in items:
            is_current = item["id"] == current_id
            glyph, tone, state = step_look(item, is_current)
            finished = item.get("done") or item.get("cancelled")
            out.append("\n")
            out.append(f"  {glyph} ", style=ink[tone])
            out.append(
                str(item.get("title") or item["id"]),
                style=ink["muted"] if finished else f"bold {ink['text']}" if is_current else ink["text"],
            )
            if state not in PLAIN_STATES:
                out.append(f"  {state.lower()}", style=ink["coral"] if tone == "coral" else ink["muted"])
        return out
