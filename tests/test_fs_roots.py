# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The project picker's quick roots follow the host the gateway runs on."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.utils import wsl as wsl_module
from navin.webui import fs_browse


def _roots(env: str, **patches: object) -> list[dict[str, str]]:
    stack: list[mock._patch] = [  # type: ignore[type-arg]
        mock.patch.object(fs_browse, "host_environment", return_value=env),
        mock.patch.object(fs_browse.Path, "is_dir", return_value=True),
    ]
    for name, value in patches.items():
        stack.append(mock.patch.object(fs_browse, name, return_value=value))
    for patch in stack:
        patch.start()
    try:
        return fs_browse.fs_roots_payload()["roots"]
    finally:
        for patch in reversed(stack):
            patch.stop()


def _kinds(roots: list[dict[str, str]]) -> list[str]:
    return [root["kind"] for root in roots]


class WslHostRootsTest(unittest.TestCase):
    def test_offers_the_windows_profile_and_drives_but_no_distribution(self) -> None:
        roots = _roots(
            "wsl",
            _windows_home_mount=Path("/mnt/c/Users/me"),
            _windows_drive_mounts=[Path("/mnt/c"), Path("/mnt/d")],
        )
        self.assertEqual(_kinds(roots), ["home", "windows-home", "windows", "windows", "system"])
        windows_home = roots[1]
        self.assertEqual(windows_home["path"], "/mnt/c/Users/me")
        self.assertEqual(windows_home["name"], "me")
        drive_c, drive_d = roots[2], roots[3]
        self.assertEqual((drive_c["label"], drive_c["name"]), ("Windows (C:)", "C"))
        self.assertEqual((drive_d["label"], drive_d["name"]), ("Windows (D:)", "D"))
        self.assertEqual(roots[-1]["path"], "/")

    def test_without_a_reachable_windows_profile_the_drives_still_come(self) -> None:
        roots = _roots(
            "wsl",
            _windows_home_mount=None,
            _windows_drive_mounts=[Path("/mnt/c")],
        )
        self.assertEqual(_kinds(roots), ["home", "windows", "system"])

    def test_the_windows_profile_is_asked_once_then_remembered(self) -> None:
        fs_browse._windows_home_cache = None
        self.addCleanup(setattr, fs_browse, "_windows_home_cache", None)
        with mock.patch.object(wsl_module, "windows_env", return_value=r"C:\Users\me") as ask:
            first = fs_browse._windows_home_mount()
            second = fs_browse._windows_home_mount()
        self.assertEqual(first, Path("/mnt/c/Users/me"))
        self.assertEqual(second, first)
        ask.assert_called_once_with("USERPROFILE")

    def test_no_answer_from_windows_is_remembered_too(self) -> None:
        fs_browse._windows_home_cache = None
        self.addCleanup(setattr, fs_browse, "_windows_home_cache", None)
        with mock.patch.object(wsl_module, "windows_env", return_value=None) as ask:
            self.assertIsNone(fs_browse._windows_home_mount())
            self.assertIsNone(fs_browse._windows_home_mount())
        ask.assert_called_once()


class WindowsHostRootsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(setattr, wsl_module, "_distros_cache", None)
        wsl_module._distros_cache = ["Ubuntu"]
        wsl_module._distros_cached_at = float("inf")

    def test_offers_each_disk_and_distribution_and_no_bare_slash(self) -> None:
        roots = _roots("windows", _windows_drive_letters=["C", "D"])
        self.assertEqual(_kinds(roots), ["home", "disk", "disk", "wsl"])
        disk_c = roots[1]
        self.assertEqual(
            (disk_c["label"], disk_c["name"], disk_c["path"]), ("Disk (C:)", "C", "C:\\")
        )
        self.assertEqual(roots[-1]["name"], "Ubuntu")
        self.assertNotIn("/", [root["path"] for root in roots])

    def test_never_shows_wsl_mounts_or_volumes(self) -> None:
        roots = _roots("windows", _windows_drive_letters=["C"])
        self.assertFalse({"windows", "windows-home", "volume", "drive"} & set(_kinds(roots)))


class MacHostRootsTest(unittest.TestCase):
    def test_offers_each_volume_but_nothing_from_windows_or_wsl(self) -> None:
        roots = _roots(
            "macos",
            _macos_volumes=[Path("/Volumes/Backup"), Path("/Volumes/Data")],
        )
        self.assertEqual(_kinds(roots), ["home", "volume", "volume", "system"])
        self.assertEqual(
            [(root["label"], root["name"]) for root in roots[1:3]],
            [("Backup", "Backup"), ("Data", "Data")],
        )

    def test_the_boot_volume_is_not_listed_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            volumes = Path(tmp)
            (volumes / "Macintosh HD").symlink_to("/")
            (volumes / "Backup").mkdir()
            (volumes / ".timemachine").mkdir()
            with mock.patch.object(fs_browse, "_MACOS_VOLUMES", volumes):
                found = fs_browse._macos_volumes()
        self.assertEqual([path.name for path in found], ["Backup"])


class LinuxHostRootsTest(unittest.TestCase):
    def test_offers_mounted_drives_and_skips_empty_mount_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            media = base / "media"
            run_media = base / "run" / "media"
            mnt = base / "mnt"
            (media / "me" / "USB").mkdir(parents=True)
            (media / "me" / "USB" / "photos").mkdir()
            (run_media / "me" / "Backup").mkdir(parents=True)
            (run_media / "me" / "Backup" / "2026").mkdir()
            (mnt / "data").mkdir(parents=True)
            (mnt / "data" / "projects").mkdir()
            (mnt / "unplugged").mkdir()
            (mnt / ".hidden").mkdir()
            with (
                mock.patch.object(fs_browse, "_LINUX_MEDIA_BASES", (media, run_media)),
                mock.patch.object(fs_browse, "_LINUX_MNT", mnt),
                mock.patch.object(fs_browse.getpass, "getuser", return_value="me"),
            ):
                found = fs_browse._linux_mounted_drives()
        self.assertEqual([path.name for path in found], ["USB", "Backup", "data"])

    def test_the_payload_has_no_windows_or_wsl_entries(self) -> None:
        roots = _roots("linux", _linux_mounted_drives=[Path("/mnt/data")])
        self.assertEqual(_kinds(roots), ["home", "drive", "system"])
        self.assertEqual((roots[1]["label"], roots[1]["name"]), ("data", "data"))


class ProjectsRootTest(unittest.TestCase):
    def test_the_default_workspace_leads_and_internal_storage_is_hidden(self) -> None:
        with mock.patch.object(fs_browse, "host_environment", return_value="linux"):
            with mock.patch.object(fs_browse.Path, "is_dir", return_value=True):
                with mock.patch.object(fs_browse, "_linux_mounted_drives", return_value=[]):
                    listed = fs_browse.fs_roots_payload(
                        default_project_path="/home/me/NavinProjects"
                    )["roots"]
                    hidden = fs_browse.fs_roots_payload(
                        default_project_path=str(Path.home() / ".navin" / "workspace")
                    )["roots"]
        self.assertEqual(
            listed[0], {"kind": "workspace", "label": "Projects", "path": "/home/me/NavinProjects"}
        )
        self.assertEqual(_kinds(hidden), ["home", "system"])


if __name__ == "__main__":
    unittest.main()
