# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebUI surface for OmniRoute Detect -> Install -> Start -> Configure."""

from __future__ import annotations

from typing import Any

from navin.providers.omniroute_setup import (
    OmniRouteSetupError,
    configure_omniroute,
    install_omniroute,
    omniroute_status_payload,
    start_omniroute,
)
from navin.webui.settings_api import settings_payload

QueryParams = dict[str, list[str]]


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def omniroute_setup_status() -> dict[str, Any]:
    return omniroute_status_payload()


def omniroute_setup_action(action: str, query: QueryParams) -> dict[str, Any]:
    if action == "install":
        return install_omniroute()
    if action == "start":
        return start_omniroute()
    if action == "configure":
        # Empty model keeps the default ``auto`` combo; "none" pins only the endpoint.
        raw_model = (_query_first(query, "model") or "").strip()
        model: str | None = raw_model or "auto"
        if raw_model.lower() == "none":
            model = None
        make_active = (_query_first(query, "make_active") or "1").lower() not in {
            "0",
            "false",
            "no",
        }
        payload = configure_omniroute(model=model, make_active=make_active)
        payload["settings"] = settings_payload()
        return payload
    raise OmniRouteSetupError(f"unknown OmniRoute action '{action}'", status=404)
