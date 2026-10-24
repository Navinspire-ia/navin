"""``/diff``: the workspace's uncommitted changes, file list then hunks.

The text is built here and returned as a plain command reply, so the CLI and
the desktop show the same output. Hunk lines reuse the line-numbered rows of
the tool preview renderer (``navin.utils.tool_hints``).
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs

GIT_TIMEOUT_S = 15
MAX_BODY_LINES = 400
MAX_UNTRACKED_BYTES = 256 * 1024
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "modified"}
_USAGE = "Usage: /diff [--stat] [--staged] [<path>]"


@dataclass
class FileChange:
    path: str
    status: str
    added: int = 0
    removed: int = 0
    old_path: str = ""
    binary: bool = False
    patch: str = ""


class GitError(RuntimeError):
    pass


def _git(cwd: Path, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, timeout=GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {GIT_TIMEOUT_S}s") from exc
    if proc.returncode not in ok_codes:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return proc.stdout.decode("utf-8", "replace")


def repo_root(workspace: Path) -> Path | None:
    try:
        out = _git(workspace, "rev-parse", "--show-toplevel").strip()
    except GitError:
        return None
    return Path(out) if out else None


def parse_args(args: str) -> tuple[dict[str, Any], str | None]:
    """Options from the command arguments, or an error message."""
    opts: dict[str, Any] = {"stat": False, "staged": False, "path": ""}
    try:
        tokens = shlex.split(args or "")
    except ValueError as exc:
        return opts, f"{exc}. {_USAGE}"
    for token in tokens:
        if token == "--stat":
            opts["stat"] = True
        elif token in ("--staged", "--cached"):
            opts["staged"] = True
        elif token.startswith("-"):
            return opts, f"Unknown option {token}. {_USAGE}"
        elif opts["path"]:
            return opts, f"Only one path is accepted. {_USAGE}"
        else:
            opts["path"] = token
    return opts, None


def _split_z(text: str) -> list[str]:
    items = text.split("\0")
    return items[:-1] if items and items[-1] == "" else items


def _collect(top: Path, base: list[str], pathspec: list[str]) -> list[FileChange]:
    names = _split_z(_git(top, "diff", "--name-status", "-M", "-z", *base, "--", *pathspec))
    changes: dict[str, FileChange] = {}
    order: list[str] = []
    i = 0
    while i < len(names):
        code = names[i]
        letter = code[:1]
        if letter in ("R", "C"):
            old, new = names[i + 1], names[i + 2]
            i += 3
            change = FileChange(path=new, status=_STATUS[letter], old_path=old)
        else:
            path = names[i + 1]
            i += 2
            change = FileChange(path=path, status=_STATUS.get(letter, "modified"))
        changes[change.path] = change
        order.append(change.path)
    stats = _split_z(_git(top, "diff", "--numstat", "-M", "-z", *base, "--", *pathspec))
    i = 0
    while i < len(stats):
        head = stats[i]
        added, removed, path = (head.split("\t", 2) + ["", ""])[:3]
        i += 1
        if not path:
            path = stats[i + 1] if i + 1 < len(stats) else ""
            i += 2
        change = changes.get(path)
        if change is None:
            continue
        if added == "-" or removed == "-":
            change.binary = True
        else:
            change.added, change.removed = int(added or 0), int(removed or 0)
    return [changes[p] for p in order]


def _patch_for(top: Path, base: list[str], change: FileChange) -> str:
    paths = [change.old_path, change.path] if change.old_path else [change.path]
    return _git(top, "diff", "-M", "--no-color", "--no-ext-diff", *base, "--", *paths)


def _untracked(top: Path, pathspec: list[str]) -> list[FileChange]:
    out = []
    for rel in _split_z(_git(top, "ls-files", "--others", "--exclude-standard", "-z", "--", *pathspec)):
        change = FileChange(path=rel, status="untracked")
        file = top / rel
        try:
            data = file.read_bytes() if file.stat().st_size <= MAX_UNTRACKED_BYTES else None
        except OSError:
            data = None
        if data is None or b"\0" in data[:8192]:
            change.binary = True
        else:
            text = data.decode("utf-8", "replace")
            lines = text.splitlines()
            change.added = len(lines)
            change.patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
        out.append(change)
    return out


def collect_changes(workspace: Path, *, staged: bool = False, path: str = "") -> tuple[Path, list[FileChange]]:
    top = repo_root(workspace)
    if top is None:
        raise GitError("not a git repository")
    pathspec: list[str] = []
    if path:
        target = Path(path)
        target = target if target.is_absolute() else Path(workspace) / target
        try:
            rel = target.resolve().relative_to(top.resolve())
        except ValueError as exc:
            raise GitError(f"{path} is outside the repository") from exc
        pathspec = [rel.as_posix() or "."]
    try:
        _git(top, "rev-parse", "--verify", "--quiet", "HEAD")
        head = "HEAD"
    except GitError:
        head = _EMPTY_TREE
    base = ["--cached", head] if staged else [head]
    changes = _collect(top, base, pathspec)
    for change in changes:
        if not change.binary:
            change.patch = _patch_for(top, base, change)
    if not staged:
        changes.extend(_untracked(top, pathspec))
    return top, changes


def _file_list(changes: list[FileChange]) -> list[str]:
    width = max(len(_name(c)) for c in changes)
    rows = []
    for c in changes:
        counts = "binary" if c.binary else f"+{c.added} -{c.removed}"
        rows.append(f"  {_name(c).ljust(width)}  {counts:>12}  {c.status}")
    return rows


def _name(change: FileChange) -> str:
    return f"{change.old_path} -> {change.path}" if change.old_path else change.path


def _hunk_rows(patch: str) -> list[str]:
    from navin.utils.tool_hints import _rows_from_unified_diff, format_preview_line

    rows = [r for r in _rows_from_unified_diff(patch) if r[1] != "meta" or "\u22ee" in r[2]]
    return [format_preview_line(number, kind, text) for number, kind, text in rows]


def render(changes: list[FileChange], *, stat: bool = False, staged: bool = False,
           max_lines: int = MAX_BODY_LINES) -> str:
    scope = "Staged changes" if staged else "Uncommitted changes"
    if not changes:
        return f"{scope}: none."
    added = sum(c.added for c in changes)
    removed = sum(c.removed for c in changes)
    noun = "file" if len(changes) == 1 else "files"
    lines = [f"{scope}: {len(changes)} {noun}, +{added} -{removed}", "", *_file_list(changes)]
    if stat:
        return "\n".join(lines)
    body: list[str] = []
    for c in changes:
        body.append("")
        counts = "binary" if c.binary else f"+{c.added} -{c.removed}"
        body.append(f"{_name(c)} ({c.status}, {counts})")
        if c.binary:
            body.append("    (binary or too large, no hunks)")
            continue
        body.extend(_hunk_rows(c.patch))
    if len(body) > max_lines:
        hidden = len(body) - max_lines
        body = body[:max_lines] + ["", f"... {hidden} more lines (narrow with /diff <path>)"]
    return "\n".join(lines + body)


def diff_text(workspace: Path | None, args: str = "") -> str:
    opts, error = parse_args(args)
    if error:
        return error
    if workspace is None:
        return "No workspace is open, nothing to diff."
    try:
        _, changes = collect_changes(Path(workspace), staged=opts["staged"], path=opts["path"])
    except GitError as exc:
        if str(exc) == "not a git repository":
            return f"{Path(workspace).as_posix()} is not a git repository, /diff needs one."
        return f"/diff failed: {exc}"
    return render(changes, stat=opts["stat"], staged=opts["staged"])


def _workspace(ctx: Any) -> Path | None:
    loop = ctx.loop
    try:
        scopes = getattr(loop, "workspace_scopes", None)
        if scopes is not None:
            scope = scopes.for_message(ctx.msg, getattr(ctx.session, "metadata", None) or {})
            project = getattr(scope, "project_path", None)
            if project:
                return Path(project)
    except Exception:  # noqa: BLE001 - fall back to the loop workspace
        pass
    ws = getattr(loop, "workspace", None)
    return Path(ws) if ws else None


async def cmd_diff(ctx: Any) -> Any:
    import asyncio

    from navin.bus.events import OutboundMessage

    text = await asyncio.to_thread(diff_text, _workspace(ctx), ctx.args)
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=text,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )
