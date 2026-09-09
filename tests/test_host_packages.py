# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Host OS package adapter: Omarchy/Arch pacman vs Debian stray pacman."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from navin.host.packages import (  # pyright: ignore[reportImplicitRelativeImport]
    PkgSpec,
    WhichFn,
    install_hint,
    linux_package_flavor,
    pacman_plan,
    parse_os_release,
    privileged_install,
)


def _which(*names: str) -> WhichFn:
    present = set(names)

    def lookup(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return lookup


def _has_debian_version(path: str) -> bool:
    return path == "/etc/debian_version"


def _missing(_path: str) -> bool:
    return False


class ParseOsReleaseTest(unittest.TestCase):
    def test_omarchy_quoted_id_like(self):
        info = parse_os_release('ID=omarchy\nID_LIKE="arch"\n')
        self.assertEqual(info["ID"], "omarchy")
        self.assertEqual(info["ID_LIKE"], "arch")

    def test_arch(self):
        info = parse_os_release("ID=arch\nID_LIKE=arch\n")
        self.assertEqual(info["ID"], "arch")


class LinuxFlavorTest(unittest.TestCase):
    def test_omarchy_id(self):
        flavor = linux_package_flavor(
            os_release={"ID": "omarchy", "ID_LIKE": "arch"},
            which=_which("pacman"),
            exists=_missing,
        )
        self.assertEqual(flavor, "omarchy")

    def test_omarchy_pkg_install_on_arch(self):
        flavor = linux_package_flavor(
            os_release={"ID": "arch", "ID_LIKE": "arch"},
            which=_which("pacman", "omarchy-pkg-install"),
            exists=_missing,
        )
        self.assertEqual(flavor, "omarchy")

    def test_arch_id_like(self):
        flavor = linux_package_flavor(
            os_release={"ID": "manjaro", "ID_LIKE": "arch"},
            which=_which("pacman"),
            exists=_missing,
        )
        self.assertEqual(flavor, "arch")

    def test_debian_with_stray_pacman(self):
        flavor = linux_package_flavor(
            os_release={"ID": "debian", "ID_LIKE": "debian"},
            which=_which("pacman", "apt-get"),
            exists=_has_debian_version,
        )
        self.assertEqual(flavor, "deb")

    def test_omarchy_tui_does_not_reclassify_debian(self):
        flavor = linux_package_flavor(
            os_release={"ID": "ubuntu", "ID_LIKE": "debian"},
            which=_which("apt-get", "omarchy-pkg-install"),
            exists=_has_debian_version,
        )
        self.assertEqual(flavor, "deb")


class PacmanPlanTest(unittest.TestCase):
    def test_omarchy_official_repo(self):
        plan = pacman_plan(
            PkgSpec(pacman="ffmpeg"),
            flavor="omarchy",
            which=_which("pacman"),
            sudo=["sudo", "-n"],
            installed=False,
            in_sync=True,
        )
        self.assertTrue(plan.manager_available)
        self.assertTrue(plan.runnable)
        self.assertEqual(
            plan.argv,
            ["sudo", "-n", "pacman", "-S", "--needed", "--noconfirm", "ffmpeg"],
        )
        self.assertEqual(plan.manual, "sudo pacman -S --needed --noconfirm ffmpeg")
        self.assertFalse(plan.already)

    def test_debian_stray_pacman_not_runnable(self):
        plan = pacman_plan(
            PkgSpec(pacman="ffmpeg"),
            os_release={"ID": "debian"},
            which=_which("pacman", "apt-get"),
            exists=_has_debian_version,
            sudo=["sudo", "-n"],
            installed=False,
            in_sync=True,
        )
        self.assertEqual(
            linux_package_flavor(
                os_release={"ID": "debian"},
                which=_which("pacman", "apt-get"),
                exists=_has_debian_version,
            ),
            "deb",
        )
        self.assertFalse(plan.manager_available)
        self.assertFalse(plan.runnable)
        self.assertIsNone(plan.argv)

    def test_no_sudo_is_manual_only(self):
        plan = pacman_plan(
            PkgSpec(pacman="ffmpeg"),
            flavor="omarchy",
            which=_which("pacman"),
            sudo=None,
            installed=False,
            in_sync=True,
        )
        self.assertTrue(plan.manager_available)
        self.assertFalse(plan.runnable)
        self.assertIsNone(plan.argv)
        self.assertIn("pacman -S --needed --noconfirm ffmpeg", plan.manual)

    def test_already_installed_keeps_needed(self):
        plan = pacman_plan(
            PkgSpec(pacman="ffmpeg"),
            flavor="arch",
            which=_which("pacman"),
            sudo=["sudo", "-n"],
            installed=True,
            in_sync=True,
        )
        self.assertTrue(plan.already)
        self.assertTrue(plan.runnable)
        argv = plan.argv
        self.assertIsNotNone(argv)
        assert argv is not None
        self.assertIn("--needed", argv)

    def test_aur_off_by_default(self):
        plan = pacman_plan(
            PkgSpec(pacman="not-in-repos"),
            flavor="omarchy",
            which=_which("pacman", "yay"),
            sudo=["sudo", "-n"],
            euid=1000,
            installed=False,
            in_sync=False,
        )
        self.assertFalse(plan.runnable)
        self.assertEqual(plan.kind, "pacman")
        self.assertIsNone(plan.argv)

    def test_aur_fallback_not_as_root(self):
        spec = PkgSpec(pacman="not-in-repos", aur="not-in-repos-git")
        as_user = pacman_plan(
            spec,
            flavor="omarchy",
            which=_which("pacman", "yay"),
            sudo=["sudo", "-n"],
            euid=1000,
            installed=False,
            in_sync=False,
        )
        self.assertEqual(as_user.kind, "aur")
        self.assertEqual(
            as_user.argv,
            ["yay", "-S", "--needed", "--noconfirm", "not-in-repos-git"],
        )
        as_root = pacman_plan(
            spec,
            flavor="omarchy",
            which=_which("pacman", "yay"),
            sudo=[],
            euid=0,
            installed=False,
            in_sync=False,
        )
        self.assertFalse(as_root.runnable)
        self.assertEqual(as_root.manual, "yay -S --needed --noconfirm not-in-repos-git")

    def test_invalid_package_name(self):
        plan = pacman_plan(
            PkgSpec(pacman="ffmpeg;true"),
            flavor="omarchy",
            which=_which("pacman"),
            sudo=["sudo", "-n"],
            installed=False,
            in_sync=True,
        )
        self.assertFalse(plan.runnable)
        self.assertEqual(plan.manual, "")

    def test_privileged_install_no_sudo(self):
        plan = privileged_install(
            "apt-get",
            ["install", "-y"],
            "ffmpeg",
            which=_which("apt-get"),
            sudo=None,
        )
        self.assertTrue(plan.manager_available)
        self.assertFalse(plan.runnable)
        self.assertEqual(plan.manual, "sudo apt-get install -y ffmpeg")


class InstallHintTest(unittest.TestCase):
    def test_sqlite_name_on_omarchy(self):
        with patch("navin.host.packages.linux_package_flavor", return_value="omarchy"):
            hint = install_hint(PkgSpec(apt="sqlite3", dnf="sqlite", pacman="sqlite"))
        self.assertEqual(hint, "sudo pacman -S --needed --noconfirm sqlite")

    def test_sqlite_name_on_debian(self):
        with patch("navin.host.packages.linux_package_flavor", return_value="deb"):
            hint = install_hint(PkgSpec(apt="sqlite3", dnf="sqlite", pacman="sqlite"))
        self.assertEqual(hint, "sudo apt-get install -y sqlite3")


class GhInstallGuideTest(unittest.TestCase):
    def test_omarchy_flavor_command(self):
        from navin.board.github_sync import gh_install_guide

        with patch("navin.board.github_sync.linux_package_flavor", return_value="omarchy"):
            guide = gh_install_guide(platform="linux")
        self.assertEqual(guide["flavor"], "omarchy")
        self.assertEqual(guide["command"], "sudo pacman -S --needed --noconfirm github-cli")
        self.assertEqual(guide["label"], "Linux Omarchy (pacman)")


class SetupOptionOrderTest(unittest.TestCase):
    def test_omarchy_lists_pacman_first(self):
        from navin.webui.skills_setup import (  # pyright: ignore[reportImplicitRelativeImport]
            _order_setup_options,
        )

        options = [
            {"kind": "apt", "command": "sudo apt-get install -y git"},
            {"kind": "dnf", "command": "sudo dnf install -y git"},
            {"kind": "pacman", "command": "sudo pacman -S --needed --noconfirm git"},
            {"kind": "brew", "command": "brew install git"},
        ]
        with (
            patch.object(sys, "platform", "linux"),
            patch(
                "navin.webui.skills_setup.linux_package_flavor",
                return_value="omarchy",
            ),
        ):
            ordered = _order_setup_options(options)
        kinds = [option["kind"] for option in ordered]
        self.assertEqual(kinds[0], "pacman")
        self.assertEqual(set(kinds), {"apt", "dnf", "pacman", "brew"})
        self.assertEqual(len(ordered), 4)
        self.assertEqual(kinds[1:], ["apt", "dnf", "brew"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
