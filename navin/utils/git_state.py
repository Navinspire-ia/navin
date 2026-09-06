"""What the working tree looks like right now, read cheaply and read-only.

The agent could always run ``git status`` through ``exec``, and the git skill
tells it how. The problem is that nothing ever told it to: an agent that has not
thought to ask believes the tree is clean, and rewrites a file whose uncommitted
changes it never saw. Losing work that was never committed is the one mistake no
checkpoint here can undo, because the checkpoint store only knows the states the
agent itself created.

So the state is read once per turn and handed to the model unasked. Everything
in this module is read-only; mutations stay with ``exec`` and the git skill,
where the safety rules already live.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from navin.utils import wsl
from navin.utils.git_argv import git_route
from navin.utils.native import native as _native
from navin.utils.proc import no_window_kwargs

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

_TIMEOUT_S = 5.0
# The context provider runs before the very first model token of every turn,
# so nothing here may hold a turn hostage: past this bound the git block is
# simply skipped and the turn goes on without it.
_PROVIDER_TIMEOUT_S = 2.5
# Shared across the parallel git + context-pack readers of one turn, and
# reused for the next few seconds so a greeting does not pay git status twice.
_TTL_S = 15.0
_MAX_NAMES = 5
_cache_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}

# Index of the path within a changed-entry record, counting from the field
# after the record marker. Ordinary entries carry XY sub mH mI mW hH hI before
# it, renames add a score, and unmerged entries carry three stages of each.
_ORDINARY_PATH_FIELD = 7
_RENAME_PATH_FIELD = 8
_U_PATH_FIELD = 9

_cache: dict[str, tuple[float, "RepoState"]] = {}


@dataclass(frozen=True)
class RepoState:
    """A snapshot of the working tree, as porcelain v2 reports it."""

    is_repo: bool = False
    branch: str = ""
    detached: bool = False
    head: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    staged: tuple[str, ...] = ()
    unstaged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    conflicted: tuple[str, ...] = ()
    unavailable: str = ""

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.unstaged or self.untracked or self.conflicted)

    @property
    def tracked_changes(self) -> frozenset[str]:
        """Paths with committed history that differ from HEAD.

        Untracked files are excluded: they have no committed version, so
        overwriting one is not the same kind of loss.
        """
        return frozenset((*self.staged, *self.unstaged, *self.conflicted))


def repo_state(root: Path | str, *, refresh: bool = False) -> RepoState:
    """Read the working tree state, memoised and single-flight.

    Two providers used to launch ``git status`` in parallel on the same
    tree, miss the 2s cache, and stall the first token for the sum of both.
    """
    key = str(Path(root).expanduser())
    now = time.monotonic()
    if not refresh:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < _TTL_S:
            return cached[1]
    with _cache_lock:
        if not refresh:
            cached = _cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < _TTL_S:
                return cached[1]
        waiter = _inflight.get(key)
        mine = False
        if waiter is None:
            waiter = threading.Event()
            _inflight[key] = waiter
            mine = True
    if not mine:
        waiter.wait(timeout=_TIMEOUT_S + 1.0)
        cached = _cache.get(key)
        if cached is not None:
            return cached[1]
        return RepoState(unavailable="git status already running")
    try:
        state = _read(Path(key))
        _cache[key] = (time.monotonic(), state)
        return state
    finally:
        with _cache_lock:
            _inflight.pop(key, None)
        waiter.set()


def clear_cache() -> None:
    """Drop memoised state. For tests, and after the agent runs a git command."""
    waiters: list[threading.Event]
    with _cache_lock:
        waiters = list(_inflight.values())
        _inflight.clear()
        _cache.clear()
    for waiter in waiters:
        waiter.set()


def _read_native(root: Path) -> RepoState | None:
    """Read the tree state through libgit2, or None to fall back to the CLI.

    Mirrors ``git status --porcelain=v2`` field for field (see the native
    ``git_state``), so the porcelain parser below stays the reference and this
    is only a faster way to the same :class:`RepoState`.
    """
    module = _native()
    if module is None:
        return None
    try:
        (
            is_repo,
            branch,
            detached,
            head,
            upstream,
            ahead,
            behind,
            staged,
            unstaged,
            untracked,
            conflicted,
        ) = module.git_state(str(root))
    except (ValueError, OSError, RuntimeError):
        return None
    if not is_repo:
        return RepoState()
    return RepoState(
        is_repo=True,
        branch=branch,
        detached=detached,
        head=head,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged=tuple(staged),
        unstaged=tuple(unstaged),
        untracked=tuple(untracked),
        conflicted=tuple(conflicted),
    )


def _use_native(root: Path) -> bool:
    """Whether libgit2 may read *root* directly.

    libgit2 walks the working tree itself, file by file. On a WSL project
    driven from Windows (``\\\\wsl.localhost\\...``) every one of those reads is
    a network round-trip through the 9p redirector: a status that takes
    milliseconds on the Linux side takes minutes here, and unlike the CLI path
    there is no timeout to cut it short. Those projects go through
    :func:`git_route` instead, which runs git inside the distribution.
    """
    return wsl.parse_unc(str(root)) is None


def _status_argv(root: Path, *, platform: str | None = None) -> list[str] | None:
    """Full ``git status`` argv for *root*, routed where the project lives."""
    route = git_route(root, platform=platform)
    if route is None:
        return None
    return [
        *route.argv,
        "--no-optional-locks",
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=normal",
        "-z",
    ]


def _read(root: Path) -> RepoState:
    if _use_native(root):
        native_state = _read_native(root)
        if native_state is not None:
            return native_state
    argv = _status_argv(root)
    if argv is None:
        return RepoState(unavailable="git is not installed")
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RepoState(unavailable=f"git status failed: {exc}")
    if completed.returncode != 0:
        # The common case by far is "not a repository", which is not an error
        # worth reporting to the model.
        return RepoState()
    return _parse(completed.stdout.decode("utf-8", "replace"))


def _parse(payload: str) -> RepoState:
    """Decode ``git status --porcelain=v2 --branch -z``.

    Records are NUL-terminated. A rename record carries two paths, the second
    in its own NUL-terminated field, so the reader consumes fields rather than
    iterating a fixed list.
    """
    fields = payload.split("\0")
    branch = head = upstream = ""
    detached = False
    ahead = behind = 0
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []

    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        marker, _, rest = record.partition(" ")
        if marker == "#":
            key, _, value = rest.partition(" ")
            if key == "branch.oid":
                head = value[:8] if value != "(initial)" else ""
            elif key == "branch.head":
                if value == "(detached)":
                    detached = True
                else:
                    branch = value
            elif key == "branch.upstream":
                upstream = value
            elif key == "branch.ab":
                ahead, behind = _ahead_behind(value)
            continue
        if marker == "?":
            _push(untracked, rest)
            continue
        if marker == "u":
            _push(conflicted, _path_of(rest, tail_fields=_U_PATH_FIELD))
            continue
        if marker in {"1", "2"}:
            code = rest[:2]
            path = _path_of(
                rest, tail_fields=_ORDINARY_PATH_FIELD if marker == "1" else _RENAME_PATH_FIELD
            )
            if marker == "2":
                # The rename source follows in its own field; skip it, the
                # destination is the path the agent would write to.
                index += 1
            if code[:1] not in {".", "", "?"}:
                _push(staged, path)
            if code[1:2] not in {".", ""}:
                _push(unstaged, path)
            continue

    return RepoState(
        is_repo=True,
        branch=branch,
        detached=detached,
        head=head,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged=tuple(staged),
        unstaged=tuple(unstaged),
        untracked=tuple(untracked),
        conflicted=tuple(conflicted),
    )


def _push(bucket: list[str], path: str) -> None:
    if path and path not in bucket:
        bucket.append(path)


def _path_of(rest: str, *, tail_fields: int) -> str:
    """The path of a changed-entry record, which is its last field.

    Splitting from the left a fixed number of times keeps a path containing
    spaces intact, which ``rsplit`` on an unknown field count would not.
    """
    parts = rest.split(" ", tail_fields)
    return parts[tail_fields] if len(parts) > tail_fields else ""


def _ahead_behind(value: str) -> tuple[int, int]:
    ahead = behind = 0
    for token in value.split():
        try:
            count = int(token[1:])
        except ValueError:
            continue
        if token.startswith("+"):
            ahead = count
        elif token.startswith("-"):
            behind = count
    return ahead, behind


def summary_lines(state: RepoState) -> list[str]:
    """One or two lines describing the tree, for the model."""
    if not state.is_repo:
        return []
    where = f"branch {state.branch}" if state.branch else "detached HEAD"
    if state.head:
        where += f" at {state.head}"
    if state.upstream:
        drift = []
        if state.ahead:
            drift.append(f"{state.ahead} ahead")
        if state.behind:
            drift.append(f"{state.behind} behind")
        where += f", {' and '.join(drift)} {state.upstream}" if drift else ""

    counts = [
        (state.conflicted, "conflicted"),
        (state.staged, "staged"),
        (state.unstaged, "modified"),
        (state.untracked, "untracked"),
    ]
    parts = [f"{len(items)} {label}" for items, label in counts if items]
    if not parts:
        return [f"Git: {where}, working tree clean."]

    lines = [f"Git: {where}, {', '.join(parts)}."]
    named = _named_sample(state)
    if not named:
        # Untracked files alone are usually scratch, not work in progress, and
        # a warning that names nothing teaches the model to ignore warnings.
        return lines
    lines.append(f"Uncommitted: {named}")
    lines.append(
        "That work is not committed and no checkpoint covers it. Do not revert, "
        "discard, or rewrite those files wholesale without saying so first."
    )
    return lines


def uncommitted_note(root: Path | str, target: Path | str) -> str:
    """A warning if ``target`` holds committed-file changes that are not saved.

    Only tracked files qualify. A dirty tree is the normal state of a working
    session, so warning on every edit would teach the model to skip the warning;
    this fires for the one case nothing else can undo - content that exists
    neither in git nor in a checkpoint the agent made.
    """
    state = repo_state(root)
    if not state.is_repo or not state.tracked_changes:
        return ""
    try:
        rel = Path(target).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return ""
    if rel not in state.tracked_changes:
        return ""
    return (
        f"Warning: {rel} has uncommitted changes. They are not in git and not in "
        "any checkpoint, so replacing the file loses them. Read it first, or ask."
    )


async def git_state_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Tell the model, every turn, which branch it is on and what is uncommitted."""
    workspace = request.workspace
    if workspace is None:
        return None
    try:
        # wait_for cannot interrupt the thread itself, but it does release the
        # turn: the stray read finishes in the background and at worst warms
        # the cache for the next turn.
        state = await asyncio.wait_for(
            asyncio.to_thread(repo_state, workspace),
            timeout=_PROVIDER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return None
    content = wrap_runtime_context_lines(summary_lines(state))
    if not content:
        return None
    return RuntimeContextBlock(source="git", content=content)


def _named_sample(state: RepoState) -> str:
    names: list[str] = []
    for bucket in (state.conflicted, state.staged, state.unstaged):
        for path in bucket:
            if path not in names:
                names.append(path)
    if not names:
        return ""
    shown = ", ".join(names[:_MAX_NAMES])
    extra = len(names) - _MAX_NAMES
    return f"{shown}, +{extra} more" if extra > 0 else shown
