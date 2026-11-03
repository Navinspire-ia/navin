# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``navin org``: the organization's model gateway (Navin SM), same actions as Settings > Organization."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table


def _run(console: Console, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    from navin.webui.organization_api import OrganizationApiError, handle_organization_action

    try:
        return handle_organization_action(action, body or {})
    except OrganizationApiError as exc:
        console.print(f"[red]{escape(exc.message)}[/red]")
        raise typer.Exit(1) from None


def _print_status(console: Console, payload: dict[str, Any]) -> None:
    if not payload.get("present"):
        console.print("No organization gateway. Import the file your IT team gave you:")
        console.print("  navin org import <file.json>")
        return
    org = payload["organization"]
    origin = "deployed by IT" if payload["source"] == "managed" else "imported"
    console.print(f"[bold]{escape(org['name'] or org['id'] or 'Organization')}[/bold] ({origin}: {escape(payload['path'])})")
    if payload.get("error"):
        console.print(f"[red]Invalid file: {escape(payload['error'])}[/red]")
        if payload.get("exclusive"):
            console.print("[red]Model calls are blocked until IT fixes the file.[/red]")
        return
    gateway = payload["gateway"]
    console.print(f"Gateway: {escape(gateway['base_url'])}")
    console.print("Only this gateway is allowed" if payload["exclusive"] else "Your own providers stay available")
    user_key = payload.get("user_key")
    if user_key is not None:
        console.print(
            f"Personal key: {escape(user_key['hint'])}" if user_key["configured"]
            else "[yellow]Personal key: not set (navin org key)[/yellow]"
        )
    table = Table(title="Models")
    table.add_column("Model")
    table.add_column("Name")
    table.add_column("")
    for model in gateway["models"]:
        marks = ["default" if model["default"] else "", "in use" if model["active"] else ""]
        table.add_row(escape(model["id"]), escape(model["label"]), ", ".join(mark for mark in marks if mark))
    console.print(table)


def create_org_app(*, console: Console) -> typer.Typer:
    org_app = typer.Typer(help="Your organization's model gateway (LiteLLM).", no_args_is_help=True)

    @org_app.command("status")
    def org_status(as_json: bool = typer.Option(False, "--json", help="Print the status as JSON")) -> None:
        """Show the organization, its gateway and its models."""
        payload = _run(console, "status")
        if as_json:
            console.print_json(json.dumps(payload))
            return
        _print_status(console, payload)

    @org_app.command("import")
    def org_import(
        file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="The organization file"),
    ) -> None:
        """Use the organization file your IT team gave you."""
        payload = _run(console, "import", {"content": file.read_text(encoding="utf-8")})
        console.print("[green]Organization file imported.[/green]")
        _print_status(console, payload)

    @org_app.command("remove")
    def org_remove() -> None:
        """Stop using the imported organization file."""
        _run(console, "remove")
        console.print("[green]Organization file removed.[/green]")

    @org_app.command("key")
    def org_key(
        from_stdin: bool = typer.Option(False, "--stdin", help="Read the key from standard input"),
        clear: bool = typer.Option(False, "--clear", help="Forget the saved key"),
    ) -> None:
        """Save the personal key your organization gave you for its gateway."""
        if clear:
            key = ""
        elif from_stdin:
            key = sys.stdin.readline().strip()
        else:
            key = typer.prompt("Personal key", hide_input=True).strip()
        if not key and not clear:
            console.print("[red]No key given.[/red]")
            raise typer.Exit(1)
        payload = _run(console, "key", {"api_key": key})
        hint = (payload.get("user_key") or {}).get("hint")
        console.print(f"[green]Key saved ({escape(hint)}).[/green]" if hint else "[green]Key removed.[/green]")

    @org_app.command("test")
    def org_test() -> None:
        """Check that the gateway answers and serves the organization's models."""
        result = _run(console, "test")
        if not result.get("ok"):
            console.print(f"[red]{escape(result.get('error') or 'The gateway check failed.')}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]The gateway answers ({result.get('served', 0)} models served to you).[/green]")
        missing = result.get("missing") or []
        if missing:
            console.print(f"[yellow]Not served to your key: {escape(', '.join(missing))}[/yellow]")

    return org_app
