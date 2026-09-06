"""Configuration schema using Pydantic."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from navin.config_base import Base
from navin.cron.types import CronSchedule

if TYPE_CHECKING:
    from navin.agent.approval import ApprovalConfig
    from navin.agent.tools.browser import BrowserToolConfig
    from navin.agent.tools.cli_apps import CliAppsToolConfig
    from navin.agent.tools.code_index import SemanticSearchConfig
    from navin.agent.tools.database import DatabaseToolConfig
    from navin.agent.tools.filesystem import FileToolsConfig
    from navin.agent.tools.image_generation import ImageGenerationToolConfig
    from navin.agent.tools.leads import LeadsToolConfig
    from navin.agent.tools.music_generation import MusicGenerationToolConfig
    from navin.agent.tools.scrape import ScrapeToolConfig
    from navin.agent.tools.self import MyToolConfig
    from navin.agent.tools.seo import SeoToolConfig
    from navin.agent.tools.shell import ExecToolConfig
    from navin.agent.tools.speech_generation import SpeechGenerationToolConfig
    from navin.agent.tools.video_generation import VideoGenerationToolConfig
    from navin.agent.tools.visual_qa import VisualQAToolConfig
    from navin.agent.tools.web import WebToolsConfig


class ChannelsConfig(Base):
    """Configuration for chat channels.

    Built-in and plugin channel configs are stored as extra fields (dicts).
    Each channel parses its own config in __init__.
    Per-channel "streaming": true enables streaming output (requires send_delta impl).
    """

    model_config = ConfigDict(extra="allow")

    send_progress: bool = True  # stream agent's text progress to the channel
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    show_reasoning: bool = True  # surface model reasoning when channel implements it
    extract_document_text: bool = True  # extract text from document attachments before sending to the model
    send_max_retries: int = Field(default=3, ge=0, le=10)  # Max delivery attempts (initial send included)
    transcription_provider: str = "groq"  # Deprecated: use top-level transcription.provider
    transcription_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")  # Deprecated: use top-level transcription.language


class TranscriptionConfig(Base):
    """Cross-channel audio transcription configuration."""

    enabled: bool = True
    # Vide = aucun choix. Les abonnés reçoivent "navin" écrit explicitement par
    # la synchro du catalogue ; sans abonnement, ne rien présélectionner plutôt
    # que d'afficher une offre à laquelle l'utilisateur n'a pas souscrit.
    provider: str | None = ""  # Validated by navin.audio.transcription_registry.
    model: str | None = "nvidia/parakeet-tdt-0.6b-v3"
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    max_duration_sec: int = Field(default=120, ge=1, le=600)
    max_upload_mb: int = Field(default=25, ge=1, le=100)


class LiveKitConfig(Base):
    """LiveKit Cloud or self-hosted SFU for Team audio / video / screen share.

    Standard LiveKit env vars (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    are also accepted as fallbacks. Secrets never go to the WebUI.
    """

    url: str = ""
    api_key: str = ""
    api_secret: str = ""


class VoiceConfig(Base):
    """Realtime voice session + TTS settings.

    STT uses top-level ``transcription``. TTS / auto-speak / plan gate live here.
    Realtime duplex voice is gated to Pro+ / Team unless ``realtime_enabled``
    is set explicitly (BYOK / local override).
    """

    # Vide = aucun choix, comme ``TranscriptionConfig.provider``.
    tts_provider: str | None = ""  # Validated by navin.audio.tts_registry.
    tts_model: str | None = "x-ai/grok-voice-tts-1.0"
    voice: str = "eve"
    auto_speak: bool = False
    response_format: str = "mp3"  # mp3 or wav
    # None = follow license plan (Pro+/Team). True/False force enable/disable.
    realtime_enabled: bool | None = None


class RecurrenceConfig(Base):
    """A clock-anchored recurrence for a scheduled loop.

    Mirrors ``navin.cron.recurrence.Recurrence``, which owns the rules about
    which intervals divide their unit evenly and which days every month has.
    """

    kind: Literal["minutes", "hourly", "every_hours", "daily", "weekly", "monthly"] = "daily"
    interval: int = Field(default=1, ge=1, le=59)  # Minutes or hours, per kind
    minute: int = Field(default=0, ge=0, le=59)
    hour: int = Field(default=0, ge=0, le=23)
    weekday: int = Field(default=1, ge=1, le=7)  # 1=Monday through 7=Sunday
    day: int | Literal["last"] = 1  # Day of the month, or its last day

    def to_recurrence(self, timezone: str | None = None) -> Any:
        """Return the runtime recurrence, raising on an unusable combination."""
        from navin.cron.recurrence import Recurrence

        return Recurrence(
            kind=self.kind,
            interval=self.interval,
            minute=self.minute,
            hour=self.hour,
            weekday=self.weekday,
            day=self.day,
            tz=timezone,
        )

    def build_schedule(self, timezone: str | None = None) -> CronSchedule:
        return self.to_recurrence(timezone).to_schedule()

    @model_validator(mode="after")
    def recurrence_must_be_runnable(self) -> "RecurrenceConfig":
        from navin.cron.recurrence import RecurrenceError

        try:
            self.to_recurrence()
        except RecurrenceError as exc:
            raise ValueError(str(exc)) from None
        return self


class LoopGuardrailsConfig(Base):
    """What happens to a scheduled loop that keeps failing.

    A loop runs with nobody watching, so retrying a broken one on its normal
    cadence spends tokens on every tick for as long as the fault lasts.
    """

    max_consecutive_failures: int = Field(
        default=5,
        ge=0,
        le=100,
        validation_alias=AliasChoices("maxConsecutiveFailures", "max_consecutive_failures"),
        serialization_alias="maxConsecutiveFailures",
    )  # Pause the loop after this many failures in a row (0 = never pause)
    retry_backoff_minutes: int = Field(
        default=1,
        ge=0,
        le=1440,
        validation_alias=AliasChoices("retryBackoffMinutes", "retry_backoff_minutes"),
        serialization_alias="retryBackoffMinutes",
    )  # First retry delay, doubling with each further failure (0 = no backoff)
    retry_backoff_cap_hours: int = Field(
        default=6,
        ge=0,
        le=168,
        validation_alias=AliasChoices("retryBackoffCapHours", "retry_backoff_cap_hours"),
        serialization_alias="retryBackoffCapHours",
    )  # Longest the doubling delay may grow to

    @property
    def backoff_base_ms(self) -> int:
        return self.retry_backoff_minutes * 60_000

    @property
    def backoff_cap_ms(self) -> int:
        return self.retry_backoff_cap_hours * 3_600_000


class DreamConfig(Base):
    """Dream memory consolidation configuration."""

    _HOUR_MS = 3_600_000

    enabled: bool = True  # Register the periodic Dream consolidation job on startup
    interval_h: int = Field(default=2, ge=1)  # Every 2 hours by default
    schedule: RecurrenceConfig | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )  # Clock-anchored recurrence; wins over interval_h when set
    daily_token_budget: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("dailyTokenBudget", "daily_token_budget"),
        serialization_alias="dailyTokenBudget",
    )  # Skip runs once the day's spend reaches this (0 = no ceiling)
    max_consecutive_failures: int = Field(
        default=0,
        ge=0,
        le=100,
        validation_alias=AliasChoices("maxConsecutiveFailures", "max_consecutive_failures"),
        serialization_alias="maxConsecutiveFailures",
    )  # Pause after this many failures in a row (0 = use the global setting)
    cron: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )  # Legacy cron expression override
    model_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelOverride", "model", "model_override"),
    )  # Model preset name or model slug Dream consolidation runs on (empty = session default)
    max_batch_size: int = Field(default=20, ge=1)  # Deprecated: no longer used
    max_iterations: int = Field(default=15, ge=1)  # Deprecated: no longer used
    annotate_line_ages: bool = True  # Deprecated: no longer used

    def build_schedule(self, timezone: str) -> CronSchedule:
        """Build the runtime schedule, most specific setting first."""
        if self.schedule is not None:
            return self.schedule.build_schedule(timezone)
        if self.cron:
            return CronSchedule(kind="cron", expr=self.cron, tz=timezone)
        return CronSchedule(kind="every", every_ms=self.interval_h * self._HOUR_MS)

    def describe_schedule(self) -> str:
        """Return a human-readable summary for logs and startup output."""
        if self.schedule is not None:
            return self.schedule.build_schedule(None).expr or ""
        if self.cron:
            return f"cron {self.cron} (legacy)"
        hours = self.interval_h
        return f"every {hours}h"


class InlineFallbackConfig(Base):
    """One inline fallback model configuration."""

    model: str
    provider: str
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


FallbackCandidate = str | InlineFallbackConfig


class ModelPresetConfig(Base):
    """A named set of model + generation parameters for quick switching."""

    label: str | None = None
    model: str
    provider: str = "auto"
    max_tokens: int = 8192
    context_window_tokens: int = 200_000
    temperature: float = 0.1
    reasoning_effort: str | None = None
    """When False, the preset stays in Settings but is hidden from the chat picker."""
    enabled: bool = True
    # text = chat ; image/video/audio/music/stt = outils média (Settings).
    modality: Literal["text", "image", "video", "audio", "music", "stt"] = "text"
    unit_price_usd: float | None = None
    price_note: str | None = None
    billing_unit: str | None = None
    billing_rate: float | None = None
    estimated_generation_cost: float | None = None
    default_profile: str | None = None
    # Set when the operator edits a catalog / imported row in Settings.
    # Catalog sync must not put the old slug back.
    user_edited: bool = Field(
        default=False,
        validation_alias=AliasChoices("userEdited", "user_edited"),
        serialization_alias="userEdited",
    )

    def to_generation_settings(self) -> Any:
        from navin.providers.base import GenerationSettings
        return GenerationSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
        )


DEFAULT_CLEARING_EXCLUDE_TOOLS: tuple[str, ...] = (
    # Each of these returns the only record of something that already happened, so
    # blanking it strands the model: it cannot re-run the tool to recover the output
    # without repeating a side effect (a second subagent run, a re-sent message, a
    # duplicate cron job) or paying again for a generated artifact whose path is in
    # the result. Everything else, including every MCP tool, is fair game.
    "apply_patch",
    "create_goal",
    "cron",
    "edit_file",
    "generate_image",
    "generate_video",
    "message",
    "spawn",
    "write_file",
)


class ToolResultClearing(Base):
    """Which already-completed tool results may be blanked to fit the context.

    A result is blanked, not deleted: the call stays in the transcript with a note
    saying it completed, so the model knows the work was done and can re-run the
    tool if it needs the output again. That is only true for tools worth
    re-running, which is what ``excludeTools`` protects.
    """

    exclude_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CLEARING_EXCLUDE_TOOLS),
        validation_alias=AliasChoices("excludeTools"),
        serialization_alias="excludeTools",
    )  # Tool names whose results are never blanked, because re-running them cannot recover the output
    keep_recent: int = Field(
        default=12,
        ge=0,
        validation_alias=AliasChoices("keepRecent"),
        serialization_alias="keepRecent",
    )  # Most recent results to blank last; a hard overflow still reaches them
    min_chars: int = Field(
        default=500,
        ge=0,
        validation_alias=AliasChoices("minChars"),
        serialization_alias="minChars",
    )  # Results shorter than this are left alone; blanking them buys nothing
    clear_at_least: int = Field(
        default=2_000,
        ge=0,
        validation_alias=AliasChoices("clearAtLeast"),
        serialization_alias="clearAtLeast",
    )  # Free at least this many tokens once clearing starts (0 = stop at the target); raising it trades context for fewer prompt-cache breaks
    soft_clear_ratio: float = Field(
        default=0.22,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("softClearRatio"),
        serialization_alias="softClearRatio",
    )  # Start blanking stale tool results once prompt exceeds this share of the input budget (0 = only on hard overflow)
    stale_after_user_turns: int = Field(
        default=1,
        ge=0,
        validation_alias=AliasChoices("staleAfterUserTurns"),
        serialization_alias="staleAfterUserTurns",
    )  # Blank bulky tool results older than this many user turns on every request, regardless of pressure (0 = disabled). Old dumps are the main source of dead replayed input; the model can re-run the tool.


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/NavinProjects"
    model_preset: str | None = None  # Active preset name - takes precedence over fields below
    # True when the user explicitly picked the chat default (composer picker /
    # Settings). Catalog syncs then leave the default alone instead of
    # rewriting it to the managed catalog's default on every refresh.
    model_preset_user_pinned: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "modelPresetUserPinned", "model_preset_user_pinned"
        ),
        serialization_alias="modelPresetUserPinned",
    )
    # No default model on purpose: any name we picked would belong to one vendor,
    # and provider auto-detection would then pair it with whatever key exists
    # (a Claude name sent to a Mistral key). An empty model means "not chosen
    # yet", which the WebUI and CLI both report as setup still to do.
    model: str = ""
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    max_tokens: int = 8192
    context_window_tokens: int = 200_000
    context_block_limit: int | None = None
    temperature: float = 0.1
    fallback_models: list[FallbackCandidate] = Field(default_factory=list)
    max_tool_iterations: int = 200
    # Keep in sync with ``_MAX_INJECTIONS_PER_TURN`` in the runner: a higher
    # spawn limit than the parent can drain per wave lets completions pile up
    # unread. Worktree checkouts for ``isolate=true`` are pooled up to this
    # size so parallel subagents do not mean as many cold ``git worktree add``
    # each turn.
    #
    # This is the ceiling every plan entitlement is capped against, so it has
    # to match the most generous plan (200) or the higher tiers would be
    # silently clamped down to it. An install with no license stays here.
    #
    # Subagents are asyncio tasks in this one process: the wall at this width
    # is the model provider's rate limit and the blocking pool, not the host.
    max_concurrent_subagents: int = Field(default=200, ge=1, le=200)
    # Soft by default: a failed tool call goes back to the model as an error
    # result so the run can self-correct (bounded by the identical-failure
    # escalation in the runner). Security boundaries (SSRF, workspace) keep
    # their own non-bypassable handling regardless of this flag.
    fail_on_tool_error: bool = False
    max_tool_result_chars: int = 16_000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    tool_hint_max_length: int = Field(
        default=120,
        ge=20,
        le=500,
        validation_alias=AliasChoices("toolHintMaxLength"),
        serialization_alias="toolHintMaxLength",
    )  # Max characters for tool hint display (e.g. "$ cd …/project && npm test")
    reasoning_effort: str | None = None  # low / medium / high / adaptive / none - LLM thinking effort; None preserves the provider default
    timezone: str = "UTC"  # IANA timezone, e.g. "Asia/Shanghai", "America/New_York"
    bot_name: str = "navin"  # Display name shown in CLI prompts (e.g. "{name} is thinking...")
    bot_icon: str = ""  # Short icon (emoji or text) shown next to the bot name in CLI; "" to omit
    unified_session: bool = False  # Share one session across all channels (single-user multi-device)
    disabled_skills: list[str] = Field(default_factory=list)  # Skill names to exclude from loading (e.g. ["summarize", "skill-creator"])
    session_ttl_minutes: int = Field(
        default=15,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes", "sessionTtlMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )  # Auto-compact idle threshold in minutes (0 = disabled)
    consolidation_ratio: float = Field(
        default=0.5,
        ge=0.1,
        le=0.95,
        validation_alias=AliasChoices("consolidationRatio"),
        serialization_alias="consolidationRatio",
    )  # Consolidation target ratio (0.5 = 50% of budget retained after compression)
    tool_result_clearing: ToolResultClearing = Field(
        default_factory=ToolResultClearing,
        validation_alias=AliasChoices("toolResultClearing"),
        serialization_alias="toolResultClearing",
    )
    dream: DreamConfig = Field(default_factory=DreamConfig)


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str | None = Field(default=None, repr=False)
    # Key obtained through a provider OAuth connect flow (e.g. OpenRouter PKCE
    # on the Free path). Such a key belongs to the signed-in person's session:
    # account disconnect clears it so the next user of this machine starts
    # clean. A key typed manually (BYOK) keeps this False and is never touched.
    oauth_key: bool = False
    api_base: str | None = None
    api_type: Literal["auto", "chat_completions", "responses"] = "auto"  # Request API surface
    # Explicit auth mode for local / OpenAI-compatible private providers.
    # None means legacy derivation: bearer when api_key is set, else none.
    auth_mode: Literal["none", "bearer"] | None = Field(
        default=None,
        validation_alias=AliasChoices("authMode", "auth_mode"),
    )
    extra_headers: dict[str, str] | None = None  # Custom headers for provider gateways
    extra_body: dict[str, Any] | None = None  # Extra provider request fields; shape depends on provider/API surface
    extra_query: dict[str, str] | None = None  # Extra query params (e.g. api-version for Azure-style gateways)
    proxy: str | None = None  # OpenAI-compatible/Codex HTTP proxy URL
    thinking_style: str | None = None  # Thinking/reasoning style for custom providers
    # Native connection profile (Chinese vendors and dual-host APIs).
    # Distinct from BedrockProviderConfig.region, which is an AWS region.
    endpoint_region: Literal["china", "international", "singapore", "us"] | None = None
    access_plan: Literal["payg", "token", "coding"] | None = None
    wire_protocol: Literal["openai", "anthropic", "native"] | None = None

    # Valid values mirror the keys of _THINKING_STYLE_MAP in
    # navin/providers/openai_compat_provider.py. Kept duplicated here to
    # avoid an import cycle (schema.py must not import from providers/).
    _VALID_THINKING_STYLES: ClassVar[tuple[str, ...]] = (
        "thinking_type",
        "enable_thinking",
        "reasoning_split",
    )

    @field_validator("thinking_style")
    @classmethod
    def _validate_thinking_style(cls, v: str | None) -> str | None:
        if not v:  # None or "" -> no injection, valid (backwards compatible)
            return v
        if v not in cls._VALID_THINKING_STYLES:
            raise ValueError(
                f"Invalid thinking_style {v!r}. "
                f"Must be one of: {', '.join(repr(s) for s in cls._VALID_THINKING_STYLES)} "
                f"(or empty/omitted)."
            )
        return v

    @field_validator("auth_mode")
    @classmethod
    def _validate_auth_mode(cls, v: str | None) -> str | None:
        if not v:
            return None
        if v not in ("none", "bearer"):
            raise ValueError("auth_mode must be none, bearer, or empty/omitted")
        return v


# Local / private OpenAI-compatible presets that expose Auth None|Bearer in Settings.
PROVIDER_AUTH_MODE_NAMES: frozenset[str] = frozenset(
    {"ollama", "vllm", "lm_studio", "omniroute", "custom", "custom_anthropic"}
)


def provider_supports_auth_mode(name: str | None) -> bool:
    """Return True when the Settings UI should show Auth None / Bearer."""
    if not name:
        return False
    return name.replace("-", "_") in PROVIDER_AUTH_MODE_NAMES


def resolve_provider_auth_mode(provider_config: ProviderConfig | None) -> Literal["none", "bearer"]:
    """Resolve the effective auth mode for a provider config.

    Explicit ``auth_mode`` wins. Legacy configs without the field derive
    ``bearer`` when an API key is present, otherwise ``none``.
    """
    if provider_config is None:
        return "none"
    explicit = getattr(provider_config, "auth_mode", None)
    if explicit in ("none", "bearer"):
        return explicit
    key = str(getattr(provider_config, "api_key", None) or "").strip()
    return "bearer" if key else "none"


class BedrockProviderConfig(ProviderConfig):
    """AWS Bedrock Runtime provider configuration."""

    region: str | None = None  # AWS region, falls back to AWS_REGION/AWS_DEFAULT_REGION/profile
    profile: str | None = None  # Optional AWS shared config profile


class ProvidersConfig(Base):
    """Configuration for LLM providers.

    Supports custom providers via extra fields - any additional field
    becomes an OpenAI-compatible custom provider.
    """

    model_config = ConfigDict(extra="allow")

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    custom_anthropic: ProviderConfig = Field(default_factory=ProviderConfig)  # Any Anthropic-compatible endpoint
    qwen: ProviderConfig = Field(default_factory=ProviderConfig)  # Alibaba Model Studio / DashScope
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # Legacy alias of qwen
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # Doubao / Ark
    hunyuan: ProviderConfig = Field(default_factory=ProviderConfig)  # Tencent Hunyuan
    qianfan: ProviderConfig = Field(default_factory=ProviderConfig)  # Baidu Qianfan / ERNIE
    stepfun: ProviderConfig = Field(default_factory=ProviderConfig)  # StepFun
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)  # Azure OpenAI (model = deployment name)
    bedrock: BedrockProviderConfig = Field(default_factory=BedrockProviderConfig)  # AWS Bedrock Converse
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    navin: ProviderConfig = Field(default_factory=ProviderConfig)  # Managed plan key (OpenRouter wire)
    assemblyai: ProviderConfig = Field(default_factory=ProviderConfig)  # AssemblyAI voice transcription
    huggingface: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zai: ProviderConfig = Field(default_factory=ProviderConfig)  # Z.AI (GLM, international)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)  # Zhipu (GLM, mainland China)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama local models
    lm_studio: ProviderConfig = Field(default_factory=ProviderConfig)  # LM Studio local models
    atomic_chat: ProviderConfig = Field(default_factory=ProviderConfig)  # Atomic Chat local models
    omniroute: ProviderConfig = Field(default_factory=ProviderConfig)  # OmniRoute local free AI gateway
    ovms: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenVINO Model Server (OVMS)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    kimi_coding: ProviderConfig = Field(default_factory=ProviderConfig)  # Kimi Coding Plan (Anthropic Messages API)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    mistral: ProviderConfig = Field(default_factory=ProviderConfig)
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow (硅基流动)
    novita: ProviderConfig = Field(default_factory=ProviderConfig)  # Novita AI
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # Github Copilot (OAuth)
    xai: ProviderConfig = Field(default_factory=ProviderConfig)  # xAI developer API (XAI_API_KEY)
    xai_oauth: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # Grok SuperGrok / X Premium+ (OAuth)
    nvidia: ProviderConfig = Field(default_factory=ProviderConfig)  # NVIDIA NIM (nvapi- keys)
    opencode: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenCode Zen (canonical provider id)
    opencode_zen: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenCode Zen (curated coding models)
    opencode_go: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenCode Go (low-cost coding models)

    @model_validator(mode="after")
    def convert_extra_providers(self):
        """Convert extra fields (custom providers) to ProviderConfig objects."""
        if self.model_extra:
            from navin.providers.registry import find_by_name

            for key, value in self.model_extra.items():
                if spec := find_by_name(key):
                    raise ValueError(
                        f"providers.{key} conflicts with built-in provider {spec.name!r}; "
                        "use the built-in provider key or choose a different custom provider name"
                    )
                if isinstance(value, dict):
                    self.model_extra[key] = ProviderConfig.model_validate(value)
        return self

    @model_validator(mode="after")
    def _validate_api_type_scope(self) -> "ProvidersConfig":
        for name in self.__class__.model_fields:
            if name == "openai":
                continue
            provider = getattr(self, name, None)
            if isinstance(provider, ProviderConfig) and provider.api_type != "auto":
                raise ValueError("providers.<name>.api_type is only supported for providers.openai")
        for provider in (self.model_extra or {}).values():
            if isinstance(provider, ProviderConfig) and provider.api_type != "auto":
                raise ValueError("providers.<name>.api_type is only supported for providers.openai")
        return self


class HeartbeatConfig(Base):
    """Heartbeat service configuration (now backed by cron)."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes
    keep_recent_messages: int = 8
    schedule: RecurrenceConfig | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )  # Clock-anchored recurrence; wins over interval_s when set
    daily_token_budget: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("dailyTokenBudget", "daily_token_budget"),
        serialization_alias="dailyTokenBudget",
    )  # Skip runs once the day's spend reaches this (0 = no ceiling)
    max_consecutive_failures: int = Field(
        default=0,
        ge=0,
        le=100,
        validation_alias=AliasChoices("maxConsecutiveFailures", "max_consecutive_failures"),
        serialization_alias="maxConsecutiveFailures",
    )  # Pause after this many failures in a row (0 = use the global setting)

    def build_schedule(self, timezone: str) -> CronSchedule:
        if self.schedule is not None:
            return self.schedule.build_schedule(timezone)
        # An interval schedule carries no timezone: it fires relative to the
        # previous run rather than at a time of day.
        return CronSchedule(kind="every", every_ms=self.interval_s * 1000)

    def describe_schedule(self) -> str:
        if self.schedule is not None:
            return self.schedule.build_schedule(None).expr or ""
        return f"every {self.interval_s}s"


class ApiConfig(Base):
    """OpenAI-compatible API server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 8900
    timeout: float = 120.0  # Per-request timeout in seconds.
    api_key: str = Field(default="", repr=False)

    @model_validator(mode="after")
    def wildcard_host_requires_auth(self) -> "ApiConfig":
        if self.host not in ("0.0.0.0", "::"):
            return self
        if self.api_key.strip():
            return self
        raise ValueError(
            "host is 0.0.0.0 (all interfaces) but api_key is not set "
            "- set api.api_key to prevent unauthenticated access"
        )


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 18790
    restart_mode: Literal["auto", "exec", "spawn", "exit"] = "auto"
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class UpdatesConfig(Base):
    """Secure desktop update configuration."""

    enabled: bool = True
    auto_check: bool = Field(
        default=True,
        validation_alias=AliasChoices("autoCheck", "auto_check"),
        serialization_alias="autoCheck",
    )
    channel: Literal["stable", "beta"] = "stable"
    base_url: str = Field(
        default="",
        validation_alias=AliasChoices("baseUrl", "base_url"),
        serialization_alias="baseUrl",
    )
    skipped_version: str = Field(
        default="",
        validation_alias=AliasChoices("skippedVersion", "skipped_version"),
        serialization_alias="skippedVersion",
    )


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    cwd: str = ""  # Stdio: working directory for MCP server runtime artifacts
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])  # Only register these tools; accepts raw MCP names or wrapped mcp_<server>_<tool> names; ["*"] = all capabilities (tools, resources, prompts); any restriction = only listed tools, no resources/prompts
    max_tools: int = 0  # Cap on tools registered from this server; 0 = built-in default, -1 = no limit
    max_tool_tokens: int = 0  # Cap on the tokens this server's tool definitions may occupy; 0 = built-in default, -1 = no limit


def _lazy_default(module_path: str, class_name: str) -> Any:
    """Deferred import helper for ToolsConfig default factories."""
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


class BoardGitConfig(Base):
    """Global kill-switches for git automation on board task runs.

    Per-project opt-in lives in ``<project>/.navin/board/settings.json``
    (the Autonomy toggle in the Tasks panel). These flags let an operator
    disable the behaviour machine-wide regardless of project settings.
    """

    auto_branch_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("autoBranchEnabled", "auto_branch_enabled"),
        serialization_alias="autoBranchEnabled",
    )  # allow the agent to create an isolated navin/task-* branch per claimed task
    open_pr_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("openPrEnabled", "open_pr_enabled"),
        serialization_alias="openPrEnabled",
    )  # allow the agent to push and open a pull request when a task is done


class ForgeConfig(Base):
    """Access to the git forges the Code panel opens pull requests on.

    Keyed by hostname so one machine can hold a github.com token next to a
    self-hosted Forgejo and a company GitLab. Without this, opening a PR
    needed the ``gh`` CLI installed and signed in, which only ever covered
    GitHub.
    """

    tokens: dict[str, str] = Field(
        default_factory=dict,
        repr=False,
    )  # host (lowercase, e.g. "forgejo.example.com") -> API token
    # Only needed when the hostname gives nothing away and the probe cannot
    # reach the server: "git.example.com" -> "forgejo" | "gitlab" | "github".
    hosts: dict[str, str] = Field(default_factory=dict)

    @field_validator("tokens", mode="before")
    @classmethod
    def _normalize_token_hosts(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            str(host).strip().lower(): str(token or "").strip()
            for host, token in value.items()
            if str(host).strip()
        }

    @field_validator("hosts", mode="before")
    @classmethod
    def _normalize_host_kinds(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            str(host).strip().lower(): str(kind or "").strip().lower()
            for host, kind in value.items()
            if str(host).strip()
        }


class MontageStockConfig(Base):
    """Free stock API keys (builtin clients - no heavy package install)."""

    pexels_api_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("pexelsApiKey", "pexels_api_key"),
        serialization_alias="pexelsApiKey",
    )
    unsplash_access_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("unsplashAccessKey", "unsplash_access_key"),
        serialization_alias="unsplashAccessKey",
    )
    pixabay_api_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("pixabayApiKey", "pixabay_api_key"),
        serialization_alias="pixabayApiKey",
    )


class MontageLipSyncConfig(Base):
    """Remote lip-sync provider settings."""

    provider: str = "sync_labs"
    api_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("apiKey", "api_key"),
        serialization_alias="apiKey",
    )
    api_base: str = Field(
        default="https://api.sync.so/v2",
        validation_alias=AliasChoices("apiBase", "api_base"),
        serialization_alias="apiBase",
    )
    model: str = "lipsync-2"
    timeout_s: float = Field(
        default=60.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices("timeoutSeconds", "timeout_s"),
        serialization_alias="timeoutSeconds",
    )
    max_wait_s: float = Field(
        default=1800.0,
        gt=0,
        le=7200,
        validation_alias=AliasChoices("maxWaitSeconds", "max_wait_s"),
        serialization_alias="maxWaitSeconds",
    )
    poll_interval_s: float = Field(
        default=10.0,
        gt=0,
        le=60,
        validation_alias=AliasChoices("pollIntervalSeconds", "poll_interval_s"),
        serialization_alias="pollIntervalSeconds",
    )


class MontageToolConfig(Base):
    """Montage studio options (stock keys + package preferences)."""

    stock: MontageStockConfig = Field(default_factory=MontageStockConfig)
    lip_sync: MontageLipSyncConfig = Field(
        default_factory=MontageLipSyncConfig,
        validation_alias=AliasChoices("lipSync", "lip_sync"),
        serialization_alias="lipSync",
    )


class ToolsConfig(Base):
    """Tools configuration.

    Field types for tool-specific sub-configs are resolved via model_rebuild()
    at the bottom of this file so tool config classes can stay next to their
    tool implementations.
    """

    web: WebToolsConfig = Field(default_factory=lambda: _lazy_default("navin.agent.tools.web", "WebToolsConfig"))
    exec: ExecToolConfig = Field(default_factory=lambda: _lazy_default("navin.agent.tools.shell", "ExecToolConfig"))
    file: FileToolsConfig = Field(default_factory=lambda: _lazy_default("navin.agent.tools.filesystem", "FileToolsConfig"))
    cli_apps: CliAppsToolConfig = Field(default_factory=lambda: _lazy_default("navin.agent.tools.cli_apps", "CliAppsToolConfig"))
    my: MyToolConfig = Field(default_factory=lambda: _lazy_default("navin.agent.tools.self", "MyToolConfig"))
    image_generation: ImageGenerationToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.image_generation", "ImageGenerationToolConfig"),
    )
    visual_qa: VisualQAToolConfig = Field(
        default_factory=lambda: _lazy_default(
            "navin.agent.tools.visual_qa", "VisualQAToolConfig"
        ),
        validation_alias=AliasChoices("visualQA", "visual_qa"),
        serialization_alias="visualQA",
    )
    video_generation: VideoGenerationToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.video_generation", "VideoGenerationToolConfig"),
    )
    music_generation: MusicGenerationToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.music_generation", "MusicGenerationToolConfig"),
    )
    speech_generation: SpeechGenerationToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.speech_generation", "SpeechGenerationToolConfig"),
    )
    montage: MontageToolConfig = Field(default_factory=MontageToolConfig)
    database: DatabaseToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.database", "DatabaseToolConfig"),
    )
    scrape: ScrapeToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.scrape", "ScrapeToolConfig"),
    )
    seo: SeoToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.seo", "SeoToolConfig"),
    )
    leads: LeadsToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.leads", "LeadsToolConfig"),
    )
    browser: BrowserToolConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.tools.browser", "BrowserToolConfig"),
    )
    semantic_search: SemanticSearchConfig = Field(
        default_factory=lambda: _lazy_default(
            "navin.agent.tools.code_index", "SemanticSearchConfig"
        ),
        validation_alias=AliasChoices("semanticSearch", "semantic_search"),
        serialization_alias="semanticSearch",
    )
    approvals: ApprovalConfig = Field(
        default_factory=lambda: _lazy_default("navin.agent.approval", "ApprovalConfig"),
    )  # whether a tool may pause a turn to ask the user before a dangerous operation
    board_git: BoardGitConfig = Field(
        default_factory=BoardGitConfig,
        validation_alias=AliasChoices("boardGit", "board_git"),
        serialization_alias="boardGit",
    )  # global kill-switches for auto-branch / auto-PR on board tasks
    forge: ForgeConfig = Field(
        default_factory=ForgeConfig,
    )  # per-host tokens so PRs open on GitHub, GitLab and Forgejo without `gh`
    # Named posture for the knobs above. ``None`` means unset: individual knobs
    # keep their open factory defaults (CLI / headless / tests). ``navin webui``
    # may soft-switch an unset config to assisted on first setup - see
    # navin.config.security_profile. Set ``"autonomous"`` explicitly to opt out
    # of that soft-switch while keeping the open knobs.
    security_profile: Literal["autonomous", "assisted", "strict"] | None = Field(
        default=None,
        validation_alias=AliasChoices("securityProfile", "security_profile"),
        serialization_alias="securityProfile",
    )
    # Off by default: real work reaches outside the project - a sibling repo, a
    # log under /var, a dotfile the build reads - and an agent that refuses those
    # sends the user to do them by hand. An operator who wants the agent fenced
    # in turns this on.
    restrict_to_workspace: bool = False
    webui_allow_local_service_access: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "webuiAllowLocalServiceAccess",
            "webui_allow_local_service_access",
            "allowLocalPreviewAccess",
            "allow_local_preview_access",
        ),
    )  # allow WebUI Full Access shell checks against localhost services; legacy allowLocalPreviewAccess still reads
    webui_allow_remote_package_install: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "webuiAllowRemotePackageInstall",
            "webui_allow_remote_package_install",
        ),
    )  # allow non-local WebUI clients to install optional Python packages
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    # Install zero-setup MCP presets (e.g. debugmcp) into mcp_servers at
    # gateway/agent startup when missing. Set false to opt out entirely.
    auto_enable_mcp_presets: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "autoEnableMcpPresets",
            "auto_enable_mcp_presets",
        ),
        serialization_alias="autoEnableMcpPresets",
    )
    # Off by default: the private ranges this blocks are where a dev server, a
    # staging host and a sibling container live, so blocking them stops ordinary
    # work far more often than an attack. Turn it on when prompts come from
    # somewhere untrusted and cloud metadata is reachable.
    ssrf_protection: bool = Field(
        default=False,
        validation_alias=AliasChoices("ssrfProtection", "ssrf_protection"),
        serialization_alias="ssrfProtection",
    )
    ssrf_whitelist: list[str] = Field(default_factory=list)  # CIDR ranges to exempt when ssrfProtection is on (e.g. ["100.64.0.0/10"] for Tailscale)


class ModelCatalogConfig(Base):
    """Managed model catalog synced from the Navin site.

    When enabled, the gateway reads the public catalog (tiers, slugs, default
    model) and materializes one model preset per tier plus task-role routes.
    Refresh: startup, picker / Settings Models, activate / plan change.
    """

    enabled: bool = False
    url: str = ""  # Empty means the default public catalog (or NAVIN_MODEL_CATALOG_URL)


class LicenseConfig(Base):
    """Navin subscription linkage (navin.live).

    Filled by ``navin license activate``. The activation token (not the
    license key) authenticates this device against the site APIs; the managed
    API key is the per-account OpenRouter key provisioned by the site, with a
    monthly spend cap enforced server-side.
    """

    server_url: str = Field(
        default="",  # Empty means https://navin.live (or NAVIN_LICENSE_SERVER_URL)
        validation_alias=AliasChoices("serverUrl", "server_url"),
        serialization_alias="serverUrl",
    )
    license_key: str = Field(
        default="",
        validation_alias=AliasChoices("licenseKey", "license_key"),
        serialization_alias="licenseKey",
    )
    activation_token: str = Field(
        default="",
        validation_alias=AliasChoices("activationToken", "activation_token"),
        serialization_alias="activationToken",
    )
    device: str = ""  # Fingerprint sent at activation; identifies this machine
    plan: str = ""  # Last plan reported by the site (free, flash, plus, pro, ultra, team)
    # Caps from the last successful validate (0 = unset, use local defaults).
    steps_per_task: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("stepsPerTask", "steps_per_task"),
        serialization_alias="stepsPerTask",
    )
    concurrent_agents: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("concurrentAgents", "concurrent_agents"),
        serialization_alias="concurrentAgents",
    )
    output_tokens_per_call: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("outputTokensPerCall", "output_tokens_per_call"),
        serialization_alias="outputTokensPerCall",
    )
    # Soft budget mode from the last validate / usage report
    # (normal | reduced | economy | exhausted). Applied live by usage_mode.
    # Flagship pause from 50 % (Opus 5+ / Fable 5+ / GPT 5.6) uses
    # usage_used_percent. At 100 % only Nemotron remains.
    usage_mode: str = Field(
        default="normal",
        validation_alias=AliasChoices("usageMode", "usage_mode"),
        serialization_alias="usageMode",
    )
    # Snapshot of managed budget from the last validate / usage report
    # (same figures as navin.live dashboard). Microdollars: 1 $ = 1_000_000.
    usage_budget_micro_usd: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("usageBudgetMicroUsd", "usage_budget_micro_usd"),
        serialization_alias="usageBudgetMicroUsd",
    )
    usage_spent_micro_usd: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("usageSpentMicroUsd", "usage_spent_micro_usd"),
        serialization_alias="usageSpentMicroUsd",
    )
    usage_used_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        validation_alias=AliasChoices("usageUsedPercent", "usage_used_percent"),
        serialization_alias="usageUsedPercent",
    )
    usage_remaining_tokens: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "usageRemainingTokens",
            "usage_remaining_tokens",
            "remainingEquivalentTokens",
        ),
        serialization_alias="usageRemainingTokens",
    )
    # Unix seconds (subscription current_period_end from validate expiresAt).
    period_end: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("periodEnd", "period_end", "expiresAt"),
        serialization_alias="periodEnd",
    )
    managed_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("managedApiKey", "managed_api_key"),
        serialization_alias="managedApiKey",
    )
    managed_provider: str = Field(
        default="",
        validation_alias=AliasChoices("managedProvider", "managed_provider"),
        serialization_alias="managedProvider",
    )
    # Profile of the connected navin.live account, persisted so the editor can
    # show who is signed in even while offline.
    account_email: str = Field(
        default="",
        validation_alias=AliasChoices("accountEmail", "account_email"),
        serialization_alias="accountEmail",
    )
    account_name: str = Field(
        default="",
        validation_alias=AliasChoices("accountName", "account_name"),
        serialization_alias="accountName",
    )
    # Team org context from the last successful validate (empty = solo / none).
    org_id: str = Field(
        default="",
        validation_alias=AliasChoices("orgId", "org_id"),
        serialization_alias="orgId",
    )
    org_role: str = Field(
        default="",
        validation_alias=AliasChoices("orgRole", "org_role", "role"),
        serialization_alias="orgRole",
    )
    seat_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("seatCount", "seat_count"),
        serialization_alias="seatCount",
    )

    @property
    def activated(self) -> bool:
        return bool(self.activation_token and self.device)


class ResourcesConfig(Base):
    """How much of the machine agents are allowed to take.

    The concurrency limit was a fixed number, so the same value applied to a
    laptop, to a 64-core server and to a container capped at one core and one
    gigabyte. Sizing it from what is actually available lets a big host run
    everything it can hold, and stops a small one from swapping.
    """

    enabled: bool = True  # False keeps the plain configured limit, unmeasured
    max_utilisation: float = Field(
        default=0.70,
        ge=0.1,
        le=0.95,
        validation_alias=AliasChoices("maxUtilisation", "max_utilisation"),
        serialization_alias="maxUtilisation",
    )  # Share of measured RAM agents may use; the rest is for everything else
    agents_per_core: int = Field(
        default=32,
        ge=1,
        le=256,
        validation_alias=AliasChoices("agentsPerCore", "agents_per_core"),
        serialization_alias="agentsPerCore",
    )  # High on purpose: an agent waiting on the model API holds no core
    memory_per_agent_mb: int = Field(
        default=96,
        ge=8,
        le=4096,
        validation_alias=AliasChoices("memoryPerAgentMb", "memory_per_agent_mb"),
        serialization_alias="memoryPerAgentMb",
    )  # Assumed cost of one running agent: conversation, context, tool results


class Config(BaseSettings):
    """Root configuration for navin."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    livekit: LiveKitConfig = Field(default_factory=LiveKitConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    loops: LoopGuardrailsConfig = Field(default_factory=LoopGuardrailsConfig)
    model_presets: dict[str, ModelPresetConfig] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("modelPresets", "model_presets"),
        serialization_alias="modelPresets",
    )
    # Task-role routing: maps a task role (e.g. "deep", "fast", "search",
    # "plan", "review", "security", "dev", "docs") to a model preset name.
    # Used by /pilot and surfaced in the WebUI so each kind of task can run
    # on its own model/budget.
    model_routes: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("modelRoutes", "model_routes"),
        serialization_alias="modelRoutes",
    )
    model_catalog: ModelCatalogConfig = Field(
        default_factory=ModelCatalogConfig,
        validation_alias=AliasChoices("modelCatalog", "model_catalog"),
        serialization_alias="modelCatalog",
    )
    license: LicenseConfig = Field(default_factory=LicenseConfig)

    def __init__(self, **values: Any) -> None:
        if not type(self).__pydantic_complete__:
            _resolve_tool_config_refs()
        super().__init__(**values)

    @model_validator(mode="after")
    def _validate_model_preset(self) -> "Config":
        if "default" in self.model_presets:
            raise ValueError("model_preset name 'default' is reserved for agents.defaults")
        name = self.agents.defaults.model_preset
        if name and name != "default" and name not in self.model_presets:
            raise ValueError(f"model_preset {name!r} not found in model_presets")
        for fallback in self.agents.defaults.fallback_models:
            if isinstance(fallback, str) and fallback not in self.model_presets:
                raise ValueError(f"fallback_models entry {fallback!r} not found in model_presets")
        # Silently drop routes pointing at deleted presets instead of failing
        # config load: routes are convenience metadata, not critical state.
        stale_routes = [
            role
            for role, preset in self.model_routes.items()
            if preset != "default" and preset not in self.model_presets
        ]
        for role in stale_routes:
            del self.model_routes[role]
        return self

    def resolve_default_preset(self) -> ModelPresetConfig:
        """Return the implicit `default` preset from agents.defaults fields."""
        d = self.agents.defaults
        return ModelPresetConfig(
            model=d.model, provider=d.provider, max_tokens=d.max_tokens,
            context_window_tokens=d.context_window_tokens,
            temperature=d.temperature, reasoning_effort=d.reasoning_effort,
        )

    def resolve_preset(self, name: str | None = None) -> ModelPresetConfig:
        """Return effective model params from a named preset or the implicit default."""
        name = self.agents.defaults.model_preset if name is None else name
        if not name or name == "default":
            return self.resolve_default_preset()
        if name not in self.model_presets:
            raise KeyError(f"model_preset {name!r} not found in model_presets")
        return self.model_presets[name]

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path (a legacy ~/.navin/workspace value is redirected)."""
        # Lazy import: navin.config.paths must stay importable without schema.
        from navin.config.paths import resolve_workspace_setting
        return resolve_workspace_setting(self.agents.defaults.workspace)

    def _byok_match_for_hidden_navin(
        self, forced: str
    ) -> tuple["ProviderConfig | None", str | None]:
        """Reuse leftover ``provider: navin`` as OpenRouter after the BYOK cut.

        ``navin-cli`` used to crash with engine offline: the preset still said
        ``navin``, the registry no longer has that slot, and ``make_provider``
        raised. The leftover key (often an OpenRouter wire URL) stays usable
        without putting Navin back in Settings.
        """
        if forced.replace("-", "_").lower() != "navin":
            return None, None
        from navin.optional_live import live_modules_available

        if live_modules_available():
            return None, None
        openrouter = getattr(self.providers, "openrouter", None)
        leftover = getattr(self.providers, "navin", None)
        if isinstance(openrouter, ProviderConfig) and openrouter.api_key:
            return openrouter, "openrouter"
        if isinstance(leftover, ProviderConfig) and leftover.api_key:
            return leftover, "openrouter"
        return None, None

    def _match_provider(
        self, model: str | None = None,
        *,
        preset: ModelPresetConfig | None = None,
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from navin.providers.registry import (
            PROVIDERS,
            ProviderSpec,
            find_by_name,
        )

        resolved = preset or self.resolve_preset()
        forced = resolved.provider

        def _custom_provider_by_name(name: str) -> tuple[ProviderConfig, str] | None:
            normalized = name.replace("-", "_").lower()
            for attr_name, provider in (self.providers.model_extra or {}).items():
                if not isinstance(provider, ProviderConfig):
                    continue
                if attr_name.replace("-", "_").lower() == normalized:
                    return provider, attr_name
            return None

        if forced != "auto":
            spec = find_by_name(forced)
            if spec:
                p = getattr(self.providers, spec.name, None)
                return (p, spec.name) if p else (None, None)
            custom = _custom_provider_by_name(forced)
            if custom is not None:
                return custom
            remapped = self._byok_match_for_hidden_navin(forced)
            if remapped[1]:
                return remapped
            return None, None

        model_lower = (model or resolved.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        def _local_ready(spec: ProviderSpec, p: ProviderConfig, *, allow_default_base: bool) -> bool:
            """Local providers are matchable only when the user opted in.

            An empty ``providers.ollama`` block always exists via defaults, so
            ``is_local`` alone must not steal auto-routing (e.g. NVIDIA
            ``nemotron`` models). Explicit ``ollama/<model>`` prefixes may use
            the registry default base; keyword / bare-name matching requires a
            configured ``api_base``.
            """
            if p.api_base:
                return True
            return bool(
                allow_default_base
                and spec.default_api_base
                and spec.route_via_default_base
            )

        # Explicit provider prefix wins - prevents `github-copilot/...codex` matching openai_codex.
        # A key exported in the environment (OPENAI_API_KEY, KIMI_API_KEY, ...)
        # counts as configured: same behavior as Claude Code or OpenCode.
        for spec in PROVIDERS:
            if spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if (
                    spec.is_oauth
                    or spec.is_direct
                    or p.api_key
                    or spec.env_api_key()
                    or (spec.is_local and _local_ready(spec, p, allow_default_base=True))
                ):
                    return p, spec.name

        # Check for custom provider by prefix (e.g., "companyProxy/gpt-4").
        # Return the matching provider even when apiBase is missing, so a
        # malformed explicit prefix fails instead of falling through to a
        # different custom provider.
        if model_prefix:
            custom = _custom_provider_by_name(normalized_prefix)
            if custom is not None:
                return custom

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            if spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if (
                    spec.is_oauth
                    or spec.is_direct
                    or p.api_key
                    or spec.env_api_key()
                    or (spec.is_local and _local_ready(spec, p, allow_default_base=False))
                ):
                    return p, spec.name

        # Fallback: configured local providers can route models without
        # provider-specific keywords (for example plain "llama3.2" on Ollama).
        # Prefer providers whose detect_by_base_keyword matches the configured api_base
        # (e.g. Ollama's "11434" in "http://localhost:11434") over plain registry order.
        local_fallback: tuple[ProviderConfig, str] | None = None
        for spec in PROVIDERS:
            if not spec.is_local:
                continue
            p = getattr(self.providers, spec.name, None)
            if not (p and p.api_base):
                continue
            if spec.detect_by_base_keyword and spec.detect_by_base_keyword in p.api_base:
                return p, spec.name
            if local_fallback is None:
                local_fallback = (p, spec.name)
        if local_fallback:
            return local_fallback

        # Fallback: gateways first, then others (follows registry order)
        # OAuth providers are NOT valid fallbacks - they require explicit model selection
        for spec in PROVIDERS:
            if spec.is_oauth or spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name

        # Same fallback for keys living only in the environment. A separate
        # pass so an explicit config key always outranks an inherited env var.
        for spec in PROVIDERS:
            if spec.is_oauth or spec.is_transcription_only:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and spec.env_api_key():
                return p, spec.name

        # Final fallback: check for any configured custom provider
        for attr_name, p in (self.providers.model_extra or {}).items():
            if isinstance(p, ProviderConfig) and p.api_base:
                return p, attr_name

        return None, None

    def get_provider(
        self,
        model: str | None = None,
        *,
        preset: ModelPresetConfig | None = None,
    ) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model, preset=preset)
        return p

    def get_provider_name(
        self,
        model: str | None = None,
        *,
        preset: ModelPresetConfig | None = None,
    ) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model, preset=preset)
        return name

    def get_api_key(
        self,
        model: str | None = None,
        *,
        preset: ModelPresetConfig | None = None,
    ) -> str | None:
        """Get API key for the given model. Falls back to first available key.

        A key missing from the config is looked up in the process environment
        (the provider's env_key and its aliases, e.g. KIMI_API_KEY for
        Moonshot): exporting the variable is enough, no config edit needed.
        """
        from navin.providers.registry import find_by_name

        p, name = self._match_provider(model, preset=preset)
        if p is None:
            return None
        from navin.config.secrets import unlocked_secret

        if p.api_key:
            key = unlocked_secret(p.api_key)
            if key:
                return key
        spec = find_by_name(name) if name else None
        if spec and (env_key := spec.env_api_key()):
            return env_key
        return unlocked_secret(p.api_key) or None

    def get_api_base(
        self,
        model: str | None = None,
        *,
        preset: ModelPresetConfig | None = None,
    ) -> str | None:
        """Get API base URL for the given model, falling back to the provider default when present."""
        from navin.providers.registry import find_by_name

        p, name = self._match_provider(model, preset=preset)
        if p and p.api_base:
            return p.api_base
        if name:
            from navin.providers.connection_presets import resolve_connection_api_base

            resolved = resolve_connection_api_base(name, p)
            if resolved:
                return resolved
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="NAVIN_", env_nested_delimiter="__")


def _resolve_tool_config_refs() -> None:
    """Resolve forward references in ToolsConfig by importing tool config classes.

    Must be called after all modules are loaded (breaks circular imports).
    Re-exports the classes into this module's namespace so existing imports
    like ``from navin.config.schema import ExecToolConfig`` continue to work.
    """
    import sys

    from navin.agent.approval import ApprovalConfig
    from navin.agent.tools.browser import BrowserToolConfig
    from navin.agent.tools.cli_apps import CliAppsToolConfig
    from navin.agent.tools.code_index import SemanticSearchConfig
    from navin.agent.tools.database import DatabaseToolConfig
    from navin.agent.tools.filesystem import FileToolsConfig
    from navin.agent.tools.image_generation import ImageGenerationToolConfig
    from navin.agent.tools.leads import LeadsToolConfig
    from navin.agent.tools.music_generation import MusicGenerationToolConfig
    from navin.agent.tools.scrape import ScrapeToolConfig
    from navin.agent.tools.self import MyToolConfig
    from navin.agent.tools.seo import SeoToolConfig
    from navin.agent.tools.shell import ExecToolConfig
    from navin.agent.tools.speech_generation import SpeechGenerationToolConfig
    from navin.agent.tools.video_generation import VideoGenerationToolConfig
    from navin.agent.tools.visual_qa import VisualQAToolConfig
    from navin.agent.tools.web import WebFetchConfig, WebSearchConfig, WebToolsConfig

    # Re-export into this module's namespace
    mod = sys.modules[__name__]
    mod.ExecToolConfig = ExecToolConfig  # type: ignore[attr-defined]
    mod.FileToolsConfig = FileToolsConfig  # type: ignore[attr-defined]
    mod.CliAppsToolConfig = CliAppsToolConfig  # type: ignore[attr-defined]
    mod.WebToolsConfig = WebToolsConfig  # type: ignore[attr-defined]
    mod.WebSearchConfig = WebSearchConfig  # type: ignore[attr-defined]
    mod.WebFetchConfig = WebFetchConfig  # type: ignore[attr-defined]
    mod.MyToolConfig = MyToolConfig  # type: ignore[attr-defined]
    mod.ImageGenerationToolConfig = ImageGenerationToolConfig  # type: ignore[attr-defined]
    mod.VisualQAToolConfig = VisualQAToolConfig  # type: ignore[attr-defined]
    mod.VideoGenerationToolConfig = VideoGenerationToolConfig  # type: ignore[attr-defined]
    mod.MusicGenerationToolConfig = MusicGenerationToolConfig  # type: ignore[attr-defined]
    mod.SpeechGenerationToolConfig = SpeechGenerationToolConfig  # type: ignore[attr-defined]
    mod.DatabaseToolConfig = DatabaseToolConfig  # type: ignore[attr-defined]
    mod.ScrapeToolConfig = ScrapeToolConfig  # type: ignore[attr-defined]
    mod.SeoToolConfig = SeoToolConfig  # type: ignore[attr-defined]
    mod.LeadsToolConfig = LeadsToolConfig  # type: ignore[attr-defined]
    mod.BrowserToolConfig = BrowserToolConfig  # type: ignore[attr-defined]
    mod.SemanticSearchConfig = SemanticSearchConfig  # type: ignore[attr-defined]
    mod.ApprovalConfig = ApprovalConfig  # type: ignore[attr-defined]

    ToolsConfig.model_rebuild()
    Config.model_rebuild()


# Eagerly resolve when the import chain allows it (no circular deps at this
# point).  If it fails (first import triggers a cycle), the rebuild will
# happen lazily when Config/ToolsConfig is first used at runtime.
try:
    _resolve_tool_config_refs()
except ImportError:
    pass
