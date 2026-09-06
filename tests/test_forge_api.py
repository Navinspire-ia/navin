"""Forge-agnostic PR/MR support: detection, tokens, payloads, HTTP errors.

Every network call is mocked: the suite must pass offline and must never
touch a real forge.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from navin.config.schema import Config
from navin.security.workspace_access import default_workspace_scope
from navin.webui import forge_api
from navin.webui.forge_api import (
    FORGEJO,
    GITHUB,
    GITLAB,
    UNKNOWN,
    ForgeError,
    ForgeRemote,
    parse_remote_url,
)


class FakeResponse:
    """Minimal ``httpx.Response`` stand-in for the request recorder."""

    def __init__(self, status_code: int, payload: Any = None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ("{}" if payload is None else "json")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class RequestRecorder:
    """Answers ``httpx.request`` from a scripted list and records the calls."""

    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


def _remote(kind: str = FORGEJO, host: str = "forgejo.navinspire.ai") -> ForgeRemote:
    return ForgeRemote(kind=kind, host=host, owner="Navinspire", repo="navin-claw")


# ---------------------------------------------------------------------------
# Remote parsing
# ---------------------------------------------------------------------------


class RemoteParsingTest(unittest.TestCase):
    def test_https_remote(self) -> None:
        remote = parse_remote_url("https://github.com/octocat/hello-world.git")
        assert remote is not None
        self.assertEqual(remote.host, "github.com")
        self.assertEqual(remote.owner, "octocat")
        self.assertEqual(remote.repo, "hello-world")
        self.assertEqual(remote.slug, "octocat/hello-world")

    def test_ssh_scp_remote(self) -> None:
        remote = parse_remote_url("git@forgejo.navinspire.ai:Navinspire/navin-claw.git")
        assert remote is not None
        self.assertEqual(remote.host, "forgejo.navinspire.ai")
        self.assertEqual(remote.slug, "Navinspire/navin-claw")
        # A web API is reached over https even when the remote is SSH.
        self.assertEqual(remote.scheme, "https")

    def test_ssh_url_drops_the_ssh_port(self) -> None:
        remote = parse_remote_url("ssh://git@gitlab.example.com:2222/group/sub/repo.git")
        assert remote is not None
        self.assertIsNone(remote.port)
        self.assertEqual(remote.owner, "group/sub")
        self.assertEqual(remote.repo, "repo")

    def test_credentials_are_dropped(self) -> None:
        remote = parse_remote_url("https://user:s3cret@forgejo.example.com/o/r.git")
        assert remote is not None
        self.assertEqual(remote.host, "forgejo.example.com")
        self.assertNotIn("s3cret", remote.url)

    def test_http_keeps_scheme_and_port(self) -> None:
        remote = parse_remote_url("http://localhost:3000/o/r.git")
        assert remote is not None
        self.assertEqual(remote.scheme, "http")
        self.assertEqual(remote.port, 3000)
        self.assertEqual(remote.base, "http://localhost:3000")

    def test_local_paths_are_not_forges(self) -> None:
        for value in ("", "/home/me/repo", "C:\\repos\\thing", "../sibling", "file:///tmp/r"):
            self.assertIsNone(parse_remote_url(value), value)

    def test_trailing_git_only_stripped_once(self) -> None:
        remote = parse_remote_url("https://github.com/o/my.git.repo.git")
        assert remote is not None
        self.assertEqual(remote.repo, "my.git.repo")


# ---------------------------------------------------------------------------
# Forge kind detection
# ---------------------------------------------------------------------------


class ForgeKindTest(unittest.TestCase):
    def test_known_hosts(self) -> None:
        self.assertEqual(forge_api.kind_from_host("github.com"), GITHUB)
        self.assertEqual(forge_api.kind_from_host("gitlab.com"), GITLAB)
        self.assertEqual(forge_api.kind_from_host("codeberg.org"), FORGEJO)

    def test_self_hosted_by_label(self) -> None:
        self.assertEqual(forge_api.kind_from_host("forgejo.navinspire.ai"), FORGEJO)
        self.assertEqual(forge_api.kind_from_host("gitlab.acme.io"), GITLAB)
        self.assertEqual(forge_api.kind_from_host("gitea.internal.lan"), FORGEJO)

    def test_no_substring_guessing(self) -> None:
        self.assertEqual(forge_api.kind_from_host("my-github-mirror.example.com"), UNKNOWN)
        self.assertEqual(forge_api.kind_from_host("code.example.com"), UNKNOWN)

    def test_config_override_wins(self) -> None:
        config = Config()
        config.tools.forge.hosts = {"code.example.com": "gitlab"}
        self.assertEqual(
            forge_api.kind_from_host("code.example.com", config=config), GITLAB
        )
        # An override also beats the built-in guess for a labelled host.
        config.tools.forge.hosts = {"gitlab.acme.io": "forgejo"}
        self.assertEqual(forge_api.kind_from_host("gitlab.acme.io", config=config), FORGEJO)

    def test_probe_identifies_forgejo(self) -> None:
        forge_api.clear_probe_cache()
        remote = _remote(kind=UNKNOWN, host="code.example.com")
        with patch.object(
            forge_api,
            "_probe_endpoint",
            side_effect=lambda base, path: path == "/api/v1/version",
        ):
            self.assertEqual(forge_api.probe_kind(remote), FORGEJO)
        forge_api.clear_probe_cache()

    def test_probe_identifies_gitlab_behind_auth(self) -> None:
        forge_api.clear_probe_cache()
        remote = _remote(kind=UNKNOWN, host="code2.example.com")
        with patch.object(
            forge_api,
            "_probe_endpoint",
            side_effect=lambda base, path: path == "/api/v4/version",
        ):
            self.assertEqual(forge_api.probe_kind(remote), GITLAB)
        forge_api.clear_probe_cache()

    def test_probe_result_is_cached(self) -> None:
        forge_api.clear_probe_cache()
        remote = _remote(kind=UNKNOWN, host="code3.example.com")
        with patch.object(
            forge_api, "_probe_endpoint", return_value=True,
        ) as probe:
            forge_api.probe_kind(remote)
            forge_api.probe_kind(remote)
        self.assertEqual(probe.call_count, 1)
        forge_api.clear_probe_cache()

    def test_detect_forge_reads_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(
                forge_api,
                "_origin_url",
                return_value="git@forgejo.navinspire.ai:Navinspire/navin-claw.git",
            ):
                remote = forge_api.detect_forge(root)
        assert remote is not None
        self.assertEqual(remote.kind, FORGEJO)
        self.assertEqual(remote.api_base, "https://forgejo.navinspire.ai/api/v1")

    def test_detect_forge_without_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(forge_api, "_origin_url", return_value=""):
                self.assertIsNone(forge_api.detect_forge(Path(tmp)))


class ApiBaseTest(unittest.TestCase):
    def test_api_bases_per_forge(self) -> None:
        self.assertEqual(
            ForgeRemote(GITHUB, "github.com", "o", "r").api_base,
            "https://api.github.com",
        )
        self.assertEqual(
            ForgeRemote(GITHUB, "ghe.acme.com", "o", "r").api_base,
            "https://ghe.acme.com/api/v3",
        )
        self.assertEqual(
            ForgeRemote(GITLAB, "gitlab.com", "g/s", "r").api_base,
            "https://gitlab.com/api/v4",
        )
        self.assertEqual(
            ForgeRemote(FORGEJO, "forgejo.navinspire.ai", "o", "r").api_base,
            "https://forgejo.navinspire.ai/api/v1",
        )


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class TokenResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        # Never let a developer's real token leak into the assertions.
        patcher = patch.dict(
            "os.environ",
            {
                key: ""
                for key in (
                    "GITHUB_TOKEN",
                    "GH_TOKEN",
                    "GITLAB_TOKEN",
                    "GL_TOKEN",
                    "GLAB_TOKEN",
                    "FORGEJO_TOKEN",
                    "FORGEJO_ACCESS_TOKEN",
                    "GITEA_TOKEN",
                    "GITEA_SERVER_TOKEN",
                    "GITEA_ACCESS_TOKEN",
                    "GITEA_HTTP_TOKEN",
                    "TEA_TOKEN",
                )
            },
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        gh = patch.object(forge_api, "gh_cli_token", return_value="")
        gh.start()
        self.addCleanup(gh.stop)

    def test_settings_token_wins(self) -> None:
        config = Config()
        config.tools.forge.tokens = {"forgejo.navinspire.ai": "cfg-token"}
        with patch.dict("os.environ", {"FORGEJO_TOKEN": "env-token"}):
            token, source = forge_api.resolve_token(_remote(), config=config)
        self.assertEqual(token, "cfg-token")
        self.assertEqual(source, "settings")

    def test_per_host_env_beats_generic_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "NAVIN_FORGE_TOKEN_FORGEJO_NAVINSPIRE_AI": "host-token",
                "FORGEJO_TOKEN": "generic-token",
            },
        ):
            token, source = forge_api.resolve_token(_remote())
        self.assertEqual(token, "host-token")
        self.assertEqual(source, "env:NAVIN_FORGE_TOKEN_FORGEJO_NAVINSPIRE_AI")

    def test_kind_env_vars(self) -> None:
        cases = {
            GITHUB: ("GITHUB_TOKEN", "github.com"),
            GITLAB: ("GITLAB_TOKEN", "gitlab.com"),
            FORGEJO: ("GITEA_SERVER_TOKEN", "gitea.internal.lan"),
        }
        for kind, (var, host) in cases.items():
            with patch.dict("os.environ", {var: "v"}):
                token, source = forge_api.resolve_token(_remote(kind=kind, host=host))
            self.assertEqual(token, "v", kind)
            self.assertEqual(source, f"env:{var}", kind)

    def test_gh_cli_is_the_last_github_road(self) -> None:
        with patch.object(forge_api, "gh_cli_token", return_value="gh-token"):
            token, source = forge_api.resolve_token(_remote(kind=GITHUB, host="github.com"))
        self.assertEqual(token, "gh-token")
        self.assertEqual(source, "gh")

    def test_no_token_is_not_a_crash(self) -> None:
        token, source = forge_api.resolve_token(_remote())
        self.assertEqual(token, "")
        self.assertEqual(source, "")

    def test_missing_token_error_names_host_and_env(self) -> None:
        error = forge_api.token_missing_error(_remote())
        self.assertEqual(error.code, "forge_token_missing")
        self.assertEqual(error.status, 401)
        self.assertIn("forgejo.navinspire.ai", error.message)
        self.assertIn("FORGEJO_TOKEN", error.message)
        self.assertIn("Settings > Git", error.message)

    def test_host_env_var_naming(self) -> None:
        self.assertEqual(
            forge_api.host_env_var("forgejo.navinspire.ai"),
            "NAVIN_FORGE_TOKEN_FORGEJO_NAVINSPIRE_AI",
        )


# ---------------------------------------------------------------------------
# Request payloads
# ---------------------------------------------------------------------------


class CreateRequestPayloadTest(unittest.TestCase):
    def _create(self, remote: ForgeRemote, response: FakeResponse, **kwargs: Any):
        recorder = RequestRecorder([response])
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.create_request(
                remote,
                "tok",
                head="feature/x",
                base="main",
                title="Ship it",
                body="why",
                **kwargs,
            )
        return result, recorder

    def test_github_payload_and_headers(self) -> None:
        result, recorder = self._create(
            ForgeRemote(GITHUB, "github.com", "o", "r"),
            FakeResponse(
                201,
                {
                    "html_url": "https://github.com/o/r/pull/7",
                    "number": 7,
                    "title": "Ship it",
                    "state": "open",
                    "draft": True,
                    "head": {"ref": "feature/x"},
                    "base": {"ref": "main"},
                },
            ),
            draft=True,
        )
        call = recorder.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://api.github.com/repos/o/r/pulls")
        self.assertEqual(
            call["json"],
            {
                "title": "Ship it",
                "body": "why",
                "head": "feature/x",
                "base": "main",
                "draft": True,
            },
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer tok")
        self.assertFalse(call["follow_redirects"])
        self.assertEqual(result["url"], "https://github.com/o/r/pull/7")
        self.assertTrue(result["is_draft"])

    def test_gitlab_payload_uses_encoded_project_and_draft_prefix(self) -> None:
        result, recorder = self._create(
            ForgeRemote(GITLAB, "gitlab.com", "group/sub", "repo"),
            FakeResponse(
                201,
                {
                    "web_url": "https://gitlab.com/group/sub/repo/-/merge_requests/4",
                    "iid": 4,
                    "title": "Draft: Ship it",
                    "state": "opened",
                    "source_branch": "feature/x",
                    "target_branch": "main",
                },
            ),
            draft=True,
        )
        call = recorder.calls[0]
        self.assertEqual(
            call["url"],
            "https://gitlab.com/api/v4/projects/group%2Fsub%2Frepo/merge_requests",
        )
        self.assertEqual(call["json"]["source_branch"], "feature/x")
        self.assertEqual(call["json"]["target_branch"], "main")
        self.assertEqual(call["json"]["title"], "Draft: Ship it")
        self.assertEqual(call["headers"]["PRIVATE-TOKEN"], "tok")
        self.assertTrue(result["is_draft"])
        self.assertEqual(result["number"], 4)

    def test_forgejo_payload_and_wip_prefix(self) -> None:
        result, recorder = self._create(
            _remote(),
            FakeResponse(
                201,
                {
                    "html_url": "https://forgejo.navinspire.ai/Navinspire/navin-claw/pulls/2",
                    "number": 2,
                    "title": "WIP: Ship it",
                    "state": "open",
                    "head": {"ref": "feature/x"},
                    "base": {"ref": "main"},
                },
            ),
            draft=True,
        )
        call = recorder.calls[0]
        self.assertEqual(
            call["url"],
            "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/navin-claw/pulls",
        )
        self.assertEqual(call["json"]["title"], "WIP: Ship it")
        self.assertEqual(call["headers"]["Authorization"], "token tok")
        self.assertTrue(result["is_draft"])

    def test_non_draft_keeps_the_title_untouched(self) -> None:
        _, recorder = self._create(
            ForgeRemote(GITLAB, "gitlab.com", "g", "r"),
            FakeResponse(201, {"web_url": "https://gitlab.com/g/r/-/merge_requests/1", "iid": 1}),
            draft=False,
        )
        self.assertEqual(recorder.calls[0]["json"]["title"], "Ship it")

    def test_github_retries_without_draft_on_422(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(422, {"message": "Draft pull requests are not supported"}),
                FakeResponse(201, {"html_url": "https://github.com/o/r/pull/8", "number": 8}),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.create_request(
                ForgeRemote(GITHUB, "github.com", "o", "r"),
                "tok",
                head="feature/x",
                base="main",
                title="t",
                body="b",
                draft=True,
            )
        self.assertEqual(len(recorder.calls), 2)
        self.assertNotIn("draft", recorder.calls[1]["json"])
        self.assertEqual(result["url"], "https://github.com/o/r/pull/8")


class FindOpenRequestTest(unittest.TestCase):
    def test_github_filters_by_qualified_head(self) -> None:
        recorder = RequestRecorder([FakeResponse(200, [])])
        with patch.object(forge_api.httpx, "request", recorder):
            forge_api.find_open_request(
                ForgeRemote(GITHUB, "github.com", "o", "r"), "tok", "feature/x"
            )
        self.assertEqual(recorder.calls[0]["params"]["head"], "o:feature/x")

    def test_gitlab_filters_by_source_branch(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "web_url": "https://gitlab.com/g/r/-/merge_requests/3",
                            "iid": 3,
                            "title": "x",
                            "state": "opened",
                            "source_branch": "feature/x",
                            "target_branch": "main",
                        }
                    ],
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            found = forge_api.find_open_request(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "tok", "feature/x"
            )
        self.assertEqual(recorder.calls[0]["params"]["source_branch"], "feature/x")
        assert found is not None
        self.assertEqual(found["number"], 3)

    def test_forgejo_filters_client_side(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "html_url": "https://f/o/r/pulls/1",
                            "number": 1,
                            "head": {"ref": "other"},
                        },
                        {
                            "html_url": "https://f/o/r/pulls/2",
                            "number": 2,
                            "head": {"ref": "feature/x"},
                        },
                    ],
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            found = forge_api.find_open_request(_remote(), "tok", "feature/x")
        assert found is not None
        self.assertEqual(found["number"], 2)

    def test_no_open_request(self) -> None:
        with patch.object(forge_api.httpx, "request", RequestRecorder([FakeResponse(200, [])])):
            self.assertIsNone(forge_api.find_open_request(_remote(), "tok", "feature/x"))

    def test_empty_branch_makes_no_call(self) -> None:
        recorder = RequestRecorder([])
        with patch.object(forge_api.httpx, "request", recorder):
            self.assertIsNone(forge_api.find_open_request(_remote(), "tok", ""))
        self.assertEqual(recorder.calls, [])


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------


class HttpErrorTest(unittest.TestCase):
    def _create_expecting_error(self, response: FakeResponse) -> ForgeError:
        with patch.object(forge_api.httpx, "request", RequestRecorder([response])):
            with self.assertRaises(ForgeError) as ctx:
                forge_api.create_request(
                    _remote(),
                    "tok",
                    head="feature/x",
                    base="main",
                    title="t",
                    body="b",
                    draft=False,
                )
        return ctx.exception

    def test_401_points_at_settings(self) -> None:
        error = self._create_expecting_error(FakeResponse(401, {"message": "bad token"}))
        self.assertEqual(error.code, "forge_token_invalid")
        self.assertEqual(error.status, 401)
        self.assertIn("Settings > Git", error.message)

    def test_403_mentions_the_missing_scope(self) -> None:
        error = self._create_expecting_error(FakeResponse(403, {"message": "forbidden"}))
        self.assertEqual(error.code, "forge_token_forbidden")
        self.assertEqual(error.status, 403)
        self.assertIn("write access", error.message)

    def test_404_names_the_repository(self) -> None:
        error = self._create_expecting_error(FakeResponse(404, {"message": "not found"}))
        self.assertEqual(error.code, "forge_repo_not_found")
        self.assertIn("Navinspire/navin-claw", error.message)

    def test_422_is_a_validation_refusal(self) -> None:
        error = self._create_expecting_error(
            FakeResponse(422, {"errors": [{"message": "No commits between main and x"}]})
        )
        self.assertEqual(error.code, "forge_rejected")
        self.assertEqual(error.status, 409)
        self.assertIn("No commits", error.message)

    def test_500_is_reported_as_upstream(self) -> None:
        error = self._create_expecting_error(FakeResponse(500, None, text="boom"))
        self.assertEqual(error.code, "forge_http_error")
        self.assertEqual(error.status, 502)

    def test_html_error_page_is_not_echoed(self) -> None:
        error = self._create_expecting_error(
            FakeResponse(502, None, text="<html><body>nginx</body></html>")
        )
        self.assertNotIn("<html>", error.message)

    def test_network_failure_is_actionable(self) -> None:
        def boom(*args: Any, **kwargs: Any):
            raise forge_api.httpx.ConnectError("connection refused")

        with patch.object(forge_api.httpx, "request", boom):
            with self.assertRaises(ForgeError) as ctx:
                forge_api.default_branch(_remote(), "tok")
        self.assertEqual(ctx.exception.code, "forge_unreachable")
        self.assertEqual(ctx.exception.status, 502)

    def test_error_never_echoes_url_credentials(self) -> None:
        redacted = forge_api.redact_url_credentials(
            "fatal: Authentication failed for 'https://bob:ghp_secret@forgejo.example/o/r.git'"
        )
        self.assertNotIn("ghp_secret", redacted)
        self.assertIn("https://forgejo.example/o/r.git", redacted)


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------


class CheckSummaryTest(unittest.TestCase):
    def test_failure_wins_over_pending(self) -> None:
        summary = forge_api.summarize_checks(
            [
                {"name": "lint", "state": "success"},
                {"name": "test", "state": "failure"},
                {"name": "build", "state": "running"},
            ]
        )
        self.assertEqual(summary["state"], "failure")
        self.assertEqual(summary["failing"], 1)
        self.assertEqual(summary["passing"], 1)
        self.assertEqual(summary["pending"], 1)

    def test_gitlab_reads_the_latest_pipeline_jobs(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(200, [{"id": 99, "status": "failed", "web_url": "https://p/99"}]),
                FakeResponse(
                    200,
                    [
                        {"name": "lint", "status": "success", "web_url": "https://j/1"},
                        {"name": "test", "status": "failed", "web_url": "https://j/2"},
                    ],
                ),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            summary = forge_api.check_summary(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "tok", branch="feature/x"
            )
        self.assertEqual(summary["state"], "failure")
        self.assertEqual(summary["total"], 2)

    def test_gitlab_without_pipeline_is_unknown_not_an_error(self) -> None:
        with patch.object(forge_api.httpx, "request", RequestRecorder([FakeResponse(200, [])])):
            summary = forge_api.check_summary(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "tok", branch="feature/x"
            )
        self.assertEqual(summary["state"], "unknown")

    def test_forgejo_commit_statuses(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    [
                        {"context": "ci/build", "status": "success", "target_url": "https://x"},
                        {"context": "ci/test", "status": "pending", "target_url": None},
                    ],
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            summary = forge_api.check_summary(_remote(), "tok", branch="feature/x")
        self.assertEqual(summary["state"], "pending")
        self.assertEqual(summary["passing"], 1)

    def test_forgejo_without_status_api_degrades_quietly(self) -> None:
        with patch.object(forge_api.httpx, "request", RequestRecorder([FakeResponse(404)])):
            summary = forge_api.check_summary(_remote(), "tok", branch="feature/x")
        self.assertEqual(summary["state"], "unknown")
        self.assertEqual(summary["total"], 0)

    def test_github_merges_check_runs_and_statuses(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    {
                        "check_runs": [
                            {
                                "name": "build",
                                "conclusion": "success",
                                "html_url": "https://c/1",
                            }
                        ]
                    },
                ),
                FakeResponse(
                    200,
                    {"statuses": [{"context": "netlify", "state": "pending"}]},
                ),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            summary = forge_api.check_summary(
                ForgeRemote(GITHUB, "github.com", "o", "r"), "tok", branch="feature/x"
            )
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["state"], "pending")


# ---------------------------------------------------------------------------
# Panel payloads
# ---------------------------------------------------------------------------


def _scope(root: Path):
    return default_workspace_scope(root, True)


class PanelPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        (self.root / ".git").mkdir()

    def _context(self, kind: str = FORGEJO, token: str = "tok", source: str = "settings"):
        from navin.webui.github_pr_api import ForgeContext

        return ForgeContext(_remote(kind=kind), token, source)

    def test_view_reports_missing_token_without_crashing(self) -> None:
        from navin.webui.github_pr_api import github_pr_view_payload

        with patch("navin.webui.github_pr_api.gh_available", return_value=False):
            with patch(
                "navin.webui.github_pr_api.forge_context",
                return_value=self._context(token="", source=""),
            ):
                with patch(
                    "navin.webui.github_pr_api._branch_state",
                    return_value={"branch": "feature/x"},
                ):
                    payload = github_pr_view_payload(_scope(self.root))
        self.assertFalse(payload["available"])
        self.assertFalse(payload["token_configured"])
        self.assertEqual(payload["forge"], FORGEJO)
        self.assertEqual(payload["host"], "forgejo.navinspire.ai")
        self.assertIn("no token", payload["detail"])
        self.assertIsNone(payload["pr"])

    def test_view_without_a_supported_remote(self) -> None:
        from navin.webui.github_pr_api import ForgeContext, github_pr_view_payload

        with patch(
            "navin.webui.github_pr_api.forge_context",
            return_value=ForgeContext(None, "", ""),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                payload = github_pr_view_payload(_scope(self.root))
        self.assertEqual(payload["forge"], UNKNOWN)
        self.assertIn("no supported forge", payload["detail"])

    def test_view_returns_the_open_request_and_checks(self) -> None:
        from navin.webui.github_pr_api import github_pr_view_payload

        pr = {
            "url": "https://forgejo.navinspire.ai/Navinspire/navin-claw/pulls/5",
            "number": 5,
            "title": "Ship",
            "state": "open",
            "is_draft": False,
            "head": "feature/x",
            "base": "main",
        }
        with patch(
            "navin.webui.github_pr_api.forge_context", return_value=self._context(),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                with patch.object(forge_api, "find_open_request", return_value=pr):
                    with patch.object(
                        forge_api,
                        "check_summary",
                        return_value=forge_api.summarize_checks(
                            [{"name": "ci", "state": "success"}]
                        ),
                    ):
                        payload = github_pr_view_payload(_scope(self.root))
        self.assertTrue(payload["available"])
        self.assertEqual(payload["pr"]["number"], 5)
        self.assertEqual(payload["pr"]["checks"]["state"], "success")
        self.assertEqual(payload["request_label"], "pull request")

    def test_view_labels_gitlab_merge_requests(self) -> None:
        from navin.webui.github_pr_api import github_pr_view_payload

        with patch(
            "navin.webui.github_pr_api.forge_context",
            return_value=self._context(kind=GITLAB),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                with patch.object(forge_api, "find_open_request", return_value=None):
                    payload = github_pr_view_payload(_scope(self.root))
        self.assertEqual(payload["request_label"], "merge request")
        self.assertEqual(payload["forge_label"], "GitLab")

    def test_create_without_token_is_a_clear_401(self) -> None:
        from navin.webui.github_pr_api import github_pr_create_payload

        with patch("navin.webui.github_pr_api.gh_available", return_value=False):
            with patch(
                "navin.webui.github_pr_api.forge_context",
                return_value=self._context(token="", source=""),
            ):
                with patch(
                    "navin.webui.github_pr_api._branch_state",
                    return_value={"branch": "feature/x"},
                ):
                    with self.assertRaises(ForgeError) as ctx:
                        github_pr_create_payload(_scope(self.root), title="t")
        self.assertEqual(ctx.exception.code, "forge_token_missing")
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("forgejo.navinspire.ai", ctx.exception.message)

    def test_create_without_a_remote_explains_the_remote(self) -> None:
        from navin.webui.github_pr_api import ForgeContext, github_pr_create_payload

        with patch(
            "navin.webui.github_pr_api.forge_context",
            return_value=ForgeContext(None, "", ""),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                with self.assertRaises(ForgeError) as ctx:
                    github_pr_create_payload(_scope(self.root), title="t")
        self.assertEqual(ctx.exception.code, "forge_unknown_remote")

    def test_create_pushes_then_opens_on_forgejo(self) -> None:
        from navin.webui.github_pr_api import github_pr_create_payload

        created = {
            "url": "https://forgejo.navinspire.ai/Navinspire/navin-claw/pulls/9",
            "number": 9,
            "is_draft": True,
        }
        with patch(
            "navin.webui.github_pr_api.forge_context", return_value=self._context(),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                with patch("navin.webui.github_pr_api._head_sha_short", return_value="abc123"):
                    with patch.object(forge_api, "find_open_request", return_value=None):
                        with patch.object(forge_api, "default_branch", return_value="main"):
                            with patch.object(
                                forge_api, "create_request", return_value=created,
                            ) as create:
                                with patch(
                                    "navin.webui.project_search._run_git_write",
                                    return_value=subprocess.CompletedProcess(
                                        args=["git", "push"], returncode=0,
                                        stdout="", stderr="",
                                    ),
                                ):
                                    payload = github_pr_create_payload(
                                        _scope(self.root), title="Ship", draft=True,
                                    )
        self.assertTrue(payload["created"])
        self.assertEqual(payload["pr_url"], created["url"])
        self.assertEqual(payload["head_sha"], "abc123")
        self.assertEqual(payload["forge"], FORGEJO)
        self.assertEqual(create.call_args.kwargs["base"], "main")
        self.assertEqual(create.call_args.kwargs["head"], "feature/x")

    def test_create_reuses_an_open_request_without_pushing(self) -> None:
        from navin.webui.github_pr_api import github_pr_create_payload

        existing = {"url": "https://forgejo.navinspire.ai/o/r/pulls/1", "number": 1}
        with patch(
            "navin.webui.github_pr_api.forge_context", return_value=self._context(),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                with patch.object(forge_api, "find_open_request", return_value=existing):
                    with patch(
                        "navin.webui.project_search._run_git_write",
                    ) as push:
                        payload = github_pr_create_payload(_scope(self.root), title="t")
        self.assertFalse(payload["created"])
        self.assertEqual(payload["pr_url"], existing["url"])
        push.assert_not_called()

    def test_create_refuses_to_target_the_default_branch(self) -> None:
        from navin.webui.github_pr_api import github_pr_create_payload

        with patch(
            "navin.webui.github_pr_api.forge_context", return_value=self._context(),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state", return_value={"branch": "main"},
            ):
                with patch.object(forge_api, "find_open_request", return_value=None):
                    with patch.object(forge_api, "default_branch", return_value="main"):
                        with patch(
                            "navin.webui.project_search._run_git_write",
                            return_value=subprocess.CompletedProcess(
                                args=["git", "push"], returncode=0, stdout="", stderr="",
                            ),
                        ):
                            with self.assertRaises(ForgeError) as ctx:
                                github_pr_create_payload(_scope(self.root), title="t")
        self.assertEqual(ctx.exception.code, "forge_head_is_base")

    def test_ci_payload_carries_forge_metadata(self) -> None:
        from navin.webui.github_pr_api import github_ci_status_payload

        with patch(
            "navin.webui.github_pr_api.forge_context",
            return_value=self._context(kind=GITLAB),
        ):
            with patch(
                "navin.webui.github_pr_api._branch_state",
                return_value={"branch": "feature/x"},
            ):
                with patch.object(forge_api, "find_open_request", return_value=None):
                    ci = github_ci_status_payload(_scope(self.root))
        self.assertEqual(ci["forge"], GITLAB)
        self.assertEqual(ci["request_label"], "merge request")
        self.assertEqual(ci["checks"]["state"], "unknown")


# ---------------------------------------------------------------------------
# Push failure wording
# ---------------------------------------------------------------------------


class PushFailureMessageTest(unittest.TestCase):
    def test_forgejo_expired_credentials(self) -> None:
        from navin.webui.project_search import explain_push_failure

        message = explain_push_failure(
            "remote: Credentials are incorrect or have expired.\n"
            "fatal: Authentication failed for "
            "'https://forgejo.navinspire.ai/Navinspire/navin-claw.git/'"
        )
        self.assertIn("git push failed", message)
        self.assertIn("wrong or expired", message)
        self.assertIn("credential helper", message)

    def test_secrets_in_the_remote_url_are_redacted(self) -> None:
        from navin.webui.project_search import explain_push_failure

        message = explain_push_failure(
            "fatal: Authentication failed for 'https://bob:ghp_topsecret@github.com/o/r.git'"
        )
        self.assertNotIn("ghp_topsecret", message)
        self.assertNotIn("bob:", message)

    def test_publickey_refusal_talks_about_ssh(self) -> None:
        from navin.webui.project_search import explain_push_failure

        message = explain_push_failure(
            "git@github.com: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository."
        )
        self.assertIn("SSH key", message)

    def test_non_fast_forward_asks_for_a_pull(self) -> None:
        from navin.webui.project_search import explain_push_failure

        message = explain_push_failure(
            "! [rejected] main -> main (non-fast-forward)\nhint: fetch first"
        )
        self.assertIn("Pull", message)

    def test_unknown_failure_keeps_gits_own_words(self) -> None:
        from navin.webui.project_search import explain_push_failure

        message = explain_push_failure("error: src refspec nope does not match any")
        self.assertEqual(message, "git push failed: error: src refspec nope does not match any")


# ---------------------------------------------------------------------------
# Settings surface
# ---------------------------------------------------------------------------


class ForgeSettingsTest(unittest.TestCase):
    def test_payload_masks_tokens(self) -> None:
        from navin.webui.settings_api import _forge_settings_payload

        config = Config()
        config.tools.forge.tokens = {"forgejo.navinspire.ai": "abcdefghijklmnop"}
        config.tools.forge.hosts = {"code.example.com": "gitlab"}
        payload = _forge_settings_payload(config)
        by_host = {row["host"]: row for row in payload["hosts"]}
        self.assertNotIn("abcdefghijklmnop", str(payload))
        self.assertTrue(by_host["forgejo.navinspire.ai"]["token_configured"])
        self.assertEqual(by_host["forgejo.navinspire.ai"]["kind"], FORGEJO)
        self.assertEqual(by_host["code.example.com"]["kind"], GITLAB)
        self.assertTrue(by_host["code.example.com"]["kind_pinned"])

    def test_add_update_and_remove_a_host(self) -> None:
        from navin.webui.settings_api import _apply_forge_host

        config = Config()
        changed = _apply_forge_host(
            config,
            {"forge_token": ["tok-1"], "forge_kind": ["forgejo"]},
            "Forgejo.Navinspire.AI",
        )
        self.assertTrue(changed)
        self.assertEqual(config.tools.forge.tokens["forgejo.navinspire.ai"], "tok-1")
        self.assertEqual(config.tools.forge.hosts["forgejo.navinspire.ai"], "forgejo")

        # Same value again is not a change.
        self.assertFalse(
            _apply_forge_host(
                config,
                {"forge_token": ["tok-1"], "forge_kind": ["forgejo"]},
                "forgejo.navinspire.ai",
            )
        )

        self.assertTrue(
            _apply_forge_host(
                config, {"forge_remove": ["1"]}, "forgejo.navinspire.ai",
            )
        )
        self.assertNotIn("forgejo.navinspire.ai", config.tools.forge.tokens)

    def test_host_pasted_as_a_url_is_accepted(self) -> None:
        from navin.webui.settings_api import _apply_forge_host

        config = Config()
        _apply_forge_host(config, {"forge_token": ["t"]}, "https://forgejo.example.com/o/r")
        self.assertIn("forgejo.example.com", config.tools.forge.tokens)

    def test_invalid_host_and_kind_are_refused(self) -> None:
        from navin.webui.settings_api import WebUISettingsError, _apply_forge_host

        config = Config()
        with self.assertRaises(WebUISettingsError):
            _apply_forge_host(config, {"forge_token": ["t"]}, "not a host")
        with self.assertRaises(WebUISettingsError):
            _apply_forge_host(config, {"forge_kind": ["bitbucket"]}, "code.example.com")

    def test_auto_kind_unpins_the_host(self) -> None:
        from navin.webui.settings_api import _apply_forge_host

        config = Config()
        config.tools.forge.hosts = {"code.example.com": "gitlab"}
        self.assertTrue(
            _apply_forge_host(config, {"forge_kind": ["auto"]}, "code.example.com")
        )
        self.assertNotIn("code.example.com", config.tools.forge.hosts)


# ---------------------------------------------------------------------------
# References: issue / request URLs and "which repository" strings
# ---------------------------------------------------------------------------


class RefParsingTest(unittest.TestCase):
    def test_issue_and_request_urls_per_forge(self) -> None:
        cases = [
            ("https://github.com/o/r/issues/7", "o", "r", 7, forge_api.ISSUE),
            ("https://github.com/o/r/pull/7", "o", "r", 7, forge_api.REQUEST),
            (
                "https://forgejo.navinspire.ai/Navinspire/navin-claw/pulls/12",
                "Navinspire",
                "navin-claw",
                12,
                forge_api.REQUEST,
            ),
            (
                "https://forgejo.navinspire.ai/Navinspire/navin-claw/issues/3",
                "Navinspire",
                "navin-claw",
                3,
                forge_api.ISSUE,
            ),
            # GitLab keeps its "-" separator and its subgroups.
            (
                "https://gitlab.com/group/sub/app/-/merge_requests/42",
                "group/sub",
                "app",
                42,
                forge_api.REQUEST,
            ),
            (
                "https://gitlab.com/group/sub/app/-/issues/42",
                "group/sub",
                "app",
                42,
                forge_api.ISSUE,
            ),
        ]
        for url, owner, repo, number, target in cases:
            with self.subTest(url=url):
                ref = forge_api.parse_ref_url(url)
                assert ref is not None
                self.assertEqual(ref.remote.owner, owner)
                self.assertEqual(ref.remote.repo, repo)
                self.assertEqual(ref.number, number)
                self.assertEqual(ref.target, target)

    def test_self_hosted_http_port_is_kept(self) -> None:
        ref = forge_api.parse_ref_url("http://localhost:3000/o/r/issues/5")
        assert ref is not None
        self.assertEqual(ref.remote.base, "http://localhost:3000")
        self.assertEqual(ref.remote.api_base, "http://localhost:3000/api/v1")

    def test_junk_urls_are_refused(self) -> None:
        for value in (
            "",
            "not a url",
            "https://github.com/o/r",
            "https://github.com/o/r/issues/not-a-number",
            "https://github.com/issues/7",
            "ftp://github.com/o/r/issues/7",
        ):
            self.assertIsNone(forge_api.parse_ref_url(value), value)

    def test_kind_is_resolved_from_the_host(self) -> None:
        ref = forge_api.resolve_ref(
            "https://forgejo.navinspire.ai/o/r/issues/1", allow_probe=False
        )
        assert ref is not None
        self.assertEqual(ref.remote.kind, FORGEJO)

    def test_repo_ref_borrows_the_origin_host(self) -> None:
        origin = ForgeRemote(FORGEJO, "forgejo.navinspire.ai", "Navinspire", "navin-claw")
        remote = forge_api.parse_repo_ref("acme/widgets", origin=origin)
        assert remote is not None
        # A bare slug on a Forgejo project must not silently mean github.com.
        self.assertEqual(remote.host, "forgejo.navinspire.ai")
        self.assertEqual(remote.kind, FORGEJO)
        self.assertEqual(remote.slug, "acme/widgets")

    def test_repo_ref_with_a_host_wins_over_the_origin(self) -> None:
        origin = ForgeRemote(FORGEJO, "forgejo.navinspire.ai", "o", "r")
        remote = forge_api.parse_repo_ref("gitlab.com/group/sub/app", origin=origin)
        assert remote is not None
        self.assertEqual(remote.host, "gitlab.com")
        self.assertEqual(remote.owner, "group/sub")
        self.assertEqual(remote.repo, "app")

    def test_repo_ref_without_origin_defaults_to_github(self) -> None:
        remote = forge_api.parse_repo_ref("acme/widgets", origin=None)
        assert remote is not None
        self.assertEqual(remote.host, "github.com")
        self.assertEqual(remote.kind, GITHUB)


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


class IssueListTest(unittest.TestCase):
    def test_github_filters_out_pull_requests(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "number": 4,
                            "title": "Crash",
                            "body": "boom",
                            "html_url": "https://github.com/o/r/issues/4",
                            "state": "open",
                            "labels": [{"name": "bug"}],
                            "user": {"login": "ada"},
                            "assignees": [{"login": "bob"}],
                            "comments": 2,
                        },
                        {
                            "number": 5,
                            "title": "A PR",
                            "html_url": "https://github.com/o/r/pull/5",
                            "pull_request": {"url": "..."},
                        },
                    ],
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issues = forge_api.list_issues(
                ForgeRemote(GITHUB, "github.com", "o", "r"), "tok", state="open"
            )
        self.assertEqual([issue["number"] for issue in issues], [4])
        self.assertEqual(issues[0]["labels"], ["bug"])
        self.assertEqual(issues[0]["author"], "ada")
        self.assertEqual(issues[0]["assignees"], ["bob"])
        call = recorder.calls[0]
        self.assertEqual(call["url"], "https://api.github.com/repos/o/r/issues")
        self.assertEqual(call["params"]["state"], "open")
        self.assertEqual(call["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(call["headers"]["X-GitHub-Api-Version"], "2022-11-28")

    def test_gitlab_uses_opened_iid_and_description(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "id": 90210,
                            "iid": 4,
                            "title": "Crash",
                            "description": "boom",
                            "web_url": "https://gitlab.com/group/sub/app/-/issues/4",
                            "state": "opened",
                            "labels": ["bug"],
                            "author": {"username": "ada"},
                            "assignees": [{"username": "bob"}],
                            "user_notes_count": 3,
                        }
                    ],
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issues = forge_api.list_issues(
                ForgeRemote(GITLAB, "gitlab.com", "group/sub", "app"), "tok"
            )
        # iid, not id: the number in the URL is what every endpoint takes.
        self.assertEqual(issues[0]["number"], 4)
        self.assertEqual(issues[0]["body"], "boom")
        self.assertEqual(issues[0]["state"], "open")
        self.assertEqual(issues[0]["comments"], 3)
        call = recorder.calls[0]
        self.assertEqual(
            call["url"], "https://gitlab.com/api/v4/projects/group%2Fsub%2Fapp/issues"
        )
        self.assertEqual(call["params"]["state"], "opened")
        self.assertEqual(call["headers"]["PRIVATE-TOKEN"], "tok")

    def test_forgejo_asks_for_issues_only(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "number": 8,
                            "title": "Crash",
                            "body": "boom",
                            "html_url": "https://forgejo.navinspire.ai/o/r/issues/8",
                            "state": "open",
                            "labels": [{"name": "bug"}],
                            "user": {"login": "ada"},
                            "comments": 0,
                        }
                    ],
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issues = forge_api.list_issues(_remote(), "tok", state="all", limit=10)
        self.assertEqual(issues[0]["number"], 8)
        call = recorder.calls[0]
        self.assertEqual(
            call["url"], "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/navin-claw/issues"
        )
        # Gitea mixes pull requests into /issues unless told otherwise.
        self.assertEqual(call["params"]["type"], "issues")
        self.assertEqual(call["params"]["limit"], 10)
        self.assertEqual(call["headers"]["Authorization"], "token tok")

    def test_http_error_never_echoes_an_html_page(self) -> None:
        with patch.object(
            forge_api.httpx,
            "request",
            RequestRecorder([FakeResponse(500, None, text="<html>Bad gateway</html>")]),
        ):
            with self.assertRaises(ForgeError) as ctx:
                forge_api.list_issues(_remote(), "tok")
        self.assertNotIn("<html>", ctx.exception.message)
        self.assertIn("HTTP 500", ctx.exception.message)


class IssueWriteTest(unittest.TestCase):
    def test_github_create_with_labels(self) -> None:
        recorder = RequestRecorder(
            [FakeResponse(201, {"number": 3, "html_url": "https://github.com/o/r/issues/3"})]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issue = forge_api.create_issue(
                ForgeRemote(GITHUB, "github.com", "o", "r"),
                "tok",
                title="Ship it",
                body="why",
                labels=["bug"],
            )
        self.assertEqual(issue["url"], "https://github.com/o/r/issues/3")
        body = recorder.calls[0]["json"]
        self.assertEqual(body["labels"], ["bug"])
        self.assertEqual(body["body"], "why")

    def test_github_retries_without_unknown_labels(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(422, {"message": "Validation Failed"}),
                FakeResponse(201, {"number": 3, "html_url": "https://github.com/o/r/issues/3"}),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issue = forge_api.create_issue(
                ForgeRemote(GITHUB, "github.com", "o", "r"),
                "tok",
                title="Ship it",
                labels=["nope"],
            )
        self.assertTrue(issue["url"])
        self.assertNotIn("labels", recorder.calls[1]["json"])

    def test_gitlab_create_uses_description_and_a_label_string(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    201,
                    {"iid": 6, "web_url": "https://gitlab.com/g/r/-/issues/6", "state": "opened"},
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issue = forge_api.create_issue(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"),
                "tok",
                title="Ship it",
                body="why",
                labels=["bug", "board"],
            )
        self.assertEqual(issue["number"], 6)
        body = recorder.calls[0]["json"]
        self.assertEqual(body["description"], "why")
        self.assertEqual(body["labels"], "bug,board")
        self.assertEqual(
            recorder.calls[0]["url"], "https://gitlab.com/api/v4/projects/g%2Fr/issues"
        )

    def test_forgejo_create_resolves_label_ids(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(200, [{"id": 11, "name": "bug"}, {"id": 12, "name": "docs"}]),
                FakeResponse(
                    201,
                    {
                        "number": 7,
                        "html_url": "https://forgejo.navinspire.ai/Navinspire/navin-claw/issues/7",
                    },
                ),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            issue = forge_api.create_issue(
                _remote(), "tok", title="Ship it", labels=["bug", "ghost"]
            )
        self.assertEqual(issue["number"], 7)
        # Gitea/Forgejo takes label ids, never names; unknown names are dropped.
        self.assertEqual(recorder.calls[1]["json"]["labels"], [11])

    def test_forgejo_create_without_matching_labels_still_files(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(200, []),
                FakeResponse(
                    201,
                    {"number": 7, "html_url": "https://forgejo.navinspire.ai/o/r/issues/7"},
                ),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            forge_api.create_issue(_remote(), "tok", title="Ship it", labels=["ghost"])
        self.assertNotIn("labels", recorder.calls[1]["json"])

    def test_comment_then_close_on_github(self) -> None:
        recorder = RequestRecorder([FakeResponse(201, {"id": 1}), FakeResponse(200, {})])
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.close_issue(
                ForgeRemote(GITHUB, "github.com", "o", "r"), "tok", 4, comment="fixed"
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["commented"])
        self.assertEqual(recorder.calls[0]["method"], "POST")
        self.assertEqual(
            recorder.calls[0]["url"], "https://api.github.com/repos/o/r/issues/4/comments"
        )
        self.assertEqual(recorder.calls[1]["method"], "PATCH")
        self.assertEqual(recorder.calls[1]["json"], {"state": "closed"})

    def test_gitlab_notes_and_state_event(self) -> None:
        recorder = RequestRecorder([FakeResponse(201, {"id": 1}), FakeResponse(200, {})])
        with patch.object(forge_api.httpx, "request", recorder):
            forge_api.close_issue(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "tok", 4, comment="fixed"
            )
        self.assertEqual(
            recorder.calls[0]["url"],
            "https://gitlab.com/api/v4/projects/g%2Fr/issues/4/notes",
        )
        self.assertEqual(recorder.calls[1]["method"], "PUT")
        self.assertEqual(recorder.calls[1]["json"], {"state_event": "close"})

    def test_forgejo_close_uses_the_issues_endpoint(self) -> None:
        recorder = RequestRecorder([FakeResponse(201, {"id": 1}), FakeResponse(200, {})])
        with patch.object(forge_api.httpx, "request", recorder):
            forge_api.close_issue(_remote(), "tok", 4, comment="fixed")
        self.assertEqual(
            recorder.calls[1]["url"],
            "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/navin-claw/issues/4",
        )
        self.assertEqual(recorder.calls[1]["json"], {"state": "closed"})

    def test_close_without_comment_makes_one_call(self) -> None:
        recorder = RequestRecorder([FakeResponse(200, {})])
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.close_issue(_remote(), "tok", 4)
        self.assertFalse(result["commented"])
        self.assertEqual(len(recorder.calls), 1)

    def test_empty_comment_is_refused_before_any_call(self) -> None:
        recorder = RequestRecorder([])
        with patch.object(forge_api.httpx, "request", recorder):
            with self.assertRaises(ForgeError) as ctx:
                forge_api.comment_issue(_remote(), "tok", 4, "   ")
        self.assertEqual(ctx.exception.code, "forge_empty_comment")
        self.assertEqual(recorder.calls, [])

    def test_permission_errors_are_actionable(self) -> None:
        for status, code, expected in (
            (401, "forge_token_invalid", "rejected the token"),
            (403, "forge_token_forbidden", "missing the scope"),
            (404, "forge_repo_not_found", "not found"),
            (422, "forge_rejected", "refused"),
        ):
            with self.subTest(status=status):
                with patch.object(
                    forge_api.httpx, "request", RequestRecorder([FakeResponse(status, {})])
                ):
                    with self.assertRaises(ForgeError) as ctx:
                        forge_api.close_issue(_remote(), "tok", 4)
                self.assertEqual(ctx.exception.code, code)
                self.assertIn(expected, ctx.exception.message)

    def test_network_failure_redacts_credentials(self) -> None:
        def _boom(method, url, **kwargs):
            raise httpx.ConnectError("cannot reach https://bob:s3cret@forgejo.navinspire.ai")

        with patch.object(forge_api.httpx, "request", _boom):
            with self.assertRaises(ForgeError) as ctx:
                forge_api.list_issues(_remote(), "tok")
        self.assertEqual(ctx.exception.code, "forge_unreachable")
        self.assertNotIn("s3cret", ctx.exception.message)


# ---------------------------------------------------------------------------
# One request by number, and closing it
# ---------------------------------------------------------------------------


class RequestByNumberTest(unittest.TestCase):
    def test_github_merged_pull_request_reads_as_merged(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(
                    200,
                    {
                        "number": 9,
                        "state": "closed",
                        "merged": True,
                        "merged_at": "2026-01-01T00:00:00Z",
                        "html_url": "https://github.com/o/r/pull/9",
                        "head": {"ref": "feature"},
                        "base": {"ref": "main"},
                    },
                )
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            request = forge_api.get_request(
                ForgeRemote(GITHUB, "github.com", "o", "r"), "tok", 9
            )
        self.assertTrue(request["merged"])
        self.assertEqual(request["state"], "merged")

    def test_gitlab_closed_without_merge_is_not_merged(self) -> None:
        recorder = RequestRecorder(
            [FakeResponse(200, {"iid": 9, "state": "closed", "web_url": "https://x/9"})]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            request = forge_api.get_request(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "tok", 9
            )
        self.assertFalse(request["merged"])
        self.assertEqual(request["state"], "closed")

    def test_close_request_per_forge(self) -> None:
        cases = [
            (
                ForgeRemote(GITHUB, "github.com", "o", "r"),
                "PATCH",
                "https://api.github.com/repos/o/r/pulls/9",
                {"state": "closed"},
            ),
            (
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"),
                "PUT",
                "https://gitlab.com/api/v4/projects/g%2Fr/merge_requests/9",
                {"state_event": "close"},
            ),
            # Forgejo models a pull request as an issue: closing goes there.
            (
                _remote(),
                "PATCH",
                "https://forgejo.navinspire.ai/api/v1/repos/Navinspire/navin-claw/issues/9",
                {"state": "closed"},
            ),
        ]
        for remote, method, url, body in cases:
            with self.subTest(forge=remote.kind):
                recorder = RequestRecorder([FakeResponse(200, {})])
                with patch.object(forge_api.httpx, "request", recorder):
                    forge_api.close_request(remote, "tok", 9)
                self.assertEqual(recorder.calls[0]["method"], method)
                self.assertEqual(recorder.calls[0]["url"], url)
                self.assertEqual(recorder.calls[0]["json"], body)


# ---------------------------------------------------------------------------
# Reviews with inline comments
# ---------------------------------------------------------------------------


class ReviewTest(unittest.TestCase):
    COMMENTS = [{"path": "a.py", "line": 12, "side": "RIGHT", "body": "fix this"}]

    def test_github_posts_one_review(self) -> None:
        recorder = RequestRecorder(
            [FakeResponse(200, {"id": 5, "html_url": "https://github.com/o/r/pull/1#r5"})]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.create_review(
                ForgeRemote(GITHUB, "github.com", "o", "r"),
                "tok",
                1,
                body="summary",
                comments=self.COMMENTS,
                event="REQUEST_CHANGES",
            )
        self.assertEqual(result["posted"], 1)
        body = recorder.calls[0]["json"]
        self.assertEqual(body["event"], "REQUEST_CHANGES")
        self.assertEqual(body["comments"][0]["line"], 12)

    def test_forgejo_uses_new_position_and_approved(self) -> None:
        recorder = RequestRecorder([FakeResponse(200, {"id": 5})])
        with patch.object(forge_api.httpx, "request", recorder):
            forge_api.create_review(
                _remote(), "tok", 1, body="summary", comments=self.COMMENTS, event="APPROVE"
            )
        body = recorder.calls[0]["json"]
        self.assertEqual(body["event"], "APPROVED")
        self.assertEqual(body["comments"][0]["new_position"], 12)
        self.assertNotIn("line", body["comments"][0])

    def test_gitlab_pins_discussions_to_the_diff(self) -> None:
        recorder = RequestRecorder(
            [
                # The MR read that carries diff_refs.
                FakeResponse(
                    200,
                    {
                        "iid": 1,
                        "diff_refs": {
                            "base_sha": "b1",
                            "start_sha": "s1",
                            "head_sha": "h1",
                        },
                    },
                ),
                FakeResponse(201, {"id": "d1"}),
                FakeResponse(201, {"id": 77, "web_url": "https://gitlab.com/g/r/-/mr/1#n77"}),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.create_review(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"),
                "tok",
                1,
                body="summary",
                comments=self.COMMENTS,
            )
        self.assertEqual(result["posted"], 1)
        position = recorder.calls[1]["json"]["position"]
        self.assertEqual(position["new_line"], 12)
        self.assertEqual(position["head_sha"], "h1")
        self.assertEqual(position["position_type"], "text")
        # The summary lands as a plain note: GitLab has no review object.
        self.assertTrue(recorder.calls[2]["url"].endswith("/notes"))

    def test_gitlab_keeps_going_when_one_line_left_the_diff(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(200, {"iid": 1, "diff_refs": {}}),
                FakeResponse(400, {"message": "line_code not found"}),
                FakeResponse(201, {"id": 5}),
                FakeResponse(201, {"id": 77}),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            result = forge_api.create_review(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"),
                "tok",
                1,
                body="summary",
                comments=[
                    {"path": "gone.py", "line": 3, "body": "x"},
                    {"path": "a.py", "line": 12, "body": "y"},
                ],
            )
        self.assertEqual(result["posted"], 1)
        self.assertEqual(result["failed"][0]["path"], "gone.py")

    def test_changed_paths_per_forge(self) -> None:
        github = RequestRecorder([FakeResponse(200, [{"filename": "a.py"}])])
        with patch.object(forge_api.httpx, "request", github):
            self.assertEqual(
                forge_api.request_files(ForgeRemote(GITHUB, "github.com", "o", "r"), "t", 1),
                ["a.py"],
            )
        gitlab = RequestRecorder([FakeResponse(200, [{"new_path": "a.py"}])])
        with patch.object(forge_api.httpx, "request", gitlab):
            self.assertEqual(
                forge_api.request_files(ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "t", 1),
                ["a.py"],
            )
        self.assertTrue(gitlab.calls[0]["url"].endswith("/merge_requests/1/diffs"))
        forgejo = RequestRecorder([FakeResponse(200, [{"filename": "a.py"}])])
        with patch.object(forge_api.httpx, "request", forgejo):
            self.assertEqual(forge_api.request_files(_remote(), "t", 1), ["a.py"])

    def test_gitlab_falls_back_to_changes_on_older_servers(self) -> None:
        recorder = RequestRecorder(
            [FakeResponse(404, {}), FakeResponse(200, {"changes": [{"new_path": "a.py"}]})]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            paths = forge_api.request_files(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "t", 1
            )
        self.assertEqual(paths, ["a.py"])
        self.assertTrue(recorder.calls[1]["url"].endswith("/changes"))


# ---------------------------------------------------------------------------
# Failed CI log
# ---------------------------------------------------------------------------


class FailedLogTest(unittest.TestCase):
    def test_gitlab_returns_the_job_trace(self) -> None:
        recorder = RequestRecorder(
            [
                FakeResponse(200, [{"id": 51}]),
                FakeResponse(200, [{"id": 900, "status": "failed"}]),
                FakeResponse(200, None, text="E   assert 1 == 2\n"),
            ]
        )
        with patch.object(forge_api.httpx, "request", recorder):
            excerpt = forge_api.failed_log_excerpt(
                ForgeRemote(GITLAB, "gitlab.com", "g", "r"), "tok", branch="feature/x"
            )
        self.assertIn("assert 1 == 2", excerpt)
        self.assertTrue(recorder.calls[2]["url"].endswith("/jobs/900/trace"))

    def test_other_forges_return_nothing_rather_than_guessing(self) -> None:
        recorder = RequestRecorder([])
        with patch.object(forge_api.httpx, "request", recorder):
            self.assertEqual(
                forge_api.failed_log_excerpt(_remote(), "tok", branch="feature/x"), ""
            )
            self.assertEqual(
                forge_api.failed_log_excerpt(
                    ForgeRemote(GITHUB, "github.com", "o", "r"), "tok", branch="x"
                ),
                "",
            )
        self.assertEqual(recorder.calls, [])


if __name__ == "__main__":
    unittest.main()
