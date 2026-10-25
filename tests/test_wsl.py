# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Reaching a WSL distribution from Windows.

All of this is string work on purpose: a Windows host is not available here, and
the parts that would need one are kept separate from the parts that decide what
a path means. The cases that matter are the ones a user actually types or that
Explorer hands over - both spellings of the redirector, either slash, and the
ordinary network share that must not be mistaken for a distribution.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import threading
import time
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock
from unittest.mock import patch

from navin.index import service
from navin.utils import wsl, wsl_bulk, wsl_health

# Captured at import, before the test isolation stubs it out.
_REAL_SCHEDULE_CHECK = wsl_health._schedule_check


class ParseTest(unittest.TestCase):
    def test_the_current_spelling(self) -> None:
        location = wsl.parse_unc(r"\\wsl.localhost\Ubuntu\home\aymen\project")
        assert location is not None
        self.assertEqual(location.distro, "Ubuntu")
        self.assertEqual(location.posix, "/home/aymen/project")

    def test_the_older_spelling_still_used_everywhere(self) -> None:
        location = wsl.parse_unc(r"\\wsl$\Debian\srv\app")
        assert location is not None
        self.assertEqual(location.distro, "Debian")
        self.assertEqual(location.posix, "/srv/app")

    def test_forward_slashes_as_typed_by_hand(self) -> None:
        location = wsl.parse_unc("//wsl.localhost/Ubuntu/home/aymen")
        assert location is not None
        self.assertEqual(location.distro, "Ubuntu")
        self.assertEqual(location.posix, "/home/aymen")

    def test_the_distribution_root(self) -> None:
        location = wsl.parse_unc(r"\\wsl.localhost\Ubuntu")
        assert location is not None
        self.assertEqual(location.posix, "/")

    def test_a_trailing_separator_changes_nothing(self) -> None:
        location = wsl.parse_unc("\\\\wsl.localhost\\Ubuntu\\home\\")
        assert location is not None
        self.assertEqual(location.posix, "/home")

    def test_quotes_survive_a_paste(self) -> None:
        location = wsl.parse_unc('"\\\\wsl.localhost\\Ubuntu\\home"')
        assert location is not None
        self.assertEqual(location.distro, "Ubuntu")

    def test_a_distribution_name_with_a_dot(self) -> None:
        location = wsl.parse_unc(r"\\wsl.localhost\Ubuntu-22.04\home")
        assert location is not None
        self.assertEqual(location.distro, "Ubuntu-22.04")

    def test_a_spelling_the_case_does_not_match(self) -> None:
        self.assertIsNotNone(wsl.parse_unc(r"\\WSL.LOCALHOST\Ubuntu\home"))


class NotAWslPathTest(unittest.TestCase):
    def test_an_ordinary_network_share_is_somebody_elses_file_server(self) -> None:
        self.assertIsNone(wsl.parse_unc(r"\\fileserver\share\project"))
        self.assertIsNone(wsl.parse_unc(r"\\wsl-backup\share"))

    def test_a_local_windows_path(self) -> None:
        self.assertIsNone(wsl.parse_unc(r"C:\Users\aymen\project"))

    def test_a_posix_path(self) -> None:
        self.assertIsNone(wsl.parse_unc("/home/aymen/project"))

    def test_nothing_at_all(self) -> None:
        self.assertIsNone(wsl.parse_unc(""))
        self.assertIsNone(wsl.parse_unc("   "))

    def test_the_prefix_with_no_distribution(self) -> None:
        self.assertIsNone(wsl.parse_unc("\\\\wsl.localhost\\"))


class RoundTripTest(unittest.TestCase):
    def test_a_path_survives_the_trip_back_to_windows(self) -> None:
        original = r"\\wsl.localhost\Ubuntu\home\aymen\project"
        location = wsl.parse_unc(original)
        assert location is not None
        self.assertEqual(location.unc(), original)

    def test_the_old_spelling_comes_back_as_the_current_one(self) -> None:
        location = wsl.parse_unc(r"\\wsl$\Ubuntu\home")
        assert location is not None
        self.assertEqual(location.unc(), r"\\wsl.localhost\Ubuntu\home")

    def test_the_root_has_no_trailing_separator(self) -> None:
        self.assertEqual(wsl.to_unc("Ubuntu", "/"), r"\\wsl.localhost\Ubuntu")


class WindowsPathTranslationTest(unittest.TestCase):
    """For handing a Windows path to a command running inside a distribution."""

    def test_a_drive_becomes_a_mount(self) -> None:
        self.assertEqual(wsl.windows_path_to_wsl(r"C:\Users\me"), "/mnt/c/Users/me")

    def test_the_drive_letter_is_lowercased_as_wsl_mounts_it(self) -> None:
        self.assertEqual(wsl.windows_path_to_wsl(r"D:\data"), "/mnt/d/data")

    def test_a_bare_drive(self) -> None:
        self.assertEqual(wsl.windows_path_to_wsl("C:\\"), "/mnt/c")

    def test_forward_slashes(self) -> None:
        self.assertEqual(wsl.windows_path_to_wsl("C:/Users/me"), "/mnt/c/Users/me")

    def test_a_wsl_path_is_already_what_the_distribution_calls_it(self) -> None:
        self.assertEqual(
            wsl.windows_path_to_wsl(r"\\wsl.localhost\Ubuntu\home\me"),
            "/home/me",
        )

    def test_a_path_it_cannot_translate_is_refused_rather_than_guessed(self) -> None:
        self.assertIsNone(wsl.windows_path_to_wsl("/already/posix"))
        self.assertIsNone(wsl.windows_path_to_wsl(r"\\fileserver\share"))
        self.assertIsNone(wsl.windows_path_to_wsl("relative/path"))


class CommandPrefixTest(unittest.TestCase):
    def test_a_command_is_directed_at_one_distribution(self) -> None:
        with mock.patch.object(wsl, "wsl_executable", return_value="wsl.exe"):
            argv = wsl.command_prefix("Ubuntu")
        self.assertEqual(argv, ["wsl.exe", "-d", "Ubuntu", "--exec"])

    def test_the_working_directory_travels_with_it(self) -> None:
        """Without --cd the build runs in the user's home, or in /mnt/c."""
        with mock.patch.object(wsl, "wsl_executable", return_value="wsl.exe"):
            argv = wsl.command_prefix("Ubuntu", PurePosixPath("/home/me/app"))
        # --exec, never --: after -- wsl.exe re-parses the line through the
        # default shell, which expanded $(...) and broke quoted pipes.
        self.assertEqual(argv, ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/app", "--exec"])

    def test_it_still_produces_a_command_when_wsl_is_not_on_the_path(self) -> None:
        with mock.patch.object(wsl, "wsl_executable", return_value=None):
            self.assertEqual(wsl.command_prefix("Ubuntu")[0], "wsl.exe")


class DistributionListTest(unittest.TestCase):
    def setUp(self) -> None:
        wsl._distros_cache = None
        wsl._distros_cached_at = 0.0

    tearDown = setUp

    def test_the_utf16_output_windows_actually_produces(self) -> None:
        raw = "Ubuntu\r\nDebian\r\n".encode("utf-16-le")
        self.assertEqual(wsl.parse_distribution_list(raw), ["Ubuntu", "Debian"])

    def test_the_nul_padding_that_comes_with_it(self) -> None:
        raw = "Ubuntu\x00\r\n\x00Debian\x00\r\n".encode("utf-16-le")
        self.assertEqual(wsl.parse_distribution_list(raw), ["Ubuntu", "Debian"])

    def test_no_distributions_installed(self) -> None:
        self.assertEqual(wsl.parse_distribution_list(b""), [])

    def test_the_list_is_read_once(self) -> None:
        with mock.patch.object(wsl, "_read_distributions", return_value=["Ubuntu"]) as read:
            for _ in range(5):
                wsl.distributions()
        self.assertEqual(read.call_count, 1)

    def test_callers_cannot_corrupt_the_cache(self) -> None:
        with mock.patch.object(wsl, "_read_distributions", return_value=["Ubuntu"]):
            wsl.distributions().append("Debian")
            self.assertEqual(wsl.distributions(), ["Ubuntu"])


class ResolveDistroTest(unittest.TestCase):
    def setUp(self) -> None:
        wsl._distros_cache = None
        wsl._distros_cached_at = 0.0

    tearDown = setUp

    def test_a_name_typed_in_the_wrong_case_finds_the_distribution(self) -> None:
        with mock.patch.object(wsl, "_read_distributions", return_value=["Ubuntu-22.04"]):
            self.assertEqual(wsl.resolve_distro("ubuntu-22.04"), "Ubuntu-22.04")

    def test_a_distribution_that_is_not_installed(self) -> None:
        with mock.patch.object(wsl, "_read_distributions", return_value=["Ubuntu"]):
            self.assertIsNone(wsl.resolve_distro("Fedora"))
            self.assertIsNone(wsl.resolve_distro(None))


class ExecRoutingTest(unittest.IsolatedAsyncioTestCase):
    """A project inside a distribution has its commands run inside it.

    Run on the Windows side instead, they fail in ways that read like a broken
    project: the virtualenv points at /usr/bin/python3, node_modules holds Linux
    binaries, and PowerShell cannot even hold the UNC path as a working
    directory, so it starts in C:\\Windows and reports that nothing is there.
    """

    async def asyncSetUp(self) -> None:
        from navin.agent.tools.shell import ExecTool

        self.ExecTool = ExecTool
        wsl._distros_cache = ["Ubuntu"]
        wsl._distros_cached_at = float("inf")
        self.addCleanup(setattr, wsl, "_distros_cache", None)

    async def _spawn_argv(self, cwd: str, command: str = "make build") -> list[str]:
        created: dict[str, tuple] = {}

        async def fake_exec(*argv, **kwargs):
            created["argv"] = argv
            created["kwargs"] = kwargs
            return mock.Mock()

        with mock.patch("navin.agent.tools.shell._IS_WINDOWS", True):
            with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                with mock.patch.object(wsl, "wsl_executable", return_value="wsl.exe"):
                    await self.ExecTool._spawn(command, cwd, {})
        self.argv = list(created["argv"])
        self.kwargs = created["kwargs"]
        return self.argv

    async def test_the_command_is_handed_to_the_distribution(self) -> None:
        argv = await self._spawn_argv(r"\\wsl.localhost\Ubuntu\home\me\app")
        self.assertEqual(argv[:3], ["wsl.exe", "-d", "Ubuntu"])
        self.assertIn("--cd", argv)
        self.assertEqual(argv[argv.index("--cd") + 1], "/home/me/app")
        self.assertEqual(argv[-3:], ["bash", "-lc", "make build"])

    async def test_the_command_is_parsed_once_by_the_shell_it_names(self) -> None:
        # Regression: `wsl.exe ... -- bash -lc CMD` sent CMD through the default
        # shell first, which ran $(...) itself and split the quoted "a|b" into
        # a pipe ("scope_anchor: command not found"). --exec hands bash the
        # command exactly as written, as one argument.
        command = 'files=$(grep -l -E "AgentRunner|scope_anchor" tests/*.py) && echo "$files"'
        argv = await self._spawn_argv(r"\\wsl.localhost\Ubuntu\home\me\app", command)
        self.assertNotIn("--", argv)
        exec_at = argv.index("--exec")
        self.assertEqual(argv[exec_at + 1:exec_at + 3], ["bash", "-lc"])
        self.assertTrue(argv[-1].endswith(command))
        self.assertEqual(len(argv), exec_at + 4)

    async def test_it_runs_in_a_login_shell_so_the_toolchain_is_on_path(self) -> None:
        argv = await self._spawn_argv(r"\\wsl$\Ubuntu\srv\app")
        self.assertIn("-lc", argv)

    async def test_wsl_itself_is_not_asked_to_start_in_a_unc_path(self) -> None:
        await self._spawn_argv(r"\\wsl.localhost\Ubuntu\home\me")
        cwd = self.kwargs.get("cwd")
        self.assertFalse(cwd and "wsl.localhost" in str(cwd))

    async def test_an_ordinary_windows_project_does_not_go_through_wsl(self) -> None:
        created: dict[str, tuple] = {}

        async def fake_exec(*argv, **kwargs):
            created["argv"] = argv
            return mock.Mock()

        with mock.patch("navin.agent.tools.shell._IS_WINDOWS", True):
            with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                await self.ExecTool._spawn("make build", r"C:\src\app", {})
        self.assertNotIn("wsl.exe", created["argv"][0].lower())


class ShapeTest(unittest.TestCase):
    def test_unc_paths_are_recognised_whatever_is_behind_them(self) -> None:
        self.assertTrue(wsl.looks_like_unc(r"\\wsl.localhost\Ubuntu"))
        self.assertTrue(wsl.looks_like_unc(r"\\fileserver\share"))
        self.assertTrue(wsl.looks_like_unc("//fileserver/share"))

    def test_ordinary_paths_are_not(self) -> None:
        self.assertFalse(wsl.looks_like_unc(r"C:\Users"))
        self.assertFalse(wsl.looks_like_unc("/home/me"))
        self.assertFalse(wsl.looks_like_unc(""))

    def test_only_wsl_paths_are_wsl_paths(self) -> None:
        self.assertTrue(wsl.is_unc(r"\\wsl$\Ubuntu\home"))
        self.assertFalse(wsl.is_unc(r"\\fileserver\share"))


class HostBrowserTest(unittest.TestCase):
    """Finding the Windows browser from inside a distribution.

    ``navin .`` in WSL has to open its window on the Windows side; the Linux
    opener ends in ``gio: Operation not supported`` in front of a Windows
    screen. These pin down where we look and in what order.
    """

    def test_machine_wide_installs_are_tried_before_per_user_ones(self) -> None:
        candidates = wsl.host_browser_candidates(r"C:\Users\me\AppData\Local")
        chrome = [c for c in candidates if c.endswith("chrome.exe")]
        self.assertEqual(
            chrome,
            [
                "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                "/mnt/c/Users/me/AppData/Local/Google/Chrome/Application/chrome.exe",
            ],
        )

    def test_a_better_browser_beats_a_better_location(self) -> None:
        """Chrome per-user still comes before Edge machine-wide."""
        candidates = wsl.host_browser_candidates(r"C:\Users\me\AppData\Local")
        last_chrome = max(i for i, c in enumerate(candidates) if "chrome.exe" in c)
        first_edge = min(i for i, c in enumerate(candidates) if "msedge.exe" in c)
        self.assertLess(last_chrome, first_edge)

    def test_without_local_app_data_only_machine_paths_are_tried(self) -> None:
        for candidate in wsl.host_browser_candidates(None):
            self.assertTrue(candidate.startswith("/mnt/c/Program Files"))

    def test_the_first_browser_that_exists_wins(self) -> None:
        edge = "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        with mock.patch("os.path.isfile", side_effect=lambda p: p == edge):
            self.assertEqual(wsl.find_host_browser(None), edge)

    def test_no_browser_is_an_answer_not_an_error(self) -> None:
        with mock.patch("os.path.isfile", return_value=False):
            self.assertIsNone(wsl.find_host_browser(r"C:\Users\me\AppData\Local"))


class HostOpenTest(unittest.TestCase):
    def test_wslview_is_preferred_and_a_url_survives_powershell(self) -> None:
        url = "http://127.0.0.1:8765/#/?bootstrapSecret=s&project=%2Fhome"
        commands = wsl.host_open_commands(url)
        self.assertEqual(commands[0][0], "wslview")
        powershell = next(c for c in commands if c[0] == "powershell.exe")
        # Single quotes, because the & between query parameters is a statement
        # separator to PowerShell.
        self.assertEqual(powershell[-1], f"Start-Process '{url}'")
        self.assertEqual(commands[-1][0], "explorer.exe")

    def test_the_first_working_opener_ends_the_search(self) -> None:
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(wsl.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}"):
            with mock.patch.object(wsl.subprocess, "run", run):
                self.assertTrue(wsl.open_url_on_host("http://x"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/wslview")

    def test_explorer_is_believed_despite_its_exit_code(self) -> None:
        """explorer.exe reports 1 for a URL it opened perfectly well."""

        def which(name: str) -> str | None:
            return "/mnt/c/Windows/explorer.exe" if name == "explorer.exe" else None

        run = mock.Mock(return_value=mock.Mock(returncode=1))
        with mock.patch.object(wsl.shutil, "which", side_effect=which):
            with mock.patch.object(wsl.subprocess, "run", run):
                self.assertTrue(wsl.open_url_on_host("http://x"))

    def test_a_failing_opener_is_not_the_end(self) -> None:
        def which(name: str) -> str | None:
            return None if name == "explorer.exe" else f"/usr/bin/{name}"

        outcomes = [mock.Mock(returncode=1), mock.Mock(returncode=0)]
        run = mock.Mock(side_effect=outcomes)
        with mock.patch.object(wsl.shutil, "which", side_effect=which):
            with mock.patch.object(wsl.subprocess, "run", run):
                self.assertTrue(wsl.open_url_on_host("http://x"))
        self.assertEqual(run.call_count, 2)

    def test_nothing_available_is_reported_not_raised(self) -> None:
        with mock.patch.object(wsl.shutil, "which", return_value=None):
            self.assertFalse(wsl.open_url_on_host("http://x"))


class WindowsEnvTest(unittest.TestCase):
    def _run_with_stdout(self, stdout: bytes) -> str | None:
        run = mock.Mock(return_value=mock.Mock(stdout=stdout))
        with mock.patch.object(wsl.shutil, "which", return_value="/mnt/c/Windows/System32/cmd.exe"):
            with mock.patch("os.path.isfile", return_value=True):
                with mock.patch("os.path.isdir", return_value=True):
                    with mock.patch.object(wsl.subprocess, "run", run):
                        return wsl.windows_env("LOCALAPPDATA")

    def test_a_value_comes_back_without_the_crlf(self) -> None:
        self.assertEqual(
            self._run_with_stdout(b"C:\\Users\\me\\AppData\\Local\r\n"),
            r"C:\Users\me\AppData\Local",
        )

    def test_an_unset_variable_is_none_not_its_own_name(self) -> None:
        """cmd echoes the pattern back verbatim when the variable is unset."""
        self.assertIsNone(self._run_with_stdout(b"%LOCALAPPDATA%\r\n"))

    def test_no_cmd_exe_means_no_answer(self) -> None:
        with mock.patch.object(wsl.shutil, "which", return_value=None):
            with mock.patch("os.path.isfile", return_value=False):
                self.assertIsNone(wsl.windows_env("LOCALAPPDATA"))

# Bulk file work in one wsl.exe call (navin/utils/wsl_bulk.py).

UNC = Path("\\\\wsl.localhost\\Ubuntu\\home\\me\\proj")


class WslBulkTest(unittest.TestCase):
    def test_stamps_parse_to_exact_nanoseconds(self):
        raw = b"1700000000 2023-11-14 22:13:20.123456789 +0000 42 a/b.py\0"
        self.assertEqual(wsl_bulk.parse_stamps(raw), {"a/b.py": (1700000000123456789, 42)})

    def test_unparseable_stat_output_is_rejected(self):
        self.assertIsNone(wsl_bulk.parse_stamps(b"garbage\0"))

    def test_not_applicable_off_windows(self):
        self.assertIsNone(wsl_bulk.location_for(UNC, platform="linux"))
        self.assertIsNone(wsl_bulk.stat_files("/home/me/proj", ["a.py"]))

    def test_read_files_unpacks_one_tar_stream(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = b"print(1)\n"
            info = tarfile.TarInfo("src/a.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        loc = object()
        with patch.object(wsl_bulk, "location_for", return_value=loc), \
                patch.object(wsl_bulk, "_run", return_value=buf.getvalue()) as run:
            out = wsl_bulk.read_files(UNC, ["src/a.py"])
        self.assertEqual(out, {"src/a.py": b"print(1)\n"})
        self.assertEqual(run.call_args.args[2], b"src/a.py\0")

    def test_index_stats_a_bridged_root_in_one_call(self):
        with patch.object(wsl_bulk, "stat_files", return_value={"a.py": (5, 7)}) as stat:
            rows = list(service._iter_stats(UNC, ["a.py", "gone.py"], paced=True))
        self.assertEqual(rows, [("a.py", 5, 7)])
        self.assertEqual(stat.call_count, 1)

    def test_a_launch_that_times_out_asks_whether_wsl_is_down(self):
        location = wsl.parse_unc(str(UNC))
        with patch.object(wsl, "resolve_distro", return_value="Ubuntu"), \
                patch.object(wsl_bulk.subprocess, "run", side_effect=subprocess.TimeoutExpired(["wsl.exe"], 120)):
            self.assertIsNone(wsl_bulk._run(location, "true", b""))
        wsl_health._schedule_check.assert_called_once_with("Ubuntu")


class WslHealthTest(unittest.TestCase):
    """The status bar tells the user to restart WSL only when WSL is really down."""

    def setUp(self) -> None:
        for patcher in (
            patch.object(wsl_health, "_schedule_check", _REAL_SCHEDULE_CHECK),
            patch.object(wsl, "wsl_executable", return_value="wsl.exe"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _settle(self) -> None:
        for thread in threading.enumerate():
            if thread.name == "navin-wsl-health":
                thread.join(5)
                self.assertFalse(thread.is_alive())

    def test_a_hung_wsl_asks_for_a_restart_until_it_answers_again(self) -> None:
        with patch.object(wsl_health.subprocess, "run", side_effect=subprocess.TimeoutExpired(["wsl.exe"], 20)) as run:
            wsl_health.suspect("Ubuntu")
            self._settle()
        self.assertEqual(run.call_args.args[0], ["wsl.exe", "-d", "Ubuntu", "--exec", "true"])
        notice = wsl_health.status()
        assert notice is not None
        self.assertEqual(notice["distro"], "Ubuntu")
        self.assertEqual(notice["state"], "hung")
        self.assertEqual(notice["command"], "wsl --shutdown")
        self.assertGreaterEqual(notice["sinceSeconds"], 0)

        # After the restart the next recovery check clears it by itself.
        healthy = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch.object(wsl_health, "RECHECK_EVERY_S", 0), \
                patch.object(wsl_health.subprocess, "run", return_value=healthy):
            self.assertIsNotNone(wsl_health.status())
            self._settle()
        self.assertIsNone(wsl_health.status())

    def test_the_service_failure_is_reported_with_its_own_words(self) -> None:
        message = "Catastrophic failure\r\nError code: Wsl/Service/E_UNEXPECTED\r\n".encode("utf-16-le")
        failed = subprocess.CompletedProcess([], 4294967295, stdout=message, stderr=b"")
        with patch.object(wsl_health.subprocess, "run", return_value=failed):
            wsl_health.suspect("Ubuntu")
            self._settle()
        notice = wsl_health.status()
        assert notice is not None
        self.assertEqual(notice["state"], "error")
        self.assertEqual(notice["detail"], "Catastrophic failure Error code: Wsl/Service/E_UNEXPECTED")

    def test_a_slow_command_on_a_healthy_wsl_shows_nothing(self) -> None:
        healthy = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch.object(wsl_health.subprocess, "run", return_value=healthy) as run:
            wsl_health.suspect("Ubuntu")
            self._settle()
            wsl_health.suspect("Ubuntu")  # within the recheck interval: no second wsl.exe
            self._settle()
        self.assertEqual(run.call_count, 1)
        self.assertIsNone(wsl_health.status())

    def test_reading_the_status_never_waits_on_wsl(self) -> None:
        release = threading.Event()

        def hung(*_args, **_kwargs):
            release.wait(5)
            raise subprocess.TimeoutExpired(["wsl.exe"], 20)

        with patch.object(wsl_health.subprocess, "run", side_effect=hung):
            wsl_health.suspect("Ubuntu")
            started = time.monotonic()
            self.assertIsNone(wsl_health.status())  # verdict still pending
            self.assertLess(time.monotonic() - started, 1.0)
            release.set()
            self._settle()
        self.assertIsNotNone(wsl_health.status())

    def test_a_launch_that_answered_clears_the_notice_at_once(self) -> None:
        with patch.object(wsl_health.subprocess, "run", side_effect=subprocess.TimeoutExpired(["wsl.exe"], 20)):
            wsl_health.suspect("Ubuntu")
            self._settle()
        self.assertIsNotNone(wsl_health.status())
        wsl_health.answered("ubuntu")
        self.assertIsNone(wsl_health.status())


class GeneratedScriptsRunForRealTest(unittest.TestCase):
    """The shell scripts sent to wsl.exe, run here with wsl.exe left out."""

    def setUp(self) -> None:
        import subprocess
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        def run_here(_location, script, payload):
            return subprocess.run(
                ["sh", "-c", script], input=payload, capture_output=True, cwd=self.dir,
            ).stdout

        for name, value in (("location_for", lambda *a, **k: object()), ("_run", run_here)):
            patcher = patch.object(wsl_bulk, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write(self, rel: str, text: str = "x") -> None:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_stat_and_read_return_every_listed_file(self):
        import os

        self._write("src/a.py", "print(1)\n")
        self._write("b c.py", "y = 2\n")
        stamps = wsl_bulk.stat_files(UNC, ["src/a.py", "b c.py", "gone.py"])
        st = os.stat(self.dir / "src" / "a.py")
        self.assertEqual(stamps["src/a.py"], (st.st_mtime_ns, st.st_size))
        self.assertNotIn("gone.py", stamps)
        contents = wsl_bulk.read_files(UNC, ["src/a.py", "b c.py", "gone.py"])
        self.assertEqual(contents, {"src/a.py": b"print(1)\n", "b c.py": b"y = 2\n"})

    def _digest(self):
        return wsl_bulk.layout_digest(
            UNC, names={"package.json", "*.y*ml"}, watched_dirs={"node_modules"},
            prune={".git", "dist"}, max_depth=3,
        )

    def test_layout_moves_with_manifests_and_folders_not_with_source(self):
        import os

        self._write("package.json", "{}")
        self._write("src/app.ts")
        self._write("dist/out.js")
        first = self._digest()
        self.assertIsNotNone(first)
        self._write("src/app.ts", "changed")
        self._write("dist/package.json", "{}")  # inside a pruned folder
        self.assertEqual(self._digest(), first)
        os.utime(self.dir / "package.json", ns=(1, 1))
        second = self._digest()
        self.assertNotEqual(second, first)
        (self.dir / "web").mkdir()
        self.assertNotEqual(self._digest(), second)

    def test_watcher_signature_sees_any_file_change(self):
        import os

        self._write("src/app.ts")
        first = wsl_bulk.tree_signature(UNC, {".git"}, max_entries=100)
        os.utime(self.dir / "src" / "app.ts", ns=(1, 1))
        self.assertNotEqual(wsl_bulk.tree_signature(UNC, {".git"}, max_entries=100), first)


class RunbookLayoutMemoTest(unittest.TestCase):
    def setUp(self) -> None:
        from navin.agent import project_runbook

        self.rb = project_runbook
        self.rb._BRIDGED_MEMO.clear()
        self.addCleanup(self.rb._BRIDGED_MEMO.clear)
        patcher = patch("navin.index.service.is_bridged_root", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = 0

    def _compute(self):
        self.calls += 1
        return self.calls

    def test_kept_while_the_layout_holds_and_redone_when_it_moves(self):
        clock = [100.0]
        with patch.object(self.rb.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(wsl_bulk, "layout_digest", side_effect=[7, 7, 8]) as layout:
            self.assertEqual(self.rb.bridged_memo("p", UNC, self._compute), 1)
            clock[0] += 1  # same tool call: no second layout check
            self.assertEqual(self.rb.bridged_memo("p", UNC, self._compute), 1)
            clock[0] += 60
            self.assertEqual(self.rb.bridged_memo("p", UNC, self._compute), 1)
            clock[0] += 60
            self.assertEqual(self.rb.bridged_memo("p", UNC, self._compute), 2)
        self.assertEqual(layout.call_count, 3)

    def test_without_a_layout_the_answer_expires(self):
        clock = [100.0]
        with patch.object(self.rb.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(wsl_bulk, "layout_digest", return_value=None):
            self.rb.bridged_memo("p", UNC, self._compute)
            clock[0] += 5
            self.assertEqual(self.rb.bridged_memo("p", UNC, self._compute), 1)
            clock[0] += self.rb.BRIDGED_TTL_S
            self.assertEqual(self.rb.bridged_memo("p", UNC, self._compute), 2)


if __name__ == "__main__":
    unittest.main()
