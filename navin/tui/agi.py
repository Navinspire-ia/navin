# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""AGI panel for navin-cli: the same switches as the desktop rail.

Reads and writes ``.navin/skills-evolve.json``, ``world-model.json``,
``policy.json``, ``transfer.json`` and ``cognition.json`` through the same
state helpers as ``navin agi`` and the WebUI. Jobs never run inside a chat
turn: they run in a worker thread (or a child process for training).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static

UNLOCK = (
    "Each stage earns the next one. A grey switch is not broken: it turns "
    "clickable by itself once the stage below has passed its exam. Nothing "
    "to edit, no file to touch."
)

# (group, field, label, help) - same names the desktop panel writes.
SWITCHES: tuple[tuple[str, str, str, str], ...] = (
    (
        "skills",
        "enabled",
        "Skills evolution",
        "Master switch. Off: no draft, no exam, no new skill. Stored in .navin/skills-evolve.json.",
    ),
    (
        "skills",
        "draft",
        "Auto-draft",
        "The same tool failure a few times in a row queues a draft after the turn.",
    ),
    (
        "skills",
        "promote_project",
        "Promote to this project",
        "An eligible draft (up, no suite down) enters .navin/skills. Off: wait for Promote.",
    ),
    (
        "skills",
        "publish_harness",
        "Allow publishing to every project",
        "Shows Publish on promoted skills. Copy to ~/.navin/skills is always your click.",
    ),
    (
        "world",
        "enabled",
        "World model",
        "Master switch. Off: no journal, no training, no advice. Stored in .navin/world-model.json.",
    ),
    (
        "world",
        "log",
        "Tool journal",
        "One secret-free line per tool call in .navin/world/trajectories.jsonl.",
    ),
    (
        "world",
        "train",
        "Train offline",
        "After enough new calls, a job outside the chat fits a local head.",
    ),
    (
        "world",
        "advise",
        "Live advice",
        "Advisory only. Refused until the exam went down and an A/B proved a gain.",
    ),
    (
        "world",
        "beliefs",
        "Readable beliefs",
        "Refresh .navin/BELIEFS.md after each checkpoint. Never read by the prompt while advice is off.",
    ),
    (
        "policy",
        "enabled",
        "Policy learning",
        "Needs the world model radar up. Stored in .navin/policy.json.",
    ),
    (
        "policy",
        "log",
        "Eval trajectories",
        "One line per eval step in .navin/policy/trajectories.jsonl. Never from a chat turn.",
    ),
    (
        "policy",
        "train",
        "Train adapter offline",
        "Child process: battery in sandboxes, adapter N+1 only if it beats N with no suite down.",
    ),
    (
        "policy",
        "steer",
        "Live steer",
        "Soft proposal only. Refused until N+1 beat N and an A/B gained.",
    ),
    (
        "transfer",
        "enabled",
        "Transfer protocol",
        "Hidden exam plus shutdown dossier, not a mode. Stored in .navin/transfer.json.",
    ),
    (
        "memory",
        "enabled",
        "Episodic memory",
        "Remember past turns of this project. Stored in .navin/cognition.json.",
    ),
    (
        "memory",
        "episodes",
        "Journal each turn",
        "After the answer: date, request, reply, tools. Heartbeat turns are skipped.",
    ),
    (
        "memory",
        "recall",
        "recall tool",
        "Read-only search over the journal and MEMORY.md. Bounded results.",
    ),
)


def _rung_line(rung: dict[str, Any]) -> str:
    state = str(rung.get("state") or "off")
    tone = {"on": "$success", "locked": "$warning", "off": "dim"}.get(state, "dim")
    return f"[{tone}]{escape(str(rung.get('label')))} {state}[/]"


def load_snapshot(workspace: Path) -> dict[str, Any]:
    from navin.cognition.settings import cognition_state
    from navin.policy.state import policy_state
    from navin.skills_evolve.state import agi_state
    from navin.transfer.ladder import ladder_dicts
    from navin.transfer.state import transfer_state
    from navin.world_model.state import world_state

    return {
        "ladder": ladder_dicts(workspace),
        "skills": agi_state(workspace),
        "world": world_state(workspace),
        "policy": policy_state(workspace),
        "transfer": transfer_state(workspace),
        "memory": cognition_state(workspace),
    }


def apply_switch(workspace: Path, group: str, field: str, value: bool) -> dict[str, Any]:
    payload = {field: value}
    if group == "skills":
        from navin.skills_evolve.state import agi_update

        return {"skills": agi_update(workspace, payload)}
    if group == "world":
        from navin.world_model.state import world_update

        return {"world": world_update(workspace, payload)}
    if group == "policy":
        from navin.policy.state import policy_update

        return {"policy": policy_update(workspace, payload)}
    if group == "transfer":
        from navin.transfer.state import transfer_update

        return {"transfer": transfer_update(workspace, payload)}
    if group == "memory":
        from navin.cognition.settings import cognition_state, update_settings

        update_settings(workspace, payload)
        return {"memory": cognition_state(workspace)}
    raise ValueError(f"unknown AGI group: {group}")


def run_named_action(workspace: Path, kind: str, action: str) -> str:
    actor = "human"
    if kind == "skills":
        from navin.skills_evolve.state import agi_action

        agi_action(workspace, action, actor=actor)
        return f"skills {action} done"
    if kind == "world":
        from navin.world_model.state import world_action

        world_action(workspace, action, actor=actor)
        return f"world {action} done"
    if kind == "policy":
        from navin.policy.state import policy_action

        policy_action(workspace, action, actor=actor)
        return f"policy {action} done"
    if kind == "transfer":
        from navin.transfer.state import transfer_action

        transfer_action(workspace, action, actor=actor)
        return f"transfer {action} done"
    raise ValueError(f"unknown AGI action family: {kind}")


class AgiSwitch(Static):
    """One clickable switch: label, On/Off/Locked, then the help line."""

    DEFAULT_CSS = """
    AgiSwitch { height: auto; margin: 0 0 1 0; color: $foreground; }
    AgiSwitch:hover { background: $primary 16%; }
    AgiSwitch.-locked { color: $text-muted; }
    """

    class Pressed(Message):
        def __init__(self, group: str, field: str) -> None:
            super().__init__()
            self.group = group
            self.field = field

    def __init__(self, group: str, field: str, label: str, help_text: str) -> None:
        super().__init__("", markup=True, id=f"sw-{group}-{field}")
        self.group = group
        self.field = field
        self.label = label
        self.help_text = help_text
        self.on = False
        self.locked = False

    def set_state(self, *, on: bool, locked: bool = False) -> None:
        self.on = on
        self.locked = locked
        self.set_class(locked, "-locked")
        if locked:
            badge = "[$warning]Locked[/]"
        elif on:
            badge = "[$success]On[/]"
        else:
            badge = "[dim]Off[/dim]"
        self.update(
            f"[b]{escape(self.label)}[/]  {badge}\n[dim]{escape(self.help_text)}[/dim]"
        )

    def on_click(self) -> None:
        self.post_message(self.Pressed(self.group, self.field))


class AgiScreen(ModalScreen[None]):
    """Desktop AGI rail, in the terminal."""

    DEFAULT_CSS = """
    AgiScreen { align: center middle; }
    AgiScreen > Vertical {
        width: 96%;
        height: 94%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    AgiScreen .a-title { text-style: bold; color: $primary; height: 1; }
    AgiScreen #a-ladder { height: auto; margin: 0 0 1 0; color: $text-muted; }
    AgiScreen #a-unlock { height: auto; margin: 0 0 1 0; color: $text-muted; }
    AgiScreen #a-body { height: 1fr; }
    AgiScreen .a-head { text-style: bold; color: $primary; margin: 1 0 0 0; height: 1; }
    AgiScreen .a-note { height: auto; color: $text-muted; margin: 0 0 1 0; }
    AgiScreen #a-actions { height: auto; margin: 1 0 0 0; }
    AgiScreen #a-actions Button { margin: 0 1 0 0; }
    AgiScreen #a-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "job('skills', 'run')", "Run skill jobs", show=False),
        Binding("2", "job('world', 'train')", "Train world", show=False),
        Binding("3", "job('policy', 'train')", "Train policy", show=False),
        Binding("4", "job('transfer', 'safety')", "Safety dossier", show=False),
        Binding("5", "job('transfer', 'kill_drill')", "Kill drill", show=False),
        Binding("6", "job('transfer', 'verify')", "Verify suites", show=False),
        Binding("7", "job('transfer', 'campaign')", "Campaign", show=False),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.snap: dict[str, Any] = {}
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"AGI  [dim]{escape(str(self.root))}[/dim]",
                classes="a-title",
                markup=True,
            )
            yield Static("", id="a-ladder", markup=True)
            yield Static(UNLOCK, id="a-unlock")
            with VerticalScroll(id="a-body"):
                yield Static("Skills evolution", classes="a-head")
                yield Static("", id="note-skills", classes="a-note", markup=True)
                for group, field, label, help_text in SWITCHES:
                    if group == "skills":
                        yield AgiSwitch(group, field, label, help_text)
                yield Static("World model", classes="a-head")
                yield Static("", id="note-world", classes="a-note", markup=True)
                for group, field, label, help_text in SWITCHES:
                    if group == "world":
                        yield AgiSwitch(group, field, label, help_text)
                yield Static("Policy", classes="a-head")
                yield Static("", id="note-policy", classes="a-note", markup=True)
                for group, field, label, help_text in SWITCHES:
                    if group == "policy":
                        yield AgiSwitch(group, field, label, help_text)
                yield Static("Transfer protocol", classes="a-head")
                yield Static("", id="note-transfer", classes="a-note", markup=True)
                for group, field, label, help_text in SWITCHES:
                    if group == "transfer":
                        yield AgiSwitch(group, field, label, help_text)
                yield Static("Memory", classes="a-head")
                yield Static("", id="note-memory", classes="a-note", markup=True)
                for group, field, label, help_text in SWITCHES:
                    if group == "memory":
                        yield AgiSwitch(group, field, label, help_text)
            with Horizontal(id="a-actions"):
                yield Button("Refresh  [r]", id="a-refresh")
                yield Button("Skill jobs  [1]", id="a-skills-run")
                yield Button("World train  [2]", id="a-world-train")
                yield Button("Policy train  [3]", id="a-policy-train")
                yield Button("Safety  [4]", id="a-transfer-safety")
                yield Button("Kill drill  [5]", id="a-transfer-kill")
                yield Button("Verify  [6]", id="a-transfer-verify")
                yield Button("Campaign  [7]", id="a-transfer-campaign")
            yield Static("Loading...", id="a-status", markup=True)

    def on_mount(self) -> None:
        self.action_refresh()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.query_one("#a-status", Static).update("[dim]refreshing…[/dim]")
        self._load()

    @work(thread=True, exclusive=True, group="agi-load")
    def _load(self) -> None:
        try:
            snap = load_snapshot(self.root)
            error = ""
        except Exception as exc:  # noqa: BLE001
            snap = {}
            error = str(exc)
        self.app.call_from_thread(self._apply, snap, error)

    def _apply(self, snap: dict[str, Any], error: str) -> None:
        self._busy = False
        if error:
            self.query_one("#a-status", Static).update(f"[$error]{escape(error)}[/]")
            return
        self.snap = snap
        self._paint()

    def _paint(self) -> None:
        skills = self.snap.get("skills") or {}
        world = self.snap.get("world") or {}
        policy = self.snap.get("policy") or {}
        transfer = self.snap.get("transfer") or {}
        memory = self.snap.get("memory") or {}
        ladder = self.snap.get("ladder") or []
        self.query_one("#a-ladder", Static).update("  >  ".join(_rung_line(r) for r in ladder))

        locked: dict[str, bool] = {}
        waits: dict[str, str] = {}
        for rung in ladder:
            locked[str(rung.get("id"))] = rung.get("state") == "locked"
            if rung.get("state") == "locked":
                reasons = "; ".join(str(x) for x in (rung.get("reasons") or []))
                waits[str(rung.get("id"))] = f"{rung.get('waits') or ''} Still missing: {reasons}".strip()

        values = {
            "skills": skills,
            "world": world,
            "policy": policy,
            "transfer": transfer,
            "memory": memory,
        }
        for switch in self.query(AgiSwitch):
            block = values.get(switch.group) or {}
            is_locked = False
            if switch.group == "policy" and switch.field == "enabled":
                is_locked = bool(locked.get("policy")) and not bool(block.get("enabled"))
            if switch.group == "transfer" and switch.field == "enabled":
                is_locked = bool(locked.get("transfer")) and not bool(block.get("enabled"))
            switch.set_state(on=bool(block.get(switch.field)), locked=is_locked)

        drafts = skills.get("drafts") or []
        pending = skills.get("pending_jobs") or []
        if not skills.get("enabled"):
            skill_note = "Off by default: no draft, no exam, no new skill."
        elif drafts:
            skill_note = f"{len(drafts)} draft(s) · {len(pending)} pending job(s)"
        else:
            skill_note = "No skill draft yet."
        self.query_one("#note-skills", Static).update(escape(skill_note))

        gate = world.get("gate") or {}
        world_note = (
            f"rows {world.get('rows') or 0} · advice {'open' if gate.get('open') else 'closed'}"
            if world.get("enabled")
            else "Off by default: no tool log, no training, no advice."
        )
        if gate.get("reasons") and not gate.get("open"):
            world_note += "  " + "; ".join(str(x) for x in gate["reasons"][:3])
        self.query_one("#note-world", Static).update(escape(world_note))

        radar = (policy.get("radar") or {}) if isinstance(policy.get("radar"), dict) else {}
        if locked.get("policy") and waits.get("policy"):
            policy_note = waits["policy"]
        elif policy.get("enabled"):
            policy_note = f"episodes {policy.get('episodes') or 0} · steer gate {'open' if (policy.get('gate') or {}).get('open') else 'closed'}"
        else:
            policy_note = "No policy without a radar." + (
                " " + "; ".join(str(x) for x in (radar.get("reasons") or [])[:3]) if radar.get("reasons") else ""
            )
        self.query_one("#note-policy", Static).update(escape(policy_note))

        claim = transfer.get("claim") or {}
        prereqs = transfer.get("prereqs") or {}
        if locked.get("transfer") and waits.get("transfer"):
            transfer_note = waits["transfer"]
        else:
            transfer_note = (
                f"Claim {claim.get('status') or 'forbidden'} · "
                f"prereqs {'ok' if prereqs.get('ok') else 'missing'}"
            )
            if prereqs.get("reasons") and not prereqs.get("ok"):
                transfer_note += "  " + " / ".join(str(x) for x in prereqs["reasons"][:4])
        self.query_one("#note-transfer", Static).update(escape(transfer_note))

        if memory.get("recall") and memory.get("enabled"):
            mem_note = "The agent has the recall tool now, no restart needed."
            if memory.get("recall_requires_restart"):
                mem_note = "recall joins after the next gateway restart."
        else:
            mem_note = f"journal {memory.get('journal_bytes') or 0} bytes · .navin/cognition.json"
        self.query_one("#note-memory", Static).update(escape(mem_note))
        self.query_one("#a-status", Static).update("[dim]r refresh · enter or click a switch · esc close[/dim]")

    def _current(self, group: str, field: str) -> bool:
        block = self.snap.get(group) or {}
        return bool(block.get(field))

    def _locked(self, group: str, field: str) -> bool:
        if field != "enabled":
            return False
        for rung in self.snap.get("ladder") or []:
            if rung.get("id") == group and rung.get("state") == "locked":
                return not self._current(group, field)
        return False

    @on(AgiSwitch.Pressed)
    def _switch(self, event: AgiSwitch.Pressed) -> None:
        if self._busy:
            return
        if self._locked(event.group, event.field):
            self.query_one("#a-status", Static).update(
                "[$warning]Locked: the stage below has not passed its exam yet.[/]"
            )
            return
        nxt = not self._current(event.group, event.field)
        self._busy = True
        self.query_one("#a-status", Static).update("[dim]saving…[/dim]")
        self._write(event.group, event.field, nxt)

    @work(thread=True, exclusive=True, group="agi-write")
    def _write(self, group: str, field: str, value: bool) -> None:
        try:
            apply_switch(self.root, group, field, value)
            snap = load_snapshot(self.root)
            error = ""
        except Exception as exc:  # noqa: BLE001
            snap = self.snap
            error = str(exc)
        self.app.call_from_thread(self._after_write, snap, error)

    def _after_write(self, snap: dict[str, Any], error: str) -> None:
        self._busy = False
        if snap:
            self.snap = snap
            self._paint()
        if error:
            self.query_one("#a-status", Static).update(f"[$error]{escape(error)}[/]")
        else:
            self.query_one("#a-status", Static).update("[$success]saved[/]")

    def action_job(self, kind: str, action: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.query_one("#a-status", Static).update(f"[dim]{kind} {action}…[/dim]")
        self._run_job(kind, action)

    @work(thread=True, exclusive=True, group="agi-job")
    def _run_job(self, kind: str, action: str) -> None:
        try:
            message = run_named_action(self.root, kind, action)
            snap = load_snapshot(self.root)
            error = ""
        except Exception as exc:  # noqa: BLE001
            message = ""
            snap = self.snap
            error = str(exc)
        self.app.call_from_thread(self._after_job, snap, message, error)

    def _after_job(self, snap: dict[str, Any], message: str, error: str) -> None:
        self._busy = False
        if snap:
            self.snap = snap
            self._paint()
        if error:
            self.query_one("#a-status", Static).update(f"[$error]{escape(error)}[/]")
        else:
            self.query_one("#a-status", Static).update(f"[$success]{escape(message)}[/]")

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        event.stop()
        if bid == "a-refresh":
            self.action_refresh()
        elif bid == "a-skills-run":
            self.action_job("skills", "run")
        elif bid == "a-world-train":
            self.action_job("world", "train")
        elif bid == "a-policy-train":
            self.action_job("policy", "train")
        elif bid == "a-transfer-safety":
            self.action_job("transfer", "safety")
        elif bid == "a-transfer-kill":
            self.action_job("transfer", "kill_drill")
        elif bid == "a-transfer-verify":
            self.action_job("transfer", "verify")
        elif bid == "a-transfer-campaign":
            self.action_job("transfer", "campaign")
