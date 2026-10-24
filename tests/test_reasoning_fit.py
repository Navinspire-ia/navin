# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Every routed reasoning effort reaches every model as a value it accepts.

Measured live on 2026-10-07 (Bedrock, Z.ai, Gemini): each case here was a
failed step before the fix.
"""

from __future__ import annotations

from navin.providers import claude_capabilities as claude
from navin.providers import reasoning_control as rc
from navin.providers.openai_compat_provider import (
    OpenAICompatProvider,
    _sign_foreign_tool_calls,
)
from navin.providers.registry import find_by_name


def test_known_families_fit_refused_efforts():
    assert rc.nearest_effort("xhigh", rc.accepted_efforts("gemini-3.8-flash")) == "high"
    assert rc.nearest_effort("max", rc.accepted_efforts("models/gemini-3.5-flash")) == "high"
    assert rc.nearest_effort("none", rc.accepted_efforts("gemini-3.5-flash-lite")) == "minimal"
    assert rc.nearest_effort("medium", rc.accepted_efforts("glm-5.3-flash")) == "high"
    assert rc.nearest_effort("xhigh", rc.accepted_efforts("z-ai/glm-5.3")) == "max"
    # Accepted values and unknown models pass through untouched.
    assert rc.nearest_effort("low", rc.accepted_efforts("gemini-3.8-flash")) == "low"
    assert rc.nearest_effort("medium", rc.accepted_efforts("glm-5.2")) == "medium"
    assert rc.nearest_effort("adaptive", rc.accepted_efforts("gemini-3.8-flash")) == "adaptive"


def test_a_refusal_listing_valid_values_is_learned():
    key = "vendor/new-model-fit-test"
    error = "400 Invalid reasoning_effort: xhigh. Valid values are: high, low, medium, minimal, none"
    assert rc.learn_accepted_efforts(key, "xhigh", error) is True
    assert rc.nearest_effort("xhigh", rc.accepted_efforts("vendor/new-model", key)) == "high"
    # Same lesson twice is not a reason to retry again.
    assert rc.learn_accepted_efforts(key, "xhigh", error) is False
    zai = "This model always engages in thinking and cannot be disabled; please use low, high, or max"
    assert rc.learn_accepted_efforts("zai/glm-6-fit-test", "medium", zai) is True
    assert rc.accepted_efforts("glm-6", "zai/glm-6-fit-test") == frozenset({"low", "high", "max"})
    assert rc.learn_accepted_efforts("k", "medium", "502 upstream timed out") is False


def test_openai_compat_sends_the_fitted_effort():
    provider = OpenAICompatProvider(api_key="k", spec=find_by_name("gemini"))
    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "hi"}], None, "gemini-3.8-flash", 256, 0.2, "xhigh", None
    )
    assert kwargs["reasoning_effort"] == "high"
    lite = provider._build_kwargs(
        [{"role": "user", "content": "hi"}], None, "gemini-3.5-flash-lite", 256, 0.2, "none", None
    )
    assert lite.get("reasoning_effort") == "minimal"


def test_claude_off_shape_is_learned_from_a_refusal():
    model = "anthropic.claude-sonnet-9-fit-test"
    assert claude.claude_thinking_fields(model, "none", max_tokens=100)[0] == {"thinking": {"type": "disabled"}}
    refused = 'To turn thinking off on this model, send "thinking": {"type": "between_tools"} instead of {"type": "disabled"}.'
    assert claude.learn_claude_thinking_off(model, refused) is True
    assert claude.claude_thinking_fields(model, "none", max_tokens=100)[0] == {"thinking": {"type": "between_tools"}}
    assert claude.learn_claude_thinking_off(model, refused) is False


def test_gemini_gets_placeholder_signatures_for_foreign_tool_calls():
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "x"},
        {"role": "user", "content": "now"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "b", "function": {"name": "f", "arguments": "{}"}},
            {"id": "c", "function": {"name": "f", "arguments": "{}"},
             "extra_content": {"google": {"thought_signature": "REAL"}}},
        ]},
        {"role": "tool", "tool_call_id": "b", "content": "x"},
    ]
    _sign_foreign_tool_calls(messages)
    current = messages[4]["tool_calls"]
    assert current[0]["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert current[1]["extra_content"]["google"]["thought_signature"] == "REAL"
    # Older turns are not validated by Gemini and stay untouched.
    assert "extra_content" not in messages[1]["tool_calls"][0]


def test_an_error_without_body_does_not_read_as_effort_none():
    error = RuntimeError("400 Reasoning is mandatory for this endpoint and cannot be disabled.")
    assert rc.learn_accepted_efforts("vendor/mandatory-fit-test", "high", error) is False
