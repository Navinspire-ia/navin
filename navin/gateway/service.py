# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Install and manage OS-level gateway services."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from navin.gateway import GatewayStartOptions, build_gateway_command

ServiceManagerKind = Literal["auto", "systemd", "launchd", "windows"]

# Where Windows keeps per-user programs started at logon. A scheduled task would
# also work, but it is created by a separate tool whose failure modes are its own,
# while this is one value the same user can always write and remove.
_WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


@dataclass(frozen=True)
class GatewayServiceOptions:
    """Inputs used to render one system service."""

    start: GatewayStartOptions
    name: str = "navin-gateway"
    manager: ServiceManagerKind = "auto"
    enable: bool = True
    start_now: bool = True
    python_executable: str = sys.executable


@dataclass(frozen=True)
class GatewayServiceResult:
    """Result from service install/uninstall operations."""

    ok: bool
    message: str
    manager: str
    path: Path | None
    commands: tuple[tuple[str, ...], ...] = ()
    content: str | None = None


class GatewayServiceInstaller:
    """Start the gateway at login: systemd user unit, LaunchAgent, or Run value."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        subprocess_run: Callable[..., Any] = subprocess.run,
        home: Path | None = None,
    ) -> None:
        self.platform_name = platform_name or _platform_name()
        self._subprocess_run = subprocess_run
        self.home = home or Path.home()

    def install(self, options: GatewayServiceOptions, *, dry_run: bool = False) -> GatewayServiceResult:
        manager = self._resolve_manager(options.manager)
        if manager == "systemd":
            return self._install_systemd(options, dry_run=dry_run)
        if manager == "launchd":
            return self._install_launchd(options, dry_run=dry_run)
        if manager == "windows":
            return self._install_windows(options, dry_run=dry_run)
        return GatewayServiceResult(False, f"unsupported_service_manager:{manager}", manager, None)

    def uninstall(
        self,
        *,
        name: str = "navin-gateway",
        manager: ServiceManagerKind = "auto",
        dry_run: bool = False,
    ) -> GatewayServiceResult:
        resolved = self._resolve_manager(manager)
        if resolved == "systemd":
            return self._uninstall_systemd(name=name, dry_run=dry_run)
        if resolved == "launchd":
            return self._uninstall_launchd(name=name, dry_run=dry_run)
        if resolved == "windows":
            return self._uninstall_windows(name=name, dry_run=dry_run)
        return GatewayServiceResult(False, f"unsupported_service_manager:{resolved}", resolved, None)

    def _install_systemd(
        self,
        options: GatewayServiceOptions,
        *,
        dry_run: bool,
    ) -> GatewayServiceResult:
        unit_name = _systemd_unit_name(options.name)
        path = self.home / ".config" / "systemd" / "user" / unit_name
        command = build_gateway_command(options.python_executable, options.start)
        content = _systemd_unit_content(
            description=f"Navin Gateway ({options.name})",
            command=command,
            working_directory=_working_directory_text(options.start),
        )
        commands: list[tuple[str, ...]] = [("systemctl", "--user", "daemon-reload")]
        if options.enable:
            commands.append(("systemctl", "--user", "enable", unit_name))
        if options.start_now:
            commands.append(("systemctl", "--user", "restart", unit_name))
        if dry_run:
            return GatewayServiceResult(True, "service_install_dry_run", "systemd", path, tuple(commands), content)

        _working_directory(options.start).mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        for command_args in commands:
            self._subprocess_run(list(command_args), check=True)
        return GatewayServiceResult(True, "service_installed", "systemd", path, tuple(commands), content)

    def _uninstall_systemd(
        self,
        *,
        name: str,
        dry_run: bool,
    ) -> GatewayServiceResult:
        unit_name = _systemd_unit_name(name)
        path = self.home / ".config" / "systemd" / "user" / unit_name
        commands = (
            ("systemctl", "--user", "disable", "--now", unit_name),
            ("systemctl", "--user", "daemon-reload"),
        )
        if dry_run:
            return GatewayServiceResult(True, "service_uninstall_dry_run", "systemd", path, commands)

        self._run_best_effort(commands[0])
        path.unlink(missing_ok=True)
        self._subprocess_run(list(commands[1]), check=True)
        return GatewayServiceResult(True, "service_uninstalled", "systemd", path, commands)

    def _install_launchd(
        self,
        options: GatewayServiceOptions,
        *,
        dry_run: bool,
    ) -> GatewayServiceResult:
        label = _launchd_label(options.name)
        path = self.home / "Library" / "LaunchAgents" / f"{label}.plist"
        log_stem = _safe_service_name(options.name)
        stdout_path = self.home / ".navin" / "logs" / f"{log_stem}.launchd.log"
        stderr_path = self.home / ".navin" / "logs" / f"{log_stem}.launchd.err.log"
        payload = {
            "Label": label,
            "ProgramArguments": build_gateway_command(options.python_executable, options.start),
            "WorkingDirectory": _working_directory_text(options.start),
            "RunAtLoad": bool(options.enable),
            "KeepAlive": {"SuccessfulExit": False},
            "StandardOutPath": str(stdout_path),
            "StandardErrorPath": str(stderr_path),
        }
        content = plistlib.dumps(payload, sort_keys=False).decode("utf-8")
        domain = _launchd_domain()
        commands: list[tuple[str, ...]] = []
        if options.start_now:
            commands.append(("launchctl", "bootstrap", domain, str(path)))
        if options.enable:
            commands.append(("launchctl", "enable", f"{domain}/{label}"))
        if options.start_now:
            commands.append(("launchctl", "kickstart", "-k", f"{domain}/{label}"))
        if dry_run:
            return GatewayServiceResult(True, "service_install_dry_run", "launchd", path, tuple(commands), content)

        _working_directory(options.start).mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if options.start_now:
            self._run_best_effort(("launchctl", "bootout", domain, str(path)))
        for command_args in commands:
            self._subprocess_run(list(command_args), check=True)
        return GatewayServiceResult(True, "service_installed", "launchd", path, tuple(commands), content)

    def _uninstall_launchd(
        self,
        *,
        name: str,
        dry_run: bool,
    ) -> GatewayServiceResult:
        label = _launchd_label(name)
        path = self.home / "Library" / "LaunchAgents" / f"{label}.plist"
        domain = _launchd_domain()
        commands = (
            ("launchctl", "bootout", domain, str(path)),
            ("launchctl", "disable", f"{domain}/{label}"),
        )
        if dry_run:
            return GatewayServiceResult(True, "service_uninstall_dry_run", "launchd", path, commands)

        for command_args in commands:
            self._run_best_effort(command_args)
        path.unlink(missing_ok=True)
        return GatewayServiceResult(True, "service_uninstalled", "launchd", path, commands)

    def _install_windows(
        self,
        options: GatewayServiceOptions,
        *,
        dry_run: bool,
    ) -> GatewayServiceResult:
        value_name = _windows_value_name(options.name)
        command = _windows_logon_command(options)
        content = subprocess.list2cmdline(command)
        # No commands: nothing external is invoked, the value is the whole
        # installation. Reported anyway so `--dry-run` shows what will be written.
        commands: tuple[tuple[str, ...], ...] = ()
        path = Path(f"HKCU:\\{_WINDOWS_RUN_KEY}\\{value_name}")
        if dry_run:
            return GatewayServiceResult(True, "service_install_dry_run", "windows", path, commands, content)

        _working_directory(options.start).mkdir(parents=True, exist_ok=True)
        try:
            self._write_run_value(value_name, content if options.enable else None)
        except OSError as exc:
            return GatewayServiceResult(False, f"service_install_failed:{exc}", "windows", path, commands, content)
        if options.start_now:
            # Detached, so the gateway outlives the shell that asked for it, the
            # same way the systemd and launchd paths start it immediately.
            from navin.process_runtime import child_environment, detached_no_window_kwargs

            with suppress(OSError):
                subprocess.Popen(  # noqa: S603
                    command,
                    cwd=str(_working_directory(options.start)),
                    env=child_environment(),
                    **detached_no_window_kwargs(),
                )
        return GatewayServiceResult(True, "service_installed", "windows", path, commands, content)

    def _uninstall_windows(
        self,
        *,
        name: str,
        dry_run: bool,
    ) -> GatewayServiceResult:
        value_name = _windows_value_name(name)
        path = Path(f"HKCU:\\{_WINDOWS_RUN_KEY}\\{value_name}")
        if dry_run:
            return GatewayServiceResult(True, "service_uninstall_dry_run", "windows", path)
        with suppress(OSError):
            self._delete_run_value(value_name)
        return GatewayServiceResult(True, "service_uninstalled", "windows", path)

    def _write_run_value(self, value_name: str, command: str | None) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if command is None:
                with suppress(FileNotFoundError):
                    winreg.DeleteValue(key, value_name)
                return
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)

    def _delete_run_value(self, value_name: str) -> None:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, value_name)

    def _resolve_manager(self, manager: ServiceManagerKind) -> str:
        if manager != "auto":
            return manager
        if self.platform_name == "Darwin":
            return "launchd"
        if self.platform_name == "Linux":
            return "systemd"
        if self.platform_name == "Windows":
            return "windows"
        return self.platform_name.lower()

    def _run_best_effort(self, command_args: tuple[str, ...]) -> None:
        self._subprocess_run(list(command_args), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _platform_name() -> str:
    if sys.platform == "darwin":
        return "Darwin"
    if sys.platform.startswith("linux"):
        return "Linux"
    if sys.platform.startswith("win"):
        return "Windows"
    return sys.platform


def _working_directory(options: GatewayStartOptions) -> Path:
    if options.workspace:
        return Path(options.workspace).expanduser()
    return Path.home()


def _working_directory_text(options: GatewayStartOptions) -> str:
    if options.workspace:
        return os.path.expanduser(options.workspace)
    return str(Path.home())


def _systemd_unit_name(name: str) -> str:
    stem = _safe_service_name(name)
    return stem if stem.endswith(".service") else f"{stem}.service"


def _launchd_label(name: str) -> str:
    if name.startswith("ai.navin."):
        return name
    suffix = _safe_service_name(name).removeprefix("navin-").replace("-", ".")
    return f"ai.navin.{suffix}"


def _safe_service_name(name: str) -> str:
    value = name.strip().lower()
    value = re.sub(r"[^a-z0-9_.-]+", "-", value)
    value = value.strip(".-")
    return value or "navin-gateway"


def _windows_value_name(name: str) -> str:
    """The Run value name. One per service name, so two instances can coexist."""
    stem = _safe_service_name(name)
    return "Navin" if stem == "navin-gateway" else f"Navin ({stem})"


def _windows_logon_command(options: GatewayServiceOptions) -> list[str]:
    """The logon command, preferring an executable that opens no window.

    A console program in the Run key flashes a black window at every logon, and
    on a packaged build it would keep one open for as long as the gateway runs.
    Both shapes ship a windowless entry point: Navin.exe beside navin-cli.exe in
    a packaged build, pythonw.exe beside python.exe in a source install.
    """
    command = build_gateway_command(options.python_executable, options.start)
    launcher = Path(command[0])
    for windowless in _WINDOWLESS_NAMES.get(launcher.name.lower(), ()):
        candidate = launcher.with_name(windowless)
        if candidate.is_file():
            return [str(candidate), *command[1:]]
    return command


_WINDOWLESS_NAMES = {
    "navin-cli.exe": ("Navin.exe",),
    "navin.exe": ("Navin.exe",),
    "python.exe": ("pythonw.exe",),
}


def _launchd_domain() -> str:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return "gui/current"
    return f"gui/{getuid()}"


def _systemd_unit_content(
    *,
    description: str,
    command: list[str],
    working_directory: str,
) -> str:
    quoted_command = " ".join(_systemd_quote(part) for part in command)
    return "\n".join(
        [
            "[Unit]",
            f"Description={description}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_systemd_quote(str(working_directory))}",
            f"ExecStart={quoted_command}",
            "Restart=always",
            "RestartSec=10",
            "Environment=PYTHONUNBUFFERED=1",
            "NoNewPrivileges=yes",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _systemd_quote(value: str) -> str:
    if value and not re.search(r"\s|['\"\\]", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
