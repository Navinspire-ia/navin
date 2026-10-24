# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Land a Multitask subagent's isolated work in the user's tree, or say why not.

Each Multitask prompt runs in its own git worktree (see :mod:`worktree`) so
parallel tasks cannot trample each other. Until now the work then sat there
with a "review and merge" note the user never saw, because the announcement
asked the model to keep technical details out of its summary. The work was
done, and nobody knew where it was.

This module closes the loop. When an isolated subagent finishes successfully:

1. its changes are committed in its checkout (detached HEAD, never a branch
   the user has checked out);
2. under a per-repository lock, so concurrent subagents land one at a time,
   the merge is computed without touching the user's tree
   (``git merge-tree --write-tree``);
3. only when the merge is conflict-free and none of the touched files has
   uncommitted edits in the user's tree is it applied, with
   ``git read-tree -m -u`` (which refuses, on its own, to overwrite a local
   change) and a compare-and-swap ``update-ref`` of HEAD.

Anything else leaves the user's tree exactly as it was: the checkout is kept,
its commit gets a branch so it can be found, and a :class:`WorktreeMergeEvent`
offers Resolve, Keep separate or Discard. No step here uses a force flag on
the user's repository; the only destructive operations are the Discard the
user explicitly asks for, and they touch the subagent's checkout and branch
only.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent import worktree
from navin.utils.proc import no_window_kwargs

_TIMEOUT_S = 120.0
_LIST_LIMIT = 20
BRANCH_PREFIX = "navin/multitask/"

# Stop reasons that mean the subagent did not finish its task: its partial
# work is kept in the checkout and never merged on its own.
FAILED_STOPS = frozenset({
    "tool_error", "max_iterations", "no_progress", "validation_failed", "error",
})

# Fallback identity for the commits made here, only used when the repository
# has none configured (a fresh machine): git refuses to commit otherwise.
_FALLBACK_NAME = "Navin"
_FALLBACK_EMAIL = "navin@localhost"

_repo_locks: dict[str, threading.Lock] = {}
_repo_locks_guard = threading.Lock()

_pending: dict[str, PendingMerge] = {}
_pending_guard = threading.Lock()


@dataclass(frozen=True, slots=True)
class MergeResult:
    """The outcome of one attempt to land a checkout's commit."""

    status: str  # merged | conflict | blocked | unknown | nothing
    files: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    reason: str = ""  # conflict | uncommitted | in_progress | unsupported | git
    commit: str = ""
    merge_commit: str = ""

    @property
    def landed(self) -> bool:
        return self.status == "merged"


@dataclass(slots=True)
class PendingMerge:
    """A checkout whose work could not land, waiting for the user's decision."""

    task_id: str
    label: str
    session_key: str
    checkout: worktree.Worktree
    commit: str
    branch: str
    files: tuple[str, ...]
    conflicts: tuple[str, ...]
    reason: str
    origin: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "label": self.label,
            "status": "blocked" if self.reason == "uncommitted" else (
                "conflict" if self.reason == "conflict" else "unknown"
            ),
            "files": list(self.files),
            "conflicts": list(self.conflicts),
            "reason": self.reason,
            "branch": self.branch,
            "resolve_prompt": resolve_prompt(self),
        }


# -- git plumbing -------------------------------------------------------------


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_S,
            env=env,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("worktree_merge: git {} failed: {}", " ".join(args[:3]), exc)
        return None


def _ok(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str | None:
    done = _run(args, cwd=cwd, env=env)
    if done is None:
        return None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        logger.info(
            "worktree_merge: git {} exited {}: {}",
            " ".join(args[:3]), done.returncode, detail[0][:200] if detail else "",
        )
        return None
    return done.stdout


def _identity_env(repo: Path) -> dict[str, str]:
    """The process env, plus a committer identity when the repo has none."""
    env = dict(os.environ)
    if not (_ok(["config", "--get", "user.email"], cwd=repo) or "").strip():
        env.setdefault("GIT_AUTHOR_EMAIL", _FALLBACK_EMAIL)
        env.setdefault("GIT_COMMITTER_EMAIL", _FALLBACK_EMAIL)
    if not (_ok(["config", "--get", "user.name"], cwd=repo) or "").strip():
        env.setdefault("GIT_AUTHOR_NAME", _FALLBACK_NAME)
        env.setdefault("GIT_COMMITTER_NAME", _FALLBACK_NAME)
    return env


def _repo_lock(origin: Path) -> threading.Lock:
    try:
        key = str(origin.resolve())
    except OSError:
        key = str(origin)
    with _repo_locks_guard:
        lock = _repo_locks.get(key)
        if lock is None:
            lock = _repo_locks[key] = threading.Lock()
        return lock


def _split_z(output: str) -> list[str]:
    return [part for part in output.split("\0") if part]


def _dirty_paths(origin: Path) -> set[str] | None:
    """Every path with uncommitted state in the user's tree, repo-relative."""
    out = _ok(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=origin)
    if out is None:
        return None
    paths: set[str] = set()
    parts = out.split("\0")
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        paths.add(path)
        if "R" in code or "C" in code:
            # -z puts a rename's source in the next field, without a status.
            if index < len(parts) and parts[index]:
                paths.add(parts[index])
            index += 1
    return paths


def _operation_in_progress(origin: Path) -> bool:
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        out = _ok(["rev-parse", "--git-path", marker], cwd=origin)
        if out is None:
            return True  # cannot tell: be conservative
        path = Path(out.strip())
        if not path.is_absolute():
            path = origin / path
        if path.exists():
            return True
    return False


def _safe_label(label: str) -> str:
    text = " ".join((label or "").split())[:72]
    return text or "multitask"


def commit_checkout(checkout: worktree.Worktree, label: str) -> str | None:
    """Commit everything the subagent left in its checkout; return the tip.

    Returns None when there is nothing tracked to land (only ignored output,
    or git failed), in which case the caller keeps the checkout as before.
    """
    path = checkout.path
    if _ok(["add", "-A"], cwd=path) is None:
        return None
    staged = _run(["diff", "--cached", "--quiet"], cwd=path)
    if staged is None:
        return None
    if staged.returncode == 1:
        message = f"Multitask: {_safe_label(label)}"
        committed = _ok(
            ["-c", "commit.gpgsign=false", "commit", "--no-verify", "-q", "-m", message],
            cwd=path, env=_identity_env(path),
        )
        if committed is None:
            return None
    elif staged.returncode != 0:
        return None
    head = _ok(["rev-parse", "HEAD"], cwd=path)
    if head is None:
        return None
    head = head.strip()
    if head == checkout.base:
        return None
    return head


def _touched(origin: Path, base: str, commit: str) -> tuple[str, ...] | None:
    out = _ok(["diff", "--name-only", "--no-renames", "-z", base, commit], cwd=origin)
    if out is None:
        return None
    return tuple(sorted(set(_split_z(out))))


def merge_commit_into_origin(
    checkout: worktree.Worktree,
    commit: str,
    label: str,
) -> MergeResult:
    """Merge ``commit`` into the user's HEAD and tree, or change nothing.

    Serialised per repository: two subagents finishing together land one after
    the other, the second on top of the first's merge.
    """
    origin = checkout.origin
    with _repo_lock(origin):
        files = _touched(origin, checkout.base, commit)
        if files is None:
            return MergeResult("unknown", reason="git", commit=commit)
        if not files:
            return MergeResult("nothing", commit=commit)
        if _operation_in_progress(origin):
            return MergeResult("blocked", files, reason="in_progress", commit=commit)

        dirty = _dirty_paths(origin)
        if dirty is None:
            return MergeResult("unknown", files, reason="git", commit=commit)
        clashing = tuple(path for path in files if path in dirty)
        if clashing:
            return MergeResult("blocked", files, clashing, reason="uncommitted", commit=commit)

        head = _ok(["rev-parse", "--verify", "HEAD"], cwd=origin)
        if head is None:
            return MergeResult("unknown", files, reason="git", commit=commit)
        head = head.strip()

        # Computed entirely in the object store: the user's index and files
        # are not read or written by this step.
        trial = _run(
            ["merge-tree", "--write-tree", "--name-only", "--no-messages", head, commit],
            cwd=origin,
        )
        if trial is None or trial.returncode not in (0, 1):
            return MergeResult("unknown", files, reason="unsupported", commit=commit)
        lines = [line for line in trial.stdout.splitlines() if line.strip()]
        if trial.returncode == 1:
            conflicts = tuple(sorted(set(lines[1:])))
            return MergeResult("conflict", files, conflicts or files, reason="conflict", commit=commit)
        if not lines:
            return MergeResult("unknown", files, reason="git", commit=commit)
        tree = lines[0].strip()

        env = _identity_env(origin)
        merged = _ok(
            [
                "commit-tree", tree, "-p", head, "-p", commit,
                "-m", f"Merge Multitask: {_safe_label(label)}",
            ],
            cwd=origin, env=env,
        )
        if merged is None:
            return MergeResult("unknown", files, reason="git", commit=commit)
        merged = merged.strip()

        # Two-tree merge of the index and files from HEAD to the merge commit.
        # git checks every path it would write and stops before writing any
        # if one has a local change or an untracked file is in the way.
        applied = _run(["read-tree", "-m", "-u", head, merged], cwd=origin)
        if applied is None or applied.returncode != 0:
            detail = (applied.stderr if applied else "") or ""
            logger.info("worktree_merge: read-tree refused: {}", detail.strip()[:300])
            return MergeResult("blocked", files, reason="uncommitted", commit=commit)

        # Compare-and-swap: only moves HEAD if it still points where it did.
        moved = _ok(
            ["update-ref", "-m", f"navin: merge Multitask {_safe_label(label)}", "HEAD", merged, head],
            cwd=origin,
        )
        if moved is None:
            # HEAD moved under us (the user committed in that instant). Put the
            # tree back the way it came, again refusing to clobber anything.
            _run(["read-tree", "-m", "-u", merged, head], cwd=origin)
            return MergeResult("unknown", files, reason="git", commit=commit)
        return MergeResult("merged", files, commit=commit, merge_commit=merged)


def _keep_branch(checkout: worktree.Worktree, commit: str) -> str:
    """A branch naming the kept commit, so it can be found from the user's repo."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", checkout.task_id).strip("-.") or "task"
    for attempt in range(20):
        name = f"{BRANCH_PREFIX}{stem}" + (f"-{attempt + 1}" if attempt else "")
        if _ok(["branch", name, commit], cwd=checkout.origin) is not None:
            return name
    return ""


# -- after a run --------------------------------------------------------------


def _render_files(paths: tuple[str, ...]) -> str:
    lines = [f"  {path}" for path in paths[:_LIST_LIMIT]]
    if len(paths) > _LIST_LIMIT:
        lines.append(f"  ... and {len(paths) - _LIST_LIMIT} more")
    return "\n".join(lines)


def merged_note(result: MergeResult) -> str:
    count = len(result.files)
    return (
        f"\n\nMerged automatically into the user's working tree: {count} "
        f"file{'s' if count != 1 else ''} changed.\n{_render_files(result.files)}"
    )


def _why(reason: str, count: int) -> str:
    if reason == "uncommitted":
        return (
            f"{count} file{'s' if count != 1 else ''} also "
            f"{'have' if count != 1 else 'has'} uncommitted edits in the user's tree"
        )
    if reason == "conflict":
        return f"{count} file{'s' if count != 1 else ''} conflict with the user's tree"
    if reason == "in_progress":
        return "another merge or rebase is in progress in the user's repository"
    return "git could not confirm the merge was safe"


def integrate_note(
    label: str, branch: str, files: tuple[str, ...], conflicts: tuple[str, ...], reason: str,
) -> str:
    """The parent agent's instruction to land the work itself, now."""
    shown = conflicts or files
    return (
        f"\n\nNot merged automatically because {_why(reason, len(shown))}:\n"
        f"{_render_files(shown)}\n"
        f"Nothing in the user's tree was changed yet. The work of \"{_safe_label(label)}\" "
        f"is committed on branch {branch}.\n"
        f"Integrate it now yourself, without asking the user: bring the changes of branch "
        f"{branch} into the working tree, resolve every conflict so both the user's edits "
        "and these changes are kept, never discard or overwrite the user's uncommitted "
        "work, check the result, delete the branch once its changes are in, then tell the "
        "user which files you merged."
    )


# Not the user's decision: the agent that asked for the work merges it. An
# operation the user has in progress (merge, rebase) stays theirs to finish.
_AGENT_INTEGRATES = frozenset({"conflict", "uncommitted", "git", "unsupported"})


def attention_note(pending: PendingMerge) -> str:
    count = len(pending.conflicts) or len(pending.files)
    why = _why(pending.reason, count)
    shown = pending.conflicts or pending.files
    return (
        f"\n\nNeeds attention: these changes could not be merged automatically "
        f"because {why}:\n{_render_files(shown)}\n"
        "Nothing in the user's tree was changed. The work is kept separately"
        + (f" on branch {pending.branch}" if pending.branch else "")
        + ". The user can choose Resolve, Keep separate or Discard."
    )


def resolve_prompt(pending: PendingMerge) -> str:
    shown = ", ".join((pending.conflicts or pending.files)[:_LIST_LIMIT])
    where = f"branch {pending.branch}" if pending.branch else f"commit {pending.commit[:12]}"
    return (
        f'Please merge the changes from "{_safe_label(pending.label)}" ({where}) '
        "into my working tree. They could not be merged automatically"
        + (f" ({shown})" if shown else "")
        + ". Resolve the conflicts so both my edits and these changes are kept, "
        "never discard my uncommitted work, then tell me what you merged."
    )


def _register(pending: PendingMerge) -> None:
    with _pending_guard:
        _pending[pending.task_id] = pending


def pending_for_session(session_key: str) -> list[dict[str, Any]]:
    with _pending_guard:
        items = [p for p in _pending.values() if p.session_key == session_key]
    return [p.describe() for p in items]


def get_pending(task_id: str) -> PendingMerge | None:
    with _pending_guard:
        return _pending.get(task_id)


def apply_action(task_id: str, action: str, *, session_key: str | None = None) -> dict[str, Any]:
    """The user's answer to a merge card: resolve | keep | discard.

    ``session_key`` must match the session the work belongs to when given, so
    one conversation cannot discard another's checkout.
    """
    with _pending_guard:
        pending = _pending.get(task_id)
        if pending is None or (session_key and pending.session_key != session_key):
            return {"ok": False, "error": "This change is no longer waiting."}
        if action not in {"resolve", "keep", "discard"}:
            return {"ok": False, "error": "Unknown action."}
        _pending.pop(task_id, None)
    base = {"ok": True, "task_id": task_id, "label": pending.label, "branch": pending.branch}
    if action == "keep":
        return {**base, "status": "kept"}
    if action == "resolve":
        return {**base, "status": "resolving", "resolve_prompt": resolve_prompt(pending)}
    # Discard: the user asked to drop the subagent's work. Only its own
    # checkout and branch are touched.
    worktree.remove(pending.checkout)
    if pending.branch:
        _ok(["branch", "-D", pending.branch], cwd=pending.checkout.origin)
    return {**base, "status": "discarded"}


def _publish(bus: Any, origin: dict[str, Any], event: Any) -> None:
    from navin.bus.outbound_events import outbound_message_for_event

    channel = str(origin.get("channel") or "")
    if channel not in {"websocket", "cli"}:
        return
    outbound = getattr(bus, "outbound", None)
    if outbound is None:
        return
    try:
        outbound.put_nowait(outbound_message_for_event(
            channel=channel, chat_id=str(origin.get("chat_id") or ""), event=event,
        ))
    except Exception:  # noqa: BLE001 - the announcement still carries the story
        logger.debug("worktree_merge: could not publish merge event")


def event_for(task_id: str, label: str, status: str, **fields: Any) -> Any:
    from navin.bus.outbound_events import WorktreeMergeEvent

    return WorktreeMergeEvent(task_id=task_id, label=label, status=status, **fields)


def land(checkout: worktree.Worktree, label: str) -> tuple[MergeResult, str] | None:
    """Commit then merge; None when there was nothing tracked to land."""
    commit = commit_checkout(checkout, label)
    if commit is None:
        return None
    return merge_commit_into_origin(checkout, commit, label), commit


async def settle_after_run(
    manager: Any,
    checkout: worktree.Worktree | None,
    *,
    ok: bool,
    task_id: str,
    label: str,
    origin: dict[str, Any],
) -> str:
    """Land a finished isolated run, or fall back to keeping its checkout.

    Returns the note appended to the subagent's result. A failed run, a
    checkout git cannot read, or one with only ignored output keeps the old
    behaviour (``manager._settle_worktree``): partial work is never merged.
    """
    if checkout is None:
        return ""
    if not ok:
        return await manager._settle_worktree(checkout)
    try:
        changes = await asyncio.to_thread(checkout.inspect)
    except Exception:  # noqa: BLE001
        changes = None
    if changes is None or changes.empty:
        return await manager._settle_worktree(checkout)
    try:
        landed = await asyncio.to_thread(land, checkout, label)
    except Exception:  # noqa: BLE001 - never lose the work over a merge bug
        logger.exception("worktree_merge: landing [{}] failed", task_id)
        landed = None
    if landed is None:
        return await manager._settle_worktree(checkout)
    result, commit = landed
    if result.status in {"merged", "nothing"}:
        if result.landed:
            logger.info("worktree_merge: [{}] merged {} file(s)", task_id, len(result.files))
            _publish(manager.bus, origin, event_for(
                task_id, label, "merged", files=list(result.files),
            ))
        await asyncio.to_thread(
            worktree.release, checkout,
            pool_size=getattr(manager, "max_concurrent_subagents", 0),
        )
        return merged_note(result) if result.landed else ""

    branch = await asyncio.to_thread(_keep_branch, checkout, commit)
    if branch and result.reason in _AGENT_INTEGRATES:
        # The commit is safe on its branch: the checkout can go, and the
        # parent agent integrates the work in its next step. No card, no
        # question to the user.
        await asyncio.to_thread(worktree.remove, checkout)
        logger.info(
            "worktree_merge: [{}] handed to the agent ({}): {} file(s) on {}",
            task_id, result.reason, len(result.conflicts or result.files), branch,
        )
        _publish(manager.bus, origin, event_for(
            task_id, label, "resolving", files=list(result.files),
            conflicts=list(result.conflicts), reason=result.reason, branch=branch,
        ))
        return integrate_note(label, branch, result.files, result.conflicts, result.reason)
    pending = PendingMerge(
        task_id=task_id,
        label=label,
        session_key=str(origin.get("session_key") or f"{origin.get('channel')}:{origin.get('chat_id')}"),
        checkout=checkout,
        commit=commit,
        branch=branch,
        files=result.files,
        conflicts=result.conflicts,
        reason=result.reason,
        origin=dict(origin),
    )
    _register(pending)
    logger.info(
        "worktree_merge: [{}] kept separate ({}): {} file(s)",
        task_id, result.reason, len(result.conflicts or result.files),
    )
    described = pending.describe()
    _publish(manager.bus, origin, event_for(
        task_id, label, described["status"],
        files=described["files"], conflicts=described["conflicts"],
        reason=described["reason"], branch=described["branch"],
        resolve_prompt=described["resolve_prompt"],
    ))
    return attention_note(pending)


def clear_pending() -> None:
    """Forget every pending card. For tests."""
    with _pending_guard:
        _pending.clear()
