# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``navin agi``: the AGI panel from the terminal.

Two families of switches live here, per project:

* **Skills evolution** (``.navin/skills-evolve.json``): the master flag, the
  three corridor stages, the drafts with their scores, and the human buttons
  (publish / force / discard).
* **Memory** (``.navin/cognition.json``): episodic journal and the recall
  tool, the same switches the AGI panel shows.
* **World model** (``.navin/world-model.json``, ``navin agi world ...``): the
  tool journal, the local head, its frozen exam and the gated advice.
* **Policy** (``.navin/policy.json``, ``navin agi policy ...``): eval
  trajectories, the local policy adapter, N vs N+1 on frozen batteries and
  the gated steer.
* **Transfer** (``.navin/transfer.json``, ``navin agi transfer ...``): the
  S5 protocol - secret campaign, safety dossier, claim forbidden or
  discussable. Refused while S2, S3.3 and S4.3 are not up.

Everything runs in this process, outside any agent turn: ``navin agi run``
drains the pending draft jobs, ``navin agi guard`` re-examines promoted
skills, ``navin agi exam <name>`` reruns the same frozen battery.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

_ON = {"on", "true", "1", "yes", "enable", "enabled"}
_OFF = {"off", "false", "0", "no", "disable", "disabled"}


def _parse_switch(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _ON:
        return True
    if lowered in _OFF:
        return False
    raise typer.BadParameter("expected on or off")


def _workspace(path: str | None) -> Path:
    return Path(path).expanduser().resolve() if path else Path.cwd()


def _tone(value: Any) -> str:
    if value is True or value == "up":
        return "green"
    if value is False or value == "down":
        return "red"
    return "yellow"


def create_agi_app(*, console: Console) -> typer.Typer:
    agi_app = typer.Typer(
        help="AGI: skills evolution and memory switches for a project.",
        no_args_is_help=True,
    )

    project_option = typer.Option(
        None, "--project", "-p", help="Project folder (default: current directory)"
    )
    json_option = typer.Option(False, "--json", help="Print JSON instead of a table")

    def _state(workspace: Path) -> dict[str, Any]:
        from navin.skills_evolve.state import agi_state

        return agi_state(workspace)

    def _print_drafts(drafts: list[dict[str, Any]]) -> None:
        if not drafts:
            console.print("[dim]No skill draft yet.[/dim]")
            return
        table = Table(title="Skill drafts")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Score /20", justify="right")
        table.add_column("Base", justify="right")
        table.add_column("Verdict")
        table.add_column("Suites")
        table.add_column("Tries", justify="right")
        table.add_column("Trigger")
        for draft in drafts:
            suites = draft.get("suites") or {}
            suite_text = " ".join(
                f"[{_tone(v)}]{k}:{v}[/{_tone(v)}]" for k, v in sorted(suites.items())
            )
            verdict = draft.get("verdict")
            table.add_row(
                escape(str(draft.get("name"))),
                escape(str(draft.get("status"))),
                "" if draft.get("score") is None else str(draft.get("score")),
                "" if draft.get("baseline_score") is None else str(draft.get("baseline_score")),
                f"[{_tone(verdict)}]{escape(str(verdict or '-'))}[/{_tone(verdict)}]",
                suite_text,
                str(draft.get("attempts") or 0),
                escape(str(draft.get("trigger") or draft.get("origin") or "")),
            )
        console.print(table)

    def _print_ladder(workspace: Path) -> None:
        """Locked switches are not broken: say what each one waits for."""
        from navin.transfer.ladder import ladder

        rungs = ladder(workspace)
        tones = {"on": "green", "off": "dim", "locked": "yellow"}
        line = "  >  ".join(f"{r.label} [{tones[r.state]}]{r.state}[/{tones[r.state]}]" for r in rungs)
        console.print(f"Ladder: {line}")
        for rung in rungs:
            if rung.state == "locked":
                console.print(f"  [yellow]{rung.label}[/yellow] {rung.waits}; missing: {escape('; '.join(rung.reasons))}")
        if any(r.state == "locked" for r in rungs):
            console.print("  A locked switch turns clickable by itself once the stage below has passed its exam. Nothing to edit.")

    @agi_app.command("status")
    def status(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Flag, stages, battery version, drafts and pending jobs."""
        from navin.cognition import cognition_state
        from navin.policy.settings import read_settings as read_policy_settings
        from navin.transfer.settings import read_settings as read_transfer_settings
        from navin.world_model.settings import read_settings as read_world_settings

        workspace = _workspace(project)
        state = _state(workspace)
        memory = cognition_state(workspace)
        world = read_world_settings(workspace).as_dict()
        policy = read_policy_settings(workspace).as_dict()
        transfer = read_transfer_settings(workspace).as_dict()
        if as_json:
            console.print_json(
                json.dumps(
                    {"skills_evolve": state, "memory": memory, "world_model": world, "policy": policy, "transfer": transfer},
                    ensure_ascii=False,
                )
            )
            return
        table = Table(title=f"AGI - {workspace}")
        table.add_column("Switch")
        table.add_column("Value")
        table.add_column("Meaning")
        rows = [
            ("skills-evolve", state["enabled"], "master flag; off = no draft, no exam, no new skill"),
            ("  draft", state["draft"], "Navin may write skill drafts alone"),
            ("  promote_project", state["promote_project"], "an eligible draft enters .navin/skills without a click"),
            ("  publish_harness", state["publish_harness"], "offer the human 'publish for every project' button"),
            ("world-model", world["enabled"], "master flag; off = no tool log, no training, no advice (navin agi world)"),
            ("  log", world["log"], "one secret-free line per tool call, after the call"),
            ("  train", world["train"], "training job outside any turn"),
            ("  advise", world["advise"], "live advice; only once exam up and A/B gain"),
            ("policy", policy["enabled"], "master flag; off = no trajectory, no training, no steer (navin agi policy)"),
            ("  log", policy["log"], "one line per step of an eval episode, from the training process"),
            ("  train", policy["train"], "training job in a child process, outside any turn"),
            ("  steer", policy["steer"], "live proposal; only once N+1 beat N with no suite down and A/B gain"),
            ("transfer", transfer["enabled"], "hidden exam + shutdown dossier; off = no secret suite, no dossier, no claim (navin agi transfer)"),
            ("memory", memory["enabled"], "episodic memory master (.navin/cognition.json)"),
            ("  episodes", memory["episodes"], "journal each turn"),
            ("  recall", memory["recall"], "recall tool"),
        ]
        for name, value, meaning in rows:
            tone = _tone(value)
            table.add_row(name, f"[{tone}]{'on' if value else 'off'}[/{tone}]", meaning)
        console.print(table)
        _print_ladder(workspace)
        battery = state.get("battery") or {}
        console.print(
            f"Battery [bold]{escape(str(battery.get('version')))}[/bold]: "
            + ", ".join(
                f"{suite['id']} ({suite['cases']})" for suite in battery.get("suites", [])
            )
            + f"  author={state['author']} exam_model={state['exam_model']} "
            f"threshold={state['failure_threshold']} max_attempts={state['max_attempts']}"
        )
        if state["pending_jobs"]:
            console.print(f"[yellow]{len(state['pending_jobs'])} draft job(s) pending[/yellow] (run: navin agi run)")
        _print_drafts(state["drafts"])

    @agi_app.command("on")
    def turn_on(project: str | None = project_option) -> None:
        """Turn skills evolution on for this project (draft + promote stages as stored)."""
        from navin.skills_evolve import update_settings

        settings = update_settings(_workspace(project), {"enabled": True})
        console.print(
            f"[green]skills-evolve on[/green] draft={settings.draft} "
            f"promote_project={settings.promote_project} publish_harness={settings.publish_harness}"
        )

    @agi_app.command("off")
    def turn_off(project: str | None = project_option) -> None:
        """Turn skills evolution off: no draft, no exam, no new skill."""
        from navin.skills_evolve import update_settings

        update_settings(_workspace(project), {"enabled": False})
        console.print("[green]skills-evolve off[/green]")

    @agi_app.command("set")
    def set_field(
        field: str = typer.Argument(
            ...,
            help="enabled | draft | promote_project | publish_harness | failure_threshold | max_attempts | exam_model | author",
        ),
        value: str = typer.Argument(..., help="on/off, an integer, or a choice"),
        project: str | None = project_option,
    ) -> None:
        """Set one skills-evolve field."""
        from navin.skills_evolve import update_settings
        from navin.skills_evolve.settings import _BOOL_FIELDS, _INT_FIELDS

        parsed: Any
        if field in _BOOL_FIELDS:
            parsed = _parse_switch(value)
        elif field in _INT_FIELDS:
            try:
                parsed = int(value)
            except ValueError as exc:
                raise typer.BadParameter("expected an integer") from exc
        else:
            parsed = value
        try:
            settings = update_settings(_workspace(project), {field: parsed})
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc
        console.print_json(json.dumps(settings.as_dict()))

    @agi_app.command("memory")
    def memory(
        switch: str = typer.Argument(..., help="on or off"),
        episodes: str | None = typer.Option(None, "--episodes", help="on/off: journal each turn"),
        recall: str | None = typer.Option(None, "--recall", help="on/off: recall tool"),
        project: str | None = project_option,
    ) -> None:
        """Episodic memory switches (.navin/cognition.json), same as the AGI panel."""
        from navin.cognition import cognition_state, update_settings

        fields: dict[str, bool] = {"enabled": _parse_switch(switch)}
        if episodes is not None:
            fields["episodes"] = _parse_switch(episodes)
        if recall is not None:
            fields["recall"] = _parse_switch(recall)
        workspace = _workspace(project)
        update_settings(workspace, fields)
        state = cognition_state(workspace)
        console.print(
            f"memory={'on' if state['enabled'] else 'off'} episodes={'on' if state['episodes'] else 'off'} "
            f"recall={'on' if state['recall'] else 'off'}"
            + (" [yellow](recall joins after the next gateway restart)[/yellow]" if state["recall_requires_restart"] else "")
        )

    @agi_app.command("drafts")
    def drafts(project: str | None = project_option, as_json: bool = json_option) -> None:
        """List the skill drafts with score, verdict and status."""
        state = _state(_workspace(project))
        if as_json:
            console.print_json(json.dumps(state["drafts"], ensure_ascii=False))
            return
        if not state["enabled"]:
            console.print("[dim]skills-evolve is off: drafts are not read.[/dim]")
        _print_drafts(state["drafts"])

    @agi_app.command("show")
    def show(name: str = typer.Argument(..., help="Draft name"), project: str | None = project_option) -> None:
        """Print the SKILL.md of a draft."""
        from navin.skills_evolve.state import draft_text

        text = draft_text(_workspace(project), name)
        if text is None:
            console.print(f"[red]no draft named {escape(name)}[/red]")
            raise typer.Exit(1)
        console.print(escape(text))

    @agi_app.command("journal")
    def journal(
        limit: int = typer.Option(30, "--limit", "-n", help="Number of lines"),
        project: str | None = project_option,
    ) -> None:
        """Tail of the evolution journal (created / examined / kept / promoted...)."""
        from navin.skills_evolve.drafts import read_journal

        rows = read_journal(_workspace(project), limit=limit)
        if not rows:
            console.print("[dim]Journal is empty.[/dim]")
            return
        for row in rows:
            extra = {k: v for k, v in row.items() if k not in ("ts", "event", "name")}
            console.print(
                f"[dim]{escape(str(row.get('ts')))}[/dim] [bold]{escape(str(row.get('event')))}[/bold] "
                f"{escape(str(row.get('name') or ''))} {escape(json.dumps(extra, ensure_ascii=False)) if extra else ''}"
            )

    @agi_app.command("battery")
    def battery(project: str | None = project_option) -> None:
        """Version and suites of the frozen exam battery (the same for every project)."""
        from navin.skills_evolve.battery import load_battery

        del project  # accepted for symmetry with the other commands; the battery is global
        loaded = load_battery()
        table = Table(title=f"Exam battery {loaded.version}")
        table.add_column("Suite")
        table.add_column("Title")
        table.add_column("Cases", justify="right")
        for suite in loaded.suites:
            table.add_row(suite.id, suite.title, str(suite.size))
        console.print(table)
        console.print(f"Source: {loaded.source}")

    def _action(project: str | None, action: str, name: str | None = None, *, actor: str = "human", brief: dict[str, Any] | None = None) -> dict[str, Any]:
        from navin.skills_evolve.state import AgiActionError, agi_action

        try:
            return agi_action(_workspace(project), action, name=name, actor=actor, brief=brief)
        except AgiActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    @agi_app.command("draft")
    def draft(
        name: str = typer.Argument(..., help="Skill name (lowercase, hyphens)"),
        description: str = typer.Option(..., "--description", "-d", help="What the skill is for"),
        tool: str | None = typer.Option(None, "--tool", help="Tool involved (picks the practice family)"),
        hint: list[str] = typer.Option([], "--hint", help="Extra workflow line (repeatable)"),
        now: bool = typer.Option(False, "--now", help="Run the corridor right away instead of queueing"),
        project: str | None = project_option,
    ) -> None:
        """Queue a draft by hand (the "after the fact" trigger, outside any chat)."""
        brief = {"name": name, "description": description, "tool": tool, "hints": list(hint), "kind": "post_hoc"}
        payload = _action(project, "draft", name, brief=brief)
        console.print(f"[green]queued[/green] {escape(str(payload['result']['name']))}")
        if now:
            results = _action(project, "run")["result"]
            console.print_json(json.dumps(results, ensure_ascii=False))

    @agi_app.command("run")
    def run(project: str | None = project_option) -> None:
        """Drain the pending draft jobs now: draft, exam, correct, promote."""
        results = _action(project, "run")["result"]
        if not results:
            console.print("[dim]No pending job.[/dim]")
            return
        for item in results:
            tone = _tone(item.get("status") in ("promoted", "eligible"))
            console.print(
                f"[{tone}]{escape(str(item.get('status')))}[/{tone}] {escape(str(item.get('name')))} "
                f"score {item.get('baseline_score')} -> {item.get('best_score')} /20 "
                f"attempts={item.get('attempts')} {escape(str(item.get('reason') or ''))}"
            )

    @agi_app.command("exam")
    def exam(name: str = typer.Argument(..., help="Draft name"), project: str | None = project_option) -> None:
        """Rerun the same frozen battery on a draft (never a new exam)."""
        result = _action(project, "exam", name)["result"]
        verdict = (result.get("verdict") or {}).get("overall")
        console.print(
            f"{escape(name)}: [{_tone(verdict)}]{escape(str(verdict))}[/{_tone(verdict)}] "
            f"score {result.get('baseline_score')} -> {result.get('best_score')} /20, status {escape(str(result.get('status')))}"
        )

    @agi_app.command("guard")
    def guard(project: str | None = project_option) -> None:
        """Periodic guard: retire a promoted skill that now regresses."""
        result = _action(project, "guard")["result"]
        console.print_json(json.dumps(result, ensure_ascii=False))

    @agi_app.command("promote")
    def promote(name: str = typer.Argument(..., help="Draft name"), project: str | None = project_option) -> None:
        """Promote an eligible draft into .navin/skills (when promote_project is off)."""
        summary = _action(project, "promote", name)["result"]
        console.print(f"[green]promoted[/green] {escape(name)} score {summary.get('baseline_score')} -> {summary.get('score')} /20")

    @agi_app.command("force")
    def force(
        name: str = typer.Argument(..., help="Draft name"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
        project: str | None = project_option,
    ) -> None:
        """Human: promote a flat draft anyway. Traced in the journal, reversible."""
        if not yes and not typer.confirm(f"Force the flat draft {name} into the project?"):
            raise typer.Exit(1)
        _action(project, "force", name, actor="human")
        console.print(f"[green]forced[/green] {escape(name)} (rollback: navin agi rollback {escape(name)})")

    @agi_app.command("publish")
    def publish(
        name: str = typer.Argument(..., help="Promoted skill name"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
        project: str | None = project_option,
    ) -> None:
        """Human: copy a promoted skill to ~/.navin/skills (every project)."""
        if not yes and not typer.confirm(f"Publish {name} for every project on this machine?"):
            raise typer.Exit(1)
        payload = _action(project, "publish", name, actor="human")
        console.print(f"[green]published[/green] {escape(str(payload['result']['target']))}")

    @agi_app.command("rollback")
    def rollback(name: str = typer.Argument(..., help="Promoted skill name"), project: str | None = project_option) -> None:
        """Take a promoted skill out of the project (the draft keeps its text)."""
        _action(project, "rollback", name, actor="human")
        console.print(f"[green]retired[/green] {escape(name)}")

    @agi_app.command("discard")
    def discard(name: str = typer.Argument(..., help="Draft name"), project: str | None = project_option) -> None:
        """Throw a draft away."""
        _action(project, "discard", name, actor="human")
        console.print(f"[green]discarded[/green] {escape(name)}")

    from navin.cli.policy import create_policy_app
    from navin.cli.transfer import create_transfer_app
    from navin.cli.world import create_world_app

    agi_app.add_typer(create_world_app(console=console), name="world")
    agi_app.add_typer(create_policy_app(console=console), name="policy")
    agi_app.add_typer(create_transfer_app(console=console), name="transfer")

    return agi_app
