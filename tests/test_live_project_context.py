# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import subprocess
from pathlib import Path

from navin.live.project_context import git_summary, project_context


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "salut-chat"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, email, file in (("Aymen", "aymen@example.com", "a.txt"), ("Salma", "salma@example.com", "b.txt"),
                              ("Aymen", "aymen@example.com", "c.txt")):
        (repo / file).write_text(file, encoding="utf-8")
        _git(repo, "add", file)
        _git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", f"add {file}")
    (repo / "README.md").write_text("# Salut chat\nA chat app.", encoding="utf-8")
    return repo


def test_git_summary_lists_contributors_commits_and_dirty_files(tmp_path: Path) -> None:
    summary = git_summary(_repo(tmp_path))
    assert summary["repository"] is True
    assert summary["contributors"][0] == {"name": "Aymen", "email": "aymen@example.com", "commits": 2}
    assert {c["name"] for c in summary["contributors"]} == {"Aymen", "Salma"}
    assert summary["total_commits"] == 3
    assert summary["recent_commits"][0]["subject"] == "add c.txt"
    assert "README.md" in summary["uncommitted_files"]


def test_project_context_reads_readme_and_survives_no_git(tmp_path: Path) -> None:
    ctx = project_context(_repo(tmp_path))
    assert ctx["project"]["name"] == "salut-chat"
    assert ctx["readme"].startswith("# Salut chat")
    assert "board" in ctx

    plain = tmp_path / "plain"
    plain.mkdir()
    assert project_context(plain)["git"] == {"repository": False}
    assert project_context(tmp_path / "missing")["project"] is None


def test_remote_credentials_are_stripped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://user:secret@github.com/acme/salut-chat.git")
    assert git_summary(repo)["remote"] == "https://github.com/acme/salut-chat.git"
