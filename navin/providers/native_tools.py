# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Provider-native tool specs (Anthropic's server-defined ``computer`` tool, ...).

Some tools also exist as a provider primitive the model was trained on. Such a
tool registers a spec factory here; the matching provider swaps the generic
JSON-schema definition for the native one at request time. Nothing else moves:
the model still calls the tool by the same name and the registry executes it.

Factories run per request so a spec can reflect live state (the current
screenshot size for a desktop tool, for instance).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NativeToolSpec:
    """One provider-side tool definition plus the beta flag it needs, if any."""

    definition: dict[str, Any]
    beta: str | None = None


SpecFactory = Callable[[], "NativeToolSpec | None"]

_REGISTRY: dict[tuple[str, str], SpecFactory] = {}


def register_native_tool(provider: str, name: str, factory: SpecFactory) -> None:
    """Expose tool *name* as a native *provider* tool built by *factory*."""
    _REGISTRY[(provider.strip().lower(), name)] = factory


def unregister_native_tool(provider: str, name: str) -> bool:
    return _REGISTRY.pop((provider.strip().lower(), name), None) is not None


def native_tool_spec(provider: str, name: str) -> NativeToolSpec | None:
    """The native definition for *name* on *provider*, or None to keep the schema."""
    factory = _REGISTRY.get((provider.strip().lower(), name))
    if factory is None:
        return None
    try:
        return factory()
    except Exception:  # noqa: BLE001 - a broken factory must not fail the request
        return None


def native_betas(provider: str, tools: list[dict[str, Any]] | None) -> list[str]:
    """Beta flags required by the native tools present in *tools* (deduplicated)."""
    if not tools:
        return []
    betas: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        func = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        spec = native_tool_spec(provider, str(func.get("name") or ""))
        if spec is not None and spec.beta and spec.beta not in betas:
            betas.append(spec.beta)
    return betas


def merge_beta_header(existing: str | None, betas: list[str]) -> str:
    """Comma-join *betas* into an ``anthropic-beta`` style header value."""
    flags = [flag.strip() for flag in str(existing or "").split(",") if flag.strip()]
    for beta in betas:
        if beta not in flags:
            flags.append(beta)
    return ",".join(flags)
