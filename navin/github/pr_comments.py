"""Post review/security findings as inline comments on a pull request.

Works on GitHub, GitLab and Forgejo/Gitea through the forge REST API
(:mod:`navin.webui.forge_api`). GitHub and Forgejo have a review object that
carries every inline comment at once; GitLab has none, so each finding
becomes a discussion pinned to the diff and the summary becomes a note.
``gh`` stays a fallback on github.com for setups that rely on it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=str(cwd) if cwd else None,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _forge() -> Any | None:
    try:
        from navin.webui import forge_api
    except ImportError:  # pragma: no cover - webui extras absent
        return None
    return forge_api


def _config() -> Any | None:
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - a broken config must not block a review
        return None


def _current_branch(root: Path) -> str:
    code, out, _err = _run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        return ""
    branch = (out or "").strip()
    return "" if branch == "HEAD" else branch


def _forge_pr_context(root: Path, pr: int | None) -> dict[str, Any] | None:
    """PR context through the forge API, or None to let ``gh`` try."""
    api = _forge()
    if api is None:
        return None
    config = _config()
    try:
        remote = api.detect_forge(root, config=config)
    except Exception:  # noqa: BLE001 - detection is best-effort
        return None
    if remote is None or remote.kind == api.UNKNOWN:
        return None
    token, _source = api.resolve_token(remote, config=config)
    label = api.REQUEST_LABELS.get(remote.kind, "pull request")
    if not token:
        if remote.kind == api.GITHUB and gh_available():
            return None
        return {
            "ok": False,
            "error": api.token_missing_error(remote).message,
            "repo": remote.slug,
            "forge": remote.kind,
            "host": remote.host,
            "request_label": label,
        }
    try:
        if pr is not None and int(pr) > 0:
            request = api.get_request(remote, token, int(pr))
        else:
            branch = _current_branch(root)
            if not branch:
                return {
                    "ok": False,
                    "error": "detached HEAD: pass pr=<number>",
                    "repo": remote.slug,
                    "forge": remote.kind,
                }
            request = api.find_open_request(remote, token, branch)
            if not request:
                return {
                    "ok": False,
                    "error": f"No open {label} for {branch}",
                    "repo": remote.slug,
                    "forge": remote.kind,
                    "host": remote.host,
                    "request_label": label,
                }
    except api.ForgeError as exc:
        return {
            "ok": False,
            "error": exc.message,
            "repo": remote.slug,
            "forge": remote.kind,
            "host": remote.host,
            "request_label": label,
        }
    return {
        "ok": True,
        "pr": int(request.get("number") or 0),
        "repo": remote.slug,
        "url": request.get("url"),
        "title": request.get("title"),
        "head_ref": request.get("head"),
        "source": "forge_api",
        "forge": remote.kind,
        "host": remote.host,
        "request_label": label,
        "remote": remote,
        "token": token,
    }


def resolve_pr_context(root: Path, pr: int | None = None) -> dict[str, Any]:
    """Resolve the request number + repo identity for the workspace.

    The forge REST API answers first (any forge, no binary needed); ``gh``
    only runs on github.com when no token is configured.
    """
    via_forge = _forge_pr_context(root, pr)
    if via_forge is not None:
        return via_forge
    if not gh_available():
        return {
            "ok": False,
            "error": (
                "no forge token for this remote and gh CLI not found - add a "
                "token in Settings > Git"
            ),
        }

    code, out, err = _run(
        ["gh", "repo", "view", "--json", "nameWithOwner,url", "-q", "."],
        cwd=root,
    )
    if code != 0:
        return {
            "ok": False,
            "error": (err or out or "gh repo view failed").strip()[:500],
            "hint": "Run from a git repo with a GitHub remote and gh auth.",
        }
    try:
        repo_data = json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": "gh repo view returned invalid JSON"}
    name_with_owner = str(repo_data.get("nameWithOwner") or "").strip()
    if "/" not in name_with_owner:
        return {"ok": False, "error": "could not resolve GitHub owner/repo"}

    if pr is not None and pr > 0:
        pr_number = int(pr)
        pr_meta: dict[str, Any] = {"number": pr_number, "source": "argument"}
        code, out, err = _run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,url,title,headRefName",
                "-q",
                ".",
            ],
            cwd=root,
        )
        if code == 0:
            try:
                viewed = json.loads(out)
                pr_meta.update(viewed if isinstance(viewed, dict) else {})
            except json.JSONDecodeError:
                pass
    else:
        code, out, err = _run(
            ["gh", "pr", "view", "--json", "number,url,title,headRefName", "-q", "."],
            cwd=root,
        )
        if code != 0:
            return {
                "ok": False,
                "error": (err or out or "gh pr view failed").strip()[:500],
                "hint": "Pass pr=<number> or checkout a branch with an open PR.",
                "repo": name_with_owner,
            }
        try:
            viewed = json.loads(out)
        except json.JSONDecodeError:
            return {"ok": False, "error": "gh pr view returned invalid JSON"}
        if not viewed.get("number"):
            return {"ok": False, "error": "No open PR for the current branch"}
        pr_meta = dict(viewed)
        pr_meta["source"] = "gh_pr_view"
        pr_number = int(viewed["number"])

    return {
        "ok": True,
        "pr": pr_number,
        "repo": name_with_owner,
        "url": pr_meta.get("url"),
        "title": pr_meta.get("title"),
        "head_ref": pr_meta.get("headRefName"),
        "source": pr_meta.get("source"),
        "repo_url": repo_data.get("url"),
    }


def list_pr_changed_paths(
    root: Path,
    pr: int,
    repo: str,
    *,
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return changed file paths on the PR (for anchoring inline comments)."""
    remote = (ctx or {}).get("remote")
    token = str((ctx or {}).get("token") or "")
    api = _forge()
    if remote is not None and token and api is not None:
        try:
            paths = api.request_files(remote, token, int(pr))
        except api.ForgeError as exc:
            return {"ok": False, "error": exc.message[:500], "paths": []}
        return {"ok": True, "paths": paths, "count": len(paths)}
    code, out, err = _run(
        [
            "gh",
            "api",
            f"repos/{repo}/pulls/{pr}/files",
            "--paginate",
            "-q",
            ".[].filename",
        ],
        cwd=root,
        timeout=120,
    )
    if code != 0:
        return {
            "ok": False,
            "error": (err or out or "failed to list PR files").strip()[:500],
            "paths": [],
        }
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    return {"ok": True, "paths": paths, "count": len(paths)}


def _finding_body(item: dict[str, Any], *, kind: str, index: int) -> str:
    sev = str(item.get("severity") or "medium").upper()
    summary = str(item.get("summary") or item.get("id") or "finding").strip()
    explanation = str(
        item.get("explanation") or item.get("trigger_flow") or item.get("impact") or ""
    ).strip()
    recommendation = str(
        item.get("recommendation") or item.get("minimal_fix") or ""
    ).strip()
    existing = str(item.get("existing_code") or "").strip()
    suggested = str(item.get("suggested_code") or "").strip()
    poc = str(
        item.get("poc_sketch")
        or item.get("malicious_input_example")
        or item.get("exploit_scenario")
        or ""
    ).strip()
    parts = [f"**[{sev}]** {summary}"]
    if explanation:
        parts.append(explanation[:1500])
    if existing:
        parts.append(f"```\n{existing[:800]}\n```")
    if suggested:
        parts.append(f"Suggested:\n```\n{suggested[:800]}\n```")
    if poc:
        parts.append(f"Evidence / PoC:\n```\n{poc[:800]}\n```")
    if recommendation:
        parts.append(f"Recommendation: {recommendation[:600]}")
    parts.append(f"_Navin {kind} finding #{index}_")
    return "\n\n".join(parts)


def build_comment_plan(
    findings: list[dict[str, Any]],
    *,
    kind: str = "review",
    max_comments: int = 40,
    allowed_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Build inline comment payloads. Optionally restrict to PR-changed paths."""
    plan: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for idx, raw in enumerate(findings, start=1):
        path = str(raw.get("file_path") or raw.get("path") or "").replace("\\", "/").lstrip("./")
        line = raw.get("start_line", raw.get("line"))
        try:
            line_n = int(line) if line is not None else 0
        except (TypeError, ValueError):
            line_n = 0
        if not path or line_n <= 0:
            skipped.append({"summary": str(raw.get("summary") or "")[:80], "reason": "missing_path_or_line"})
            continue
        if allowed_paths is not None and path not in allowed_paths:
            skipped.append({"path": path, "summary": str(raw.get("summary") or "")[:80], "reason": "not_in_pr_diff"})
            continue
        if len(plan) >= max_comments:
            skipped.append({"path": path, "reason": "max_comments"})
            continue
        plan.append(
            {
                "path": path,
                "line": line_n,
                "side": "RIGHT",
                "body": _finding_body(raw, kind=kind, index=idx),
                "severity": str(raw.get("severity") or "medium"),
                "summary": str(raw.get("summary") or "")[:120],
            }
        )
    return {"comments": plan, "skipped": skipped}


def preview_pr_comments(
    root: Path,
    findings: list[dict[str, Any]],
    *,
    kind: str = "review",
    pr: int | None = None,
    max_comments: int = 40,
) -> dict[str, Any]:
    """Resolve PR context and return the comment plan without posting."""
    root = root.expanduser().resolve(strict=False)
    ctx = resolve_pr_context(root, pr)
    if not ctx.get("ok"):
        # Still return a local plan so the agent can show what would be posted.
        plan = build_comment_plan(findings, kind=kind, max_comments=max_comments)
        return {
            **ctx,
            "kind": kind,
            "comment_count": len(plan["comments"]),
            "preview": [
                {"path": c["path"], "line": c["line"], "severity": c["severity"], "summary": c["summary"]}
                for c in plan["comments"]
            ],
            "skipped": plan["skipped"],
            "note": "PR context unavailable - preview is local only.",
        }

    files = list_pr_changed_paths(root, int(ctx["pr"]), str(ctx["repo"]), ctx=ctx)
    allowed = set(files.get("paths") or []) if files.get("ok") else None
    plan = build_comment_plan(
        findings,
        kind=kind,
        max_comments=max_comments,
        allowed_paths=allowed,
    )
    return {
        "ok": True,
        "action": "preview",
        "pr": ctx["pr"],
        "repo": ctx["repo"],
        "url": ctx.get("url"),
        "title": ctx.get("title"),
        "kind": kind,
        "pr_file_count": files.get("count"),
        "comment_count": len(plan["comments"]),
        "preview": [
            {"path": c["path"], "line": c["line"], "severity": c["severity"], "summary": c["summary"]}
            for c in plan["comments"]
        ],
        "skipped": plan["skipped"],
        "forge": ctx.get("forge") or "github",
        "request_label": ctx.get("request_label") or "pull request",
        "note": (
            "Preview only - call action=post to create the review on "
            f"{ctx.get('host') or 'the forge'}."
        ),
    }


def post_pr_review(
    root: Path,
    findings: list[dict[str, Any]],
    *,
    kind: str = "review",
    pr: int | None = None,
    event: str = "COMMENT",
    summary: str | None = None,
    max_comments: int = 40,
) -> dict[str, Any]:
    """Create a review with inline comments on GitHub, GitLab or Forgejo."""
    root = root.expanduser().resolve(strict=False)
    ctx = resolve_pr_context(root, pr)
    if not ctx.get("ok"):
        return ctx

    files = list_pr_changed_paths(root, int(ctx["pr"]), str(ctx["repo"]), ctx=ctx)
    if not files.get("ok"):
        return {"ok": False, "error": files.get("error"), "pr": ctx["pr"], "repo": ctx["repo"]}

    allowed = set(files.get("paths") or [])
    plan = build_comment_plan(
        findings,
        kind=kind,
        max_comments=max_comments,
        allowed_paths=allowed,
    )
    comments = plan["comments"]
    if not comments:
        return {
            "ok": False,
            "error": "No findings with path+line on the PR diff to comment on",
            "pr": ctx["pr"],
            "repo": ctx["repo"],
            "skipped": plan["skipped"],
        }

    title = "Security review" if kind == "security" else "Code review"
    body = (summary or "").strip() or (
        f"## Navin {title}\n\n"
        f"{len(comments)} inline comment(s) from structured findings"
        + (f" ({len(plan['skipped'])} skipped)." if plan["skipped"] else ".")
    )
    payload = {
        "event": event if event in {"COMMENT", "REQUEST_CHANGES", "APPROVE"} else "COMMENT",
        "body": body[:65000],
        "comments": [
            {"path": c["path"], "line": c["line"], "side": c["side"], "body": c["body"]}
            for c in comments
        ],
    }

    remote = ctx.get("remote")
    token = str(ctx.get("token") or "")
    api = _forge()
    if remote is not None and token and api is not None:
        try:
            result = api.create_review(
                remote,
                token,
                int(ctx["pr"]),
                body=str(payload["body"]),
                comments=payload["comments"],  # type: ignore[arg-type]
                event=str(payload["event"]),
            )
        except api.ForgeError as exc:
            return {
                "ok": False,
                "error": exc.message[:800],
                "pr": ctx["pr"],
                "repo": ctx["repo"],
                "forge": ctx.get("forge"),
                "comment_count": len(comments),
                "skipped": plan["skipped"],
            }
        return {
            "ok": True,
            "action": "post",
            "posted": True,
            "pr": ctx["pr"],
            "repo": ctx["repo"],
            "url": ctx.get("url"),
            "kind": kind,
            "forge": ctx.get("forge"),
            "request_label": ctx.get("request_label") or "pull request",
            "comment_count": int(result.get("posted") or 0),
            # GitLab refuses a discussion whose line left the diff, one by one.
            "rejected_comments": result.get("failed") or [],
            "skipped": plan["skipped"],
            "review_id": result.get("review_id"),
            "html_url": result.get("html_url"),
            "preview": [
                {
                    "path": c["path"],
                    "line": c["line"],
                    "severity": c["severity"],
                    "summary": c["summary"],
                }
                for c in comments
            ],
            "note": f"Review posted on {ctx.get('host') or 'the forge'} over REST.",
        }

    api_path = f"repos/{ctx['repo']}/pulls/{ctx['pr']}/reviews"
    code, out, err = _run(
        ["gh", "api", "--method", "POST", api_path, "--input", "-"],
        cwd=root,
        timeout=90,
        input_text=json.dumps(payload),
    )
    if code != 0:
        return {
            "ok": False,
            "error": (err or out or "gh api failed").strip()[:800],
            "pr": ctx["pr"],
            "repo": ctx["repo"],
            "comment_count": len(comments),
            "skipped": plan["skipped"],
        }

    try:
        data = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        data = {"raw": out[:500]}

    return {
        "ok": True,
        "action": "post",
        "posted": True,
        "pr": ctx["pr"],
        "repo": ctx["repo"],
        "url": ctx.get("url"),
        "kind": kind,
        "comment_count": len(comments),
        "skipped": plan["skipped"],
        "review_id": data.get("id"),
        "html_url": data.get("html_url"),
        "preview": [
            {"path": c["path"], "line": c["line"], "severity": c["severity"], "summary": c["summary"]}
            for c in comments
        ],
        "note": "Review comments posted via gh api.",
    }
