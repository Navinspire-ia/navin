"""Reaching a WSL distribution from Windows.

All of this is string work on purpose: a Windows host is not available here, and
the parts that would need one are kept separate from the parts that decide what
a path means. The cases that matter are the ones a user actually types or that
Explorer hands over - both spellings of the redirector, either slash, and the
ordinary network share that must not be mistaken for a distribution.
"""

from __future__ import annotations

import unittest
from pathlib import PurePosixPath
from unittest import mock

from navin.utils import wsl


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
        self.assertEqual(argv, ["wsl.exe", "-d", "Ubuntu", "--"])

    def test_the_working_directory_travels_with_it(self) -> None:
        """Without --cd the build runs in the user's home, or in /mnt/c."""
        with mock.patch.object(wsl, "wsl_executable", return_value="wsl.exe"):
            argv = wsl.command_prefix("Ubuntu", PurePosixPath("/home/me/app"))
        self.assertEqual(argv, ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/app", "--"])

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

    async def _spawn_argv(self, cwd: str) -> list[str]:
        created: dict[str, tuple] = {}

        async def fake_exec(*argv, **kwargs):
            created["argv"] = argv
            created["kwargs"] = kwargs
            return mock.Mock()

        with mock.patch("navin.agent.tools.shell._IS_WINDOWS", True):
            with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                with mock.patch.object(wsl, "wsl_executable", return_value="wsl.exe"):
                    await self.ExecTool._spawn("make build", cwd, {})
        self.argv = list(created["argv"])
        self.kwargs = created["kwargs"]
        return self.argv

    async def test_the_command_is_handed_to_the_distribution(self) -> None:
        argv = await self._spawn_argv(r"\\wsl.localhost\Ubuntu\home\me\app")
        self.assertEqual(argv[:3], ["wsl.exe", "-d", "Ubuntu"])
        self.assertIn("--cd", argv)
        self.assertEqual(argv[argv.index("--cd") + 1], "/home/me/app")
        self.assertEqual(argv[-3:], ["bash", "-lc", "make build"])

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


if __name__ == "__main__":
    unittest.main()
