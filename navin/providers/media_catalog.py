# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Media models of providers whose model list does not show them.

xAI's ``/v1/models`` lists the chat models only, and its voice API has no
model id at all; Alibaba's compatible-mode listing does not show the Qwen
TTS / ASR / Omni Realtime or Wan models. For these providers the media pickers (Settings > Voice,
image, video, and the CLI) read this table instead of the live listing.
"""

from __future__ import annotations

from typing import Any

#: provider -> use -> [(model id, label)]
STATIC_MEDIA_MODELS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "xai": {
        "tts": [("grok-voice-tts", "Grok Voice")],
        "stt": [("grok-voice-transcribe-2.0", "Grok Transcribe 2.0")],
        "live": [("grok-voice-latest", "Grok Voice (realtime)"),
                 ("grok-voice-think-fast-2.0", "Grok Voice Think Fast 2.0")],
        "image": [("grok-imagine-image-2.0", "Grok Imagine Image 2.0")],
        "video": [("grok-imagine-video-1.5", "Grok Imagine Video 1.5")],
    },
    "qwen": {
        "tts": [("qwen3-tts-flash", "Qwen3 TTS Flash"), ("qwen3-tts-instruct-flash", "Qwen3 TTS Instruct Flash")],
        "stt": [("qwen3-asr-flash", "Qwen3 ASR Flash")],
        "live": [("qwen3.8-omni-flash-realtime", "Qwen3.8 Omni Flash Realtime"),
                 ("qwen3.5-omni-plus-realtime", "Qwen3.5 Omni Plus Realtime"),
                 ("qwen3.5-omni-flash-realtime", "Qwen3.5 Omni Flash Realtime"),
                 ("qwen3-omni-flash-realtime", "Qwen3 Omni Flash Realtime")],
        "image": [("qwen-image-2.0-pro", "Qwen Image 2.0 Pro"), ("qwen-image-max", "Qwen Image Max"),
                  ("qwen-image-plus", "Qwen Image Plus"), ("wan2.6-t2i", "Wan 2.6 Image")],
        "video": [("wan2.7-t2v-2026-06-12", "Wan 2.7 Video"), ("wan2.6-t2v", "Wan 2.6 Video"),
                  ("wan2.6-i2v", "Wan 2.6 Image to Video")],
    },
}


def static_media_rows(provider: str, kind: str) -> list[dict[str, Any]] | None:
    """Picker rows for *provider* and *kind*, or None when the provider is not in the table."""
    uses = STATIC_MEDIA_MODELS.get((provider or "").strip().lower())
    if uses is None:
        return None
    return [
        {"id": model_id, "label": label, "owned_by": provider, "media_modalities": [kind], "context_window": None}
        for model_id, label in uses.get(kind, [])
    ]
