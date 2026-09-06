"""``navin agi policy``: policy learning (S4) from the terminal.

Same switches as the AGI panel, per project (``.navin/policy.json``): the
master flag (refused while the world model radar S3.3 is not up), the three
corridors (log / train / steer), and the jobs that never run inside a chat
turn: ``train`` (a human forcing a run, in a child process), ``run`` (train
if due), ``exam`` (same frozen set, never a new one), ``ab`` (offline A/B),
``rollback`` (adapter N-1), ``force`` (a flat adapter, on purpose),
``freeze`` (a new held-out version), ``publish`` / ``adopt`` (an adapter
outside this project, human only).
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
    if value is True or value in ("up", "gain", "trained", "scored", "frozen", "rolled_back", "forced", "published", "adopted"):
        return "green"
    if value is False or value in ("down", "regress", "tampered", "budget_exceeded", "error", "radar_down", "refused"):
        return "red"
    return "yellow"


def _fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.{digits}f}"
    return "-" if value is None else str(value)


def _pct(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{round(value * 100)}%"
    return "-"


def _suites(verdict: dict[str, Any] | None) -> str:
    if not isinstance(verdict, dict):
        return ""
    suites = verdict.get("suites") or {}
    return " ".join(f"{name}:{value}" for name, value in sorted(suites.items()))


def create_policy_app(*, console: Console) -> typer.Typer:
    app = typer.Typer(
        help="Policy learning: Navin learns which action to take, from eval trajectories. Locked until the world model has passed its exam.",
        no_args_is_help=True,
    )

    project_option = typer.Option(None, "--project", "-p", help="Project folder (default: current directory)")
    json_option = typer.Option(False, "--json", help="Print JSON instead of a table")

    def _state(workspace: Path) -> dict[str, Any]:
        from navin.policy.state import policy_state

        return policy_state(workspace)

    def _action(
        project: str | None,
        action: str,
        *,
        actor: str = "human",
        name: str | None = None,
        number: int | None = None,
    ) -> dict[str, Any]:
        from navin.policy.state import PolicyActionError, policy_action

        try:
            return policy_action(_workspace(project), action, actor=actor, name=name, number=number)
        except PolicyActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    def _update(project: str | None, fields: dict[str, Any]) -> dict[str, Any]:
        from navin.policy.state import PolicyActionError, policy_update

        try:
            return policy_update(_workspace(project), fields)
        except PolicyActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    @app.command("status")
    def status(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Flag, corridors, radar, trajectories, frozen set, adapters, score, steer gate."""
        workspace = _workspace(project)
        state = _state(workspace)
        if as_json:
            console.print_json(json.dumps(state, ensure_ascii=False, default=str))
            return
        table = Table(title=f"Policy learning - {workspace}")
        table.add_column("Switch")
        table.add_column("Value")
        table.add_column("Meaning")
        rows = [
            ("policy", state["enabled"], "master flag; off = no trajectory, no training, no steer (needs the world model radar up)"),
            ("  log", state["log"], "one line per step of an eval episode, written by the training process"),
            ("  train", state["train"], "training job every train_every turns, in a child process (never in a turn)"),
            ("  steer", state["steer"], "live proposal; refused while the gate is closed, cuts itself off on regression"),
        ]
        for name, value, meaning in rows:
            tone = _tone(value)
            table.add_row(name, f"[{tone}]{'on' if value else 'off'}[/{tone}]", meaning)
        console.print(table)
        radar = state.get("radar") or {}
        tone = _tone(radar.get("up"))
        console.print(
            f"Radar (world model exam): [{tone}]{'up' if radar.get('up') else 'not up'}[/{tone}]"
            + ("" if radar.get("up") else " - " + escape("; ".join(radar.get("reasons") or [])))
        )
        if not state["enabled"]:
            console.print("[dim]Off: no file is read or created. navin agi policy on[/dim]")
            return
        battery = state.get("battery") or {}
        heldout = state["heldout"]
        console.print(
            f"Battery: {escape(str(battery.get('version') or '-'))} "
            f"({battery.get('total_cases', 0)} cases, {battery.get('project_cases', 0)} from this project)  "
            f"Trajectories: [bold]{state['rows']}[/bold] steps, {state['episodes']} episodes  "
            f"Held-out: {heldout.get('rows', 0)} steps, version {escape(str(heldout.get('version') or '-'))}"
        )
        active = state["active"] or {}
        console.print(
            f"Active adapter: [bold]{escape(str(active.get('checkpoint', '-')))}[/bold]"
            + (" (forced by a human)" if active.get("forced") else "")
            + f"  Turns since last train: {state.get('turns_since_train', 0)}/{state['train_every']}"
        )
        score = state.get("score")
        if score:
            verdict = score.get("verdict_vs_active") or score.get("verdict_vs_baseline") or {}
            tone = _tone(verdict.get("overall"))
            console.print(
                f"Score on {escape(str(score.get('heldout_version')))}: next-action accuracy "
                f"{_pct(score.get('baseline_accuracy'))} ({escape(str(score.get('baseline_kind')))}) -> "
                f"[bold]{_pct(score.get('accuracy'))}[/bold] ({score.get('score')}/20), "
                f"vs {escape(str(score.get('reference')))}: [{tone}]{escape(str(verdict.get('overall')))}[/{tone}] "
                f"[dim]{escape(_suites(verdict))}[/dim]"
            )
        gate = state["gate"]
        tone = _tone(gate["open"])
        console.print(
            f"Steer gate: [{tone}]{'open' if gate['open'] else 'closed'}[/{tone}]"
            + ("" if gate["open"] else " - " + escape("; ".join(gate["reasons"])))
        )
        live = state.get("live") or {}
        if live.get("suggested"):
            console.print(f"Live steer: {live['suggested']} suggestions, precision {_fmt(live.get('precision'), 2)} on the last {live['window']}")

    @app.command("on")
    def turn_on(project: str | None = project_option) -> None:
        """Turn policy learning on (refused while the world model radar is not up). Steer stays off."""
        state = _update(project, {"enabled": True})
        console.print(f"[green]policy on[/green] log={state['log']} train={state['train']} steer={state['steer']}")

    @app.command("off")
    def turn_off(project: str | None = project_option) -> None:
        """Turn policy learning off: no trajectory, no training, no steer."""
        _update(project, {"enabled": False})
        console.print("[green]policy off[/green]")

    @app.command("set")
    def set_field(
        field: str = typer.Argument(..., help="enabled | log | train | steer | confidence_threshold | train_every | min_rows"),
        value: str = typer.Argument(..., help="on/off, an integer, or a number"),
        project: str | None = project_option,
    ) -> None:
        """Set one policy field. ``steer on`` is refused while the gate is closed."""
        from navin.policy.settings import BOOL_FIELDS, FLOAT_FIELDS, INT_FIELDS

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
        limit: int = typer.Option(20, "--limit", "-n", help="Number of steps"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Tail of the trajectories (.navin/policy/trajectories.jsonl): state, action, observation, reward."""
        from navin.policy.journal import recent_steps

        rows = recent_steps(_workspace(project), limit=limit)
        if as_json:
            console.print_json(json.dumps(rows, ensure_ascii=False))
            return
        if not rows:
            console.print("[dim]No eval trajectory yet. navin agi policy train[/dim]")
            return
        table = Table(title="Trajectories (most recent first)")
        table.add_column("Case")
        table.add_column("Intent")
        table.add_column("After")
        table.add_column("Action")
        table.add_column("Obs")
        table.add_column("Reward", justify="right")
        for row in rows:
            reward = float(row.get("reward") or 0.0)
            tone = "green" if reward > 0 else "red"
            table.add_row(
                escape(str(row.get("case"))),
                escape(str(row.get("intent"))),
                escape(" > ".join(row.get("prev") or []) or "start"),
                escape(str(row.get("action"))),
                escape(str(row.get("obs"))),
                f"[{tone}]{reward:.0f}[/{tone}]",
            )
        console.print(table)

    @app.command("train")
    def train_now(project: str | None = project_option) -> None:
        """Human: run the battery and train adapter N+1 now, in a child process (radar must be up)."""
        result = _action(project, "train")["result"]
        tone = _tone(result.get("status"))
        if result.get("status") != "trained":
            console.print(f"[{tone}]{escape(str(result.get('status')))}[/{tone}] {escape(str(result.get('reason') or ''))}")
            return
        verdict = result.get("verdict_vs_active") or result.get("verdict_vs_baseline") or {}
        console.print(
            f"[{tone}]trained[/{tone}] adapter {result.get('checkpoint')} "
            f"accuracy {_pct((result.get('baseline') or {}).get('accuracy'))} -> {_pct((result.get('metrics') or {}).get('accuracy'))} "
            f"vs {'N' if result.get('verdict_vs_active') else 'baseline'}: [{_tone(verdict.get('overall'))}]{escape(str(verdict.get('overall')))}[/{_tone(verdict.get('overall'))}] "
            f"[dim]{escape(_suites(verdict))}[/dim] "
            f"{'activated' if result.get('activated') else 'not activated: ' + escape(str(result.get('reason') or ''))}"
        )
        episodes = result.get("episodes") or []
        if episodes:
            passed = sum(1 for e in episodes if e.get("passed"))
            console.print(f"[dim]Battery: {passed}/{len(episodes)} episodes passed in {result.get('duration_ms')} ms ({result.get('process', 'child')} process)[/dim]")

    @app.command("run")
    def run(project: str | None = project_option) -> None:
        """Run the training job now if it is due (what the runner does after enough turns)."""
        result = _action(project, "run", actor="auto")["result"]
        console.print_json(json.dumps(result, ensure_ascii=False))

    @app.command("exam")
    def exam(project: str | None = project_option) -> None:
        """Re-score the active adapter on the same frozen set (never a new one)."""
        result = _action(project, "exam", actor="human")["result"]
        if result.get("status") != "scored":
            console.print(f"[yellow]{escape(str(result.get('status')))}[/yellow] {escape(str(result.get('reason') or ''))}")
            return
        verdict = result.get("verdict_vs_baseline") or {}
        tone = _tone(verdict.get("overall"))
        console.print(
            f"adapter {result.get('checkpoint')} on {escape(str(result.get('heldout_version')))}: "
            f"accuracy {_pct(result['baseline'].get('accuracy'))} -> {_pct(result['metrics'].get('accuracy'))} "
            f"[{tone}]{escape(str(verdict.get('overall')))}[/{tone}] [dim]{escape(_suites(verdict))}[/dim]"
        )

    @app.command("ab")
    def ab(project: str | None = project_option) -> None:
        """Offline A/B of the active adapter on the frozen set (a gate for steer)."""
        result = _action(project, "ab", actor="human")["result"]
        if result.get("status") != "scored":
            console.print(f"[yellow]{escape(str(result.get('status')))}[/yellow] {escape(str(result.get('reason') or ''))}")
            return
        tone = _tone(result.get("verdict"))
        console.print(
            f"[{tone}]{escape(str(result.get('verdict')))}[/{tone}] {result.get('n')} held-out steps, "
            f"{result.get('flagged')} confident suggestions, precision {_pct(result.get('precision'))}, "
            f"coverage {_pct(result.get('coverage'))}, no-steer baseline {_pct(result.get('baseline_accuracy'))}"
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
        """Every adapter on disk with its verdicts; the active and the previous one."""
        state = _state(_workspace(project))
        if as_json:
            console.print_json(json.dumps(state["checkpoints"], ensure_ascii=False))
            return
        if not state["checkpoints"]:
            console.print("[dim]No adapter yet.[/dim]")
            return
        table = Table(title="Adapters")
        table.add_column("#", justify="right")
        table.add_column("Trained")
        table.add_column("Rows", justify="right")
        table.add_column("Held-out")
        table.add_column("Accuracy", justify="right")
        table.add_column("vs baseline")
        table.add_column("vs N")
        table.add_column("Serving")
        for item in state["checkpoints"]:
            metrics = item.get("metrics") or {}
            vb = (item.get("verdict_vs_baseline") or {}).get("overall")
            va = (item.get("verdict_vs_active") or {}).get("overall")
            table.add_row(
                str(item["number"]),
                escape(str(item.get("trained_at") or ""))[:19],
                str(item.get("rows")),
                escape(str(item.get("heldout_version") or "")),
                _pct(metrics.get("accuracy")),
                f"[{_tone(vb)}]{escape(str(vb))}[/{_tone(vb)}]",
                f"[{_tone(va)}]{escape(str(va))}[/{_tone(va)}]" if va else "-",
                ("active" + (" (forced)" if item.get("forced") else "")) if item.get("active") else ("previous" if item.get("previous") else ""),
            )
        console.print(table)

    @app.command("scoreboard")
    def scoreboard(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Every exam line: reference -> adapter, verdict up / flat / down, per suite."""
        state = _state(_workspace(project))
        if as_json:
            console.print_json(json.dumps(state["scoreboard"], ensure_ascii=False))
            return
        if not state["scoreboard"]:
            console.print("[dim]No exam yet.[/dim]")
            return
        for entry in state["scoreboard"]:
            verdict = entry.get("verdict_vs_active") or entry.get("verdict_vs_baseline") or {}
            tone = _tone(verdict.get("overall"))
            console.print(
                f"[dim]{escape(str(entry.get('ts')))}[/dim] adapter {entry.get('checkpoint')} "
                f"{_pct(entry.get('baseline_accuracy'))} -> {_pct(entry.get('accuracy'))} "
                f"vs {escape(str(entry.get('reference')))}: [{tone}]{escape(str(verdict.get('overall')))}[/{tone}] "
                f"[dim]{escape(_suites(verdict))}[/dim]"
                + (" (exam)" if entry.get("exam") else "")
                + (" active" if entry.get("activated") else "")
            )

    @app.command("rollback")
    def rollback(project: str | None = project_option) -> None:
        """Human: back to adapter N-1 (the newer one stays on disk as evidence)."""
        result = _action(project, "rollback")["result"]
        console.print_json(json.dumps(result, ensure_ascii=False))

    @app.command("force")
    def force(
        number: int | None = typer.Option(None, "--number", "-n", help="Adapter to activate (default: the newest inactive one)"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
        project: str | None = project_option,
    ) -> None:
        """Human: activate a flat adapter on purpose (never a down one). Traced, reversible."""
        if not yes and not typer.confirm("Activate an adapter that did not beat N? This is traced and reversible."):
            raise typer.Exit(1)
        result = _action(project, "force", number=number)["result"]
        tone = _tone(result.get("status"))
        console.print(f"[{tone}]{escape(str(result.get('status')))}[/{tone}] {escape(str(result.get('reason') or ''))} adapter {result.get('checkpoint', '-')}")

    @app.command("publish")
    def publish(
        note: str = typer.Option("", "--note", help="Short note stored with the adapter"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
        project: str | None = project_option,
    ) -> None:
        """Human: copy the active adapter to ~/.navin/policy/published for other projects to adopt."""
        if not yes and not typer.confirm("Publish the active adapter outside this project?"):
            raise typer.Exit(1)
        result = _action(project, "publish", name=note)["result"]
        tone = _tone(result.get("status"))
        console.print(f"[{tone}]{escape(str(result.get('status')))}[/{tone}] {escape(str(result.get('name') or result.get('reason') or ''))}")

    @app.command("published")
    def published(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Adapters published on this machine (the list is machine-wide, --project is accepted for symmetry)."""
        from navin.policy.publish import read_published

        rows = read_published()
        if as_json:
            console.print_json(json.dumps(rows, ensure_ascii=False))
            return
        if not rows:
            console.print("[dim]Nothing published.[/dim]")
            return
        table = Table(title="Published adapters")
        table.add_column("Name")
        table.add_column("Published")
        table.add_column("From")
        table.add_column("Accuracy", justify="right")
        table.add_column("Note")
        for item in rows:
            table.add_row(
                escape(str(item.get("name"))),
                escape(str(item.get("published_at") or ""))[:19],
                escape(str(item.get("from") or "")),
                _pct((item.get("metrics") or {}).get("accuracy")),
                escape(str(item.get("note") or "")),
            )
        console.print(table)

    @app.command("adopt")
    def adopt(
        name: str = typer.Argument(..., help="Published adapter name (navin agi policy published)"),
        project: str | None = project_option,
    ) -> None:
        """Human: import a published adapter here as a new checkpoint; it serves only if it passes the exam."""
        result = _action(project, "adopt", name=name)["result"]
        tone = _tone(result.get("status"))
        console.print(
            f"[{tone}]{escape(str(result.get('status')))}[/{tone}] adapter {result.get('checkpoint', '-')} "
            f"{'activated' if result.get('activated') else 'not activated'} {escape(str(result.get('reason') or ''))}"
        )

    @app.command("unpublish")
    def unpublish(
        name: str = typer.Argument(..., help="Published adapter name"),
        project: str | None = project_option,
    ) -> None:
        """Human: remove one published adapter from this machine."""
        result = _action(project, "unpublish", name=name)["result"]
        console.print_json(json.dumps(result, ensure_ascii=False))

    @app.command("journal")
    def journal(
        limit: int = typer.Option(30, "--limit", "-n", help="Number of lines"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Tail of the policy journal (eval_run / trained / activated / rollback / steer...)."""
        from navin.policy.journal import read_journal

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
