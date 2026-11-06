# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Log of the model calls that ended an agent step on an error.

One JSON line per failure in ``<logs>/model-errors.jsonl``: when, which
session and model, the class of the error (timeout, connection, HTTP status,
code), whether it could clear by itself, and what the provider said, with
anything shaped like a key or a token masked.

The CLI keeps no log by default. On 2026-11-06 subagents stopped on "Sorry, I
encountered an error calling the AI model." after hours of work, and nothing
on disk could tell a model that never answered from a refused request.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.config.paths import get_logs_dir

LOG_NAME = "model-errors.jsonl"
# Past this size the file moves to ``.1`` and starts over: bounded on a
# machine that runs many CLIs for weeks, and still readable.
MAX_BYTES = 5 * 1024 * 1024
_TEXT_MAX_CHARS = 400
_LOCK = threading.Lock()
_KEY_PREFIXED = re.compile(r"\b(sk|pk|rk|ak)-[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}")
_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\"',;}]{4,}"
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")


def mask_secrets(text: str) -> str:
    """*text* with key- and token-shaped values replaced by ``***``."""
    masked = _KEY_PREFIXED.sub(lambda match: match.group(1) + "-***", text)
    masked = _BEARER.sub("Bearer ***", masked)
    masked = _NAMED_SECRET.sub(lambda match: match.group(1) + match.group(2) + "***", masked)
    return _LONG_TOKEN.sub("***", masked)


def log_path() -> Path:
    return get_logs_dir() / LOG_NAME


def record_model_error(entry: dict[str, Any]) -> bool:
    """Append one failure; False, with a warning, when the log cannot be written.

    Empty values are left out and text is masked and cut, so a line never
    carries a key the provider echoed back.
    """
    row: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pid": os.getpid()}
    for key, value in entry.items():
        if value is None or value == "":
            continue
        row[key] = mask_secrets(value)[:_TEXT_MAX_CHARS] if isinstance(value, str) else value
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    path = log_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                os.replace(path, path.with_name(LOG_NAME + ".1"))
            # One write in append mode: several CLIs share the file.
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError as exc:
        logger.warning("Could not record the model error in {}: {}", path, exc)
        return False
    return True
