"""PR merge -> board suggestions (no auto-close), on every forge.

The forge layer is always neutralized or mocked here: no test may reach a
real GitHub, GitLab or Forgejo server, and a machine with a signed-in ``gh``
must not change the outcome.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.board import pr_sync as pr_sync_mod
from navin.board.pr_sync import (
    check_pr_merged,
    pr_sync_payload,
    suggest_merged_task_prs,
)
from navin.board.store import ProjectBoardStore
from navin.webui.board_api import github_pr_sync_payload


class _Scope:
    def __init__(self, path: Path):
        self.project_path = path
        self.project_name = path.name


@contextlib.contextmanager
def _gh_only():
    """Force the legacy ``gh`` road: no forge module, no token lookup."""
    with patch.object(pr_sync_mod, "_forge_api", return_value=None):
        yield


class PrSyncTests(unittest.TestCase):
    def test_merged_pr_returns_suggestion(self) -> None:
        tasks = [
            {
                "id": "t-1",
                "status": "review",
                "pr_url": "https://github.com/o/r/pull/1",
            },
            {
                "id": "t-2",
                "status": "done",
                "pr_url": "https://github.com/o/r/pull/2",
            },
        ]
        merged_json = json.dumps(
            {
                "state": "MERGED",
                "mergedAt": "2026-01-01T00:00:00Z",
                "url": "https://github.com/o/r/pull/1",
            }
        )
        with (
            _gh_only(),
            patch.object(pr_sync_mod, "gh_available", return_value=True),
            patch.object(
                pr_sync_mod, "_gh", return_value=(0, merged_json, "")
            ) as gh,
        ):
            suggestions = suggest_merged_task_prs("/tmp/repo", tasks)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["task_id"], "t-1")
        self.assertTrue(suggestions[0]["merged"])
        self.assertEqual(suggestions[0]["pr_url"], "https://github.com/o/r/pull/1")
        # Done tasks are skipped - only one gh call.
        self.assertEqual(gh.call_count, 1)

    def test_open_pr_is_not_suggested(self) -> None:
        tasks = [
            {
                "id": "t-3",
                "status": "in_progress",
                "pr_url": "https://github.com/o/r/pull/3",
            }
        ]
        open_json = json.dumps(
            {"state": "OPEN", "mergedAt": None, "url": "https://github.com/o/r/pull/3"}
        )
        with (
            _gh_only(),
            patch.object(pr_sync_mod, "gh_available", return_value=True),
            patch.object(pr_sync_mod, "_gh", return_value=(0, open_json, "")),
        ):
            suggestions = suggest_merged_task_prs("/tmp/repo", tasks)
        self.assertEqual(suggestions, [])

    def test_check_pr_merged_without_gh(self) -> None:
        with (
            _gh_only(),
            patch.object(pr_sync_mod, "gh_available", return_value=False),
        ):
            result = check_pr_merged("/tmp/repo", "https://github.com/o/r/pull/1")
        self.assertFalse(result["ok"])
        self.assertFalse(result["merged"])

    def test_payload_and_http_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectBoardStore(root)
            task = store.create_task(
                title="Ship feature",
                actor="navin",
                actor_type="agent",
            )
            store.update_task(
                task["id"],
                fields={
                    "status": "review",
                    "pr_url": "https://github.com/o/r/pull/9",
                    "head_sha": "abc123def456",
                },
                actor="navin",
                actor_type="agent",
            )
            merged_json = json.dumps(
                {
                    "state": "MERGED",
                    "mergedAt": "2026-02-02T00:00:00Z",
                    "url": "https://github.com/o/r/pull/9",
                }
            )
            with (
                _gh_only(),
                patch.object(pr_sync_mod, "gh_available", return_value=True),
                patch.object(pr_sync_mod, "_gh", return_value=(0, merged_json, "")),
            ):
                payload = pr_sync_payload(root, store)
                http = github_pr_sync_payload(_Scope(root))  # type: ignore[arg-type]
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["suggestions"]), 1)
            self.assertEqual(payload["suggestions"][0]["task_id"], task["id"])
            self.assertTrue(http["ok"])
            self.assertEqual(http["suggestions"][0]["pr_url"], "https://github.com/o/r/pull/9")
            # head_sha remains on the task (PR sync does not mutate).
            reloaded = store.get_task(task["id"])
            self.assertEqual(reloaded["head_sha"], "abc123def456")
            self.assertEqual(reloaded["status"], "review")


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


class ForgeBackedPrSyncTests(unittest.TestCase):
    """The stored PR URL says which forge to ask; no ``gh`` in sight."""

    def _check(self, url: str, responses, token: str = "t0k3n"):
        from navin.webui import forge_api

        recorder = _Recorder(responses)
        with (
            patch.object(pr_sync_mod, "_config", return_value=None),
            patch.object(forge_api, "resolve_token", return_value=(token, "settings")),
            patch.object(forge_api.httpx, "request", recorder),
        ):
            return check_pr_merged("/tmp/repo", url), recorder

    def test_forgejo_merged_pull_request(self) -> None:
        result, recorder = self._check(
            "https://forgejo.navinspire.ai/Navinspire/navin-claw/pulls/12",
            [
                _Response(
                    200,
                    {
                        "number": 12,
                        "title": "Ship it",
                        "state": "closed",
                        "merged": True,
                        "merged_at": "2026-03-03T10:00:00Z",
                        "html_url": "https://forgejo.navinspire.ai/Navinspire/navin-claw/pulls/12",
                        "head": {"ref": "navin/task-1"},
                        "base": {"ref": "main"},
                    },
                )
            ],
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["merged"])
        self.assertEqual(result["forge"], "forgejo")
        self.assertEqual(result["branch"], "navin/task-1")
        # A merged request must not read as a plain "closed" one.
        self.assertEqual(result["state"], "merged")
        call = recorder.calls[0]
        self.assertEqual(
            call["url"],
            "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/navin-claw/pulls/12",
        )
        self.assertEqual(call["headers"]["Authorization"], "token t0k3n")

    def test_gitlab_merge_request_uses_the_iid_and_state(self) -> None:
        result, recorder = self._check(
            "https://gitlab.com/group/sub/app/-/merge_requests/7",
            [
                _Response(
                    200,
                    {
                        "iid": 7,
                        "id": 99181,
                        "title": "Ship it",
                        "state": "merged",
                        "merged_at": "2026-03-04T10:00:00Z",
                        "web_url": "https://gitlab.com/group/sub/app/-/merge_requests/7",
                        "source_branch": "navin/task-2",
                        "target_branch": "main",
                    },
                )
            ],
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["merged"])
        self.assertEqual(result["forge"], "gitlab")
        call = recorder.calls[0]
        # The project is URL-encoded and the MR addressed by iid, not id.
        self.assertEqual(
            call["url"],
            "https://gitlab.com/api/v4/projects/group%2Fsub%2Fapp/merge_requests/7",
        )
        self.assertEqual(call["headers"]["PRIVATE-TOKEN"], "t0k3n")

    def test_open_request_is_not_a_suggestion(self) -> None:
        from navin.webui import forge_api

        recorder = _Recorder(
            [
                _Response(
                    200,
                    {
                        "number": 3,
                        "state": "open",
                        "merged": False,
                        "html_url": "https://forgejo.navinspire.ai/o/r/pulls/3",
                        "head": {"ref": "feature"},
                        "base": {"ref": "main"},
                    },
                )
            ]
        )
        with (
            patch.object(pr_sync_mod, "_config", return_value=None),
            patch.object(forge_api, "resolve_token", return_value=("t0k3n", "settings")),
            patch.object(forge_api.httpx, "request", recorder),
        ):
            suggestions = suggest_merged_task_prs(
                "/tmp/repo",
                [
                    {
                        "id": "t-9",
                        "status": "review",
                        "pr_url": "https://forgejo.navinspire.ai/o/r/pulls/3",
                    }
                ],
            )
        self.assertEqual(suggestions, [])

    def test_missing_token_is_actionable_and_hides_nothing(self) -> None:
        from navin.webui import forge_api

        with (
            patch.object(pr_sync_mod, "_config", return_value=None),
            patch.object(forge_api, "resolve_token", return_value=("", "")),
            patch.object(pr_sync_mod, "gh_available", return_value=False),
        ):
            result = check_pr_merged(
                "/tmp/repo", "https://forgejo.navinspire.ai/o/r/pulls/3"
            )
        self.assertFalse(result["ok"])
        self.assertIn("forgejo.navinspire.ai", result["detail"])
        self.assertIn("Settings > Git", result["detail"])

    def test_http_errors_are_explained_not_echoed(self) -> None:
        from navin.webui import forge_api

        cases = {
            401: "rejected the token",
            403: "missing the scope",
            404: "not found",
            500: "HTTP 500",
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                result, _recorder = self._check(
                    "https://forgejo.navinspire.ai/o/r/pulls/3",
                    [_Response(status, None, text="<html>nginx error page</html>")],
                )
                self.assertFalse(result["ok"])
                self.assertIn(expected, result["detail"])
                # An HTML error page is never copied into the UI.
                self.assertNotIn("<html>", result["detail"])
        del forge_api

    def test_network_failure_is_reported_without_credentials(self) -> None:
        import httpx

        from navin.webui import forge_api

        def _boom(method, url, **kwargs):
            raise httpx.ConnectError("failed to connect to https://user:s3cret@host")

        with (
            patch.object(pr_sync_mod, "_config", return_value=None),
            patch.object(forge_api, "resolve_token", return_value=("t0k3n", "settings")),
            patch.object(forge_api.httpx, "request", _boom),
        ):
            result = check_pr_merged(
                "/tmp/repo", "https://forgejo.navinspire.ai/o/r/pulls/3"
            )
        self.assertFalse(result["ok"])
        self.assertNotIn("s3cret", result["detail"])

    def test_payload_reports_unavailable_without_token_or_cli(self) -> None:
        from navin.webui import forge_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectBoardStore(root)
            with (
                patch.object(pr_sync_mod, "_config", return_value=None),
                patch.object(pr_sync_mod, "gh_available", return_value=False),
                patch.object(forge_api, "detect_forge", return_value=None),
            ):
                payload = pr_sync_payload(root, store)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["available"])
        self.assertIn("Settings > Git", payload["detail"])


if __name__ == "__main__":
    unittest.main()
