# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skill install must run git / npm with host libraries (issue #15)."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.plugins.manager import PluginError, PluginManager
from navin.process_runtime import child_environment
from navin.utils import host_env
from navin.utils.host_env import failure_detail, host_tool_env, sanitize_process_environment

_BUNDLE = "/usr/lib/Navin/navin-dist/_internal"
_MOUNT = "/tmp/.mount_NavinAb12"


@contextmanager
def frozen_bundle(meipass: str = _BUNDLE, platform: str = "linux"):
    """This process as the packaged navin whose bundle sits at ``meipass``."""
    with (
        patch.object(host_env.sys, "frozen", True, create=True),
        patch.object(host_env.sys, "_MEIPASS", meipass, create=True),
        patch.object(host_env.sys, "executable", str(Path(meipass).parent / "navin")),
        patch.object(host_env.sys, "platform", platform),
    ):
        yield


class HostToolEnvTests(unittest.TestCase):
    def test_strips_bundle_keeps_user_entries(self) -> None:
        env = host_tool_env(
            {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/custom/lib", "PATH": "/usr/bin"},
            GIT_TERMINAL_PROMPT="0",
        )
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/custom/lib")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_drops_key_when_only_bundle(self) -> None:
        env = host_tool_env({"LD_LIBRARY_PATH": _BUNDLE, "DYLD_LIBRARY_PATH": _BUNDLE})
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertNotIn("DYLD_LIBRARY_PATH", env)

    def test_restores_bootloader_original(self) -> None:
        env = host_tool_env({"LD_LIBRARY_PATH": _BUNDLE, "LD_LIBRARY_PATH_ORIG": "/home/u/lib"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/home/u/lib")

    def test_unrelated_internal_path_is_kept(self) -> None:
        env = host_tool_env({"LD_LIBRARY_PATH": "/opt/my_internal/lib"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/my_internal/lib")

    def test_failure_detail_keeps_first_error(self) -> None:
        raw = "git-remote-https: version `OPENSSL_3.2.0' not found (required by /usr/lib/libcurl.so.4)\n"
        raw += "x" * 1000 + "\nfatal: remote helper 'https' aborted session"
        detail = failure_detail(raw)
        self.assertTrue(detail.startswith("git-remote-https: version"))
        self.assertTrue(detail.endswith("aborted session"))
        self.assertLessEqual(len(detail), 410)


class SanitizeProcessEnvironmentTests(unittest.TestCase):
    """The packaged navin cleans its environment once, before spawning anything."""

    def test_a_source_install_is_left_alone(self) -> None:
        env = {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/u"}
        with patch.object(host_env.sys, "frozen", False, create=True):
            self.assertEqual(sanitize_process_environment(env), {})
        self.assertEqual(env, {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/u"})

    def test_the_bootloader_entry_goes_and_the_users_stays(self) -> None:
        # What the bootloader leaves when the user had a loader path of their own.
        env = {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/u", "LD_LIBRARY_PATH_ORIG": "/opt/u", "PATH": "/usr/bin"}
        with frozen_bundle():
            changes = sanitize_process_environment(env)
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/u")
        self.assertEqual(changes["LD_LIBRARY_PATH"], "/opt/u")
        # Kept, so a child navin still restores the user's value.
        self.assertEqual(env["LD_LIBRARY_PATH_ORIG"], "/opt/u")
        with patch("navin.process_runtime.sys.frozen", True, create=True):
            self.assertEqual(child_environment(dict(env))["LD_LIBRARY_PATH"], "/opt/u")

    def test_a_loader_path_only_the_bootloader_set_is_removed(self) -> None:
        env = {"LD_LIBRARY_PATH": _BUNDLE, "PATH": "/usr/bin"}
        with frozen_bundle():
            sanitize_process_environment(env)
        self.assertEqual(env, {"PATH": "/usr/bin"})

    def test_any_bundle_location_is_recognized(self) -> None:
        # The CLI tarball unpacks wherever the user wants, not under navin-dist.
        where = "/home/u/apps/navin-cli-2.4.0/_internal"
        env = {"LD_LIBRARY_PATH": f"{where}:{where}/lib"}
        with frozen_bundle(where):
            sanitize_process_environment(env)
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_appimage_mount_paths_leave_and_host_paths_stay(self) -> None:
        env = {
            "APPDIR": _MOUNT,
            "APPIMAGE": "/home/u/Navin.AppImage",
            "XDG_DATA_DIRS": f"{_MOUNT}/usr/share:/usr/share:/usr/local/share",
            "GTK_PATH": f"{_MOUNT}//usr/lib/x86_64-linux-gnu/gtk-3.0:/usr/lib64/gtk-3.0",
            "GIO_EXTRA_MODULES": f"{_MOUNT}/usr/lib/x86_64-linux-gnu/gio/modules",
            "GDK_PIXBUF_MODULE_FILE": f"{_MOUNT}//usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/2.10.0/loaders.cache",
            "GST_PLUGIN_PATH_1_0": f"{_MOUNT}/usr/lib/gstreamer-1.0",
        }
        with frozen_bundle(f"{_MOUNT}/usr/lib/Navin/navin-dist/_internal"):
            sanitize_process_environment(env)
        self.assertEqual(env["XDG_DATA_DIRS"], "/usr/share:/usr/local/share")
        self.assertEqual(env["GTK_PATH"], "/usr/lib64/gtk-3.0")
        for key in ("GIO_EXTRA_MODULES", "GDK_PIXBUF_MODULE_FILE", "GST_PLUGIN_PATH_1_0"):
            self.assertNotIn(key, env)
        # The AppImage's own identity is not a library path: self-update reads it.
        self.assertEqual(env["APPDIR"], _MOUNT)
        self.assertEqual(env["APPIMAGE"], "/home/u/Navin.AppImage")

    def test_gtk_variables_outside_an_appimage_are_the_users(self) -> None:
        env = {"GTK_PATH": "/opt/gtk", "XDG_DATA_DIRS": "/usr/share"}
        with frozen_bundle():
            sanitize_process_environment(env)
        self.assertEqual(env, {"GTK_PATH": "/opt/gtk", "XDG_DATA_DIRS": "/usr/share"})

    def _verify_paths(self, cafile: str, capath: str):
        return patch.object(
            host_env.ssl,
            "get_default_verify_paths",
            return_value=host_env.ssl.DefaultVerifyPaths("", "", "SSL_CERT_FILE", cafile, "SSL_CERT_DIR", capath),
        )

    def test_the_hosts_ca_bundle_is_used_when_the_built_in_place_is_missing(self) -> None:
        # Arch and Fedora have no /usr/lib/ssl: TLS on the default context failed.
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "ca-certificates.crt"
            bundle.write_text("-----BEGIN CERTIFICATE-----\n")
            env: dict[str, str] = {}
            with (
                frozen_bundle(),
                self._verify_paths(f"{tmp}/missing/cert.pem", f"{tmp}/missing/certs"),
                patch.object(host_env, "_HOST_CA_FILES", (f"{tmp}/none.pem", str(bundle))),
            ):
                changes = sanitize_process_environment(env)
        self.assertEqual(env, {"SSL_CERT_FILE": str(bundle)})
        self.assertEqual(changes, {"SSL_CERT_FILE": str(bundle)})

    def test_a_working_or_chosen_ca_setup_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "ca.pem"
            bundle.write_text("x")
            candidates = patch.object(host_env, "_HOST_CA_FILES", (str(bundle),))
            # The built-in place exists (Debian, Ubuntu).
            env: dict[str, str] = {}
            with frozen_bundle(), self._verify_paths(str(bundle), f"{tmp}/certs"), candidates:
                sanitize_process_environment(env)
            self.assertEqual(env, {})
            # The user chose a bundle.
            env = {"SSL_CERT_DIR": "/etc/corp/certs"}
            with frozen_bundle(), self._verify_paths(f"{tmp}/x", f"{tmp}/y"), candidates:
                sanitize_process_environment(env)
            self.assertEqual(env, {"SSL_CERT_DIR": "/etc/corp/certs"})
            # Not a Linux build: its OpenSSL finds the system store itself.
            env = {}
            with frozen_bundle(platform="darwin"), self._verify_paths(f"{tmp}/x", f"{tmp}/y"), candidates:
                sanitize_process_environment(env)
            self.assertEqual(env, {})

    def test_a_macos_runtime_hook_path_into_the_bundle_goes(self) -> None:
        frameworks = "/Applications/Navin.app/Contents/Frameworks"
        env = {"DYLD_LIBRARY_PATH": f"{frameworks}:/opt/homebrew/lib"}
        with frozen_bundle(frameworks, platform="darwin"):
            sanitize_process_environment(env)
        self.assertEqual(env, {"DYLD_LIBRARY_PATH": "/opt/homebrew/lib"})

    def test_windows_keeps_the_bundle_for_itself_then_drops_the_inherited_directory(self) -> None:
        calls: list[tuple[str, object]] = []

        def set_dll_directory(value: object) -> int:
            calls.append(("SetDllDirectoryW", value))
            return 1

        # ctypes exposes the Win32 entry point under its own name, as an attribute.
        kernel32 = SimpleNamespace(SetDllDirectoryW=set_dll_directory)

        bundle = r"C:\Program Files\Navin\navin-dist\_internal"
        with (
            frozen_bundle(bundle, platform="win32"),
            patch.object(host_env.os, "add_dll_directory", lambda path: calls.append(("add", path)) or "handle", create=True),
            patch("ctypes.WinDLL", lambda *a, **k: kernel32, create=True),
            patch.object(host_env, "_BUNDLE_DLL_DIRECTORY", None),
        ):
            changes = sanitize_process_environment()
            self.assertEqual(host_env._BUNDLE_DLL_DIRECTORY, "handle")
        # Order matters: the bundle stays reachable before the inherited path goes.
        self.assertEqual(calls, [("add", bundle), ("SetDllDirectoryW", None)])
        self.assertIn("DLL directory", changes)

    def test_windows_keeps_the_directory_when_the_bundle_cannot_be_re_added(self) -> None:
        calls: list[str] = []

        def refuse(path: str) -> None:
            raise OSError("access denied")

        with (
            frozen_bundle(r"C:\Navin\_internal", platform="win32"),
            patch.object(host_env.os, "add_dll_directory", refuse, create=True),
            patch("ctypes.WinDLL", lambda *a, **k: calls.append("WinDLL"), create=True),
            patch.object(host_env, "_BUNDLE_DLL_DIRECTORY", None),
        ):
            changes = sanitize_process_environment()
        self.assertEqual(calls, [])
        self.assertNotIn("DLL directory", changes)


class AgentGitToolEnvTests(unittest.TestCase):
    def test_the_agents_git_runs_without_bundle_libs(self) -> None:
        from navin.agent.tools import git as git_tool

        with patch.dict("os.environ", {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/custom/lib"}):
            env = git_tool._git_env()
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/custom/lib")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_EDITOR"], "true")


class SkillInstallEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = PluginManager(root=Path(self._tmp.name) / "plugins")
        patcher = patch.dict("os.environ", {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/custom/lib"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_git_clone_runs_without_bundle_libs(self) -> None:
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)

            class Result:
                returncode = 128
                stdout = ""
                stderr = "fatal: remote helper 'https' aborted session"

            return Result()

        with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
            with self.assertRaises(PluginError):
                self.manager.install_from_git("https://github.com/acme/skill.git")
        self.assertEqual(seen["env"]["LD_LIBRARY_PATH"], "/opt/custom/lib")
        self.assertEqual(seen["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_npm_pack_runs_without_bundle_libs(self) -> None:
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)

            class Result:
                returncode = 1
                stdout = ""
                stderr = "npm ERR! 404"

            return Result()

        with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
            with self.assertRaises(PluginError):
                self.manager.install_from_npx("@acme/skill")
        self.assertEqual(seen["env"]["LD_LIBRARY_PATH"], "/opt/custom/lib")


if __name__ == "__main__":
    unittest.main()
