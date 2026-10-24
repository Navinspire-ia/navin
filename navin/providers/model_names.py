"""Readable names for Bedrock models.

Bedrock names carry the routing in them: "GLOBAL Anthropic Claude Sonnet 5.5",
"EU Anthropic ...", or IDs like ``global.anthropic.claude-sonnet-5-5-v1:0``.
The chat only needs the model: "Claude Sonnet 5.5", "GPT 6 Sol". The region
and the full ID stay in the configuration.
"""

from __future__ import annotations

import re

# Region scope of a cross-region inference profile (ID prefix / name prefix).
_REGION_ID = re.compile(r"^(?:global|eu|us|us-gov|apac|au|jp|ca)\.", re.IGNORECASE)
_REGION_NAME = re.compile(r"^(?:global|eu|us|us[- ]gov|apac|au|jp|ca)\s+", re.IGNORECASE)
_REGION_LABELS = {"global": "Global", "eu": "EU", "us": "US", "us-gov": "US Gov", "apac": "APAC",
                  "au": "AU", "jp": "JP", "ca": "CA"}
# Bedrock vendor segment of an ID (``anthropic.claude-...``).
_VENDOR_ID = re.compile(
    r"^(?:anthropic|amazon|meta|openai|mistral|moonshot|moonshotai|zai|z-ai|qwen|deepseek|minimax|cohere|writer|"
    r"google|nvidia|ai21|stability|twelvelabs|luma)\.",
    re.IGNORECASE,
)
# Company names in front of the model name ("Anthropic Claude ..." -> "Claude ...").
# Brands that ARE the model name (DeepSeek, MiniMax, Qwen, Mistral Large) stay.
_COMPANY_NAME = re.compile(
    r"^(?:anthropic|amazon|meta|openai|mistral ai|moonshot ai|moonshot|zhipu ai|z\.ai|cohere|writer|google|nvidia)"
    r"[\s:]+(?=\S)",
    re.IGNORECASE,
)
_WORD_CASE = {"gpt": "GPT", "glm": "GLM", "oss": "OSS", "ai": "AI", "llm": "LLM", "r1": "R1", "v3": "V3"}


def is_bedrock_model_id(model_id: str | None) -> bool:
    text = (model_id or "").strip().split("/")[-1]
    return bool(_REGION_ID.match(text) or _VENDOR_ID.match(text))


def bedrock_region_scope(model_id: str | None) -> str | None:
    """"Global", "EU", ... for an inference profile ID, None for a regional model."""
    match = _REGION_ID.match((model_id or "").strip().split("/")[-1])
    return _REGION_LABELS.get(match.group(0)[:-1].lower()) if match else None


def _name_from_id(model_id: str) -> str:
    text = model_id.strip().split("/")[-1]
    text = _REGION_ID.sub("", text)
    text = _VENDOR_ID.sub("", text)
    text = re.sub(r"(?:-v?\d+)?:\d+$", "", text)  # ...-v1:0, ...-1:0
    text = re.sub(r"-v\d+$", "", text)            # ...-v1
    text = re.sub(r"-\d{8}$", "", text)           # ...-20250929
    parts = [part for part in text.split("-") if part]
    words: list[str] = []
    for part in parts:
        if part.isdigit() and words and re.fullmatch(r"\d+(?:\.\d+)*", words[-1]):
            words[-1] = f"{words[-1]}.{part}"     # claude-sonnet-5-5 -> 5.5
            continue
        lower = part.lower()
        words.append(_WORD_CASE.get(lower) or (part if any(ch.isdigit() for ch in part[:1]) else part[:1].upper() + part[1:]))
    return " ".join(words) or model_id


def bedrock_display_name(model_id: str | None, label: str | None = None) -> str:
    """Clean chat name for a Bedrock model. Anything that is not Bedrock comes
    back unchanged (label first, else the ID)."""
    model_id = (model_id or "").strip()
    raw = (label or "").strip()
    if raw and is_bedrock_model_id(raw):
        return _name_from_id(raw)  # an ID typed or pasted as the name
    bedrock = is_bedrock_model_id(model_id) or bool(_REGION_NAME.match(raw))
    if not bedrock:
        return raw or model_id
    if raw and raw != model_id:
        name = _REGION_NAME.sub("", raw)
        name = _COMPANY_NAME.sub("", name).strip()
        if name:
            return name
    return _name_from_id(model_id) if model_id else raw
