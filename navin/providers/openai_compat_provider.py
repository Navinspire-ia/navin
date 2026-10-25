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
from contextvars import ContextVar
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
    wait_stream_idle,
)
from navin.providers.gateway import is_navin_gateway
from navin.providers.openai_responses import (
    consume_sdk_stream,
    convert_messages,
    convert_tools,
    parse_response_output,
)
from navin.providers.reasoning_control import (
    REJECT_UNSUPPORTED,
    REJECT_VALUE,
    WIRE_CHAT,
    WIRE_RESPONSES,
    ReasoningOffNegotiator,
    always_reasons,
    apply_shape,
    classify_rejection,
    non_anthropic_effort,
    off_shapes,
    openai_rejects_reasoning,
    suggested_floor,
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

# Idle time a pooled cloud socket may be reused. Long enough to span the
# model -> tools -> model gap of one turn (a fresh TCP+TLS handshake costs
# 130-230 ms per step), short enough that a NAT or proxy rarely drops it.
_CLOUD_KEEPALIVE_S = 4.0
# Set while a request replays on a fresh socket, so it is retried only once.
_FRESH_SOCKET_RETRY: ContextVar[bool] = ContextVar("_fresh_socket_retry", default=False)

_STANDARD_TC_KEYS = frozenset({"id", "type", "index", "function"})


class _ToolCallSlots:
    """Stream index -> call slot for live tool-call deltas (see _starts_new_tool_call)."""

    def __init__(self) -> None:
        self._slot_of: dict[int, int] = {}
        self._bufs: dict[int, dict[str, Any]] = {}

    def key(self, raw_index: int, tc_id: Any, fn_name: Any, args_delta: str) -> int:
        key = self._slot_of.get(raw_index, raw_index)
        current = self._bufs.get(key)
        if current is not None and _starts_new_tool_call(current, tc_id, fn_name):
            key = max(self._bufs) + 1
        self._slot_of[raw_index] = key
        buf = self._bufs.setdefault(key, {"id": "", "name": "", "arguments": ""})
        if tc_id:
            buf["id"] = str(tc_id)
        if fn_name:
            buf["name"] = str(fn_name)
        buf["arguments"] += args_delta
        return key


def _starts_new_tool_call(buf: dict[str, Any], tc_id: Any, fn_name: Any) -> bool:
    """A delta opens another call when it brings a different call id, or a
    function name after the current call's arguments are already complete."""
    if tc_id and buf["id"] and str(tc_id) != buf["id"]:
        return True
    if not (fn_name and buf["name"] and buf["arguments"].strip()):
        return False
    try:
        json.loads(buf["arguments"])
    except ValueError:
        return False
    return True

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
# Silence allowed once the model has been thinking or has started a tool call.
# Some endpoints do not stream what comes next: Kimi K3 on Bedrock (through
# LiteLLM) sends its reasoning live, then nothing at all while it writes a tool
# call, then the whole arguments at once (measured: 31 s of silence for a
# 9,600-char write_file, more for a bigger file). The usual 120 s stall rule
# cut such a turn and failed over mid-generation.
_BUFFERED_OUTPUT_IDLE_S = 420.0


def _buffered_output_idle_s() -> float:
    return _float_env("NAVIN_STREAM_BUFFERED_IDLE_S", _BUFFERED_OUTPUT_IDLE_S)
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


def _replayable_thinking_blocks(blocks: Any) -> list[dict[str, Any]] | None:
    """Claude thinking blocks that can go back to the model unchanged.

    LiteLLM returns them on Claude replies (``message.thinking_blocks``). A
    thinking block is only valid with its signature; a redacted one with its
    opaque ``data``. Anything else would be refused, so it is dropped.
    """
    if not isinstance(blocks, list):
        return None
    kept: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            dump = getattr(block, "model_dump", None)
            block = dump() if callable(dump) else None
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking" and block.get("signature"):
            kept.append({
                "type": "thinking",
                "thinking": str(block.get("thinking") or ""),
                "signature": str(block["signature"]),
            })
        elif kind == "redacted_thinking" and block.get("data"):
            kept.append({"type": "redacted_thinking", "data": str(block["data"])})
    return kept or None


# Replayed thinking blocks the gateway refused because their signature no
# longer matches the conversation before them (Bedrock: "Invalid `signature`
# in `thinking` block. The block is bound to a different conversation"). It
# happens inside one turn once the earlier context was rewritten (compaction,
# cleared tool results, injected reports). Left in the history, the block
# made every later step of the turn fail on the chosen model, so the fallback
# chain answered the rest of the turn: Opus picked, Sonnet served. Keyed by
# signature, shared by every instance: the runtime is rebuilt between turns.
_REJECTED_THINKING: dict[str, None] = {}
_MAX_REJECTED_THINKING = 2048


def _thinking_block_key(block: dict[str, Any]) -> str | None:
    if block.get("type") == "thinking" and block.get("signature"):
        return "s:" + str(block["signature"])
    if block.get("type") == "redacted_thinking" and block.get("data"):
        return "r:" + hashlib.sha256(str(block["data"]).encode()).hexdigest()
    return None


def _is_thinking_binding_rejection(error: Exception) -> bool:
    """A replayed thinking block was refused for its signature.

    Not the "Expected thinking or redacted_thinking" refusal: that one asks
    for a missing block, and removing more blocks cannot fix it.
    """
    text = f"{getattr(error, 'body', None)} {error}".lower()
    if "thinking" not in text or "signature" not in text:
        return False
    return "bound to a different conversation" in text or "invalid `signature`" in text \
        or "invalid signature" in text


def _current_turn_thinking_only(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep thinking blocks only in the current turn (after the last user message).

    A thinking signature is bound to the turn that produced it: sending back
    a previous turn's blocks gets a 400 from Bedrock ("Invalid `signature` in
    `thinking` block. The block is bound to a different conversation", seen
    live on test-02). LiteLLM may then drop thinking for the first step of a
    new turn (its warning names the previous turn's tool call); the model
    thinks again from the next step on.
    """
    last_user = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1,
    )
    return [
        {k: v for k, v in m.items() if k != "thinking_blocks"}
        if i < last_user and "thinking_blocks" in m else m
        for i, m in enumerate(messages)
    ]


def _accumulate_thinking_deltas(acc: list[dict[str, Any]], blocks: Any) -> None:
    """Fold streamed ``delta.thinking_blocks`` pieces into whole blocks.

    LiteLLM streams a Claude thinking block as text pieces, then a piece that
    carries the signature; a redacted block arrives whole. A text piece after
    a signed block opens the next block (interleaved thinking).
    """
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict):
            dump = getattr(block, "model_dump", None)
            block = dump() if callable(dump) else None
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "redacted_thinking":
            if block.get("data"):
                acc.append({"type": "redacted_thinking", "data": str(block["data"])})
            continue
        if kind != "thinking":
            continue
        text = str(block.get("thinking") or "")
        signature = block.get("signature")
        current = acc[-1] if acc and acc[-1].get("type") == "thinking" else None
        if current is None or current.get("signature"):
            if not text and not signature:
                continue
            current = {"type": "thinking", "thinking": "", "signature": ""}
            acc.append(current)
        current["thinking"] += text
        if signature:
            current["signature"] = str(signature)


def _model_slug(model_name: str) -> str:
    return model_name.lower().rsplit("/", 1)[-1]


def _provider_prefix_key(name: str) -> str:
    return to_snake(name.replace("-", "_")).lower()


def _requires_max_completion_tokens(model_name: str) -> bool:
    """Return True for models that reject ``max_tokens`` (GPT-5/6 family, o-series)."""
    slug = _model_slug(model_name)
    return "gpt-5" in slug or "gpt-6" in slug or any(
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
        if spec is not None and spec.name == "navin":
            # The subscription runs on the Navin gateway only; an OpenRouter
            # base left in an older config is not a fallback route.
            from navin.providers.gateway import navin_managed_base

            effective_base = navin_managed_base(effective_base)
            self.api_base = effective_base
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
        # Output ceilings learned from "max_tokens too large" refusals.
        self._max_output_caps: dict[str, int] = {}
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
        # Models whose native thinking toggle refused "disabled" (GLM-5.3 on
        # Z.ai: code 1210, "always engages in thinking"). Stop asking for off
        # and ask for the lowest effort the refusal named instead (None when it
        # named none: then say nothing and take the endpoint default).
        self._thinking_off_rejected: dict[str, str | None] = {}

    def _build_client(self) -> None:
        """Create the OpenAI client using the current module-level AsyncOpenAI."""
        import httpx

        timeout_s = _openai_compat_timeout_s()
        request_timeout = httpx.Timeout(
            connect=min(30.0, timeout_s),
            # The stream loop enforces its own idle windows (120 s, longer after
            # reasoning); a 120 s socket read limit fired first and reported a
            # buffered tool call as "Request timed out".
            read=max(timeout_s, _buffered_output_idle_s()),
            write=timeout_s,
            pool=30.0,
        )
        http_client: httpx.AsyncClient | None = None
        # A 200-worker wave shares this client with its parent and background
        # requests. Bound every transport consistently, including proxy pools.
        pool_limits = dict(max_connections=256, max_keepalive_connections=32)
        if self._proxy:
            http_client = httpx.AsyncClient(
                timeout=request_timeout,
                limits=httpx.Limits(**pool_limits, keepalive_expiry=_CLOUD_KEEPALIVE_S),
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
            _local_limits = httpx.Limits(**pool_limits, keepalive_expiry=0)
            http_client = httpx.AsyncClient(
                limits=_local_limits,
                timeout=request_timeout,
                transport=httpx.AsyncHTTPTransport(proxy=None, limits=_local_limits),
            )
        else:
            # Cloud providers: reuse a socket only within one turn. A stale
            # keepalive used to surface as "Error calling LLM: Connection
            # error." on the first turn of a session, so the idle window is
            # short and a connection error before any output replays at once
            # on a fresh socket (_replay_on_fresh_socket) instead of failing.
            _cloud_limits = httpx.Limits(**pool_limits, keepalive_expiry=_CLOUD_KEEPALIVE_S)
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

    @staticmethod
    async def _close_streams(streams: list[Any]) -> None:
        """Close SDK streams so their HTTP responses go back to the pool."""
        while streams:
            close = getattr(streams.pop(), "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug("openai-compat stream close failed", exc_info=True)

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

            # Off the event loop: the SSL context loads the system certificate
            # store, seconds on Windows, and every reset after a connection
            # error rebuilt it while streams and subagents waited.
            await asyncio.to_thread(self._build_client)
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
        *,
        mark_tools: bool = True,
        mark_system: bool = True,
        mark_tail: bool = True,
        mark_tool_results: bool = False,
        prefix_ttl: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Inject cache_control markers for prompt caching.

        ``mark_tool_results`` lets the tail marker land on a tool result. In a
        tool loop the newest messages are tool results: without it the marker
        walks back to the user turn and every step re-prefills the whole loop.
        ``prefix_ttl`` ("1h") keeps the tools and system prompt cached past the
        default 5 minutes, so a first message after a pause is not a full miss.
        """
        cache_marker = {"type": "ephemeral"}
        prefix_marker = {**cache_marker, "ttl": prefix_ttl} if prefix_ttl else cache_marker
        new_messages = list(messages)

        def _mark(msg: dict[str, Any], marker: dict[str, Any] = cache_marker) -> dict[str, Any]:
            content = msg.get("content")
            if isinstance(content, str):
                if not content:
                    return msg  # an empty block is not cacheable
                return {**msg, "content": [
                    {"type": "text", "text": content, "cache_control": marker},
                ]}
            if isinstance(content, list) and content:
                nc = list(content)
                # Same rule as the native Anthropic provider: walk back past
                # empty text blocks, never mark a block the API discards.
                for idx in range(len(nc) - 1, -1, -1):
                    block = nc[idx]
                    if isinstance(block, dict) and not (block.get("type") == "text" and not block.get("text")):
                        nc[idx] = {**block, "cache_control": marker}
                        return {**msg, "content": nc}
            return msg

        if mark_system and new_messages and new_messages[0].get("role") == "system":
            new_messages[0] = _mark(new_messages[0], prefix_marker)
        if mark_tail and len(new_messages) >= 3:
            # The newest turn before the last one that has text to carry the
            # marker. In a tool loop messages[-2] is an assistant turn with
            # only tool_calls (no content block to mark): stopping there left
            # the whole conversation uncached on every step. The last user
            # turn carries the runtime context and is never marked, but a
            # trailing tool result is stable and caches the whole loop.
            start = len(new_messages) - 2
            if mark_tool_results and new_messages[-1].get("role") == "tool":
                start = len(new_messages) - 1
            for idx in range(start, 0, -1):
                if new_messages[idx].get("role") == "tool" and not mark_tool_results:
                    continue
                marked = _mark(new_messages[idx])
                if marked is not new_messages[idx]:
                    new_messages[idx] = marked
                    break

        new_tools = tools
        if tools and mark_tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": prefix_marker}
        return new_messages, new_tools

    @classmethod
    def _apply_gateway_cache_control(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Cache markers on the Navin gateway (Claude on Bedrock via LiteLLM):
        each breakpoint only when the prefix it closes reaches the model's
        cache minimum. Tools come first, then the system prompt, then the turns."""
        from navin.providers.model_capabilities import prompt_cache_min_tokens
        from navin.utils.helpers import estimate_prompt_tokens

        minimum = prompt_cache_min_tokens(model_name)
        tools_tokens = estimate_prompt_tokens([], tools) if tools else 0
        system = messages[:1] if messages and messages[0].get("role") == "system" else []
        system_tokens = estimate_prompt_tokens(system, tools) if system else tools_tokens
        tail_tokens = estimate_prompt_tokens(messages[:-1], tools) if len(messages) >= 3 else 0
        return cls._apply_cache_control(
            messages, tools,
            mark_tools=tools_tokens >= minimum,
            mark_system=system_tokens >= minimum,
            mark_tail=tail_tokens >= minimum,
            mark_tool_results=True,
            prefix_ttl="1h",
        )

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

    def _replays_thinking_blocks(self, model_name: str) -> bool:
        """True for every model behind the Navin gateway (LiteLLM).

        LiteLLM reads ``thinking_blocks`` on assistant messages and hands them
        back to Bedrock. Without them a thinking turn that called a tool is
        either refused ("Expected thinking or redacted_thinking") or, with
        modify_params, silently loses thinking for every follow-up step. This
        is not Claude only: GPT and other models served through Bedrock
        Converse hit the same check (seen live on test-02 with GPT 6.1).
        Blocks only exist when LiteLLM returned them, and only the current
        turn's blocks are sent back (``_current_turn_thinking_only``).
        """
        return bool(self._spec is not None and self._spec.name == "navin")

    def _register_thinking_binding_rejection(
        self, model: str | None, messages: list[dict[str, Any]], e: Exception,
    ) -> bool:
        """Stop replaying the thinking blocks a signature refusal hit.

        The refusal names a block by its position in the gateway's own
        Anthropic payload, which does not map back to these messages, so every
        block this request replayed is retired. Blocks the model writes from
        now on are bound to the context as it is now and keep being replayed.
        True when the same model can be asked again; False once nothing is
        left to remove, so a refusal that persists still reaches the caller.
        """
        if not _is_thinking_binding_rejection(e):
            return False
        if not self._replays_thinking_blocks(model or self.default_model):
            return False
        retired = 0
        for msg in _current_turn_thinking_only(messages):
            if msg.get("role") != "assistant":
                continue
            for block in _replayable_thinking_blocks(msg.get("thinking_blocks")) or ():
                key = _thinking_block_key(block)
                if key and key not in _REJECTED_THINKING:
                    _REJECTED_THINKING[key] = None
                    retired += 1
        while len(_REJECTED_THINKING) > _MAX_REJECTED_THINKING:
            _REJECTED_THINKING.pop(next(iter(_REJECTED_THINKING)))
        if retired:
            logger.warning(
                "Model {} refused {} replayed thinking block(s): their signature is bound "
                "to an earlier context. Asking the same model again without them",
                model or self.default_model,
                retired,
            )
        return bool(retired)

    def _sanitize_messages(
        self, messages: list[dict[str, Any]], *, keep_thinking_blocks: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip non-standard keys, normalize tool_call IDs."""
        allowed = _ALLOWED_MSG_KEYS | {"thinking_blocks"} if keep_thinking_blocks else _ALLOWED_MSG_KEYS
        sanitized = LLMProvider._sanitize_request_messages(messages, allowed)
        if keep_thinking_blocks:
            for msg in sanitized:
                if "thinking_blocks" not in msg:
                    continue
                blocks = (
                    _replayable_thinking_blocks(msg.get("thinking_blocks"))
                    if msg.get("role") == "assistant" else None
                )
                if blocks and _REJECTED_THINKING:
                    blocks = [b for b in blocks if _thinking_block_key(b) not in _REJECTED_THINKING]
                if blocks:
                    msg["thinking_blocks"] = blocks
                else:
                    msg.pop("thinking_blocks", None)
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
        if self._speaks_gemini_natively():
            _sign_foreign_tool_calls(sanitized)
        return self._enforce_role_alternation(sanitized)

    def _speaks_gemini_natively(self) -> bool:
        """True when requests go straight to Google (AI Studio / Vertex OpenAI endpoint)."""
        base = (self._effective_base or "").lower()
        return bool(self._spec and self._spec.name == "gemini") or (
            "googleapis.com" in base and "openai" in base
        )

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
        if any(token in name for token in ("gpt-5", "gpt-6", "o1", "o3", "o4")):
            return False
        # Grok 4+ always reasons; Bedrock refuses temperature on it outright
        # (measured 2026-10-07, also through the LiteLLM gateway).
        if re.search(r"grok-(?:[4-9]|\d{2})", name):
            return False
        from navin.providers.claude_capabilities import claude_supports_temperature

        return claude_supports_temperature(name)

    def _temperature_model_key(self, model: str | None) -> str:
        return (model or self.default_model).lower()

    def _temperature_store_key(self, key: str) -> str:
        return f"no-temperature|{self._reasoning_off._scope}|{key}"

    def _temperature_refused(self, model: str | None) -> bool:
        key = self._temperature_model_key(model)
        if key in self._no_temperature_models:
            return True
        from navin.providers.reasoning_control import stored_flag

        if stored_flag(self._temperature_store_key(key)):
            self._no_temperature_models.add(key)
            return True
        return False

    _TEMPERATURE_REJECTION_MARKERS = (
        "temperature` is deprecated",
        "temperature is deprecated",
        "does not support temperature",
        "doesn't support the temperature",
        "does not support the temperature",
        "remove temperature",
        "temperature is not supported",
        "'temperature' is not supported",
        "unsupported parameter: 'temperature'",
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
        # Kept on disk: a gateway restart or a new provider instance (model
        # switch, subagent) must not pay this refused round trip again.
        from navin.providers.reasoning_control import remember_flag

        remember_flag(self._temperature_store_key(key))
        logger.info(
            "Model {} rejected the temperature parameter; retrying without it",
            key,
        )
        return True

    _MAX_TOKENS_REJECTION_MARKERS = (
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "maximum tokens",
        "max tokens",
        "output tokens",
        "completion tokens",
    )
    _MAX_TOKENS_LIMIT_MARKERS = (
        "too large",
        "exceed",
        "at most",
        "maximum allowed",
        "must be less than",
        "less than or equal",
        "above maximum",
        "out of range",
        "model limit",
    )
    _MAX_TOKENS_FLOOR = 1_024
    _MAX_TOKENS_RETRY_WHEN_UNSTATED = 8_192

    def _clamp_max_tokens(self, model: str | None, max_tokens: int) -> int:
        cap = self._max_output_caps.get(self._temperature_model_key(model))
        return min(max_tokens, cap) if cap else max_tokens

    def _register_max_tokens_rejection(
        self,
        model: str | None,
        e: Exception,
        max_tokens: int,
    ) -> bool:
        """Detect a "max_tokens above this model's limit" refusal.

        The default output budget is large (32k); some models stop lower.
        Learn the limit the provider states (or a safe default) and retry
        once. Loop-free: a model that already has a learned cap, or a request
        already at or under it, falls through to normal error handling.
        """
        body = getattr(e, "body", None)
        text = f"{body} {e}".lower()
        if not any(m in text for m in self._MAX_TOKENS_REJECTION_MARKERS):
            return False
        if not any(m in text for m in self._MAX_TOKENS_LIMIT_MARKERS):
            return False
        if self._is_context_overflow_text(text):
            return False
        key = self._temperature_model_key(model)
        if key in self._max_output_caps:
            return False
        numbers = [int(n) for n in re.findall(r"\d+", text.replace(",", "").replace("_", ""))]
        stated = [n for n in numbers if self._MAX_TOKENS_FLOOR <= n < max_tokens]
        cap = max(stated) if stated else self._MAX_TOKENS_RETRY_WHEN_UNSTATED
        if cap >= max_tokens:
            return False
        self._max_output_caps[key] = cap
        logger.info(
            "Model {} refused max_tokens={}; retrying with {}",
            key,
            max_tokens,
            cap,
        )
        return True

    _CONTEXT_OVERFLOW_MARKERS = (
        "maximum context length",
        "context limit",
        "context length",
        "context window",
    )
    _CONTEXT_OVERFLOW_MARGIN = 1_024

    @classmethod
    def _is_context_overflow_text(cls, text: str) -> bool:
        return any(marker in text for marker in cls._CONTEXT_OVERFLOW_MARKERS)

    @classmethod
    def _context_overflow_max_tokens(cls, e: Exception, max_tokens: int) -> int | None:
        """Output budget that fits when prompt + max_tokens overflowed the window.

        Per request, never remembered: the room left depends on this prompt.
        Reads the two shapes providers use:
        ``maximum context length is 131072 tokens ... (12000 in the messages,
        128000 in the completion)`` and ``... exceed context limit:
        188000 + 128000 > 200000``. None when the numbers are not there.
        """
        body = getattr(e, "body", None)
        text = f"{body} {e}".lower().replace(",", "")
        if not cls._is_context_overflow_text(text):
            return None
        limit = prompt = None
        summed = re.search(r"(\d+)\s*\+\s*(\d+)\s*>\s*(\d+)", text)
        if summed:
            prompt, limit = int(summed.group(1)), int(summed.group(3))
        else:
            window = re.search(r"maximum context length is (\d+)", text)
            if window:
                limit = int(window.group(1))
                in_messages = re.search(r"(\d+) in the messages", text)
                requested = re.search(r"requested (\d+) tokens", text)
                if in_messages:
                    prompt = int(in_messages.group(1))
                elif requested:
                    prompt = int(requested.group(1)) - max_tokens
        if limit is None or prompt is None:
            return None
        room = limit - prompt - cls._CONTEXT_OVERFLOW_MARGIN
        if room < cls._MAX_TOKENS_FLOOR or room >= max_tokens:
            return None
        logger.info(
            "Prompt + max_tokens={} overflowed a {}-token window; retrying with {}",
            max_tokens,
            limit,
            room,
        )
        return room

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
        return (
            "gpt-5" in slug
            or "gpt-6" in slug
            or "gpt-oss" in slug
            or _requires_max_completion_tokens(model_name)
        )

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
        if effort and effort not in ("none", "minimal", "minimum"):
            from navin.providers.reasoning_control import learn_accepted_efforts

            if learn_accepted_efforts(key, effort, e):
                # e.g. GLM 5.3 "please use low, high, or max" for "medium".
                return True
        if (
            effort in ("none", "minimal", "minimum")
            and key not in self._thinking_off_rejected
            and _thinking_styles_for(self._spec, model_name)
            and classify_rejection(e) == REJECT_VALUE
        ):
            self._thinking_off_rejected[key] = suggested_floor(e)
            logger.info(
                "Model {} refused to disable thinking; retrying at the lowest effort",
                key,
            )
            return True
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
                if spec.name == "navin" and is_navin_gateway(self.api_base):
                    messages, tools = self._apply_gateway_cache_control(messages, tools, model_name)
                else:
                    messages, tools = self._apply_cache_control(messages, tools)

        model_name = self._request_model_name(model_name)

        if self._is_openrouter and tools:
            tools = self._alias_tools_for_openrouter(tools)
            messages = self._alias_messages_for_openrouter(messages)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_messages(
                self._sanitize_empty_content(
                    _current_turn_thinking_only(messages)
                    if self._replays_thinking_blocks(model_name) else messages
                ),
                keep_thinking_blocks=self._replays_thinking_blocks(model_name),
            ),
        }

        # GPT-5 and reasoning models (o1/o3/o4) reject temperature when
        # reasoning_effort is active.  Only include it when safe.
        if (
            self._supports_temperature(model_name, reasoning_effort)
            and not self._temperature_refused(model)
        ):
            kwargs["temperature"] = temperature

        max_tokens = self._clamp_max_tokens(model, max_tokens)
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
        reasoning_effort = non_anthropic_effort(reasoning_effort)
        semantic_effort: str | None = None
        if isinstance(reasoning_effort, str):
            semantic_effort = reasoning_effort.lower()
            if semantic_effort == "minimum":
                semantic_effort = "minimal"

        if semantic_effort == "minimal" and "claude" in _model_slug(model_name):
            # Claude has no rung below "low": "minimal" means no thinking, as
            # on the native Anthropic and Bedrock wires.
            semantic_effort = "none"
            reasoning_effort = "none"

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
            or openai_rejects_reasoning(model_name)
            or self._temperature_model_key(model) in self._reasoning_knob_rejected
        ):
            strip_effort = True

        # A value this model is known, or learned, to refuse goes out as the
        # nearest one it takes: a routed "xhigh" or "none" must never fail a step.
        if isinstance(semantic_effort, str) and semantic_effort:
            from navin.providers.reasoning_control import accepted_efforts, nearest_effort

            fitted = nearest_effort(
                semantic_effort,
                accepted_efforts(
                    model_name,
                    self._temperature_model_key(model),
                    include_openai=semantic_effort != "none",
                ),
            )
            if fitted != semantic_effort:
                semantic_effort = fitted
                wire_effort = fitted

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
            model_key = self._temperature_model_key(model)
            off_refused = not thinking_enabled and model_key in self._thinking_off_rejected
            floor = self._thinking_off_rejected.get(model_key) if off_refused else None
            if floor and not strip_effort:
                kwargs["reasoning_effort"] = floor
            for thinking_style in _thinking_styles_for(spec, model_name):
                if off_refused:
                    continue
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
        model_name = (model or self.default_model).lower()
        if self._spec is None or self._spec.name != "github_copilot":
            if not _is_direct_openai_base(self._effective_base):
                return False
        elif not re.search(r"(?:^|/)(?:gpt-|o[134](?:-|$)|codex)", model_name):
            # Copilot serves Claude, Gemini and Grok on /chat/completions only;
            # its /responses endpoint is for OpenAI models.
            return False

        wants = False
        if reasoning_effort and reasoning_effort.lower() != "none":
            wants = True
        elif any(token in model_name for token in ("gpt-5", "gpt-6", "o1", "o3", "o4")):
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
        max_tokens = self._clamp_max_tokens(model, max_tokens)
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

        reasoning_effort = non_anthropic_effort(reasoning_effort)
        if self._supports_temperature(model_name, reasoning_effort):
            body["temperature"] = temperature

        effort_lower = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
        knob_rejected = (
            self._temperature_model_key(model) in self._reasoning_knob_rejected
            or openai_rejects_reasoning(model_name)
        )
        if effort_lower and effort_lower != "none":
            # Same fitting as Chat Completions: a documented or learned ladder
            # turns a refused level into the nearest accepted one, so a refusal
            # is never replayed with the very value that failed.
            from navin.providers.reasoning_control import accepted_efforts, nearest_effort

            effort_lower = nearest_effort(
                effort_lower,
                accepted_efforts(model_name, self._temperature_model_key(model)),
            )
            reasoning_effort = effort_lower
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

        # --- Anthropic-shaped cache counters (Claude behind LiteLLM / the
        # Navin gateway): a write is billed differently from a read. ---
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            value = cls._get_nested_int(usage_map, (key,)) if usage_map is not None else 0
            if not value and usage_obj is not None:
                value = cls._get_nested_int(usage_obj, (key,))
            if value:
                result[key] = value
        if "cached_tokens" not in result and result.get("cache_read_input_tokens"):
            result["cached_tokens"] = result["cache_read_input_tokens"]

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
                thinking_blocks=_replayable_thinking_blocks(msg0.get("thinking_blocks")),
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
            thinking_blocks=_replayable_thinking_blocks(getattr(msg, "thinking_blocks", None)),
        )

    @classmethod
    def _parse_chunks(cls, chunks: list[Any]) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        thinking_acc: list[dict[str, Any]] = []
        finish_reason = "stop"
        usage: dict[str, int] = {}

        # Raw stream index -> buffer key. Gemini's OpenAI endpoint sends each
        # parallel call whole, all under index 0: without a split they would
        # merge into one call with arguments like '{...}{...}'.
        slot_of: dict[int, int] = {}

        def _accum_tc(tc: Any, idx_hint: int) -> None:
            """Accumulate one streaming tool-call delta into *tc_bufs*."""
            raw_index: int = _get(tc, "index") if _get(tc, "index") is not None else idx_hint
            tc_id = _get(tc, "id")
            fn = _get(tc, "function")
            fn_name = _get(fn, "name") if fn is not None else None
            key = slot_of.get(raw_index, raw_index)
            current = tc_bufs.get(key)
            if current is not None and _starts_new_tool_call(current, tc_id, fn_name):
                key = max(tc_bufs) + 1
            slot_of[raw_index] = key
            buf = tc_bufs.setdefault(key, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            if tc_id:
                buf["id"] = str(tc_id)
            if fn is not None:
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
                _accumulate_thinking_deltas(thinking_acc, delta.get("thinking_blocks"))
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
            if delta:
                _accumulate_thinking_deltas(thinking_acc, getattr(delta, "thinking_blocks", None))
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
            thinking_blocks=_replayable_thinking_blocks(thinking_acc),
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
        from navin.providers.user_facing_errors import is_model_unavailable_text

        if not is_model_unavailable_text(text):
            # A model the gateway's provider account cannot reach says nothing
            # about our key: re-validating the license would not help.
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
        reasoning_effort = non_anthropic_effort(reasoning_effort)
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
            if self._register_thinking_binding_rejection(model, messages, e):
                return await self.chat(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                )
            if self._register_temperature_rejection(model, e):
                return await self.chat(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                )
            room = self._context_overflow_max_tokens(e, max_tokens)
            if room is not None:
                return await self.chat(
                    messages, tools, model, room, temperature,
                    reasoning_effort, tool_choice,
                )
            if self._register_max_tokens_rejection(model, e, max_tokens):
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
            if await self._replay_on_fresh_socket(e, delivered=False):
                token = _FRESH_SOCKET_RETRY.set(True)
                try:
                    return await self.chat(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                finally:
                    _FRESH_SOCKET_RETRY.reset(token)
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
        reasoning_effort = non_anthropic_effort(reasoning_effort)
        idle_timeout_s = resolve_stream_idle_timeout_s()
        # A replay after a stale socket is safe only while nothing streamed.
        delivered = False
        caller_callbacks = (on_content_delta, on_thinking_delta, on_tool_call_delta)

        def _tracked(callback):
            if callback is None:
                return None

            async def _forward(value):
                nonlocal delivered
                delivered = True
                await callback(value)

            return _forward

        on_content_delta = _tracked(on_content_delta)
        on_thinking_delta = _tracked(on_thinking_delta)
        on_tool_call_delta = _tracked(on_tool_call_delta)
        open_streams: list[Any] = []
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    body["stream"] = True
                    stream = await self._client.responses.create(**body)
                    open_streams.append(stream)

                    async def _timed_stream():
                        stream_iter = stream.__aiter__()
                        while True:
                            try:
                                yield await wait_stream_idle(
                                    stream_iter.__anext__(),
                                    idle_timeout_s,
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
                        on_reasoning_delta=on_thinking_delta,
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
                    await self._close_streams(open_streams)
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
            open_streams.append(stream)
            live_slots = _ToolCallSlots()
            chunks: list[Any] = []
            stream_iter = stream.__aiter__()
            buffered_idle_s = max(idle_timeout_s, _buffered_output_idle_s())
            # True after reasoning or a tool-call start: what follows may come
            # in one piece at the end, so silence is not a stall yet. A request
            # that asked for reasoning starts that way: Claude behind LiteLLM /
            # Bedrock can think for minutes before its first streamed byte
            # when the thinking text itself is not streamed.
            producing_unstreamed = isinstance(reasoning_effort, str) and (
                reasoning_effort.strip().lower() not in ("", "none", "minimal", "off")
            )
            while True:
                window_s = buffered_idle_s if producing_unstreamed else idle_timeout_s
                try:
                    chunk = await wait_stream_idle(
                        stream_iter.__anext__(),
                        window_s,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    idle_timeout_s = window_s  # reported in the stall message
                    raise
                chunks.append(chunk)
                if chunk.choices:
                    delta_obj = chunk.choices[0].delta
                    if getattr(delta_obj, "tool_calls", None) or getattr(delta_obj, "function_call", None):
                        producing_unstreamed = True
                    elif getattr(delta_obj, "reasoning_content", None) or getattr(delta_obj, "reasoning", None):
                        producing_unstreamed = True
                    elif self._extract_text_content(getattr(delta_obj, "content", None)):
                        producing_unstreamed = False
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
                                # Same split as _parse_chunks, so the live
                                # cards match the calls that will run.
                                "index": live_slots.key(
                                    tool_index if tool_index is not None else idx,
                                    _get(tool_delta, "id"),
                                    _get(fn, "name") if fn is not None else None,
                                    str(_get(fn, "arguments") or "") if fn is not None else "",
                                ),
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
            # A stalled stream usually means a dead socket: drop it and the
            # pool, like other connection-level failures.
            await self._close_streams(open_streams)
            await self._reset_client()
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{idle_timeout_s:g} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            await self._close_streams(open_streams)
            if not delivered and self._register_thinking_binding_rejection(model, messages, e):
                return await self.chat_stream(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            if self._register_temperature_rejection(model, e):
                return await self.chat_stream(
                    messages, tools, model, max_tokens, temperature,
                    reasoning_effort, tool_choice,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            room = None if delivered else self._context_overflow_max_tokens(e, max_tokens)
            if room is not None:
                return await self.chat_stream(
                    messages, tools, model, room, temperature,
                    reasoning_effort, tool_choice,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                )
            if not delivered and self._register_max_tokens_rejection(model, e, max_tokens):
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
            if await self._replay_on_fresh_socket(e, delivered=delivered):
                token = _FRESH_SOCKET_RETRY.set(True)
                try:
                    return await self.chat_stream(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                        on_content_delta=caller_callbacks[0],
                        on_thinking_delta=caller_callbacks[1],
                        on_tool_call_delta=caller_callbacks[2],
                    )
                finally:
                    _FRESH_SOCKET_RETRY.reset(token)
            await self._maybe_reset_client_after_error(e)
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)
        finally:
            await self._close_streams(open_streams)

    async def _replay_on_fresh_socket(self, error: Exception, delivered: bool) -> bool:
        """Drop the pool and allow one immediate replay after a stale socket.

        Only for cloud pools (local ones never keep sockets), only before any
        output reached the caller, and only once per request.
        """
        if delivered or self._is_local or _FRESH_SOCKET_RETRY.get():
            return False
        kind = (self._extract_error_metadata(error).get("error_kind") or "").lower()
        if kind != "connection":
            return False
        logger.info("Pooled LLM connection was stale; replaying on a fresh socket")
        await self._reset_client()
        await self._ensure_client()
        return True

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


# Google's documented placeholder for function calls Gemini did not produce.
_GEMINI_FOREIGN_SIGNATURE = "skip_thought_signature_validator"


def _sign_foreign_tool_calls(messages: list[dict[str, Any]]) -> None:
    """Give the current turn's unsigned tool calls Gemini's placeholder signature.

    Gemini 3 validates thought signatures on every function call of the
    current turn. A call another model made (a failover or a model switch
    mid-turn: Claude, GLM, GPT...) has none, and the request failed with
    "Function call is missing a thought_signature". Calls Gemini made keep
    their real signature; older turns are not validated.
    """
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    for message in messages[last_user + 1:]:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            if not isinstance(call, dict):
                continue
            extra = call.get("extra_content") if isinstance(call.get("extra_content"), dict) else {}
            google = extra.get("google") if isinstance(extra.get("google"), dict) else {}
            if google.get("thought_signature"):
                continue
            call["extra_content"] = {
                **extra,
                "google": {**google, "thought_signature": _GEMINI_FOREIGN_SIGNATURE},
            }
