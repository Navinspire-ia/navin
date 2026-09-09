# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn raw provider errors into chat-safe user-facing text.

Never leak upstream brands (OpenRouter), docs URLs, or Python dict dumps
into the assistant bubble.
"""

from __future__ import annotations

import ast
import json
import re

_DEFAULT = "Sorry, I encountered an error calling the AI model."
_TOOL_USE = (
    "This model could not run with tools enabled "
    "(needed for Agent, Review, Debug, etc.). "
    "Ask mode may still work. Pick a tool-capable model "
    "and try again."
)
_VIDEO_AUTH = (
    "Video download failed due to an authentication error. Please try again."
)
_AUDIO_AUTH = (
    "Audio failed due to an authentication error. Please try again."
)
_MEDIA_EMPTY = (
    "The provider finished the job but produced no media. This is "
    "usually a content-policy block (brand names, logos, real product "
    "UI, people) or a transient provider failure. Rephrase the prompt "
    "with neutral wording and try again, or switch to another media "
    "model in Settings."
)
_RATE_LIMIT = (
    "The model is at capacity right now. Navin already retried "
    "automatically. Wait a few seconds and send your message again, "
    "or pick another model."
)
_QUOTA = (
    "Your own API key is out of credit. Top up at the provider, "
    "or switch to a Navin plan model."
)
_CONNECTION = (
    "Could not reach the model provider. Navin already retried "
    "automatically. Check your internet connection, and any VPN, "
    "proxy or firewall that could block the request, then send your "
    "message again."
)
_UPSTREAM = (
    "The model provider hit a temporary error. Navin already retried "
    "automatically. Send your message again, or pick another model."
)
_CONTEXT = (
    "This turn no longer fits in the model's context window. Start a "
    "new conversation, or pick a model with a larger window."
)
_AUTH = (
    "Authentication with the model provider failed. Check the API key "
    "in Settings, or switch to a Navin plan model."
)
_NOT_FOUND = (
    "This model is no longer available. Pick another model from the "
    "selector and try again."
)

_SENTINELS = frozenset(
    {
        _DEFAULT,
        _TOOL_USE,
        _VIDEO_AUTH,
        _AUDIO_AUTH,
        _MEDIA_EMPTY,
        _RATE_LIMIT,
        _QUOTA,
        _CONNECTION,
        _UPSTREAM,
        _CONTEXT,
        _AUTH,
        _NOT_FOUND,
    }
)
# Capacity / transport / unknown provider failures: another catalog model on
# the same plan can finish the turn. Auth, a too-long prompt, and a retired
# model cannot, so those stay out of the failover set.
_FAILOVER_SENTINELS = frozenset({_DEFAULT, _UPSTREAM, _RATE_LIMIT, _CONNECTION})
_HTTP_STATUS_RE = re.compile(r"\b([45]\d{2})\b")

_ERROR_PREFIX_RE = re.compile(r"^(?:error:\s*)+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_OPENROUTER_NAME_RE = re.compile(r"\bopenrouter\b", re.IGNORECASE)
_OPENROUTER_HOST_RE = re.compile(r"openrouter\.ai", re.IGNORECASE)
_DICTISH_RE = re.compile(
    r"^(?:Error:\s*)?\{['\"]message['\"].*\}\s*$",
    re.IGNORECASE | re.DOTALL,
)
_HTTP_5XX_RE = re.compile(r"\b(500|502|503|504)\b")
# Phrases raised by httpx / the OpenAI SDK when the request never reached the
# provider. Kept specific so an HTTP body merely mentioning a connection (an
# upstream 500, say) still surfaces its own message.
_CONNECTION_FAILURE_MARKERS = (
    "connection error",
    "connect error",
    "connection refused",
    "connection reset",
    "connection aborted",
    "all connection attempts failed",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "getaddrinfo failed",
    "failed to establish a new connection",
    "apiconnectionerror",
)
_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "max context",
    "prompt is too long",
    "prompt too long",
    "exceeds the model's context",
    "exceeds the context",
    "too many tokens",
)
_UPSTREAM_MARKERS = (
    "provider_unavailable",
    "provider unavailable",
    "upstream error",
    "internal server error",
    "service unavailable",
    "empty choices",
    "empty response",
    "no choices returned",
)


def _is_connection_failure(lower: str) -> bool:
    return any(marker in lower for marker in _CONNECTION_FAILURE_MARKERS)


def content_allows_model_switch(text: str | None) -> bool:
    """True when this chat-safe error is still worth trying on another model."""
    stripped = _strip_error_prefix((text or "").strip())
    return stripped in _FAILOVER_SENTINELS


def is_quota_error_text(text: str | None) -> bool:
    """True when *text* is the chat-safe rewrite of a credit / quota refusal."""
    return _strip_error_prefix((text or "").strip()) == _QUOTA


# "Error code: 402 - " / "402: " as the SDK and our own wrappers prefix bodies.
_STATUS_PREFIX_RE = re.compile(
    r"^(?:error(?:\s+code)?\s*:?\s*)?[45]\d{2}\s*[-:]\s*", re.IGNORECASE
)
_LLM_CALL_PREFIX_RE = re.compile(r"^error calling llm:\s*", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DETAIL_MAX_CHARS = 280


def _blob_message(data: dict) -> str | None:
    """The human sentence inside an error body, however the provider nests it."""
    err = data.get("error")
    if isinstance(err, dict):
        nested = _blob_message(err)
        if nested:
            return nested
    elif isinstance(err, str) and err.strip():
        return err.strip()
    for key in ("message", "detail", "msg"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    meta = data.get("metadata")
    if isinstance(meta, dict):
        raw = meta.get("raw")
        if isinstance(raw, str) and raw.strip():
            inner = _first_dict_blob(raw)
            nested = _blob_message(inner) if inner else None
            return nested or raw.strip()
    return None


def _without_links(message: str) -> str:
    """Drop the sentences that carry a URL; keep the message when it is one sentence."""
    if not _URL_RE.search(message):
        return message
    sentences = _SENTENCE_SPLIT_RE.split(message)
    kept = [s for s in sentences if not _URL_RE.search(s)]
    if kept:
        return " ".join(kept)
    return _URL_RE.sub("", message)


def provider_error_detail(raw: str | None) -> str | None:
    """What the provider itself said, safe to quote in the chat.

    Unlike :func:`user_facing_llm_error` this keeps the provider's wording
    ("Insufficient balance or no resource package. Please recharge.") so a
    refusal on the user's own key explains itself. Links (which may embed key
    ids), status prefixes and dict dumps are removed; our own sentinels yield
    ``None`` because they are not the provider's words.
    """
    text = (raw or "").strip()
    if not text:
        return None
    stripped = _strip_error_prefix(text)
    if stripped in _SENTINELS or text in _SENTINELS:
        return None
    blob = _first_dict_blob(text)
    if blob is not None:
        message = _blob_message(blob)
        if not message:
            return None
    else:
        message = stripped
    message = _LLM_CALL_PREFIX_RE.sub("", message)
    message = _STATUS_PREFIX_RE.sub("", message)
    message = _without_links(message)
    message = re.sub(r"\s+([.,;:!?])", r"\1", message)
    # One line: the chat card reads the detail from a single prefixed line.
    message = re.sub(r"\s+", " ", message).strip(" :,-")
    if not message or message.isdigit() or message.lower() in {"error", "error:"}:
        return None
    if len(message) > _DETAIL_MAX_CHARS:
        message = message[: _DETAIL_MAX_CHARS - 3].rstrip() + "..."
    return message


def http_status_from_error_payload(payload: object) -> int | None:
    """Best-effort HTTP status from an SDK body, dict dump, or error string."""
    if isinstance(payload, dict):
        return _status_from_blob(payload)
    if not isinstance(payload, str) or not payload.strip():
        return None
    blob = _first_dict_blob(payload)
    if blob:
        status = _status_from_blob(blob)
        if status is not None:
            return status
    match = _HTTP_STATUS_RE.search(payload)
    if match:
        return int(match.group(1))
    return None


def _strip_error_prefix(text: str) -> str:
    return _ERROR_PREFIX_RE.sub("", text).strip()


def _first_dict_blob(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = text[start : end + 1]
    for loader in (ast.literal_eval, json.loads):
        try:
            parsed = loader(blob)
        except (SyntaxError, ValueError, TypeError, RecursionError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _status_from_blob(data: dict) -> int | None:
    for key in ("code", "status", "status_code"):
        val = data.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, int) and 100 <= val <= 599:
            return val
        if isinstance(val, str) and val.isdigit():
            parsed = int(val)
            if 100 <= parsed <= 599:
                return parsed
    err = data.get("error")
    if isinstance(err, dict):
        return _status_from_blob(err)
    return None


def _blob_haystack(data: dict) -> str:
    parts: list[str] = []
    for key in ("message", "type", "code"):
        val = data.get(key)
        if val is not None:
            parts.append(str(val))
    err = data.get("error")
    if isinstance(err, dict):
        parts.append(_blob_haystack(err))
    elif err is not None:
        parts.append(str(err))
    meta = data.get("metadata")
    if isinstance(meta, dict):
        for key in ("raw", "error_type", "provider_error_code"):
            val = meta.get(key)
            if val is not None:
                parts.append(str(val))
    return " ".join(parts)


def _classify_error_text(lower: str) -> str | None:
    """Return a sentinel when *lower* matches a known failure class."""
    if "no endpoints found that support tool use" in lower:
        return _TOOL_USE
    if "support tool use" in lower and ("404" in lower or "no endpoints" in lower):
        return _TOOL_USE
    if "no user or org id found in auth cookie" in lower or (
        "auth cookie" in lower and ("401" in lower or "unauthorized" in lower)
    ):
        return _VIDEO_AUTH
    if "video download" in lower and ("401" in lower or "unauthorized" in lower):
        return _VIDEO_AUTH
    if "audio" in lower and (
        "download" in lower or "synthesis" in lower or "tts" in lower
    ) and ("401" in lower or "unauthorized" in lower or "auth cookie" in lower):
        return _AUDIO_AUTH
    if "completed with no output" in lower and (
        "video" in lower or "image" in lower or "music" in lower or "audio" in lower
    ):
        return _MEDIA_EMPTY
    if (
        "rate limit" in lower
        or "rate-limited" in lower
        or "ratelimited" in lower
        or "429" in lower
    ):
        return _RATE_LIMIT
    if "insufficient" in lower and ("credit" in lower or "quota" in lower):
        return _QUOTA
    if _is_connection_failure(lower):
        return _CONNECTION
    if any(marker in lower for marker in _CONTEXT_MARKERS):
        return _CONTEXT
    if (
        "invalid api key" in lower
        or "incorrect api key" in lower
        or "invalid_api_key" in lower
        or (
            ("401" in lower or "unauthorized" in lower)
            and ("api key" in lower or "authentication" in lower)
        )
    ):
        return _AUTH
    if any(marker in lower for marker in _UPSTREAM_MARKERS) or _HTTP_5XX_RE.search(
        lower
    ):
        return _UPSTREAM
    if (
        "404" in lower
        and ("not found" in lower or "no endpoints" in lower or "unknown model" in lower)
    ):
        return _NOT_FOUND
    return None


def user_facing_llm_error(raw: str | None) -> str:
    """Map a provider error string to something safe to show in chat."""
    text = (raw or "").strip()
    if not text:
        return _DEFAULT

    stripped = _strip_error_prefix(text)
    if stripped in _SENTINELS:
        return stripped
    if text in _SENTINELS:
        return text

    classified = _classify_error_text(text.lower())
    if classified:
        return classified

    blob = _first_dict_blob(text)
    if blob:
        haystack = f"{text} {_blob_haystack(blob)}".lower()
        classified = _classify_error_text(haystack)
        if classified:
            return classified
        status = _status_from_blob(blob)
        if status == 429:
            return _RATE_LIMIT
        if status in {500, 502, 503, 504}:
            return _UPSTREAM
        if status in {401, 403}:
            return _AUTH
        if status == 404:
            return _NOT_FOUND
        if status == 402:
            return _QUOTA

    if _DICTISH_RE.match(text) or (blob is not None and _DICTISH_RE.match(stripped)):
        return _DEFAULT

    cleaned = _URL_RE.sub("", text)
    cleaned = _OPENROUTER_HOST_RE.sub("the model provider", cleaned)
    cleaned = _OPENROUTER_NAME_RE.sub("the model provider", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" :,-")
    if not cleaned or cleaned.lower() in {"error", "error:"}:
        return _DEFAULT
    cleaned_stripped = _strip_error_prefix(cleaned)
    if cleaned_stripped in _SENTINELS:
        return cleaned_stripped
    if not cleaned.lower().startswith("error"):
        cleaned = f"Error: {cleaned}"
    return cleaned[:400]
