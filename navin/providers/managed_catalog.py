# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Sync the managed model catalog into presets and task routes.

The Navin site publishes its managed models at ``/api/models``: one slug per
model, a tier (light / executor / main / expert) and the default model, all
served by OpenRouter. Paid plans use ``byPlan[plan]``:

- Flash → free OpenRouter (Ultra / Super / Omni) + DeepSeek V4.1 Flash,
  MiniMax M3, GLM 4.7 Flash, Qwen3.7 Flash, GLM 5.3 Flash (default)
- Plus / Pro / Ultra / Team / Enterprise → catalogue complet (hors Flash-only),
  default GLM 5.3 Flash (same chat default as Flash)
- Vision route → Grok 4.6 when present (never a :free multimodal on paid;
  Flash falls to MiMo V2.5)
- Task routes on paid plans skip :free endpoints (Nemotron Ultra / Super / Omni)

This module reads that catalog at gateway startup / license activation and
turns it into the constructs the rest of the app already routes with:

- tier aliases (light / executor / main / expert) for task routes and usage
  clamps (hidden from the model picker);
- one selectable preset per catalog model under provider ``navin``;
- task-role routes pointing at tier aliases, filled only when the user has
  not routed the role themselves;
- ``agents.defaults.model``, filled while unset, or forced after a paid plan sync.

The sync is opt-in (``modelCatalog.enabled``) and never blocks startup: any
network or payload problem is logged and the previously synced presets keep
serving, since they were persisted into config.json by the last good sync.

Refresh policy (intentionally lean):
- once at gateway startup;
- forced when the chat picker / Settings → Models open;
- forced on license activate / plan change.
No periodic catalog loop and no catalog fetch on every license poll.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from navin.audio.models import NAVIN_STT_MODEL, NAVIN_TTS_MODEL
from navin.config.schema import Config, ModelPresetConfig

DEFAULT_CATALOG_URL = "https://navin.live/api/models"
_FETCH_TIMEOUT_S = 5.0

# Throttle when Settings reopen without sync_catalog=1 (avoid hammering).
UI_SYNC_MIN_INTERVAL_S = 30.0

_last_sync_attempt_mono = 0.0
_sync_lock = threading.Lock()

TIERS = ("light", "executor", "main", "expert")

# Multimodal free model for image / video analysis (vision task route).
FREE_VISION_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
# Default chat on every subscription (Flash and Plus / Pro / Ultra / Team /
# Enterprise): GLM 5.3 Flash.
FLASH_DEFAULT_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_MANAGED_MODEL = FLASH_DEFAULT_MODEL
# Grok 4.6 is Plus+ only (vision / Montage default), never a Flash picker row.
PLUS_VISION_MODEL = "x-ai/grok-4.6"
ASTRA_MODEL = "openai/gpt-6-astra"
DS_V41_FLASH = "deepseek/deepseek-v4.1-flash"
DS_V4_FLASH = "deepseek/deepseek-v4-flash"
FLASH_EXCLUDED_SLUGS = frozenset({PLUS_VISION_MODEL, "x-ai/grok-4.5", ASTRA_MODEL})
# Dropped from every Navin subscription catalog (Free / Flash / Plus+).
OX_ALPHA_MODEL = "stealth/ox-alpha"
SUBSCRIPTION_EXCLUDED_SLUGS = frozenset({OX_ALPHA_MODEL, DS_V4_FLASH})
# Montage / vision Plus+ stays on Grok 4.6. Flash falls to MiMo.
DEFAULT_VISION_MODEL = PLUS_VISION_MODEL
# Economy multimodal (native omnimodal, strong cost/perf).
ECONOMY_VISION_MODEL = "xiaomi/mimo-v2.5"

# Embedded catalog used when the public /api/models endpoint is unreachable
# (or not deployed yet). Mirrors site/src/lib/models.ts leaders + default.
FALLBACK_CATALOG_PAYLOAD: dict[str, object] = {
    "provider": "openrouter",
    "defaultModel": DEFAULT_MANAGED_MODEL,
    "models": [
        {
            "slug": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "name": "Nemotron 3 Ultra",
            "tier": "light",
        },
        {
            "slug": "nvidia/nemotron-3-super-120b-a12b:free",
            "name": "Nemotron 3 Super",
            "tier": "light",
        },
        {
            "slug": FREE_VISION_MODEL,
            "name": "Nemotron 3 Nano Omni",
            "tier": "light",
        },
        {
            "slug": ECONOMY_VISION_MODEL,
            "name": "MiMo V2.5",
            "tier": "light",
        },
        {
            "slug": "google/gemini-3.6-flash",
            "name": "Gemini 3.6 Flash",
            "tier": "main",
        },
        {
            "slug": "google/gemini-3.7-flash",
            "name": "Gemini 3.7 Flash",
            "tier": "main",
        },
        {
            "slug": DS_V41_FLASH,
            "name": "DeepSeek V4.1 Flash",
            "tier": "executor",
        },
        {
            "slug": "deepseek/deepseek-v4-pro",
            "name": "DeepSeek V4 Pro",
            "tier": "main",
        },
        {
            "slug": FLASH_DEFAULT_MODEL,
            "name": "GLM 5.3 Flash",
            "tier": "executor",
        },
        {"slug": "z-ai/glm-5.3", "name": "GLM 5.3", "tier": "main"},
        {"slug": "z-ai/glm-5.2", "name": "GLM 5.2", "tier": "main"},
        {"slug": "meta/muse-spark-1.3", "name": "Muse Spark 1.3", "tier": "main"},
        {
            "slug": PLUS_VISION_MODEL,
            "name": "Grok 4.6",
            "tier": "expert",
        },
        {"slug": "moonshotai/kimi-k3", "name": "Kimi K3", "tier": "expert"},
        {"slug": "qwen/qwen3.8-max", "name": "Qwen3.8 Max", "tier": "expert"},
        {
            "slug": "anthropic/claude-fable-5.1",
            "name": "Claude Fable 5.1",
            "tier": "expert",
        },
        {
            "slug": ASTRA_MODEL,
            "name": "GPT-6 Astra",
            "tier": "expert",
        },
    ],
    "byPlan": {
        "flash": {
            "defaultModel": FLASH_DEFAULT_MODEL,
            "models": [
                {
                    "slug": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "name": "Nemotron 3 Ultra",
                    "tier": "light",
                },
                {
                    "slug": "nvidia/nemotron-3-super-120b-a12b:free",
                    "name": "Nemotron 3 Super",
                    "tier": "light",
                },
                {
                    "slug": FREE_VISION_MODEL,
                    "name": "Nemotron 3 Nano Omni",
                    "tier": "light",
                },
                {
                    "slug": ECONOMY_VISION_MODEL,
                    "name": "MiMo V2.5",
                    "tier": "light",
                },
                {
                    "slug": "google/gemini-3.6-flash",
                    "name": "Gemini 3.6 Flash",
                    "tier": "main",
                },
                {
                    "slug": "qwen/qwen3.7-flash",
                    "name": "Qwen3.7 Flash",
                    "tier": "light",
                },
                {
                    "slug": "z-ai/glm-4.7-flash",
                    "name": "GLM 4.7 Flash",
                    "tier": "light",
                },
                {
                    "slug": FLASH_DEFAULT_MODEL,
                    "name": "GLM 5.3 Flash",
                    "tier": "executor",
                },
                {
                    "slug": DS_V41_FLASH,
                    "name": "DeepSeek V4.1 Flash",
                    "tier": "executor",
                },
                {
                    "slug": "minimax/minimax-m3",
                    "name": "MiniMax M3",
                    "tier": "main",
                },
            ],
        },
        "free": {
            "defaultModel": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "models": [
                {
                    "slug": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "name": "Nemotron 3 Ultra",
                    "tier": "light",
                },
                {
                    "slug": "nvidia/nemotron-3-super-120b-a12b:free",
                    "name": "Nemotron 3 Super",
                    "tier": "light",
                },
                {
                    "slug": FREE_VISION_MODEL,
                    "name": "Nemotron 3 Nano Omni",
                    "tier": "light",
                },
            ],
        },
    },
    "media": {
        "image": {
            "defaultModel": "google/gemini-3.1-flash-image",
            "models": [
                {
                    "slug": "google/gemini-3.1-flash-image",
                    "name": "Nano Banana 2 (Gemini 3.1 Flash Image)",
                    "unitPriceUsd": None,
                    "billingUnit": "image_output_token",
                    "priceNote": (
                        "Défaut qualité/prix. Texte: 0,50 $/M in, 3 $/M out "
                        "+ 60 $/M image-output tokens. Coût final via usage.cost."
                    ),
                },
                {
                    "slug": "google/gemini-3-pro-image",
                    "name": "Nano Banana Pro (Gemini 3 Pro Image)",
                    "unitPriceUsd": None,
                    "billingUnit": "image_output_token",
                    "priceNote": (
                        "Premium 2K/4K. Texte: 2 $/M in, 12 $/M out "
                        "+ 120 $/M image-output tokens. Coût final via usage.cost."
                    ),
                },
                {
                    "slug": "bytedance-seed/seedream-4.5",
                    "name": "Seedream 4.5",
                    "unitPriceUsd": 0.04,
                    "billingUnit": "image",
                    "billingRate": 0.04,
                    "estimatedGenerationCost": 0.04,
                    "defaultProfile": "1 image",
                    "priceNote": (
                        "Économique - 0,04 $ / image (taille incluse). "
                        "Débite le budget IA."
                    ),
                },
            ],
        },
        "video": {
            "defaultModel": "minimax/hailuo-3",
            "models": [
                {
                    "slug": "google/veo-3.1-fast",
                    "name": "Veo 3.1 Fast",
                    "unitPriceUsd": 0.80,
                    "billingUnit": "video_second",
                    "billingRate": 0.10,
                    "estimatedGenerationCost": 0.80,
                    "defaultProfile": "720p / 8s",
                    "priceNote": "From $0.10/s (720p). ~$0.80 for 8s.",
                },
                {
                    "slug": "google/veo-3.1",
                    "name": "Veo 3.1",
                    "unitPriceUsd": 3.20,
                    "billingUnit": "video_second",
                    "billingRate": 0.40,
                    "estimatedGenerationCost": 3.20,
                    "defaultProfile": "8s from $0.40/s",
                    "priceNote": "From $0.40/s. ~$3.20 for 8s.",
                },
                {
                    "slug": "bytedance/seedance-2.5",
                    "name": "Seedance 2.5",
                    "unitPriceUsd": 0.8224,
                    "billingUnit": "video_second",
                    "billingRate": 0.1028,
                    "estimatedGenerationCost": 0.8224,
                    "defaultProfile": "8s from $0.1028/s",
                    "priceNote": "From $0.1028/s. ~$0.82 for 8s.",
                },
                {
                    "slug": "kwaivgi/kling-v3.0-pro",
                    "name": "Kling v3.0 Pro",
                    "unitPriceUsd": 1.344,
                    "billingUnit": "video_second",
                    "billingRate": 0.168,
                    "estimatedGenerationCost": 1.344,
                    "defaultProfile": "8s from $0.168/s",
                    "priceNote": "From $0.168/s. ~$1.34 for 8s.",
                },
                {
                    "slug": "minimax/hailuo-3",
                    "name": "MiniMax H3",
                    "unitPriceUsd": 1.04,
                    "billingUnit": "video_second",
                    "billingRate": 0.13,
                    "estimatedGenerationCost": 1.04,
                    "defaultProfile": "8s from $0.13/s",
                    "priceNote": "Default video. From $0.13/s. ~$1.04 for 8s.",
                },
                {
                    "slug": "bytedance/seedance-2.0-fast",
                    "name": "Seedance 2.0 Fast",
                    "unitPriceUsd": 0.97,
                    "billingUnit": "video_token",
                    "billingRate": 0.0000056,
                    "estimatedGenerationCost": 0.96768,
                    "defaultProfile": "1280x720 / 8s",
                    "priceNote": "~$0.97 for 720p / 8s (video tokens).",
                },
            ],
        },
        "audio": {
            "defaultModel": NAVIN_TTS_MODEL,
            "models": [
                {
                    "slug": NAVIN_TTS_MODEL,
                    "name": "Qwen Audio 3.0 TTS Flash",
                    "unitPriceUsd": 15,
                    "billingUnit": "million_characters",
                    "billingRate": 15,
                    "priceNote": "Défaut TTS. Qwen. 15 $ / 1M caractères.",
                },
                {
                    "slug": "qwen/qwen-audio-3.0-tts-plus",
                    "name": "Qwen Audio 3.0 TTS Plus",
                    "unitPriceUsd": 20,
                    "billingUnit": "million_characters",
                    "billingRate": 20,
                    "priceNote": "Qwen. 20 $ / 1M caractères.",
                },
                {
                    "slug": "google/gemini-3.1-flash-tts-preview",
                    "name": "Gemini 3.1 Flash TTS",
                    "unitPriceUsd": None,
                    "billingUnit": "token",
                    "priceNote": (
                        "1 $/M input + 20 $/M output tokens. "
                        "Coût final via usage.cost."
                    ),
                },
                {
                    "slug": "fish-audio/s2.1-pro-free:free",
                    "name": "Fish Audio S2.1 Pro (free)",
                    "unitPriceUsd": 0,
                    "billingUnit": "free",
                    "billingRate": 0,
                    "estimatedGenerationCost": 0,
                    "priceNote": (
                        "Fallback gratuit - prototypage / faible volume, sans SLA."
                    ),
                },
                {
                    "slug": "x-ai/grok-voice-tts-1.0",
                    "name": "Grok Voice TTS 1.0",
                    "unitPriceUsd": 15,
                    "billingUnit": "million_characters",
                    "billingRate": 15,
                    "priceNote": (
                        "15 $ / 1M caractères (1000 car. = 0,015 $)."
                    ),
                },
                {
                    "slug": "fish-audio/s2.1-pro",
                    "name": "Fish Audio S2.1 Pro",
                    "unitPriceUsd": 15,
                    "billingUnit": "million_utf8_bytes",
                    "billingRate": 15,
                    "priceNote": "Fallback payant. 15 $ / 1M UTF-8 bytes.",
                },
            ],
        },
        "music": {
            "defaultModel": "google/lyria-3-clip-preview",
            "models": [
                {
                    "slug": "google/lyria-3-clip-preview",
                    "name": "Lyria 3 Clip Preview",
                    "unitPriceUsd": 0.04,
                    "billingUnit": "clip_30s",
                    "billingRate": 0.04,
                    "estimatedGenerationCost": 0.04,
                    "defaultProfile": "30s clip",
                    "priceNote": "Défaut musique. Clip / jingle 30 s. 0,04 $ / clip.",
                },
                {
                    "slug": "google/lyria-3-pro-preview",
                    "name": "Lyria 3 Pro Preview",
                    "unitPriceUsd": 0.08,
                    "billingUnit": "song",
                    "billingRate": 0.08,
                    "estimatedGenerationCost": 0.08,
                    "defaultProfile": "full song",
                    "priceNote": "Chanson complète sur demande. 0,08 $ / chanson.",
                },
            ],
        },
        "stt": {
            "defaultModel": NAVIN_STT_MODEL,
            "models": [
                {
                    "slug": "nvidia/parakeet-tdt-0.6b-v3",
                    "name": "Parakeet TDT 0.6B v3",
                    "unitPriceUsd": 0.0015,
                    "billingUnit": "minute",
                    "billingRate": 0.0015,
                    "estimatedGenerationCost": 0.0015,
                    "defaultProfile": "1 min",
                    "priceNote": "NVIDIA. 0,0015 $ / minute. UE + détection de langue.",
                },
                {
                    "slug": "qwen/qwen3-asr-flash-2026-02-10",
                    "name": "Qwen3 ASR Flash",
                    "unitPriceUsd": 0.0021,
                    "billingUnit": "second",
                    "billingRate": 0.000035,
                    "estimatedGenerationCost": 0.0021,
                    "defaultProfile": "1 min",
                    "priceNote": "Défaut STT. Qwen. 0,000035 $/s ≈ 0,0021 $ / minute. Multilingue.",
                },
                {
                    "slug": "openai/gpt-transcribe",
                    "name": "GPT Transcribe",
                    "unitPriceUsd": 0.0045,
                    "billingUnit": "minute",
                    "billingRate": 0.0045,
                    "estimatedGenerationCost": 0.0045,
                    "defaultProfile": "1 min",
                    "priceNote": "0,0045 $ / minute.",
                },
                {
                    "slug": "microsoft/mai-transcribe-1.5",
                    "name": "MAI-Transcribe 1.5",
                    "unitPriceUsd": 0.006,
                    "billingUnit": "hour",
                    "billingRate": 0.36,
                    "estimatedGenerationCost": 0.006,
                    "defaultProfile": "1 min",
                    "priceNote": "Microsoft. 0,36 $ / heure ≈ 0,006 $ / minute. 43 langues.",
                },
                {
                    "slug": "deepgram/nova-3",
                    "name": "Deepgram Nova-3",
                    "unitPriceUsd": 0.0043,
                    "billingUnit": "minute",
                    "billingRate": 0.0043,
                    "estimatedGenerationCost": 0.0043,
                    "defaultProfile": "1 min",
                    "priceNote": "Deepgram. à partir de 0,0043 $ / minute.",
                },
            ],
        },
    },
}

# Fallback tier when a role has no preferred paid slug in the catalog.
# Paid routes themselves use PAID_ROLE_DEFAULTS / FLASH_ROLE_DEFAULTS so a
# :free light leader (Nemotron) never becomes the everyday task model.
ROLE_TIERS: dict[str, str] = {
    "fast": "light",
    "code": "light",
    "search": "light",
    "docs": "executor",
    "dev": "main",
    "plan": "main",
    "deep": "expert",
    "review": "expert",
    "security": "expert",
}

# Flash n'a pas de tier expert : deep / review / security → main (MiniMax M3).
FLASH_ROLE_TIERS: dict[str, str] = {
    "fast": "light",
    "code": "light",
    "search": "light",
    "docs": "executor",
    "dev": "executor",
    "plan": "main",
    "deep": "main",
    "review": "main",
    "security": "main",
}

# First match present in the catalog wins. Free / stealth slugs are skipped.
PAID_ROLE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "deep": ("qwen/qwen3.8-max", "moonshotai/kimi-k3", "x-ai/grok-4.6"),
    "dev": ("z-ai/glm-5.3-flash", "z-ai/glm-5.2", DS_V41_FLASH),
    "fast": (DS_V41_FLASH, "z-ai/glm-5.3-flash"),
    "code": (DS_V41_FLASH, "z-ai/glm-5.3-flash"),
    "search": (DS_V41_FLASH, "z-ai/glm-5.3-flash"),
    "plan": (
        "z-ai/glm-5.3-flash",
        "google/gemini-3.7-flash",
        "google/gemini-3.6-flash",
    ),
    "docs": (DS_V41_FLASH, "z-ai/glm-5.3-flash"),
    "review": ("z-ai/glm-5.3-flash", "z-ai/glm-5.3", "z-ai/glm-5.2"),
    "security": ("z-ai/glm-5.3-flash", "z-ai/glm-5.3", "x-ai/grok-4.6"),
}

FLASH_ROLE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "deep": ("minimax/minimax-m3", "z-ai/glm-5.3-flash"),
    "dev": ("z-ai/glm-5.3-flash", DS_V41_FLASH),
    "fast": (
        DS_V41_FLASH,
        "qwen/qwen3.7-flash",
        "z-ai/glm-4.7-flash",
    ),
    "code": (
        DS_V41_FLASH,
        "qwen/qwen3.7-flash",
        "z-ai/glm-4.7-flash",
    ),
    "search": (
        DS_V41_FLASH,
        "qwen/qwen3.7-flash",
        "z-ai/glm-4.7-flash",
    ),
    "plan": ("z-ai/glm-5.3-flash", "minimax/minimax-m3"),
    "docs": (DS_V41_FLASH, "z-ai/glm-5.3-flash"),
    "review": ("minimax/minimax-m3", "z-ai/glm-5.3-flash"),
    "security": ("minimax/minimax-m3", "z-ai/glm-5.3-flash"),
}


def is_free_model_slug(slug: str) -> bool:
    """True for OpenRouter :free endpoints and leftover stealth/ox-alpha."""
    cleaned = (slug or "").strip().lower()
    if not cleaned:
        return False
    if cleaned.endswith(":free"):
        return True
    return cleaned == OX_ALPHA_MODEL


@dataclass(frozen=True)
class CatalogModel:
    slug: str
    tier: str
    name: str = ""
    price_unconfirmed: bool = False


@dataclass(frozen=True)
class MediaCatalogModel:
    slug: str
    name: str = ""
    unit_price_usd: float | None = None
    price_note: str = ""
    billing_unit: str = ""
    billing_rate: float | None = None
    estimated_generation_cost: float | None = None
    default_profile: str = ""


@dataclass(frozen=True)
class MediaCatalogBucket:
    default_model: str = ""
    models: tuple[MediaCatalogModel, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Catalog:
    provider: str = "openrouter"
    default_model: str = ""
    models: tuple[CatalogModel, ...] = field(default_factory=tuple)
    role_tiers: dict[str, str] = field(default_factory=lambda: dict(ROLE_TIERS))
    media: dict[str, MediaCatalogBucket] = field(default_factory=dict)

    def tier_leader(self, tier: str) -> CatalogModel | None:
        """First model of the tier, preferring paid + confirmed prices.

        Free OpenRouter endpoints share one account-wide rate limit and block
        paid subscribers. They must not lead a tier while a paid sibling exists.
        Catalog order is the ranking among remaining models; an unconfirmed
        price must not become a daily driver while a confirmed sibling exists.
        """
        in_tier = [model for model in self.models if model.tier == tier]
        paid = [model for model in in_tier if not is_free_model_slug(model.slug)]
        pool = paid or in_tier
        for model in pool:
            if not model.price_unconfirmed:
                return model
        return pool[0] if pool else None


def _parse_models(raw_models: object) -> list[CatalogModel]:
    if not isinstance(raw_models, list) or not raw_models:
        return []
    models: list[CatalogModel] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        slug = raw.get("slug")
        tier = raw.get("tier")
        if not isinstance(slug, str) or not slug or tier not in TIERS:
            continue
        models.append(
            CatalogModel(
                slug=slug,
                tier=tier,
                name=str(raw.get("name") or ""),
                price_unconfirmed=bool(raw.get("priceUnconfirmed")),
            )
        )
    return models


def _parse_media(payload: object) -> dict[str, MediaCatalogBucket]:
    out: dict[str, MediaCatalogBucket] = {}
    if not isinstance(payload, dict):
        return out
    raw_media = payload.get("media")
    if not isinstance(raw_media, dict):
        return out
    def _opt_float(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    for modality in ("image", "video", "audio", "music", "stt"):
        bucket = raw_media.get(modality)
        if not isinstance(bucket, dict):
            continue
        models: list[MediaCatalogModel] = []
        raw_models = bucket.get("models")
        if isinstance(raw_models, list):
            for raw in raw_models:
                if not isinstance(raw, dict):
                    continue
                slug = raw.get("slug")
                if not isinstance(slug, str) or not slug.strip():
                    continue
                unit = raw.get("unitPriceUsd", raw.get("unit_price_usd"))
                estimated = raw.get(
                    "estimatedGenerationCost",
                    raw.get("estimated_generation_cost"),
                )
                unit_f = _opt_float(unit)
                estimated_f = _opt_float(estimated)
                # unit_price_usd reste l'estimation profil défaut (débit budget).
                if unit_f is None and estimated_f is not None:
                    unit_f = estimated_f
                models.append(
                    MediaCatalogModel(
                        slug=slug.strip(),
                        name=str(raw.get("name") or ""),
                        unit_price_usd=unit_f,
                        price_note=str(raw.get("priceNote") or raw.get("price_note") or ""),
                        billing_unit=str(
                            raw.get("billingUnit") or raw.get("billing_unit") or ""
                        ),
                        billing_rate=_opt_float(
                            raw.get("billingRate", raw.get("billing_rate"))
                        ),
                        estimated_generation_cost=estimated_f,
                        default_profile=str(
                            raw.get("defaultProfile") or raw.get("default_profile") or ""
                        ),
                    )
                )
        default = bucket.get("defaultModel") or bucket.get("default_model") or ""
        if not isinstance(default, str):
            default = ""
        if not default and models:
            default = models[0].slug
        out[modality] = MediaCatalogBucket(
            default_model=default.strip(),
            models=tuple(models),
        )
    return out


def _fallback_media_slugs() -> set[str]:
    """Media slugs from the embedded catalog (image / video / TTS / music / STT)."""
    slugs: set[str] = set()
    media = FALLBACK_CATALOG_PAYLOAD.get("media")
    if isinstance(media, dict):
        for bucket in media.values():
            if not isinstance(bucket, dict):
                continue
            rows = bucket.get("models")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    slug = row.get("slug")
                    if isinstance(slug, str) and slug.strip():
                        slugs.add(slug.strip())
    return slugs


def known_media_slugs(catalog: Catalog | None = None) -> set[str]:
    """Every slug known to be a media model (payload media + embedded list).

    A degraded /api/models payload (old site code + new DB rows) can list
    media models inside ``models`` with a text tier and even as
    ``defaultModel``. The union with the embedded list means the gateway can
    still recognize them and refuse to run chat turns on a music model.
    """
    slugs = _fallback_media_slugs()
    if catalog is not None:
        for bucket in catalog.media.values():
            slugs.update(m.slug for m in bucket.models if m.slug)
    return slugs


# Offline heuristics for stale configs where the modality field was lost.
_MEDIA_SLUG_TOKENS = (
    "lyria",
    "veo-",
    "seedream",
    "seedance",
    "kling",
    "hailuo",
    "flash-image",
    "pro-image",
    "flash-tts",
    "grok-voice",
    "grok-stt",
    "transcribe",
    "parakeet",
    "nova-3",
    "asr-",
)


def is_media_model_slug(slug: str) -> bool:
    """True when *slug* is a media specialty model (image/video/TTS/music/STT).

    Media models must never run chat/agent turns in any mode: they have no
    tool endpoints and produce media, not answers. Used as the last line of
    defense wherever a chat runtime is selected.
    """
    cleaned = (slug or "").strip()
    if not cleaned:
        return False
    if cleaned in known_media_slugs():
        return True
    from navin.providers.media_models import media_model_kinds

    if media_model_kinds(cleaned):
        return True
    leaf = cleaned.rsplit("/", 1)[-1].lower()
    return any(token in leaf for token in _MEDIA_SLUG_TOKENS)


def _shape_flash_text_models(
    models: list[CatalogModel],
    default_model: str,
) -> tuple[list[CatalogModel], str]:
    """Flash subscribers never see Grok; GLM 5.3 Flash is always in the list.

    A stale /api/models payload (root catalog, or ``in_flash`` still true on
    Grok 4.6) used to leak Plus-only rows into the Flash picker. Strip those
    slugs and inject the Flash default when the site forgot it.
    """
    kept = [m for m in models if m.slug not in FLASH_EXCLUDED_SLUGS]
    if not any(m.slug == FLASH_DEFAULT_MODEL for m in kept):
        kept.append(
            CatalogModel(
                slug=FLASH_DEFAULT_MODEL,
                tier="executor",
                name="GLM 5.3 Flash",
            )
        )
    if not default_model or default_model in FLASH_EXCLUDED_SLUGS:
        default_model = FLASH_DEFAULT_MODEL
    elif default_model not in {m.slug for m in kept}:
        default_model = FLASH_DEFAULT_MODEL
    return kept, default_model


def parse_catalog(payload: object, *, plan: str | None = None) -> Catalog:
    """Validate the /api/models payload; raise ValueError when unusable.

    When *plan* is set and ``byPlan[plan]`` exists, that subset is used
    (Flash = 5 modèles, autres = catalogue complet).
    """
    if not isinstance(payload, dict):
        raise ValueError("catalog payload is not an object")

    plan_key = (plan or "").strip().lower()
    by_plan = payload.get("byPlan")
    selected: dict[str, object] | None = None
    if (
        plan_key
        and plan_key != "free"
        and isinstance(by_plan, dict)
        and isinstance(by_plan.get(plan_key), dict)
    ):
        selected = by_plan[plan_key]  # type: ignore[assignment]

    source = selected if selected is not None else payload
    models = _parse_models(source.get("models") if isinstance(source, dict) else None)
    if not models and selected is not None:
        # byPlan vide / invalide → retombe sur le catalogue racine.
        models = _parse_models(payload.get("models"))
    if not models:
        raise ValueError("catalog payload has no usable models")

    provider = payload.get("provider")
    default_raw = (
        source.get("defaultModel")
        if isinstance(source, dict) and "defaultModel" in source
        else payload.get("defaultModel")
    )
    default_model = default_raw if isinstance(default_raw, str) else ""

    role_tiers = dict(FLASH_ROLE_TIERS if plan_key == "flash" else ROLE_TIERS)
    media = _parse_media(payload)

    # A stale site deployment can leak media rows into the chat list (they
    # carry text tiers in the DB) and even pick one as defaultModel. Chat
    # presets and the default must stay text-only, whatever the payload says.
    # The slug heuristic also runs: a degraded payload once mapped the
    # 'executor' tier to an STT model absent from every known-media list.
    media_slugs = _fallback_media_slugs()
    for bucket in media.values():
        media_slugs.update(m.slug for m in bucket.models if m.slug)
    text_models = [
        m
        for m in models
        if m.slug not in media_slugs and not is_media_model_slug(m.slug)
    ]
    if not text_models:
        raise ValueError("catalog payload has no usable text models")
    if plan_key == "flash":
        text_models, default_model = _shape_flash_text_models(text_models, default_model)
    text_models = [m for m in text_models if m.slug not in SUBSCRIPTION_EXCLUDED_SLUGS]
    if not text_models:
        raise ValueError("catalog payload has no usable text models")
    if default_model in SUBSCRIPTION_EXCLUDED_SLUGS:
        default_model = text_models[0].slug
    if default_model in media_slugs or is_media_model_slug(default_model):
        preferred = (
            (FLASH_DEFAULT_MODEL, DS_V41_FLASH)
            if plan_key == "flash"
            else (DEFAULT_MANAGED_MODEL, PLUS_VISION_MODEL, DS_V41_FLASH)
        )
        default_model = next(
            (m.slug for m in text_models if m.slug in preferred),
            text_models[0].slug,
        )

    return Catalog(
        provider=provider if isinstance(provider, str) and provider else "openrouter",
        default_model=default_model,
        models=tuple(text_models),
        role_tiers=role_tiers,
        media=media,
    )


def pick_role_default_slug(catalog: Catalog, role: str) -> str:
    """Best paid slug for a task role, or empty when the catalog has none."""
    flash = catalog.role_tiers.get("deep") != "expert"
    table = FLASH_ROLE_DEFAULTS if flash else PAID_ROLE_DEFAULTS
    known = {model.slug for model in catalog.models}
    has_paid = any(not is_free_model_slug(model.slug) for model in catalog.models)
    for slug in table.get(role, ()):
        if slug in known and not is_free_model_slug(slug):
            return slug
    tier = catalog.role_tiers.get(role)
    if tier:
        leader = catalog.tier_leader(tier)
        if leader and (not is_free_model_slug(leader.slug) or not has_paid):
            return leader.slug
    if has_paid:
        for fallback_tier in ("executor", "main", "expert", "light"):
            leader = catalog.tier_leader(fallback_tier)
            if leader and not is_free_model_slug(leader.slug):
                return leader.slug
    return ""


def slug_preset_key(slug: str) -> str:
    """Stable preset name for a managed model slug (chat / Settings list)."""
    tail = (slug or "").strip().split("/")[-1]
    key = re.sub(r"[^a-zA-Z0-9]+", "-", tail).strip("-").lower()
    if not key or key in TIERS:
        key = re.sub(r"[^a-zA-Z0-9]+", "-", (slug or "").strip()).strip("-").lower()
    return (key or "model")[:64]


def _known_managed_slugs() -> set[str]:
    """Slugs from the embedded fallback (root + byPlan) for legacy cleanup."""
    slugs: set[str] = set()

    def _collect(models: object) -> None:
        if not isinstance(models, list):
            return
        for row in models:
            if isinstance(row, dict):
                slug = row.get("slug")
                if isinstance(slug, str) and slug.strip():
                    slugs.add(slug.strip())

    _collect(FALLBACK_CATALOG_PAYLOAD.get("models"))
    by_plan = FALLBACK_CATALOG_PAYLOAD.get("byPlan")
    if isinstance(by_plan, dict):
        for plan_data in by_plan.values():
            if isinstance(plan_data, dict):
                _collect(plan_data.get("models"))
    slugs.update(SUBSCRIPTION_EXCLUDED_SLUGS)
    return slugs


def catalog_presets(catalog: Catalog) -> dict[str, ModelPresetConfig]:
    """Tier aliases (routing) + selectable presets (chat + média)."""
    presets: dict[str, ModelPresetConfig] = {}
    for tier in TIERS:
        leader = catalog.tier_leader(tier)
        if leader is None:
            continue
        presets[tier] = ModelPresetConfig(
            label=leader.name or leader.slug,
            model=leader.slug,
            provider="navin",
            modality="text",
        )
    for model in catalog.models:
        key = slug_preset_key(model.slug)
        if key in presets and presets[key].model != model.slug:
            key = slug_preset_key(model.slug.replace("/", "-"))
        if key in TIERS:
            continue
        presets[key] = ModelPresetConfig(
            label=model.name or model.slug,
            model=model.slug,
            provider="navin",
            modality="text",
        )
    for modality, bucket in catalog.media.items():
        if modality not in {"image", "video", "audio", "music", "stt"}:
            continue
        for model in bucket.models:
            key = slug_preset_key(model.slug)
            if key in TIERS:
                key = f"{modality}-{key}"
            if key in presets and presets[key].modality == "text":
                key = f"{modality}-{key}"
            presets[key] = ModelPresetConfig(
                label=model.name or model.slug,
                model=model.slug,
                provider="navin",
                modality=modality,  # type: ignore[arg-type]
                unit_price_usd=model.unit_price_usd,
                price_note=model.price_note or None,
                billing_unit=model.billing_unit or None,
                billing_rate=model.billing_rate,
                estimated_generation_cost=model.estimated_generation_cost,
                default_profile=model.default_profile or None,
            )
    return presets


def apply_catalog(
    config: Config,
    catalog: Catalog,
    *,
    force_managed: bool = False,
    steer_tools: bool = False,
) -> bool:
    """Materialize the catalog into *config*; return True when it changed.

    *force_managed*: after a paid activate/validate, rewrite the default model
    and catalog-owned role routes so Flash ↔ Plus switches take effect.

    *steer_tools*: on activate / new key / plan change, steer generated media
    onto Navin catalog defaults. Saved speech choices are always preserved.
    """
    changed = False
    presets = catalog_presets(catalog)
    catalog_slugs = {model.slug for model in catalog.models}
    for bucket in catalog.media.values():
        catalog_slugs.update(m.slug for m in bucket.models)

    for name, preset in presets.items():
        previous = config.model_presets.get(name)
        if previous is not None and getattr(previous, "user_edited", False):
            continue
        if previous is not None and previous.enabled is False:
            preset.enabled = False
        if config.model_presets.get(name) != preset:
            config.model_presets[name] = preset
            changed = True

    # Drop obsolete managed presets and legacy OpenRouter catalog copies.
    known_managed_slugs = _known_managed_slugs() | catalog_slugs
    for name, existing in list(config.model_presets.items()):
        if name in presets:
            continue
        drop = False
        modality = getattr(existing, "modality", "text") or "text"
        if existing.provider == "navin" or name in TIERS:
            drop = not getattr(existing, "user_edited", False) or name in TIERS
        elif force_managed and existing.provider == "openrouter" and modality == "text":
            if name in TIERS or (
                existing.model in known_managed_slugs
                and name == slug_preset_key(existing.model)
            ):
                drop = True
        if drop:
            del config.model_presets[name]
            changed = True

    for role in catalog.role_tiers:
        slug = pick_role_default_slug(catalog, role)
        if not slug:
            continue
        key = slug_preset_key(slug)
        if key not in config.model_presets:
            continue
        current = config.model_routes.get(role)
        current_slug = ""
        if current:
            preset = config.model_presets.get(current)
            current_slug = ((preset.model if preset is not None else current) or "")
        # Keep an explicit paid pin (composer / Settings). Rewrite empty
        # routes, tier aliases, and anything still on a :free endpoint.
        if (
            current
            and current not in TIERS
            and current != key
            and not is_free_model_slug(current_slug)
        ):
            continue
        if current != key:
            config.model_routes[role] = key
            changed = True

    # Vision / multimodal analysis, in order: Grok 4.6 (Plus+), then MiMo V2.5
    # for Flash plans, and only then the free Nemotron Omni. A paid multimodal
    # always wins because the free endpoints share one account-wide rate limit
    # across every subscriber. Does not steal an explicit user pin.
    catalog_slugs = {model.slug for model in catalog.models}
    vision_slug = next(
        (
            slug
            for slug in (DEFAULT_VISION_MODEL, ECONOMY_VISION_MODEL, FREE_VISION_MODEL)
            if slug in catalog_slugs
        ),
        "",
    )
    if vision_slug:
        vision_key = slug_preset_key(vision_slug)
        if vision_key in config.model_presets:
            current_vision = config.model_routes.get("vision")
            if current_vision is None or (
                force_managed and current_vision in TIERS
            ):
                if current_vision != vision_key:
                    config.model_routes["vision"] = vision_key
                    changed = True

    # Computer needs a visual model even when the chat default is text-only.
    # Seed the route once and keep later choices made in Computer settings.
    if not config.model_routes.get("computer"):
        from navin.providers.model_capabilities import supports_vision

        computer_slug = next((slug for slug in (
            "qwen/qwen3.8-max", "qwen/qwen3.8-flash", DEFAULT_VISION_MODEL,
            ECONOMY_VISION_MODEL, FREE_VISION_MODEL,
        ) if slug in catalog_slugs and supports_vision(slug)), "")
        if computer_slug:
            computer_key = slug_preset_key(computer_slug)
            if computer_key in config.model_presets and config.model_presets[computer_key].enabled:
                config.model_routes["computer"] = computer_key
                changed = True

    if force_managed:
        for role, target in list(config.model_routes.items()):
            if target in TIERS and target not in config.model_presets:
                del config.model_routes[role]
                changed = True

    should_set_default = bool(catalog.default_model) and (
        force_managed or not config.agents.defaults.model
    )
    # A default the user explicitly picked (composer picker or Settings) is
    # not the catalog's to rewrite: without this, every routine sync on a paid
    # plan snapped the chat model back to the managed default, undoing the
    # user's choice as soon as the picker reopened or a new chat started. The
    # pin is honoured only while it still points at a usable chat preset; a
    # media or vanished preset falls through to the managed default (and the
    # media heal below stays authoritative either way).
    if should_set_default and _user_pin_holds(config):
        should_set_default = False
    if should_set_default and catalog.default_model:
        managed_provider = "navin"
        if (
            config.agents.defaults.model != catalog.default_model
            or config.agents.defaults.provider != managed_provider
        ):
            config.agents.defaults.model = catalog.default_model
            config.agents.defaults.provider = managed_provider
            changed = True
        selectable = slug_preset_key(catalog.default_model)
        preferred_preset = (
            selectable
            if selectable in config.model_presets
            else next(
                (
                    tier
                    for tier in TIERS
                    if tier in config.model_presets
                    and config.model_presets[tier].model == catalog.default_model
                ),
                "",
            )
        )
        if preferred_preset:
            pref = config.model_presets.get(preferred_preset)
            if pref is not None and (getattr(pref, "modality", "text") or "text") != "text":
                preferred_preset = ""
        if force_managed or not config.agents.defaults.model_preset:
            if preferred_preset and config.agents.defaults.model_preset != preferred_preset:
                config.agents.defaults.model_preset = preferred_preset
                # Reaching here means no valid user pin held (media preset,
                # vanished preset, or never pinned): the flag must not keep
                # protecting a default the catalog just had to replace.
                if config.agents.defaults.model_preset_user_pinned:
                    config.agents.defaults.model_preset_user_pinned = False
                changed = True
            elif (
                force_managed
                and not preferred_preset
                and config.agents.defaults.model_preset in TIERS
            ):
                config.agents.defaults.model_preset = ""
                changed = True

    if _heal_media_chat_default(config, catalog):
        changed = True

    # Image / vidéo / TTS : Navin par défaut à l'abonnement.
    # Free = BYOK libre (on ne touche pas). Abonné qui a choisi un autre
    # provider = on respecte, sauf steer_tools (activate / plan change).
    if force_managed:
        image_bucket = catalog.media.get("image")
        image_slug = (
            (image_bucket.default_model if image_bucket else "")
            or "google/gemini-3.1-flash-image"
        )
        video_bucket = catalog.media.get("video")
        video_slug = (
            (video_bucket.default_model if video_bucket else "")
            or "minimax/hailuo-3"
        )

        def _should_steer(provider: str | None) -> bool:
            name = (provider or "").strip().lower()
            return steer_tools or name in {"", "navin", "auto"}

        img = config.tools.image_generation
        if _should_steer(img.provider) and (
            img.provider != "navin" or img.model != image_slug
        ):
            img.provider = "navin"
            img.model = image_slug
            changed = True
        # Seedream 4.5+ rejects 1K; keep managed defaults at 2K+.
        if (
            _should_steer(img.provider)
            and "seedream-4.5" in (img.model or "").lower()
            and (img.default_image_size or "").upper() in {"", "1K", "512"}
        ):
            img.default_image_size = "2K"
            changed = True
        vid = config.tools.video_generation
        if _should_steer(vid.provider) and (
            vid.provider != "navin" or vid.model != video_slug
        ):
            vid.provider = "navin"
            vid.model = video_slug
            changed = True
        if _seed_managed_voice_defaults(config):
            changed = True

        music_bucket = catalog.media.get("music")
        music_slug = (
            (music_bucket.default_model if music_bucket else "")
            or "google/lyria-3-clip-preview"
        )
        music = config.tools.music_generation
        if _should_steer(music.provider) and (
            music.provider != "navin" or music.model != music_slug
        ):
            music.provider = "navin"
            music.model = music_slug
            changed = True

    return changed


def _user_pin_holds(config: Config) -> bool:
    """True while the user's explicit chat-default choice is still usable.

    Usable means: the pinned preset still exists and is a text (chat) model.
    Anything else - media modality, media slug, vanished preset - releases the
    pin so the managed default can take over again.
    """
    defaults = config.agents.defaults
    if not getattr(defaults, "model_preset_user_pinned", False):
        return False
    name = (defaults.model_preset or "").strip()
    if not name or name not in config.model_presets:
        return False
    preset = config.model_presets[name]
    if (getattr(preset, "modality", "text") or "text") != "text":
        return False
    model = (preset.model or "").strip()
    return bool(model) and not is_media_model_slug(model)


def _heal_media_chat_default(config: Config, catalog: Catalog) -> bool:
    """Never keep Lyria/Veo/etc. as the chat default; prefer the catalog default."""
    changed = False
    # Union with the embedded list: a degraded payload without a media block
    # (old site deployment) must not blind this heal.
    media_slugs = known_media_slugs(catalog)
    # Repair stale presets that lost modality (defaulted to text).
    for preset in config.model_presets.values():
        model = (preset.model or "").strip()
        if not model or model not in media_slugs:
            continue
        for modality, bucket in catalog.media.items():
            if any(m.slug == model for m in bucket.models):
                if (getattr(preset, "modality", "text") or "text") != modality:
                    preset.modality = modality  # type: ignore[assignment]
                    changed = True
                break

    current_default = (config.agents.defaults.model_preset or "").strip()
    current_model = (config.agents.defaults.model or "").strip()
    bad_preset = False
    if current_default and current_default in config.model_presets:
        current_modality = (
            getattr(config.model_presets[current_default], "modality", "text") or "text"
        )
        bad_preset = current_modality in {"image", "video", "audio", "music", "stt"}
        if not bad_preset:
            pinned_model = (config.model_presets[current_default].model or "").strip()
            bad_preset = pinned_model in media_slugs
    bad_model = bool(current_model) and current_model in media_slugs
    if not (bad_preset or bad_model):
        return changed

    # Chat default follows the catalog (GLM 5.3 Flash on every plan).
    fallback = ""
    if catalog.default_model:
        selectable = slug_preset_key(catalog.default_model)
        if (
            selectable in config.model_presets
            and (
                getattr(config.model_presets[selectable], "modality", "text") or "text"
            )
            == "text"
        ):
            fallback = selectable
    if not fallback:
        fallback = next(
            (
                name
                for name, preset in config.model_presets.items()
                if (getattr(preset, "modality", "text") or "text") == "text"
                and name in TIERS
                and (preset.model or "") not in media_slugs
            ),
            "",
        )
    if not fallback:
        fallback = next(
            (
                name
                for name, preset in config.model_presets.items()
                if (getattr(preset, "modality", "text") or "text") == "text"
                and preset.model
                and preset.model not in media_slugs
            ),
            "",
        )
    if fallback:
        fb = config.model_presets[fallback]
        if config.agents.defaults.model_preset != fallback:
            config.agents.defaults.model_preset = fallback
            # The heal overrode whatever was pinned: a media default is never
            # a valid user choice to preserve.
            config.agents.defaults.model_preset_user_pinned = False
            changed = True
        if fb.model and config.agents.defaults.model != fb.model:
            config.agents.defaults.model = fb.model
            changed = True
        if config.agents.defaults.provider != "navin":
            config.agents.defaults.provider = "navin"
            changed = True
    elif bad_preset:
        config.agents.defaults.model_preset = ""
        config.agents.defaults.model_preset_user_pinned = False
        changed = True
        if bad_model:
            config.agents.defaults.model = catalog.default_model or ""
            changed = True
    return changed


def heal_media_chat_default(config: Config) -> bool:
    """Offline heal: media models must never remain the chat default."""
    try:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
    except Exception:
        return False
    # Ensure Grok 4.6 exists so Montage can pin it even if catalog sync lagged.
    vision_key = slug_preset_key(DEFAULT_VISION_MODEL)
    if vision_key not in config.model_presets:
        config.model_presets[vision_key] = ModelPresetConfig(
            label="Grok 4.6",
            model=DEFAULT_VISION_MODEL,
            provider="navin",
            modality="text",
        )
    return _heal_media_chat_default(config, catalog)


def fallback_stt_managed_models() -> list[dict[str, Any]]:
    """Curated OpenRouter STT rows for Settings → Voice (offline fallback)."""
    rows: list[dict[str, Any]] = []
    try:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        bucket = catalog.media.get("stt")
        default_slug = (bucket.default_model if bucket else "") or NAVIN_STT_MODEL
        for model in bucket.models if bucket else []:
            if not model.slug:
                continue
            rows.append(
                {
                    "slug": model.slug,
                    "name": model.name or model.slug,
                    "unitPriceUsd": model.unit_price_usd,
                    "priceNote": model.price_note or None,
                    "isDefault": model.slug == default_slug,
                }
            )
    except Exception:
        rows = [
            {
                "slug": NAVIN_STT_MODEL,
                "name": "Qwen3 ASR Flash",
                "unitPriceUsd": 0.0021,
                "priceNote": "Défaut STT. Qwen. 0,0021 $ / minute.",
                "isDefault": True,
            }
        ]
    return rows


def heal_managed_voice_settings(config: Config) -> bool:
    """Prepare subscriber speech offline while preserving saved choices."""
    from navin.optional_live import live_modules_available

    if not live_modules_available():
        return False
    plan = (getattr(config.license, "plan", None) or "").strip().lower()
    from navin.config.secrets import unlocked_secret

    managed_key = unlocked_secret(getattr(config.license, "managed_api_key", None)) or unlocked_secret(
        getattr(config.providers.navin, "api_key", None)
    )
    if not managed_key or plan in {"", "free"}:
        return False

    return _seed_managed_voice_defaults(config)


def _seed_managed_voice_defaults(config: Config) -> bool:
    """Seed missing choices; an incomplete/offline catalog never invalidates a pick."""
    changed = False
    transcription = config.transcription
    stt_provider = (transcription.provider or "").strip().lower()
    stt_cfg = getattr(config.providers, stt_provider, None) if stt_provider else None
    stt_has_key = bool(getattr(stt_cfg, "api_key", None))
    legacy_stt = not transcription.selection_explicit and (
        stt_provider in {"", "auto"} or (stt_provider == "groq" and not stt_has_key)
    )
    model_raw = (transcription.model or "").strip()
    wrong_navin_model = stt_provider == "navin" and (
        not model_raw or (
            not transcription.selection_explicit and model_raw in {"whisper-large-v3", "whisper-1"}
        )
    )
    if legacy_stt or wrong_navin_model:
        if transcription.provider != "navin" or transcription.model != NAVIN_STT_MODEL:
            transcription.provider = "navin"
            transcription.model = NAVIN_STT_MODEL
            changed = True
        if legacy_stt and not transcription.enabled:
            transcription.enabled = True
            changed = True

    voice = config.voice
    tts_provider = (voice.tts_provider or "").strip().lower()
    tts_cfg = getattr(config.providers, tts_provider, None) if tts_provider else None
    tts_has_key = bool(getattr(tts_cfg, "api_key", None))
    legacy_tts = not voice.selection_explicit and (
        tts_provider in {"", "auto"} or (tts_provider == "openai" and not tts_has_key)
    )
    if legacy_tts or (
        tts_provider == "navin"
        and (not voice.tts_model or (
            not voice.selection_explicit and voice.tts_model in {"tts-1", "tts-1-hd"}
        ))
    ):
        if voice.tts_provider != "navin" or voice.tts_model != NAVIN_TTS_MODEL:
            voice.tts_provider = "navin"
            voice.tts_model = NAVIN_TTS_MODEL
            changed = True
        if (voice.voice or "").strip().lower() in {"", "alloy"}:
            voice.voice = "auto"
            changed = True

    return changed


def heal_managed_media_settings(config: Config) -> bool:
    """Point unset image / video / music providers at Navin for a paid plan.

    Media providers start unset so nobody without a subscription is shown Navin
    as their setup. Subscribers must still find their managed provider selected
    even when the catalog sync is disabled or unreachable, so an empty pick is
    filled in here. An explicit BYOK choice is never touched.
    """
    from navin.config.secrets import unlocked_secret
    from navin.optional_live import live_modules_available

    if not live_modules_available():
        return False

    plan = (getattr(config.license, "plan", None) or "").strip().lower()
    managed_key = unlocked_secret(getattr(config.license, "managed_api_key", None))
    if not managed_key or plan in {"", "free"}:
        return False

    changed = False
    for tool_config in (
        config.tools.image_generation,
        config.tools.video_generation,
        config.tools.music_generation,
    ):
        if not (tool_config.provider or "").strip():
            tool_config.provider = "navin"
            changed = True
    return changed


def enable_managed_catalog(config: Config) -> bool:
    """Turn catalog sync on for managed subscribers; return True if flipped."""
    if config.model_catalog.enabled:
        return False
    config.model_catalog.enabled = True
    return True


def catalog_url(config: Config) -> str:
    return (
        config.model_catalog.url.strip()
        or os.environ.get("NAVIN_MODEL_CATALOG_URL", "").strip()
        or DEFAULT_CATALOG_URL
    )


def _mark_sync_attempt() -> None:
    global _last_sync_attempt_mono
    _last_sync_attempt_mono = time.monotonic()


def seconds_since_last_sync_attempt() -> float:
    if _last_sync_attempt_mono <= 0:
        return float("inf")
    return time.monotonic() - _last_sync_attempt_mono


def sync_managed_catalog(
    config: Config,
    *,
    force: bool = False,
    min_interval_s: float | None = None,
    steer_tools: bool = False,
) -> bool:
    """Fetch and apply the catalog; persist when something changed.

    Best-effort by design: a gateway must boot on a plane. When the remote
    catalog is unreachable (or not deployed yet), the embedded fallback is
    applied so paid activations still get DeepSeek V4.1 Flash + task routes.
    Returns True only when the catalog was applied and saved.

    *min_interval_s*: skip the network fetch when a recent attempt already ran
    (Settings / picker reopen). *force* bypasses the throttle.
    *steer_tools*: force image / video / TTS onto Navin defaults (activate /
    plan change). Routine syncs preserve an explicit BYOK provider choice.
    """
    if not config.model_catalog.enabled:
        return False
    from navin.optional_live import live_modules_available

    if not live_modules_available():
        return False

    interval = 0.0 if min_interval_s is None else max(0.0, min_interval_s)
    with _sync_lock:
        if not force and interval > 0 and seconds_since_last_sync_attempt() < interval:
            return False
        _mark_sync_attempt()

        plan = (getattr(config.license, "plan", None) or "").strip().lower()
        force_managed = bool(plan and plan != "free")

        url = catalog_url(config)
        catalog: Catalog | None = None
        try:
            response = httpx.get(url, timeout=_FETCH_TIMEOUT_S, follow_redirects=True)
            response.raise_for_status()
            catalog = parse_catalog(response.json(), plan=plan or None)
        except Exception as exc:
            # A catalog synced earlier is a better answer than the embedded
            # fallback: applying the fallback over it dropped presets (27 -> 15)
            # and flipped the default model every time the network blinked,
            # then flipped it back on the next successful fetch.
            if any(
                getattr(preset, "provider", "") == "navin"
                for preset in config.model_presets.values()
            ):
                logger.warning(
                    "Model catalog fetch failed ({}): {}; keeping the catalog synced earlier",
                    url,
                    exc,
                )
                return False
            logger.warning(
                "Model catalog fetch failed ({}): {}; applying embedded fallback",
                url,
                exc,
            )
            try:
                catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD, plan=plan or None)
            except Exception as fallback_exc:
                logger.warning("Model catalog fallback unusable: {}", fallback_exc)
                return False

        try:
            if not apply_catalog(
                config,
                catalog,
                force_managed=force_managed,
                steer_tools=steer_tools and force_managed,
            ):
                logger.debug(
                    "Model catalog unchanged ({} models, plan={})",
                    len(catalog.models),
                    plan or "default",
                )
                return False
            from navin.config.loader import save_config

            save_config(config)
        except Exception as exc:
            logger.warning("Model catalog could not be applied: {}", exc)
            return False

        logger.info(
            "Model catalog synced: {} models, plan='{}', default '{}'",
            len(catalog.models),
            plan or "default",
            catalog.default_model or config.agents.defaults.model,
        )
        return True


def refresh_managed_catalog(
    *,
    force: bool = False,
    min_interval_s: float = UI_SYNC_MIN_INTERVAL_S,
) -> bool:
    """Load config from disk, sync catalog, return True when presets changed."""
    from navin.config.loader import load_config

    config = load_config()
    return sync_managed_catalog(
        config,
        force=force,
        min_interval_s=min_interval_s,
    )
