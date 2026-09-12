# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings REST helpers for the WebUI HTTP surface.

The WebSocket channel owns transport/authentication. This module owns the
settings payload shape and the allowlisted config mutations exposed to WebUI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from navin import __version__
from navin.agent.tools.web import SEARCH_PROVIDER_OPTIONS
from navin.audio.models import NAVIN_STT_MODEL, NAVIN_TTS_MODEL
from navin.audio.transcription import resolve_transcription_config
from navin.audio.transcription_registry import (
    resolve_transcription_provider,
    transcription_provider_names,
)
from navin.audio.tts import live_voice_status, resolve_tts_config, voice_realtime_allowed
from navin.audio.tts_registry import resolve_tts_provider, tts_provider_names
from navin.config.loader import get_config_path, load_config, resolve_config_env_vars, save_config
from navin.config.schema import (
    Config,
    ModelPresetConfig,
    ProviderConfig,
    provider_supports_auth_mode,
    resolve_provider_auth_mode,
)
from navin.config.secrets import unlocked_secret
from navin.optional_live import live_modules_available
from navin.providers.connection_presets import (
    connection_payload,
    lookup_connection_base,
    resolve_connection_api_base,
)
from navin.providers.image_generation import (
    get_image_gen_provider,
    image_gen_provider_names,
)
from navin.providers.managed_catalog import TIERS
from navin.providers.media_credentials import (
    media_credentials_ready,
    resolve_media_tool_enabled,
)
from navin.providers.media_models import (
    MEDIA_MODEL_KINDS,
    OPENROUTER_OUTPUT_MODALITIES,
    media_model_kinds,
    supports_media_model,
)
from navin.providers.model_capabilities import (
    supports_grounding,
    supports_vision,
    vision_flag_for_row,
)
from navin.providers.music_generation import (
    get_music_gen_provider,
    music_gen_provider_names,
)
from navin.providers.omniroute import OMNIROUTE_KEYLESS_MESSAGE, omniroute_keyless_catalog
from navin.providers.registry import (
    PROVIDERS,
    create_dynamic_spec,
    find_by_model,
    find_by_name,
)
from navin.providers.settings_order import is_retired_llm_provider, settings_provider_rank
from navin.providers.video_generation import (
    get_video_gen_provider,
    video_gen_provider_names,
)
from navin.security.network import is_loopback_host
from navin.security.workspace_access import workspace_sandbox_status
from navin.update.service import consume_completed_update, updates_configured
from navin.webui.oauth_login import oauth_logins
from navin.webui.runtime_surface import (
    normalize_surface as _normalize_surface,
)
from navin.webui.token_usage import token_usage_payload
from navin.webui.workspaces import (
    read_webui_default_access_mode,
    write_webui_default_access_mode,
)

QueryParams = dict[str, list[str]]


def _version_payload() -> dict[str, Any]:
    """Return version info for the settings payload."""
    return {
        "current": __version__,
    }


DEFAULT_DOCS_BASE_URL = ""


def _docs_base_url() -> str:
    """Optional public docs root. Empty on purpose: Tools uses in-app guides."""
    override = os.environ.get("NAVIN_DOCS_BASE_URL", "").strip()
    return (override or DEFAULT_DOCS_BASE_URL).rstrip("/")


def _docs_payload() -> dict[str, Any]:
    """Return documentation links for the WebUI."""
    base_url = _docs_base_url()
    return {
        "base_url": base_url,
        "chat_apps_url": f"{base_url}/chat-apps.md" if base_url else "",
    }


_RUNTIME_CAPABILITIES = {
    "can_restart_engine": False,
    "can_pick_folder": False,
    "can_open_logs": False,
    "can_export_diagnostics": False,
}

_NATIVE_RUNTIME_CAPABILITIES = {
    **_RUNTIME_CAPABILITIES,
    "can_restart_engine": True,
    "can_pick_folder": True,
    "can_open_logs": True,
    "can_export_diagnostics": True,
}

_BROWSER_RESTART_BEHAVIOR_BY_SECTION = {
    "appearance": "none",
    "models": "none",
    "providers": "none",
    "runtime": "engineRestart",
    "browser": "engineRestart",
    "image": "engineRestart",
    "apps": "engineRestart",
    "advanced": "appRestart",
}

_NATIVE_RESTART_BEHAVIOR_BY_SECTION = {
    **_BROWSER_RESTART_BEHAVIOR_BY_SECTION,
    "runtime": "engineRestart",
    "browser": "engineRestart",
    "image": "engineRestart",
    "apps": "engineRestart",
}

_WEB_SEARCH_PROVIDER_OPTIONS = SEARCH_PROVIDER_OPTIONS
_WEB_SEARCH_PROVIDER_BY_NAME = {
    provider["name"]: provider for provider in _WEB_SEARCH_PROVIDER_OPTIONS
}

_IMAGE_GENERATION_ASPECT_RATIOS = {
    "1:1",
    "3:4",
    "9:16",
    "4:3",
    "16:9",
    "3:2",
    "2:3",
    "21:9",
}
_CONTEXT_WINDOW_TOKEN_OPTIONS = {
    65_536,
    131_072,
    200_000,
    262_144,
    400_000,
    1_000_000,
    2_000_000,
}
# Specialty presets that can never be the chat default / chat runtime.
_MEDIA_MODALITIES = {"image", "video", "audio", "music", "stt"}


def _is_media_model_slug(slug: str) -> bool:
    """True when *slug* is a known media specialty model (even if modality was lost)."""
    try:
        from navin.providers.managed_catalog import is_media_model_slug

        return is_media_model_slug(slug)
    except Exception:
        return False


_MODEL_CONFIGURATION_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_MODEL_IMPORT_MAX = 200
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

class WebUISettingsError(ValueError):
    """User-facing settings validation failure.

    ``field`` names the form field the message is about, so the UI can show
    it next to that control instead of in the generic banner.
    """

    def __init__(self, message: str, *, status: int = 400, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field


def runtime_capabilities(
    surface: str | None = "browser",
    overrides: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Return the capability flags exposed to the WebUI runtime."""
    base = (
        _NATIVE_RUNTIME_CAPABILITIES
        if _normalize_surface(surface) == "native"
        else _RUNTIME_CAPABILITIES
    )
    result = dict(base)
    for key, value in (overrides or {}).items():
        if key in result:
            result[key] = bool(value)
    return result


def restart_behavior_by_section(surface: str | None = "browser") -> dict[str, str]:
    return dict(
        _NATIVE_RESTART_BEHAVIOR_BY_SECTION
        if _normalize_surface(surface) == "native"
        else _BROWSER_RESTART_BEHAVIOR_BY_SECTION
    )


def decorate_settings_payload(
    payload: dict[str, Any],
    *,
    surface: str | None = "browser",
    runtime_capability_overrides: dict[str, Any] | None = None,
    restart_required_sections: list[str] | None = None,
    apply_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach runtime-surface metadata without changing the core settings shape."""
    surface_value = _normalize_surface(surface)
    sections = restart_required_sections
    if sections is None:
        raw_sections = payload.get("restart_required_sections") or []
        sections = [str(section) for section in raw_sections if isinstance(section, str)]
    sections = sorted(dict.fromkeys(sections))
    result = dict(payload)
    result["surface"] = surface_value
    result["runtime_surface"] = surface_value
    result["runtime_capabilities"] = runtime_capabilities(
        surface_value,
        runtime_capability_overrides,
    )
    result["restart_behavior_by_section"] = restart_behavior_by_section(surface_value)
    result["restart_required_sections"] = sections
    if sections:
        result["requires_restart"] = True
    else:
        result["requires_restart"] = bool(result.get("requires_restart", False))
    result["apply_state"] = apply_state or {
        "status": "pending" if result["requires_restart"] else "idle",
        "sections": sections,
    }
    return result


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_first_alias(query: QueryParams, snake: str, camel: str) -> str | None:
    value = _query_first(query, snake)
    return _query_first(query, camel) if value is None else value


def _mask_secret_hint(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}••••{secret[-4:]}"


def _forge_settings_payload(config: Any) -> dict[str, Any]:
    """Forge tokens for Settings > Git: hosts and hints, never the secrets.

    Env-provided tokens are listed too (read-only), so a user who exported
    ``FORGEJO_TOKEN`` sees why Create PR already works and does not paste a
    duplicate into the config.
    """
    from navin.webui import forge_api

    forge_config = getattr(config.tools, "forge", None)
    stored: dict[str, str] = dict(getattr(forge_config, "tokens", {}) or {})
    host_kinds: dict[str, str] = dict(getattr(forge_config, "hosts", {}) or {})

    hosts: list[dict[str, Any]] = []
    for host in sorted(set(stored) | set(host_kinds)):
        token = stored.get(host) or ""
        hosts.append(
            {
                "host": host,
                "kind": host_kinds.get(host) or forge_api.kind_from_host(host),
                "kind_pinned": host in host_kinds,
                "token_hint": _mask_secret_hint(token),
                "token_configured": bool(token),
            }
        )

    env_tokens: list[dict[str, str]] = []
    for name in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "GL_TOKEN",
        "GLAB_TOKEN",
        "FORGEJO_TOKEN",
        "FORGEJO_ACCESS_TOKEN",
        "GITEA_TOKEN",
        "GITEA_SERVER_TOKEN",
        "GITEA_ACCESS_TOKEN",
        "GITEA_HTTP_TOKEN",
        "TEA_TOKEN",
    ):
        if (os.environ.get(name) or "").strip():
            env_tokens.append({"name": name})
    for name in os.environ:
        if name.startswith("NAVIN_FORGE_TOKEN_") and (os.environ.get(name) or "").strip():
            env_tokens.append({"name": name})

    return {
        "hosts": hosts,
        "env_tokens": sorted(env_tokens, key=lambda row: row["name"]),
        "gh_available": shutil.which("gh") is not None,
        "kinds": list(forge_api.FORGE_KINDS),
    }


_FORGE_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?$")


def _normalize_forge_host(raw: str) -> str:
    """Accept ``forgejo.example.com`` or a pasted URL, keep the hostname."""
    value = (raw or "").strip().lower()
    if "://" in value:
        from urllib.parse import urlsplit

        value = (urlsplit(value).hostname or "").strip().lower()
    value = value.strip("/").split("/")[0]
    if not value or not _FORGE_HOST_RE.match(value):
        raise WebUISettingsError("forge_host must be a hostname")
    return value


def _apply_forge_host(config: Any, query: QueryParams, raw_host: str) -> bool:
    """Add, update or remove one forge host entry. Returns True when changed."""
    from navin.webui.forge_api import FORGE_KINDS

    host = _normalize_forge_host(raw_host)
    forge = config.tools.forge
    tokens = dict(forge.tokens or {})
    hosts = dict(forge.hosts or {})
    changed = False

    remove = _query_first_alias(query, "forge_remove", "forgeRemove")
    if remove is not None and _parse_bool(remove, "forge_remove"):
        if tokens.pop(host, None) is not None:
            changed = True
        if hosts.pop(host, None) is not None:
            changed = True
        forge.tokens = tokens
        forge.hosts = hosts
        return changed

    token = _query_first_alias(query, "forge_token", "forgeToken")
    if token is not None:
        cleaned = token.strip()
        if cleaned:
            if tokens.get(host) != cleaned:
                tokens[host] = cleaned
                changed = True
        elif tokens.pop(host, None) is not None:
            changed = True

    kind = _query_first_alias(query, "forge_kind", "forgeKind")
    if kind is not None:
        flavour = kind.strip().lower()
        if flavour in ("", "auto"):
            if hosts.pop(host, None) is not None:
                changed = True
        elif flavour in FORGE_KINDS:
            if hosts.get(host) != flavour:
                hosts[host] = flavour
                changed = True
        else:
            raise WebUISettingsError(
                f"forge_kind must be one of {', '.join(FORGE_KINDS)} or auto"
            )

    forge.tokens = tokens
    forge.hosts = hosts
    return changed


def _resolve_env_placeholders(value: str | None) -> str | None:
    if not value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        env_value = os.environ.get(match.group(1))
        if env_value is None:
            missing = True
            return ""
        return env_value

    resolved = _ENV_REF_RE.sub(replace, value).strip()
    if missing and not resolved:
        return None
    return resolved or None


def _provider_requires_api_key(spec: Any) -> bool:
    if spec.name == "azure_openai":
        return False
    if spec.is_oauth:
        return False
    if spec.is_local or spec.is_direct:
        return False
    return True


def _provider_requires_api_base(spec: Any) -> bool:
    if spec.name == "azure_openai":
        return True
    return bool(
        spec.is_direct
        and spec.backend in {"openai_compat", "anthropic"}
        and not spec.default_api_base
    )


def _oauth_provider_status(spec: Any) -> dict[str, Any]:
    if not getattr(spec, "is_oauth", False):
        return {"configured": False, "account": None, "expires_at": None, "login_supported": False}

    if spec.name == "openai_codex":
        try:
            # Le store Navin uniquement : jamais la session du CLI Codex
            # officiel, sinon une installation neuve s'affiche "Signed in".
            from navin.providers.openai_codex_provider import codex_token_storage
        except Exception:
            return {
                "configured": False,
                "account": None,
                "expires_at": None,
                "login_supported": False,
            }
        token = None
        with suppress(Exception):
            token = codex_token_storage().load()
        expires_at = getattr(token, "expires", None) if token else None
        now_ms = int(time.time() * 1000)
        return {
            "configured": bool(
                token
                and token.access
                and (getattr(token, "refresh", None) or (expires_at and expires_at > now_ms))
            ),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": expires_at,
            "login_supported": True,
        }

    if spec.name == "github_copilot":
        try:
            from navin.providers.github_copilot_provider import get_github_copilot_login_status
        except Exception:
            return {
                "configured": False,
                "account": None,
                "expires_at": None,
                "login_supported": False,
            }
        token = None
        with suppress(Exception):
            token = get_github_copilot_login_status()
        return {
            "configured": bool(token and token.access and token.expires > int(time.time() * 1000)),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": getattr(token, "expires", None) if token else None,
            "login_supported": True,
        }

    if spec.name == "xai_oauth":
        try:
            from navin.providers.xai_oauth_provider import get_xai_oauth_login_status
        except Exception:
            return {
                "configured": False,
                "account": None,
                "expires_at": None,
                "login_supported": False,
            }
        token = None
        with suppress(Exception):
            token = get_xai_oauth_login_status()
        expires_at = getattr(token, "expires", None) if token else None
        now_ms = int(time.time() * 1000)
        return {
            "configured": bool(
                token
                and token.access
                and (getattr(token, "refresh", None) or (expires_at and expires_at > now_ms))
            ),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": expires_at,
            "login_supported": True,
        }

    return {"configured": False, "account": None, "expires_at": None, "login_supported": False}


def _provider_configured_for_settings(spec: Any, provider_config: Any) -> bool:
    if spec.is_oauth:
        return bool(_oauth_provider_status(spec)["configured"])
    if _provider_requires_api_base(spec):
        return bool(provider_config.api_base)
    if _provider_requires_api_key(spec):
        return bool(provider_config.api_key)
    return bool(
        provider_config.api_key
        or provider_config.api_base
        or getattr(provider_config, "region", None)
        or getattr(provider_config, "profile", None)
    )


def _dynamic_provider_items(config: Any) -> list[tuple[str, ProviderConfig]]:
    return [
        (name, provider_config)
        for name, provider_config in (config.providers.model_extra or {}).items()
        if isinstance(provider_config, ProviderConfig)
        and not is_retired_llm_provider(name)
    ]


def _resolve_settings_provider(
    config: Any,
    provider_name: str,
) -> tuple[Any, str, ProviderConfig] | None:
    spec = find_by_name(provider_name)
    if spec is not None:
        if is_retired_llm_provider(spec.name):
            return None
        provider_config = getattr(config.providers, spec.name, None)
        if isinstance(provider_config, ProviderConfig):
            return spec, spec.name, provider_config
        return None

    normalized = provider_name.replace("-", "_")
    for extra_name, provider_config in _dynamic_provider_items(config):
        if provider_name == extra_name or normalized == extra_name.replace("-", "_"):
            return create_dynamic_spec(extra_name, thinking_style=(provider_config.thinking_style or "")), extra_name, provider_config
    return None


def _provider_settings_row(
    name: str,
    spec: Any,
    provider_config: ProviderConfig,
    *,
    managed: bool = False,
) -> dict[str, Any]:
    oauth_status = _oauth_provider_status(spec) if spec.is_oauth else None
    configured = (
        bool(oauth_status["configured"])
        if oauth_status is not None
        else _provider_configured_for_settings(spec, provider_config)
    )
    if managed:
        configured = True
    supports_auth_mode = provider_supports_auth_mode(name)
    row = {
        "name": name,
        "label": spec.label,
        "configured": configured,
        "auth_type": "oauth" if spec.is_oauth else "api_key",
        "api_key_required": False if managed else _provider_requires_api_key(spec),
        "api_key_hint": (
            "Navin plan"
            if managed
            else _mask_secret_hint(provider_config.api_key)
        ),
        # Never expose the upstream OpenRouter wire URL on the Navin slot
        # (managed plan or empty) - the product surface is "Navin", not OR.
        "api_base": (
            None
            if managed or name == "navin"
            else provider_config.api_base
        ),
        "default_api_base": (
            "navin/api"
            if managed or name == "navin"
            else (spec.default_api_base or None)
        ),
        "model_selectable": not spec.is_transcription_only,
        "model_catalog": _model_catalog_kind(spec),
        "managed": managed,
        "supports_auth_mode": supports_auth_mode,
    }
    if supports_auth_mode:
        row["auth_mode"] = resolve_provider_auth_mode(provider_config)
    if oauth_status is not None:
        row["oauth_account"] = oauth_status["account"]
        row["oauth_expires_at"] = oauth_status["expires_at"]
        row["oauth_login_supported"] = oauth_status["login_supported"]
    if spec.name == "openai":
        row["api_type"] = provider_config.api_type
    catalog = connection_payload(name) or connection_payload(spec.name)
    if catalog:
        region = provider_config.endpoint_region or catalog["default_region"]
        plan = provider_config.access_plan or catalog["default_plan"]
        protocol = provider_config.wire_protocol or catalog["default_protocol"]
        resolved = (
            _resolve_env_placeholders(provider_config.api_base)
            or lookup_connection_base(name, region, plan, protocol)
            or spec.default_api_base
        )
        row["connection"] = catalog
        row["endpoint_region"] = region
        row["access_plan"] = plan
        row["wire_protocol"] = protocol
        row["resolved_api_base"] = resolved
        if not managed and name != "navin":
            row["default_api_base"] = (
                lookup_connection_base(
                    name,
                    catalog["default_region"],
                    catalog["default_plan"],
                    catalog["default_protocol"],
                )
                or spec.default_api_base
                or None
            )
    return row


def _provider_settings_rows(config: Any, selected_provider: str | None) -> list[dict[str, Any]]:
    """Return one Settings row per provider family while preserving legacy configs."""
    aliases: dict[str, list[Any]] = {}
    for spec in PROVIDERS:
        if spec.settings_alias_for:
            aliases.setdefault(spec.settings_alias_for, []).append(spec)

    managed_active = _managed_navin_active(config)

    rows: list[dict[str, Any]] = []
    for canonical in PROVIDERS:
        if canonical.settings_alias_for or is_retired_llm_provider(canonical.name):
            continue
        if not live_modules_available() and canonical.name == "navin":
            continue
        candidates = [canonical, *aliases.get(canonical.name, [])]
        chosen = next((spec for spec in candidates if spec.name == selected_provider), None)
        if chosen is None:
            chosen = next(
                (
                    spec
                    for spec in candidates
                    if (provider_config := getattr(config.providers, spec.name, None)) is not None
                    and _provider_configured_for_settings(spec, provider_config)
                ),
                canonical,
            )
        if is_retired_llm_provider(chosen.name):
            continue
        provider_config = getattr(config.providers, chosen.name, None)
        if provider_config is None:
            continue
        managed = managed_active and chosen.name == "navin"
        row = _provider_settings_row(
            chosen.name, chosen, provider_config, managed=managed
        )
        row["label"] = canonical.label
        rows.append(row)

    rows.sort(key=lambda row: (settings_provider_rank(str(row["name"])), str(row["label"]).lower()))
    return rows


def _model_catalog_kind(spec: Any) -> str:
    catalog = getattr(spec, "model_catalog", "auto")
    if catalog != "auto":
        return catalog
    if spec.is_transcription_only or spec.is_oauth:
        return "unsupported"
    if spec.backend not in {"openai_compat", "anthropic"} and spec.name != "minimax_anthropic":
        return "unsupported"
    if spec.is_local:
        return "local"
    if spec.is_direct:
        return "custom"
    if spec.is_gateway:
        return "catalog"
    return "official"


def _model_id_from_row(row: Any) -> str | None:
    if isinstance(row, str):
        return row.strip() or None
    if not isinstance(row, dict):
        return None
    for key in ("id", "name", "model"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_context_window(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    for key in (
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
        "inputTokenLimit",
    ):
        value = row.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def _model_row_is_free(row: Any, model_id: str) -> bool:
    """Free-tier model detection (OpenRouter and compatible catalogs).

    OpenRouter tags free variants with a ``:free`` suffix and prices them at
    zero in the ``pricing`` object; either signal qualifies.
    """
    if model_id.endswith(":free"):
        return True
    if not isinstance(row, dict):
        return False
    pricing = row.get("pricing")
    if not isinstance(pricing, dict) or not pricing:
        return False
    seen = False
    for key in ("prompt", "completion", "request"):
        value = pricing.get(key)
        if value is None:
            continue
        try:
            if float(value) != 0.0:
                return False
        except (TypeError, ValueError):
            return False
        seen = True
    return seen


def _model_row_payload(row: Any) -> dict[str, Any] | None:
    model_id = _model_id_from_row(row)
    if not model_id:
        return None
    label: str | None = None
    description: str | None = None
    owned_by: str | None = None
    if isinstance(row, dict):
        raw_label = row.get("display_name") or row.get("displayName") or row.get("label") or row.get("name")
        if isinstance(raw_label, str) and raw_label.strip() and raw_label.strip() != model_id:
            label = raw_label.strip()
        raw_description = row.get("description")
        if isinstance(raw_description, str) and raw_description.strip():
            description = raw_description.strip()
        raw_owner = row.get("owned_by") or row.get("owner") or row.get("organization")
        if isinstance(raw_owner, str) and raw_owner.strip():
            owned_by = raw_owner.strip()
    payload = {
        "id": model_id,
        "label": label,
        "owned_by": owned_by,
        "context_window": _model_context_window(row),
    }
    if description:
        payload["description"] = description
    if _model_row_is_free(row, model_id):
        payload["free"] = True
    # Keep an explicit false: the picker must not override a provider's
    # text-only declaration with a name-based vision guess.
    payload["vision"] = vision_flag_for_row(model_id, row if isinstance(row, dict) else None)
    payload["media_modalities"] = media_model_kinds(model_id, row)
    if isinstance(row, dict):
        architecture = row.get("architecture")
        architecture = architecture if isinstance(architecture, dict) else {}
        output_modalities = row.get("output_modalities", architecture.get("output_modalities"))
        if isinstance(output_modalities, list):
            payload["output_modalities"] = [m for m in output_modalities if isinstance(m, str)]
        from navin.audio.models import default_voice, known_voices

        declared_voices = row.get("supported_voices", row.get("voices"))
        voices = (declared_voices if isinstance(declared_voices, list)
                  else list(known_voices(model_id)))
        if voices:
            payload["voices"] = [v for v in voices if isinstance(v, str) and v.strip()]
            preferred = default_voice(model_id)
            payload["default_voice"] = (preferred if preferred in payload["voices"]
                                        else next(iter(payload["voices"]), ""))
    return payload


def _omniroute_keyless_rows(
    spec: Any, api_base: str | None, api_key: str | None
) -> list[dict[str, Any]] | None:
    """OmniRoute's public catalog when ``/v1/models`` wants a key we do not have.

    Only for a keyless OmniRoute slot: with a key configured, a 401 really is a
    rejected credential and must surface as such.
    """
    if spec.name != "omniroute" or api_key or not api_base:
        return None
    return omniroute_keyless_catalog(api_base)


def _extract_model_rows(body: Any) -> list[dict[str, Any]]:
    raw_rows = body.get("data", body.get("models")) if isinstance(body, dict) else body
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in raw_rows:
        row = _model_row_payload(raw_row)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def _model_id_aliases(model_id: str) -> set[str]:
    text = (model_id or "").strip().lower()
    if not text:
        return set()
    aliases = {text}
    if "/" in text:
        aliases.add(text.rsplit("/", 1)[-1])
    return aliases


def _builtin_catalog_rows(spec: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in spec.builtin_models:
        payload = _model_row_payload(
            {
                "id": model.id,
                "label": model.label or None,
                "description": model.description or None,
                "owned_by": spec.label,
                "context_window": model.context_window,
            }
        )
        if payload is not None:
            rows.append(payload)
    return rows


def _merge_builtin_model_rows(rows: list[dict[str, Any]], spec: Any) -> list[dict[str, Any]]:
    extras = _builtin_catalog_rows(spec)
    if not extras:
        return rows
    seen: set[str] = set()
    for row in rows:
        seen.update(_model_id_aliases(str(row.get("id") or "")))
    injected: list[dict[str, Any]] = []
    for extra in extras:
        aliases = _model_id_aliases(str(extra.get("id") or ""))
        if aliases & seen:
            continue
        injected.append(extra)
        seen.update(aliases)
    return injected + rows


def provider_models_payload(query: QueryParams) -> dict[str, Any]:
    """Fetch an OpenAI-compatible provider's model list for Settings.

    The result is advisory only: users can always type a custom model id. This
    helper deliberately avoids mutating config so probing model lists never
    changes runtime behavior.
    """
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    media_kind = (_query_first(query, "modality") or "").strip()
    if media_kind and media_kind not in MEDIA_MODEL_KINDS:
        raise WebUISettingsError("modality must be stt, tts, image, video or music")
    resolved_provider = _resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider

    catalog_kind = _model_catalog_kind(spec)
    base_payload: dict[str, Any] = {
        "provider": provider_key,
        "label": spec.label,
        "catalog_kind": catalog_kind,
        "models": [],
        "model_count": 0,
        "message": None,
        "fetched_at": time.time(),
    }
    if catalog_kind == "unsupported":
        return {
            **base_payload,
            "status": "unsupported",
            "message": "Model list is not available for this provider. Type a model ID manually.",
        }

    if catalog_kind == "builtin":
        rows = _builtin_catalog_rows(spec)
        if media_kind:
            rows = [row for row in rows if supports_media_model(row["id"], media_kind)]
        return {
            **base_payload,
            "status": "available",
            "models": rows,
            "model_count": len(rows),
        }

    api_base = (
        _resolve_env_placeholders(provider_config.api_base)
        or resolve_connection_api_base(spec.name, provider_config)
        or spec.default_api_base
    )
    if spec.name == "openai" and not api_base:
        api_base = "https://api.openai.com/v1"
    if not api_base:
        return {
            **base_payload,
            "status": "missing_api_base",
            "message": "Configure an API base URL to load models.",
        }

    api_key = unlocked_secret(_resolve_env_placeholders(provider_config.api_key))
    if media_kind in {"stt", "tts"} and not api_key:
        # Use the same managed-key / environment resolution as actual speech.
        # Catalog sync may not have copied the license key into providers yet.
        speech_config = config.model_copy(deep=True)
        if media_kind == "tts":
            speech_config.voice.tts_provider = provider_key
            effective_speech = resolve_tts_config(speech_config)
        else:
            speech_config.transcription.provider = provider_key
            effective_speech = resolve_transcription_config(speech_config)
        if effective_speech.provider == provider_key:
            api_key = unlocked_secret(effective_speech.api_key)
    if media_kind and spec.name == "navin" and not api_key:
        api_key = unlocked_secret(config.license.managed_api_key or "")
    if _provider_requires_api_key(spec) and not api_key:
        return {
            **base_payload,
            "status": "not_configured",
            "message": "Configure this provider before loading models.",
        }

    is_anthropic_backend = _is_anthropic_catalog(spec, provider_config)

    headers = {"Accept": "application/json"}
    if api_key:
        if spec.name == "minimax_anthropic":
            headers["X-Api-Key"] = api_key
        elif is_anthropic_backend:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    normalized_base = api_base.rstrip("/")
    models_url = f"{normalized_base}/models"
    if (
        spec.name == "minimax_anthropic" or is_anthropic_backend
    ) and not normalized_base.endswith("/v1"):
        models_url = f"{normalized_base}/v1/models"

    # The Anthropic Models API paginates (default page size 20).
    params = {"limit": "1000"} if is_anthropic_backend else None
    if media_kind and spec.name in {"navin", "openrouter"}:
        params = {"output_modalities": OPENROUTER_OUTPUT_MODALITIES[media_kind]}
    native_google = bool(media_kind and spec.name == "gemini"
                         and urlsplit(normalized_base).hostname == "generativelanguage.googleapis.com")
    if native_google:
        models_url = normalized_base.removesuffix("/openai") + "/models"
        headers = {"Accept": "application/json", "x-goog-api-key": api_key}
        params = {"pageSize": "1000"}

    try:
        response = httpx.get(
            models_url,
            headers=headers,
            params=params,
            timeout=10.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        if native_google and isinstance(body, dict):
            native_rows = list(body.get("models") or [])
            next_page = body.get("nextPageToken")
            for _ in range(3):
                if not isinstance(next_page, str) or not next_page:
                    break
                page = httpx.get(models_url, headers=headers, params={**params, "pageToken": next_page},
                                 timeout=10.0, follow_redirects=False)
                page.raise_for_status()
                next_body = page.json()
                native_rows.extend(next_body.get("models") or [])
                next_page = next_body.get("nextPageToken")
            body = {"data": [
                {**row, "id": str(row.get("name") or "").removeprefix("models/")}
                for row in native_rows if isinstance(row, dict)
            ]}
        rows = _merge_builtin_model_rows(_extract_model_rows(body), spec)
        if media_kind:
            from navin.audio.models import NAVIN_STT_MODEL, NAVIN_TTS_MODEL

            rows = [row for row in rows if supports_media_model(row["id"], media_kind, row)]
            recommended = {"tts": NAVIN_TTS_MODEL, "stt": NAVIN_STT_MODEL}.get(media_kind, "")
            rows.sort(key=lambda row: (row["id"] != recommended,
                                      not row["id"].startswith("qwen/"),
                                      str(row.get("label") or row["id"]).lower()))
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            keyless_rows = _omniroute_keyless_rows(spec, api_base, api_key)
            if keyless_rows is not None:
                if media_kind:
                    keyless_rows = [row for row in keyless_rows if supports_media_model(row["id"], media_kind, row)]
                return {
                    **base_payload,
                    "status": "available",
                    "models": keyless_rows,
                    "model_count": len(keyless_rows),
                    "message": OMNIROUTE_KEYLESS_MESSAGE,
                }
            return {
                **base_payload,
                "status": "not_configured",
                "message": "The provider rejected the configured credential.",
            }
        return {
            **base_payload,
            "status": "error",
            "message": f"Model list request failed with HTTP {status}.",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            **base_payload,
            "status": "error",
            "message": f"Could not load models: {exc}",
        }

    return {
        **base_payload,
        "status": "available",
        "models": rows,
        "model_count": len(rows),
    }


_CONNECTION_REGIONS = frozenset({"china", "international", "singapore", "us"})
_CONNECTION_PLANS = frozenset({"payg", "token", "coding"})
_CONNECTION_PROTOCOLS = frozenset({"openai", "anthropic", "native"})
_PROVIDER_PROBE_TIMEOUT_S = 8.0
_QUOTA_PROBE_TIMEOUT_S = 4.0
_QUOTA_PATHS: dict[str, tuple[str, ...]] = {
    "deepseek": ("/user/balance",),
    "moonshot": ("/users/me/balance",),
    "siliconflow": ("/user/info",),
}


def _is_anthropic_catalog(spec: Any, provider_config: ProviderConfig | None) -> bool:
    protocol = getattr(provider_config, "wire_protocol", None) if provider_config else None
    if protocol == "anthropic":
        return True
    return bool(spec.backend == "anthropic" and spec.name != "minimax_anthropic")


def _optional_connection_field(
    query: QueryParams,
    snake: str,
    camel: str,
    allowed: frozenset[str],
    field: str,
) -> str | None:
    """Return the new value, empty string to clear, or None if the field is absent."""
    if snake not in query and camel not in query:
        return None
    raw = (_query_first_alias(query, snake, camel) or "").strip().lower()
    if not raw:
        return ""
    if raw not in allowed:
        raise WebUISettingsError(f"{field} is invalid")
    return raw


def _infer_model_capabilities(rows: list[dict[str, Any]]) -> list[str]:
    blob = " ".join(str(row.get("id") or "") for row in rows).lower()
    caps: list[str] = []
    if any(token in blob for token in ("vision", "-vl", "vl-", "image", "img")):
        caps.append("vision")
    if any(token in blob for token in ("audio", "asr", "tts", "speech", "whisper")):
        caps.append("audio")
    if any(token in blob for token in ("code", "coder", "coding")):
        caps.append("coding")
    if rows:
        caps.append("chat")
    return caps


def _probe_quota(
    spec: Any,
    api_base: str,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    paths = _QUOTA_PATHS.get(spec.name)
    if not paths:
        return None
    root = api_base.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    for path in paths:
        try:
            response = httpx.get(
                f"{root}{path}",
                headers=headers,
                timeout=_QUOTA_PROBE_TIMEOUT_S,
                follow_redirects=False,
            )
            if response.status_code >= 400:
                continue
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def test_provider_connection(query: QueryParams) -> dict[str, Any]:
    """Probe a provider with a hard timeout. Never writes config."""
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    resolved_provider = _resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider
    draft = provider_config.model_copy(deep=True)

    if "api_key" in query or "apiKey" in query:
        draft.api_key = (_query_first_alias(query, "api_key", "apiKey") or "").strip() or None
    if "api_base" in query or "apiBase" in query:
        draft.api_base = (_query_first_alias(query, "api_base", "apiBase") or "").strip() or None
    region = _optional_connection_field(
        query, "endpoint_region", "endpointRegion", _CONNECTION_REGIONS, "endpoint_region"
    )
    plan = _optional_connection_field(
        query, "access_plan", "accessPlan", _CONNECTION_PLANS, "access_plan"
    )
    protocol = _optional_connection_field(
        query, "wire_protocol", "wireProtocol", _CONNECTION_PROTOCOLS, "wire_protocol"
    )
    if region is not None:
        draft.endpoint_region = None if region == "" else region  # type: ignore[assignment]
    if plan is not None:
        draft.access_plan = None if plan == "" else plan  # type: ignore[assignment]
    if protocol is not None:
        draft.wire_protocol = None if protocol == "" else protocol  # type: ignore[assignment]
    if not draft.api_base:
        draft.api_base = lookup_connection_base(
            spec.name, draft.endpoint_region, draft.access_plan, draft.wire_protocol
        )

    started = time.monotonic()
    catalog_kind = _model_catalog_kind(spec)
    result: dict[str, Any] = {
        "ok": False,
        "provider": provider_key,
        "label": spec.label,
        "status": "error",
        "message": None,
        "api_base": draft.api_base or spec.default_api_base,
        "models": [],
        "model_count": 0,
        "capabilities": [],
        "quota": None,
        "latency_ms": 0,
    }
    if catalog_kind == "unsupported":
        result["status"] = "unsupported"
        result["message"] = "This provider has no live model list. Save the key and type a model ID."
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result

    api_base = (
        _resolve_env_placeholders(draft.api_base)
        or resolve_connection_api_base(spec.name, draft)
        or spec.default_api_base
    )
    if spec.name == "openai" and not api_base:
        api_base = "https://api.openai.com/v1"
    result["api_base"] = api_base
    if not api_base:
        result["status"] = "missing_api_base"
        result["message"] = "Configure an API base URL to test the connection."
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result

    api_key = unlocked_secret(_resolve_env_placeholders(draft.api_key))
    if _provider_requires_api_key(spec) and not api_key:
        result["status"] = "not_configured"
        result["message"] = "Enter an API key or subscription key to test."
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result

    is_anthropic_backend = _is_anthropic_catalog(spec, draft)
    headers = {"Accept": "application/json"}
    if api_key:
        if spec.name == "minimax_anthropic":
            headers["X-Api-Key"] = api_key
        elif is_anthropic_backend:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    normalized_base = api_base.rstrip("/")
    models_url = f"{normalized_base}/models"
    if (spec.name == "minimax_anthropic" or is_anthropic_backend) and not normalized_base.endswith(
        "/v1"
    ):
        models_url = f"{normalized_base}/v1/models"
    params = {"limit": "1000"} if is_anthropic_backend else None

    try:
        response = httpx.get(
            models_url,
            headers=headers,
            params=params,
            timeout=_PROVIDER_PROBE_TIMEOUT_S,
            follow_redirects=False,
        )
        response.raise_for_status()
        rows = _extract_model_rows(response.json())
    except httpx.TimeoutException:
        result["status"] = "error"
        result["message"] = "Connection timed out. Check the region, plan and base URL."
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            keyless_rows = _omniroute_keyless_rows(spec, api_base, api_key)
            if keyless_rows is not None:
                result["ok"] = True
                result["status"] = "available"
                result["models"] = keyless_rows[:40]
                result["model_count"] = len(keyless_rows)
                result["capabilities"] = _infer_model_capabilities(keyless_rows)
                result["message"] = OMNIROUTE_KEYLESS_MESSAGE
                result["latency_ms"] = int((time.monotonic() - started) * 1000)
                return result
        result["status"] = "not_configured" if status in {401, 403} else "error"
        detail = ""
        with suppress(ValueError, UnicodeDecodeError):
            body = exc.response.text.strip()
            detail = f" {body[:180]}" if body else ""
        result["message"] = (
            "The provider rejected the credential."
            if status in {401, 403}
            else f"HTTP {status}.{detail}"
        )
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result
    except (httpx.HTTPError, ValueError) as exc:
        result["status"] = "error"
        result["message"] = f"Could not reach the provider: {exc}"
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result

    result["ok"] = True
    result["status"] = "available"
    result["models"] = rows[:40]
    result["model_count"] = len(rows)
    result["capabilities"] = _infer_model_capabilities(rows)
    result["quota"] = _probe_quota(spec, api_base, headers)
    result["message"] = f"{len(rows)} model(s) reachable."
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    return result


def _parse_bool(value: str, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"1", "0", "true", "false", "yes", "no"}:
        raise WebUISettingsError(f"{field} must be boolean")
    return normalized in {"1", "true", "yes"}


def _parse_context_window_tokens(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise WebUISettingsError("context_window_tokens must be an integer") from None
    if parsed not in _CONTEXT_WINDOW_TOKEN_OPTIONS:
        allowed = ", ".join(str(v) for v in sorted(_CONTEXT_WINDOW_TOKEN_OPTIONS))
        raise WebUISettingsError(f"context_window_tokens must be one of: {allowed}")
    return parsed


def _model_configuration_slug(label: str) -> str:
    normalized = _MODEL_CONFIGURATION_SLUG_RE.sub("-", label.strip().lower())
    normalized = normalized.strip("-_")
    if not normalized:
        raise WebUISettingsError("configuration name is required")
    if normalized == "default":
        raise WebUISettingsError("configuration name is reserved")
    if len(normalized) > 48:
        normalized = normalized[:48].rstrip("-_")
    return normalized


def _import_preset_slug(model: str) -> str:
    """Slug for an imported model, tolerant of any provider model id.

    Unlike :func:`_model_configuration_slug` this never raises: bulk import
    derives names from provider-supplied ids, so an unusable id falls back to
    a generic stem that :func:`_unique_preset_slug` then disambiguates.
    """
    normalized = _MODEL_CONFIGURATION_SLUG_RE.sub("-", model.strip().lower())
    normalized = normalized.strip("-_")
    if len(normalized) > 48:
        normalized = normalized[:48].rstrip("-_")
    if not normalized or normalized == "default":
        return "model"
    return normalized


def _unique_preset_slug(existing: Any, base: str) -> str:
    if base not in existing:
        return base
    for suffix in range(2, 100):
        candidate = f"{base[:44].rstrip('-_')}-{suffix}"
        if candidate not in existing:
            return candidate
    raise WebUISettingsError("too many similar configuration names", status=409)


def _snap_context_window_tokens(tokens: Any) -> int | None:
    """Map a provider-reported context window onto an allowed preset value.

    Provider catalogs report exact windows (e.g. 128000, 1048576) while presets
    only accept a fixed ladder. Round down so a preset never claims a larger
    window than the model actually supports.
    """
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
        return None
    allowed = sorted(_CONTEXT_WINDOW_TOKEN_OPTIONS)
    eligible = [value for value in allowed if value <= tokens]
    return eligible[-1] if eligible else allowed[0]


def _parse_import_models(raw: str | None) -> list[dict[str, Any]]:
    """Parse the ``models`` payload of a bulk import request.

    Accepts a JSON array of model ids or of ``{id, label?, context_window?}``
    objects, so the caller can forward rows straight from the provider catalog.
    """
    if not raw or not raw.strip():
        raise WebUISettingsError("models is required")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        raise WebUISettingsError("models must be JSON") from None
    if not isinstance(decoded, list):
        raise WebUISettingsError("models must be a JSON array")
    if len(decoded) > _MODEL_IMPORT_MAX:
        raise WebUISettingsError(f"import is limited to {_MODEL_IMPORT_MAX} models")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in decoded:
        if isinstance(item, str):
            model_id, label, context_window = item, None, None
        elif isinstance(item, dict):
            model_id = item.get("id")
            label = item.get("label")
            context_window = item.get("context_window")
        else:
            continue
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        rows.append(
            {
                "id": model_id[:200],
                "label": label.strip()[:120] if isinstance(label, str) and label.strip() else None,
                "context_window": _snap_context_window_tokens(context_window),
            }
        )
    if not rows:
        raise WebUISettingsError("no valid models to import")
    return rows


def _validate_configured_provider(config: Any, provider: str) -> None:
    if provider == "auto":
        return
    resolved_provider = _resolve_settings_provider(config, provider)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, _, provider_config = resolved_provider
    if spec.is_transcription_only:
        raise WebUISettingsError("provider does not support chat models")
    if not _provider_configured_for_settings(spec, provider_config):
        raise WebUISettingsError("provider is not configured")


def _managed_navin_active(config: Any) -> bool:
    return bool(
        getattr(config.license, "managed_api_key", None)
        and getattr(config.license, "plan", None)
        and str(getattr(config.license, "plan", "")).strip().lower() not in ("", "free")
    )


def _media_provider_row(
    config: Any,
    name: str,
    *,
    spec: Any,
    provider_config: Any,
) -> dict[str, Any]:
    """Image / video / TTS provider row; hide OpenRouter wire URL for Navin."""
    is_navin = name == "navin"
    managed = is_navin and _managed_navin_active(config)
    # Align Settings "Configured" with media tool auto-enable: local providers
    # are ready via default_api_base (Ollama / vLLM) without a pasted key.
    if managed:
        configured = True
    elif provider_config is not None or (spec is not None and getattr(spec, "is_local", False)):
        ready_config = provider_config if provider_config is not None else ProviderConfig()
        configured = media_credentials_ready(name, ready_config)
    else:
        configured = bool(getattr(provider_config, "api_key", None))
    if is_navin and not managed and bool(getattr(provider_config, "api_key", None)):
        configured = True
    return {
        "name": name,
        "label": spec.label if spec is not None else name,
        "configured": configured,
        "auth_type": "oauth" if spec is not None and spec.is_oauth else "api_key",
        "api_key_hint": (
            "Navin plan"
            if managed
            else _mask_secret_hint(getattr(provider_config, "api_key", None))
        ),
        # Never expose the upstream OpenRouter URL on the managed Navin slot.
        "api_base": None if is_navin else getattr(provider_config, "api_base", None),
        "default_api_base": (
            "navin/api"
            if is_navin
            else (spec.default_api_base if spec and spec.default_api_base else None)
        ),
        # Only an active paid plan is "managed". Claiming it for every Navin row
        # advertised a subscription to users who never signed in.
        "managed": managed,
    }


def _media_display_provider(configured_choice: Any, resolved: str, usable: bool) -> str:
    """Return the provider Settings should display for STT / TTS.

    Preserve explicit choices and the managed Navin default. A legacy BYOK
    fallback is not a saved Live setup: displaying it as selected prevents the
    user from choosing that same provider and enabling the Save button.
    """
    if str(configured_choice or "").strip():
        return resolved
    return resolved if usable and resolved == "navin" else ""


def _visible_media_provider_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not is_retired_llm_provider(str(row.get("name") or ""))]


def _image_generation_provider_rows(config: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in image_gen_provider_names():
        if is_retired_llm_provider(name):
            continue
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        rows.append(_media_provider_row(config, name, spec=spec, provider_config=provider_config))
    return _visible_media_provider_rows(rows)


def _video_generation_provider_rows(config: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in video_gen_provider_names():
        if is_retired_llm_provider(name):
            continue
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        rows.append(_media_provider_row(config, name, spec=spec, provider_config=provider_config))
    return _visible_media_provider_rows(rows)


_DEFAULT_REASONING_EFFORT_VALUES: tuple[str, ...] = ("", "low", "medium", "high")


def _reasoning_effort_values_for(provider_name: str, model: str) -> list[str]:
    """Return user-facing reasoning_effort options for this provider+model.

    Values mirror each vendor's real API vocabulary (verified against the
    official docs) so the UI never offers a level the model would reject:

    - Claude: ``output_config.effort`` low/medium/high (+max on Opus 4.6,
      +xhigh/max on Opus 4.7/4.8 and the 5.x families) and adaptive thinking
      on 4.6+; older 4.x/3.7 models use thinking budgets (low/medium/high).
    - OpenAI: none/minimal/low/medium/high/xhigh depending on model
      generation (GPT-5 → minimal…high, GPT-5.1 → none…high,
      GPT-5.2/codex-max → +xhigh, o-series → low…high).
    - Gemini: thinking_level low/medium/high (+minimal on Flash tiers);
      2.5 Flash can disable thinking, 2.5/3 Pro cannot.
    - Grok: grok-4.6 low…xhigh, grok-4.5 low/medium/high, grok-4.3 none…high,
      multi-agent +xhigh, grok-3-mini low/high, others reject the parameter.
    - Kimi: kimi-k3 low/high/max; k2.5/k2.6 toggle on/off;
      k2.7-code and k2-thinking always think.
    - GLM: 5.x full ladder up to max; 4.x toggle on/off.
    - DeepSeek: V4 Pro high/xhigh/max; V4 Flash high/max; V3.x/chat toggle on/off; reasoner always thinks.
    - Qwen: qwen3.8-max low/medium/xhigh; hybrid Qwen3 toggle on/off;
      QwQ always thinks.
    - MiniMax: reasoning always on, no level control.

    ``provider_name`` may be a config key (``minimax``), the display name the
    Settings form sends (``MiniMax``) or a custom gateway. A custom gateway
    that serves a known vendor's model (``magistral-medium``, ``MiniMax-M3``)
    gets that vendor's rules: the model decides what it accepts, not the URL
    it is reached through.
    """
    model_lower = (model or "").lower()
    spec = find_by_name(provider_name) if provider_name else None
    vendor = spec if spec is not None else find_by_model(model_lower)

    if vendor is not None:
        implicit = getattr(vendor, "implicit_reasoning_models", ())
        if implicit and any(pat in model_lower for pat in implicit):
            # Reasoning is always on; only "Default" makes sense.
            return [""]

        remap = getattr(vendor, "reasoning_effort_remap", ())
        if remap:
            # Reverse the remap: surface the distinct wire-vocab outputs as
            # the user's options. Mistral collapses to "high"/"none" → UI
            # shows "Default" + "High".
            wire_values: list[str] = []
            for _user_val, wire_val in remap:
                if wire_val and wire_val != "none" and wire_val not in wire_values:
                    wire_values.append(wire_val)
            return ["", *wire_values]

    # The catalog's provider_any rules name config keys; hand them the
    # canonical key whatever spelling the caller used.
    catalog_provider = spec.name if spec is not None else (provider_name or "")
    family_values = _model_family_reasoning_values(model_lower, catalog_provider)
    if family_values is not None:
        return family_values
    catalog_default = _load_reasoning_catalog().get("default_values")
    if isinstance(catalog_default, list) and catalog_default:
        return [str(v) for v in catalog_default]
    return list(_DEFAULT_REASONING_EFFORT_VALUES)


def _describe_reasoning_effort_value(value: str) -> str:
    return "Auto" if value == "" else value


def _check_reasoning_effort(effort: str, provider: str, model: str) -> None:
    """Refuse a level the provider+model would reject, and say which ones it takes.

    The message carries provider, model and the accepted ladder so the next
    report is self-diagnosing; ``field`` lets the form show it on the Thinking
    control. A bare "invalid reasoning_effort" told the user nothing about
    which of the three inputs was wrong.
    """
    if not effort:
        return
    allowed = _reasoning_effort_values_for(provider, model)
    if not allowed or effort in allowed:
        return
    ladder = ", ".join(_describe_reasoning_effort_value(v) for v in allowed)
    where = f"{provider}/{model}" if provider else (model or "this model")
    raise WebUISettingsError(
        f"invalid reasoning_effort '{effort}' for {where}; allowed values: {ladder}",
        field="reasoning_effort",
    )


def reasoning_effort_values_payload(provider: str, model: str) -> dict[str, Any]:
    """The Thinking ladder for a provider+model pair the form is editing.

    Served live so the dropdown follows what the user just picked instead of
    the preset saved earlier: choosing a level the new model rejects is what
    produced the opaque error this replaces.
    """
    values = _reasoning_effort_values_for(provider or "", model or "")
    return {"provider": provider or "", "model": model or "", "values": values}


# Per-model-family reasoning levels live in a data file so new vendor releases
# only require editing JSON (packaged default below, user override next to
# config.json), not shipping new code.
_REASONING_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "reasoning_catalog.json"
)
_reasoning_catalog_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _read_json_catalog(path: str) -> dict[str, Any]:
    """Read and cache a catalog file, refreshing when its mtime changes."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _reasoning_catalog_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        data = {}
    _reasoning_catalog_cache[path] = (mtime, data)
    return data


def _load_reasoning_catalog() -> dict[str, Any]:
    """Packaged catalog merged with an optional user override.

    User families (from ``<config dir>/reasoning_catalog.json``) are checked
    first so corrections and brand-new models can be added without updating
    the installed package.
    """
    packaged = _read_json_catalog(_REASONING_CATALOG_PATH)
    user_path = str(get_config_path().parent / "reasoning_catalog.json")
    user = _read_json_catalog(user_path)
    if not user:
        return packaged
    merged = dict(packaged)
    if isinstance(user.get("default_values"), list):
        merged["default_values"] = user["default_values"]
    user_families = user.get("families")
    if isinstance(user_families, list):
        merged["families"] = [*user_families, *packaged.get("families", [])]
    return merged


def _catalog_condition_matches(rule: dict[str, Any], model: str, provider: str) -> bool:
    """AND across fields; OR within each list field."""
    contains_any = rule.get("contains_any")
    if contains_any and not any(str(p) in model for p in contains_any):
        return False
    contains_all = rule.get("contains_all")
    if contains_all and not all(str(p) in model for p in contains_all):
        return False
    excludes_any = rule.get("excludes_any")
    if excludes_any and any(str(p) in model for p in excludes_any):
        return False
    provider_any = rule.get("provider_any")
    if provider_any and provider not in [str(p) for p in provider_any]:
        return False
    pattern = rule.get("regex")
    if pattern:
        try:
            if re.search(pattern, model) is None:
                return False
        except re.error:
            return False
    return True


def _catalog_family_matches(family: dict[str, Any], model: str, provider: str) -> bool:
    """Family-level match: any declared condition hitting is enough."""
    match = family.get("match")
    if not isinstance(match, dict):
        return False
    contains_any = match.get("contains_any")
    if contains_any and any(str(p) in model for p in contains_any):
        return True
    provider_any = match.get("provider_any")
    if provider_any and provider in [str(p) for p in provider_any]:
        return True
    pattern = match.get("regex")
    if pattern:
        with suppress(re.error):
            if re.search(pattern, model) is not None:
                return True
    return False


def _model_family_reasoning_values(model: str, provider: str) -> list[str] | None:
    """Resolve reasoning levels from the JSON catalog (first match wins)."""
    catalog = _load_reasoning_catalog()
    families = catalog.get("families")
    if not isinstance(families, list):
        return None
    for family in families:
        if not isinstance(family, dict):
            continue
        if not _catalog_family_matches(family, model, provider):
            continue
        rules = family.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                values = rule.get("values")
                if isinstance(values, list) and _catalog_condition_matches(
                    rule, model, provider
                ):
                    return [str(v) for v in values]
        fallback = family.get("fallback")
        if isinstance(fallback, list):
            return [str(v) for v in fallback]
        # No fallback: let later families (e.g. packaged ones behind a
        # user-override family) take a shot.
    return None


def _transcription_provider_rows(config: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in transcription_provider_names():
        if is_retired_llm_provider(name):
            continue
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        rows.append(
            _media_provider_row(config, name, spec=spec, provider_config=provider_config)
        )
    # Navin first so subscribers land on the managed slot in the picker.
    rows.sort(key=lambda row: (0 if row.get("name") == "navin" else 1, row.get("name") or ""))
    return _visible_media_provider_rows(rows)


def _transcription_managed_models(
    model_presets: list[dict[str, Any]],
    *,
    current_model: str | None,
) -> list[dict[str, Any]]:
    """Curated OpenRouter STT list for Settings → Voice (always the offline catalog).

    Presets may lag behind after a catalog change; the Voice picker must not
    keep showing Fish/Grok leftovers. Enrich price notes from live presets when
    the slug matches.
    """
    from navin.providers.managed_catalog import fallback_stt_managed_models

    preset_by_slug = {
        str(p["model"]): p
        for p in model_presets
        if p.get("modality") == "stt" and p.get("model")
    }
    rows: list[dict[str, Any]] = []
    for row in fallback_stt_managed_models():
        slug = str(row.get("slug") or "")
        preset = preset_by_slug.get(slug)
        enriched = dict(row)
        if preset:
            if preset.get("label"):
                enriched["name"] = preset["label"]
            if preset.get("unit_price_usd") is not None:
                enriched["unitPriceUsd"] = preset.get("unit_price_usd")
            if preset.get("price_note"):
                enriched["priceNote"] = preset.get("price_note")
        # isDefault = catalog default; selected value is form.model separately.
        rows.append(enriched)
    if current_model and not any(r.get("slug") == current_model for r in rows):
        rows.append(
            {
                "slug": current_model,
                "name": current_model,
                "unitPriceUsd": None,
                "priceNote": None,
                "isDefault": False,
            }
        )
    return rows


def _music_generation_provider_rows(config: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in music_gen_provider_names():
        if is_retired_llm_provider(name):
            continue
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        rows.append(_media_provider_row(config, name, spec=spec, provider_config=provider_config))
    return _visible_media_provider_rows(rows)


def _tts_model_supports_reference(model: Any, provider: str | None) -> bool:
    from navin.audio.tts_clone import model_supports_reference

    return model_supports_reference(str(model or ""), provider)


def _tts_provider_rows(config: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in tts_provider_names():
        if is_retired_llm_provider(name):
            continue
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        rows.append(_media_provider_row(config, name, spec=spec, provider_config=provider_config))
    return _visible_media_provider_rows(rows)


def settings_payload(
    *,
    requires_restart: bool = False,
    surface: str | None = "browser",
    runtime_capability_overrides: dict[str, Any] | None = None,
    restart_required_sections: list[str] | None = None,
    apply_state: dict[str, Any] | None = None,
    sync_catalog: bool = False,
) -> dict[str, Any]:
    config = load_config()
    live_account = live_modules_available()
    if live_account and sync_catalog and config.model_catalog.enabled:
        try:
            from navin.providers.managed_catalog import sync_managed_catalog

            sync_managed_catalog(config, force=True, min_interval_s=0)
            config = load_config()
        except Exception:
            pass
    # Paid plan: Groq/whisper leftovers must not stick on Settings → Voice.
    # Runs offline so an open Voice page always shows Navin + managed STT/TTS.
    if live_account:
        try:
            from navin.providers.managed_catalog import heal_managed_voice_settings

            if heal_managed_voice_settings(config):
                save_config(config)
                config = load_config()
        except Exception:
            pass
        # Same guarantee for image / video / music: an unset provider means "not
        # chosen", which for a subscriber has to resolve to their managed slot.
        try:
            from navin.providers.managed_catalog import heal_managed_media_settings

            if heal_managed_media_settings(config):
                save_config(config)
                config = load_config()
        except Exception:
            pass
        # Lyria/Veo/etc. must never remain the chat default (stale modality=text).
        try:
            from navin.providers.managed_catalog import heal_media_chat_default

            if heal_media_chat_default(config):
                save_config(config)
                config = load_config()
        except Exception:
            pass
        # Dead catalog self-heal: without a managed key every "navin ·" preset is
        # unusable (calls would 401) and only pollutes the picker - typically a
        # leftover from a session that ended before disconnect cleanup existed.
        # Removing them here makes the Settings view truthful immediately instead
        # of waiting for the next license validate round-trip.
        try:
            if not (config.license.managed_api_key or "").strip() and any(
                preset.provider == "navin" for preset in config.model_presets.values()
            ):
                from navin.license_client import reset_managed_model_state

                if reset_managed_model_state(config):
                    save_config(config)
        except Exception:
            pass
    # Free plan self-heal: while an OAuth-connected OpenRouter key is present,
    # the two default free presets must exist - whatever deleted them (manual
    # cleanup, an older catalog sync, a config edit), they come back on the
    # next Settings load. install_free_presets is idempotent and offline.
    try:
        openrouter = config.providers.openrouter
        if getattr(openrouter, "oauth_key", False) and (openrouter.api_key or "").strip():
            from navin.webui.openrouter_oauth import install_free_presets

            if install_free_presets(config):
                save_config(config)
    except Exception:
        pass
    defaults = config.agents.defaults
    active_preset_name = defaults.model_preset or "default"
    try:
        effective_preset = config.resolve_preset()
    except Exception:
        effective_preset = config.resolve_default_preset()
        active_preset_name = "default"

    provider_name = (
        config.get_provider_name(effective_preset.model, preset=effective_preset)
        or effective_preset.provider
    )
    provider = config.get_provider(effective_preset.model, preset=effective_preset)
    selected_provider = provider_name
    if effective_preset.provider != "auto":
        spec = find_by_name(effective_preset.provider)
        selected_provider = spec.name if spec else provider_name
    if not live_account and str(selected_provider or "").strip().lower() == "navin":
        selected_provider = ""
        provider_name = ""

    providers = [
        row
        for row in _provider_settings_rows(config, selected_provider)
        if not is_retired_llm_provider(str(row.get("name") or ""))
    ]
    for provider_key, provider_config in _dynamic_provider_items(config):
        providers.append(
            _provider_settings_row(
                provider_key,
                create_dynamic_spec(provider_key, thinking_style=(provider_config.thinking_style or "")),
                provider_config,
            )
        )

    search_config = config.tools.web.search
    image_config = config.tools.image_generation
    transcription = resolve_transcription_config(config)
    tts = resolve_tts_config(config)
    # Keep Settings aligned with Live readiness: BYOK speech providers must
    # be explicitly selected, while a usable Navin default is automatic.
    transcription_provider = _media_display_provider(
        config.transcription.provider, transcription.provider, transcription.configured
    )
    tts_provider = _media_display_provider(
        config.voice.tts_provider, tts.provider, tts.configured
    )
    search_provider = (
        search_config.provider
        if search_config.provider in _WEB_SEARCH_PROVIDER_BY_NAME
        else "duckduckgo"
    )
    image_providers = _image_generation_provider_rows(config)
    selected_image_provider = next(
        (
            provider
            for provider in image_providers
            if provider["name"] == image_config.provider
        ),
        None,
    )
    video_config = config.tools.video_generation
    video_providers = _video_generation_provider_rows(config)
    selected_video_provider = next(
        (
            provider
            for provider in video_providers
            if provider["name"] == video_config.provider
        ),
        None,
    )
    music_config = config.tools.music_generation
    music_providers = _music_generation_provider_rows(config)
    selected_music_provider = next(
        (
            provider
            for provider in music_providers
            if provider["name"] == music_config.provider
        ),
        None,
    )
    # The toggles report the effective state, which for an unset flag depends on
    # the provider credential exactly like the agent's tool gate does.
    image_generation_enabled = resolve_media_tool_enabled(
        image_config.enabled,
        media_credentials_ready(
            image_config.provider,
            getattr(config.providers, image_config.provider, None),
        ),
    )
    video_generation_enabled = resolve_media_tool_enabled(
        video_config.enabled,
        media_credentials_ready(
            video_config.provider,
            getattr(config.providers, video_config.provider, None),
        ),
    )
    music_generation_enabled = resolve_media_tool_enabled(
        music_config.enabled,
        media_credentials_ready(
            music_config.provider,
            getattr(config.providers, music_config.provider, None),
        ),
    )
    budget_percent = 0
    clamp_managed = False
    if live_account:
        try:
            from navin.license_client import uses_managed_key
            from navin.usage_mode import budget_used_percent

            clamp_managed = uses_managed_key(config)
            budget_percent = budget_used_percent(config)
        except Exception:
            pass

    def _budget_allowed(provider: str | None, model: str | None, modality: str | None) -> bool:
        # Navin-subscription catalog only. BYOK / custom providers stay
        # fully selectable: those calls are billed to the user.
        if not clamp_managed:
            return True
        if (provider or "").strip().lower() != "navin":
            return True
        from navin.usage_mode import slug_allowed_for_budget

        # Same denylist for chat and vision: Opus 5+ / Fable 5+ / GPT 5.6
        # hide together. Other vision readers stay, like other chat models.
        return slug_allowed_for_budget(model or "", budget_percent)

    model_presets = [
        {
            "name": "default",
            "label": "Default",
            "active": active_preset_name == "default",
            "is_default": True,
            "model": defaults.model,
            "provider": (
                defaults.provider
                if live_account or (defaults.provider or "").strip().lower() != "navin"
                else ""
            ),
            "max_tokens": defaults.max_tokens,
            "context_window_tokens": defaults.context_window_tokens,
            "temperature": defaults.temperature,
            "reasoning_effort": defaults.reasoning_effort,
            "reasoning_effort_values": _reasoning_effort_values_for(
                defaults.provider, defaults.model
            ),
            "locked": False,
            "source": "managed" if defaults.provider == "navin" else "byok",
            "enabled": True,
            "budget_allowed": _budget_allowed(
                defaults.provider, defaults.model, "text"
            ),
            "modality": "text",
            "vision": supports_vision(defaults.model),
            "grounding": supports_grounding(defaults.model),
            "unit_price_usd": None,
            "price_note": None,
        }
    ]
    for name, preset in config.model_presets.items():
        is_navin = (preset.provider or "").strip().lower() == "navin"
        if is_navin and not live_account:
            continue
        routing_only = name in TIERS
        # Only the router aliases (main / expert / …) stay identity-locked.
        # Imported and catalog chat rows can change model (e.g. Sonnet 5 → 4.5).
        locked = routing_only
        modality = getattr(preset, "modality", None) or "text"
        model_presets.append(
            {
                "name": name,
                "label": preset.label or name,
                "active": active_preset_name == name,
                "is_default": False,
                "model": preset.model,
                "provider": preset.provider,
                "max_tokens": preset.max_tokens,
                "context_window_tokens": preset.context_window_tokens,
                "temperature": preset.temperature,
                "reasoning_effort": preset.reasoning_effort,
                "reasoning_effort_values": _reasoning_effort_values_for(
                    preset.provider, preset.model
                ),
                "locked": locked,
                "routing_only": routing_only,
                "source": "managed" if is_navin or routing_only else "byok",
                "enabled": bool(preset.enabled),
                "budget_allowed": _budget_allowed(
                    preset.provider, preset.model, modality
                ),
                "modality": modality,
                "vision": supports_vision(preset.model, input_modalities=preset.input_modalities),
                "grounding": supports_grounding(preset.model, input_modalities=preset.input_modalities),
                "unit_price_usd": getattr(preset, "unit_price_usd", None),
                "price_note": getattr(preset, "price_note", None),
                "billing_unit": getattr(preset, "billing_unit", None),
                "billing_rate": getattr(preset, "billing_rate", None),
            }
        )

    exec_config = config.tools.exec
    sandbox_status = workspace_sandbox_status(
        restrict_to_workspace=config.tools.restrict_to_workspace,
        workspace=config.workspace_path,
    )
    payload = {
        "agent": {
            "model": effective_preset.model,
            "provider": selected_provider,
            "resolved_provider": provider_name,
            "has_api_key": bool(provider and provider.api_key),
            "model_preset": active_preset_name,
            "max_tokens": effective_preset.max_tokens,
            "context_window_tokens": effective_preset.context_window_tokens,
            "temperature": effective_preset.temperature,
            "reasoning_effort": effective_preset.reasoning_effort,
            "timezone": defaults.timezone,
            "bot_name": defaults.bot_name,
            "bot_icon": defaults.bot_icon,
            "tool_hint_max_length": defaults.tool_hint_max_length,
        },
        "model_presets": model_presets,
        "model_routes": dict(config.model_routes),
        "providers": providers,
        "web_search": {
            "provider": search_provider,
            "api_key_hint": _mask_secret_hint(search_config.api_key),
            "base_url": search_config.base_url or None,
            "max_results": search_config.max_results,
            "timeout": search_config.timeout,
            "providers": list(_WEB_SEARCH_PROVIDER_OPTIONS),
        },
        "web": {
            "enable": config.tools.web.enable,
            "proxy": config.tools.web.proxy,
            "user_agent": config.tools.web.user_agent,
            "search": {
                "max_results": search_config.max_results,
                "timeout": search_config.timeout,
            },
            "fetch": {
                "use_jina_reader": config.tools.web.fetch.use_jina_reader,
            },
        },
        "api": {
            "host": config.api.host,
            "port": config.api.port,
            "timeout": config.api.timeout,
            "api_key_hint": _mask_secret_hint(config.api.api_key),
        },
        # Machine-wide kill-switches for board task git automation; per-project
        # consent lives in <project>/.navin/board/settings.json.
        "board_git": {
            "auto_branch_enabled": config.tools.board_git.auto_branch_enabled,
            "open_pr_enabled": config.tools.board_git.open_pr_enabled,
        },
        # Per-host forge tokens: what lets Create PR work on GitHub, GitLab
        # and Forgejo without the `gh` CLI. Only a hint is ever sent back.
        "forge": _forge_settings_payload(config),
        # The agent's browser. Headless is right almost always, but a site that
        # refuses automated traffic - a captcha above all - is sometimes only
        # solvable in a window the user can see, so the choice belongs here
        # rather than in a config file nobody opens.
        "browser": {
            "headless": config.tools.browser.headless,
            "live_view": config.tools.browser.live_view,
        },
        "computer": _computer_payload(config),
        "observability": {
            "provider": "langfuse",
            "configured": bool(
                os.environ.get("LANGFUSE_SECRET_KEY")
                and os.environ.get("LANGFUSE_PUBLIC_KEY")
            ),
            "base_url": os.environ.get("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com",
        },
        "image_generation": {
            "enabled": image_generation_enabled,
            "enabled_auto": image_config.enabled is None,
            "provider": image_config.provider,
            "provider_configured": bool(
                selected_image_provider and selected_image_provider["configured"]
            ),
            "model": image_config.model,
            "default_aspect_ratio": image_config.default_aspect_ratio,
            "default_image_size": image_config.default_image_size,
            "max_images_per_turn": image_config.max_images_per_turn,
            "save_dir": image_config.save_dir,
            "providers": image_providers,
            "managed_models": [
                {
                    "slug": p["model"],
                    "name": p["label"],
                    "unitPriceUsd": p.get("unit_price_usd"),
                    "priceNote": p.get("price_note"),
                    "isDefault": p["model"] == image_config.model,
                }
                for p in model_presets
                if p.get("modality") == "image" and p.get("model")
            ],
            "budget_caution": "Images use your AI budget. Check Account > Usage.",
        },
        "video_generation": {
            "enabled": video_generation_enabled,
            "enabled_auto": video_config.enabled is None,
            "provider": video_config.provider,
            "provider_configured": bool(
                selected_video_provider and selected_video_provider["configured"]
            ),
            "model": video_config.model,
            "default_aspect_ratio": video_config.default_aspect_ratio,
            "default_duration_seconds": video_config.default_duration_seconds,
            "default_resolution": video_config.default_resolution,
            "max_wait_seconds": video_config.max_wait_seconds,
            "save_dir": video_config.save_dir,
            "providers": video_providers,
            "managed_models": [
                {
                    "slug": p["model"],
                    "name": p["label"],
                    "unitPriceUsd": p.get("unit_price_usd"),
                    "priceNote": p.get("price_note"),
                    "billingUnit": p.get("billing_unit"),
                    "billingRate": p.get("billing_rate"),
                    "isDefault": p["model"] == video_config.model,
                }
                for p in model_presets
                if p.get("modality") == "video" and p.get("model")
            ],
            "budget_caution": "Video generation uses your AI budget. Check Account > Usage.",
        },
        "music_generation": {
            "enabled": music_generation_enabled,
            "enabled_auto": music_config.enabled is None,
            "provider": music_config.provider,
            "provider_configured": bool(
                selected_music_provider and selected_music_provider["configured"]
            ),
            "model": music_config.model,
            "save_dir": music_config.save_dir,
            "providers": music_providers,
            "managed_models": [
                {
                    "slug": p["model"],
                    "name": p["label"],
                    "unitPriceUsd": p.get("unit_price_usd"),
                    "priceNote": p.get("price_note"),
                    "isDefault": p["model"] == music_config.model,
                }
                for p in model_presets
                if p.get("modality") == "music" and p.get("model")
            ],
            "budget_caution": "Music generation uses your AI budget. Check Account > Usage.",
        },
        "transcription": {
            "enabled": transcription.enabled,
            "provider": transcription_provider,
            "provider_configured": bool(transcription_provider) and next(
                (
                    row["configured"]
                    for row in _transcription_provider_rows(config)
                    if row["name"] == transcription_provider
                ),
                transcription.configured,
            ),
            "model": transcription.model if transcription_provider else "",
            "language": transcription.language,
            "max_duration_sec": transcription.max_duration_sec,
            "max_upload_mb": transcription.max_upload_mb,
            "providers": _transcription_provider_rows(config),
            "managed_models": _transcription_managed_models(
                model_presets, current_model=transcription.model
            ),
            "budget_caution": "Transcription uses your AI budget. Check Account > Usage.",
        },
        "voice": {
            "tts_provider": tts_provider,
            "tts_provider_configured": bool(tts_provider) and tts.configured,
            "tts_model": tts.model if tts_provider else "",
            "voice": tts.voice if tts_provider else "auto",
            "auto_speak": tts.auto_speak,
            "response_format": tts.response_format,
            "realtime_enabled": getattr(config.voice, "realtime_enabled", None),
            "realtime_allowed": voice_realtime_allowed(config),
            "realtime_required_plans": ["pro", "ultra", "team"],
            "live": live_voice_status(config),
            "managed_defaults": {"stt_model": NAVIN_STT_MODEL, "tts_model": NAVIN_TTS_MODEL, "voice": "auto"},
            "providers": _tts_provider_rows(config),
            "managed_models": [
                {
                    "slug": p["model"],
                    "name": p["label"],
                    "unitPriceUsd": p.get("unit_price_usd"),
                    "priceNote": p.get("price_note"),
                    "isDefault": p["model"] == tts.model,
                    "supportsReference": _tts_model_supports_reference(
                        p.get("model"), tts_provider
                    ),
                }
                for p in model_presets
                if p.get("modality") == "audio" and p.get("model")
            ],
            "budget_caution": "Voice / TTS uses your AI budget. Check Account > Usage.",
        },
        "runtime": {
            "config_path": str(get_config_path().expanduser()),
            "workspace_path": str(config.workspace_path),
            "gateway_host": config.gateway.host,
            "gateway_port": config.gateway.port,
            "heartbeat": {
                "enabled": config.gateway.heartbeat.enabled,
                "interval_s": config.gateway.heartbeat.interval_s,
                "keep_recent_messages": config.gateway.heartbeat.keep_recent_messages,
            },
            "dream": {
                "schedule": defaults.dream.describe_schedule(),
            },
            "unified_session": defaults.unified_session,
        },
        "usage": token_usage_payload(timezone_name=defaults.timezone),
        "advanced": {
            "restrict_to_workspace": config.tools.restrict_to_workspace,
            "workspace_sandbox": sandbox_status.as_dict(),
            "webui_allow_local_service_access": config.tools.webui_allow_local_service_access,
            "allow_local_preview_access": config.tools.webui_allow_local_service_access,
            "webui_default_access_mode": read_webui_default_access_mode(),
            "private_service_protection_enabled": True,
            "ssrf_whitelist_count": len(config.tools.ssrf_whitelist),
            "mcp_server_count": len(config.tools.mcp_servers),
            "exec_enabled": exec_config.enable,
            "exec_sandbox": exec_config.sandbox or None,
            "exec_path_prepend_set": bool(exec_config.path_prepend),
            "exec_path_append_set": bool(exec_config.path_append),
        },
        "requires_restart": requires_restart,
        "version": _version_payload(),
        "updates": {
            "enabled": config.updates.enabled,
            "autoCheck": config.updates.auto_check,
            "channel": config.updates.channel,
            "configured": updates_configured(),
            "skippedVersion": config.updates.skipped_version,
            # Reported exactly once, on the first snapshot after a restart that
            # followed an install: the user was promised a new version and has
            # no way of telling, from a window that looks identical, whether
            # they got it.
            "justInstalled": consume_completed_update(),
        },
        "docs": _docs_payload(),
    }
    return decorate_settings_payload(
        payload,
        surface=surface,
        runtime_capability_overrides=runtime_capability_overrides,
        restart_required_sections=restart_required_sections,
        apply_state=apply_state,
    )


def settings_usage_payload() -> dict[str, Any]:
    """Return the lightweight token usage slice for Overview refreshes."""
    config = load_config()
    return token_usage_payload(timezone_name=config.agents.defaults.timezone)


def update_agent_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    defaults = config.agents.defaults
    changed = False
    restart_required = False

    if "model_preset" in query or "modelPreset" in query:
        preset = (_query_first_alias(query, "model_preset", "modelPreset") or "").strip()
        preset_value = None if not preset or preset == "default" else preset
        if preset_value is not None and preset_value not in config.model_presets:
            raise WebUISettingsError("unknown model preset")
        if preset_value is not None:
            preset_row = config.model_presets[preset_value]
            modality = getattr(preset_row, "modality", "text") or "text"
            model_slug = (getattr(preset_row, "model", None) or "").strip()
            if modality in _MEDIA_MODALITIES or _is_media_model_slug(model_slug):
                raise WebUISettingsError(
                    "media models (image, video, audio, music, stt) cannot be the chat default"
                )
        if defaults.model_preset != preset_value:
            defaults.model_preset = preset_value
            changed = True
        # An explicit pick (even re-picking the same model) pins the default:
        # catalog syncs must stop rewriting it to the managed default.
        # Clearing back to "default" hands control back to the catalog.
        pinned = preset_value is not None
        if defaults.model_preset_user_pinned != pinned:
            defaults.model_preset_user_pinned = pinned
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("model is required")
        if defaults.model != model:
            defaults.model = model
            changed = True

    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip()
        if not provider:
            raise WebUISettingsError("provider is required")
        _validate_configured_provider(config, provider)
        if defaults.provider != provider:
            defaults.provider = provider
            changed = True

    context_window_tokens = _parse_context_window_tokens(
        _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
    )
    if (
        context_window_tokens is not None
        and defaults.context_window_tokens != context_window_tokens
    ):
        defaults.context_window_tokens = context_window_tokens
        changed = True

    timezone = _query_first(query, "timezone")
    if timezone is not None:
        timezone = timezone.strip()
        if not timezone:
            raise WebUISettingsError("timezone is required")
        try:
            ZoneInfo(timezone)
        except Exception:
            raise WebUISettingsError("invalid timezone") from None
        if defaults.timezone != timezone:
            defaults.timezone = timezone
            changed = True
            restart_required = True

    bot_name = _query_first_alias(query, "bot_name", "botName")
    if bot_name is not None:
        bot_name = bot_name.strip()
        if not bot_name:
            raise WebUISettingsError("bot_name is required")
        if defaults.bot_name != bot_name:
            defaults.bot_name = bot_name
            changed = True
            restart_required = True

    bot_icon = _query_first_alias(query, "bot_icon", "botIcon")
    if bot_icon is not None:
        bot_icon = bot_icon.strip()
        if defaults.bot_icon != bot_icon:
            defaults.bot_icon = bot_icon
            changed = True
            restart_required = True

    reasoning_effort = _query_first_alias(query, "reasoning_effort", "reasoningEffort")
    if reasoning_effort is not None:
        effort = reasoning_effort.strip().lower()
        target_provider = defaults.provider
        target_model = defaults.model
        if defaults.model_preset and defaults.model_preset in config.model_presets:
            active = config.model_presets[defaults.model_preset]
            target_provider = active.provider
            target_model = active.model
        _check_reasoning_effort(effort, target_provider, target_model)
        effort_value = effort or None
        if defaults.reasoning_effort != effort_value:
            defaults.reasoning_effort = effort_value
            changed = True
        if defaults.model_preset and defaults.model_preset in config.model_presets:
            active = config.model_presets[defaults.model_preset]
            if active.reasoning_effort != effort_value:
                active.reasoning_effort = effort_value
                changed = True

    tool_hint_max_length = _query_first_alias(
        query,
        "tool_hint_max_length",
        "toolHintMaxLength",
    )
    if tool_hint_max_length is not None:
        try:
            parsed = int(tool_hint_max_length)
        except ValueError:
            raise WebUISettingsError("tool_hint_max_length must be an integer") from None
        if parsed < 20 or parsed > 500:
            raise WebUISettingsError("tool_hint_max_length must be between 20 and 500")
        if defaults.tool_hint_max_length != parsed:
            defaults.tool_hint_max_length = parsed
            changed = True
            restart_required = True

    # Machine-wide kill-switches for board git automation (Settings > Git).
    board_auto_branch = _query_first_alias(query, "board_auto_branch", "boardAutoBranch")
    if board_auto_branch is not None:
        parsed_flag = _parse_bool(board_auto_branch, "board_auto_branch")
        if config.tools.board_git.auto_branch_enabled != parsed_flag:
            config.tools.board_git.auto_branch_enabled = parsed_flag
            changed = True

    board_open_pr = _query_first_alias(query, "board_open_pr", "boardOpenPr")
    if board_open_pr is not None:
        parsed_flag = _parse_bool(board_open_pr, "board_open_pr")
        if config.tools.board_git.open_pr_enabled != parsed_flag:
            config.tools.board_git.open_pr_enabled = parsed_flag
            changed = True

    # Forge tokens (Settings > Git): what makes Create PR work on GitHub,
    # GitLab and Forgejo without the `gh` CLI.
    forge_host = _query_first_alias(query, "forge_host", "forgeHost")
    if forge_host is not None:
        changed = _apply_forge_host(config, query, forge_host) or changed

    # Settings > Computer > Agent browser. Window mode follows the next
    # launch; live view follows the next action. Neither needs a restart.
    browser_headless = _query_first_alias(query, "browser_headless", "browserHeadless")
    if browser_headless is not None:
        parsed_flag = _parse_bool(browser_headless, "browser_headless")
        if config.tools.browser.headless != parsed_flag:
            config.tools.browser.headless = parsed_flag
            changed = True

    browser_live_view = _query_first_alias(query, "browser_live_view", "browserLiveView")
    if browser_live_view is not None:
        parsed_flag = _parse_bool(browser_live_view, "browser_live_view")
        if config.tools.browser.live_view != parsed_flag:
            config.tools.browser.live_view = parsed_flag
            changed = True

    # Settings > Computer. Disabling closes live sessions; other options are
    # reread before the next action. Tool registration follows on the next turn.
    computer_changed, computer_restart = _apply_computer_settings(config, query)
    changed = changed or computer_changed
    restart_required = restart_required or computer_restart

    if changed:
        save_config(config)
    return settings_payload(requires_restart=restart_required)


_COMPUTER_ASK_VALUES = ("never", "destructive", "always")
_COMPUTER_SESSION_MODES = ("shared", "dedicated")


def _computer_payload(config: Any) -> dict[str, Any]:
    """What Settings shows for the desktop-control tool."""
    cfg = config.tools.computer
    payload: dict[str, Any] = {
        "enabled": bool(cfg.enabled),
        "ask": str(cfg.ask),
        "session_mode": str(cfg.session_mode),
        "live_view": bool(cfg.live_view),
        "audit_log": bool(cfg.audit_log),
        "anthropic_native": bool(cfg.anthropic_native),
        "protected_apps": list(cfg.protected_apps),
        "route_preset": config.model_routes.get("computer") or "",
        "stopped": None,
        "backend": "",
        "backend_reason": "",
        "backend_preference": str(cfg.backend),
        "display": cfg.display or "",
        "audit_screenshots": bool(cfg.audit_screenshots),
        "settle_ms": int(cfg.settle_ms),
        "type_delay_ms": int(cfg.type_delay_ms),
        "max_actions_per_turn": int(cfg.max_actions_per_turn),
        "screenshot_max_width": int(cfg.screenshot_max_width),
        "screenshot_max_height": int(cfg.screenshot_max_height),
        "user_takeover_px": int(cfg.user_takeover_px),
        "failsafe_corner": bool(cfg.failsafe_corner),
        "allowed_apps": list(cfg.allowed_apps),
        "blocked_apps": list(cfg.blocked_apps),
        "ask_apps": list(cfg.ask_apps),
    }
    try:
        from navin.computer.detect import detect_platform
        from navin.computer.policy import stop_reason

        payload["stopped"] = stop_reason()
        choice = detect_platform(preferred=str(cfg.backend), display=cfg.display)
        payload["backend"] = choice.name
        payload["backend_reason"] = choice.reason
        payload["backend_notes"] = list(choice.notes)
    except Exception:  # noqa: BLE001 - never fail the settings page over a probe
        pass
    from navin.providers.model_capabilities import supports_grounding, supports_vision

    preset = config.model_presets.get(payload["route_preset"]) or config.resolve_default_preset()
    payload["model"] = preset.model
    payload["model_provider"] = preset.provider if preset.provider != "auto" else config.get_provider_name(preset.model)
    payload["model_vision"] = supports_vision(preset.model, input_modalities=preset.input_modalities)
    payload["model_grounding"] = supports_grounding(preset.model, input_modalities=preset.input_modalities)
    return payload


def _apply_computer_settings(config: Any, query: QueryParams) -> tuple[bool, bool]:
    """Apply ``computer_*`` query fields; returns (changed, restart_required)."""
    cfg = config.tools.computer
    changed = False
    restart = False

    enabled = _query_first_alias(query, "computer_enabled", "computerEnabled")
    if enabled is not None:
        parsed_flag = _parse_bool(enabled, "computer_enabled")
        if cfg.enabled != parsed_flag:
            cfg.enabled = parsed_flag
            changed = True

    ask = _query_first_alias(query, "computer_ask", "computerAsk")
    if ask is not None:
        value = ask.strip().lower()
        if value not in _COMPUTER_ASK_VALUES:
            raise WebUISettingsError("computer_ask must be never, destructive or always")
        if cfg.ask != value:
            cfg.ask = value  # type: ignore[assignment]
            changed = True

    session_mode = _query_first_alias(query, "computer_session_mode", "computerSessionMode")
    if session_mode is not None:
        value = session_mode.strip().lower()
        if value not in _COMPUTER_SESSION_MODES:
            raise WebUISettingsError("computer_session_mode must be shared or dedicated")
        if cfg.session_mode != value:
            cfg.session_mode = value  # type: ignore[assignment]
            changed = True

    for field, names in (
        ("live_view", ("computer_live_view", "computerLiveView")),
        ("audit_log", ("computer_audit_log", "computerAuditLog")),
        ("anthropic_native", ("computer_anthropic_native", "computerAnthropicNative")),
        ("audit_screenshots", ("computer_audit_screenshots", "computerAuditScreenshots")),
        ("failsafe_corner", ("computer_failsafe_corner", "computerFailsafeCorner")),
    ):
        raw = _query_first_alias(query, *names)
        if raw is None:
            continue
        parsed_flag = _parse_bool(raw, names[0])
        if getattr(cfg, field) != parsed_flag:
            setattr(cfg, field, parsed_flag)
            changed = True

    from pydantic import ValidationError

    from navin.agent.tools.computer import ComputerToolConfig

    updates: dict[str, Any] = {}
    for field in (
        "backend", "display", "settle_ms", "type_delay_ms", "max_actions_per_turn",
        "screenshot_max_width", "screenshot_max_height", "user_takeover_px",
        "protected_apps", "allowed_apps", "blocked_apps", "ask_apps",
    ):
        camel = "computer" + "".join(part.title() for part in field.split("_"))
        raw = _query_first_alias(query, f"computer_{field}", camel)
        if raw is None:
            continue
        value: Any = raw.strip()
        if field.endswith("_apps"):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise WebUISettingsError(f"computer_{field} must be a JSON list of application names") from exc
            if (not isinstance(value, list) or len(value) > 100
                    or any(not isinstance(item, str) or len(item) > 256 or "\x00" in item for item in value)):
                raise WebUISettingsError(f"computer_{field} must contain at most 100 application names")
            value = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        elif field == "display":
            if len(value) > 256 or "\x00" in value:
                raise WebUISettingsError("invalid computer_display")
            value = value or None
        updates[field] = value
    if updates:
        try:
            validated = ComputerToolConfig.model_validate({**cfg.model_dump(), **updates})
        except ValidationError as exc:
            error = exc.errors(include_input=False)[0]
            field = ".".join(str(part) for part in error["loc"])
            raise WebUISettingsError(f"computer_{field}: {error['msg']}") from exc
        for field in updates:
            value = getattr(validated, field)
            if getattr(cfg, field) != value:
                setattr(cfg, field, value)
                changed = True

    return changed, restart


def create_model_configuration(query: QueryParams) -> dict[str, Any]:
    label = (_query_first_alias(query, "label", "displayName") or "").strip()
    raw_name = (_query_first(query, "name") or label).strip()
    model = (_query_first(query, "model") or "").strip()
    provider = (_query_first(query, "provider") or "").strip()

    if not label:
        label = raw_name
    if not model:
        raise WebUISettingsError("model is required")
    if not provider:
        raise WebUISettingsError("provider is required")

    name = _model_configuration_slug(raw_name or label)
    config = load_config()
    if name in config.model_presets:
        raise WebUISettingsError("configuration already exists", status=409)
    _validate_configured_provider(config, provider)

    base = config.resolve_default_preset()
    config.model_presets[name] = ModelPresetConfig(
        label=label,
        model=model,
        provider=provider,
        max_tokens=base.max_tokens,
        context_window_tokens=base.context_window_tokens,
        temperature=base.temperature,
        reasoning_effort=base.reasoning_effort,
    )
    config.agents.defaults.model_preset = name
    config.agents.defaults.model_preset_user_pinned = True
    save_config(config)
    return settings_payload()


def import_model_configurations(query: QueryParams) -> dict[str, Any]:
    """Create one configuration per selected provider model in a single write.

    The bulk counterpart of :func:`create_model_configuration`: names are
    derived from the model ids, models already saved for that provider are
    skipped, and the active preset is left untouched so importing a whole
    catalog never hijacks the model in use.
    """
    provider = (_query_first(query, "provider") or "").strip()
    if not provider:
        raise WebUISettingsError("provider is required")
    rows = _parse_import_models(_query_first(query, "models"))

    config = load_config()
    _validate_configured_provider(config, provider)
    base = config.resolve_default_preset()
    already = {
        preset.model
        for preset in config.model_presets.values()
        if preset.provider == provider
    }

    imported: list[str] = []
    skipped: list[str] = []
    for row in rows:
        model = row["id"]
        if model in already:
            skipped.append(model)
            continue
        name = _unique_preset_slug(config.model_presets, _import_preset_slug(model))
        config.model_presets[name] = ModelPresetConfig(
            label=row["label"] or model,
            model=model,
            provider=provider,
            max_tokens=base.max_tokens,
            context_window_tokens=row["context_window"] or base.context_window_tokens,
            temperature=base.temperature,
            reasoning_effort=base.reasoning_effort,
        )
        already.add(model)
        imported.append(name)

    if imported:
        save_config(config)
    return {
        **settings_payload(),
        "model_import": {
            "provider": provider,
            "imported": len(imported),
            "skipped": len(skipped),
        },
    }


def _apply_preset_reasoning_effort(
    config: Any,
    preset: Any,
    *,
    preset_name: str,
    reasoning_effort_raw: str | None,
) -> bool:
    """Apply a reasoning_effort change to a preset (and active defaults).

    Plan / Navin models are identity-locked, but effort is a user preference
    and must still persist - otherwise the composer Effort picker silently
    stays stuck on Auto.
    """
    if reasoning_effort_raw is None:
        return False
    effort = reasoning_effort_raw.strip().lower()
    _check_reasoning_effort(effort, preset.provider, preset.model)
    effort_value = effort or None
    changed = False
    if preset.reasoning_effort != effort_value:
        preset.reasoning_effort = effort_value
        changed = True
    if config.agents.defaults.model_preset == preset_name:
        if config.agents.defaults.reasoning_effort != effort_value:
            config.agents.defaults.reasoning_effort = effort_value
            changed = True
    return changed


def update_model_configuration(query: QueryParams) -> dict[str, Any]:
    name = (_query_first(query, "name") or "").strip()
    if not name or name == "default":
        raise WebUISettingsError("model configuration is required")

    config = load_config()
    preset = config.model_presets.get(name)
    if preset is None:
        raise WebUISettingsError("unknown model configuration")

    locked = name in TIERS
    enabled_raw = _query_first_alias(query, "enabled", "visible")
    enabled_update: bool | None = None
    if enabled_raw is not None:
        enabled_update = str(enabled_raw).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    # Plan models: identity is read-only. Visibility may change; selecting the
    # model as the chat default is also allowed (Save / star in the UI).
    # Reasoning effort remains writable (composer Effort picker).
    if locked:
        changed = False
        if enabled_update is not None and preset.enabled != enabled_update:
            preset.enabled = enabled_update
            changed = True
        modality = (getattr(preset, "modality", None) or "text").strip().lower()
        model_slug = (getattr(preset, "model", None) or "").strip()
        wants_activate = enabled_update is None or any(
            value is not None
            for value in (
                _query_first_alias(query, "label", "displayName"),
                _query_first(query, "model"),
                _query_first(query, "provider"),
            )
        )
        if modality in _MEDIA_MODALITIES or _is_media_model_slug(model_slug):
            if enabled_update is None:
                raise WebUISettingsError(
                    "media models (image, video, audio, music, stt) cannot be the chat default"
                )
        elif wants_activate and config.agents.defaults.model_preset != name:
            # Older UIs POST label/model/provider on Save after a click; treat
            # that as "make this the active chat model" without rewriting the
            # plan catalog entry.
            config.agents.defaults.model_preset = name
            config.agents.defaults.model_preset_user_pinned = True
            changed = True
        context_window_tokens = _parse_context_window_tokens(
            _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
        )
        if (
            context_window_tokens is not None
            and preset.context_window_tokens != context_window_tokens
        ):
            preset.context_window_tokens = context_window_tokens
            changed = True
        if _apply_preset_reasoning_effort(
            config,
            preset,
            preset_name=name,
            reasoning_effort_raw=_query_first_alias(
                query, "reasoning_effort", "reasoningEffort"
            ),
        ):
            changed = True
        if changed:
            save_config(config)
        return settings_payload()

    changed = False
    if enabled_update is not None and preset.enabled != enabled_update:
        preset.enabled = enabled_update
        changed = True

    label = _query_first_alias(query, "label", "displayName")
    if label is not None:
        label = label.strip()
        if not label:
            raise WebUISettingsError("label is required")
        if preset.label != label:
            preset.label = label
            preset.user_edited = True
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("model is required")
        if preset.model != model:
            preset.model = model
            preset.user_edited = True
            changed = True

    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip()
        if not provider:
            raise WebUISettingsError("provider is required")
        _validate_configured_provider(config, provider)
        if preset.provider != provider:
            preset.provider = provider
            preset.user_edited = True
            changed = True

    context_window_tokens = _parse_context_window_tokens(
        _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
    )
    if (
        context_window_tokens is not None
        and preset.context_window_tokens != context_window_tokens
    ):
        preset.context_window_tokens = context_window_tokens
        changed = True

    if _apply_preset_reasoning_effort(
        config,
        preset,
        preset_name=name,
        reasoning_effort_raw=_query_first_alias(
            query, "reasoning_effort", "reasoningEffort"
        ),
    ):
        changed = True

    if config.agents.defaults.model_preset != name and (
        label is not None or model is not None or provider is not None
    ):
        modality = (getattr(preset, "modality", None) or "text").strip().lower()
        if modality in _MEDIA_MODALITIES:
            raise WebUISettingsError(
                "media models (image, video, audio, music, stt) cannot be the chat default"
            )
        config.agents.defaults.model_preset = name
        config.agents.defaults.model_preset_user_pinned = True
        changed = True

    if changed:
        save_config(config)
    return settings_payload()


def delete_model_configuration(query: QueryParams) -> dict[str, Any]:
    name = (_query_first(query, "name") or "").strip()
    if not name:
        raise WebUISettingsError("model configuration is required")

    config = load_config()

    # "default" est la configuration implicite issue de agents.defaults :
    # la supprimer signifie effacer le modèle actif (retour à "Not
    # configured"), pas retirer la ligne.
    if name == "default":
        defaults = config.agents.defaults
        defaults.model = ""
        defaults.provider = "auto"
        defaults.model_preset = None
        defaults.model_preset_user_pinned = False
        save_config(config)
        return settings_payload()

    if name not in config.model_presets:
        raise WebUISettingsError("unknown model configuration")
    preset = config.model_presets[name]
    if name in TIERS or (preset.provider or "").strip().lower() == "navin":
        raise WebUISettingsError("plan models cannot be deleted")

    del config.model_presets[name]
    if config.agents.defaults.model_preset == name:
        config.agents.defaults.model_preset = None
        config.agents.defaults.model_preset_user_pinned = False
    for role in [r for r, p in config.model_routes.items() if p == name]:
        del config.model_routes[role]
    save_config(config)
    return settings_payload()


_MODEL_ROUTE_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def update_model_route(query: QueryParams) -> dict[str, Any]:
    """Assign (or clear) the model preset used for a task role.

    ``role`` is a short slug such as "deep", "fast", "search", "plan",
    "review", "security", "dev", "docs", "vision" or "computer". ``preset`` is
    the model preset name to route that role to; an empty ``preset`` clears
    the route.
    """
    role = (_query_first(query, "role") or "").strip().lower()
    if not role or not _MODEL_ROUTE_ROLE_RE.match(role):
        raise WebUISettingsError("invalid role")
    preset = (_query_first(query, "preset") or "").strip()

    config = load_config()
    changed = False
    if preset:
        if preset != "default" and preset not in config.model_presets:
            raise WebUISettingsError("unknown model preset")
        selected = config.resolve_default_preset() if preset == "default" else config.model_presets[preset]
        if (getattr(selected, "modality", "text") != "text" or _is_media_model_slug(selected.model)):
            raise WebUISettingsError("task routing requires a chat model, not a media generator")
        if role in {"vision", "computer"}:
            inputs = getattr(selected, "input_modalities", None)
            compatible = (supports_grounding(selected.model, input_modalities=inputs) if role == "computer"
                          else supports_vision(selected.model, input_modalities=inputs))
            if not compatible:
                raise WebUISettingsError("this task requires a compatible vision model")
        if config.model_routes.get(role) != preset:
            config.model_routes[role] = preset
            changed = True
    elif role in config.model_routes:
        del config.model_routes[role]
        changed = True

    if changed:
        save_config(config)
    return settings_payload()


def update_provider_settings(query: QueryParams) -> dict[str, Any]:
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    if provider_name.replace("-", "_") == "navin" and getattr(
        config.license, "managed_api_key", None
    ):
        raise WebUISettingsError("Navin plan provider is managed by your subscription")

    resolved_provider = _resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider
    if spec.is_oauth:
        raise WebUISettingsError("unknown provider")

    changed = False
    supports_auth = provider_supports_auth_mode(provider_key)

    if "api_key" in query or "apiKey" in query:
        api_key = _query_first_alias(query, "api_key", "apiKey")
        api_key = (api_key or "").strip() or None
        if provider_config.api_key != api_key:
            provider_config.api_key = api_key
            changed = True

    if "api_base" in query or "apiBase" in query:
        api_base = _query_first_alias(query, "api_base", "apiBase")
        api_base = (api_base or "").strip() or None
        if provider_config.api_base != api_base:
            provider_config.api_base = api_base
            changed = True

    if "auth_mode" in query or "authMode" in query:
        if not supports_auth:
            raise WebUISettingsError("auth_mode is not supported for this provider")
        raw_mode = (_query_first_alias(query, "auth_mode", "authMode") or "").strip().lower()
        if raw_mode not in {"none", "bearer"}:
            raise WebUISettingsError("auth_mode must be none or bearer")
        if provider_config.auth_mode != raw_mode:
            provider_config.auth_mode = raw_mode  # type: ignore[assignment]
            changed = True
        if raw_mode == "none" and provider_config.api_key:
            provider_config.api_key = None
            changed = True

    if "api_type" in query:
        if spec.name == "openai":
            api_type = (_query_first(query, "api_type") or "").strip()
            try:
                parsed_api_type = type(provider_config)(api_type=api_type).api_type
            except Exception:
                raise WebUISettingsError("api_type must be auto, chat_completions, or responses") from None
            if provider_config.api_type != parsed_api_type:
                provider_config.api_type = parsed_api_type
                changed = True

    connection_changed = False
    region = _optional_connection_field(
        query, "endpoint_region", "endpointRegion", _CONNECTION_REGIONS, "endpoint_region"
    )
    plan = _optional_connection_field(
        query, "access_plan", "accessPlan", _CONNECTION_PLANS, "access_plan"
    )
    protocol = _optional_connection_field(
        query, "wire_protocol", "wireProtocol", _CONNECTION_PROTOCOLS, "wire_protocol"
    )
    if region is not None:
        value = None if region == "" else region
        if provider_config.endpoint_region != value:
            provider_config.endpoint_region = value  # type: ignore[assignment]
            changed = True
            connection_changed = True
    if plan is not None:
        value = None if plan == "" else plan
        if provider_config.access_plan != value:
            provider_config.access_plan = value  # type: ignore[assignment]
            changed = True
            connection_changed = True
    if protocol is not None:
        value = None if protocol == "" else protocol
        if provider_config.wire_protocol != value:
            provider_config.wire_protocol = value  # type: ignore[assignment]
            changed = True
            connection_changed = True
    if connection_changed and "api_base" not in query and "apiBase" not in query:
        filled = lookup_connection_base(
            provider_key,
            provider_config.endpoint_region,
            provider_config.access_plan,
            provider_config.wire_protocol,
        )
        if filled and provider_config.api_base != filled:
            provider_config.api_base = filled
            changed = True

    if changed:
        save_config(config)
    image_config = config.tools.image_generation
    restart_required = (
        changed
        and image_config.enabled
        and image_config.provider == provider_key
        and get_image_gen_provider(provider_key) is not None
    )
    return settings_payload(requires_restart=restart_required)


def _discard_oauth_token(token_path: Any) -> None:
    for path in (token_path, token_path.with_suffix(".lock")):
        with suppress(OSError):
            path.unlink()


def oauth_login_status(query: QueryParams) -> dict[str, Any]:
    """Poll a sign-in started by :func:`login_oauth_provider`."""
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")
    code = (_query_first(query, "code") or "").strip()
    if code:
        try:
            state = oauth_logins.submit_code(spec.name, code)
        except LookupError as e:
            raise WebUISettingsError(str(e), status=409) from e
    else:
        state = oauth_logins.snapshot(spec.name)
    payload = settings_payload()
    payload["oauth"] = state
    return payload


def login_oauth_provider(query: QueryParams) -> dict[str, Any]:
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit import get_token, login_oauth_interactive

            from navin.providers.openai_codex_provider import codex_token_storage
        except ImportError:
            raise WebUISettingsError(
                "oauth_cli_kit not installed. Run: pip install oauth-cli-kit", status=500
            ) from None

        try:
            proxy = resolve_config_env_vars(load_config()).providers.openai_codex.proxy or None
        except ValueError as e:
            raise WebUISettingsError(str(e), status=400) from e
        storage = codex_token_storage()
        token = None
        with suppress(Exception):
            token = get_token(storage=storage, proxy=proxy)
        if token and token.access:
            payload = settings_payload()
            payload["oauth"] = {
                "provider": spec.name,
                "status": "signed_in",
                "authorize_url": None,
                "awaiting_code": False,
                "error": None,
            }
            return payload

        # A refresh token the server answered 401 to can never be revived, and
        # leaving it on disk is what kept the panel claiming "Signed in" while
        # every prompt failed. Start the new flow from a clean slate.
        _discard_oauth_token(storage.get_token_path())

        def _runner(print_fn, prompt_fn):
            # ``open_browser=True`` is about the callback listener, not about
            # the browser: left to itself the kit checks DISPLAY in the *server*
            # process, and a gateway started without one tore the localhost
            # listener down before the redirect could ever arrive. The browser
            # is opened by the client, which is where the user actually is.
            return login_oauth_interactive(
                print_fn=print_fn,
                prompt_fn=prompt_fn,
                storage=storage,
                proxy=proxy,
                open_browser=True,
            )

        state = oauth_logins.start(spec.name, _runner)
        payload = settings_payload()
        payload["oauth"] = state
        return payload

    if spec.name == "github_copilot":
        try:
            from navin.providers.github_copilot_provider import (
                get_github_copilot_login_status,
                login_github_copilot,
            )
        except ImportError:
            raise WebUISettingsError(
                "oauth_cli_kit not installed. Run: pip install oauth-cli-kit", status=500
            ) from None

        token = get_github_copilot_login_status()
        if not token:
            token = login_github_copilot(print_fn=lambda _message: None)
        if not (token and token.access):
            raise WebUISettingsError("OAuth login failed", status=401)
        return settings_payload()

    if spec.name == "xai_oauth":
        try:
            from navin.providers.xai_oauth_provider import (
                get_storage,
                get_valid_xai_oauth_token,
                login_xai_oauth,
            )
        except ImportError:
            raise WebUISettingsError(
                "oauth_cli_kit not installed. Run: pip install oauth-cli-kit", status=500
            ) from None

        token = None
        with suppress(Exception):
            token = get_valid_xai_oauth_token()
        if token and token.access:
            payload = settings_payload()
            payload["oauth"] = {
                "provider": spec.name,
                "status": "signed_in",
                "authorize_url": None,
                "awaiting_code": False,
                "error": None,
            }
            return payload

        _discard_oauth_token(get_storage().get_token_path())
        state = oauth_logins.start(spec.name, login_xai_oauth)
        payload = settings_payload()
        payload["oauth"] = state
        return payload

    raise WebUISettingsError("OAuth login is not supported for this provider")


def logout_oauth_provider(query: QueryParams) -> dict[str, Any]:
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")

    if spec.name == "openai_codex":
        try:
            from navin.providers.openai_codex_provider import codex_token_storage
        except ImportError:
            raise WebUISettingsError(
                "oauth_cli_kit not installed. Run: pip install oauth-cli-kit", status=500
            ) from None
        token_path = codex_token_storage().get_token_path()
    elif spec.name == "github_copilot":
        try:
            from navin.providers.github_copilot_provider import get_storage
        except ImportError:
            raise WebUISettingsError(
                "oauth_cli_kit not installed. Run: pip install oauth-cli-kit", status=500
            ) from None
        token_path = get_storage().get_token_path()
    elif spec.name == "xai_oauth":
        try:
            from navin.providers.xai_oauth_provider import get_storage
        except ImportError:
            raise WebUISettingsError(
                "oauth_cli_kit not installed. Run: pip install oauth-cli-kit", status=500
            ) from None
        token_path = get_storage().get_token_path()
    else:
        raise WebUISettingsError("OAuth logout is not supported for this provider")

    oauth_logins.cancel(spec.name)
    _discard_oauth_token(token_path)
    return settings_payload()


def update_network_safety_settings(query: QueryParams) -> dict[str, Any]:
    raw_allow = (
        _query_first_alias(query, "webui_allow_local_service_access", "webuiAllowLocalServiceAccess")
        or _query_first_alias(query, "allow_local_preview_access", "allowLocalPreviewAccess")
    )
    raw_default_access_mode = _query_first_alias(query, "webui_default_access_mode", "webuiDefaultAccessMode")
    if raw_allow is None and raw_default_access_mode is None:
        raise WebUISettingsError("webui_allow_local_service_access or webui_default_access_mode is required")

    config = load_config()
    changed = False
    if raw_allow is not None:
        webui_allow_local_service_access = _parse_bool(raw_allow, "webui_allow_local_service_access")
        if config.tools.webui_allow_local_service_access != webui_allow_local_service_access:
            config.tools.webui_allow_local_service_access = webui_allow_local_service_access
            changed = True

    if changed:
        save_config(config)
    if raw_default_access_mode is not None:
        default_access_mode = raw_default_access_mode.strip().lower()
        if default_access_mode == "restricted":
            default_access_mode = "default"
        if default_access_mode not in {"default", "full"}:
            raise WebUISettingsError("webui_default_access_mode must be default or full")
        try:
            write_webui_default_access_mode(default_access_mode)
        except ValueError as exc:
            raise WebUISettingsError(str(exc)) from exc
    return settings_payload(requires_restart=changed)


def update_web_search_settings(query: QueryParams) -> dict[str, Any]:
    provider_name = (_query_first(query, "provider") or "").strip().lower()
    provider_option = _WEB_SEARCH_PROVIDER_BY_NAME.get(provider_name)
    if provider_option is None:
        raise WebUISettingsError("unknown web search provider")

    config = load_config()
    search_config = config.tools.web.search
    web_config = config.tools.web
    previous_provider = search_config.provider
    changed = False
    restart_required = False

    def set_search_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(search_config, attr) != value:
            setattr(search_config, attr, value)
            changed = True

    def set_fetch_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(web_config.fetch, attr) != value:
            setattr(web_config.fetch, attr, value)
            changed = True

    if search_config.provider != provider_name:
        search_config.provider = provider_name
        changed = True

    credential = provider_option["credential"]
    if credential == "none":
        set_search_value("api_key", "")
        set_search_value("base_url", "")
    elif credential == "base_url":
        base_url = _query_first_alias(query, "base_url", "baseUrl")
        base_url = base_url.strip() if base_url is not None else None
        if not base_url and previous_provider == provider_name and search_config.base_url:
            base_url = search_config.base_url
        if not base_url:
            raise WebUISettingsError("base_url is required")
        set_search_value("base_url", base_url)
        set_search_value("api_key", "")
    elif credential in {"api_key", "optional_api_key"}:
        raw_api_key = _query_first_alias(query, "api_key", "apiKey")
        api_key = raw_api_key.strip() if raw_api_key is not None else None
        if api_key is None and previous_provider == provider_name and search_config.api_key:
            api_key = search_config.api_key
        if credential == "api_key" and not api_key:
            raise WebUISettingsError("api_key is required")
        set_search_value("api_key", api_key or "")
        set_search_value("base_url", "")
    else:
        raise WebUISettingsError("unknown web search credential type")

    max_results = _query_first_alias(query, "max_results", "maxResults")
    if max_results is not None:
        try:
            parsed = int(max_results)
        except ValueError:
            raise WebUISettingsError("max_results must be an integer") from None
        if parsed < 1 or parsed > 10:
            raise WebUISettingsError("max_results must be between 1 and 10")
        set_search_value("max_results", parsed)

    timeout = _query_first(query, "timeout")
    if timeout is not None:
        try:
            parsed_timeout = int(timeout)
        except ValueError:
            raise WebUISettingsError("timeout must be an integer") from None
        if parsed_timeout < 1 or parsed_timeout > 120:
            raise WebUISettingsError("timeout must be between 1 and 120")
        set_search_value("timeout", parsed_timeout)

    use_jina_reader = _query_first_alias(query, "use_jina_reader", "useJinaReader")
    if use_jina_reader is not None:
        normalized = use_jina_reader.strip().lower()
        if normalized not in {"1", "0", "true", "false", "yes", "no"}:
            raise WebUISettingsError("use_jina_reader must be boolean")
        previous_jina_reader = web_config.fetch.use_jina_reader
        set_fetch_value("use_jina_reader", normalized in {"1", "true", "yes"})
        if web_config.fetch.use_jina_reader != previous_jina_reader:
            restart_required = True

    if changed:
        save_config(config)
    return settings_payload(requires_restart=restart_required)


def update_api_settings(query: QueryParams) -> dict[str, Any]:
    """Update the managed OpenAI-compatible API configuration."""
    config = load_config()
    api = config.api

    host = _query_first(query, "host")
    if host is not None:
        host = host.strip()
        if not host:
            raise WebUISettingsError("host is required")
        api.host = host

    port = _query_first(query, "port")
    if port is not None:
        try:
            parsed_port = int(port)
        except ValueError:
            raise WebUISettingsError("port must be an integer") from None
        if parsed_port < 1 or parsed_port > 65535:
            raise WebUISettingsError("port must be between 1 and 65535")
        api.port = parsed_port

    timeout = _query_first(query, "timeout")
    if timeout is not None:
        try:
            parsed_timeout = float(timeout)
        except ValueError:
            raise WebUISettingsError("timeout must be a number") from None
        if parsed_timeout < 1 or parsed_timeout > 3600:
            raise WebUISettingsError("timeout must be between 1 and 3600")
        api.timeout = parsed_timeout

    api_key = _query_first_alias(query, "api_key", "apiKey")
    if api_key is not None:
        api.api_key = api_key.strip()

    if not is_loopback_host(api.host) and not api.api_key.strip():
        raise WebUISettingsError("an API key is required when the API is available on the network")

    save_config(config)
    return settings_payload()


def update_image_generation_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    image_config = config.tools.image_generation
    changed = False

    provider_name = _query_first(query, "provider")
    if provider_name is not None:
        provider_name = provider_name.strip().lower()
        # Empty clears the selection: a media section with no plan and no key
        # starts unset instead of pointing at a provider nobody chose.
        if provider_name and get_image_gen_provider(provider_name) is None:
            raise WebUISettingsError("unknown image generation provider")
        if image_config.provider != provider_name:
            image_config.provider = provider_name
            changed = True

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        parsed_enabled = _parse_bool(enabled, "enabled")
        auto_enabled = media_credentials_ready(
            image_config.provider,
            getattr(config.providers, image_config.provider, None),
        )
        # A save that only echoes the resolved state must not freeze the flag,
        # otherwise editing the model would opt the install out of auto mode.
        echoes_auto = image_config.enabled is None and parsed_enabled == auto_enabled
        if not echoes_auto and image_config.enabled != parsed_enabled:
            image_config.enabled = parsed_enabled
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("image generation model is required")
        if len(model) > 200:
            raise WebUISettingsError("image generation model is too long")
        if image_config.model != model:
            image_config.model = model
            changed = True

    default_aspect_ratio = _query_first_alias(
        query,
        "default_aspect_ratio",
        "defaultAspectRatio",
    )
    if default_aspect_ratio is not None:
        default_aspect_ratio = default_aspect_ratio.strip()
        if default_aspect_ratio not in _IMAGE_GENERATION_ASPECT_RATIOS:
            raise WebUISettingsError("unsupported image generation aspect ratio")
        if image_config.default_aspect_ratio != default_aspect_ratio:
            image_config.default_aspect_ratio = default_aspect_ratio
            changed = True

    default_image_size = _query_first_alias(
        query,
        "default_image_size",
        "defaultImageSize",
    )
    if default_image_size is not None:
        default_image_size = default_image_size.strip()
        if not default_image_size:
            raise WebUISettingsError("default image size is required")
        if len(default_image_size) > 32 or not all(
            char.isascii() and (char.isalnum() or char in {"x", "X", ":", "-", "_"})
            for char in default_image_size
        ):
            raise WebUISettingsError("unsupported image generation size")
        if image_config.default_image_size != default_image_size:
            image_config.default_image_size = default_image_size
            changed = True

    max_images_per_turn = _query_first_alias(
        query,
        "max_images_per_turn",
        "maxImagesPerTurn",
    )
    if max_images_per_turn is not None:
        try:
            parsed_max = int(max_images_per_turn)
        except ValueError:
            raise WebUISettingsError("max_images_per_turn must be an integer") from None
        if parsed_max < 1 or parsed_max > 8:
            raise WebUISettingsError("max_images_per_turn must be between 1 and 8")
        if image_config.max_images_per_turn != parsed_max:
            image_config.max_images_per_turn = parsed_max
            changed = True

    if image_config.enabled:
        selected_provider = next(
            (
                provider
                for provider in _image_generation_provider_rows(config)
                if provider["name"] == image_config.provider
            ),
            None,
        )
        if not selected_provider or not selected_provider["configured"]:
            raise WebUISettingsError("image generation provider is not configured")

    if changed:
        save_config(config)
    return settings_payload(requires_restart=changed)


def update_video_generation_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    video_config = config.tools.video_generation
    changed = False

    provider_name = _query_first(query, "provider")
    if provider_name is not None:
        provider_name = provider_name.strip().lower()
        if provider_name and get_video_gen_provider(provider_name) is None:
            raise WebUISettingsError("unknown video generation provider")
        if video_config.provider != provider_name:
            video_config.provider = provider_name
            changed = True

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        parsed_enabled = _parse_bool(enabled, "enabled")
        auto_enabled = media_credentials_ready(
            video_config.provider,
            getattr(config.providers, video_config.provider, None),
        )
        # A save that only echoes the resolved state must not freeze the flag,
        # otherwise editing the model would opt the install out of auto mode.
        echoes_auto = video_config.enabled is None and parsed_enabled == auto_enabled
        if not echoes_auto and video_config.enabled != parsed_enabled:
            video_config.enabled = parsed_enabled
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("video generation model is required")
        if len(model) > 200:
            raise WebUISettingsError("video generation model is too long")
        if video_config.model != model:
            video_config.model = model
            changed = True

    default_aspect_ratio = _query_first_alias(
        query,
        "default_aspect_ratio",
        "defaultAspectRatio",
    )
    if default_aspect_ratio is not None:
        default_aspect_ratio = default_aspect_ratio.strip()
        if default_aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise WebUISettingsError("unsupported video generation aspect ratio")
        if video_config.default_aspect_ratio != default_aspect_ratio:
            video_config.default_aspect_ratio = default_aspect_ratio
            changed = True

    default_duration = _query_first_alias(
        query,
        "default_duration_seconds",
        "defaultDurationSeconds",
    )
    if default_duration is not None:
        try:
            parsed_duration = int(default_duration)
        except ValueError:
            raise WebUISettingsError("default_duration_seconds must be an integer") from None
        if parsed_duration < 1 or parsed_duration > 60:
            raise WebUISettingsError("default_duration_seconds must be between 1 and 60")
        if video_config.default_duration_seconds != parsed_duration:
            video_config.default_duration_seconds = parsed_duration
            changed = True

    default_resolution = _query_first_alias(
        query,
        "default_resolution",
        "defaultResolution",
    )
    if default_resolution is not None:
        default_resolution = default_resolution.strip()
        if len(default_resolution) > 32 or not all(
            char.isascii() and (char.isalnum() or char in {"x", "X", ":", "-", "_"})
            for char in default_resolution
        ):
            raise WebUISettingsError("unsupported video generation resolution")
        if video_config.default_resolution != default_resolution:
            video_config.default_resolution = default_resolution
            changed = True

    if video_config.enabled:
        selected_provider = next(
            (
                provider
                for provider in _video_generation_provider_rows(config)
                if provider["name"] == video_config.provider
            ),
            None,
        )
        if not selected_provider or not selected_provider["configured"]:
            raise WebUISettingsError("video generation provider is not configured")

    if changed:
        save_config(config)
    return settings_payload(requires_restart=changed)


def update_transcription_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    if _apply_transcription_settings(config, query):
        save_config(config)
    return settings_payload()


def _apply_transcription_settings(config: Config, query: QueryParams) -> bool:
    transcription = config.transcription
    changed = False

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        parsed_enabled = _parse_bool(enabled, "enabled")
        if transcription.enabled != parsed_enabled:
            transcription.enabled = parsed_enabled
            changed = True

    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip().lower()
        if provider:
            provider_spec = resolve_transcription_provider(provider)
            if provider_spec is None:
                raise WebUISettingsError("unknown transcription provider")
            provider = provider_spec.name
        if transcription.provider != provider:
            transcription.provider = provider
            if _query_first(query, "model") is None:
                transcription.model = ""
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip() or None
        if model is not None and len(model) > 200:
            raise WebUISettingsError("transcription model is too long")
        if transcription.model != model:
            transcription.model = model
            changed = True

    language = _query_first(query, "language")
    if language is not None:
        language = language.strip().lower() or None
        if language is not None and not re.fullmatch(r"[a-z]{2,3}", language):
            raise WebUISettingsError("transcription language must be 2-3 lowercase letters")
        if transcription.language != language:
            transcription.language = language
            changed = True

    max_duration_sec = _query_first_alias(query, "max_duration_sec", "maxDurationSec")
    if max_duration_sec is not None:
        try:
            parsed_duration = int(max_duration_sec)
        except ValueError:
            raise WebUISettingsError("max_duration_sec must be an integer") from None
        if parsed_duration < 1 or parsed_duration > 600:
            raise WebUISettingsError("max_duration_sec must be between 1 and 600")
        if transcription.max_duration_sec != parsed_duration:
            transcription.max_duration_sec = parsed_duration
            changed = True

    max_upload_mb = _query_first_alias(query, "max_upload_mb", "maxUploadMb")
    if max_upload_mb is not None:
        try:
            parsed_upload = int(max_upload_mb)
        except ValueError:
            raise WebUISettingsError("max_upload_mb must be an integer") from None
        if parsed_upload < 1 or parsed_upload > 100:
            raise WebUISettingsError("max_upload_mb must be between 1 and 100")
        if transcription.max_upload_mb != parsed_upload:
            transcription.max_upload_mb = parsed_upload
            changed = True

    if (provider is not None or model is not None) and not transcription.selection_explicit:
        transcription.selection_explicit = True
        changed = True
    return changed


def update_music_generation_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    music_config = config.tools.music_generation
    changed = False

    provider_name = _query_first(query, "provider")
    if provider_name is not None:
        provider_name = provider_name.strip().lower()
        if provider_name and get_music_gen_provider(provider_name) is None:
            raise WebUISettingsError("unknown music generation provider")
        if music_config.provider != provider_name:
            music_config.provider = provider_name
            changed = True

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        parsed_enabled = _parse_bool(enabled, "enabled")
        auto_enabled = media_credentials_ready(
            music_config.provider,
            getattr(config.providers, music_config.provider, None),
        )
        echoes_auto = music_config.enabled is None and parsed_enabled == auto_enabled
        if not echoes_auto and music_config.enabled != parsed_enabled:
            music_config.enabled = parsed_enabled
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("music generation model is required")
        if len(model) > 200:
            raise WebUISettingsError("music generation model is too long")
        if music_config.model != model:
            music_config.model = model
            changed = True

    if music_config.enabled:
        selected_provider = next(
            (
                provider
                for provider in _music_generation_provider_rows(config)
                if provider["name"] == music_config.provider
            ),
            None,
        )
        if not selected_provider or not selected_provider["configured"]:
            raise WebUISettingsError("music generation provider is not configured")

    if changed:
        save_config(config)
    return settings_payload(requires_restart=changed)


def update_voice_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    if _apply_voice_settings(config, query):
        save_config(config)
    return settings_payload()


def update_live_voice_settings(query: QueryParams) -> dict[str, Any]:
    """Validate and save both speech engines together, without resetting either draft."""
    config = load_config()
    stt_changed = _apply_transcription_settings(config, query)
    tts_changed = _apply_voice_settings(config, query)
    if stt_changed or tts_changed:
        save_config(config)
    return settings_payload()


def _apply_voice_settings(config: Config, query: QueryParams) -> bool:
    voice = config.voice
    changed = False

    tts_provider = _query_first_alias(query, "tts_provider", "ttsProvider")
    if tts_provider is not None:
        tts_provider = tts_provider.strip().lower()
        if tts_provider:
            provider_spec = resolve_tts_provider(tts_provider)
            if provider_spec is None:
                raise WebUISettingsError("unknown TTS provider")
            tts_provider = provider_spec.name
        if voice.tts_provider != tts_provider:
            voice.tts_provider = tts_provider
            if _query_first_alias(query, "tts_model", "ttsModel") is None:
                voice.tts_model = ""
            if _query_first(query, "voice") is None:
                voice.voice = "auto"
            changed = True

    tts_model = _query_first_alias(query, "tts_model", "ttsModel")
    if tts_model is not None:
        tts_model = tts_model.strip() or None
        if tts_model is not None and len(tts_model) > 200:
            raise WebUISettingsError("TTS model is too long")
        if voice.tts_model != tts_model:
            voice.tts_model = tts_model
            if _query_first(query, "voice") is None:
                voice.voice = "auto"
            changed = True

    voice_name = _query_first(query, "voice")
    if voice_name is not None:
        voice_name = voice_name.strip() or "auto"
        if len(voice_name) > 80:
            raise WebUISettingsError("voice name is too long")
        if voice.voice != voice_name:
            voice.voice = voice_name
            changed = True

    auto_speak = _query_first_alias(query, "auto_speak", "autoSpeak")
    if auto_speak is not None:
        parsed = _parse_bool(auto_speak, "auto_speak")
        if voice.auto_speak != parsed:
            voice.auto_speak = parsed
            changed = True

    response_format = _query_first_alias(query, "response_format", "responseFormat")
    if response_format is not None:
        response_format = response_format.strip().lower() or "mp3"
        if response_format not in {"mp3", "wav"}:
            raise WebUISettingsError("response_format must be mp3 or wav")
        if voice.response_format != response_format:
            voice.response_format = response_format
            changed = True

    realtime_enabled = _query_first_alias(query, "realtime_enabled", "realtimeEnabled")
    if realtime_enabled is not None:
        raw = realtime_enabled.strip().lower()
        if raw in {"", "null", "none", "auto"}:
            parsed_flag: bool | None = None
        else:
            parsed_flag = _parse_bool(realtime_enabled, "realtime_enabled")
        if voice.realtime_enabled != parsed_flag:
            voice.realtime_enabled = parsed_flag
            changed = True

    if (tts_provider is not None or tts_model is not None) and not voice.selection_explicit:
        voice.selection_explicit = True
        changed = True
    return changed
