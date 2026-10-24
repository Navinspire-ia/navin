# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""GPT and Claude, every route, every reasoning level: the payload is valid.

No network. Each provider's own request builder (or its SDK client, mocked)
produces the payload, and each payload is checked against the documented API
contract of the model it targets:

* Claude (Anthropic direct, Bedrock Converse, the Navin gateway): thinking
  shape per generation (budget / adaptive / disabled / between_tools),
  1024 <= budget_tokens < max_tokens, output_config.effort on a documented
  ladder, temperature never alongside thinking (or on 4.7+), forced
  tool_choice never with thinking (or on the models that refuse it),
  max_tokens within the output ceiling, thinking blocks replayed with their
  signatures, cache breakpoints valid and stable.
* GPT (Chat Completions, Responses, Codex, Azure, Copilot, Bedrock): no
  reasoning field to a model without reasoning, the effort on the model's
  ladder (reasoning_catalog.json, the Settings source of truth), tool-call
  round trips, prompt_cache_key, usage counters.
* A refused effort is learned and replayed once with a different value; an
  endpoint that keeps refusing is not called again with the same payload.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from navin.providers import claude_capabilities, reasoning_control
from navin.providers.anthropic_provider import AnthropicProvider
from navin.providers.azure_openai_provider import AzureOpenAIProvider
from navin.providers.bedrock_provider import BedrockProvider
from navin.providers.claude_capabilities import claude_version
from navin.providers.github_copilot_provider import GitHubCopilotProvider
from navin.providers.openai_codex_provider import (
    OpenAICodexProvider,
    _build_reasoning_options,
    _CodexHTTPError,
)
from navin.providers.openai_compat_provider import OpenAICompatProvider
from navin.providers.openai_responses import convert_messages
from navin.providers.openai_responses.parsing import parse_response_output
from navin.providers.registry import find_by_name
from navin.providers.session_affinity import session_affinity
from navin.webui.settings_api import _reasoning_effort_values_for

# -- the matrix -----------------------------------------------------------------

EFFORTS = [None, "none", "minimal", "off", "low", "medium", "high", "xhigh", "max", "adaptive", "ultra"]
OFF_EFFORTS = {"none", "minimal", "off"}
LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

CLAUDE_MODELS = [
    "claude-3-5-haiku-20241022",
    "claude-3-7-sonnet-20250219",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-opus-4-1-20250805",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-5-20251101",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-mythos-5-1",
]


def _bedrock_id(model: str) -> str:
    return f"us.anthropic.{model}-v1:0"


BEDROCK_CLAUDE_MODELS = [_bedrock_id(m) for m in CLAUDE_MODELS] + ["anthropic.claude-opus-5-5"]

GPT_MODELS = [
    "gpt-3.5-turbo",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "chatgpt-4o-latest",
    "gpt-5-chat-latest",
    "o1",
    "o3",
    "o3-mini",
    "o4-mini",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5-pro",
    "gpt-5-codex",
    "gpt-5.1",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.1-codex-max",
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.2-codex",
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.5",
    "gpt-5.6",
    "gpt-5.6-pro",
    "gpt-6",
    "gpt-6-pro",
]
CODEX_MODELS = [m for m in GPT_MODELS if m.startswith("gpt-5") and "chat" not in m]


@pytest.fixture(autouse=True)
def _fresh_learning(monkeypatch):
    """What one test taught (refused efforts, off styles) stays in that test."""
    monkeypatch.setattr(reasoning_control, "_learned_efforts", {})
    monkeypatch.setattr(claude_capabilities, "_learned_off_styles", {})
    monkeypatch.setattr(claude_capabilities, "_learned_claude_efforts", {})


# -- the documented Claude contract (independent of the provider code) ----------


def _claude_rules(model: str) -> dict[str, Any]:
    name = model.lower()
    version = claude_version(name)
    always_thinks = "fable" in name or "mythos" in name
    new = always_thinks or version is None or version >= (4, 7)
    if always_thinks or (version is not None and version >= (4, 7)):
        efforts = {"low", "medium", "high", "xhigh", "max"}
    elif version == (4, 6):
        efforts = {"low", "medium", "high", "max"}
    elif "opus-4-5" in name:
        efforts = {"low", "medium", "high"}
    else:
        efforts = set()
    if always_thinks or (version is not None and version >= (4, 6)):
        max_out = 128_000
    elif version is not None and version >= (4, 0):
        max_out = 32_000 if ("opus" in name and version < (4, 5)) else 64_000
    elif version == (3, 7):
        max_out = 64_000
    else:
        max_out = 8_192
    return {
        "thinking_param": version is None or version >= (3, 7),
        "adaptive_ok": always_thinks or version is None or version >= (4, 6),
        "budget_ok": not new,
        "disabled_ok": not (always_thinks or "opus-5-5" in name or "sonnet-5-5" in name),
        "between_tools_ok": "sonnet-5-5" in name,
        "efforts": efforts,
        "temperature_ok": not always_thinks and version is not None and version <= (4, 6),
        "forced_tool_ok": not any(
            m in name for m in ("opus-5-5", "sonnet-5-5", "fable-5-1", "mythos-5-1")
        ),
        "max_out": max_out,
    }


def _rank(effort: str) -> int:
    return LADDER.index(effort)


def _check_claude_payload(
    model: str,
    effort: str | None,
    *,
    thinking: dict[str, Any] | None,
    output_config: dict[str, Any] | None,
    max_tokens: int,
    temperature: float | None,
    tool_choice_type: str | None,
    asked_forced: bool,
) -> None:
    rules = _claude_rules(model)
    where = f"{model} effort={effort!r}"
    active = bool(thinking) and thinking.get("type") in ("enabled", "adaptive")

    assert max_tokens <= rules["max_out"], f"{where}: max_tokens {max_tokens} > {rules['max_out']}"

    if effort is None:
        assert thinking is None and output_config is None, f"{where}: no effort must send nothing"
    if thinking is not None:
        assert rules["thinking_param"], f"{where}: thinking sent to a model without it"
        kind = thinking.get("type")
        if kind == "adaptive":
            assert rules["adaptive_ok"], f"{where}: adaptive on a pre-4.6 model"
            assert set(thinking) <= {"type", "display"}, where
        elif kind == "enabled":
            assert rules["budget_ok"], f"{where}: budget_tokens on a model that removed it"
            budget = thinking["budget_tokens"]
            assert 1024 <= budget < max_tokens, f"{where}: budget {budget} vs max_tokens {max_tokens}"
        elif kind == "disabled":
            assert rules["disabled_ok"], f"{where}: disabled is a 400 on this model"
        elif kind == "between_tools":
            assert rules["between_tools_ok"], f"{where}: between_tools outside Sonnet 5.5"
            assert set(thinking) == {"type"}, f"{where}: between_tools takes no other field"
        else:
            pytest.fail(f"{where}: unknown thinking type {kind!r}")
    if output_config is not None:
        level = output_config.get("effort")
        assert level in rules["efforts"], f"{where}: effort {level!r} not in {sorted(rules['efforts'])}"
        if thinking and thinking.get("type") in ("disabled", "between_tools"):
            assert _rank(level) <= _rank("high"), f"{where}: off accepted only at high or below"

    if temperature is not None:
        assert rules["temperature_ok"], f"{where}: temperature sent to a 4.7+ model"
        if active:
            assert temperature == 1.0, f"{where}: thinking requires temperature 1"

    if tool_choice_type in ("any", "tool"):
        assert rules["forced_tool_ok"], f"{where}: forced tool_choice is a 400 here"
        assert not active, f"{where}: forced tool_choice with thinking is a 400"
    elif asked_forced and rules["forced_tool_ok"] and not active:
        pytest.fail(f"{where}: a forced tool_choice the model accepts was dropped")

    if effort in OFF_EFFORTS and rules["thinking_param"]:
        assert thinking is not None, f"{where}: off must be spelled out"
        assert thinking.get("type") != "enabled", f"{where}: off enabled thinking"
        if thinking.get("type") == "adaptive":
            assert not rules["disabled_ok"] or not rules["thinking_param"], where
            assert (output_config or {}).get("effort") == "low", f"{where}: closest off is low"
    if effort not in (None, *OFF_EFFORTS) and rules["thinking_param"]:
        assert active, f"{where}: an effort must turn thinking on"
        if effort == "adaptive" and rules["adaptive_ok"]:
            assert thinking.get("type") == "adaptive", where
        elif rules["efforts"] and rules["adaptive_ok"]:
            assert output_config is not None, f"{where}: effort missing"


def _anthropic_tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {
            "name": "read_file", "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }},
        {"type": "function", "function": {
            "name": "write_file", "description": "Write a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
        }},
    ]


SIMPLE = [{"role": "system", "content": "You are terse."}, {"role": "user", "content": "hi"}]


def _anthropic_kwargs(model: str, effort: str | None, max_tokens: int, tool_choice: Any = "required") -> dict[str, Any]:
    provider = AnthropicProvider(api_key="k")
    return provider._build_kwargs(SIMPLE, _anthropic_tools(), model, max_tokens, 0.3, effort, tool_choice)


@pytest.mark.parametrize("max_tokens", [4096, 64_000, 128_000])
@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", CLAUDE_MODELS)
def test_anthropic_direct_payload(model: str, effort: str | None, max_tokens: int) -> None:
    kwargs = _anthropic_kwargs(model, effort, max_tokens)
    output_config = (kwargs.get("extra_body") or {}).get("output_config")
    _check_claude_payload(
        model, effort,
        thinking=kwargs.get("thinking"),
        output_config=output_config,
        max_tokens=kwargs["max_tokens"],
        temperature=kwargs.get("temperature"),
        tool_choice_type=(kwargs.get("tool_choice") or {}).get("type"),
        asked_forced=True,
    )
    if output_config and "opus-4-5" in model:
        beta = (kwargs.get("extra_headers") or {}).get("anthropic-beta", "")
        assert "effort-2025-11-24" in beta, "Opus 4.5 effort needs its beta header"
    if (kwargs.get("thinking") or {}).get("type") == "adaptive" and claude_version(model) not in ((4, 6),):
        # From 4.7 the API omits thinking text unless asked: no summary, no
        # stream activity for minutes.
        if kwargs["thinking"] and "display" in kwargs["thinking"]:
            assert kwargs["thinking"]["display"] in ("summarized", "omitted")


@pytest.mark.parametrize("max_tokens", [4096, 64_000, 128_000])
@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", BEDROCK_CLAUDE_MODELS)
def test_bedrock_claude_payload(model: str, effort: str | None, max_tokens: int) -> None:
    provider = BedrockProvider(client=SimpleNamespace())
    kwargs = provider._build_kwargs(SIMPLE, _anthropic_tools(), model, max_tokens, 0.3, effort, "required")
    extra = kwargs.get("additionalModelRequestFields") or {}
    choice = (kwargs.get("toolConfig") or {}).get("toolChoice") or {}
    _check_claude_payload(
        model, effort,
        thinking=extra.get("thinking"),
        output_config=extra.get("output_config"),
        max_tokens=kwargs["inferenceConfig"]["maxTokens"],
        temperature=kwargs["inferenceConfig"].get("temperature"),
        tool_choice_type=next(iter(choice), None),
        asked_forced=True,
    )
    assert set(extra) <= {"thinking", "output_config"}, f"{model}: unexpected fields {sorted(extra)}"


def test_claude_effort_is_monotonic_and_ultra_is_deepest() -> None:
    for model in CLAUDE_MODELS:
        rules = _claude_rules(model)
        if not rules["efforts"] or not rules["adaptive_ok"]:
            continue
        levels = []
        for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
            kwargs = _anthropic_kwargs(model, effort, 16_000, tool_choice=None)
            levels.append(kwargs["extra_body"]["output_config"]["effort"])
        ranks = [_rank(level) for level in levels]
        assert ranks == sorted(ranks), f"{model}: {levels}"
        assert levels[-1] == levels[-2], f"{model}: ultra must equal max ({levels})"


def test_claude_budget_respects_output_ceiling() -> None:
    # Sonnet 4.5 stops at 64k output: a high budget must not lift max_tokens past it.
    kwargs = _anthropic_kwargs("claude-sonnet-4-5-20250929", "high", 64_000, tool_choice=None)
    assert kwargs["max_tokens"] == 64_000
    assert kwargs["thinking"]["budget_tokens"] < 64_000
    # xhigh / max on a budget model think at least as deep as high.
    high = _anthropic_kwargs("claude-haiku-4-5-20251001", "high", 16_000, tool_choice=None)
    deepest = _anthropic_kwargs("claude-haiku-4-5-20251001", "max", 16_000, tool_choice=None)
    assert deepest["thinking"]["budget_tokens"] >= high["thinking"]["budget_tokens"]


def test_claude_4_0_dated_ids_are_not_read_as_4_20250514() -> None:
    assert claude_version("claude-opus-4-20250514") == (4, 0)
    assert claude_version("us.anthropic.claude-sonnet-4-20250514-v1:0") == (4, 0)
    fields, _ = claude_capabilities.claude_thinking_fields(
        "us.anthropic.claude-sonnet-4-20250514-v1:0", "high", max_tokens=16_000,
    )
    assert fields["thinking"]["type"] == "enabled"


def test_unknown_future_claude_uses_the_current_surface() -> None:
    kwargs = _anthropic_kwargs("claude-opus-7", "high", 16_000, tool_choice=None)
    assert kwargs["thinking"]["type"] == "adaptive"
    assert kwargs["extra_body"]["output_config"] == {"effort": "high"}
    assert "temperature" not in kwargs


# -- Claude through the Navin gateway (OpenAI wire, LiteLLM behind) --------------


# Production default and the staging LiteLLM the site delivers as managedApiBase.
GATEWAY_BASES = ["https://llm.navin.live/v1", "https://llm.test-02.hp.navin.live/v1"]


def _gateway(model: str, base: str = GATEWAY_BASES[0]) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        api_key="k", api_base=base, default_model=model,
        spec=find_by_name("navin"),
    )


@pytest.mark.parametrize("base", GATEWAY_BASES)
@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", CLAUDE_MODELS)
def test_gateway_claude_payload(model: str, effort: str | None, base: str) -> None:
    provider = _gateway(model, base)
    assert provider.api_base == base  # the delivered base is kept, whatever its host
    kwargs = provider._build_kwargs(SIMPLE, _anthropic_tools(), model, 8192, 0.3, effort, None)
    sent = kwargs.get("reasoning_effort")
    extra = kwargs.get("extra_body") or {}
    nested = (extra.get("reasoning") or {}).get("effort")
    for value in (sent, nested):
        assert value is None or value in LADDER, f"{model} {effort}: {value!r} is not an OpenAI-wire effort"
    assert "thinking" not in kwargs and "output_config" not in kwargs
    rules = _claude_rules(model)
    if "temperature" in kwargs:
        assert rules["temperature_ok"], f"{model}: temperature on a 4.7+ model"
        assert effort in (None, "none", "minimal", "off"), f"{model} {effort}: temperature with thinking"
    if effort is None:
        assert sent is None and nested is None
    if effort in OFF_EFFORTS:
        # "disabled" is a 400 on Opus 5.5 / Sonnet 5.5 / Fable / Mythos:
        # LiteLLM gets the lowest effort instead of "none" for those.
        floor = "low" if not _claude_rules(model)["disabled_ok"] else "none"
        assert sent in (floor, None) and nested in (floor, None), f"{model} {effort}: {sent!r}"
    assert kwargs.get("max_tokens", kwargs.get("max_completion_tokens")) == 8192


# -- GPT: the ladder each model documents ---------------------------------------


def _catalog_efforts(model: str) -> set[str]:
    return {v for v in _reasoning_effort_values_for("openai", model) if v}


def _reasoning_on(model: str) -> bool:
    return bool(_catalog_efforts(model))


def _expected_floor(model: str) -> str:
    accepted = _catalog_efforts(model)
    for floor in ("none", "minimal", "low"):
        if floor in accepted:
            return floor
    return min(accepted, key=_rank)


def test_provider_ladders_match_the_settings_catalog() -> None:
    for model in GPT_MODELS:
        catalog = _catalog_efforts(model)
        known = reasoning_control.openai_known_efforts(model)
        if not catalog:
            assert known is None and reasoning_control.openai_rejects_reasoning(model), model
        else:
            assert known == catalog, f"{model}: provider {sorted(known or ())} vs catalog {sorted(catalog)}"


def _check_gpt_effort(model: str, effort: str | None, sent: str | None, where: str) -> None:
    if not _reasoning_on(model):
        assert sent is None, f"{where}: reasoning sent to a model without reasoning"
        return
    if effort is None:
        assert sent is None, f"{where}: no effort must keep the model default"
        return
    accepted = _catalog_efforts(model)
    assert sent in accepted, f"{where}: {sent!r} not in {sorted(accepted)}"
    if effort in OFF_EFFORTS:
        assert sent == _expected_floor(model), f"{where}: off went out as {sent!r}"


def _openai(model: str, api_type: str) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        api_key="k", api_base="https://api.openai.com/v1", default_model=model,
        spec=find_by_name("openai"), api_type=api_type,
    )


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", GPT_MODELS)
def test_openai_chat_completions_payload(model: str, effort: str | None) -> None:
    kwargs = _openai(model, "chat_completions")._build_kwargs(
        SIMPLE, _anthropic_tools(), model, 4096, 0.3, effort, None,
    )
    extra = kwargs.get("extra_body") or {}
    assert "reasoning" not in extra and "thinking" not in extra
    _check_gpt_effort(model, effort, kwargs.get("reasoning_effort"), f"chat {model} {effort}")
    if _reasoning_on(model):
        assert "max_completion_tokens" in kwargs and "max_tokens" not in kwargs
        assert "temperature" not in kwargs, f"{model}: temperature on a reasoning model"


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", GPT_MODELS)
def test_openai_responses_payload(model: str, effort: str | None) -> None:
    body = _openai(model, "responses")._build_responses_body(
        SIMPLE, _anthropic_tools(), model, 4096, 0.3, effort, None,
    )
    sent = (body.get("reasoning") or {}).get("effort")
    _check_gpt_effort(model, effort, sent, f"responses {model} {effort}")
    if sent and effort not in OFF_EFFORTS:
        assert body.get("include") == ["reasoning.encrypted_content"]
    assert body["store"] is False and body["max_output_tokens"] == 4096
    if _reasoning_on(model):
        assert "temperature" not in body


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", CODEX_MODELS)
def test_codex_payload(model: str, effort: str | None) -> None:
    options = _build_reasoning_options(effort, model) or {}
    _check_gpt_effort(model, effort, options.get("effort"), f"codex {model} {effort}")


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", GPT_MODELS)
def test_azure_payload(model: str, effort: str | None) -> None:
    provider = AzureOpenAIProvider(api_key="k", api_base="https://x.openai.azure.com", default_model=model)
    body = provider._build_body(SIMPLE, _anthropic_tools(), model, 4096, 0.3, effort, None)
    sent = (body.get("reasoning") or {}).get("effort")
    _check_gpt_effort(model, effort, sent, f"azure {model} {effort}")


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o", "o4-mini", "gpt-5", "gpt-5.1-codex", "gpt-5.2", "gpt-5-mini"])
def test_copilot_gpt_payload(model: str, effort: str | None) -> None:
    provider = GitHubCopilotProvider(default_model=model)
    if provider._should_use_responses_api(model, effort):
        body = provider._build_responses_body(SIMPLE, _anthropic_tools(), model, 4096, 0.3, effort, None)
        sent = (body.get("reasoning") or {}).get("effort")
    else:
        kwargs = provider._build_kwargs(SIMPLE, _anthropic_tools(), model, 4096, 0.3, effort, None)
        sent = kwargs.get("reasoning_effort")
    _check_gpt_effort(model, effort, sent, f"copilot {model} {effort}")


@pytest.mark.parametrize("model", ["claude-sonnet-4.5", "claude-opus-4.6", "gemini-2.5-pro"])
def test_copilot_routes_non_openai_models_to_chat_completions(model: str) -> None:
    provider = GitHubCopilotProvider(default_model=model)
    for effort in ("high", "none", None):
        assert not provider._should_use_responses_api(model, effort)
        kwargs = provider._build_kwargs(SIMPLE, None, model, 4096, 0.3, effort, None)
        assert kwargs.get("reasoning_effort") in (None, *LADDER)


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", ["openai.gpt-oss-120b-1:0", "openai.gpt-oss-20b-1:0"])
def test_bedrock_gpt_payload(model: str, effort: str | None) -> None:
    provider = BedrockProvider(client=SimpleNamespace())
    kwargs = provider._build_kwargs(SIMPLE, None, model, 4096, 0.3, effort, None)
    extra = kwargs.get("additionalModelRequestFields") or {}
    assert "thinking" not in extra and "output_config" not in extra
    level = (extra.get("reasoning") or {}).get("effort")
    assert level is None or level in ("none", "low", "medium", "high", "xhigh", "max")
    if effort is None:
        assert not extra


# -- learn and retry: bounded, never the same failing call twice ----------------


class _RefusalError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        # OpenAI's real refusal names the parameter in ``param``.
        self.body = body if body is not None else {"error": {
            "message": message, "type": "invalid_request_error",
            "param": "reasoning_effort", "code": "unsupported_value",
        }}
        self.response = None


OPENAI_EFFORT_REFUSAL = (
    "Unsupported value: 'xhigh' is not supported with this model. "
    "Supported values are: 'low', 'medium', and 'high'."
)
OK_CHAT = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
OK_RESPONSES = {"status": "completed", "output": [
    {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
    "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}}


def _wire(provider: OpenAICompatProvider, *, chat=None, responses=None) -> None:
    async def ready():
        return provider._client

    provider._ensure_client = ready  # type: ignore[method-assign]
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat)),
        responses=SimpleNamespace(create=responses),
    )


def _scripted(calls: list[dict[str, Any]], outcomes: list[Any]):
    async def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        outcome = outcomes.pop(0) if outcomes else outcomes_last[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    outcomes_last = [outcomes[-1]]
    return create


def _assert_no_identical_repeats(payloads: list[Any]) -> None:
    dumps = [json.dumps(p, sort_keys=True, default=str) for p in payloads]
    assert len(dumps) == len(set(dumps)), "the same failing request was sent twice"


def test_chat_completions_learns_a_refused_effort_once() -> None:
    provider = OpenAICompatProvider(api_key="k", api_base="https://example.invalid/v1", default_model="vendor-reasoner-9")
    calls: list[dict[str, Any]] = []
    _wire(provider, chat=_scripted(calls, [_RefusalError(OPENAI_EFFORT_REFUSAL), OK_CHAT]))
    result = asyncio.run(provider.chat(SIMPLE, model="vendor-reasoner-9", reasoning_effort="xhigh"))
    assert result.finish_reason == "stop"
    assert [c.get("reasoning_effort") for c in calls] == ["xhigh", "high"]


def test_chat_completions_stops_when_the_endpoint_keeps_refusing() -> None:
    provider = OpenAICompatProvider(api_key="k", api_base="https://example.invalid/v1", default_model="vendor-reasoner-9")
    calls: list[dict[str, Any]] = []
    _wire(provider, chat=_scripted(calls, [_RefusalError(OPENAI_EFFORT_REFUSAL)]))
    result = asyncio.run(provider.chat(SIMPLE, model="vendor-reasoner-9", reasoning_effort="xhigh"))
    assert result.finish_reason == "error"
    assert len(calls) <= 3
    _assert_no_identical_repeats(calls)


def test_responses_learns_a_refused_effort_without_replaying_it() -> None:
    provider = _openai("gpt-7-preview", "responses")
    calls: list[dict[str, Any]] = []
    _wire(provider, responses=_scripted(calls, [_RefusalError(OPENAI_EFFORT_REFUSAL), OK_RESPONSES]))
    result = asyncio.run(provider.chat(SIMPLE, model="gpt-7-preview", reasoning_effort="xhigh"))
    assert result.finish_reason == "stop"
    assert [c["reasoning"]["effort"] for c in calls] == ["xhigh", "high"]


def test_responses_never_sends_a_documented_refusal() -> None:
    provider = _openai("gpt-5.1-codex", "responses")
    calls: list[dict[str, Any]] = []
    _wire(provider, responses=_scripted(calls, [OK_RESPONSES]))
    asyncio.run(provider.chat(SIMPLE, model="gpt-5.1-codex", reasoning_effort="xhigh"))
    assert [c["reasoning"]["effort"] for c in calls] == ["high"]


def test_responses_keeps_refusing_is_bounded() -> None:
    provider = _openai("gpt-7-preview", "responses")
    calls: list[dict[str, Any]] = []
    _wire(provider, responses=_scripted(calls, [_RefusalError(OPENAI_EFFORT_REFUSAL)]))
    asyncio.run(provider.chat(SIMPLE, model="gpt-7-preview", reasoning_effort="xhigh"))
    assert len(calls) <= 3
    _assert_no_identical_repeats(calls)


def _codex(monkeypatch, outcomes: list[Any], calls: list[dict[str, Any]]) -> OpenAICodexProvider:
    import navin.providers.openai_codex_provider as codex

    monkeypatch.setattr(codex, "get_codex_token", lambda **_: SimpleNamespace(account_id="a", access="t"))

    async def fake_request(url, headers, body, verify, proxy=None, **_):
        calls.append(copy.deepcopy(body))
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(codex, "_request_codex", fake_request)
    return OpenAICodexProvider(default_model="gpt-7-codex")


def test_codex_learns_a_refused_effort_once(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    refusal = _CodexHTTPError("HTTP 400", status_code=400, body=json.dumps({"error": {
        "message": OPENAI_EFFORT_REFUSAL, "param": "reasoning.effort"}}))
    provider = _codex(monkeypatch, [refusal, ("ok", [], "stop", {}, None)], calls)
    result = asyncio.run(provider.chat(SIMPLE, model="gpt-7-codex", reasoning_effort="xhigh"))
    assert result.content == "ok"
    assert [c["reasoning"]["effort"] for c in calls] == ["xhigh", "high"]


def test_codex_keeps_refusing_is_bounded(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    refusal = _CodexHTTPError("HTTP 400", status_code=400, body=json.dumps({"error": {
        "message": OPENAI_EFFORT_REFUSAL, "param": "reasoning.effort"}}))
    provider = _codex(monkeypatch, [refusal], calls)
    result = asyncio.run(provider.chat_stream(SIMPLE, model="gpt-7-codex", reasoning_effort="xhigh"))
    assert result.finish_reason == "error"
    assert len(calls) == 2
    _assert_no_identical_repeats(calls)


def test_codex_never_sends_none_to_a_codex_model() -> None:
    assert _build_reasoning_options("none", "gpt-5.1-codex") == {"summary": "auto", "effort": "low"}
    assert _build_reasoning_options("none", "gpt-5.1") == {"effort": "none"}


def test_azure_learns_a_refused_effort_once() -> None:
    provider = AzureOpenAIProvider(api_key="k", api_base="https://x.openai.azure.com", default_model="my-deploy")
    calls: list[dict[str, Any]] = []

    async def create(**body):
        calls.append(copy.deepcopy(body))
        if len(calls) == 1:
            raise _RefusalError(OPENAI_EFFORT_REFUSAL)
        return OK_RESPONSES

    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    result = asyncio.run(provider.chat(SIMPLE, model="my-deploy", reasoning_effort="xhigh"))
    assert result.content == "ok"
    assert [c["reasoning"]["effort"] for c in calls] == ["xhigh", "high"]


ANTHROPIC_EFFORT_REFUSAL = {"type": "error", "error": {
    "type": "invalid_request_error",
    "message": "output_config.effort: Input should be 'low', 'medium', 'high' or 'max'",
}}


def _anthropic_with(create) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="k")
    provider._client_instance = SimpleNamespace(messages=SimpleNamespace(create=create))
    return provider


def _anthropic_message(blocks: list[Any], *, stop: str = "end_turn", usage: dict[str, int] | None = None):
    usage = usage or {"input_tokens": 10, "output_tokens": 5}
    return SimpleNamespace(
        content=blocks, stop_reason=stop,
        usage=SimpleNamespace(**{"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, **usage}),
    )


def test_anthropic_learns_a_refused_effort_once() -> None:
    calls: list[dict[str, Any]] = []

    async def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            raise _RefusalError("400", body=ANTHROPIC_EFFORT_REFUSAL)
        return _anthropic_message([SimpleNamespace(type="text", text="ok")])

    result = asyncio.run(_anthropic_with(create).chat(SIMPLE, model="claude-opus-7", reasoning_effort="xhigh"))
    assert result.content == "ok"
    assert [c["extra_body"]["output_config"]["effort"] for c in calls] == ["xhigh", "max"]


def test_anthropic_keeps_refusing_is_bounded() -> None:
    calls: list[dict[str, Any]] = []

    async def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        raise _RefusalError("400", body=ANTHROPIC_EFFORT_REFUSAL)

    result = asyncio.run(_anthropic_with(create).chat(SIMPLE, model="claude-opus-7", reasoning_effort="xhigh"))
    assert result.finish_reason == "error"
    assert len(calls) == 2
    _assert_no_identical_repeats(calls)


def test_anthropic_contradicting_off_refusals_do_not_loop() -> None:
    calls: list[dict[str, Any]] = []
    errors = [
        "thinking.type.disabled is not supported for this model; use between_tools instead of disabled",
        "thinking.type.between_tools is not supported for this model",
    ]

    async def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        raise _RefusalError("400", body=errors[(len(calls) - 1) % 2])

    asyncio.run(_anthropic_with(create).chat(SIMPLE, model="claude-sonnet-9", reasoning_effort="none"))
    assert len(calls) <= 2


def test_bedrock_claude_learns_a_refused_effort_once() -> None:
    calls: list[dict[str, Any]] = []

    def converse(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            raise _RefusalError(
                "ValidationException: output_config.effort: Input should be 'low', 'medium', 'high' or 'max'"
            )
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "stopReason": "end_turn",
                "usage": {"inputTokens": 3, "outputTokens": 1}}

    provider = BedrockProvider(client=SimpleNamespace(converse=converse))
    model = "us.anthropic.claude-opus-7-v1:0"
    result = asyncio.run(provider.chat(SIMPLE, model=model, reasoning_effort="xhigh"))
    assert result.content == "ok"
    sent = [c["additionalModelRequestFields"].get("output_config", {}).get("effort") for c in calls]
    assert sent == ["xhigh", "max"]
    _assert_no_identical_repeats(calls)


# -- calls, tool round trips and replay, per route ------------------------------

SIG = "c2lnbmF0dXJl"
HISTORY: list[dict[str, Any]] = [
    {"role": "system", "content": "You are a coding agent. " * 400},
    {"role": "user", "content": "Read a.py and b.py, then fix the bug."},
    {
        "role": "assistant",
        "content": "",
        "thinking_blocks": [
            {"type": "thinking", "thinking": "Both files first.", "signature": SIG},
            {"type": "redacted_thinking", "data": "cmVkYWN0ZWQ="},
        ],
        "tool_calls": [
            {"id": "toolu_A1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
            {"id": "toolu_B2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "b.py"}'}},
        ],
    },
    {"role": "tool", "tool_call_id": "toolu_A1", "name": "read_file", "content": "print('a')"},
    {"role": "tool", "tool_call_id": "toolu_B2", "name": "read_file", "content": "print('b')"},
    {
        "role": "assistant",
        "content": "Fixing b.py.",
        "thinking_blocks": [{"type": "thinking", "thinking": "b is wrong.", "signature": SIG}],
        "tool_calls": [
            {"id": "toolu_C3", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "b.py", "content": "x"}'}},
        ],
    },
    {"role": "tool", "tool_call_id": "toolu_C3", "name": "write_file", "content": "ok"},
]


def _count_cache_markers(kwargs: dict[str, Any]) -> int:
    return json.dumps(kwargs, default=str).count('"cache_control"')


def _blocks_with_cache(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for message in kwargs["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            found += [b for b in content if isinstance(b, dict) and "cache_control" in b]
    return found


@pytest.mark.parametrize("effort", [None, "none", "low", "high", "max", "adaptive"])
@pytest.mark.parametrize("model", ["claude-sonnet-4-5-20250929", "claude-opus-4-6", "claude-opus-4-8", "claude-sonnet-5-5", "claude-opus-5-5"])
def test_anthropic_replay_and_cache(model: str, effort: str | None) -> None:
    provider = AnthropicProvider(api_key="k")
    kwargs = provider._build_kwargs(HISTORY, _anthropic_tools(), model, 16_000, 0.3, effort, None)
    messages = kwargs["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    first = messages[1]["content"]
    assert [b["type"] for b in first] == ["thinking", "redacted_thinking", "tool_use", "tool_use"]
    assert first[0]["signature"] == SIG and first[1]["data"] == "cmVkYWN0ZWQ="
    results = messages[2]["content"]
    assert [b["tool_use_id"] for b in results] == ["toolu_A1", "toolu_B2"]
    second = messages[3]["content"]
    assert [b["type"] for b in second] == ["thinking", "text", "tool_use"]
    assert messages[4]["content"][0]["tool_use_id"] == "toolu_C3"
    # Cache: at most 4 breakpoints, never on thinking or empty text.
    assert _count_cache_markers(kwargs) <= 4
    for block in _blocks_with_cache(kwargs):
        assert block["type"] not in ("thinking", "redacted_thinking")
        assert not (block["type"] == "text" and not block.get("text"))
    assert kwargs["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def _strip_cache(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_cache(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_strip_cache(v) for v in value]
    return value


@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-sonnet-4-5-20250929"])
def test_anthropic_prefix_is_stable_across_turns_and_efforts(model: str) -> None:
    provider = AnthropicProvider(api_key="k")
    turn = provider._build_kwargs(HISTORY[:5], _anthropic_tools(), model, 16_000, 0.3, "high", None)
    later = provider._build_kwargs(HISTORY, _anthropic_tools(), model, 16_000, 0.3, "low", None)
    # tools and system are byte-identical whatever the effort: the cached
    # prefix survives an effort change on the provider side.
    assert json.dumps(turn["tools"]) == json.dumps(later["tools"])
    assert json.dumps(turn["system"]) == json.dumps(later["system"])
    # The earlier turns are replayed unchanged (markers move, content does not).
    assert _strip_cache(turn["messages"][:3]) == _strip_cache(later["messages"][:3])


def _anthropic_stream_provider(final) -> AnthropicProvider:
    chunks = [
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="thinking")),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="thinking_delta", thinking="hmm")),
        SimpleNamespace(type="content_block_delta", index=1, delta=SimpleNamespace(type="text_delta", text="Hi")),
        SimpleNamespace(type="content_block_start", index=2, content_block=SimpleNamespace(type="tool_use", id="toolu_X", name="read_file")),
        SimpleNamespace(type="content_block_delta", index=2, delta=SimpleNamespace(type="input_json_delta", partial_json='{"path": "a"}')),
    ]

    class _Stream:
        def __init__(self, kwargs):
            self.kwargs = kwargs
            self._it = iter(chunks)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

        async def get_final_message(self):
            return final

    captured: list[dict[str, Any]] = []

    def stream(**kwargs):
        captured.append(kwargs)
        return _Stream(kwargs)

    async def create(**kwargs):
        captured.append(kwargs)
        return final

    provider = AnthropicProvider(api_key="k")
    provider._client_instance = SimpleNamespace(messages=SimpleNamespace(stream=stream, create=create))
    provider.captured = captured  # type: ignore[attr-defined]
    return provider


def _anthropic_final():
    return _anthropic_message(
        [
            SimpleNamespace(type="thinking", thinking="hmm", signature=SIG),
            SimpleNamespace(type="redacted_thinking", data="cmVk"),
            SimpleNamespace(type="text", text="Hi"),
            SimpleNamespace(type="tool_use", id="toolu_X", name="read_file", input={"path": "a"}),
            SimpleNamespace(type="tool_use", id="toolu_Y", name="read_file", input={"path": "b"}),
        ],
        stop="tool_use",
        usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 700, "cache_creation_input_tokens": 90},
    )


@pytest.mark.parametrize("stream", [False, True])
def test_anthropic_calls_parse_tools_thinking_and_cache_usage(stream: bool) -> None:
    provider = _anthropic_stream_provider(_anthropic_final())
    deltas: list[str] = []

    async def on_delta(text):
        deltas.append(text)

    if stream:
        result = asyncio.run(provider.chat_stream(
            HISTORY, _anthropic_tools(), "claude-opus-4-8", 16_000, 0.3, "high", None,
            on_content_delta=on_delta,
        ))
        assert deltas == ["Hi"]
    else:
        result = asyncio.run(provider.chat(HISTORY, _anthropic_tools(), "claude-opus-4-8", 16_000, 0.3, "high", None))
    assert result.finish_reason == "tool_calls"
    assert [c.id for c in result.tool_calls] == ["toolu_X", "toolu_Y"]
    assert result.thinking_blocks[0] == {"type": "thinking", "thinking": "hmm", "signature": SIG}
    assert result.thinking_blocks[1] == {"type": "redacted_thinking", "data": "cmVk"}
    assert result.usage["cache_read_input_tokens"] == 700
    assert result.usage["cache_creation_input_tokens"] == 90
    assert result.usage["cached_tokens"] == 700
    assert result.usage["prompt_tokens"] == 800
    # The reply replays as the next request's assistant turn, signatures intact.
    replay = HISTORY + [{
        "role": "assistant", "content": result.content, "thinking_blocks": result.thinking_blocks,
        "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                       for c in result.tool_calls],
    }, {"role": "tool", "tool_call_id": "toolu_X", "content": "a"}, {"role": "tool", "tool_call_id": "toolu_Y", "content": "b"}]
    kwargs = AnthropicProvider(api_key="k")._build_kwargs(replay, _anthropic_tools(), "claude-opus-4-8", 16_000, 0.3, "high", None)
    last_assistant = kwargs["messages"][-2]["content"]
    assert [b["type"] for b in last_assistant] == ["thinking", "redacted_thinking", "text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in kwargs["messages"][-1]["content"]] == ["toolu_X", "toolu_Y"]


# Bedrock


def _bedrock_history() -> list[dict[str, Any]]:
    history = copy.deepcopy(HISTORY)
    # Bedrock-origin redacted thinking travels as base64 of the raw bytes.
    history[2]["thinking_blocks"][1] = {"type": "redacted_thinking", "redactedContentBase64": base64.b64encode(b"opaque").decode()}
    return history


@pytest.mark.parametrize("model", [_bedrock_id("claude-opus-4-8"), _bedrock_id("claude-sonnet-4-5-20250929"), "anthropic.claude-opus-5-5"])
def test_bedrock_replay_and_cache_points(model: str) -> None:
    provider = BedrockProvider(client=SimpleNamespace())
    kwargs = provider._build_kwargs(_bedrock_history(), _anthropic_tools(), model, 16_000, 0.3, "high", None)
    messages = kwargs["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    first = messages[1]["content"]
    assert first[0]["reasoningContent"]["reasoningText"] == {"text": "Both files first.", "signature": SIG}
    assert first[1]["reasoningContent"]["redactedContent"] == b"opaque"
    assert [b["toolUse"]["toolUseId"] for b in first[2:]] == ["toolu_A1", "toolu_B2"]
    assert [b["toolResult"]["toolUseId"] for b in messages[2]["content"]] == ["toolu_A1", "toolu_B2"]
    points = json.dumps(kwargs, default=str).count('"cachePoint"')
    assert points == 3  # tools, system, conversation end; Bedrock allows 4
    assert "cachePoint" in kwargs["toolConfig"]["tools"][-1]
    assert "cachePoint" in kwargs["system"][-1]
    assert "cachePoint" in messages[-1]["content"][-1]
    for message in messages[:-1]:
        assert not any("cachePoint" in block for block in message["content"])


@pytest.mark.parametrize("model", ["anthropic.claude-3-5-sonnet-20240620-v1:0", "anthropic.claude-3-haiku-20240307-v1:0", "openai.gpt-oss-120b-1:0"])
def test_bedrock_no_cache_points_where_unsupported(model: str) -> None:
    kwargs = BedrockProvider(client=SimpleNamespace())._build_kwargs(
        SIMPLE, _anthropic_tools(), model, 4096, 0.3, None, None,
    )
    assert '"cachePoint"' not in json.dumps(kwargs, default=str)


def test_bedrock_prefix_is_stable_across_efforts() -> None:
    provider = BedrockProvider(client=SimpleNamespace())
    model = _bedrock_id("claude-opus-4-8")
    high = provider._build_kwargs(_bedrock_history(), _anthropic_tools(), model, 16_000, 0.3, "high", None)
    none = provider._build_kwargs(_bedrock_history(), _anthropic_tools(), model, 16_000, 0.3, "none", None)
    for key in ("system", "toolConfig", "messages"):
        assert json.dumps(high[key], default=str) == json.dumps(none[key], default=str), key


@pytest.mark.parametrize("stream", [False, True])
def test_bedrock_calls_parse_tools_thinking_and_cache_usage(stream: bool) -> None:
    usage = {"inputTokens": 12, "outputTokens": 4, "cacheReadInputTokens": 900, "cacheWriteInputTokens": 50}
    captured: list[dict[str, Any]] = []

    def converse(**kwargs):
        captured.append(kwargs)
        return {"output": {"message": {"content": [
            {"reasoningContent": {"reasoningText": {"text": "hmm", "signature": SIG}}},
            {"text": "Hi"},
            {"toolUse": {"toolUseId": "t1", "name": "read_file", "input": {"path": "a"}}},
            {"toolUse": {"toolUseId": "t2", "name": "read_file", "input": {"path": "b"}}},
        ]}}, "stopReason": "tool_use", "usage": usage}

    def converse_stream(**kwargs):
        captured.append(kwargs)
        events = [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"reasoningContent": {"text": "hmm"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"reasoningContent": {"signature": SIG}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "Hi"}}},
            {"contentBlockStart": {"contentBlockIndex": 2, "start": {"toolUse": {"toolUseId": "t1", "name": "read_file"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 2, "delta": {"toolUse": {"input": '{"path": "a"}'}}}},
            {"contentBlockStart": {"contentBlockIndex": 3, "start": {"toolUse": {"toolUseId": "t2", "name": "read_file"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 3, "delta": {"toolUse": {"input": '{"path": "b"}'}}}},
            {"messageStop": {"stopReason": "tool_use"}},
            {"metadata": {"usage": usage}},
        ]
        return {"stream": events}

    provider = BedrockProvider(client=SimpleNamespace(converse=converse, converse_stream=converse_stream))
    model = _bedrock_id("claude-opus-4-8")
    call = provider.chat_stream if stream else provider.chat
    result = asyncio.run(call(_bedrock_history(), _anthropic_tools(), model, 16_000, 0.3, "high", None))
    assert result.finish_reason == "tool_calls"
    assert [c.id for c in result.tool_calls] == ["t1", "t2"]
    assert result.thinking_blocks == [{"type": "thinking", "thinking": "hmm", "signature": SIG}]
    assert result.usage["cached_tokens"] == 900
    assert result.usage["cache_read_input_tokens"] == 900
    assert result.usage["cache_creation_input_tokens"] == 50
    assert captured and captured[0]["modelId"] == model


# OpenAI: Chat Completions, Responses, Codex, Azure, Copilot, gateway

GPT_HISTORY: list[dict[str, Any]] = [
    {"role": "system", "content": "You are a coding agent."},
    {"role": "user", "content": "Read a.py and b.py."},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_A|fc_A", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
        {"id": "call_B|fc_B", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "b.py"}'}},
    ]},
    {"role": "tool", "tool_call_id": "call_A|fc_A", "content": "print('a')"},
    {"role": "tool", "tool_call_id": "call_B|fc_B", "content": "print('b')"},
    {"role": "assistant", "content": "Both read."},
    {"role": "user", "content": "Now fix b."},
]


def test_responses_replay_keeps_parallel_calls_paired() -> None:
    instructions, items = convert_messages(GPT_HISTORY)
    assert instructions == "You are a coding agent."
    kinds = [i.get("type", i.get("role")) for i in items]
    assert kinds == ["user", "function_call", "function_call", "function_call_output", "function_call_output", "message", "user"]
    calls = [i for i in items if i.get("type") == "function_call"]
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert [c["call_id"] for c in calls] == [o["call_id"] for o in outputs] == ["call_A", "call_B"]
    assert len({i["id"] for i in items if "id" in i}) == len([i for i in items if "id" in i])


def test_openai_tool_order_and_cache_key_are_stable() -> None:
    provider = _openai("gpt-5.1", "responses")
    with session_affinity("session-42"):
        body_1 = provider._build_responses_body(GPT_HISTORY[:2], _anthropic_tools(), "gpt-5.1", 4096, 0.3, "high", None)
        body_2 = provider._build_responses_body(GPT_HISTORY, _anthropic_tools(), "gpt-5.1", 4096, 0.3, "low", None)
        chat = _openai("gpt-4.1", "chat_completions")._build_kwargs(GPT_HISTORY, _anthropic_tools(), "gpt-4.1", 4096, 0.3, None, None)
    assert body_1["prompt_cache_key"] == body_2["prompt_cache_key"] == "session-42"
    assert chat["extra_body"]["prompt_cache_key"] == "session-42"
    # Prefix order: instructions, then tools in caller order, then turns.
    assert body_1["instructions"] == body_2["instructions"]
    assert json.dumps(body_1["tools"]) == json.dumps(body_2["tools"])
    assert [t["name"] for t in body_1["tools"]] == ["read_file", "write_file"]
    assert body_2["input"][: len(body_1["input"])] == body_1["input"]


def _chat_completion(tool_calls: bool, usage: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": None if tool_calls else "done"}
    if tool_calls:
        message["tool_calls"] = [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}},
            {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "b"}'}},
        ]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}], "usage": usage}


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    async def close(self):
        return None


def _chat_chunks(usage: dict[str, Any]) -> list[Any]:
    def chunk(delta, finish=None, use=None):
        fields = {"content": None, "reasoning_content": None, "reasoning": None, "tool_calls": None, "function_call": None, "role": None}
        fields.update(delta)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**fields), finish_reason=finish, index=0)], usage=use)

    def tc(index, call_id, name, args):
        return SimpleNamespace(index=index, id=call_id, type="function", function=SimpleNamespace(name=name, arguments=args))

    return [
        chunk({"tool_calls": [tc(0, "call_1", "read_file", '{"path": "a"}')]}),
        chunk({"tool_calls": [tc(1, "call_2", "read_file", '{"path": "b"}')]}),
        chunk({}, finish="tool_calls"),
        SimpleNamespace(choices=[], usage=SimpleNamespace(**usage)),
    ]


CHAT_USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
              "prompt_tokens_details": {"cached_tokens": 64}}
GATEWAY_USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                 "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("route,model,effort", [
    ("openai_chat", "gpt-4.1", None),
    ("openai_chat", "gpt-5.2", "high"),
    ("gateway", "claude-sonnet-4-5", "high"),
    ("gateway", "claude-opus-5-5", "none"),
])
def test_chat_completions_calls(route: str, model: str, effort: str | None, stream: bool) -> None:
    provider = _gateway(model) if route == "gateway" else _openai(model, "chat_completions")
    usage = GATEWAY_USAGE if route == "gateway" else CHAT_USAGE
    captured: list[dict[str, Any]] = []

    async def create(**kwargs):
        captured.append(kwargs)
        if kwargs.get("stream"):
            usage_obj = dict(usage)
            if "prompt_tokens_details" in usage_obj:
                usage_obj["prompt_tokens_details"] = SimpleNamespace(**usage_obj["prompt_tokens_details"])
            return _AsyncIter(_chat_chunks(usage_obj))
        return _chat_completion(True, usage)

    _wire(provider, chat=create)
    call = provider.chat_stream if stream else provider.chat
    big_system = [{"role": "system", "content": "rules " * 3000}, *GPT_HISTORY[1:]]
    result = asyncio.run(call(big_system, _anthropic_tools(), model, 4096, 0.3, effort, None))
    assert result.finish_reason == "tool_calls", result.content
    assert [c.name for c in result.tool_calls] == ["read_file", "read_file"]
    assert len({c.id for c in result.tool_calls}) == 2
    expected_cached = 80 if route == "gateway" else 64
    assert result.usage.get("cached_tokens") == expected_cached
    if route == "gateway":
        assert result.usage.get("cache_creation_input_tokens") == 20
        assert _count_cache_markers(captured[0]) <= 4
    sent = captured[0]
    tool_msgs = [m for m in sent["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 2


def _responses_events(usage: dict[str, Any]) -> list[Any]:
    def item(**kw):
        return SimpleNamespace(**kw)

    return [
        item(type="response.reasoning_summary_text.delta", delta="thinking"),
        item(type="response.output_item.added", item=item(type="function_call", call_id="call_1", id="fc_1", name="read_file", arguments="")),
        item(type="response.function_call_arguments.delta", call_id="call_1", delta='{"path": "a"}'),
        item(type="response.output_item.done", item=item(type="function_call", call_id="call_1", id="fc_1", name="read_file", arguments='{"path": "a"}')),
        item(type="response.output_item.added", item=item(type="function_call", call_id="call_2", id="fc_2", name="read_file", arguments="")),
        item(type="response.output_item.done", item=item(type="function_call", call_id="call_2", id="fc_2", name="read_file", arguments='{"path": "b"}')),
        item(type="response.completed", response=item(status="completed", usage=usage, output=[])),
    ]


RESPONSES_USAGE = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                   "input_tokens_details": {"cached_tokens": 48}, "output_tokens_details": {"reasoning_tokens": 7}}


def _responses_reply() -> dict[str, Any]:
    return {"status": "completed", "usage": RESPONSES_USAGE, "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}], "encrypted_content": "enc"},
        {"type": "function_call", "call_id": "call_1", "id": "fc_1", "name": "read_file", "arguments": '{"path": "a"}'},
        {"type": "function_call", "call_id": "call_2", "id": "fc_2", "name": "read_file", "arguments": '{"path": "b"}'},
    ]}


def _check_responses_result(result) -> None:
    assert result.finish_reason == "stop"
    assert [c.id for c in result.tool_calls] == ["call_1|fc_1", "call_2|fc_2"]
    assert result.usage["cached_tokens"] == 48
    assert result.usage["reasoning_tokens"] == 7
    assert result.reasoning_content == "thinking"
    # The ids round-trip into the next request's input as paired items.
    replay = GPT_HISTORY[:2] + [{"role": "assistant", "content": None, "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
        for c in result.tool_calls]}] + [
        {"role": "tool", "tool_call_id": c.id, "content": "x"} for c in result.tool_calls]
    _, items = convert_messages(replay)
    assert [i["call_id"] for i in items if i.get("type") == "function_call"] == ["call_1", "call_2"]
    assert [i["call_id"] for i in items if i.get("type") == "function_call_output"] == ["call_1", "call_2"]


def _usage_obj(usage: dict[str, Any]) -> Any:
    return SimpleNamespace(**{k: SimpleNamespace(**v) if isinstance(v, dict) else v for k, v in usage.items()})


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("route", ["openai", "copilot", "azure"])
def test_responses_calls(route: str, stream: bool) -> None:
    model = "gpt-5.2"
    captured: list[dict[str, Any]] = []

    async def create(**body):
        captured.append(body)
        if body.get("stream"):
            return _AsyncIter(_responses_events(_usage_obj(RESPONSES_USAGE)))
        return _responses_reply()

    if route == "azure":
        provider: Any = AzureOpenAIProvider(api_key="k", api_base="https://x.openai.azure.com", default_model=model)
        provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    else:
        provider = _openai(model, "responses") if route == "openai" else GitHubCopilotProvider(default_model=model)
        if route == "copilot":
            async def fresh_key():
                return "t"
            provider._refresh_client_api_key = fresh_key
        _wire(provider, responses=create)
    call = provider.chat_stream if stream else provider.chat
    result = asyncio.run(call(GPT_HISTORY, _anthropic_tools(), model, 4096, 0.3, "high", None))
    _check_responses_result(result)
    body = captured[0]
    assert body["reasoning"] == {"effort": "high"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["store"] is False


@pytest.mark.parametrize("stream", [False, True])
def test_codex_calls(monkeypatch, stream: bool) -> None:
    calls: list[dict[str, Any]] = []
    usage = {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105, "cached_tokens": 40}
    from navin.providers.base import ToolCallRequest

    reply = ("", [ToolCallRequest(id="call_1|fc_1", name="read_file", arguments={"path": "a"}),
                  ToolCallRequest(id="call_2|fc_2", name="read_file", arguments={"path": "b"})], "stop", usage, "thinking")
    provider = _codex(monkeypatch, [reply], calls)
    call = provider.chat_stream if stream else provider.chat
    result = asyncio.run(call(GPT_HISTORY, _anthropic_tools(), "gpt-5.1-codex", 4096, 0.3, "none", None))
    assert [c.id for c in result.tool_calls] == ["call_1|fc_1", "call_2|fc_2"]
    body = calls[0]
    assert body["reasoning"]["effort"] == "low"  # Codex has no "none"
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["store"] is False and body["parallel_tool_calls"] is True
    # Same conversation start -> same cache key, turn after turn.
    calls.clear()
    asyncio.run(provider.chat(GPT_HISTORY[:2], None, "gpt-5.1-codex", 4096, 0.3, "high", None))
    first_key = calls[0]["prompt_cache_key"]
    calls.clear()
    asyncio.run(provider.chat(GPT_HISTORY, None, "gpt-5.1-codex", 4096, 0.3, "low", None))
    assert calls[0]["prompt_cache_key"] == first_key


def test_usage_parsers_read_every_cache_counter() -> None:
    compat = OpenAICompatProvider._extract_usage(_chat_completion(False, CHAT_USAGE))
    assert compat["cached_tokens"] == 64
    gateway = OpenAICompatProvider._extract_usage(_chat_completion(False, GATEWAY_USAGE))
    assert gateway["cached_tokens"] == 80 and gateway["cache_creation_input_tokens"] == 20
    responses = parse_response_output(_responses_reply()).usage
    assert responses["cached_tokens"] == 48
    bedrock = BedrockProvider._usage({"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 5, "cacheWriteInputTokens": 6})
    assert bedrock == {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cached_tokens": 5,
                       "cache_read_input_tokens": 5, "cache_creation_input_tokens": 6}


def test_non_anthropic_effort_aliases() -> None:
    assert reasoning_control.non_anthropic_effort("adaptive") == "high"
    assert reasoning_control.non_anthropic_effort("ultra") == "max"
    assert reasoning_control.non_anthropic_effort("off") == "none"
    assert reasoning_control.non_anthropic_effort("HIGH") == "HIGH"
    assert reasoning_control.non_anthropic_effort(None) is None


def test_matrix_covers_every_effort_the_product_accepts() -> None:
    from navin.agent.adaptive_reasoning import _ALLOWED_OVERRIDES

    assert set(_ALLOWED_OVERRIDES) <= set(EFFORTS) - {None}
    for model in CLAUDE_MODELS:
        for value in _reasoning_effort_values_for("anthropic", model):
            assert value == "" or value in EFFORTS
    dashes = chr(0x2014) + chr(0x2013)
    assert not any(ch in open(__file__, encoding="utf-8").read() for ch in dashes)


# -- Bedrock, long-running: ids, errors, timeouts, streams, credentials ---------

BEDROCK_ID_SPELLINGS = [
    "anthropic.claude-opus-4-8-v1:0",
    "us.anthropic.claude-opus-4-8-v1:0",
    "eu.anthropic.claude-opus-4-8-v1:0",
    "apac.anthropic.claude-opus-4-8-v1:0",
    "global.anthropic.claude-opus-4-8-v1:0",
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-opus-4-8-v1:0",
    "bedrock/us.anthropic.claude-opus-4-8-v1:0",
]


@pytest.mark.parametrize("model", BEDROCK_ID_SPELLINGS)
def test_bedrock_inference_profile_spellings_get_the_same_request(model: str) -> None:
    provider = BedrockProvider(client=SimpleNamespace())
    kwargs = provider._build_kwargs(SIMPLE, _anthropic_tools(), model, 200_000, 0.3, "xhigh", "required")
    extra = kwargs["additionalModelRequestFields"]
    assert extra["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert extra["output_config"] == {"effort": "xhigh"}
    assert kwargs["inferenceConfig"] == {"maxTokens": 128_000}  # clamped, no temperature
    assert kwargs["toolConfig"]["toolChoice"] == {"auto": {}}  # forced + thinking is a 400
    assert '"cachePoint"' in json.dumps(kwargs, default=str)
    assert kwargs["modelId"] == model.removeprefix("bedrock/")


@pytest.mark.parametrize("code,status,retry", [
    ("ThrottlingException", 429, True),
    ("ServiceUnavailableException", 503, True),
    ("InternalServerException", 500, True),
    ("ModelStreamErrorException", 424, True),
    ("modelStreamErrorException", None, True),  # raised mid-stream by EventStream
    ("ModelTimeoutException", 408, True),
    ("ModelNotReadyException", 429, True),
    ("ValidationException", 400, False),
    ("AccessDeniedException", 403, False),
])
def test_bedrock_errors_map_to_retryable_or_not(code: str, status: int | None, retry: bool) -> None:
    class _ClientError(Exception):
        pass

    error = _ClientError(f"An error occurred ({code})")
    metadata = {"HTTPStatusCode": status} if status is not None else {}
    error.response = {"Error": {"Code": code, "Message": "boom"}, "ResponseMetadata": metadata}
    result = BedrockProvider._handle_error(error)
    assert result.finish_reason == "error"
    assert bool(result.error_should_retry) is retry


def test_bedrock_display_refusal_keeps_reasoning(monkeypatch) -> None:
    from navin.providers import bedrock_provider

    monkeypatch.setattr(bedrock_provider, "_NO_DISPLAY_LEARNED", set())
    monkeypatch.setattr(bedrock_provider, "_NO_REASONING_LEARNED", set())
    calls: list[dict[str, Any]] = []

    def converse(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            raise _RefusalError("ValidationException: thinking.adaptive.display: Extra inputs are not permitted")
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "stopReason": "end_turn", "usage": {}}

    provider = BedrockProvider(client=SimpleNamespace(converse=converse))
    model = "eu.anthropic.claude-opus-4-8-v1:0"
    assert asyncio.run(provider.chat(SIMPLE, model=model, reasoning_effort="high")).content == "ok"
    assert [c["additionalModelRequestFields"]["thinking"] for c in calls] == [
        {"type": "adaptive", "display": "summarized"}, {"type": "adaptive"},
    ]
    assert calls[1]["additionalModelRequestFields"]["output_config"] == {"effort": "high"}


def test_bedrock_tool_results_keep_images_and_json() -> None:
    png = base64.b64encode(b"\x89PNG....").decode()
    history = [
        {"role": "user", "content": "look"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "shot", "arguments": "{}"}},
            {"id": "t2", "type": "function", "function": {"name": "stat", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": [
            {"type": "text", "text": "screen"}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}}]},
        {"role": "tool", "tool_call_id": "t2", "content": [{"size": 3}]},
    ]
    kwargs = BedrockProvider(client=SimpleNamespace())._build_kwargs(
        history, _anthropic_tools(), _bedrock_id("claude-sonnet-4-6"), 4096, 0.3, None, None,
    )
    results = [b["toolResult"] for b in kwargs["messages"][-1]["content"] if "toolResult" in b]
    assert [r["toolUseId"] for r in results] == ["t1", "t2"]
    assert results[0]["content"][0] == {"text": "screen"}
    assert results[0]["content"][1]["image"]["format"] == "png"
    assert results[1]["content"] == [{"json": {"size": 3}}]


def test_bedrock_gpt_oss_round_trip_and_reasoning() -> None:
    captured: list[dict[str, Any]] = []

    def converse(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return {"output": {"message": {"content": [
            {"reasoningContent": {"reasoningText": {"text": "plan"}}},
            {"toolUse": {"toolUseId": "tooluse_1", "name": "read_file", "input": {"path": "a"}}},
            {"toolUse": {"toolUseId": "tooluse_2", "name": "read_file", "input": {"path": "b"}}},
        ]}}, "stopReason": "tool_use", "usage": {"inputTokens": 5, "outputTokens": 2}}

    provider = BedrockProvider(client=SimpleNamespace(converse=converse))
    model = "openai.gpt-oss-120b-1:0"
    result = asyncio.run(provider.chat(SIMPLE, _anthropic_tools(), model, 4096, 0.3, "high", "required"))
    assert [c.id for c in result.tool_calls] == ["tooluse_1", "tooluse_2"]
    first = captured[0]
    assert first["additionalModelRequestFields"] == {"reasoning": {"effort": "high"}}
    assert first["toolConfig"]["toolChoice"] == {"any": {}}
    assert '"cachePoint"' not in json.dumps(first, default=str)
    # Replay: unsigned GPT reasoning is not sent back as a Claude reasoning block.
    replay = SIMPLE + [{
        "role": "assistant", "content": None, "thinking_blocks": result.thinking_blocks,
        "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                       for c in result.tool_calls],
    }] + [{"role": "tool", "tool_call_id": c.id, "content": "x"} for c in result.tool_calls]
    kwargs = provider._build_kwargs(replay, _anthropic_tools(), model, 4096, 0.3, "high", None)
    assistant = kwargs["messages"][1]["content"]
    assert [next(iter(b)) for b in assistant] == ["toolUse", "toolUse"]
    assert [b["toolResult"]["toolUseId"] for b in kwargs["messages"][2]["content"]] == ["tooluse_1", "tooluse_2"]


def test_bedrock_stalled_stream_times_out_and_is_closed(monkeypatch) -> None:
    import threading

    monkeypatch.setenv("NAVIN_STREAM_IDLE_TIMEOUT_S", "0.2")
    release = threading.Event()

    class _Stream:
        closed = False

        def __iter__(self):
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hi"}}}
            release.wait(2)

        def close(self):
            _Stream.closed = True
            release.set()

    provider = BedrockProvider(client=SimpleNamespace(converse_stream=lambda **_: {"stream": _Stream()}))
    result = asyncio.run(provider.chat_stream(SIMPLE, model=_bedrock_id("claude-opus-4-8")))
    assert result.finish_reason == "error" and result.error_kind == "timeout"
    assert _Stream.closed


def test_bedrock_mid_stream_error_is_reported_retryable() -> None:
    class _EventStreamError(Exception):
        pass

    error = _EventStreamError("An error occurred (modelStreamErrorException) when calling ConverseStream")
    error.response = {"Error": {"Code": "modelStreamErrorException", "Message": "retry"}, "ResponseMetadata": {}}

    class _Stream:
        def __iter__(self):
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "partial"}}}
            raise error

    provider = BedrockProvider(client=SimpleNamespace(converse_stream=lambda **_: {"stream": _Stream()}))
    result = asyncio.run(provider.chat_stream(SIMPLE, model=_bedrock_id("claude-opus-4-8")))
    assert result.finish_reason == "error" and result.error_should_retry is True


def test_bedrock_assumed_role_credentials_refresh(monkeypatch) -> None:
    pytest.importorskip("boto3")
    import datetime

    from navin.providers import bedrock_media

    issued: list[int] = []

    class _Sts:
        def assume_role(self, **_):
            issued.append(1)
            expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
            return {"Credentials": {"AccessKeyId": f"AK{len(issued)}", "SecretAccessKey": "s",
                                    "SessionToken": "t", "Expiration": expiry}}

    class _Base:
        def client(self, name, **_):
            assert name == "sts"
            return _Sts()

    import boto3

    real_session = boto3.Session
    monkeypatch.setattr(boto3, "Session", lambda *a, **kw: real_session(**kw) if kw.get("botocore_session") else _Base())
    session = bedrock_media.aws_session(None, "arn:aws:iam::123456789012:role/navin")
    creds = session.get_credentials()
    # Expiring within the refresh window: reading them again re-assumes the role.
    assert creds.get_frozen_credentials().access_key.startswith("AK")
    assert len(issued) >= 2


# -- The Navin route: app -> OpenAI wire -> LiteLLM -> Bedrock ------------------
#
# LiteLLM returns Claude's thinking as ``reasoning_content`` plus
# ``thinking_blocks`` (signed, or redacted) and reads ``thinking_blocks`` back
# on assistant messages. A thinking turn that called a tool must come back with
# them, or Bedrock refuses it (or LiteLLM's modify_params drops thinking for
# every follow-up step).

LITELLM_THINKING = [
    {"type": "thinking", "thinking": "Read both files.", "signature": "EqQBCkYIBRgC"},
    {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix"},
]


def _litellm_reply() -> dict[str, Any]:
    return {
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "reasoning_content": "Read both files.",
            "thinking_blocks": LITELLM_THINKING,
            "tool_calls": [
                {"id": "toolu_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}},
                {"id": "toolu_2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "b"}'}},
            ],
        }}],
        "usage": {"prompt_tokens": 5000, "completion_tokens": 50, "total_tokens": 5050,
                  "prompt_tokens_details": {"cached_tokens": 4800},
                  "cache_read_input_tokens": 4800, "cache_creation_input_tokens": 150},
    }


def _litellm_chunks() -> list[dict[str, Any]]:
    def chunk(delta, finish=None):
        return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    return [
        chunk({"role": "assistant", "reasoning_content": "Read both ",
               "thinking_blocks": [{"type": "thinking", "thinking": "Read both ", "signature": None}]}),
        chunk({"reasoning_content": "files.",
               "thinking_blocks": [{"type": "thinking", "thinking": "files.", "signature": None}]}),
        chunk({"thinking_blocks": [{"type": "thinking", "thinking": "", "signature": "EqQBCkYIBRgC"}]}),
        chunk({"thinking_blocks": [{"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix"}]}),
        chunk({"tool_calls": [{"index": 0, "id": "toolu_1", "type": "function",
                               "function": {"name": "read_file", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"path": "a"}'}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "toolu_2", "type": "function",
                               "function": {"name": "read_file", "arguments": '{"path": "b"}'}}]}),
        chunk({}, finish="tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 5000, "completion_tokens": 50, "total_tokens": 5050,
                                   "prompt_tokens_details": {"cached_tokens": 4800},
                                   "cache_read_input_tokens": 4800, "cache_creation_input_tokens": 150}},
    ]


@pytest.mark.parametrize("base", GATEWAY_BASES)
@pytest.mark.parametrize("stream", [False, True])
def test_navin_route_claude_thinking_tool_round_trip(base: str, stream: bool) -> None:
    model = "anthropic/claude-opus-4-8"
    provider = _gateway(model, base)
    sent: list[dict[str, Any]] = []

    async def create(**kwargs):
        sent.append(copy.deepcopy(kwargs))
        # The real SDK types, so LiteLLM's extra fields travel as they do live.
        from openai.types.chat import ChatCompletion, ChatCompletionChunk

        if kwargs.get("stream"):
            return _AsyncIter([
                ChatCompletionChunk.model_validate({
                    "id": "c", "object": "chat.completion.chunk", "created": 0, "model": model, **c,
                }) for c in _litellm_chunks()
            ])
        return ChatCompletion.model_validate({
            "id": "c", "object": "chat.completion", "created": 0, "model": model, **_litellm_reply(),
        })

    _wire(provider, chat=create)
    history = [{"role": "system", "content": "rules " * 3000}, {"role": "user", "content": "Read a and b."}]
    call = provider.chat_stream if stream else provider.chat
    result = asyncio.run(call(history, _anthropic_tools(), model, 16_000, 0.3, "high", None))
    assert result.finish_reason == "tool_calls"
    assert [c.id for c in result.tool_calls] == ["toolu_1", "toolu_2"]
    assert result.thinking_blocks == LITELLM_THINKING
    assert result.reasoning_content == "Read both files."
    assert result.usage["cached_tokens"] == 4800
    assert result.usage["cache_creation_input_tokens"] == 150
    first = sent[0]
    assert first["reasoning_effort"] == "high" and "temperature" not in first

    # Next step: the assistant turn goes back with its signed blocks intact.
    follow = history + [{
        "role": "assistant", "content": result.content, "reasoning_content": result.reasoning_content,
        "thinking_blocks": result.thinking_blocks,
        "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                       for c in result.tool_calls],
    }] + [{"role": "tool", "tool_call_id": c.id, "name": c.name, "content": "x" * 4000} for c in result.tool_calls]
    kwargs = provider._build_kwargs(follow, _anthropic_tools(), model, 16_000, 0.3, "high", None)
    assistant = next(m for m in kwargs["messages"] if m["role"] == "assistant")
    assert assistant["thinking_blocks"] == LITELLM_THINKING
    assert [m["tool_call_id"] for m in kwargs["messages"] if m["role"] == "tool"] == ["toolu_1", "toolu_2"]
    # Cache: system, tools and the conversation tail, never more than 4.
    assert _count_cache_markers(kwargs) <= 4
    assert isinstance(kwargs["messages"][0]["content"], list)
    assert kwargs["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    marked_tail = [m for m in kwargs["messages"][1:] if isinstance(m.get("content"), list)
                   and any("cache_control" in b for b in m["content"])]
    assert marked_tail, "a tool loop must still cache the conversation"


def test_navin_route_drops_unreplayable_blocks_and_other_models_never_get_them() -> None:
    blocks = [{"type": "thinking", "thinking": "unsigned"}, {"type": "redacted_thinking"}, *LITELLM_THINKING]
    message = {
        "role": "assistant", "content": "", "thinking_blocks": blocks,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
    }
    current_turn = [
        {"role": "user", "content": "hi"}, message,
        {"role": "tool", "tool_call_id": "call_1", "content": "file"},
    ]
    # Every model behind the gateway gets the current turn's blocks back:
    # GPT on Bedrock Converse needs them as much as Claude (seen on test-02).
    for model in ("anthropic/claude-sonnet-4-6", "openai/gpt-6.1-sol", "glm/glm-5.3"):
        kwargs = _gateway(model)._build_kwargs(current_turn, None, model, 4096, 0.3, None, None)
        assert kwargs["messages"][1]["thinking_blocks"] == LITELLM_THINKING, model
    # A previous turn's blocks are never sent (the model may have changed).
    previous_turn = [*current_turn, {"role": "assistant", "content": "done"}, {"role": "user", "content": "go"}]
    kwargs = _gateway("anthropic/claude-sonnet-4-6")._build_kwargs(
        previous_turn, None, "anthropic/claude-sonnet-4-6", 4096, 0.3, None, None,
    )
    assert "thinking_blocks" not in kwargs["messages"][1]
    # Outside the gateway, never.
    kwargs = _openai("gpt-4.1", "chat_completions")._build_kwargs(
        current_turn, None, "gpt-4.1", 4096, 0.3, None, None,
    )
    assert "thinking_blocks" not in kwargs["messages"][1]


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "anthropic/claude-fable-5-1"])
def test_navin_route_off_never_asks_litellm_to_disable_thinking_where_it_cannot(model: str) -> None:
    kwargs = _gateway(model)._build_kwargs(SIMPLE, None, model, 8192, 0.3, "none", None)
    assert kwargs["reasoning_effort"] == "low"


def test_navin_route_no_cache_markers_for_models_without_prompt_caching_rules() -> None:
    kwargs = _gateway("glm/glm-5.3")._build_kwargs(
        [{"role": "system", "content": "rules " * 3000}, {"role": "user", "content": "hi"}],
        _anthropic_tools(), "glm/glm-5.3", 4096, 0.3, None, None,
    )
    assert _count_cache_markers(kwargs) == 0


def test_navin_route_host_is_not_a_behaviour_switch() -> None:
    model = "anthropic/claude-opus-4-8"
    history = [{"role": "system", "content": "rules " * 3000}, *HISTORY[1:]]
    payloads = []
    for base in GATEWAY_BASES:
        kwargs = _gateway(model, base)._build_kwargs(history, _anthropic_tools(), model, 16_000, 0.3, "high", None)
        payloads.append(json.dumps(kwargs, sort_keys=True, default=str))
    assert payloads[0] == payloads[1]
    spec = find_by_name("navin")
    assert spec is not None and not spec.is_local  # detect_by_base_keyword only feeds the local fallback


@pytest.mark.parametrize("effort,stalls", [("high", False), ("xhigh", False), (None, True), ("none", True)])
def test_navin_route_thinking_silence_before_first_byte_is_not_a_stall(monkeypatch, effort, stalls) -> None:
    monkeypatch.setenv("NAVIN_STREAM_IDLE_TIMEOUT_S", "0.2")
    monkeypatch.setenv("NAVIN_STREAM_BUFFERED_IDLE_S", "2")
    model = "anthropic/claude-opus-4-8"
    provider = _gateway(model, GATEWAY_BASES[1])

    class _SlowStart(_AsyncIter):
        async def __anext__(self):
            if not getattr(self, "_waited", False):
                self._waited = True
                await asyncio.sleep(0.6)  # thinking, nothing streamed yet
            return await super().__anext__()

    def chunk(text):
        return SimpleNamespace(choices=[SimpleNamespace(index=0, finish_reason=None, delta=SimpleNamespace(
            content=text, reasoning_content=None, reasoning=None, tool_calls=None, function_call=None,
            thinking_blocks=None))], usage=None)

    async def create(**_):
        return _SlowStart([chunk("Hello")])

    _wire(provider, chat=create)

    async def sink(_):
        return None

    result = asyncio.run(provider.chat_stream(SIMPLE, None, model, 4096, 0.3, effort, None, on_content_delta=sink))
    assert (result.finish_reason == "error") is stalls, result.content



def test_navin_route_never_replays_blocks_from_earlier_turns() -> None:
    """A signature is bound to its turn: Bedrock refuses an older one (400)."""
    tool_call = [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "", "tool_calls": tool_call, "thinking_blocks": LITELLM_THINKING},
        {"role": "tool", "tool_call_id": "call_1", "content": "file"},
        {"role": "assistant", "content": "done", "thinking_blocks": LITELLM_THINKING},
        {"role": "user", "content": "b"},
    ]
    kwargs = _gateway("anthropic/claude-sonnet-5.5")._build_kwargs(
        history, None, "anthropic/claude-sonnet-5.5", 4096, 0.3, None, None,
    )
    assert all("thinking_blocks" not in m for m in kwargs["messages"])


BINDING_REFUSAL = (
    "litellm.BadRequestError: BedrockException - {\"message\":\"The model returned the following "
    "errors: messages.3.content.0: Invalid `signature` in `thinking` block. The block is bound "
    "to a different conversation. Remove the block, or set "
    "`thinking.block_binding.prefix_mismatch_behavior` to \\\"drop_block\\\".\"}"
)


@pytest.mark.parametrize("stream", [False, True])
def test_navin_route_heals_a_signature_refusal_on_the_chosen_model(monkeypatch, stream) -> None:
    """Seen live: Opus 5.5 kept refusing its own block, Sonnet served the turn."""
    from navin.providers import openai_compat_provider as compat

    monkeypatch.setattr(compat, "_REJECTED_THINKING", {})
    model = "anthropic/claude-opus-5.5"
    stale = [{"type": "thinking", "thinking": "old", "signature": "SIG-STALE"}]
    tool_call = [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
    history = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "summary of the compacted start"},
        {"role": "assistant", "content": "", "tool_calls": tool_call, "thinking_blocks": stale},
        {"role": "tool", "tool_call_id": "call_1", "content": "file"},
    ]
    calls: list[dict[str, Any]] = []

    def chunk(text):
        return SimpleNamespace(choices=[SimpleNamespace(index=0, finish_reason="stop", delta=SimpleNamespace(
            content=text, reasoning_content=None, reasoning=None, tool_calls=None, function_call=None,
            thinking_blocks=None))], usage=None)

    async def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        replayed = [b for m in kwargs["messages"] for b in m.get("thinking_blocks") or ()]
        if replayed:
            raise _RefusalError(BINDING_REFUSAL, body={"message": BINDING_REFUSAL})
        return _AsyncIter([chunk("ok")]) if kwargs.get("stream") else OK_CHAT

    provider = _gateway(model)
    _wire(provider, chat=create)
    call = provider.chat_stream if stream else provider.chat
    result = asyncio.run(call(history, None, model, 4096, 0.3, "high", None))

    assert result.finish_reason != "error", result.content
    assert len(calls) == 2
    assert calls[0]["model"] == calls[1]["model"]
    assert "thinking_blocks" in calls[0]["messages"][2]
    assert "thinking_blocks" not in calls[1]["messages"][2]
    # The next step of the turn does not pay the refusal again.
    asyncio.run(call(history, None, model, 4096, 0.3, "high", None))
    assert len(calls) == 3 and "thinking_blocks" not in calls[2]["messages"][2]
    # A block written after the heal is replayed as usual.
    fresh = [*history, {"role": "assistant", "content": "", "tool_calls": [{**tool_call[0], "id": "call_2"}],
                        "thinking_blocks": LITELLM_THINKING},
             {"role": "tool", "tool_call_id": "call_2", "content": "file"}]
    kwargs = provider._build_kwargs(fresh, None, model, 4096, 0.3, "high", None)
    assert kwargs["messages"][4]["thinking_blocks"] == LITELLM_THINKING


def test_navin_route_a_refusal_that_persists_still_reaches_the_fallback_chain(monkeypatch) -> None:
    from navin.providers import openai_compat_provider as compat

    monkeypatch.setattr(compat, "_REJECTED_THINKING", {})
    calls: list[dict[str, Any]] = []
    refusal = _RefusalError(BINDING_REFUSAL, body={"message": BINDING_REFUSAL})
    provider = _gateway("anthropic/claude-opus-5.5")
    _wire(provider, chat=_scripted(calls, [refusal]))
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "thinking_blocks": LITELLM_THINKING,
         "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file"},
    ]
    result = asyncio.run(provider.chat(history, None, "anthropic/claude-opus-5.5", 4096, 0.3, None, None))

    assert result.finish_reason == "error"
    assert len(calls) == 2  # one heal, never a loop
    # Other refusals are not mistaken for a signature problem.
    assert not compat._is_thinking_binding_rejection(
        _RefusalError("messages.1.content.0.type: Expected `thinking` or `redacted_thinking`")
    )
