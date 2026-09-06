"""Environment forwarded into sandboxed ``exec`` commands.

The shell tool used to copy only HOME / PATH / LANG plus whatever the
operator listed in ``allowedEnvKeys``. That hid forge tokens (``tea``,
``gh``, ``glab``, Gitea Actions' ``GITEA_SERVER_TOKEN``), the SSH agent,
proxies and CA bundles - so git push and Forgejo only worked after a
manual allowlist edit.

Secrets that are not needed to talk to a git forge or the local toolchain
(OpenAI keys, license tokens) stay out unless the operator opts them in.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping

_NON_ENV_CHAR = re.compile(r"[^A-Z0-9]+")

# Names ``gh`` / ``glab`` / ``tea`` / Gitea Actions / git-credential look at.
FORGE_ENV_NAMES: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GH_ENTERPRISE_HOST",
    "GH_HOST",
    "GH_CONFIG_DIR",
    "GH_PATH",
    "GH_REPO",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GITHUB_API_URL",
    "GITLAB_TOKEN",
    "GL_TOKEN",
    "GLAB_TOKEN",
    "GLAB_HOST",
    "GITLAB_HOST",
    "GITLAB_API_URL",
    "GITLAB_URI",
    "GITLAB_BASE_URL",
    "GITLAB_PERSONAL_ACCESS_TOKEN",
    "FORGEJO_TOKEN",
    "FORGEJO_ACCESS_TOKEN",
    "FORGEJO_URL",
    "FORGEJO_HOST",
    "FORGEJO_INSTANCE",
    "FORGEJO_INSTANCE_URL",
    "FORGEJO_SERVER_URL",
    "GITEA_TOKEN",
    "GITEA_SERVER_TOKEN",
    "GITEA_ACCESS_TOKEN",
    "GITEA_HTTP_TOKEN",
    "GITEA_SERVER_URL",
    "GITEA_URL",
    "GITEA_INSTANCE",
    "GITEA_INSTANCE_URL",
    "TEA_TOKEN",
    "TEA_URL",
    "TEA_LOGIN",
    "BITBUCKET_TOKEN",
    "BITBUCKET_USERNAME",
    "BITBUCKET_APP_PASSWORD",
    "BB_TOKEN",
    "CODEBERG_TOKEN",
    "HUT_TOKEN",
    "SRHT_TOKEN",
    "SOURCEHUT_TOKEN",
    "AZURE_DEVOPS_EXT_PAT",
    "SYSTEM_ACCESSTOKEN",
)

FORGE_ENV_PREFIXES: tuple[str, ...] = ("NAVIN_FORGE_TOKEN_",)

# SSH agent + git identity / TLS. Without SSH_AUTH_SOCK a sandboxed
# ``git push`` over SSH fails even when the key is on disk.
GIT_SSH_ENV_NAMES: tuple[str, ...] = (
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "SSH_ASKPASS",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "GIT_TERMINAL_PROMPT",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CAPATH",
    "GIT_PROXY_COMMAND",
    "GIT_EXEC_PATH",
    "GIT_TEMPLATE_DIR",
    "GNUPGHOME",
    "GPG_TTY",
    "GPG_AGENT_INFO",
    "KRB5CCNAME",
    "KRB5_CONFIG",
)

PROXY_ENV_NAMES: tuple[str, ...] = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
    "all_proxy",
    "ALL_PROXY",
)

CERT_ENV_NAMES: tuple[str, ...] = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)

RUNTIME_ENV_NAMES: tuple[str, ...] = (
    "USER",
    "LOGNAME",
    "SHELL",
    "TZ",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "WSL_DISTRO_NAME",
    "WSLENV",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "CONTAINER_HOST",
    "TMPDIR",
    "USERNAME",
    "USERDOMAIN",
)

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITLAB_HOSTS = frozenset({"gitlab.com", "www.gitlab.com"})


def host_env_var(host: str) -> str:
    slug = _NON_ENV_CHAR.sub("_", (host or "").upper()).strip("_")
    return f"NAVIN_FORGE_TOKEN_{slug}" if slug else ""


def names_for_forge_host(host: str) -> tuple[str, ...]:
    """Standard CLI env names for this forge host."""
    name = (host or "").strip().lower()
    if name in _GITHUB_HOSTS:
        return ("GITHUB_TOKEN", "GH_TOKEN")
    if name in _GITLAB_HOSTS:
        return ("GITLAB_TOKEN", "GL_TOKEN", "GLAB_TOKEN")
    return (
        "FORGEJO_TOKEN",
        "FORGEJO_ACCESS_TOKEN",
        "GITEA_TOKEN",
        "GITEA_SERVER_TOKEN",
        "GITEA_ACCESS_TOKEN",
        "GITEA_HTTP_TOKEN",
        "TEA_TOKEN",
    )


def _copy_named(source: Mapping[str, str], names: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in names:
        value = source.get(name)
        if value is not None and str(value) != "":
            out[name] = str(value)
    return out


def forwarded_host_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Host variables a sandboxed git / forge CLI actually needs."""
    source = os.environ if environ is None else environ
    out = _copy_named(
        source,
        (
            *FORGE_ENV_NAMES,
            *GIT_SSH_ENV_NAMES,
            *PROXY_ENV_NAMES,
            *CERT_ENV_NAMES,
            *RUNTIME_ENV_NAMES,
        ),
    )
    for key, value in source.items():
        if any(key.startswith(prefix) for prefix in FORGE_ENV_PREFIXES):
            if value:
                out[key] = value
    return out


def forge_tokens_from_config(
    tokens: Mapping[str, str] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Export Settings > Git tokens under the names ``tea`` / ``gh`` expect.

    Existing process env wins: an operator who exported ``GITEA_SERVER_TOKEN``
    keeps that value. Empty settings tokens are ignored.
    """
    source = os.environ if environ is None else environ
    out: dict[str, str] = {}
    if not tokens:
        return out
    for host, token in tokens.items():
        value = str(token or "").strip()
        if not value:
            continue
        per_host = host_env_var(str(host))
        names = list(names_for_forge_host(str(host)))
        if per_host:
            names.insert(0, per_host)
        for name in names:
            if source.get(name):
                continue
            out.setdefault(name, value)
    return out


def operator_allowed_env(
    keys: Iterable[str] | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    out: dict[str, str] = {}
    for key in keys or ():
        name = str(key or "").strip()
        if not name:
            continue
        value = source.get(name)
        if value is not None:
            out[name] = value
    return out


def build_exec_env(
    *,
    base: Mapping[str, str],
    allowed_env_keys: Iterable[str] | None = None,
    forge_tokens: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge the curated exec environment.

    Order: base (PATH / HOME) < host forge/git/ssh/proxy < operator
    allowlist < settings tokens that do not already exist in the process.
    """
    source = os.environ if environ is None else environ
    env = dict(base)
    env.update(forwarded_host_env(source))
    env.update(operator_allowed_env(allowed_env_keys, source))
    env.update(forge_tokens_from_config(forge_tokens, environ=source))
    return env


def forge_tokens_from_tools_config(config: Any | None) -> dict[str, str]:
    """Read Settings > Git tokens from ToolsConfig or a full Config."""
    forge = getattr(config, "forge", None)
    if forge is None:
        forge = getattr(getattr(config, "tools", None), "forge", None)
    tokens = getattr(forge, "tokens", None)
    if not isinstance(tokens, dict):
        return {}
    return {str(host): str(token) for host, token in tokens.items() if str(host).strip()}
