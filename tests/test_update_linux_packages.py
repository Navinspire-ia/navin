# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A .deb, .rpm or pacman install updates itself like the Windows setup does.

The app used to answer "update it with your package manager" on Debian,
Fedora and Arch while no repository exists: the Linux desktop was the Windows
desktop minus the update. The manager that owns the package now installs the
new one through polkit, after the application closed, and the published
manifest signs those packages so the toast has something to offer.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.update import service

ROOT = Path(__file__).resolve().parents[1]


def _manifest(version: str = "2.0.0", keys: tuple[str, ...] = ("linux-deb-x64",)) -> dict:
    return {
        "schemaVersion": 1,
        "channel": "stable",
        "version": version,
        "minimumVersion": "",
        "rollout": 100,
        "notes": "",
        "artifacts": {
            key: {
                "url": f"releases/{version}/navin_{version}_amd64.deb",
                "filename": f"navin_{version}_amd64.deb",
                "size": 4096,
                "sha256": "b" * 64,
            }
            for key in keys
        },
    }


class PackageManagerDetectionTest(unittest.TestCase):
    """Which manager owns navin is read from its database, not guessed from the distro."""

    def setUp(self):
        service._linux_package_kind.cache_clear()
        self.addCleanup(service._linux_package_kind.cache_clear)

    def test_dpkg_pacman_rpm_and_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(service._linux_package_manager(root))

            (root / "var/lib/pacman/local/other-1.0-1").mkdir(parents=True)
            self.assertIsNone(service._linux_package_manager(root))
            (root / "var/lib/pacman/local/navin-2.3.12-1").mkdir()
            self.assertEqual(service._linux_package_manager(root), "pacman")

            (root / "var/lib/dpkg/info").mkdir(parents=True)
            (root / "var/lib/dpkg/info/navin.list").write_text("/usr/bin/navin-desktop\n")
            self.assertEqual(service._linux_package_manager(root), "deb")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "var/lib/rpm").mkdir(parents=True)
            installed = mock.Mock(returncode=0)
            with (
                mock.patch.object(service.shutil, "which", return_value="/usr/bin/rpm"),
                mock.patch.object(service.subprocess, "run", return_value=installed) as run,
            ):
                self.assertEqual(service._linux_package_manager(root), "rpm")
            self.assertEqual(run.call_args.args[0], ["rpm", "-q", "navin"])
            with (
                mock.patch.object(service.shutil, "which", return_value="/usr/bin/rpm"),
                mock.patch.object(service.subprocess, "run", return_value=mock.Mock(returncode=1)),
            ):
                self.assertIsNone(service._linux_package_manager(root))

    def test_the_shells_linux_package_is_refined_from_the_database(self):
        for manager, kind in (("deb", "linux-deb"), ("rpm", "linux-rpm"), ("pacman", "linux-pacman"), (None, "linux-package")):
            service._linux_package_kind.cache_clear()
            with (
                mock.patch.object(service, "_linux_package_manager", return_value=manager),
                mock.patch.dict(os.environ, {"NAVIN_INSTALL_KIND": "linux-package"}),
            ):
                self.assertEqual(service._install_kind(), kind)

    def test_a_sidecar_under_usr_lib_is_a_package_even_for_an_old_shell(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "usr/lib/Navin/navin-dist/navin"
            exe.parent.mkdir(parents=True)
            exe.write_text("")
            with (
                mock.patch.object(service, "_linux_package_manager", return_value="deb"),
                mock.patch.object(service, "_SYSTEM_PREFIXES", (str(Path(tmp) / "usr/lib"),)),
                mock.patch.object(service.sys, "frozen", True, create=True),
                mock.patch.object(service.sys, "executable", str(exe)),
                mock.patch.object(service.sys, "platform", "linux"),
                mock.patch.object(service, "_cli_tree", return_value=None),
                mock.patch.dict(os.environ, {"NAVIN_INSTALL_KIND": "", "APPIMAGE": ""}),
            ):
                self.assertEqual(service._install_kind(), "linux-deb")


class PackageUpdateOfferTest(unittest.TestCase):
    """The toast offers the package when the manifest signs one for this manager."""

    def test_platform_keys_name_the_manager(self):
        with mock.patch.object(service, "_arch", return_value="x64"):
            self.assertEqual(service._platform_keys("linux-deb"), ("linux-deb-x64",))
            self.assertEqual(service._platform_keys("linux-rpm"), ("linux-rpm-x64",))
            self.assertEqual(service._platform_keys("linux-pacman"), ("linux-pacman-x64",))

    def test_a_deb_install_gets_a_supported_update(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="linux-deb"),
            mock.patch.object(service, "_arch", return_value="x64"),
        ):
            info = service._release_info(_manifest(), base_url="https://example.invalid", skipped_version="")
        assert info is not None
        self.assertTrue(info["available"])
        self.assertTrue(info["supported"])
        self.assertEqual(info["installKind"], "linux-deb")

    def test_an_rpm_install_waits_for_its_own_package(self):
        # A deb-only drop must not raise a toast on Fedora.
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="linux-rpm"),
            mock.patch.object(service, "_arch", return_value="x64"),
        ):
            self.assertIsNone(
                service._release_info(_manifest(), base_url="https://example.invalid", skipped_version="")
            )

    def test_download_names_keep_the_package_extension(self):
        with mock.patch.object(Path, "mkdir"):
            self.assertTrue(str(service._download_path("2.0.0", "linux-deb")).endswith(".deb"))
            self.assertTrue(str(service._download_path("2.0.0", "linux-rpm")).endswith(".rpm"))
            self.assertTrue(str(service._download_path("2.0.0", "linux-pacman")).endswith(".pkg.tar.zst"))


class PackageInstallTest(unittest.TestCase):
    """The owning manager installs the package as root, after Navin closed."""

    def test_the_helper_runs_the_owning_manager_through_pkexec(self):
        for kind, expected in (
            ("linux-deb", 'dpkg -i "$1"'),
            ("linux-rpm", 'rpm -Uvh --replacepkgs "$1"'),
            ("linux-pacman", 'pacman -U --noconfirm "$1"'),
        ):
            with (
                mock.patch.object(service.shutil, "which", return_value="/usr/bin/pkexec"),
                mock.patch.object(service, "_desktop_executable", return_value=Path("/usr/bin/navin-desktop")),
                mock.patch.object(service, "_start_desktop_updater") as start,
            ):
                service._install_linux_package(Path("/tmp/navin-update.deb"), kind, pid=41, desktop_pid=42)
            command = start.call_args.args[0]
            self.assertEqual(command[:3], ["/bin/sh", "-c", service._PACKAGE_INSTALL_SCRIPT])
            self.assertEqual(command[4:6], ["41", "42"])
            self.assertEqual(command[6], "/tmp/navin-update.deb")
            self.assertEqual(command[7], "/usr/bin/navin-desktop")
            self.assertEqual(command[8], expected)
            self.assertTrue(start.call_args.kwargs["markers"])
        script = service._PACKAGE_INSTALL_SCRIPT
        # Ready/proceed handoff and the wait for the app come BEFORE the install:
        # the package replaces files the running tree still loads lazily.
        self.assertLess(script.index("NAVIN_UPDATE_READY"), script.index("pkexec"))
        self.assertLess(script.index('gone "$1"'), script.index("pkexec"))
        self.assertIn('pkexec sh -c "$5" navin-package-update "$3"', script)
        # Both outcomes bring a window back; a refused prompt relaunches the old one.
        self.assertEqual(script.count('"$4" >/dev/null 2>&1 &'), 2)

    def test_without_pkexec_the_user_gets_the_exact_command(self):
        with (
            mock.patch.object(service.shutil, "which", return_value=None),
            mock.patch.object(service, "_start_desktop_updater") as start,
        ):
            with self.assertRaises(service.UpdateError) as caught:
                service._install_linux_package(Path("/tmp/navin 2.deb"), "linux-deb", pid=1, desktop_pid=0)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("sudo dpkg -i '/tmp/navin 2.deb'", str(caught.exception))
        start.assert_not_called()

    def test_install_update_dispatches_package_kinds(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="linux-pacman"),
            mock.patch.object(
                service,
                "_STATE",
                {"path": "/tmp/navin-update.pkg.tar.zst", "update": {"installKind": "linux-pacman", "latestVersion": "2.0.0"}},
            ),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(service, "_install_linux_package") as install,
            mock.patch.object(service, "_record_pending_install"),
            mock.patch.object(service, "_schedule_shutdown"),
            mock.patch.object(service, "notify"),
        ):
            result = service.install_update()
        self.assertEqual(result["state"], "restarting")
        install.assert_called_once()
        self.assertEqual(install.call_args.args[1], "linux-pacman")

    def test_the_relaunched_window_is_what_the_desktop_pid_runs(self):
        with TemporaryDirectory() as tmp:
            shell = Path(tmp) / "navin-desktop"
            shell.write_text("")
            with mock.patch.object(Path, "resolve", return_value=shell):
                self.assertEqual(service._desktop_executable(4242), shell)
        with (
            mock.patch.object(Path, "resolve", side_effect=OSError("no such process")),
            mock.patch.object(service.shutil, "which", side_effect=lambda name: "/usr/bin/Navin" if name == "Navin" else None),
        ):
            self.assertEqual(service._desktop_executable(4242), Path("/usr/bin/Navin"))
        with mock.patch.object(service.shutil, "which", return_value=None):
            self.assertIsNone(service._desktop_executable(0))


class PublishedPackagesTest(unittest.TestCase):
    """What the publisher signs is what the app can install: the two lists agree."""

    def test_the_manifest_signs_deb_rpm_and_pacman_for_both_arches(self):
        text = (ROOT / "scripts" / "publish-os-to-s3.sh").read_text(encoding="utf-8")
        keys = set(re.findall(r'^\s*"([a-z0-9-]+)\|', text, re.MULTILINE))
        for kind in service._PACKAGE_MANAGER_KINDS:
            for arch in ("x64", "arm64"):
                self.assertIn(f"{kind}-{arch}", keys)
        self.assertIn('linux-deb-x64|linux/deb/x64/navin_${VERSION}_amd64.deb', text)
        self.assertIn('linux-rpm-x64|linux/rpm/x64/navin-${VERSION}-1.x86_64.rpm', text)
        self.assertIn('linux-pacman-x64|linux/pacman/x64/navin-${VERSION}-1-x86_64.pkg.tar.zst', text)

    def test_the_linux_bundler_needs_no_workflow_patch(self):
        # The release workflow used to rewrite this script at build time: the
        # pacman restage read the cargo bundle path and the cargo target filled
        # the runner disk. Both are the script's own business now.
        text = (ROOT / "packaging" / "linux" / "build-appimage.sh").read_text(encoding="utf-8")
        self.assertIn('--deb "$deb_dir/$deb_name"', text)
        self.assertNotIn('--deb "$deb_src"', text)
        self.assertIn("rm -rf desktop/src-tauri/target build/linux", text)
        self.assertLess(text.index("rm -rf desktop/src-tauri/target build/linux"), text.index("pack_pacman.py"))
        self.assertIn("NAVIN_FREE_BUILD_DISK", text)
        # The workflow's patch step still finds its anchor and stays a no-op.
        self.assertIn("# .pkg.tar.zst from the Tauri .deb", text)


if __name__ == "__main__":
    unittest.main()
