# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OmniRoute local setup: detect, install, start, and configure Navin.

Used by the first-run wizard (Free > OmniRoute) and Settings > Providers for a
guided Detect -> Install -> Start -> Configure flow. OmniRoute ships as an npm
package: ``npm install -g omniroute`` puts an ``omniroute`` binary on PATH and
running it serves the dashboard plus the OpenAI-compatible API on port 20128.
A fresh install answers ``model: auto`` without any key, so "configure" only
has to pin the endpoint and create the preset.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from navin.config.loader import load_config, save_config
from navin.config.paths import get_logs_dir
from navin.config.schema import ModelPresetConfig
from navin.providers.omniroute import (
    OMNIROUTE_AUTO_COMBOS,
    OMNIROUTE_DEFAULT_API_BASE,
    omniroute_keyless_catalog,
    omniroute_root,
)
from navin.providers.registry import find_by_name
from navin.utils.host import host_platform
from navin.utils.proc import detached_no_window_kwargs, no_window_kwargs

OMNIROUTE_REPO_URL = "https://github.com/diegosouzapw/OmniRoute"
OMNIROUTE_SITE_URL = "https://omniroute.online"
OMNIROUTE_INSTALL_COMMAND = "npm install -g omniroute"
OMNIROUTE_START_COMMAND = "omniroute"
OMNIROUTE_DEFAULT_MODEL = "auto"
# package.json engines: >=22.22.2 <23 || >=24.0.0 <27
OMNIROUTE_NODE_REQUIREMENT = "Node.js 22 or 24 (LTS)"
NODE_DOWNLOAD_URL = "https://nodejs.org/en/download"

_PROBE_TIMEOUT_S = 2.5
_INSTALL_TIMEOUT_S = 900
# The Next.js server boots in a few seconds; the first run also initializes
# its SQLite database, so leave headroom before reporting "not reachable".
_START_WAIT_S = 40.0
_START_POLL_S = 0.5
_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
_PRESET_SLUG_RE = re.compile(r"[^a-z0-9]+")
_NODE_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")

# Live progress for install/start (read by concurrent status polls).
_job_lock = threading.Lock()
_active_job: dict[str, Any] | None = None
# ``npm prefix -g`` costs a process spawn; remember it for the whole session.
_npm_prefix_cache: str | None = None
_npm_prefix_checked = False


class OmniRouteSetupError(ValueError):
    """User-facing OmniRoute setup failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class OmniRouteBinary:
    path: str
    source: str  # path | npm-prefix | common


def _set_active_job(**fields: Any) -> None:
    global _active_job
    with _job_lock:
        current = dict(_active_job or {})
        current.update(fields)
        current["updated_at"] = time.time()
        if "started_at" not in current:
            current["started_at"] = current["updated_at"]
        _active_job = current


def _clear_active_job() -> None:
    global _active_job
    with _job_lock:
        _active_job = None


def _snapshot_active_job() -> dict[str, Any] | None:
    with _job_lock:
        return dict(_active_job) if _active_job else None


def _run(
    argv: list[str],
    *,
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
        **no_window_kwargs(),
    )


def _clip(text: str, limit: int) -> str:
    return text.strip()[:limit]


# --- detection -------------------------------------------------------------


def _default_api_base() -> str:
    spec = find_by_name("omniroute")
    return (spec.default_api_base if spec and spec.default_api_base else None) or (
        OMNIROUTE_DEFAULT_API_BASE
    )


def _configured_api_base(config: Any | None = None) -> str | None:
    config = config or load_config()
    base = getattr(config.providers.omniroute, "api_base", None)
    if isinstance(base, str) and base.strip():
        return base.strip()
    return None


def _probe_base() -> str:
    return _configured_api_base() or _default_api_base()


def _npm_global_prefix() -> str | None:
    global _npm_prefix_cache, _npm_prefix_checked
    if _npm_prefix_checked:
        return _npm_prefix_cache
    _npm_prefix_checked = True
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        result = _run([npm, "prefix", "-g"], timeout_s=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    prefix = (result.stdout or "").strip()
    _npm_prefix_cache = prefix or None
    return _npm_prefix_cache


def _forget_npm_prefix() -> None:
    global _npm_prefix_cache, _npm_prefix_checked
    _npm_prefix_cache = None
    _npm_prefix_checked = False


def _common_binaries() -> list[Path]:
    home = Path.home()
    if host_platform() == "windows":
        app_data = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
        return [app_data / "npm" / "omniroute.cmd"]
    return [
        home / ".npm-global" / "bin" / "omniroute",
        Path("/opt/homebrew/bin/omniroute"),
        Path("/usr/local/bin/omniroute"),
        home / ".local" / "bin" / "omniroute",
    ]


def resolve_omniroute_binary() -> OmniRouteBinary | None:
    which = shutil.which("omniroute")
    if which:
        return OmniRouteBinary(path=which, source="path")
    windows = host_platform() == "windows"
    for candidate in _common_binaries():
        if candidate.is_file() and (windows or os.access(candidate, os.X_OK)):
            return OmniRouteBinary(path=str(candidate), source="common")
    prefix = _npm_global_prefix()
    if prefix:
        # npm puts global shims in <prefix>/bin on POSIX and in <prefix> itself
        # on Windows.
        candidate = (
            Path(prefix) / "omniroute.cmd" if windows else Path(prefix) / "bin" / "omniroute"
        )
        if candidate.is_file() and (windows or os.access(candidate, os.X_OK)):
            return OmniRouteBinary(path=str(candidate), source="npm-prefix")
    return None


def _node_version() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        result = _run([node, "--version"], timeout_s=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    version = (result.stdout or "").strip()
    return version or None


def _node_supported(version: str | None) -> bool | None:
    """None when unknown; otherwise whether OmniRoute's engines range accepts it."""
    if not version:
        return None
    match = _NODE_VERSION_RE.match(version)
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    if major == 22:
        return (minor, patch) >= (22, 2)
    return 24 <= major < 27


def _health_url(api_base: str) -> str:
    return f"{omniroute_root(api_base)}/api/health"


def server_running(api_base: str | None = None) -> bool:
    """True when an OmniRoute server answers on *api_base* (default port)."""
    base = api_base or _probe_base()
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT_S, trust_env=False) as client:
            response = client.get(_health_url(base), headers={"Accept": "application/json"})
        if response.status_code == 200:
            return True
    except httpx.HTTPError:
        return False
    # A locked-down build may gate /api/health; the read-only combo route is
    # public on every version we know of.
    return omniroute_keyless_catalog(base, timeout=_PROBE_TIMEOUT_S) is not None


def _install_plan() -> dict[str, Any]:
    node_version = _node_version()
    node_ok = _node_supported(node_version)
    npm = shutil.which("npm")
    return {
        "method": "npm",
        # Automatic install needs npm on PATH and a Node the package accepts.
        "supported": bool(npm) and node_ok is not False,
        "command": OMNIROUTE_INSTALL_COMMAND,
        "start_command": OMNIROUTE_START_COMMAND,
        "node_version": node_version,
        "node_supported": node_ok,
        "node_requirement": OMNIROUTE_NODE_REQUIREMENT,
        "node_download_url": NODE_DOWNLOAD_URL,
        "repo_url": OMNIROUTE_REPO_URL,
        "site_url": OMNIROUTE_SITE_URL,
        "steps": [
            f"Install {OMNIROUTE_NODE_REQUIREMENT} if it is missing.",
            f"Run `{OMNIROUTE_INSTALL_COMMAND}` in a terminal.",
            f"Run `{OMNIROUTE_START_COMMAND}` to start the gateway on port 20128.",
        ],
    }


def _phase(*, installed: bool, running: bool, configured: bool) -> str:
    if running and configured:
        return "ready"
    if running:
        return "configure"
    if installed:
        return "start"
    return "install"


def _combo_rows() -> list[dict[str, Any]]:
    return [
        {"id": model_id, "label": label, "description": description}
        for model_id, label, description in OMNIROUTE_AUTO_COMBOS
    ]


def omniroute_status_payload() -> dict[str, Any]:
    config = load_config()
    configured_base = _configured_api_base(config)
    probe_base = configured_base or _default_api_base()
    binary = resolve_omniroute_binary()
    running = server_running(probe_base)
    catalog = omniroute_keyless_catalog(probe_base) if running else None
    models = catalog or []
    combo_ids = {combo[0] for combo in OMNIROUTE_AUTO_COMBOS}
    upstream = [row for row in models if row.get("id") not in combo_ids]
    preset_name = next(
        (name for name, preset in config.model_presets.items() if preset.provider == "omniroute"),
        None,
    )
    active_preset = (config.agents.defaults.model_preset or "").strip()
    return {
        "provider": "omniroute",
        "platform": host_platform(),
        "phase": _phase(
            installed=binary is not None or running,
            running=running,
            configured=bool(configured_base),
        ),
        "binary": binary.path if binary else None,
        "binary_source": binary.source if binary else None,
        "installed": binary is not None or running,
        "running": running,
        "configured": bool(configured_base),
        "api_base": configured_base,
        "default_api_base": _default_api_base(),
        "dashboard_url": omniroute_root(probe_base),
        "default_model": OMNIROUTE_DEFAULT_MODEL,
        "combos": _combo_rows(),
        "models": upstream,
        "model_count": len(upstream),
        "free_model_count": sum(1 for row in upstream if row.get("free")),
        "preset": preset_name,
        "active": bool(preset_name) and active_preset == preset_name,
        "install": _install_plan(),
        "active_job": _snapshot_active_job(),
        "fetched_at": time.time(),
    }


# --- actions ---------------------------------------------------------------


def install_omniroute() -> dict[str, Any]:
    if resolve_omniroute_binary() is not None or server_running():
        payload = omniroute_status_payload()
        payload["last_action"] = {
            "ok": True,
            "action": "install",
            "message": "OmniRoute is already installed.",
        }
        return payload

    plan = _install_plan()
    npm = shutil.which("npm")
    if not npm:
        raise OmniRouteSetupError(
            f"npm was not found. Install {OMNIROUTE_NODE_REQUIREMENT} from "
            f"{NODE_DOWNLOAD_URL}, then run `{OMNIROUTE_INSTALL_COMMAND}`.",
            status=400,
        )
    if plan["node_supported"] is False:
        raise OmniRouteSetupError(
            f"Node {plan['node_version']} is too old for OmniRoute. Install "
            f"{OMNIROUTE_NODE_REQUIREMENT} from {NODE_DOWNLOAD_URL} and try again.",
            status=400,
        )

    _set_active_job(
        action="install",
        status="running",
        progress=None,
        message="Installing OmniRoute with npm (this can take a few minutes)...",
        detail=OMNIROUTE_INSTALL_COMMAND,
    )
    try:
        result = _run([npm, "install", "-g", "omniroute"], timeout_s=_INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        _clear_active_job()
        raise OmniRouteSetupError("OmniRoute install timed out.", status=504) from exc
    except OSError as exc:
        _clear_active_job()
        raise OmniRouteSetupError(f"Could not run npm: {exc}", status=500) from exc
    except Exception:
        _clear_active_job()
        raise

    _forget_npm_prefix()
    binary = resolve_omniroute_binary()
    if result.returncode != 0 and binary is None:
        _clear_active_job()
        detail = _clip((result.stderr or result.stdout or ""), 500)
        hint = ""
        if "EACCES" in detail or "permission denied" in detail.lower():
            hint = (
                " npm's global folder is not writable: run the command in a "
                "terminal with the right permissions, or set a user prefix "
                "(`npm config set prefix ~/.npm-global`)."
            )
        raise OmniRouteSetupError(
            f"OmniRoute install failed.{(' ' + detail) if detail else ''}{hint}",
            status=500,
        )

    _clear_active_job()
    payload = omniroute_status_payload()
    payload["last_action"] = {
        "ok": True,
        "action": "install",
        "message": "OmniRoute installed. Start it, then let Navin use the auto model.",
        "output": _clip((result.stdout or "") + (result.stderr or ""), 800) or None,
    }
    return payload


def _server_log_path() -> Path:
    logs = get_logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "omniroute-server.log"


def start_omniroute() -> dict[str, Any]:
    if server_running():
        payload = omniroute_status_payload()
        payload["last_action"] = {
            "ok": True,
            "action": "start",
            "message": "OmniRoute is already running.",
        }
        return payload

    binary = resolve_omniroute_binary()
    if binary is None:
        raise OmniRouteSetupError("OmniRoute is not installed yet.", status=400)

    _set_active_job(
        action="start",
        status="running",
        progress=None,
        message="Starting the OmniRoute gateway...",
        detail=binary.path,
    )
    try:
        log_handle = open(_server_log_path(), "ab")  # noqa: SIM115 - handed to the child
        try:
            # The gateway must outlive this request: detach it from the Navin
            # process group (no console on Windows, own session elsewhere) and
            # keep its output in a log file for diagnosis.
            subprocess.Popen(  # noqa: S603
                [binary.path],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path.home()),
                **detached_no_window_kwargs(),
            )
        finally:
            log_handle.close()
    except OSError as exc:
        _clear_active_job()
        raise OmniRouteSetupError(f"Could not start OmniRoute: {exc}", status=500) from exc

    deadline = time.monotonic() + _START_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(_START_POLL_S)
        if server_running():
            break

    _clear_active_job()
    payload = omniroute_status_payload()
    if not payload["running"]:
        payload["last_action"] = {
            "ok": False,
            "action": "start",
            "message": (
                "Started OmniRoute, but nothing answers on port 20128 yet. "
                f"It may still be booting; see {_server_log_path()}."
            ),
        }
        return payload
    payload["last_action"] = {
        "ok": True,
        "action": "start",
        "message": "OmniRoute is running.",
    }
    return payload


def _validate_model_id(model: str) -> str:
    value = model.strip()
    if not value or _MODEL_ID_RE.fullmatch(value) is None:
        raise OmniRouteSetupError("Invalid model id.", status=400)
    return value


def _preset_slug(model_id: str, existing: dict[str, Any]) -> str:
    base = _PRESET_SLUG_RE.sub("-", model_id.lower()).strip("-") or "model"
    base = f"omniroute-{base}"
    if len(base) > 48:
        base = base[:48].rstrip("-")
    if base not in existing:
        return base
    for suffix in range(2, 100):
        candidate = f"{base[:44].rstrip('-')}-{suffix}"
        if candidate not in existing:
            return candidate
    raise OmniRouteSetupError("too many similar model configuration names", status=409)


def _preset_label(model_id: str) -> str:
    for combo_id, label, _description in OMNIROUTE_AUTO_COMBOS:
        if combo_id == model_id:
            return f"OmniRoute {label} (free)"
    return f"OmniRoute {model_id}"


def configure_omniroute(
    *,
    model: str | None = OMNIROUTE_DEFAULT_MODEL,
    make_active: bool = True,
) -> dict[str, Any]:
    """Pin ``providers.omniroute.apiBase`` and create the preset for *model*.

    Keeps a custom ``apiBase`` the user already set (a LAN gateway, Docker
    on another port). With *make_active* the new preset becomes the chat
    default. Returns setup status only; the WebUI layer attaches the full
    settings payload.
    """
    default_base = _default_api_base()
    config = load_config()
    changed = False
    if not (config.providers.omniroute.api_base or "").strip():
        config.providers.omniroute.api_base = default_base
        changed = True
    effective_base = (config.providers.omniroute.api_base or "").strip() or default_base

    preset_name: str | None = None
    if model:
        model_id = _validate_model_id(model)
        for name, preset in config.model_presets.items():
            if preset.provider == "omniroute" and preset.model == model_id:
                preset_name = name
                break
        if preset_name is None:
            preset_name = _preset_slug(model_id, config.model_presets)
            base = config.resolve_default_preset()
            config.model_presets[preset_name] = ModelPresetConfig(
                label=_preset_label(model_id),
                model=model_id,
                provider="omniroute",
                max_tokens=base.max_tokens,
                context_window_tokens=base.context_window_tokens,
                temperature=base.temperature,
                reasoning_effort=None,
            )
            changed = True
        defaults = config.agents.defaults
        if make_active and defaults.model_preset != preset_name:
            defaults.model_preset = preset_name
            defaults.model = ""
            defaults.provider = "omniroute"
            changed = True

    if changed:
        save_config(config)

    status = omniroute_status_payload()
    status["last_action"] = {
        "ok": True,
        "action": "configure",
        "message": (
            f"Configured OmniRoute at {effective_base}"
            + (f" with model {model}." if model else ".")
        ),
        "preset": preset_name,
        "api_base": effective_base,
    }
    return status
