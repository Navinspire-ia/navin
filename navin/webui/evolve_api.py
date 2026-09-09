# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bridge between the WebUI gateway and the navin-engine daemon.

The Rust daemon (crates/navin-engine) owns proofs, benchmarks, fixes and
promotions for a project workspace. It persists every artefact as JSON under
``<project>/.navin/`` and serves a line-delimited JSON-RPC protocol over a
loopback TCP port whose number and access token it publishes in
``<project>/.navin/evolve/endpoint.json``. This module gives the gateway a
synchronous view of both: artefact files are read directly (they exist with
or without a daemon), while campaign submission goes through the socket.

The transport used to be a Unix domain socket, which meant Evolve simply did
not exist on Windows: CPython has no ``AF_UNIX`` there and the engine had no
listener to offer. A loopback port is the one transport all three platforms
and both languages speak, and it is also the only one that crosses the WSL
boundary - a socket file on the ``\\\\wsl.localhost`` share cannot be
connected to, while a port inside a distribution is forwarded to the Windows
host. What the socket file gave for free, access control, is now explicit:
the token in the endpoint file is required on the first frame of every
connection.

Actions with side effects (merge, rollback, verify) shell out to the engine
binary so the gate logic (Ed25519 authenticity, fast-forward only, clean
tree) always runs in exactly one place: the Rust code.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from navin.utils import wsl
from navin.utils.proc import detached_no_window_kwargs, no_window_kwargs

ENGINE_ENDPOINT_RELPATH = Path(".navin") / "evolve" / "endpoint.json"

# Artefact directories written by the engine, newest-first in the overview.
_ARTEFACT_DIRS = {
    "proofs": "proofs",
    "diagnoses": "diagnoses",
    "optimize_runs": "optimize",
    "evolve_runs": "evolve-runs",
    "fix_reports": "fixes",
}

_MAX_ITEMS_PER_KIND = 12
_SOCKET_TIMEOUT_S = 10.0
_CONNECT_TIMEOUT_S = 3.0
_CLI_TIMEOUT_S = 120.0
# WSL and cold starts routinely take more than 8s. The WebUI client waits 35s.
DAEMON_START_WAIT_S = 25.0

# Why the daemon is not answering, as a stable code the UI can translate.
DAEMON_REASON_NOT_RUNNING = "not_running"
DAEMON_REASON_UNREACHABLE = "unreachable"
# No longer produced: every platform Navin runs on can host a daemon since
# the engine moved to a loopback port. Kept because a new dashboard may still
# be talking to an older gateway that reports it.
DAEMON_REASON_UNSUPPORTED = "unsupported"


class EvolveApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def daemon_transport_supported() -> bool:
    """Whether this interpreter can reach a daemon at all.

    Only ordinary TCP over loopback is needed now, so the honest answer is
    yes everywhere - the check survives as the single place that would say
    otherwise if a runtime ever turned up without ``AF_INET``.
    """
    return hasattr(socket, "AF_INET")


def _project_root(raw_path: str) -> Path:
    if not raw_path:
        raise EvolveApiError(400, "missing `path`")
    root = Path(raw_path).expanduser()
    if not root.is_absolute():
        raise EvolveApiError(400, "`path` must be absolute")
    try:
        root = root.resolve()
    except OSError as exc:
        raise EvolveApiError(400, f"unresolvable path: {exc}") from exc
    if not root.is_dir():
        raise EvolveApiError(404, f"no such directory: {root}")
    return root


def resolve_engine_bin() -> str | None:
    """Locate the navin-engine binary: env override, the copy a packaged
    build ships under the resources tree, PATH, then the local release
    build (the development layout)."""
    override = os.environ.get("NAVIN_ENGINE_BIN", "").strip()
    if override and Path(override).is_file():
        return override
    name = "navin-engine.exe" if os.name == "nt" else "navin-engine"
    bundled = Path(__file__).resolve().parents[1] / "resources" / "bin" / name
    if bundled.is_file():
        # Data files unpacked by the installer may have lost the exec bit.
        if os.name != "nt" and not os.access(bundled, os.X_OK):
            try:
                bundled.chmod(bundled.stat().st_mode | 0o755)
            except OSError:
                bundled = None
        if bundled is not None:
            return str(bundled)
    found = shutil.which("navin-engine")
    if found:
        return found
    local = (
        Path(__file__).resolve().parents[2]
        / "crates"
        / "navin-engine"
        / "target"
        / "release"
        / name
    )
    if local.is_file():
        return str(local)
    return None


def _daemon_argv(root: Path) -> tuple[list[str], str | None]:
    """The command that starts a daemon for ``root``, and its working
    directory (``None`` when the launcher picks its own).

    A project opened from Windows at ``\\\\wsl.localhost\\Ubuntu\\...`` is a
    Linux project: its virtualenv, its ``node_modules`` and the commands the
    engine has to run for proofs all belong to the distribution. Starting a
    Windows daemon on the UNC path would produce runs that fail in ways that
    read like a broken project, so the daemon is started *inside* the
    distribution instead - the same routing as :func:`_git_argv` in
    :mod:`navin.webui.project_search`.

    That daemon binds a loopback port inside the distribution and writes its
    endpoint file on the distribution's filesystem, which the Windows host
    reads straight through the redirector. WSL 2 forwards loopback ports to
    the host, so the gateway dials ``127.0.0.1`` and lands in the guest with
    no extra plumbing.

    ``bash -lc`` rather than a bare command, because a login shell is what
    puts the distribution's own toolchain on PATH - including the engine.
    """
    location = wsl.parse_unc(str(root)) if sys.platform == "win32" else None
    if location is not None:
        if not wsl.wsl_executable():
            raise EvolveApiError(
                501,
                "this project lives in a WSL distribution and wsl.exe is not "
                "available to reach it",
            )
        import shlex

        distro = wsl.resolve_distro(location.distro) or location.distro
        inner = f"exec navin-engine daemon {shlex.quote(location.posix)}"
        return [*wsl.command_prefix(distro, location.path), "bash", "-lc", inner], None
    binary = resolve_engine_bin()
    if not binary:
        raise EvolveApiError(
            501,
            "navin-engine binary not found (set NAVIN_ENGINE_BIN or install it on PATH)",
        )
    return [binary, "daemon", str(root)], str(root)


def _terminate(pid: int) -> bool:
    """Ask the daemon at ``pid`` to stop, whatever the platform.

    Windows has no signal to send a detached process: ``os.kill`` there ends
    up as ``TerminateProcess`` for anything but the console-group signals,
    and the daemon is deliberately detached from any console. ``taskkill /T``
    ends the tree instead. For a WSL-hosted project that tree is the
    ``wsl.exe`` relay rather than the Linux process, which is why the IPC
    shutdown above is the path that matters there.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return True
    try:
        os.kill(pid, signal.SIGINT)
    except OSError:
        return False
    return True


# -- Daemon endpoint --------------------------------------------------------


def endpoint_path(root: Path) -> Path:
    return root / ENGINE_ENDPOINT_RELPATH


def read_endpoint(root: Path) -> dict[str, Any] | None:
    """Where this workspace's daemon listens, or ``None`` if none does.

    The file is written by the daemon at bind time and removed when it stops,
    so its absence is the ordinary "not started" answer rather than an error.
    Every field is validated here: a truncated or half-written file must read
    as "no daemon", never as an address to dial.
    """
    try:
        data = json.loads(endpoint_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("transport", "tcp") != "tcp":
        return None
    port = data.get("port")
    token = data.get("token")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return None
    if not isinstance(token, str) or not token:
        return None
    host = data.get("host")
    # Never dial an address the daemon did not pick: the file lives in a
    # project directory, and a project directory can come from anywhere.
    if host not in ("127.0.0.1", "::1", "localhost", None, ""):
        return None
    return {"host": host or "127.0.0.1", "port": port, "token": token, "pid": data.get("pid")}


def clear_endpoint(root: Path) -> None:
    """Drop a stale endpoint file, so a restart is not raced by the old one."""
    try:
        endpoint_path(root).unlink()
    except OSError:
        pass


def daemon_call(root: Path, method: str, params: Any) -> Any:
    """One-shot request to the daemon; raises EvolveApiError(503) when no
    daemon serves this workspace.

    The token from the endpoint file rides on the request frame, so this is
    still a single write and a single read - the daemon authenticates the
    connection from that first frame.
    """
    endpoint = read_endpoint(root)
    if endpoint is None:
        raise EvolveApiError(
            503,
            f"no engine daemon serves this workspace ({endpoint_path(root)} is absent)",
        )
    request = (
        json.dumps(
            {"id": 1, "method": method, "params": params, "token": endpoint["token"]}
        )
        + "\n"
    )
    try:
        client = socket.create_connection(
            (endpoint["host"], endpoint["port"]), timeout=_CONNECT_TIMEOUT_S
        )
    except OSError as exc:
        raise EvolveApiError(503, f"engine daemon not reachable: {exc}") from exc
    try:
        client.settimeout(_SOCKET_TIMEOUT_S)
        client.sendall(request.encode("utf-8"))
        buffer = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                raise EvolveApiError(502, "daemon closed the connection without answering")
            buffer += chunk
            # The daemon interleaves event frames; scan every complete line
            # for the response that matches our request id.
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if frame.get("id") != 1:
                    continue  # event frame or someone else's response
                error = frame.get("error")
                if error:
                    raise _daemon_error(error)
                return frame.get("result")
    except socket.timeout as exc:
        raise EvolveApiError(504, "daemon did not answer in time") from exc
    except OSError as exc:
        raise EvolveApiError(503, f"engine daemon not reachable: {exc}") from exc
    finally:
        client.close()


def _daemon_error(error: dict[str, Any]) -> EvolveApiError:
    """Turn a daemon error frame into a shaped gateway error.

    A rejected token is deliberately not a 401: nothing about the user's
    session is wrong. It means the endpoint file and the process listening on
    that port disagree, which a restart fixes.
    """
    message = error.get("message", "unknown") if isinstance(error, dict) else "unknown"
    if isinstance(error, dict) and error.get("code") == "unauthorized":
        return EvolveApiError(
            502,
            "the daemon refused the endpoint token; stop it and start it again",
        )
    return EvolveApiError(502, f"daemon error: {message}")


def daemon_snapshot(root: Path) -> dict[str, Any]:
    """The daemon state the dashboard renders, and never an exception.

    A dashboard that cannot reach the daemon still has artefacts to show, so
    an unreachable daemon is a state, not a failure. ``reason`` says which
    one: nothing is running, or something answered badly.

    ``supported`` is what used to carry the Windows verdict. It survives
    because dashboards branch on it, and because it is still the honest
    answer for a runtime with no TCP at all - which is no runtime Navin
    currently ships on.
    """
    if not daemon_transport_supported():
        return {
            "online": False,
            "status": None,
            "supported": False,
            "reason": DAEMON_REASON_UNSUPPORTED,
        }
    try:
        status = daemon_call(root, "engine.status", {})
    except EvolveApiError as exc:
        return {
            "online": False,
            "status": None,
            "supported": True,
            "reason": (
                DAEMON_REASON_NOT_RUNNING
                if exc.status == 503
                else DAEMON_REASON_UNREACHABLE
            ),
        }
    return {"online": True, "status": status, "supported": True, "reason": None}


def daemon_status(raw_path: str) -> dict[str, Any]:
    return daemon_snapshot(_project_root(raw_path))


# -- Artefact files ---------------------------------------------------------


def _read_artefacts(root: Path, subdir: str) -> list[dict[str, Any]]:
    directory = root / ".navin" / subdir
    if not directory.is_dir():
        return []
    entries: list[tuple[float, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["_file"] = path.name
            entries.append((mtime, data))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [data for _, data in entries[:_MAX_ITEMS_PER_KIND]]


def _config_value(section: Any, *keys: str) -> Any:
    """A config value under any of its accepted spellings.

    The schema accepts both camelCase and snake_case, so a hand-written
    config must not empty the picker just because of its key style.
    """
    if not isinstance(section, dict):
        return None
    for key in keys:
        if key in section:
            return section[key]
    return None


def _model_presets() -> tuple[list[str], str | None]:
    """Model presets from the user config, for the campaign LLM picker.

    Every text preset is offered whatever its provider: the bridge resolves
    the choice through the normal provider factory, so a managed catalog
    model, a BYOK one and a local Ollama one are all equally usable.
    """
    try:
        from navin.config.loader import get_config_path

        config_path = get_config_path()
    except Exception:
        config_path = Path.home() / ".navin" / "config.json"
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    presets = _config_value(config, "modelPresets", "model_presets")
    names: list[str] = []
    if isinstance(presets, dict):
        for name, entry in sorted(presets.items()):
            if not isinstance(entry, dict):
                continue
            # The bridge needs a text model: skip image/video/audio presets
            # and the ones the user disabled.
            if entry.get("modality") not in (None, "text"):
                continue
            if entry.get("enabled") is False:
                continue
            names.append(name)
    defaults = _config_value(config, "agents") or {}
    default = _config_value(
        _config_value(defaults, "defaults") or {}, "modelPreset", "model_preset"
    )
    return names, default if isinstance(default, str) and default else None


# Default dev-server port per detected framework, used to suggest the probe
# URL when no service topology declares one.
_FRAMEWORK_PORTS = {
    "flask": 5000,
    "fastapi": 8000,
    "django": 8000,
    "express": 3000,
    "nest": 3000,
    "next": 3000,
    "nuxt": 3000,
    "react": 3000,
    "svelte": 5173,
    "vite": 5173,
}


def _manifest_hint(root: Path) -> dict[str, Any] | None:
    """What the engine's inspector knows about this project: detected
    commands, framework and a suggested probe URL. Best effort."""
    manifest: Any = None
    try:
        manifest = daemon_call(root, "project.inspect", {"path": str(root)})
    except EvolveApiError:
        binary = resolve_engine_bin()
        if binary:
            try:
                completed = subprocess.run(
                    [binary, "inspect", str(root)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    **no_window_kwargs(),
                )
                if completed.returncode == 0:
                    manifest = json.loads(completed.stdout)
            except (OSError, ValueError, subprocess.SubprocessError):
                manifest = None
    if not isinstance(manifest, dict):
        return None
    units = manifest.get("units") or []
    unit = units[0] if units and isinstance(units[0], dict) else {}
    commands = unit.get("commands") or {}
    framework = unit.get("framework")
    port = None
    for service in manifest.get("services") or []:
        if isinstance(service, dict) and service.get("kind") == "app" and service.get("ports"):
            port = service["ports"][0]
            break
    if port is None and isinstance(framework, str):
        port = _FRAMEWORK_PORTS.get(framework)
    # The engine resolves the start command across units, Procfile and
    # Makefile; the first unit is only a fallback for older daemons.
    start = manifest.get("start_command") or commands.get("start") or commands.get("dev")
    guessed = f"http://127.0.0.1:{port}/" if port else None
    return {
        "framework": framework,
        "start": start,
        "test": commands.get("test"),
        # A URL already observed beats any guess: it is the one a run uses.
        "url": _probed_url(root, start) or guessed,
    }


def _probed_url(root: Path, start: str | None) -> str | None:
    """The URL the engine measured last time it booted this exact command."""
    if not start:
        return None
    try:
        cached = json.loads((root / ".navin" / "evolve" / "probe-url.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or cached.get("start") != start:
        return None
    url = cached.get("url")
    return url if isinstance(url, str) else None


def overview(raw_path: str) -> dict[str, Any]:
    """Everything the dashboard needs in one round trip: daemon state,
    recent artefacts of every kind, and the promotion history."""
    root = _project_root(raw_path)
    status = daemon_snapshot(root)

    preset_names, default_preset = _model_presets()
    payload: dict[str, Any] = {
        "root": str(root),
        "daemon": status,
        "engine_bin": resolve_engine_bin(),
        "model_presets": preset_names,
        "default_preset": default_preset,
        "hint": _manifest_hint(root),
        "autorun": autorun_state(root),
        "promotions": _read_artefacts(root, "promotions"),
    }
    for key, subdir in _ARTEFACT_DIRS.items():
        payload[key] = _read_artefacts(root, subdir)
    return payload


# -- Engine CLI actions -----------------------------------------------------


def _run_engine_cli(args: list[str]) -> Any:
    binary = resolve_engine_bin()
    if not binary:
        raise EvolveApiError(
            501,
            "navin-engine binary not found (set NAVIN_ENGINE_BIN or install it on PATH)",
        )
    try:
        completed = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLI_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise EvolveApiError(504, "engine command timed out") from exc
    except OSError as exc:
        raise EvolveApiError(500, f"cannot run the engine binary: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        # anyhow errors land on stderr as "Error: ..."; keep the tail only.
        raise EvolveApiError(409, detail.splitlines()[-1] if detail else "engine command failed")
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise EvolveApiError(502, "engine returned non-JSON output") from exc


def verify_certificate(raw_path: str, promotion_id: str) -> Any:
    root = _project_root(raw_path)
    if not promotion_id:
        raise EvolveApiError(400, "missing `id`")
    return _run_engine_cli(["verify-cert", str(root), "--id", promotion_id])


def merge_promotion(raw_path: str, promotion_id: str) -> Any:
    root = _project_root(raw_path)
    if not promotion_id:
        raise EvolveApiError(400, "missing `id`")
    return _run_engine_cli(["merge", str(root), "--id", promotion_id])


def publish_promotion(raw_path: str, promotion_id: str) -> Any:
    """Push the promotion branch and open a pull request for it.

    The engine uses the GitHub CLI when it is installed, and otherwise
    returns a compare link, so this never dead-ends on a missing token.
    """
    root = _project_root(raw_path)
    if not promotion_id:
        raise EvolveApiError(400, "missing `id`")
    return _run_engine_cli(["pr", str(root), "--id", promotion_id])


def rollback_promotion(raw_path: str, promotion_id: str) -> Any:
    root = _project_root(raw_path)
    if not promotion_id:
        raise EvolveApiError(400, "missing `id`")
    return _run_engine_cli(["rollback", str(root), "--id", promotion_id])


def start_daemon(raw_path: str) -> dict[str, Any]:
    """Spawn a detached engine daemon for the workspace and wait for it to
    publish an endpoint. Idempotent: an already-serving daemon is reported as
    such."""
    root = _project_root(raw_path)
    try:
        daemon_call(root, "engine.status", {})
        return {"started": False, "online": True}
    except EvolveApiError:
        pass

    argv, cwd = _daemon_argv(root)
    # A daemon that died without cleaning up leaves an endpoint file pointing
    # at a dead port. Left in place, the wait below would keep reading it and
    # report a timeout instead of the daemon that has just come up.
    clear_endpoint(root)
    log_dir = root / ".navin" / "evolve"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "daemon.log", "ab")
    except OSError as exc:
        raise EvolveApiError(500, f"cannot prepare {log_dir}: {exc}") from exc
    try:
        with log_file:
            process = subprocess.Popen(
                argv,
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                cwd=cwd,
                **detached_no_window_kwargs(),
            )
        # Remembered so stop_daemon can signal daemons that predate the
        # engine.shutdown IPC method (or whose endpoint file vanished).
        (log_dir / "daemon.pid").write_text(str(process.pid), encoding="utf-8")
    except OSError as exc:
        raise EvolveApiError(500, f"cannot spawn the daemon: {exc}") from exc

    deadline = time.monotonic() + DAEMON_START_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.25)
        try:
            daemon_call(root, "engine.status", {})
            return {"started": True, "online": True}
        except EvolveApiError:
            continue
    raise EvolveApiError(
        502,
        "daemon did not come up in time; check .navin/evolve/daemon.log",
    )


def _autorun_path(root: Path) -> Path:
    return root / ".navin" / "evolve" / "autorun.json"


def autorun_state(root: Path) -> dict[str, Any]:
    try:
        data = json.loads(_autorun_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False, "kind": None}
    return {"enabled": bool(data.get("enabled")), "kind": data.get("kind")}


def _save_autorun(root: Path, kind: str, params: dict[str, Any]) -> None:
    """Remember the last launched operation so the daemon's commit watcher
    can replay it. The enabled flag is the user's choice and is preserved."""
    if kind == "baseline.run":
        return
    path = _autorun_path(root)
    enabled = False
    try:
        enabled = bool(json.loads(path.read_text(encoding="utf-8")).get("enabled"))
    except (OSError, ValueError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"enabled": enabled, "kind": kind, "params": params}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def set_autorun(raw_path: str, enable: bool) -> dict[str, Any]:
    """Toggle the on-commit auto-run. Without a previously launched
    operation, default to a quick robustness proof built from detection."""
    root = _project_root(raw_path)
    path = _autorun_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if "kind" not in data:
        # Nothing launched yet: a quick robustness proof, with the engine
        # working out how to start the app and where to probe it.
        params: dict[str, Any] = {"path": str(root), "profile": "quick"}
        data.update({"kind": "proof.run", "params": params})
    data["enabled"] = enable
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        raise EvolveApiError(500, f"cannot persist the auto-run setting: {exc}") from exc
    return {"enabled": bool(data["enabled"]), "kind": data.get("kind")}


def docs(lang: str) -> dict[str, Any]:
    """Serve the Evolve dashboard documentation shipped in the repo."""
    repo_root = Path(__file__).resolve().parents[2]
    base = repo_root / "docs" / "navin_evolve"
    path = base / "fr" / "README.md" if (lang or "").startswith("fr") else base / "README.md"
    if not path.is_file():
        path = base / "README.md"
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvolveApiError(404, f"documentation not found: {exc}") from exc
    return {"markdown": markdown, "lang": "fr" if "fr" in path.parts else "en"}


def stop_daemon(raw_path: str) -> dict[str, Any]:
    """Ask the daemon to shut down (IPC first, recorded PID as fallback)
    and wait for its endpoint to disappear."""
    root = _project_root(raw_path)
    if read_endpoint(root) is None:
        return {"stopped": False, "online": False}

    stopped_via_ipc = False
    try:
        daemon_call(root, "engine.shutdown", {})
        stopped_via_ipc = True
    except EvolveApiError:
        pass

    if not stopped_via_ipc:
        pid_path = root / ".navin" / "evolve" / "daemon.pid"
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            raise EvolveApiError(
                409,
                f"daemon did not accept shutdown and no usable PID file: {exc}",
            ) from exc
        if not _terminate(pid):
            raise EvolveApiError(
                409, f"daemon did not accept shutdown and process {pid} could not be ended"
            )

    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if read_endpoint(root) is None:
            return {"stopped": True, "online": False}
        try:
            daemon_call(root, "engine.status", {})
        except EvolveApiError:
            # A daemon ended by signal never retracted its own file; leaving
            # it would make the next snapshot dial a dead port.
            clear_endpoint(root)
            return {"stopped": True, "online": False}
    raise EvolveApiError(502, "daemon is still up after the shutdown request")


# -- Campaign submission ----------------------------------------------------

_CAMPAIGN_KINDS = {
    "proof.run",
    "baseline.run",
    "diagnose.run",
    "evolve.run",
    "optimize.run",
}

_NUMERIC_PARAMS = {"duration", "concurrency", "max_variants", "max_findings", "diff_vectors", "repeats"}
_FLOAT_PARAMS = {"min_gain"}
_STRING_PARAMS = {"start", "url", "profile", "objective", "test", "preset"}
# `dirty` proves the working tree as it stands (pending, uncommitted fixes
# included) instead of the last commit: the review panel's "Prove this change".
_BOOL_PARAMS = {"dirty"}

# Campaigns that need a model to invent candidates. A proof only breaks and
# measures, so it never calls one.
_GENERATIVE_KINDS = {"evolve.run", "optimize.run"}


def _bridge_command() -> str | None:
    """The command the daemon runs to ask a model for candidates.

    The engine never talks to a provider itself: it shells out to this
    bridge, which resolves the preset through navin's normal provider
    factory. The command is built here, never taken from the request, so a
    browser cannot ask the daemon to run something of its choosing.
    """
    try:
        from navin.python_runtime import python_command

        argv = [*python_command(), "-m", "navin.evolve.bridge"]
    except Exception:
        return None
    if os.name == "nt":
        # The engine runs the command through `cmd /C`, which knows double
        # quotes only.
        return " ".join(f'"{part}"' if " " in part else part for part in argv)
    import shlex

    return shlex.join(argv)


def enqueue_campaign(raw_path: str, kind: str, query: dict[str, list[str]]) -> Any:
    """Submit a campaign to the daemon. The daemon must be running: long
    jobs belong to its scheduler, not to a gateway thread."""
    root = _project_root(raw_path)
    if kind not in _CAMPAIGN_KINDS:
        raise EvolveApiError(400, f"unknown campaign kind `{kind}`")

    params: dict[str, Any] = {"path": str(root)}
    for key, values in query.items():
        value = values[0] if values else ""
        if not value:
            continue
        if key in _STRING_PARAMS:
            params[key] = value
        elif key in _NUMERIC_PARAMS:
            try:
                params[key] = int(value)
            except ValueError as exc:
                raise EvolveApiError(400, f"`{key}` must be an integer") from exc
        elif key in _FLOAT_PARAMS:
            try:
                params[key] = float(value)
            except ValueError as exc:
                raise EvolveApiError(400, f"`{key}` must be a number") from exc
        elif key in _BOOL_PARAMS:
            params[key] = value.lower() in {"1", "true", "yes"}

    # `start` and `url` are deliberately not filled in here: the engine
    # resolves whatever is missing from the project itself, and finds the
    # probe URL by booting the app once in a shadow.
    if kind in _GENERATIVE_KINDS:
        bridge = _bridge_command()
        if bridge:
            params["generator"] = bridge
    result = daemon_call(root, "job.enqueue", {"kind": kind, "params": params})
    _save_autorun(root, kind, params)
    return result


def cancel_job(raw_path: str, raw_id: str) -> Any:
    """Stop a queued or running campaign. The daemon undoes the shadow and
    kills the app it started, so a stop leaves nothing behind."""
    root = _project_root(raw_path)
    try:
        job_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise EvolveApiError(400, "`id` must be a job number") from exc
    if job_id <= 0:
        raise EvolveApiError(400, "`id` must be a job number")
    return daemon_call(root, "job.cancel", {"id": job_id})
