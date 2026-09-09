# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OmniRoute helpers: the keyless catalog behind its authenticated ``/v1/models``.

OmniRoute (https://github.com/diegosouzapw/OmniRoute) serves chat completions
without any API key on a fresh install, but ``GET /v1/models`` answers 401
until a dashboard key exists. Reporting that 401 as "credential rejected"
would tell users their working gateway is broken. The read-only
``/v1/auto-combo/<channel>/candidates`` route is public, so Settings falls
back to it: the ``auto`` combos plus the live pool they route across.
"""

from __future__ import annotations

from typing import Any

import httpx

OMNIROUTE_DEFAULT_API_BASE = "http://localhost:20128/v1"

# Channel -> (model id, label, description). ``auto`` is the balanced default.
OMNIROUTE_AUTO_COMBOS: tuple[tuple[str, str, str], ...] = (
    ("auto", "Auto", "Balanced default, sticks to the last provider that answered well."),
    ("auto/coding", "Auto - coding", "Quality-first weights for code generation."),
    ("auto/fast", "Auto - fast", "Lowest latency first."),
    ("auto/cheap", "Auto - cheap", "Cheapest per token first."),
    ("auto/offline", "Auto - offline", "Most quota and rate-limit headroom first."),
    ("auto/smart", "Auto - smart", "Quality-first with 10% exploration of new models."),
)

OMNIROUTE_KEYLESS_MESSAGE = (
    "OmniRoute answers without an API key, so chat works as is. Its full model "
    "list needs a key from the OmniRoute dashboard (Endpoints); showing the "
    "auto combos and the free pool they route across."
)

_CANDIDATES_TIMEOUT_S = 6.0


def omniroute_root(api_base: str) -> str:
    """Server root for management routes: ``http://host:20128/v1`` -> ``http://host:20128``."""
    root = api_base.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _combo_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "label": label,
            "owned_by": "OmniRoute",
            "context_window": None,
            "description": description,
            "free": True,
        }
        for model_id, label, description in OMNIROUTE_AUTO_COMBOS
    ]


def _candidate_rows(payload: Any) -> list[dict[str, Any]]:
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        model_id = candidate.get("modelStr") or candidate.get("model")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        if model_id in seen or candidate.get("excluded") is True:
            continue
        seen.add(model_id)
        provider = candidate.get("provider")
        row: dict[str, Any] = {
            "id": model_id,
            "label": None,
            "owned_by": provider.strip() if isinstance(provider, str) and provider.strip() else None,
            "context_window": None,
        }
        # A ``noauth`` connection is an upstream that OmniRoute reaches with no
        # credential at all, i.e. one of its free tiers.
        if candidate.get("connectionId") == "noauth":
            row["free"] = True
        rows.append(row)
    return rows


def omniroute_keyless_catalog(
    api_base: str,
    *,
    timeout: float = _CANDIDATES_TIMEOUT_S,
) -> list[dict[str, Any]] | None:
    """Return the public catalog, or None when OmniRoute is not reachable.

    The rows use the same shape as Settings model rows: the combos first, then
    every reachable candidate of the ``auto`` channel.
    """
    url = f"{omniroute_root(api_base)}/v1/auto-combo/auto/candidates"
    try:
        response = httpx.get(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return [*_combo_rows(), *_candidate_rows(payload)]
