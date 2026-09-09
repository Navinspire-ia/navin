# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The Dev status bar must tell the truth: real branch, real host, real context.

The footer used to say "no git" for a healthy repository opened across the
WSL boundary, because git ran on the Windows side against a UNC path. These
tests pin the two data sources it draws from: the git argv selection and the
context-usage estimate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navin.webui.context_usage import context_usage_payload
from navin.webui.project_insights import (
    ProjectInsightsError,
    _git_argv,
    file_diagnostics_payload,
    git_status_payload,
)


def _run(cwd: Path, *argv: str) -> None:
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init", "-b", "prod")
    _run(repo, "git", "config", "user.email", "t@t")
    _run(repo, "git", "config", "user.name", "t")
    (repo / "a.txt").write_text("one\n")
    _run(repo, "git", "add", "a.txt")
    _run(repo, "git", "commit", "-m", "init")
    return repo


class TestGitStatusPayload:
    def test_reports_the_real_branch_of_a_repository(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        payload = git_status_payload(str(repo))
        assert payload["is_repo"] is True
        assert payload["branch"] == "prod"
        assert payload["dirty"] is False

    def test_sees_a_branch_switch_immediately(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _run(repo, "git", "checkout", "-b", "feature/x")
        assert git_status_payload(str(repo))["branch"] == "feature/x"

    def test_counts_pending_changes(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "a.txt").write_text("two\n")
        (repo / "new.txt").write_text("hello\n")
        payload = git_status_payload(str(repo))
        assert payload["dirty"] is True
        assert payload["unstaged"] == 1
        assert payload["untracked"] == 1

    def test_a_plain_folder_is_not_painted_as_a_repo(self, tmp_path: Path) -> None:
        payload = git_status_payload(str(tmp_path))
        assert payload["is_repo"] is False

    def test_git_argv_targets_the_local_git_off_windows(self, tmp_path: Path) -> None:
        # On Linux/macOS the WSL redirector never applies: the argv must be
        # plain local git addressed at the folder.
        argv = _git_argv(tmp_path)
        assert argv is not None
        assert argv[0].endswith("git")
        assert str(tmp_path) in argv


class TestFileDiagnosticsPathResolution:
    """Editor panes address files tree-relatively; the API must cope.

    A bare 'src/app.py' used to come back as a 400 'path must be absolute'
    that surfaced as a raw red toast in front of the user.
    """

    def test_a_relative_path_resolves_against_the_root(self, tmp_path: Path) -> None:
        (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
        payload = file_diagnostics_payload("note.txt", raw_root=str(tmp_path))
        assert payload["path"].endswith("note.txt")

    def test_an_absolute_path_needs_no_root(self, tmp_path: Path) -> None:
        target = tmp_path / "note.txt"
        target.write_text("hello\n", encoding="utf-8")
        payload = file_diagnostics_payload(str(target))
        assert payload["path"].endswith("note.txt")

    def test_a_relative_path_without_root_is_a_clear_client_error(self) -> None:
        with pytest.raises(ProjectInsightsError) as caught:
            file_diagnostics_payload("just-a-name.txt")
        assert "root" in caught.value.message

    def test_a_missing_file_is_a_404_not_a_crash(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectInsightsError) as caught:
            file_diagnostics_payload("absent.txt", raw_root=str(tmp_path))
        assert caught.value.status == 404


class TestContextUsagePayload:
    def test_estimates_tokens_from_the_session_messages(self) -> None:
        payload = context_usage_payload(
            {
                "messages": [
                    {"role": "user", "content": "hello world " * 100},
                    {"role": "assistant", "content": "sure thing " * 100},
                ]
            }
        )
        assert payload["messages"] == 2
        assert payload["tokens"] > 0
        by_id = {bucket["id"]: bucket["tokens"] for bucket in payload["buckets"]}
        assert by_id["conversation"] > 0
        assert payload["tokens"] == sum(by_id.values())
        if payload["context_window"]:
            assert payload["percent"] is not None
            assert 0 <= payload["percent"] <= 100

    def test_prefers_last_peak_prompt_over_message_only_count(self) -> None:
        payload = context_usage_payload(
            {
                "messages": [
                    {"role": "user", "content": "hi"},
                ],
                "metadata": {
                    "_last_context_usage": {
                        "prompt_tokens": 120_000,
                        "peak_prompt_tokens": 120_000,
                        "context_window": 200_000,
                        "source": "provider",
                        "billed_tokens_session": 2_000_000,
                    }
                },
            }
        )
        assert payload["tokens"] == 120_000
        assert payload["percent"] == 60.0
        assert payload["source"] == "provider"
        assert payload["billed_tokens_session"] == 2_000_000
        assert payload["buckets"] == []

    def test_persisted_profile_sections_become_navin_buckets(self) -> None:
        payload = context_usage_payload(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {
                    "_last_context_usage": {
                        "prompt_tokens": 40_000,
                        "peak_prompt_tokens": 40_000,
                        "context_window": 200_000,
                        "source": "provider",
                        "sections": {
                            "system.identity": 1_400,
                            "system.skills": 2_100,
                            "tools": 13_800,
                            "tools.mcp": 2_800,
                            "conversation": 19_900,
                        },
                    }
                },
            }
        )
        by_id = {b["id"]: b["tokens"] for b in payload["buckets"]}
        assert by_id["system"] == 1_400
        assert by_id["skills"] == 2_100
        assert by_id["tools"] == 13_800
        assert by_id["mcp"] == 2_800
        assert by_id["conversation"] == 19_900
        assert "other" not in by_id
        assert payload["tokens"] == sum(by_id.values())
        assert payload["tokens"] == 40_000

    def test_peak_surplus_is_not_dumped_into_other(self) -> None:
        payload = context_usage_payload(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {
                    "_last_context_usage": {
                        "prompt_tokens": 50_000,
                        "peak_prompt_tokens": 50_000,
                        "context_window": 200_000,
                        "source": "provider",
                        "sections": {
                            "system.identity": 1_400,
                            "tools": 13_800,
                            "conversation": 19_900,
                        },
                    }
                },
            }
        )
        by_id = {b["id"]: b["tokens"] for b in payload["buckets"]}
        assert "other" not in by_id
        assert payload["tokens"] == 1_400 + 13_800 + 19_900
        assert payload["peak_prompt_tokens"] == 50_000

    def test_token_estimate_caps_very_long_histories(self) -> None:
        payload = context_usage_payload(
            {
                "messages": [
                    {"role": "user", "content": f"turn {i} " * 8} for i in range(400)
                ]
            }
        )
        assert payload["messages"] == 400
        assert payload["tokens"] > 0

    def test_an_empty_or_missing_session_reads_zero(self) -> None:
        assert context_usage_payload(None)["tokens"] == 0
        assert context_usage_payload({})["messages"] == 0

    def test_garbage_rows_are_skipped_not_fatal(self) -> None:
        payload = context_usage_payload({"messages": ["junk", 42, {"role": "user", "content": "hi"}]})
        assert payload["messages"] == 1
        assert payload["tokens"] >= 0

    def test_included_explains_open_files_and_rules(self, tmp_path: Path) -> None:
        rules = tmp_path / ".navin" / "rules"
        rules.mkdir(parents=True)
        (rules / "style.md").write_text("No em dashes.\n", encoding="utf-8")
        payload = context_usage_payload(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "fix the helper",
                        "metadata": {
                            "open_files": ["src/a.ts", "src/b.ts"],
                            "file_mentions": [{"path": "docs/note.md"}],
                        },
                    }
                ]
            },
            project_path=tmp_path,
        )
        by_path = {row["path"]: row["reason"] for row in payload["included"]}
        assert by_path["src/a.ts"] == "open_file"
        assert by_path["docs/note.md"] == "file_mention"
        assert by_path[".navin/rules/style.md"] == "project_rules"
