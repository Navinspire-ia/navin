"""Registry for text-to-speech providers.

Provider-specific HTTP adapters live in ``navin.providers.tts``.
This module is the app-level source of truth for provider names, aliases,
default models/voices, and adapter class paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol

from navin.audio.models import GROQ_TTS_MODEL, GROQ_TTS_VOICE, NAVIN_TTS_MODEL, NAVIN_TTS_VOICE
from navin.optional_live import live_modules_available


class TtsProviderAdapter(Protocol):
    """Runtime protocol implemented by provider-specific TTS adapters."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
    ) -> None: ...

    async def synthesize(self, text: str) -> bytes: ...


@dataclass(frozen=True)
class TtsProviderSpec:
    name: str
    default_model: str
    default_voice: str
    adapter: str
    aliases: tuple[str, ...] = ()

    def load_adapter(self) -> type[TtsProviderAdapter]:
        module_name, _, class_name = self.adapter.partition(":")
        if not module_name or not class_name:
            raise RuntimeError(f"Invalid TTS adapter path: {self.adapter}")
        return getattr(import_module(module_name), class_name)


TTS_PROVIDERS: tuple[TtsProviderSpec, ...] = (
    TtsProviderSpec(
        name="navin",
        default_model=NAVIN_TTS_MODEL,
        default_voice=NAVIN_TTS_VOICE,
        adapter="navin.providers.tts:NavinTtsProvider",
    ),
    TtsProviderSpec(
        name="openai",
        default_model="tts-1",
        default_voice="alloy",
        adapter="navin.providers.tts:OpenAICompatibleTtsProvider",
    ),
    TtsProviderSpec(
        name="openrouter",
        default_model=NAVIN_TTS_MODEL,
        default_voice=NAVIN_TTS_VOICE,
        adapter="navin.providers.tts:OpenRouterTtsProvider",
    ),
    TtsProviderSpec(
        name="groq",
        default_model=GROQ_TTS_MODEL,
        default_voice=GROQ_TTS_VOICE,
        adapter="navin.providers.tts:GroqTtsProvider",
    ),
    TtsProviderSpec(
        name="gemini",
        default_model="gemini-3.1-flash-tts-preview",
        default_voice="Kore",
        adapter="navin.providers.tts:GeminiTtsProvider",
        aliases=("google",),
    ),
    TtsProviderSpec(
        name="ollama",
        default_model="tts",
        default_voice="alloy",
        adapter="navin.providers.tts:OllamaTtsProvider",
    ),
    TtsProviderSpec(
        name="vllm",
        default_model="tts-1",
        default_voice="alloy",
        adapter="navin.providers.tts:VllmTtsProvider",
    ),
    TtsProviderSpec(
        name="lm_studio",
        default_model="tts-1",
        default_voice="alloy",
        adapter="navin.providers.tts:LmStudioTtsProvider",
        aliases=("lm-studio", "lmstudio"),
    ),
)

if not live_modules_available():
    TTS_PROVIDERS = tuple(spec for spec in TTS_PROVIDERS if spec.name != "navin")

_BY_NAME = {spec.name: spec for spec in TTS_PROVIDERS}
_BY_ALIAS = {alias: spec for spec in TTS_PROVIDERS for alias in spec.aliases}


def tts_provider_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in TTS_PROVIDERS)


def get_tts_provider(name: str) -> TtsProviderSpec | None:
    return _BY_NAME.get(name)


def resolve_tts_provider(value: Any) -> TtsProviderSpec | None:
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    return _BY_NAME.get(name) or _BY_ALIAS.get(name)
