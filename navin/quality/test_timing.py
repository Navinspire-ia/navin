# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How long a test run or a command usually takes, so the journal can say it before it ends.

A long suite or build with no hint looks frozen. Each finished run is
remembered, and the next one starts with a measured estimate and a quick / long
label instead of a guess:

- ``test_run`` per runner and target, in ``<project>/.navin/test-timing.json``;
- shell commands per folder and command, in ``<data dir>/command-timing.json``
  (a command can run in any folder, which must not get files written into it).

When the runner prints no percent (output piped into ``tail``, vitest, jest),
the elapsed share of the usual duration gives an estimated one.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from loguru import logger

# Under 2 minutes a run reads as quick; at or over it, the user needs a time to wait.
LONG_RUN_SECONDS = 120.0
# An estimate never claims the end: the last stretch waits for the real exit.
ESTIMATE_CAP_PERCENT = 95.0
_KEEP = 5
_MAX_COMMANDS = 300
_FILE = Path(".navin") / "test-timing.json"
_COMMAND_FILE = "command-timing.json"

# Output filters and trailers that do not change what the command does.
_OUTPUT_PIPE = re.compile(r"\s*\|\s*(?:tail|head)\b[^|;&]*", re.IGNORECASE)
_REDIRECT = re.compile(r"\s*2>&1")
_ECHO_TRAILER = re.compile(r"\s*;\s*echo\b[^;&|]*$")


def _key(runner: str | None, target: str | None) -> str:
    return f"{runner or '*'}::{target or '*'}"


def _path(root: Path) -> Path:
    return root / _FILE


def _command_path() -> Path:
    from navin.config.paths import get_data_dir

    return get_data_dir() / _COMMAND_FILE


def _load_file(path: Path) -> dict[str, list[float]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.debug("run timing history unreadable: {}", path, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): [float(v) for v in values if isinstance(v, (int, float))]
        for key, values in raw.items()
        if isinstance(values, list)
    }


def _save_file(path: Path, history: dict[str, list[float]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=1), encoding="utf-8")
    except OSError:
        logger.debug("run timing history not saved: {}", path, exc_info=True)


def _median(values: list[float] | None) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _append(history: dict[str, list[float]], key: str, seconds: float) -> None:
    previous = history.pop(key, None) or []
    history[key] = [*previous, round(seconds, 1)][-_KEEP:]


def expected_seconds(root: Path, runner: str | None, target: str | None) -> float | None:
    """Median of the last runs of this runner and target, None when never run."""
    return _median(_load_file(_path(root)).get(_key(runner, target)))


def run_kind(seconds: float | None) -> str | None:
    """``quick`` or ``long`` for a known duration, None while it is unknown."""
    if seconds is None:
        return None
    return "long" if seconds >= LONG_RUN_SECONDS else "quick"


def record_run(root: Path, runner: str | None, target: str | None, seconds: float) -> None:
    """Remember one finished test run. Best-effort: a read-only project just skips it."""
    if seconds <= 0:
        return
    history = _load_file(_path(root))
    _append(history, _key(runner, target), seconds)
    _save_file(_path(root), history)


def command_key(command: str, cwd: str | Path | None) -> str:
    """The same command in the same folder, whatever filter its output goes through.

    ``pytest -q | tail -25`` and ``pytest -q 2>&1`` take the same time.
    """
    text = _OUTPUT_PIPE.sub("", command or "")
    text = _REDIRECT.sub("", text)
    text = _ECHO_TRAILER.sub("", text)
    text = " ".join(text.split())
    return f"{cwd or ''}::{text}"[:600]


def expected_command_seconds(key: str) -> float | None:
    """Median of the last runs of this command in this folder, None when never run."""
    return _median(_load_file(_command_path()).get(key))


def record_command_run(key: str, seconds: float) -> None:
    """Remember one finished command; only the most recent commands are kept."""
    if seconds <= 0 or not key:
        return
    path = _command_path()
    history = _load_file(path)
    _append(history, key, seconds)
    while len(history) > _MAX_COMMANDS:
        history.pop(next(iter(history)))
    _save_file(path, history)


def estimated_percent(elapsed_s: float, expected_s: float | None) -> float | None:
    """Share of the usual duration already spent, capped below 100 until it really ends."""
    if not expected_s or expected_s <= 0 or elapsed_s < 0:
        return None
    return min(ESTIMATE_CAP_PERCENT, elapsed_s / expected_s * 100.0)


def duration_hint(seconds: float) -> str:
    """``quick`` or ``long ~4 min`` for the CLI row of a running test or command."""
    if run_kind(seconds) == "long":
        return f"long ~{max(1, round(seconds / 60))} min"
    return "quick"
