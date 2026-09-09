# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``navin agi world``: the world model (S3) from the terminal.

Same switches as the AGI panel, per project (``.navin/world-model.json``):
the master flag, the three corridors (log / train / advise), the readable
beliefs, and the jobs that never run inside a chat turn: ``train`` (a human
forcing a run), ``run`` (train if due), ``exam`` (same frozen set, never a new
one), ``ab`` (offline A/B), ``rollback`` (checkpoint N-1), ``freeze`` (a new
held-out version).
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
    if value is True or value in ("up", "gain", "trained", "scored", "frozen", "rolled_back"):
        return "green"
    if value is False or value in ("down", "regress", "tampered", "budget_exceeded", "error"):
        return "red"
    return "yellow"


def _fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.{digits}f}"
    return "-" if value is None else str(value)


def create_world_app(*, console: Console) -> typer.Typer:
    app = typer.Typer(
        help="World model: Navin learns to predict what a tool will answer. Its exam unlocks the policy switch.",
        no_args_is_help=True,
    )

    project_option = typer.Option(None, "--project", "-p", help="Project folder (default: current directory)")
    json_option = typer.Option(False, "--json", help="Print JSON instead of a table")

    def _state(workspace: Path) -> dict[str, Any]:
        from navin.world_model.state import world_state

        return world_state(workspace)

    def _action(project: str | None, action: str, *, actor: str = "human", key: str | None = None) -> dict[str, Any]:
        from navin.world_model.state import WorldActionError, world_action

        try:
            return world_action(_workspace(project), action, actor=actor, key=key)
        except WorldActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    def _update(project: str | None, fields: dict[str, Any]) -> dict[str, Any]:
        from navin.world_model.state import WorldActionError, world_update

        try:
            return world_update(_workspace(project), fields)
        except WorldActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    @app.command("status")
    def status(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Flag, corridors, journal size, frozen set, active head, score, gate."""
        workspace = _workspace(project)
        state = _state(workspace)
        if as_json:
            console.print_json(json.dumps(state, ensure_ascii=False, default=str))
            return
        table = Table(title=f"World model - {workspace}")
        table.add_column("Switch")
        table.add_column("Value")
        table.add_column("Meaning")
        rows = [
            ("world-model", state["enabled"], "master flag; off = no tool log, no training, no advice"),
            ("  log", state["log"], "one secret-free line per tool call, written after the call"),
            ("  train", state["train"], "training job when enough new calls arrived (never in a turn)"),
            ("  advise", state["advise"], "live advice; refused while the gate is closed"),
            ("  beliefs", state["beliefs"], "refresh .navin/BELIEFS.md after each checkpoint"),
            ("  skip_hint", state["skip_hint"], "later mode: flag repeated read-only calls (never write/delete/mail/payment)"),
        ]
        for name, value, meaning in rows:
            tone = _tone(value)
            table.add_row(name, f"[{tone}]{'on' if value else 'off'}[/{tone}]", meaning)
        console.print(table)
        if not state["enabled"]:
            console.print("[dim]Off: no file is read or created. navin agi world on[/dim]")
            return
        heldout = state["heldout"]
        console.print(
            f"Journal: [bold]{state['rows']}[/bold] calls  "
            f"Held-out: {heldout.get('rows', 0)} calls, version {escape(str(heldout.get('version') or '-'))}  "
            f"Active checkpoint: {escape(str((state['active'] or {}).get('checkpoint', '-')))}"
        )
        score = state.get("score")
        if score:
            tone = _tone(score.get("verdict"))
            console.print(
                f"Score on {escape(str(score.get('heldout_version')))}: log-loss "
                f"{_fmt(score.get('baseline_log_loss'))} ({escape(str(score.get('baseline_kind')))}) -> "
                f"[bold]{_fmt(score.get('log_loss'))}[/bold], wrong class "
                f"{_fmt(score.get('baseline_error_rate'))} -> {_fmt(score.get('error_rate'))}, "
                f"calibration (ECE) {_fmt(score.get('ece'))}: [{tone}]{escape(str(score.get('verdict')))}[/{tone}]"
            )
        gate = state["gate"]
        tone = _tone(gate["open"])
        console.print(
            f"Advice gate: [{tone}]{'open' if gate['open'] else 'closed'}[/{tone}]"
            + ("" if gate["open"] else " - " + escape("; ".join(gate["reasons"])))
        )
        live = state.get("live") or {}
        if live.get("advised"):
            console.print(f"Live advice: {live['advised']} given, precision {_fmt(live.get('precision'), 2)} on the last {live['window']}")
        if state["belief_items"]:
            console.print("Beliefs:")
            for belief in state["belief_items"]:
                console.print(f"  - {escape(belief['text'])}  [dim]{escape(belief['key'])}[/dim]")

    @app.command("on")
    def turn_on(project: str | None = project_option) -> None:
        """Turn the world model on: tool journal and training as stored, advice stays off."""
        state = _update(project, {"enabled": True})
        console.print(f"[green]world-model on[/green] log={state['log']} train={state['train']} advise={state['advise']}")

    @app.command("off")
    def turn_off(project: str | None = project_option) -> None:
        """Turn the world model off: no tool log, no training, no advice."""
        _update(project, {"enabled": False})
        console.print("[green]world-model off[/green]")

    @app.command("set")
    def set_field(
        field: str = typer.Argument(
            ..., help="enabled | log | train | advise | beliefs | skip_hint | confidence_threshold | train_every | min_rows"
        ),
        value: str = typer.Argument(..., help="on/off, an integer, or a number"),
        project: str | None = project_option,
    ) -> None:
        """Set one world-model field. ``advise on`` is refused while the gate is closed."""
        from navin.world_model.settings import BOOL_FIELDS, FLOAT_FIELDS, INT_FIELDS

        parsed: Any
        if field in BOOL_FIELDS:
            parsed = _parse_switch(value)
        elif field in INT_FIELDS:
            try:
                parsed = int(value)
            except ValueError as exc:
                raise typer.BadParameter("expected an integer") from exc
        elif field in FLOAT_FIELDS:
            try:
                parsed = float(value)
            except ValueError as exc:
                raise typer.BadParameter("expected a number") from exc
        else:
            parsed = value
        state = _update(project, {field: parsed})
        console.print_json(json.dumps({k: state[k] for k in state["fields"]}, ensure_ascii=False))

    @app.command("log")
    def log(
        limit: int = typer.Option(20, "--limit", "-n", help="Number of calls"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Tail of the tool journal (.navin/world/trajectories.jsonl): class, key, duration."""
        from navin.world_model.journal import recent_trajectories

        rows = recent_trajectories(_workspace(project), limit=limit)
        if as_json:
            console.print_json(json.dumps(rows, ensure_ascii=False))
            return
        if not rows:
            console.print("[dim]No tool call logged yet.[/dim]")
            return
        table = Table(title="Tool journal (most recent first)")
        table.add_column("When")
        table.add_column("Key")
        table.add_column("Class")
        table.add_column("ms", justify="right")
        table.add_column("Observation")
        for row in rows:
            cls = str(row.get("cls"))
            tone = "green" if row.get("ok") else "red"
            table.add_row(
                escape(str(row.get("ts", ""))[11:19]),
                escape(str(row.get("key"))),
                f"[{tone}]{escape(cls)}[/{tone}]",
                escape(str(row.get("ms") if row.get("ms") is not None else "-")),
                escape(str(row.get("obs") or ""))[:60],
            )
        console.print(table)

    @app.command("train")
    def train_now(project: str | None = project_option) -> None:
        """Human: train a checkpoint now (the flag must be on; the train switch may be off)."""
        result = _action(project, "train")["result"]
        tone = _tone(result.get("status"))
        console.print(
            f"[{tone}]{escape(str(result.get('status')))}[/{tone}] "
            + (
                f"checkpoint {result.get('checkpoint')} log-loss {_fmt((result.get('baseline') or {}).get('log_loss'))} -> "
                f"{_fmt((result.get('metrics') or {}).get('log_loss'))} verdict {escape(str(result.get('verdict_vs_baseline')))} "
                f"{'activated' if result.get('activated') else 'not activated'}"
                if result.get("status") == "trained"
                else escape(str(result.get("reason") or ""))
            )
        )

    @app.command("run")
    def run(project: str | None = project_option) -> None:
        """Run the training job now if it is due (what the runner does after a turn)."""
        result = _action(project, "run", actor="auto")["result"]
        console.print_json(json.dumps(result, ensure_ascii=False))

    @app.command("exam")
    def exam(project: str | None = project_option) -> None:
        """Re-score the active head on the same frozen set (never a new one)."""
        result = _action(project, "exam", actor="human")["result"]
        if result.get("status") != "scored":
            console.print(f"[yellow]{escape(str(result.get('status')))}[/yellow] {escape(str(result.get('reason') or ''))}")
            return
        tone = _tone(result.get("verdict_vs_baseline"))
        console.print(
            f"checkpoint {result.get('checkpoint')} on {escape(str(result.get('heldout_version')))}: "
            f"log-loss {_fmt(result['baseline'].get('log_loss'))} -> {_fmt(result['metrics'].get('log_loss'))} "
            f"[{tone}]{escape(str(result.get('verdict_vs_baseline')))}[/{tone}]"
        )

    @app.command("ab")
    def ab(project: str | None = project_option) -> None:
        """Offline A/B of the active head on the frozen set (a gate for advise)."""
        result = _action(project, "ab", actor="human")["result"]
        if result.get("status") != "scored":
            console.print(f"[yellow]{escape(str(result.get('status')))}[/yellow] {escape(str(result.get('reason') or ''))}")
            return
        tone = _tone(result.get("verdict"))
        console.print(
            f"[{tone}]{escape(str(result.get('verdict')))}[/{tone}] useless calls {result.get('useless_calls')}, "
            f"flagged {result.get('flagged')}, avoided {result.get('avoided')}, false alarms {result.get('false_alarms')}, "
            f"precision {_fmt(result.get('precision'), 2)}"
        )

    @app.command("freeze")
    def freeze(
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
        project: str | None = project_option,
    ) -> None:
        """Human: freeze a new held-out version. Scores of different versions never compare."""
        if not yes and not typer.confirm("Freeze a new exam set? Existing scores stop being comparable."):
            raise typer.Exit(1)
        result = _action(project, "freeze")["result"]
        tone = _tone(result.get("status"))
        console.print(f"[{tone}]{escape(str(result.get('status')))}[/{tone}] {escape(str(result.get('version') or result.get('reason') or ''))} {result.get('rows', '')}")

    @app.command("checkpoints")
    def checkpoints(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Every checkpoint on disk with its score; the active and the previous one."""
        state = _state(_workspace(project))
        if as_json:
            console.print_json(json.dumps(state["checkpoints"], ensure_ascii=False))
            return
        if not state["checkpoints"]:
            console.print("[dim]No checkpoint yet.[/dim]")
            return
        table = Table(title="Checkpoints")
        table.add_column("#", justify="right")
        table.add_column("Trained")
        table.add_column("Rows", justify="right")
        table.add_column("Held-out")
        table.add_column("Log-loss", justify="right")
        table.add_column("Baseline", justify="right")
        table.add_column("Verdict")
        table.add_column("Serving")
        for item in state["checkpoints"]:
            metrics = item.get("metrics") or {}
            baseline = item.get("baseline") or {}
            verdict = item.get("verdict_vs_baseline")
            table.add_row(
                str(item["number"]),
                escape(str(item.get("trained_at") or ""))[:19],
                str(item.get("rows")),
                escape(str(item.get("heldout_version") or "")),
                _fmt(metrics.get("log_loss")),
                _fmt(baseline.get("log_loss")),
                f"[{_tone(verdict)}]{escape(str(verdict))}[/{_tone(verdict)}]",
                "active" if item.get("active") else ("previous" if item.get("previous") else ""),
            )
        console.print(table)

    @app.command("scoreboard")
    def scoreboard(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Every exam line: baseline -> checkpoint, verdict up / flat / down."""
        state = _state(_workspace(project))
        if as_json:
            console.print_json(json.dumps(state["scoreboard"], ensure_ascii=False))
            return
        if not state["scoreboard"]:
            console.print("[dim]No exam yet.[/dim]")
            return
        for entry in state["scoreboard"]:
            tone = _tone(entry.get("verdict"))
            console.print(
                f"[dim]{escape(str(entry.get('ts')))}[/dim] ckpt {entry.get('checkpoint')} "
                f"{_fmt(entry.get('baseline_log_loss'))} -> {_fmt(entry.get('log_loss'))} "
                f"[{tone}]{escape(str(entry.get('verdict')))}[/{tone}]"
                + (" (exam)" if entry.get("exam") else "")
                + (" active" if entry.get("activated") else "")
            )

    @app.command("rollback")
    def rollback(project: str | None = project_option) -> None:
        """Human: back to checkpoint N-1 (the newer one stays on disk as evidence)."""
        result = _action(project, "rollback")["result"]
        console.print_json(json.dumps(result, ensure_ascii=False))

    @app.command("beliefs")
    def beliefs(
        render: bool = typer.Option(False, "--render", help="Write .navin/BELIEFS.md now"),
        discard: str | None = typer.Option(None, "--discard", help="Belief key to throw away (never comes back)"),
        restore: bool = typer.Option(False, "--restore", help="Forget every discard"),
        project: str | None = project_option,
    ) -> None:
        """Readable beliefs of the active head (10 lines max). Not injected while advise is off."""
        if discard:
            result = _action(project, "discard_belief", key=discard)["result"]
        elif restore:
            result = _action(project, "restore_beliefs")["result"]
        elif render:
            result = _action(project, "beliefs")["result"]
            console.print(f"[green]written[/green] {escape(str(result.get('file')))}")
        else:
            result = {"beliefs": _state(_workspace(project))["belief_items"]}
        rows = result.get("beliefs") or []
        if not rows:
            console.print("[dim]No confident belief yet.[/dim]")
            return
        for belief in rows:
            console.print(f"- {escape(belief['text'])}  [dim]{escape(belief['key'])}[/dim]")

    @app.command("journal")
    def journal(
        limit: int = typer.Option(30, "--limit", "-n", help="Number of lines"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Tail of the world model journal (trained / activated / rollback / advise...)."""
        from navin.world_model.journal import read_journal

        rows = read_journal(_workspace(project), limit=limit)
        if as_json:
            console.print_json(json.dumps(rows, ensure_ascii=False, default=str))
            return
        if not rows:
            console.print("[dim]Journal is empty.[/dim]")
            return
        for row in rows:
            extra = {k: v for k, v in row.items() if k not in ("ts", "event")}
            console.print(
                f"[dim]{escape(str(row.get('ts')))}[/dim] [bold]{escape(str(row.get('event')))}[/bold] "
                f"{escape(json.dumps(extra, ensure_ascii=False, default=str)) if extra else ''}"
            )

    return app
