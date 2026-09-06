"""Workspace checkpoints: instant snapshots + one-click restore.

Snapshots live in a *shadow* git repository (a bare repo under
``~/.navin/checkpoints/<digest>``, driven with ``--git-dir``) whose work-tree
is the project root. The project's own ``.git`` - when it exists - is never
read or written, so
checkpoints work identically on git and non-git projects and never pollute
the user's history, index, or reflog.

Restore is safe by construction: a safety checkpoint of the current state is
always taken first, then the target tree is checked out and files created
after the target checkpoint are removed.

**Projects that live in a WSL distribution.** Opened from Windows, such a
project is reachable at ``\\\\wsl.localhost\\<distro>\\...`` but its files are
Linux files, and git has to run on that side (see
:mod:`navin.utils.git_argv`). The shadow repo therefore follows the work-tree
into the distribution, at ``~/.navin/checkpoints/<digest>`` *inside* the
distro, so both halves of the snapshot sit on the same filesystem: git never
crosses the boundary mid-command, and it never pays the ``/mnt/c`` penalty on
every object it writes. The digest is taken from the path the distribution
knows the project by, so opening the same project from Windows and from inside
the distro lands on one shared history instead of two divergent ones. A shadow
repo left on the Windows side by an older build is moved across on first use
rather than abandoned.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from navin.utils import wsl
from navin.utils.git_argv import git_route
from navin.utils.proc import no_window_kwargs

MAX_DIFF_CHARS = 200_000
_IDENTITY = ["-c", "user.name=Navin Checkpoints", "-c", "user.email=checkpoints@navin.local"]

# Environment that would silently redirect the shadow repo somewhere else.
# Inherited from a git hook or an outer git command, any of these wins over
# what we intend, so they are dropped rather than trusted.
_HOSTILE_GIT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)

# Artifacts nobody wants snapshotted, even when the project has no .gitignore.
DEFAULT_EXCLUDES = [
    ".git/",
    ".navin/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    "target/",
    ".cache/",
    ".DS_Store",
]


class CheckpointError(Exception):
    """A checkpoint operation that failed for a reason worth showing.

    ``code`` names the failure so the WebUI can translate it; the message is
    the English fallback shown when no translation exists.
    """

    def __init__(self, message: str, *, status: int = 400, code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def checkpoints_home() -> Path:
    """Where shadow repos live on *this* machine.

    Only ever the host side: a project inside a WSL distribution keeps its
    shadow repo in that distribution's home, which this override cannot name.
    """
    override = os.environ.get("NAVIN_CHECKPOINTS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".navin" / "checkpoints"


def _digest(path_text: str) -> str:
    return hashlib.sha256(path_text.encode("utf-8")).hexdigest()[:16]


def _host_path(distro: str, posix_path: str) -> Path:
    """How this process reads a file that lives inside *distro*.

    Windows serves every distribution through the ``wsl.localhost`` redirector,
    so plain Python file access works; only *commands* have to be routed.
    """
    return Path(wsl.to_unc(distro, posix_path))


@dataclass(frozen=True)
class ShadowRepo:
    """A shadow repo, named from both sides of a possible WSL boundary.

    ``git_dir`` / ``work_tree`` are what the git process is told; ``host_*``
    are what this Python process opens. They differ only for a WSL project
    driven from Windows.
    """

    argv: tuple[str, ...]
    git_dir: str
    work_tree: str
    host_git_dir: Path
    host_work_tree: Path
    cwd: str | None = None
    distro: str | None = None


def shadow_repo(root: Path | str, *, platform: str | None = None) -> ShadowRepo:
    """Locate the shadow repo for *root* and the way to drive git over it."""
    route = git_route(root, platform=platform)
    if route is None:
        raise CheckpointError(
            "git is not available on this machine. Install git, then reopen the project.",
            status=500,
            code="gitMissing",
        )

    if route.distro is None:
        if wsl.parse_unc(str(root)) is not None:
            # A WSL project with no way into the distribution: Windows git
            # would build a shadow repo the Linux side can never read.
            raise CheckpointError(
                "This project lives in a WSL distribution, but wsl.exe was not found, "
                "so checkpoints cannot run where the files are. Enable WSL on this "
                "machine, or open the project from inside the distribution.",
                status=501,
                code="wslUnreachable",
            )
        resolved = Path(root).resolve()
        git_dir = checkpoints_home() / _digest(str(resolved))
        return ShadowRepo(
            argv=route.argv,
            git_dir=str(git_dir),
            work_tree=str(resolved),
            host_git_dir=git_dir,
            host_work_tree=resolved,
            cwd=str(resolved),
        )

    home = wsl.distro_home(route.distro)
    if not home:
        raise CheckpointError(
            f"The WSL distribution '{route.distro}' did not answer, so checkpoints "
            f"cannot be stored next to the project. Start it (wsl -d {route.distro}) "
            "and try again.",
            status=503,
            code="wslHomeUnreachable",
        )
    git_dir = str(PurePosixPath(home) / ".navin" / "checkpoints" / _digest(route.root))
    return ShadowRepo(
        argv=route.argv,
        git_dir=git_dir,
        work_tree=route.root,
        host_git_dir=_host_path(route.distro, git_dir),
        host_work_tree=_host_path(route.distro, route.root),
        # wsl.exe already carries --cd into the project; a Windows working
        # directory would only add a UNC warning on the way.
        cwd=None,
        distro=route.distro,
    )


def shadow_git_dir(root: Path, *, platform: str | None = None) -> Path:
    """The shadow repo directory, as this process addresses it."""
    return shadow_repo(root, platform=platform).host_git_dir


def _exec(
    repo: ShadowRepo, argv: list[str], *, label: str, check: bool
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=repo.cwd,
        env={k: v for k, v in os.environ.items() if k not in _HOSTILE_GIT_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        **no_window_kwargs(),
    )
    if check and proc.returncode != 0:
        raise CheckpointError(
            f"git {label} failed: {proc.stderr.strip()[:400]}",
            status=500,
        )
    return proc


def _run(repo: ShadowRepo, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command over the shadow repo.

    ``--git-dir`` / ``--work-tree`` rather than the matching environment
    variables: the command may be handed to ``wsl.exe``, which does not carry
    the caller's environment across, and the flags also outrank any GIT_* the
    process inherited.
    """
    argv = [
        *repo.argv,
        "--no-optional-locks",
        "--git-dir",
        repo.git_dir,
        "--work-tree",
        repo.work_tree,
        *_IDENTITY,
        *args,
    ]
    return _exec(repo, argv, label=" ".join(args[:2]), check=check)


def _legacy_shadow_dirs(root: str, location: wsl.WslLocation) -> list[Path]:
    """Host-side shadow repos an older build may have left for a WSL project.

    Before the shadow repo followed WSL projects into their distribution, the
    digest was taken from the Windows path - and the path the project was
    opened with, the redirector's modern spelling and its older ``wsl$``
    spelling are three different strings. Every one of them could have been
    hashed, so every one of them is a candidate.
    """
    home = checkpoints_home()
    tail = "\\".join(part for part in location.path.parts if part != "/")
    candidates = [root]
    for prefix in (
        f"\\\\wsl.localhost\\{location.distro}",
        f"\\\\wsl$\\{location.distro}",
    ):
        candidates.append(f"{prefix}\\{tail}" if tail else prefix)
    seen: set[str] = set()
    dirs: list[Path] = []
    for text in candidates:
        if text in seen:
            continue
        seen.add(text)
        dirs.append(home / _digest(text))
    return dirs


def _adopt_legacy_shadow(root: Path | str, repo: ShadowRepo) -> bool:
    """Move a Windows-side shadow repo into the distribution. True if adopted.

    The repo is self-contained (objects, refs, excludes; no absolute path is
    baked into it), so a copy is a faithful move. The original is kept under a
    ``.migrated`` name: a snapshot history is the one thing a checkpoint
    feature must never lose quietly.
    """
    location = wsl.parse_unc(str(root))
    if location is None:
        return False
    for legacy in _legacy_shadow_dirs(str(root), location):
        if not (legacy / "HEAD").is_file():
            continue
        try:
            repo.host_git_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(legacy, repo.host_git_dir, dirs_exist_ok=True)
        except OSError as exc:
            raise CheckpointError(
                f"could not move existing checkpoints into the WSL distribution: {exc}",
                status=500,
                code="migrationFailed",
            ) from exc
        try:
            legacy.rename(legacy.with_name(legacy.name + ".migrated"))
        except OSError:
            # Keeping the original in place is harmless: the adoption only
            # runs when the distro side has no HEAD yet.
            pass
        return True
    return False


def ensure_shadow_repo(root: Path | str, *, platform: str | None = None) -> ShadowRepo:
    repo = shadow_repo(root, platform=platform)
    if not repo.host_work_tree.is_dir():
        raise CheckpointError("project root not found", status=404)
    if (repo.host_git_dir / "HEAD").exists():
        return repo
    if repo.distro is not None and _adopt_legacy_shadow(root, repo):
        return repo
    # init names its target positionally: --git-dir would fight it, and
    # --work-tree is meaningless on a repo that does not exist yet.
    _exec(
        repo,
        [*repo.argv, "init", "--bare", "--initial-branch=checkpoints", repo.git_dir],
        label="init",
        check=True,
    )
    # GIT_WORK_TREE-driven commands refuse to run against a bare repo.
    _run(repo, "config", "core.bare", "false")
    exclude = repo.host_git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("\n".join(DEFAULT_EXCLUDES) + "\n", encoding="utf-8")
    return repo


def _head_sha(repo: ShadowRepo) -> str | None:
    proc = _run(repo, "rev-parse", "--verify", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _resolve(repo: ShadowRepo, checkpoint_id: str) -> str:
    checkpoint_id = checkpoint_id.strip()
    if not checkpoint_id or not all(c in "0123456789abcdef" for c in checkpoint_id.lower()):
        raise CheckpointError("invalid checkpoint id")
    proc = _run(repo, "rev-parse", "--verify", f"{checkpoint_id}^{{commit}}", check=False)
    if proc.returncode != 0:
        raise CheckpointError("checkpoint not found", status=404)
    return proc.stdout.strip()


def create_checkpoint(
    root: Path | str, *, label: str = "", reason: str = "manual", platform: str | None = None
) -> dict[str, Any]:
    """Snapshot the current work-tree. No-op (created=False) when unchanged."""
    repo = ensure_shadow_repo(root, platform=platform)
    _run(repo, "add", "-A")
    head = _head_sha(repo)
    if head is not None:
        status = _run(repo, "status", "--porcelain", check=False)
        if not status.stdout.strip():
            return {
                "created": False,
                "id": head[:12],
                "reason": reason,
                "label": label,
            }
    message = f"[{reason}] {label}".strip() if label else f"[{reason}]"
    _run(repo, "commit", "--quiet", "--allow-empty", "-m", message)
    sha = _head_sha(repo) or ""
    return {"created": True, "id": sha[:12], "reason": reason, "label": label}


def list_checkpoints(
    root: Path | str, *, limit: int = 50, platform: str | None = None
) -> dict[str, Any]:
    repo = shadow_repo(root, platform=platform)
    if not (repo.host_git_dir / "HEAD").exists():
        # Nothing here yet, but an older build may have left a history on the
        # Windows side; adopting it now is what makes it visible.
        if repo.distro is None or not _adopt_legacy_shadow(root, repo):
            return {"checkpoints": []}
    proc = _run(
        repo,
        "log",
        f"-{max(1, min(limit, 200))}",
        "--format=%H%x00%ct%x00%s",
        check=False,
    )
    if proc.returncode != 0:
        return {"checkpoints": []}
    checkpoints: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) != 3:
            continue
        sha, ts, message = parts
        reason = ""
        label = message
        if message.startswith("[") and "]" in message:
            reason = message[1 : message.index("]")]
            label = message[message.index("]") + 1 :].strip()
        checkpoints.append(
            {
                "id": sha[:12],
                "ts": int(ts),
                "reason": reason,
                "label": label,
            }
        )
    return {"checkpoints": checkpoints}


def diff_checkpoint(
    root: Path | str, checkpoint_id: str, *, platform: str | None = None
) -> dict[str, Any]:
    """Diff between a checkpoint and the current work-tree state."""
    repo = ensure_shadow_repo(root, platform=platform)
    sha = _resolve(repo, checkpoint_id)
    _run(repo, "add", "-A")
    names = _run(repo, "diff", "--cached", "--name-status", sha)
    files = []
    for line in names.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0][:1], "path": parts[-1]})
    patch_proc = _run(repo, "diff", "--cached", sha)
    patch = patch_proc.stdout
    truncated = len(patch) > MAX_DIFF_CHARS
    if truncated:
        patch = patch[:MAX_DIFF_CHARS]
    return {"id": sha[:12], "files": files, "patch": patch, "truncated": truncated}


def spawn_pre_turn_checkpoint(root: Path | str, label: str = "") -> threading.Thread:
    """Best-effort full workspace snapshot before an agent turn.

    Runs in a daemon thread: the turn must never wait on (or fail because of)
    snapshotting. The agent's first file edit only happens after a full LLM
    round-trip, which in practice gives the snapshot ample time to land.
    """

    def _work() -> None:
        try:
            create_checkpoint(root, label=label[:80], reason="pre-turn")
        except Exception:  # noqa: BLE001 - snapshot failures must stay silent
            pass

    thread = threading.Thread(target=_work, name="workspace-checkpoint", daemon=True)
    thread.start()
    return thread


def restore_checkpoint(
    root: Path | str, checkpoint_id: str, *, platform: str | None = None
) -> dict[str, Any]:
    """Restore the work-tree to a checkpoint (with an automatic safety snapshot)."""
    repo = ensure_shadow_repo(root, platform=platform)
    target = _resolve(repo, checkpoint_id)

    safety = create_checkpoint(root, label="before restore", reason="pre-restore", platform=platform)
    safety_sha = _resolve(repo, safety["id"])

    # Files that exist now but not in the target snapshot must be deleted,
    # otherwise "restore" would silently keep new files around.
    added = _run(repo, "diff", "--name-only", "--diff-filter=A", target, safety_sha)
    work_tree = repo.host_work_tree
    deleted = 0
    for rel in added.stdout.splitlines():
        parts = PurePosixPath(rel.strip()).parts
        # git lists repo-relative paths; anything else is not ours to delete.
        if not parts or parts[0] == "/" or any(part == ".." for part in parts):
            continue
        path = work_tree.joinpath(*parts)
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
            deleted += 1
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            deleted += 1

    _run(repo, "read-tree", "--reset", target)
    _run(repo, "checkout-index", "-a", "-f")
    return {
        "restored": target[:12],
        "safety": safety_sha[:12],
        "deletedFiles": deleted,
    }
