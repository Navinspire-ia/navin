"""Persisted first-run onboarding state for the WebUI.

Stored under the instance data dir (``~/.navin/webui/onboarding.json``) so an
upgrade or a cleared browser localStorage does not re-run the welcome wizard
when the install is already configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from navin.config.paths import get_webui_dir

_MAX_STATE_FILE_BYTES = 8 * 1024
# "omniroute" / "ollama" are the keyless local flavours of the Free path.
_ALLOWED_PATHS = frozenset({"account", "byok", "free", "omniroute", "ollama", "skip"})


def webui_onboarding_path() -> Path:
    return get_webui_dir() / "onboarding.json"


def default_webui_onboarding() -> dict[str, Any]:
    return {"completed": False}


def normalize_webui_onboarding(raw: Any) -> dict[str, Any]:
    state = default_webui_onboarding()
    if not isinstance(raw, dict):
        return state
    state["completed"] = bool(raw.get("completed"))
    completed_at = raw.get("completedAt") or raw.get("completed_at")
    if isinstance(completed_at, str) and completed_at.strip():
        state["completedAt"] = completed_at.strip()
    language = raw.get("language")
    if isinstance(language, str) and language.strip():
        state["language"] = language.strip()[:16]
    path = raw.get("path")
    if path in _ALLOWED_PATHS:
        state["path"] = path
    if "demoOpened" in raw or "demo_opened" in raw:
        state["demoOpened"] = bool(raw.get("demoOpened", raw.get("demo_opened")))
    return state


def read_webui_onboarding() -> dict[str, Any]:
    path = webui_onboarding_path()
    if not path.exists():
        return default_webui_onboarding()
    try:
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > _MAX_STATE_FILE_BYTES:
            logger.warning("webui onboarding state too large, ignoring: {}", path)
            return default_webui_onboarding()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("read webui onboarding failed {}: {}", path, e)
        return default_webui_onboarding()
    return normalize_webui_onboarding(raw)


def write_webui_onboarding(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_webui_onboarding(raw)
    path = webui_onboarding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    if len(payload.encode("utf-8")) > _MAX_STATE_FILE_BYTES:
        raise ValueError("onboarding state is too large")
    path.write_text(payload, encoding="utf-8")
    return state
