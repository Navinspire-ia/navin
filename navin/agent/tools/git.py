"""Git tool: read history, record work, and sync with the remote.

``navin.utils.git_state`` already tells the model, every turn, which branch it is
on and what is uncommitted. What it did not do is act: the agent had to fall back
on ``exec git`` for a diff, a log, a blame, or a commit, and then re-parse output
meant for a human. Reading the change it just made was a shell call away, so it
often skipped it.

This module covers the whole local workflow, including the parts that move
history: fetch, push, pull, merge, rebase and reset. An agent that can commit but
not push, or merge but not abort the merge it started, is stuck halfway through
its own work; routing those through the shell only hid them from the parsing and
the path checks that make the rest of this tool worth using.

Every command runs non-interactively: git is never allowed to open an editor or
prompt for credentials, because there is no terminal on the other end and a
prompt would simply hang until the timeout.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.filesystem import _FsTool
from navin.utils.git_state import RepoState, clear_cache, repo_state
from navin.utils.proc import no_window_kwargs

_TIMEOUT = 30.0
_SHORT_TIMEOUT = 10.0
# Talking to a remote is slower than anything local, and a clone-sized fetch on a
# thin connection is not a hang.
_NETWORK_TIMEOUT = 180.0
# Above the loop's own result ceiling a note appended at the end would be cut
# off, so long output carries its warning at the top instead.
_MAX_CHARS = 24_000
# How many paths to name per status section before switching to a count.
_STATUS_SAMPLE = 25
_QUERY_ACTIONS = frozenset(
    {"status", "diff", "log", "show", "blame", "branches", "stash_list"}
)
_RESET_MODES = ("soft", "mixed", "hard")
_STEPS = ("start", "continue", "abort", "skip")


def _git_binary() -> str | None:
    """Resolve git. A separate function so tests can force its absence."""
    return shutil.which("git")


def _clone_folder_name(url: str) -> str:
    """Last path segment of a git URL, without .git."""
    text = url.strip().rstrip("/")
    if "://" in text:
        text = text.split("://", 1)[-1]
    text = text.split(":", 1)[-1]
    name = text.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


async def _ask_before_discarding(
    *,
    action: str,
    reason: str,
    detail: str,
    consequence: str,
    scope: str | None = None,
) -> str | None:
    """Put a history-losing git command to the user; return the refusal, if any.

    Only the two commands git itself cannot undo go through here. Everything
    else - a commit, a switch, a stash - is recoverable from the reflog, and
    asking about recoverable work would make the question meaningless by the time
    it matters.
    """
    from navin.agent.approval import ApprovalRequest, request_approval

    decision = await request_approval(ApprovalRequest(
        tool="git",
        action=action,
        reason=reason,
        detail=detail,
        consequence=consequence,
        scope=scope or "",
        allow_when_unattended=_unattended_discard_allowed(),
    ))
    if decision.allowed:
        return None
    return ToolResult.error(f"Error: refused. {decision.reason}")


def _unattended_discard_allowed() -> bool:
    """Whether a history-losing git command may proceed with nobody watching.

    With approvals off (the autonomous default) these commands keep working
    everywhere - CLI, cron, subagents - exactly as they always did. But an
    operator who turned approvals on asked for a stop before history is
    destroyed, so an unattended run is refused instead of silently waved
    through.
    """
    try:
        from navin.config.loader import load_config

        return not load_config().tools.approvals.enabled
    except Exception:
        return True


def _git_env() -> dict[str, str]:
    """Environment that cannot stop and wait for a human.

    A turn has no terminal attached, so an editor for a merge message or a
    username prompt on push does not ask anyone anything - it blocks until the
    timeout and reports a hang instead of the real problem. Credential helpers
    are left alone: those answer without a prompt, which is the point.
    Settings > Git tokens are forwarded under the same names ``gh`` / ``glab``
    / ``tea`` already read, so HTTPS helpers see what the Code panel uses.
    """

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_EDITOR"] = "true"
    env["GIT_PAGER"] = "cat"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env.pop("EDITOR", None)
    env.pop("VISUAL", None)
    from navin.agent.tools.exec_env import forge_tokens_from_config

    env.update(forge_tokens_from_config(_GIT_FORGE_TOKENS.get(), environ=env))
    return env


async def _git(
    cwd: Path,
    args: Sequence[str],
    *,
    timeout: float = _TIMEOUT,
) -> tuple[int, str, str]:
    """Run one git command, never through a shell and never paging."""
    binary = _git_binary()
    if binary is None:
        return 127, "", "git is not installed or not on PATH"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--no-pager",
            "-C",
            str(cwd),
            "-c",
            "core.quotepath=false",
            "-c",
            "color.ui=false",
            "-c",
            "advice.detachedHead=false",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_git_env(),
            **no_window_kwargs(),
        )
    except (OSError, ValueError) as exc:
        return 127, "", str(exc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        proc.kill()
        await proc.wait()
        head = args[0] if args else "command"
        return 124, "", f"git {head} timed out after {timeout:.0f}s"
    return (
        proc.returncode or 0,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


async def _repo_root(start: Path) -> Path | None:
    """The top level of the repository containing ``start``, or None."""
    code, out, _ = await _git(start, ["rev-parse", "--show-toplevel"], timeout=_SHORT_TIMEOUT)
    text = out.strip()
    if code != 0 or not text:
        return None
    return Path(text)


async def _in_progress(root: Path) -> str | None:
    """Name the operation git is midway through, so the agent does not walk into it."""
    code, out, _ = await _git(root, ["rev-parse", "--git-dir"], timeout=_SHORT_TIMEOUT)
    if code != 0:
        return None
    raw = Path(out.strip())
    git_dir = raw if raw.is_absolute() else root / raw
    for marker, label in (
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("BISECT_LOG", "bisect"),
    ):
        if (git_dir / marker).exists():
            return label
    return None


def _tracking_note(state: RepoState) -> str:
    if state.detached or not state.branch:
        return f"detached HEAD at {state.head or 'unknown'}"
    if not state.upstream:
        return "no upstream"
    drift = []
    if state.ahead:
        drift.append(f"{state.ahead} ahead")
    if state.behind:
        drift.append(f"{state.behind} behind")
    if not drift:
        return f"in sync with {state.upstream}"
    return f"{' and '.join(drift)} {state.upstream}"


def _section(title: str, entries: Sequence[str]) -> list[str]:
    if not entries:
        return []
    lines = [f"{title} ({len(entries)}):"]
    lines.extend(f"  {entry}" for entry in entries[:_STATUS_SAMPLE])
    if len(entries) > _STATUS_SAMPLE:
        lines.append(f"  … {len(entries) - _STATUS_SAMPLE} more")
    return lines


def _format_status(state: RepoState, busy: str | None) -> str:
    where = state.branch if state.branch and not state.detached else "HEAD detached"
    lines = [f"branch {where} ({_tracking_note(state)})"]
    if busy:
        lines.append(f"a {busy} is in progress - finish or abort it before editing")
    if not state.dirty:
        lines.append("working tree clean")
        return "\n".join(lines)
    lines.extend(_section("conflicted", state.conflicted))
    lines.extend(_section("staged", state.staged))
    lines.extend(_section("unstaged", state.unstaged))
    lines.extend(_section("untracked", state.untracked))
    return "\n".join(lines)


def _ref_arg(value: str | None) -> str:
    """A branch, remote or commit-ish, refused if it could pass as an option.

    Nothing here goes through a shell, so quoting is not the risk; a value
    starting with a dash being read as a flag is.
    """

    text = (value or "").strip()
    if text.startswith("-"):
        raise ValueError(f"{text!r} is not a valid name")
    return text


def _remote_of(state: RepoState, fallback: str = "origin") -> str:
    """The remote this branch tracks, or ``fallback``."""
    if state.upstream and "/" in state.upstream:
        return state.upstream.split("/", 1)[0]
    return fallback


async def _remotes(root: Path) -> list[str]:
    code, out, _ = await _git(root, ["remote"], timeout=_SHORT_TIMEOUT)
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _clip(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    return (
        f"[truncated: {len(text)} chars of output, showing the first {_MAX_CHARS}. "
        "Narrow it with paths=[...] or stat=true.]\n" + text[:_MAX_CHARS]
    )


def _combined(out: str, err: str) -> str:
    """Git reports success on stdout and progress on stderr; keep both."""
    parts = [part for part in (out.strip(), err.strip()) if part]
    return "\n".join(parts)


_NO_REMOTE = (
    "Error: this repository has no remote. "
    "Use action=remote create=true name=origin remote=<url>."
)

# Settings > Git tokens for this git-tool call. Empty outside execute().
_GIT_FORGE_TOKENS: ContextVar[dict[str, str]] = ContextVar(
    "navin_git_forge_tokens",
    default={},
)


class GitTool(_FsTool):
    """Query history and record work in the repository."""

    _scopes = {"core", "subagent"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return super().enabled(ctx) and _git_binary() is not None

    @classmethod
    def create(cls, ctx: Any) -> Any:
        from navin.agent.tools.exec_env import forge_tokens_from_tools_config

        tool = super().create(ctx)
        tool._forge_tokens = forge_tokens_from_tools_config(ctx.config)
        return tool

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return (
            "The full local git workflow, with parsed output instead of raw "
            "shell text, including GitHub, GitLab and Forgejo remotes. Use this "
            "instead of 'exec git': paths are checked against the workspace, "
            "git can never stop at an editor or a credential prompt, and the "
            "output is stable. Clone, init and remote add work without a shell. "
            "Merge and rebase take step='continue'|'abort'|'skip' for "
            "conflicted runs. Review your own work with diff before you claim "
            "it is done."
        )

    @property
    def read_only(self) -> bool:
        """False overall; see call_concurrency_safe for the query actions."""
        return False

    def call_concurrency_safe(self, arguments: Any) -> bool:
        """Let the reads batch in parallel while the recording actions serialize."""
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") in _QUERY_ACTIONS

    def call_read_only(self, arguments: Any) -> bool:
        """Query actions stay available in Ask and Plan; recording ones do not."""
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") in _QUERY_ACTIONS

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "diff",
                        "log",
                        "show",
                        "blame",
                        "branches",
                        "add",
                        "commit",
                        "restore",
                        "switch",
                        "stash",
                        "stash_list",
                        "stash_pop",
                        "fetch",
                        "push",
                        "pull",
                        "merge",
                        "rebase",
                        "reset",
                        "clone",
                        "init",
                        "remote",
                    ],
                    "description": (
                        "Standard git semantics on GitHub, GitLab, Forgejo and "
                        "any other remote. Notes: restore discards "
                        "changes to named paths (staged=true unstages); pull "
                        "rebases by default; merge merges 'name' into the "
                        "current branch; rebase replays onto 'name'; reset "
                        "moves HEAD to 'commit' with the given mode; clone "
                        "needs remote=URL; remote with create=true adds "
                        "name + remote=URL; init creates a repo in the project"
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Workspace-relative paths scoping the action. Required for "
                        "add and restore, optional for diff, log and show."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Single file, for blame",
                },
                "message": {
                    "type": "string",
                    "description": "Commit or stash message",
                },
                "commit": {
                    "type": "string",
                    "description": (
                        "Commit-ish for show, or the base for diff "
                        "(e.g. 'HEAD~2', a branch name, or a sha)"
                    ),
                },
                "staged": {
                    "type": "boolean",
                    "description": (
                        "diff: show the index instead of the working tree; "
                        "restore: unstage instead of discarding (default false)"
                    ),
                },
                "stat": {
                    "type": "boolean",
                    "description": "Summarize changed files instead of printing the patch",
                },
                "context": {
                    "type": "integer",
                    "description": "Lines of context around each diff hunk (default 3)",
                    "minimum": 0,
                    "maximum": 20,
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of commits for log (default 20)",
                    "minimum": 1,
                    "maximum": 200,
                },
                "line_start": {
                    "type": "integer",
                    "description": "First line for blame (default 1)",
                    "minimum": 1,
                },
                "line_end": {
                    "type": "integer",
                    "description": "Last line for blame",
                    "minimum": 1,
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Branch name: the target for switch and push, the source "
                        "for merge, the base for rebase"
                    ),
                },
                "create": {
                    "type": "boolean",
                    "description": "switch: create the branch rather than expect it (default false)",
                },
                "all": {
                    "type": "boolean",
                    "description": (
                        "commit: stage every tracked modification first (default "
                        "false). Untracked files are never added this way."
                    ),
                },
                "include_untracked": {
                    "type": "boolean",
                    "description": "stash: park untracked files too (default false)",
                },
                "remote": {
                    "type": "string",
                    "description": (
                        "Remote for fetch, push and pull. Defaults to the one the "
                        "branch tracks, else 'origin'."
                    ),
                },
                "step": {
                    "type": "string",
                    "enum": list(_STEPS),
                    "description": (
                        "merge and rebase: 'start' (default) begins it, "
                        "'continue' resumes after you staged the resolved files, "
                        "'abort' returns to the state before it, "
                        "'skip' drops the current commit (rebase only)"
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": list(_RESET_MODES),
                    "description": (
                        "reset: 'soft' keeps the index and tree, 'mixed' (default) "
                        "keeps the tree, 'hard' throws the tree away too"
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "push: overwrite the remote branch with --force-with-lease, "
                        "which still refuses if the remote moved since your last "
                        "fetch. Pass stale=true as well for a plain --force."
                    ),
                },
                "stale": {
                    "type": "boolean",
                    "description": (
                        "push with force=true: skip the lease check and overwrite "
                        "whatever is on the remote (default false)"
                    ),
                },
                "set_upstream": {
                    "type": "boolean",
                    "description": (
                        "push: record the tracking branch. Applied automatically "
                        "when the branch has no upstream yet."
                    ),
                },
                "rebase": {
                    "type": "boolean",
                    "description": (
                        "pull: rebase local commits onto the upstream instead of "
                        "creating a merge commit (default true)"
                    ),
                },
                "squash": {
                    "type": "boolean",
                    "description": (
                        "merge: stage the result as one change without committing "
                        "it (default false)"
                    ),
                },
                "no_verify": {
                    "type": "boolean",
                    "description": (
                        "commit and push: skip the hooks (default false). Use it "
                        "when a hook is broken or irrelevant, not to get past one "
                        "that is telling you something."
                    ),
                },
                "allow_empty": {
                    "type": "boolean",
                    "description": "commit: record a commit even with nothing staged (default false)",
                },
            },
            "required": ["action"],
        }

    async def _repo_relative(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        write: bool,
    ) -> tuple[list[str], str | None]:
        """Resolve workspace paths, then re-express them relative to the repo root."""
        resolved: list[str] = []
        for raw in paths:
            candidate = str(raw).strip()
            if not candidate:
                continue
            try:
                full = await self._bound_path(candidate, write=write)
            except (PermissionError, ValueError, OSError) as exc:
                return [], f"Error: {exc}"
            try:
                rel = Path(full).resolve().relative_to(Path(root).resolve())
            except (ValueError, OSError):
                return [], f"Error: {candidate} is outside the repository at {root}"
            resolved.append(rel.as_posix() or ".")
        return resolved, None

    async def _state(self, root: Path) -> RepoState:
        return await asyncio.to_thread(repo_state, root, refresh=True)

    async def execute(
        self,
        action: str = "status",
        paths: list[str] | None = None,
        path: str | None = None,
        message: str | None = None,
        commit: str | None = None,
        staged: bool = False,
        stat: bool = False,
        context: int = 3,
        limit: int = 20,
        line_start: int | None = None,
        line_end: int | None = None,
        name: str | None = None,
        create: bool = False,
        all: bool = False,
        include_untracked: bool = False,
        remote: str | None = None,
        step: str = "start",
        mode: str = "mixed",
        force: bool = False,
        stale: bool = False,
        set_upstream: bool = False,
        rebase: bool = True,
        squash: bool = False,
        no_verify: bool = False,
        allow_empty: bool = False,
        **kwargs: Any,
    ) -> Any:
        token = _GIT_FORGE_TOKENS.set(getattr(self, "_forge_tokens", {}) or {})
        try:
            return await self._execute_bound(
                action=action,
                paths=paths,
                path=path,
                message=message,
                commit=commit,
                staged=staged,
                stat=stat,
                context=context,
                limit=limit,
                line_start=line_start,
                line_end=line_end,
                name=name,
                create=create,
                all=all,
                include_untracked=include_untracked,
                remote=remote,
                step=step,
                mode=mode,
                force=force,
                stale=stale,
                set_upstream=set_upstream,
                rebase=rebase,
                squash=squash,
                no_verify=no_verify,
                allow_empty=allow_empty,
                **kwargs,
            )
        finally:
            _GIT_FORGE_TOKENS.reset(token)

    async def _execute_bound(
        self,
        action: str = "status",
        paths: list[str] | None = None,
        path: str | None = None,
        message: str | None = None,
        commit: str | None = None,
        staged: bool = False,
        stat: bool = False,
        context: int = 3,
        limit: int = 20,
        line_start: int | None = None,
        line_end: int | None = None,
        name: str | None = None,
        create: bool = False,
        all: bool = False,
        include_untracked: bool = False,
        remote: str | None = None,
        step: str = "start",
        mode: str = "mixed",
        force: bool = False,
        stale: bool = False,
        set_upstream: bool = False,
        rebase: bool = True,
        squash: bool = False,
        no_verify: bool = False,
        allow_empty: bool = False,
        **kwargs: Any,
    ) -> Any:
        if action not in self.parameters["properties"]["action"]["enum"]:
            return self.unknown_action(action)
        if step not in _STEPS:
            return ToolResult.error(
                f"Error: step must be one of {', '.join(_STEPS)}, not {step!r}"
            )
        if mode not in _RESET_MODES:
            return ToolResult.error(
                f"Error: mode must be one of {', '.join(_RESET_MODES)}, not {mode!r}"
            )

        workspace = self._display_workspace() or self._workspace
        if workspace is None:
            return ToolResult.error("Error: no workspace is open")
        if action in {"clone", "init"}:
            root = Path(workspace)
        else:
            root = await _repo_root(Path(workspace))
            if root is None:
                return ToolResult.error(
                    f"Error: {workspace} is not inside a git repository. "
                    "Use action=init, or action=clone remote=<url>."
                )

        try:
            handler = getattr(self, f"_do_{action}")
            result = await handler(
                root,
                paths=paths or [],
                path=path,
                message=message,
                commit=commit,
                staged=staged,
                stat=stat,
                context=context,
                limit=limit,
                line_start=line_start,
                line_end=line_end,
                name=name,
                create=create,
                stage_all=all,
                include_untracked=include_untracked,
                remote=remote,
                step=step,
                mode=mode,
                force=force,
                stale=stale,
                set_upstream=set_upstream,
                rebase=rebase,
                squash=squash,
                no_verify=no_verify,
                allow_empty=allow_empty,
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:
            return ToolResult.error(f"Error running git {action}: {exc}")
        if action not in _QUERY_ACTIONS:
            # The per-turn state block is memoised; a commit or a switch would
            # otherwise be described by the tree as it was before the call.
            clear_cache()
        return result

    # -- queries -----------------------------------------------------------

    async def _do_status(self, root: Path, **_: Any) -> Any:
        state = await self._state(root)
        if not state.is_repo:
            return ToolResult.error(
                f"Error: could not read git status in {root}"
                + (f": {state.unavailable}" if state.unavailable else "")
            )
        return _format_status(state, await _in_progress(root))

    async def _do_diff(
        self,
        root: Path,
        *,
        paths: list[str],
        staged: bool,
        stat: bool,
        context: int,
        commit: str | None,
        **_: Any,
    ) -> Any:
        rel, error = await self._repo_relative(root, paths, write=False)
        if error:
            return ToolResult.error(error)
        args = ["diff", "--find-renames"]
        if staged:
            args.append("--cached")
        # Same dash check as every other ref: "--output=x" as a base would make
        # git diff write a file, sidestepping the workspace path checks.
        base = _ref_arg(commit)
        if base:
            args.append(base)
        args.append("--stat" if stat else f"-U{max(0, min(20, context))}")
        if rel:
            args.extend(["--", *rel])
        code, out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git diff failed'}")
        if not out.strip():
            where = "the index" if staged else "the working tree"
            hint = (
                " Pass staged=false for unstaged work."
                if staged
                else " Pass staged=true for what is already staged."
            )
            return f"No changes in {where}.{hint}"
        return _clip(out)

    async def _do_log(
        self,
        root: Path,
        *,
        paths: list[str],
        limit: int,
        stat: bool,
        **_: Any,
    ) -> Any:
        rel, error = await self._repo_relative(root, paths, write=False)
        if error:
            return ToolResult.error(error)
        args = [
            "log",
            f"-n{max(1, min(200, limit))}",
            "--date=short",
            "--pretty=format:%h %ad %an: %s",
        ]
        if stat:
            args.append("--name-status")
        if rel:
            args.extend(["--", *rel])
        code, out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git log failed'}")
        return _clip(out.strip() or "No commits yet.")

    async def _do_show(
        self,
        root: Path,
        *,
        commit: str | None,
        paths: list[str],
        stat: bool,
        context: int,
        **_: Any,
    ) -> Any:
        rel, error = await self._repo_relative(root, paths, write=False)
        if error:
            return ToolResult.error(error)
        args = [
            "show",
            "--date=short",
            "--pretty=format:%h %ad %an: %s%n",
            _ref_arg(commit) or "HEAD",
            "--stat" if stat else f"-U{max(0, min(20, context))}",
        ]
        if rel:
            args.extend(["--", *rel])
        code, out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git show failed'}")
        return _clip(out)

    async def _do_blame(
        self,
        root: Path,
        *,
        path: str | None,
        paths: list[str],
        line_start: int | None,
        line_end: int | None,
        **_: Any,
    ) -> Any:
        target = path or (paths[0] if paths else None)
        if not target:
            return ToolResult.error("Error: action=blame requires 'path'")
        rel, error = await self._repo_relative(root, [target], write=False)
        if error:
            return ToolResult.error(error)
        args = ["blame", "--date=short", "-w"]
        if line_start or line_end:
            start = max(1, line_start or 1)
            end = line_end or start
            if end < start:
                return ToolResult.error("Error: line_end must be >= line_start")
            args.extend(["-L", f"{start},{end}"])
        args.extend(["--", *rel])
        code, out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git blame failed'}")
        return _clip(out)

    async def _do_branches(self, root: Path, **_: Any) -> Any:
        code, out, err = await _git(
            root,
            [
                "branch",
                "--list",
                "--sort=-committerdate",
                "--format=%(if)%(HEAD)%(then)* %(else)  %(end)%(refname:short)"
                "\t%(upstream:short)\t%(contents:subject)",
            ],
        )
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git branch failed'}")
        return _clip(out.strip() or "No branches yet.")

    async def _do_stash_list(self, root: Path, **_: Any) -> Any:
        code, out, err = await _git(root, ["stash", "list", "--date=short"])
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git stash list failed'}")
        return _clip(out.strip() or "No stashes.")

    # -- recording ---------------------------------------------------------

    async def _do_add(self, root: Path, *, paths: list[str], **_: Any) -> Any:
        if not paths:
            return ToolResult.error(
                "Error: action=add requires 'paths'. Name what to stage rather than "
                "staging whatever happens to be in the tree."
            )
        rel, error = await self._repo_relative(root, paths, write=False)
        if error:
            return ToolResult.error(error)
        code, _out, err = await _git(root, ["add", "--", *rel])
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git add failed'}")
        state = await self._state(root)
        return f"Staged {len(rel)} path(s). {len(state.staged)} path(s) now staged."

    async def _do_commit(
        self,
        root: Path,
        *,
        message: str | None,
        stage_all: bool,
        no_verify: bool,
        allow_empty: bool,
        **_: Any,
    ) -> Any:
        if not (message or "").strip():
            # Without -m git opens an editor, which has nothing to talk to here
            # and would hang until the timeout.
            return ToolResult.error("Error: action=commit requires a non-empty 'message'")
        state = await self._state(root)
        if state.conflicted:
            return ToolResult.error(
                f"Error: {len(state.conflicted)} path(s) are still conflicted; "
                "git refuses to commit with unmerged paths. Resolve them, "
                "action=add them, then commit."
            )
        if not state.staged and not stage_all and not allow_empty:
            return ToolResult.error(
                "Error: nothing is staged. Call action=add with the paths you mean, "
                "all=true to stage every tracked modification, or allow_empty=true "
                "for a commit with no changes."
            )
        args = ["commit"]
        if stage_all:
            args.append("--all")
        if no_verify:
            args.append("--no-verify")
        if allow_empty:
            args.append("--allow-empty")
        args.extend(["-m", str(message)])
        code, out, err = await _git(root, args)
        if code != 0:
            hint = (
                ""
                if no_verify
                else "\nIf a hook rejected this, fix what it reports; no_verify=true skips it."
            )
            return ToolResult.error(f"Error: {_combined(out, err) or 'git commit failed'}{hint}")
        return _clip(_combined(out, err))

    async def _do_restore(
        self,
        root: Path,
        *,
        paths: list[str],
        staged: bool,
        **_: Any,
    ) -> Any:
        if not paths:
            # Bare "git restore" is a no-op in git too; naming the target is the
            # difference between discarding one file and discarding the session.
            return ToolResult.error(
                "Error: action=restore requires 'paths'. Pass ['.'] for the whole "
                "tree if that is what you mean."
            )
        rel, error = await self._repo_relative(root, paths, write=True)
        if error:
            return ToolResult.error(error)
        args = ["restore"]
        if staged:
            args.append("--staged")
        args.extend(["--", *rel])
        code, _out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git restore failed'}")
        what = "Unstaged" if staged else "Discarded changes in"
        return f"{what} {len(rel)} path(s): {', '.join(rel)}"

    async def _do_switch(
        self,
        root: Path,
        *,
        name: str | None,
        create: bool,
        **_: Any,
    ) -> Any:
        target = _ref_arg(name)
        if not target:
            return ToolResult.error("Error: action=switch requires 'name'")
        args = ["switch"]
        if create:
            args.append("-c")
        args.append(target)
        code, out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git switch failed'}")
        state = await self._state(root)
        head = _combined(out, err) or f"On branch {target}"
        return f"{head}\n{_format_status(state, await _in_progress(root))}"

    async def _do_stash(
        self,
        root: Path,
        *,
        message: str | None,
        include_untracked: bool,
        **_: Any,
    ) -> Any:
        args = ["stash", "push"]
        if include_untracked:
            args.append("--include-untracked")
        if (message or "").strip():
            args.extend(["-m", str(message).strip()])
        code, out, err = await _git(root, args)
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git stash failed'}")
        return _clip(_combined(out, err))

    async def _do_stash_pop(self, root: Path, **_: Any) -> Any:
        code, out, err = await _git(root, ["stash", "pop"])
        if code != 0:
            return ToolResult.error(f"Error: {err.strip() or 'git stash pop failed'}")
        return _clip(_combined(out, err))

    # -- moving history ----------------------------------------------------

    async def _do_fetch(self, root: Path, *, remote: str | None, **_: Any) -> Any:
        state = await self._state(root)
        target = _ref_arg(remote) or _remote_of(state)
        available = await _remotes(root)
        if not available:
            return ToolResult.error(_NO_REMOTE)
        if target not in available:
            return ToolResult.error(
                f"Error: no remote named {target!r}. Available: {', '.join(available)}"
            )
        code, out, err = await _git(
            root, ["fetch", "--prune", target], timeout=_NETWORK_TIMEOUT
        )
        if code != 0:
            return ToolResult.error(f"Error: {_combined(out, err) or 'git fetch failed'}")
        after = await self._state(root)
        moved = _combined(out, err) or f"{target} was already up to date."
        return _clip(f"{moved}\nbranch {after.branch or 'HEAD'} ({_tracking_note(after)})")

    async def _do_init(self, root: Path, **_: Any) -> Any:
        existing = await _repo_root(root)
        if existing is not None:
            return f"Already a git repository at {existing}."
        code, out, err = await _git(root, ["init", "-b", "main"])
        if code != 0:
            code, out, err = await _git(root, ["init"])
        if code != 0:
            return ToolResult.error(f"Error: {_combined(out, err) or 'git init failed'}")
        return _clip(_combined(out, err) or f"Initialized git repository in {root}.")

    async def _do_clone(
        self,
        root: Path,
        *,
        remote: str | None,
        path: str | None,
        name: str | None,
        **_: Any,
    ) -> Any:
        url = (remote or name or "").strip()
        if not url:
            return ToolResult.error(
                "Error: action=clone needs remote=<git URL> "
                "(GitHub, GitLab, Forgejo, or any git remote)."
            )
        dest_name = (path or "").strip()
        if not dest_name:
            dest_name = _clone_folder_name(url)
        dest = root if dest_name in {".", "./"} else (root / dest_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        code, out, err = await _git(
            root, ["clone", "--", url, str(dest)], timeout=_NETWORK_TIMEOUT
        )
        if code != 0:
            return ToolResult.error(f"Error: {_combined(out, err) or 'git clone failed'}")
        return _clip(_combined(out, err) or f"Cloned {url} into {dest}.")

    async def _do_remote(
        self,
        root: Path,
        *,
        name: str | None,
        remote: str | None,
        create: bool,
        **_: Any,
    ) -> Any:
        if not create:
            code, out, err = await _git(root, ["remote", "-v"])
            if code != 0:
                return ToolResult.error(f"Error: {_combined(out, err) or 'git remote failed'}")
            return _clip(out.strip() or "No remotes. action=remote create=true name=origin remote=<url> to add one.")
        label = (name or "origin").strip() or "origin"
        url = (remote or "").strip()
        if not url:
            return ToolResult.error(
                "Error: adding a remote needs name (default origin) and remote=<url>."
            )
        code, out, err = await _git(root, ["remote", "add", label, url])
        if code != 0:
            text = _combined(out, err)
            if "already exists" in text.lower():
                code, out, err = await _git(root, ["remote", "set-url", label, url])
                if code != 0:
                    return ToolResult.error(
                        f"Error: {_combined(out, err) or 'git remote set-url failed'}"
                    )
                listed = await _git(root, ["remote", "-v"])
                return _clip(
                    f"Updated remote {label} -> {url}.\n{listed[1].strip()}"
                )
            return ToolResult.error(f"Error: {text or 'git remote add failed'}")
        listed = await _git(root, ["remote", "-v"])
        return _clip(
            _combined(out, err)
            or f"Added remote {label} -> {url}.\n{listed[1].strip()}"
        )

    async def _do_push(
        self,
        root: Path,
        *,
        remote: str | None,
        name: str | None,
        force: bool,
        stale: bool,
        set_upstream: bool,
        no_verify: bool,
        **_: Any,
    ) -> Any:
        state = await self._state(root)
        branch = _ref_arg(name) or state.branch
        if not branch:
            return ToolResult.error(
                "Error: HEAD is detached, so there is no branch to push. "
                "Switch to one first, or name it with 'name'."
            )
        target = _ref_arg(remote) or _remote_of(state)
        available = await _remotes(root)
        if not available:
            return ToolResult.error(_NO_REMOTE)
        if target not in available:
            return ToolResult.error(
                f"Error: no remote named {target!r}. Available: {', '.join(available)}"
            )

        args = ["push"]
        if force:
            # --force-with-lease is the same overwrite, minus the case where it
            # would silently drop a commit somebody else pushed meanwhile.
            args.append("--force" if stale else "--force-with-lease")
            refusal = await _ask_before_discarding(
                action=f"Overwrite the remote branch {target}/{branch}",
                reason=(
                    "A force push rewrites published history, which is the one "
                    "thing other people's clones cannot follow."
                    + (
                        " stale=true also skips the lease check, so it overwrites "
                        "commits pushed by someone else."
                        if stale
                        else ""
                    )
                ),
                detail=f"$ git {' '.join(args)} {target} {branch}",
                consequence=(
                    "Commits only present on the remote are dropped, and anyone "
                    "who pulled the old history has to recover by hand."
                ),
                scope=None if stale else "git:push-force",
            )
            if refusal is not None:
                return refusal
        if set_upstream or not state.upstream:
            args.append("--set-upstream")
        if no_verify:
            args.append("--no-verify")
        args.extend([target, branch])
        code, out, err = await _git(root, args, timeout=_NETWORK_TIMEOUT)
        text = _combined(out, err)
        if code != 0:
            hint = ""
            if "rejected" in text and "fetch first" in text:
                hint = (
                    "\nThe remote has commits you do not. action=pull to integrate "
                    "them, or force=true to overwrite the remote branch."
                )
            elif "stale info" in text:
                hint = "\naction=fetch first, then push again with force=true."
            return ToolResult.error(f"Error: {text or 'git push failed'}{hint}")
        return _clip(text or f"Pushed {branch} to {target}.")

    async def _do_pull(
        self,
        root: Path,
        *,
        remote: str | None,
        name: str | None,
        rebase: bool,
        **_: Any,
    ) -> Any:
        state = await self._state(root)
        if state.detached:
            return ToolResult.error("Error: HEAD is detached; switch to a branch before pulling.")
        if busy := await _in_progress(root):
            return ToolResult.error(
                f"Error: a {busy} is already in progress. Finish it with "
                f"step=continue or back out with step=abort before pulling."
            )
        target = _ref_arg(remote) or _remote_of(state)
        branch = _ref_arg(name)
        args = ["pull", "--rebase" if rebase else "--no-rebase"]
        if branch or not state.upstream:
            args.extend([target, branch or state.branch])
        code, out, err = await _git(root, args, timeout=_NETWORK_TIMEOUT)
        text = _combined(out, err)
        if code != 0:
            return await self._conflict_report(
                root, "pull", text or "git pull failed", rebasing=rebase
            )
        after = await self._state(root)
        return _clip(f"{text}\nbranch {after.branch or 'HEAD'} ({_tracking_note(after)})")

    async def _do_merge(
        self,
        root: Path,
        *,
        name: str | None,
        commit: str | None,
        step: str,
        squash: bool,
        message: str | None,
        **_: Any,
    ) -> Any:
        if step == "skip":
            return ToolResult.error("Error: step=skip applies to rebase, not merge")
        if step in {"continue", "abort"}:
            return await self._resume("merge", root, step)

        source = _ref_arg(name) or _ref_arg(commit)
        if not source:
            return ToolResult.error("Error: action=merge requires 'name', the branch to merge in")
        if busy := await _in_progress(root):
            return ToolResult.error(
                f"Error: a {busy} is already in progress. step=continue to finish "
                "it or step=abort to back out."
            )
        args = ["merge", "--no-edit"]
        if squash:
            args.append("--squash")
        if (message or "").strip():
            args.extend(["-m", str(message).strip()])
        args.append(source)
        code, out, err = await _git(root, args)
        text = _combined(out, err)
        if code != 0:
            return await self._conflict_report(root, "merge", text or "git merge failed")
        state = await self._state(root)
        note = (
            "\nStaged as one change; action=commit to record it."
            if squash
            else ""
        )
        return _clip(f"{text or f'Merged {source}.'}{note}\n{_format_status(state, None)}")

    async def _do_rebase(
        self,
        root: Path,
        *,
        name: str | None,
        commit: str | None,
        step: str,
        **_: Any,
    ) -> Any:
        if step in {"continue", "abort", "skip"}:
            return await self._resume("rebase", root, step)

        onto = _ref_arg(name) or _ref_arg(commit)
        if not onto:
            return ToolResult.error(
                "Error: action=rebase requires 'name', the branch to replay onto"
            )
        if busy := await _in_progress(root):
            return ToolResult.error(
                f"Error: a {busy} is already in progress. step=continue, step=skip "
                "or step=abort first."
            )
        state = await self._state(root)
        if state.staged or state.unstaged or state.conflicted:
            return ToolResult.error(
                "Error: git refuses to rebase with uncommitted changes. "
                "action=commit them, or action=stash and stash_pop afterwards."
            )
        code, out, err = await _git(root, ["rebase", onto])
        text = _combined(out, err)
        if code != 0:
            return await self._conflict_report(root, "rebase", text or "git rebase failed")
        after = await self._state(root)
        return _clip(f"{text or f'Rebased onto {onto}.'}\n{_format_status(after, None)}")

    async def _do_reset(
        self,
        root: Path,
        *,
        commit: str | None,
        mode: str,
        paths: list[str],
        **_: Any,
    ) -> Any:
        if paths:
            # "git reset -- <paths>" unstages; that is what restore staged=true
            # is for, and mixing the two spellings under one action reads as if
            # mode applied to the paths, which it does not.
            return ToolResult.error(
                "Error: action=reset moves HEAD and takes no 'paths'. "
                "Use action=restore staged=true paths=[...] to unstage instead."
            )
        target = _ref_arg(commit) or "HEAD"
        before = await self._state(root)
        if mode == "hard" and before.tracked_changes:
            refusal = await _ask_before_discarding(
                action="Throw away uncommitted changes with git reset --hard",
                reason=(
                    f"{len(before.tracked_changes)} tracked file(s) have changes "
                    "that are in neither a commit nor a checkpoint."
                ),
                detail=f"$ git reset --hard {target}",
                consequence="The working tree is overwritten; those changes are gone.",
            )
            if refusal is not None:
                return refusal
        code, out, err = await _git(root, ["reset", f"--{mode}", target])
        text = _combined(out, err)
        if code != 0:
            return ToolResult.error(f"Error: {text or 'git reset failed'}")
        after = await self._state(root)
        lines = [text or f"HEAD is now at {target} (--{mode})."]
        if mode == "hard":
            # The one outcome worth spelling out: this is the call that made
            # uncommitted work stop existing, and the transcript should say so.
            lost = len(before.tracked_changes) - len(after.tracked_changes)
            if lost > 0:
                lines.append(f"Discarded uncommitted changes in {lost} tracked path(s).")
        lines.append(_format_status(after, await _in_progress(root)))
        return _clip("\n".join(lines))

    async def _resume(self, operation: str, root: Path, step: str) -> Any:
        """Finish or back out of a merge or rebase that stopped on a conflict."""
        busy = await _in_progress(root)
        if busy is None:
            return ToolResult.error(
                f"Error: no {operation} is in progress, so there is nothing to "
                f"{step}. action=status shows where the tree stands."
            )
        if step == "continue":
            state = await self._state(root)
            if state.conflicted:
                return ToolResult.error(
                    f"Error: {len(state.conflicted)} path(s) are still conflicted: "
                    f"{', '.join(state.conflicted[:_STATUS_SAMPLE])}. "
                    "Edit them, action=add them, then step=continue."
                )
        code, out, err = await _git(root, [operation, f"--{step}"])
        text = _combined(out, err)
        if code != 0:
            return await self._conflict_report(root, operation, text or f"git {operation} failed")
        after = await self._state(root)
        done = text or f"{operation} --{step} done."
        return _clip(f"{done}\n{_format_status(after, await _in_progress(root))}")

    async def _conflict_report(
        self,
        root: Path,
        operation: str,
        text: str,
        *,
        rebasing: bool = False,
    ) -> Any:
        """Turn a stopped merge/rebase/pull into instructions, not just a failure.

        A conflict is the expected outcome of half these calls, and the agent's
        next move depends on which files stopped it - so the paths and the exact
        follow-up call go in the same result as the error.
        """

        state = await self._state(root)
        busy = await _in_progress(root)
        if not state.conflicted and busy is None:
            return ToolResult.error(f"Error: {text}")
        step_owner = busy or ("rebase" if rebasing else operation)
        lines = [f"Error: {text}", ""]
        if state.conflicted:
            lines.extend(_section("conflicted", state.conflicted))
        lines.append(
            f"Resolve those files, action=add them, then "
            f"action={step_owner} step=continue. "
            f"action={step_owner} step=abort returns to the previous state."
        )
        return ToolResult.error("\n".join(lines))
