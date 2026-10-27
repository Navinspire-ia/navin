# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads of the Desktop Ops panel's Infrastructure section.

Routes (session-scoped, the project is the session's workspace):

- ``GET /api/sessions/{key}/infra`` state; ``?fields=<JSON>`` updates the policy
- ``GET .../infra/discover?provider=&deep=0|1``
- ``GET .../infra/catalog?q=&provider=&risk=&tier=&limit=``
- ``GET .../infra/operation?id=``
- ``GET .../infra/graph?node=&build=kubernetes,aws,terraform,docker,gcp,azure,openstack,vsphere,proxmox``
- ``GET .../infra/audit?limit=`` and ``GET .../infra/runs``

The person clicking in the Desktop is the user, so a policy change made here
needs no extra approval; the agent's own requests to widen it always ask.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navin.infra.model import DISCOVERABLE, RISKS, TIERS

SECTIONS = ("", "discover", "catalog", "operation", "graph", "audit", "runs")


class InfraApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _first(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name) or []
    return str(values[0]).strip() if values else default


def _int(query: dict[str, list[str]], name: str, default: int, top: int) -> int:
    raw = _first(query, name)
    try:
        return max(1, min(int(raw), top)) if raw else default
    except ValueError as exc:
        raise InfraApiError(400, f"{name} must be a number") from exc


def infra_payload(project_path: str | Path, section: str, query: dict[str, list[str]]) -> dict[str, Any]:
    """Answer one Infrastructure request for the project (blocking: call it off the loop)."""
    from navin.infra.engine import InfraEngine
    from navin.infra.orchestrator import PlanError

    if section not in SECTIONS:
        raise InfraApiError(404, "unknown infra section")
    engine = InfraEngine(project_path)
    try:
        if section == "":
            raw = _first(query, "fields")
            if raw:
                try:
                    fields = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise InfraApiError(400, "fields must be JSON") from exc
                if not isinstance(fields, dict):
                    raise InfraApiError(400, "fields must be an object")
                engine.set_policy(fields)
            return engine.state(probe=_first(query, "probe", "1") != "0")
        if section == "discover":
            provider = _first(query, "provider", "all").lower()
            if provider != "all" and provider not in DISCOVERABLE:
                raise InfraApiError(400, f"provider must be all or one of {', '.join(DISCOVERABLE)}")
            return engine.discover(provider, deep=_first(query, "deep") in {"1", "true"})
        if section == "catalog":
            risk, tier = _first(query, "risk"), _first(query, "tier")
            if risk and risk not in RISKS:
                raise InfraApiError(400, f"risk must be one of {', '.join(RISKS)}")
            if tier and tier not in TIERS:
                raise InfraApiError(400, f"tier must be one of {', '.join(TIERS)}")
            return engine.catalog(_first(query, "q"), provider=_first(query, "provider"), risk=risk, tier=tier,
                                  limit=_int(query, "limit", 50, 500))
        if section == "operation":
            ref = _first(query, "id")
            if not ref:
                raise InfraApiError(400, "id is required")
            record = engine.describe(ref)
            if record is None:
                raise InfraApiError(404, f"no operation {ref}")
            return record
        if section == "graph":
            build = [name for name in _first(query, "build").split(",") if name.strip()]
            unknown = [name for name in build if name not in {"kubernetes", "aws", "terraform", "docker", "gcp", "azure", "openstack", "vsphere", "proxmox"}]
            if unknown:
                raise InfraApiError(400, f"build takes kubernetes, aws, terraform, docker, gcp, azure, openstack, vsphere, proxmox (not {unknown[0]})")
            node = _first(query, "node")
            op = "build" if build else ("impact" if node else "summary")
            if build and node:
                engine.graph("build", sources=build)
                op = "impact"
            return engine.graph(op, sources=build or None, node=node)
        if section == "audit":
            return engine.audit(_int(query, "limit", 100, 2000))
        return engine.runs()
    except PlanError as exc:
        raise InfraApiError(404, str(exc)) from exc
    except ValueError as exc:
        raise InfraApiError(400, str(exc)) from exc
