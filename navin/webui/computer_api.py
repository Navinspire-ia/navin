# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Computer diagnostics and desktop stop controls shared by WebUI hosts."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from navin.computer.base import Check, ComputerError
from navin.computer.detect import create_backend, detect_platform
from navin.computer.policy import engage_stop, release_stop, stop_reason
from navin.config.loader import load_config


def computer_diagnostics(*, permission: str | None = None, passive: bool = False) -> dict[str, Any]:
    """Probe the local gateway's desktop; never return screen or window content."""
    config = load_config().tools.computer
    # A permission button opens only the requested consent flow. Pixel/window
    # checks belong to the final explicit check, after the user has returned.
    passive = passive or permission is not None
    choice = detect_platform(preferred=config.backend, display=config.display)
    checks: list[Check] = []
    screen = None
    backend = None
    try:
        backend = create_backend(config)
        if permission is not None:
            checks.extend(backend.request_permissions(permission))
        checks.extend(backend.permission_checks() if passive else backend.doctor())
        # The native dialog can be accepted while doctor is running. Its most
        # recent result replaces the initial pending state for that permission.
        checks = list({check.name: check for check in checks}.values())
        # Geometry is cheap on native backends. A Wayland screen() can open an
        # interactive portal; its explicit permission flow belongs to a session.
        if choice.name != "wayland":
            info = backend.screen()
            screen = asdict(info)
    except ComputerError as exc:
        checks.append(Check("desktop", False, str(exc)))
    except Exception as exc:  # noqa: BLE001 - report probe errors in Settings
        checks.append(Check("desktop", False, str(exc)))
    finally:
        if backend is not None:
            backend.close()
    optional_checks = {"accessibility"} if choice.name in {"x11", "windows", "wayland"} else set()
    return {
        "backend": choice.name,
        "backend_reason": choice.reason,
        "notes": list(choice.notes),
        "enabled": config.enabled,
        "passive": passive,
        "ready": not passive and config.enabled and bool(checks)
        and all(check.ok or check.name in optional_checks for check in checks) and not stop_reason(),
        "checks": [{**asdict(check), "optional": check.name in optional_checks} for check in checks],
        "screen": screen,
        "stopped": stop_reason(),
    }


async def computer_control(action: str) -> dict[str, Any]:
    if action == "stop":
        from navin.agent.tools.computer import shutdown_computer_sessions

        engage_stop("stopped from Settings > Computer")
        await shutdown_computer_sessions()
    elif action == "go":
        release_stop()
    else:
        raise ComputerError(f"unknown computer control {action!r}")
    return {"stopped": stop_reason()}


async def close_disabled_computer_sessions(query: dict[str, list[str]]) -> None:
    raw = query.get("computer_enabled", query.get("computerEnabled", []))
    if raw and raw[0].strip().lower() in {"false", "0", "no", "off"}:
        from navin.agent.tools.computer import shutdown_computer_sessions

        await shutdown_computer_sessions()


async def probe_computer(permission: str | None = None, *, passive: bool = False) -> dict[str, Any]:
    return await asyncio.to_thread(computer_diagnostics, permission=permission, passive=passive)
