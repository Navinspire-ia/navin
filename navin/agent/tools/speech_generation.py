"""Speech generation tool: narration and voice over as persistent artifacts.

The TTS engine already existed for the realtime voice session, but no agent tool
exposed it, so the agent could produce music and footage yet never a spoken
track. That made "une video avec voix off" impossible to fulfil. This tool wires
the same engine into the tool surface and stores the result as an artifact the
montage step can mix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema
from navin.config_base import Base
from navin.utils.artifacts import (
    ArtifactError,
    generated_speech_tool_result,
    store_generated_speech_artifact,
)

#: Guard against a runaway narration script burning a whole TTS quota in one call.
MAX_SPEECH_CHARS = 8000

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
            "is synthesized verbatim. Keep punctuation, it drives the pacing.",
            min_length=1,
        ),
        voice=StringSchema(
            "Optional voice id override. Defaults to the configured voice.",
        ),
        language=StringSchema(
            "Optional BCP-47 language tag recorded on the artifact (e.g. fr-FR).",
        ),
        required=["text"],
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

    async def execute(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        **kwargs: Any,
    ) -> str:
        from navin.audio.tts import (
            TtsError,
            resolve_tts_config,
            synthesize_speech_with_config,
        )
        from navin.config.loader import load_config

        try:
            script = validate_speech_text(text)
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

        resolved = resolve_tts_config(load_config())
        if not resolved.configured:
            return ToolResult.error(
                "Error: no text-to-speech credential is configured. Set a provider "
                "API key or enable the managed Navin plan to generate voice tracks."
            )

        chosen_voice = (voice or self.config.voice or resolved.voice or "").strip()
        chosen_model = (self.config.model or resolved.model or "").strip()
        effective = replace_tts_config(resolved, voice=chosen_voice, model=chosen_model)

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
