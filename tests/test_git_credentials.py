# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Git credential bridge, forge accounts / device sign-in, merge, forge tool.

Every network call is mocked; nothing touches a real forge or the user's
config file.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from navin.utils import git_credentials as gc
from navin.webui import forge_api, git_accounts
from navin.webui.forge_api import FORGEJO, GITHUB, GITLAB, ForgeError, ForgeRemote

_TOKEN_ENV = (
    "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GL_TOKEN", "GLAB_TOKEN",
    "FORGEJO_TOKEN", "FORGEJO_ACCESS_TOKEN", "GITEA_TOKEN", "GITEA_SERVER_TOKEN",
    "GITEA_ACCESS_TOKEN", "GITEA_HTTP_TOKEN", "TEA_TOKEN", "BITBUCKET_TOKEN",
    "BITBUCKET_ACCESS_TOKEN", "GIT_SSH_COMMAND", "GIT_SSH", "GIT_CONFIG_COUNT",
    "NAVIN_GITHUB_OAUTH_CLIENT_ID",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("NAVIN_FORGE_TOKEN_"):
            monkeypatch.delenv(name, raising=False)
    # Never ask a real gh for a token.
    monkeypatch.setattr(forge_api, "gh_cli_token", lambda host: "")
    git_accounts.clear_account_cache()
    git_accounts._flows.clear()
    yield
    git_accounts.clear_account_cache()
    git_accounts._flows.clear()


def _config(tokens: dict[str, str] | None = None, hosts: dict[str, str] | None = None, client_id: str = ""):
    forge = SimpleNamespace(tokens=tokens or {}, hosts=hosts or {}, github_oauth_client_id=client_id)
    return SimpleNamespace(tools=SimpleNamespace(forge=forge))


def _decoded_header(env: dict[str, str], index: int) -> str:
    value = env[f"GIT_CONFIG_VALUE_{index}"]
    assert value.startswith("Authorization: Basic ")
    return base64.b64decode(value.split()[-1]).decode()


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,kind,user",
    [
        ("github.com", GITHUB, "x-access-token"),
        ("gitlab.com", GITLAB, "oauth2"),
        ("codeberg.org", FORGEJO, "git"),
        ("bitbucket.org", gc.BITBUCKET, "x-token-auth"),
    ],
)
def test_username_per_forge(host: str, kind: str, user: str) -> None:
    cred = gc.credential_for_url(
        f"https://{host}/team/repo.git", config=_config({host: "tok-123"})
    )
    assert cred is not None
    assert (cred.kind, cred.username, cred.source) == (kind, user, "settings")
    env = gc.git_env([f"https://{host}/team/repo.git"], config=_config({host: "tok-123"}), base={})
    assert env["GIT_CONFIG_KEY_0"] == f"http.https://{host}/.extraheader"
    assert _decoded_header(env, 0) == f"{user}:tok-123"


def test_forgejo_uses_the_known_token_owner() -> None:
    gc.remember_login("forgejo.example.com", "aymen")
    try:
        cred = gc.credential_for_url(
            "https://forgejo.example.com/a/b", config=_config({"forgejo.example.com": "t"})
        )
        assert cred is not None and cred.username == "aymen"
    finally:
        gc._known_logins.clear()


def test_env_is_non_interactive_and_token_never_in_argv_or_repr() -> None:
    env = gc.git_env(
        ["https://github.com/o/r.git", "git@github.com:o/r.git"],
        config=_config({"github.com": "ghp_secret"}),
        base={"PATH": "/usr/bin"},
    )
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=accept-new" in env["GIT_SSH_COMMAND"]
    assert env["GIT_CONFIG_COUNT"] == "1"
    # The token only exists base64-encoded inside an env value.
    assert all("ghp_secret" not in value for value in env.values())
    cred = gc.credential_for_url("https://github.com/o/r", config=_config({"github.com": "ghp_secret"}))
    assert "ghp_secret" not in repr(cred)


def test_ssh_and_embedded_credentials_and_local_paths_get_no_header() -> None:
    cfg = _config({"github.com": "t"})
    for url in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git",
                "https://me:pw@github.com/o/r.git", "/srv/repo.git"):
        assert gc.credential_for_url(url, config=cfg) is None
    env = gc.git_env(["git@github.com:o/r.git"], config=cfg, base={})
    assert "GIT_CONFIG_COUNT" not in env


def test_existing_git_config_count_is_appended_to() -> None:
    env = gc.git_env(
        ["https://gitlab.com/g/r"],
        config=_config({"gitlab.com": "glpat"}),
        base={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.x", "GIT_CONFIG_VALUE_0": "y"},
    )
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.x"
    assert env["GIT_CONFIG_KEY_1"] == "http.https://gitlab.com/.extraheader"


def test_header_is_scoped_per_host_and_port() -> None:
    env = gc.git_env(
        ["https://git.example.com:8443/a/b", "https://github.com/a/b"],
        config=_config({"git.example.com": "forge-tok"}, {"git.example.com": "forgejo"}),
        base={},
    )
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://git.example.com:8443/.extraheader"


def test_env_tokens_and_tokens_override() -> None:
    with patch.dict(os.environ, {"GITHUB_TOKEN": "from-env"}):
        cred = gc.credential_for_url("https://github.com/o/r", tokens={})
    assert cred is not None and cred.source == "env:GITHUB_TOKEN"
    cred = gc.credential_for_url("https://github.com/o/r", tokens={"github.com": "snap"})
    assert cred is not None and cred.token == "snap"


@pytest.mark.parametrize(
    "existing,expected",
    [
        (None, "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"),
        ("ssh -i ~/.ssh/k", "ssh -i ~/.ssh/k -o BatchMode=yes -o StrictHostKeyChecking=accept-new"),
        ("ssh -o BatchMode=yes", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"),
        ("plink -batch", "plink -batch"),
    ],
)
def test_ssh_command_preserves_user_choice(existing, expected) -> None:
    assert gc.ssh_command(existing) == expected


def test_git_ssh_is_not_overridden() -> None:
    env = gc.apply_noninteractive({"GIT_SSH": "/usr/bin/plink"})
    assert "GIT_SSH_COMMAND" not in env


def test_wsl_forwarding_lists_the_auth_entries() -> None:
    env = gc.git_env(
        ["https://github.com/o/r"], config=_config({"github.com": "t"}), base={}, wsl=True
    )
    forwarded = env["WSLENV"].split(":")
    for name in ("GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE", "GIT_CONFIG_COUNT",
                 "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_SSH_COMMAND"):
        assert name in forwarded


@pytest.mark.parametrize(
    "text,kind",
    [
        ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", "auth"),
        ("fatal: Authentication failed for 'https://x/'", "auth"),
        ("git@github.com: Permission denied (publickey).", "ssh_auth"),
        ("remote: Repository not found.", "not_found"),
        ("The requested URL returned error: 403", "forbidden"),
        ("Could not resolve host: github.com", "network"),
        ("fatal: something else", None),
    ],
)
def test_classify_git_failure(text, kind) -> None:
    assert gc.classify_git_failure(text) == kind


def test_is_network_command() -> None:
    assert gc.is_network_command(["push", "-u", "origin", "HEAD"])
    assert gc.is_network_command(("fetch", "--all"))
    assert not gc.is_network_command(["commit", "-m", "x"])


def test_run_git_write_push_gets_bridge_env(tmp_path: Path) -> None:
    from navin.webui import project_search

    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured.setdefault("calls", []).append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(project_search, "_git_argv", return_value=["git", "-C", str(tmp_path)]), \
            patch.object(gc, "git_env_for_repo", return_value={"GIT_TERMINAL_PROMPT": "0", "MARK": "1"}), \
            patch.object(project_search.subprocess, "run", side_effect=fake_run):
        project_search._run_git_write(tmp_path, "push")
        project_search._run_git_write(tmp_path, "add", "-A")
    push_kwargs = captured["calls"][0][1]
    add_kwargs = captured["calls"][1][1]
    assert push_kwargs["env"]["MARK"] == "1"
    assert "env" not in add_kwargs


def test_board_push_gets_bridge_env(tmp_path: Path) -> None:
    from navin.board import autonomy

    seen: list[dict[str, Any]] = []

    def fake_run(argv, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(gc, "git_env_for_repo", return_value={"MARK": "board"}), \
            patch.object(autonomy.subprocess, "run", side_effect=fake_run):
        autonomy._git(tmp_path, "push", "-u", "origin", "b")
        autonomy._git(tmp_path, "status")
    assert seen[0]["env"] == {"MARK": "board"}
    assert "env" not in seen[1]


def test_agent_git_push_carries_header_not_argv(monkeypatch, tmp_path: Path) -> None:
    from navin.agent.tools import git as git_tool

    calls: list[tuple[tuple, dict]] = []

    async def fake_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        out = b""
        if "config" in argv and "--get-regexp" in argv:
            out = b"remote.origin.url https://github.com/o/r.git\n"
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(out, b"")))

    monkeypatch.setattr(git_tool.asyncio, "create_subprocess_exec", fake_exec)
    token = git_tool._GIT_FORGE_TOKENS.set({"github.com": "ghp_agent"})
    try:
        code, _out, _err = asyncio.run(git_tool._git(tmp_path, ["push", "origin", "main"]))
    finally:
        git_tool._GIT_FORGE_TOKENS.reset(token)
    assert code == 0
    argv, kwargs = calls[-1]
    assert "push" in argv
    assert all("ghp_agent" not in str(part) for part in argv)
    env = kwargs["env"]
    assert env["GCM_INTERACTIVE"] == "never"
    idx = int(env["GIT_CONFIG_COUNT"]) - 1
    assert env[f"GIT_CONFIG_KEY_{idx}"] == "http.https://github.com/.extraheader"
    assert _decoded_header(env, idx) == "x-access-token:ghp_agent"


def test_agent_git_clone_authenticates_against_its_url(monkeypatch, tmp_path: Path) -> None:
    from navin.agent.tools import git as git_tool

    calls: list[tuple[tuple, dict]] = []

    async def fake_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))

    monkeypatch.setattr(git_tool.asyncio, "create_subprocess_exec", fake_exec)
    token = git_tool._GIT_FORGE_TOKENS.set({"gitlab.com": "glpat"})
    try:
        asyncio.run(git_tool._git(tmp_path, ["clone", "--", "https://gitlab.com/g/r.git", "dest"]))
    finally:
        git_tool._GIT_FORGE_TOKENS.reset(token)
    assert len(calls) == 1  # no remote listing for a clone
    env = calls[0][1]["env"]
    assert _decoded_header(env, int(env["GIT_CONFIG_COUNT"]) - 1) == "oauth2:glpat"


# ---------------------------------------------------------------------------
# forge_api: current user and merge
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, payload: Any = None, headers: dict[str, str] | None = None):
        self.status_code = status
        self._payload = payload
        self.text = "" if payload is None else json.dumps(payload)
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Recorder:
    def __init__(self, responses: list[_Resp]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def _remote(kind: str, host: str) -> ForgeRemote:
    return ForgeRemote(kind=kind, host=host, owner="org", repo="app")


@pytest.mark.parametrize(
    "kind,host,method,url",
    [
        (GITHUB, "github.com", "PUT", "https://api.github.com/repos/org/app/pulls/7/merge"),
        (GITLAB, "gitlab.com", "PUT", "https://gitlab.com/api/v4/projects/org%2Fapp/merge_requests/7/merge"),
        (FORGEJO, "codeberg.org", "POST", "https://codeberg.org/api/v1/repos/org/app/pulls/7/merge"),
    ],
)
def test_merge_request_per_forge(kind, host, method, url) -> None:
    recorder = _Recorder([_Resp(200, {"sha": "abc", "merged": True})])
    with patch.object(forge_api.httpx, "request", recorder):
        result = forge_api.merge_request(_remote(kind, host), "t", 7, method="squash", sha="deadbeef")
    call = recorder.calls[0]
    assert (call["method"], call["url"]) == (method, url)
    body = call["json"]
    if kind == GITHUB:
        assert body == {"merge_method": "squash", "sha": "deadbeef"}
    elif kind == GITLAB:
        assert body == {"squash": True, "sha": "deadbeef"}
    else:
        assert body == {"Do": "squash", "head_commit_id": "deadbeef"}
    assert result["ok"] and result["merged"] and result["method"] == "squash"


def test_merge_requires_an_explicit_method() -> None:
    with pytest.raises(ForgeError) as info:
        forge_api.merge_request(_remote(GITHUB, "github.com"), "t", 1, method="")
    assert info.value.code == "forge_bad_merge_method"


def test_merge_not_mergeable_is_a_clear_conflict() -> None:
    recorder = _Recorder([_Resp(405, {"message": "Pull Request is not mergeable"})])
    with patch.object(forge_api.httpx, "request", recorder):
        with pytest.raises(ForgeError) as info:
            forge_api.merge_request(_remote(GITHUB, "github.com"), "t", 3, method="merge")
    assert info.value.code == "forge_not_mergeable"
    assert info.value.status == 409


def test_current_user_reads_github_scopes() -> None:
    with patch.object(
        forge_api.httpx, "get",
        return_value=_Resp(200, {"login": "octo"}, {"X-OAuth-Scopes": "repo, workflow"}),
    ):
        info = forge_api.current_user(_remote(GITHUB, "github.com"), "t")
    assert info == {"login": "octo", "scopes": ["repo", "workflow"]}


def test_token_pointer_strings_name_the_new_settings_page() -> None:
    error = forge_api.token_missing_error(_remote(GITHUB, "github.com"))
    assert "Settings > Git & GitHub" in error.message


# ---------------------------------------------------------------------------
# Accounts / test / device flow
# ---------------------------------------------------------------------------


def test_list_accounts_never_returns_the_token(monkeypatch) -> None:
    cfg = _config({"gitlab.com": "glpat-secret"}, client_id="")
    monkeypatch.setattr(forge_api, "gh_cli_token", lambda host: "gho_from_gh" if host == "github.com" else "")

    def fake_user(remote, token):
        return {"login": f"user-{remote.host}", "scopes": ["api"] if remote.kind == GITLAB else None}

    with patch.object(forge_api, "current_user", side_effect=fake_user):
        payload = git_accounts.list_accounts(cfg)
    dumped = json.dumps(payload)
    assert "glpat-secret" not in dumped and "gho_from_gh" not in dumped
    rows = {row["host"]: row for row in payload["accounts"]}
    assert rows["gitlab.com"] == {
        "host": "gitlab.com", "kind": GITLAB, "login": "user-gitlab.com",
        "source": "settings", "scopes": ["api"],
    }
    assert rows["github.com"]["source"] == "gh"
    assert payload["device_flow"] == {"github": False}


def test_list_accounts_env_source_and_device_flag(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-tok")
    monkeypatch.setenv("NAVIN_GITHUB_OAUTH_CLIENT_ID", "Iv1.abc")
    with patch.object(forge_api, "current_user", return_value={"login": "me", "scopes": None}):
        payload = git_accounts.list_accounts(_config())
    assert payload["accounts"][0]["source"] == "env"
    assert payload["device_flow"] == {"github": True}


def test_account_lookup_is_cached() -> None:
    cfg = _config({"github.com": "t"})
    with patch.object(forge_api, "current_user", return_value={"login": "me", "scopes": []}) as user:
        git_accounts.list_accounts(cfg)
        git_accounts.list_accounts(cfg)
    assert user.call_count == 1


def test_test_account_ok_and_missing_token() -> None:
    with patch.object(forge_api, "current_user", return_value={"login": "me", "scopes": ["repo"]}):
        ok = git_accounts.test_account("github.com", _config({"github.com": "t"}))
    assert ok == {"ok": True, "login": "me", "scopes": ["repo"], "error": None}
    missing = git_accounts.test_account("gitlab.com", _config())
    assert missing["ok"] is False and "Settings > Git & GitHub" in missing["error"]


def test_test_account_reports_a_rejected_token() -> None:
    err = ForgeError("github.com rejected the token (401)", status=401, code="forge_token_invalid")
    with patch.object(forge_api, "current_user", side_effect=err):
        result = git_accounts.test_account("github.com", _config({"github.com": "bad"}))
    assert result["ok"] is False and "401" in result["error"]


class _Post:
    """Scripted ``httpx.post`` for the GitHub device endpoints."""

    def __init__(self, answers: list[dict[str, Any]]):
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> _Resp:
        self.calls.append({"url": url, **kwargs})
        return _Resp(200, self.answers.pop(0))


_DEVICE = {
    "device_code": "dev-secret", "user_code": "ABCD-1234",
    "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5,
}


def test_device_flow_happy_path(monkeypatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(git_accounts, "_store_github_token", lambda host, token: stored.update({host: token}))
    post = _Post([_DEVICE, {"error": "authorization_pending"}, {"access_token": "gho_new", "scope": "repo"}])
    cfg = _config(client_id="Iv1.client")
    with patch.object(git_accounts.httpx, "post", post), \
            patch.object(forge_api, "current_user", return_value={"login": "octo", "scopes": ["repo"]}):
        started = git_accounts.start_device_flow("github.com", cfg)
        assert set(started) == {"flow_id", "user_code", "verification_uri", "interval", "expires_in"}
        assert "dev-secret" not in json.dumps(started)
        assert post.calls[0]["url"] == git_accounts.GITHUB_DEVICE_CODE_URL
        assert post.calls[0]["data"] == {"client_id": "Iv1.client", "scope": "repo read:org workflow"}
        first = git_accounts.poll_device_flow(started["flow_id"], cfg)
        assert first["status"] == "pending"
        # Polling faster than the interval is answered locally.
        throttled = git_accounts.poll_device_flow(started["flow_id"], cfg)
        assert throttled["status"] == "pending" and len(post.calls) == 2
        git_accounts._flows[started["flow_id"]]["next_poll"] = 0.0
        done = git_accounts.poll_device_flow(started["flow_id"], cfg)
    assert done == {"status": "ok", "login": "octo"}
    assert stored == {"github.com": "gho_new"}
    assert post.calls[-1]["data"]["device_code"] == "dev-secret"
    assert post.calls[-1]["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    # The flow is gone once used.
    assert git_accounts.poll_device_flow(started["flow_id"], cfg) == {"status": "expired"}


@pytest.mark.parametrize(
    "answer,status",
    [
        ({"error": "expired_token"}, "expired"),
        ({"error": "access_denied"}, "denied"),
        ({"error": "slow_down", "interval": 10}, "slow_down"),
        ({"error": "incorrect_client_credentials", "error_description": "bad"}, "error"),
    ],
)
def test_device_flow_terminal_states(answer, status) -> None:
    post = _Post([_DEVICE, answer])
    cfg = _config(client_id="Iv1.client")
    with patch.object(git_accounts.httpx, "post", post):
        started = git_accounts.start_device_flow("github.com", cfg)
        result = git_accounts.poll_device_flow(started["flow_id"], cfg)
    assert result["status"] == status
    if status == "slow_down":
        assert result["interval"] == 10
        assert started["flow_id"] in git_accounts._flows
    else:
        assert started["flow_id"] not in git_accounts._flows


def test_device_flow_expires_locally() -> None:
    post = _Post([_DEVICE])
    cfg = _config(client_id="Iv1.client")
    with patch.object(git_accounts.httpx, "post", post):
        started = git_accounts.start_device_flow("github.com", cfg)
    git_accounts._flows[started["flow_id"]]["expires_at"] = 0.0
    assert git_accounts.poll_device_flow(started["flow_id"], cfg) == {"status": "expired"}
    assert git_accounts.poll_device_flow("unknown", cfg) == {"status": "expired"}


def test_device_flow_needs_a_client_id_and_github() -> None:
    with pytest.raises(git_accounts.GitAccountError) as info:
        git_accounts.start_device_flow("github.com", _config())
    assert info.value.code == "device_flow_unavailable"
    with pytest.raises(git_accounts.GitAccountError) as info:
        git_accounts.start_device_flow("gitlab.com", _config(client_id="x"))
    assert info.value.code == "device_flow_unsupported"


def test_device_token_is_stored_encrypted_through_settings(tmp_path: Path, monkeypatch) -> None:
    from navin.config import loader
    from navin.config.schema import Config

    path = tmp_path / "config.json"
    monkeypatch.setattr(loader, "_current_config_path", path)
    loader.save_config(Config())
    git_accounts._store_github_token("github.com", "gho_stored_secret")
    raw = path.read_text(encoding="utf-8")
    assert "gho_stored_secret" not in raw
    config = loader.load_config()
    assert config.tools.forge.tokens["github.com"] == "gho_stored_secret"
    assert config.tools.forge.hosts["github.com"] == GITHUB


def test_schema_has_the_client_id_field() -> None:
    from navin.config.schema import Config

    config = Config.model_validate({"tools": {"forge": {"githubOauthClientId": "Iv1.x"}}})
    assert config.tools.forge.github_oauth_client_id == "Iv1.x"
    assert git_accounts.device_flow_client_id(config) == "Iv1.x"


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class _Request:
    def __init__(self, path: str) -> None:
        self.path = path
        self.headers: dict[str, str] = {}


def _handler():
    from navin.webui.stall_watch import StallReporter
    from navin.webui.ws_http import GatewayHTTPHandler

    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: True
    handler._log = SimpleNamespace(exception=lambda *a: None, warning=lambda *a: None)
    handler._stalls = StallReporter(handler._log)
    return handler


@pytest.mark.parametrize(
    "path,action",
    [
        ("/api/webui/git/accounts", "accounts"),
        ("/api/webui/git/test", "test"),
        ("/api/webui/git/device/start", "device_start"),
        ("/api/webui/git/device/poll", "device_poll"),
    ],
)
def test_git_account_routes_are_registered(path: str, action: str) -> None:
    handler = _handler()
    seen: dict[str, str] = {}

    async def capture(request, got):
        seen["action"] = got
        return "response"

    handler._handle_webui_git_accounts = capture
    assert asyncio.run(handler._dispatch_misc_routes(None, _Request(path), path)) == "response"
    assert seen["action"] == action


def test_accounts_route_returns_json(monkeypatch) -> None:
    handler = _handler()
    monkeypatch.setattr(git_accounts, "list_accounts", lambda: {"accounts": [], "device_flow": {"github": False}})
    response = asyncio.run(handler._handle_webui_git_accounts(_Request("/api/webui/git/accounts"), "accounts"))
    assert response.status_code == 200
    assert json.loads(response.body) == {"accounts": [], "device_flow": {"github": False}}


def test_test_route_passes_the_host(monkeypatch) -> None:
    handler = _handler()
    monkeypatch.setattr(git_accounts, "test_account", lambda host: {"ok": True, "login": host, "scopes": None, "error": None})
    response = asyncio.run(
        handler._handle_webui_git_accounts(_Request("/api/webui/git/test?host=gitlab.com"), "test")
    )
    assert json.loads(response.body)["login"] == "gitlab.com"


def test_device_route_errors_are_coded(monkeypatch) -> None:
    handler = _handler()

    def boom(host):
        raise git_accounts.GitAccountError("not configured", status=400, code="device_flow_unavailable")

    monkeypatch.setattr(git_accounts, "start_device_flow", boom)
    response = asyncio.run(
        handler._handle_webui_git_accounts(_Request("/api/webui/git/device/start?host=github.com"), "device_start")
    )
    assert response.status_code == 400
    assert json.loads(response.body)["code"] == "device_flow_unavailable"


def test_clone_route_returns_auth_required_json(monkeypatch) -> None:
    from navin.webui import git_clone

    handler = _handler()
    handler.workspaces = SimpleNamespace(default_scope=lambda: SimpleNamespace(project_path="/tmp"))

    def fail(*args, **kwargs):
        raise git_clone.GitCloneError(
            "github.com needs you to sign in", status=401, code="auth_required", host="github.com"
        )

    monkeypatch.setattr(git_clone, "clone_git_project", fail)
    response = asyncio.run(
        handler._handle_webui_git_clone(_Request("/api/webui/git/clone?url=org/private"))
    )
    assert response.status_code == 401
    body = json.loads(response.body)
    assert body["code"] == "auth_required" and body["host"] == "github.com"
    assert body["message"] == "github.com needs you to sign in"


# ---------------------------------------------------------------------------
# Forge tool
# ---------------------------------------------------------------------------


def _forge_tool(tmp_path: Path):
    from navin.agent.tools.forge import ForgeTool

    tool = object.__new__(ForgeTool)
    tool._root_or_error = lambda: (tmp_path, "")
    return tool


def _allow(monkeypatch, allowed: bool = True) -> list[Any]:
    from navin.agent import approval

    asked: list[Any] = []

    async def fake(request):
        asked.append(request)
        return SimpleNamespace(allowed=allowed, reason="denied by user")

    monkeypatch.setattr(approval, "request_approval", fake)
    return asked


@pytest.fixture
def _forge_remote(monkeypatch):
    remote = _remote(GITHUB, "github.com")
    from navin.agent.tools import forge as forge_tool

    monkeypatch.setattr(forge_tool.ForgeTool, "_resolve", lambda self, root: (remote, "tok"))
    return remote


@pytest.mark.parametrize(
    "action,kwargs,target,expected",
    [
        ("pr_list", {"state": "all"}, "list_requests", {"requests": [{"n": 1}]}),
        ("pr_view", {"number": 4}, "get_request", {"request": {"n": 1}}),
        ("issue_list", {}, "list_issues", {"issues": [{"n": 1}]}),
    ],
)
def test_forge_tool_read_dispatch(tmp_path, monkeypatch, _forge_remote, action, kwargs, target, expected) -> None:
    asked = _allow(monkeypatch)
    tool = _forge_tool(tmp_path)
    value = [{"n": 1}] if target != "get_request" else {"n": 1}
    with patch.object(forge_api, target, return_value=value) as fn:
        out = asyncio.run(tool.execute(action=action, **kwargs))
    payload = json.loads(out)
    for key, val in expected.items():
        assert payload[key] == val
    assert payload["repo"] == "org/app" and payload["forge"] == GITHUB
    assert fn.called and not asked
    assert tool.call_read_only({"action": action})


def test_forge_ci_status_with_no_run_says_waiting_will_not_help(tmp_path, monkeypatch) -> None:
    # The agent slept 240 s, polled, slept 180 s, polled: 0 runs, because the
    # push had gone to another repository. The answer now says so.
    from navin.agent.tools import forge as forge_tool

    remote = _remote(GITHUB, "github.com")
    monkeypatch.setattr(forge_tool.ForgeTool, "_resolve", lambda self, root: (remote, "tok"))
    tool = _forge_tool(tmp_path)
    empty = {"state": "none", "total": 0, "passing": 0, "failing": 0, "pending": 0, "items": []}
    with patch.object(forge_api, "check_summary", return_value=empty):
        payload = json.loads(asyncio.run(tool.execute(action="ci_status", branch="main")))
    assert "Waiting will not create one" in payload["note"]
    assert "path=" in payload["note"] and remote.slug in payload["note"]
    found = {**empty, "total": 1, "passing": 1}
    with patch.object(forge_api, "check_summary", return_value=found):
        payload = json.loads(asyncio.run(tool.execute(action="ci_status", branch="main")))
    assert "note" not in payload


def test_forge_tool_path_reads_the_other_repository_not_this_project(tmp_path, monkeypatch) -> None:
    # The reported loop: a push from ~/bbac, CI read on this project's origin,
    # 0 runs found, sleep and poll forever. path= reads the other repository.
    from navin.agent.tools import forge as forge_tool

    other = tmp_path / "bbac"
    (other / ".git").mkdir(parents=True)
    (other / "frontend").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    seen: list[Path] = []
    remote = _remote(GITHUB, "github.com")

    def resolve(self, root):
        seen.append(root)
        return remote, "tok"

    monkeypatch.setattr(forge_tool.ForgeTool, "_resolve", resolve)
    tool = _forge_tool(project)
    with patch.object(forge_api, "list_requests", return_value=[]) as listed:
        out = asyncio.run(tool.execute(action="pr_list", path=str(other / "frontend")))
    payload = json.loads(out)
    assert seen == [other.resolve()], "the repository top, found from a subfolder"
    assert payload["path"] == str(other.resolve())
    assert listed.called
    # without path= the project's own origin is read, and no path is reported
    with patch.object(forge_api, "list_requests", return_value=[]):
        out = asyncio.run(tool.execute(action="pr_list"))
    assert seen[-1] == project and "path" not in json.loads(out)


def test_forge_tool_path_refuses_what_is_not_a_repository(tmp_path, monkeypatch, _forge_remote) -> None:
    tool = _forge_tool(tmp_path)
    out = asyncio.run(tool.execute(action="ci_status", path=str(tmp_path / "gone")))
    assert "not a folder" in str(out)
    plain = tmp_path / "plain"
    plain.mkdir()
    out = asyncio.run(tool.execute(action="ci_status", path=str(plain)))
    assert "not inside a git repository" in str(out)


def test_forge_tool_merge_asks_and_dispatches(tmp_path, monkeypatch, _forge_remote) -> None:
    asked = _allow(monkeypatch)
    tool = _forge_tool(tmp_path)
    with patch.object(forge_api, "merge_request", return_value={"ok": True, "merged": True}) as merge:
        out = asyncio.run(tool.execute(action="pr_merge", number=9, method="rebase", sha="abc"))
    assert json.loads(out)["merged"] is True
    assert merge.call_args.kwargs["method"] == "rebase"
    assert merge.call_args.kwargs["sha"] == "abc"
    assert asked and asked[0].tool == "forge" and asked[0].allow_when_unattended is False
    assert not tool.call_read_only({"action": "pr_merge"})


def test_forge_tool_merge_needs_method_and_respects_refusal(tmp_path, monkeypatch, _forge_remote) -> None:
    tool = _forge_tool(tmp_path)
    out = asyncio.run(tool.execute(action="pr_merge", number=9))
    assert "method=merge|squash|rebase" in str(out)
    _allow(monkeypatch, allowed=False)
    with patch.object(forge_api, "merge_request") as merge:
        out = asyncio.run(tool.execute(action="pr_merge", number=9, method="merge"))
    assert "refused" in str(out) and not merge.called


def test_forge_tool_review_maps_event(tmp_path, monkeypatch, _forge_remote) -> None:
    _allow(monkeypatch)
    tool = _forge_tool(tmp_path)
    with patch.object(forge_api, "create_review", return_value={"ok": True}) as review:
        asyncio.run(tool.execute(action="pr_review", number=2, event="request_changes", body="fix x"))
    assert review.call_args.kwargs["event"] == "REQUEST_CHANGES"
    assert review.call_args.kwargs["body"] == "fix x"


def test_forge_tool_issue_writes(tmp_path, monkeypatch, _forge_remote) -> None:
    asked = _allow(monkeypatch)
    tool = _forge_tool(tmp_path)
    with patch.object(forge_api, "create_issue", return_value={"url": "u"}) as create, \
            patch.object(forge_api, "comment_issue", return_value={"ok": True}) as comment, \
            patch.object(forge_api, "close_issue", return_value={"ok": True}) as close:
        asyncio.run(tool.execute(action="issue_create", title="Bug", body="b", labels=["bug"]))
        asyncio.run(tool.execute(action="issue_comment", number=5, body="hi"))
        asyncio.run(tool.execute(action="issue_close", number=5, body="done"))
    assert create.call_args.kwargs["labels"] == ["bug"]
    assert comment.call_args.args[2:] == (5, "hi")
    assert close.call_args.kwargs["comment"] == "done"
    assert [req.allow_when_unattended for req in asked] == [True, True, False]


def test_forge_tool_pr_create_and_ci(tmp_path, monkeypatch, _forge_remote) -> None:
    from navin.agent.tools import forge as forge_tool

    _allow(monkeypatch)
    monkeypatch.setattr(forge_tool, "_current_branch", lambda root: "feature/x")
    tool = _forge_tool(tmp_path)
    with patch.object(forge_api, "find_open_request", return_value=None), \
            patch.object(forge_api, "default_branch", return_value="main"), \
            patch.object(forge_api, "create_request", return_value={"url": "u", "number": 3}) as create:
        out = json.loads(asyncio.run(tool.execute(action="pr_create", title="T")))
    assert out["created"] is True
    assert create.call_args.kwargs["head"] == "feature/x"
    assert create.call_args.kwargs["base"] == "main"
    with patch.object(forge_api, "check_summary", return_value={"state": "success"}) as checks:
        out = json.loads(asyncio.run(tool.execute(action="ci_status")))
    assert out["branch"] == "feature/x" and out["checks"]["state"] == "success"
    assert checks.call_args.kwargs["branch"] == "feature/x"


def test_forge_tool_reports_missing_token(tmp_path, monkeypatch) -> None:
    from navin.agent.tools import forge as forge_tool

    remote = _remote(GITHUB, "github.com")

    def missing(self, root):
        raise forge_api.token_missing_error(remote)

    monkeypatch.setattr(forge_tool.ForgeTool, "_resolve", missing)
    out = asyncio.run(_forge_tool(tmp_path).execute(action="pr_list"))
    assert "Settings > Git & GitHub" in str(out)


def test_forge_tool_is_on_the_code_build_allowlist() -> None:
    from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS

    assert "forge" in CODE_BUILD_ALLOWED_TOOLS
