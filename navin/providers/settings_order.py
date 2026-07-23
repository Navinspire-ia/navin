"""Settings UI ordering and retired LLM provider names.

Match priority in ``PROVIDERS`` stays gateway-first for routing. Settings
display order is independent and puts mainstream vendors first.
"""

from __future__ import annotations

# Retired from the product surface (LLM settings / onboard / image gen picks).
# Kept as a denylist so stale config extras and old docs cannot resurface.
RETIRED_LLM_PROVIDERS = frozenset(
    {
        "qianfan",
        "stepfun",
        "xiaomi_mimo",
        "longcat",
        "ant_ling",
        "minimax_anthropic",
        "dashscope",
        "volcengine",
        "volcengine_coding_plan",
        "byteplus",
        "byteplus_coding_plan",
        "skywork",
        "aihubmix",
    }
)

# Lower index = higher in Settings → Providers (unconfigured list).
SETTINGS_PROVIDER_ORDER: tuple[str, ...] = (
    "openai",
    "azure_openai",
    "bedrock",
    "gemini",
    "anthropic",
    "zhipu",
    "moonshot",
    "kimi_coding",
    "deepseek",
    "vllm",
    "ollama",
    "lm_studio",
    "openai_codex",
    "github_copilot",
    "openrouter",
    "mistral",
    "minimax",
    "nvidia",
    "groq",
    "huggingface",
    "siliconflow",
    "novita",
    "opencode",
    "opencode_go",
    "custom",
    "atomic_chat",
    "ovms",
    "assemblyai",
)

_SETTINGS_RANK = {name: index for index, name in enumerate(SETTINGS_PROVIDER_ORDER)}


def settings_provider_rank(name: str) -> int:
    """Return sort key for Settings provider rows (unknowns last)."""
    return _SETTINGS_RANK.get(name, 10_000)


def is_retired_llm_provider(name: str) -> bool:
    return name.replace("-", "_").lower() in RETIRED_LLM_PROVIDERS
