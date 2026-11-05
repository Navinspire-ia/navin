# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The WSL out-of-memory guard every installer and update path runs.

On 2026-11-05 the kernel killed one browser process in a full WSL VM, and
systemd stopped init.scope, where WSL keeps every terminal, with eight
navin-cli in it. ``navin/utils/wsl_oom_guard.sh`` gives init.scope
OOMPolicy=continue; the same file is the .deb postinst/prerm and the .rpm
%post/%preun, so a failure must never abort a package operation.

The script runs for real here, with ``uname``, ``systemctl`` and ``id``
replaced on PATH and its drop-in folder moved to a temporary one, so nothing
on this machine changes. The root runs against a real systemd are in the
release notes of the change (Ubuntu 24.04 / systemd 255, Arch / systemd 262).
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from navin import diagnostics
from navin.utils import wsl_guard
from navin.utils.process_isolation import WSL_OOM_FIX

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "navin" / "utils" / "wsl_oom_guard.sh"
REAL_DIR = "DROPIN_DIR=/etc/systemd/system/init.scope.d\n"
WSL_KERNEL = "6.6.87.2-microsoft-standard-WSL2"

_FAKE_SYSTEMCTL = """#!/bin/sh
# systemd as the guard sees it: not running, or init.scope with a policy
# that a daemon-reload recomputes from the drop-in.
[ -n "$FAKE_BASE_POLICY" ] || { echo "System has not been booted with systemd" >&2; exit 1; }
case "$1" in
    show)
        if [ -f "$FAKE_LOADED" ]; then cat "$FAKE_LOADED"; else echo "$FAKE_BASE_POLICY"; fi
        ;;
    daemon-reload)
        [ -z "$FAKE_RELOAD_FAILS" ] || exit 1
        echo reload >> "$FAKE_CALLS"
        if [ -f "$FAKE_DROPIN" ] && grep -q '^OOMPolicy=continue$' "$FAKE_DROPIN"; then
            echo continue > "$FAKE_LOADED"
        else
            echo "$FAKE_BASE_POLICY" > "$FAKE_LOADED"
        fi
        ;;
esac
"""


@dataclass
class Host:
    """One fake machine: its kernel, its systemd, its user, its drop-in folder."""

    tmp: Path
    script: Path
    dropin_dir: Path
    env: dict[str, str]

    @property
    def dropin(self) -> Path:
        return self.dropin_dir / "90-navin-oom.conf"

    @property
    def reloads(self) -> int:
        calls = Path(self.env["FAKE_CALLS"])
        return len(calls.read_text().splitlines()) if calls.exists() else 0

    def run(self, *args: str) -> tuple[int, str]:
        done = subprocess.run(
            ["/bin/sh", str(self.script), *args], env=self.env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        return done.returncode, done.stdout.strip()


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def host(tmp_path: Path) -> Host:
    source = GUARD.read_text(encoding="utf-8")
    assert source.count(REAL_DIR) == 1, "the drop-in folder is set once, at the top"
    dropin_dir = tmp_path / "etc" / "init.scope.d"
    script = tmp_path / "wsl_oom_guard.sh"
    script.write_text(source.replace(REAL_DIR, f"DROPIN_DIR={dropin_dir}\n"), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(fake_bin / "uname", '#!/bin/sh\necho "$FAKE_KERNEL"\n')
    _executable(fake_bin / "id", '#!/bin/sh\necho "$FAKE_UID"\n')
    _executable(fake_bin / "systemctl", _FAKE_SYSTEMCTL)
    env = {
        "PATH": f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "FAKE_KERNEL": WSL_KERNEL,
        "FAKE_UID": "0",
        "FAKE_BASE_POLICY": "stop",
        "FAKE_LOADED": str(tmp_path / "loaded"),
        "FAKE_DROPIN": str(dropin_dir / "90-navin-oom.conf"),
        "FAKE_CALLS": str(tmp_path / "calls"),
        "FAKE_RELOAD_FAILS": "",
    }
    return Host(tmp_path, script, dropin_dir, env)


def test_the_script_parses_as_posix_sh() -> None:
    subprocess.run(["/bin/sh", "-n", str(GUARD)], check=True)
    assert GUARD.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


def test_root_on_wsl_gets_the_drop_in_once(host: Host) -> None:
    assert host.run("status") == (0, "navin-wsl-oom-guard: needed: init.scope OOMPolicy=stop")

    assert host.run("install") == (0, "navin-wsl-oom-guard: applied: init.scope OOMPolicy=continue")

    text = host.dropin.read_text(encoding="utf-8")
    assert text.startswith("# Written by Navin: ")
    assert text.endswith("[Scope]\nOOMPolicy=continue\n")
    assert stat.S_IMODE(host.dropin.stat().st_mode) == 0o644
    assert [p.name for p in host.dropin_dir.iterdir()] == ["90-navin-oom.conf"]
    assert host.reloads == 1
    assert host.run("status") == (0, "navin-wsl-oom-guard: protected: init.scope OOMPolicy=continue")
    # Idempotent: an upgrade runs it again and nothing is reloaded twice.
    assert host.run("install") == (0, "navin-wsl-oom-guard: present: init.scope OOMPolicy=continue")
    assert host.reloads == 1


@pytest.mark.parametrize("argument", ["configure", "abort-upgrade", "abort-remove", "1", "2"])
def test_package_install_steps_apply_it(host: Host, argument: str) -> None:
    # deb postinst (configure, abort-*), rpm %post (1, 2) and an upgrade's %preun (1).
    assert host.run(argument) == (0, "navin-wsl-oom-guard: applied: init.scope OOMPolicy=continue")


@pytest.mark.parametrize("argument", ["upgrade", "deconfigure", "failed-upgrade"])
def test_other_package_steps_change_nothing(host: Host, argument: str) -> None:
    host.run("install")

    assert host.run(argument) == (0, f"navin-wsl-oom-guard: unchanged: nothing to do for {argument}")
    assert host.dropin.is_file()


@pytest.mark.parametrize("argument", ["remove", "purge", "0"])
def test_removal_takes_back_only_navin_s_file(host: Host, argument: str) -> None:
    host.run("install")

    code, line = host.run(argument)

    assert (code, line) == (0, f"navin-wsl-oom-guard: removed: {host.dropin}")
    assert not host.dropin_dir.exists()
    assert host.run("status") == (0, "navin-wsl-oom-guard: needed: init.scope OOMPolicy=stop")


def test_removal_keeps_other_files(host: Host) -> None:
    host.dropin_dir.mkdir(parents=True)
    host.dropin.write_text("[Scope]\nOOMPolicy=continue\n", encoding="utf-8")
    assert host.run("remove") == (0, f"navin-wsl-oom-guard: kept: {host.dropin} was not written by Navin")
    assert host.dropin.is_file()

    host.dropin.unlink()
    host.run("install")
    other = host.dropin_dir / "50-site.conf"
    other.write_text("[Scope]\n", encoding="utf-8")
    assert host.run("remove")[1] == f"navin-wsl-oom-guard: removed: {host.dropin}"
    assert other.is_file()
    assert host.run("remove") == (0, "navin-wsl-oom-guard: absent: nothing to remove")


def test_nothing_happens_outside_wsl_or_without_systemd(host: Host) -> None:
    host.env["FAKE_KERNEL"] = "6.8.0-1017-azure"
    assert host.run("install") == (0, "navin-wsl-oom-guard: skipped: not WSL")
    assert host.run("status") == (0, "navin-wsl-oom-guard: not-needed: not WSL")

    host.env["FAKE_KERNEL"] = WSL_KERNEL
    host.env["FAKE_BASE_POLICY"] = ""
    assert host.run("install") == (0, "navin-wsl-oom-guard: skipped: systemd does not run this system")
    assert host.run("status") == (0, "navin-wsl-oom-guard: not-needed: systemd does not run this system")
    assert not host.dropin_dir.exists()
    assert host.reloads == 0


def test_a_policy_set_elsewhere_is_left_alone(host: Host) -> None:
    host.env["FAKE_BASE_POLICY"] = "continue"

    assert host.run("status") == (
        0, "navin-wsl-oom-guard: protected: init.scope OOMPolicy=continue (set outside Navin)",
    )
    assert host.run("install") == (0, "navin-wsl-oom-guard: present: init.scope OOMPolicy=continue")
    assert not host.dropin_dir.exists()


def test_failures_are_reported_and_never_fail_a_package_step(host: Host) -> None:
    host.env["FAKE_UID"] = "1000"
    assert host.run("install") == (0, "navin-wsl-oom-guard: failed: needs root (run it with sudo)")
    assert host.run("configure") == (0, "navin-wsl-oom-guard: failed: needs root (run it with sudo)")
    assert not host.dropin_dir.exists()

    host.env["FAKE_UID"] = "0"
    host.env["FAKE_RELOAD_FAILS"] = "1"
    assert host.run("install") == (0, "navin-wsl-oom-guard: failed: systemctl daemon-reload")

    host.env["FAKE_RELOAD_FAILS"] = ""
    host.dropin.unlink()
    blocker = host.dropin_dir
    blocker.rmdir()
    blocker.write_text("not a folder", encoding="utf-8")
    assert host.run("install") == (0, f"navin-wsl-oom-guard: failed: cannot write {host.dropin}")


def test_no_argument_is_a_usage_error(host: Host) -> None:
    done = subprocess.run(["/bin/sh", str(host.script)], env=host.env, capture_output=True, text=True, check=False)
    assert done.returncode == 2
    assert "usage: wsl_oom_guard.sh status|install|remove" in done.stderr


# --- Where Navin runs the guard by itself ------------------------------------


def test_the_guard_ships_next_to_its_runner() -> None:
    assert wsl_guard.guard_script() == GUARD


def test_the_guard_line_is_read_from_any_output() -> None:
    text = "noise\nnavin-wsl-oom-guard: applied: init.scope OOMPolicy=continue\n"
    assert wsl_guard.parse_line(text) == wsl_guard.GuardResult("applied", "init.scope OOMPolicy=continue")
    assert wsl_guard.parse_line("sudo: a password is required") is None


def test_run_here_reports_the_guard_s_own_words(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl_guard, "guard_script", lambda: host.script)
    monkeypatch.setattr(wsl_guard, "host_tool_env", lambda: host.env)

    assert wsl_guard.run_here("status") == wsl_guard.GuardResult("needed", "init.scope OOMPolicy=stop")
    assert wsl_guard.run_here("install") == wsl_guard.GuardResult("applied", "init.scope OOMPolicy=continue")
    assert wsl_guard.run_here("status").protected


class _FakeWslExe:
    """wsl.exe as Windows answers: UTF-16 for its own lists, UTF-8 from a distribution."""

    def __init__(self, answer: bytes) -> None:
        self.answer = answer
        self.runs: list[tuple[list[str], bytes]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[1:] == ["-l", "--running", "-q"]:
            listed = "Ubuntu\r\ndocker-desktop\r\n".encode("utf-16-le")
            return subprocess.CompletedProcess(argv, 0, stdout=listed, stderr=b"")
        payload = kwargs["input"]
        assert isinstance(payload, bytes)
        self.runs.append((list(argv), payload))
        return subprocess.CompletedProcess(argv, 0, stdout=self.answer, stderr=b"")


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """A Windows host with Ubuntu and Docker Desktop's VM running."""
    seen: dict[str, object] = {"notified": [], "version": "2.5.0"}
    notified: list[str] = []
    seen["notified"] = notified
    monkeypatch.setattr(wsl_guard, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(wsl_guard, "_version", lambda: str(seen["version"]))
    monkeypatch.setattr(wsl_guard.wsl_paths, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(wsl_guard.wsl_paths, "distributions", lambda refresh=False: ["Ubuntu", "docker-desktop"])
    monkeypatch.setattr(wsl_guard.wsl_health, "refuse_reason", lambda distro: None)
    monkeypatch.setattr(wsl_guard, "_announce", notified.append)
    return seen


def test_windows_protects_each_running_distribution_once_per_version(
    windows: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWslExe(b"navin-wsl-oom-guard: applied: init.scope OOMPolicy=continue\n")
    monkeypatch.setattr(wsl_guard.subprocess, "run", fake)

    first = wsl_guard.protect_running_distributions()

    assert first == [("Ubuntu", wsl_guard.GuardResult("applied", "init.scope OOMPolicy=continue"))]
    ((argv, payload),) = fake.runs
    assert argv == ["wsl.exe", "-d", "Ubuntu", "-u", "root", "--exec", "sh", "-s", "install"]
    assert payload == GUARD.read_bytes()
    assert windows["notified"] == ["Ubuntu"]
    # Same version: not again. The next version (an update) runs it again.
    assert wsl_guard.protect_running_distributions() == []
    windows["version"] = "2.5.1"
    fake.answer = b"navin-wsl-oom-guard: present: init.scope OOMPolicy=continue\n"
    assert wsl_guard.protect_running_distributions() == [
        ("Ubuntu", wsl_guard.GuardResult("present", "init.scope OOMPolicy=continue")),
    ]
    assert len(fake.runs) == 2
    assert windows["notified"] == ["Ubuntu"]


def test_a_wsl_exe_failure_is_tried_again_at_the_next_start(
    windows: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWslExe("Error code: Wsl/Service/E_UNEXPECTED\r\n".encode("utf-16-le"))
    monkeypatch.setattr(wsl_guard.subprocess, "run", fake)

    ((distro, result),) = wsl_guard.protect_running_distributions()

    assert (distro, result.state) == ("Ubuntu", "error")
    assert "Wsl/Service/E_UNEXPECTED" in result.detail
    assert len(wsl_guard.protect_running_distributions()) == 1
    assert len(fake.runs) == 2
    assert windows["notified"] == []


def test_the_cli_reminds_once_per_version_inside_wsl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NAVIN_WSL_OOM_GUARD", raising=False)
    monkeypatch.setattr(wsl_guard, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(wsl_guard, "_version", lambda: "2.5.0")
    monkeypatch.setattr(wsl_guard.wsl_paths, "is_wsl_guest", lambda: True)
    monkeypatch.setattr(wsl_guard, "_is_root", lambda: False)
    answer = [wsl_guard.GuardResult("needed", "init.scope OOMPolicy=stop")]
    monkeypatch.setattr(wsl_guard, "run_here", lambda action: answer[0])

    assert wsl_guard.reminder_once() == wsl_guard.REMINDER
    assert wsl_guard.reminder_once() == ""
    monkeypatch.setattr(wsl_guard, "_version", lambda: "2.5.1")
    answer[0] = wsl_guard.GuardResult("protected", "init.scope OOMPolicy=continue")
    assert wsl_guard.reminder_once() == ""
    answer[0] = wsl_guard.GuardResult("needed", "init.scope OOMPolicy=stop")
    monkeypatch.setenv("NAVIN_WSL_OOM_GUARD", "0")
    assert wsl_guard.reminder_once() == ""


def test_root_inside_wsl_applies_it_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAVIN_WSL_OOM_GUARD", raising=False)
    monkeypatch.setattr(wsl_guard.wsl_paths, "is_wsl_guest", lambda: True)
    monkeypatch.setattr(wsl_guard, "_is_root", lambda: True)
    runs: list[str] = []

    def run_here(action: str) -> wsl_guard.GuardResult:
        runs.append(action)
        return wsl_guard.GuardResult("applied", "init.scope OOMPolicy=continue")

    monkeypatch.setattr(wsl_guard, "run_here", run_here)

    thread = wsl_guard.start_in_background("cli")

    assert thread is not None
    thread.join(5)
    assert runs == ["install"]
    monkeypatch.setattr(wsl_guard, "_is_root", lambda: False)
    assert wsl_guard.start_in_background("cli") is None
    monkeypatch.setattr(wsl_guard, "_is_root", lambda: True)
    monkeypatch.setenv("NAVIN_WSL_OOM_GUARD", "0")
    assert wsl_guard.start_in_background("cli") is None
    assert runs == ["install"]


def test_doctor_install_applies_it_or_names_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    check = diagnostics.Check(name="wsl oom policy", ok=False, detail="init.scope OOMPolicy=stop", hint="")
    sections = [diagnostics.Section("Memory", [check])]
    asked: list[bool] = []

    def applied(*, interactive: bool) -> wsl_guard.GuardResult:
        asked.append(interactive)
        return wsl_guard.GuardResult("applied", "init.scope OOMPolicy=continue")

    monkeypatch.setattr(wsl_guard, "install_here", applied)
    (outcome,) = diagnostics.install_missing_tools(sections, interactive=True)
    assert (outcome.name, outcome.status, outcome.detail) == (
        "wsl oom policy", "installed", "init.scope OOMPolicy=continue",
    )
    assert asked == [True]

    def refused(*, interactive: bool) -> wsl_guard.GuardResult:
        return wsl_guard.GuardResult("error", "sudo: a password is required")

    monkeypatch.setattr(wsl_guard, "install_here", refused)
    (outcome,) = diagnostics.install_missing_tools(sections, interactive=False)
    assert outcome.status == "manual"
    assert outcome.detail == "error: sudo: a password is required"
    assert outcome.command == WSL_OOM_FIX
    assert WSL_OOM_FIX in diagnostics.manual_commands([outcome])

    protected = diagnostics.Check(name="wsl oom policy", ok=True, detail="init.scope OOMPolicy=continue", hint="")
    assert diagnostics.install_missing_tools([diagnostics.Section("Memory", [protected])], interactive=True) == []
