# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Speech model defaults and voice IDs shared by settings and runtime adapters."""

from __future__ import annotations

import hashlib
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
# Amazon Polly (AWS Bedrock provider): French first, then English and Arabic.
POLLY_NEURAL_VOICES = (
    "Lea", "Remi", "Gabrielle", "Liam", "Joanna", "Matthew", "Ruth", "Stephen",
    "Amy", "Brian", "Arthur", "Hala", "Zayd",
)
POLLY_GENERATIVE_VOICES = ("Lea", "Remi", "Ruth", "Matthew", "Stephen", "Danielle", "Joanna", "Amy")

# Who a known voice sounds like and the language it is made for ("" =
# multilingual), so a teammate speaks in Live with a voice of its own gender.
_VOICE_PROFILES: dict[str, tuple[str, str]] = {
    # Amazon Polly
    "lea": ("female", "fr"), "remi": ("male", "fr"), "gabrielle": ("female", "fr"), "liam": ("male", "fr"),
    "joanna": ("female", "en"), "matthew": ("male", "en"), "ruth": ("female", "en"), "stephen": ("male", "en"),
    "amy": ("female", "en"), "brian": ("male", "en"), "arthur": ("male", "en"), "danielle": ("female", "en"),
    "hala": ("female", "ar"), "zayd": ("male", "ar"),
    # OpenAI
    "alloy": ("female", ""), "ash": ("male", ""), "ballad": ("male", ""), "coral": ("female", ""),
    "echo": ("male", ""), "fable": ("male", ""), "nova": ("female", ""), "onyx": ("male", ""),
    "sage": ("female", ""), "shimmer": ("female", ""), "verse": ("male", ""), "marin": ("female", ""),
    "cedar": ("male", ""),
    # xAI Grok
    "eve": ("female", ""), "ara": ("female", ""), "rex": ("male", ""), "sal": ("male", ""), "leo": ("male", ""),
    # Orpheus
    "troy": ("male", "en"), "autumn": ("female", "en"), "diana": ("female", "en"), "hannah": ("female", "en"),
    "austin": ("male", "en"), "daniel": ("male", "en"), "abdullah": ("male", "ar"), "fahad": ("male", "ar"),
    "sultan": ("male", "ar"), "lulwa": ("female", "ar"), "noura": ("female", "ar"), "aisha": ("female", "ar"),
    # Qwen audio (Alibaba Cloud)
    "loongjohn": ("male", ""), "longanhuan_v3.6": ("female", ""), "longanlingxin": ("female", ""),
    "longanlufeng": ("male", ""),
    # Gemini
    **{name: ("female", "") for name in (
        "kore", "aoede", "leda", "zephyr", "callirrhoe", "autonoe", "despina", "erinome", "laomedeia",
        "achernar", "gacrux", "pulcherrima", "vindemiatrix", "sulafat",
    )},
    **{name: ("male", "") for name in (
        "puck", "charon", "fenrir", "orus", "enceladus", "iapetus", "umbriel", "algieba", "algenib",
        "rasalgethi", "alnilam", "schedar", "achird", "zubenelgenubi", "sadachbia", "sadaltager",
    )},
}


def voice_profile(voice: str) -> tuple[str, str]:
    """(gender, language) of a known voice; ("", "") when Navin does not know it."""
    return _VOICE_PROFILES.get((voice or "").strip().lower(), ("", ""))


def teammate_voice(model: str, gender: str, language: str = "", *, key: str = "", avoid: str = "") -> str:
    """A voice of *gender* among the voices *model* has, for one teammate.

    The user's language comes first, then multilingual and English voices.
    Teammates of one gender are spread over the voices there are (the same
    one for the same *key*), and *avoid* (the lead's own voice) is only used
    when nothing else fits, so a teammate does not sound like Navin. "" when
    the model lists no voice of that gender.
    """
    lang = (language or "").strip().lower()[:2]

    def rank(voice: str) -> int:
        spoken = voice_profile(voice)[1]
        return 0 if lang and spoken == lang else 1 if spoken == "" else 2 if spoken == "en" else 3

    voices = [voice for voice in known_voices(model) if voice_profile(voice)[0] == gender]
    if not voices:
        return ""
    best = min(rank(voice) for voice in voices)
    group = [voice for voice in voices if rank(voice) == best]
    others = [voice for voice in group if voice.lower() != (avoid or "").strip().lower()]
    group = others or group
    index = int(hashlib.sha256((key or gender).encode("utf-8")).hexdigest()[:8], 16) % len(group)
    return group[index]


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
    if slug.startswith("qwen3-tts"):
        from navin.providers.qwen_media import QWEN_TTS_VOICES

        return QWEN_TTS_VOICES
    if slug == "polly-neural":
        return POLLY_NEURAL_VOICES
    if slug == "polly-generative":
        return POLLY_GENERATIVE_VOICES
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
