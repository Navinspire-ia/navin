# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``navin infra``: the infrastructure orchestrator from a terminal.

The same engine as the agent's ``infra`` tool and the Desktop Ops panel:
status, discovery, the operation catalog, policy checks, the resource graph,
runs, the audit log, and a policy-gated ``run`` that asks at the terminal.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

_RISK_STYLE = {"read": "dim", "plan": "cyan", "change": "yellow", "critical": "bold red"}


def create_infra_app(*, console: Console) -> typer.Typer:
    infra_app = typer.Typer(help="Infrastructure orchestrator: discover, catalog, check, plan, run, verify, graph, audit.")

    def engine(workspace: str | None):
        from navin.infra.engine import InfraEngine

        return InfraEngine(Path(workspace or ".").expanduser().resolve())

    def emit(payload, as_json: bool) -> bool:
        if as_json:
            console.print_json(json.dumps(payload, default=str))
        return as_json

    workspace_opt = typer.Option(None, "--workspace", "-w", help="Project folder (default: current folder)")
    json_opt = typer.Option(False, "--json", help="Print JSON")

    @infra_app.command("status")
    def status(workspace: str | None = workspace_opt, probe: bool = typer.Option(False, "--probe", help="Read tool versions"), as_json: bool = json_opt):
        """Tools, contexts, autonomy level, warnings and catalog counts."""
        state = engine(workspace).state(probe=probe)
        if emit(state, as_json):
            return
        policy = state["policy"]
        console.print(f"[bold]Project[/bold] {escape(state['project'])}  [bold]level[/bold] {policy['level']}")
        tools = [tool for tool in state["tools"] if tool["available"]]
        console.print("[bold]Tools[/bold] " + ", ".join(f"{t['name']}{(' ' + escape(t['version'])) if t['version'] else ''}" for t in tools))
        missing = [tool["name"] for tool in state["tools"] if not tool["available"]]
        if missing:
            console.print(f"[dim]missing: {', '.join(missing)}[/dim]")
        contexts = state["contexts"]
        for name, value in (("kubernetes", contexts["kubernetes"]["current"]), ("aws", contexts["aws"]["profile"]),
                            ("gcp", contexts["gcp"]["project"]), ("terraform", contexts["terraform"]["workspace"])):
            if value:
                badge = " [bold red]production[/bold red]" if contexts[name]["production"] else ""
                console.print(f"  {name}: {escape(value)}{badge}")
        counts = state["catalog"]
        console.print(f"[bold]Catalog[/bold] known {counts['known']}  available {counts['available']}  authorized {counts['authorized']}  tested {counts['tested']}")
        for row in counts["providers"]:
            console.print(f"  {row['provider']}: {row['count']} ({escape(row['source'])}, {escape(row['version'])})")
        for warning in state["warnings"]:
            console.print(f"[yellow]! {escape(warning)}[/yellow]")

    @infra_app.command("discover")
    def discover(provider: str = typer.Argument("all", help="aws, gcp, azure, kubernetes, openshift, docker, podman, terraform, opentofu, helm, ansible, argocd, flux, github, gitlab, openstack, vsphere, proxmox, pulumi, containerd or all"),
                 deep: bool = typer.Option(False, "--deep", help="Cloud Control types, Google API Discovery, every az group"),
                 context: str = typer.Option("", "--context", help="Kube context or AWS profile"),
                 workdir: str = typer.Option("", "--workdir", help="Terraform module folder"),
                 workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Build the operation registry from what this machine has."""
        with console.status(f"discovering {provider}..."):
            result = engine(workspace).discover(provider, deep=deep, context=context, workdir=workdir)
        if emit(result, as_json):
            return
        for row in result["results"]:
            state = "[green]ok[/green]" if row["count"] else ("[dim]not installed[/dim]" if not row["available"] else "[yellow]nothing[/yellow]")
            console.print(f"{row['provider']:<12} {state} {row['count']:>6} ops  {escape(row['version'])}  ({row['duration_ms']} ms)")
            for error in row["errors"][:3]:
                console.print(f"   [yellow]{escape(error)}[/yellow]")

    @infra_app.command("catalog")
    def catalog(query: str = typer.Argument("", help="Search words"), provider: str = typer.Option("", "--provider", "-p"),
                risk: str = typer.Option("", "--risk"), tier: str = typer.Option("", "--tier", help="known, available, authorized or tested"),
                limit: int = typer.Option(30, "--limit", "-n"), workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Search the Universal Command & API Registry."""
        found = engine(workspace).catalog(query, provider=provider, risk=risk, tier=tier, limit=limit)
        if emit(found, as_json):
            return
        table = Table(title=f"{found['total']} operations", show_lines=False)
        for column in ("command", "risk", "tiers", "id"):
            table.add_column(column)
        for item in found["items"]:
            tiers = "".join(letter if item[name] else "-" for letter, name in (("K", "known"), ("A", "available"), ("Z", "authorized"), ("T", "tested")))
            table.add_row(escape(item["command"]), f"[{_RISK_STYLE[item['risk']]}]{item['risk']}[/]", tiers, escape(item["id"]))
        console.print(table)
        console.print("[dim]tiers: K known, A available here, Z authorized by the policy, T tested here[/dim]")

    @infra_app.command("describe")
    def describe(operation: str = typer.Argument(..., help="Operation id or command line"), workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Everything about one operation."""
        record = engine(workspace).describe(operation)
        if record is None:
            console.print(f"[red]No operation matches {escape(operation)}[/red]")
            raise typer.Exit(1)
        if emit(record, as_json):
            return
        console.print(f"[bold]{escape(record['command'])}[/bold]  [{_RISK_STYLE[record['risk']]}]{record['risk']}[/]  ({escape(record['id'])})")
        for label, key in (("provider", "provider"), ("service", "service"), ("version", "version"), ("endpoint", "endpoint"), ("effects", "effects"), ("source", "source")):
            if record.get(key):
                console.print(f"  {label}: {escape(str(record[key]))}")
        for label, key in (("required", "required"), ("optional", "optional"), ("permissions", "permissions"), ("examples", "examples"),
                           ("verify", "verify"), ("rollback", "rollback"), ("compatible", "compat")):
            if record.get(key):
                console.print(f"  {label}: " + escape(", ".join(record[key][:20])))
        decision = record["decision"]
        console.print(f"  decision: {decision['action']}" + (f" ({escape('; '.join(decision['reasons']))})" if decision["reasons"] else ""))
        tiers = ", ".join(name for name in ("known", "available", "authorized", "tested") if record[name])
        console.print(f"  tiers: {tiers}")

    @infra_app.command("check")
    def check(command: str = typer.Argument(..., help="The command line to judge"), workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Risk and policy decision of a command, without running it."""
        result = engine(workspace).check(command)
        if emit(result, as_json):
            return
        decision = result["decision"] or {}
        if not result["infrastructure"]:
            console.print("not an infrastructure command: allowed")
            return
        console.print(f"[{_RISK_STYLE.get(decision.get('risk', 'change'), 'yellow')}]{decision.get('risk')}[/] -> [bold]{decision.get('action')}[/bold]")
        for reason in decision.get("reasons", []):
            console.print(f"  - {escape(reason)}")
        if decision.get("targets"):
            console.print("  targets: " + escape(", ".join(f"{k}={v}" for k, v in decision["targets"].items())))

    @infra_app.command("run")
    def run(command: str = typer.Argument(..., help="One infrastructure command (no pipes)"), yes: bool = typer.Option(False, "--yes", help="Approve without asking (not for critical operations)"),
            timeout: int = typer.Option(300, "--timeout"), workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Run one command through the broker: policy, snapshot, verification, audit."""
        eng = engine(workspace)
        prepared = eng.prepare(command)
        approved = False
        if not prepared.error and prepared.decision.action == "ask":
            console.print(f"[bold]{prepared.decision.risk}[/bold]: " + escape("; ".join(prepared.decision.reasons)))
            if yes and prepared.decision.risk != "critical":
                approved = True
            else:
                approved = typer.confirm(f"Run {command}?", default=False)
        report = eng.execute(prepared, approved=approved, timeout=timeout)
        if emit(report, as_json):
            raise typer.Exit(0 if report["status"] == "done" else 1)
        console.print(f"[bold]{report['status']}[/bold] exit={report.get('exit_code')} verified={report.get('verified')}")
        if report.get("output"):
            console.print(escape(report["output"][-4000:]))
        for item in report.get("diagnosis", []):
            console.print(f"[yellow]{escape(item['cause'])}[/yellow]: {escape(item['advice'])}")
        for step in report.get("recovery", []):
            console.print(f"  recover: {escape(step)}")
        if report.get("error"):
            console.print(f"[red]{escape(report['error'])}[/red]")
        raise typer.Exit(0 if report["status"] == "done" else 1)

    @infra_app.command("policy")
    def policy(level: str = typer.Option("", "--level", help="observe, prepare or execute"),
               scope: list[str] = typer.Option([], "--scope", help="dimension=a,b (aws_profiles, kube_contexts, namespaces, gcp_projects, azure_subscriptions)"),
               aws_profile: str | None = typer.Option(None, "--aws-profile", help="Pin an IAM Identity Center profile"),
               gcp_impersonate: str | None = typer.Option(None, "--gcp-impersonate", help="Service account to impersonate"),
               workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Show or change the project's infrastructure policy."""
        eng = engine(workspace)
        fields: dict = {}
        if level:
            fields["level"] = level
        if scope:
            fields["scopes"] = {}
            for item in scope:
                name, _, values = item.partition("=")
                fields["scopes"][name.strip()] = values
        auth = {key: value for key, value in (("aws_profile", aws_profile), ("gcp_impersonate", gcp_impersonate)) if value is not None}
        if auth:
            fields["auth"] = auth
        try:
            state = eng.set_policy(fields) if fields else eng.state()
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(2) from exc
        if emit(state["policy"], as_json):
            return
        current = state["policy"]
        console.print(f"level: [bold]{current['level']}[/bold]")
        for name, values in current["scopes"].items():
            console.print(f"  {name}: {', '.join(values) or 'any'}")
        console.print(f"  production markers: {', '.join(current['production_markers'])}")
        console.print(f"  auth: aws_profile={current['auth']['aws_profile'] or '-'} gcp_impersonate={current['auth']['gcp_impersonate'] or '-'}")

    @infra_app.command("graph")
    def graph(op: str = typer.Argument("summary", help="build, summary, impact, deps, path or find"), node: str = typer.Option("", "--node"),
              target: str = typer.Option("", "--target"), query: str = typer.Option("", "--query", "-q"),
              source: list[str] = typer.Option([], "--source", help="kubernetes, aws, terraform, docker, gcp, azure, openstack, vsphere, proxmox"),
              workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """The infrastructure resource graph and impact analysis."""
        with console.status("reading the infrastructure...") if op == "build" else contextlib.nullcontext():
            result = engine(workspace).graph(op, sources=source or None, node=node, target=target, query=query)
        if emit(result, as_json):
            return
        summary = result["summary"]
        console.print(f"{summary['nodes']} nodes, {summary['edges']} edges ({', '.join(summary['sources']) or 'not built'}) {summary['updated_at']}")
        for kind, count in summary["kinds"].items():
            console.print(f"  {kind}: {count}")
        if result.get("node"):
            console.print(f"[bold]{escape(result['node']['id'])}[/bold] impacts {len(result['impact'])} resources")
            for nid in result["impact"][:40]:
                console.print(f"  <- {escape(nid)}")
            for nid in result.get("deps", [])[:40]:
                console.print(f"  -> {escape(nid)}")
            if result.get("path"):
                console.print("  path: " + " -> ".join(escape(p) for p in result["path"]))
        elif op == "find":
            for item in result["nodes"][:50]:
                console.print(f"  {item['kind']:<18} {escape(item['id'])}")
        for error in result["errors"]:
            console.print(f"[yellow]{escape(error)}[/yellow]")

    @infra_app.command("audit")
    def audit(limit: int = typer.Option(30, "--limit", "-n"), workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Infrastructure commands run through Navin in this project."""
        items = engine(workspace).audit(limit)["items"]
        if emit(items, as_json):
            return
        if not items:
            console.print("[dim]nothing ran through the broker yet[/dim]")
        for item in items:
            mark = "[green]ok[/green]" if item.get("ok") else f"[red]{item.get('status')}[/red]"
            console.print(f"{item['ts']} {mark} [{_RISK_STYLE.get(item.get('risk', 'change'), 'yellow')}]{item.get('risk')}[/] {escape(item['command'])}")

    @infra_app.command("runs")
    def runs(run_id: str = typer.Argument("", help="One run"), workspace: str | None = workspace_opt, as_json: bool = json_opt):
        """Multi-step infrastructure runs and their steps."""
        result = engine(workspace).runs(run_id)
        if emit(result, as_json):
            return
        for item in ([result] if run_id else result["items"]):
            console.print(f"[bold]{item['id']}[/bold] {item['status']}  {escape(item['goal'])}")
            for step in item["steps"]:
                console.print(f"  {step['id']:<8} {step['status']:<15} {step.get('risk', ''):<9} {escape(step['title'])}")

    return infra_app
