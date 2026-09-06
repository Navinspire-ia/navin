"""OpenRouter requests must ask for the fastest upstream provider.

OpenRouter's default routing balances on price and regularly lands on hosts
serving 15-30 tok/s, which makes the same model feel an order of magnitude
slower than it should. Navin sends provider.sort=throughput by default, while
still letting a user-configured extra_body take precedence.
"""

from __future__ import annotations

from navin.providers.openai_compat_provider import OpenAICompatProvider
from navin.providers.registry import PROVIDERS


def _spec(name: str):
    return next(s for s in PROVIDERS if s.name == name)


def _kwargs(provider: OpenAICompatProvider) -> dict:
    return provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model=None,
        max_tokens=256,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )


def test_openrouter_requests_sort_providers_by_throughput():
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="moonshotai/kimi-k2",
        spec=_spec("openrouter"),
    )
    kwargs = _kwargs(provider)
    assert kwargs["extra_body"]["provider"] == {"sort": "throughput"}


def test_user_extra_body_overrides_the_default_routing():
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="moonshotai/kimi-k2",
        spec=_spec("openrouter"),
        extra_body={"provider": {"sort": "price"}},
    )
    kwargs = _kwargs(provider)
    assert kwargs["extra_body"]["provider"]["sort"] == "price"


def test_non_openrouter_providers_do_not_get_routing_preferences():
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="gpt-4o-mini",
        spec=_spec("openai"),
    )
    kwargs = _kwargs(provider)
    assert "provider" not in kwargs.get("extra_body", {})
    assert "usage" not in kwargs.get("extra_body", {})


def test_openrouter_tool_requests_require_tool_capable_endpoints():
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="deepseek/deepseek-v4-flash",
        spec=_spec("openrouter"),
    )
    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "apply_patch", "parameters": {}}}],
        model=None,
        max_tokens=256,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )
    assert kwargs["extra_body"]["provider"]["sort"] == "throughput"
    assert kwargs["extra_body"]["provider"]["require_parameters"] is True
    # OpenRouter treats a function named apply_patch as OpenAI-native patching
    # and 404s non-OpenAI hosts. Wire it as file_patch outbound.
    assert kwargs["tools"][0]["function"]["name"] == "file_patch"


def test_openrouter_aliases_apply_patch_in_history_and_tool_choice():
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="meta/muse-spark-1.1",
        spec=_spec("openrouter"),
    )
    kwargs = provider._build_kwargs(
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "arguments": "{\"path\":\"a.py\"}",
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "ok",
            },
        ],
        tools=[{"type": "function", "function": {"name": "apply_patch", "parameters": {}}}],
        model=None,
        max_tokens=256,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice={
            "type": "function",
            "function": {"name": "apply_patch"},
        },
    )
    assert kwargs["tools"][0]["function"]["name"] == "file_patch"
    assert kwargs["tool_choice"]["function"]["name"] == "file_patch"
    assert kwargs["messages"][1]["tool_calls"][0]["function"]["name"] == "file_patch"
    assert provider._inbound_tool_name("file_patch") == "apply_patch"


def test_openrouter_drops_require_parameters_after_tool_endpoint_miss():
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="meta/muse-spark-1.1",
        spec=_spec("openrouter"),
    )
    exc = RuntimeError(
        "Error: {'message': 'No endpoints found that support tool use. "
        'Try disabling "apply_patch".\', \'code\': 404}'
    )
    assert provider._register_tool_endpoint_miss("meta/muse-spark-1.1", exc)
    assert not provider._register_tool_endpoint_miss("meta/muse-spark-1.1", exc)
    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        model="meta/muse-spark-1.1",
        max_tokens=256,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )
    assert "require_parameters" not in kwargs["extra_body"]["provider"]


def test_openrouter_requests_include_real_cost_accounting():
    """usage.include=true makes OpenRouter return the billed cost (usage.cost)
    so the managed budget debits actual spend instead of an estimate."""
    provider = OpenAICompatProvider(
        api_key="k",
        default_model="deepseek/deepseek-v4-flash",
        spec=_spec("openrouter"),
    )
    kwargs = _kwargs(provider)
    assert kwargs["extra_body"]["usage"] == {"include": True}


def test_openrouter_requests_pin_the_session_for_cache_affinity():
    """A stable session_id makes OpenRouter sticky-route every request of the
    session to the same upstream host, keeping the prompt cache warm."""
    from navin.providers.session_affinity import current_session_id, session_affinity

    provider = OpenAICompatProvider(
        api_key="k",
        default_model="z-ai/glm-5.2",
        spec=_spec("openrouter"),
    )
    with session_affinity("webui:main"):
        assert current_session_id() == "webui:main"
        kwargs = _kwargs(provider)
    assert kwargs["extra_body"]["session_id"] == "webui:main"
    assert current_session_id() is None
    # Without an active session, no session_id is sent.
    assert "session_id" not in _kwargs(provider).get("extra_body", {})


def test_direct_openai_requests_use_prompt_cache_key():
    from navin.providers.session_affinity import session_affinity

    provider = OpenAICompatProvider(
        api_key="k",
        default_model="gpt-4o-mini",
        spec=_spec("openai"),
    )
    with session_affinity("telegram:123"):
        kwargs = _kwargs(provider)
    assert kwargs["extra_body"]["prompt_cache_key"] == "telegram:123"
    # OpenRouter-only fields must not leak to direct OpenAI requests.
    assert "session_id" not in kwargs["extra_body"]


def test_session_affinity_truncates_oversized_keys():
    from navin.providers.session_affinity import current_session_id, session_affinity

    with session_affinity("x" * 500):
        assert len(current_session_id()) == 256


def test_billed_cost_is_extracted_as_integer_micro_usd():
    usage = OpenAICompatProvider._extract_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost": 0.0123,
            }
        }
    )
    assert usage["cost_micro_usd"] == 12300
    assert all(isinstance(v, int) for v in usage.values())


def test_missing_cost_leaves_usage_untouched():
    usage = OpenAICompatProvider._extract_usage(
        {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    )
    assert "cost_micro_usd" not in usage
