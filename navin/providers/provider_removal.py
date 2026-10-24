# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What a disconnected provider leaves behind: nothing.

Its chat models leave the model list, and every pick made on it (the default
chat model, voice, transcription, Live, image, video) moves to a provider that
still works: Navin's defaults for a subscriber, otherwise another connected
provider's model, otherwise nothing chosen (the user picks).
"""

from __future__ import annotations

from typing import Any

from navin.providers.media_choice import media_selected, set_media

MEDIA_KINDS = ("image", "video", "tts", "stt", "live")


def forget_provider(config: Any, name: str, *, media: bool = True) -> bool:
    """Drop ``name`` from the model list and from every pick. True when something changed.

    ``media=False`` keeps the voice / image / video / Live picks: one made before
    the key is pasted must survive until the key arrives.
    """
    name = (name or "").strip().lower()
    if not name or name == "navin":
        return False
    changed = False

    removed = [key for key, preset in config.model_presets.items() if (preset.provider or "").strip().lower() == name]
    for key in removed:
        del config.model_presets[key]
        changed = True

    defaults = config.agents.defaults
    if (defaults.model_preset or "") in removed or (defaults.provider or "").strip().lower() == name:
        defaults.model_preset = ""
        defaults.model = ""
        defaults.provider = "auto"
        defaults.model_preset_user_pinned = False
        changed = True

    for kind in MEDIA_KINDS if media else ():
        provider, _model = media_selected(config, kind)
        if provider.strip().lower() == name:
            set_media(config, kind, "", "")
            changed = True

    if not changed:
        return False

    # Navin's defaults for a subscriber: the catalog sync and the heals fill
    # what was emptied above (chat model, voice, transcription, Live, media).
    try:
        from navin.license_client import uses_managed_key

        managed = uses_managed_key(config)
    except Exception:  # noqa: BLE001 - no license state: no managed defaults
        managed = False
    if managed:
        from navin.providers.managed_catalog import (
            heal_managed_media_settings,
            heal_managed_voice_settings,
            heal_media_chat_default,
        )

        for heal in (heal_media_chat_default, heal_managed_voice_settings, heal_managed_media_settings):
            try:
                heal(config)
            except Exception:  # noqa: BLE001 - one heal failing leaves the others
                continue

    # Otherwise (or if nothing filled it): another connected provider's model.
    if not defaults.model_preset and not defaults.model and config.model_presets:
        defaults.model_preset = next(iter(config.model_presets))
    return True


def prune_disconnected_providers(config: Any) -> bool:
    """Clean what earlier disconnections left: the chat models of a provider
    that has no key, no address and no key in the environment anymore. Media
    picks stay (one may wait for its key); the Disconnect button clears them.

    Account sign-ins (Codex, Grok...), Navin and providers that need nothing
    (local ones with a default address) are never touched.
    """
    import os

    from navin.providers.registry import find_by_name

    in_use = {(preset.provider or "").strip().lower() for preset in config.model_presets.values()}
    in_use.add((config.agents.defaults.provider or "").strip().lower())
    changed = False
    for name in sorted(in_use - {"", "auto", "navin"}):
        spec = find_by_name(name)
        if spec is None or spec.is_oauth or spec.is_local:
            continue
        provider_config = getattr(config.providers, spec.name, None)
        if provider_config is None:
            continue
        if any(getattr(provider_config, field, None) for field in ("api_key", "api_base", "region", "profile")):
            continue
        if any(os.environ.get(key) for key in (spec.env_key, *spec.env_key_aliases) if key):
            continue
        if forget_provider(config, spec.name, media=False):
            changed = True
    return changed
