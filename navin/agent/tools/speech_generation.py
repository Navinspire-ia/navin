# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Speech generation tool: narration and voice over as persistent artifacts.

The TTS engine already existed for the realtime voice session, but no agent tool
exposed it, so the agent could produce music and footage yet never a spoken
track. That made "une video avec voix off" impossible to fulfil. This tool wires
the same engine into the tool surface and stores the result as an artifact the
montage step can mix.

For marketing videos one monolithic clip is not enough: each scene needs its
own line so cuts land on sentence boundaries, and captions need word timings.
``segments`` synthesizes one clip per scene and returns per-segment paths,
durations and word timestamps (from the provider when it has them, from a
read-back transcription as forced alignment for OpenAI/Groq, otherwise a
syllable-weighted estimate). Narration can use its own voice/model
(``narration_voice``/``narration_model`` in config, ``voice``/``model`` per
call), separate from the realtime chat voice.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.config_base import Base
from navin.utils.artifacts import (
    ArtifactError,
    generated_speech_tool_result,
    store_generated_speech_artifact,
)

#: Guard against a runaway narration script burning a whole TTS quota in one call.
MAX_SPEECH_CHARS = 8000
#: One segment per scene; a marketing cut rarely has more than a few dozen.
MAX_SPEECH_SEGMENTS = 60
#: Parallel synthesis requests for a segmented narration.
SEGMENT_CONCURRENCY = 3
#: Typical narration pace, only used when the clip length cannot be measured.
ESTIMATED_WORDS_PER_SECOND = 2.6

QUALITY_STANDARD = "standard"
QUALITY_NARRATION = "narration"

#: Higher-fidelity sibling of a realtime model, used for narration when the
#: config names no narration model. Only well-known slugs are mapped.
_NARRATION_GRADE_MODELS: dict[str, str] = {
    "tts-1": "tts-1-hd",
    "openai/tts-1": "openai/tts-1-hd",
}

_FORMAT_MIMES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/wav",
}


class SpeechGenerationToolConfig(Base):
    """Speech generation tool configuration.

    Voice, model and provider intentionally default to the top-level ``voice``
    settings so narration matches the voice the user already picked for the
    realtime session. Set them here only to diverge on purpose.
    """

    enabled: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    voice: str = ""
    model: str = ""
    save_dir: str = "generated-speech"
    #: Voice and model for narration-grade reads (segmented narration, or
    #: ``quality="narration"``), independent of the realtime chat voice.
    narration_voice: str = ""
    narration_model: str = ""
    #: Ask for word timestamps on segmented narration (captions need them).
    word_timestamps: bool = True


def narration_grade_model(model: str) -> str:
    """The higher-fidelity sibling of *model*, or *model* itself."""
    return _NARRATION_GRADE_MODELS.get((model or "").strip(), model)


def validate_speech_segments(segments: Any) -> list[str]:
    """Return the cleaned segment scripts, or raise ``ValueError``."""
    if isinstance(segments, str):
        try:
            segments = json.loads(segments)
        except ValueError:
            segments = [segments]
    if not isinstance(segments, (list, tuple)) or not segments:
        raise ValueError("segments must be a non-empty list of scripts, one per scene")
    out: list[str] = []
    for index, item in enumerate(segments):
        text = item.get("text") if isinstance(item, dict) else item
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError(f"segment {index + 1} is empty")
        out.append(cleaned)
    if len(out) > MAX_SPEECH_SEGMENTS:
        raise ValueError(
            f"{len(out)} segments is over the {MAX_SPEECH_SEGMENTS} limit; merge short lines"
        )
    total = sum(len(text) for text in out)
    if total > MAX_SPEECH_CHARS:
        raise ValueError(
            f"segments total {total} characters, over the {MAX_SPEECH_CHARS} limit; "
            "split the narration into several calls"
        )
    return out


def wav_duration(audio: bytes) -> float | None:
    """Length of a WAV payload in seconds, without ffmpeg."""
    if not audio.startswith(b"RIFF"):
        return None
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            rate = source.getframerate()
            return source.getnframes() / rate if rate else None
    except (wave.Error, EOFError):
        return None


async def measure_speech_duration(audio: bytes, path: str) -> tuple[float | None, bool]:
    """``(seconds, measured)`` for a stored clip: WAV header, then ffprobe/ffmpeg."""
    duration = wav_duration(audio)
    if duration:
        return duration, True
    try:
        from navin.montage import detect
        from navin.montage.assemble import (
            find_ffprobe,
            probe_duration,
            probe_duration_with_ffmpeg,
        )

        ffmpeg = await asyncio.to_thread(detect.find_ffmpeg)
        ffprobe = await asyncio.to_thread(find_ffprobe, ffmpeg)
        if ffprobe:
            duration = await probe_duration(path, ffprobe)
        elif ffmpeg:
            duration = await probe_duration_with_ffmpeg(path, ffmpeg)
    except Exception:  # noqa: BLE001 - measuring is best effort
        duration = None
    return (duration, True) if duration else (None, False)


def resolve_speech_mime(response_format: str | None) -> str:
    """Map a TTS response format to a mime type, defaulting to MP3."""
    key = (response_format or "").strip().lower()
    return _FORMAT_MIMES.get(key, "audio/mpeg")


def validate_speech_text(text: str | None) -> str:
    """Return the narration script, or raise ``ValueError`` with a usable reason."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("text is required and must not be empty")
    if len(cleaned) > MAX_SPEECH_CHARS:
        raise ValueError(
            f"text is {len(cleaned)} characters, over the {MAX_SPEECH_CHARS} limit; "
            "split the narration into several calls"
        )
    return cleaned


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema(
            "Exact words to speak. Write the final script, not a summary: this text "
            "is synthesized verbatim. Keep punctuation, it drives the pacing. "
            "Omit when passing segments.",
            min_length=1,
        ),
        segments=ArraySchema(
            StringSchema("Script of one scene, spoken verbatim."),
            description=(
                "Narration for a video, one script per scene or shot, in order. "
                "Returns one clip per segment with its duration and word timestamps "
                "for captions. Use instead of text for montage voice over."
            ),
            min_items=1,
            max_items=MAX_SPEECH_SEGMENTS,
        ),
        voice=StringSchema(
            "Optional voice id override. Defaults to the configured voice "
            "(the narration voice for segments).",
        ),
        model=StringSchema(
            "Optional TTS model override, e.g. a narration-grade model.",
        ),
        quality=StringSchema(
            "standard (chat voice settings) or narration (narration voice/model). "
            "Segments default to narration.",
            enum=[QUALITY_STANDARD, QUALITY_NARRATION],
        ),
        timestamps=BooleanSchema(
            description="Return word timestamps per segment (default true for segments).",
        ),
        language=StringSchema(
            "Optional BCP-47 language tag recorded on the artifact (e.g. fr-FR).",
        ),
    )
)
class SpeechGenerationTool(Tool):
    """Synthesize narration through the configured TTS provider."""

    config_key = "speech_generation"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return SpeechGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        config = getattr(ctx.config, "speech_generation", None)
        explicit = getattr(config, "enabled", None) if config else None
        if explicit is not None:
            return bool(explicit)
        # Auto mode: on as soon as the voice stack has a usable credential, which
        # is the same rule the image and video tools use.
        try:
            from navin.audio.tts import resolve_tts_config
            from navin.config.loader import load_config

            return resolve_tts_config(load_config()).configured
        except Exception:  # noqa: BLE001 - config probing must never break loading
            return False

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=getattr(ctx.config, "speech_generation", None)
            or SpeechGenerationToolConfig(),
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: SpeechGenerationToolConfig,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config

    @property
    def name(self) -> str:
        return "generate_speech"

    @property
    def description(self) -> str:
        return (
            "Synthesize spoken audio (narration, voice over, podcast read) from text "
            "using the configured TTS voice, and store it as a persistent artifact. "
            "Use this for any spoken track, including the voice layer of a video. "
            "Returns artifact ids and local paths."
        )

    def _choose_voice_model(
        self,
        resolved: Any,
        *,
        voice: str | None,
        model: str | None,
        narration: bool,
    ) -> tuple[str, str]:
        """Per-call override, then narration config, then tool config, then chat voice."""
        config = self.config
        narration_voice = getattr(config, "narration_voice", "") if narration else ""
        narration_model = getattr(config, "narration_model", "") if narration else ""
        chosen_voice = (voice or narration_voice or config.voice or resolved.voice or "").strip()
        chosen_model = (model or narration_model or config.model or "").strip()
        if not chosen_model:
            base = (resolved.model or "").strip()
            chosen_model = narration_grade_model(base) if narration else base
        return chosen_voice, chosen_model

    async def execute(
        self,
        text: str | None = None,
        voice: str | None = None,
        language: str | None = None,
        segments: list[Any] | None = None,
        model: str | None = None,
        quality: str | None = None,
        timestamps: bool | None = None,
        **kwargs: Any,
    ) -> str:
        from navin.audio.tts import (
            TtsError,
            resolve_tts_config,
            synthesize_speech_with_config,
        )
        from navin.config.loader import load_config

        try:
            if segments:
                scripts = validate_speech_segments(segments)
            else:
                script = validate_speech_text(text)
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

        resolved = resolve_tts_config(load_config())
        if not resolved.configured:
            return ToolResult.error(
                "Error: no text-to-speech credential is configured. Set a provider "
                "API key or enable the managed Navin plan to generate voice tracks."
            )

        narration = (quality or (QUALITY_NARRATION if segments else QUALITY_STANDARD)) == (
            QUALITY_NARRATION
        )
        chosen_voice, chosen_model = self._choose_voice_model(
            resolved, voice=voice, model=model, narration=narration
        )
        effective = replace_tts_config(resolved, voice=chosen_voice, model=chosen_model)

        if segments:
            want_words = (
                bool(timestamps)
                if timestamps is not None
                else bool(getattr(self.config, "word_timestamps", True))
            )
            return await self._execute_segments(
                scripts,
                effective,
                language=(language or "").strip() or None,
                want_words=want_words,
            )

        try:
            audio = await synthesize_speech_with_config(script, effective)
            artifact = store_generated_speech_artifact(
                audio,
                mime=resolve_speech_mime(effective.response_format),
                text=script,
                model=effective.model,
                voice=effective.voice,
                save_dir=self.config.save_dir,
                provider=effective.provider,
                language=(language or "").strip() or None,
            )
            return generated_speech_tool_result([artifact])
        except (ArtifactError, TtsError, OSError) as exc:
            from navin.providers.user_facing_errors import user_facing_llm_error

            return ToolResult.error(user_facing_llm_error(str(exc)))

    async def _execute_segments(
        self,
        scripts: list[str],
        effective: Any,
        *,
        language: str | None,
        want_words: bool,
    ) -> str:
        """One clip per scene, with duration and word timings for captions."""
        from navin.audio.tts import TtsError

        gate = asyncio.Semaphore(SEGMENT_CONCURRENCY)

        async def one(index: int, script: str) -> tuple[dict[str, Any], dict[str, Any]]:
            async with gate:
                return await self._synthesize_segment(
                    index, script, effective, language=language, want_words=want_words
                )

        try:
            results = await asyncio.gather(
                *(one(index, script) for index, script in enumerate(scripts))
            )
        except (ArtifactError, TtsError, OSError) as exc:
            from navin.providers.user_facing_errors import user_facing_llm_error

            return ToolResult.error(user_facing_llm_error(str(exc)))
        artifacts = [artifact for artifact, _segment in results]
        segment_rows = [segment for _artifact, segment in results]
        total = sum(float(row["duration_s"]) for row in segment_rows)
        return json.dumps(
            {
                "artifacts": artifacts,
                "segments": segment_rows,
                "total_duration_s": round(total, 3),
                "voice": effective.voice,
                "model": effective.model,
                "next_step": (
                    "Pass the segments to the montage assemble step as voice_segments "
                    "(add each scene start as at), with captions=True for word-by-word "
                    "captions. Word times are relative to each segment. Keep raw paths "
                    "internal unless the user asks for debug details."
                ),
            },
            ensure_ascii=False,
        )

    async def _synthesize_segment(
        self,
        index: int,
        script: str,
        effective: Any,
        *,
        language: str | None,
        want_words: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from navin.audio.tts import TtsError, synthesize_speech_with_config
        from navin.montage.captions import (
            CaptionWord,
            align_script_words,
            estimate_word_timings,
            split_words,
        )
        from navin.providers import tts as tts_providers

        audio = b""
        heard: list[dict[str, Any]] = []
        source = "estimate"
        mime = resolve_speech_mime(effective.response_format)
        if want_words and tts_providers.is_elevenlabs_base(effective.api_base):
            audio, heard = await tts_providers.elevenlabs_synthesize_with_timestamps(
                script,
                api_key=effective.api_key,
                voice_id=effective.voice,
                model=effective.model or None,
                api_base=effective.api_base,
            )
            if audio:
                mime = "audio/mpeg"
            if heard:
                source = "provider"
        if not audio:
            audio = await synthesize_speech_with_config(script, effective)
        if not audio:
            raise TtsError("synthesis_failed", provider=effective.provider)
        artifact = store_generated_speech_artifact(
            audio,
            mime=mime,
            text=script,
            model=effective.model,
            voice=effective.voice,
            save_dir=self.config.save_dir,
            provider=effective.provider,
            language=language,
        )
        path = str(artifact["path"])
        duration, measured = await measure_speech_duration(audio, path)
        if duration is None:
            duration = max(0.5, len(split_words(script)) / ESTIMATED_WORDS_PER_SECOND)
        if want_words and not heard:
            words_model = tts_providers.WORD_TIMESTAMP_MODELS.get(effective.provider)
            base = effective.api_base or _TRANSCRIPTION_BASES.get(effective.provider, "")
            if words_model and base:
                heard = await tts_providers.transcribe_word_timestamps(
                    audio,
                    api_key=effective.api_key,
                    api_base=base,
                    model=words_model,
                    filename=Path(path).name,
                    language=language,
                    provider_label=str(effective.provider),
                )
                if heard:
                    source = "alignment"
        words: list[CaptionWord] = []
        if want_words:
            words = (
                align_script_words(script, heard, 0.0, duration)
                if heard
                else estimate_word_timings(script, 0.0, duration)
            )
        segment = {
            "index": index,
            "text": script,
            "artifact_id": artifact.get("id"),
            "path": path,
            "duration_s": round(duration, 3),
            "duration_measured": measured,
            "timing_source": source if want_words else None,
            "words_relative": True,
            "words": [word.to_dict() for word in words],
        }
        if want_words:
            timing_path = Path(path).with_suffix(".timing.json")
            timing_text = json.dumps(segment, ensure_ascii=False, indent=2)
            try:
                await asyncio.to_thread(timing_path.write_text, timing_text, encoding="utf-8")
            except OSError as exc:
                # The audio is already on disk; a missing sidecar only costs
                # word timings, which the caller can recompute.
                logger.warning("speech: cannot write word timings {}: {}", timing_path, exc)
        return artifact, segment


#: Transcription endpoints used for word timings when the TTS config carries
#: no explicit API base (the adapters fall back to these hosts too).
_TRANSCRIPTION_BASES: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def replace_tts_config(resolved: Any, *, voice: str, model: str) -> Any:
    """Return *resolved* with voice/model overridden, keeping credentials intact."""
    import dataclasses

    updates: dict[str, Any] = {}
    if voice and voice != getattr(resolved, "voice", ""):
        updates["voice"] = voice
    if model and model != getattr(resolved, "model", ""):
        updates["model"] = model
    if not updates:
        return resolved
    return dataclasses.replace(resolved, **updates)
