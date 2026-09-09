"""``navin computer``: turn desktop control on, check it works, run it sandboxed."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table


def _xvfb_transport_args() -> list[str]:
    """Keep X11 local when WSLg mounts its socket directory read-only."""
    try:
        readonly = bool(os.statvfs("/tmp/.X11-unix").f_flag & os.ST_RDONLY)
    except (AttributeError, OSError):
        return []
    # Linux Xlib and XCB clients also support abstract Unix sockets. This
    # avoids changing the WSLg mount and keeps TCP disabled.
    return ["-nolisten", "unix", "-listen", "local"] if readonly else []


def create_computer_app(*, console: Console) -> typer.Typer:
    computer_app = typer.Typer(
        help="Desktop control (computer use): enable, check permissions, dedicated display."
    )

    def _select_config(config_path: str | None) -> None:
        from navin.config.loader import set_config_path

        if config_path:
            set_config_path(Path(config_path).expanduser())

    def _load(config_path: str | None):
        from navin.config.loader import load_config

        _select_config(config_path)
        return load_config()

    def _save(config, config_path: str | None) -> None:
        from navin.config.loader import save_config

        save_config(config, Path(config_path).expanduser() if config_path else None)

    def _grounding_hint(config, preset: str) -> None:
        """Say whether the preset behind a route can actually aim at the screen."""
        from navin.providers.model_capabilities import supports_grounding, supports_vision

        spec = config.model_presets.get(preset) if preset != "default" else None
        model = getattr(spec, "model", None) if spec is not None else None
        modalities = getattr(spec, "input_modalities", None)
        if not model:
            return
        if not supports_vision(str(model), input_modalities=modalities):
            console.print(
                f"  [red]{escape(str(model))} has no vision[/red]: it cannot see screenshots."
            )
        elif not supports_grounding(str(model), input_modalities=modalities):
            console.print(
                f"  [yellow]{escape(str(model))}: vision, GUI coordinate accuracy "
                "unverified[/yellow]; use accessibility refs and verify each action."
            )
        else:
            console.print(f"  [green]{escape(str(model))}: vision + grounding[/green]")

    @computer_app.command("status")
    def computer_status(
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Show whether the computer tool is on and which backend would drive the desktop."""
        from navin.computer.detect import detect_platform

        config = _load(config_path)
        cfg = config.tools.computer
        choice = detect_platform(preferred=str(cfg.backend), display=cfg.display)
        state = "[green]enabled[/green]" if cfg.enabled else "[yellow]disabled[/yellow]"
        console.print(
            f"computer tool: {state}  (ask: {cfg.ask}, backend: {cfg.backend}, "
            f"session: {cfg.session_mode})"
        )
        console.print(f"detected backend: [bold]{choice.name}[/bold] - {escape(choice.reason)}")
        for note in choice.notes:
            console.print(f"  [dim]{escape(note)}[/dim]")
        preset = config.model_routes.get("computer")
        if preset:
            console.print(f"model route: desktop turns -> preset [bold]{escape(preset)}[/bold]")
            _grounding_hint(config, preset)
        else:
            console.print(
                "model route: [dim]none[/dim] - the default model drives the screen "
                "(set one with [bold]navin computer enable --model <preset>[/bold])"
            )
        if cfg.anthropic_native:
            console.print(
                f"anthropic native tool: {escape(cfg.anthropic_tool_type)} "
                f"(beta {escape(cfg.anthropic_beta)})"
            )
        from navin.computer.policy import AppPolicy, stop_reason

        stopped = stop_reason()
        if stopped:
            console.print(
                f"[red]STOPPED[/red] - {escape(stopped)}. Allow again with "
                "[bold]navin computer go[/bold]."
            )
        for line in AppPolicy.from_config(cfg).describe():
            console.print(f"  policy {escape(line)}")
        if not cfg.enabled:
            console.print("Turn it on with [bold]navin computer enable[/bold].")

    @computer_app.command("stop")
    def computer_stop(
        reason: str = typer.Argument("", help="Why (shown to the agent)"),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Kill switch: halt every desktop session now, until `navin computer go`."""
        from navin.computer.policy import engage_stop

        _select_config(config_path)
        path = engage_stop(reason)
        console.print(
            f"[red]computer use stopped[/red] - the agent gets a refusal on its next action.\n"
            f"[dim]{escape(str(path))}[/dim]  Resume with [bold]navin computer go[/bold]."
        )

    @computer_app.command("go")
    def computer_go(
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Release the kill switch set by `navin computer stop`."""
        from navin.computer.policy import release_stop

        _select_config(config_path)
        if release_stop():
            console.print("[green]computer use allowed again[/green]")
        else:
            console.print("computer use was not stopped")

    @computer_app.command("audit")
    def computer_audit(
        session: str | None = typer.Argument(None, help="Session id (folder name) to print"),
        last: int = typer.Option(10, "--last", "-n", help="How many sessions / steps to show"),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """List recorded desktop sessions, or replay one step by step."""
        import json

        from navin.utils.artifacts import artifact_directory

        cfg = _load(config_path).tools.computer
        root = artifact_directory(str(cfg.screenshot_dir or "computer"))
        if not root.exists():
            console.print(f"no audit trail yet ({escape(str(root))})")
            raise typer.Exit(0)
        if session:
            folder = root / session
            log = folder / "actions.jsonl"
            if not log.exists():
                console.print(f"[red]no such session:[/red] {escape(str(folder))}")
                raise typer.Exit(1)
            rows = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            table = Table(title=f"session {session} ({len(rows)} step(s))")
            table.add_column("#", justify="right")
            table.add_column("t (s)", justify="right")
            table.add_column("action")
            table.add_column("args / result")
            for row in rows[-last:] if last > 0 else rows:
                args = row.get("args") or {}
                summary = row.get("denied") or row.get("result") or ""
                if row.get("rule"):
                    summary = f"[{row['rule']}] {summary}"
                extra = json.dumps(args, ensure_ascii=False) if args else ""
                table.add_row(
                    str(row.get("step", "")),
                    f"{row.get('t', 0):.1f}",
                    str(row.get("action", "")),
                    escape((extra + " " if extra else "") + str(summary))[:200]
                    + (
                        f"\n[dim]{escape(str(row['screenshot']))}[/dim]"
                        if row.get("screenshot")
                        else ""
                    ),
                )
            console.print(table)
            raise typer.Exit(0)
        folders = sorted(
            (p for p in root.iterdir() if p.is_dir() and (p / "actions.jsonl").exists()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not folders:
            console.print(f"no audit trail yet ({escape(str(root))})")
            raise typer.Exit(0)
        table = Table(title=f"desktop sessions ({escape(str(root))})")
        table.add_column("session")
        table.add_column("when")
        table.add_column("steps", justify="right")
        table.add_column("denied", justify="right")
        table.add_column("last action")
        for folder in folders[:last]:
            lines = [
                ln
                for ln in (folder / "actions.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            denied = 0
            last_action = ""
            for ln in lines:
                try:
                    row = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if row.get("denied"):
                    denied += 1
                last_action = str(row.get("action") or last_action)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(folder.stat().st_mtime))
            table.add_row(folder.name, when, str(len(lines)), str(denied), last_action)
        console.print(table)
        console.print("Replay one: [bold]navin computer audit <session>[/bold]")

    @computer_app.command("enable")
    def computer_enable(
        ask: str | None = typer.Option(
            None,
            "--ask",
            help="never | destructive | always: preserve the configured policy when omitted",
        ),
        backend: str | None = typer.Option(
            None, "--backend", help="auto | windows | macos | x11 | wayland | none"
        ),
        display: str | None = typer.Option(
            None, "--display", help="X11 display to drive (e.g. :99 for a dedicated Xvfb)"
        ),
        dedicated: bool | None = typer.Option(
            None,
            "--dedicated/--shared",
            help="Only drive a display reserved for the agent (never the user's own desktop)",
        ),
        native: bool | None = typer.Option(
            None,
            "--anthropic-native/--no-anthropic-native",
            help=(
                "Send the tool to Claude as Anthropic's native computer-use tool "
                "(direct Anthropic provider only; other providers keep the JSON schema)"
            ),
        ),
        live_view: bool | None = typer.Option(
            None, "--live-view/--no-live-view", help="Stream the desktop with interactive takeover"
        ),
        route: str | None = typer.Option(
            None,
            "--model",
            help=(
                "Model preset to route desktop-control turns to "
                "(Settings > Task routing > Desktop control); needs a grounding model"
            ),
        ),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Let the agent see the screen and use the mouse and keyboard."""
        if ask is not None and ask not in {"never", "destructive", "always"}:
            console.print("[red]--ask must be never, destructive or always[/red]")
            raise typer.Exit(1)
        if backend is not None and backend not in {"auto", "windows", "macos", "x11", "wayland", "none"}:
            console.print("[red]--backend must be auto, windows, macos, x11, wayland or none[/red]")
            raise typer.Exit(1)
        config = _load(config_path)
        config.tools.computer.enabled = True
        if ask is not None:
            config.tools.computer.ask = ask  # type: ignore[assignment]
        if backend is not None:
            config.tools.computer.backend = backend  # type: ignore[assignment]
        if dedicated is not None:
            config.tools.computer.session_mode = "dedicated" if dedicated else "shared"
        if display is not None:
            config.tools.computer.display = display or None
        if native is not None:
            config.tools.computer.anthropic_native = native
        if live_view is not None:
            config.tools.computer.live_view = live_view
        if route is not None:
            preset = route.strip()
            if preset and preset != "default" and preset not in config.model_presets:
                names = ", ".join(sorted(config.model_presets)) or "(none)"
                console.print(f"[red]unknown model preset {escape(preset)}[/red]; presets: {names}")
                raise typer.Exit(1)
            if preset:
                config.model_routes["computer"] = preset
            else:
                config.model_routes.pop("computer", None)
        _save(config, config_path)
        console.print(
            f"[green]computer tool enabled[/green] (ask: {config.tools.computer.ask}, session: "
            + config.tools.computer.session_mode
            + f", backend: {config.tools.computer.backend}"
            + (f", display: {config.tools.computer.display}" if config.tools.computer.display else "")
            + (", anthropic native tool" if config.tools.computer.anthropic_native else "")
            + (
                f", routed to preset {config.model_routes['computer']}"
                if config.model_routes.get("computer")
                else ""
            )
            + "). Available on the next agent turn."
        )
        if route is not None and route.strip():
            _grounding_hint(config, route.strip())
        if config.tools.computer.session_mode == "dedicated" and not config.tools.computer.display:
            console.print(
                "[yellow]dedicated mode needs a reserved display:[/yellow] run "
                "[bold]navin computer display start[/bold] (Linux, Xvfb) or use a VM / RDP session."
            )
        console.print("Check it works: [bold]navin computer doctor[/bold]")

    @computer_app.command("disable")
    def computer_disable(
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Stop the agent from driving the desktop."""
        config = _load(config_path)
        config.tools.computer.enabled = False
        _save(config, config_path)
        console.print("[yellow]computer tool disabled[/yellow]")

    @computer_app.command("permissions")
    def computer_permissions(
        kind: str = typer.Option(
            "all", "--kind", help="macOS: all | screen_recording | accessibility | automation"
        ),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Open the OS permission request for the app or terminal running Navin."""
        from navin.computer.base import ComputerError
        from navin.computer.detect import create_backend

        backend = None
        try:
            backend = create_backend(_load(config_path).tools.computer)
            for check in backend.request_permissions(kind):
                console.print(f"{escape(check.name)}: {escape(check.detail)}")
        except ComputerError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc
        finally:
            if backend is not None:
                backend.close()

    @computer_app.command("doctor")
    def computer_doctor(
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Check display, permissions and dependencies for desktop control."""
        from navin.computer.detect import create_backend, detect_platform

        config = _load(config_path)
        cfg = config.tools.computer
        choice = detect_platform(preferred=str(cfg.backend), display=cfg.display)
        console.print(f"backend: [bold]{choice.name}[/bold] - {escape(choice.reason)}")
        for note in choice.notes:
            console.print(f"  [dim]{escape(note)}[/dim]")
        if choice.name == "none":
            raise typer.Exit(1)
        try:
            backend = create_backend(cfg)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]cannot start backend: {escape(str(exc))}[/red]")
            raise typer.Exit(1) from None
        try:
            checks = backend.doctor()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]desktop check failed: {escape(str(exc))}[/red]")
            raise typer.Exit(1) from None
        finally:
            backend.close()
        table = Table(title=f"navin computer doctor ({backend.label})")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        failed = 0
        for check in checks:
            status = "[green]ok[/green]" if check.ok else "[red]FAIL[/red]"
            detail = escape(check.detail)
            if check.fix:
                detail += f"\n[dim]{escape(check.fix)}[/dim]"
            table.add_row(check.name, status, detail)
            if not check.ok and check.name in {
                "screen",
                "screenshot",
                "input",
                "display",
                "permissions",
                "screen_recording",
            }:
                failed += 1
        console.print(table)
        if not cfg.enabled:
            console.print(
                "[yellow]The tool is disabled:[/yellow] run [bold]navin computer enable[/bold]."
            )
        raise typer.Exit(1 if failed else 0)

    @computer_app.command("screenshot")
    def computer_screenshot(
        output: str = typer.Argument("desktop.png", help="Where to save the PNG"),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Capture the screen the agent would see, at full resolution."""
        from navin.computer.base import ComputerError
        from navin.computer.detect import create_backend

        cfg = _load(config_path).tools.computer
        backend = None
        try:
            backend = create_backend(cfg)
            shot = backend.screenshot()
            path = Path(output).expanduser()
            path.write_bytes(shot.png)
        except (ComputerError, OSError) as exc:
            console.print(f"[red]screenshot failed: {escape(str(exc))}[/red]")
            raise typer.Exit(1) from None
        finally:
            if backend is not None:
                backend.close()
        console.print(
            f"saved {shot.width}x{shot.height} screenshot to [bold]{escape(str(path))}[/bold]"
        )

    display_app = typer.Typer(
        help="Dedicated X display (Xvfb) so the agent never touches your own desktop."
    )
    computer_app.add_typer(display_app, name="display")

    def _pid_file(display: str) -> Path:
        from navin.config.paths import get_runtime_subdir

        if not re.fullmatch(r":[0-9]+", display):
            console.print("[red]--display must be an X display number such as :99[/red]")
            raise typer.Exit(1)
        return get_runtime_subdir("computer") / f"xvfb{display.replace(':', '-')}.pid"

    @display_app.command("start")
    def display_start(
        display: str = typer.Option(":99", "--display", help="X display number to create"),
        size: str = typer.Option("1600x900", "--size", help="Virtual screen WIDTHxHEIGHT"),
        wm: bool = typer.Option(
            True, "--wm/--no-wm", help="Start a window manager if one is installed"
        ),
        vnc: bool = typer.Option(
            False, "--vnc", help="Also expose the display over VNC (x11vnc) to watch it"
        ),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Start an Xvfb display and point tools.computer.display at it (Linux)."""
        if sys.platform != "linux":
            console.print(
                "[red]dedicated displays are a Linux (Xvfb) feature; use a VM elsewhere[/red]"
            )
            raise typer.Exit(1)
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            console.print(
                "[red]Xvfb is not installed:[/red] apt install xvfb (and openbox for a window manager)"
            )
            raise typer.Exit(1)
        config = _load(config_path)
        pid_file = _pid_file(display)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                console.print(f"[yellow]display {display} already running (pid {pid})[/yellow]")
                raise typer.Exit(0)
            except (ValueError, ProcessLookupError, PermissionError):
                pid_file.unlink(missing_ok=True)
        try:
            width, height = (int(v) for v in size.lower().split("x", 1))
            if width <= 0 or height <= 0:
                raise ValueError("screen dimensions must be positive")
        except ValueError:
            console.print("[red]--size must look like 1600x900[/red]")
            raise typer.Exit(1) from None
        proc = subprocess.Popen(
            [
                xvfb,
                display,
                "-screen",
                "0",
                f"{width}x{height}x24",
                "-nolisten",
                "tcp",
                *_xvfb_transport_args(),
                "+extension",
                "RANDR",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.8)
        if proc.poll() is not None:
            console.print(f"[red]Xvfb exited immediately (is {display} in use?)[/red]")
            raise typer.Exit(1)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(proc.pid))
        env = dict(os.environ, DISPLAY=display)
        started = [f"Xvfb {display} {width}x{height} (pid {proc.pid})"]
        if wm:
            for candidate in ("openbox", "fluxbox", "xfwm4", "icewm", "twm"):
                exe = shutil.which(candidate)
                if exe:
                    subprocess.Popen(
                        [exe],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    started.append(candidate)
                    break
            else:
                started.append("no window manager found (apt install openbox)")
        if vnc:
            x11vnc = shutil.which("x11vnc")
            if x11vnc:
                subprocess.Popen(
                    [
                        x11vnc,
                        "-display",
                        display,
                        "-forever",
                        "-shared",
                        "-nopw",
                        "-localhost",
                        "-quiet",
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                started.append("x11vnc on localhost:5900")
            else:
                started.append("x11vnc not installed")
        config.tools.computer.display = display
        # A reserved display is exactly what dedicated mode is for.
        config.tools.computer.session_mode = "dedicated"
        _save(config, config_path)
        for line in started:
            console.print(f"  {escape(line)}")
        console.print(
            f"[green]tools.computer.display = {display}[/green]. Launch apps into it with "
            f"[bold]DISPLAY={display} <app> &[/bold]; stop with [bold]navin computer display stop[/bold]."
        )

    @display_app.command("stop")
    def display_stop(
        display: str = typer.Option(":99", "--display", help="X display to stop"),
        config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    ):
        """Stop the dedicated Xvfb display and clear tools.computer.display."""
        config = _load(config_path)
        pid_file = _pid_file(display)
        stopped = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.killpg(pid, signal.SIGTERM)
                stopped = True
            except (ValueError, ProcessLookupError, PermissionError):
                try:
                    os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
                    stopped = True
                except Exception:  # noqa: BLE001
                    pass
            pid_file.unlink(missing_ok=True)
        if config.tools.computer.display == display:
            config.tools.computer.display = None
            _save(config, config_path)
        console.print(
            "[green]stopped[/green]"
            if stopped
            else "[yellow]no dedicated display was running[/yellow]"
        )

    return computer_app
