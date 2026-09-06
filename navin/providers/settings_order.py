"""Settings UI ordering and retired LLM provider names.

Match priority in ``PROVIDERS`` stays gateway-first for routing. Settings
display order is independent and puts mainstream vendors first.
"""

from __future__ import annotations

from navin.optional_live import live_modules_available

# Retired from the product surface (LLM settings / onboard / image gen picks).
# Kept as a denylist so stale config extras and old docs cannot resurface.
RETIRED_LLM_PROVIDERS = frozenset(
    {
        "xiaomi_mimo",
        "longcat",
        "ant_ling",
        "minimax_anthropic",
        "volcengine_coding_plan",
        "byteplus",
        "byteplus_coding_plan",
        "skywork",
        "aihubmix",
    }
)

# Lower index = higher in Settings → Providers (unconfigured list).
SETTINGS_PROVIDER_ORDER: tuple[str, ...] = (
    "navin",
    "openai",
    "azure_openai",
    "bedrock",
    "gemini",
    "anthropic",
    "qwen",
    "zai",
    "zhipu",
    "moonshot",
    "kimi_coding",
    "deepseek",
    "minimax",
    "siliconflow",
    "volcengine",
    "hunyuan",
    "qianfan",
    "stepfun",
    "vllm",
    "ollama",
    "lm_studio",
    "openai_codex",
    "github_copilot",
    "xai",
    "xai_oauth",
    "openrouter",
    "omniroute",
    "mistral",
    "nvidia",
    "groq",
    "huggingface",
    "novita",
    "opencode",
    "opencode_go",
    "custom",
    "custom_anthropic",
    "atomic_chat",
    "ovms",
    "assemblyai",
)
if not live_modules_available():
    SETTINGS_PROVIDER_ORDER = tuple(
        name for name in SETTINGS_PROVIDER_ORDER if name != "navin"
    )
    RETIRED_LLM_PROVIDERS = RETIRED_LLM_PROVIDERS | {"navin"}

_SETTINGS_RANK = {name: index for index, name in enumerate(SETTINGS_PROVIDER_ORDER)}


def settings_provider_rank(name: str) -> int:
    """Return sort key for Settings provider rows (unknowns last)."""
    return _SETTINGS_RANK.get(name, 10_000)


def is_retired_llm_provider(name: str) -> bool:
    return name.replace("-", "_").lower() in RETIRED_LLM_PROVIDERS
