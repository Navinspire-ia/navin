# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from pathlib import Path

import typer
from rich.console import Console

from navin.improvement.control import control


def create_improvement_app(*, console: Console) -> typer.Typer:
    app = typer.Typer(help="Bounded strategy improvement: Code, Career and Tenders.", no_args_is_help=True)
    module_option = typer.Option("all", "--module", "-m", help="all, code, career or tenders")
    project_option = typer.Option(None, "--project", "-p", help="Code project folder; defaults to the current folder")

    def run(action, module, project, context=""):
        try:
            console.print_json(data=control(Path(project or Path.cwd()).expanduser().resolve(), module, action, context))
        except (ValueError, OSError) as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command()
    def status(module: str = module_option, project: str | None = project_option):
        """Show accepted policies, comparisons and the local event history."""
        run("status", module, project)

    @app.command()
    def enable(module: str = module_option, project: str | None = project_option):
        """Resume automatic observation and bounded comparisons."""
        run("enable", module, project)

    @app.command()
    def pause(module: str = module_option, project: str | None = project_option):
        """Pause learning and use the original execution strategy."""
        run("pause", module, project)

    @app.command()
    def rollback(module: str = module_option, project: str | None = project_option,
                 context: str = typer.Option("", "--context", help="One context ID; empty restores all accepted policies with a predecessor")):
        """Restore the predecessor of an accepted policy."""
        run("rollback", module, project, context)

    return app
