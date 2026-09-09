# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Filter model catalogs by the media they produce, not what they can read."""

from __future__ import annotations

import re
from typing import Any

MEDIA_MODEL_KINDS = frozenset({"stt", "tts", "image", "video", "music"})
OPENROUTER_OUTPUT_MODALITIES = {
    "stt": "transcription", "tts": "speech", "image": "image", "video": "video", "music": "audio",
}

# Used only when the provider does not declare output capabilities. Deliberately
# match dedicated generators: a GPT/Claude/Gemini vision family is not an image generator.
_FAMILY_PATTERNS = {
    "stt": r"(?:^|[-_.])(?:asr|stt|whisper|transcribe|parakeet|sensevoice(?:small)?|nova-3|chirp-3)(?:[-_.]|$)",
    "tts": r"(?:^|[-_.])(?:tts|kokoro|orpheus|csm|aura-2|mai-voice)(?:[-_.]|$)",
    "video": r"(?:^|[-_.])(?:video|veo|sora|seedance|kling|hailuo|happyhorse|wan-[0-9]|gen-4|aleph)(?:[-_.0-9]|$)",
    "image": r"(?:^|[-_.])(?:gpt-image|qwen-image|glm-image|mai-image|muse-image|grok-imagine-image|hunyuan-image|imagegen|imagen|dall-e|seedream|stable-diffusion|sdxl|sd3|recraft|riverflow|ideogram|kolors|cogview|flux)(?:[-_.0-9]|$)|^gemini-[\w.-]*(?:flash|pro)(?:-lite|-preview)?-image(?:[-_.]|$)",
    "music": r"(?:^|[-_.])(?:lyria|musicgen|music|stable-audio|suno|udio|ace-step)(?:[-_.0-9]|$)",
}
_TASK_KINDS = {
    "automatic-speech-recognition": "stt", "speech-to-text": "stt", "transcription": "stt",
    "text-to-speech": "tts", "speech": "tts", "text-to-image": "image", "image-generation": "image",
    "text-to-video": "video", "image-to-video": "video", "video-generation": "video",
    "text-to-music": "music", "music-generation": "music",
}


def media_model_kinds(model: str, row: dict[str, Any] | None = None) -> list[str]:
    """Provider declarations take precedence; an explicit text output stays text."""
    row = row if isinstance(row, dict) else {}
    normalized = row.get("media_modalities")
    if isinstance(normalized, list):
        return [kind for kind in normalized if isinstance(kind, str) and kind in MEDIA_MODEL_KINDS]
    slug = model.strip().lower().rsplit("/", 1)[-1]
    families = {kind for kind, pattern in _FAMILY_PATTERNS.items() if re.search(pattern, slug)}
    if re.search(r"embed|rerank|caption|classif|moderation|segmentation|understanding", slug):
        families.clear()
    if "video" in families:
        families.discard("image")
    architecture = row.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    outputs = row.get("output_modalities", architecture.get("output_modalities"))
    declared = set()
    for value in (row.get("task"), row.get("pipeline_tag")):
        if isinstance(value, str) and value.lower() in _TASK_KINDS:
            declared.add(_TASK_KINDS[value.lower()])
    if isinstance(outputs, list) and outputs:
        outputs = {str(value).lower() for value in outputs}
        declared.update(kind for kind in ("image", "video", "music") if kind in outputs)
        if "speech" in outputs:
            declared.add("tts")
        if "transcription" in outputs:
            declared.add("stt")
        if "audio" in outputs:
            # OpenRouter Lyria declares text+audio, just like audio chat. Its
            # dedicated family distinguishes music from conversational audio.
            declared.update(families & {"music", "tts"})
        return sorted(declared)
    return sorted(declared or families)


def supports_media_model(model: str, kind: str, row: dict[str, Any] | None = None) -> bool:
    return kind in media_model_kinds(model, row)
