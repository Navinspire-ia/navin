"""Updating the application the user installed, not the sidecar inside it.

The gateway is a child process buried in the app: on Linux its own executable
sits on a read-only AppImage mount, on Windows it is an .exe next to a window
nobody would see restart. Every case here failed before the desktop shell
started naming what it installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from navin.update import service


def _manifest(version: str = "1.0.1", **artifacts: dict) -> dict:
    return {
        "schemaVersion": 1,
        "channel": "stable",
        "version": version,
        "minimumVersion": "",
        "rollout": 100,
        "notes": "",
        "artifacts": artifacts
        or {
            "linux-appimage-x64": {
                "url": f"v{version}/Navin-{version}-x86_64.AppImage",
                "size": 1024,
                "sha256": "a" * 64,
            }
        },
    }


class InstallKindTests(unittest.TestCase):
    """The shell's word is taken over what this process can see about itself."""

    def test_the_shell_names_the_kind(self):
        for kind in ("linux-appimage", "linux-package", "macos-app", "windows-setup"):
            with self.subTest(kind=kind):
                with mock.patch.dict(os.environ, {"NAVIN_INSTALL_KIND": kind}):
                    self.assertEqual(service._install_kind(), kind)

    def test_an_unknown_kind_is_ignored(self):
        with mock.patch.dict(os.environ, {"NAVIN_INSTALL_KIND": "linux-flatpak"}):
            os.environ.pop("APPIMAGE", None)
            # Falls through to detection rather than trusting a value no
            # installer path knows how to honour.
            self.assertNotEqual(service._install_kind(), "linux-flatpak")

    def test_an_appimage_env_names_the_kind_when_the_shell_forgot(self):
        with mock.patch.dict(
            os.environ,
            {"APPIMAGE": "/tmp/Navin.AppImage"},
            clear=False,
        ):
            os.environ.pop("NAVIN_INSTALL_KIND", None)
            self.assertEqual(service._install_kind(), "linux-appimage")

    def test_the_desktop_app_must_exist_to_count(self):
        with mock.patch.dict(os.environ, {"NAVIN_DESKTOP_APP": "/nope/Navin.AppImage"}):
            os.environ.pop("APPIMAGE", None)
            self.assertIsNone(service._desktop_app())

    def test_a_missing_desktop_pid_is_zero_not_a_crash(self):
        for raw in ("", "not-a-pid", "0", "1"):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"NAVIN_DESKTOP_PID": raw}):
                    self.assertEqual(service._desktop_pid(), 0)


class PackagedInstallTests(unittest.TestCase):
    """A .deb belongs to the package manager, and the app says so."""

    def test_a_package_hears_the_news_without_a_button(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="linux-package"),
        ):
            info = service._release_info(
                _manifest(), base_url="https://example.invalid", skipped_version=""
            )
        assert info is not None
        self.assertTrue(info["available"])
        self.assertFalse(info["supported"])
        self.assertIn("package manager", info["reason"])

    def test_installing_a_package_is_refused_clearly(self):
        with (
            mock.patch.object(service, "_install_kind", return_value="linux-package"),
            mock.patch.object(
                service,
                "_STATE",
                {"path": "/tmp/whatever", "update": {"installKind": "linux-package"}},
            ),
            mock.patch.object(Path, "is_file", return_value=True),
        ):
            with self.assertRaises(service.UpdateError) as caught:
                service.install_update()
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("package manager", str(caught.exception))


class AppImageInstallTests(unittest.TestCase):
    """The file swapped is the .AppImage, never the sidecar inside its mount."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_the_appimage_is_the_target(self):
        target = self.tmp / "Navin-1.0.0-x86_64.AppImage"
        target.write_bytes(b"old")
        artifact = self.tmp / "downloaded.AppImage"
        artifact.write_bytes(b"new")
        with (
            mock.patch.dict(os.environ, {"NAVIN_DESKTOP_APP": str(target)}),
            mock.patch.object(service.subprocess, "Popen") as popen,
        ):
            service._install_appimage(artifact, pid=1234, desktop_pid=99)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[:2], ["/bin/sh", "-c"])
        # label, gateway pid, window pid, artifact, target, backup
        self.assertEqual(argv[3:], ["navin-updater", "1234", "99", str(artifact), str(target), f"{target}.old"])

    def test_a_read_only_location_says_so_instead_of_failing_later(self):
        target = self.tmp / "Navin.AppImage"
        target.write_bytes(b"old")
        with (
            mock.patch.dict(os.environ, {"NAVIN_DESKTOP_APP": str(target)}),
            mock.patch.object(service.os, "access", return_value=False),
        ):
            with self.assertRaises(service.UpdateError) as caught:
                service._install_appimage(self.tmp / "new.AppImage", pid=1, desktop_pid=2)
        self.assertEqual(caught.exception.status, 409)

    def test_without_a_shell_there_is_nothing_to_replace(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIN_DESKTOP_APP", None)
            os.environ.pop("APPIMAGE", None)
            with self.assertRaises(service.UpdateError):
                service._install_appimage(self.tmp / "new.AppImage", pid=1, desktop_pid=2)


class InstallScriptTests(unittest.TestCase):
    """The swap scripts are run for real, because a quoting slip is invisible."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _run(self, target: Path, artifact: Path) -> subprocess.CompletedProcess:
        # A live pid would make the script wait for it, so it is handed one that
        # has already gone: the wait then falls straight through to the swap.
        finished = subprocess.Popen(["/bin/true"])
        finished.wait()
        return subprocess.run(
            [
                "/bin/sh",
                "-c",
                service._FILE_SWAP_INSTALL_SCRIPT,
                "navin-updater",
                str(finished.pid),
                "0",
                str(artifact),
                str(target),
                f"{target}.old",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_the_new_file_takes_the_place_of_the_old_one(self):
        target = self.tmp / "app"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        artifact = self.tmp / "new"
        # Stays alive past the twenty second watch so the swap is kept.
        artifact.write_text("#!/bin/sh\nsleep 40\n")
        artifact.chmod(0o755)

        result = self._run(target, artifact)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), artifact.read_text())
        self.assertFalse((self.tmp / "app.old").exists(), "backup should be cleared")

    def test_a_version_that_dies_at_once_is_rolled_back(self):
        target = self.tmp / "app"
        target.write_text("#!/bin/sh\nsleep 40\n")
        target.chmod(0o755)
        original = target.read_text()
        artifact = self.tmp / "broken"
        artifact.write_text("#!/bin/sh\nexit 1\n")
        artifact.chmod(0o755)

        result = self._run(target, artifact)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            target.read_text(),
            original,
            "an update that cannot start must leave the working version in place",
        )


class RestartConfirmationTests(unittest.TestCase):
    """What the user was promised, checked after the app comes back."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.home / ".navin").mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.object(Path, "home", return_value=self.home))

    def test_nothing_pending_says_nothing(self):
        self.assertIsNone(service.consume_completed_update())

    def test_a_successful_install_is_confirmed_once(self):
        with mock.patch.object(service, "__version__", "1.0.3"):
            service._record_pending_install("1.0.3")
            self.assertEqual(
                service.consume_completed_update(), {"version": "1.0.3", "ok": True}
            )
            # Reported once: the second start is not news.
            self.assertIsNone(service.consume_completed_update())

    def test_a_rollback_is_reported_rather_than_hidden(self):
        with mock.patch.object(service, "__version__", "1.0.2"):
            service._pending_path().write_text(
                json.dumps({"version": "1.0.3", "from": "1.0.2", "at": time.time()})
            )
            result = service.consume_completed_update()
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["currentVersion"], "1.0.2")

    def test_a_stale_note_is_dropped_silently(self):
        service._pending_path().write_text(
            json.dumps({"version": "9.9.9", "from": "1.0.0", "at": time.time() - 86_400})
        )
        self.assertIsNone(service.consume_completed_update())


class EndToEndAppImageTests(unittest.TestCase):
    """The whole journey a Linux desktop user takes, over a real HTTP server.

    Each piece is covered above; this is here because the pieces used to fit
    together only on paper - the manifest named a path the store did not have,
    and the installer aimed at a file inside a read-only mount.
    """

    def test_notice_download_and_swap(self):
        import base64
        import hashlib
        import threading
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "stable").mkdir()
            (root / "v9.9.9").mkdir()
            published = root / "v9.9.9" / "Navin-9.9.9-x86_64.AppImage"
            published.write_bytes(b"#!/bin/sh\nsleep 40\n")

            private = Ed25519PrivateKey.generate()
            public = base64.b64encode(
                private.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            ).decode()

            class Quiet(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(root), **kwargs)

                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            base_url = f"http://127.0.0.1:{server.server_port}"

            manifest = {
                "schemaVersion": 1,
                "channel": "stable",
                "version": "9.9.9",
                "minimumVersion": "",
                "notes": "",
                "rollout": 100,
                "artifacts": {
                    "linux-appimage-x64": {
                        "url": "v9.9.9/Navin-9.9.9-x86_64.AppImage",
                        "filename": "Navin-9.9.9-x86_64.AppImage",
                        "size": published.stat().st_size,
                        "sha256": hashlib.sha256(published.read_bytes()).hexdigest(),
                    }
                },
            }
            body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            (root / "stable" / "manifest.json").write_bytes(body)
            (root / "stable" / "manifest.sig").write_text(
                base64.b64encode(private.sign(body)).decode()
            )

            installed = root / "Applications" / "Navin-1.0.0-x86_64.AppImage"
            installed.parent.mkdir()
            installed.write_bytes(b"old")

            env = {
                "NAVIN_UPDATE_PUBLIC_KEY": public,
                "NAVIN_UPDATE_ALLOW_HTTP": "1",
                "NAVIN_UPDATE_BASE_URL": base_url,
                "NAVIN_INSTALL_KIND": "linux-appimage",
                "NAVIN_DESKTOP_APP": str(installed),
                "NAVIN_DESKTOP_PID": "4242",
                "HOME": str(root / "home"),
            }
            spawned: list[list[str]] = []
            with (
                mock.patch.dict(os.environ, env),
                mock.patch.object(service, "_CACHE", (0.0, None)),
                mock.patch.object(service, "_STATE", dict(service._STATE)),
                mock.patch.object(Path, "home", return_value=root / "home"),
                mock.patch.object(
                    service.subprocess,
                    "Popen",
                    lambda command, **_: spawned.append(command) or mock.Mock(),
                ),
                mock.patch.object(service, "_schedule_shutdown"),
            ):
                info = service.check_for_update(force=True)
                self.assertTrue(info["available"], info)
                self.assertTrue(info["supported"])

                downloaded = service.download_update()
                self.assertEqual(
                    Path(downloaded["path"]).read_bytes(), published.read_bytes()
                )

                result = service.install_update()
                self.assertEqual(result["state"], "restarting")

            # label, gateway pid, window pid, artifact, target, backup.
            argv = spawned[0]
            # The .AppImage the user launched is the target, and the window is
            # closed with it. Aiming at sys.executable is what used to make this
            # impossible: it lives on a read-only mount.
            self.assertEqual(argv[5], "4242")
            self.assertEqual(argv[7], str(installed))
            self.assertEqual(argv[8], f"{installed}.old")
            self.assertIn("update-pending", os.listdir(root / "home" / ".navin"))


class WindowsPathTests(unittest.TestCase):
    """The dialog on Windows was Path.GetFullPath rejecting ``?`` in ``\\\\?\\``."""

    def test_the_extended_prefix_is_dropped(self):
        self.assertEqual(
            service._strip_windows_extended_prefix(r"\\?\C:\Users\me\Navin.exe"),
            r"C:\Users\me\Navin.exe",
        )
        self.assertEqual(
            service._strip_windows_extended_prefix(r"\\?\UNC\server\share\Navin.exe"),
            r"\\server\share\Navin.exe",
        )
        self.assertEqual(
            service._os_path(r"\\?\C:\Users\me\Navin.exe"),
            r"C:\Users\me\Navin.exe",
        )
        self.assertEqual(service._os_path("/opt/Navin.AppImage"), "/opt/Navin.AppImage")

    def test_the_desktop_app_is_found_behind_the_prefix(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = tmp / "Navin.exe"
        target.write_bytes(b"x")
        with mock.patch.dict(
            os.environ,
            {"NAVIN_DESKTOP_APP": "\\\\?\\" + str(target)},
        ):
            os.environ.pop("APPIMAGE", None)
            self.assertEqual(service._desktop_app(), target)


class WindowsInstallArgvTests(unittest.TestCase):
    """NavinUpdater.exe must never see a ``\\\\?\\`` path, and must outlive the job."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.tmp / "home" / ".navin").mkdir(parents=True)

    def test_extended_prefix_is_stripped_before_the_helper_sees_it(self):
        helper = self.tmp / "NavinUpdater.exe"
        helper.write_bytes(b"x")
        target = self.tmp / "Navin.exe"
        target.write_bytes(b"y")
        artifact = self.tmp / "setup.exe"
        artifact.write_bytes(b"z")
        exe = self.tmp / "navin.exe"
        exe.write_bytes(b"w")
        with (
            mock.patch.dict(
                os.environ,
                {
                    "NAVIN_DESKTOP_APP": "\\\\?\\" + str(target),
                    "NAVIN_DESKTOP_PID": "99",
                },
            ),
            mock.patch.object(service, "_install_kind", return_value="windows-setup"),
            mock.patch.object(sys, "executable", str(exe)),
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(
                service,
                "_STATE",
                {
                    "path": str(artifact),
                    "update": {
                        "installKind": "windows-setup",
                        "latestVersion": "1.3.0",
                    },
                },
            ),
            mock.patch.object(Path, "home", return_value=self.tmp / "home"),
            mock.patch.object(service.subprocess, "Popen") as popen,
            mock.patch.object(service, "_schedule_shutdown"),
            mock.patch.object(service, "notify"),
        ):
            os.environ.pop("APPIMAGE", None)
            service.install_update()
        argv = popen.call_args.args[0]
        for part in argv:
            self.assertFalse(str(part).startswith("\\\\?\\"), part)
        self.assertEqual(argv[argv.index("--target") + 1], str(target))
        self.assertEqual(argv[argv.index("--restart") + 1], str(target))
        flags = popen.call_args.kwargs.get("creationflags", 0)
        self.assertTrue(flags & 0x01000000, "CREATE_BREAKAWAY_FROM_JOB so the helper survives the window")


class MacosInstallScriptTests(unittest.TestCase):
    def test_the_image_is_mounted_at_a_known_path(self):
        script = service._MACOS_INSTALL_SCRIPT
        self.assertIn("-mountpoint", script)
        self.assertIn('find "$mnt"', script)
        self.assertNotIn("grep -o '/Volumes/", script)


if __name__ == "__main__":
    unittest.main()
