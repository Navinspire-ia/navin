"""Whether a chat model can actually read an attached image.

Providers disagree on how they advertise this. OpenRouter ships
``architecture.input_modalities`` on ``GET /models``, most OpenAI-compatible
endpoints ship nothing at all. So the provider answer wins when it exists, and
a family-name heuristic covers the rest.

The heuristic matches model *families*, not exact slugs: vendors ship a new
dated or sized variant every few weeks, and a hardcoded slug list silently
misclassifies every one of them as text-only.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# Substrings that identify a multimodal-input family. Matched against the
# lowercased slug, after the provider prefix is also tried on its own.
_VISION_FAMILY_MARKERS: tuple[str, ...] = (
    # OpenAI
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-5",
    "o3",
    "o4-mini",
    "chatgpt-4o",
    # Anthropic: every Claude 3 and later reads images.
    "claude-3",
    "claude-4",
    "claude-5",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku",
    # Google
    "gemini",
    "gemma-3",
    # xAI
    "grok-2-vision",
    "grok-3",
    "grok-4",
    "grok-vision",
    # Meta
    "llama-3.2-11b",
    "llama-3.2-90b",
    "llama-4",
    # Qwen
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "qwen3.8-max",
    "qwen3.8-flash",
    "qwen3.8-27b",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qvq",
    # Mistral
    "pixtral",
    "mistral-medium-3",
    "mistral-small-3.1",
    "mistral-small-3.2",
    # Open multimodal families
    "internvl",
    "minicpm-v",
    "moondream",
    "llava",
    "cogvlm",
    "glm-4v",
    "glm-4.1v",
    "glm-4.5v",
    "yi-vision",
    "phi-3-vision",
    "phi-3.5-vision",
    "phi-4-multimodal",
    "molmo",
    "aya-vision",
    "nemotron-nano-vl",
    "step-1v",
    "ernie-4.5-vl",
    "kimi-vl",
    "dots.vlm",
    # GUI agents: trained on screenshots, so vision by construction.
    "computer-use",
    "ui-tars",
    "showui",
    "os-atlas",
    "seeclick",
    "aguvis",
    "cogagent",
    "opencua",
    "ferret-ui",
    "holo1",
    "holo-1",
    "gui-actor",
    "uground",
    # Generic markers used across vendors.
    "-vl-",
    "-vl:",
    "vision",
    "omni",
    "multimodal",
    "mimo-v2.5",
)

# Families whose name contains a vision marker but that cannot read images.
# Checked first so `-omni` style suffixes do not over-match audio-only models.
_TEXT_ONLY_OVERRIDES: tuple[str, ...] = (
    "whisper",
    "tts",
    "text-embedding",
    "embed",
    "rerank",
    "moderation",
    "voice",
    "-stt",
    "transcribe",
    "parakeet",
)


def _normalize(slug: str | None) -> str:
    return (slug or "").strip().lower()


def _modalities_say_vision(modalities: Iterable[Any] | None) -> bool | None:
    """Provider-declared answer, or ``None`` when nothing was declared."""
    if modalities is None:
        return None
    values = [str(item).strip().lower() for item in modalities if item is not None]
    if not values:
        return None
    return any(value in {"image", "images", "video", "vision"} for value in values)


def input_modalities_from_row(row: Mapping[str, Any] | None) -> list[str] | None:
    """Pull ``architecture.input_modalities`` out of a provider model row."""
    if not isinstance(row, Mapping):
        return None
    architecture = row.get("architecture")
    if isinstance(architecture, Mapping):
        modalities = architecture.get("input_modalities")
        if isinstance(modalities, (list, tuple)):
            return [str(item) for item in modalities]
        modality = architecture.get("modality")
        if isinstance(modality, str) and modality:
            # Legacy OpenRouter shape: "text+image->text".
            head = modality.split("->", 1)[0]
            return [part for part in head.split("+") if part]
    modalities = row.get("input_modalities")
    if isinstance(modalities, (list, tuple)):
        return [str(item) for item in modalities]
    return None


def supports_vision(
    slug: str | None,
    *,
    input_modalities: Iterable[Any] | None = None,
) -> bool:
    """True when *slug* can read image input.

    ``input_modalities`` (from the provider catalog) takes precedence; the
    family heuristic is the fallback for endpoints that declare nothing.
    """
    declared = _modalities_say_vision(input_modalities)
    if declared is not None:
        return declared

    cleaned = _normalize(slug)
    if not cleaned:
        return False
    # Drop an OpenRouter-style ":free" / ":nitro" variant suffix.
    base = cleaned.split(":", 1)[0]
    if any(marker in base for marker in _TEXT_ONLY_OVERRIDES):
        return False
    return any(marker in base for marker in _VISION_FAMILY_MARKERS)


def vision_flag_for_row(model_id: str | None, row: Mapping[str, Any] | None) -> bool:
    """Vision capability for a provider catalog row, declaration first."""
    return supports_vision(model_id, input_modalities=input_modalities_from_row(row))


# Families trained to answer with *pixel coordinates* on a screenshot (GUI
# grounding), which is what desktop / browser control needs on top of plain
# image understanding. A vision model outside this list still works, but it
# should lean on accessibility refs and shortcuts rather than guessed pixels.
_GROUNDING_FAMILY_MARKERS: tuple[str, ...] = (
    # Anthropic: computer use since Claude 3.5 Sonnet (new) / 3.7 / 4.x.
    "claude-3-5",
    "claude-3.5",
    "claude-3-7",
    "claude-3.7",
    "claude-4",
    "claude-5",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    "claude-sonnet",
    "claude-opus",
    # OpenAI: CUA / Operator lineage and the 4o / 4.1 / 5 generations.
    "computer-use",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o3",
    "o4-mini",
    # Google: Gemini 2.x+ (and the dedicated computer-use variants).
    "gemini-2",
    "gemini-3",
    "gemini-computer-use",
    # Open GUI agents and VLMs with explicit grounding training.
    "qwen2.5-vl",
    "qwen3-vl",
    "ui-tars",
    "showui",
    "os-atlas",
    "seeclick",
    "aguvis",
    "cogagent",
    "opencua",
    "ferret-ui",
    "holo1",
    "holo-1",
    "glm-4.1v",
    "glm-4.5v",
    "kimi-vl",
    "molmo",
    "gui-actor",
    "uground",
)


def supports_grounding(
    slug: str | None,
    *,
    input_modalities: Iterable[Any] | None = None,
) -> bool:
    """True when *slug* is known to output screen coordinates reliably.

    Grounding implies vision, so the family list is the answer unless the
    provider declared ``input_modalities`` without images: a model that cannot
    read a screenshot cannot aim at it, whatever its name says.
    """
    if _modalities_say_vision(input_modalities) is False:
        return False
    base = _normalize(slug).split(":", 1)[0]
    if not base or any(marker in base for marker in _TEXT_ONLY_OVERRIDES):
        return False
    return any(marker in base for marker in _GROUNDING_FAMILY_MARKERS)
