# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The Navin LLM gateway (LiteLLM) behind the managed ``navin`` provider.

With the site in PROVIDER_MODE=navin_litellm, the managed key works on the
Navin gateway (``managedApiBase``, e.g. https://llm.navin.live/v1) instead of
OpenRouter. The gateway speaks the standard OpenAI API for every modality:
``/chat/completions`` (text, multimodal, music with audio output),
``/images/generations``, ``/videos``, ``/audio/speech`` and
``/audio/transcriptions``. The managed ``navin`` media clients, which speak
OpenRouter's own image, video and speech endpoints, switch to the standard
OpenAI clients when their base is the gateway: same key, same base, same
models chosen in the admin's LiteLLM tabs.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_OPENROUTER_DOMAIN = "openrouter.ai"


def _is_openrouter_host(host: str) -> bool:
    return host == _OPENROUTER_DOMAIN or host.endswith("." + _OPENROUTER_DOMAIN)

# Where the Navin subscription runs. It never falls back to OpenRouter: a
# missing base, or one left over from the OpenRouter era, means this one.
NAVIN_GATEWAY_BASE = "https://llm.navin.live/v1"


def is_navin_gateway(api_base: str | None) -> bool:
    """True when the managed base is the Navin gateway, not OpenRouter."""
    if not api_base or not isinstance(api_base, str):
        return False
    host = (urlsplit(api_base.strip()).hostname or "").lower()
    return bool(host) and not _is_openrouter_host(host)


def navin_managed_base(api_base: str | None) -> str:
    """The base the managed ``navin`` provider must use: never OpenRouter.

    A delivered or user-set gateway base is kept (staging, self-hosted
    LiteLLM); an empty or OpenRouter base becomes the Navin gateway.
    """
    if is_navin_gateway(api_base):
        return str(api_base).strip().rstrip("/")
    return NAVIN_GATEWAY_BASE
