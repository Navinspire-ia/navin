"""Git readiness on this machine after an account is connected (navin/webui/git_setup.py)."""

import json
import subprocess

import pytest

from navin.config.schema import Config
from navin.webui import forge_api, git_accounts, git_setup


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fresh repository with an empty global git config of its own."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                 "EMAIL", "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _global(key):
    out = subprocess.run(["git", "config", "--global", "--get", key], capture_output=True, text=True)
    return out.stdout.strip() or None


def test_windows_install_hint_is_the_unattended_winget_command():
    assert git_setup.install_hint(git_setup.WINDOWS)["command"] == (
        "winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements"
    )
    assert git_setup.install_hint(git_setup.WINDOWS, upgrade=True)["command"].startswith(
        "winget upgrade --id Git.Git -e --silent"
    )
    assert "brew" in git_setup.install_hint(git_setup.MACOS)["command"] or "xcode-select" in (
        git_setup.install_hint(git_setup.MACOS)["command"]
    )
    assert git_setup.install_hint(git_setup.LINUX, in_wsl_target=True)["command"] == (
        "sudo apt-get update && sudo apt-get install -y git"
    )


def test_each_os_gets_its_own_secure_credential_helper():
    windows = git_setup.recommended_helper(git_setup.WINDOWS, in_wsl=False, libsecret=False)
    macos = git_setup.recommended_helper(git_setup.MACOS, in_wsl=False, libsecret=False)
    wsl = git_setup.recommended_helper(git_setup.LINUX, in_wsl=True, libsecret=False)
    keyring = git_setup.recommended_helper(git_setup.LINUX, in_wsl=False, libsecret=True)
    bare = git_setup.recommended_helper(git_setup.LINUX, in_wsl=False, libsecret=False)
    assert (windows["helper"], macos["helper"], keyring["helper"], bare["helper"]) == (
        "manager", "osxkeychain", "libsecret", "cache",
    )
    assert "git-credential-manager.exe" in wsl["command"]
    for helper in (windows, macos, wsl, keyring, bare):
        assert "store" not in helper["command"]


@pytest.mark.parametrize(
    ("value", "family"),
    [
        ("manager", "manager"),
        ("store", "store"),
        ("cache --timeout=3600", "cache"),
        ("/usr/lib/git-core/git-credential-libsecret", "libsecret"),
        ("!/usr/bin/gh auth git-credential", "gh"),
        ("!my-script", "custom"),
    ],
)
def test_helper_family(value, family):
    assert git_setup.helper_name(value) == family


def test_ssh_remote_gets_its_https_equivalent():
    assert git_setup.remote_protocol("git@github.com:owner/repo.git") == "ssh"
    assert git_setup.remote_protocol("https://github.com/owner/repo.git") == "https"
    assert git_setup.remote_protocol("C:\\work\\repo") == "local"
    assert git_setup.https_equivalent("git@github.com:owner/repo.git") == "https://github.com/owner/repo.git"
    assert git_setup.https_equivalent("https://github.com/owner/repo.git") is None


def test_commit_identity_uses_the_forge_noreply_address():
    assert git_setup.identity_for_account(forge_api.GITHUB, "github.com", "octo", {"id": "42"}) == (
        "octo", "42+octo@users.noreply.github.com",
    )
    assert git_setup.identity_for_account(
        forge_api.GITHUB, "github.com", "octo", {"id": "42", "name": "Octo Cat", "email": "octo@example.com"}
    ) == ("Octo Cat", "octo@example.com")
    assert git_setup.identity_for_account(forge_api.FORGEJO, "git.example.com", "me", {}) == (
        "me", "me@noreply.git.example.com",
    )


def test_missing_scopes_per_forge():
    assert git_setup.missing_scopes(forge_api.GITHUB, ["repo"]) == ["workflow", "read:org"]
    assert git_setup.missing_scopes(forge_api.GITHUB, ["repo", "workflow", "admin:org"]) == []
    assert git_setup.missing_scopes(forge_api.GITLAB, ["read_api"]) == ["api"]
    assert git_setup.missing_scopes(forge_api.GITHUB, None) is None


def test_machine_report_lists_what_is_left_to_do_and_never_a_secret(repo):
    _git(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")
    _git(repo, "remote", "add", "mirror", "https://user:secret123@example.com/owner/repo.git")
    report = git_setup.machine_report(str(repo), config=Config())
    checks = {check["id"]: check for check in report["checks"]}
    assert report["git"]["available"] and report["git"]["bridge_ok"]
    assert checks["identity_missing"]["fix"] == {"action": "identity"}
    assert checks["helper_missing"]["fix"]["command"].startswith("git config --global credential.helper")
    ssh = checks["remote_ssh_no_key"]
    assert ssh["fix"] == {"action": "use_https", "remote": "origin", "url": "https://github.com/owner/repo.git"}
    assert "secret123" not in json.dumps(report)


def _gitlab_origin(repo, monkeypatch):
    for name in ("GL_TOKEN", "GLAB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(forge_api, "gh_cli_token", lambda host: "")
    forge_api.clear_sign_in_cache()
    _git(repo, "remote", "add", "origin", "https://gitlab.com/team/app.git")


def test_a_remote_signed_in_in_the_project_git_is_not_asked_to_connect(repo, monkeypatch, tmp_path):
    # The reported bug: pushes worked from WSL for months, the panel still said
    # "no token yet" and offered Connect.
    _gitlab_origin(repo, monkeypatch)
    store = tmp_path / "creds"
    store.write_text("https://alice:glpat-project@gitlab.com\n", encoding="utf-8")
    _git(repo, "config", "credential.helper", f"store --file={store}")
    report = git_setup.machine_report(str(repo), config=Config())
    checks = {check["id"]: check for check in report["checks"]}
    assert "remote_no_token" not in checks
    assert checks["remote_ok"]["params"] == {"remote": "origin", "host": "gitlab.com", "auth": "git"}
    assert "glpat-project" not in json.dumps(report)


def test_a_remote_with_no_sign_in_anywhere_still_offers_to_connect(repo, monkeypatch):
    _gitlab_origin(repo, monkeypatch)
    report = git_setup.machine_report(str(repo), config=Config())
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["remote_no_token"]["fix"] == {"action": "connect", "host": "gitlab.com"}


def test_plaintext_store_helper_is_reported(repo):
    subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=True)
    report = git_setup.machine_report(str(repo), config=Config())
    plaintext = [check for check in report["checks"] if check["id"] == "helper_plaintext"]
    assert plaintext and "store" not in plaintext[0]["fix"]["command"].split("helper", 1)[1]
    assert report["credential_helper"]["secure"] is False


def test_connecting_an_account_fills_only_the_missing_identity(repo, monkeypatch):
    subprocess.run(["git", "config", "--global", "user.name", "Me Myself"], check=True)
    info = {
        "ok": True, "host": "github.com", "kind": forge_api.GITHUB, "login": "octo",
        "scopes": ["repo", "workflow", "read:org"], "error": None,
        "profile": {"id": "42", "name": "Octo Cat"},
    }
    monkeypatch.setattr(git_accounts, "account_info", lambda *_a, **_k: dict(info))
    result = git_setup.setup_account("github.com", root=str(repo), config=Config())
    actions = {action["id"]: action for action in result["actions"]}
    assert result["ok"] and result["login"] == "octo"
    assert actions["identity"]["status"] == "configured"
    assert actions["identity"]["changed"] == ["user.email"]
    assert actions["scopes"]["status"] == "ok"
    assert _global("user.name") == "Me Myself"
    assert _global("user.email") == "42+octo@users.noreply.github.com"
    assert any(check["id"] == "identity_ok" for check in result["machine"]["checks"])


def test_failed_account_check_changes_nothing(repo, monkeypatch):
    info = {"ok": False, "host": "github.com", "kind": forge_api.GITHUB, "login": None,
            "scopes": None, "error": "Bad credentials", "profile": {}}
    monkeypatch.setattr(git_accounts, "account_info", lambda *_a, **_k: dict(info))
    result = git_setup.setup_account("github.com", root=str(repo), config=Config())
    assert result["ok"] is False and result["error"] == "Bad credentials"
    assert result["actions"] == []
    assert _global("user.email") is None


def test_identity_is_validated_before_git_config(repo):
    with pytest.raises(git_setup.GitSetupError) as bad:
        git_setup.set_identity("Me", "not-an-email", root=str(repo))
    assert bad.value.code == "bad_email"
    with pytest.raises(git_setup.GitSetupError):
        git_setup.set_identity("--evil", "me@example.com", root=str(repo))
    result = git_setup.set_identity("Me", "me@example.com", root=str(repo))
    assert result["ok"] and result["changed"] == ["user.name", "user.email"]


def test_token_never_goes_to_a_plaintext_or_missing_helper(repo):
    with pytest.raises(git_setup.GitSetupError) as missing:
        git_setup.store_in_helper("github.com", root=str(repo), config=Config())
    assert missing.value.code == "no_helper"
    subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=True)
    with pytest.raises(git_setup.GitSetupError) as plaintext:
        git_setup.store_in_helper("github.com", root=str(repo), config=Config())
    assert plaintext.value.code == "plaintext_helper"


def test_ssh_remote_switches_to_https(repo):
    _git(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")
    result = git_setup.use_https_remote("origin", root=str(repo))
    assert result == {"ok": True, "remote": "origin", "url": "https://github.com/owner/repo.git"}
    assert _git(repo, "remote", "get-url", "origin") == "https://github.com/owner/repo.git"
    with pytest.raises(git_setup.GitSetupError):
        git_setup.use_https_remote("--upload-pack=x", root=str(repo))
