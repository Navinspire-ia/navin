"""Credential readiness for the media generation tools.

Image and video generation reuse the chat provider credentials, so a media tool
is usable exactly when its selected provider already carries what the client
needs. That rule backs the ``auto`` mode of ``tools.imageGeneration.enabled``
and ``tools.videoGeneration.enabled``: unset means "on as soon as the provider
is usable", which keeps the studio modules from promising visuals that the
runtime cannot produce.
"""

from __future__ import annotations

from typing import Any

from navin.providers.registry import find_by_name


def media_credentials_ready(provider_name: str, provider_config: Any | None) -> bool:
    """Report whether ``provider_name`` can serve a media request as configured.

    OAuth providers always report ``False``: their credential lives outside the
    config file, so auto-enabling on their behalf would be a guess. They need an
    explicit ``enabled: true``.
    """
    if not provider_name or provider_config is None:
        return False
    api_key = str(getattr(provider_config, "api_key", "") or "").strip()
    api_base = str(getattr(provider_config, "api_base", "") or "").strip()
    spec = find_by_name(provider_name)
    if spec is None:
        return bool(api_key or api_base)
    if spec.is_oauth:
        return False
    if spec.is_local:
        return bool(api_base or spec.default_api_base)
    if spec.is_direct:
        return bool(api_base)
    return bool(api_key)


def resolve_media_tool_enabled(explicit: bool | None, provider_ready: bool) -> bool:
    """Resolve a media tool's tri-state ``enabled`` flag.

    ``True``/``False`` are operator decisions and always win; ``None`` defers to
    whether the provider is usable.
    """
    if explicit is not None:
        return explicit
    return provider_ready
