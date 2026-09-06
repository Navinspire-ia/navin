"""Session HTTP helpers for PR/MR draft + CI checks, on any git forge.

This used to be a thin wrapper around the ``gh`` CLI, so a machine without
it answered "gh CLI not installed" and a GitLab or Forgejo remote had no
path at all. The default road is now the forge REST API
(:mod:`navin.webui.forge_api`), which needs no external binary and covers
GitHub, GitLab and Forgejo/Gitea. ``gh`` stays a supported *token source*
and a last-resort fallback on github.com so existing setups keep working.

The public function names keep their ``github_`` prefix (the routes, the
board and the WebUI all import them); ``forge_*`` aliases exist for new code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from navin.board.github_sync import _first_url, _gh, gh_available
from navin.security.workspace_access import WorkspaceScope
from navin.webui import forge_api
from navin.webui.forge_api import (
    FORGE_LABELS,
    GITHUB,
    REQUEST_LABELS,
    UNKNOWN,
    ForgeError,
    ForgeRemote,
)
from navin.webui.project_search import ProjectSearchError, _branch_state, _root, _run_git


class GithubPrError(ProjectSearchError):
    """Alias so handlers can catch PR-specific failures with the same shape."""


_BRANCH_SAFE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _head_sha_short(root) -> str | None:
    """Short HEAD sha for board continuity (same shape as board github_sync)."""
    result = _run_git(root, "rev-parse", "--short=12", "HEAD")
    if result is None or result.returncode != 0:
        return None
    sha = (result.stdout or "").strip()
    return sha or None


def _load_config() -> Any | None:
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - a broken config must not break the panel
        return None


class ForgeContext:
    """Everything one panel action needs to know about the remote."""

    def __init__(
        self,
        remote: ForgeRemote | None,
        token: str,
        token_source: str,
    ) -> None:
        self.remote = remote
        self.token = token
        self.token_source = token_source

    @property
    def kind(self) -> str:
        return self.remote.kind if self.remote else UNKNOWN

    @property
    def host(self) -> str:
        return self.remote.host if self.remote else ""

    @property
    def slug(self) -> str:
        return self.remote.slug if self.remote else ""

    @property
    def usable(self) -> bool:
        """A REST call can be made right now."""
        return bool(self.remote and self.remote.kind != UNKNOWN and self.token)

    def base_payload(self) -> dict[str, Any]:
        return {
            "forge": self.kind,
            "forge_label": FORGE_LABELS.get(self.kind, self.kind),
            "request_label": REQUEST_LABELS.get(self.kind, "pull request"),
            "host": self.host,
            "repo": self.slug,
            "token_configured": bool(self.token),
            "token_source": self.token_source,
            "gh_available": gh_available(),
        }


def forge_context(root: Path, *, allow_probe: bool = True) -> ForgeContext:
    config = _load_config()
    remote = forge_api.detect_forge(root, config=config, allow_probe=allow_probe)
    if remote is None:
        return ForgeContext(None, "", "")
    token, source = forge_api.resolve_token(remote, config=config)
    return ForgeContext(remote, token, source)


def _no_remote_error() -> ForgeError:
    return ForgeError(
        "No supported forge on remote 'origin'. Add a GitHub, GitLab or "
        "Forgejo remote, or set its type in Settings > Git.",
        status=409,
        code="forge_unknown_remote",
    )


def _unknown_kind_error(remote: ForgeRemote) -> ForgeError:
    return ForgeError(
        f"Could not tell what {remote.host} runs. Set its type "
        f"(github / gitlab / forgejo) in Settings > Git.",
        status=409,
        code="forge_unknown_kind",
        host=remote.host,
    )


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


def github_pr_view_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Current branch PR/MR (if any) plus basic metadata for the Code panel."""
    root = _root(scope)
    state = _branch_state(root)
    branch = str(state.get("branch") or "")
    ctx = forge_context(root)
    base: dict[str, Any] = {
        "available": False,
        "branch": branch,
        "pr": None,
        "detail": "",
        **ctx.base_payload(),
    }

    if ctx.remote is None:
        base["detail"] = "no supported forge on remote 'origin'"
        return base
    if ctx.kind == UNKNOWN:
        base["detail"] = f"unknown forge type for {ctx.host}"
        return base
    if not branch:
        base["detail"] = "detached HEAD"
        base["available"] = bool(ctx.token)
        return base

    if not ctx.token:
        if ctx.kind == GITHUB and gh_available():
            return _gh_pr_view(root, branch, base)
        base["detail"] = f"no token for {ctx.host}"
        return base

    base["available"] = True
    try:
        pr = forge_api.find_open_request(ctx.remote, ctx.token, branch)
    except ForgeError as exc:
        base["detail"] = exc.message[:300]
        base["available"] = exc.code not in ("forge_token_missing", "forge_token_invalid")
        return base
    if pr is None:
        base["detail"] = "no PR for this branch"
        return base

    checks = forge_api.empty_checks()
    try:
        checks = forge_api.check_summary(ctx.remote, ctx.token, branch=branch)
    except ForgeError:
        # CI is decoration next to the PR link: a forge without a CI API
        # should not blank out the PR the user just opened.
        pass
    base["pr"] = {**pr, "checks": checks}
    return base


def _gh_pr_view(root: Path, branch: str, base: dict[str, Any]) -> dict[str, Any]:
    """Legacy github.com road for a machine with ``gh`` but no token."""
    base["available"] = True
    code, out, err = _gh(
        root,
        "pr",
        "view",
        "--json",
        "url,number,title,state,isDraft,statusCheckRollup,headRefName,baseRefName",
    )
    if code != 0:
        detail = (err or out or "").strip()
        if "no pull requests found" in detail.lower() or "could not find" in detail.lower():
            base["detail"] = "no PR for this branch"
            return base
        base["detail"] = detail[:300] or "gh pr view failed"
        return base

    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        base["detail"] = "invalid gh pr view JSON"
        return base

    rollup = data.get("statusCheckRollup") or []
    base["token_source"] = base.get("token_source") or "gh"
    base["pr"] = {
        "url": data.get("url"),
        "number": data.get("number"),
        "title": data.get("title") or "",
        "state": data.get("state") or "",
        "is_draft": bool(data.get("isDraft")),
        "head": data.get("headRefName") or branch,
        "base": data.get("baseRefName") or "",
        "checks": _summarize_checks(rollup if isinstance(rollup, list) else []),
    }
    return base


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def github_pr_create_payload(
    scope: WorkspaceScope,
    *,
    title: str | None,
    body: str | None = None,
    draft: bool = True,
) -> dict[str, Any]:
    """Push HEAD and open (or reuse) a PR/MR for the current branch."""
    root = _root(scope)
    state = _branch_state(root)
    branch = str(state.get("branch") or "").strip()
    if not branch or not _BRANCH_SAFE.match(branch):
        raise GithubPrError("current branch cannot open a PR", status=409)

    ctx = forge_context(root)
    if ctx.remote is None:
        raise _no_remote_error()
    if ctx.kind == UNKNOWN:
        raise _unknown_kind_error(ctx.remote)
    if not ctx.token:
        if ctx.kind == GITHUB and gh_available():
            return _gh_pr_create(root, branch, title=title, body=body, draft=draft)
        raise forge_api.token_missing_error(ctx.remote)

    head_sha = _head_sha_short(root)
    remote = ctx.remote

    existing = forge_api.find_open_request(remote, ctx.token, branch)
    if existing and existing.get("url"):
        return {
            "ok": True,
            "created": False,
            "pr_url": existing["url"],
            "detail": "existing PR reused",
            "branch": branch,
            "head_sha": head_sha,
            **ctx.base_payload(),
        }

    _push_head(root)

    base_branch = forge_api.default_branch(remote, ctx.token)
    if base_branch == branch:
        raise ForgeError(
            f"{branch} is the default branch on {remote.host}: switch to a "
            f"feature branch before opening a "
            f"{REQUEST_LABELS.get(remote.kind, 'pull request')}.",
            status=409,
            code="forge_head_is_base",
            host=remote.host,
            forge=remote.kind,
        )

    created = forge_api.create_request(
        remote,
        ctx.token,
        head=branch,
        base=base_branch,
        title=(title or "").strip() or branch,
        body=(body or "").strip() or f"PR for `{branch}` (opened from Navin Code).",
        draft=draft,
    )
    # Re-read after push so board continuity gets the tip that was published.
    head_sha = _head_sha_short(root) or head_sha
    return {
        "ok": True,
        "created": True,
        "pr_url": created.get("url"),
        "detail": "draft PR opened" if draft else "PR opened",
        "branch": branch,
        "is_draft": bool(created.get("is_draft")) or draft,
        "head_sha": head_sha,
        "pr_number": created.get("number"),
        **ctx.base_payload(),
    }


def _push_head(root: Path) -> None:
    from navin.webui.project_search import _run_git_write, explain_push_failure

    push = _run_git_write(root, "push", "-u", "origin", "HEAD")
    if push is None:
        raise GithubPrError("git is not available on this host", status=503)
    if push.returncode != 0:
        raise GithubPrError(
            explain_push_failure((push.stderr or push.stdout or "")),
            status=409,
        )


def _gh_pr_create(
    root: Path,
    branch: str,
    *,
    title: str | None,
    body: str | None,
    draft: bool,
) -> dict[str, Any]:
    """Legacy github.com road: `gh` is installed and holds the credentials."""
    head_sha = _head_sha_short(root)
    code, out, _ = _gh(root, "pr", "view", "--json", "url", "--jq", ".url")
    if code == 0 and (out or "").strip().startswith("https://"):
        return {
            "ok": True,
            "created": False,
            "pr_url": out.strip(),
            "detail": "existing PR reused",
            "branch": branch,
            "head_sha": head_sha,
            "forge": GITHUB,
            "forge_label": FORGE_LABELS[GITHUB],
            "request_label": REQUEST_LABELS[GITHUB],
            "token_source": "gh",
        }

    _push_head(root)

    cleaned_title = (title or "").strip() or f"{branch}"
    cleaned_body = (body or "").strip() or f"PR for `{branch}` (opened from Navin Code)."
    args = [
        "pr",
        "create",
        "--title",
        cleaned_title[:200],
        "--body",
        cleaned_body[:4000],
    ]
    if draft:
        args.append("--draft")
    code, out, err = _gh(root, *args)
    if code != 0:
        raise GithubPrError(
            (err or out or "gh pr create failed").strip()[:500],
            status=409,
        )
    url = _first_url(out) or _first_url(err)
    if not url:
        raise GithubPrError("gh pr create returned no URL", status=409)
    head_sha = _head_sha_short(root) or head_sha
    return {
        "ok": True,
        "created": True,
        "pr_url": url,
        "detail": "draft PR opened" if draft else "PR opened",
        "branch": branch,
        "is_draft": draft,
        "head_sha": head_sha,
        "forge": GITHUB,
        "forge_label": FORGE_LABELS[GITHUB],
        "request_label": REQUEST_LABELS[GITHUB],
        "token_source": "gh",
    }


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------


def github_ci_status_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """CI rollup for the current branch PR (status bar + Git panel)."""
    view = github_pr_view_payload(scope)
    pr = view.get("pr") if isinstance(view.get("pr"), dict) else None
    checks = (pr or {}).get("checks") if pr else None
    if not isinstance(checks, dict):
        checks = forge_api.empty_checks()
    return {
        "available": view.get("available", False),
        "branch": view.get("branch") or "",
        "pr_url": (pr or {}).get("url") if pr else None,
        "pr_number": (pr or {}).get("number") if pr else None,
        "detail": view.get("detail") or "",
        "checks": checks,
        "forge": view.get("forge") or UNKNOWN,
        "forge_label": view.get("forge_label") or "",
        "request_label": view.get("request_label") or "pull request",
        "host": view.get("host") or "",
        "repo": view.get("repo") or "",
        "token_configured": bool(view.get("token_configured")),
        "token_source": view.get("token_source") or "",
        "gh_available": bool(view.get("gh_available")),
    }


def github_fix_ci_prompt_payload(
    scope: WorkspaceScope,
    mode: str = "fix",
) -> dict[str, Any]:
    """Structured prompt seed for the Fix CI / Explain CI agent actions.

    mode="fix" asks the agent to repair and re-push; mode="explain" asks for
    a root-cause diagnosis only, without touching the tree.
    """
    root = _root(scope)
    ci = github_ci_status_payload(scope)
    checks = ci.get("checks") if isinstance(ci.get("checks"), dict) else {}
    failing = int(checks.get("failing") or 0)
    log_excerpt = ""
    if failing > 0:
        log_excerpt = _failed_log(root, ci)

    items = checks.get("items") if isinstance(checks.get("items"), list) else []
    failing_names = [
        str(item.get("name") or "")
        for item in items
        if isinstance(item, dict) and str(item.get("state") or "").lower() in {
            "failure",
            "failed",
            "error",
            "cancelled",
            "canceled",
            "timed_out",
        }
    ]
    context = (
        f"Branch: {ci.get('branch') or '(unknown)'}\n"
        f"PR: {ci.get('pr_url') or '(none)'}\n"
        f"Forge: {ci.get('forge_label') or ci.get('forge') or '(unknown)'}\n"
        f"Failing checks: {', '.join(failing_names) or '(see logs)'}\n"
    )
    log_hint = _LOG_HINTS.get(str(ci.get("forge") or ""), _LOG_HINTS[UNKNOWN])
    if mode == "explain":
        prompt = (
            "Explain why CI is failing on the current branch. Diagnosis only: "
            "do not modify any file.\n"
            + context
            + f"Steps: read the failed CI logs ({log_hint}), identify the root "
            "cause, point to the exact file/line or config responsible, and "
            "finish with a short plain-language summary plus the single most "
            "likely fix.\n"
        )
    else:
        prompt = (
            "Fix CI on the current branch without leaving the repo flow.\n"
            + context
            + "Steps: read the failed CI logs, identify root cause, apply_patch, "
            "run verify, commit, push, and re-check CI.\n"
        )
    if log_excerpt:
        prompt += "\n--- failed log excerpt ---\n" + log_excerpt + "\n--- end ---\n"
    return {
        "available": bool(ci.get("available")),
        "branch": ci.get("branch") or "",
        "pr_url": ci.get("pr_url"),
        "failing": failing,
        "prompt": prompt,
        "detail": ci.get("detail") or "",
        "forge": ci.get("forge") or UNKNOWN,
    }


# Where the agent should go looking for the failed job output, per forge.
_LOG_HINTS = {
    GITHUB: "gh run view --log-failed, or the Actions run linked in the checks",
    forge_api.GITLAB: "the failed job trace linked in the pipeline",
    forge_api.FORGEJO: "the failed job linked in the checks",
    UNKNOWN: "the CI run linked in the checks",
}


def _failed_log(root: Path, ci: dict[str, Any]) -> str:
    """Failed CI output for the prompt seed, whichever forge hosts it.

    GitHub goes through ``gh`` (its REST log endpoint redirects to a signed
    blob); GitLab exposes the job trace as plain text over REST. Forgejo has
    no stable log endpoint yet, so its prompt carries the check names only.
    """
    branch = str(ci.get("branch") or "")
    if not branch:
        return ""
    if ci.get("forge") == GITHUB:
        return _gh_failed_log(root, branch) if gh_available() else ""
    ctx = forge_context(root, allow_probe=False)
    if not ctx.usable or ctx.remote is None:
        return ""
    try:
        return forge_api.failed_log_excerpt(ctx.remote, ctx.token, branch=branch)
    except ForgeError:
        return ""


def _gh_failed_log(root: Path, branch: str) -> str:
    """Failed GitHub Actions log via ``gh`` when it is installed."""
    code, out, err = _gh(
        root,
        "run",
        "list",
        "--branch",
        branch,
        "--status",
        "failure",
        "--limit",
        "1",
        "--json",
        "databaseId,name,url,conclusion",
    )
    run_id = None
    if code == 0 and out.strip():
        try:
            rows = json.loads(out)
            if isinstance(rows, list) and rows:
                run_id = rows[0].get("databaseId")
        except json.JSONDecodeError:
            run_id = None
    if run_id is None:
        return ""
    code, out, err = _gh(root, "run", "view", str(run_id), "--log-failed")
    return ((out or err) or "")[:6000]


def _summarize_checks(rollup: list[Any]) -> dict[str, Any]:
    """Fold a ``gh pr view --json statusCheckRollup`` list into the summary."""
    items: list[dict[str, Any]] = []
    for row in rollup[:40]:
        if not isinstance(row, dict):
            continue
        state = str(
            row.get("state")
            or row.get("conclusion")
            or row.get("status")
            or "UNKNOWN"
        ).lower()
        items.append(
            {
                "name": str(row.get("name") or row.get("context") or "check"),
                "state": state,
                "url": row.get("detailsUrl") or row.get("targetUrl"),
            }
        )
    return forge_api.summarize_checks(items)


# New code should read these names; the ``github_`` ones stay for the routes,
# the board and the WebUI client that already import them.
forge_pr_view_payload = github_pr_view_payload
forge_pr_create_payload = github_pr_create_payload
forge_ci_status_payload = github_ci_status_payload
forge_fix_ci_prompt_payload = github_fix_ci_prompt_payload
