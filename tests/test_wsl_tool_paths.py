# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Paths and shell overrides retain their meaning across the Windows/WSL bridge."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navin.agent.tools import shell
from navin.security import workspace_policy
from navin.utils import wsl

ROOT = r"\\wsl.localhost\Ubuntu\home\me\projet équipe"


@pytest.mark.parametrize("path,expected", [
    ("/home/me/projet équipe/src/new.py", ROOT + r"\src\new.py"),
    ("/tmp/audit.txt", r"\\wsl.localhost\Ubuntu\tmp\audit.txt"),
    ("/", r"\\wsl.localhost\Ubuntu"),
    ("src/new.py", "src/new.py"),
    (r"src\new.py", r"src\new.py"),
    (r"C:\work\new.py", r"C:\work\new.py"),
    (r"\\wsl$\Debian\tmp\new.py", r"\\wsl$\Debian\tmp\new.py"),
])
def test_wsl_paths_round_trip_without_guessing_the_file_location(path, expected):
    assert wsl.host_tool_path(path, ROOT, platform="win32") == expected


@pytest.mark.parametrize("platform,workspace", [
    ("linux", "/home/me/project"), ("darwin", "/Users/me/project"),
    ("win32", r"C:\work\project"), ("win32", r"\\server\share\project"),
])
def test_native_workspaces_keep_native_path_semantics(platform, workspace):
    assert wsl.host_tool_path("/src/file.py", workspace, platform=platform) == "/src/file.py"


def test_shared_tool_resolver_keeps_the_absolute_wsl_path(monkeypatch):
    monkeypatch.setattr(workspace_policy, "host_tool_path", lambda path, root: wsl.host_tool_path(
        path, root, platform="win32",
    ))
    # This is the common resolution entry point for file, Git and exec tools.
    assert workspace_policy.project_rooted_path(
        "/home/me/projet équipe/src/new.py", ROOT, [ROOT],
    ) == ROOT + r"\src\new.py"


@pytest.mark.parametrize("program,login,expected", [
    (None, False, ("bash", "-lc")),
    ("bash", False, ("bash", "-c")),
    ("/bin/bash", True, ("/bin/bash", "-lc")),
    ("/usr/bin/zsh", True, ("/usr/bin/zsh", "-lc")),
    ("sh", True, ("sh", "-c")),
    ("wsl", False, ("bash", "-lc")),
])
def test_wsl_shell_is_resolved_in_the_distribution(tmp_path, monkeypatch, program, login, expected):
    monkeypatch.setattr(shell, "_IS_WINDOWS", True)
    monkeypatch.setattr(shell.shutil, "which", lambda name: None)
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: name)
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(shell.ExecTool, "_resolve_cwd", staticmethod(lambda *args, **kwargs: ROOT))
    spawn = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(shell.asyncio, "create_subprocess_exec", spawn)

    async def run():
        tool = shell.ExecTool(working_dir=str(tmp_path))
        prepared = await tool._prepare_command("printf 'ok'", shell=program, login=login)
        assert not isinstance(prepared, str), prepared
        await tool._spawn(
            prepared.command, prepared.cwd, prepared.env, prepared.shell_program, prepared.login,
        )

    asyncio.run(run())
    assert spawn.call_args.args == (
        "wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/projet équipe", "--exec",
        *expected, "printf 'ok'",
    )


@pytest.mark.parametrize("program", [r"C:\Windows\powershell.exe", "bash -i", "bin/bash", "bash\nwhoami"])
def test_invalid_wsl_shell_returns_a_recoverable_error(program):
    resolved, error = shell.ExecTool()._resolve_shell(program, wsl_project=True)
    assert resolved is None and error.is_error


def test_wsl_shell_keeps_the_operators_allowlist():
    resolved, error = shell.ExecTool(allowed_shells=["bash"])._resolve_shell("/bin/zsh", wsl_project=True)
    assert resolved is None and error.is_error
    assert "Allowed by tools.exec.allowedShells: bash" in error


@pytest.mark.parametrize("program", ["cmd", "powershell", "pwsh.exe"])
def test_windows_shells_are_refused_with_bash_guidance_in_a_wsl_project(program):
    resolved, error = shell.ExecTool()._resolve_shell(program, wsl_project=True)
    assert resolved is None and error.is_error
    assert "inside WSL" in error and "bash syntax" in error


def test_wsl_project_description_asks_for_bash(monkeypatch):
    monkeypatch.setattr(shell, "_IS_WINDOWS", True)
    tool = shell.ExecTool(working_dir=r"\\wsl.localhost\Ubuntu\home\u\proj")
    assert "lives inside WSL" in tool.description
    assert "use PowerShell syntax by default" not in tool.description


def test_wsl_project_exec_keeps_slow_probes_off_the_event_loop(tmp_path, monkeypatch):
    """Desktop on Windows + project in WSL: no blocking call on the shared loop.

    wsl.exe -l -q (distro lookup), the sandbox helper probe and the venv walk
    over the UNC path each took seconds and froze every chat at once.
    """
    import threading

    main = threading.get_ident()
    on_loop: list[str] = []

    def slow(name, result):
        def probe(*args, **kwargs):
            if threading.get_ident() == main:
                on_loop.append(name)
            return result
        return probe

    monkeypatch.setattr(shell, "_IS_WINDOWS", True)
    monkeypatch.setattr(shell.shutil, "which", lambda name: None)
    monkeypatch.setattr(wsl, "resolve_distro", slow("resolve_distro", "Ubuntu"))
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(shell.ExecTool, "_resolve_cwd", staticmethod(lambda *a, **k: ROOT))
    monkeypatch.setattr(shell.ExecTool, "_path_for", slow("_path_for", ("", "")))
    spawn = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(shell.asyncio, "create_subprocess_exec", spawn)

    async def run():
        tool = shell.ExecTool(working_dir=str(tmp_path))
        guard = tool._guard_command
        tool._guard_command = lambda *a, **k: (slow("guard", None)(), guard(*a, **k))[1]
        prepared = await tool._prepare_command("ls")
        assert not isinstance(prepared, str), prepared
        await tool._spawn(
            prepared.command, prepared.cwd, prepared.env, prepared.shell_program, prepared.login,
        )

    asyncio.run(run())
    assert on_loop == []
    assert spawn.call_args.args[:3] == ("wsl.exe", "-d", "Ubuntu")


def test_helper_git_calls_route_through_wsl_for_a_wsl_project(monkeypatch):
    """Windows git refuses \\\\wsl.localhost repos: helpers must use wsl.exe too."""
    import subprocess
    from pathlib import Path

    from navin.agent.tools import path_utils, quality, search
    from navin.utils.git_argv import GitRoute

    root = Path(r"\\wsl.localhost\Ubuntu\home\me\proj")
    route = GitRoute(argv=("wsl.exe", "-d", "Ubuntu", "git"), root="/home/me/proj", distro="Ubuntu")
    for mod in (path_utils, quality):
        monkeypatch.setattr(mod, "git_route", lambda r: route, raising=False)
    monkeypatch.setattr("navin.utils.git_argv.git_route", lambda r: route)
    monkeypatch.setattr(wsl, "parse_unc", lambda s: SimpleNamespace(posix="/home/me/proj/a.log")
                        if s.endswith("a.log") else None)
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        out = "/home/me/proj/a.log\0" if "check-ignore" in argv else (b"src/x.py\0" if "ls-files" in argv else "x.py\n")
        return SimpleNamespace(returncode=0, stdout=out)

    monkeypatch.setattr(subprocess, "run", fake_run)
    log = root / "a.log"
    assert search._gitignored_paths(root, [log]) == {log}
    assert quality._tracked_modifications(root, None) == ["x.py"]
    assert path_utils._git_basename_matches(root, "x.py") == [root / "src/x.py"]
    for argv, _ in calls:
        assert argv[:4] == ["wsl.exe", "-d", "Ubuntu", "git"]
        assert "-C" not in argv  # the wsl.exe prefix already sets the Linux cwd
    assert calls[0][1] == "/home/me/proj/a.log"


def test_verify_index_and_panels_route_git_through_wsl(monkeypatch):
    """verify, code index, audit/brain panels: same wsl.exe routing as the git tool."""
    import subprocess

    from navin.index import store
    from navin.quality import verify
    from navin.webui import project_audit, project_brain

    prefix = ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/proj", "--", "git"]
    for mod in (verify, store, project_audit, project_brain):
        monkeypatch.setattr(mod, "git_argv", lambda root: list(prefix))
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="?? src/a.py\0" if "status" in argv else "src/a.py\0", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    root = SimpleNamespace(__str__=lambda self: r"\\wsl.localhost\Ubuntu\home\me\proj")
    from pathlib import Path

    root = Path(r"\\wsl.localhost\Ubuntu\home\me\proj")
    verify.changed_files(root)
    store._git_files(root)
    project_audit._run_git(root, "log")
    project_brain._run_git(root, "log")
    assert len(calls) == 4
    for argv in calls:
        assert argv[: len(prefix)] == prefix


def test_share_bundle_and_clone_route_git_through_wsl(monkeypatch, tmp_path):
    """Share context, session export and UI clone use the distro's git too."""
    import subprocess
    from pathlib import Path

    from navin.session import bundle
    from navin.share import context
    from navin.utils.git_argv import GitRoute
    from navin.webui import git_clone

    prefix = ("wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/proj", "--", "git")
    route = GitRoute(argv=prefix, root="/home/me/proj", distro="Ubuntu")
    for mod in (bundle, context):
        monkeypatch.setattr(mod, "git_route", lambda r: route)
    for mod in (bundle, git_clone):
        monkeypatch.setattr(mod, "git_argv", lambda r: list(prefix))
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        out = "/home/me/proj\n"
        return SimpleNamespace(returncode=0, stdout=out if kw.get("text") else out.encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    root = Path(r"\\wsl.localhost\Ubuntu\home\me\proj")
    unc = wsl.to_unc("Ubuntu", "/home/me/proj")
    assert context._git(root, "rev-parse", "--show-toplevel") == unc
    assert bundle._git_root(root) == Path(unc)
    assert git_clone._existing_remote_url(root) == "/home/me/proj"
    assert len(calls) == 3
    for argv in calls:
        assert tuple(argv[: len(prefix)]) == prefix

    # Cloning into a \\wsl.localhost folder runs the distro's git on the Linux path.
    calls.clear()
    monkeypatch.setattr(git_clone, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: "Ubuntu")
    monkeypatch.setattr(wsl, "parse_unc", lambda s: SimpleNamespace(distro="Ubuntu", posix="/home/me/repo")
                        if s.endswith("repo") else None)
    git_clone.clone_git_project("https://github.com/me/repo.git", parent=str(tmp_path))
    assert calls[-1][:3] == ["wsl.exe", "-d", "Ubuntu"]
    assert calls[-1][-5:] == ["clone", "--depth", "1", "https://github.com/me/repo.git", "/home/me/repo"]


# file-preview: a bare file name cited in the chat, looked up in a WSL project
# seen from Windows. The 2026-10-21 desktop log had these requests waiting 29 s
# at the median inside an os.walk over the 9P bridge.

def _basename_root(tmp_path):
    linux_root = tmp_path / "proj"
    (linux_root / "backend").mkdir(parents=True)
    (linux_root / "backend" / "ocr.py").write_text("x\n")
    (linux_root / "node_modules" / "pkg").mkdir(parents=True)
    (linux_root / "node_modules" / "pkg" / "ocr.py").write_text("x\n")
    (linux_root / ".hidden").mkdir()
    (linux_root / ".hidden" / "ocr.py").write_text("x\n")
    return linux_root


def test_basename_lookup_runs_find_inside_the_distribution(tmp_path, monkeypatch):
    import shutil as _shutil

    from navin.webui import file_preview

    if not _shutil.which("find"):
        pytest.skip("find not installed")
    linux_root = _basename_root(tmp_path)
    unc = file_preview.Path("\\\\wsl.localhost\\Ubuntu" + str(linux_root).replace("/", "\\"))
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: "Ubuntu")
    # The real find on this machine, with wsl.exe itself left out.
    seen = {}
    real_prefix = wsl.command_prefix

    def prefix(distro, cwd=None):
        seen["prefix"] = real_prefix(distro)
        return []

    monkeypatch.setattr(wsl, "command_prefix", prefix)
    found = file_preview._wsl_find_basename(unc, "ocr.py", platform="win32")
    assert seen["prefix"][:3] == ["wsl.exe", "-d", "Ubuntu"]
    # node_modules and hidden folders are pruned, as in the walk.
    assert found == (unc / "backend" / "ocr.py", True)
    assert file_preview._wsl_find_basename(unc, "absent.py", platform="win32") == (None, True)


def test_basename_lookup_falls_back_when_find_cannot_run(tmp_path, monkeypatch):
    import subprocess

    from navin.webui import file_preview

    unc = file_preview.Path(r"\\wsl.localhost\Ubuntu\home\me\proj")
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: "Ubuntu")
    monkeypatch.setattr(file_preview.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    assert file_preview._wsl_find_basename(unc, "ocr.py", platform="win32") is None
    assert file_preview._wsl_find_basename(unc, "ocr.py", platform="linux") is None
    assert file_preview._find_name_pattern("a*b?[c].py") == "a\\*b\\?\\[c].py"


def test_basename_walk_stops_at_its_time_budget(tmp_path, monkeypatch):
    from navin.webui import file_preview

    root = _basename_root(tmp_path)
    file_preview._BASENAME_WALK_CACHE.clear()
    monkeypatch.setattr(file_preview, "_BASENAME_WALK_BUDGET_S", -1.0)
    found, complete = file_preview._walk_unique_basename(root, "ocr.py", cached=False)
    assert (found, complete) == (None, False)
    monkeypatch.setattr(file_preview, "_BASENAME_WALK_BUDGET_S", 30.0)
    found, complete = file_preview._walk_unique_basename(root, "ocr.py", cached=False)
    assert (found, complete) == ((root / "backend" / "ocr.py").resolve(), True)


def test_concurrent_chip_probes_share_one_walk_of_the_root(tmp_path, monkeypatch):
    # A rendered answer probes each of its file chips in its own request: on
    # the user's Windows desktop twenty probes were twenty concurrent walks
    # of the same tree, 2 to 5 s each. One walk now serves them all.
    import threading

    from navin.webui import file_preview

    root = _basename_root(tmp_path)
    for name in ("a.py", "b.py", "c.py", "d.py"):
        (root / "backend" / name).write_text("x\n")
    file_preview._BASENAME_WALK_CACHE.clear()
    file_preview._BASENAME_INDEX_CACHE.clear()
    walks = []
    real_walk = file_preview.os.walk

    def counted_walk(*args, **kwargs):
        walks.append(args[0])
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(file_preview.os, "walk", counted_walk)
    results = {}

    def probe(name):
        results[name] = file_preview._walk_unique_basename(root.resolve(), name)

    threads = [threading.Thread(target=probe, args=(name,)) for name in ("ocr.py", "a.py", "b.py", "c.py", "d.py", "absent.py")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(walks) == 1
    assert results["ocr.py"] == ((root / "backend" / "ocr.py").resolve(), True)
    assert results["d.py"] == ((root / "backend" / "d.py").resolve(), True)
    assert results["absent.py"] == (None, True)
    # The next probe, within the index's life, walks nothing at all.
    assert file_preview._walk_unique_basename(root.resolve(), "c.py") == ((root / "backend" / "c.py").resolve(), True)
    assert len(walks) == 1


def test_a_file_written_after_a_miss_is_found_on_the_retry(tmp_path, monkeypatch):
    # The agent usually writes the file a chip names right after the chip is
    # rendered: "Retry" must find it even though the index is shared and cached.
    from navin.webui import file_preview

    root = _basename_root(tmp_path)
    file_preview._BASENAME_WALK_CACHE.clear()
    file_preview._BASENAME_INDEX_CACHE.clear()
    clock = [1000.0]
    monkeypatch.setattr(file_preview.time, "monotonic", lambda: clock[0])
    assert file_preview._walk_unique_basename(root.resolve(), "new.py") == (None, True)
    (root / "backend" / "new.py").write_text("x\n")
    # Within the miss cache the answer stands, as before.
    assert file_preview._walk_unique_basename(root.resolve(), "new.py") == (None, True)
    clock[0] += file_preview._BASENAME_MISS_TTL_S + 0.5
    assert file_preview._walk_unique_basename(root.resolve(), "new.py") == ((root / "backend" / "new.py").resolve(), True)
    # Opening a file (cached=False) always looks again, however fresh the index.
    (root / "backend" / "late.py").write_text("x\n")
    assert file_preview._walk_unique_basename(root.resolve(), "late.py", cached=False) == ((root / "backend" / "late.py").resolve(), True)


def test_the_wsl_listing_is_one_find_for_every_name(tmp_path, monkeypatch):
    # find's own output is replayed, so the test needs no find binary and
    # checks the exact parsing of "%T@\t%d\t%p\0" rows.
    from types import SimpleNamespace

    from navin.webui import file_preview

    linux_root = tmp_path / "proj"
    unc = file_preview.Path("\\\\wsl.localhost\\Ubuntu" + str(linux_root).replace("/", "\\"))
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: "Ubuntu")
    monkeypatch.setattr(wsl, "command_prefix", lambda distro, cwd=None: ["wsl.exe", "-d", distro, "--"])
    calls = []
    listing = (
        f"1700000000.5\t2\t{linux_root}/backend/ocr.py\0"
        f"1700000900.0\t3\t{linux_root}/backend/deep/ocr.py\0"
        f"1700000100.0\t2\t{linux_root}/backend/other.py\0"
        "garbage-without-tabs\0"
    )

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=listing, stderr="")

    monkeypatch.setattr(file_preview.subprocess, "run", fake_run)
    monkeypatch.setattr(file_preview.sys, "platform", "win32")
    file_preview._BASENAME_WALK_CACHE.clear()
    file_preview._BASENAME_INDEX_CACHE.clear()
    # Two files share the name: the newest one (find's mtime) wins without a stat.
    assert file_preview._walk_unique_basename(unc, "ocr.py") == (unc / "backend" / "deep" / "ocr.py", True)
    assert file_preview._walk_unique_basename(unc, "other.py") == (unc / "backend" / "other.py", True)
    assert file_preview._walk_unique_basename(unc, "absent.py") == (None, True)
    # One wsl.exe for the three names, listing every file (no -iname filter).
    assert len(calls) == 1
    assert calls[0][:4] == ["wsl.exe", "-d", "Ubuntu", "--"]
    assert "find" in calls[0] and "-iname" not in calls[0]
