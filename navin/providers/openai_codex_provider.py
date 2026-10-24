# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenAI Codex Responses Provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from loguru import logger
from oauth_cli_kit import get_token as get_codex_token
from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
from oauth_cli_kit.storage import FileTokenStorage

from navin.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    resolve_stream_idle_timeout_s,
)
from navin.providers.openai_responses import (
    consume_sse_with_reasoning,
    convert_messages,
    convert_tools,
)
from navin.providers.reasoning_control import (
    accepted_efforts,
    learn_accepted_efforts,
    nearest_effort,
    non_anthropic_effort,
)

DEFAULT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_ORIGINATOR = "navin"


def codex_token_storage() -> FileTokenStorage:
    """Navin's own Codex OAuth token store.

    ``import_codex_cli=False`` is the whole point: by default oauth_cli_kit
    silently adopts ``~/.codex/auth.json`` from the official Codex CLI, which
    made a fresh Navin install claim "Signed in" (and copy that token) when
    the user had never configured anything here. Navin only trusts tokens
    obtained through its own login flow.
    """
    return FileTokenStorage(
        token_filename=OPENAI_CODEX_PROVIDER.token_filename,
        import_codex_cli=False,
    )


class OpenAICodexProvider(LLMProvider):
    """Use Codex OAuth to call the Responses API."""

    supports_progress_deltas = True

    def __init__(
        self,
        default_model: str = "",
        proxy: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ):
        super().__init__(api_key=None, api_base=None)
        self.default_model = default_model
        self.proxy = proxy or None
        self._extra_body = dict(extra_body or {})

    async def _call_codex(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        learn: bool = True,
    ) -> LLMResponse:
        """Shared request logic for both chat() and chat_stream()."""
        model = model or self.default_model
        system_prompt, input_items = convert_messages(messages)

        body: dict[str, Any] = {
            "model": _strip_model_prefix(model),
            "store": False,
            "stream": True,
            "instructions": system_prompt,
            "input": input_items,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": _prompt_cache_key(messages[:2]),
            "tool_choice": tool_choice or "auto",
            "parallel_tool_calls": True,
        }
        reasoning_options = _build_reasoning_options(reasoning_effort, _strip_model_prefix(model))
        if reasoning_options:
            body["reasoning"] = reasoning_options
        if tools:
            body["tools"] = convert_tools(tools)
        if self._extra_body:
            # Apply explicit provider overrides last, matching other provider backends.
            body.update(self._extra_body)

        stage = "oauth_token"
        try:
            token = await asyncio.to_thread(
                get_codex_token, storage=codex_token_storage(), proxy=self.proxy
            )
            headers = _build_headers(token.account_id, token.access)

            stage = "codex_request"
            # TLS verification is never disabled, even on CERTIFICATE_VERIFY_FAILED.
            # Failing closed prevents an on-path MITM (captive portal, corporate
            # TLS intercept) from receiving the OAuth account_id / access token.
            # Corporate CAs are honored via SSL_CERT_FILE (default ssl context).
            try:
                content, tool_calls, finish_reason, usage, reasoning_content = await _request_codex(
                    DEFAULT_CODEX_URL, headers, body, verify=_default_ssl_context(),
                    proxy=self.proxy,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            except Exception as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    logger.error(
                        "SSL certificate verification failed for Codex API; refusing to "
                        "send credentials over an unverified connection. Configure a "
                        "corporate CA via SSL_CERT_FILE or proxy settings."
                    )
                raise
            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                reasoning_content=reasoning_content,
            )
        except Exception as e:
            if learn and self._learned_effort(model, reasoning_effort, e):
                # A refused effort named the levels this model takes: replay
                # once with the nearest of them, never with the same value.
                return await self._call_codex(
                    messages, tools, model, reasoning_effort, tool_choice,
                    on_content_delta, on_thinking_delta, on_tool_call_delta,
                    learn=False,
                )
            response = _codex_error_response(e)
            exc_type = "CodexHTTPError" if isinstance(e, _CodexHTTPError) else type(e).__name__
            logger.warning(
                "Codex API request failed: stage={} type={} kind={} retryable={} status={} "
                "error_type={} error_code={} retry_after={} summary={}",
                stage,
                exc_type,
                response.error_kind,
                response.error_should_retry,
                response.error_status_code,
                response.error_type,
                response.error_code,
                response.retry_after,
                _codex_log_summary(exc_type, response),
            )
            return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
        model: str | None = None, max_tokens: int = 4096, temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        return await self._call_codex(messages, tools, model, reasoning_effort, tool_choice)

    async def chat_stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
        model: str | None = None, max_tokens: int = 4096, temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        args = (
            messages,
            tools,
            model,
            reasoning_effort,
            tool_choice,
            on_content_delta,
            on_thinking_delta,
            on_tool_call_delta,
        )
        return await self._call_codex(*args)

    @staticmethod
    def _learned_effort(model: str, reasoning_effort: str | None, error: Exception) -> bool:
        """True when a 400 refused the effort and listed the accepted ones."""
        if not reasoning_effort or not isinstance(error, _CodexHTTPError):
            return False
        if error.status_code != 400 or not error.body:
            return False
        effort = (non_anthropic_effort(reasoning_effort) or "").lower()
        return learn_accepted_efforts(
            _effort_key(_strip_model_prefix(model)), effort, error.body,
        )

    def get_default_model(self) -> str:
        return self.default_model


def _default_ssl_context() -> ssl.SSLContext:
    """Default SSL context honoring SSL_CERT_FILE (corporate CA bundles)."""
    return ssl.create_default_context()


def _strip_model_prefix(model: str) -> str:
    if model.startswith("openai-codex/") or model.startswith("openai_codex/"):
        return model.split("/", 1)[1]
    return model


def _build_reasoning_options(
    reasoning_effort: str | None, model: str = "",
) -> dict[str, str] | None:
    """Opt in to visible summaries without changing provider-default effort.

    The effort goes out as a value the model takes: Codex models have no
    "none" (their floor is "low") and GPT-5 has no "xhigh"; the documented
    ladder, or what a refusal taught, picks the nearest accepted level.
    """
    reasoning_effort = non_anthropic_effort(reasoning_effort)
    if reasoning_effort:
        reasoning_effort = nearest_effort(
            reasoning_effort.lower(), accepted_efforts(model, _effort_key(model)),
        )
    if reasoning_effort and reasoning_effort.lower() == "none":
        return {"effort": "none"}
    options = {"summary": "auto"}
    if reasoning_effort:
        options["effort"] = reasoning_effort
    return options


def _effort_key(model: str) -> str:
    return f"openai_codex|{model.lower()}"


def _build_headers(account_id: str, token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": DEFAULT_ORIGINATOR,
        "User-Agent": "navin (python)",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }


class _CodexHTTPError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        should_retry: bool | None = None,
        body: str = "",
    ):
        super().__init__(message)
        # Upstream payload, kept off the user-facing message; read only to
        # learn which effort values a refusal accepts.
        self.body = body
        self.status_code = status_code
        self.retry_after = retry_after
        self.error_type = error_type
        self.error_code = error_code
        self.should_retry = should_retry


# One pooled client per (verify, proxy): a fresh TCP+TLS handshake costs
# 130-230 ms per model step. The short idle window spans one turn's
# model -> tools -> model gap; a stale socket is replayed once below.
_CODEX_KEEPALIVE_S = 4.0
# Keyed by event loop too: an httpx pool must not cross loops (tests, CLI).
_CODEX_CLIENTS: dict[tuple[int, bool, str | None], httpx.AsyncClient] = {}


def _codex_client_key(verify: bool, proxy: str | None) -> tuple[int, bool, str | None]:
    return (id(asyncio.get_running_loop()), verify, proxy)


def _codex_client(verify: bool, proxy: str | None, timeout: float) -> httpx.AsyncClient:
    key = _codex_client_key(verify, proxy)
    client = _CODEX_CLIENTS.get(key)
    if client is None or client.is_closed:
        client_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "verify": verify,
            "limits": httpx.Limits(keepalive_expiry=_CODEX_KEEPALIVE_S),
        }
        if proxy:
            client_kwargs["proxy"] = proxy
            client_kwargs["trust_env"] = False
        client = httpx.AsyncClient(**client_kwargs)
        _CODEX_CLIENTS[key] = client
    return client


async def _drop_codex_client(verify: bool, proxy: str | None) -> None:
    client = _CODEX_CLIENTS.pop(_codex_client_key(verify, proxy), None)
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            logger.debug("codex client close failed", exc_info=True)


async def _request_codex(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    verify: bool,
    proxy: str | None = None,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
    on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, list[ToolCallRequest], str, dict[str, int], str | None]:
    idle_timeout_s = resolve_stream_idle_timeout_s()
    delivered = False

    def _tracked(callback):
        if callback is None:
            return None

        async def _forward(value):
            nonlocal delivered
            delivered = True
            await callback(value)

        return _forward

    tracked = (
        _tracked(on_content_delta),
        _tracked(on_thinking_delta),
        _tracked(on_tool_call_delta),
    )
    for attempt in (0, 1):
        client = _codex_client(verify, proxy, idle_timeout_s)
        try:
            return await _stream_codex(client, url, headers, body, *tracked)
        except httpx.TimeoutException:
            await _drop_codex_client(verify, proxy)
            raise
        except httpx.TransportError:
            await _drop_codex_client(verify, proxy)
            if attempt or delivered:
                raise
            logger.info("Pooled Codex connection was stale; replaying on a fresh socket")
    raise AssertionError("unreachable")


async def _stream_codex(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    on_content_delta: Callable[[str], Awaitable[None]] | None,
    on_thinking_delta: Callable[[str], Awaitable[None]] | None,
    on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> tuple[str, list[ToolCallRequest], str, dict[str, int], str | None]:
    async with client.stream("POST", url, headers=headers, json=body) as response:
        if response.status_code != 200:
            text = await response.aread()
            raw = text.decode("utf-8", "ignore")
            retry_after = LLMProvider._extract_retry_after_from_headers(response.headers)
            error_type, error_code = LLMProvider._extract_error_type_code(raw)
            raise _CodexHTTPError(
                _friendly_error(response.status_code, raw),
                status_code=response.status_code,
                retry_after=retry_after,
                error_type=error_type,
                error_code=error_code,
                should_retry=_should_retry_status(response.status_code, error_type, error_code, raw),
                body=raw[:4000],
            )
        return await consume_sse_with_reasoning(
            response,
            on_content_delta=on_content_delta,
            on_tool_call_delta=on_tool_call_delta,
            on_reasoning_delta=on_thinking_delta,
        )


def _prompt_cache_key(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _friendly_error(status_code: int, raw: str) -> str:
    _ = raw
    if status_code == 429:
        return "ChatGPT usage quota exceeded or rate limit triggered. Please try again later."
    return f"HTTP {status_code}: Codex API request failed"


def _codex_error_response(exc: Exception) -> LLMResponse:
    """Convert Codex transport/API failures into actionable, retryable metadata."""
    exc_type = "CodexHTTPError" if isinstance(exc, _CodexHTTPError) else type(exc).__name__
    detail = str(exc).strip()

    status_code = getattr(exc, "status_code", None)
    error_kind: str | None = None
    default_detail: str | None = None
    should_retry: bool | None = getattr(exc, "should_retry", None)

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        error_kind = "timeout"
        default_detail = "timed out waiting for response"
        should_retry = True if should_retry is None else should_retry
    elif isinstance(exc, httpx.RemoteProtocolError):
        error_kind = "connection"
        default_detail = "network protocol error while reading response"
        should_retry = True if should_retry is None else should_retry
    elif isinstance(exc, (httpx.NetworkError, httpx.TransportError)):
        error_kind = "connection"
        default_detail = "network connection failed"
        should_retry = True if should_retry is None else should_retry
    elif isinstance(exc, _CodexHTTPError):
        error_kind = "http"
        default_detail = "HTTP request failed"

    if status_code is not None and should_retry is None:
        retry_content = None if int(status_code) == 429 and isinstance(exc, _CodexHTTPError) else detail
        should_retry = _should_retry_status(
            int(status_code),
            getattr(exc, "error_type", None),
            getattr(exc, "error_code", None),
            retry_content,
        )

    detail = detail or default_detail or "unexpected error"
    message = f"Error calling Codex ({exc_type}): {detail}"
    retry_after = getattr(exc, "retry_after", None) or LLMProvider._extract_retry_after(message)
    return LLMResponse(
        content=message,
        finish_reason="error",
        retry_after=retry_after,
        error_status_code=int(status_code) if status_code is not None else None,
        error_kind=error_kind,
        error_type=getattr(exc, "error_type", None),
        error_code=getattr(exc, "error_code", None),
        error_retry_after_s=retry_after,
        error_should_retry=should_retry,
    )


def _codex_log_summary(exc_type: str, response: LLMResponse) -> str:
    """Return a bounded diagnostic summary without request body or raw upstream payload."""
    if response.error_status_code is not None:
        parts = [f"HTTP {response.error_status_code}"]
        if response.error_type:
            parts.append(f"type={response.error_type}")
        if response.error_code:
            parts.append(f"code={response.error_code}")
        return " ".join(parts)

    kind = (response.error_kind or "").strip()
    if kind:
        return f"{exc_type} {kind}"

    return exc_type


def _should_retry_status(
    status_code: int,
    error_type: str | None,
    error_code: str | None,
    content: str | None,
) -> bool:
    if status_code == 429:
        return LLMProvider._is_retryable_429_response(
            LLMResponse(
                content=content or "",
                finish_reason="error",
                error_status_code=status_code,
                error_type=error_type,
                error_code=error_code,
            )
        )
    return status_code in LLMProvider._RETRYABLE_STATUS_CODES or status_code >= 500
