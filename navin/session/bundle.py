# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin session bundles: hand a whole session to someone else's machine.

``export_session_bundle`` packs one chat into a single ``.navin-session`` file
(a zip): the conversation, the attachments its messages reference, the
project (git history + uncommitted changes + new files, or a plain copy when
the project is not a git repository) and the project's ``.navin`` memory.
``import_session_bundle`` rebuilds all of it in a new folder: clone, branch,
pending changes, files, then the conversation, with paths from the original
machine rewritten to the new folder. The receiver opens Navin there and
continues where the sender stopped.

Secrets never travel: ``.env`` files, private keys and certificates are left
out even when git does not ignore them, and the manifest lists what was left
out so the receiver knows what to recreate.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from navin.utils import wsl
from navin.utils.git_argv import git_argv, git_route
from navin.utils.proc import no_window_kwargs

BUNDLE_FORMAT = "navin-session"
BUNDLE_VERSION = 1
BUNDLE_SUFFIX = ".navin-session"

# Never packed, wherever they are: a bundle is meant to be handed to others.
SECRET_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.ppk", ".netrc", ".npmrc", ".pypirc",
    "credentials", "credentials.json", "service-account*.json", "*.kdbx",
)
# Heavy or rebuildable folders skipped in a non-git copy.
SKIPPED_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".next", ".nuxt", "dist", "build", "target", ".gradle", ".idea", ".turbo",
    ".cache", "sessions",
})
# Parts of the project's .navin that are caches or other sessions' state.
SKIPPED_NAVIN_DIRS = frozenset({"checkpoints", "cache", "tmp", "worktrees", "index", "logs", "sessions"})
# About the person, not the project: left out when the bundle goes to someone else.
PERSONAL_NAVIN_FILES = frozenset({"USER.md", "SOUL.md"})
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_GIT_TIMEOUT_S = 300


class BundleError(RuntimeError):
    """The bundle cannot be written or read; the message says why."""


@dataclass
class ExportResult:
    path: Path
    session_key: str
    project_mode: str  # git | files | none
    files: int
    excluded_secrets: list[str] = field(default_factory=list)
    skipped_large: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    project_dir: Path
    session_key: str
    project_mode: str
    files: int
    excluded_secrets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def is_secret_path(rel_path: str) -> bool:
    name = PurePosixPath(rel_path.replace("\\", "/")).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def _git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    prefix = git_argv(cwd)
    if prefix is None:
        raise BundleError("git is not installed")
    # git_argv already scopes to *cwd* (and routes a \\wsl.localhost repo
    # through wsl.exe, where Windows git would refuse it).
    completed = subprocess.run(  # noqa: S603 - fixed git argv
        [*prefix, *args], capture_output=True, timeout=_GIT_TIMEOUT_S, **no_window_kwargs(),
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
        raise BundleError(f"git {' '.join(args[:2])} failed: {detail}")
    return completed


def _git_path(path: Path, repo: Path) -> str:
    """*path* (a host file) as the git process for *repo* will see it.

    A ``\\\\wsl.localhost`` repository runs git inside the distribution: a
    Windows temp path handed to it as-is becomes one odd file name created in
    the repository (``C:\\Users\\...\\repo.bundle``) and nothing lands where we
    look, hence ``WinError 2`` on export. Translate to ``/mnt/c/...`` instead.
    """
    route = git_route(repo)
    if route is None or route.distro is None:
        return str(path)
    translated = wsl.windows_path_to_wsl(str(path))
    if translated is None:
        raise BundleError(f"cannot hand {path} to git inside WSL")
    return translated


def _git_root(path: Path) -> Path | None:
    route = git_route(path)
    if route is None:
        return None
    try:
        completed = _git(["rev-parse", "--show-toplevel"], path, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.decode().strip()
    if root and route.distro:
        root = wsl.to_unc(route.distro, root)  # Linux answer, Windows caller
    return Path(root) if root else None


def _has_commits(repo: Path) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, check=False).returncode == 0


def _session_file(workspace: Path, session_key: str) -> Path:
    from navin.session.manager import SessionManager

    manager = SessionManager(workspace)
    for candidate in (
        manager._get_session_path(session_key),
        manager._get_legacy_lossy_path(session_key),
    ):
        if candidate.is_file():
            return candidate
    raise BundleError(f"no saved session {session_key!r} in {workspace}")


def _media_refs(lines: list[str]) -> list[str]:
    refs: list[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        media = row.get("media") if isinstance(row, dict) else None
        if isinstance(media, list):
            refs.extend(item for item in media if isinstance(item, str) and item)
    return list(dict.fromkeys(refs))


class _Budget:
    def __init__(self) -> None:
        self.total = 0
        self.files = 0
        self.skipped_large: list[str] = []

    def admit(self, path: Path, label: str) -> bool:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size > MAX_FILE_BYTES or self.total + size > MAX_TOTAL_BYTES:
            self.skipped_large.append(label)
            return False
        self.total += size
        self.files += 1
        return True


def _add_tree(zf: zipfile.ZipFile, root: Path, prefix: str, budget: _Budget, excluded: list[str],
              *, skip_dirs: frozenset[str], skip_files: frozenset[str] = frozenset()) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                continue
            if rel in skip_files:
                excluded.append(f"{prefix}{rel}")
                continue
            if is_secret_path(rel):
                excluded.append(f"{prefix}{rel}")
                continue
            if budget.admit(path, f"{prefix}{rel}"):
                zf.write(path, f"{prefix}{rel}")


def export_session_bundle(
    workspace: Path | str,
    session_key: str,
    output: Path | str | None = None,
    *,
    include_project: bool = True,
    project_root: Path | str | None = None,
    for_someone_else: bool = False,
) -> ExportResult:
    """Write ``session_key`` of ``workspace`` (and its project) to one file.

    ``workspace`` holds the conversation; ``project_root`` is the project it
    works on when they differ (a desktop chat lives in the gateway workspace
    and points at its project folder). By default they are the same folder.
    """
    sessions_workspace = Path(workspace).expanduser().resolve()
    session_path = _session_file(sessions_workspace, session_key)
    workspace = Path(project_root).expanduser().resolve() if project_root else sessions_workspace
    lines = session_path.read_text(encoding="utf-8").splitlines()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(output).expanduser() if output else Path.cwd() / f"{workspace.name}-{stamp}{BUNDLE_SUFFIX}"
    if target.suffix != BUNDLE_SUFFIX:
        target = target.with_name(target.name + BUNDLE_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)

    budget = _Budget()
    excluded: list[str] = []
    manifest: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created_at": int(time.time()),
        "session_key": session_key,
        "project_name": workspace.name,
        "source_root": str(workspace),
        "project_mode": "none",
        "media": {},
    }

    with tempfile.TemporaryDirectory(prefix="navin-bundle-") as tmp, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True,
    ) as zf:
        zf.writestr("session.jsonl", "\n".join(lines) + "\n")

        for ref in _media_refs(lines):
            path = Path(ref)
            if not path.is_absolute():
                path = workspace / path
            if not path.is_file() or is_secret_path(path.name):
                continue
            digest = hashlib.sha256(ref.encode()).hexdigest()[:16]
            arcname = f"media/{digest}-{path.name}"
            if budget.admit(path, arcname):
                zf.write(path, arcname)
                manifest["media"][ref] = arcname

        repo = _git_root(workspace) if include_project else None
        if include_project and repo is not None and _has_commits(repo):
            manifest["project_mode"] = "git"
            manifest["git_root"] = str(repo)
            manifest["workspace_in_repo"] = workspace.relative_to(repo).as_posix() if workspace != repo else ""
            branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.decode().strip()
            manifest["branch"] = "" if branch == "HEAD" else branch
            manifest["head"] = _git(["rev-parse", "HEAD"], repo).stdout.decode().strip()
            bundle_path = Path(tmp) / "repo.bundle"
            _git(["bundle", "create", _git_path(bundle_path, repo), "--all"], repo)
            if not bundle_path.is_file():
                raise BundleError("git bundle create wrote nothing where Navin can read it")
            # Already zlib-packed by git: deflating it again costs tens of seconds for ~0%.
            zf.write(bundle_path, "project/repo.bundle", compress_type=zipfile.ZIP_STORED)
            patch = _git(["diff", "--binary", "HEAD"], repo).stdout
            if patch.strip():
                zf.writestr("project/worktree.patch", patch)
            listed = _git(["ls-files", "--others", "--exclude-standard", "-z"], repo).stdout.decode()
            for rel in sorted(filter(None, listed.split("\0"))):
                path = repo / rel
                if path.is_symlink() or not path.is_file():
                    continue
                if is_secret_path(rel):
                    excluded.append(rel)
                    continue
                if budget.admit(path, rel):
                    zf.write(path, f"project/untracked/{rel}")
            # The .navin memory is usually ignored by git: carry it explicitly.
            navin_dir = workspace / ".navin"
            if navin_dir.is_dir():
                _add_tree(
                    zf, navin_dir, "project/navin/", budget, excluded, skip_dirs=SKIPPED_NAVIN_DIRS,
                    skip_files=PERSONAL_NAVIN_FILES if for_someone_else else frozenset(),
                )
        elif include_project:
            manifest["project_mode"] = "files"
            _add_tree(
                zf, workspace, "project/files/", budget, excluded, skip_dirs=SKIPPED_DIRS,
                skip_files=frozenset(f".navin/{name}" for name in PERSONAL_NAVIN_FILES)
                if for_someone_else else frozenset(),
            )

        # Dates, project, git history and prompts: the whole picture at a glance.
        try:
            from navin.share.context import context_markdown, session_context

            manifest["context"] = session_context(session_path, workspace)
            zf.writestr("CONTEXT.md", context_markdown(manifest["context"]))
        except Exception:  # noqa: BLE001 - the bundle is complete without it
            manifest["context"] = None
        manifest["excluded_secrets"] = sorted(set(excluded))
        manifest["skipped_large"] = budget.skipped_large
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    return ExportResult(
        path=target,
        session_key=session_key,
        project_mode=manifest["project_mode"],
        files=budget.files,
        excluded_secrets=manifest["excluded_secrets"],
        skipped_large=budget.skipped_large,
    )


def read_manifest(bundle: Path | str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(bundle) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise BundleError(f"not a Navin session bundle: {exc}") from exc
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError("not a Navin session bundle")
    if int(manifest.get("version") or 0) > BUNDLE_VERSION:
        raise BundleError("this bundle was made by a newer Navin: update Navin to open it")
    return manifest


def _safe_extract(zf: zipfile.ZipFile, member: str, dest_root: Path, rel: str) -> Path | None:
    """Extract one member under ``dest_root`` only (no ``..``, no absolute path)."""
    target = (dest_root / rel).resolve()
    root = dest_root.resolve()
    if target != root and root not in target.parents:
        return None
    if is_secret_path(rel):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(target, "wb") as out:
        shutil.copyfileobj(src, out)
    return target


def _rewrite_paths(text: str, old_root: str, new_root: str) -> str:
    """Point paths of the sender's machine at the receiver's folder."""
    if not old_root or old_root == new_root:
        return text
    for old, new in ((old_root, new_root), (json.dumps(old_root)[1:-1], json.dumps(new_root)[1:-1])):
        text = text.replace(old, new)
    return text


def _free_session_key(workspace: Path, key: str) -> str:
    from navin.session.manager import SessionManager

    manager = SessionManager(workspace)
    if not manager._get_session_path(key).exists():
        return key
    n = 2
    while manager._get_session_path(f"{key}:imported-{n}").exists():
        n += 1
    return f"{key}:imported-{n}"


def import_session_bundle(
    bundle: Path | str,
    into: Path | str | None = None,
    *,
    sessions_workspace: Path | str | None = None,
    session_key: str | None = None,
    metadata: dict[str, Any] | Callable[[Path], dict[str, Any]] | None = None,
) -> ImportResult:
    """Rebuild a bundle's project and conversation in ``into`` (a new folder).

    By default the conversation goes into the project (``navin-cli <folder>``).
    The desktop passes its own ``sessions_workspace``, a fresh ``session_key``
    and ``metadata`` linking the chat to the rebuilt project; ``metadata`` may
    be a callable receiving the project folder.
    """
    bundle = Path(bundle).expanduser().resolve()
    manifest = read_manifest(bundle)
    project_dir = Path(into).expanduser() if into else Path.cwd() / str(manifest.get("project_name") or "navin-session")
    project_dir = project_dir.resolve()
    if project_dir.exists() and any(project_dir.iterdir()):
        raise BundleError(f"{project_dir} is not empty: choose a new folder")
    project_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    files = 0
    mode = str(manifest.get("project_mode") or "none")

    with zipfile.ZipFile(bundle) as zf, tempfile.TemporaryDirectory(prefix="navin-bundle-") as tmp:
        names = zf.namelist()
        workspace = project_dir
        if mode == "git":
            bundle_file = Path(tmp) / "repo.bundle"
            bundle_file.write_bytes(zf.read("project/repo.bundle"))
            project_dir.rmdir()
            _git(["clone", "--quiet", str(bundle_file), str(project_dir)], Path(tmp))
            branch = str(manifest.get("branch") or "")
            head = str(manifest.get("head") or "")
            if branch:
                if _git(["checkout", "--quiet", branch], project_dir, check=False).returncode != 0:
                    _git(["checkout", "--quiet", "-B", branch, head or f"origin/{branch}"], project_dir)
            elif head:
                _git(["checkout", "--quiet", head], project_dir)
            # The clone's origin is a temp file: drop it rather than leave a dead remote.
            _git(["remote", "remove", "origin"], project_dir, check=False)
            if "project/worktree.patch" in names:
                patch_file = Path(tmp) / "worktree.patch"
                patch_file.write_bytes(zf.read("project/worktree.patch"))
                applied = _git(
                    ["apply", "--binary", "--whitespace=nowarn", _git_path(patch_file, project_dir)],
                    project_dir, check=False,
                )
                if applied.returncode != 0:
                    kept = project_dir / ".navin-import-uncommitted.patch"
                    shutil.copyfile(patch_file, kept)
                    notes.append(f"uncommitted changes did not apply cleanly; saved to {kept.name}")
            for member in names:
                if member.startswith("project/untracked/") and not member.endswith("/"):
                    if _safe_extract(zf, member, project_dir, member[len("project/untracked/"):]):
                        files += 1
            sub = str(manifest.get("workspace_in_repo") or "")
            workspace = (project_dir / sub).resolve() if sub else project_dir
            for member in names:
                if member.startswith("project/navin/") and not member.endswith("/"):
                    if _safe_extract(zf, member, workspace / ".navin", member[len("project/navin/"):]):
                        files += 1
        elif mode == "files":
            for member in names:
                if member.startswith("project/files/") and not member.endswith("/"):
                    if _safe_extract(zf, member, project_dir, member[len("project/files/"):]):
                        files += 1

        media_dir = workspace / ".navin" / "imported-media"
        media_map: dict[str, str] = {}
        for ref, arcname in (manifest.get("media") or {}).items():
            if arcname in names:
                extracted = _safe_extract(zf, arcname, media_dir, PurePosixPath(arcname).name)
                if extracted:
                    media_map[ref] = str(extracted)

        text = zf.read("session.jsonl").decode("utf-8")
        for old, new in media_map.items():
            text = text.replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])
        old_root = str(manifest.get("source_root") or "")
        text = _rewrite_paths(text, old_root, str(workspace))

    target_workspace = Path(sessions_workspace).expanduser().resolve() if sessions_workspace else workspace
    key = _free_session_key(target_workspace, session_key or str(manifest.get("session_key") or "cli:direct"))
    extra = metadata(workspace) if callable(metadata) else (metadata or {})
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("_type") == "metadata":
            row["key"] = key
            row_meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            row_meta.update(extra)
            row_meta.setdefault("imported_from", {"project": manifest.get("project_name"), "at": int(time.time())})
            row["metadata"] = row_meta
        rows.append(json.dumps(row, ensure_ascii=False))
    from navin.session.manager import SessionManager

    session_path = SessionManager(target_workspace)._get_session_path(key)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    return ImportResult(
        project_dir=workspace,
        session_key=key,
        project_mode=mode,
        files=files,
        excluded_secrets=list(manifest.get("excluded_secrets") or []),
        notes=notes,
    )
