# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Computer models stay inside the selected configured provider's catalog."""

from __future__ import annotations

import re
from typing import Any

from navin.config.loader import load_config, save_config
from navin.config.schema import ModelPresetConfig
from navin.providers.model_capabilities import supports_grounding, supports_vision
from navin.webui.http_utils import query_first
from navin.webui.settings_api import (
    WebUISettingsError,
    _is_media_model_slug,
    _model_configuration_slug,
    _resolve_settings_provider,
    _validate_configured_provider,
    provider_models_payload,
    settings_payload,
)


def _navin_selection(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep current flagship families from the subscriber's vision catalog.

    This is catalog curation, not a benchmark score. BYOK does not use it.
    Limited plans keep their available vision models when no flagship exists.
    """
    leaders: dict[str, tuple[tuple[int, tuple[int, ...]], list[dict[str, Any]]]] = {}
    for row in models:
        slug = row["id"].lower().rsplit("/", 1)[-1]
        if re.search(r"(?:^|[-.])(?:mini|nano|lite|small|haiku)(?:[-.:]|$)", slug):
            continue
        capacity = 1
        if "claude" in slug and ("opus" in slug or "sonnet" in slug):
            family = "claude-opus" if "opus" in slug else "claude-sonnet"
        elif "gpt-5" in slug or "computer-use" in slug:
            family = "gpt" if "gpt-5" in slug else "computer-use"
        elif "gemini" in slug:
            family, capacity = "gemini", 2 if "pro" in slug else 1
        elif "qwen" in slug and any(part in slug for part in ("max", "plus", "flash", "vl")):
            family = "qwen-vl" if "vl" in slug else "qwen"
            capacity = 3 if "max" in slug else 2 if "plus" in slug else 1
        elif "grok-4" in slug:
            family = "grok"
        elif row["grounding"]:
            family = re.sub(r"[\d.]+", "", slug)
        else:
            continue
        match = re.search(r"\d{1,2}(?:[.-]\d{1,2})?", slug)
        version = tuple(int(part) for part in re.split(r"[.-]", match[0])) if match else (0,)
        rank = capacity, version
        previous = leaders.get(family)
        if previous is None or rank > previous[0]:
            leaders[family] = rank, [row]
        elif rank == previous[0]:
            previous[1].append(row)
    return [row for _, rows in leaders.values() for row in rows] or models


def computer_models_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    provider = (query_first(query, "provider") or "").strip()
    if not provider or provider == "auto":
        raise WebUISettingsError("Select a configured provider")
    config = load_config()
    _validate_configured_provider(config, provider)
    resolved = _resolve_settings_provider(config, provider)
    if resolved is None:
        raise WebUISettingsError("Unknown provider")
    provider = resolved[1]
    presets = {
        name: preset for name, preset in config.model_presets.items()
        if preset.provider == provider and preset.enabled and preset.modality == "text"
    }
    status, message = "available", None
    if provider == "navin":
        # Only the models in this subscriber's managed catalog. The generic
        # /v1/models catalog can contain models outside the subscribed plan.
        catalog = provider_models_payload({"provider": [provider]})
        status, message = catalog["status"], catalog.get("message")
        declarations = {row["id"]: row for row in catalog["models"]}
        rows = [{"id": preset.model, "label": preset.label or preset.model,
                 "vision": declarations.get(preset.model, {}).get("vision",
                     supports_vision(preset.model, input_modalities=preset.input_modalities)),
                 "context_window": preset.context_window_tokens}
                for name, preset in presets.items() if name not in {"main", "light", "executor", "expert"}]
    else:
        catalog = provider_models_payload({"provider": [provider]})
        status, message = catalog["status"], catalog.get("message")
        rows = catalog["models"]
        if status != "available":
            rows = [{"id": preset.model, "label": preset.label or preset.model,
                     "vision": supports_vision(preset.model, input_modalities=preset.input_modalities),
                     "context_window": preset.context_window_tokens} for preset in presets.values()]
    models = []
    seen: set[str] = set()
    for row in rows:
        model = str(row.get("id") or "")
        declared = row.get("vision")
        vision = declared if isinstance(declared, bool) else supports_vision(model)
        if not vision or not model or model in seen or _is_media_model_slug(model):
            continue
        seen.add(model)
        preset_name = next((name for name, preset in presets.items() if preset.model == model), "")
        models.append({
            "id": model, "label": row.get("label") or model,
            "vision": True, "grounding": supports_grounding(model),
            "preset": preset_name, "context_window": row.get("context_window"),
        })
    if provider == "navin":
        models = _navin_selection(models)
    def priority(row: dict[str, Any]) -> tuple[int, str]:
        model = row["id"].lower()
        if provider == "navin" and "qwen3.8-max" in model:
            rank = 0
        else:
            rank = 1 if row["grounding"] else 2
        return rank, str(row["label"]).lower()

    models.sort(key=priority)
    return {"provider": provider, "status": status, "message": message, "models": models,
            "recommended": models[0]["id"] if models else None}


def update_computer_model(query: dict[str, list[str]]) -> dict[str, Any]:
    model = (query_first(query, "model") or "").strip()
    catalog = computer_models_payload(query)
    candidate = next((row for row in catalog["models"] if row["id"] == model), None)
    if candidate is None:
        raise WebUISettingsError("Choose a vision model from the selected provider")
    # Reload after catalog I/O so another Settings change is not overwritten.
    config = load_config()
    provider = catalog["provider"]
    _validate_configured_provider(config, provider)
    key = candidate["preset"]
    if key and (key not in config.model_presets or config.model_presets[key].model != model
                or config.model_presets[key].provider != provider or not config.model_presets[key].enabled):
        key = ""
    if not key:
        if provider == "navin":
            raise WebUISettingsError("This model is no longer available in the Navin catalog")
        stem = _model_configuration_slug(f"computer-{provider}-{model}")
        key = stem
        suffix = 2
        while key in config.model_presets:
            key = f"{stem}-{suffix}"
            suffix += 1
        # A route-specific preset never changes the general chat model.
        base = config.resolve_default_preset()
        config.model_presets[key] = ModelPresetConfig(
            label=str(candidate["label"]), provider=provider, model=model,
            max_tokens=min(base.max_tokens, 8192),
            context_window_tokens=int(candidate.get("context_window") or base.context_window_tokens),
            input_modalities=["text", "image"],
        )
    else:
        config.model_presets[key].input_modalities = ["text", "image"]
    config.model_routes["computer"] = key
    save_config(config)
    return settings_payload()
