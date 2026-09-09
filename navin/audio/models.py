# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Speech model defaults and voice IDs shared by settings and runtime adapters."""

from __future__ import annotations

from typing import Any

NAVIN_STT_MODEL = "qwen/qwen3-asr-flash-2026-02-10"
NAVIN_TTS_MODEL = "qwen/qwen-audio-3.0-tts-flash"
NAVIN_TTS_VOICE = "loongjohn"
GROQ_TTS_MODEL = "canopylabs/orpheus-v1-english"
GROQ_TTS_VOICE = "troy"
ORPHEUS_VOICES = {
    "orpheus-v1-english": ("troy", "autumn", "diana", "hannah", "austin", "daniel"),
    "orpheus-arabic-saudi": ("abdullah", "fahad", "sultan", "lulwa", "noura", "aisha"),
}

# OpenRouter's speech catalog advertises these IDs. They are different from
# the Cherry / Serena voices of Qwen's older DashScope TTS models.
QWEN_SPEECH_VOICES = {
    "qwen-audio-3.0-tts-flash": ("loongjohn", "longanhuan_v3.6"),
    "qwen-audio-3.0-tts-plus": ("longanlingxin", "longanlufeng"),
}
OPENAI_TTS_VOICES = (
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
    "sage", "shimmer", "verse", "marin", "cedar",
)
GROK_TTS_VOICES = ("eve", "ara", "rex", "sal", "leo")


def known_voices(model: str) -> tuple[str, ...]:
    """Offline voice fallback; a fetched provider declaration takes precedence."""
    slug = model.lower().rsplit("/", 1)[-1]
    if slug in ORPHEUS_VOICES:
        return ORPHEUS_VOICES[slug]
    for prefix, voices in QWEN_SPEECH_VOICES.items():
        if slug.startswith(prefix):
            return voices
    if "grok-voice" in slug:
        return GROK_TTS_VOICES
    if slug in {"tts-1", "tts-1-hd"}:
        return ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
    if "gpt" in slug and "tts" in slug:
        return OPENAI_TTS_VOICES
    if "gemini" in slug and "tts" in slug:
        from navin.providers.tts import GEMINI_TTS_VOICES

        return GEMINI_TTS_VOICES
    return ()


def default_voice(model: str) -> str:
    if "gemini" in model.lower() and "tts" in model.lower():
        return "Kore"
    voices = known_voices(model)
    return voices[0] if voices else ""


def speech_model_kind(model: str, row: dict[str, Any] | None = None) -> str | None:
    """Only dedicated speech endpoints qualify, not audio chat or music models."""
    from navin.providers.media_models import media_model_kinds

    return next((kind for kind in media_model_kinds(model, row) if kind in {"stt", "tts"}), None)
