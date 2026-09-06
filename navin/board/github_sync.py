"""Forge side of board autonomy: pull requests and issue sync.

The road is the forge REST API (:mod:`navin.webui.forge_api`), which covers
GitHub, GitLab and Forgejo/Gitea with a token and no external binary. ``gh``
remains a fallback on github.com for setups that already rely on it.

Everything is best-effort: the board must keep working on machines without a
token, without ``gh``, without a remote, or without network. Failures come
back as ``{"ok": False, "detail": ...}`` so the caller can record them as a
task comment instead of raising.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from navin.board.autonomy import _git, normalize_issues_repo, read_autonomy, task_branch_name
from navin.host.packages import linux_package_flavor
from navin.utils.host import HostPlatform, host_platform
from navin.utils.proc import no_window_kwargs

_GH_TIMEOUT_S = 120
_IMPORT_LIMIT = 50
_MAX_BODY_LEN = 4000


def gh_available() -> bool:
    return shutil.which("gh") is not None


_GH_DOCS_URL = "https://cli.github.com"


def gh_install_guide(*, platform: HostPlatform | None = None) -> dict[str, Any]:
    """Host-specific install steps for the Issues panel when ``gh`` is missing.

    Detects Windows, macOS, WSL, and Linux (deb / rpm / Arch / Omarchy) so the
    UI can show one command that actually works on this machine, plus the others.
    """
    plat = platform or host_platform()
    flavor = linux_package_flavor() if plat in ("linux", "wsl") else plat
    commands = {
        "windows": "winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements",
        "macos": "brew install gh",
        "deb": "sudo apt-get update && sudo apt-get install -y gh",
        "rpm": "sudo dnf install -y gh",
        "arch": "sudo pacman -S --needed --noconfirm github-cli",
        "omarchy": "sudo pacman -S --needed --noconfirm github-cli",
        "other": "sudo apt-get install -y gh",
    }
    labels = {
        "windows": "Windows (winget)",
        "macos": "macOS (Homebrew)",
        "deb": "Linux Debian / Ubuntu (.deb)",
        "rpm": "Linux Fedora / RHEL (.rpm)",
        "arch": "Linux Arch (pacman)",
        "omarchy": "Linux Omarchy (pacman)",
        "other": "Linux (official installer)",
        "wsl": "WSL (Linux guest)",
    }
    primary_key = {
        "windows": "windows",
        "macos": "macos",
        "wsl": flavor if flavor in ("deb", "rpm", "arch", "omarchy") else "deb",
        "linux": flavor if flavor in ("deb", "rpm", "arch", "omarchy") else "other",
    }[plat]
    alternatives: list[dict[str, str]] = []
    for key in ("windows", "macos", "deb", "rpm", "arch"):
        if key == primary_key:
            continue
        alternatives.append({"id": key, "label": labels[key], "command": commands[key]})
    return {
        "needed": True,
        "platform": plat,
        "flavor": primary_key,
        "label": labels["wsl"] if plat == "wsl" else labels[primary_key],
        "command": commands[primary_key],
        "auth_command": "gh auth login",
        "docs_url": _GH_DOCS_URL,
        "alternatives": alternatives,
    }


def _gh(root: Path, *args: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            ["gh", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GH_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _first_url(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("https://"):
            return line
    return None


def _task_body(task: dict[str, Any]) -> str:
    parts = [str(task.get("description") or "").strip()]
    if task.get("acceptance"):
        parts.append(f"## Acceptance\n\n{task['acceptance']}")
    if task.get("evidence"):
        parts.append(f"## Evidence\n\n{task['evidence']}")
    parts.append(f"Board task: `{task.get('id')}` (managed by Navin)")
    return "\n\n".join(p for p in parts if p)[:_MAX_BODY_LEN]


# ---------------------------------------------------------------------------
# Pull request on done
# ---------------------------------------------------------------------------


def _forge_api() -> Any | None:
    try:
        from navin.webui import forge_api
    except ImportError:  # pragma: no cover - webui extras absent
        return None
    return forge_api


def _config() -> Any | None:
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - a broken config must not block the board
        return None


def gh_repo_arg(slug: str | None) -> str | None:
    """The ``--repo`` value for ``gh``, or None when the target is elsewhere.

    ``gh`` only speaks to github.com, so a tracker stored as
    ``forgejo.example.com/acme/widgets`` must never be handed to it.
    """
    text = str(slug or "").strip()
    if not text:
        return None
    if text.lower().startswith("github.com/"):
        return text[len("github.com/") :]
    head = text.split("/")[0].split(":")[0]
    if "." in head or head == "localhost":
        return None
    return text


class _IssueTarget:
    """Where the issues of one project live, and how to talk to that forge."""

    def __init__(self, api: Any | None, remote: Any, token: str, slug: str | None) -> None:
        self.api = api
        self.remote = remote
        self.token = token
        self.slug = slug

    @property
    def kind(self) -> str:
        if self.api is None or self.remote is None:
            return "unknown"
        return str(self.remote.kind)

    @property
    def host(self) -> str:
        return str(self.remote.host) if self.remote is not None else ""

    @property
    def known(self) -> bool:
        return self.api is not None and self.remote is not None and self.kind != "unknown"

    @property
    def usable(self) -> bool:
        return self.known and bool(self.token)

    @property
    def gh_possible(self) -> bool:
        """``gh`` can still serve this target: github.com, and it is installed."""
        if not gh_available():
            return False
        if self.slug and gh_repo_arg(self.slug) is None:
            return False
        return self.remote is None or self.kind in ("github", "unknown")

    def setup_detail(self) -> str:
        """One actionable sentence when nothing can talk to the forge."""
        if self.api is not None and self.remote is not None and self.kind == "unknown":
            return (
                f"Could not tell what {self.host} runs. Set its type "
                f"(github / gitlab / forgejo) in Settings > Git."
            )
        if self.api is not None and self.remote is not None:
            return self.api.token_missing_error(self.remote).message
        return (
            "no forge remote for this project - add a GitHub, GitLab or "
            "Forgejo remote, or point this panel at a repository in Settings > Git"
        )

    def payload_fields(self) -> dict[str, Any]:
        """Forge identity for the UI (labels differ: issue vs ticket, PR vs MR)."""
        if self.api is None or self.remote is None:
            return {"forge": "unknown", "host": "", "remote_repo": None}
        return {
            "forge": self.kind,
            "forge_label": self.api.FORGE_LABELS.get(self.kind, self.kind),
            "host": self.host,
            "remote_repo": self.remote.slug,
        }


def issue_target(project_path: Path | str, repo: str | None = None) -> _IssueTarget:
    """Resolve which forge repository this project's issues live in."""
    root = Path(project_path).expanduser().resolve(strict=False)
    api = _forge_api()
    slug = normalize_issues_repo(repo) if repo else configured_issues_repo(root)
    if api is None:
        return _IssueTarget(None, None, "", slug)
    config = _config()
    try:
        origin = api.detect_forge(root, config=config)
    except Exception:  # noqa: BLE001 - detection is best-effort
        origin = None
    if origin is None and slug and gh_repo_arg(slug) and gh_available():
        # A bare owner/name with no local remote is what `gh` already handled
        # as github.com; keep that road while the CLI is there.
        return _IssueTarget(api, None, "", slug)
    try:
        remote = api.parse_repo_ref(slug, origin=origin) if slug else origin
        if remote is not None:
            remote = api.resolve_kind(remote, config=config)
    except Exception:  # noqa: BLE001
        remote = origin
    if remote is None:
        return _IssueTarget(api, None, "", slug)
    token, _source = api.resolve_token(remote, config=config)
    return _IssueTarget(api, remote, token, slug)


def _open_pr_via_forge(
    root: Path,
    task: dict[str, Any],
    branch: str,
    head_sha: str | None,
) -> dict[str, Any] | None:
    """Open the task PR through the forge REST API.

    Returns ``None`` when this road is not available (no recognizable remote
    or no token), so the caller can still try ``gh``.
    """
    forge_api = _forge_api()
    if forge_api is None:
        return None
    config = _config()

    try:
        remote = forge_api.detect_forge(root, config=config)
    except Exception:  # noqa: BLE001 - detection is best-effort
        return None
    if remote is None or remote.kind == forge_api.UNKNOWN:
        return None
    token, _source = forge_api.resolve_token(remote, config=config)
    if not token:
        return None

    label = forge_api.REQUEST_LABELS.get(remote.kind, "pull request")
    try:
        existing = forge_api.find_open_request(remote, token, branch)
        if existing and existing.get("url"):
            return {
                "ok": True,
                "pr_url": existing["url"],
                "head_sha": head_sha,
                "detail": f"existing {label} reused",
                "request_label": label,
                "forge": remote.kind,
            }
        base = forge_api.default_branch(remote, token)
        if base == branch:
            return {
                "ok": False,
                "pr_url": None,
                "head_sha": head_sha,
                "detail": f"{branch} is the default branch on {remote.host}",
            }
        created = forge_api.create_request(
            remote,
            token,
            head=branch,
            base=base,
            title=str(task.get("title") or f"Task {task.get('id')}"),
            body=_task_body(task),
            draft=False,
        )
    except forge_api.ForgeError as exc:
        return {
            "ok": False,
            "pr_url": None,
            "head_sha": head_sha,
            "detail": exc.message[:300],
            "request_label": label,
            "forge": remote.kind,
        }
    return {
        "ok": True,
        "pr_url": created.get("url"),
        "head_sha": head_sha,
        "detail": f"{label} opened",
        "request_label": label,
        "forge": remote.kind,
    }


def open_task_pr(project_path: Path | str, task: dict[str, Any]) -> dict[str, Any]:
    """Push the task branch and open a PR. Returns ok/pr_url/detail.

    Commits leftover changes first (on the task branch only), so a task the
    agent finished but forgot to commit still ships as a reviewable PR.
    """
    root = Path(project_path).expanduser().resolve(strict=False)

    code, inside, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside.strip() != "true":
        return {"ok": False, "pr_url": None, "head_sha": None, "detail": "not a git repository"}

    _, head_sha_raw, _ = _git(root, "rev-parse", "--short=12", "HEAD")
    head_sha = head_sha_raw.strip() or None

    branch = str(task.get("branch") or "").strip() or task_branch_name(task)
    _, current, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if current.strip() != branch:
        code, _, _ = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if code != 0:
            return {
                "ok": False,
                "pr_url": None,
                "head_sha": head_sha,
                "detail": f"task branch {branch} does not exist",
            }
        code, out, err = _git(root, "switch", branch)
        if code != 0:
            return {
                "ok": False,
                "pr_url": None,
                "head_sha": head_sha,
                "detail": (err or out or "git switch failed").strip()[:300],
            }

    # Safety: only auto-commit on the isolated task branch, never elsewhere.
    _, dirty, _ = _git(root, "status", "--porcelain")
    if dirty.strip() and branch.startswith("navin/task-"):
        _git(root, "add", "-A")
        code, out, err = _git(
            root, "commit", "-m", f"task {task.get('id')}: {task.get('title')}"
        )
        if code != 0:
            return {
                "ok": False,
                "pr_url": None,
                "head_sha": head_sha,
                "detail": (err or out or "git commit failed").strip()[:300],
            }
        _, head_sha_raw, _ = _git(root, "rev-parse", "--short=12", "HEAD")
        head_sha = head_sha_raw.strip() or head_sha

    code, out, err = _git(root, "push", "-u", "origin", branch)
    if code != 0:
        return {
            "ok": False,
            "pr_url": None,
            "head_sha": head_sha,
            "detail": (err or out or "git push failed").strip()[:300],
        }

    # GitLab and Forgejo have no `gh`, and a GitHub user without it should not
    # lose auto-PR either: the REST road covers all three with a token.
    forge_result = _open_pr_via_forge(root, task, branch, head_sha)
    if forge_result is not None:
        return forge_result

    if not gh_available():
        return {
            "ok": False,
            "pr_url": None,
            "head_sha": head_sha,
            "detail": (
                "no forge token for this remote and gh CLI not installed - "
                "add a token in Settings > Git"
            ),
        }

    # An existing PR for this branch is reused, not duplicated.
    code, out, _ = _gh(
        root, "pr", "view", branch, "--json", "url", "--jq", ".url"
    )
    if code == 0 and out.strip().startswith("https://"):
        return {
            "ok": True,
            "pr_url": out.strip(),
            "head_sha": head_sha,
            "detail": "existing PR reused",
        }

    code, out, err = _gh(
        root,
        "pr",
        "create",
        "--head",
        branch,
        "--title",
        str(task.get("title") or f"Task {task.get('id')}")[:200],
        "--body",
        _task_body(task),
    )
    if code != 0:
        return {
            "ok": False,
            "pr_url": None,
            "head_sha": head_sha,
            "detail": (err or out or "gh pr create failed").strip()[:300],
        }
    url = _first_url(out) or _first_url(err)
    return {
        "ok": bool(url),
        "pr_url": url,
        "head_sha": head_sha,
        "detail": "PR opened" if url else "no PR URL returned",
    }


# ---------------------------------------------------------------------------
# Issue sync
# ---------------------------------------------------------------------------


def push_task_to_issue(
    project_path: Path | str,
    task: dict[str, Any],
    *,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create an issue mirroring one board task, on GitHub, GitLab or Forgejo.

    The issue is filed where the project reads its issues from: filing it in the
    local remote while the panel lists another tracker would split the board's
    mirror across two repositories.
    """
    root = Path(project_path).expanduser().resolve(strict=False)
    target = issue_target(root, repo)
    slug = target.slug
    if target.usable:
        try:
            issue = target.api.create_issue(
                target.remote,
                target.token,
                title=str(task.get("title") or f"Task {task.get('id')}"),
                body=_task_body(task),
                labels=[str(label) for label in (task.get("labels") or [])[:5]],
            )
        except target.api.ForgeError as exc:
            return {
                "ok": False,
                "issue_url": None,
                "repo": slug,
                "detail": exc.message[:300],
                **target.payload_fields(),
            }
        return {
            "ok": True,
            "issue_url": issue.get("url"),
            "repo": slug,
            "detail": "issue created",
            **target.payload_fields(),
        }
    if not target.gh_possible:
        return {
            "ok": False,
            "issue_url": None,
            "repo": slug,
            "detail": target.setup_detail()[:300],
            **target.payload_fields(),
        }
    slug = gh_repo_arg(slug)
    base_args = [
        "issue",
        "create",
        "--title",
        str(task.get("title") or f"Task {task.get('id')}")[:200],
        "--body",
        _task_body(task),
        *_issue_repo_args(slug),
    ]
    label_args: list[str] = []
    for label in (task.get("labels") or [])[:5]:
        label_args.extend(["--label", str(label)])
    code, out, err = _gh(root, *base_args, *label_args)
    if code != 0 and label_args:
        # Labels that do not exist on the repo make gh fail; retry without.
        code, out, err = _gh(root, *base_args)
    if code != 0:
        return {
            "ok": False,
            "issue_url": None,
            "repo": slug,
            "detail": (err or out or "gh issue create failed").strip()[:300],
        }
    url = _first_url(out) or _first_url(err)
    return {
        "ok": bool(url),
        "issue_url": url,
        "repo": slug,
        "detail": "issue created" if url else "no issue URL returned",
    }


def _close_comment(task: dict[str, Any]) -> str:
    """What actually fixed the issue, so the closing comment is not just noise."""
    parts = ["Fixed and closed from the Navin board."]
    pr_url = str(task.get("pr_url") or "").strip()
    head_sha = str(task.get("head_sha") or "").strip()
    if pr_url:
        # The URL shape says it: GitLab uses /merge_requests/, the others /pull(s)/.
        label = "Merge request" if "/merge_requests/" in pr_url else "Pull request"
        parts.append(f"{label}: {pr_url}")
    elif head_sha:
        parts.append(f"Commit: {head_sha}")
    evidence = str(task.get("evidence") or "").strip()
    if evidence:
        parts.append(f"Evidence: {evidence[:500]}")
    return "\n\n".join(parts)


def close_task_issue(project_path: Path | str, task: dict[str, Any]) -> dict[str, Any]:
    """Close the issue linked to a task (when it exists), on any forge.

    The issue is addressed by its URL, so this closes issues living in another
    repository (a configured tracker, an upstream) just as well as local ones,
    and on another forge than the project's own remote.
    """
    url = str(task.get("issue_url") or "").strip()
    if not url:
        return {"ok": False, "detail": "task has no linked issue"}
    root = Path(project_path).expanduser().resolve(strict=False)

    api = _forge_api()
    ref = None
    if api is not None:
        try:
            ref = api.resolve_ref(url, config=_config())
        except Exception:  # noqa: BLE001 - resolution is best-effort
            ref = None
    if ref is not None and ref.remote.kind != api.UNKNOWN:
        token, _source = api.resolve_token(ref.remote, config=_config())
        if token:
            try:
                api.close_issue(
                    ref.remote, token, ref.number, comment=_close_comment(task)
                )
            except api.ForgeError as exc:
                return {"ok": False, "detail": exc.message[:300]}
            return {"ok": True, "detail": "issue closed"}
        if ref.remote.kind != "github" or not gh_available():
            return {"ok": False, "detail": api.token_missing_error(ref.remote).message[:300]}

    if not gh_available():
        return {
            "ok": False,
            "detail": (
                "no forge token for this issue and gh CLI not installed - "
                "add a token in Settings > Git"
            ),
        }
    code, out, err = _gh(root, "issue", "close", url, "--comment", _close_comment(task))
    if code != 0:
        return {"ok": False, "detail": (err or out or "gh issue close failed").strip()[:300]}
    return {"ok": True, "detail": "issue closed"}


def configured_issues_repo(project_path: Path | str) -> str | None:
    """The ``owner/name`` this project reads issues from, or None for its remote."""
    try:
        return read_autonomy(project_path)["issues_repo"]
    except Exception:
        return None


def _issue_repo_args(slug: str | None) -> list[str]:
    return ["--repo", slug] if slug else []


def _github_remote_hint(root: Path) -> str | None:
    """Return a short reason when ``gh`` cannot resolve a GitHub remote here."""
    code, inside, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside.strip() != "true":
        return "not a git repository - pick a cloned GitHub project"
    code, remotes, _ = _git(root, "remote", "-v")
    if code != 0 or not remotes.strip():
        return (
            "no git remotes found - open a repository with a GitHub remote "
            "(not a parent folder like NavinProjects)"
        )
    if "github.com" not in remotes.lower():
        return "no GitHub remote found - add an origin pointing at github.com"
    return None


def list_github_issues(
    project_path: Path | str,
    *,
    state: str = "open",
    limit: int = _IMPORT_LIMIT,
    repo: str | None = None,
) -> dict[str, Any]:
    """Open (or all) issues for the Issues panel, on GitHub, GitLab or Forgejo.

    Reads the project's configured ``issues_repo`` when *repo* is not given, and
    falls back to the folder's own GitHub remote when neither is set - so a
    project whose tracker lives elsewhere (upstream, a public repo) is not
    limited to the issues of the code it happens to have checked out.
    """
    root = Path(project_path).expanduser().resolve(strict=False)
    if state not in ("open", "closed", "all"):
        state = "open"
    target = issue_target(root, repo)
    slug = target.slug
    if target.usable:
        try:
            issues = target.api.list_issues(
                target.remote,
                target.token,
                state=state,
                limit=max(1, min(int(limit), 200)),
            )
        except target.api.ForgeError as exc:
            return {
                "ok": False,
                "issues": [],
                "repo": slug,
                "detail": exc.message[:300],
                **target.payload_fields(),
            }
        return {
            "ok": True,
            "issues": issues,
            "repo": slug,
            "detail": f"{len(issues)} issue(s)",
            **target.payload_fields(),
        }
    if not target.gh_possible:
        payload = {
            "ok": False,
            "issues": [],
            "repo": slug,
            "detail": target.setup_detail(),
            **target.payload_fields(),
        }
        if target.known:
            payload["token_setup"] = {
                "host": target.host,
                "forge": target.kind,
                "env": target.api.env_var_names(target.kind, target.host),
            }
        if target.kind in ("github", "unknown") and not gh_available():
            # On github.com the CLI stays a valid answer, next to the token.
            payload["install"] = gh_install_guide()
        return payload
    slug = gh_repo_arg(slug)
    if not slug:
        remote_hint = _github_remote_hint(root)
        if remote_hint:
            return {"ok": False, "issues": [], "repo": None, "detail": remote_hint}
    code, out, err = _gh(
        root,
        "issue",
        "list",
        "--state",
        state,
        "--limit",
        str(max(1, min(int(limit), 200))),
        "--json",
        "number,title,body,url,state,labels,author,assignees,createdAt,updatedAt,comments",
        *_issue_repo_args(slug),
    )
    if code != 0:
        detail = (err or out or "gh issue list failed").strip()
        if not slug and "no git remotes found" in detail.lower():
            detail = (
                "no git remotes found - open a repository with a GitHub remote "
                "(not a parent folder like NavinProjects)"
            )
        return {
            "ok": False,
            "issues": [],
            "repo": slug,
            "detail": detail[:300],
        }
    try:
        raw = json.loads(out or "[]")
    except json.JSONDecodeError:
        return {"ok": False, "issues": [], "repo": slug, "detail": "gh returned invalid JSON"}
    issues: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "number": item.get("number"),
                "title": str(item.get("title") or ""),
                "body": str(item.get("body") or "")[:2000],
                "url": str(item.get("url") or ""),
                "state": str(item.get("state") or "").lower(),
                "labels": [
                    str(label.get("name"))
                    for label in (item.get("labels") or [])
                    if isinstance(label, dict) and label.get("name")
                ],
                "author": (item.get("author") or {}).get("login")
                if isinstance(item.get("author"), dict)
                else None,
                "assignees": [
                    str(a.get("login"))
                    for a in (item.get("assignees") or [])
                    if isinstance(a, dict) and a.get("login")
                ],
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt"),
                "comments": len(item.get("comments") or [])
                if isinstance(item.get("comments"), list)
                else 0,
            }
        )
    return {"ok": True, "issues": issues, "repo": slug, "detail": f"{len(issues)} issue(s)"}


def import_issues_to_board(
    project_path: Path | str,
    store: Any,
    *,
    actor: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create board tasks for open issues not on the board yet, on any forge.

    The issues may come from another repository (*repo*, or the project's
    configured ``issues_repo``); the tasks always land on this project's board.
    """
    root = Path(project_path).expanduser().resolve(strict=False)
    target = issue_target(root, repo)
    slug = target.slug
    forge_label = "github"
    issues: list[Any] = []
    if target.usable:
        forge_label = target.kind
        try:
            issues = target.api.list_issues(
                target.remote, target.token, state="open", limit=_IMPORT_LIMIT
            )
        except target.api.ForgeError as exc:
            return {
                "ok": False,
                "imported": 0,
                "repo": slug,
                "detail": exc.message[:300],
                **target.payload_fields(),
            }
    elif not target.gh_possible:
        return {
            "ok": False,
            "imported": 0,
            "repo": slug,
            "detail": target.setup_detail()[:300],
            **target.payload_fields(),
        }
    else:
        slug = gh_repo_arg(slug)
        code, out, err = _gh(
            root,
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(_IMPORT_LIMIT),
            "--json",
            "title,body,url,labels",
            *_issue_repo_args(slug),
        )
        if code != 0:
            return {
                "ok": False,
                "imported": 0,
                "repo": slug,
                "detail": (err or out or "gh issue list failed").strip()[:300],
            }
        try:
            issues = json.loads(out or "[]")
        except json.JSONDecodeError:
            return {
                "ok": False,
                "imported": 0,
                "repo": slug,
                "detail": "gh returned invalid JSON",
            }
        if not isinstance(issues, list):
            return {"ok": False, "imported": 0, "repo": slug, "detail": "unexpected gh payload"}

    known_urls = {
        str(t.get("issue_url") or "").strip()
        for t in store.read_tasks()
        if t.get("issue_url")
    }
    imported = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        url = str(issue.get("url") or "").strip()
        title = str(issue.get("title") or "").strip()
        if not url or not title or url in known_urls:
            continue
        # gh returns [{"name": ...}]; the REST path already normalized to names.
        labels = [
            str(item.get("name")) if isinstance(item, dict) else str(item)
            for item in (issue.get("labels") or [])
            if (item.get("name") if isinstance(item, dict) else str(item).strip())
        ]
        try:
            task = store.create_task(
                title=title,
                description=str(issue.get("body") or "")[:_MAX_BODY_LEN],
                labels=[forge_label, *labels][:10],
                actor=actor,
                actor_type="agent",
            )
            store.update_task(
                task["id"],
                fields={"issue_url": url},
                actor=actor,
                actor_type="agent",
            )
            imported += 1
        except Exception as exc:  # board full, validation - keep importing others
            logger.warning("issue import skipped {}: {}", url, exc)
    origin = f" from {slug}" if slug else ""
    return {
        "ok": True,
        "imported": imported,
        "repo": slug,
        "detail": f"{imported} issue(s) imported{origin}",
    }
