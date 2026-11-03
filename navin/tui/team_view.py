# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collaboration Mode in the terminal: ``/team``, the footer, teammate replies.

The team settings are the desktop's (config key ``collab``, saved through
``navin.webui.collab_api.update_collab``) and the chat's team is the session
metadata the desktop's Team picker writes, so the CLI and the desktop always
show the same switch and the same team.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from navin.tui.runtime import TEAM_GLYPHS, UiTeamMessage, team_face, team_job
from navin.tui.settled import SettledMarkdown
from navin.tui.textmarkup import escape
from navin.tui.virtual import BlockRecord

TEAM_USAGE = "/team on | /team off | /team <team> | /team none"

# Compact journal tags, by message kind.
_TAGS = {"assign": "task", "user": "interrupts", "email": "email sent"}
_BODY_MAX = 900
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _hex_color(identity: dict[str, Any]) -> str:
    color = str(identity.get("color") or "")
    return color if _HEX.fullmatch(color) else ""


def _glyph(identity: dict[str, Any]) -> str:
    """The teammate's face alone, in its color."""
    glyph = TEAM_GLYPHS.get(str(identity.get("avatar") or ""), "◉")
    color = _hex_color(identity)
    return f"[{color}]{glyph}[/]" if color else glyph


def is_team_reply(event: UiTeamMessage) -> bool:
    """A teammate's report to the lead, or its message to the user.

    Those read as the teammate's own reply; assignments and peer messages
    stay compact journal rows.
    """
    if event.kind == "result":
        return True
    return event.kind == "message" and str((event.target or {}).get("id") or "") == "user"


def reply_tag(event: UiTeamMessage) -> str:
    if event.kind == "result":
        return "report" if event.status == "ok" else "stopped"
    return "to you"


def team_journal_line(event: UiTeamMessage) -> str:
    """One compact row: who talks to whom, why, and what (Textual markup)."""
    head = team_face(event.sender, with_job=True)
    if event.target:
        head += f" [dim]→[/dim] {team_face(event.target)}"
    tag = _TAGS.get(event.kind) or (event.status or "")
    if tag:
        head += f"  [dim]{escape(tag)}[/dim]"
    body = event.content if len(event.content) <= _BODY_MAX else event.content[:_BODY_MAX] + "..."
    return f"{head}\n{escape(body)}"


class TeamReply(Vertical):
    """A teammate speaking in the chat: face, name and job, then its Markdown."""

    ALLOW_SELECT = True

    DEFAULT_CSS = """
    TeamReply {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border-left: tall $accent;
        background: $background;
    }
    TeamReply > .team-reply-head { height: auto; width: 1fr; }
    TeamReply > .team-reply-body { margin: 0; }
    TeamReply.-stopped > .team-reply-head { color: $warning; }
    """

    def __init__(self, sender: dict[str, str], content: str, *, tag: str, stopped: bool = False) -> None:
        super().__init__(classes="-stopped" if stopped else None)
        self.sender = dict(sender)
        self.markdown = content
        self.tag = tag
        self.stopped = stopped
        color = _hex_color(self.sender)
        if color:
            # The teammate's own color; without one the theme accent stays.
            self.styles.border_left = ("tall", Color.parse(color))

    @classmethod
    def from_event(cls, event: UiTeamMessage) -> TeamReply:
        return cls(
            event.sender, event.content, tag=reply_tag(event),
            stopped=event.kind == "result" and event.status != "ok",
        )

    @property
    def head_markup(self) -> str:
        return f"{team_face(self.sender, with_job=True)}  [dim]{escape(self.tag)}[/dim]"

    @property
    def speaker(self) -> str:
        """``Mark - Senior Developer``, for the exported transcript."""
        name = self.sender.get("name") or "?"
        job = team_job(self.sender)
        return f"{name} - {job}" if job else name

    def compose(self) -> ComposeResult:
        yield Static(self.head_markup, classes="team-reply-head", markup=True)
        yield SettledMarkdown(self.markdown, classes="team-reply-body")

    def copy_text(self) -> str:
        return self.markdown

    def snapshot_record(self) -> BlockRecord | None:
        return BlockRecord(
            type(self),
            {
                "sender": self.sender, "markdown": self.markdown, "tag": self.tag,
                "stopped": self.stopped, "speaker": self.speaker,
            },
            TeamReply._from_record,
            classes=set(self.classes),
            copy=lambda record: record.state["markdown"],
        )

    @staticmethod
    def _from_record(record: BlockRecord) -> TeamReply:
        state = record.state
        return TeamReply(state["sender"], state["markdown"], tag=state["tag"], stopped=state["stopped"])


# -- /team ------------------------------------------------------------------


def footer_label(state: dict[str, Any]) -> str:
    """``Team: Dev`` when Collaboration Mode works in this chat, else empty."""
    if not state.get("enabled") or not state.get("team_name"):
        return ""
    return f"Team: {state['team_name']}"


def find_team(state: dict[str, Any], wanted: str) -> dict[str, str] | None:
    """A team by id or name, case-insensitive."""
    key = wanted.strip().lower()
    for team in state.get("teams") or []:
        if key in {str(team.get("id") or "").lower(), str(team.get("name") or "").lower()}:
            return team
    return None


def team_ids(state: dict[str, Any]) -> str:
    return ", ".join(str(team.get("id")) for team in state.get("teams") or []) or "no team saved"


def status_markup(state: dict[str, Any]) -> str:
    """What ``/team`` prints: the switch, this chat's team, its teammates."""
    on = bool(state.get("enabled"))
    lines = [f"[b]Team mode:[/b] {'[$success]on[/]' if on else 'off'}"]
    name = str(state.get("team_name") or "")
    if not name:
        lines.append("[b]This chat:[/b] no team")
    else:
        default = " [dim](default team)[/dim]" if state.get("picked") is None else ""
        idle = "" if on else " [dim](works with it once Team mode is on)[/dim]"
        lines.append(f"[b]This chat:[/b] {escape(name)}{default}{idle}")
        for member in state.get("members") or []:
            job = team_job(member)
            label = f"{member.get('name') or '?'} - {job}" if job else str(member.get("name") or "?")
            lines.append(f"  {_glyph(member)} {escape(label)}")
    lines.append(f"[dim]Change it: {escape(TEAM_USAGE)} (teams: {escape(team_ids(state))})[/dim]")
    return "\n".join(lines)


def refresh_team_meta(app: Any, state: dict[str, Any] | None = None) -> None:
    """Put this chat's team on the footer (or hide it)."""
    from navin.tui.widgets import ComposerMeta

    label = footer_label(app.runtime.team_state() if state is None else state)
    try:
        meta = app.query_one("#composer-meta", ComposerMeta)
    except NoMatches:
        # Another screen is on top: the footer is painted again when it closes.
        return
    meta.set_team(label)


def _save_enabled(on: bool) -> None:
    """Same save as the desktop's Team switch, so both stay in sync."""
    from navin.webui.collab_api import update_collab

    update_collab({"enabled": on})


async def run_team_command(app: Any, argument: str) -> None:
    """``/team [on|off|<team>|none]``: show or change Collaboration Mode here."""
    runtime = app.runtime
    choice = argument.strip()
    lowered = choice.lower()
    state = await asyncio.to_thread(runtime.team_state)
    try:
        if lowered in {"on", "off"}:
            on = lowered == "on"
            await asyncio.to_thread(_save_enabled, on)
            if on and state.get("picked") == "off" and state.get("teams"):
                # Turned on in a chat set to no team: it gets the default team.
                default = find_team(state, str(state.get("default_team") or "")) or state["teams"][0]
                await asyncio.to_thread(runtime.set_session_team, str(default["id"]))
        elif lowered in {"none", "no", "nobody"}:
            await asyncio.to_thread(runtime.set_session_team, "off")
        elif lowered and lowered not in {"status", "help", "?"}:
            team = find_team(state, choice)
            if team is None:
                await app._note(
                    f"[$warning]No team {escape(choice)}.[/] Teams: {escape(team_ids(state))}. "
                    f"[dim]{escape(TEAM_USAGE)}[/dim]",
                    "warning",
                )
                return
            await asyncio.to_thread(runtime.set_session_team, str(team["id"]))
    except (ValueError, OSError, RuntimeError) as exc:
        # WebUISettingsError is a ValueError; RuntimeError: engine still starting.
        await app._note(f"[$error]Team not changed:[/] {escape(str(exc))}", "error")
        return
    state = await asyncio.to_thread(runtime.team_state)
    refresh_team_meta(app, state)
    await app._note(status_markup(state))
