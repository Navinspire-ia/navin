# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings > Organization and ``navin org``: the organization's model gateway.

Actions: ``status``, ``import`` (the file the IT team gave), ``remove`` (an
imported file only), ``key`` (the user's personal gateway key) and ``test``
(the gateway answers and serves the listed models). Answers never carry a
key: whether one is set and a masked hint only.
"""

from __future__ import annotations

from typing import Any

import httpx

from navin import organization
from navin.config.loader import get_config_path, load_config, save_config
from navin.config.schema import ProviderConfig
from navin.config.secrets import unlocked_secret
from navin.utils.tls import trust_with_extra_ca

TEST_TIMEOUT_S = 10.0
MAX_IMPORT_CHARS = 512 * 1024


class OrganizationApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _hint(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "\u2022\u2022\u2022\u2022"
    return f"{secret[:4]}\u2022\u2022\u2022\u2022{secret[-4:]}"


def _gateway_provider(config: Any) -> ProviderConfig | None:
    provider = (config.providers.model_extra or {}).get(organization.PROVIDER_NAME)
    return provider if isinstance(provider, ProviderConfig) else None


def status_payload() -> dict[str, Any]:
    config_dir = get_config_path().parent
    org = organization.load(config_dir)
    base: dict[str, Any] = {"import_path": str(organization.imported_path(config_dir))}
    if org is None:
        return {**base, "present": False}
    config = load_config()
    gateway = org.gateway
    user_key = None
    if gateway is not None and gateway.auth == "user_key":
        provider = _gateway_provider(config)
        key = unlocked_secret(provider.api_key) if provider is not None else None
        user_key = {"configured": bool(key), "hint": _hint(key)}
    active = config.agents.defaults.model_preset
    return {
        **base,
        "present": True,
        "source": org.source,
        "locked": org.locked,
        "path": org.path,
        "error": org.error,
        "exclusive": org.exclusive,
        "organization": {"id": org.id, "name": org.name, "support_contact": org.support_contact},
        "gateway": None if gateway is None else {
            "base_url": gateway.base_url,
            "auth": gateway.auth,
            "header_names": sorted(gateway.headers),
            "ca_bundle": gateway.ca_bundle,
            "default_model": gateway.default_model,
            "routes": dict(gateway.routes),
            "models": [
                {
                    "id": model.id,
                    "label": model.label,
                    "preset": model.preset_name,
                    "context_window": model.context_window,
                    "max_output_tokens": model.max_output_tokens,
                    "vision": model.vision,
                    "reasoning_effort": model.reasoning_effort,
                    "default": model.id == gateway.default_model,
                    "active": model.preset_name == active,
                }
                for model in gateway.models
            ],
        },
        "user_key": user_key,
    }


def _import(body: dict[str, Any]) -> dict[str, Any]:
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OrganizationApiError(400, "choose the organization file to import")
    if len(content) > MAX_IMPORT_CHARS:
        raise OrganizationApiError(413, "the organization file is larger than 512 KB")
    try:
        organization.import_file(content, get_config_path().parent)
    except organization.OrganizationLockedError as exc:
        raise OrganizationApiError(409, str(exc)) from None
    except ValueError as exc:
        raise OrganizationApiError(400, f"this organization file cannot be used: {exc}") from None
    return status_payload()


def _remove() -> dict[str, Any]:
    try:
        removed = organization.remove_imported(get_config_path().parent)
    except organization.OrganizationLockedError as exc:
        raise OrganizationApiError(409, str(exc)) from None
    if not removed:
        raise OrganizationApiError(404, "no imported organization file to remove")
    return status_payload()


def _save_key(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("api_key", body.get("apiKey"))
    if raw is not None and not isinstance(raw, str):
        raise OrganizationApiError(400, "api_key must be a string")
    key = (raw or "").strip() or None
    if key and (len(key) > 4096 or any(ord(ch) < 33 for ch in key)):
        raise OrganizationApiError(400, "the key must be one line without spaces")
    org = organization.load(get_config_path().parent)
    if org is None or not org.usable or org.gateway is None:
        raise OrganizationApiError(409, "import a valid organization file first")
    if org.gateway.auth != "user_key":
        raise OrganizationApiError(409, f"{org.display_name} does not use personal keys")
    config = load_config()
    provider = _gateway_provider(config)
    if provider is None:
        raise OrganizationApiError(409, "the organization gateway is not loaded; restart Navin")
    provider.api_key = key
    save_config(config)
    return status_payload()


def _test() -> dict[str, Any]:
    org = organization.load(get_config_path().parent)
    if org is None or not org.usable or org.gateway is None:
        raise OrganizationApiError(409, "import a valid organization file first")
    gateway = org.gateway
    provider = _gateway_provider(load_config())
    key = unlocked_secret(provider.api_key) if provider is not None else None
    if gateway.auth == "user_key" and not key:
        return {"ok": False, "error": "enter your personal key first"}
    headers = dict(gateway.headers)
    if key and gateway.auth != "none":
        headers["Authorization"] = f"Bearer {key}"
    try:
        verify = trust_with_extra_ca(gateway.ca_bundle)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    url = f"{gateway.base_url}/models"
    try:
        with httpx.Client(timeout=TEST_TIMEOUT_S, verify=verify, trust_env=True) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"cannot reach {url}: {exc}"}
    status = response.status_code
    if status in (401, 403):
        return {"ok": False, "status": status, "error": "the gateway refused the key"}
    if status >= 400:
        return {"ok": False, "status": status, "error": f"the gateway answered HTTP {status} on {url}"}
    try:
        payload = response.json()
    except ValueError:
        return {"ok": False, "status": status, "error": f"{url} did not answer JSON: is baseUrl the LiteLLM address?"}
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "status": status, "error": f"{url} did not answer a model list"}
    served = {row.get("id") for row in rows if isinstance(row, dict)}
    missing = [model.id for model in gateway.models if model.id not in served]
    return {"ok": True, "status": status, "served": len(served), "missing": missing}


def handle_organization_action(action: str, body: dict[str, Any]) -> dict[str, Any]:
    if action == "status":
        return status_payload()
    if action == "import":
        return _import(body)
    if action == "remove":
        return _remove()
    if action == "key":
        return _save_key(body)
    if action == "test":
        return _test()
    raise OrganizationApiError(404, f"unknown organization action: {action}")
