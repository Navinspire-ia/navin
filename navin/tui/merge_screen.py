# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Multitask merge outcome in the terminal: a note, and a choice when needed.

A Multitask subagent works in its own checkout. When it finishes, its changes
are merged into the user's tree automatically if that is safe; this module
says so in the chat. When they could not be merged (a conflict, or uncommitted
edits on the same files), it adds a note and opens a small modal with the same
three actions as the desktop card: Resolve (ask the agent to merge them),
Keep separate, Discard. ``/merge`` reopens the choice for the latest pending
one, ``/merge keep`` / ``/merge discard`` / ``/merge resolve`` answer directly.
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from navin.tui.textmarkup import escape

_ATTENTION = frozenset({"conflict", "blocked", "unknown"})
_LIST_LIMIT = 8


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def attention_text(event: Any) -> str:
    """One plain sentence: what could not be merged and why."""
    shown = list(event.conflicts or event.files)
    count = len(shown)
    if event.reason == "uncommitted":
        why = f"{_plural(count, 'file')} also changed in your working tree"
    elif event.reason == "conflict":
        why = f"{_plural(count, 'file')} conflict"
    elif event.reason == "in_progress":
        why = "another merge is in progress"
    else:
        why = "the merge could not be checked safely"
    return f"Changes from {event.label} could not be merged automatically: {why}."


def _file_lines(paths: list[str]) -> str:
    lines = [f"  {escape(path)}" for path in paths[:_LIST_LIMIT]]
    if len(paths) > _LIST_LIMIT:
        lines.append(f"  ... and {len(paths) - _LIST_LIMIT} more")
    return "\n".join(lines)


class MergeScreen(ModalScreen[str]):
    """Dismisses with ``resolve`` | ``keep`` | ``discard`` (escape = keep)."""

    DEFAULT_CSS = """
    MergeScreen { align: center middle; }
    MergeScreen > Vertical {
        width: 72;
        max-width: 96%;
        height: auto;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }
    MergeScreen .merge-hint { color: $text-muted; margin: 1 0 0 0; }
    MergeScreen Horizontal { height: auto; margin: 1 0 0 0; }
    MergeScreen Button { margin: 0 1 0 0; }
    """

    BINDINGS = [
        Binding("escape", "keep", "Keep separate"),
        Binding("r", "resolve", "Resolve", show=False),
        Binding("d", "discard", "Discard", show=False),
    ]

    def __init__(self, event: Any) -> None:
        super().__init__()
        self.event = event

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(escape(attention_text(self.event)), classes="picker-title", markup=True)
            files = list(self.event.conflicts or self.event.files)
            if files:
                yield Static(_file_lines(files), markup=True)
            yield Static(
                "Nothing in your files was changed. Resolve asks the agent to merge "
                "them for you; Keep separate leaves the work aside; Discard drops it.",
                classes="merge-hint",
            )
            with Horizontal():
                yield Button("Resolve", variant="primary", id="merge-resolve")
                yield Button("Keep separate  esc", id="merge-keep")
                yield Button("Discard", variant="error", id="merge-discard")

    def on_mount(self) -> None:
        self.query_one("#merge-resolve", Button).focus()

    @on(Button.Pressed, "#merge-resolve")
    def action_resolve(self) -> None:
        self.dismiss("resolve")

    @on(Button.Pressed, "#merge-keep")
    def action_keep(self) -> None:
        self.dismiss("keep")

    @on(Button.Pressed, "#merge-discard")
    def action_discard(self) -> None:
        self.dismiss("discard")


def _pending(app: Any) -> dict[str, Any]:
    pending = getattr(app, "_merge_pending", None)
    if pending is None:
        pending = {}
        app._merge_pending = pending
    return pending


async def apply_merge_choice(app: Any, event: Any, choice: str) -> None:
    """Send the user's choice to the runtime and say what happened."""
    from navin.tui.widgets import SystemNote

    _pending(app).pop(event.task_id, None)
    result = await app.runtime.merge_action(event.task_id, choice)
    if not result.get("ok"):
        await app.transcript.add(SystemNote("This change is no longer waiting.", "quiet"))
        return
    label = escape(event.label)
    if choice == "discard":
        await app.transcript.add(SystemNote(f"Discarded the changes from {label}.", "quiet"))
        return
    if choice == "keep":
        await app.transcript.add(SystemNote(f"Kept the changes from {label} separate.", "quiet"))
        return
    prompt = str(result.get("resolve_prompt") or event.resolve_prompt or "")
    if prompt:
        await app._send_prompt(
            prompt, prompt, followup=bool(app.runtime.turn_active), restore_input=False,
        )


async def open_merge_choice(app: Any, event: Any) -> None:
    async def _answer(choice: str | None) -> None:
        if event.task_id not in _pending(app):
            return  # already answered (command, or another window)
        await apply_merge_choice(app, event, choice or "keep")

    await app.push_screen(MergeScreen(event), _answer)


async def handle_merge_event(app: Any, event: Any) -> bool:
    """Note (and modal, when a decision is needed) for a merge event."""
    from navin.tui.runtime import UiWorktreeMerge
    from navin.tui.widgets import SystemNote

    if not isinstance(event, UiWorktreeMerge):
        return False
    if event.status == "merged":
        count = len(event.files)
        await app.transcript.add(SystemNote(
            f"Merged the changes from {escape(event.label)} into your working tree: "
            f"{_plural(count, 'file')}.\n{_file_lines(list(event.files))}",
            "success",
        ))
        return True
    if event.status in _ATTENTION:
        _pending(app)[event.task_id] = event
        await app.transcript.add(SystemNote(
            f"{escape(attention_text(event))}\n{_file_lines(list(event.conflicts or event.files))}\n"
            "[dim]/merge to choose: resolve, keep or discard[/dim]",
            "warning",
        ))
        await open_merge_choice(app, event)
        return True
    if event.status == "resolving" and (event.files or event.conflicts):
        # Handed to the agent on its own: say it, ask nothing.
        _pending(app).pop(event.task_id, None)
        shown = list(event.conflicts or event.files)
        await app.transcript.add(SystemNote(
            f"The changes from {escape(event.label)} also touch files you changed: "
            f"the agent is merging them now, keeping your edits.\n{_file_lines(shown)}",
            "quiet",
        ))
        return True
    if event.status in {"kept", "discarded", "resolving"}:
        _pending(app).pop(event.task_id, None)
        return True
    return False


async def run_merge_command(app: Any, argument: str) -> None:
    """``/merge [resolve|keep|discard]`` on the latest pending change."""
    from navin.tui.widgets import SystemNote

    pending = _pending(app)
    if not pending:
        await app.transcript.add(SystemNote("No changes are waiting to be merged.", "quiet"))
        return
    event = list(pending.values())[-1]
    choice = argument.strip().lower()
    if choice in {"resolve", "keep", "discard"}:
        await apply_merge_choice(app, event, choice)
        return
    await open_merge_choice(app, event)
