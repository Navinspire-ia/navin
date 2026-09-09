# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How navin writes down the command that re-launches navin.

A packaged build has no interpreter to call: ``sys.executable`` is the navin
binary. Handing that binary ``-m navin`` makes it reject ``-m`` as an unknown
option, and the damage lands far from the mistake - a systemd unit that restarts
forever, an API process the WebUI reports as dead with nothing in the log. These
pin both forms so the packaged one cannot regress silently again.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from navin.api.runtime import ApiRuntime, ApiStartOptions, api_runtime_paths
from navin.gateway import GatewayStartOptions, build_gateway_command
from navin.gateway.service import GatewayServiceInstaller, GatewayServiceOptions
from navin.process_runtime import child_command_prefix, child_environment

FROZEN_BINARY = "/opt/navin/navin"


def frozen_as(binary: str):
    """Pretend this process is the PyInstaller build at ``binary``."""
    return mock.patch.multiple(
        sys,
        frozen=True,
        executable=binary,
        create=True,
    )


class PrefixTest(unittest.TestCase):
    def test_a_source_install_names_the_module(self) -> None:
        self.assertEqual(
            child_command_prefix("/usr/bin/python3"),
            ["/usr/bin/python3", "-m", "navin"],
        )

    def test_a_frozen_build_calls_itself_directly(self) -> None:
        with frozen_as(FROZEN_BINARY):
            self.assertEqual(child_command_prefix(), [FROZEN_BINARY])

    def test_a_frozen_build_recognises_its_own_path(self) -> None:
        with frozen_as(FROZEN_BINARY):
            self.assertEqual(child_command_prefix(FROZEN_BINARY), [FROZEN_BINARY])

    def test_a_real_interpreter_keeps_the_module_form_even_when_frozen(self) -> None:
        """Naming an interpreter is a deliberate choice, not the binary talking."""
        with frozen_as(FROZEN_BINARY):
            self.assertEqual(
                child_command_prefix("/usr/bin/python3"),
                ["/usr/bin/python3", "-m", "navin"],
            )

    def test_no_argument_falls_back_to_this_process(self) -> None:
        self.assertEqual(child_command_prefix()[0], sys.executable)


class GatewayCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.options = GatewayStartOptions(port=18790)

    def test_the_source_command_runs_the_module(self) -> None:
        command = build_gateway_command("/usr/bin/python3", self.options)
        self.assertEqual(
            command,
            ["/usr/bin/python3", "-m", "navin", "gateway", "--foreground", "--port", "18790"],
        )

    def test_the_packaged_command_has_no_module_flag(self) -> None:
        with frozen_as(FROZEN_BINARY):
            command = build_gateway_command(FROZEN_BINARY, self.options)
        self.assertEqual(
            command,
            [FROZEN_BINARY, "gateway", "--foreground", "--port", "18790"],
        )
        self.assertNotIn("-m", command)

    def test_selectors_survive_the_prefix_change(self) -> None:
        options = GatewayStartOptions(
            port=1234,
            verbose=True,
            workspace="/w",
            config_path="/c.json",
        )
        with frozen_as(FROZEN_BINARY):
            command = build_gateway_command(FROZEN_BINARY, options)
        self.assertEqual(
            command,
            [
                FROZEN_BINARY, "gateway", "--foreground", "--port", "1234",
                "--verbose", "--workspace", "/w", "--config", "/c.json",
            ],
        )


class ApiCommandTest(unittest.TestCase):
    def _command(self, executable: str) -> list[str]:
        runtime = ApiRuntime(
            paths=api_runtime_paths(Path("/tmp/navin-test/config.json")),
            python_executable=executable,
        )
        return runtime._build_child_command(ApiStartOptions(port=9000))

    def test_the_source_command_runs_the_module(self) -> None:
        command = self._command("/usr/bin/python3")
        self.assertEqual(command[:4], ["/usr/bin/python3", "-m", "navin", "serve"])

    def test_the_packaged_command_has_no_module_flag(self) -> None:
        with frozen_as(FROZEN_BINARY):
            command = self._command(FROZEN_BINARY)
        self.assertEqual(command[:2], [FROZEN_BINARY, "serve"])
        self.assertNotIn("-m", command)


class ChildEnvironmentTest(unittest.TestCase):
    """What the bootloader records about its unpack directory must not travel."""

    def test_a_source_install_passes_its_environment_through(self) -> None:
        base = {"PATH": "/usr/bin", "_MEIPASS2": "/tmp/_MEIxyz"}
        self.assertEqual(child_environment(base), base)

    def test_a_frozen_build_drops_every_bootloader_variable(self) -> None:
        base = {
            "PATH": "/usr/bin",
            "_MEIPASS2": "/tmp/_MEIxyz",
            "_PYI_ARCHIVE_FILE": "/opt/navin/navin",
            "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEIxyz",
            "_PYI_PARENT_PROCESS_LEVEL": "0",
        }
        with frozen_as(FROZEN_BINARY):
            env = child_environment(base)
        self.assertEqual(env, {"PATH": "/usr/bin"})

    def test_the_callers_loader_path_is_restored_not_dropped(self) -> None:
        base = {"LD_LIBRARY_PATH": "/tmp/_MEIxyz", "LD_LIBRARY_PATH_ORIG": "/opt/mine/lib"}
        with frozen_as(FROZEN_BINARY):
            env = child_environment(base)
        self.assertEqual(env, {"LD_LIBRARY_PATH": "/opt/mine/lib"})

    def test_a_loader_path_the_bootloader_invented_is_removed(self) -> None:
        with frozen_as(FROZEN_BINARY):
            env = child_environment({"LD_LIBRARY_PATH": "/tmp/_MEIxyz"})
        self.assertEqual(env, {})


class StartupWatchTest(unittest.TestCase):
    """A start is only reported once the child has outlived its own imports."""

    def _runtime(self, alive_after: list[bool]) -> tuple[Any, list[float]]:
        from navin.process_runtime import ManagedProcessRuntime, ProcessRuntimePaths

        slept: list[float] = []
        runtime = ManagedProcessRuntime(
            paths=ProcessRuntimePaths(
                run_dir=Path("/tmp/navin-watch/run"),
                logs_dir=Path("/tmp/navin-watch/logs"),
                state_path=Path("/tmp/navin-watch/run/s.json"),
                log_path=Path("/tmp/navin-watch/logs/s.log"),
            ),
            sleep=slept.append,
        )
        answers = iter(alive_after)
        runtime._is_pid_running = lambda _pid: next(answers, False)  # type: ignore[method-assign]
        return runtime, slept

    def test_a_source_install_is_judged_quickly(self) -> None:
        runtime, slept = self._runtime([True])
        self.assertTrue(runtime._survives_startup(123))
        self.assertEqual(sum(slept), 0.2)

    def test_a_packaged_build_is_watched_past_its_unpacking(self) -> None:
        runtime, slept = self._runtime([True] * 40)
        with frozen_as(FROZEN_BINARY):
            self.assertTrue(runtime._survives_startup(123))
        self.assertAlmostEqual(sum(slept), 4.0)

    def test_a_child_that_dies_late_is_still_caught(self) -> None:
        """The old check looked once, too early, and called this a success."""
        runtime, _ = self._runtime([True, True, False])
        with frozen_as(FROZEN_BINARY):
            self.assertFalse(runtime._survives_startup(123))


class ServiceUnitTest(unittest.TestCase):
    """The unit file is the version of this bug that survives a reboot."""

    def _unit(self, executable: str, home: Path) -> str:
        installer = GatewayServiceInstaller(platform_name="linux", home=home)
        result = installer.install(
            GatewayServiceOptions(
                start=GatewayStartOptions(port=18790),
                manager="systemd",
                python_executable=executable,
            ),
            dry_run=True,
        )
        assert result.content is not None
        return result.content

    def test_a_packaged_install_writes_a_runnable_execstart(self) -> None:
        with frozen_as(FROZEN_BINARY):
            content = self._unit(FROZEN_BINARY, Path("/home/tester"))
        self.assertIn(f"ExecStart={FROZEN_BINARY} gateway --foreground", content)
        self.assertNotIn("-m navin", content)

    def test_a_source_install_still_writes_the_module_form(self) -> None:
        content = self._unit("/usr/bin/python3", Path("/home/tester"))
        self.assertIn("ExecStart=/usr/bin/python3 -m navin gateway", content)


class WindowsLogonTest(unittest.TestCase):
    """Windows had no "start at login" at all: the only platform without one.

    Linux gets a systemd user unit and macOS a LaunchAgent, while Windows
    answered `unsupported_service_manager:windows`, so the gateway had to be
    started by hand after every reboot.
    """

    def _install(self, executable: str, *, tmp: str, start_now: bool = False):
        installer = GatewayServiceInstaller(platform_name="Windows", home=Path(tmp))
        return installer.install(
            GatewayServiceOptions(
                start=GatewayStartOptions(port=18790, workspace=tmp),
                python_executable=executable,
                start_now=start_now,
            ),
            dry_run=True,
        )

    def test_windows_resolves_to_a_logon_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._install(FROZEN_BINARY + ".exe", tmp=tmp)
        self.assertTrue(result.ok)
        self.assertEqual(result.manager, "windows")
        self.assertIn("CurrentVersion\\Run", str(result.path))
        self.assertTrue(str(result.path).endswith("Navin"))

    def test_the_logon_command_starts_the_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, frozen_as(FROZEN_BINARY + ".exe"):
            result = self._install(FROZEN_BINARY + ".exe", tmp=tmp)
        assert result.content is not None
        self.assertIn("gateway", result.content)
        self.assertIn("--foreground", result.content)
        self.assertIn("18790", result.content)
        self.assertNotIn("-m navin", result.content)

    def test_a_windowless_executable_is_preferred(self) -> None:
        """A console program in the Run key flashes a window at every logon."""
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            app.mkdir()
            console_exe = app / "navin-cli.exe"
            console_exe.write_text("", encoding="utf-8")
            (app / "Navin.exe").write_text("", encoding="utf-8")
            with frozen_as(str(console_exe)):
                result = self._install(str(console_exe), tmp=tmp)
        assert result.content is not None
        self.assertIn("Navin.exe", result.content)
        self.assertNotIn("navin-cli.exe", result.content)

    def test_removal_names_the_same_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installer = GatewayServiceInstaller(platform_name="Windows", home=Path(tmp))
            installed = self._install(FROZEN_BINARY + ".exe", tmp=tmp)
            removed = installer.uninstall(dry_run=True)
        self.assertTrue(removed.ok)
        self.assertEqual(removed.path, installed.path)


if __name__ == "__main__":
    unittest.main()
