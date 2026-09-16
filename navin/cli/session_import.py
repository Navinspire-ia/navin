"""``navin sessions`` sub-app: import sessions from external coding agents."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from navin.session.import_sessions import (
    SourceImportStats,
    SourceReport,
)


def _stat_from_report(report: SourceReport) -> SourceImportStats:
    return SourceImportStats(
        name=report.name,
        label=report.label,
        status=report.status,
        discovered=len(report.sessions),
        note=report.note,
    )


def create_sessions_app(console: Console) -> typer.Typer:
    sessions_app = typer.Typer(help="Manage chat sessions")

    @sessions_app.command("import")
    def import_cmd(
        source: str = typer.Option(
            "auto",
            "--source",
            "-s",
            help="auto, claude-code, codex, opencode, oh-my-pi or cursor",
        ),
        workspace: Optional[Path] = typer.Option(
            None,
            "--workspace",
            "-w",
            help="Target workspace (default: the current workspace)",
        ),
        limit: int = typer.Option(
            0,
            "--limit",
            "-n",
            help="Max sessions imported per source (0 = all)",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Report what would be imported without writing anything",
        ),
        overwrite: bool = typer.Option(
            False,
            "--overwrite",
            help="Re-import sessions that already exist in the workspace",
        ),
        root: list[str] = typer.Option(
            [],
            "--root",
            "-r",
            help=(
                "Extra data root for a source, as SOURCE=PATH "
                "(repeatable; e.g. --root cursor=/mnt/win/Cursor/User/"
                "workspaceStorage). Also saved for future runs."
            ),
        ),
    ) -> None:
        """Recover chat sessions from Claude Code, Codex, OpenCode, oh-my-pi and Cursor."""
        from navin.config.loader import load_config
        from navin.session import import_sessions

        try:
            names = import_sessions.resolve_sources(source)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc

        extra_roots: dict[str, list[Path]] = {}
        for entry in root:
            if "=" not in entry:
                console.print(
                    f"[red]--root expects SOURCE=PATH, got: {entry}[/red]"
                )
                raise typer.Exit(code=2)
            src, _, value = entry.partition("=")
            src = src.strip()
            try:
                import_sessions.save_extra_root(src, value)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=2) from exc
            extra_roots.setdefault(src, []).append(
                Path(value).expanduser()
            )
            console.print(
                f"[green]Saved root for {src}:[/green] {value}"
            )

        target = (workspace or load_config().workspace_path).expanduser().resolve()
        reports = import_sessions.discover(names, extra_roots or None)

        if dry_run:
            console.print(f"[bold]Dry run - target workspace:[/bold] {target}")
        else:
            console.print(f"[bold]Importing into workspace:[/bold] {target}")

        stats = (
            [_stat_from_report(report) for report in reports]
            if dry_run
            else import_sessions.import_to_workspace(
                reports, target, overwrite=overwrite, limit=limit
            )
        )

        for stat in stats:
            if stat.status in {"missing", "unsupported"}:
                console.print(f"- {stat.label}: {stat.note}")
                continue
            prefix = "would import" if dry_run else "imported"
            count = stat.discovered if dry_run else stat.imported
            line = f"- {stat.label}: {prefix} {count}"
            if stat.skipped_existing:
                line += f" (skipped {stat.skipped_existing} already present)"
            if not stat.discovered:
                line += f" ({stat.note or 'no sessions found'})"
            console.print(line)

        if not dry_run:
            total = sum(stat.imported for stat in stats)
            console.print(
                f"[green]Done: {total} session(s) imported into {target}[/green]"
            )

    return sessions_app
