"""Per-project board autonomy: consent, git isolation, PR and issue sync flags.

The Tasks panel exposes an "Autonomy" toggle. Turning it on is an explicit,
recorded consent that lets the agent, during a run it was invited into
(Run agent, /board task, /forge, /cruise, /mission, or the /board loop cron):

- chain through ready tasks without asking again per card;
- create one isolated ``navin/task-<id>-<slug>`` branch per claimed task so
  the user's current branch is never touched;
- push that branch and open a pull request when the task reaches ``done``;
- create / close forge issues mirroring board tasks (GitHub, GitLab,
  Forgejo/Gitea);
- when issue fixing is consented, fix those issues end to end (reproduce,
  fix on the task branch, tests green, commit, close the issue, re-sync).

Settings live in ``<project>/.navin/board/settings.json`` so they travel with
the repository, like the rest of the board. The same file holds ``issues_repo``:
the ``owner/name`` this project reads and imports issues from when they do not
live in its own remote (an upstream or public tracker, for instance). A machine-wide kill-switch lives
in the global config (``tools.board_git``): when the operator turns it off,
no project may auto-branch or auto-PR regardless of its own settings.

Everything here is best-effort by design: a project that is not a git repo,
a missing forge token, or a failed push must never block the board itself.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.proc import no_window_kwargs

AUTONOMY_SCHEMA_VERSION = 1

_SETTINGS_NAME = "settings.json"
_MAX_SETTINGS_BYTES = 64 * 1024
_GIT_TIMEOUT_S = 60

# One lock per settings file; the HTTP API and the board tool share a process.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "consented_at": None,
    # Off by default: silently moving the user off their own branch is a
    # surprise they only discover later, and for a small fix the branch plus
    # its pull request cost more than the fix. Opt in per project instead.
    "auto_branch": False,
    "open_pr_on_done": True,
    "sync_github_issues": False,
    "fix_issues": False,
    "autopilot_loop": False,
    "issues_repo": None,
}
_BOOL_FIELDS = (
    "enabled",
    "auto_branch",
    "open_pr_on_done",
    "sync_github_issues",
    "fix_issues",
    "autopilot_loop",
)
_TEXT_FIELDS = ("issues_repo",)

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Web-UI paths a user may paste: everything from there on is not the project.
_URL_TAIL_SEGMENTS = frozenset(
    {
        "issues",
        "issue",
        "pull",
        "pulls",
        "merge_requests",
        "tree",
        "blob",
        "src",
        "commit",
        "commits",
        "branches",
        "tags",
        "releases",
        "wiki",
        "actions",
        "pipelines",
        "settings",
        "milestones",
        "labels",
        "projects",
        "activity",
        "compare",
    }
)


def normalize_issues_repo(value: Any) -> str | None:
    """Canonical repository reference for the issues panel, or None.

    Accepts what a user actually has at hand: the slug, a browser URL, an SSH
    remote. Without this, "which repo" would only ever be the folder's own
    remote, and issues living in another (often public) repository would be
    unreachable from the panel.

    A bare ``owner/name`` is read on the same host as the project's own
    remote, so a Forgejo or GitLab project keeps talking to its own server.
    A reference on another host keeps that host: ``forgejo.example.com/acme/app``.
    GitLab subgroups are preserved.
    """
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^(?:https?|ssh|git)://", "", text)
    text = re.sub(r"^[^@/\s]+@", "", text)
    # scp-like git@host:owner/repo -> host/owner/repo
    text = re.sub(r"^([A-Za-z0-9._-]+):(?![0-9]+/)", r"\1/", text, count=1)
    text = text.strip("/")
    if text.lower().endswith(".git"):
        text = text[: -len(".git")]
    parts = [part for part in text.split("/") if part and part != "-"]
    if not parts:
        return None
    head = parts[0].lower()
    host = head.split(":")[0]
    if "." in host or host == "localhost":
        if head in ("github.com", "www.github.com"):
            # Historical shape: github.com is the implicit default host, and
            # GitHub has no subgroups, so owner/name is the whole path.
            return _repo_slug(parts[1:], max_parts=2)
        slug = _repo_slug(parts[1:])
        return f"{head}/{slug}" if slug else None
    # A bare path is read on the project's own host; GitLab subgroups stay.
    return _repo_slug(parts)


def _repo_slug(parts: list[str], *, max_parts: int | None = None) -> str | None:
    """``owner/name``, or ``group/sub/name`` for a GitLab subgroup path."""
    cleaned: list[str] = []
    for part in parts:
        if part.lower() in _URL_TAIL_SEGMENTS:
            break
        cleaned.append(part)
        if max_parts is not None and len(cleaned) == max_parts:
            break
    if len(cleaned) < 2 or not all(_REPO_PART_RE.match(part) for part in cleaned):
        return None
    return "/".join(cleaned)


def autonomy_settings_path(project_path: Path | str) -> Path:
    return Path(project_path).expanduser() / ".navin" / "board" / _SETTINGS_NAME


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize(raw: Any) -> dict[str, Any]:
    data = raw.get("autonomy") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        data = {}
    settings: dict[str, Any] = {}
    for field in _BOOL_FIELDS:
        value = data.get(field, _DEFAULTS[field])
        settings[field] = bool(value) if isinstance(value, bool) else _DEFAULTS[field]
    consented = data.get("consented_at")
    settings["consented_at"] = consented if isinstance(consented, str) else None
    settings["issues_repo"] = normalize_issues_repo(data.get("issues_repo"))
    return settings


def read_autonomy(project_path: Path | str) -> dict[str, Any]:
    """Normalized autonomy settings for one project (defaults when absent)."""
    path = autonomy_settings_path(project_path)
    try:
        if not path.is_file() or path.stat().st_size > _MAX_SETTINGS_BYTES:
            return dict(_DEFAULTS)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("autonomy settings read failed {}: {}", path, e)
        return dict(_DEFAULTS)
    return _normalize(raw)


def write_autonomy(
    project_path: Path | str,
    fields: dict[str, Any],
    *,
    actor: str = "user",
) -> dict[str, Any]:
    """Merge *fields* into the project autonomy settings (atomic write).

    Enabling autonomy records ``consented_at``: the toggle is a consent, and
    the timestamp is what proves the user granted it.
    """
    path = autonomy_settings_path(project_path)
    unknown = set(fields) - set(_BOOL_FIELDS) - set(_TEXT_FIELDS)
    if unknown:
        raise ValueError(f"unknown autonomy fields: {', '.join(sorted(unknown))}")
    with _lock_for(path):
        current = read_autonomy(project_path)
        for field in _BOOL_FIELDS:
            if field in fields:
                current[field] = bool(fields[field])
        if "issues_repo" in fields:
            raw = fields["issues_repo"]
            slug = normalize_issues_repo(raw)
            if slug is None and str(raw or "").strip():
                raise ValueError(
                    "issues repository must look like owner/name "
                    "(or a github.com URL pointing at one)"
                )
            current["issues_repo"] = slug
        if current["enabled"] and not current.get("consented_at"):
            current["consented_at"] = _now_iso()
        if not current["enabled"]:
            # Consent is tied to the enabled state; a fresh enable re-records it.
            current["consented_at"] = None
        payload = {
            "schema_version": AUTONOMY_SCHEMA_VERSION,
            "autonomy": current,
            "updated_at": _now_iso(),
            "updated_by": (actor or "user").strip()[:80] or "user",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "wb") as f:
            f.write(encoded)
            f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    return current


# ---------------------------------------------------------------------------
# Effective permissions (project consent AND global kill-switches)
# ---------------------------------------------------------------------------


def _board_git_config() -> Any:
    try:
        from navin.config.loader import load_config

        return load_config().tools.board_git
    except Exception:
        return None


def autonomy_state(project_path: Path | str) -> dict[str, Any]:
    """Project settings plus the effective, kill-switch-aware permissions."""
    settings = read_autonomy(project_path)
    cfg = _board_git_config()
    auto_branch_globally = bool(getattr(cfg, "auto_branch_enabled", True))
    open_pr_globally = bool(getattr(cfg, "open_pr_enabled", True))
    enabled = settings["enabled"]
    return {
        **settings,
        "effective": {
            "auto_branch": enabled and settings["auto_branch"] and auto_branch_globally,
            "open_pr_on_done": enabled and settings["open_pr_on_done"] and open_pr_globally,
            "sync_github_issues": enabled and settings["sync_github_issues"],
            "fix_issues": enabled and settings["fix_issues"],
            "chain_ready_tasks": enabled,
        },
        "global": {
            "auto_branch_enabled": auto_branch_globally,
            "open_pr_enabled": open_pr_globally,
        },
    }


def should_auto_branch(project_path: Path | str) -> bool:
    return bool(autonomy_state(project_path)["effective"]["auto_branch"])


def should_open_pr(project_path: Path | str) -> bool:
    return bool(autonomy_state(project_path)["effective"]["open_pr_on_done"])


def should_sync_issues(project_path: Path | str) -> bool:
    return bool(autonomy_state(project_path)["effective"]["sync_github_issues"])


def should_fix_issues(project_path: Path | str) -> bool:
    return bool(autonomy_state(project_path)["effective"]["fix_issues"])


def issues_repo(project_path: Path | str) -> str | None:
    """The repository this project reads issues from, when it is not its own."""
    return read_autonomy(project_path)["issues_repo"]


# ---------------------------------------------------------------------------
# Git isolation: one branch per claimed task
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _slugify(text: str, *, max_len: int = 32) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-")


def task_branch_name(task: dict[str, Any]) -> str:
    slug = _slugify(str(task.get("title") or ""))
    base = f"navin/task-{task.get('id')}"
    return f"{base}-{slug}" if slug else base


def start_task_branch(project_path: Path | str, task: dict[str, Any]) -> dict[str, Any]:
    """Create and switch to the task's isolated branch (idempotent, best-effort).

    Returns ``{"ok": bool, "branch": str | None, "base": str | None,
    "created": bool, "detail": str}``. ``base`` is the branch the repo was on
    beforehand, so callers can tell the user where their work went instead of
    leaving them to discover the move on their next commit. A dirty but
    unconflicted working tree is preserved by ``git switch -c``. When the repo
    is already on the task's branch (a resumed run), this is a no-op success.
    """
    root = Path(project_path).expanduser().resolve(strict=False)
    code, inside, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside.strip() != "true":
        return {
            "ok": False,
            "branch": None,
            "base": None,
            "created": False,
            "detail": "not a git repository",
        }

    _, base_raw, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    base = base_raw.strip() or None

    for lock_name, label in (
        (".git/MERGE_HEAD", "merge in progress"),
        (".git/rebase-merge", "rebase in progress"),
        (".git/rebase-apply", "rebase in progress"),
        (".git/CHERRY_PICK_HEAD", "cherry-pick in progress"),
    ):
        if (root / lock_name).exists():
            return {
                "ok": False,
                "branch": None,
                "base": base,
                "created": False,
                "detail": f"cannot branch: {label}",
            }

    branch = task_branch_name(task)
    if base == branch:
        return {
            "ok": True,
            "branch": branch,
            "base": base,
            "created": False,
            "detail": "already on task branch",
        }

    # Resumed task whose branch already exists: switch back onto it.
    code, _, _ = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if code == 0:
        code, out, err = _git(root, "switch", branch)
        if code != 0:
            return {
                "ok": False,
                "branch": branch,
                "base": base,
                "created": False,
                "detail": (err or out or "git switch failed").strip()[:300],
            }
        return {
            "ok": True,
            "branch": branch,
            "base": base,
            "created": False,
            "detail": "switched to existing task branch",
        }

    code, out, err = _git(root, "switch", "-c", branch)
    if code != 0:
        code, out, err = _git(root, "checkout", "-b", branch)
    if code != 0:
        return {
            "ok": False,
            "branch": branch,
            "base": base,
            "created": False,
            "detail": (err or out or "git switch -c failed").strip()[:300],
        }
    return {
        "ok": True,
        "branch": branch,
        "base": base,
        "created": True,
        "detail": "created isolated task branch",
    }


def current_branch(project_path: Path | str) -> str | None:
    root = Path(project_path).expanduser().resolve(strict=False)
    code, out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return None
    return out.strip() or None


def runtime_lines(project_path: Path | str) -> list[str]:
    """Autonomy digest for the agent's runtime context (empty when off)."""
    state = autonomy_state(project_path)
    if not state["enabled"]:
        return []
    effective = state["effective"]
    lines = [
        "Board autonomy is ENABLED for this project (user consent recorded "
        f"{state.get('consented_at') or 'previously'}). During a run you were "
        "invited into, chain through ready board tasks without asking again "
        "per task; stop and notify on blocked."
    ]
    if effective["auto_branch"]:
        lines.append(
            "Work each claimed task on its isolated navin/task-<id> branch "
            "(created automatically at claim; never commit to the user's "
            "starting branch). Say which branch you moved to, and which one "
            "you left, the first time you mention the task."
        )
    else:
        lines.append(
            "Auto-branch is OFF: work claimed tasks on the branch the user is "
            "already on, and never create or switch branches without asking."
        )
    if effective["open_pr_on_done"]:
        lines.append(
            "When a task reaches done: commit, push its branch and let the "
            "board open the pull request (merge request on GitLab) through "
            "the forge API, then record its URL on the task."
        )
    if effective["sync_github_issues"]:
        lines.append(
            "Keep the forge issues in sync with board tasks "
            "(board action=sync_github, works on GitHub, GitLab and Forgejo)."
        )
    repo = state.get("issues_repo")
    if repo and (effective["sync_github_issues"] or effective["fix_issues"]):
        lines.append(
            f"This project tracks its issues in {repo}, not in its own remote: "
            f"always target the repo {repo} (the board already does). Code "
            "fixes still belong to the local checkout."
        )
    if effective["fix_issues"]:
        lines.append(
            "You may fix forge issues end to end: claim (or import) the "
            "matching board task, reproduce the problem, implement the fix on "
            "the isolated task branch, run the relevant test suite until it is "
            "fully green, commit, then let the board close the issue with a "
            "comment referencing the fix and re-sync (action=sync_github). "
            "Never close an issue whose fix is not tested and committed."
        )
    lines.append(
        "Destructive git operations (force-push, hard reset, deletes) still "
        "require explicit user approval."
    )
    return lines
