# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool: pull / merge requests, issues and CI on GitHub, GitLab, Forgejo.

The repository is the project's ``origin`` remote and the token is the one
from Settings > Git & GitHub (or the env / a signed-in ``gh``), exactly what
the Code panel uses. Everything goes through ``navin.webui.forge_api`` over
REST, so no ``gh`` / ``glab`` / ``tea`` binary is needed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.quality import _QualityTool

_READ_ACTIONS = frozenset(
    {"pr_view", "pr_list", "issue_list", "ci_status", "pr_threads", "ci_logs", "pr_sync_base"}
)
_WRITE_ACTIONS = frozenset(
    {
        "pr_create",
        "pr_merge",
        "pr_review",
        "issue_create",
        "issue_comment",
        "issue_close",
        "pr_reply",
        "pr_resolve_thread",
    }
)
# Actions addressed to one PR/MR or issue by number.
_NUMBERED_ACTIONS = frozenset(
    {
        "pr_view",
        "pr_merge",
        "pr_review",
        "issue_comment",
        "issue_close",
        "pr_threads",
        "pr_reply",
        "pr_resolve_thread",
        "pr_sync_base",
    }
)
_ACTIONS = tuple(sorted(_READ_ACTIONS | _WRITE_ACTIONS))
_REVIEW_EVENTS = {
    "approve": "APPROVE",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
}
# Additive writes keep working in unattended runs (the board opens PRs on its
# own already); a merge, a verdict on a review and closing an issue need a
# person, as posting a review does in pr_comments. A thread reply is additive
# like a comment; resolving a reviewer's thread closes it, like an issue.
_UNATTENDED_OK = frozenset({"pr_create", "issue_create", "issue_comment", "pr_reply"})
# Creating a repository needs no origin remote and always needs a person:
# it runs on its own path and is never allowed unattended.
_REPO_ACTIONS = frozenset({"repo_create"})
_ACTIONS = tuple(sorted(set(_ACTIONS) | _REPO_ACTIONS))
# Issue-triage autopilot (navin/github/triage_runner.py): own path and approval.
_TRIAGE_ACTIONS = frozenset({"issue_triage_plan", "issue_triage_apply", "issue_labels"})
_ACTIONS = tuple(sorted(set(_ACTIONS) | _TRIAGE_ACTIONS))
# Self-healing (navin/agent/self_heal.py): runs in a throwaway git worktree.
_HEAL_ACTIONS = frozenset({"ci_heal"})
_ACTIONS = tuple(sorted(set(_ACTIONS) | _HEAL_ACTIONS))


def _load_config() -> Any | None:
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - env / gh tokens still work
        return None


def repo_name_for(folder: str) -> str:
    """Folder name as a repository name: lowercase, dashes, safe characters."""
    import re

    slug = re.sub(r"[^a-z0-9._-]+", "-", (folder or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-.")
    return slug[:100]


def _current_branch(root: Path) -> str:
    from navin.webui.project_search import _run_git

    result = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if result is None or result.returncode != 0:
        return ""
    branch = (result.stdout or "").strip()
    return "" if branch == "HEAD" else branch


def resolve_forge(root: Path) -> tuple[Any, str]:
    """Public: the project's forge remote and API token, or raise ForgeError."""
    from navin.webui import forge_api

    config = _load_config()
    remote = forge_api.detect_forge(root, config=config)
    if remote is None:
        raise forge_api.ForgeError(
            "this project has no origin remote on a forge. Add one with "
            "git action=remote create=true name=origin remote=<url>.",
            status=409,
            code="forge_no_remote",
        )
    if remote.kind == forge_api.UNKNOWN:
        raise forge_api.ForgeError(
            f"cannot tell which forge {remote.host} runs. Set its type in "
            "Settings > Git & GitHub.",
            status=409,
            code="forge_unknown_kind",
            host=remote.host,
        )
    token, _source = forge_api.resolve_token(remote, config=config, root=root)
    if not token:
        raise forge_api.token_missing_error(remote)
    return remote, token


class ForgeTool(_QualityTool):
    """Act on the project's forge: requests, reviews, issues, CI."""

    @property
    def name(self) -> str:
        return "forge"

    @property
    def description(self) -> str:
        return (
            "Pull/merge requests, issues and CI on the project's origin forge "
            "(GitHub, GitLab, Forgejo) over REST, no gh/glab CLI needed. "
            "pr_create opens a PR for a pushed branch (git action=push first); "
            "pr_merge needs an explicit method (merge|squash|rebase) and asks "
            "approval; pr_review posts approve|request_changes|comment; "
            "ci_status reads checks for a branch or PR. repo_create makes a new "
            "private repository (name, host, org; asks approval) for a project "
            "with no remote yet, then git action=remote + push. PR autopilot: "
            "pr_threads lists review threads (unresolved only unless "
            "include_resolved=true) with thread ids; pr_reply answers a thread "
            "(thread_id, body); pr_resolve_thread resolves it; ci_logs reads the "
            "redacted log tail of failing jobs (check= narrows by name); "
            "pr_sync_base reports behind/ahead vs base and mergeability (merge "
            "the base with git action=fetch + merge, never here). Issue triage: "
            "issue_triage_plan is a dry-run plan (stale, lifecycle labels, "
            "duplicates); issue_triage_apply applies a fresh plan after batch "
            "approval; issue_labels adds/removes labels without replacing others "
            "(skill issue-triage). ci_heal (branch, fix=false, open_pr=false) "
            "reproduces the first failing CI job in a disposable git worktree and "
            "returns a JSON repair plan; fix=true lets an agent repair it there; "
            "open_pr=true (asks approval) commits on navin/heal-<sha>, pushes and "
            "opens a draft PR only when the command passes after the fix. The "
            "user's working tree is never modified. Needs a forge token "
            "(Settings > Git & GitHub) or a signed-in gh on github.com."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        """Reads stay available in Ask and Plan; writes do not."""
        return isinstance(arguments, dict) and (
            arguments.get("action") in _READ_ACTIONS or arguments.get("action") == "issue_triage_plan"
        )

    def call_concurrency_safe(self, arguments: Any) -> bool:
        return self.call_read_only(arguments)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "name": {
                    "type": "string",
                    "description": "repo_create: new repository name (lowercase-dashes; default: the project folder name).",
                },
                "private": {"type": "boolean", "description": "repo_create visibility (default true, private)."},
                "description": {"type": "string", "description": "repo_create short description."},
                "org": {
                    "type": "string",
                    "description": "repo_create owner org / GitLab group (default: the token's own account).",
                },
                "host": {
                    "type": "string",
                    "description": "repo_create forge host (default: github.com), e.g. gitlab.com or a Forgejo host.",
                },
                "number": {
                    "type": "integer",
                    "description": "PR/MR or issue number (pr_view/merge/review, issue_comment/close; ci_status optional).",
                },
                "title": {"type": "string", "description": "pr_create / issue_create title; pr_merge commit title."},
                "body": {
                    "type": "string",
                    "description": "Markdown body: PR/issue description, review text, comment, or merge commit message.",
                },
                "head": {"type": "string", "description": "pr_create source branch (default: current branch)."},
                "base": {"type": "string", "description": "pr_create target branch (default: the repository default branch)."},
                "draft": {"type": "boolean", "description": "pr_create as draft."},
                "method": {
                    "type": "string",
                    "enum": ["merge", "squash", "rebase"],
                    "description": "pr_merge method. Required, never guessed.",
                },
                "sha": {
                    "type": "string",
                    "description": "pr_merge: head sha you reviewed; the merge fails if the branch moved.",
                },
                "event": {
                    "type": "string",
                    "enum": ["approve", "request_changes", "comment"],
                    "description": "pr_review verdict.",
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "merged", "all"],
                    "description": "pr_list / issue_list filter (default open; merged is PR-only).",
                },
                "limit": {
                    "type": "integer",
                    "description": "pr_list / issue_list size (default 20); pr_threads (default 50); ci_logs jobs (default 2, max 5).",
                },
                "thread_id": {
                    "type": "string",
                    "description": "pr_reply / pr_resolve_thread: the thread id from pr_threads.",
                },
                "include_resolved": {
                    "type": "boolean",
                    "description": "pr_threads: also list resolved threads (default false).",
                },
                "resolved": {
                    "type": "boolean",
                    "description": "pr_resolve_thread: false reopens the thread (default true).",
                },
                "check": {
                    "type": "string",
                    "description": "ci_logs: only failing jobs whose name contains this text.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "ci_logs: log tail size per job (default 6000, max 20000).",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "issue_create labels (unknown ones are dropped); issue_labels labels to add.",
                },
                "remove_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "issue_labels: labels to remove (others are kept).",
                },
                "repo": {
                    "type": "string",
                    "description": "issue_triage_plan / issue_triage_apply / issue_labels: owner/name or URL (default: origin).",
                },
                "branch": {"type": "string", "description": "ci_status / ci_heal branch or ref (default: current branch)."},
                "fix": {"type": "boolean", "description": "ci_heal: let an agent apply the fix in the worktree (default false, plan only)."},
                "open_pr": {"type": "boolean", "description": "ci_heal: open a draft PR after a verified fix (default false; asks approval)."},
            },
            "required": ["action"],
        }

    async def execute(self, action: str = "", **kwargs: Any) -> Any:
        act = (action or "").strip().lower()
        if act in _REPO_ACTIONS:
            return await self._repo_create(kwargs)
        if act in _TRIAGE_ACTIONS:
            from navin.github.triage_runner import forge_tool_action

            return await forge_tool_action(self, act, kwargs)
        if act in _HEAL_ACTIONS:
            return await self._ci_heal(kwargs)
        if act not in _READ_ACTIONS and act not in _WRITE_ACTIONS:
            return ToolResult.error(f"Error: action must be one of {', '.join(_ACTIONS)}")
        root, error = self._root_or_error()
        if root is None:
            return error
        if act == "pr_merge" and str(kwargs.get("method") or "").strip().lower() not in (
            "merge",
            "squash",
            "rebase",
        ):
            return ToolResult.error(
                "Error: pr_merge needs method=merge|squash|rebase. Pick the one the "
                "project uses; it is never guessed."
            )
        if act in _NUMBERED_ACTIONS and not kwargs.get("number"):
            return ToolResult.error(f"Error: {act} needs number=<PR/MR or issue number>.")
        if act in {"pr_reply", "pr_resolve_thread"} and not str(kwargs.get("thread_id") or "").strip():
            return ToolResult.error(f"Error: {act} needs thread_id=<id from pr_threads>.")
        if act == "pr_reply" and not str(kwargs.get("body") or "").strip():
            return ToolResult.error("Error: pr_reply needs body=<reply text>.")
        if act == "pr_review" and str(kwargs.get("event") or "").strip().lower() not in _REVIEW_EVENTS:
            return ToolResult.error("Error: pr_review needs event=approve|request_changes|comment.")

        from navin.webui import forge_api

        try:
            remote, token = await asyncio.to_thread(self._resolve, root)
        except forge_api.ForgeError as exc:
            remote, token, error = await self._after_auth_error(exc, root)
            if error is not None:
                return error

        if act in _WRITE_ACTIONS:
            refusal = await self._approve(act, remote, kwargs, root)
            if refusal is not None:
                return refusal

        try:
            result = await asyncio.to_thread(self._dispatch, act, root, remote, token, kwargs)
        except forge_api.ForgeError as exc:
            if exc.code != "forge_token_invalid":
                return ToolResult.error(f"Error: {exc.message}")
            remote, token, error = await self._after_auth_error(exc, root)
            if error is not None:
                return error
            try:
                result = await asyncio.to_thread(self._dispatch, act, root, remote, token, kwargs)
            except forge_api.ForgeError as again:
                return ToolResult.error(f"Error: {again.message}")
            except (TypeError, ValueError) as again:
                return ToolResult.error(f"Error: {again}")
        except (TypeError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")
        payload = {"forge": remote.kind, "repo": remote.slug, "host": remote.host, **result}
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    # -- repo_create -------------------------------------------------------

    async def _repo_create(self, args: dict[str, Any]) -> Any:
        """Create a new repository for a project that has no remote yet.

        Refuses when ``origin`` already exists (never a second repository),
        always asks a person (never unattended) and defaults to private.
        """
        from navin.agent.approval import ApprovalRequest, request_approval
        from navin.agent.credentials import ensure_git_credentials, git_auth_note
        from navin.webui import forge_api

        root, _error = self._root_or_error()
        if root is not None:
            origin = await asyncio.to_thread(forge_api._origin_url, root)
            if origin:
                return ToolResult.error(
                    "Error: this project already has an origin remote "
                    f"({forge_api.redact_url_credentials(origin)}). Commit and push there "
                    "instead; repo_create never makes a second repository or changes a remote."
                )
        name = str(args.get("name") or "").strip() or (repo_name_for(root.name) if root is not None else "")
        if not name:
            return ToolResult.error("Error: repo_create needs name=<repository name>.")
        host = str(args.get("host") or "").strip() or "github.com"
        private = args.get("private")
        private = True if private is None else bool(private)
        org = str(args.get("org") or "").strip() or None
        description = str(args.get("description") or "")

        config = _load_config()
        host_name, port = forge_api._split_host(host)
        if not host_name:
            return ToolResult.error("Error: repo_create needs a valid host, e.g. github.com.")
        kind = forge_api.kind_from_host(host_name, config=config)
        probe = forge_api.ForgeRemote(kind=kind, host=host_name, owner=org or "-", repo=name, port=port)
        if kind not in forge_api.FORGE_KINDS:
            kind = await asyncio.to_thread(forge_api.probe_kind, probe)
            probe = probe.with_kind(kind)
        if kind not in forge_api.FORGE_KINDS:
            return ToolResult.error(
                f"Error: cannot tell which forge {host_name} runs. Set its type in Settings > Git & GitHub."
            )

        def _token() -> str:
            return forge_api.resolve_token(probe, config=_load_config())[0]

        token = await asyncio.to_thread(_token)
        if not token:
            answer = await ensure_git_credentials(host_name, f"Connect {host_name} to create the repository.")
            if answer is not None and answer.ok:
                token = await asyncio.to_thread(_token)
            if not token:
                missing = forge_api.token_missing_error(probe).message
                note = git_auth_note(host_name, answer)
                return ToolResult.error(f"Error: {missing}" + (f"\n{note}" if note else ""))

        owner = org or "your account"
        visibility = "private" if private else "public"
        decision = await request_approval(
            ApprovalRequest(
                tool="forge",
                action=f"Create a {visibility} repository {name} on {host_name} ({owner})",
                reason=f"forge repo_create on {host_name}",
                detail=f"name={name} private={private} org={org or '-'}",
                consequence=(
                    "Creates a new repository on the forge. It stays until someone deletes it there."
                    if private
                    else "Creates a new PUBLIC repository: anyone can see what gets pushed."
                ),
                scope="forge:repo_create",
                allow_when_unattended=False,
            )
        )
        if not decision.allowed:
            return ToolResult.error(f"Error: refused. {decision.reason}")
        try:
            created = await asyncio.to_thread(
                lambda: forge_api.create_repo(
                    host,
                    token,
                    name,
                    private=private,
                    description=description,
                    org=org,
                    kind=kind,
                    config=config,
                )
            )
        except forge_api.ForgeError as exc:
            return ToolResult.error(f"Error: {exc.message}")
        except (TypeError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")
        created["next"] = (
            "git action=remote create=true name=origin remote=<clone_url>, then "
            "git action=push set_upstream=true"
        )
        return json.dumps(created, ensure_ascii=False, indent=2, default=str)

    # -- ci_heal -----------------------------------------------------------

    async def _ci_heal(self, args: dict[str, Any]) -> Any:
        from navin.agent import self_heal

        root, error = self._root_or_error()
        if root is None:
            return error
        branch = str(args.get("branch") or "").strip()
        fix = bool(args.get("fix", False))
        open_pr = bool(args.get("open_pr", False))
        if open_pr and not fix:
            return ToolResult.error("Error: ci_heal open_pr=true needs fix=true (a PR needs a verified fix).")
        if open_pr:
            from navin.agent.approval import ApprovalRequest, request_approval

            decision = await request_approval(
                ApprovalRequest(
                    tool="forge",
                    action="Open a self-heal draft pull request",
                    reason="forge ci_heal open_pr",
                    detail=f"branch={branch or 'current branch'} head=navin/heal-<sha>",
                    consequence="Pushes a navin/heal-* branch and creates a visible draft PR on the forge.",
                    scope="forge:ci_heal",
                    allow_when_unattended=True,
                )
            )
            if not decision.allowed:
                return ToolResult.error(f"Error: refused. {decision.reason}")
        try:
            report = await asyncio.to_thread(
                self_heal.heal_in_worktree, root, branch, fix=fix, open_pr=open_pr
            )
        except Exception as exc:  # surfaced to the model, never hidden
            return ToolResult.error(f"Error: ci_heal failed: {exc}")
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str)

    # -- plumbing ----------------------------------------------------------

    async def _after_auth_error(self, exc: Any, root: Path) -> tuple[Any, str, Any]:
        """``(remote, token, None)`` once the user connected the host, else an error.

        A missing or rejected token opens the secure git account card once
        per turn per host (interactive UIs only); the token goes from the card
        to Settings > Git & GitHub and never through the model.
        """
        from navin.agent.credentials import ensure_git_credentials, git_auth_note
        from navin.webui import forge_api

        failed = ToolResult.error(f"Error: {exc.message}")
        host = str(getattr(exc, "host", "") or "")
        if getattr(exc, "code", "") not in {"forge_token_missing", "forge_token_invalid"} or not host:
            return None, "", failed
        answer = await ensure_git_credentials(host, f"Connect {host} so the forge actions work.")
        if answer is not None and answer.ok:
            try:
                remote, token = await asyncio.to_thread(self._resolve, root)
                return remote, token, None
            except forge_api.ForgeError as again:
                return None, "", ToolResult.error(f"Error: {again.message}")
        note = git_auth_note(host, answer)
        return None, "", ToolResult.error(f"Error: {exc.message}\n{note}") if note else failed

    def _resolve(self, root: Path) -> tuple[Any, str]:
        return resolve_forge(root)

    async def _approve(self, act: str, remote: Any, args: dict[str, Any], root: Path) -> Any:
        from navin.agent.approval import ApprovalRequest, request_approval

        number = args.get("number")
        label = f"{remote.slug}#{number}" if number else remote.slug
        details = {
            "pr_create": (
                f"Open a pull request on {remote.slug}",
                f"head={args.get('head') or 'current branch'} base={args.get('base') or 'default'}",
                "Creates a visible pull/merge request on the forge.",
            ),
            "pr_merge": (
                f"Merge {label} ({args.get('method')})",
                f"method={args.get('method')} sha={args.get('sha') or 'any'}",
                "Merges into the target branch on the forge; reverting needs a new commit.",
            ),
            "pr_review": (
                f"Post a review ({args.get('event')}) on {label}",
                f"event={args.get('event')}",
                "Publishes a review verdict visible to the PR author and reviewers.",
            ),
            "issue_create": (
                f"Open an issue on {remote.slug}",
                f"title={str(args.get('title') or '')[:120]}",
                "Creates a visible issue on the forge.",
            ),
            "issue_comment": (
                f"Comment on {label}",
                str(args.get("body") or "")[:200],
                "Posts a visible comment on the forge.",
            ),
            "issue_close": (
                f"Close issue {label}",
                str(args.get("body") or "")[:200],
                "Closes the issue for everyone watching it.",
            ),
            "pr_reply": (
                f"Reply in a review thread on {label}",
                str(args.get("body") or "")[:200],
                "Posts a visible reply in the review conversation.",
            ),
            "pr_resolve_thread": (
                f"{'Resolve' if args.get('resolved', True) is not False else 'Reopen'} a review thread on {label}",
                f"thread={str(args.get('thread_id') or '')[:80]}",
                "Changes the thread state for the reviewer; it can be reopened.",
            ),
        }
        action, detail, consequence = details[act]
        decision = await request_approval(
            ApprovalRequest(
                tool="forge",
                action=action,
                reason=f"forge {act} on {remote.host}",
                detail=detail,
                consequence=consequence,
                scope=f"forge:{act}",
                allow_when_unattended=act in _UNATTENDED_OK,
            )
        )
        if decision.allowed:
            return None
        return ToolResult.error(f"Error: refused. {decision.reason}")

    def _dispatch(
        self,
        act: str,
        root: Path,
        remote: Any,
        token: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        from navin.webui import forge_api

        number = int(args["number"]) if args.get("number") else 0
        limit = int(args.get("limit") or 20)
        state = str(args.get("state") or "open")
        body = str(args.get("body") or "")

        if act == "pr_list":
            return {"requests": forge_api.list_requests(remote, token, state=state, limit=limit)}
        if act == "pr_view":
            return {"request": forge_api.get_request(remote, token, number)}
        if act == "pr_create":
            head = str(args.get("head") or "").strip() or _current_branch(root)
            if not head:
                raise ValueError("HEAD is detached: name the source branch with head=")
            existing = forge_api.find_open_request(remote, token, head)
            if existing is not None:
                return {"created": False, "request": existing}
            base = str(args.get("base") or "").strip() or forge_api.default_branch(remote, token)
            if base == head:
                raise ValueError(f"head and base are both {head!r}: switch to a feature branch first")
            created = forge_api.create_request(
                remote,
                token,
                head=head,
                base=base,
                title=str(args.get("title") or head),
                body=body,
                draft=bool(args.get("draft")),
            )
            return {"created": True, "request": created}
        if act == "pr_merge":
            return forge_api.merge_request(
                remote,
                token,
                number,
                method=str(args.get("method") or ""),
                title=str(args.get("title") or ""),
                message=body,
                sha=str(args.get("sha") or ""),
            )
        if act == "pr_review":
            event = _REVIEW_EVENTS[str(args.get("event") or "").strip().lower()]
            return forge_api.create_review(
                remote, token, number, body=body, comments=[], event=event
            )
        if act == "issue_list":
            issue_state = state if state in ("open", "closed", "all") else "open"
            return {"issues": forge_api.list_issues(remote, token, state=issue_state, limit=limit)}
        if act == "issue_create":
            labels = args.get("labels") or []
            if not isinstance(labels, list):
                labels = [str(labels)]
            return {
                "issue": forge_api.create_issue(
                    remote,
                    token,
                    title=str(args.get("title") or ""),
                    body=body,
                    labels=[str(label) for label in labels],
                )
            }
        if act == "issue_comment":
            return forge_api.comment_issue(remote, token, number, body)
        if act == "issue_close":
            return forge_api.close_issue(remote, token, number, comment=body)
        if act == "pr_threads":
            return forge_api.list_review_threads(
                remote,
                token,
                number,
                include_resolved=bool(args.get("include_resolved")),
                limit=int(args.get("limit") or 50),
            )
        if act == "pr_reply":
            return forge_api.reply_review_thread(
                remote, token, number, str(args.get("thread_id") or ""), body
            )
        if act == "pr_resolve_thread":
            return forge_api.resolve_review_thread(
                remote,
                token,
                number,
                str(args.get("thread_id") or ""),
                resolved=args.get("resolved", True) is not False,
            )
        if act == "pr_sync_base":
            sync = forge_api.request_sync_state(remote, token, number)
            if sync.get("needs_base_merge"):
                sync["next"] = (
                    f"git action=fetch, then git action=merge name=origin/{sync.get('base') or '<base>'} "
                    "on the PR branch; resolve conflicts, commit, push (never force)"
                )
            return {"sync": sync}
        # ci_status / ci_logs
        branch = str(args.get("branch") or "").strip()
        if not branch and number:
            branch = str(forge_api.get_request(remote, token, number).get("head") or "")
        if not branch:
            branch = _current_branch(root)
        if not branch:
            raise ValueError("HEAD is detached: name the ref with branch=")
        if act == "ci_logs":
            return {
                "branch": branch,
                **forge_api.ci_failed_logs(
                    remote,
                    token,
                    ref=branch,
                    check=str(args.get("check") or ""),
                    limit=int(args.get("limit") or 2),
                    max_chars=int(args.get("max_chars") or 6000),
                ),
            }
        return {"branch": branch, "checks": forge_api.check_summary(remote, token, branch=branch)}
