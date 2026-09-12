# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""
Provider Registry - single source of truth for LLM provider metadata.

Adding a new provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Add a field to ProvidersConfig in config/schema.py.
  Done. Env vars, config matching, status display all derive from here.

Order matters - it controls match priority and fallback. Gateways first.
Every entry writes out all fields so you can copy-paste as a template.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic.alias_generators import to_snake

from navin.optional_live import live_modules_available


@dataclass(frozen=True)
class ProviderModelSpec:
    """A curated model exposed by providers without a model-list endpoint."""

    id: str
    label: str = ""
    description: str = ""
    context_window: int | None = None

@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  - the user's API key
      {api_base} - api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "dashscope"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # env var for API key, e.g. "DASHSCOPE_API_KEY"
    # Alternative env var names honored by other tools (e.g. KIMI_API_KEY for
    # Moonshot). A key found under any of these works exactly like env_key.
    env_key_aliases: tuple[str, ...] = ()
    display_name: str = ""  # shown in `navin status`
    model_catalog: str = "auto"  # WebUI model-list source
    builtin_models: tuple[ProviderModelSpec, ...] = ()
    settings_alias_for: str = ""  # compatibility alias grouped under this provider in Settings

    # which provider implementation to use
    # "openai_compat" | "anthropic" | "azure_openai" | "openai_codex" | "github_copilot" | "xai_oauth" | "bedrock"
    backend: str = "openai_compat"

    # extra env vars / request headers supplied by the provider integration.
    env_extras: tuple[tuple[str, str], ...] = ()
    default_extra_headers: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False  # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # OpenAI-compatible base URL for this provider
    # Whether chat routing may assume default_api_base without a configured
    # api_base. Ollama's port is unambiguous; vLLM's localhost:8000 is a
    # generic dev port, so routing there without opt-in would invent an
    # endpoint. The default base still counts for media readiness and UI hints.
    route_via_default_base: bool = True

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before sending to gateway
    strip_model_prefixes: tuple[str, ...] = ()  # strip only when the first model segment matches
    supports_max_completion_tokens: bool = False

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False

    # Direct providers skip API-key validation (user supplies everything)
    is_direct: bool = False

    # Provider is listed for shared credentials but cannot serve chat completions.
    is_transcription_only: bool = False

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    # How to inject the thinking on/off toggle into extra_body.
    # ""              - no extra_body needed (default)
    # "thinking_type" - {"thinking": {"type": "enabled"/"disabled"}}
    #                   (DeepSeek, VolcEngine, BytePlus)
    # "enable_thinking" - {"enable_thinking": true/false}  (DashScope)
    # "reasoning_split" - {"reasoning_split": true/false}  (MiniMax)
    thinking_style: str = ""

    # Gateway-native reasoning control to pair with model-level thinking styles.
    # "reasoning_effort" - {"reasoning": {"effort": <none|minimal|...>}}
    #                      (OpenRouter)
    gateway_reasoning_style: str = ""

    # When True, treat the "reasoning" response field as formal content
    # when "content" is empty.  Only set this for providers (e.g. StepFun)
    # whose API returns the actual answer in "reasoning" instead of "content".
    reasoning_as_content: bool = False

    # Map user-supplied reasoning_effort (OpenAI vocab: minimal/low/medium/high)
    # to the value this provider accepts on the wire. Set when the provider's
    # accepted set differs from OpenAI's. An empty mapped value omits the kwarg.
    # Mistral: only "high"/"none" - low/minimal map to "none", medium maps to "high".
    reasoning_effort_remap: tuple[tuple[str, str], ...] = ()

    # Models whose API rejects the reasoning_effort kwarg because reasoning is
    # implicit (Magistral always reasons; sending the kwarg returns HTTP 400).
    # Substring match against the wire model name (lowercased).
    implicit_reasoning_models: tuple[str, ...] = ()

    # When the model returns content as a list of {"type":"thinking",...} +
    # {"type":"text",...} blocks, extract the thinking text into
    # reasoning_content. Mistral's Magistral / reasoning-enabled responses use
    # this shape.
    extract_thinking_blocks: bool = False

    # Strip ``reasoning_content`` from assistant history messages before
    # sending. Mistral validates its request schema strictly and 400s on
    # any extra fields; other providers (DeepSeek) require this key on the
    # wire to keep thinking-mode history intact.
    strip_history_reasoning_content: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()

    def env_api_key(self) -> str:
        """API key found in the process environment for this provider.

        Checked when the config carries no key, so exporting OPENAI_API_KEY,
        KIMI_API_KEY, etc. is enough for the provider to work - the same
        behavior as Claude Code, OpenCode, or Cursor. Never persisted.
        """
        for var in (self.env_key, *self.env_key_aliases):
            if var and (value := os.environ.get(var, "").strip()):
                return value
        return ""

# ---------------------------------------------------------------------------
# PROVIDERS - the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Custom (direct OpenAI-compatible endpoint) ========================
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom OpenAI Compatible",
        backend="openai_compat",
        is_direct=True,
    ),
    ProviderSpec(
        name="custom_anthropic",
        keywords=("custom-anthropic", "custom_anthropic"),
        env_key="",
        display_name="Custom Anthropic Compatible",
        backend="anthropic",
        is_direct=True,
    ),

    # === Azure OpenAI (direct API calls with API version 2024-10-21) =====
    ProviderSpec(
        name="azure_openai",
        keywords=("azure", "azure-openai"),
        env_key="",
        display_name="Azure OpenAI",
        backend="azure_openai",
        is_direct=True,
    ),
    # === AWS Bedrock (native Converse API via bedrock-runtime) =============
    ProviderSpec(
        name="bedrock",
        keywords=(
            "bedrock",
            "anthropic.claude",
            "amazon.nova",
            "meta.",
            "mistral.",
            "cohere.",
            "qwen.",
            "deepseek.",
            "openai.gpt-oss",
            "ai21.",
            "moonshot.",
            "writer.",
            "zai.",
        ),
        env_key="AWS_BEARER_TOKEN_BEDROCK",
        display_name="AWS Bedrock",
        backend="bedrock",
        is_direct=True,
    ),
    # === Gateways (detected by api_key / api_base, not model name) =========
    # Gateways can route any model, so they win in fallback.
    # OpenRouter: global gateway, keys start with "sk-or-"
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
        gateway_reasoning_style="reasoning_effort",
    ),
    # Navin managed gateway (subscription): same OpenRouter wire protocol,
    # dedicated slot so BYOK OpenRouter keys never collide with the plan key.
    ProviderSpec(
        name="navin",
        keywords=("navin/", "navin"),
        env_key="",
        display_name="Navin",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
        gateway_reasoning_style="reasoning_effort",
    ),
    # OpenCode Zen: OpenAI-compatible chat-completions gateway for coding models.
    # models.dev/OpenCode use provider id "opencode" and model ids like
    # "opencode/<model>"; send the bare model upstream.
    ProviderSpec(
        name="opencode",
        keywords=("opencode/", "opencode", "opencode-zen", "opencode_zen"),
        env_key="OPENCODE_API_KEY",
        env_key_aliases=("OPENCODE_ZEN_API_KEY",),
        display_name="OpenCode Zen",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="opencode.ai/zen",
        default_api_base="https://opencode.ai/zen/v1",
        strip_model_prefixes=("opencode", "opencode_zen", "opencode-zen"),
    ),
    # Compatibility alias for configs that already used providers.opencodeZen.
    ProviderSpec(
        name="opencode_zen",
        keywords=("opencode/", "opencode_zen", "opencode-zen"),
        env_key="OPENCODE_API_KEY",
        display_name="OpenCode Zen",
        settings_alias_for="opencode",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="opencode.ai/zen",
        default_api_base="https://opencode.ai/zen/v1",
        strip_model_prefixes=("opencode", "opencode_zen", "opencode-zen"),
    ),
    # OpenCode Go: OpenAI-compatible chat-completions gateway for low-cost models.
    # OpenCode's own config uses "opencode-go/<model>"; send the bare model upstream.
    ProviderSpec(
        name="opencode_go",
        keywords=("opencode-go", "opencode_go"),
        env_key="OPENCODE_API_KEY",
        env_key_aliases=("OPENCODE_GO_API_KEY",),
        display_name="OpenCode Go",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="opencode.ai/zen/go",
        default_api_base="https://opencode.ai/zen/go/v1",
        strip_model_prefixes=("opencode-go", "opencode_go"),
    ),
    # Hugging Face Inference Providers: OpenAI-compatible router for chat models.
    ProviderSpec(
        name="huggingface",
        keywords=("huggingface", "hugging-face"),
        env_key="HF_TOKEN",
        env_key_aliases=("HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"),
        display_name="Hugging Face",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="hf_",
        detect_by_base_keyword="huggingface",
        default_api_base="https://router.huggingface.co/v1",
    ),
    # SiliconFlow (硅基流动): OpenAI-compatible gateway, model names keep org prefix
    ProviderSpec(
        name="siliconflow",
        keywords=("siliconflow",),
        env_key="SILICONFLOW_API_KEY",
        env_key_aliases=("SF_API_KEY",),
        display_name="SiliconFlow",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="siliconflow",
        default_api_base="https://api.siliconflow.cn/v1",
    ),

    # Novita AI: OpenAI-compatible gateway for hosted model APIs.
    ProviderSpec(
        name="novita",
        keywords=("novita",),
        env_key="NOVITA_API_KEY",
        display_name="Novita AI",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="novita",
        default_api_base="https://api.novita.ai/openai",
    ),

    # === Standard providers (matched by model-name keywords) ===============
    # Anthropic: native Anthropic SDK
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        default_api_base="https://api.anthropic.com",
        supports_prompt_caching=True,
    ),
    # OpenAI: SDK default base URL (no override needed)
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        supports_max_completion_tokens=True,
        builtin_models=(
            ProviderModelSpec(
                id="gpt-6-astra",
                label="GPT-6 Astra",
                description="Flagship model for long-horizon agentic work.",
                context_window=1050000,
            ),
        ),
    ),
    # OpenAI Codex: OAuth-based, dedicated provider
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex",),
        env_key="",
        display_name="OpenAI Codex",
        model_catalog="builtin",
        builtin_models=(
            ProviderModelSpec(
                id="openai-codex/gpt-6-astra",
                label="GPT-6-Astra",
                description="Flagship long-horizon agentic coding model.",
                context_window=1050000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-sol",
                label="GPT-5.6-Sol",
                description="Latest frontier agentic coding model.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-terra",
                label="GPT-5.6-Terra",
                description="Balanced agentic coding model for everyday work.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-luna",
                label="GPT-5.6-Luna",
                description="Fast and affordable agentic coding model.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.5",
                label="GPT-5.5",
                description="Frontier model for complex coding, research, and real-world work.",
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.4",
                label="GPT-5.4",
                description="Strong model for everyday coding.",
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.4-mini",
                label="GPT-5.4-Mini",
                description="Small, fast, and cost-efficient model for simpler coding tasks.",
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.3-codex-spark",
                label="GPT-5.3-Codex-Spark",
                description="Ultra-fast coding model.",
            ),
        ),
        backend="openai_codex",
        detect_by_base_keyword="codex",
        default_api_base="https://chatgpt.com/backend-api",
        is_oauth=True,
    ),
    # GitHub Copilot: OAuth-based
    ProviderSpec(
        name="github_copilot",
        keywords=("github_copilot", "copilot"),
        env_key="",
        display_name="Github Copilot",
        backend="github_copilot",
        default_api_base="https://api.githubcopilot.com",
        strip_model_prefix=True,
        is_oauth=True,
        supports_max_completion_tokens=True,
    ),
    # xAI developer API: billed per token, key from console.x.ai.
    ProviderSpec(
        name="xai",
        keywords=("xai", "x-ai", "grok"),
        env_key="XAI_API_KEY",
        display_name="xAI",
        backend="openai_compat",
        default_api_base="https://api.x.ai/v1",
        detect_by_base_keyword="api.x.ai",
        supports_max_completion_tokens=True,
        builtin_models=(
            ProviderModelSpec(
                id="xai/grok-4.6",
                label="Grok 4.6",
                description="Latest Grok model on the xAI developer API.",
                context_window=256000,
            ),
            ProviderModelSpec(
                id="xai/grok-4",
                label="Grok 4",
                description="Grok 4 on the xAI developer API.",
                context_window=256000,
            ),
            ProviderModelSpec(
                id="xai/grok-3",
                label="Grok 3",
                description="Grok 3 on the xAI developer API.",
            ),
        ),
    ),
    # SuperGrok / X Premium+ session (``grok login --device-auth``).
    ProviderSpec(
        name="xai_oauth",
        keywords=("xai-oauth", "xai_oauth"),
        env_key="",
        display_name="Grok (x.ai subscription)",
        model_catalog="builtin",
        builtin_models=(
            ProviderModelSpec(
                id="xai-oauth/grok-4.6",
                label="Grok 4.6",
                description="Latest Grok model via SuperGrok / X Premium+.",
                context_window=256000,
            ),
            ProviderModelSpec(
                id="xai-oauth/grok-4",
                label="Grok 4",
                description="Grok 4 via SuperGrok / X Premium+.",
                context_window=256000,
            ),
            ProviderModelSpec(
                id="xai-oauth/grok-3",
                label="Grok 3",
                description="Grok 3 via SuperGrok / X Premium+.",
            ),
        ),
        backend="xai_oauth",
        detect_by_base_keyword="cli-chat-proxy.grok.com",
        default_api_base="https://cli-chat-proxy.grok.com/v1",
        strip_model_prefix=True,
        is_oauth=True,
        supports_max_completion_tokens=True,
    ),
    # Qwen / Alibaba Model Studio (DashScope). Region + plan pick the host.
    ProviderSpec(
        name="qwen",
        keywords=("qwen", "dashscope", "alibaba"),
        env_key="DASHSCOPE_API_KEY",
        env_key_aliases=("QWEN_API_KEY", "ALIBABA_API_KEY"),
        display_name="Qwen (Alibaba)",
        backend="openai_compat",
        detect_by_base_keyword="dashscope",
        default_api_base="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        thinking_style="enable_thinking",
    ),
    ProviderSpec(
        name="dashscope",
        keywords=("dashscope",),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        detect_by_base_keyword="dashscope.aliyuncs",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        thinking_style="enable_thinking",
        settings_alias_for="qwen",
    ),
    # DeepSeek: OpenAI-compatible at api.deepseek.com
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        backend="openai_compat",
        default_api_base="https://api.deepseek.com",
        thinking_style="thinking_type",
    ),
    # Gemini: Google's OpenAI-compatible endpoint
    ProviderSpec(
        name="gemini",
        keywords=("gemini", "gemma"),
        env_key="GEMINI_API_KEY",
        env_key_aliases=("GOOGLE_API_KEY",),
        display_name="Gemini",
        backend="openai_compat",
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    # Z.AI: Zhipu's international endpoint. Same API, different host and keys -
    # a z.ai key sent to open.bigmodel.cn is rejected, so the two cannot share
    # one entry. Listed first because z.ai is the current brand for GLM outside
    # mainland China; a config that only fills providers.zhipu still matches it,
    # since keyword matching skips providers without a key.
    ProviderSpec(
        name="zai",
        keywords=("z.ai", "zai", "glm"),
        env_key="ZAI_API_KEY",
        env_key_aliases=("GLM_API_KEY", "Z_AI_API_KEY"),
        display_name="Z.AI",
        backend="openai_compat",
        detect_by_base_keyword="z.ai",
        default_api_base="https://api.z.ai/api/paas/v4",
        thinking_style="thinking_type",
    ),
    # Zhipu (智谱): OpenAI-compatible at open.bigmodel.cn
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZHIPUAI_API_KEY",
        env_key_aliases=("ZHIPU_API_KEY",),
        display_name="Zhipu AI",
        backend="openai_compat",
        detect_by_base_keyword="bigmodel",
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
        thinking_style="thinking_type",
    ),
    # Moonshot (月之暗面): Kimi K2.5+ enforce temperature >= 1.0.
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        env_key_aliases=("KIMI_API_KEY",),
        display_name="Moonshot",
        backend="openai_compat",
        default_api_base="https://api.moonshot.ai/v1",
        model_overrides=(
            ("kimi-k2.5", {"temperature": 1.0}),
            ("kimi-k2.6", {"temperature": 1.0}),
            ("kimi-k2.7", {"temperature": 1.0}),
            ("kimi-k2.7-code", {"temperature": 1.0}),
            ("kimi-k2.7-code-highspeed", {"temperature": 1.0}),
        ),
    ),
    # Kimi Coding Plan - Anthropic Messages API at api.kimi.com/coding
    # sk-kimi-* keys; requires User-Agent: claude-code/0.1.0 header.
    ProviderSpec(
        name="kimi_coding",
        keywords=("kimi-coding", "kimi_coding", "kimi-for-coding"),
        env_key="KIMI_CODING_API_KEY",
        display_name="Kimi Coding",
        backend="anthropic",
        default_api_base="https://api.kimi.com/coding/v1",
        default_extra_headers=(("User-Agent", "claude-code/0.1.0"),),
    ),
    # MiniMax: OpenAI-compatible API
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        backend="openai_compat",
        default_api_base="https://api.minimax.io/v1",
        thinking_style="reasoning_split",
        # The M family (M3, M4, ...; catalog slug minimax/minimax-m3) always
        # reasons and rejects any effort level, so Settings offers Auto only.
        # Same shape as Magistral below.
        implicit_reasoning_models=("minimax-m", "minimax/minimax-m"),
    ),
    ProviderSpec(
        name="volcengine",
        keywords=("volcengine", "doubao", "ark", "byteplus"),
        env_key="VOLCENGINE_API_KEY",
        env_key_aliases=("ARK_API_KEY", "BYTEPLUS_API_KEY"),
        display_name="Doubao (Volcengine Ark)",
        backend="openai_compat",
        detect_by_base_keyword="volces.com",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
        thinking_style="thinking_type",
    ),
    ProviderSpec(
        name="hunyuan",
        keywords=("hunyuan", "tencent"),
        env_key="HUNYUAN_API_KEY",
        env_key_aliases=("TENCENT_HUNYUAN_API_KEY",),
        display_name="Tencent Hunyuan",
        backend="openai_compat",
        detect_by_base_keyword="hunyuan",
        default_api_base="https://api.hunyuan.cloud.tencent.com/v1",
    ),
    ProviderSpec(
        name="qianfan",
        keywords=("qianfan", "ernie", "baidu"),
        env_key="QIANFAN_API_KEY",
        env_key_aliases=("ERNIE_API_KEY", "BAIDU_API_KEY"),
        display_name="Baidu Qianfan",
        backend="openai_compat",
        detect_by_base_keyword="qianfan",
        default_api_base="https://qianfan.baidubce.com/v2",
    ),
    ProviderSpec(
        name="stepfun",
        keywords=("stepfun", "step-"),
        env_key="STEPFUN_API_KEY",
        display_name="StepFun",
        backend="openai_compat",
        detect_by_base_keyword="stepfun",
        default_api_base="https://api.stepfun.com/v1",
    ),
    # Mistral AI: OpenAI-compatible API.
    # Reasoning quirks:
    #   * mistral-medium-3-5 / mistral-vibe-cli-* accept reasoning_effort but
    #     only "high" or "none" - low/medium/minimal must be remapped.
    #   * Magistral-* models reason implicitly and reject the kwarg entirely.
    #   * Reasoning responses return content as a list of thinking + text
    #     blocks; thinking text gets extracted into reasoning_content.
    ProviderSpec(
        name="mistral",
        keywords=("mistral", "magistral", "ministral", "codestral", "devstral"),
        env_key="MISTRAL_API_KEY",
        display_name="Mistral",
        backend="openai_compat",
        default_api_base="https://api.mistral.ai/v1",
        reasoning_effort_remap=(
            ("minimal", "none"),
            ("low", "none"),
            ("medium", "high"),
            ("high", "high"),
            ("none", "none"),
        ),
        implicit_reasoning_models=("magistral",),
        extract_thinking_blocks=True,
        strip_history_reasoning_content=True,
    ),
    # === Local deployment (matched by config key, NOT by api_base) =========
    # vLLM / any OpenAI-compatible local server
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        env_key_aliases=("VLLM_API_KEY",),
        display_name="vLLM",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="8000",
        default_api_base="http://localhost:8000/v1",
        route_via_default_base=False,
    ),
    # Ollama (local, OpenAI-compatible).
    # Do not list "nemotron" here: that keyword belongs to NVIDIA NIM. Local
    # Nemotron weights on Ollama are selected via ``ollama/<model>`` or the
    # configured local-provider fallback once ``api_base`` is set.
    ProviderSpec(
        name="ollama",
        keywords=("ollama",),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="11434",
        default_api_base="http://localhost:11434/v1",
    ),
    # LM Studio (local, OpenAI-compatible)
    ProviderSpec(
        name="lm_studio",
        keywords=("lm-studio", "lmstudio", "lm_studio"),
        env_key="LM_STUDIO_API_KEY",
        env_key_aliases=("LM_API_KEY", "LMSTUDIO_API_KEY"),
        display_name="LM Studio",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="1234",
        default_api_base="http://localhost:1234/v1",
    ),
    # Atomic Chat (local, OpenAI-compatible) - https://atomic.chat/
    ProviderSpec(
        name="atomic_chat",
        keywords=("atomic-chat", "atomic_chat", "atomicchat"),
        env_key="ATOMIC_CHAT_API_KEY",
        display_name="Atomic Chat",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="1337",
        default_api_base="http://localhost:1337/v1",
    ),
    # OmniRoute (local free AI gateway, OpenAI-compatible) - https://omniroute.online
    # One local server in front of 350+ upstream providers. A fresh install
    # answers chat without any key (REQUIRE_API_KEY is off by default) and the
    # model id ``auto`` builds a virtual combo from the connected providers;
    # ``auto/coding``, ``auto/fast``, ``auto/cheap`` weight that choice. Only
    # ``GET /v1/models`` insists on a dashboard key: Settings falls back to the
    # public auto-combo candidates route (see navin/providers/omniroute.py).
    # Upstream ids keep their own prefix (``openai/gpt-5.4``, ``cc/claude-...``)
    # because OmniRoute routes on it, so only our ``omniroute/`` routing prefix
    # is stripped before the request goes out.
    ProviderSpec(
        name="omniroute",
        keywords=("omniroute",),
        env_key="OMNIROUTE_API_KEY",
        display_name="OmniRoute",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="20128",
        default_api_base="http://localhost:20128/v1",
        strip_model_prefixes=("omniroute",),
    ),
    # === OpenVINO Model Server (direct, local, OpenAI-compatible at /v3) ===
    ProviderSpec(
        name="ovms",
        keywords=("openvino", "ovms"),
        env_key="",
        display_name="OpenVINO Model Server",
        backend="openai_compat",
        is_direct=True,
        is_local=True,
        default_api_base="http://localhost:8000/v3",
    ),
    # === NVIDIA NIM (NVIDIA Inference Microservices) =======================
    # Keys start with "nvapi-", base URL at integrate.api.nvidia.com
    ProviderSpec(
        name="nvidia",
        keywords=("nvidia", "nemotron", "nvapi"),
        env_key="NVIDIA_NIM_API_KEY",
        env_key_aliases=("NVIDIA_API_KEY",),
        display_name="NVIDIA NIM",
        backend="openai_compat",
        is_gateway=False,
        detect_by_key_prefix="nvapi-",
        detect_by_base_keyword="nvidia.com",
        default_api_base="https://integrate.api.nvidia.com/v1",
    ),
    # === Auxiliary (not a primary LLM provider) ============================
    # Groq: mainly used for Whisper voice transcription, also usable for LLM
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        backend="openai_compat",
        default_api_base="https://api.groq.com/openai/v1",
    ),
    # AssemblyAI: voice transcription only. It appears in provider settings so
    # users can manage credentials, but WebUI excludes it from chat model pickers.
    ProviderSpec(
        name="assemblyai",
        keywords=("assemblyai",),
        env_key="ASSEMBLYAI_API_KEY",
        display_name="AssemblyAI",
        backend="openai_compat",
        default_api_base="https://api.assemblyai.com/v2",
        is_transcription_only=True,
    ),
)
if not live_modules_available():
    PROVIDERS = tuple(spec for spec in PROVIDERS if spec.name != "navin")

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _compact(name: str) -> str:
    """Lowercase letters and digits only: one key for every spelling of a name.

    ``MiniMax``, ``minimax``, ``mini_max``, ``Kimi Coding``, ``kimi-coding``
    and ``GitHub Copilot`` all have to reach the same spec: the Settings form
    sends display names, config files send snake_case keys, and ``to_snake``
    turns ``MiniMax`` into ``mini_max``, which matched nothing.
    """
    return "".join(ch for ch in name.lower() if ch.isalnum())


_SPEC_BY_COMPACT: dict[str, ProviderSpec] = {}


def _spec_index() -> dict[str, ProviderSpec]:
    if not _SPEC_BY_COMPACT:
        # Canonical specs before their settings aliases (opencode_zen is an
        # alias of opencode and shares its display name), config keys before
        # display names within each group: a fuzzy spelling lands on the spec
        # the exact key would have picked.
        canonical = [spec for spec in PROVIDERS if not spec.settings_alias_for]
        aliases = [spec for spec in PROVIDERS if spec.settings_alias_for]
        for group in (canonical, aliases):
            for spec in group:
                _SPEC_BY_COMPACT.setdefault(_compact(spec.name), spec)
            for spec in group:
                _SPEC_BY_COMPACT.setdefault(_compact(spec.display_name), spec)
    return _SPEC_BY_COMPACT


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config key or display name, in any spelling.

    ``"dashscope"``, ``"MiniMax"``, ``"mini-max"``, ``"Kimi Coding"`` and
    ``"GITHUB_COPILOT"`` all resolve. Custom providers are not in the
    registry; callers fall back to :func:`create_dynamic_spec` for them.
    """
    if not name:
        return None
    normalized = to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return _spec_index().get(_compact(name))


def find_by_model(model: str) -> ProviderSpec | None:
    """The registry spec whose model keywords name ``model``, if any.

    A custom OpenAI-compatible gateway can serve ``magistral-medium`` or
    ``MiniMax-M3``; the model's reasoning rules come from the vendor that made
    it, not from the gateway. Keywords are matched the way config routing
    matches them (substring, hyphen and underscore interchangeable), in
    registry order. Transcription-only providers never own a chat model.
    """
    model_lower = (model or "").lower()
    if not model_lower:
        return None
    model_normalized = model_lower.replace("-", "_")
    for spec in PROVIDERS:
        if spec.is_transcription_only:
            continue
        for keyword in spec.keywords:
            kw = keyword.lower()
            if kw in model_lower or kw.replace("-", "_") in model_normalized:
                return spec
    return None

def create_dynamic_spec(name: str, *, thinking_style: str = "") -> ProviderSpec:
    """Create a dynamic ProviderSpec for custom user-defined providers."""
    normalized = to_snake(name.replace("-", "_"))
    strip_prefixes = tuple(dict.fromkeys((name, normalized)))
    return ProviderSpec(
        name=normalized,
        keywords=(),
        env_key="",
        display_name=name.title(),
        backend="openai_compat",
        is_direct=True,
        strip_model_prefixes=strip_prefixes,
        thinking_style=thinking_style,
    )
