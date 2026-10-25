# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""AWS Bedrock Converse provider."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from loguru import logger

from navin.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    parse_tool_arguments,
    resolve_stream_idle_timeout_s,
    tool_arguments_object_for_replay,
    wait_stream_idle,
)
from navin.providers.claude_capabilities import claude_version
from navin.providers.reasoning_control import non_anthropic_effort

_IMAGE_DATA_URL = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)
_TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
_NOOP_TOOL_NAME = "navin_noop"
_CACHE_POINT = {"cachePoint": {"type": "default"}}
# Models that refused temperature at runtime (a release the rules predate).
_NO_TEMPERATURE_LEARNED: set[str] = set()
# Models that refused every reasoning field we sent: they run on their default.
_NO_REASONING_LEARNED: set[str] = set()
# Models that refused ``thinking.display``: adaptive thinking goes out bare.
_NO_DISPLAY_LEARNED: set[str] = set()
_REASONING_WORDS = ("reasoning", "thinking", "effort", "output_config")
# Replayed thinking blocks Bedrock refused because their signature is bound to
# a context that has since changed ("Invalid signature in thinking block. The
# block is bound to a different conversation"). Left in the history, one such
# block failed every later request of the session, "hello" included. Keyed by
# signature and shared by every instance: the runtime is rebuilt between turns.
_REJECTED_THINKING: dict[str, None] = {}
_MAX_REJECTED_THINKING = 2048
_REFUSAL_WORDS = (
    "unknown parameter", "not supported", "unsupported", "extra inputs",
    "not permitted", "invalid", "unrecognized",
)


def _supports_cache_points(model_id: str) -> bool:
    """Claude on Bedrock accepts cachePoint from 3.5 Haiku / 3.7 Sonnet on.

    Older Claude models reject the block, so they are left untouched; an
    unparseable (future) Claude name is treated as new.
    """
    name = model_id.lower()
    if "claude" not in name:
        return False
    if "claude-3-5-haiku" in name:
        return True
    version = claude_version(name)
    return version is None or version >= (3, 7)


def _has_cache_point(blocks: list[dict[str, Any]] | None) -> bool:
    return any(isinstance(block, dict) and "cachePoint" in block for block in blocks or ())


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _thinking_key(block: dict[str, Any]) -> str | None:
    if block.get("signature"):
        return "s:" + str(block["signature"])
    redacted = block.get("redactedContentBase64") or block.get("redactedContent")
    if redacted:
        raw = redacted if isinstance(redacted, bytes) else str(redacted).encode()
        return "r:" + hashlib.sha256(raw).hexdigest()
    return None


def _is_thinking_binding_rejection(text: str) -> bool:
    """A replayed thinking block was refused for its signature.

    Not the "Expected thinking or redacted_thinking" refusal: that one asks
    for a missing block, and removing more blocks cannot fix it.
    """
    text = text.lower()
    if "thinking" not in text or "signature" not in text:
        return False
    return "bound to a different conversation" in text or "invalid signature" in text \
        or "invalid `signature`" in text


def _current_turn_index(messages: list[dict[str, Any]]) -> int:
    """Index of the last user message: thinking before it is never replayed.

    A thinking signature is bound to the exact system prompt and history it
    was written under. The system prompt moves between turns (date, memory,
    skills), so a previous turn's block came back as "bound to a different
    conversation" and every new message of the session failed.
    """
    return max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)


def _next_or_none(iterator: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


class BedrockProvider(LLMProvider):
    """LLM provider using AWS Bedrock Runtime's Converse APIs."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        # No vendor default on purpose: the factory always passes the model.
        default_model: str = "",
        *,
        region: str | None = None,
        profile: str | None = None,
        role_arn: str | None = None,
        extra_body: dict[str, Any] | None = None,
        client: Any | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        self.profile = profile
        self.role_arn = role_arn
        self._extra_body = extra_body or {}
        self._client = client if client is not None else self._make_client()

    def _make_client(self) -> Any:
        if self.api_key:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.api_key
        from navin.providers.bedrock_media import DEFAULT_REGION, aws_session

        session = aws_session(self.profile, self.role_arn)
        # The AWS profile's own region still wins over the default.
        self.region = self.region or getattr(session, "region_name", None) or DEFAULT_REGION

        from botocore.config import Config

        # Long reads cover slow extended-thinking streams; botocore retries are
        # off because the provider layer runs its own retry ladder.
        client_kwargs: dict[str, Any] = {
            "config": Config(
                # A socket read limit, not a deadline: streams have their own
                # idle watchdog, but a non-streamed converse() sends nothing
                # until the whole answer exists, which can take longer than
                # ten minutes at high effort with a 128k output budget.
                read_timeout=3600,
                connect_timeout=30,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        }
        client_kwargs["region_name"] = self.region
        if self.api_base:
            client_kwargs["endpoint_url"] = self.api_base
        return session.client("bedrock-runtime", **client_kwargs)

    @staticmethod
    def _strip_prefix(model: str) -> str:
        if model.startswith("bedrock/"):
            return model[len("bedrock/"):]
        return model

    @classmethod
    def _supports_temperature(cls, model: str) -> bool:
        # Version-based rule shared with the other providers: Claude 4.7+
        # deprecated temperature, unknown/future Claude models fail safe.
        # Measured on Bedrock 2026-10-07: GPT-5.6/6 and Grok 4.7 refuse it too.
        from navin.providers.claude_capabilities import claude_supports_temperature

        name = model.lower()
        if name in _NO_TEMPERATURE_LEARNED:
            return False
        if re.search(r"(?:^|[./])openai\.(?:gpt-[5-9]|o[1-9])", name) or re.search(r"(?:^|[./])xai\.grok-[4-9]", name):
            return False
        return claude_supports_temperature(model)

    @staticmethod
    def _vendor_reasoning_fields(model_id: str, reasoning_effort: str | None) -> dict[str, Any]:
        """Reasoning fields for the non-Claude families Bedrock serves.

        Measured 2026-10-07: GPT takes ``reasoning: {effort}`` (none, low ..
        max; "minimal" and ``reasoning_effort`` are refused), Grok takes
        ``reasoning_effort``, GLM takes ``thinking.type``. MiniMax always
        reasons and has no knob. Without these, "none" reached GPT as its
        default effort.
        """
        if not reasoning_effort:
            return {}
        effort = reasoning_effort.lower()
        if effort == "adaptive":
            return {}
        effort = non_anthropic_effort(effort) or effort
        name = model_id.lower()
        if re.search(r"(?:^|[./])openai\.", name):
            level = "none" if effort in ("minimal", "off") else effort
            return {"reasoning": {"effort": level}}
        if re.search(r"(?:^|[./])xai\.", name):
            level = "none" if effort in ("minimal", "off") else effort
            return {"reasoning_effort": level}
        if re.search(r"(?:^|[./])zai\.glm", name):
            off = effort in ("none", "minimal", "off")
            return {"thinking": {"type": "disabled" if off else "enabled"}}
        return {}

    @staticmethod
    def _image_url_block(block: dict[str, Any]) -> dict[str, Any] | None:
        url = (block.get("image_url") or {}).get("url", "")
        if not isinstance(url, str) or not url:
            return None
        match = _IMAGE_DATA_URL.match(url)
        if not match:
            return {"text": f"(image URL: {url})"}
        fmt = match.group(1).lower()
        if fmt == "jpg":
            fmt = "jpeg"
        try:
            data = base64.b64decode(match.group(2), validate=False)
        except Exception:
            return {"text": "(invalid image data)"}
        return {"image": {"format": fmt, "source": {"bytes": data}}}

    @classmethod
    def _content_blocks(cls, content: Any, *, for_tool_result: bool = False) -> list[dict[str, Any]]:
        if isinstance(content, str) or content is None:
            return [{"text": content or "(empty)"}]
        if not isinstance(content, list):
            if for_tool_result and isinstance(content, dict):
                return [{"json": content}]
            return [{"text": str(content)}]

        blocks: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                blocks.append({"text": str(item)})
                continue

            item_type = item.get("type")
            if item_type in _TEXT_BLOCK_TYPES or "text" in item:
                text = item.get("text")
                if text:
                    blocks.append({"text": str(text)})
                continue
            if item_type == "image_url":
                converted = cls._image_url_block(item)
                if converted:
                    blocks.append(converted)
                continue

            # Preserve already-Bedrock-shaped content where possible.
            for key in ("text", "image", "document", "video", "json", "searchResult", "cachePoint"):
                if key in item:
                    blocks.append({key: item[key]})
                    break
            else:
                blocks.append({"json": item} if for_tool_result else {"text": json.dumps(item)})

        return blocks or [{"text": "(empty)"}]

    @classmethod
    def _system_blocks(cls, content: Any) -> list[dict[str, Any]]:
        return [
            block for block in cls._content_blocks(content)
            if "text" in block or "cachePoint" in block or "guardContent" in block
        ]

    @classmethod
    def _tool_result_block(cls, msg: dict[str, Any]) -> dict[str, Any]:
        return {
            "toolResult": {
                "toolUseId": str(msg.get("tool_call_id") or ""),
                "content": cls._content_blocks(msg.get("content"), for_tool_result=True),
                "status": "success",
            }
        }

    @staticmethod
    def _tool_use_block(tool_call: dict[str, Any]) -> dict[str, Any] | None:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return None
        args = tool_arguments_object_for_replay(function.get("arguments", {}))
        return {
            "toolUse": {
                "toolUseId": str(tool_call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "input": args,
            }
        }

    @staticmethod
    def _reasoning_block(block: dict[str, Any]) -> dict[str, Any] | None:
        if block.get("type") not in {"thinking", "reasoning", "redacted_thinking"}:
            return None
        text = block.get("thinking") or block.get("text")
        signature = block.get("signature")
        if text and signature:
            return {
                "reasoningContent": {
                    "reasoningText": {"text": str(text), "signature": str(signature)}
                }
            }
        redacted = block.get("redactedContent")
        if redacted is None and isinstance(block.get("redactedContentBase64"), str):
            try:
                redacted = base64.b64decode(block["redactedContentBase64"])
            except Exception:
                redacted = None
        if redacted is not None:
            return {"reasoningContent": {"redactedContent": redacted}}
        return None

    @classmethod
    def _assistant_blocks(cls, msg: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []

        for thinking in msg.get("thinking_blocks") or []:
            if isinstance(thinking, dict):
                reasoning = cls._reasoning_block(thinking)
                if reasoning:
                    blocks.append(reasoning)

        content = msg.get("content")
        if isinstance(content, str) and content:
            blocks.append({"text": content})
        elif isinstance(content, list):
            blocks.extend(block for block in cls._content_blocks(content) if "text" in block)

        for tool_call in msg.get("tool_calls") or []:
            if isinstance(tool_call, dict):
                block = cls._tool_use_block(tool_call)
                if block:
                    blocks.append(block)

        return blocks or [{"text": ""}]

    @staticmethod
    def _without_stale_thinking(msg: dict[str, Any], *, keep: bool) -> dict[str, Any]:
        """The message minus thinking from earlier turns or already refused."""
        blocks = [
            block for block in msg.get("thinking_blocks") or []
            if keep and isinstance(block, dict) and _thinking_key(block) not in _REJECTED_THINKING
        ]
        return {**msg, "thinking_blocks": blocks or None}

    @staticmethod
    def _retire_replayed_thinking(model_id: str, messages: list[dict[str, Any]], text: str) -> bool:
        """Stop replaying the thinking blocks a signature refusal hit.

        The refusal names a block by its position in Bedrock's payload, which
        does not map back to these messages reliably, so every block this
        request replayed is retired. Blocks written from now on are bound to
        the context as it is now and keep being replayed. True when the same
        model can be asked again; False once nothing is left to remove.
        """
        if not _is_thinking_binding_rejection(text):
            return False
        current_turn = _current_turn_index(messages)
        retired = 0
        for msg in messages[current_turn + 1:]:
            if msg.get("role") != "assistant":
                continue
            for block in msg.get("thinking_blocks") or []:
                key = _thinking_key(block) if isinstance(block, dict) else None
                if key and key not in _REJECTED_THINKING:
                    _REJECTED_THINKING[key] = None
                    retired += 1
        while len(_REJECTED_THINKING) > _MAX_REJECTED_THINKING:
            _REJECTED_THINKING.pop(next(iter(_REJECTED_THINKING)))
        if retired:
            logger.warning(
                "Bedrock {} refused {} replayed thinking block(s) bound to an earlier "
                "context; asking again without them", model_id, retired,
            )
        return bool(retired)

    @staticmethod
    def _has_tool_use(msg: dict[str, Any]) -> bool:
        content = msg.get("content")
        return isinstance(content, list) and any(
            isinstance(block, dict) and "toolUse" in block for block in content
        )

    @staticmethod
    def _merge_consecutive(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for msg in messages:
            if merged and merged[-1].get("role") == msg.get("role"):
                prev = merged[-1].setdefault("content", [])
                cur = msg.get("content") or []
                if not isinstance(prev, list):
                    prev = [{"text": str(prev)}]
                    merged[-1]["content"] = prev
                if isinstance(cur, list):
                    prev.extend(cur)
                else:
                    prev.append({"text": str(cur)})
            else:
                merged.append(msg)

        last_popped: dict[str, Any] | None = None
        while merged and merged[-1].get("role") == "assistant":
            last_popped = merged.pop()
        if not merged and last_popped is not None and not BedrockProvider._has_tool_use(last_popped):
            merged.append({"role": "user", "content": last_popped.get("content") or [{"text": "(empty)"}]})
        if merged and merged[0].get("role") == "assistant" and not BedrockProvider._has_tool_use(merged[0]):
            merged.insert(0, {"role": "user", "content": [{"text": "(conversation continued)"}]})
        return merged

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system: list[dict[str, Any]] = []
        converted: list[dict[str, Any]] = []
        current_turn = _current_turn_index(messages)

        for index, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                system.extend(self._system_blocks(content))
                continue
            if role == "tool":
                block = self._tool_result_block(msg)
                if converted and converted[-1].get("role") == "user":
                    converted[-1].setdefault("content", []).append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
                continue
            if role == "assistant":
                if index < current_turn or _REJECTED_THINKING:
                    msg = self._without_stale_thinking(msg, keep=index > current_turn)
                converted.append({"role": "assistant", "content": self._assistant_blocks(msg)})
                continue
            if role == "user":
                converted.append({"role": "user", "content": self._content_blocks(content)})

        return system, self._merge_consecutive(converted)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        result: list[dict[str, Any]] = []
        for tool in tools:
            func = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            if not isinstance(func, dict):
                continue
            name = str(func.get("name") or "")
            if not name:
                continue
            spec: dict[str, Any] = {
                "name": name,
                "inputSchema": {
                    "json": func.get("parameters") or {"type": "object", "properties": {}}
                },
            }
            description = func.get("description")
            if description:
                spec["description"] = str(description)
            strict = func.get("strict", tool.get("strict"))
            if isinstance(strict, bool):
                spec["strict"] = strict
            result.append({"toolSpec": spec})
        return result or None

    @staticmethod
    def _contains_tool_blocks(messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and ("toolUse" in block or "toolResult" in block):
                    return True
        return False

    @staticmethod
    def _noop_tool() -> dict[str, Any]:
        return {
            "toolSpec": {
                "name": _NOOP_TOOL_NAME,
                "description": "Internal placeholder for Bedrock tool history validation.",
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        }

    @staticmethod
    def _convert_tool_choice(
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if tool_choice is None or tool_choice == "auto":
            return {"auto": {}}
        if tool_choice == "required":
            return {"any": {}}
        if tool_choice == "none":
            return None
        if isinstance(tool_choice, dict):
            name = tool_choice.get("function", {}).get("name")
            if name:
                return {"tool": {"name": str(name)}}
        return {"auto": {}}

    def _thinking_request(
        self,
        model_id: str,
        reasoning_effort: str | None,
        max_tokens: int,
    ) -> tuple[dict[str, Any], int]:
        """Claude's reasoning fields for this effort, and the max_tokens they need.

        No effort configured means no field: the model keeps its default. An
        explicit effort is always spelled out, "none" included, in the form
        each generation accepts (see claude_capabilities.claude_thinking_fields).
        """
        from navin.providers.claude_capabilities import claude_thinking_fields

        return claude_thinking_fields(model_id, reasoning_effort, max_tokens=max_tokens)

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
        model_id = self._strip_prefix(model or self.default_model)
        system, bedrock_messages = self._convert_messages(self._sanitize_empty_content(messages))
        if not bedrock_messages:
            bedrock_messages = [{"role": "user", "content": [{"text": "(empty)"}]}]

        if model_id.lower() in _NO_REASONING_LEARNED:
            reasoning_effort = None
        if "claude" in model_id.lower():
            from navin.providers.claude_capabilities import clamp_claude_max_tokens

            max_tokens = clamp_claude_max_tokens(model_id, max_tokens)
        thinking_fields, max_tokens = self._thinking_request(model_id, reasoning_effort, max_tokens)
        thinking = thinking_fields.get("thinking")
        if thinking and "display" in thinking and model_id.lower() in _NO_DISPLAY_LEARNED:
            thinking = {k: v for k, v in thinking.items() if k != "display"}
            thinking_fields = {**thinking_fields, "thinking": thinking}
        kwargs: dict[str, Any] = {
            "modelId": model_id,
            "messages": bedrock_messages,
            "inferenceConfig": {"maxTokens": max(1, max_tokens)},
        }
        if system:
            kwargs["system"] = system
        thinking_on = bool(thinking) and thinking.get("type") not in ("disabled", "between_tools")
        if self._supports_temperature(model_id):
            # Extended thinking only runs at temperature 1.
            kwargs["inferenceConfig"]["temperature"] = 1.0 if thinking_on else temperature

        additional: dict[str, Any] = dict(thinking_fields) or self._vendor_reasoning_fields(
            model_id, reasoning_effort
        )
        if self._extra_body:
            additional = _deep_merge(additional, self._extra_body)
        if additional:
            kwargs["additionalModelRequestFields"] = additional

        bedrock_tools = self._convert_tools(tools)
        tool_config: dict[str, Any] | None = None
        if bedrock_tools:
            tool_config = {"tools": bedrock_tools}
            if tool_choice not in (None, "auto", "none") and "claude" in model_id.lower():
                # Claude refuses forced tool use while thinking runs, and the
                # newest generations refuse it outright.
                from navin.providers.claude_capabilities import claude_rejects_forced_tool_choice

                if thinking_on or claude_rejects_forced_tool_choice(model_id):
                    tool_choice = "auto"
            choice = self._convert_tool_choice(tool_choice)
            if choice:
                tool_config["toolChoice"] = choice
        elif self._contains_tool_blocks(bedrock_messages):
            tool_config = {"tools": [self._noop_tool()]}

        if tool_config:
            kwargs["toolConfig"] = tool_config

        if _supports_cache_points(model_id):
            self._add_cache_points(kwargs)
        return kwargs

    @staticmethod
    def _add_cache_points(kwargs: dict[str, Any]) -> None:
        """Mark tools, system and conversation end as cacheable prefixes.

        Without them every step re-read the whole prompt at full price and
        speed. The last-message point lets the next step of a turn read all
        of this one from cache. Three of Bedrock's four allowed points; a
        caller that already placed its own is left alone.
        """
        system = kwargs.get("system")
        messages = kwargs.get("messages") or []
        if _has_cache_point(system) or any(
            _has_cache_point(message.get("content")) for message in messages
        ):
            return
        tools = (kwargs.get("toolConfig") or {}).get("tools")
        if tools:
            tools.append(dict(_CACHE_POINT))
        if system:
            system.append(dict(_CACHE_POINT))
        if messages and isinstance(messages[-1].get("content"), list):
            messages[-1]["content"].append(dict(_CACHE_POINT))

    @staticmethod
    def _finish_reason(stop_reason: str | None) -> str:
        return {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }.get(stop_reason or "", stop_reason or "stop")

    @staticmethod
    def _usage(usage: dict[str, Any] | None) -> dict[str, int]:
        if not usage:
            return {}
        prompt = int(usage.get("inputTokens") or 0)
        completion = int(usage.get("outputTokens") or 0)
        total = int(usage.get("totalTokens") or prompt + completion)
        result = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }
        cache_read = int(usage.get("cacheReadInputTokens") or 0)
        cache_write = int(usage.get("cacheWriteInputTokens") or 0)
        if cache_read:
            result["cached_tokens"] = cache_read
            result["cache_read_input_tokens"] = cache_read
        if cache_write:
            result["cache_creation_input_tokens"] = cache_write
        return result

    @staticmethod
    def _parse_reasoning(block: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        reasoning = block.get("reasoningContent")
        if not isinstance(reasoning, dict):
            return None, None
        text_obj = reasoning.get("reasoningText")
        if isinstance(text_obj, dict):
            text = text_obj.get("text")
            if isinstance(text, str):
                return text, {
                    "type": "thinking",
                    "thinking": text,
                    "signature": text_obj.get("signature", ""),
                }
        redacted = reasoning.get("redactedContent")
        if redacted is not None:
            if isinstance(redacted, (bytes, bytearray)):
                encoded = base64.b64encode(bytes(redacted)).decode("ascii")
                return None, {"type": "redacted_thinking", "redactedContentBase64": encoded}
            return None, {"type": "redacted_thinking", "redactedContent": redacted}
        return None, None

    @classmethod
    def _parse_response(cls, response: dict[str, Any]) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        thinking_blocks: list[dict[str, Any]] = []
        message = (response.get("output") or {}).get("message") or {}

        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                content_parts.append(block["text"])
            tool_use = block.get("toolUse")
            if isinstance(tool_use, dict):
                arguments = tool_use.get("input", {})
                tool_calls.append(ToolCallRequest(
                    id=str(tool_use.get("toolUseId") or ""),
                    name=str(tool_use.get("name") or ""),
                    arguments=arguments,
                ))
            reasoning_text, thinking = cls._parse_reasoning(block)
            if reasoning_text:
                reasoning_parts.append(reasoning_text)
            if thinking:
                thinking_blocks.append(thinking)

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=cls._finish_reason(response.get("stopReason")),
            usage=cls._usage(response.get("usage")),
            reasoning_content="".join(reasoning_parts) or None,
            thinking_blocks=thinking_blocks or None,
        )

    @classmethod
    def _parse_stream_event(
        cls,
        event: dict[str, Any],
        *,
        content_parts: list[str],
        reasoning_parts: list[str],
        thinking_blocks: list[dict[str, Any]],
        tool_buffers: dict[int, dict[str, Any]],
        state: dict[str, Any],
    ) -> str | None:
        if "contentBlockStart" in event:
            data = event["contentBlockStart"]
            idx = int(data.get("contentBlockIndex") or 0)
            start = data.get("start") or {}
            tool_use = start.get("toolUse")
            if isinstance(tool_use, dict):
                tool_buffers[idx] = {
                    "id": str(tool_use.get("toolUseId") or ""),
                    "name": str(tool_use.get("name") or ""),
                    "input": "",
                }
            return None

        if "contentBlockDelta" in event:
            data = event["contentBlockDelta"]
            idx = int(data.get("contentBlockIndex") or 0)
            delta = data.get("delta") or {}
            text = delta.get("text")
            if isinstance(text, str):
                content_parts.append(text)
                return text
            tool_delta = delta.get("toolUse")
            if isinstance(tool_delta, dict):
                buf = tool_buffers.setdefault(idx, {"id": "", "name": "", "input": ""})
                if isinstance(tool_delta.get("input"), str):
                    buf["input"] += tool_delta["input"]
            reasoning = delta.get("reasoningContent")
            if isinstance(reasoning, dict):
                buf = state.setdefault("reasoning_buffers", {}).setdefault(
                    idx, {"text": "", "signature": "", "redactedContent": None}
                )
                if isinstance(reasoning.get("text"), str):
                    buf["text"] += reasoning["text"]
                    reasoning_parts.append(reasoning["text"])
                if isinstance(reasoning.get("signature"), str):
                    buf["signature"] = reasoning["signature"]
                if reasoning.get("redactedContent") is not None:
                    buf["redactedContent"] = reasoning["redactedContent"]
            return None

        if "contentBlockStop" in event:
            idx = int((event["contentBlockStop"] or {}).get("contentBlockIndex") or 0)
            reasoning_buf = state.setdefault("reasoning_buffers", {}).pop(idx, None)
            if reasoning_buf:
                if reasoning_buf.get("text"):
                    thinking_blocks.append({
                        "type": "thinking",
                        "thinking": reasoning_buf["text"],
                        "signature": reasoning_buf.get("signature", ""),
                    })
                elif reasoning_buf.get("redactedContent") is not None:
                    redacted = reasoning_buf["redactedContent"]
                    if isinstance(redacted, (bytes, bytearray)):
                        redacted_block = {
                            "type": "redacted_thinking",
                            "redactedContentBase64": base64.b64encode(bytes(redacted)).decode("ascii"),
                        }
                    else:
                        redacted_block = {
                            "type": "redacted_thinking",
                            "redactedContent": redacted,
                        }
                    thinking_blocks.append({
                        **redacted_block,
                    })
            return None

        if "messageStop" in event:
            state["stop_reason"] = (event["messageStop"] or {}).get("stopReason")
            return None

        if "metadata" in event:
            metadata = event["metadata"] or {}
            if isinstance(metadata.get("usage"), dict):
                state["usage"] = metadata["usage"]
            return None

        return None

    @classmethod
    def _stream_result(
        cls,
        *,
        content_parts: list[str],
        reasoning_parts: list[str],
        thinking_blocks: list[dict[str, Any]],
        tool_buffers: dict[int, dict[str, Any]],
        state: dict[str, Any],
    ) -> LLMResponse:
        tool_calls: list[ToolCallRequest] = []
        for buf in tool_buffers.values():
            args: Any = {}
            if buf.get("input"):
                args = parse_tool_arguments(buf["input"])
            tool_calls.append(ToolCallRequest(
                id=buf.get("id") or "",
                name=buf.get("name") or "",
                arguments=args,
            ))
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=cls._finish_reason(state.get("stop_reason")),
            usage=cls._usage(state.get("usage")),
            reasoning_content="".join(reasoning_parts) or None,
            thinking_blocks=thinking_blocks or None,
        )

    @classmethod
    def _handle_error(cls, e: Exception) -> LLMResponse:
        response = getattr(e, "response", None)
        metadata = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
        headers = metadata.get("HTTPHeaders") if isinstance(metadata, dict) else None
        error_obj = response.get("Error", {}) if isinstance(response, dict) else {}
        message = error_obj.get("Message") if isinstance(error_obj, dict) else None
        code = error_obj.get("Code") if isinstance(error_obj, dict) else None
        status_code = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        body = message or str(e)
        retry_after = cls._extract_retry_after_from_headers(headers)
        if retry_after is None:
            retry_after = cls._extract_retry_after(body)

        error_name = e.__class__.__name__.lower()
        error_kind = None
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name or "endpoint" in error_name:
            error_kind = "connection"

        code_text = str(code or "").lower()
        should_retry = None
        if status_code is not None:
            should_retry = int(status_code) == 429 or int(status_code) >= 500
        if any(
            token in code_text
            for token in (
                "throttl", "timeout", "unavailable", "modelnotready",
                # Transient per AWS: "An error occurred while streaming the
                # response. Retry your request." (HTTP 424, no 5xx status).
                "modelstreamerror", "internalserver",
            )
        ):
            should_retry = True

        return LLMResponse(
            content=f"Error: {str(body).strip()[:500]}",
            finish_reason="error",
            retry_after=retry_after,
            error_status_code=int(status_code) if status_code is not None else None,
            error_kind=error_kind,
            error_type=code_text or None,
            error_code=code_text or None,
            error_retry_after_s=retry_after,
            error_should_retry=should_retry,
        )

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
        try:
            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice
            )
            response = await asyncio.to_thread(self._client.converse, **kwargs)
            return self._parse_response(response)
        except Exception as e:
            result = self._handle_error(e)
        if self._learned_thinking_retry(model, reasoning_effort, result, messages):
            return await self.chat(
                messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice
            )
        return result

    def _learned_thinking_retry(
        self,
        model: str | None,
        reasoning_effort: str | None,
        result: LLMResponse,
        messages: list[dict[str, Any]] | None = None,
    ) -> bool:
        """True once a refusal taught what this model wants (one retry).

        Covers a temperature the model does not take, a "thinking off"
        spelled the way an older generation expected, and replayed thinking
        whose signature no longer matches the history.
        """
        if result.finish_reason != "error":
            return False
        model_id = self._strip_prefix(model or self.default_model)
        text = (result.content or "").lower()
        if _is_thinking_binding_rejection(text):
            # The history is at fault, not the reasoning settings: turning
            # reasoning off for the model would not remove the block.
            return messages is not None and self._retire_replayed_thinking(model_id, messages, text)
        if "temperature" in text and ("not support" in text or "remove temperature" in text):
            if model_id.lower() not in _NO_TEMPERATURE_LEARNED:
                _NO_TEMPERATURE_LEARNED.add(model_id.lower())
                return True
            return False
        from navin.providers.claude_capabilities import learn_claude_thinking_off

        if (
            "display" in text
            and any(word in text for word in _REFUSAL_WORDS)
            and model_id.lower() not in _NO_DISPLAY_LEARNED
        ):
            # A deployment whose schema predates thinking.display: keep the
            # reasoning, drop only the summary request.
            _NO_DISPLAY_LEARNED.add(model_id.lower())
            return True

        effort = (reasoning_effort or "").lower()
        if (
            effort in ("none", "minimal", "off")
            and "claude" in model_id.lower()
            and learn_claude_thinking_off(model_id, result.content)
        ):
            return True
        if effort and effort not in ("none", "minimal", "off", "adaptive") and "claude" in model_id.lower():
            from navin.providers.claude_capabilities import learn_claude_effort

            # A refused level names the ones the model takes: replay with the
            # nearest of them before giving up on reasoning altogether.
            if learn_claude_effort(model_id, "max" if effort == "ultra" else effort, result.content):
                return True
        # Last resort: a reasoning field this model refuses. Run it on its
        # default rather than failing the step; once per model.
        if (
            reasoning_effort
            and any(word in text for word in _REASONING_WORDS)
            and any(word in text for word in _REFUSAL_WORDS)
            and model_id.lower() not in _NO_REASONING_LEARNED
        ):
            _NO_REASONING_LEARNED.add(model_id.lower())
            logger.warning("Bedrock {} refused its reasoning fields; using its default", model_id)
            return True
        return False

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
        _ = on_tool_call_delta
        idle_timeout_s = resolve_stream_idle_timeout_s()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        tool_buffers: dict[int, dict[str, Any]] = {}
        state: dict[str, Any] = {}
        event_stream: Any = None

        try:
            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice
            )
            response = await asyncio.to_thread(self._client.converse_stream, **kwargs)
            event_stream = response.get("stream")
            stream = iter(event_stream or [])
            while True:
                event = await wait_stream_idle(
                    asyncio.to_thread(_next_or_none, stream),
                    idle_timeout_s,
                )
                if event is None:
                    break
                reasoning_before = len(reasoning_parts)
                delta = self._parse_stream_event(
                    event,
                    content_parts=content_parts,
                    reasoning_parts=reasoning_parts,
                    thinking_blocks=thinking_blocks,
                    tool_buffers=tool_buffers,
                    state=state,
                )
                if delta and on_content_delta:
                    await on_content_delta(delta)
                if on_thinking_delta and len(reasoning_parts) > reasoning_before:
                    # Show the thinking as it arrives, not after the answer.
                    thought = "".join(reasoning_parts[reasoning_before:])
                    if thought:
                        await on_thinking_delta(thought)
            return self._stream_result(
                content_parts=content_parts,
                reasoning_parts=reasoning_parts,
                thinking_blocks=thinking_blocks,
                tool_buffers=tool_buffers,
                state=state,
            )
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
            result = self._handle_error(e)
        finally:
            close = getattr(event_stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if not content_parts and self._learned_thinking_retry(model, reasoning_effort, result, messages):
            return await self.chat_stream(
                messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice,
                on_content_delta=on_content_delta,
                on_thinking_delta=on_thinking_delta,
                on_tool_call_delta=on_tool_call_delta,
            )
        return result

    def get_default_model(self) -> str:
        return self.default_model
