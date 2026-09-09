# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``navin agi transfer``: the transfer protocol (S5) from the terminal.

Same view as the AGI panel, per project (``.navin/transfer.json``): the
master flag (refused while S2, S3.3 and S4.3 are not up), the prerequisites,
the secret suites (counts and lock only, never an item), the campaign per
family, the safety dossier, the kill drill, and the claim the protocol
computes: ``forbidden`` or ``discussable``. Nothing here writes a stronger
word; that is a human decision made elsewhere, with the protocol cited.
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
    if value is True or value in ("pass", "green", "frozen", "scored", "drilled", "discussable"):
        return "green"
    if value is False or value in ("fail", "collapse", "void", "holes", "tampered", "error", "refused", "forbidden"):
        return "red"
    return "yellow"


def _pct(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{round(value * 100)}%"
    return "-"


def create_transfer_app(*, console: Console) -> typer.Typer:
    app = typer.Typer(
        help="Transfer protocol: a hidden exam plus a shutdown dossier, not a mode. Unlocks by itself once skills evolution, the world model and the policy have passed their exams. Claim stays forbidden until both proofs pass.",
        no_args_is_help=True,
    )

    project_option = typer.Option(None, "--project", "-p", help="Project folder (default: current directory)")
    json_option = typer.Option(False, "--json", help="Print JSON instead of a table")

    def _state(workspace: Path) -> dict[str, Any]:
        from navin.transfer.state import transfer_state

        return transfer_state(workspace)

    def _action(project: str | None, action: str, **kwargs: Any) -> dict[str, Any]:
        from navin.transfer.state import TransferActionError, transfer_action

        try:
            return transfer_action(_workspace(project), action, **kwargs)
        except TransferActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    def _update(project: str | None, fields: dict[str, Any]) -> dict[str, Any]:
        from navin.transfer.state import TransferActionError, transfer_update

        try:
            return transfer_update(_workspace(project), fields)
        except TransferActionError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

    def _print_result(payload: dict[str, Any], as_json: bool) -> None:
        if as_json:
            console.print_json(json.dumps(payload, ensure_ascii=False, default=str))
            return
        result = payload.get("result") or {}
        status = result.get("status")
        line = f"[{_tone(status)}]{escape(str(status))}[/{_tone(status)}]"
        for key in ("verdict", "stopped_at", "version", "reason"):
            if result.get(key) is not None:
                line += f"  {key}: {escape(str(result[key]))}"
        console.print(line)
        claim = (payload.get("state") or {}).get("claim") or {}
        if claim:
            console.print(
                f"transfer: [{_tone(claim.get('transfer'))}]{claim.get('transfer')}[/{_tone(claim.get('transfer'))}]  "
                f"safety: [{_tone(claim.get('safety'))}]{claim.get('safety')}[/{_tone(claim.get('safety'))}]  "
                f"claim: [{_tone(claim.get('status'))}]{claim.get('status')}[/{_tone(claim.get('status'))}]"
            )

    @app.command("status")
    def status(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Flag, prerequisites, suites (counts), campaign per family, safety, claim."""
        workspace = _workspace(project)
        state = _state(workspace)
        if as_json:
            console.print_json(json.dumps(state, ensure_ascii=False, default=str))
            return
        table = Table(title=f"Transfer protocol {state['protocol']['version']} - {workspace}")
        table.add_column("Item")
        table.add_column("Value")
        table.add_column("Meaning")
        table.add_row("transfer", f"[{_tone(state['enabled'])}]{'on' if state['enabled'] else 'off'}[/{_tone(state['enabled'])}]", "master flag; off = no secret suite drawn, no dossier, no claim")
        gates = state["prereqs"]
        table.add_row("prerequisites", f"[{_tone(gates['ok'])}]{'met' if gates['ok'] else 'missing'}[/{_tone(gates['ok'])}]", "; ".join(gates["reasons"]) or "S2 finished, S3.3 up, S4.3 up")
        suites = state.get("suites")
        if suites:
            counts = " ".join(f"{fam}:{n}" for fam, n in sorted(suites["families"].items())) or "no items"
            placement = "" if suites["outside"] else f" [red]{escape(str(suites['placement_error']))}[/red]"
            lock = "tampered" if suites.get("tampered") else ("frozen" if suites["frozen"] else "not frozen")
            table.add_row("suites", f"[{_tone(lock)}]{lock}[/{_tone(lock)}] {suites.get('version') or ''}", f"{escape(suites['dir'])}: {counts}{placement}")
        campaign = state.get("campaign")
        if campaign:
            fams = " ".join(
                f"{fam}:{_pct(d.get('pass_rate'))}/{_pct(d.get('bar'))}={d.get('verdict')}" for fam, d in campaign["families"].items()
            )
            table.add_row("campaign", f"[{_tone(campaign['verdict'])}]{campaign['verdict']}[/{_tone(campaign['verdict'])}] {campaign['id']}", fams + (f"  stopped at {campaign['stopped_at']}" if campaign.get("stopped_at") else ""))
        safety = state.get("safety")
        if safety:
            mode = "steer on" if (safety.get("mode") or {}).get("steer_on") else "steer off"
            drill = safety.get("drill") or {}
            table.add_row(
                "safety",
                f"[{_tone(safety.get('ok'))}]{'green' if safety.get('ok') else 'holes'}[/{_tone(safety.get('ok'))}] ({mode})",
                ("; ".join(safety.get("holes") or []) or f"{len(safety.get('checks') or [])} checks hold")
                + (f"; kill drill {'ok' if drill.get('ok') else 'holes'} ({drill.get('ts')})" if drill else "; kill switch not drilled"),
            )
        claim = state["claim"]
        table.add_row(
            "claim",
            f"[{_tone(claim['status'])}]{claim['status']}[/{_tone(claim['status'])}]",
            f"transfer {claim['transfer']}, safety {claim['safety']}, drill {claim['drill']}"
            + ("; " + "; ".join(claim["reasons"]) if claim["reasons"] else ""),
        )
        console.print(table)

    @app.command("on")
    def on(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Turn the protocol on for this project (refused while S2, S3.3, S4.3 are not up)."""
        state = _update(project, {"enabled": True})
        if as_json:
            console.print_json(json.dumps(state, ensure_ascii=False, default=str))
        else:
            console.print("[green]transfer protocol on[/green]: no campaign runs by itself; freeze the suites, then start one.")

    @app.command("off")
    def off(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Turn the protocol off: no suite drawn, no dossier, no claim."""
        state = _update(project, {"enabled": False})
        if as_json:
            console.print_json(json.dumps(state, ensure_ascii=False, default=str))
        else:
            console.print("[yellow]transfer protocol off[/yellow]")

    @app.command("set")
    def set_field(
        field: str = typer.Argument(..., help="enabled | suites_dir"),
        value: str = typer.Argument(..., help="on/off, or a folder outside every repository"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Write one field: the flag, or the secret suites folder."""
        key = field.strip().lower()
        payload: dict[str, Any]
        if key == "enabled":
            payload = {key: _parse_switch(value)}
        elif key == "suites_dir":
            payload = {key: value.strip() or None}
        else:
            console.print(f"[red]unknown field: {escape(field)}[/red]")
            raise typer.Exit(2)
        state = _update(project, payload)
        if as_json:
            console.print_json(json.dumps(state, ensure_ascii=False, default=str))
        else:
            console.print(f"{key} = {escape(str(payload[key]))}")

    @app.command("prereqs")
    def prereqs_cmd(project: str | None = project_option, as_json: bool = json_option) -> None:
        """The hard prerequisites: S2 finished, S3.3 up, S4.3 up."""
        from navin.transfer.prereqs import prereqs

        gates = prereqs(_workspace(project)).as_dict()
        if as_json:
            console.print_json(json.dumps(gates, ensure_ascii=False))
            return
        for check in gates["checks"]:
            console.print(f"[{_tone(check['ok'])}]{check['id']}[/{_tone(check['ok'])}]  {escape(check['detail'])}")

    @app.command("protocol")
    def protocol_cmd(as_json: bool = json_option) -> None:
        """The public rules: families, junior bar, collapse, budget, replay."""
        from navin.transfer.protocol import protocol_summary

        summary = protocol_summary()
        if as_json:
            console.print_json(json.dumps(summary, ensure_ascii=False))
            return
        console.print(f"protocol {summary['version']}  junior bar {_pct(summary['junior_bar'])}  collapse {_pct(summary['collapse'])}  min items/family {summary['min_items_per_family']}")
        for fam in summary["families"]:
            console.print(f"  {fam['id']:9} {fam['title']}: {fam['about']}")
        budget = summary["budget"]
        console.print(f"budget per item: {budget['max_tool_calls']} tool calls, {budget['timeout_s']:.0f}s, {budget['max_tokens']} tokens")
        for rule in summary["rules"]:
            console.print(f"  - {rule}")

    @app.command("freeze")
    def freeze_cmd(
        author: list[str] = typer.Option(..., "--author", help="Who wrote items (repeatable)"),
        attester: str = typer.Option(..., "--attester", help="Who reads the score (not an author)"),
        trainer: list[str] = typer.Option([], "--trainer", help="Who trains the policy (repeatable, must not be an author)"),
        baseline: list[str] = typer.Option([], "--junior", help="Measured junior pass rate, family=rate (repeatable)"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Freeze the secret suites: hash per family, authors, attester, junior baseline."""
        junior: dict[str, float] = {}
        for entry in baseline:
            fam, _, rate = entry.partition("=")
            try:
                junior[fam.strip()] = float(rate)
            except ValueError as exc:
                raise typer.BadParameter(f"--junior expects family=rate, got {entry}") from exc
        payload = _action(project, "freeze", actor="human", authors=author, attester=attester, trainers=trainer, junior_baseline=junior)
        _print_result(payload, as_json)

    @app.command("verify")
    def verify_cmd(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Re-hash the suites against their lock."""
        _print_result(_action(project, "verify", actor="human"), as_json)

    @app.command("campaign")
    def campaign_cmd(
        replay: str | None = typer.Option(None, "--replay", help="Replay this campaign id (same items, same seeds)"),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Run the secret campaign in a child process (a human action; hours)."""
        if replay:
            payload = _action(project, "replay", actor="human", name=replay)
        else:
            payload = _action(project, "campaign", actor="human")
        _print_result(payload, as_json)

    @app.command("safety")
    def safety_cmd(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Run the safety dossier (deny, sandbox, approvals, heartbeat, cron, steer, kill switches)."""
        payload = _action(project, "safety", actor="human")
        if as_json:
            console.print_json(json.dumps(payload, ensure_ascii=False, default=str))
            return
        result = payload["result"]
        for check in result.get("checks") or []:
            console.print(f"[{_tone(check['ok'])}]{check['section']}/{check['id']}[/{_tone(check['ok'])}]  {escape(check['detail'])}")
        mode = "steer on" if (result.get("mode") or {}).get("steer_on") else "steer off"
        console.print(f"dossier: [{_tone(result['status'])}]{result['status']}[/{_tone(result['status'])}] ({mode})")

    @app.command("kill-drill")
    def kill_drill_cmd(project: str | None = project_option, as_json: bool = json_option) -> None:
        """Exercise the kill switches (key, process, network, exec). Relaunch is manual."""
        payload = _action(project, "kill_drill", actor="human")
        if as_json:
            console.print_json(json.dumps(payload, ensure_ascii=False, default=str))
            return
        result = payload["result"]
        for check in result.get("checks") or []:
            console.print(f"[{_tone(check['ok'])}]{check['section']}/{check['id']}[/{_tone(check['ok'])}]  {escape(check['detail'])}")
        console.print(f"drill: [{_tone(result['status'])}]{result['status']}[/{_tone(result['status'])}]  restart: {result.get('restart')}")

    @app.command("journal")
    def journal_cmd(
        limit: int = typer.Option(30, "--limit", "-n", min=1, max=500),
        project: str | None = project_option,
        as_json: bool = json_option,
    ) -> None:
        """Last events: enabled, frozen, campaign_started, campaign_scored, safety_case, kill_drill."""
        from navin.transfer.journal import read_journal

        rows = read_journal(_workspace(project), limit=limit)
        if as_json:
            console.print_json(json.dumps(rows, ensure_ascii=False))
            return
        for row in rows:
            extra = " ".join(f"{k}={v}" for k, v in row.items() if k not in ("ts", "event"))
            console.print(f"{row.get('ts', '')}  {row.get('event', '')}  {escape(extra)}")

    return app
