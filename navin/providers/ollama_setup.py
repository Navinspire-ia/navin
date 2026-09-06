"""Ollama local setup: detect, install, pull models, and configure Navin.

Used by Settings → Providers for a guided Detect → Install → Pull → Configure
flow. Installers call the platform package manager or the official Ollama
install script; model pulls go through the ``ollama`` CLI.
"""

from __future__ import annotations

import json
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
from navin.config.schema import ModelPresetConfig
from navin.providers.registry import find_by_name
from navin.utils.host import HostPlatform, host_platform
from navin.utils.proc import no_window_kwargs

_DEFAULT_API_BASE = "http://localhost:11434/v1"
_NATIVE_BASE = "http://127.0.0.1:11434"
_NATIVE_TAGS_URL = f"{_NATIVE_BASE}/api/tags"
_NATIVE_PULL_URL = f"{_NATIVE_BASE}/api/pull"
_PROBE_TIMEOUT_S = 2.5
_INSTALL_TIMEOUT_S = 600
_PULL_TIMEOUT_S = 1800
_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
_PRESET_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Live progress for long install/pull actions (read by concurrent status polls).
_job_lock = threading.Lock()
_active_job: dict[str, Any] | None = None


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

RECOMMENDED_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "llama3.2",
        "label": "Llama 3.2",
        "description": "General chat and light coding. Good default.",
        "size_hint": "~2 GB",
    },
    {
        "id": "qwen2.5-coder:7b",
        "label": "Qwen 2.5 Coder 7B",
        "description": "Strong local coding model.",
        "size_hint": "~4.7 GB",
    },
    {
        "id": "mistral",
        "label": "Mistral 7B",
        "description": "Fast general-purpose model.",
        "size_hint": "~4.1 GB",
    },
    {
        "id": "gemma3:4b",
        "label": "Gemma 3 4B",
        "description": "Small and efficient for everyday tasks.",
        "size_hint": "~3.3 GB",
    },
    {
        "id": "nomic-embed-text",
        "label": "Nomic Embed Text",
        "description": "Embeddings for free local semantic search.",
        "size_hint": "~274 MB",
    },
)


class OllamaSetupError(ValueError):
    """User-facing Ollama setup failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class OllamaBinary:
    path: str
    source: str  # path | common


def _common_ollama_binaries(plat: HostPlatform) -> list[Path]:
    home = Path.home()
    if plat == "windows":
        local_app = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        program_files = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        return [
            local_app / "Programs" / "Ollama" / "ollama.exe",
            program_files / "Ollama" / "ollama.exe",
        ]
    if plat == "macos":
        return [
            Path("/opt/homebrew/bin/ollama"),
            Path("/usr/local/bin/ollama"),
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    return [
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
        home / ".local" / "bin" / "ollama",
    ]


def resolve_ollama_binary() -> OllamaBinary | None:
    which = shutil.which("ollama")
    if which:
        return OllamaBinary(path=which, source="path")
    plat = host_platform()
    for candidate in _common_ollama_binaries(plat):
        if not candidate.is_file():
            continue
        if plat != "windows" and not os.access(candidate, os.X_OK):
            continue
        return OllamaBinary(path=str(candidate), source="common")
    return None


def _http_json(url: str, *, timeout_s: float = _PROBE_TIMEOUT_S) -> Any | None:
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None


def _list_installed_models() -> list[dict[str, Any]]:
    payload = _http_json(_NATIVE_TAGS_URL)
    if isinstance(payload, dict) and isinstance(payload.get("models"), list):
        rows: list[dict[str, Any]] = []
        for item in payload["models"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if not isinstance(name, str) or not name.strip():
                continue
            rows.append(
                {
                    "id": name.strip(),
                    "size": item.get("size"),
                    "modified_at": item.get("modified_at") or item.get("modified"),
                }
            )
        return rows

    payload = _http_json(f"{_DEFAULT_API_BASE.rstrip('/')}/models")
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            rows.append({"id": item["id"].strip(), "size": None, "modified_at": None})
        elif isinstance(item, str) and item.strip():
            rows.append({"id": item.strip(), "size": None, "modified_at": None})
    return rows


def _daemon_running() -> bool:
    return _http_json(_NATIVE_TAGS_URL) is not None


def _install_plan(plat: HostPlatform) -> dict[str, Any]:
    download_url = "https://ollama.com/download"
    if plat == "macos":
        if shutil.which("brew"):
            return {
                "method": "brew",
                "supported": True,
                "command": "brew install ollama",
                "download_url": download_url,
                "steps": [
                    "Install with Homebrew, or download the macOS app from ollama.com.",
                    "After install, open Ollama once so the local server starts.",
                ],
            }
        return {
            "method": "download",
            "supported": False,
            "command": None,
            "download_url": download_url,
            "steps": [
                "Download Ollama for macOS from ollama.com/download.",
                "Open the app once so the local server starts on port 11434.",
            ],
        }
    if plat == "windows":
        if shutil.which("winget"):
            return {
                "method": "winget",
                "supported": True,
                "command": "winget install -e --id Ollama.Ollama",
                "download_url": download_url,
                "steps": [
                    "Install with winget, or use the Windows installer from ollama.com.",
                    "Launch Ollama after install so the API listens on port 11434.",
                ],
            }
        return {
            "method": "download",
            "supported": False,
            "command": None,
            "download_url": download_url,
            "steps": [
                "Download the Windows installer from ollama.com/download.",
                "Launch Ollama after install so the API listens on port 11434.",
            ],
        }
    if plat == "wsl":
        return {
            "method": "script",
            "supported": True,
            "command": "curl -fsSL https://ollama.com/install.sh | sh",
            "download_url": download_url,
            "steps": [
                "Detected WSL: use the Linux install script inside this distro.",
                "If Ollama already runs on Windows and answers on localhost:11434, skip install and pull models from there.",
                "Then start the daemon with: ollama serve",
            ],
        }
    return {
        "method": "script",
        "supported": True,
        "command": "curl -fsSL https://ollama.com/install.sh | sh",
        "download_url": download_url,
        "steps": [
            "Run the official Linux install script from ollama.com.",
            "Then start the daemon with: ollama serve",
        ],
    }


def _configured_api_base() -> str | None:
    config = load_config()
    base = getattr(config.providers.ollama, "api_base", None)
    if isinstance(base, str) and base.strip():
        return base.strip()
    return None


def _default_api_base() -> str:
    spec = find_by_name("ollama")
    return (spec.default_api_base if spec and spec.default_api_base else None) or _DEFAULT_API_BASE


def _model_installed(model_id: str, installed_ids: set[str]) -> bool:
    if model_id in installed_ids:
        return True
    base = model_id.split(":", 1)[0]
    return any(
        row_id == base or row_id.startswith(f"{base}:") or row_id.startswith(f"{model_id}:")
        for row_id in installed_ids
    )


def _phase(*, installed: bool, running: bool, configured: bool) -> str:
    if running and configured:
        return "ready"
    if running:
        return "configure"
    if installed:
        return "start"
    return "install"


def ollama_status_payload() -> dict[str, Any]:
    plat = host_platform()
    binary = resolve_ollama_binary()
    running = _daemon_running()
    # Daemon reachable counts as installed even when the CLI is missing from PATH
    # (common on WSL when Ollama runs on the Windows host).
    binary_available = binary is not None
    installed = binary_available or running
    configured_base = _configured_api_base()
    installed_models = _list_installed_models() if running else []
    installed_ids = {row["id"] for row in installed_models}
    recommended = [
        {**model, "installed": _model_installed(model["id"], installed_ids)}
        for model in RECOMMENDED_MODELS
    ]
    default_base = _default_api_base()
    return {
        "provider": "ollama",
        "platform": plat,
        "phase": _phase(
            installed=installed,
            running=running,
            configured=bool(configured_base),
        ),
        "binary": binary.path if binary else None,
        "binary_source": binary.source if binary else None,
        "binary_available": binary_available,
        "installed": installed,
        "running": running,
        "configured": bool(configured_base),
        "api_base": configured_base,
        "default_api_base": default_base,
        "models": installed_models,
        "model_count": len(installed_models),
        "recommended_models": recommended,
        "install": _install_plan(plat),
        "active_job": _snapshot_active_job(),
        "fetched_at": time.time(),
    }


def _run(
    argv: list[str],
    *,
    timeout_s: float,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        input=input_text,
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


def install_ollama() -> dict[str, Any]:
    if resolve_ollama_binary() is not None or _daemon_running():
        payload = ollama_status_payload()
        payload["last_action"] = {
            "ok": True,
            "action": "install",
            "message": "Ollama is already installed.",
        }
        return payload

    plan = _install_plan(host_platform())
    if not plan["supported"] or not plan["command"]:
        raise OllamaSetupError(
            "Automatic install is not available on this host. "
            f"Download Ollama from {plan['download_url']}.",
            status=400,
        )

    method = plan["method"]
    _set_active_job(
        action="install",
        status="running",
        progress=None,
        message="Starting Ollama install...",
        detail=plan["command"],
    )
    try:
        if method == "brew":
            _set_active_job(message="Installing Ollama with Homebrew...")
            result = _run(["brew", "install", "ollama"], timeout_s=_INSTALL_TIMEOUT_S)
        elif method == "winget":
            _set_active_job(message="Installing Ollama with winget...")
            result = _run(
                [
                    "winget",
                    "install",
                    "-e",
                    "--id",
                    "Ollama.Ollama",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                timeout_s=_INSTALL_TIMEOUT_S,
            )
        elif method == "script":
            _set_active_job(message="Downloading the official Ollama install script...")
            script = _run(
                ["curl", "-fsSL", "https://ollama.com/install.sh"],
                timeout_s=120,
            )
            if script.returncode != 0:
                detail = _clip((script.stderr or script.stdout or ""), 400)
                raise OllamaSetupError(
                    f"Failed to download the Ollama install script.{(' ' + detail) if detail else ''}",
                    status=500,
                )
            _set_active_job(message="Running the Ollama install script (this can take a few minutes)...")
            result = _run(
                ["sh", "-s", "--"],
                timeout_s=_INSTALL_TIMEOUT_S,
                input_text=script.stdout or "",
            )
        else:
            raise OllamaSetupError("Unsupported install method.", status=400)
    except subprocess.TimeoutExpired as exc:
        _clear_active_job()
        raise OllamaSetupError("Ollama install timed out.", status=504) from exc
    except FileNotFoundError as exc:
        _clear_active_job()
        raise OllamaSetupError(
            f"Install tool missing. Download Ollama from {plan['download_url']}.",
            status=400,
        ) from exc
    except OllamaSetupError:
        _clear_active_job()
        raise
    except Exception:
        _clear_active_job()
        raise

    if result.returncode != 0 and resolve_ollama_binary() is None and not _daemon_running():
        _clear_active_job()
        detail = _clip((result.stderr or result.stdout or ""), 500)
        raise OllamaSetupError(
            f"Ollama install failed.{(' ' + detail) if detail else ''}",
            status=500,
        )

    _clear_active_job()
    payload = ollama_status_payload()
    payload["last_action"] = {
        "ok": True,
        "action": "install",
        "message": "Ollama installed. Start the daemon, then pull a model.",
        "output": _clip((result.stdout or "") + (result.stderr or ""), 800) or None,
    }
    return payload


def start_ollama() -> dict[str, Any]:
    if _daemon_running():
        payload = ollama_status_payload()
        payload["last_action"] = {
            "ok": True,
            "action": "start",
            "message": "Ollama is already running.",
        }
        return payload

    binary = resolve_ollama_binary()
    if binary is None:
        raise OllamaSetupError("Ollama is not installed yet.", status=400)

    plat = host_platform()
    _set_active_job(
        action="start",
        status="running",
        progress=None,
        message="Starting the Ollama daemon...",
    )
    try:
        if plat == "macos" and Path("/Applications/Ollama.app").exists():
            subprocess.Popen(  # noqa: S603
                ["open", "-a", "Ollama"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
        elif plat == "windows":
            app = Path(binary.path).with_name("Ollama.exe")
            target = str(app if app.is_file() else binary.path)
            subprocess.Popen(  # noqa: S603
                [target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
        else:
            subprocess.Popen(  # noqa: S603
                [binary.path, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                **no_window_kwargs(),
            )
    except OSError as exc:
        _clear_active_job()
        raise OllamaSetupError(f"Could not start Ollama: {exc}", status=500) from exc

    for _ in range(20):
        time.sleep(0.4)
        if _daemon_running():
            break

    _clear_active_job()
    payload = ollama_status_payload()
    if not payload["running"]:
        payload["last_action"] = {
            "ok": False,
            "action": "start",
            "message": "Started Ollama, but the API is not reachable yet on port 11434.",
        }
        return payload

    payload["last_action"] = {
        "ok": True,
        "action": "start",
        "message": "Ollama is running.",
    }
    return payload


def _validate_model_id(model: str) -> str:
    value = model.strip()
    if not value or _MODEL_ID_RE.fullmatch(value) is None:
        raise OllamaSetupError("Invalid model id.", status=400)
    return value


def _pull_progress_pct(event: dict[str, Any]) -> float | None:
    total = event.get("total")
    completed = event.get("completed")
    if not isinstance(total, (int, float)) or not isinstance(completed, (int, float)):
        return None
    if total <= 0:
        return None
    return max(0.0, min(100.0, (float(completed) / float(total)) * 100.0))


def _pull_via_http(model_id: str) -> None:
    """Stream ``/api/pull`` and publish live progress for status polls."""
    _set_active_job(
        action="pull",
        model=model_id,
        status="running",
        progress=0,
        message=f"Pulling {model_id}...",
    )
    timeout = httpx.Timeout(connect=10.0, read=_PULL_TIMEOUT_S, write=30.0, pool=10.0)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            with client.stream(
                "POST",
                _NATIVE_PULL_URL,
                json={"name": model_id, "stream": True},
                headers={"Accept": "application/x-ndjson, application/json"},
            ) as response:
                if response.status_code >= 400:
                    body = _clip(response.read().decode("utf-8", errors="replace"), 400)
                    raise OllamaSetupError(
                        f"Failed to pull {model_id}.{(' ' + body) if body else ''}",
                        status=500,
                    )
                last_status = f"Pulling {model_id}..."
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if isinstance(event.get("error"), str) and event["error"].strip():
                        raise OllamaSetupError(
                            f"Failed to pull {model_id}: {event['error'].strip()}",
                            status=500,
                        )
                    status_text = event.get("status")
                    if isinstance(status_text, str) and status_text.strip():
                        last_status = status_text.strip()
                    update: dict[str, Any] = {"message": last_status}
                    pct = _pull_progress_pct(event)
                    if pct is not None:
                        update["progress"] = pct
                    if status_text == "success":
                        update["progress"] = 100
                        update["message"] = f"Pulled {model_id}."
                    _set_active_job(**update)
    except httpx.TimeoutException as exc:
        raise OllamaSetupError(f"Pull timed out for {model_id}.", status=504) from exc
    except httpx.HTTPError as exc:
        raise OllamaSetupError(f"Failed to pull {model_id}: {exc}", status=500) from exc


def _pull_via_cli(model_id: str, binary: OllamaBinary) -> None:
    _set_active_job(
        action="pull",
        model=model_id,
        status="running",
        progress=None,
        message=f"Pulling {model_id} via ollama CLI (no live % available)...",
    )
    try:
        result = _run([binary.path, "pull", model_id], timeout_s=_PULL_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise OllamaSetupError(f"Pull timed out for {model_id}.", status=504) from exc
    if result.returncode != 0:
        detail = _clip((result.stderr or result.stdout or ""), 500)
        raise OllamaSetupError(
            f"Failed to pull {model_id}.{(' ' + detail) if detail else ''}",
            status=500,
        )


def pull_ollama_model(model: str) -> dict[str, Any]:
    model_id = _validate_model_id(model)
    if not _daemon_running():
        start_ollama()
        if not _daemon_running():
            raise OllamaSetupError(
                "Ollama is not running. Start it, then pull a model.",
                status=400,
            )

    binary = resolve_ollama_binary()
    try:
        # Prefer the HTTP pull API: works without a local CLI (WSL + Windows host)
        # and exposes download progress for the Settings UI.
        try:
            _pull_via_http(model_id)
        except OllamaSetupError as exc:
            # Retry via CLI only when the HTTP API itself is unreachable.
            detail = exc.message.lower()
            transport_issue = any(
                token in detail
                for token in ("connect", "unreachable", "refused", "timed out", "timeout")
            )
            if binary is None or not transport_issue:
                raise
            _pull_via_cli(model_id, binary)
    except Exception:
        _clear_active_job()
        raise

    _clear_active_job()
    payload = ollama_status_payload()
    payload["last_action"] = {
        "ok": True,
        "action": "pull",
        "model": model_id,
        "message": f"Pulled {model_id}.",
    }
    return payload


def _preset_slug(model_id: str, existing: dict[str, Any]) -> str:
    base = _PRESET_SLUG_RE.sub("-", model_id.lower()).strip("-") or "ollama-model"
    if len(base) > 48:
        base = base[:48].rstrip("-")
    if base == "default":
        base = "ollama-model"
    if base not in existing:
        return base
    for suffix in range(2, 100):
        candidate = f"{base[:44].rstrip('-')}-{suffix}"
        if candidate not in existing:
            return candidate
    raise OllamaSetupError("too many similar model configuration names", status=409)


def configure_ollama(*, model: str | None = None, make_active: bool = True) -> dict[str, Any]:
    """Write ``providers.ollama.apiBase`` when missing and optionally create a preset.

    Does not overwrite a custom ``apiBase`` the user already set (for example a
    LAN Ollama host). Returns setup status only; the WebUI layer attaches the
    full settings payload.
    """
    default_base = _default_api_base()
    config = load_config()
    changed = False
    if not (config.providers.ollama.api_base or "").strip():
        config.providers.ollama.api_base = default_base
        changed = True

    effective_base = (config.providers.ollama.api_base or "").strip() or default_base
    preset_name: str | None = None
    if model:
        model_id = _validate_model_id(model)
        for name, preset in config.model_presets.items():
            if preset.provider == "ollama" and preset.model == model_id:
                preset_name = name
                break
        if preset_name is None:
            preset_name = _preset_slug(model_id, config.model_presets)
            base = config.resolve_default_preset()
            config.model_presets[preset_name] = ModelPresetConfig(
                label=model_id,
                model=model_id,
                provider="ollama",
                max_tokens=min(base.max_tokens, 8192),
                context_window_tokens=min(base.context_window_tokens, 32768),
                temperature=base.temperature,
                reasoning_effort=None,
            )
            changed = True
        if make_active and config.agents.defaults.model_preset != preset_name:
            config.agents.defaults.model_preset = preset_name
            changed = True

    if changed:
        save_config(config)

    status = ollama_status_payload()
    status["last_action"] = {
        "ok": True,
        "action": "configure",
        "message": (
            f"Configured Ollama at {effective_base}"
            + (f" with model {model}." if model else ".")
        ),
        "preset": preset_name,
        "api_base": effective_base,
    }
    return status
