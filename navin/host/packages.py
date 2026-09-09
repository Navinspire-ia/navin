# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One host adapter for OS package installs (pacman, Omarchy, apt, dnf).

Montage, skills setup, doctor hints, and GitHub CLI install all go through
here instead of growing another ``sudo pacman -S --noconfirm`` branch.

Omarchy is Arch plus Hyprland. ``omarchy-pkg-install`` is a TUI picker: a
user-facing hint only, never argv for the WebUI or the agent.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from navin.utils.proc import no_window_kwargs

LinuxFlavor = Literal["deb", "rpm", "arch", "omarchy", "other"]
WhichFn = Callable[[str], str | None]
ExistsFn = Callable[[str], bool]
ProbeFn = Callable[[str], bool | None]

_OS_RELEASE_PATH = Path("/etc/os-release")
_PACMAN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+_-]*$")
_DEB_IDS = frozenset(
    {
        "debian",
        "ubuntu",
        "linuxmint",
        "pop",
        "elementary",
        "raspbian",
        "kali",
        "zorin",
        "neon",
    }
)
_RPM_IDS = frozenset(
    {
        "fedora",
        "rhel",
        "centos",
        "rocky",
        "almalinux",
        "opensuse",
        "sles",
        "mageia",
    }
)
_ARCH_IDS = frozenset(
    {
        "arch",
        "archlinux",
        "manjaro",
        "cachyos",
        "endeavouros",
        "garuda",
        "artix",
        "arcolinux",
    }
)


class _Unset:
    """Sentinel meaning 'probe passwordless sudo on this host'."""


_UNSET = _Unset()


@dataclass(frozen=True)
class PkgSpec:
    """Package name per ecosystem. Never reuse apt names on Arch."""

    apt: str = ""
    dnf: str = ""
    pacman: str = ""
    aur: str = ""
    brew: str = ""
    winget: str = ""


@dataclass(frozen=True)
class InstallPlan:
    """Argv for a non-interactive install, plus the manual command to show."""

    argv: list[str] | None
    manual: str
    manager_available: bool
    already: bool = False
    kind: str = ""

    @property
    def runnable(self) -> bool:
        return self.argv is not None


def parse_os_release(text: str) -> dict[str, str]:
    """Parse ``/etc/os-release`` KEY=value lines (quoted or not)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def read_os_release(path: Path = _OS_RELEASE_PATH) -> dict[str, str]:
    try:
        return parse_os_release(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def linux_package_flavor(
    *,
    os_release: dict[str, str] | None = None,
    which: WhichFn | None = None,
    exists: ExistsFn | None = None,
) -> LinuxFlavor:
    """Which Linux installer to lead with.

    os-release ID/ID_LIKE first. ``which("pacman")`` is a fallback only:
    stray ``pacman`` binaries exist on non-Arch hosts.
    """
    which_fn = shutil.which if which is None else which

    def present(path: str) -> bool:
        if exists is None:
            return Path(path).is_file()
        return exists(path)

    info = os_release if os_release is not None else read_os_release()
    distro_id = (info.get("ID") or "").strip().casefold()
    like = (info.get("ID_LIKE") or "").casefold().split()
    debish = distro_id in _DEB_IDS or "debian" in like or "ubuntu" in like
    rpmish = distro_id in _RPM_IDS or any(token in like for token in ("rhel", "fedora", "suse"))

    if distro_id == "omarchy":
        return "omarchy"
    # TUI picker is an Omarchy signal only when the host is not already
    # a Debian/RPM distro (a copied binary must not reclassify Ubuntu).
    if which_fn("omarchy-pkg-install") and not debish and not rpmish:
        return "omarchy"
    if distro_id in _ARCH_IDS or "arch" in like:
        return "arch"

    if debish or present("/etc/debian_version") or which_fn("apt-get") or which_fn("apt"):
        return "deb"
    if (
        rpmish
        or present("/etc/redhat-release")
        or present("/etc/fedora-release")
        or which_fn("dnf")
        or which_fn("yum")
        or which_fn("rpm")
    ):
        return "rpm"
    if which_fn("pacman"):
        return "arch"
    return "other"


def sudo_prefix() -> list[str] | None:
    """Privilege prefix for system package managers, or None.

    Root needs no prefix; otherwise passwordless sudo is required because
    there is no way to prompt for a password from the WebUI.
    """
    if sys.platform == "win32":
        return []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if not shutil.which("sudo"):
        return None
    try:
        probe = subprocess.run(  # noqa: S603
            ["sudo", "-n", "true"],
            capture_output=True,
            timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return ["sudo", "-n"] if probe.returncode == 0 else None


def privileged_install(
    manager_bin: str,
    install_args: list[str],
    package: str,
    *,
    which: WhichFn | None = None,
    sudo: list[str] | None | _Unset = _UNSET,
) -> InstallPlan:
    """Build argv for a root-owned manager (apt-get, dnf, yum)."""
    which_fn = shutil.which if which is None else which
    manual = " ".join(["sudo", manager_bin, *install_args, package])
    manager_available = bool(which_fn(manager_bin))
    if not manager_available:
        return InstallPlan(
            argv=None,
            manual=manual,
            manager_available=False,
            kind=manager_bin,
        )
    prefix: list[str] | None = sudo_prefix() if isinstance(sudo, _Unset) else sudo
    if prefix is None:
        return InstallPlan(
            argv=None,
            manual=manual,
            manager_available=True,
            kind=manager_bin,
        )
    return InstallPlan(
        argv=[*prefix, manager_bin, *install_args, package],
        manual=manual,
        manager_available=True,
        kind=manager_bin,
    )


def _pacman_si(name: str) -> bool | None:
    """True if in the sync DB, False if missing, None if the probe cannot run."""
    if not shutil.which("pacman"):
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            ["pacman", "-Si", "--", name],
            capture_output=True,
            timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.returncode == 0


def pacman_installed(name: str, *, probe: ProbeFn | None = None) -> bool:
    if probe is not None:
        return bool(probe(name))
    if not shutil.which("pacman"):
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            ["pacman", "-Qq", "--", name],
            capture_output=True,
            timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def pacman_in_sync_db(name: str, *, probe: ProbeFn | None = None) -> bool | None:
    """True if ``pacman -Si`` finds the package, False if it misses, None if unknown."""
    if probe is not None:
        return probe(name)
    return _pacman_si(name)


def _effective_euid(euid: int | None) -> int:
    if euid is not None:
        return euid
    if hasattr(os, "geteuid"):
        return os.geteuid()
    return 0


def pacman_plan(
    spec: PkgSpec,
    *,
    flavor: LinuxFlavor | None = None,
    which: WhichFn | None = None,
    sudo: list[str] | None | _Unset = _UNSET,
    euid: int | None = None,
    installed: bool | None = None,
    in_sync: bool | None = None,
    os_release: dict[str, str] | None = None,
    exists: ExistsFn | None = None,
) -> InstallPlan:
    """Install plan for an Arch/Omarchy package.

    Official repo: ``pacman -S --needed --noconfirm``. Never ``pacman -Sy``.
    AUR only when the package is missing from the sync DB, ``spec.aur`` is
    set, and ``yay``/``paru`` is present. AUR helpers are not run as root.
    """
    name = (spec.pacman or "").strip()
    if not name or not _PACMAN_NAME_RE.fullmatch(name):
        return InstallPlan(argv=None, manual="", manager_available=False, kind="pacman")

    which_fn = shutil.which if which is None else which
    host_flavor = (
        linux_package_flavor(os_release=os_release, which=which_fn, exists=exists)
        if flavor is None
        else flavor
    )
    manual = f"sudo pacman -S --needed --noconfirm {name}"
    manager_available = host_flavor in {"arch", "omarchy"} and bool(which_fn("pacman"))
    if host_flavor not in {"arch", "omarchy"}:
        return InstallPlan(
            argv=None,
            manual=manual,
            manager_available=False,
            kind="pacman",
        )

    already = pacman_installed(name) if installed is None else installed
    prefix: list[str] | None = sudo_prefix() if isinstance(sudo, _Unset) else sudo
    argv: list[str] | None = None
    if manager_available and prefix is not None:
        argv = [*prefix, "pacman", "-S", "--needed", "--noconfirm", name]

    sync_state = pacman_in_sync_db(name) if in_sync is None else in_sync
    if sync_state is False:
        aur = (spec.aur or "").strip()
        helper = next((bin_name for bin_name in ("yay", "paru") if which_fn(bin_name)), "")
        if aur and _PACMAN_NAME_RE.fullmatch(aur) and helper:
            aur_manual = f"{helper} -S --needed --noconfirm {aur}"
            if _effective_euid(euid) == 0:
                return InstallPlan(
                    argv=None,
                    manual=aur_manual,
                    manager_available=True,
                    already=already,
                    kind="aur",
                )
            return InstallPlan(
                argv=[helper, "-S", "--needed", "--noconfirm", aur],
                manual=aur_manual,
                manager_available=True,
                already=already,
                kind="aur",
            )
        return InstallPlan(
            argv=None,
            manual=manual,
            manager_available=manager_available,
            already=already,
            kind="pacman",
        )

    return InstallPlan(
        argv=argv,
        manual=manual,
        manager_available=manager_available,
        already=already,
        kind="pacman",
    )


def install_hint(spec: PkgSpec) -> str:
    """The command that installs a tool on this machine, when there is one."""
    if sys.platform == "win32":
        return f"winget install --id {spec.winget} -e" if spec.winget else ""
    if sys.platform == "darwin":
        return f"brew install {spec.brew}" if spec.brew else ""
    flavor = linux_package_flavor()
    if flavor in {"arch", "omarchy"} and spec.pacman:
        return f"sudo pacman -S --needed --noconfirm {spec.pacman}"
    if flavor == "deb" and spec.apt:
        return f"sudo apt-get install -y {spec.apt}"
    if flavor == "rpm" and spec.dnf:
        return f"sudo dnf install -y {spec.dnf}"
    if spec.apt and shutil.which("apt-get"):
        return f"sudo apt-get install -y {spec.apt}"
    if spec.dnf and shutil.which("dnf"):
        return f"sudo dnf install -y {spec.dnf}"
    if spec.pacman and shutil.which("pacman"):
        return f"sudo pacman -S --needed --noconfirm {spec.pacman}"
    return (
        f"install {spec.apt or spec.dnf or spec.brew}"
        if (spec.apt or spec.dnf or spec.brew)
        else ""
    )
