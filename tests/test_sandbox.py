# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the shell exec sandbox backends (navin.agent.tools.sandbox).

The bubblewrap backend only builds a command line, so it is checked by shape.
The native ``landlock`` backend is checked end to end when its binary is
present and the kernel enforces Landlock: a command may write inside the
workspace and read the system, but a write outside the workspace is refused.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools import sandbox


def _bwrap_installed():
    """Pretend bubblewrap is on PATH and the host is Linux.

    The command line is built the same way either way; these tests are about
    its shape, not about what this particular machine has installed.
    """
    real_which = shutil.which

    def which(name: str, *args, **kwargs):
        if name == "bwrap":
            return "/usr/bin/bwrap"
        return real_which(name, *args, **kwargs)

    stack = ExitStack()
    stack.enter_context(patch.object(sandbox.sys, "platform", "linux"))
    stack.enter_context(patch.object(sandbox.shutil, "which", side_effect=which))
    return stack


class WrapCommandDispatchTest(unittest.TestCase):
    def test_an_unknown_backend_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sandbox.wrap_command("nope", "echo hi", "/ws", "/ws")

    def test_bwrap_builds_a_bwrap_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as ws, _bwrap_installed():
            wrapped = sandbox.wrap_command("bwrap", "echo hi", ws, ws)
            self.assertTrue(wrapped.startswith("bwrap "))
            self.assertIn("--ro-bind", wrapped)
            self.assertIn("echo hi", wrapped)

    def test_bwrap_without_the_binary_says_so_instead_of_failing_every_command(self) -> None:
        """A missing bwrap used to surface as 'command not found' on each exec.

        Raising here lets exec fall back to an unsandboxed run (with a warning)
        outside the strict profile, instead of every command dying silently.
        """
        with tempfile.TemporaryDirectory() as ws, patch.object(
            sandbox.shutil, "which", return_value=None
        ), self.assertRaises(ValueError) as ctx:
            sandbox.wrap_command("bwrap", "echo hi", ws, ws)
        self.assertIn("bwrap", str(ctx.exception))

    def test_bwrap_is_linux_only(self) -> None:
        with tempfile.TemporaryDirectory() as ws, patch.object(
            sandbox.sys, "platform", "darwin"
        ), self.assertRaises(ValueError):
            sandbox.wrap_command("bwrap", "echo hi", ws, ws)

    def test_native_is_an_alias_for_the_os_sandbox(self) -> None:
        if sandbox.native_sandbox_binary() is None:
            self.skipTest("navin-sandbox binary not built")
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(
                sandbox.wrap_command("native", "echo hi", ws, ws),
                sandbox.wrap_command("landlock", "echo hi", ws, ws),
            )


class LandlockWrappingTest(unittest.TestCase):
    def test_the_wrapper_names_the_binary_workspace_and_shell(self) -> None:
        binary = sandbox.native_sandbox_binary()
        if binary is None:
            self.skipTest("navin-sandbox binary not built")
        with tempfile.TemporaryDirectory() as ws:
            wrapped = sandbox.wrap_command("landlock", "echo hi", ws, ws)
            self.assertIn("navin-sandbox", wrapped)
            self.assertIn("--workspace", wrapped)
            self.assertIn(str(Path(ws).resolve()), wrapped)
            # Ends by handing the command to a shell, like the real spawn does.
            self.assertRegex(wrapped, r"(bash|sh) -c ")

    def test_a_missing_binary_runs_unconfined_unless_strict(self) -> None:
        with (
            patch.object(sandbox, "native_sandbox_binary", return_value=None),
            patch.object(sandbox, "_compile_native_sandbox", return_value=None),
        ):
            sandbox._MISSING_SANDBOX_WARNED = False
            with tempfile.TemporaryDirectory() as ws:
                wrapped = sandbox.wrap_command("landlock", "echo hi", ws, ws)
            self.assertEqual(wrapped, "echo hi")
            with self.assertRaises(ValueError) as caught:
                sandbox.wrap_command(
                    "landlock", "echo hi", "/ws", "/ws", strict=True,
                )
            self.assertIn("navin-sandbox", str(caught.exception))
            sandbox._MISSING_SANDBOX_WARNED = False

    def test_an_unrunnable_binary_runs_unconfined_unless_strict(self) -> None:
        sandbox._RUNNABLE_CACHE.clear()
        sandbox._UNRUNNABLE_WARNED = False
        with tempfile.TemporaryDirectory() as tmp:
            garbage = Path(tmp) / "navin-sandbox"
            garbage.write_bytes(b"not an executable\n")
            garbage.chmod(0o755)
            with patch.object(sandbox, "ensure_native_sandbox", return_value=str(garbage)):
                with tempfile.TemporaryDirectory() as ws:
                    wrapped = sandbox.wrap_command("landlock", "echo hi", ws, ws)
                self.assertEqual(wrapped, "echo hi")
                with self.assertRaises(ValueError) as caught:
                    sandbox.wrap_command(
                        "landlock", "echo hi", "/ws", "/ws", strict=True,
                    )
                self.assertIn("cannot run", str(caught.exception))
        sandbox._RUNNABLE_CACHE.clear()
        sandbox._UNRUNNABLE_WARNED = False

    def test_a_helper_that_exits_125_runs_unconfined(self) -> None:
        sandbox._RUNNABLE_CACHE.clear()
        sandbox._UNRUNNABLE_WARNED = False
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "navin-sandbox"
            stub.write_text("#!/bin/sh\nexit 125\n", encoding="utf-8")
            stub.chmod(0o755)
            with patch.object(sandbox, "ensure_native_sandbox", return_value=str(stub)):
                with tempfile.TemporaryDirectory() as ws:
                    wrapped = sandbox.wrap_command("landlock", "echo hi", ws, ws)
            self.assertEqual(wrapped, "echo hi")
        sandbox._RUNNABLE_CACHE.clear()
        sandbox._UNRUNNABLE_WARNED = False

    def test_frozen_tree_is_searched_for_the_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "navin" / "resources" / "bin" / "navin-sandbox"
            fake.parent.mkdir(parents=True)
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            fake.chmod(0o755)
            with (
                patch.dict(os.environ, {"NAVIN_SANDBOX_BIN": ""}, clear=False),
                patch.object(sandbox, "_runtime_sandbox_path", return_value=None),
                patch.object(sandbox.sys, "_MEIPASS", tmp, create=True),
            ):
                located = sandbox.native_sandbox_binary()
            self.assertEqual(located, str(fake))

    def test_ensure_compiles_when_the_locator_is_empty(self) -> None:
        with (
            patch.object(sandbox, "_IS_WINDOWS", False),
            patch.object(sandbox, "native_sandbox_binary", side_effect=[None, None, "/opt/bin/navin-sandbox"]),
            patch.object(
                sandbox, "_compile_native_sandbox", return_value="/opt/bin/navin-sandbox",
            ) as compile_fn,
        ):
            located = sandbox.ensure_native_sandbox()
        self.assertEqual(located, "/opt/bin/navin-sandbox")
        compile_fn.assert_called_once()

    def test_ensure_required_raises_when_compile_cannot_run(self) -> None:
        with (
            patch.object(sandbox, "_IS_WINDOWS", False),
            patch.object(sandbox, "native_sandbox_binary", return_value=None),
            patch.object(sandbox, "_compile_native_sandbox", return_value=None),
            patch.object(sandbox, "sandbox_crate_dir", return_value=None),
        ):
            with self.assertRaises(RuntimeError) as caught:
                sandbox.ensure_native_sandbox(required=True)
        self.assertIn("navin-sandbox is missing", str(caught.exception))

    def test_compile_copies_the_release_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crate = Path(tmp) / "navin-sandbox"
            crate.mkdir()
            (crate / "Cargo.toml").write_text("[package]\nname='navin-sandbox'\n", encoding="utf-8")
            built = crate / "target" / "release" / "navin-sandbox"
            built.parent.mkdir(parents=True)
            built.write_text("#!/bin/sh\n", encoding="utf-8")
            built.chmod(0o755)
            dest = Path(tmp) / "pkg" / "resources" / "bin" / "navin-sandbox"

            def fake_run(*_args, **_kwargs):
                return subprocess.CompletedProcess(
                    args=["cargo"], returncode=0, stdout="", stderr="",
                )

            with (
                patch.object(sandbox, "_IS_WINDOWS", False),
                patch.object(sandbox, "sandbox_crate_dir", return_value=crate),
                patch.object(sandbox, "staged_sandbox_path", return_value=dest),
                patch.object(sandbox.shutil, "which", return_value="/usr/bin/cargo"),
                patch.object(sandbox.subprocess, "run", side_effect=fake_run),
            ):
                located = sandbox._compile_native_sandbox()
            self.assertEqual(located, str(dest))
            self.assertTrue(dest.is_file())
            self.assertTrue(os.access(dest, os.X_OK))


class StrictModeTest(unittest.TestCase):
    """The strict security profile turns the native sandbox fail-closed.

    The binary already refuses to run unconfined under --strict; what these
    pin is that the Python wrapper actually passes the flag when asked, and
    keeps the historical warn-and-run default otherwise.
    """

    def setUp(self) -> None:
        self._original = sandbox.native_sandbox_binary
        sandbox.native_sandbox_binary = lambda: "/opt/bin/navin-sandbox"  # type: ignore[assignment]
        self.addCleanup(
            lambda: setattr(sandbox, "native_sandbox_binary", self._original)
        )

    def test_strict_passes_the_flag_to_the_binary(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            wrapped = sandbox.wrap_command("native", "echo hi", ws, ws, strict=True)
            self.assertIn("--strict", wrapped)
            # The flag belongs to the sandbox, not to the wrapped command.
            self.assertLess(wrapped.index("--strict"), wrapped.index("echo hi"))

    def test_the_default_stays_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            wrapped = sandbox.wrap_command("native", "echo hi", ws, ws)
            self.assertNotIn("--strict", wrapped)

    def test_native_passes_chdir_and_host_write_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "project"
            sub = ws / "webui"
            cache = Path(tmp) / "cache"
            sock_dir = Path(tmp) / "ssh"
            ws.mkdir()
            sub.mkdir()
            cache.mkdir()
            sock_dir.mkdir()
            sock = sock_dir / "agent.sock"
            sock.write_text("", encoding="utf-8")
            wrapped = sandbox.wrap_command(
                "native", "npm test", str(ws), str(sub),
            )
            self.assertIn("cd ", wrapped)
            self.assertIn(str(sub.resolve()), wrapped)
            with patch.dict(
                os.environ,
                {"SSH_AUTH_SOCK": str(sock), "XDG_CACHE_HOME": str(cache)},
                clear=False,
            ):
                wrapped = sandbox.wrap_command(
                    "native", "git fetch", str(ws), str(ws),
                )
            self.assertIn("--allow-write", wrapped)
            self.assertIn(str(cache), wrapped)
            self.assertIn(str(sock), wrapped)

    def test_bwrap_ignores_strict_it_is_already_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as ws, _bwrap_installed():
            self.assertEqual(
                sandbox.wrap_command("bwrap", "echo hi", ws, ws, strict=True),
                sandbox.wrap_command("bwrap", "echo hi", ws, ws),
            )


class LandlockEnforcementTest(unittest.TestCase):
    """End-to-end confinement, skipped unless the kernel enforces Landlock."""

    def setUp(self) -> None:
        self.binary = sandbox.native_sandbox_binary()
        if self.binary is None:
            self.skipTest("navin-sandbox binary not built")
        # Probe: --strict exits non-zero when Landlock is not actually enforced.
        with tempfile.TemporaryDirectory() as probe:
            result = subprocess.run(
                [self.binary, "--strict", "--workspace", probe, "--", "true"],
                capture_output=True,
                timeout=10,
            )
        if result.returncode != 0:
            self.skipTest("kernel does not enforce Landlock")

    def _run(self, command: str, workspace: str) -> subprocess.CompletedProcess:
        wrapped = sandbox.wrap_command("landlock", command, workspace, workspace)
        # Run it the way _spawn_unix does: through a shell.
        return subprocess.run(
            ["bash", "-c", wrapped], capture_output=True, text=True, timeout=15
        )

    def test_a_write_inside_the_workspace_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            target = Path(ws).resolve() / "inside.txt"
            result = self._run(f"echo ok > {target} && echo DONE", ws)
            self.assertIn("DONE", result.stdout)
            self.assertTrue(target.is_file())

    def test_reading_the_system_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            result = self._run("cat /etc/hostname >/dev/null && echo READ_OK", ws)
            self.assertIn("READ_OK", result.stdout)

    def test_a_write_outside_the_workspace_is_refused(self) -> None:
        # /etc is read-only under the policy and is not the scratch space, so
        # the write is denied by Landlock and no file is ever created.
        target = "/etc/navin-sandbox-should-not-exist"
        with tempfile.TemporaryDirectory() as ws:
            result = self._run(
                f"echo x > {target} 2>/dev/null && echo LEAK || echo BLOCKED", ws
            )
            self.assertIn("BLOCKED", result.stdout)
            self.assertNotIn("LEAK", result.stdout)
            self.assertFalse(Path(target).exists())

    def test_chdir_writes_in_a_workspace_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            sub = Path(ws).resolve() / "pkg"
            sub.mkdir()
            wrapped = sandbox.wrap_command(
                "landlock", "echo ok > inside.txt && pwd", ws, str(sub),
            )
            result = subprocess.run(
                ["bash", "-c", wrapped], capture_output=True, text=True, timeout=15,
            )
            self.assertTrue(
                (sub / "inside.txt").is_file(),
                msg=result.stdout + result.stderr,
            )
            self.assertIn(str(sub), result.stdout)


class PackagedSandboxMustShipTest(unittest.TestCase):
    """A Tauri sidecar without navin-sandbox is a failed release on every OS."""

    def test_linux_and_macos_builds_refuse_a_missing_binary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertIn("cargo is required to build navin-sandbox", text, rel)
            self.assertIn("ships no navin-sandbox", text, rel)
            required = text.find("Building navin-sandbox (required OS command jail)")
            self.assertGreater(required, 0, rel)
            skip_block = text.find("NAVIN_SKIP_NATIVE:-0")
            self.assertGreater(skip_block, required, f"{rel}: sandbox is inside the optional skip")

    def test_pyinstaller_refuses_to_collect_without_it(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "packaging/pyinstaller/bundle_contents.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Linux, macOS and Windows Tauri sidecars must ship it", text)
        self.assertIn("raise RuntimeError", text)

    def test_macos_desktop_and_smoke_test_gate_on_it(self) -> None:
        root = Path(__file__).resolve().parents[1]
        desktop = (root / "packaging/macos/build-desktop.sh").read_text(encoding="utf-8")
        self.assertIn("ships no navin-sandbox", desktop)
        self.assertIn("-name 'navin-sandbox'", desktop)
        smoke = (root / "packaging/smoke-test.sh").read_text(encoding="utf-8")
        self.assertIn("ships no navin-sandbox", smoke)

    def test_windows_tauri_build_and_smoke_test_gate_on_it(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offline = (root / "packaging/windows/build-offline.ps1").read_text(
            encoding="utf-8",
        )
        self.assertIn("cargo is required to build navin-sandbox.exe", offline)
        self.assertIn("navin-sandbox.exe missing from navin-dist", offline)
        sandbox_build = offline.find("Building navin-sandbox")
        skip_core = offline.find("NAVIN_SKIP_NATIVE")
        self.assertGreater(sandbox_build, 0)
        self.assertGreater(
            skip_core, sandbox_build, "windows: sandbox is inside the optional skip",
        )
        desktop = (root / "packaging/windows/build-desktop.ps1").read_text(
            encoding="utf-8",
        )
        self.assertIn("navin-sandbox.exe", desktop)
        self.assertIn("embedded sidecar has no navin-sandbox.exe", desktop)
        smoke = (root / "packaging/windows/smoke-test.ps1").read_text(encoding="utf-8")
        self.assertIn("ships no navin-sandbox.exe", smoke)

    def test_linux_tauri_embed_gates_on_it(self) -> None:
        root = Path(__file__).resolve().parents[1]
        appimage = (root / "packaging/linux/build-appimage.sh").read_text(
            encoding="utf-8",
        )
        self.assertIn("embedded sidecar has no navin-sandbox", appimage)

    def test_hatch_compiles_it_on_editable_install(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "hatch_build.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self._ensure_sandbox_binary(version)", text)
        self.assertIn("cargo build --release", text)

    def test_ci_installs_rust_before_the_editable_install(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
        ).read_text(encoding="utf-8")
        rust = text.find("dtolnay/rust-toolchain@stable")
        install = text.find('python -m pip install -e ".[dev-tools,documents]"')
        self.assertGreater(rust, 0)
        self.assertGreater(install, rust)

    def test_filesystem_and_git_never_go_through_the_sandbox_wrapper(self) -> None:
        root = Path(__file__).resolve().parents[1] / "navin" / "agent" / "tools"
        for name in ("filesystem.py", "git.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("wrap_command", text, name)
            self.assertNotIn("navin-sandbox", text, name)


if __name__ == "__main__":
    unittest.main()
