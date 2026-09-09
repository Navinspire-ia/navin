# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Typer commands for inspecting and installing language servers."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table


def create_lsp_app(*, console: Console) -> typer.Typer:
    lsp_app = typer.Typer(help="Inspect and install language servers.")

    @lsp_app.command("list")
    def lsp_list(
        workspace: str | None = typer.Option(
            None, "--workspace", "-w", help="Project to resolve servers against"
        ),
    ):
        """Show every configured language server and whether it is usable."""
        from navin.lsp.manager import available_servers
        from navin.lsp.vsix import installed

        root = Path(workspace).expanduser().resolve() if workspace else Path.cwd()
        rows = available_servers(root)
        from_vsix = installed()

        table = Table(title=f"Language servers for {root}")
        table.add_column("Server")
        table.add_column("Files")
        table.add_column("Status")
        table.add_column("Source")
        for row in rows:
            ok = row["available"]
            status = "[green]ready[/green]" if ok else f"[yellow]{escape(row['reason'])}[/yellow]"
            source = "extension" if row["server"] in from_vsix else "system"
            table.add_row(
                row["server"],
                escape(" ".join(row["extensions"][:6])),
                status,
                source,
            )
        console.print(table)

    @lsp_app.command("catalog")
    def lsp_catalog():
        """List the VS Code extensions that can provide a language server."""
        from navin.lsp.vsix import catalog, installed

        entries = catalog()
        if not entries:
            console.print("[yellow]The extension catalogue is empty.[/yellow]")
            return
        have = installed()

        table = Table(title="Language servers available from VS Code extensions")
        table.add_column("Name")
        table.add_column("Extension")
        table.add_column("Files")
        table.add_column("Installed")
        for name, spec in sorted(entries.items()):
            table.add_row(
                name,
                escape(str(spec.get("marketplace", ""))),
                escape(" ".join(spec.get("lsp", {}).get("extensions", []))),
                "[green]yes[/green]" if name in have else "no",
            )
        console.print(table)
        for name, spec in sorted(entries.items()):
            if spec.get("notes"):
                console.print(f"  [dim]{name}: {escape(str(spec['notes']))}[/dim]")
        console.print("\nInstall one with [bold]navin lsp install <name>[/bold].")

    @lsp_app.command("install")
    def lsp_install(
        name: str = typer.Argument(..., help="Catalogue name, or a name you choose"),
        version: str | None = typer.Option(None, "--version", help="Extension version"),
        extension: str | None = typer.Option(
            None, "--extension", help="Open VSX id '<publisher>/<name>' for an uncatalogued extension"
        ),
        entrypoint: str | None = typer.Option(
            None, "--entrypoint", help="Path of the server inside the archive"
        ),
        suffixes: str | None = typer.Option(
            None, "--suffixes", help="File suffixes to route, comma separated (e.g. '.vue,.svelte')"
        ),
        language_id: str | None = typer.Option(
            None, "--language-id", help="LSP languageId for those suffixes"
        ),
        native: bool = typer.Option(
            False, "--native", help="The server is an executable rather than a node script"
        ),
        platform_specific: bool = typer.Option(
            False,
            "--platform-specific",
            help="The extension publishes one archive per platform (usual for a compiled server)",
        ),
    ):
        """Install a language server bundled inside a VS Code extension."""
        from navin.lsp.vsix import VsixError, install

        custom: dict | None = None
        if extension or entrypoint or suffixes:
            missing = [
                flag
                for flag, value in (
                    ("--extension", extension),
                    ("--entrypoint", entrypoint),
                    ("--suffixes", suffixes),
                )
                if not value
            ]
            if missing:
                console.print(
                    f"[red]Installing an uncatalogued extension needs {', '.join(missing)}.[/red]"
                )
                raise typer.Exit(2)
            parsed = [s.strip() for s in str(suffixes).split(",") if s.strip()]
            bad = [s for s in parsed if not s.startswith(".")]
            if bad:
                console.print(f"[red]File suffixes must start with a dot: {', '.join(bad)}[/red]")
                raise typer.Exit(2)
            lang = language_id or parsed[0].lstrip(".")
            custom = {
                "languages": [lang],
                "extensions": parsed,
                "args": [] if native else ["--stdio"],
                "root_markers": ["package.json", ".git"],
                "language_ids": dict.fromkeys(parsed, lang),
                "priority": 20,
            }

        try:
            result = install(
                name,
                version=version,
                slug=extension,
                entrypoint=entrypoint,
                runtime="native" if native else "node",
                lsp=custom,
                platform_specific=platform_specific,
            )
        except VsixError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc

        console.print(
            f"[green]Installed[/green] {escape(result['server'])} "
            f"from {escape(result['marketplace'])} {escape(result['version'])}"
        )
        console.print(f"  handles {escape(' '.join(result['extensions']))}")
        raise typer.Exit(0)

    @lsp_app.command("uninstall")
    def lsp_uninstall(
        name: str = typer.Argument(..., help="Name of the installed server"),
    ):
        """Remove a language server that was installed from an extension."""
        from navin.lsp.vsix import VsixError, uninstall

        try:
            removed = uninstall(name)
        except VsixError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc
        if not removed:
            console.print(f"[yellow]'{escape(name)}' is not installed.[/yellow]")
            raise typer.Exit(1)
        console.print(f"[green]Removed[/green] {escape(name)}")
        raise typer.Exit(0)

    return lsp_app
