"""The packaged CLI updates itself: kind ``cli``, signed archive, directory swap.

A user who ran ``curl https://navin.live/install`` has a ``navin-dist/`` tree
somewhere they own and wrappers pointing at it. ``navin update`` must find that
tree, fetch the archive signed for this OS, prove the new tree starts and swap
it in place, never leaving a half-replaced install behind.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.update import notice, service

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "scripts" / "publish-os-to-s3.sh"

POSIX = sys.platform != "win32"


def _cli_manifest(version: str = "1.0.1") -> dict:
    return {
        "schemaVersion": 1,
        "channel": "stable",
        "version": version,
        "minimumVersion": "",
        "rollout": 100,
        "notes": "",
        "artifacts": {
            "cli-linux-x64": {
                "url": f"v{version}/navin-cli-{version}-linux-x64.tar.gz",
                "size": 2048,
                "sha256": "b" * 64,
            },
            "cli-macos-x64": {
                "url": f"v{version}/navin-cli-{version}-macos-x64.tar.gz",
                "size": 2048,
                "sha256": "c" * 64,
            },
        },
    }


class InstallKindTest(unittest.TestCase):
    """A frozen ``navin-dist/navin`` the user owns is a CLI install."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {}, clear=False))
        os.environ.pop("NAVIN_INSTALL_KIND", None)
        os.environ.pop("APPIMAGE", None)
        os.environ.pop("NAVIN_DESKTOP_APP", None)

    def _frozen_at(self, executable: Path):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"")
        return (
            mock.patch.object(service.sys, "frozen", True, create=True),
            mock.patch.object(service.sys, "executable", str(executable)),
        )

    def test_owned_navin_dist_is_kind_cli(self):
        exe = self.tmp / "pkg" / "navin-dist" / service._cli_binary_name()
        frozen, executable = self._frozen_at(exe)
        with frozen, executable:
            self.assertEqual(service._cli_tree(), exe.parent)
            self.assertEqual(service._install_kind(), "cli")

    def test_the_desktop_sidecar_tree_is_not_cli(self):
        # The same tree next to the window executable belongs to the setup.
        exe = self.tmp / "Navin" / "navin-dist" / service._cli_binary_name()
        frozen, executable = self._frozen_at(exe)
        (self.tmp / "Navin" / "Navin.exe").write_bytes(b"")
        with frozen, executable:
            self.assertIsNone(service._cli_tree())
            self.assertNotEqual(service._install_kind(), "cli")

    def test_the_cli_home_next_to_a_desktop_install_is_still_cli(self):
        # %LOCALAPPDATA%\Navin\Navin.exe (desktop) and %LOCALAPPDATA%\Navin\cli\
        # navin-dist (CLI) coexist; the CLI must keep updating itself.
        exe = self.tmp / "Navin" / "cli" / "navin-dist" / service._cli_binary_name()
        frozen, executable = self._frozen_at(exe)
        (self.tmp / "Navin" / "Navin.exe").write_bytes(b"")
        with frozen, executable:
            self.assertEqual(service._cli_tree(), exe.parent)

    def test_a_tree_under_binaries_next_to_the_window_is_not_cli(self):
        exe = self.tmp / "Navin" / "binaries" / "navin-dist" / service._cli_binary_name()
        frozen, executable = self._frozen_at(exe)
        (self.tmp / "Navin" / "Navin.exe").write_bytes(b"")
        with frozen, executable:
            self.assertIsNone(service._cli_tree())

    def test_a_tree_named_differently_is_not_cli(self):
        exe = self.tmp / "somewhere" / "bin" / service._cli_binary_name()
        frozen, executable = self._frozen_at(exe)
        with frozen, executable:
            self.assertIsNone(service._cli_tree())

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "needs POSIX permissions")
    def test_a_tree_the_user_cannot_write_to_is_not_cli(self):
        exe = self.tmp / "opt" / "navin-dist" / "navin"
        frozen, executable = self._frozen_at(exe)
        parent = exe.parent.parent
        parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with frozen, executable:
                self.assertIsNone(service._cli_tree())
        finally:
            parent.chmod(stat.S_IRWXU)

    def test_explicit_kind_from_the_desktop_shell_wins(self):
        exe = self.tmp / "pkg" / "navin-dist" / service._cli_binary_name()
        frozen, executable = self._frozen_at(exe)
        with (
            frozen,
            executable,
            mock.patch.dict(os.environ, {"NAVIN_INSTALL_KIND": "linux-package"}),
        ):
            self.assertEqual(service._install_kind(), "linux-package")

    def test_source_checkout_is_not_cli(self):
        with mock.patch.object(service.sys, "frozen", False, create=True):
            self.assertIsNone(service._cli_tree())
            self.assertEqual(service._install_kind(), "source")


class PlatformKeyTest(unittest.TestCase):
    """The CLI key names the OS: it is what the publisher signs."""

    def test_keys_per_os(self):
        for platform_name, machine, expected in (
            ("linux", "x86_64", ("cli-linux-x64",)),
            ("linux", "aarch64", ("cli-linux-arm64",)),
            ("win32", "AMD64", ("cli-windows-x64",)),
            ("darwin", "x86_64", ("cli-macos-x64",)),
            ("darwin", "arm64", ("cli-macos-arm64", "cli-macos-x64")),
        ):
            with self.subTest(platform=platform_name, machine=machine):
                with (
                    mock.patch.object(service.sys, "platform", platform_name),
                    mock.patch.object(service.platform, "machine", return_value=machine),
                ):
                    self.assertEqual(service._platform_keys("cli"), expected)

    def test_desktop_keys_are_unchanged(self):
        with mock.patch.object(service.platform, "machine", return_value="AMD64"):
            self.assertEqual(service._platform_keys("windows-setup"), ("windows-setup-x64",))
        with mock.patch.object(service.platform, "machine", return_value="arm64"):
            self.assertEqual(
                service._platform_keys("macos-app"), ("macos-app-arm64", "macos-app-x64")
            )

    def test_cli_install_finds_its_artifact(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="cli"),
            mock.patch.object(service.sys, "platform", "linux"),
            mock.patch.object(service.platform, "machine", return_value="x86_64"),
        ):
            info = service._release_info(
                _cli_manifest(), base_url="https://updates.navin.live", skipped_version=""
            )
        assert info is not None
        self.assertTrue(info["available"])
        self.assertTrue(info["supported"])
        self.assertEqual(info["installKind"], "cli")
        self.assertTrue(info["artifact"]["url"].endswith("navin-cli-1.0.1-linux-x64.tar.gz"))

    def test_cli_install_is_silent_when_its_os_was_not_shipped(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="cli"),
            mock.patch.object(service.sys, "platform", "win32"),
            mock.patch.object(service.platform, "machine", return_value="AMD64"),
        ):
            info = service._release_info(
                _cli_manifest(), base_url="https://updates.navin.live", skipped_version=""
            )
        self.assertIsNone(info)

    def test_download_name_matches_the_archive_format(self):
        with (
            mock.patch.object(service.sys, "platform", "win32"),
            mock.patch.object(service.Path, "home", return_value=Path(tempfile.mkdtemp())),
        ):
            self.assertTrue(service._download_path("1.0.1", "cli").name.endswith(".zip"))
        with (
            mock.patch.object(service.sys, "platform", "linux"),
            mock.patch.object(service.Path, "home", return_value=Path(tempfile.mkdtemp())),
        ):
            self.assertTrue(service._download_path("1.0.1", "cli").name.endswith(".tar.gz"))


def _fake_tree(root: Path, version: str, *, broken: bool = False) -> Path:
    """A ``navin-dist/`` whose ``navin`` is a shell script answering --version."""
    tree = root / "navin-dist"
    (tree / "_internal").mkdir(parents=True)
    (tree / "_internal" / "marker.txt").write_text(version)
    binary = tree / "navin"
    body = "#!/bin/sh\nexit 3\n" if broken else f'#!/bin/sh\necho "navin v{version}"\n'
    binary.write_text(body)
    binary.chmod(0o755)
    return tree


def _archive_of(tree: Path, destination: Path) -> Path:
    with tarfile.open(destination, "w:gz") as bundle:
        bundle.add(tree, arcname=tree.name)
    return destination


@unittest.skipUnless(POSIX, "the in-process directory swap is the POSIX path")
class TreeSwapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install = self.tmp / "pkg"
        self.install.mkdir()
        self.tree = _fake_tree(self.install, "1.0.0")
        self.enterContext(mock.patch.object(service, "_cli_tree", return_value=self.tree))

    def _archive(self, version: str, *, broken: bool = False) -> Path:
        source = _fake_tree(self.tmp / f"build-{version}", version, broken=broken)
        return _archive_of(source, self.tmp / f"navin-cli-{version}.tar.gz")

    def _installed_version(self) -> str:
        return (self.tree / "_internal" / "marker.txt").read_text()

    def test_swaps_the_tree_and_leaves_nothing_behind(self):
        result = service._install_cli(
            self._archive("1.0.1"), version="1.0.1", pid=os.getpid(), relaunch=False
        )
        self.assertFalse(result["deferred"])
        self.assertEqual(self._installed_version(), "1.0.1")
        self.assertEqual(sorted(p.name for p in self.install.iterdir()), ["navin-dist"])
        self.assertTrue(os.access(self.tree / "navin", os.X_OK))

    def test_a_tree_that_does_not_start_never_replaces_the_working_one(self):
        with self.assertRaises(service.UpdateError):
            service._install_cli(
                self._archive("1.0.1", broken=True),
                version="1.0.1",
                pid=os.getpid(),
                relaunch=False,
            )
        self.assertEqual(self._installed_version(), "1.0.0")
        self.assertEqual(sorted(p.name for p in self.install.iterdir()), ["navin-dist"])

    def test_a_tree_reporting_another_version_is_refused(self):
        # The archive says 1.0.1 but the binary inside answers 1.0.0: the
        # manifest and the payload disagree, and the payload does not win.
        with self.assertRaises(service.UpdateError):
            service._install_cli(
                self._archive("1.0.0"), version="1.0.1", pid=os.getpid(), relaunch=False
            )
        self.assertEqual(self._installed_version(), "1.0.0")

    def test_an_archive_escaping_its_folder_is_refused(self):
        archive = self.tmp / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(self.tree / "navin", arcname="navin-dist/navin")
            info = tarfile.TarInfo("navin-dist/../../evil.sh")
            payload = b"#!/bin/sh\n"
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        with self.assertRaises(service.UpdateError):
            service._install_cli(archive, version="1.0.1", pid=os.getpid(), relaunch=False)
        self.assertFalse((self.tmp / "evil.sh").exists())
        self.assertEqual(self._installed_version(), "1.0.0")

    def test_relaunch_is_handed_to_a_detached_shell(self):
        with mock.patch.object(service, "_relaunch_after_exit") as relaunch:
            service._install_cli(self._archive("1.0.1"), version="1.0.1", pid=4242, relaunch=True)
        relaunch.assert_called_once()
        argv, pid = relaunch.call_args.args
        self.assertEqual(argv[0], str(self.tree / "navin"))
        self.assertEqual(pid, 4242)


class WindowsHandOffTest(unittest.TestCase):
    """On Windows the swap waits for this process: the helper gets the whole job."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tree = self.tmp / "cli" / "navin-dist"
        self.tree.mkdir(parents=True)
        self.staged = self.tmp / "cli" / "navin-dist.new"
        self.staged.mkdir()
        helper = self.tmp / "helper" / "NavinUpdater.exe"
        helper.parent.mkdir()
        helper.write_bytes(b"MZ")
        self.enterContext(mock.patch.object(service, "_windows_helper", return_value=helper))
        self.enterContext(mock.patch.object(service.Path, "home", return_value=self.tmp / "home"))
        self.enterContext(mock.patch.object(service.sys, "platform", "win32"))

    def test_helper_runs_from_outside_the_tree_with_a_restart_line(self):
        with mock.patch.object(service.subprocess, "Popen") as popen:
            service._launch_windows_tree_updater(
                self.staged,
                self.tree,
                version="1.0.1",
                pid=777,
                restart=[str(self.tree / "navin.exe"), "gateway", "--port", "18790"],
            )
        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertTrue(command[0].endswith("NavinUpdater.exe"))
        self.assertNotIn(
            str(self.tree), command[0], "the helper must not run from the tree it renames"
        )
        self.assertTrue(
            (self.tmp / "home" / ".navin" / "updates" / "1.0.1" / "NavinUpdater.exe").is_file()
        )
        self.assertEqual(command[command.index("--mode") + 1], "tree")
        self.assertEqual(command[command.index("--wait-pid") + 1], "777")
        self.assertEqual(command[command.index("--artifact") + 1], str(self.staged))
        self.assertEqual(command[command.index("--target") + 1], str(self.tree))
        self.assertEqual(command[command.index("--restart-args") + 1], "gateway --port 18790")

    def test_navin_update_hands_off_without_a_restart(self):
        with mock.patch.object(service.subprocess, "Popen") as popen:
            service._launch_windows_tree_updater(
                self.staged, self.tree, version="1.0.1", pid=777, restart=None
            )
        command = popen.call_args.args[0]
        self.assertNotIn("--restart", command)
        self.assertNotIn("--restart-args", command)


class ApplyCliUpdateTest(unittest.TestCase):
    def test_refuses_a_desktop_install(self):
        with (
            mock.patch.object(
                service, "_STATE", {"path": __file__, "update": {"installKind": "windows-setup"}}
            ),
            mock.patch.object(service, "_install_kind", return_value="windows-setup"),
        ):
            with self.assertRaises(service.UpdateError):
                service.apply_cli_update()


class StartupNoticeTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(notice.Path, "home", return_value=self.home))

    def _available(self, kind: str = "cli") -> dict:
        return {
            "available": True,
            "supported": True,
            "installKind": kind,
            "latestVersion": "9.9.9",
            "currentVersion": notice.__version__,
        }

    def test_cli_install_is_told_the_command(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="cli"),
            mock.patch.object(service, "updates_configured", return_value=True),
            mock.patch.object(service, "check_for_update", return_value=self._available()) as check,
        ):
            text = notice.update_notice()
            again = notice.update_notice()
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn("9.9.9", text)
        self.assertIn("navin update", text)
        self.assertEqual(text, again)
        # The second call came from the daily cache, not the server.
        self.assertEqual(check.call_count, 1)
        cached = json.loads((self.home / ".navin" / "update-check.json").read_text())
        self.assertEqual(cached["info"]["latestVersion"], "9.9.9")

    def test_desktop_install_is_pointed_at_its_window(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="windows-setup"),
            mock.patch.object(service, "updates_configured", return_value=True),
            mock.patch.object(
                service, "check_for_update", return_value=self._available("windows-setup")
            ),
        ):
            text = notice.update_notice()
        assert text is not None
        self.assertIn("Settings > Updates", text)
        self.assertNotIn("navin update", text)

    def test_source_checkout_never_asks_the_server(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="source"),
            mock.patch.object(service, "check_for_update") as check,
        ):
            self.assertIsNone(notice.update_notice())
        check.assert_not_called()

    def test_a_server_error_is_silent(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="cli"),
            mock.patch.object(service, "updates_configured", return_value=True),
            mock.patch.object(
                service, "check_for_update", side_effect=service.UpdateError("down", status=503)
            ),
        ):
            self.assertIsNone(notice.update_notice())

    def test_up_to_date_says_nothing(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="cli"),
            mock.patch.object(service, "updates_configured", return_value=True),
            mock.patch.object(service, "check_for_update", return_value={"available": False}),
        ):
            self.assertIsNone(notice.update_notice())


class PublisherSignsTheCliArchivesTest(unittest.TestCase):
    def test_every_cli_key_the_app_asks_for_is_published(self):
        script = PUBLISHER.read_text(encoding="utf-8")
        for key in ("cli-linux-x64", "cli-macos-arm64", "cli-macos-x64", "cli-windows-x64"):
            self.assertIn(
                f'"{key}|', script, f"{key} missing from UPDATABLE in publish-os-to-s3.sh"
            )


if __name__ == "__main__":
    unittest.main()
