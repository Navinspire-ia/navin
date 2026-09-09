# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""On-demand toolchains have to follow the build that is installed.

Installing Navin replaces every file the installer owns, on all three desktop
apps: the NSIS/MSI setup on Windows, the .app bundle on macOS, the deb/rpm/pacman on
Linux. What no installer touches is ``~/.navin``, where Montage/HyperFrames and
the Android SDK are fetched on demand - and their setup is idempotent, so it
answered "already ready" forever and a machine kept the toolchain its very
first install pulled, whatever fix shipped since.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin import __version__, toolchains


class BuildStampTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_unstamped_toolchain_is_stale(self):
        self.assertIsNone(toolchains.installed_build(self.root))
        self.assertTrue(toolchains.is_stale(self.root))

    def test_what_this_build_installed_is_not_stale(self):
        toolchains.record_build(self.root)
        self.assertEqual(toolchains.installed_build(self.root), __version__)
        self.assertFalse(toolchains.is_stale(self.root))

    def test_another_build_is_stale(self):
        toolchains.record_build(self.root)
        with mock.patch.object(toolchains, "__version__", "9.9.9"):
            self.assertTrue(toolchains.is_stale(self.root))

    def test_nothing_installed_is_never_stale(self):
        """Nothing to refresh; the plain install path covers a first setup."""
        self.assertFalse(toolchains.is_stale(self.root, installed=False))

    def test_a_corrupt_stamp_reads_as_missing(self):
        toolchains.stamp_path(self.root).write_text("{not json", encoding="utf-8")
        self.assertIsNone(toolchains.installed_build(self.root))

    def test_an_unwritable_home_does_not_fail_the_install(self):
        with mock.patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            toolchains.record_build(self.root)  # must not raise


class MontageRefreshTest(unittest.TestCase):
    """`montage(action=setup)` must stop claiming "already ready" after an upgrade."""

    def setUp(self):
        from navin.montage import bootstrap

        self.bootstrap = bootstrap

    def _run_setup(self, *, stale: bool, install):
        with (
            mock.patch.object(
                self.bootstrap, "_installed_by_another_build", return_value=stale
            ),
            mock.patch.object(self.bootstrap, "install_hyperframes", install),
            mock.patch.object(self.bootstrap, "record_build") as record,
        ):
            result = asyncio.run(self.bootstrap.run_setup(package="hyperframes"))
        return result, record

    def test_a_toolchain_from_another_build_is_reinstalled(self):
        install = mock.AsyncMock(return_value={"ok": True, "path": "/hf"})
        result, record = self._run_setup(stale=True, install=install)
        self.assertTrue(result["ok"])
        self.assertTrue(install.call_args.kwargs["force"])
        record.assert_called_once()

    def test_a_toolchain_from_this_build_is_left_alone(self):
        install = mock.AsyncMock(return_value={"ok": True, "path": "/hf"})
        _, record = self._run_setup(stale=False, install=install)
        self.assertFalse(install.call_args.kwargs["force"])
        record.assert_called_once()

    def test_a_failed_install_is_not_stamped(self):
        install = mock.AsyncMock(return_value={"ok": False, "error": "npm_failed"})
        _, record = self._run_setup(stale=True, install=install)
        record.assert_not_called()


class MobileSdkStampTest(unittest.TestCase):
    """Only the SDK we manage is stamped; Android Studio's tree is not ours."""

    def setUp(self):
        from navin.mobile import bootstrap

        self.bootstrap = bootstrap
        self._tmp = tempfile.TemporaryDirectory()
        self.sdk = Path(self._tmp.name) / "android-platform-tools"
        self.sdk.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_our_sdk_is_stamped_and_then_current(self):
        with mock.patch.object(self.bootstrap, "navin_sdk_root", return_value=self.sdk):
            self.assertTrue(self.bootstrap.sdk_toolchain_stale(self.sdk))
            self.bootstrap.record_sdk_build(self.sdk)
            self.assertFalse(self.bootstrap.sdk_toolchain_stale(self.sdk))

    def test_an_android_studio_sdk_is_never_written_to(self):
        theirs = Path(self._tmp.name) / "Android" / "Sdk"
        theirs.mkdir(parents=True)
        with mock.patch.object(self.bootstrap, "navin_sdk_root", return_value=self.sdk):
            self.bootstrap.record_sdk_build(theirs)
            self.assertFalse(toolchains.stamp_path(theirs).exists())
            self.assertFalse(self.bootstrap.sdk_toolchain_stale(theirs))


if __name__ == "__main__":
    unittest.main()
