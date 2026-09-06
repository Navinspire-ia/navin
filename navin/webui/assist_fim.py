"""Native fill-in-the-middle (FIM) paths for Tab / ghost-text completion.

Chat prompting with a ``<CURSOR>`` marker works everywhere but is slower and
less precise than provider-native FIM:

- Mistral Codestral: ``POST /v1/fim/completions`` with ``prompt`` + ``suffix``
- OpenAI-compatible coder models: ``POST /v1/completions`` with ``suffix``
- Local HF-style models (Ollama / llama.cpp): FIM special tokens in ``prompt``

Callers should fall back to the chat path when detection returns ``None`` or
the native call fails.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

FimMode = Literal["mistral_fim", "openai_completions", "fim_tokens"]

# HF / StarCoder / CodeLlama / Qwen-Coder family.
_HF_FIM_PREFIX = "<fim_prefix>"
_HF_FIM_SUFFIX = "<fim_suffix>"
_HF_FIM_MIDDLE = "<fim_middle>"

# DeepSeek-Coder token layout (used when the completions suffix path is
# unavailable, e.g. some OpenRouter routes).
_DEEPSEEK_FIM_BEGIN = "<｜fim▁begin｜>"
_DEEPSEEK_FIM_HOLE = "<｜fim▁hole｜>"
_DEEPSEEK_FIM_END = "<｜fim▁end｜>"


class FimError(Exception):
    """Native FIM call failed; caller should fall back to chat completion."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def resolve_fim_mode(model: str, api_base: str | None = None) -> FimMode | None:
    """Return the best native FIM mode for ``model``, or ``None`` if chat-only."""
    name = (model or "").strip().lower()
    if not name:
        return None
    base = api_base.strip().lower() if isinstance(api_base, str) else ""

    # Codestral exposes a dedicated FIM endpoint on Mistral (and mirrors).
    if "codestral" in name:
        return "mistral_fim"

    # DeepSeek coder checkpoints accept ``suffix`` on /v1/completions.
    if "deepseek-coder" in name or "deepseek/deepseek-coder" in name:
        return "openai_completions"

    # Common local / HF fill-in-the-middle checkpoints.
    localish = (
        "11434" in base
        or "ollama" in base
        or "localhost" in base
        or "127.0.0.1" in base
        or ":8080" in base
    )
    token_models = (
        "starcoder",
        "codellama",
        "codegemma",
        "qwen2.5-coder",
        "qwen-coder",
        "qwen2-coder",
    )
    if any(token in name for token in token_models):
        return "fim_tokens" if localish else "openai_completions"

    return None


def build_fim_token_prompt(prefix: str, suffix: str, *, model: str = "") -> str:
    """Build a single-string FIM prompt using model-appropriate special tokens."""
    name = (model or "").lower()
    if "deepseek" in name:
        return (
            f"{_DEEPSEEK_FIM_BEGIN}{prefix}"
            f"{_DEEPSEEK_FIM_HOLE}{suffix}"
            f"{_DEEPSEEK_FIM_END}"
        )
    return f"{_HF_FIM_PREFIX}{prefix}{_HF_FIM_SUFFIX}{suffix}{_HF_FIM_MIDDLE}"


def normalize_openai_base(api_base: str) -> str:
    """Normalize an OpenAI-compatible base URL (no trailing slash)."""
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise FimError("missing api_base for FIM")
    return base


def fim_endpoint(api_base: str, mode: FimMode) -> str:
    """Absolute URL for the native FIM / completions call."""
    base = normalize_openai_base(api_base)
    if mode == "mistral_fim":
        return f"{base}/fim/completions"
    return f"{base}/completions"


def _auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key and key != "no-key":
        headers["Authorization"] = f"Bearer {key}"
    return headers


def build_fim_body(
    *,
    mode: FimMode,
    model: str,
    prefix: str,
    suffix: str,
    max_tokens: int,
    stream: bool = False,
) -> dict[str, Any]:
    """JSON body for a native FIM request."""
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max(1, int(max_tokens)),
        "temperature": 0.0,
        "stream": bool(stream),
    }
    if mode == "fim_tokens":
        body["prompt"] = build_fim_token_prompt(prefix, suffix, model=model)
    else:
        body["prompt"] = prefix
        body["suffix"] = suffix
    return body


def extract_fim_text(payload: dict[str, Any]) -> str:
    """Pull completion text out of an OpenAI/Mistral-style response body."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    text = first.get("text")
    if isinstance(text, str):
        return text
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def extract_fim_stream_delta(payload: dict[str, Any]) -> str:
    """Pull an incremental text delta from a streamed FIM chunk."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    text = first.get("text")
    if isinstance(text, str) and text:
        return text
    delta = first.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
        text = delta.get("text")
        if isinstance(text, str):
            return text
    return ""


async def fim_complete(
    *,
    api_base: str,
    api_key: str | None,
    model: str,
    prefix: str,
    suffix: str,
    max_tokens: int,
    mode: FimMode,
    timeout_s: float,
) -> str:
    """One-shot native FIM call. Raises ``FimError`` on transport/API failure."""
    import httpx

    url = fim_endpoint(api_base, mode)
    body = build_fim_body(
        mode=mode,
        model=model,
        prefix=prefix,
        suffix=suffix,
        max_tokens=max_tokens,
        stream=False,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(url, headers=_auth_headers(api_key), json=body)
    except httpx.TimeoutException as exc:
        raise FimError("FIM timed out") from exc
    except httpx.HTTPError as exc:
        raise FimError(f"FIM request failed: {exc}") from exc

    if response.status_code >= 400:
        detail = (response.text or "")[:200]
        raise FimError(f"FIM HTTP {response.status_code}: {detail}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise FimError("FIM returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise FimError("FIM returned unexpected JSON")
    return extract_fim_text(payload)


async def fim_complete_stream(
    *,
    api_base: str,
    api_key: str | None,
    model: str,
    prefix: str,
    suffix: str,
    max_tokens: int,
    mode: FimMode,
    timeout_s: float,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Stream a native FIM call; returns the full text."""
    import httpx

    url = fim_endpoint(api_base, mode)
    body = build_fim_body(
        mode=mode,
        model=model,
        prefix=prefix,
        suffix=suffix,
        max_tokens=max_tokens,
        stream=True,
    )
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            async with client.stream(
                "POST",
                url,
                headers=_auth_headers(api_key),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", errors="replace")[:200]
                    raise FimError(f"FIM HTTP {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                    else:
                        data = line.strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    delta = extract_fim_stream_delta(payload)
                    if not delta:
                        continue
                    chunks.append(delta)
                    if on_delta is not None:
                        await on_delta(delta)
    except FimError:
        raise
    except httpx.TimeoutException as exc:
        raise FimError("FIM timed out") from exc
    except httpx.HTTPError as exc:
        raise FimError(f"FIM request failed: {exc}") from exc
    return "".join(chunks)


def _direct_attr(obj: Any, name: str) -> Any:
    """Read a real attribute without triggering ``unittest.mock`` auto-children."""
    try:
        return object.__getattribute__(obj, name)
    except AttributeError:
        return None


def provider_fim_credentials(provider: Any) -> tuple[str | None, str | None]:
    """Unwrap fallback wrappers and return ``(api_base, api_key)``."""
    current = provider
    # FallbackProvider keeps the live credentials on ``_primary``.
    for _ in range(3):
        primary = _direct_attr(current, "_primary")
        if primary is None:
            break
        current = primary
    api_base = _direct_attr(current, "api_base") or _direct_attr(
        current, "_effective_base"
    )
    api_key = _direct_attr(current, "api_key")
    if isinstance(api_base, str):
        api_base = api_base.strip() or None
    else:
        api_base = None
    if isinstance(api_key, str):
        api_key = api_key.strip() or None
    else:
        api_key = None
    return api_base, api_key
