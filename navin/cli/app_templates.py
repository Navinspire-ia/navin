"""Typer commands: navin app list|info|create|install|publish."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from navin.config.paths import get_workspace_path


def create_app_templates_app(*, console: Console) -> typer.Typer:
    app_cmd = typer.Typer(help="Create and install Navin app templates.")

    @app_cmd.command("list")
    def app_list(
        kind: str | None = typer.Option(None, "--kind", help="business or ai-product"),
        workspace: str | None = typer.Option(None, "--workspace", "-w"),
    ) -> None:
        from navin.templates.apps.catalog import list_app_templates
        from navin.templates.apps.install import is_installed, is_publishable, local_cache_dir

        root = Path(workspace).expanduser().resolve() if workspace else get_workspace_path()
        rows = list_app_templates(kind=kind)
        table = Table(title=f"App templates ({len(rows)})")
        table.add_column("Slug")
        table.add_column("Name")
        table.add_column("Kind")
        table.add_column("Audit")
        table.add_column("Status")
        table.add_column("Local")
        table.add_column("Installed")
        for row in rows:
            table.add_row(
                row["slug"],
                escape(row["name"]),
                row["kind"],
                row["audit_status"],
                row["status"],
                "yes" if local_cache_dir(row["slug"]).is_dir() else "no",
                "yes" if is_installed(root, row["slug"]) else "no",
            )
        console.print(table)
        publishable = [row["slug"] for row in rows if is_publishable(row)]
        if publishable:
            console.print(f"[green]Ready for AWS:[/green] {', '.join(publishable)}")
        else:
            console.print(
                "[yellow]AWS is blocked.[/yellow] Audit, restyle and test each "
                "template before `navin app publish <slug>`."
            )

    @app_cmd.command("info")
    def app_info(slug: str = typer.Argument(..., help="Template slug, e.g. crm")) -> None:
        from navin.templates.apps.catalog import get_app_template
        from navin.templates.apps.install import template_payload

        row = get_app_template(slug)
        if row is None:
            console.print(f"[red]unknown template:[/red] {escape(slug)}")
            raise typer.Exit(1)
        payload = template_payload(row)
        console.print(f"[bold]{escape(payload['name'])}[/bold]  ({payload['slug']})")
        console.print(escape(payload["description"]))
        console.print(f"kind        {payload['kind']}")
        console.print(f"category    {payload['category']}")
        console.print(f"license     {payload['license']}")
        console.print(f"stack       {', '.join(payload['stack'])}")
        console.print(f"source      {payload['source_github']}")
        console.print(f"audit       {payload['audit_status']}")
        console.print(f"status      {payload['status']}")
        console.print(f"local cache {payload['local_cache']}")
        console.print(f"can publish {payload['can_publish']}")
        console.print(f"aws key     {payload['aws_key']}")
        console.print("agents      " + ", ".join(payload["domain_agents"]))
        if not payload["can_publish"]:
            console.print(
                "[yellow]Not on AWS.[/yellow] Use locally with "
                f"`navin app create {payload['slug']} ./mon-app` or "
                f"`navin app install {payload['slug']}`."
            )

    @app_cmd.command("create")
    def app_create(
        slug: str = typer.Argument(..., help="Template slug, e.g. crm"),
        dest: str = typer.Argument(..., help="New project folder, e.g. ./mon-crm"),
        no_start: bool = typer.Option(False, "--no-start", help="Write files only, do not start"),
    ) -> None:
        from navin.templates.apps.install import AppTemplateError, create_app

        try:
            result = create_app(slug, dest, start=not no_start)
        except AppTemplateError as exc:
            console.print(f"[red]{escape(exc.message)}[/red]")
            raise typer.Exit(1) from exc
        console.print(
            f"[green]Created[/green] {escape(result['slug'])} in {escape(result['dest'])}"
        )
        source = str(result.get("source") or "")
        if source == "aws":
            console.print(f"Downloaded from S3: {escape(str(result.get('aws_url') or ''))}")
        elif result["copied_source"]:
            console.print("Copied local study cache (gitignored templates_apps/).")
        else:
            console.print(
                "[yellow]No S3 package and no local cache.[/yellow] "
                "Wrote the Navin plugin overlay only. Run make aws-upload-temp."
            )
        console.print(f"Plugin: {escape(result['plugin_dir'])}")
        started = result.get("started") or []
        if started:
            console.print("Started: " + ", ".join(str(item) for item in started))
        preview = result.get("preview_url")
        if preview:
            console.print(f"Preview: {escape(str(preview))}")

    @app_cmd.command("install")
    def app_install(
        slug: str = typer.Argument(..., help="Template slug, e.g. crm"),
        workspace: str | None = typer.Option(
            None, "--workspace", "-w", help="Workspace to install into"
        ),
        no_start: bool = typer.Option(False, "--no-start", help="Write overlay only, do not start"),
    ) -> None:
        from navin.templates.apps.install import AppTemplateError, install_app

        root = Path(workspace).expanduser().resolve() if workspace else get_workspace_path()
        try:
            result = install_app(slug, root, start=not no_start)
        except AppTemplateError as exc:
            console.print(f"[red]{escape(exc.message)}[/red]")
            raise typer.Exit(1) from exc
        verb = "Already installed" if result["already_installed"] else "Installed"
        console.print(f"[green]{verb}[/green] {escape(result['slug'])}")
        console.print(f"Workspace: {escape(result['workspace'])}")
        console.print(f"Plugin: {escape(result['plugin_dir'])}")
        started = result.get("started") or []
        if started:
            console.print("Started: " + ", ".join(str(item) for item in started))
        preview = result.get("preview_url")
        if preview:
            console.print(f"Preview: {escape(str(preview))}")
        console.print(
            f"Audit {result['audit_status']} / {result['status']} - AWS upload is blocked."
        )

    @app_cmd.command("publish")
    def app_publish(slug: str = typer.Argument(..., help="Template slug")) -> None:
        from navin.templates.apps.install import AppTemplateError, publish_to_aws

        try:
            publish_to_aws(slug)
        except AppTemplateError as exc:
            console.print(f"[red]{escape(exc.message)}[/red]")
            raise typer.Exit(1) from exc

    return app_cmd
