# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""This Navin installation's identity, told by its health endpoints.

Several Navins can share one machine's ports: the Windows desktop, a Windows
CLI, and a Linux Navin in WSL, whose localhost is forwarded to Windows. A
launcher that finds its port taken asks /health who answers. The same id is
this installation (attach to it); another id is another Navin, so this one
takes free ports instead of attaching to it with the wrong secret and
showing a broken app. The id lives next to the config (``instance-id``), so
the desktop shell reads the very same file (desktop/src-tauri/src/main.rs).
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from pathlib import Path

INSTANCE_FILE = "instance-id"
# Ports tried after the configured pair, one step at a time for both.
PORT_SLIDE_TRIES = 20

_cached: dict[Path, str] = {}


def instance_id_path(config_path: Path | None = None) -> Path:
    if config_path is None:
        from navin.config.loader import get_config_path

        config_path = get_config_path()
    return Path(config_path).expanduser().parent / INSTANCE_FILE


def read_instance_id(config_path: Path | None = None) -> str | None:
    try:
        value = instance_id_path(config_path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value or None


def instance_id(config_path: Path | None = None) -> str:
    """This installation's id, created on first use."""
    path = instance_id_path(config_path)
    cached = _cached.get(path)
    if cached:
        return cached
    value = read_instance_id(config_path)
    if not value:
        value = uuid.uuid4().hex
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{INSTANCE_FILE}.{os.getpid()}.tmp")
            tmp.write_text(value, encoding="ascii")
            # Two first starts at once: the first file wins, both use it.
            if path.exists():
                tmp.unlink(missing_ok=True)
                value = read_instance_id(config_path) or value
            else:
                os.replace(tmp, path)
        except OSError:
            pass
    _cached[path] = value
    return value


def health_payload() -> dict[str, str]:
    """What every Navin health endpoint answers."""
    try:
        return {"status": "ok", "instance": instance_id()}
    except Exception:  # noqa: BLE001 - health must answer whatever happens
        return {"status": "ok"}


def answering_instance(host: str, port: int, *, timeout_s: float = 0.6) -> tuple[bool, str | None]:
    """(answers as a Navin, its instance id). Older Navins answer without an id."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout_s) as response:
            if response.status != 200:
                return False, None
            payload = json.loads(response.read(4096).decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False, None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return False, None
    instance = payload.get("instance")
    return True, str(instance) if instance else None


def is_own_navin(host: str, port: int, config_path: Path | None = None) -> bool | None:
    """True: this installation answers there. False: another Navin. None: no Navin answers."""
    answers, instance = answering_instance(host, port)
    if not answers:
        return None
    if instance is None:
        return True  # a Navin from before ids: assumed to be this one, as before
    return instance == read_instance_id(config_path)


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def free_port_pair(webui_port: int, gateway_port: int) -> tuple[int, int] | None:
    """The next pair after the given one where both ports are free."""
    for step in range(1, PORT_SLIDE_TRIES + 1):
        webui, gateway = webui_port + step, gateway_port + step
        if webui <= 65535 and gateway <= 65535 and port_free(webui) and port_free(gateway):
            return webui, gateway
    return None


def ports_for_this_navin(
    webui_port: int, gateway_port: int, config_path: Path | None = None,
) -> tuple[int, int, str]:
    """The pair this installation should use, and why it moved ("" when it did not).

    Ports held by this installation stay (the caller attaches to it, or reports
    it as before when it is half started); ports held only by another Navin or
    another program move to the next free pair.
    """
    owners = []
    for port in (webui_port, gateway_port):
        if port_free(port):
            continue
        owners.append(is_own_navin("127.0.0.1", port, config_path))
    if not owners or any(owner is True for owner in owners):
        return webui_port, gateway_port, ""
    pair = free_port_pair(webui_port, gateway_port)
    if pair is None:
        return webui_port, gateway_port, ""
    reason = "another_navin" if any(owner is False for owner in owners) else "busy"
    return pair[0], pair[1], reason


# Ports kept for Navin, never for an app the agent starts: the WebUI and engine
# of a source checkout (8765 / 18790), of the desktop (8766 / 18791) and the
# next pairs it moves to when another Navin holds them, plus the Vite ports of
# Navin's own interface in development (5173-5176).
NAVIN_DEFAULT_PORTS = (5173, 5174, 5175, 5176, 8765, 8766, 8767, 8768, 18790, 18791, 18792, 18793)


def navin_reserved_ports(config_path: Path | None = None) -> list[int]:
    """Ports an app started by the agent must never take: Navin's defaults and
    the ones this installation really uses (read from its config, which also
    holds a pair moved away from another Navin)."""
    ports = set(NAVIN_DEFAULT_PORTS)
    try:
        if config_path is None:
            from navin.config.loader import get_config_path

            config_path = get_config_path()
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        websocket = ((data.get("channels") or {}).get("websocket") or {}).get("port")
        gateway = (data.get("gateway") or {}).get("port")
        for value in (websocket, gateway):
            if isinstance(value, int) and 0 < value < 65536:
                ports.add(value)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return sorted(ports)
