# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Board autonomy: consent settings, git isolation, PR/issue sync plumbing."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.board import autonomy as autonomy_mod
from navin.board import github_sync as github_mod
from navin.board.autonomy import (
    autonomy_settings_path,
    autonomy_state,
    current_branch,
    normalize_issues_repo,
    read_autonomy,
    runtime_lines,
    start_task_branch,
    task_branch_name,
    write_autonomy,
)
from navin.board.context import board_digest
from navin.board.github_sync import (
    close_task_issue,
    import_issues_to_board,
    list_github_issues,
    open_task_pr,
    push_task_to_issue,
)
from navin.board.store import ProjectBoardStore


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


class TestAutonomySettings(unittest.TestCase):
    def test_defaults_when_absent(self):
        with TemporaryDirectory() as tmp:
            settings = read_autonomy(tmp)
        self.assertFalse(settings["enabled"])
        self.assertIsNone(settings["consented_at"])
        # Off by default: the agent commits where the user already is, so a
        # branch switch is never something they discover after the fact.
        self.assertFalse(settings["auto_branch"])
        self.assertTrue(settings["open_pr_on_done"])
        self.assertFalse(settings["sync_github_issues"])
        self.assertFalse(settings["fix_issues"])

    def test_enable_records_consent_and_persists(self):
        with TemporaryDirectory() as tmp:
            written = write_autonomy(tmp, {"enabled": True}, actor="aymen")
            self.assertTrue(written["enabled"])
            self.assertIsNotNone(written["consented_at"])
            # Round-trips through disk.
            again = read_autonomy(tmp)
            self.assertTrue(again["enabled"])
            self.assertEqual(again["consented_at"], written["consented_at"])
            raw = json.loads(autonomy_settings_path(tmp).read_text(encoding="utf-8"))
            self.assertEqual(raw["updated_by"], "aymen")

    def test_disable_clears_consent(self):
        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"enabled": True})
            settings = write_autonomy(tmp, {"enabled": False})
            self.assertFalse(settings["enabled"])
            self.assertIsNone(settings["consented_at"])

    def test_unknown_field_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_autonomy(tmp, {"rm_rf": True})

    def test_issues_repo_accepts_what_a_user_pastes(self):
        for pasted, expected in (
            ("acme/widgets", "acme/widgets"),
            ("  acme/widgets  ", "acme/widgets"),
            ("https://github.com/acme/widgets", "acme/widgets"),
            ("https://github.com/acme/widgets/issues", "acme/widgets"),
            ("git@github.com:acme/widgets.git", "acme/widgets"),
            ("github.com/acme/widgets", "acme/widgets"),
            # Another forge keeps its host, or the panel would query github.com.
            ("https://gitlab.com/acme/widgets", "gitlab.com/acme/widgets"),
            # GitLab subgroups survive, on a URL and on a bare path.
            ("https://gitlab.com/group/sub/app/-/issues", "gitlab.com/group/sub/app"),
            ("group/sub/app", "group/sub/app"),
            (
                "https://forgejo.navinspire.ai/Navinspire/navin-claw/issues/4",
                "forgejo.navinspire.ai/Navinspire/navin-claw",
            ),
            (
                "git@forgejo.navinspire.ai:Navinspire/navin-claw.git",
                "forgejo.navinspire.ai/Navinspire/navin-claw",
            ),
        ):
            with self.subTest(pasted=pasted):
                self.assertEqual(normalize_issues_repo(pasted), expected)
        for junk in ("", "   ", "acme", "https://gitlab.com/acme", "/", "-/x"):
            with self.subTest(junk=junk):
                self.assertIsNone(normalize_issues_repo(junk))

    def test_issues_repo_persists_and_clears(self):
        with TemporaryDirectory() as tmp:
            written = write_autonomy(tmp, {"issues_repo": "https://github.com/acme/widgets"})
            self.assertEqual(written["issues_repo"], "acme/widgets")
            self.assertEqual(read_autonomy(tmp)["issues_repo"], "acme/widgets")
            # An empty value means "use this project's own remote again".
            self.assertIsNone(write_autonomy(tmp, {"issues_repo": ""})["issues_repo"])

    def test_unusable_issues_repo_is_rejected_not_stored(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_autonomy(tmp, {"issues_repo": "not a repo"})
            self.assertIsNone(read_autonomy(tmp)["issues_repo"])

    def test_external_tracker_is_told_to_the_agent(self):
        with TemporaryDirectory() as tmp:
            write_autonomy(
                tmp,
                {"enabled": True, "sync_github_issues": True, "issues_repo": "acme/widgets"},
            )
            digest = "\n".join(runtime_lines(tmp))
            self.assertIn("acme/widgets", digest)
            self.assertIn("target the repo acme/widgets", digest)

    def test_corrupt_file_falls_back_to_defaults(self):
        with TemporaryDirectory() as tmp:
            path = autonomy_settings_path(tmp)
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            self.assertFalse(read_autonomy(tmp)["enabled"])


class TestAutonomyState(unittest.TestCase):
    def test_effective_requires_enabled(self):
        with TemporaryDirectory() as tmp:
            state = autonomy_state(tmp)
            self.assertFalse(state["effective"]["auto_branch"])
            self.assertFalse(state["effective"]["chain_ready_tasks"])

    def test_global_kill_switch_wins(self):
        class _Cfg:
            auto_branch_enabled = False
            open_pr_enabled = True

        with TemporaryDirectory() as tmp:
            # Consent explicitly, otherwise the project default alone would
            # explain the False below and the kill-switch would go untested.
            write_autonomy(tmp, {"enabled": True, "auto_branch": True})
            with patch.object(autonomy_mod, "_board_git_config", return_value=_Cfg()):
                state = autonomy_state(tmp)
            self.assertFalse(state["effective"]["auto_branch"])
            self.assertTrue(state["effective"]["open_pr_on_done"])
            # The consent itself is untouched: only the effect is gated.
            self.assertTrue(state["enabled"])

    def test_runtime_lines_empty_when_off_and_present_when_on(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(runtime_lines(tmp), [])
            write_autonomy(tmp, {"enabled": True})
            lines = runtime_lines(tmp)
            self.assertTrue(lines)
            self.assertIn("ENABLED", lines[0])

    def test_enabling_autonomy_alone_does_not_move_the_user_off_their_branch(self):
        """Consenting to task chaining must not silently consent to branching."""
        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"enabled": True})
            state = autonomy_state(tmp)
            self.assertTrue(state["effective"]["chain_ready_tasks"])
            self.assertFalse(state["effective"]["auto_branch"])
            digest = "\n".join(runtime_lines(tmp))
            self.assertIn("Auto-branch is OFF", digest)
            self.assertNotIn("isolated navin/task-<id> branch", digest)
            # Opting in is what flips it, and the agent is then told to say so.
            write_autonomy(tmp, {"auto_branch": True})
            digest = "\n".join(runtime_lines(tmp))
            self.assertIn("isolated navin/task-<id> branch", digest)
            self.assertIn("which branch you moved to", digest)

    def test_fix_issues_flag_is_consented_and_gated(self):
        with TemporaryDirectory() as tmp:
            # Off by default even when autonomy is enabled.
            write_autonomy(tmp, {"enabled": True})
            state = autonomy_state(tmp)
            self.assertFalse(state["fix_issues"])
            self.assertFalse(state["effective"]["fix_issues"])
            self.assertNotIn("fix forge issues", "\n".join(runtime_lines(tmp)))
            # Explicit consent turns on the runtime instructions.
            write_autonomy(tmp, {"fix_issues": True})
            state = autonomy_state(tmp)
            self.assertTrue(state["effective"]["fix_issues"])
            digest = "\n".join(runtime_lines(tmp))
            # Wording stays forge-neutral: the same flow runs on GitLab and
            # Forgejo, where `gh issue close` means nothing.
            self.assertIn("fix forge issues", digest)
            self.assertNotIn("gh issue close", digest)
            self.assertIn("close the issue with a comment", digest)
            self.assertIn("Never close an issue", digest)
            # The effect disappears with the master toggle, consent stays.
            write_autonomy(tmp, {"enabled": False})
            state = autonomy_state(tmp)
            self.assertTrue(state["fix_issues"])
            self.assertFalse(state["effective"]["fix_issues"])
            self.assertEqual(runtime_lines(tmp), [])

    def test_board_digest_includes_autonomy(self):
        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"enabled": True})
            digest = "\n".join(board_digest(tmp))
            self.assertIn("autonomy", digest.lower())


class TestTaskBranch(unittest.TestCase):
    def test_branch_name_is_slugged_and_bounded(self):
        task = {"id": "t-abc12345", "title": "Fix the Login FLOW !!"}
        self.assertEqual(task_branch_name(task), "navin/task-t-abc12345-fix-the-login-flow")

    def test_not_a_repo_is_reported_not_raised(self):
        with TemporaryDirectory() as tmp:
            result = start_task_branch(tmp, {"id": "t-1", "title": "x"})
        self.assertFalse(result["ok"])
        self.assertIn("not a git repository", result["detail"])

    def test_creates_switches_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            task = {"id": "t-42", "title": "Add API"}
            base = current_branch(root)
            first = start_task_branch(root, task)
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["branch"], "navin/task-t-42-add-api")
            # The branch left behind is reported so the move can be shown to
            # the user instead of being discovered on their next commit.
            self.assertEqual(first["base"], base)
            self.assertTrue(first["created"])
            # Same call again: already on the branch, still ok.
            second = start_task_branch(root, task)
            self.assertTrue(second["ok"])
            self.assertIn("already", second["detail"])
            self.assertFalse(second["created"])

    def test_switches_back_to_existing_branch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            task = {"id": "t-9", "title": "resume me"}
            start_task_branch(root, task)
            _git(root, "switch", "-q", "-")  # back to the original branch
            result = start_task_branch(root, task)
            self.assertTrue(result["ok"])
            self.assertIn("existing", result["detail"])

    def test_dirty_tree_is_preserved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "wip.txt").write_text("draft\n", encoding="utf-8")
            result = start_task_branch(root, {"id": "t-7", "title": "dirty"})
            self.assertTrue(result["ok"])
            self.assertTrue((root / "wip.txt").is_file())


class TestStoreGitFields(unittest.TestCase):
    def test_branch_pr_issue_fields_persist(self):
        with TemporaryDirectory() as tmp:
            store = ProjectBoardStore(tmp)
            task = store.create_task(title="ship it", actor="navin", actor_type="agent")
            updated = store.update_task(
                task["id"],
                fields={
                    "branch": "navin/task-x-ship-it",
                    "pr_url": "https://github.com/o/r/pull/1",
                    "issue_url": "https://github.com/o/r/issues/2",
                    "head_sha": "abc123def456",
                },
                actor="navin",
                actor_type="agent",
            )
            self.assertEqual(updated["branch"], "navin/task-x-ship-it")
            reloaded = store.get_task(task["id"])
            self.assertEqual(reloaded["pr_url"], "https://github.com/o/r/pull/1")
            self.assertEqual(reloaded["issue_url"], "https://github.com/o/r/issues/2")
            self.assertEqual(reloaded["head_sha"], "abc123def456")


class TestGithubSync(unittest.TestCase):
    def test_open_task_pr_without_gh_or_forge_token(self):
        """No `gh` and no forge token: say so, and name where to fix it."""
        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=False):
                with patch.object(
                    github_mod,
                    "_git",
                    side_effect=[
                        (0, "true", ""),  # is-inside-work-tree
                        (0, "deadbeefabcd", ""),  # rev-parse short HEAD
                        (0, "navin/task-t-1-x", ""),  # current branch
                        (0, "", ""),  # status porcelain
                        (0, "", ""),  # push
                    ],
                ):
                    with patch.object(
                        github_mod, "_open_pr_via_forge", return_value=None,
                    ):
                        result = open_task_pr(tmp, {"id": "t-1", "title": "x"})
        self.assertFalse(result["ok"])
        self.assertIn("gh CLI", result["detail"])
        self.assertIn("Settings > Git", result["detail"])

    def test_open_task_pr_uses_forge_api_when_token_present(self):
        """A Forgejo remote with a token opens the PR without `gh`."""
        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=False):
                with patch.object(
                    github_mod,
                    "_git",
                    side_effect=[
                        (0, "true", ""),
                        (0, "deadbeefabcd", ""),
                        (0, "navin/task-t-1-x", ""),
                        (0, "", ""),
                        (0, "", ""),
                    ],
                ):
                    with patch.object(
                        github_mod,
                        "_open_pr_via_forge",
                        return_value={
                            "ok": True,
                            "pr_url": "https://forgejo.example.com/o/r/pulls/3",
                            "head_sha": "deadbeefabcd",
                            "detail": "PR opened",
                        },
                    ):
                        result = open_task_pr(tmp, {"id": "t-1", "title": "x"})
        self.assertTrue(result["ok"])
        self.assertIn("forgejo.example.com", result["pr_url"])

    def test_open_task_pr_outside_repo(self):
        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=True):
                result = open_task_pr(tmp, {"id": "t-1", "title": "x"})
        self.assertFalse(result["ok"])
        self.assertIn("not a git repository", result["detail"])
        self.assertIsNone(result.get("head_sha"))

    def test_open_task_pr_reuses_existing_with_head_sha(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(
                ["git", "init", "-q", "-b", "main"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "t@t"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "f.txt").write_text("x\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-A"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "checkout", "-b", "navin/task-t1-ship"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            with patch.object(github_mod, "gh_available", return_value=True):
                with patch.object(
                    github_mod,
                    "_git",
                    side_effect=[
                        (0, "true", ""),  # is-inside-work-tree
                        (0, "deadbeefabcd", ""),  # rev-parse short HEAD
                        (0, "navin/task-t1-ship", ""),  # current branch
                        (0, "", ""),  # status porcelain
                        (0, "", ""),  # push
                    ],
                ):
                    with patch.object(
                        github_mod,
                        "_gh",
                        return_value=(0, "https://github.com/o/r/pull/42\n", ""),
                    ):
                        result = open_task_pr(
                            root,
                            {
                                "id": "t1",
                                "title": "ship",
                                "branch": "navin/task-t1-ship",
                            },
                        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["pr_url"], "https://github.com/o/r/pull/42")
        self.assertEqual(result["head_sha"], "deadbeefabcd")

    def test_list_issues_without_gh(self):
        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=False):
                result = list_github_issues(tmp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["install"]["needed"])
        self.assertIn(result["install"]["platform"], ("macos", "linux", "wsl", "windows"))
        self.assertTrue(result["install"]["command"])
        self.assertEqual(result["install"]["auth_command"], "gh auth login")

    def test_install_guide_matches_the_host(self):
        from navin.board.github_sync import gh_install_guide

        windows = gh_install_guide(platform="windows")
        self.assertIn("winget", windows["command"])
        self.assertEqual(windows["flavor"], "windows")
        macos = gh_install_guide(platform="macos")
        self.assertIn("brew install gh", macos["command"])
        linux_deb = gh_install_guide(platform="linux")
        self.assertIn(linux_deb["flavor"], ("deb", "rpm", "arch", "omarchy", "other"))
        ids = {item["id"] for item in linux_deb["alternatives"]}
        self.assertIn("windows", ids)
        self.assertIn("macos", ids)
        wsl = gh_install_guide(platform="wsl")
        self.assertEqual(wsl["platform"], "wsl")
        self.assertRegex(wsl["command"], r"\b(gh|github-cli|GitHub\.cli)\b")

    def test_list_issues_without_remotes_explains_parent_folder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(root, "init")
            with patch.object(github_mod, "gh_available", return_value=True):
                result = list_github_issues(root)
        self.assertFalse(result["ok"])
        self.assertIn("no git remotes found", result["detail"])
        self.assertIn("NavinProjects", result["detail"])

    def test_mirror_issue_is_filed_in_the_configured_repo(self):
        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, "https://github.com/acme/widgets/issues/9\n", ""

        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"issues_repo": "acme/widgets"})
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                result = push_task_to_issue(tmp, {"id": "t-1", "title": "ship it"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["repo"], "acme/widgets")
        self.assertEqual(calls[0][calls[0].index("--repo") + 1], "acme/widgets")

    def test_closing_comment_cites_the_pull_request_and_evidence(self):
        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, "", ""

        task = {
            "id": "t-1",
            "title": "Crash on login",
            "issue_url": "https://github.com/acme/widgets/issues/7",
            "pr_url": "https://github.com/me/fork/pull/12",
            "evidence": "pytest tests/test_login.py: 3 passed",
        }
        with TemporaryDirectory() as tmp:
            with (
                # No forge layer: this exercises the legacy github.com road.
                patch.object(github_mod, "_forge_api", return_value=None),
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                result = close_task_issue(tmp, task)
        self.assertTrue(result["ok"], result)
        args = calls[0]
        # The issue is addressed by URL, which is what makes cross-repo work.
        self.assertIn("https://github.com/acme/widgets/issues/7", args)
        comment = args[args.index("--comment") + 1]
        self.assertIn("https://github.com/me/fork/pull/12", comment)
        self.assertIn("3 passed", comment)

    def test_list_issues_reads_the_configured_repo_without_a_local_remote(self):
        """A folder with no GitHub remote still shows the tracker it follows."""
        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, "[]", ""

        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"issues_repo": "acme/widgets"})
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                result = list_github_issues(tmp)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["repo"], "acme/widgets")
        self.assertIn("--repo", calls[0])
        self.assertEqual(calls[0][calls[0].index("--repo") + 1], "acme/widgets")

    def test_list_issues_argument_wins_over_the_setting(self):
        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, "[]", ""

        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"issues_repo": "acme/widgets"})
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                result = list_github_issues(tmp, repo="https://github.com/other/tracker")
        self.assertEqual(result["repo"], "other/tracker")
        self.assertEqual(calls[0][calls[0].index("--repo") + 1], "other/tracker")

    def test_list_issues_without_a_repo_still_requires_a_remote(self):
        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=True):
                result = list_github_issues(tmp)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["repo"])
        self.assertIn("not a git repository", result["detail"])

    def test_import_pulls_the_configured_repo_onto_this_board(self):
        issue = {
            "title": "Upstream crash",
            "body": "boom",
            "url": "https://github.com/acme/widgets/issues/3",
            "labels": [],
        }
        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, json.dumps([issue]), ""

        with TemporaryDirectory() as tmp:
            write_autonomy(tmp, {"issues_repo": "acme/widgets"})
            store = ProjectBoardStore(tmp)
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                result = import_issues_to_board(tmp, store, actor="navin")
            tasks = store.read_tasks()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["repo"], "acme/widgets")
        self.assertIn("from acme/widgets", result["detail"])
        self.assertEqual(calls[0][calls[0].index("--repo") + 1], "acme/widgets")
        self.assertEqual(tasks[0]["issue_url"], "https://github.com/acme/widgets/issues/3")

    def test_import_issues_creates_and_dedupes(self):
        issues = [
            {
                "title": "Crash on login",
                "body": "stacktrace...",
                "url": "https://github.com/o/r/issues/1",
                "labels": [{"name": "bug"}],
            },
            {
                "title": "Docs missing",
                "body": "",
                "url": "https://github.com/o/r/issues/2",
                "labels": [],
            },
        ]
        with TemporaryDirectory() as tmp:
            store = ProjectBoardStore(tmp)
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(
                    github_mod, "_gh", return_value=(0, json.dumps(issues), "")
                ),
            ):
                first = import_issues_to_board(tmp, store, actor="navin")
                second = import_issues_to_board(tmp, store, actor="navin")
            tasks = store.read_tasks()
        self.assertTrue(first["ok"])
        self.assertEqual(first["imported"], 2)
        # Idempotent: the same issues do not import twice.
        self.assertEqual(second["imported"], 0)
        self.assertEqual(len(tasks), 2)
        by_url = {t["issue_url"] for t in tasks}
        self.assertIn("https://github.com/o/r/issues/1", by_url)
        crash = next(t for t in tasks if t["title"] == "Crash on login")
        self.assertIn("github", crash["labels"])
        self.assertIn("bug", crash["labels"])

    def test_import_reports_gh_failure(self):
        with TemporaryDirectory() as tmp:
            store = ProjectBoardStore(tmp)
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", return_value=(1, "", "no remote")),
            ):
                result = import_issues_to_board(tmp, store, actor="navin")
        self.assertFalse(result["ok"])
        self.assertIn("no remote", result["detail"])


class TestWebApi(unittest.TestCase):
    def test_autonomy_update_payload_round_trip(self):
        from navin.webui.board_api import board_autonomy_update_payload

        class _Scope:
            def __init__(self, path: str) -> None:
                self.project_path = path

        with TemporaryDirectory() as tmp:
            state = board_autonomy_update_payload(
                _Scope(tmp), {"enabled": True, "sync_github_issues": True}, actor="aymen"
            )
            self.assertTrue(state["enabled"])
            self.assertTrue(state["sync_github_issues"])
            self.assertIn("effective", state)
            # The consent is visible in the activity timeline.
            entries = ProjectBoardStore(tmp).read_activity()
            self.assertTrue(any(e["kind"] == "autonomy_updated" for e in entries))

    def test_board_payload_carries_autonomy(self):
        from navin.webui.board_api import board_payload

        class _Scope:
            def __init__(self, path: str) -> None:
                self.project_path = path

        with TemporaryDirectory() as tmp:
            payload = board_payload(_Scope(tmp))
            self.assertIn("autonomy", payload)
            self.assertFalse(payload["autonomy"]["enabled"])

    def test_issues_repo_endpoint_round_trip(self):
        from navin.webui.board_api import github_issues_payload, github_issues_repo_payload

        class _Scope:
            def __init__(self, path: str) -> None:
                self.project_path = path

        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, "[]", ""

        with TemporaryDirectory() as tmp:
            saved = github_issues_repo_payload(
                _Scope(tmp), "https://github.com/acme/widgets", actor="aymen"
            )
            self.assertEqual(saved["repo"], "acme/widgets")
            # The panel reads back the tracker it just chose.
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                payload = github_issues_payload(_Scope(tmp))
            self.assertEqual(payload["repo"], "acme/widgets")
            self.assertEqual(calls[0][calls[0].index("--repo") + 1], "acme/widgets")
            # And clearing it goes back to the project's own remote.
            self.assertIsNone(github_issues_repo_payload(_Scope(tmp), "")["repo"])
            entries = ProjectBoardStore(tmp).read_activity()
            self.assertTrue(any(e["kind"] == "issues_repo_updated" for e in entries))

    def test_issues_repo_endpoint_rejects_junk(self):
        from navin.board.store import BoardError
        from navin.webui.board_api import github_issues_repo_payload

        class _Scope:
            def __init__(self, path: str) -> None:
                self.project_path = path

        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardError):
                github_issues_repo_payload(_Scope(tmp), "definitely not a repo")

    def test_human_sync_github_imports_the_repo_on_screen(self):
        from navin.webui.board_api import board_update_payload

        class _Scope:
            def __init__(self, path: str) -> None:
                self.project_path = path

        calls: list[tuple[str, ...]] = []

        def _record(_root, *args):
            calls.append(args)
            return 0, "[]", ""

        with TemporaryDirectory() as tmp:
            with (
                patch.object(github_mod, "gh_available", return_value=True),
                patch.object(github_mod, "_gh", side_effect=_record),
            ):
                board_update_payload(
                    _Scope(tmp), {"action": "sync_github", "repo": "acme/widgets"}
                )
        self.assertEqual(calls[0][calls[0].index("--repo") + 1], "acme/widgets")

    def test_human_sync_github_fails_cleanly_without_gh(self):
        from navin.board.store import BoardError
        from navin.webui.board_api import board_update_payload

        class _Scope:
            def __init__(self, path: str) -> None:
                self.project_path = path

        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=False):
                with self.assertRaises(BoardError):
                    board_update_payload(_Scope(tmp), {"action": "sync_github"})


class TestIssuesRepoRoute(unittest.TestCase):
    """The Issues panel talks HTTP, so the route itself must be exercised.

    A payload builder that works while its route is unwired reads in the UI as
    "API route not found" - a failure no payload-level test would catch.
    """

    def _handler(self, project: str):
        from navin.webui.ws_http import GatewayHTTPHandler

        class _Scope:
            project_path = project

        class _Workspaces:
            def scope_for_session_key(self, key: str):
                return _Scope()

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.bus = None
        handler.workspaces = _Workspaces()
        return handler

    def _get(self, handler, path: str):
        import asyncio

        class _Request:
            def __init__(self, full_path: str) -> None:
                self.path = full_path
                self.headers = {}

        got = path.split("?", 1)[0]
        return asyncio.run(handler._dispatch_session_routes(_Request(path), got))

    def test_setting_the_repo_over_http(self):
        with TemporaryDirectory() as tmp:
            handler = self._handler(tmp)
            response = self._get(
                handler,
                "/api/sessions/websocket%3Aabc/github/issues/repo"
                "?repo=https://github.com/acme/widgets",
            )
            self.assertIsNotNone(response, "route not registered")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.body)["repo"], "acme/widgets")
            self.assertEqual(read_autonomy(tmp)["issues_repo"], "acme/widgets")
            # Clearing it travels the same way.
            response = self._get(
                handler, "/api/sessions/websocket%3Aabc/github/issues/repo?repo="
            )
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(json.loads(response.body)["repo"])

    def test_a_bad_repo_answers_400_not_500(self):
        with TemporaryDirectory() as tmp:
            response = self._get(
                self._handler(tmp),
                "/api/sessions/websocket%3Aabc/github/issues/repo?repo=nonsense",
            )
            self.assertEqual(response.status_code, 400)

    def test_the_repo_route_does_not_shadow_the_issues_list(self):
        with TemporaryDirectory() as tmp:
            with patch.object(github_mod, "gh_available", return_value=False):
                response = self._get(
                    self._handler(tmp), "/api/sessions/websocket%3Aabc/github/issues?state=open"
                )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(json.loads(response.body)["ok"])


class TestNeverLoseWork(unittest.TestCase):
    """The autonomy git plumbing must never contain a work-destroying command.

    This is a guard against regressions: if someone adds a reset, clean, force
    push, forced checkout or branch delete to these modules, this test fails
    before any user loses anything.
    """

    _FORBIDDEN = (
        r"reset",
        r"clean",
        r"--force",
        r"\"-f\"",
        r"'-f'",
        r"-D\b",
        r"--delete",
        r"stash drop",
        r"filter-branch",
        r"update-ref -d",
    )

    def _scan(self, module) -> list[str]:
        import inspect
        import re

        source = inspect.getsource(module)
        hits = []
        for line in source.splitlines():
            code = line.split("#", 1)[0]  # comments may name the enemy; code may not
            if '"git"' not in code and "_git(" not in code and "_gh(" not in code:
                continue
            for pattern in self._FORBIDDEN:
                if re.search(pattern, code):
                    hits.append(line.strip())
        return hits

    def test_autonomy_module_has_no_destructive_git(self):
        self.assertEqual(self._scan(autonomy_mod), [])

    def test_github_sync_module_has_no_destructive_git(self):
        self.assertEqual(self._scan(github_mod), [])

    def test_branching_preserves_uncommitted_work_and_untracked_files(self):
        """End to end: claim-branch on a dirty tree, nothing is ever lost."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "tracked.py").write_text("v1\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "add tracked")
            # Uncommitted edit + untracked file: the exact things a hard
            # reset or clean would destroy.
            (root / "tracked.py").write_text("v2 uncommitted\n", encoding="utf-8")
            (root / "untracked-notes.md").write_text("draft\n", encoding="utf-8")

            result = start_task_branch(root, {"id": "t-keep", "title": "safety"})
            self.assertTrue(result["ok"], result)

            self.assertEqual(
                (root / "tracked.py").read_text(encoding="utf-8"), "v2 uncommitted\n"
            )
            self.assertEqual(
                (root / "untracked-notes.md").read_text(encoding="utf-8"), "draft\n"
            )


class TestGlobalKillSwitchEndpoint(unittest.TestCase):
    """Settings > Git: /api/settings/update toggles tools.board_git."""

    def _update(self, query: dict[str, list[str]]):
        from navin.config.schema import Config
        from navin.webui import settings_api

        config = Config()
        saved: list = []
        with (
            patch.object(settings_api, "load_config", return_value=config),
            patch.object(settings_api, "save_config", side_effect=saved.append),
            patch.object(settings_api, "settings_payload", return_value={}),
        ):
            settings_api.update_agent_settings(query)
        return config, saved

    def test_disable_auto_branch_persists(self):
        config, saved = self._update({"board_auto_branch": ["false"]})
        self.assertFalse(config.tools.board_git.auto_branch_enabled)
        self.assertTrue(config.tools.board_git.open_pr_enabled)
        self.assertEqual(len(saved), 1)

    def test_disable_open_pr_persists(self):
        config, saved = self._update({"boardOpenPr": ["false"]})
        self.assertFalse(config.tools.board_git.open_pr_enabled)
        self.assertTrue(config.tools.board_git.auto_branch_enabled)
        self.assertEqual(len(saved), 1)

    def test_noop_value_does_not_save(self):
        config, saved = self._update({"board_auto_branch": ["true"]})
        self.assertTrue(config.tools.board_git.auto_branch_enabled)
        self.assertEqual(saved, [])


_GH_STUB = """#!/bin/sh
# Records every invocation, then mimics the gh subcommands the pipeline uses.
echo "$@" >> "$GH_STUB_LOG"
case "$1 $2" in
  "pr view") exit 1 ;;
  "pr create") echo "https://github.example/acme/repo/pull/1"; exit 0 ;;
  "issue create") echo "https://github.example/acme/repo/issues/7"; exit 0 ;;
  "issue close") exit 0 ;;
  "issue list") echo '[{"number":7,"title":"Login broken","body":"login 500",\
"url":"https://github.example/acme/repo/issues/7","labels":[{"name":"bug"}]}]'; exit 0 ;;
esac
exit 1
"""


class TestEndToEndAutonomyPipeline(unittest.TestCase):
    """The full loop through the real BoardTool: import an issue, claim it
    (auto-branch), commit work, move to done (auto-commit leftovers, push to
    the remote, open the PR, close the linked issue) - on a real git repo
    with a local bare remote and a stubbed ``gh``.
    """

    def setUp(self):
        import os

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)

        # Real project repo with one commit, plus a local bare "origin".
        self.project = base / "project"
        self.project.mkdir()
        _init_repo(self.project)
        self.origin = base / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.origin)],
            check=True, capture_output=True, text=True,
        )
        _git(self.project, "remote", "add", "origin", str(self.origin))
        self.start_branch = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        # Stubbed gh at the front of PATH, logging every call.
        bin_dir = base / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(_GH_STUB, encoding="utf-8")
        gh.chmod(0o755)
        self.gh_log = base / "gh-calls.log"
        self.gh_log.write_text("", encoding="utf-8")
        env = patch.dict(
            os.environ,
            {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
             "GH_STUB_LOG": str(self.gh_log)},
        )
        env.start()
        self.addCleanup(env.stop)

        # Full consent, hermetic global kill-switches.
        write_autonomy(
            self.project,
            {
                "enabled": True,
                "auto_branch": True,
                "open_pr_on_done": True,
                "sync_github_issues": True,
            },
        )
        cfg = patch.object(autonomy_mod, "_board_git_config", return_value=None)
        cfg.start()
        self.addCleanup(cfg.stop)

    def _run(self, coro):
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_issue_to_closed_pr_pipeline(self):
        from navin.agent.tools.board import BoardTool

        tool = BoardTool(workspace=self.project)
        store = ProjectBoardStore(self.project)

        # 1. GitHub issue imported as a board task (deduplicated by URL).
        result = self._run(tool.execute(action="sync_github", actor="navin"))
        self.assertIn("1 issue(s) imported", result)
        task = store.read_tasks()[0]
        self.assertEqual(task["issue_url"], "https://github.example/acme/repo/issues/7")
        self.assertIn("github", task["labels"])
        self.assertIn("bug", task["labels"])
        again = self._run(tool.execute(action="sync_github", actor="navin"))
        self.assertIn("0 issue(s) imported", again)

        # 2. Claim: the isolated branch is created and recorded on the task.
        result = self._run(
            tool.execute(action="claim", task_id=task["id"], actor="navin")
        )
        self.assertIn("Isolated branch ready", result)
        branch = f"navin/task-{task['id']}-login-broken"
        current = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(current, branch)
        task = store.get_task(task["id"])
        self.assertEqual(task["branch"], branch)
        self.assertEqual(task["status"], "in_progress")
        self.assertTrue(
            any("isolated branch" in c["text"].lower() for c in task["comments"])
        )

        # 3. Development: one committed fix plus a leftover uncommitted change.
        (self.project / "fix.py").write_text("def login():\n    return True\n",
                                             encoding="utf-8")
        _git(self.project, "add", "-A")
        _git(self.project, "commit", "-q", "-m", "fix login 500")
        (self.project / "notes.md").write_text("leftover\n", encoding="utf-8")

        # 4. Done: leftovers committed, branch pushed, PR opened, issue closed.
        result = self._run(
            tool.execute(
                action="move", task_id=task["id"], status="done",
                evidence="pytest tests/test_login.py: 3 passed", actor="navin",
            )
        )
        self.assertIn("Pull request opened: https://github.example/acme/repo/pull/1",
                      result)
        self.assertIn("Linked issue closed", result)

        task = store.get_task(task["id"])
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["pr_url"], "https://github.example/acme/repo/pull/1")

        # The branch reached the remote, carrying both commits.
        remote_log = subprocess.run(
            ["git", "-C", str(self.origin), "log", "--format=%s",
             f"refs/heads/{branch}"],
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertIn("fix login 500", remote_log)
        self.assertIn(f"task {task['id']}", remote_log)  # auto-committed leftovers

        # The starting branch was never touched (still the single init commit).
        local_log = subprocess.run(
            ["git", "-C", str(self.project), "log", "--format=%s",
             self.start_branch],
            check=True, capture_output=True, text=True,
        ).stdout.strip().splitlines()
        self.assertEqual(local_log, ["init"])

        # gh really was driven through the whole chain.
        calls = self.gh_log.read_text(encoding="utf-8")
        self.assertIn("issue list", calls)
        self.assertIn("pr create", calls)
        self.assertIn("issue close https://github.example/acme/repo/issues/7", calls)

        # The whole story is auditable in the task comments.
        comments = " | ".join(c["text"] for c in task["comments"])
        self.assertIn("Pull request: https://github.example/acme/repo/pull/1", comments)


class _Response:
    def __init__(self, status_code: int, payload=None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ("{}" if payload is None else "json")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


class TestBoardIssuesOnEveryForge(unittest.TestCase):
    """The board mirrors issues on Forgejo and GitLab, not only on GitHub.

    Every call is mocked at the HTTP layer: these tests must pass offline and
    must not change behaviour on a machine with a signed-in ``gh``.
    """

    def _forge(self, kind: str, host: str, owner: str = "Navinspire", repo: str = "app"):
        from navin.webui.forge_api import ForgeRemote

        return ForgeRemote(kind=kind, host=host, owner=owner, repo=repo)

    def _patched(self, remote, responses, token: str = "t0k3n"):
        """Board code talking to one mocked forge, with no config on disk."""
        from navin.webui import forge_api

        recorder = _Recorder(responses)
        stack = [
            patch.object(github_mod, "_config", return_value=None),
            patch.object(forge_api, "detect_forge", return_value=remote),
            patch.object(forge_api, "resolve_token", return_value=(token, "settings")),
            patch.object(forge_api.httpx, "request", recorder),
        ]
        return recorder, stack

    @staticmethod
    def _enter(stack):
        for ctx in stack:
            ctx.start()

    @staticmethod
    def _exit(stack):
        for ctx in reversed(stack):
            ctx.stop()

    def test_task_is_mirrored_as_a_forgejo_issue(self):
        remote = self._forge("forgejo", "forgejo.navinspire.ai")
        recorder, stack = self._patched(
            remote,
            [
                _Response(200, [{"id": 3, "name": "bug"}]),
                _Response(
                    201,
                    {
                        "number": 11,
                        "html_url": "https://forgejo.navinspire.ai/Navinspire/app/issues/11",
                    },
                ),
            ],
        )
        self._enter(stack)
        try:
            with TemporaryDirectory() as tmp:
                result = push_task_to_issue(
                    tmp,
                    {"id": "t-1", "title": "ship it", "description": "why", "labels": ["bug"]},
                )
        finally:
            self._exit(stack)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["issue_url"], "https://forgejo.navinspire.ai/Navinspire/app/issues/11"
        )
        self.assertEqual(result["forge"], "forgejo")
        create = recorder.calls[1]
        self.assertEqual(
            create["url"],
            "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/app/issues",
        )
        self.assertIn("Board task: `t-1`", create["json"]["body"])

    def test_issues_panel_lists_a_gitlab_project(self):
        remote = self._forge("gitlab", "gitlab.com", owner="group/sub", repo="app")
        recorder, stack = self._patched(
            remote,
            [
                _Response(
                    200,
                    [
                        {
                            "iid": 4,
                            "id": 90210,
                            "title": "Crash",
                            "description": "boom",
                            "web_url": "https://gitlab.com/group/sub/app/-/issues/4",
                            "state": "opened",
                            "labels": ["bug"],
                            "author": {"username": "ada"},
                        }
                    ],
                )
            ],
        )
        self._enter(stack)
        try:
            with TemporaryDirectory() as tmp:
                result = list_github_issues(tmp, state="open")
        finally:
            self._exit(stack)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["forge"], "gitlab")
        self.assertEqual(result["forge_label"], "GitLab")
        self.assertEqual(result["remote_repo"], "group/sub/app")
        issue = result["issues"][0]
        self.assertEqual(issue["number"], 4)
        self.assertEqual(issue["state"], "open")
        self.assertTrue(
            recorder.calls[0]["url"].endswith("/projects/group%2Fsub%2Fapp/issues")
        )

    def test_import_tags_tasks_with_the_forge_name(self):
        remote = self._forge("forgejo", "forgejo.navinspire.ai")
        recorder, stack = self._patched(
            remote,
            [
                _Response(
                    200,
                    [
                        {
                            "number": 2,
                            "title": "Upstream crash",
                            "body": "boom",
                            "html_url": "https://forgejo.navinspire.ai/Navinspire/app/issues/2",
                            "state": "open",
                            "labels": [{"name": "bug"}],
                        }
                    ],
                )
            ],
        )
        self._enter(stack)
        try:
            with TemporaryDirectory() as tmp:
                store = ProjectBoardStore(tmp)
                result = import_issues_to_board(tmp, store, actor="navin")
                tasks = store.read_tasks()
        finally:
            self._exit(stack)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["imported"], 1)
        self.assertIn("forgejo", tasks[0]["labels"])
        self.assertIn("bug", tasks[0]["labels"])
        self.assertEqual(
            tasks[0]["issue_url"],
            "https://forgejo.navinspire.ai/Navinspire/app/issues/2",
        )
        del recorder

    def test_linked_issue_is_closed_on_its_own_host(self):
        from navin.webui import forge_api

        recorder = _Recorder([_Response(201, {"id": 1}), _Response(200, {})])
        task = {
            "id": "t-1",
            "issue_url": "https://forgejo.navinspire.ai/Navinspire/app/issues/9",
            "pr_url": "https://forgejo.navinspire.ai/Navinspire/app/pulls/10",
            "evidence": "pytest: 3 passed",
        }
        with (
            patch.object(github_mod, "_config", return_value=None),
            patch.object(forge_api, "resolve_token", return_value=("t0k3n", "settings")),
            patch.object(forge_api.httpx, "request", recorder),
        ):
            with TemporaryDirectory() as tmp:
                result = close_task_issue(tmp, task)
        self.assertTrue(result["ok"], result)
        # The URL alone said host, repo and number: no local remote needed.
        self.assertEqual(
            recorder.calls[0]["url"],
            "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/app/issues/9/comments",
        )
        comment = recorder.calls[0]["json"]["body"]
        self.assertIn("pulls/10", comment)
        self.assertIn("3 passed", comment)
        self.assertEqual(recorder.calls[1]["json"], {"state": "closed"})

    def test_missing_token_is_actionable_and_offers_no_gh_on_forgejo(self):
        from navin.webui import forge_api

        remote = self._forge("forgejo", "forgejo.navinspire.ai")
        with (
            patch.object(github_mod, "_config", return_value=None),
            patch.object(github_mod, "gh_available", return_value=True),
            patch.object(forge_api, "detect_forge", return_value=remote),
            patch.object(forge_api, "resolve_token", return_value=("", "")),
        ):
            with TemporaryDirectory() as tmp:
                result = list_github_issues(tmp)
        self.assertFalse(result["ok"])
        self.assertIn("forgejo.navinspire.ai", result["detail"])
        self.assertIn("Settings > Git", result["detail"])
        self.assertEqual(result["token_setup"]["host"], "forgejo.navinspire.ai")
        self.assertIn(
            "NAVIN_FORGE_TOKEN_FORGEJO_NAVINSPIRE_AI", result["token_setup"]["env"]
        )
        # Installing the GitHub CLI would not help here.
        self.assertNotIn("install", result)

    def test_unknown_forge_asks_for_its_type(self):
        from navin.webui import forge_api

        remote = self._forge("unknown", "code.example.com")
        with (
            patch.object(github_mod, "_config", return_value=None),
            patch.object(github_mod, "gh_available", return_value=False),
            patch.object(forge_api, "detect_forge", return_value=remote),
            patch.object(forge_api, "resolve_kind", return_value=remote),
        ):
            with TemporaryDirectory() as tmp:
                result = list_github_issues(tmp)
        self.assertFalse(result["ok"])
        self.assertIn("code.example.com", result["detail"])
        self.assertIn("Settings > Git", result["detail"])

    def test_gitlab_tracker_reference_keeps_its_host(self):
        from navin.webui import forge_api

        origin = self._forge("forgejo", "forgejo.navinspire.ai")
        recorder = _Recorder([_Response(200, [])])
        with (
            patch.object(github_mod, "_config", return_value=None),
            patch.object(github_mod, "gh_available", return_value=True),
            patch.object(forge_api, "detect_forge", return_value=origin),
            patch.object(forge_api, "resolve_token", return_value=("t0k3n", "settings")),
            patch.object(forge_api.httpx, "request", recorder),
        ):
            with TemporaryDirectory() as tmp:
                result = list_github_issues(tmp, repo="https://gitlab.com/group/sub/app")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["repo"], "gitlab.com/group/sub/app")
        self.assertEqual(result["forge"], "gitlab")
        self.assertTrue(
            recorder.calls[0]["url"].startswith("https://gitlab.com/api/v4/projects/")
        )

    def test_http_error_from_the_forge_is_explained(self):
        remote = self._forge("forgejo", "forgejo.navinspire.ai")
        recorder, stack = self._patched(
            remote, [_Response(403, None, text="<html>forbidden</html>")]
        )
        self._enter(stack)
        try:
            with TemporaryDirectory() as tmp:
                result = list_github_issues(tmp)
        finally:
            self._exit(stack)
        self.assertFalse(result["ok"])
        self.assertIn("scope", result["detail"])
        self.assertNotIn("<html>", result["detail"])
        del recorder


if __name__ == "__main__":
    unittest.main()
