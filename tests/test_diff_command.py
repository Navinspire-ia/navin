from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.command import diff as d

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git missing")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=30)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mod.txt").write_text("".join(f"line{i}\n" for i in range(1, 21)), encoding="utf-8")
    (tmp_path / "gone.txt").write_text("bye\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    lines = (tmp_path / "mod.txt").read_text(encoding="utf-8").splitlines()
    lines[14] = "changed15"
    (tmp_path / "mod.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "new.txt").write_text("fresh\n", encoding="utf-8")
    _git(tmp_path, "add", "new.txt")
    (tmp_path / "loose.txt").write_text("a\nb\n", encoding="utf-8")
    return tmp_path


def test_file_list_with_counts_and_status(repo):
    out = d.diff_text(repo, "")
    head = out.split("\n\n")[1]
    assert "Uncommitted changes: 4 files, +4 -2" in out
    rows = {r.split()[0]: r for r in head.splitlines()}
    assert "+1 -1" in rows["mod.txt"] and rows["mod.txt"].endswith("modified")
    assert rows["gone.txt"].endswith("deleted") and "+0 -1" in rows["gone.txt"]
    assert rows["new.txt"].endswith("added")
    assert rows["loose.txt"].endswith("untracked") and "+2 -0" in rows["loose.txt"]


def test_hunks_carry_line_numbers(repo):
    out = d.diff_text(repo, "mod.txt")
    assert "  15 -line15" in out
    assert "  15 +changed15" in out
    assert "  14  line14" in out
    assert "gone.txt" not in out


def test_stat_only(repo):
    out = d.diff_text(repo, "--stat")
    assert "mod.txt" in out and "+changed15" not in out


def test_staged(repo):
    out = d.diff_text(repo, "--staged")
    assert out.startswith("Staged changes: 1 file")
    assert "new.txt" in out and "loose.txt" not in out


def test_rename(repo):
    _git(repo, "mv", "mod.txt", "moved.txt")
    out = d.diff_text(repo, "--stat --staged")
    assert "mod.txt -> moved.txt" in out and "renamed" in out


def test_non_git_folder(tmp_path):
    out = d.diff_text(tmp_path, "")
    assert "is not a git repository" in out


def test_bad_args(repo):
    assert "Unknown option" in d.diff_text(repo, "--nope")


def test_clean_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    assert d.diff_text(tmp_path, "") == "Uncommitted changes: none."


def test_truncation(repo):
    (repo / "big.txt").write_text("".join(f"x{i}\n" for i in range(900)), encoding="utf-8")
    out = d.diff_text(repo, "")
    assert "more lines (narrow with /diff <path>)" in out
    assert len(out.splitlines()) < d.MAX_BODY_LINES + 20


def test_command_and_help(repo):
    from navin.command.builtin import build_help_text, register_builtin_commands
    from navin.command.router import CommandContext, CommandRouter

    assert "/diff [--stat] [--staged] [path]" in build_help_text()
    router = CommandRouter()
    register_builtin_commands(router)
    msg = SimpleNamespace(channel="cli", chat_id="c", metadata={}, content="/diff --stat")
    ctx = CommandContext(msg=msg, session=None, key="k", raw="/diff --stat",
                         loop=SimpleNamespace(workspace=repo))
    out = asyncio.run(router.dispatch(ctx))
    assert out is not None and "mod.txt" in out.content and out.metadata["render_as"] == "text"
