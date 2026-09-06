"""Git blame payload for the editor current-line strip."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.webui import project_search


def _scope(root: Path) -> SimpleNamespace:
    return SimpleNamespace(project_path=root)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "Dev User")
    (root / "hello.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "hello.py")
    _git(root, "commit", "-m", "initial hello")
    return root


def test_parse_blame_porcelain_extracts_lines():
    sample = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 2\n"
        "author Dev User\n"
        "author-mail <dev@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "committer Dev User\n"
        "committer-mail <dev@example.com>\n"
        "committer-time 1700000000\n"
        "committer-tz +0000\n"
        "summary initial hello\n"
        "filename hello.py\n"
        "\tdef hello():\n"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 2 2\n"
        "author Dev User\n"
        "author-mail <dev@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "committer Dev User\n"
        "committer-mail <dev@example.com>\n"
        "committer-time 1700000000\n"
        "committer-tz +0000\n"
        "summary initial hello\n"
        "filename hello.py\n"
        "\t    return 1\n"
    )
    items = project_search._parse_blame_porcelain(sample)
    assert len(items) == 2
    assert items[0]["line"] == 1
    assert items[0]["author"] == "Dev User"
    assert items[0]["commit"] == "aaaaaaaaaaaa"
    assert items[0]["summary"] == "initial hello"
    assert items[1]["line"] == 2


def test_git_blame_payload_returns_committed_lines(repo: Path):
    payload = project_search.git_blame_payload(_scope(repo), "hello.py")
    assert payload["available"] is True
    assert payload["total"] >= 2
    by_line = {item["line"]: item for item in payload["items"]}
    assert 1 in by_line
    assert by_line[1]["author"] == "Dev User"
    assert "hello" in (by_line[1]["summary"] or "").lower()


def test_git_blame_payload_accepts_absolute_path(repo: Path):
    abs_path = str((repo / "hello.py").resolve())
    payload = project_search.git_blame_payload(_scope(repo), abs_path)
    assert payload["path"] == "hello.py"
    assert payload["total"] >= 1


def test_git_blame_untracked_is_empty_not_error(repo: Path):
    (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    payload = project_search.git_blame_payload(_scope(repo), "new.py")
    assert payload["available"] is True
    assert payload["items"] == []


def test_git_blame_basename_in_extra_root(tmp_path: Path, repo: Path):
    other = tmp_path / "other"
    nested = other / "navin" / "documents"
    nested.mkdir(parents=True)
    target = nested / "ppt_qa.py"
    target.write_text("ok = True\n", encoding="utf-8")
    payload = project_search.git_blame_payload(
        _scope(repo),
        "ppt_qa.py",
        extra_roots=[other],
    )
    assert payload["path"] == "navin/documents/ppt_qa.py"
    assert payload["items"] == []
