# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The plan of this chat under the CLI transcript: its steps, the current one, progress.

The same narrow plan as the desktop thread panel (``navin.board.plan.session_plan``):
only the board tasks this conversation touched, in the order it touched them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events
from textual.widgets import Static


def load_cli_plan(project: Path, session_key: str) -> dict[str, Any] | None:
    """This session's plan, read from the project board. Nothing is written.

    None when the session has not touched the board: no panel, not an empty one.
    Raises the board's own errors (unreadable or invalid files).
    """
    from navin.board import session_focus
    from navin.board.ledger import MissionLedgerStore
    from navin.board.plan import session_plan
    from navin.board.store import ProjectBoardStore

    focus = session_focus.touched(session_key)
    if not focus:
        return None
    ledger = MissionLedgerStore(project)
    mission = ledger.load() if ledger.exists() else None
    return session_plan(ProjectBoardStore(project).read_tasks(), focus, mission=mission)


class PlanPanel(Static):
    """``▾ Plan  1/3 done`` then one row per step, the current one marked.

    Click folds it to its first line. A finished plan between turns shows one
    line on its own; the reader's choice wins until the plan changes.
    """

    DEFAULT_CSS = """
    PlanPanel {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        background: $background;
        display: none;
    }
    """

    MAX_ROWS = 7

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
        if self._user_folded is not None:
            return self._user_folded
        return bool(self.plan)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        if self.plan is None:
            return
        self._user_folded = not self.folded
        self.repaint()

    def _current(self) -> tuple[int, dict[str, Any]] | None:
        plan = self.plan
        if not plan or plan.get("complete"):
            return None
        items = plan.get("items") or []
        index = next((i for i, item in enumerate(items) if item["id"] == plan.get("current_id")), None)
        if index is None or items[index].get("done") or items[index].get("cancelled"):
            return None
        return index, items[index]

    def step_label(self) -> str:
        """``Étape 22/40: Add the tests`` for the working line; empty without a current step.

        Numbered by progress, like the panel's "21/40 terminées": the steps are
        listed in the order the chat touched them, so a step taken up again
        after a stop can sit first in that list. Numbered by its place, the
        line read "Étape 1/40" with 21 steps done and looked like the plan
        had started over.
        """
        current = self._current()
        if current is None:
            return ""
        plan = self.plan or {}
        total = int(plan.get("total_count") or len(plan.get("items") or []))
        done = int(plan.get("done_count") or 0)
        return f"Étape {min(done + 1, total)}/{total}: {current[1]['title']}"

    def repaint(self) -> None:
        if self.plan is None and not self.error:
            if self.display:
                self.display = False
                self.update("")
            return
        self.display = True
        self.update(self._render_plan())

    def _palette(self) -> dict[str, str]:
        if self.app.current_theme.dark:
            return {"blue": "#5EA8FF", "green": "#8FBC8F", "coral": "#E39B91", "muted": "#8A8A8A", "text": "#E6E6E6"}
        return {"blue": "#3D82FF", "green": "#2D6A4F", "coral": "#914747", "muted": "#626262", "text": "#20242A"}

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
        out.append("▸ " if folded else "▾ ", style=ink["muted"])
        if plan.get("complete"):
            out.append("✓ Plan terminé", style=f"bold {ink['green']}")
            out.append(f"  {done}/{total} étapes", style=ink["muted"])
        else:
            out.append("Plan", style=f"bold {ink['blue']}")
            out.append(f"  {done}/{total} terminées", style=ink["muted"])
            current = self._current()
            if folded and current is not None:
                out.append(" · en cours : ", style=ink["muted"])
                out.append(current[1]["title"], style=ink["text"])
        goal = " ".join(str(plan.get("goal") or "").split())
        if goal and not folded:
            out.append(f" · {goal}", style=ink["muted"])
        warning = (
            f"En pause : {plan['pause_reason']}" if plan.get("pause_reason")
            else "Boucle détectée, révision du plan" if plan.get("loop_detected")
            else "Révision du plan" if plan.get("replan_needed") else ""
        )
        if warning:
            out.append("\n  ! ", style=ink["coral"])
            out.append(warning, style=ink["coral"])
        if folded:
            return out
        current_id = plan.get("current_id")
        for row in self._window(items, current_id):
            out.append("\n")
            if row[0] == "earlier":
                out.append(f"  ↑ {row[1]} étape(s) précédente(s)", style=ink["muted"])
                continue
            if row[0] == "later":
                out.append(f"  … {row[1]} autre(s) étape(s)", style=ink["muted"])
                continue
            item = row[1]
            title_style, suffix = ink["text"], ""
            if item.get("done"):
                glyph, mark = "✓", ink["green"]
                title_style = ink["muted"]
            elif item.get("cancelled"):
                glyph, mark, title_style, suffix = "-", ink["muted"], ink["muted"], "  annulée"
            elif item["id"] == current_id:
                glyph, mark = "◉", ink["blue"]
                title_style = f"bold {ink['text']}"
                if item.get("blocked"):
                    suffix = "  bloquée"
            elif item.get("blocked"):
                glyph, mark, suffix = "!", ink["coral"], "  bloquée"
            else:
                glyph, mark = "○", ink["muted"]
            out.append(f"  {glyph} ", style=mark)
            out.append(str(item.get("title") or item["id"]), style=title_style)
            if suffix:
                out.append(suffix, style=ink["coral"] if "bloquée" in suffix else ink["muted"])
        return out

    def _window(self, items: list[dict[str, Any]], current_id: Any) -> list[tuple[Any, ...]]:
        """Every step, or the ones around the current step when there are many."""
        if len(items) <= self.MAX_ROWS:
            return [("item", item) for item in items]
        index = next((i for i, item in enumerate(items) if item["id"] == current_id), 0)
        start = max(0, min(index - 2, len(items) - self.MAX_ROWS))
        end = start + self.MAX_ROWS
        rows: list[tuple[Any, ...]] = [("earlier", start)] if start else []
        rows += [("item", item) for item in items[start:end]]
        if end < len(items):
            rows.append(("later", len(items) - end))
        return rows
