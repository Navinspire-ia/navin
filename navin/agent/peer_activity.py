# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Peer agents awareness: what the agents of the other sessions of a project do.

Several sessions can run agents in the same project at once (navin-cli
windows, desktop and web chats, channel bots). Each running turn owns one
small JSON file in a cross-process registry under the Navin home::

    <home>/activity/<sha1(project)[:16]>/<pid>-<sha1(session_key)[:12]>.json

The file names the session, its task and the project files it edited during
the turn. Readers list one directory: an entry whose process is gone (or
whose pid was reused, told apart by when the process started) is deleted on
sight, so claims vanish when their turn ends or their process dies.

A *family* is the root chat: a chat and the subagents it spawned in the same
tree share it and never block each other. Every other family is a peer: the
agent is told what they do (runtime context) and refused when it is about to
change a file a live peer edited during its current turn.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil
from loguru import logger

from navin.runtime_context import (
    RUNTIME_CONTEXT_END,
    RUNTIME_CONTEXT_TAG,
    RuntimeContextBlock,
    wrap_runtime_context_lines,
)

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

_TASK_CHARS = 160
_TITLE_CHARS = 60
_MAX_PEERS_SHOWN = 5
_MAX_FILES_SHOWN = 6
# Files remembered per turn: the most recent ones win.
_MAX_FILES_KEPT = 500
# A temp file this old was left by a writer that died mid-write.
_STALE_TEMP_S = 60.0
# Outside Linux the kernel keeps each process's start time and psutil returns
# it unchanged; a reused pid belongs to a process started at least this much
# later.
_CREATE_TIME_TOLERANCE_S = 1.0
_PROVIDER_TIMEOUT_S = 1.5

_SURFACES = {"cli": "navin-cli", "websocket": "web"}


@dataclass(frozen=True, slots=True)
class PeerEntry:
    """One live turn of another session, as read from the registry."""

    pid: int
    create_time: float
    # When the process started, in a unit that never moves (see _start_marker).
    start: float
    session_key: str
    family: str
    title: str
    surface: str
    project: str
    task: str
    started_at: float
    updated_at: float
    files: dict[str, float]
    path: Path


@dataclass(slots=True)
class PeerTurn:
    """The registry entry this process owns for one running turn."""

    project: str
    session_key: str
    family: str
    path: Path
    entry: dict[str, Any]
    refs: int = 1
    ended: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_current_turn: ContextVar[PeerTurn | None] = ContextVar("navin_peer_turn", default=None)
_active: dict[Path, PeerTurn] = {}
_active_lock = threading.Lock()
_atexit_registered = False
_own_start: float | None = None


# -- paths -------------------------------------------------------------------


def _activity_root() -> Path:
    """The registry root under the Navin home (never inside a project)."""
    from navin.config.paths import get_data_dir

    return get_data_dir() / "activity"


def _resolved_project(project: str | os.PathLike[str]) -> str:
    return str(Path(project).expanduser().resolve(strict=False))


def resolve_project(project: str | os.PathLike[str] | None) -> str | None:
    """The project path every process keys the registry on (stats: off the loop)."""
    return _resolved_project(project) if project else None


def _project_dir(project: str) -> Path:
    digest = hashlib.sha1(project.encode("utf-8")).hexdigest()[:16]
    return _activity_root() / digest


def _entry_name(pid: int, session_key: str) -> str:
    digest = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]
    return f"{pid}-{digest}.json"


def _relative(project: str, path: str | os.PathLike[str]) -> str | None:
    """Project-relative posix path, or None when the path is outside it."""
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
        return resolved.relative_to(Path(project)).as_posix()
    except ValueError:
        return None
    except OSError as exc:
        logger.debug("Peer activity: cannot resolve {}: {}", path, exc)
        return None


# -- process identity --------------------------------------------------------


def _start_marker(pid: int) -> float:
    """When ``pid`` started, in a unit that never moves for one process.

    psutil's create_time is the boot time plus the start offset, and Linux
    derives the boot time from the wall clock: each clock correction (under
    WSL, after every sleep of the host) gave the same process another
    create_time, so a live peer looked dead and its entry was deleted with
    its claims. On Linux the start offset in clock ticks since boot is read as
    is; elsewhere the kernel keeps the start time itself.
    """
    if sys.platform.startswith("linux"):
        with open(f"/proc/{int(pid)}/stat", encoding="ascii", errors="replace") as handle:
            stat = handle.read()
        # The command name sits in parentheses and may hold spaces: starttime,
        # field 22, is the 20th field after it.
        return float(int(stat[stat.rfind(")") + 2:].split()[19]))
    return float(psutil.Process(pid).create_time())


def _self_start() -> float:
    global _own_start
    if _own_start is None:
        _own_start = _start_marker(os.getpid())
    return _own_start


def _same_start(actual: float, recorded: float) -> bool:
    if sys.platform.startswith("linux"):
        return actual == recorded
    return abs(actual - recorded) < _CREATE_TIME_TOLERANCE_S


def _alive(pid: int, start: float) -> bool:
    """True while the process that wrote the entry still runs."""
    try:
        actual = _start_marker(pid)
    except (FileNotFoundError, ProcessLookupError, psutil.NoSuchProcess):
        return False
    except (PermissionError, psutil.AccessDenied) as exc:
        # Another user's process: it exists, its start time cannot be read.
        logger.debug("Peer activity: start time of pid {} denied: {}", pid, exc)
        return True
    except (psutil.Error, OSError, ValueError, IndexError) as exc:
        logger.debug("Peer activity: cannot inspect pid {}: {}", pid, exc)
        return psutil.pid_exists(pid)
    return _same_start(actual, start)


# -- text helpers ------------------------------------------------------------


def clean_task(text: str | None) -> str:
    """The user's request without runtime-context blocks, on one short line."""
    remaining = text or ""
    parts: list[str] = []
    while remaining:
        start = remaining.find(RUNTIME_CONTEXT_TAG)
        if start < 0:
            parts.append(remaining)
            break
        parts.append(remaining[:start])
        end = remaining.find(RUNTIME_CONTEXT_END, start)
        if end < 0:
            break
        remaining = remaining[end + len(RUNTIME_CONTEXT_END):]
    flat = " ".join(" ".join(parts).split())
    return flat[:_TASK_CHARS]


def _short_key(session_key: str) -> str:
    return session_key if len(session_key) <= 24 else session_key[:21] + "..."


def surface_for(channel: str | None) -> str:
    """What the user calls the place a turn runs in."""
    name = (channel or "").strip() or "unknown"
    return _SURFACES.get(name, name)


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)} s"
    if seconds < 7200:
        return f"{int(seconds // 60)} min"
    return f"{int(seconds // 3600)} h"


# -- writing -----------------------------------------------------------------


def _write_entry(path: Path, data: dict[str, Any]) -> None:
    """Atomic replace: a reader sees the old entry or the new one, never half."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError as cleanup_error:
            logger.warning("Peer activity: cannot remove {}: {}", tmp_name, cleanup_error)
        raise


def _publish(turn: PeerTurn) -> None:
    """Write the turn's entry, unless the turn already ended (lock held)."""
    if turn.ended:
        return
    try:
        _write_entry(turn.path, turn.entry)
    except OSError as exc:
        logger.warning("Peer activity: cannot write {}: {}", turn.path, exc)


def _unlink(path: Path, reason: str) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Peer activity: cannot remove {} ({}): {}", path, reason, exc)


def _remove_own_entries() -> None:
    """atexit: a process that ends takes its claims with it."""
    with _active_lock:
        turns = list(_active.values())
        _active.clear()
    for turn in turns:
        with turn.lock:
            turn.ended = True
            _unlink(turn.path, "process exit")


def begin_turn(
    *,
    project: str | os.PathLike[str] | None,
    session_key: str | None,
    family: str | None = None,
    title: str | None = None,
    surface: str | None = None,
    task: str | None = None,
    publish: bool = True,
    resolved: bool = False,
) -> PeerTurn | None:
    """Register a running turn; None when there is no project or session.

    ``publish=False`` registers the turn without touching the disk, so an
    async caller can bind it at once and write the entry in a worker thread
    (see :func:`publish_turn`); :func:`end_turn` is safe either way.
    ``resolved=True`` says ``project`` already went through
    :func:`resolve_project` (which stats every component: run it off the loop).
    """
    global _atexit_registered
    if not project or not session_key:
        return None
    resolved_project = str(project) if resolved else _resolved_project(project)
    path = _project_dir(resolved_project) / _entry_name(os.getpid(), session_key)
    with _active_lock:
        existing = _active.get(path)
        if existing is not None and not existing.ended:
            # A nested turn of the same session in this process shares the
            # entry; the outer turn's end removes it.
            existing.refs += 1
            return existing
        if not _atexit_registered:
            atexit.register(_remove_own_entries)
            _atexit_registered = True
        try:
            start = _self_start()
            # Wall-clock start, for people reading the file; liveness compares
            # "start", which no clock correction moves.
            create_time = float(psutil.Process(os.getpid()).create_time())
        except (psutil.Error, OSError, ValueError, IndexError) as exc:
            logger.warning("Peer activity: own start time unavailable: {}", exc)
            return None
        now = time.time()
        turn = PeerTurn(
            project=resolved_project,
            session_key=session_key,
            family=family or session_key,
            path=path,
            entry={
                "pid": os.getpid(),
                "create_time": create_time,
                "start": start,
                "session_key": session_key,
                "family": family or session_key,
                "title": (title or "").strip()[:_TITLE_CHARS] or _short_key(session_key),
                "surface": (surface or "").strip() or "unknown",
                "project": resolved_project,
                "task": clean_task(task),
                "started_at": now,
                "updated_at": now,
                "files": {},
            },
        )
        _active[path] = turn
    if publish:
        publish_turn(turn)
    return turn


def publish_turn(turn: PeerTurn | None) -> None:
    """Write the turn's entry now (blocking file I/O: call it off the loop)."""
    if turn is None:
        return
    with turn.lock:
        _publish(turn)


def end_turn(turn: PeerTurn | None) -> None:
    """Drop the turn's entry: its claims vanish with it."""
    if turn is None:
        return
    with _active_lock:
        turn.refs -= 1
        if turn.refs > 0:
            return
        if _active.get(turn.path) is turn:
            del _active[turn.path]
    with turn.lock:
        turn.ended = True
        _unlink(turn.path, "turn end")


def current_turn() -> PeerTurn | None:
    """The registry turn of the agent running this task, None outside one."""
    return _current_turn.get()


def bind_turn(turn: PeerTurn | None) -> Token[PeerTurn | None]:
    return _current_turn.set(turn)


def reset_turn(token: Token[PeerTurn | None]) -> None:
    _current_turn.reset(token)


@contextmanager
def active_turn(**kwargs: Any) -> Iterator[PeerTurn | None]:
    """begin_turn + bind for the block, end_turn on the way out (any exit)."""
    turn = begin_turn(**kwargs)
    token = bind_turn(turn)
    try:
        yield turn
    finally:
        reset_turn(token)
        end_turn(turn)


def note_file_edit(path: str | os.PathLike[str], turn: PeerTurn | None = None) -> None:
    """Record that the agent of ``turn`` (default: the bound one) changed a file."""
    turn = turn or current_turn()
    if turn is None or turn.ended:
        return
    rel = _relative(turn.project, path)
    if not rel or rel == ".":
        return
    with turn.lock:
        if turn.ended:
            return
        now = time.time()
        files: dict[str, float] = turn.entry["files"]
        files.pop(rel, None)
        files[rel] = now
        while len(files) > _MAX_FILES_KEPT:
            files.pop(next(iter(files)))
        turn.entry["updated_at"] = now
        _publish(turn)


# -- reading -----------------------------------------------------------------


def _parse(raw: Any, path: Path) -> PeerEntry:
    """A validated entry; ValueError names what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("not a JSON object")
    pid = raw.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("bad pid")
    numbers = {}
    for key in ("create_time", "start", "started_at", "updated_at"):
        value = raw.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"bad {key}")
        numbers[key] = float(value)
    texts = {}
    for key in ("session_key", "family", "project"):
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"bad {key}")
        texts[key] = value
    files_raw = raw.get("files")
    if not isinstance(files_raw, dict):
        raise ValueError("bad files")
    files: dict[str, float] = {}
    for name, stamp in files_raw.items():
        if not isinstance(name, str) or not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
            raise ValueError("bad files entry")
        files[name] = float(stamp)
    return PeerEntry(
        pid=pid,
        create_time=numbers["create_time"],
        start=numbers["start"],
        session_key=texts["session_key"],
        family=texts["family"],
        title=str(raw.get("title") or _short_key(texts["session_key"])),
        surface=str(raw.get("surface") or "unknown"),
        project=texts["project"],
        task=str(raw.get("task") or ""),
        started_at=numbers["started_at"],
        updated_at=numbers["updated_at"],
        files=files,
        path=path,
    )


def live_entries(project: str | os.PathLike[str]) -> list[PeerEntry]:
    """Every live entry of the project, own ones included; dead ones are deleted."""
    directory = _project_dir(_resolved_project(project))
    try:
        listing = list(os.scandir(directory))
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Peer activity: cannot list {}: {}", directory, exc)
        return []
    entries: list[PeerEntry] = []
    now = time.time()
    for item in listing:
        path = Path(item.path)
        if item.name.startswith("."):
            if item.name.endswith(".tmp"):
                try:
                    if now - item.stat().st_mtime > _STALE_TEMP_S:
                        _unlink(path, "stale temp file")
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    logger.debug("Peer activity: cannot stat {}: {}", path, exc)
            continue
        if not item.name.endswith(".json"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue  # its turn ended between the listing and the read
        except OSError as exc:
            logger.warning("Peer activity: cannot read {}: {}", path, exc)
            continue
        except ValueError as exc:
            logger.warning("Peer activity: corrupt entry {} deleted: {}", path, exc)
            _unlink(path, "corrupt")
            continue
        try:
            entry = _parse(raw, path)
        except ValueError as exc:
            logger.warning("Peer activity: corrupt entry {} deleted: {}", path, exc)
            _unlink(path, "corrupt")
            continue
        if not _alive(entry.pid, entry.start):
            _unlink(path, "process gone")
            continue
        entries.append(entry)
    return entries


def peers(project: str | os.PathLike[str] | None, family: str | None) -> list[PeerEntry]:
    """Live turns of OTHER families in this project, most recently active first."""
    if not project:
        return []
    others = [entry for entry in live_entries(project) if entry.family != family]
    others.sort(key=lambda entry: entry.updated_at, reverse=True)
    return others


def claim_of(
    project: str | os.PathLike[str] | None,
    family: str | None,
    path: str | os.PathLike[str],
) -> PeerEntry | None:
    """The live peer of another family that edited ``path`` during its turn."""
    if not project:
        return None
    rel = _relative(_resolved_project(project), path)
    if not rel or rel == ".":
        return None
    owners = [entry for entry in peers(project, family) if rel in entry.files]
    if not owners:
        return None
    return max(owners, key=lambda entry: entry.files[rel])


# -- what the agent is told --------------------------------------------------


def _refusal_text(rel: str, owner: PeerEntry) -> str:
    ago = _age(time.time() - owner.files.get(rel, owner.updated_at))
    return (
        f"Not changed: {rel} is being edited by another agent right now "
        f"(session '{owner.title}', {owner.surface} pid {owner.pid}, last change {ago} ago). "
        "Leave it to them: finish your part and add one line at the end of your "
        "answer about what you needed in this file."
    )


def first_refusal(paths: list[Path] | list[str]) -> str | None:
    """Why the bound agent must not change these paths now, or None when it may.

    The registry is read once for the whole list: a recursive delete checks
    hundreds of files against the same few peers.
    """
    turn = current_turn()
    if turn is None or not paths:
        return None
    others = [entry for entry in peers(turn.project, turn.family) if entry.files]
    if not others:
        return None
    for path in paths:
        rel = _relative(turn.project, path)
        if not rel or rel == ".":
            continue
        owners = [entry for entry in others if rel in entry.files]
        if owners:
            return _refusal_text(rel, max(owners, key=lambda entry: entry.files[rel]))
    return None


def refusal_for(path: str | os.PathLike[str]) -> str | None:
    """Why the bound agent must not change ``path`` now, or None when it may."""
    return first_refusal([str(path)])


def peer_lines(entries: list[PeerEntry], *, now: float | None = None) -> list[str]:
    """The runtime-context lines for these peers; empty when there are none."""
    if not entries:
        return []
    now = time.time() if now is None else now
    lines = ["Other agents are working in this project right now (not your work):"]
    for entry in entries[:_MAX_PEERS_SHOWN]:
        ordered = sorted(entry.files, key=lambda name: entry.files[name], reverse=True)
        shown = ", ".join(ordered[:_MAX_FILES_SHOWN])
        extra = len(ordered) - _MAX_FILES_SHOWN
        if extra > 0:
            shown += f" (+{extra} more)"
        editing = f"editing: {shown}" if shown else "no file edited yet"
        task = f"{entry.task}; " if entry.task else ""
        lines.append(
            f"- '{entry.title}' ({entry.surface} pid {entry.pid}, "
            f"{_age(now - entry.started_at)}): {task}{editing}"
        )
    hidden = len(entries) - _MAX_PEERS_SHOWN
    if hidden > 0:
        lines.append(f"- and {hidden} more agent(s)")
    lines.append(
        "Their files are theirs: do not edit, revert, format or fix them. If your "
        "task needs one of them, or you notice a conflict, a problem in their area "
        "or a task of theirs left unfinished, finish your own part and end your "
        "answer with one short line per point, naming the session."
    )
    return lines


async def peer_activity_context_provider(
    request: RequestContext,
) -> RuntimeContextBlock | None:
    """Tell the model which agents of other sessions work in this project now."""
    workspace = request.workspace
    if not workspace:
        return None
    family = request.session_key or f"{request.channel}:{request.chat_id}"
    try:
        entries = await asyncio.wait_for(
            asyncio.to_thread(peers, workspace, family),
            timeout=_PROVIDER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("Peer activity: registry read exceeded {}s", _PROVIDER_TIMEOUT_S)
        return None
    content = wrap_runtime_context_lines(peer_lines(entries))
    if not content:
        return None
    return RuntimeContextBlock(source="peers", content=content)
