# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenAI-compatible provider for all non-Anthropic LLM APIs."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import secrets
import string
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from loguru import logger
from pydantic.alias_generators import to_snake

from navin.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    parse_tool_arguments,
    resolve_stream_idle_timeout_s,
    tool_arguments_json_for_replay,
)
from navin.providers.openai_responses import (
    consume_sdk_stream,
    convert_messages,
    convert_tools,
    parse_response_output,
)
from navin.providers.reasoning_control import (
    REJECT_UNSUPPORTED,
    WIRE_CHAT,
    WIRE_RESPONSES,
    ReasoningOffNegotiator,
    always_reasons,
    apply_shape,
    classify_rejection,
    off_shapes,
)
from navin.providers.session_affinity import current_session_id

if TYPE_CHECKING:
    from openai import AsyncOpenAI as AsyncOpenAIType

    from navin.providers.registry import ProviderSpec

# Module-level placeholder - set lazily by _ensure_client on first real
# use, or replaced by tests via ``patch(...)``.  Kept as a plain name so
# that ``unittest.mock.patch`` can find and replace it.
AsyncOpenAI: Any = None

_ALLOWED_MSG_KEYS = frozenset({
    "role", "content", "tool_calls", "tool_call_id", "name",
    "reasoning_content", "extra_content",
})
_ALNUM = string.ascii_letters + string.digits

_STANDARD_TC_KEYS = frozenset({"id", "type", "index", "function"})

# OpenRouter 404s non-OpenAI hosts when a function tool is literally named
# "apply_patch" ("Try disabling apply_patch"). Wire it as file_patch outbound
# and map the name back inbound so Agent/Review keep working on Muse, etc.
_OR_APPLY_PATCH_LOCAL = "apply_patch"
_OR_APPLY_PATCH_WIRE = "file_patch"
_STANDARD_FN_KEYS = frozenset({"name", "arguments"})
_DEFAULT_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/Navinspire-ia/navin",
    "X-OpenRouter-Title": "Navin",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}
_KIMI_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.5",
    "kimi-k2.6",
    "kimi-k2.7",
    "kimi-k2.7-code",
    "kimi-k2.7-code-highspeed",
    "k2.6-code-preview",
})
_KIMI_ALWAYS_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.7-code",
    "kimi-k2.7-code-highspeed",
})
_TEXT_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
# Thinking-capable MiMo models per Xiaomi docs (see
# tests/providers/test_xiaomi_mimo_thinking.py). mimo-v2-flash is omitted
# because it does not support thinking.
_MIMO_THINKING_MODELS: frozenset[str] = frozenset({
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "mimo-v2-pro",
    "mimo-v2-omni",
})
_OPENAI_COMPAT_REQUEST_TIMEOUT_S = 120.0
# GLM is served from two hosts with two key namespaces - open.bigmodel.cn for
# mainland China and api.z.ai elsewhere - but it is one API, so quirks keyed on
# the provider have to name both.
_GLM_PROVIDERS = frozenset({"zai", "zhipu"})

# Maps ProviderSpec.thinking_style → extra_body builder.
# Each builder takes a bool (thinking_enabled) and returns the dict to
# merge into extra_body, keeping the style→wire-format mapping in one place.
_THINKING_STYLE_MAP: dict[str, Any] = {
    "thinking_type": lambda on: {"thinking": {"type": "enabled" if on else "disabled"}},
    "enable_thinking": lambda on: {"enable_thinking": on},
    "reasoning_split": lambda on: {"reasoning_split": on},
}
_GATEWAY_REASONING_STYLE_MAP: dict[str, Any] = {
    "reasoning_effort": lambda effort: {"reasoning": {"effort": effort}},
}
# Thinking-capable Qwen models keyed by model rather than by provider, so the
# right toggle also reaches them through gateways instead of only DashScope.
_QWEN_THINKING_MODELS: frozenset[str] = frozenset({
    "qwen3.8-max",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-max-preview",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen3.5-plus",
    "qwen3.5-flash",
})
_MODEL_THINKING_STYLES: dict[str, str] = {
    **dict.fromkeys(_KIMI_THINKING_MODELS, "thinking_type"),
    **dict.fromkeys(_MIMO_THINKING_MODELS, "thinking_type"),
    **dict.fromkeys(_QWEN_THINKING_MODELS, "enable_thinking"),
}


def _model_slug(model_name: str) -> str:
    return model_name.lower().rsplit("/", 1)[-1]


def _provider_prefix_key(name: str) -> str:
    return to_snake(name.replace("-", "_")).lower()


def _requires_max_completion_tokens(model_name: str) -> bool:
    """Return True for models that reject ``max_tokens`` (GPT-5 family, o-series)."""
    slug = _model_slug(model_name)
    return "gpt-5" in slug or any(
        slug == p or slug.startswith((p + "-", p + ".")) for p in ("o1", "o3", "o4")
    )


def _model_thinking_style(model_name: str) -> str:
    return _MODEL_THINKING_STYLES.get(_model_slug(model_name), "")


def _thinking_styles_for(spec: ProviderSpec | None, model_name: str) -> list[str]:
    styles: list[str] = []
    if spec and spec.thinking_style:
        styles.append(spec.thinking_style)
    model_style = _model_thinking_style(model_name)
    if model_style and model_style not in styles:
        styles.append(model_style)
    return styles


def _thinking_extra_body(style: str, thinking_enabled: bool) -> dict[str, Any] | None:
    builder = _THINKING_STYLE_MAP.get(style)
    return builder(thinking_enabled) if builder else None


def _gateway_reasoning_extra_body(style: str, effort: str | None) -> dict[str, Any] | None:
    if not effort:
        return None
    builder = _GATEWAY_REASONING_STYLE_MAP.get(style)
    return builder(effort) if builder else None


def _openai_compat_timeout_s() -> float:
    """Return the bounded request timeout used for OpenAI-compatible providers."""
    return _float_env("NAVIN_OPENAI_COMPAT_TIMEOUT_S", _OPENAI_COMPAT_REQUEST_TIMEOUT_S)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid {}={!r}; using {}", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive {}={!r}; using {}", name, raw, default)
        return default
    return value


def _short_tool_id() -> str:
    """9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _extract_text_tool_calls(content: str | None) -> tuple[str | None, list[ToolCallRequest]]:
    """Normalize common text-format tool call blocks into structured calls."""
    if not content or "<tool_call>" not in content:
        return content, []

    tool_calls: list[ToolCallRequest] = []
    spans: list[tuple[int, int]] = []
    for match in _TEXT_TOOL_CALL_RE.finditer(content):
        try:
            payload = json.loads(_strip_json_fence(match.group(1)))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        nested = payload.get("tool_call")
        if isinstance(nested, dict):
            payload = nested
        function = payload.get("function")
        if not isinstance(function, dict):
            function = payload
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue

        arguments = function.get("arguments", payload.get("arguments", {}))
        tool_calls.append(ToolCallRequest(
            id=str(payload.get("id") or _short_tool_id()),
            name=name,
            arguments=parse_tool_arguments(arguments),
        ))
        spans.append(match.span())

    if not tool_calls:
        return content, []

    visible_parts: list[str] = []
    last = 0
    for start, end in spans:
        visible_parts.append(content[last:start])
        last = end
    visible_parts.append(content[last:])
    visible_content = "".join(visible_parts).strip() or None
    return visible_content, tool_calls


def _get(obj: Any, key: str) -> Any:
    """Get a value from dict or object attribute, returning None if absent."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    """Try to coerce *value* to a dict; return None if not possible or empty."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value if value else None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict) and dumped:
            return dumped
    return None


def _extract_tc_extras(tc: Any) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Extract (extra_content, provider_specific_fields, fn_provider_specific_fields).

    Works for both SDK objects and dicts.  Captures Gemini ``extra_content``
    verbatim and any non-standard keys on the tool-call / function.
    """
    extra_content = _coerce_dict(_get(tc, "extra_content"))

    tc_dict = _coerce_dict(tc)
    prov = None
    fn_prov = None
    if tc_dict is not None:
        leftover = {k: v for k, v in tc_dict.items()
                    if k not in _STANDARD_TC_KEYS and k != "extra_content" and v is not None}
        if leftover:
            prov = leftover
        fn = _coerce_dict(tc_dict.get("function"))
        if fn is not None:
            fn_leftover = {k: v for k, v in fn.items()
                          if k not in _STANDARD_FN_KEYS and v is not None}
            if fn_leftover:
                fn_prov = fn_leftover
    else:
        prov = _coerce_dict(_get(tc, "provider_specific_fields"))
        fn_obj = _get(tc, "function")
        if fn_obj is not None:
            fn_prov = _coerce_dict(_get(fn_obj, "provider_specific_fields"))

    return extra_content, prov, fn_prov


def _uses_openrouter_attribution(spec: "ProviderSpec | None", api_base: str | None) -> bool:
    """Apply Navin attribution headers to OpenRouter requests by default."""
    if spec and spec.name == "openrouter":
        return True
    return bool(api_base and "openrouter" in api_base.lower())


_RESPONSES_FAILURE_THRESHOLD = 3
_RESPONSES_PROBE_INTERVAL_S = 300  # 5 minutes


def _is_local_endpoint(
    spec: "ProviderSpec | None",
    api_base: str | None,
) -> bool:
    """Return True when the endpoint is a local or LAN model server.

    Matches either the provider spec's ``is_local`` flag or common private-
    network patterns in the base URL (localhost, 127.x, 192.168.x, 10.x,
    172.16-31.x, Docker ``host.docker.internal``).
    """
    if spec and spec.is_local:
        return True
    if not api_base:
        return False
    raw = api_base.strip().lower()
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    try:
        host = parsed.hostname
    except ValueError:
        return False
    if host in {"localhost", "host.docker.internal"}:
        return True
    if not host:
        return False
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def _is_direct_openai_base(api_base: str | None) -> bool:
    """Return True for direct OpenAI endpoints, not generic OpenAI-compatible gateways."""
    if not api_base:
        return True
    normalized = api_base.strip().lower().rstrip("/")
    return "api.openai.com" in normalized and "openrouter" not in normalized


def _responses_circuit_key(
    model: str | None,
    default_model: str,
    reasoning_effort: str | None,
) -> str:
    model_name = (model or default_model).lower()
    effort = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
    return f"{model_name}:{effort}"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    Nested dicts are merged key-by-key; all other types in *override*
    replace the corresponding key in *base*.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_unique_list(base: Any, override: Any) -> Any:
    """Append list values while preserving order and removing duplicates."""
    if not isinstance(base, list) or not isinstance(override, list):
        return override
    result: list[Any] = []
    seen: set[str] = set()
    for value in [*base, *override]:
        try:
            key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except Exception:
            key = repr(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _merge_responses_extra_body(
    body: dict[str, Any],
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    """Merge configured Responses API body fields without clobbering tools."""
    reserved = {"include", "tools"}
    regular_extra = {key: value for key, value in extra_body.items() if key not in reserved}
    merged = _deep_merge(body, regular_extra)

    if "include" in extra_body:
        merged["include"] = _merge_unique_list(body.get("include"), extra_body["include"])

    if "tools" in extra_body:
        current_tools = body.get("tools")
        configured_tools = extra_body["tools"]
        if isinstance(current_tools, list) and isinstance(configured_tools, list):
            merged["tools"] = [*current_tools, *configured_tools]
        else:
            merged["tools"] = configured_tools

    return merged


class OpenAICompatProvider(LLMProvider):
    """Unified provider for all OpenAI-compatible APIs.

    Receives a resolved ``ProviderSpec`` from the caller - no internal
    registry lookups needed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        # No vendor default on purpose: the factory always passes the model.
        default_model: str = "",
        extra_headers: dict[str, str] | None = None,
        spec: ProviderSpec | None = None,
        extra_body: dict[str, Any] | None = None,
        api_type: str = "auto",
        extra_query: dict[str, str] | None = None,
        proxy: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._spec = spec
        self._extra_body = extra_body or {}
        self._api_type = api_type if spec and spec.name == "openai" else "auto"
        self._extra_query = extra_query or {}
        self._proxy = proxy or None

        if api_key and spec and spec.env_key:
            self._setup_env(api_key, api_base)

        effective_base = api_base or (spec.default_api_base if spec else None) or None
        self._effective_base = effective_base
        self._default_headers = {"x-session-affinity": uuid.uuid4().hex}
        self._is_openrouter = _uses_openrouter_attribution(spec, effective_base)
        if self._is_openrouter:
            self._default_headers.update(_DEFAULT_OPENROUTER_HEADERS)
        if extra_headers:
            self._default_headers.update(extra_headers)
        self._api_key_for_client = api_key or "no-key"
        self._is_local = _is_local_endpoint(spec, effective_base)

        # Lazy-init: the OpenAI client and its httpx transport are expensive
        # to create (~700 ms on Windows). Defer until first use.
        self._client: AsyncOpenAIType | None = None
        self._client_lock = asyncio.Lock()

        # Responses API circuit breaker: skip after repeated failures,
        # probe again after _RESPONSES_PROBE_INTERVAL_S seconds.
        self._responses_failures: dict[str, int] = {}
        self._responses_tripped_at: dict[str, float] = {}

        # Models observed rejecting the temperature parameter at runtime
        # (e.g. Anthropic "temperature is deprecated", OpenRouter 404 with
        # require_parameters). One retry without it, then remembered here.
        self._no_temperature_models: set[str] = set()
        # Models where require_parameters=true found no tool-capable endpoint.
        # Retry once without that filter (still keep the apply_patch wire alias).
        self._no_require_parameters_models: set[str] = set()
        # How far down the "no thinking" ladder each model pushed us, learned
        # from its refusals; see reasoning_control for why omission is not
        # an option.
        self._reasoning_off = ReasoningOffNegotiator(
            scope=effective_base or self.api_base or self.__class__.__name__
        )
        # Models that rejected the reasoning_effort parameter itself, at any
        # level. They reason (or not) on their own terms; stop sending it.
        self._reasoning_knob_rejected: set[str] = set()

    def _build_client(self) -> None:
        """Create the OpenAI client using the current module-level AsyncOpenAI."""
        import httpx

        timeout_s = _openai_compat_timeout_s()
        request_timeout = httpx.Timeout(
            connect=min(30.0, timeout_s),
            read=timeout_s,
            write=timeout_s,
            pool=30.0,
        )
        http_client: httpx.AsyncClient | None = None
        if self._proxy:
            http_client = httpx.AsyncClient(
                timeout=request_timeout,
                proxy=self._proxy,
                trust_env=False,
                follow_redirects=True,
            )
        elif self._is_local:
            # Local model servers (Ollama, llama.cpp, vLLM) often close idle
            # HTTP connections before the client-side keepalive expires. When
            # two LLM calls happen seconds apart (e.g. heartbeat _decide then
            # process_direct), the second call may grab a now-dead pooled
            # connection, causing a transient APIConnectionError on every first
            # attempt. Disabling keepalive for local endpoints avoids this by
            # opening a fresh connection for each request, which is cheap on a
            # LAN. Cloud providers benefit from keepalive, so we leave the
            # default pool settings for them.
            #
            # Also disable proxy for local endpoints: when the host has
            # HTTP_PROXY / HTTPS_PROXY / ALL_PROXY set, httpx would try to
            # route local traffic through the proxy, which typically cannot
            # reach localhost or LAN addresses.
            _local_limits = httpx.Limits(keepalive_expiry=0)
            http_client = httpx.AsyncClient(
                limits=_local_limits,
                timeout=request_timeout,
                transport=httpx.AsyncHTTPTransport(proxy=None, limits=_local_limits),
            )
        else:
            # Cloud providers: never reuse a pooled socket. A stale keepalive
            # is what surfaces as "Error calling LLM: Connection error." on
            # the first turn of a session; switching models "fixes" it only
            # because that rebuilds the client. Five seconds was still long
            # enough for OpenRouter / a WSL NAT to close the socket under us.
            _cloud_limits = httpx.Limits(keepalive_expiry=0, max_keepalive_connections=0)
            http_client = httpx.AsyncClient(
                limits=_cloud_limits,
                timeout=request_timeout,
                follow_redirects=True,
            )
        self._client = AsyncOpenAI(
            api_key=self._api_key_for_client,
            base_url=self._effective_base,
            default_headers=self._default_headers,
            default_query=self._extra_query or None,
            max_retries=0,
            timeout=request_timeout,
            http_client=http_client,
        )

    async def _reset_client(self) -> None:
        """Drop a dead HTTP pool so the next call opens a fresh connection."""
        client = self._client
        self._client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.debug("openai-compat client close after connection error failed", exc_info=True)

    async def _ensure_client(self):
        """Return the shared OpenAI client, creating it on first call."""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            global AsyncOpenAI
            if AsyncOpenAI is None:
                if os.environ.get("LANGFUSE_SECRET_KEY") and importlib.util.find_spec("langfuse"):
                    from langfuse.openai import AsyncOpenAI as _AsyncOpenAI
                else:
                    if os.environ.get("LANGFUSE_SECRET_KEY"):
                        logger.warning(
                            "LANGFUSE_SECRET_KEY is set but langfuse is not installed; "
                            "install with `pip install langfuse` to enable tracing"
                        )
                    from openai import AsyncOpenAI as _AsyncOpenAI
                AsyncOpenAI = _AsyncOpenAI

            self._build_client()
            return self._client

    def _setup_env(self, api_key: str, api_base: str | None) -> None:
        """Set environment variables based on provider spec."""
        spec = self._spec
        if not spec or not spec.env_key:
            return
        if spec.is_gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key).replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    @classmethod
    def _apply_cache_control(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Inject cache_control markers for prompt caching."""
        cache_marker = {"type": "ephemeral"}
        new_messages = list(messages)

        def _mark(msg: dict[str, Any]) -> dict[str, Any]:
            content = msg.get("content")
            if isinstance(content, str):
                return {**msg, "content": [
                    {"type": "text", "text": content, "cache_control": cache_marker},
                ]}
            if isinstance(content, list) and content:
                nc = list(content)
                nc[-1] = {**nc[-1], "cache_control": cache_marker}
                return {**msg, "content": nc}
            return msg

        if new_messages and new_messages[0].get("role") == "system":
            new_messages[0] = _mark(new_messages[0])
        if len(new_messages) >= 3:
            new_messages[-2] = _mark(new_messages[-2])

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": cache_marker}
        return new_messages, new_tools

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """Normalize to a provider-safe 9-char alphanumeric form."""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    def _should_normalize_tool_call_ids(self) -> bool:
        """Return True for providers that reject normal OpenAI tool call IDs."""
        return bool(self._spec and self._spec.name == "mistral")

    @staticmethod
    def _coerce_content_to_string(content: Any) -> str | None:
        """Coerce block/list content into plain text for strict string-only APIs."""
        if content is None or isinstance(content, str):
            return content
        text = OpenAICompatProvider._extract_text_content(content)
        if isinstance(text, str) and text:
            return text
        try:
            dumped = json.dumps(content, ensure_ascii=False)
        except Exception:
            dumped = str(content)
        return dumped or "(empty)"

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard keys, normalize tool_call IDs."""
        sanitized = LLMProvider._sanitize_request_messages(messages, _ALLOWED_MSG_KEYS)
        id_map: dict[str, str] = {}
        pending_tool_ids: dict[str, deque[str]] = {}
        force_string_content = bool(self._spec and self._spec.name == "deepseek")
        normalize_tool_ids = self._should_normalize_tool_call_ids()
        strip_reasoning = bool(
            self._spec
            and getattr(self._spec, "strip_history_reasoning_content", False)
        )
        if strip_reasoning:
            for msg in sanitized:
                msg.pop("reasoning_content", None)

        def map_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            if not normalize_tool_ids:
                return value
            return id_map.setdefault(value, self._normalize_tool_call_id(value))

        def unique_tool_id(value: Any, used_ids: set[str], idx: int) -> str:
            if isinstance(value, str) and value:
                base = map_id(value)
            else:
                base = _short_tool_id()
            if not isinstance(base, str) or not base:
                base = _short_tool_id()
            if base not in used_ids:
                return base
            seed = value if isinstance(value, str) and value else base
            salt = 1
            while True:
                candidate = self._normalize_tool_call_id(f"{seed}:{idx}:{salt}")
                if isinstance(candidate, str) and candidate not in used_ids:
                    return candidate
                salt += 1

        def map_tool_result_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            queue = pending_tool_ids.get(value)
            if queue:
                mapped = queue.popleft()
                if not queue:
                    pending_tool_ids.pop(value, None)
                return mapped
            return map_id(value)

        for clean in sanitized:
            if isinstance(clean.get("tool_calls"), list):
                normalized = []
                used_ids: set[str] = set()
                for idx, tc in enumerate(clean["tool_calls"]):
                    if not isinstance(tc, dict):
                        normalized.append(tc)
                        continue
                    tc_clean = dict(tc)
                    raw_id = tc_clean.get("id")
                    mapped_id = unique_tool_id(raw_id, used_ids, idx)
                    tc_clean["id"] = mapped_id
                    used_ids.add(mapped_id)
                    if isinstance(raw_id, str) and raw_id:
                        pending_tool_ids.setdefault(raw_id, deque()).append(mapped_id)
                    function = tc_clean.get("function")
                    if isinstance(function, dict):
                        function_clean = dict(function)
                        if "arguments" in function_clean:
                            function_clean["arguments"] = tool_arguments_json_for_replay(
                                function_clean.get("arguments")
                            )
                        else:
                            function_clean["arguments"] = "{}"
                        tc_clean["function"] = function_clean
                    normalized.append(tc_clean)
                clean["tool_calls"] = normalized
                if clean.get("role") == "assistant":
                    # Some OpenAI-compatible gateways reject assistant messages
                    # that mix non-empty content with tool_calls.
                    clean["content"] = None
            if "tool_call_id" in clean and clean["tool_call_id"]:
                clean["tool_call_id"] = map_tool_result_id(clean["tool_call_id"])
            if (
                force_string_content
                and not (clean.get("role") == "assistant" and clean.get("tool_calls"))
            ):
                clean["content"] = self._coerce_content_to_string(clean.get("content"))
        return self._enforce_role_alternation(sanitized)

    # ------------------------------------------------------------------
    # Build kwargs
    # ------------------------------------------------------------------

    def _request_model_name(self, model_name: str) -> str:
        spec = self._spec
        if not spec or "/" not in model_name:
            return model_name
        if spec.strip_model_prefix:
            return model_name.split("/")[-1]

        route_prefixes = getattr(spec, "strip_model_prefixes", ())
        if not isinstance(route_prefixes, tuple) or not route_prefixes:
            return model_name
        model_prefix, routed_model = model_name.split("/", 1)
        model_prefix_key = _provider_prefix_key(model_prefix)
        if any(_provider_prefix_key(prefix) == model_prefix_key for prefix in route_prefixes):
            return routed_model
        return model_name

    @staticmethod
    def _supports_temperature(
        model_name: str,
        reasoning_effort: str | None = None,
    ) -> bool:
        """Return True when the model accepts a temperature parameter.

        GPT-5 family and reasoning models (o1/o3/o4) reject temperature
        when reasoning_effort is set to anything other than ``"none"``.
        Anthropic deprecated temperature starting with the Claude 4.7/5
        generation: their endpoints 400 on it, and OpenRouter routing with
        ``require_parameters`` finds no endpoint at all (404). The Claude
        rule is version-based (see claude_capabilities) so future models
        are covered automatically.
        """
        if reasoning_effort and reasoning_effort.lower() != "none":
            return False
        name = model_name.lower()
        if any(token in name for token in ("gpt-5", "o1", "o3", "o4")):
            return False
        from navin.providers.claude_capabilities import claude_supports_temperature

        return claude_supports_temperature(name)

    def _temperature_model_key(self, model: str | None) -> str:
        return (model or self.default_model).lower()

    _TEMPERATURE_REJECTION_MARKERS = (
        "temperature` is deprecated",
        "temperature is deprecated",
        "does not support temperature",
        "no endpoints found that can handle the requested parameters",
    )

    def _register_temperature_rejection(
        self,
        model: str | None,
        e: Exception,
    ) -> bool:
        """Detect a temperature rejection and remember it for this model.

        Returns True when the caller should retry once without temperature.
        The set membership check makes the retry loop-free: a second failure
        for the same model falls through to normal error handling.
        """
        body = getattr(e, "body", None)
        text = f"{body} {e}".lower()
        if not any(marker in text for marker in self._TEMPERATURE_REJECTION_MARKERS):
            return False
        key = self._temperature_model_key(model)
        if key in self._no_temperature_models:
            return False
        self._no_temperature_models.add(key)
        logger.info(
            "Model {} rejected the temperature parameter; retrying without it",
            key,
        )
        return True

    def _is_openrouter_wire(self) -> bool:
        """True when the endpoint speaks OpenRouter's reasoning vocabulary."""
        base = (self._effective_base or "").lower()
        return "openrouter" in base

    def _reasoning_off_shapes(self, model_name: str, wire: str) -> list[dict[str, Any]]:
        """The "no thinking" requests this endpoint understands, strongest first.

        Saying nothing is not saying no: measured 2026-09-02 on
        ``z-ai/glm-5.3-flash``, an omitted reasoning parameter yields 726
        reasoning tokens and an answer truncated to nothing, while the same
        call with the floor effort spends 209 and answers. Reasoning tokens are
        generated at normal speed and are invisible to the loop, so that
        omission bought several seconds of silence per step, on every step.

        Providers with a native toggle (DeepSeek, Z.ai, DashScope, MiniMax,
        Volcengine, Kimi, MiMo) already say it in their own words in
        _build_kwargs; nothing to add for them.
        """
        if wire == WIRE_CHAT and _thinking_styles_for(self._spec, model_name):
            return []
        return off_shapes(
            model_name,
            spec_name=self._spec.name if self._spec else "",
            base_url=self._effective_base or "",
            wire=wire,
        )

    def _reasoning_knob_documented(self, model_name: str) -> bool:
        """True when the endpoint documents reasoning_effort for this model.

        A refusal there can only be about the value, so the negotiator keeps
        walking the floors instead of giving up on "not supported" wording.
        """
        if self._is_openrouter_wire():
            return True
        spec = self._spec.name if self._spec else ""
        if spec in ("gemini", "groq", "ollama", "mistral", "openai", "azure_openai"):
            return True
        slug = _model_slug(model_name)
        return "gpt-5" in slug or "gpt-oss" in slug or _requires_max_completion_tokens(model_name)

    def _reasoning_off_patch(
        self, model: str | None, model_name: str, wire: str
    ) -> dict[str, Any] | None:
        key = self._temperature_model_key(model)
        return self._reasoning_off.shape(key, self._reasoning_off_shapes(model_name, wire))

    def _register_reasoning_rejection(
        self,
        model: str | None,
        e: Exception,
        reasoning_effort: str | None = None,
    ) -> bool:
        """Learn from an endpoint refusing the reasoning request.

        Two lessons are possible. A refused "off" steps down to the next floor
        (off, minimal, low, then the endpoint's default). A parameter the
        endpoint does not know at all is remembered so it is never sent to
        that model again, whatever the effort. Returns True when the caller
        should retry with the corrected request.
        """
        key = self._temperature_model_key(model)
        model_name = self._request_model_name(model or self.default_model)
        effort = (reasoning_effort or "").lower() if isinstance(reasoning_effort, str) else ""
        if effort == "none":
            wire = WIRE_RESPONSES if self._should_use_responses_api(model, reasoning_effort) else WIRE_CHAT
            shapes = self._reasoning_off_shapes(model_name, wire)
            if self._reasoning_off.register_rejection(
                key, e, shapes, knob_known=self._reasoning_knob_documented(model_name)
            ):
                return True
        if (
            effort
            and effort != "none"
            and key not in self._reasoning_knob_rejected
            and classify_rejection(e) == REJECT_UNSUPPORTED
        ):
            self._reasoning_knob_rejected.add(key)
            logger.info(
                "Model {} rejected the reasoning_effort parameter; retrying without it",
                key,
            )
            return True
        return False

    @classmethod
    def _openrouter_outbound_tool_name(cls, name: str) -> str:
        if name == _OR_APPLY_PATCH_LOCAL:
            return _OR_APPLY_PATCH_WIRE
        return name

    @classmethod
    def _openrouter_inbound_tool_name(cls, name: str) -> str:
        if name == _OR_APPLY_PATCH_WIRE:
            return _OR_APPLY_PATCH_LOCAL
        return name

    def _inbound_tool_name(self, name: str) -> str:
        if self._is_openrouter:
            return self._openrouter_inbound_tool_name(name)
        return name

    def _remap_tool_calls(
        self,
        tool_calls: list[ToolCallRequest],
    ) -> list[ToolCallRequest]:
        if not self._is_openrouter or not tool_calls:
            return tool_calls
        remapped: list[ToolCallRequest] = []
        for tc in tool_calls:
            local = self._openrouter_inbound_tool_name(tc.name)
            if local == tc.name:
                remapped.append(tc)
                continue
            remapped.append(ToolCallRequest(
                id=tc.id,
                name=local,
                arguments=tc.arguments,
                extra_content=tc.extra_content,
                provider_specific_fields=tc.provider_specific_fields,
                function_provider_specific_fields=tc.function_provider_specific_fields,
            ))
        return remapped

    @classmethod
    def _alias_tools_for_openrouter(
        cls,
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return tools
        aliased: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                aliased.append(tool)
                continue
            fn = tool.get("function")
            if not isinstance(fn, dict):
                aliased.append(tool)
                continue
            local_name = str(fn.get("name") or "")
            wire_name = cls._openrouter_outbound_tool_name(local_name)
            if wire_name == local_name:
                aliased.append(tool)
                continue
            aliased.append({
                **tool,
                "function": {**fn, "name": wire_name},
            })
        return aliased

    @classmethod
    def _alias_messages_for_openrouter(
        cls,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rewrite apply_patch tool names in history so OR routing stays happy."""
        out: list[dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                out.append(msg)
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                out.append(msg)
                continue
            new_calls: list[Any] = []
            changed = False
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    new_calls.append(tc)
                    continue
                fn = tc.get("function")
                if not isinstance(fn, dict):
                    new_calls.append(tc)
                    continue
                local_name = str(fn.get("name") or "")
                wire_name = cls._openrouter_outbound_tool_name(local_name)
                if wire_name == local_name:
                    new_calls.append(tc)
                    continue
                changed = True
                new_calls.append({
                    **tc,
                    "function": {**fn, "name": wire_name},
                })
            out.append({**msg, "tool_calls": new_calls} if changed else msg)
        return out

    def _register_tool_endpoint_miss(self, model: str | None, e: Exception) -> bool:
        """True once when OR has no tool-capable endpoint under require_parameters."""
        text = str(e).lower()
        if "no endpoints found that support tool use" not in text and not (
            "support tool use" in text and "no endpoints" in text
        ):
            return False
        key = self._temperature_model_key(model)
        if key in self._no_require_parameters_models:
            return False
        self._no_require_parameters_models.add(key)
        logger.info(
            "Model {} has no tool endpoint under require_parameters; "
            "retrying without that filter",
            key,
        )
        return True

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        spec = self._spec

        if spec and spec.supports_prompt_caching:
            model_name = model or self.default_model
            if any(model_name.lower().startswith(k) for k in ("anthropic/", "claude")):
                messages, tools = self._apply_cache_control(messages, tools)

        model_name = self._request_model_name(model_name)

        if self._is_openrouter and tools:
            tools = self._alias_tools_for_openrouter(tools)
            messages = self._alias_messages_for_openrouter(messages)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
        }

        # GPT-5 and reasoning models (o1/o3/o4) reject temperature when
        # reasoning_effort is active.  Only include it when safe.
        if (
            self._supports_temperature(model_name, reasoning_effort)
            and self._temperature_model_key(model) not in self._no_temperature_models
        ):
            kwargs["temperature"] = temperature

        if (
            spec and getattr(spec, "supports_max_completion_tokens", False)
        ) or _requires_max_completion_tokens(model_name):
            kwargs["max_completion_tokens"] = max(1, max_tokens)
        else:
            kwargs["max_tokens"] = max(1, max_tokens)

        if spec:
            model_lower = model_name.lower()
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    break

        # Normalize reasoning_effort into a semantic form (OpenAI vocab)
        # used for internal decisions, and a wire form actually sent out.
        # "minimum" is accepted as a DashScope-native alias for "minimal".
        semantic_effort: str | None = None
        if isinstance(reasoning_effort, str):
            semantic_effort = reasoning_effort.lower()
            if semantic_effort == "minimum":
                semantic_effort = "minimal"

        wire_effort = reasoning_effort
        if spec and spec.name == "dashscope" and semantic_effort == "minimal":
            # DashScope accepts none/minimum/low/medium/high/xhigh; "minimal" 400s.
            wire_effort = "minimum"

        # Magistral and other providers where reasoning is implicit reject the
        # reasoning_effort kwarg entirely. Strip it before the remap so we don't
        # accidentally send "none"/"high" to a model that always reasons.
        strip_effort = False
        if spec and getattr(spec, "implicit_reasoning_models", ()):
            model_lower = model_name.lower()
            strip_effort = any(
                pat in model_lower for pat in spec.implicit_reasoning_models
            )
        # Same rule, learned rather than declared: families that reason on
        # their own terms (Grok 4, Magistral, R1...) or that already answered
        # 400 to the parameter itself.
        if not strip_effort and (
            always_reasons(model_name)
            or self._temperature_model_key(model) in self._reasoning_knob_rejected
        ):
            strip_effort = True

        # Some providers accept a constrained reasoning_effort vocabulary
        # (Mistral: only "high"/"none"). Remap from OpenAI vocab to the
        # provider's accepted set; an empty mapped value means "omit".
        if (
            not strip_effort
            and spec
            and getattr(spec, "reasoning_effort_remap", ())
            and isinstance(semantic_effort, str)
        ):
            remap = dict(spec.reasoning_effort_remap)
            mapped = remap.get(semantic_effort)
            if mapped is not None:
                wire_effort = mapped or None
                semantic_effort = mapped or "none"

        if strip_effort:
            wire_effort = None
        elif wire_effort and semantic_effort != "none":
            kwargs["reasoning_effort"] = wire_effort

        # Only send thinking controls when reasoning_effort is explicit so
        # omitting the config preserves each provider's default.
        if reasoning_effort is not None:
            slug = _model_slug(model_name)
            thinking_enabled = semantic_effort not in ("none", "minimal")
            for thinking_style in _thinking_styles_for(spec, model_name):
                if not thinking_enabled and slug in _KIMI_ALWAYS_THINKING_MODELS:
                    continue
                extra = _thinking_extra_body(thinking_style, thinking_enabled)
                if extra:
                    kwargs.setdefault("extra_body", {}).update(extra)
            gateway_style = getattr(spec, "gateway_reasoning_style", "") if spec else ""
            if (
                gateway_style
                and _model_thinking_style(model_name)
                and (thinking_enabled or slug not in _KIMI_ALWAYS_THINKING_MODELS)
            ):
                extra = _gateway_reasoning_extra_body(gateway_style, semantic_effort)
                if extra:
                    kwargs.setdefault("extra_body", {}).update(extra)

            # Moonshot rejects requests that carry both 'reasoning_effort'
            # and the native 'thinking' param.  We already expressed the
            # user's intent via the provider-native shape, so drop the
            # redundant wire-level kwarg.  Only kimi models need this -
            # Xiaomi's API accepts both params.
            if slug in _KIMI_THINKING_MODELS:
                kwargs.pop("reasoning_effort", None)

            # "None" has to be said in the endpoint's own words; omitting the
            # parameter leaves its default on (GPT-5 medium, Gemini dynamic,
            # Ollama auto-thinking, every gateway's upstream default). Native
            # controls above win when the provider has one; the shape list is
            # empty for them and for families that never or always reason.
            if semantic_effort == "none" and not strip_effort:
                off = self._reasoning_off_patch(model, model_name, WIRE_CHAT)
                if off:
                    apply_shape(kwargs, off)

        if tools:
            kwargs["tools"] = tools
            if (
                self._is_openrouter
                and isinstance(tool_choice, dict)
                and isinstance(tool_choice.get("function"), dict)
            ):
                fn = tool_choice["function"]
                local_name = str(fn.get("name") or "")
                wire_name = self._openrouter_outbound_tool_name(local_name)
                if wire_name != local_name:
                    tool_choice = {
                        **tool_choice,
                        "function": {**fn, "name": wire_name},
                    }
            kwargs["tool_choice"] = tool_choice or "auto"

        # Backfill reasoning_content="" on assistants missing it: DeepSeek
        # thinking mode rejects history otherwise (#3554, #3584); "" reads
        # as "no thinking that turn". DeepSeek-V4/reasoner reason natively,
        # so backfill even without explicit reasoning_effort.
        explicit_thinking = (
            reasoning_effort is not None
            and semantic_effort not in ("none", "minimal")
            and (
                (spec and spec.thinking_style)
                or _model_thinking_style(model_name)
            )
        )
        implicit_deepseek_thinking = (
            spec is not None
            and spec.name == "deepseek"
            and semantic_effort not in ("none", "minimal", "minimum")
            and any(t in model_name.lower() for t in ("deepseek-v4", "deepseek-reasoner"))
        )
        if explicit_thinking or implicit_deepseek_thinking:
            for msg in kwargs["messages"]:
                if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                    msg["reasoning_content"] = ""

        # OpenRouter's default routing balances on price and often lands on
        # hosts serving 15-30 tok/s; the same model then feels 10x slower
        # than on a fast host.  For an interactive agent, latency is the
        # product, so ask for the fastest provider.  setdefault keeps any
        # user-configured "provider" preferences (merged below) in charge.
        if self._is_openrouter:
            provider = kwargs.setdefault("extra_body", {}).setdefault(
                "provider", {"sort": "throughput"},
            )
            # When we send tools, only route to endpoints that support all
            # request params (incl. tools). require_parameters is a boolean
            # per OpenRouter provider routing docs - not a string list.
            # Skip after a tool-endpoint miss retry for this model.
            if (
                tools
                and isinstance(provider, dict)
                and self._temperature_model_key(model_name)
                not in self._no_require_parameters_models
            ):
                provider["require_parameters"] = True
            # Real billed cost in the response (usage.cost, USD credits) so
            # managed-budget accounting debits actual spend, not an estimate.
            kwargs.setdefault("extra_body", {}).setdefault(
                "usage", {"include": True},
            )
            # Sticky routing: pin the whole session to one upstream host so
            # the prompt cache stays warm across agent-loop iterations.
            # Without it OpenRouter re-picks a host per request and the
            # cached prefix is billed at full price again.
            session_id = current_session_id()
            if session_id:
                kwargs.setdefault("extra_body", {}).setdefault(
                    "session_id", session_id,
                )
        elif _is_direct_openai_base(self.api_base):
            # Direct OpenAI: prompt_cache_key routes requests to the machine
            # holding the warm cache (required on GPT-5.6+ for reliable hits).
            session_id = current_session_id()
            if session_id:
                kwargs.setdefault("extra_body", {}).setdefault(
                    "prompt_cache_key", session_id,
                )

        # Merge user-configured extra_body last so it can override or
        # extend provider-specific defaults (e.g. chat_template_kwargs,
        # guided_json, repetition_penalty).  Uses recursive merge so
        # nested dicts like {"chat_template_kwargs": {"enable_thinking": false}}
        # do not clobber sibling keys already set by thinking-style logic.
        if self._extra_body:
            existing = kwargs.get("extra_body", {})
            kwargs["extra_body"] = _deep_merge(existing, self._extra_body)

        return kwargs

    def _should_use_responses_api(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> bool:
        """Use Responses API only for direct OpenAI requests that benefit from it."""
        if self._api_type == "chat_completions":
            return False
        if self._spec and self._spec.name not in ("openai", "github_copilot"):
            return False
        if self._api_type == "responses":
            # Explicit configuration means Responses is mandatory; do not
            # consult the circuit breaker or fall back to Chat Completions.
            return True
        if self._spec is None or self._spec.name != "github_copilot":
            if not _is_direct_openai_base(self._effective_base):
                return False

        model_name = (model or self.default_model).lower()
        wants = False
        if reasoning_effort and reasoning_effort.lower() != "none":
            wants = True
        elif any(token in model_name for token in ("gpt-5", "o1", "o3", "o4")):
            wants = True
        if not wants:
            return False

        return self._responses_circuit_allows_probe(model, reasoning_effort)

    def _responses_circuit_allows_probe(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> bool:
        """Return False when the Responses API circuit breaker is open."""
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        failures = self._responses_failures.get(key, 0)
        if failures >= _RESPONSES_FAILURE_THRESHOLD:
            tripped = self._responses_tripped_at.get(key, 0.0)
            if (time.monotonic() - tripped) < _RESPONSES_PROBE_INTERVAL_S:
                return False
            # Half-open: allow one probe attempt
        return True

    def _record_responses_failure(self, model: str | None, reasoning_effort: str | None) -> None:
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        count = self._responses_failures.get(key, 0) + 1
        self._responses_failures[key] = count
        if count >= _RESPONSES_FAILURE_THRESHOLD:
            self._responses_tripped_at[key] = time.monotonic()
            logger.warning(
                "Responses API circuit open for {} - falling back to Chat Completions",
                key,
            )

    def _record_responses_success(self, model: str | None, reasoning_effort: str | None) -> None:
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        self._responses_failures.pop(key, None)
        self._responses_tripped_at.pop(key, None)

    @staticmethod
    def _should_fallback_from_responses_error(e: Exception) -> bool:
        """Fallback only for likely Responses API compatibility errors."""
        response = getattr(e, "response", None)
        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code not in {400, 404, 422}:
            return False

        body = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        body_text = str(body).lower() if body is not None else ""
        compatibility_markers = (
            "responses",
            "response api",
            "max_output_tokens",
            "instructions",
            "previous_response",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized request argument",
        )
        return any(marker in body_text for marker in compatibility_markers)

    def _build_responses_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a Responses API body for direct OpenAI requests."""
        model_name = model or self.default_model
        model_name = self._request_model_name(model_name)
        sanitized_messages = self._sanitize_messages(self._sanitize_empty_content(messages))
        instructions, input_items = convert_messages(sanitized_messages)

        body: dict[str, Any] = {
            "model": model_name,
            "instructions": instructions or None,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "store": False,
            "stream": False,
        }

        if self._supports_temperature(model_name, reasoning_effort):
            body["temperature"] = temperature

        effort_lower = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
        knob_rejected = self._temperature_model_key(model) in self._reasoning_knob_rejected
        if effort_lower == "none":
            # GPT-5 defaults to "medium" when the field is missing, so "none"
            # is spelled out (GPT-5.1+), or its floor for older generations.
            off = self._reasoning_off_patch(model, model_name, WIRE_RESPONSES)
            if off:
                apply_shape(body, off)
        elif reasoning_effort and not knob_rejected and not always_reasons(model_name):
            body["reasoning"] = {"effort": reasoning_effort}
            body["include"] = ["reasoning.encrypted_content"]

        if tools:
            body["tools"] = convert_tools(tools)
            body["tool_choice"] = tool_choice or "auto"

        # OpenAI routes cache lookups by prompt_cache_key; GPT-5.6+ needs it
        # for reliable prefix matching across agent-loop iterations.
        session_id = current_session_id()
        if session_id:
            body["prompt_cache_key"] = session_id

        extra_body = getattr(self, "_extra_body", {})
        if extra_body:
            body = _merge_responses_extra_body(body, extra_body)

        return body

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_mapping(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        return None

    @classmethod
    def _extract_text_content(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                item_map = cls._maybe_mapping(item)
                if item_map:
                    # Skip Mistral-style {"type":"thinking","thinking":[...]}
                    # blocks: their text belongs in reasoning_content.
                    if item_map.get("type") == "thinking":
                        continue
                    text = item_map.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                    continue
                if isinstance(item, str):
                    parts.append(item)
            return "".join(parts) or None
        return str(value)

    @classmethod
    def _extract_thinking_content(cls, value: Any) -> str | None:
        """Extract reasoning text from Mistral-style thinking blocks.

        Mistral returns content as a list mixing
        ``{"type":"thinking","thinking":[{"type":"text","text":...}]}`` and
        ``{"type":"text","text":...}``. The thinking text belongs in
        ``reasoning_content`` so the agent can surface it as a reasoning
        trace rather than as the assistant's reply.
        """
        if not isinstance(value, list):
            return None
        parts: list[str] = []
        for item in value:
            item_map = cls._maybe_mapping(item)
            if not item_map:
                continue
            if item_map.get("type") != "thinking":
                continue
            inner = item_map.get("thinking")
            text = cls._extract_text_content(inner)
            if text:
                parts.append(text)
        return "".join(parts) or None

    @classmethod
    def _extract_usage(cls, response: Any) -> dict[str, int]:
        """Extract token usage from an OpenAI-compatible response.

        Handles both dict-based (raw JSON) and object-based (SDK Pydantic)
        responses.  Provider-specific ``cached_tokens`` fields are normalised
        under a single key; see the priority chain inside for details.
        """
        # --- resolve usage object ---
        usage_obj = None
        response_map = cls._maybe_mapping(response)
        if response_map is not None:
            usage_obj = response_map.get("usage")
        elif hasattr(response, "usage") and response.usage:
            usage_obj = response.usage

        usage_map = cls._maybe_mapping(usage_obj)
        if usage_map is not None:
            result = {
                "prompt_tokens": int(usage_map.get("prompt_tokens") or 0),
                "completion_tokens": int(usage_map.get("completion_tokens") or 0),
                "total_tokens": int(usage_map.get("total_tokens") or 0),
            }
        elif usage_obj:
            result = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
            }
        else:
            return {}

        # --- cached_tokens (normalised across providers) ---
        # Try nested paths first (dict), fall back to attribute (SDK object).
        # Priority order ensures the most specific field wins.
        for path in (
            ("prompt_tokens_details", "cached_tokens"),  # OpenAI/Zhipu/MiniMax/Qwen/Mistral/xAI
            ("cached_tokens",),                          # StepFun/Moonshot (top-level)
            ("prompt_cache_hit_tokens",),                # DeepSeek/SiliconFlow
        ):
            cached = cls._get_nested_int(usage_map, path)
            if not cached and usage_obj:
                cached = cls._get_nested_int(usage_obj, path)
            if cached:
                result["cached_tokens"] = cached
                break

        # --- reasoning tokens: the only witness that "none" reached the wire ---
        for path in (
            ("completion_tokens_details", "reasoning_tokens"),  # OpenAI/OpenRouter/Groq
            ("output_tokens_details", "reasoning_tokens"),      # Responses-shaped usage
            ("reasoning_tokens",),                              # flat variants
        ):
            reasoning = cls._get_nested_int(usage_map, path) if usage_map is not None else 0
            if not reasoning and usage_obj is not None:
                reasoning = cls._get_nested_int(usage_obj, path)
            if reasoning:
                result["reasoning_tokens"] = reasoning
                break

        # --- real billed cost (OpenRouter `usage.cost`, USD credits) ---
        # Kept as integer micro-USD so the whole usage dict stays dict[str, int]
        # and survives the runner's int-only accumulation.
        cost_raw: Any = None
        if usage_map is not None:
            cost_raw = usage_map.get("cost")
        if cost_raw is None and usage_obj is not None:
            cost_raw = getattr(usage_obj, "cost", None)
            if cost_raw is None:
                extra = getattr(usage_obj, "model_extra", None)
                if isinstance(extra, dict):
                    cost_raw = extra.get("cost")
        try:
            cost_usd = float(cost_raw) if cost_raw is not None else 0.0
        except (TypeError, ValueError):
            cost_usd = 0.0
        if cost_usd > 0:
            result["cost_micro_usd"] = max(1, round(cost_usd * 1_000_000))

        return result

    @staticmethod
    def _get_nested_int(obj: Any, path: tuple[str, ...]) -> int:
        """Drill into *obj* by *path* segments and return an ``int`` value.

        Supports both dict-key access and attribute access so it works
        uniformly with raw JSON dicts **and** SDK Pydantic models.
        """
        current = obj
        for segment in path:
            if current is None:
                return 0
            if isinstance(current, dict):
                current = current.get(segment)
            else:
                current = getattr(current, segment, None)
        return int(current or 0) if current is not None else 0

    def _parse(self, response: Any) -> LLMResponse:
        if isinstance(response, str):
            return LLMResponse(content=response, finish_reason="stop")

        response_map = self._maybe_mapping(response)
        if response_map is not None:
            choices = response_map.get("choices") or []
            if not choices:
                content = self._extract_text_content(
                    response_map.get("content") or response_map.get("output_text")
                )
                reasoning_content = self._extract_text_content(
                    response_map.get("reasoning_content")
                )
                if content is not None:
                    return LLMResponse(
                        content=content,
                        reasoning_content=reasoning_content,
                        finish_reason=str(response_map.get("finish_reason") or "stop"),
                        usage=self._extract_usage(response_map),
                    )
                return LLMResponse(
                    content="Error: API returned empty choices.",
                    finish_reason="error",
                    error_kind="empty",
                )

            choice0 = self._maybe_mapping(choices[0]) or {}
            msg0 = self._maybe_mapping(choice0.get("message")) or {}
            content = self._extract_text_content(msg0.get("content"))
            finish_reason = str(choice0.get("finish_reason") or "stop")

            raw_tool_calls: list[Any] = []
            # StepFun: fallback to reasoning field when content is empty
            if not content and msg0.get("reasoning") and self._spec and self._spec.reasoning_as_content:
                content = self._extract_text_content(msg0.get("reasoning"))
            reasoning_content = msg0.get("reasoning_content")
            if reasoning_content is None and msg0.get("reasoning"):
                reasoning_content = self._extract_text_content(msg0.get("reasoning"))
            # Mistral reasoning models return thinking text inside the content
            # array; lift it into reasoning_content so the runner records it
            # under the reasoning trace.
            spec = getattr(self, "_spec", None)
            if reasoning_content is None and getattr(spec, "extract_thinking_blocks", False):
                reasoning_content = self._extract_thinking_content(msg0.get("content"))
            for ch in choices:
                ch_map = self._maybe_mapping(ch) or {}
                m = self._maybe_mapping(ch_map.get("message")) or {}
                tool_calls = m.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    raw_tool_calls.extend(tool_calls)
                    if ch_map.get("finish_reason") in ("tool_calls", "stop"):
                        finish_reason = str(ch_map["finish_reason"])
                if not content:
                    content = self._extract_text_content(m.get("content"))
                if reasoning_content is None:
                    reasoning_content = m.get("reasoning_content")

            # Deduplicate tool call IDs (same pattern as streaming path)
            # Some providers reuse the same ID for parallel tool calls.
            _seen_tc_ids: set[str] = set()
            parsed_tool_calls = []
            for tc in raw_tool_calls:
                tc_map = self._maybe_mapping(tc) or {}
                fn = self._maybe_mapping(tc_map.get("function")) or {}
                args = parse_tool_arguments(fn.get("arguments", {}))
                ec, prov, fn_prov = _extract_tc_extras(tc)
                raw_id = str(tc_map.get("id") or _short_tool_id())
                if not raw_id or raw_id in _seen_tc_ids:
                    raw_id = _short_tool_id()
                _seen_tc_ids.add(raw_id)
                parsed_tool_calls.append(ToolCallRequest(
                    id=raw_id,
                    name=self._inbound_tool_name(str(fn.get("name") or "")),
                    arguments=args,
                    extra_content=ec,
                    provider_specific_fields=prov,
                    function_provider_specific_fields=fn_prov,
                ))
            if not parsed_tool_calls:
                content, parsed_tool_calls = _extract_text_tool_calls(content)
                parsed_tool_calls = self._remap_tool_calls(parsed_tool_calls)

            return LLMResponse(
                content=content,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
                usage=self._extract_usage(response_map),
                reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
            )

        if not response.choices:
            return LLMResponse(
                content="Error: API returned empty choices.",
                finish_reason="error",
                error_kind="empty",
            )

        choice = response.choices[0]
        msg = choice.message
        content = msg.content
        finish_reason = choice.finish_reason

        raw_tool_calls: list[Any] = []
        for ch in response.choices:
            m = ch.message
            if hasattr(m, "tool_calls") and m.tool_calls:
                raw_tool_calls.extend(m.tool_calls)
                if ch.finish_reason in ("tool_calls", "stop"):
                    finish_reason = ch.finish_reason
            if not content and m.content:
                content = m.content
            if not content and getattr(m, "reasoning", None) and self._spec and self._spec.reasoning_as_content:
                content = m.reasoning

        tool_calls = []
        for tc in raw_tool_calls:
            args = parse_tool_arguments(tc.function.arguments)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            tool_calls.append(ToolCallRequest(
                id=str(getattr(tc, "id", None) or _short_tool_id()),
                name=self._inbound_tool_name(str(tc.function.name or "")),
                arguments=args,
                extra_content=ec,
                provider_specific_fields=prov,
                function_provider_specific_fields=fn_prov,
            ))
        if not tool_calls:
            content, tool_calls = _extract_text_tool_calls(content)
            tool_calls = self._remap_tool_calls(tool_calls)

        reasoning_content = getattr(msg, "reasoning_content", None)
        if reasoning_content is None and getattr(msg, "reasoning", None):
            reasoning_content = msg.reasoning

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason or "stop",
            usage=self._extract_usage(response),
            reasoning_content=reasoning_content,
        )

    @classmethod
    def _parse_chunks(cls, chunks: list[Any]) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        def _accum_tc(tc: Any, idx_hint: int) -> None:
            """Accumulate one streaming tool-call delta into *tc_bufs*."""
            tc_index: int = _get(tc, "index") if _get(tc, "index") is not None else idx_hint
            buf = tc_bufs.setdefault(tc_index, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            tc_id = _get(tc, "id")
            if tc_id:
                buf["id"] = str(tc_id)
            fn = _get(tc, "function")
            if fn is not None:
                fn_name = _get(fn, "name")
                if fn_name:
                    buf["name"] = str(fn_name)
                fn_args = _get(fn, "arguments")
                if fn_args:
                    buf["arguments"] += str(fn_args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            if ec:
                buf["extra_content"] = ec
            if prov:
                buf["prov"] = prov
            if fn_prov:
                buf["fn_prov"] = fn_prov

        def _accum_legacy_function_call(function_call: Any) -> None:
            """Accumulate legacy ``delta.function_call`` streaming chunks."""
            if not function_call:
                return
            buf = tc_bufs.setdefault(0, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            fn_name = _get(function_call, "name")
            if fn_name:
                buf["name"] = str(fn_name)
            fn_args = _get(function_call, "arguments")
            if fn_args:
                buf["arguments"] += str(fn_args)

        for chunk in chunks:
            if isinstance(chunk, str):
                content_parts.append(chunk)
                continue

            chunk_map = cls._maybe_mapping(chunk)
            if chunk_map is not None:
                choices = chunk_map.get("choices") or []
                if not choices:
                    usage = cls._extract_usage(chunk_map) or usage
                    text = cls._extract_text_content(
                        chunk_map.get("content") or chunk_map.get("output_text")
                    )
                    if text:
                        content_parts.append(text)
                    continue
                choice = cls._maybe_mapping(choices[0]) or {}
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
                delta = cls._maybe_mapping(choice.get("delta")) or {}
                raw_delta_content = delta.get("content")
                text = cls._extract_text_content(raw_delta_content)
                if text:
                    content_parts.append(text)
                text = cls._extract_text_content(delta.get("reasoning_content"))
                if not text:
                    text = cls._extract_text_content(delta.get("reasoning"))
                if not text:
                    # Mistral streams thinking inside the content array as
                    # {"type":"thinking", thinking:[{"type":"text", ...}]}.
                    text = cls._extract_thinking_content(raw_delta_content)
                if text:
                    reasoning_parts.append(text)
                for idx, tc in enumerate(delta.get("tool_calls") or []):
                    _accum_tc(tc, idx)
                _accum_legacy_function_call(delta.get("function_call"))
                usage = cls._extract_usage(chunk_map) or usage
                continue

            if not chunk.choices:
                usage = cls._extract_usage(chunk) or usage
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta and delta.content:
                text = cls._extract_text_content(delta.content)
                if text:
                    content_parts.append(text)
                thinking_text = cls._extract_thinking_content(delta.content)
                if thinking_text:
                    reasoning_parts.append(thinking_text)
            if delta:
                reasoning = getattr(delta, "reasoning_content", None)
                if not reasoning:
                    reasoning = getattr(delta, "reasoning", None)
                if reasoning:
                    text = cls._extract_text_content(reasoning)
                    if text:
                        reasoning_parts.append(text)
            for tc in (getattr(delta, "tool_calls", None) or []) if delta else []:
                _accum_tc(tc, getattr(tc, "index", 0))
            if delta:
                _accum_legacy_function_call(getattr(delta, "function_call", None))

        # Some providers (e.g. Zhipu/GLM) reuse the same tool_call id for
        # parallel tool calls in streaming mode. Deduplicate before building
        # the response so downstream tool messages don't collide.
        _seen_tc_ids: set[str] = set()
        for b in tc_bufs.values():
            if not b["id"] or b["id"] in _seen_tc_ids:
                b["id"] = _short_tool_id()
            _seen_tc_ids.add(b["id"])

        content = "".join(content_parts) or None
        tool_calls = [
            ToolCallRequest(
                id=b["id"] or _short_tool_id(),
                name=b["name"],
                arguments=parse_tool_arguments(b["arguments"]),
                extra_content=b.get("extra_content"),
                provider_specific_fields=b.get("prov"),
                function_provider_specific_fields=b.get("fn_prov"),
            )
            for b in tc_bufs.values()
        ]
        if not tool_calls:
            content, tool_calls = _extract_text_tool_calls(content)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )

    @classmethod
    def _extract_error_metadata(cls, e: Exception) -> dict[str, Any]:
        response = getattr(e, "response", None)
        headers = getattr(response, "headers", None)
        payload = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        if payload is None and response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    payload = response_json()
                except Exception:
                    payload = None
        error_type, error_code = LLMProvider._extract_error_type_code(payload)

        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code is None:
            from navin.providers.user_facing_errors import http_status_from_error_payload

            status_code = http_status_from_error_payload(payload)
            if status_code is None:
                status_code = http_status_from_error_payload(str(e))

        should_retry: bool | None = None
        if headers is not None:
            raw = headers.get("x-should-retry")
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "true":
                    should_retry = True
                elif lowered == "false":
                    should_retry = False

        error_kind: str | None = None
        error_name = e.__class__.__name__.lower()
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name:
            error_kind = "connection"
        elif status_code is not None:
            try:
                status = int(status_code)
            except (TypeError, ValueError):
                status = None
            if status == 429:
                error_kind = "rate_limit"
            elif status is not None and 500 <= status <= 599:
                error_kind = "server_error"
            elif status in (401, 403):
                error_kind = "authentication"
            elif status == 408:
                error_kind = "timeout"
            elif status == 404:
                error_kind = "invalid_request"

        return {
            "error_status_code": int(status_code) if status_code is not None else None,
            "error_kind": error_kind,
            "error_type": error_type,
            "error_code": error_code,
            "error_retry_after_s": cls._extract_retry_after_from_headers(headers),
            "error_should_retry": should_retry,
        }

    @staticmethod
    def _maybe_resync_managed_key(status_code: int | None) -> None:
        """401/403 sur la clé gérée : demander un validate immédiat.

        Après une rotation (renouvellement Stripe), l'ancienne clé meurt
        instantanément côté fournisseur ; sans ceci, la nouvelle clé
        n'arrivait qu'au prochain poll (jusqu'à 10 minutes d'appels en
        échec). Best-effort et throttlé côté license_sync.
        """
        if status_code not in (401, 403, 402):
            return
        try:
            from navin.optional_live import live_modules_available

            if not live_modules_available():
                return
            from navin.config.loader import load_config
            from navin.license_client import uses_managed_key
            from navin.license_sync import request_immediate_sync

            if uses_managed_key(load_config()):
                # 402 = plafond OpenRouter : souvent une clé périmée / sous-capée
                # après upgrade. Resync pour PATCH du cap ou nouvelle clé.
                request_immediate_sync(
                    "provider_quota_or_auth_error"
                    if status_code == 402
                    else "provider_auth_error"
                )
        except Exception:
            logger.debug("managed key resync skipped", exc_info=True)

    @staticmethod
    def _handle_error(
        e: Exception,
        *,
        spec: ProviderSpec | None = None,
        api_base: str | None = None,
    ) -> LLMResponse:
        body = (
            getattr(e, "doc", None)
            or getattr(e, "body", None)
            or getattr(getattr(e, "response", None), "text", None)
        )
        body_text = body if isinstance(body, str) else str(body) if body is not None else ""
        from navin.providers.user_facing_errors import (
            provider_error_detail,
            user_facing_llm_error,
        )

        raw = body_text.strip() if body_text.strip() else f"Error calling LLM: {e}"
        # The chat surface only shows the sanitized message; keep the raw
        # provider error in the log so failures stay diagnosable.
        logger.warning("LLM provider error ({}): {}", e.__class__.__name__, raw[:600])
        msg = user_facing_llm_error(
            raw if raw.lower().startswith("error") else f"Error: {raw}"
        )

        text = f"{body_text} {e}".lower()
        if spec and spec.is_local and ("502" in text or "connection" in text or "refused" in text):
            if not body_text.strip():
                # Nothing came back from the server, so the generic internet
                # wording would send the user chasing their ISP instead of
                # their own Ollama / vLLM process.
                msg = "Error: could not reach the local model server."
            msg += (
                "\nHint: this is a local model endpoint. Check that the local server is reachable at "
                f"{api_base or spec.default_api_base}, and if you are using a proxy/tunnel, make sure it "
                "can reach your local Ollama/vLLM service instead of routing localhost through the remote host."
            )

        response = getattr(e, "response", None)
        retry_after = LLMProvider._extract_retry_after_from_headers(getattr(response, "headers", None))
        if retry_after is None:
            retry_after = LLMProvider._extract_retry_after(msg)
        metadata = OpenAICompatProvider._extract_error_metadata(e)
        OpenAICompatProvider._maybe_resync_managed_key(
            metadata.get("error_status_code"),
        )
        provider_label = ""
        if spec is not None:
            provider_label = str(
                getattr(spec, "display_name", "") or getattr(spec, "name", "") or ""
            ).strip()
        return LLMResponse(
            content=msg,
            finish_reason="error",
            retry_after=retry_after,
            error_detail=provider_error_detail(raw),
            error_provider=provider_label or None,
            **metadata,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        await self._ensure_client()
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    result = parse_response_output(await self._client.responses.create(**body))
                    self._record_responses_success(model, reasoning_effort)
                    return result
                except Exception as responses_error:
                    if self._register_reasoning_rejection(model, responses_error, reasoning_effort):
                        return await self.chat(
                            messages, tools, model, max_tokens, temperature,
                            reasoning_effort, tool_choice,
                        )
                    if self._spec and self._spec.name == "github_copilot":
                        # Copilot gateway exposes GPT-5/o-series only via /responses;
                        # falling back to /chat/completions cannot succeed and would
                        # hide the real error.
                        raise
                    if self._api_type == "responses":
                        raise
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
            return self._parse(await self._client.chat.completions.create(**kwargs))
        except Exception as e:
            if self._register_temperature_rejection(model, e):
                return await self.chat(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                )
            if self._register_tool_endpoint_miss(model, e):
                return await self.chat(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                )
            if self._register_reasoning_rejection(model, e, reasoning_effort):
                return await self.chat(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                )
            await self._maybe_reset_client_after_error(e)
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        await self._ensure_client()
        idle_timeout_s = resolve_stream_idle_timeout_s()
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    body["stream"] = True
                    stream = await self._client.responses.create(**body)

                    async def _timed_stream():
                        stream_iter = stream.__aiter__()
                        while True:
                            try:
                                yield await asyncio.wait_for(
                                    stream_iter.__anext__(),
                                    timeout=idle_timeout_s,
                                )
                            except StopAsyncIteration:
                                break

                    (
                        content,
                        tool_calls,
                        finish_reason,
                        usage,
                        reasoning_content,
                    ) = await consume_sdk_stream(
                        _timed_stream(),
                        on_content_delta,
                        on_tool_call_delta=on_tool_call_delta,
                    )
                    self._record_responses_success(model, reasoning_effort)
                    return LLMResponse(
                        content=content or None,
                        tool_calls=tool_calls,
                        finish_reason=finish_reason,
                        usage=usage,
                        reasoning_content=reasoning_content,
                    )
                except Exception as responses_error:
                    if self._register_reasoning_rejection(model, responses_error, reasoning_effort):
                        return await self.chat_stream(
                            messages, tools, model, max_tokens, temperature,
                            reasoning_effort, tool_choice,
                            on_content_delta=on_content_delta,
                            on_thinking_delta=on_thinking_delta,
                            on_tool_call_delta=on_tool_call_delta,
                        )
                    if self._spec and self._spec.name == "github_copilot":
                        # Copilot gateway exposes GPT-5/o-series only via /responses;
                        # falling back to /chat/completions cannot succeed and would
                        # hide the real error.
                        raise
                    if self._api_type == "responses":
                        raise
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
            if self._spec and self._spec.name in _GLM_PROVIDERS and tools and on_tool_call_delta:
                # Z.AI/GLM keeps streaming tool-call arguments behind an
                # explicit provider flag.  Pass it through the OpenAI SDK's
                # extra_body escape hatch so the usual delta.tool_calls path
                # can surface live file-edit progress.
                kwargs.setdefault("extra_body", {})["tool_stream"] = True
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            stream = await self._client.chat.completions.create(**kwargs)
            chunks: list[Any] = []
            stream_iter = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=idle_timeout_s,
                    )
                except StopAsyncIteration:
                    break
                chunks.append(chunk)
                if chunk.choices:
                    delta_obj = chunk.choices[0].delta
                    raw_delta_content = getattr(delta_obj, "content", None)
                    if on_content_delta:
                        # Mistral streams content as a list of {"type":"thinking",
                        # ...} + {"type":"text",...} blocks. Extract just the
                        # text portion before invoking the callback so callers
                        # never see non-string content.
                        text = self._extract_text_content(raw_delta_content)
                        if text:
                            await on_content_delta(text)
                    if on_thinking_delta:
                        reasoning = getattr(delta_obj, "reasoning_content", None) or getattr(
                            delta_obj, "reasoning", None,
                        )
                        r_text = self._extract_text_content(reasoning)
                        if not r_text:
                            # Mistral keeps the thinking trace inside the
                            # content array rather than a separate field.
                            r_text = self._extract_thinking_content(raw_delta_content)
                        if r_text:
                            await on_thinking_delta(r_text)
                    if on_tool_call_delta:
                        for idx, tool_delta in enumerate(
                            getattr(delta_obj, "tool_calls", None) or []
                        ):
                            fn = _get(tool_delta, "function")
                            tool_index = _get(tool_delta, "index")
                            await on_tool_call_delta({
                                "index": tool_index if tool_index is not None else idx,
                                "call_id": str(_get(tool_delta, "id") or ""),
                                "name": self._inbound_tool_name(
                                    str(_get(fn, "name") or "") if fn is not None else "",
                                ),
                                "arguments_delta": (
                                    str(_get(fn, "arguments") or "") if fn is not None else ""
                                ),
                            })
                        function_call = getattr(delta_obj, "function_call", None)
                        if function_call:
                            await on_tool_call_delta({
                                "index": 0,
                                "call_id": "",
                                "name": self._inbound_tool_name(
                                    str(_get(function_call, "name") or ""),
                                ),
                                "arguments_delta": str(_get(function_call, "arguments") or ""),
                            })
            parsed = self._parse_chunks(chunks)
            if self._is_openrouter and parsed.tool_calls:
                parsed.tool_calls = self._remap_tool_calls(parsed.tool_calls)
            return parsed
        except asyncio.TimeoutError:
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{idle_timeout_s:g} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            if self._register_temperature_rejection(model, e):
                return await self.chat_stream(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            if self._register_tool_endpoint_miss(model, e):
                return await self.chat_stream(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            if self._register_reasoning_rejection(model, e, reasoning_effort):
                return await self.chat_stream(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            await self._maybe_reset_client_after_error(e)
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)

    async def _maybe_reset_client_after_error(self, error: Exception) -> None:
        kind = (self._extract_error_metadata(error).get("error_kind") or "").lower()
        if kind in {"connection", "timeout"}:
            logger.warning(
                "Resetting OpenAI-compatible HTTP client after {} error",
                kind,
            )
            await self._reset_client()

    def get_default_model(self) -> str:
        return self.default_model
