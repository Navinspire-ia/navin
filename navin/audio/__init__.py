# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared audio service helpers (transcription / TTS)."""

from navin.audio.transcription import (
    resolve_transcription_config,
    transcribe_audio_file,
)
from navin.audio.tts import (
    synthesize_speech,
    voice_realtime_allowed,
)

__all__ = [
    "resolve_transcription_config",
    "synthesize_speech",
    "transcribe_audio_file",
    "voice_realtime_allowed",
]

