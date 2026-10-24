# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Model lists for the media uses in the terminal settings (image, video,
listening, voice, Live): the same lists as the desktop, read from the models
already added and from every connected provider's catalog."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from navin.tui.screens import PickItem

# (provider path, model path) in the camelCase config document.
USE_PATHS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "image": (("tools", "imageGeneration", "provider"), ("tools", "imageGeneration", "model")),
    "video": (("tools", "videoGeneration", "provider"), ("tools", "videoGeneration", "model")),
    "stt": (("transcription", "provider"), ("transcription", "model")),
    "tts": (("voice", "ttsProvider"), ("voice", "ttsModel")),
    "live": (("voice", "liveRealtimeProvider"), ("voice", "liveRealtimeModel")),
}
NONE_ID = "::none"


def media_model_items(kind: str) -> list[PickItem]:
    """Every model usable for *kind*, grouped by provider (Navin first)."""
    from navin.config.loader import load_config
    from navin.providers.media_choice import LIVE_PROVIDERS, normalize_media_model
    from navin.webui.settings_api import (
        connected_media_providers,
        media_model_choices,
        provider_models_payload,
    )

    config = load_config()
    seen: set[str] = set()
    items: list[PickItem] = []

    def add(provider: str, provider_label: str, model: str, label: str = "") -> None:
        model = normalize_media_model(kind, provider, model)
        value = f"{provider}::{model}"
        if not provider or not model or value in seen:
            return
        seen.add(value)
        items.append(PickItem(value, label or model, model if label and label != model else "", group=provider_label))

    for option in media_model_choices(config).get(kind, {}).get("options", []):
        add(option["provider"], option["provider_label"], option["model"], option.get("label") or "")

    providers = [
        ref for ref in connected_media_providers(config)
        if kind != "live" or ref["provider"] in LIVE_PROVIDERS
    ]

    def fetch(ref: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        try:
            payload = provider_models_payload({"provider": [ref["provider"]], "modality": [kind]})
        except Exception:  # noqa: BLE001 - one provider down must not hide the others
            return ref, []
        return ref, payload.get("models", []) if payload.get("status") == "available" else []

    if providers:
        with ThreadPoolExecutor(max_workers=min(6, len(providers))) as pool:
            for ref, rows in pool.map(fetch, providers):
                for row in rows:
                    add(ref["provider"], ref["label"], str(row.get("id") or ""), str(row.get("label") or ""))

    items.sort(key=lambda item: (item.group != "Navin", item.group.lower(), item.title.lower()))
    return items


def apply_media_model(data: dict[str, Any], kind: str, chosen: str, setter: Any) -> None:
    """Write a picked "provider::model" (or NONE_ID for Live) into *data*."""
    from navin.providers.media_choice import normalize_media_model

    provider_path, model_path = USE_PATHS[kind]
    if chosen == NONE_ID:
        setter(data, provider_path, "")
        setter(data, model_path, "")
        return
    provider, _, model = chosen.partition("::")
    current_provider = _get(data, provider_path)
    setter(data, provider_path, provider)
    setter(data, model_path, normalize_media_model(kind, provider, model))
    if kind == "tts":
        if current_provider != provider:
            # A voice name belongs to its provider (Kore is Gemini's).
            setter(data, ("voice", "voice"), "auto")
        setter(data, ("voice", "selectionExplicit"), True)
    elif kind == "stt":
        setter(data, ("transcription", "selectionExplicit"), True)
        setter(data, ("transcription", "enabled"), True)


def describe(data: dict[str, Any], kind: str) -> str:
    provider_path, model_path = USE_PATHS[kind]
    provider = str(_get(data, provider_path) or "")
    model = str(_get(data, model_path) or "")
    if not model:
        return ""
    return f"{model}  ·  {provider}" if provider else model


def _get(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node
