"""WebUI surface for Ollama Detect → Install → Pull → Configure."""

from __future__ import annotations

from typing import Any

from navin.providers.ollama_setup import (
    OllamaSetupError,
    configure_ollama,
    install_ollama,
    ollama_status_payload,
    pull_ollama_model,
    start_ollama,
)
from navin.webui.settings_api import settings_payload

QueryParams = dict[str, list[str]]


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def ollama_setup_status() -> dict[str, Any]:
    return ollama_status_payload()


def ollama_setup_action(action: str, query: QueryParams) -> dict[str, Any]:
    if action == "install":
        return install_ollama()
    if action == "start":
        return start_ollama()
    if action == "pull":
        model = (_query_first(query, "model") or "").strip()
        if not model:
            raise OllamaSetupError("model is required")
        return pull_ollama_model(model)
    if action == "configure":
        model = (_query_first(query, "model") or "").strip() or None
        make_active = (_query_first(query, "make_active") or "1").lower() not in {
            "0",
            "false",
            "no",
        }
        payload = configure_ollama(model=model, make_active=make_active)
        payload["settings"] = settings_payload()
        return payload
    raise OllamaSetupError(f"unknown Ollama action '{action}'", status=404)
