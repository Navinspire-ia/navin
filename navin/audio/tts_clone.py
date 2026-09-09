# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Voice-clone capability for TTS, without a new provider or a local model.

Catalogue voices keep working exactly as they do today. Cloning is an extra
field on the same HTTP call, and only for models that accept a reference clip.
Nothing here imports torch or talks to a local ML runtime.
"""

from __future__ import annotations

import base64

# Slugs (or fragments) that accept a reference clip or a provider voice id.
# Compared case-insensitively against the model id the user typed or picked.
_REFERENCE_MARKERS: tuple[str, ...] = (
    "fish-audio",
    "fishaudio",
    "elevenlabs",
    "eleven_labs",
    "eleven-multilingual",
    "minimax",
    "playht",
    "play.ai",
    "playai-clone",
)

# These providers speak catalogue voices only. A BYOK user pointing them at a
# custom model id still cannot send a reference clip through this payload.
_CATALOGUE_ONLY_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "groq",
        "gemini",
        "google",
        "ollama",
        "vllm",
        "lm_studio",
        "lm-studio",
        "lmstudio",
    }
)


def model_supports_reference(
    model: str | None,
    provider: str | None = None,
) -> bool:
    """True when this TTS model can clone from a clip or a provider voice id.

    Navin and OpenRouter inherit the capability from the *model slug*, so a
    user who types ``fish-audio/s2.1-pro`` in the BYOK field gets cloning
    without a new provider row. Local and OpenAI-style catalogue providers
    never do, even if the model string looks exotic.
    """
    slug = (model or "").strip().lower()
    host = (provider or "").strip().lower()
    if host in _CATALOGUE_ONLY_PROVIDERS:
        return False
    return any(marker in slug for marker in _REFERENCE_MARKERS)


def reference_extra_fields(audio: bytes) -> dict[str, object]:
    """Fields merged into ``/audio/speech`` when a clone clip is present.

    Fish Audio (native and via OpenRouter) reads ``references``. Other
    clone-capable gateways ignore unknown keys. Core fields (model, input,
    voice, response_format) are never included, so callers cannot overwrite
    them by accident.
    """
    if not audio:
        raise ValueError("reference audio is empty")
    encoded = base64.b64encode(audio).decode("ascii")
    return {"references": [{"audio": encoded, "text": ""}]}
