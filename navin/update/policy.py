# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One update policy for the Navin engine and the Tauri shell.

The signed manifest (navin/update/service.py) is the single authority: which
version a machine may install, whether a release is paused, whether the
version it runs was revoked and which older signed build replaces it. The
desktop shell installs Windows, macOS and AppImage updates with the official
Tauri updater, but it asks this module first, so a rollout percentage, a
suspension or a revocation applies the same way to every format.

Also here: the per-format install strategy, the local update history shown in
Settings, and the note that tells the Python engine the shell owns installs.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

CHANNELS: tuple[str, ...] = ("stable", "beta", "enterprise")
# Checked when the engine starts, then on this period.
CHECK_INTERVAL_S = 2 * 60 * 60
# A ready update installs only after the user left Navin alone this long.
IDLE_BEFORE_INSTALL_S = 10 * 60
# The shell reports on each of its checks (every 2 h): past this the note is
# stale (old shell, endpoint gone) and the Python engine installs again.
TAURI_ENGINE_TTL_S = 3 * 60 * 60
_HISTORY_MAX_LINES = 300

# Install kind -> (engine, how it installs). "tauri" kinds fall back to the
# Navin engine whenever the shell has not claimed them recently.
STRATEGIES: dict[str, tuple[str, str]] = {
    "windows-setup": ("tauri", "Signed NSIS installer in passive mode, then Navin restarts"),
    "windows-portable": ("navin", "Windowless helper swaps the folder after Navin exits, then restarts it"),
    "macos-app": ("tauri", "Signed .app archive replaces the bundle, then Navin relaunches"),
    "macos": ("navin", "File swap after exit, then relaunch"),
    "linux-appimage": ("tauri", "Signed AppImage replaces the file in place, then Navin relaunches"),
    "linux": ("navin", "File swap after exit, then relaunch"),
    "linux-deb": ("navin", "dpkg installs the signed .deb through pkexec after Navin exits"),
    "linux-rpm": ("navin", "rpm installs the signed package through pkexec after Navin exits"),
    "linux-pacman": ("navin", "pacman installs the signed package through pkexec after Navin exits"),
    "cli": ("navin", "The CLI tree is staged, probed, then swapped; `navin update` or the gateway"),
    "linux-package": ("manual", "Owned by an unknown package manager: update it there"),
    "source": ("manual", "Source checkout: git pull, or install the packaged CLI"),
}


def install_strategy(kind: str) -> dict[str, str]:
    engine, mode = STRATEGIES.get(kind, ("manual", "Download the latest installer from navin.live"))
    return {"installKind": kind, "engine": engine, "mode": mode}


def _version(value: Any) -> Version | None:
    try:
        return Version(str(value).strip())
    except InvalidVersion:
        return None


def is_suspended(manifest: dict[str, Any]) -> bool:
    """A paused release: published, signed, and installed by nobody."""
    return manifest.get("suspended") is True


def is_revoked(manifest: dict[str, Any], version: str) -> bool:
    """Whether the running version was withdrawn by the publisher."""
    current = _version(version)
    if current is None:
        return False
    revoked = manifest.get("revokedVersions") or []
    if not isinstance(revoked, list):
        return False
    return any(_version(item) == current for item in revoked)


def rollback_offer(
    manifest: dict[str, Any], current_version: str, data_version: int,
) -> dict[str, Any] | None:
    """The signed older build to go back to, when the running one is revoked.

    Only a version strictly older than the running one, that is not revoked
    itself, and that can read the local data (its dataVersion is at least the
    one on disk): a rollback that cannot open the sessions is a second outage.
    """
    if not is_revoked(manifest, current_version):
        return None
    rollback = manifest.get("rollback")
    if not isinstance(rollback, dict):
        return None
    target = _version(rollback.get("version"))
    current = _version(current_version)
    if target is None or current is None or target >= current:
        return None
    if is_revoked(manifest, str(target)):
        return None
    try:
        target_data = int(rollback.get("dataVersion", 1))
    except (TypeError, ValueError):
        return None
    if target_data < data_version:
        return None
    if not isinstance(rollback.get("artifacts"), dict):
        return None
    return rollback


def decide(
    manifest: dict[str, Any],
    *,
    current_version: str,
    candidate_version: str,
    in_rollout: bool,
) -> dict[str, Any]:
    """May this machine install ``candidate_version``? Asked by the Tauri shell."""
    latest = str(manifest.get("version", "")).strip()
    candidate = _version(candidate_version)
    current = _version(current_version)
    if candidate is None or current is None:
        return {"allowed": False, "reason": "invalid version", "mandatory": False}
    mandatory = is_revoked(manifest, current_version)
    minimum = _version(manifest.get("minimumVersion") or "")
    if minimum is not None and current < minimum:
        mandatory = True
    rollback = manifest.get("rollback") if isinstance(manifest.get("rollback"), dict) else {}
    if candidate < current:
        # A downgrade is only ever the signed rollback of a revoked version.
        allowed = _version(rollback.get("version")) == candidate and mandatory
        return {
            "allowed": bool(allowed),
            "reason": "rollback" if allowed else "downgrade not allowed",
            "mandatory": mandatory,
            "rollback": bool(allowed),
        }
    if candidate == current:
        return {"allowed": False, "reason": "already installed", "mandatory": False}
    if _version(latest) != candidate:
        return {"allowed": False, "reason": "not the signed release", "mandatory": mandatory}
    if is_suspended(manifest):
        return {"allowed": False, "reason": "release suspended", "mandatory": mandatory}
    if is_revoked(manifest, candidate_version):
        return {"allowed": False, "reason": "release revoked", "mandatory": mandatory}
    if not in_rollout and not mandatory:
        return {"allowed": False, "reason": "not in rollout yet", "mandatory": False}
    return {"allowed": True, "reason": "", "mandatory": mandatory}


# --- local state under ~/.navin/updates ---------------------------------------


def updates_dir() -> Path:
    return Path.home() / ".navin" / "updates"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def history_path() -> Path:
    return updates_dir() / "history.jsonl"


def record_event(
    event: str,
    *,
    version: str = "",
    engine: str = "navin",
    detail: str = "",
    ok: bool = True,
) -> dict[str, Any]:
    """Append one line to the update history (Settings > Updates reads it)."""
    entry = {
        "at": time.time(),
        "event": str(event)[:40],
        "version": str(version)[:40],
        "engine": str(engine)[:20],
        "ok": bool(ok),
        "detail": str(detail)[:500],
    }
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _trim_history(path)
    return entry


def _trim_history(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > _HISTORY_MAX_LINES:
        _write_atomic(path, "\n".join(lines[-_HISTORY_MAX_LINES:]) + "\n")


def read_history(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. A corrupt line is skipped, the rest of the history stays."""
    path = history_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return list(reversed(rows))[: max(1, limit)]


def _engine_path() -> Path:
    return updates_dir() / "engine.json"


def note_engine(engine: str, version: str = "") -> dict[str, Any]:
    """The shell says it owns installs for its formats (sent after each check)."""
    if engine not in {"tauri", "navin"}:
        raise ValueError("unknown update engine")
    payload = {"engine": engine, "version": str(version)[:40], "at": time.time()}
    _write_atomic(_engine_path(), json.dumps(payload))
    return payload


def tauri_engine_active(now: float | None = None) -> bool:
    try:
        payload = json.loads(_engine_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # No note, or one the shell left half-written: Navin installs itself.
        return False
    if not isinstance(payload, dict) or payload.get("engine") != "tauri":
        return False
    try:
        at = float(payload.get("at") or 0)
    except (TypeError, ValueError):
        return False
    return ((now if now is not None else time.time()) - at) < TAURI_ENGINE_TTL_S


def engine_for(kind: str, now: float | None = None) -> str:
    """Who installs this format right now: the shell, Navin, or the user."""
    engine, _mode = STRATEGIES.get(kind, ("manual", ""))
    if engine == "tauri" and not tauri_engine_active(now):
        return "navin"
    return engine
