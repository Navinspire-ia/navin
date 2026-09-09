# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the pre-commit review prompt (code_review_api)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navin.webui.code_review_api import (
    build_review_prompt,
    collect_pending_changes,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
        },
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


class TestCollectPendingChanges:
    def test_not_a_repo(self, tmp_path: Path) -> None:
        result = collect_pending_changes(tmp_path)
        assert result["is_repo"] is False
        assert result["files"] == []

    def test_clean_repo_has_no_diff(self, repo: Path) -> None:
        result = collect_pending_changes(repo)
        assert result["is_repo"] is True
        assert result["files"] == []
        assert result["diff"].strip() == ""

    def test_modified_file_appears_in_diff(self, repo: Path) -> None:
        (repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
        result = collect_pending_changes(repo)
        assert {"path": "app.py", "status": "M"} in result["files"]
        assert "print('v2')" in result["diff"]

    def test_staged_change_is_included(self, repo: Path) -> None:
        (repo / "app.py").write_text("print('staged')\n", encoding="utf-8")
        _git(repo, "add", "app.py")
        result = collect_pending_changes(repo)
        assert "print('staged')" in result["diff"]

    def test_untracked_file_is_appended(self, repo: Path) -> None:
        (repo / "new.py").write_text("VALUE = 42\n", encoding="utf-8")
        result = collect_pending_changes(repo)
        assert {"path": "new.py", "status": "untracked"} in result["files"]
        assert "new.py (new file)" in result["diff"]
        assert "VALUE = 42" in result["diff"]

    def test_diff_is_bounded(self, repo: Path) -> None:
        (repo / "app.py").write_text("x = 1\n" * 20_000, encoding="utf-8")
        result = collect_pending_changes(repo, max_diff_chars=1_000)
        assert result["truncated"] is True
        assert len(result["diff"]) < 1_100
        assert "diff truncated" in result["diff"]


class TestBuildReviewPrompt:
    def test_prompt_carries_context_and_diff(self) -> None:
        prompt = build_review_prompt(
            "feature/x",
            [{"path": "a.py", "status": "M"}],
            "diff --git a/a.py b/a.py\n+new line\n",
        )
        assert "Branch: feature/x" in prompt
        assert "a.py" in prompt
        assert "+new line" in prompt
        assert "Do not modify any file" in prompt

    def test_empty_diff_short_circuits(self) -> None:
        prompt = build_review_prompt("main", [], "  \n")
        assert "no pending diff" in prompt
        assert "--- pending diff ---" not in prompt


class TestExplainCiPrompt:
    def test_explain_mode_changes_instructions(self, monkeypatch) -> None:
        from navin.webui import github_pr_api

        monkeypatch.setattr(
            github_pr_api,
            "github_ci_status_payload",
            lambda scope: {
                "available": True,
                "branch": "main",
                "pr_url": "https://example.com/pr/1",
                "detail": "",
                "checks": {"failing": 0, "items": []},
            },
        )

        class Scope:
            project_path = Path(".")

        fix = github_pr_api.github_fix_ci_prompt_payload(Scope(), "fix")
        explain = github_pr_api.github_fix_ci_prompt_payload(Scope(), "explain")
        assert "apply_patch" in fix["prompt"]
        assert "Diagnosis only" in explain["prompt"]
        assert "do not modify any file" in explain["prompt"]
        assert "Branch: main" in explain["prompt"]
