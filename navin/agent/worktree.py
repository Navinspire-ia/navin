"""Give a background subagent a tree of its own.

Subagents run concurrently by default, and until now they all wrote into the
same working tree. Two of them touching the same file interleave their edits,
and the checkpoint baselines they record describe a state neither of them
produced. That is not a missing feature, it is a correctness hole that only
shows up under the exact conditions the tool encourages: several independent
tasks started together.

A git worktree fixes it at the level where the problem lives. The subagent gets
a real checkout of the same repository, sharing object storage so it costs a
checkout rather than a clone, on a detached HEAD so it cannot move a branch the
user cares about. Its edits land somewhere the parent can diff and merge on
purpose, instead of appearing in the user's tree as a surprise.

The trees live under the navin home rather than inside the project: a worktree
nested in the repository is something every search tool then has to be taught to
skip, and one that is missed shows the agent its own duplicate of every file.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from navin.utils.proc import no_window_kwargs

_TIMEOUT_S = 60.0
# A large migration repo can take well over a minute to materialise. The
# generic 60s cap would then return None, every isolate=true spawn would fall
# back to the shared tree, and a hundred agents would write over each other.
_CREATE_TIMEOUT_S = 180.0
# git takes a lock on the origin. A wave of isolate=true spawns used to all
# call ``worktree add`` at once, so the first few ran and the rest timed out
# waiting for the lock, then silently shared the user's tree.
_CREATE_LIMIT = 4
_create_sema = threading.Semaphore(_CREATE_LIMIT)

# Idle clean checkouts parked per origin repo. Reusing one is a ``reset
# --hard`` + ``clean``; creating one is a full ``git worktree add``. Cap
# matches the default subagent concurrency so a wave of isolate=true
# spawns does not pay N cold checkouts every turn.
_DEFAULT_POOL_SIZE = 8
_pool: dict[str, list[Path]] = {}
_pool_lock = threading.Lock()


_REPORT_LIMIT = 20

# Cheap: an untracked or ignored directory is one line, so a node_modules costs
# one entry instead of a walk. Enough to know something is there.
_STATUS_CHEAP = ("status", "--porcelain", "--ignored=matching")
# Precise: every file named individually. Only used when the checkout was
# already dirty at creation, because then the two listings have to be compared
# file by file - at directory granularity, a deliverable written into a folder
# a hook created reads as the same line and would be subtracted away.
_STATUS_PRECISE = ("status", "--porcelain", "--ignored=traditional", "-uall")


@dataclass(frozen=True, slots=True)
class _Entry:
    """A status line present the moment the checkout was made.

    The fingerprint is what stops the subtraction from hiding real work: a
    status line does not change when a file's contents do, so a subagent
    rewriting a file its repository's hook had already written would look
    exactly like the hook's own output.
    """

    line: str
    fingerprint: tuple[int, int] | None


def _fingerprint(root: Path, line: str) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` for the path a porcelain status line names."""
    # "XY path", and for a rename "XY old -> new"; the arrival is what exists.
    path = line[3:].strip().split(" -> ")[-1].strip('"')
    try:
        stat = (root / path).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


@dataclass(frozen=True, slots=True)
class Changes:
    """What a subagent left behind in its checkout."""

    entries: tuple[str, ...]
    commits: int
    moved: bool

    @property
    def empty(self) -> bool:
        return not self.entries and not self.commits and not self.moved

    def render(self) -> str:
        lines: list[str] = list(self.entries[:_REPORT_LIMIT])
        if len(self.entries) > _REPORT_LIMIT:
            lines.append(f"... and {len(self.entries) - _REPORT_LIMIT} more")
        if self.commits:
            lines.insert(0, f"{self.commits} commit(s) on a detached HEAD")
        elif self.moved:
            lines.insert(
                0,
                "HEAD moved during the run; `git reflog` in the checkout has the "
                "commits it left behind",
            )
        return "\n".join(f"  {line}" for line in lines)


@dataclass(frozen=True, slots=True)
class Worktree:
    """A checkout created for one subagent, and where it came from."""

    path: Path
    origin: Path
    task_id: str
    base: str
    initial: tuple[_Entry, ...]
    precise: bool

    def inspect(self) -> Changes | None:
        """What changed here, or None when git would not say.

        Four things make this harder than a ``git diff``, and each one was a way
        to delete a subagent's work while reporting that it did nothing, or to
        keep a checkout forever that held none.

        A subagent may commit, which leaves the tree clean. Comparing HEAD to
        the SHA the checkout started from catches most of that, but not a
        subagent that commits and then tidies up with ``checkout <base>``: the
        commit still exists, HEAD is back where it began, and no comparison of
        two states can see between them. So HEAD's reflog is read as well, which
        lists every position it held. A reflog that comes back empty means
        reflogs are switched off in this repository, and that is reported as
        "cannot tell" rather than as zero commits.

        The tree is read the same way. Output covered by ``.gitignore`` counts,
        since a build artefact is still work. Whatever a ``post-checkout`` hook
        generated at creation is subtracted, but only while it is untouched:
        matching on the status line alone would also subtract the subagent's
        edit to a file the hook had written, which is how a deliverable gets
        deleted while the report says nothing happened.

        Any git failure returns None - never an empty result, because callers
        delete on empty.
        """
        head = _git(["rev-parse", "HEAD"], cwd=self.path)
        if head is None:
            return None
        moved = head.strip() != self.base
        commits = 0
        if moved:
            count = _git(["rev-list", "--count", f"{self.base}..HEAD"], cwd=self.path)
            if count is None:
                return None
            commits = int(count.strip() or 0)

        # -g lists what HEAD pointed at over time. `git worktree add` writes the
        # first entry, so an empty answer means reflogs are disabled rather than
        # that nothing happened, and the difference decides whether a checkout
        # is safe to delete.
        walked = _git(["rev-list", "-g", "HEAD"], cwd=self.path)
        if walked is None:
            return None
        visited = {line.strip() for line in walked.splitlines() if line.strip()}
        if not visited:
            return None
        moved = moved or bool(visited - {self.base})

        status = _git(self._status_argv(), cwd=self.path)
        if status is None:
            return None
        untouched = {
            entry.line for entry in self.initial
            if entry.fingerprint == _fingerprint(self.path, entry.line)
        }
        entries = tuple(
            line for line in status.splitlines()
            if line.strip() and line not in untouched
        )
        return Changes(entries=entries, commits=commits, moved=moved)

    def _status_argv(self) -> list[str]:
        return list(_STATUS_PRECISE if self.precise else _STATUS_CHEAP)


def worktrees_root() -> Path:
    return Path.home() / ".navin" / "worktrees"


def repo_root(workspace: Path) -> Path | None:
    """The git repository containing ``workspace``, if there is one."""
    if shutil.which("git") is None:
        return None
    top = _git(["rev-parse", "--show-toplevel"], cwd=workspace)
    if not top:
        return None
    candidate = Path(top.strip())
    return candidate if candidate.is_dir() else None


def create(
    workspace: Path,
    task_id: str,
    *,
    pool_size: int = _DEFAULT_POOL_SIZE,
) -> Worktree | None:
    """Check the repository out again for ``task_id``, or return None.

    None is not an error the caller should report: a workspace that is not a git
    repository simply cannot be isolated this way, and the subagent still has to
    run.

    When ``pool_size > 0``, an idle clean checkout for this repository is
    reused (reset to current HEAD) instead of paying another ``worktree add``.
    """
    origin = repo_root(workspace)
    if origin is None:
        return None
    base = _git(["rev-parse", "--verify", "HEAD"], cwd=origin)
    if base is None:
        # A repository with no commit has no HEAD to check out. Nothing to
        # isolate from, and nothing the parent could diff against afterwards.
        return None
    base = base.strip()

    if pool_size > 0:
        reused = _take_from_pool(origin, base, task_id)
        if reused is not None:
            logger.info(
                "worktree: subagent [{}] reused pooled checkout at {}",
                task_id,
                reused.path,
            )
            return reused

    target = worktrees_root() / origin.name / task_id
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("worktree: cannot create {}", target.parent)
        return None

    # Detached: a subagent must never be able to move a branch the user has
    # checked out, and git refuses to check out an already-checked-out branch
    # anyway, which would fail the spawn for the common case.
    #
    # The semaphore is the whole point of the limiter: without it a wave of
    # isolate=true spawns all pile onto ``git worktree add``, the origin lock
    # serialises them anyway, and the ones waiting past the timeout fall back
    # to the shared tree - the exact corruption isolation exists to prevent.
    with _create_sema:
        added = _git(
            ["worktree", "add", "--detach", str(target), "HEAD"],
            cwd=origin,
            timeout=_CREATE_TIMEOUT_S,
        )
    if added is None:
        return None
    return _finish_create(origin, target, task_id, base)


def release(
    worktree: Worktree,
    *,
    pool_size: int = _DEFAULT_POOL_SIZE,
) -> bool:
    """Park a clean empty checkout in the pool, or remove it.

    Returns True when the checkout was pooled for reuse. Callers that already
    know the tree holds work must not call this - only empty settlements.
    """
    if pool_size <= 0:
        remove(worktree)
        return False
    if not _reset_clean(worktree):
        remove(worktree)
        return False
    with _pool_lock:
        idle = _pool.setdefault(str(worktree.origin.resolve()), [])
        if len(idle) >= pool_size:
            remove(worktree)
            return False
        idle.append(worktree.path)
    logger.debug("worktree: pooled idle checkout at {}", worktree.path)
    return True


def clear_pool() -> None:
    """Drop every parked checkout. For tests."""
    with _pool_lock:
        paths = [path for idle in _pool.values() for path in idle]
        _pool.clear()
    for path in paths:
        with suppress(Exception):
            origin = repo_root(path)
            if origin is not None:
                _git(["worktree", "remove", "--force", str(path)], cwd=origin)
            shutil.rmtree(path, ignore_errors=True)


def _take_from_pool(origin: Path, base: str, task_id: str) -> Worktree | None:
    key = str(origin.resolve())
    while True:
        with _pool_lock:
            idle = _pool.get(key) or []
            if not idle:
                return None
            path = idle.pop()
        if not path.is_dir():
            continue
        if not _reset_path(path, base):
            with suppress(Exception):
                _git(["worktree", "remove", "--force", str(path)], cwd=origin)
            continue
        return _finish_create(origin, path, task_id, base)


def _reset_clean(worktree: Worktree) -> bool:
    return _reset_path(worktree.path, worktree.base)


def _reset_path(path: Path, base: str) -> bool:
    if _git(["reset", "--hard", base], cwd=path) is None:
        return False
    if _git(["clean", "-fdx"], cwd=path) is None:
        return False
    # Point detached HEAD at the origin's current tip when the pool entry
    # was parked on an older base; ``base`` is already the tip we want.
    if _git(["checkout", "--detach", base], cwd=path) is None:
        return False
    return True


def _finish_create(origin: Path, target: Path, task_id: str, base: str) -> Worktree:
    # Whatever a post-checkout hook just produced belongs to the repository, not
    # to the subagent. Recorded now so it is not later mistaken for work, which
    # would make every checkout on such a repository un-cleanable. A clean
    # checkout is the norm, so the expensive listing is only taken when the
    # cheap one shows there is in fact something to subtract later.
    lines = _status_lines(target, _STATUS_CHEAP)
    precise = bool(lines)
    if precise:
        lines = _status_lines(target, _STATUS_PRECISE)
    initial = tuple(_Entry(line, _fingerprint(target, line)) for line in lines)
    logger.info("worktree: subagent [{}] isolated at {}", task_id, target)
    return Worktree(
        path=target,
        origin=origin,
        task_id=task_id,
        base=base,
        initial=initial,
        precise=precise,
    )


def _status_lines(target: Path, argv: tuple[str, ...]) -> list[str]:
    status = _git(list(argv), cwd=target)
    return [line for line in (status or "").splitlines() if line.strip()]


def remove(worktree: Worktree) -> None:
    """Drop the checkout and its administrative entry."""
    if _git(["worktree", "remove", "--force", str(worktree.path)], cwd=worktree.origin) is None:
        # The directory may survive a failed removal; prune keeps `git worktree
        # list` honest either way, so a later run does not trip over a stale
        # entry pointing at a path that is gone.
        _git(["worktree", "prune"], cwd=worktree.origin)
    with suppress(OSError):
        # The per-repository folder, once its last checkout is gone. rmdir
        # refuses a non-empty directory, which is exactly the guard wanted.
        worktree.path.parent.rmdir()


def _git(args: list[str], *, cwd: Path, timeout: float | None = None) -> str | None:
    """Run git, returning its stdout, or None when it failed for any reason."""
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_S if timeout is None else timeout,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("worktree: git {} failed: {}", " ".join(args), exc)
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        logger.warning(
            "worktree: git {} exited {}: {}",
            " ".join(args),
            completed.returncode,
            detail[0][:200] if detail else "",
        )
        return None
    return completed.stdout
