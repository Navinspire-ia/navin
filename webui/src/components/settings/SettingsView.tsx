// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  useCallback,
  useEffect,
  forwardRef,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { Dropdown as FluentDropdown } from "@fluentui/react";
import {
  Activity,
  ArrowUpCircle,
  ArrowUpDown,
  Bot,
  Brain,
  Check,
  CircleAlert,
  CircleUserRound,
  Clapperboard,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Cloud,
  Clipboard,
  Cpu,
  Database,
  DownloadCloud,
  Eye,
  EyeOff,
  ExternalLink,
  Gem,
  Globe2,
  Grid3X3,
  HardDrive,
  Hexagon,
  ImageIcon,
  Info,
  KeyRound,
  Layers,
  Loader2,
  Mic,
  Monitor,
  Moon,
  PauseCircle,
  PlayCircle,
  Plus,
  Orbit,
  Palette,
  Radio,
  Pencil,
  Puzzle,
  RotateCcw,
  Route,
  ScrollText,
  Search,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Star,
  Trash2,
  Waves,
  Wrench,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { AccountSettings } from "@/components/settings/AccountSettings";
import { ComputerSettings } from "@/components/settings/ComputerSettings";
import { LiveVoiceSettings } from "@/components/settings/LiveVoiceSettings";
import { MediaModelPicker, MediaSettingsSurface } from "@/components/settings/MediaModelPicker";
import { AutomationEditDialog } from "@/components/settings/AutomationEditDialog";
import { AppTemplatesSettings } from "@/components/settings/AppTemplatesSettings";
import { SkillsCatalogSettings } from "@/components/settings/SkillsCatalogSettings";
import { ModelTokenUsageTable } from "@/components/settings/ModelTokenUsageTable";
import { TokenUsageHeatmap } from "@/components/settings/TokenUsageHeatmap";
import { ExecPolicySettings } from "@/components/settings/ExecPolicySettings";
import { ToggleButton } from "@/components/settings/ToggleButton";
import {
  channelFilterTab,
  isHiddenToolsChannel,
  isSocialChannel,
  mergeSocialChannelFeatures,
  type ChannelFilterTab,
} from "@/components/settings/channels/catalog";
import {
  channelDisplayName,
  channelSearchText,
} from "@/components/settings/channels/ChannelIdentity";
import {
  ChannelCatalogRow,
  ChannelSetupPanel,
} from "@/components/settings/channels/ChannelSetupPanel";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { OllamaSetupPanel } from "@/components/settings/OllamaSetupPanel";
import {
  checkVersion,
  createModelConfiguration,
  downloadUpdate,
  disableNavinFeature,
  enableNavinFeature,
  fetchAutomations,
  fetchReasoningEffortValues,
  fetchSettings,
  fetchSettingsUsage,
  isReasoningEffortError,
  fetchCliApps,
  fetchMcpPresets,
  fetchNavinFeatures,
  fetchProviderModels,
  chunkModelImportEntries,
  importMcpConfig,
  importModelConfigurations,
  installUpdate,
  loginProviderOAuth,
  logoutProviderOAuth,
  openExternalUrl,
  pollProviderOAuth,
  runAutomationAction,
  runCliAppAction,
  runMcpPresetAction,
  saveCustomMcpServer,
  updateAutomation,
  updateImageGenerationSettings,
  updateMcpServerTools,
  updateModelConfiguration,
  deleteModelConfiguration,
  updateModelRoute,
  updateNetworkSafetySettings,
  testProviderConnection,
  updateProviderSettings,
  updateSettings,
  updateMusicGenerationSettings,
  updateLiveVoiceSettings,
  updateVideoGenerationSettings,
  updateWebSearchSettings,
  updatePreferences,
  type UpdateInfo,
} from "@/lib/api";
import { notifyCliAppsChanged } from "@/lib/cli-app-events";
import { copyTextOrNotify, copyTextToClipboard } from "@/lib/clipboard";
import {
  readLocalPreferences,
  writeLocalPreferences,
  type FileEditDisplayMode,
  type LocalActivityMode,
  type LocalDensity,
  type LocalPreferences,
} from "@/lib/local-preferences";
import { getRuntimeHost, isNativeRuntime } from "@/lib/runtime";
import { notifyMcpPresetsChanged } from "@/lib/mcp-preset-events";
import { fmtDateTime, relativeTime } from "@/lib/format";
import {
  describeRecurrence,
  isAutoPaused,
  isConfigurableSystemLoop,
  loopLabel,
  loopPurpose,
  recurrenceOf,
  spendState,
} from "@/lib/loop-schedule";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import {
  logoFallbackUrls,
  providerBrand,
  providerDisplayLabel,
} from "@/lib/provider-brand";
import { lookupConnectionBase } from "@/lib/provider-connection";
import { cn } from "@/lib/utils";
import {
  canBeChatDefault,
  isMediaModality,
  isVisionChatModel,
  modalityBadgeClass,
  modalityBadgeLabel,
  SETTINGS_MODEL_SECTIONS,
  settingsModelSection,
  visionBadgeClass,
  visionBadgeLabel,
  type SettingsModelSection,
} from "@/lib/model-modality";
import { shortWorkspacePath } from "@/lib/workspace";
import { useClient } from "@/providers/ClientProvider";
import type {
  AutomationsPayload,
  AutomationUpdatePayload,
  CliAppInfo,
  CliAppsPayload,
  ForgeKind,
  ImageGenerationSettingsUpdate,
  McpPresetInfo,
  McpPresetsPayload,
  ModelConfigurationImportEntry,
  NavinFeatureInfo,
  NavinFeaturesPayload,
  NetworkSafetySettingsUpdate,
  ProviderConnectionTestPayload,
  ProviderModelsPayload,
  SessionAutomationJob,
  SettingsPayload,
  SettingsUpdate,
  SkillSummary,
  MusicGenerationSettingsUpdate,
  TranscriptionSettingsUpdate,
  VideoGenerationSettingsUpdate,
  VoiceSettingsUpdate,
  WebSearchSettingsUpdate,
  WebuiDefaultAccessMode,
} from "@/lib/types";
import { useAccount } from "@/hooks/useAccount";

export type SettingsSectionKey =
  | "overview"
  | "account"
  | "appearance"
  | "providers"
  | "models"
  | "image"
  | "video"
  | "voice"
  | "browser"
  | "computer"
  | "tools"
  | "channels"
  | "apps"
  | "automations"
  | "skills"
  | "templates"
  | "runtime"
  | "advanced"
  | "about";

type AppsKindFilter = "all" | "ready" | "cli" | "mcp";
type ToolsPane = "mcp" | "channels";

const SHARED_MCP_NAMES = new Set(["linkedin", "exa"]);
const CAREER_MCP_NAMES = new Set(["linkedin", "notion", "github", "exa"]);
const TENDERS_MCP_NAMES = new Set(["linkedin", "exa"]);

function isCareerMcp(preset: McpPresetInfo): boolean {
  if (CAREER_MCP_NAMES.has(preset.name)) return true;
  if ((preset.category || "").toLowerCase() === "career") return true;
  return (preset.modules ?? []).includes("career");
}

function isTendersMcp(preset: McpPresetInfo): boolean {
  if (TENDERS_MCP_NAMES.has(preset.name)) return true;
  return (preset.modules ?? []).includes("tenders");
}

type AutomationFilter = "all" | "active" | "paused" | "failed" | "system";
type AutomationSort = "next" | "last" | "updated" | "name";
type AutomationAction = "enable" | "disable" | "delete" | "run";
type AppsCatalogItem =
  | { id: string; kind: "cli"; app: CliAppInfo }
  | { id: string; kind: "mcp"; preset: McpPresetInfo };

interface AgentSettingsDraft {
  model: string;
  provider: string;
  modelPreset: string;
  presetLabel: string;
  contextWindowTokens: number;
  reasoningEffort: string;
  timezone: string;
  botName: string;
  botIcon: string;
  toolHintMaxLength: number;
}

interface ModelConfigurationDraft {
  label: string;
  provider: string;
  model: string;
}

type PendingRestartSection = "runtime" | "browser" | "image" | "video";
type PendingRestartSections = Record<PendingRestartSection, boolean>;
type RestartAwarePayload = {
  requires_restart?: boolean;
  surface?: SettingsPayload["surface"];
  runtime_surface?: SettingsPayload["runtime_surface"];
  runtime_capabilities?: SettingsPayload["runtime_capabilities"];
};
type ProviderApiType = "auto" | "chat_completions" | "responses";
type ProviderAuthMode = "none" | "bearer";
type ProviderForm = {
  apiKey: string;
  apiBase: string;
  apiType: ProviderApiType;
  authMode: ProviderAuthMode;
  endpointRegion: string;
  accessPlan: string;
  wireProtocol: string;
};

const emptyProviderForm = (): ProviderForm => ({
  apiKey: "",
  apiBase: "",
  apiType: "auto",
  authMode: "none",
  endpointRegion: "",
  accessPlan: "",
  wireProtocol: "",
});

type CustomMcpTransport = "stdio" | "streamableHttp" | "sse";

const CONTEXT_WINDOW_TOKEN_OPTIONS = [
  65_536,
  131_072,
  200_000,
  262_144,
  1_000_000,
] as const;

function contextWindowTokenLabel(tokens: number): string {
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return Number.isInteger(millions) ? `${millions}M` : `${millions.toFixed(1)}M`;
  }
  // Powers of two read as binary K (65 536 → 64K); round decimals otherwise.
  return tokens % 1_024 === 0 ? `${tokens / 1_024}K` : `${Math.round(tokens / 1_000)}K`;
}
const DEFERRED_MODEL_LIST_PROVIDERS = new Set([
  "atomic_chat",
  "huggingface",
  "lm_studio",
  "novita",
  "ollama",
  "omniroute",
  "openrouter",
  "ovms",
  "siliconflow",
  "vllm",
]);
const DEFERRED_MODEL_LIST_QUERY_MIN_LENGTH = 2;
const CLI_APPS_REFRESH_RETRY_MS = 2_000;
const CLI_APPS_REFRESH_MAX_RETRIES = 30;

const FALLBACK_TIMEZONES = [
  "UTC",
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Singapore",
  "Asia/Taipei",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Amsterdam",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Toronto",
  "America/Sao_Paulo",
  "Australia/Sydney",
  "Pacific/Auckland",
];

interface CustomMcpForm {
  name: string;
  transport: CustomMcpTransport;
  command: string;
  args: string;
  url: string;
  env: string;
  headers: string;
  toolTimeout: string;
}

const OPENAI_API_TYPE_OPTIONS: Array<{ value: ProviderApiType; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "chat_completions", label: "Chat Completions" },
  { value: "responses", label: "Responses" },
];

const RETIRED_LLM_PROVIDERS = new Set([
  "xiaomi_mimo",
  "longcat",
  "ant_ling",
  "minimax_anthropic",
  "dashscope",
  "volcengine_coding_plan",
  "byteplus",
  "byteplus_coding_plan",
  "skywork",
  "aihubmix",
]);

function hiddenSettingsProvider(name: string): boolean {
  return RETIRED_LLM_PROVIDERS.has(name);
}

const PROVIDER_DISPLAY_ORDER = new Map(
  [
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
  ].map((name, index) => [name, index]),
);

const IMAGE_ASPECT_RATIO_OPTIONS = ["1:1", "3:4", "9:16", "4:3", "16:9", "3:2", "2:3", "21:9"];
const IMAGE_SIZE_OPTIONS = ["1K", "2K", "4K", "1024x1024", "1536x1024", "1024x1536"];
const EMPTY_PENDING_RESTART_SECTIONS: PendingRestartSections = {
  runtime: false,
  browser: false,
  image: false,
  video: false,
};
const VIDEO_ASPECT_RATIO_OPTIONS = ["16:9", "9:16", "1:1"];
const VIDEO_RESOLUTION_OPTIONS = ["", "720p", "1080p"];

const DEFAULT_CUSTOM_MCP_FORM: CustomMcpForm = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  url: "",
  env: "",
  headers: "",
  toolTimeout: "30",
};

interface SettingsViewProps {
  theme: "light" | "dark";
  initialSection?: SettingsSectionKey;
  initialSettings?: SettingsPayload | null;
  showSidebar?: boolean;
  onToggleTheme: () => void;
  onBackToChat: () => void;
  onModelNameChange: (modelName: string | null) => void;
  onSettingsChange?: (payload: SettingsPayload) => void;
  skills?: SkillSummary[];
  onSkillsChange?: () => void | Promise<void>;
  onWorkspaceSettingsChange?: () => void | Promise<void>;
  onSectionChange?: (section: SettingsSectionKey) => void;
  onRestart?: () => void;
  onNativeEngineRestart?: () => Promise<string>;
  isRestarting?: boolean;
  hostChromeInset?: boolean;
}

/** Same height as `HostChrome` (`h-11`). Extra 4.25rem / 4.75rem made Settings jump. */
const SETTINGS_HOST_CHROME_PAD = "pt-11";

function modelPresetValue(payload: SettingsPayload): string {
  return payload.agent.model_preset || "default";
}

function defaultPreset(payload: SettingsPayload): SettingsPayload["model_presets"][number] | null {
  return payload.model_presets.find((preset) => preset.is_default) ?? null;
}

function normalizeContextWindowTokens(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 200_000;
}

function editableDefaultProvider(payload: SettingsPayload): string {
  const base = defaultPreset(payload);
  return base?.provider ?? payload.agent.provider ?? payload.agent.resolved_provider ?? "";
}

function settingsProviderRow(
  payload: SettingsPayload,
  provider: string | null | undefined,
): SettingsPayload["providers"][number] | null {
  if (!provider) return null;
  return payload.providers.find((row) => row.name === provider) ?? null;
}

function settingsProviderConfigured(
  payload: SettingsPayload,
  provider: string | null | undefined,
): boolean {
  const row = settingsProviderRow(payload, provider);
  if (row) return row.configured;
  if (provider === "auto") {
    const resolvedRow = settingsProviderRow(
      payload,
      payload.agent.resolved_provider ?? payload.agent.provider,
    );
    if (resolvedRow) return resolvedRow.configured;
  }
  return payload.agent.has_api_key;
}

/** True when a usable model is selected (via the active preset or defaults). */
function activeModelConfigured(payload: SettingsPayload): boolean {
  const presetName = payload.agent.model_preset;
  if (presetName && presetName !== "default") {
    const preset = payload.model_presets.find((item) => item.name === presetName);
    if (preset?.model) return true;
  }
  return Boolean(payload.agent.model);
}

/** Managed-key subscribers get models from the catalog; everyone else is BYOK. */
function isManagedSubscriber(account: {
  connected?: boolean;
  managed_key_active?: boolean;
} | null | undefined): boolean {
  return Boolean(account?.connected && account?.managed_key_active);
}

const MODEL_ROUTE_ROLES: Array<{
  role: string;
  fallbackLabel: string;
  fallbackHelp: string;
}> = [
  {
    role: "deep",
    fallbackLabel: "Complex tasks",
    fallbackHelp:
      "Architecture, large refactors, tricky debugging, migrations. Use a real capable model (e.g. Qwen 3.8 Max) - never a :free endpoint.",
  },
  {
    role: "dev",
    fallbackLabel: "Medium tasks - everyday coding",
    fallbackHelp:
      "Writing a function, fixing a bug, unit tests, multi-step edits. A balanced model (e.g. Sonnet) is usually enough.",
  },
  {
    role: "fast",
    fallbackLabel: "Simple tasks",
    fallbackHelp:
      "Quick questions, renames, short summaries, commit messages. An economy model (e.g. Haiku, Flash) costs 10-30x less per token.",
  },
  {
    role: "code",
    fallbackLabel: "Editor Tab / Cmd+K",
    fallbackHelp:
      "Inline ghost completions and Cmd+K rewrites. Prefer the fastest cheap model - this runs on every keystroke pause.",
  },
  {
    role: "vision",
    fallbackLabel: "Image / video analysis",
    fallbackHelp:
      "Screenshots, photos and video clips attached in chat. Defaults to Gemini 3.7 Flash on paid catalogs, not a :free multimodal.",
  },
  {
    role: "computer",
    fallbackLabel: "Desktop control",
    fallbackHelp:
      "Turns where the agent drives the mouse and keyboard. Needs a model trained to place clicks on a screenshot (Claude Sonnet/Opus 4+, GPT-5 / CUA, Gemini 2.5+, Qwen VL, UI-TARS) - a plain vision model misses buttons.",
  },
  {
    role: "search",
    fallbackLabel: "Web search",
    fallbackHelp:
      "Research, comparisons, reading online docs. Browsing burns many tokens - a fast model keeps it cheap.",
  },
  {
    role: "plan",
    fallbackLabel: "Planning",
    fallbackHelp: "Breaking work into steps, drafting specs before implementation.",
  },
  {
    role: "review",
    fallbackLabel: "Code review",
    fallbackHelp: "Reviewing diffs, spotting bugs and regressions.",
  },
  {
    role: "security",
    fallbackLabel: "Security",
    fallbackHelp: "Audits, secrets handling, permission checks - accuracy matters here.",
  },
  {
    role: "docs",
    fallbackLabel: "Documentation",
    fallbackHelp: "READMEs, docstrings, summaries and content writing.",
  },
];

function reasoningEffortLabel(
  value: string,
  tx: (key: string, fallback: string) => string,
): string {
  switch (value) {
    case "":
      return tx("settings.values.reasoningAuto", "Auto");
    case "none":
      return tx("settings.values.reasoningOff", "Off");
    case "minimal":
      return tx("settings.values.reasoningMinimal", "Minimal");
    case "low":
      return tx("settings.values.reasoningLow", "Low");
    case "medium":
      return tx("settings.values.reasoningMedium", "Medium");
    case "high":
      return tx("settings.values.reasoningHigh", "High");
    case "xhigh":
      return tx("settings.values.reasoningXHigh", "X-High");
    case "max":
      return tx("settings.values.reasoningMax", "Max");
    case "adaptive":
      return tx("settings.values.reasoningAdaptive", "Adaptive");
    default:
      return value;
  }
}

const DEFAULT_AGENT_SETTINGS_DRAFT: AgentSettingsDraft = {
  model: "",
  provider: "",
  modelPreset: "default",
  presetLabel: "Default",
  contextWindowTokens: 200_000,
  reasoningEffort: "",
  timezone: "UTC",
  botName: "navin",
  botIcon: "",
  toolHintMaxLength: 40,
};

const DEFAULT_WEB_SEARCH_FORM: WebSearchSettingsUpdate = {
  provider: "duckduckgo",
  apiKey: "",
  baseUrl: "",
  maxResults: 5,
  timeout: 30,
  useJinaReader: true,
};

const DEFAULT_IMAGE_GENERATION_FORM: ImageGenerationSettingsUpdate = {
  enabled: false,
  provider: "",
  model: "google/gemini-3.1-flash-image",
  defaultAspectRatio: "1:1",
  defaultImageSize: "2K",
  maxImagesPerTurn: 4,
};

const DEFAULT_VIDEO_GENERATION_FORM: VideoGenerationSettingsUpdate = {
  enabled: false,
  provider: "",
  model: "minimax/hailuo-3",
  defaultAspectRatio: "16:9",
  defaultDurationSeconds: 8,
  defaultResolution: "",
};

const DEFAULT_VIDEO_GENERATION_SETTINGS: NonNullable<SettingsPayload["video_generation"]> = {
  enabled: false,
  provider: "",
  provider_configured: false,
  model: "minimax/hailuo-3",
  default_aspect_ratio: "16:9",
  default_duration_seconds: 8,
  default_resolution: "",
  max_wait_seconds: 600,
  save_dir: "generated-video",
  providers: [],
};

const DEFAULT_TRANSCRIPTION_FORM: TranscriptionSettingsUpdate = {
  enabled: true,
  provider: "",
  model: "nvidia/parakeet-tdt-0.6b-v3",
  language: "",
  maxDurationSec: 120,
  maxUploadMb: 25,
};

const DEFAULT_TRANSCRIPTION_SETTINGS: NonNullable<SettingsPayload["transcription"]> = {
  enabled: true,
  provider: "",
  provider_configured: false,
  model: "nvidia/parakeet-tdt-0.6b-v3",
  language: null,
  max_duration_sec: 120,
  max_upload_mb: 25,
  providers: [],
  managed_models: [],
};

const DEFAULT_MUSIC_GENERATION_FORM: MusicGenerationSettingsUpdate = {
  enabled: true,
  provider: "",
  model: "google/lyria-3-clip-preview",
};

const DEFAULT_MUSIC_GENERATION_SETTINGS: NonNullable<SettingsPayload["music_generation"]> = {
  enabled: true,
  enabled_auto: true,
  provider: "",
  provider_configured: false,
  model: "google/lyria-3-clip-preview",
  save_dir: "generated-music",
  providers: [],
  managed_models: [],
};

const DEFAULT_VOICE_FORM: VoiceSettingsUpdate = {
  ttsProvider: "",
  ttsModel: "",
  voice: "auto",
  autoSpeak: false,
  responseFormat: "mp3",
  realtimeEnabled: null,
};

const DEFAULT_VOICE_SETTINGS: NonNullable<SettingsPayload["voice"]> = {
  tts_provider: "",
  tts_provider_configured: false,
  tts_model: "",
  voice: "auto",
  auto_speak: false,
  response_format: "mp3",
  realtime_enabled: null,
  realtime_allowed: false,
  realtime_required_plans: ["pro", "ultra", "team"],
  providers: [],
};

const DEFAULT_NETWORK_SAFETY_FORM: NetworkSafetySettingsUpdate = {
  webuiAllowLocalServiceAccess: true,
  webuiDefaultAccessMode: "default",
};

function agentDraftFromPayload(payload: SettingsPayload): AgentSettingsDraft {
  const fallbackDefault = defaultPreset(payload);
  const activePresetName = modelPresetValue(payload);
  const activePreset =
    payload.model_presets.find((preset) => preset.name === activePresetName) ?? fallbackDefault;
  return {
    model: activePreset?.model ?? payload.agent.model,
    provider: activePreset?.is_default
      ? editableDefaultProvider(payload)
      : activePreset?.provider ?? editableDefaultProvider(payload),
    modelPreset: activePresetName,
    presetLabel: activePreset?.label ?? activePresetName,
    contextWindowTokens: normalizeContextWindowTokens(
      activePreset?.context_window_tokens ?? payload.agent.context_window_tokens,
    ),
    reasoningEffort: activePreset?.reasoning_effort ?? payload.agent.reasoning_effort ?? "",
    timezone: payload.agent.timezone,
    botName: payload.agent.bot_name,
    botIcon: payload.agent.bot_icon,
    toolHintMaxLength: payload.agent.tool_hint_max_length,
  };
}

function webSearchFormFromPayload(
  payload: SettingsPayload,
  previous?: WebSearchSettingsUpdate,
): WebSearchSettingsUpdate {
  return {
    provider: payload.web_search.provider,
    apiKey: previous?.provider === payload.web_search.provider ? previous.apiKey ?? "" : "",
    baseUrl: payload.web_search.base_url ?? "",
    maxResults: payload.web_search.max_results,
    timeout: payload.web_search.timeout,
    useJinaReader: payload.web.fetch.use_jina_reader,
  };
}

type WebSearchProviderOption = SettingsPayload["web_search"]["providers"][number];

function webSearchProviderAcceptsApiKey(provider?: WebSearchProviderOption): boolean {
  return provider?.credential === "api_key" || provider?.credential === "optional_api_key";
}

function webSearchProviderRequiresApiKey(provider?: WebSearchProviderOption): boolean {
  return provider?.credential === "api_key";
}

function imageGenerationFormFromPayload(payload: SettingsPayload): ImageGenerationSettingsUpdate {
  return {
    enabled: payload.image_generation.enabled,
    provider: payload.image_generation.provider,
    model: payload.image_generation.model,
    defaultAspectRatio: payload.image_generation.default_aspect_ratio,
    defaultImageSize: payload.image_generation.default_image_size,
    maxImagesPerTurn: payload.image_generation.max_images_per_turn,
  };
}

function videoGenerationFormFromPayload(payload: SettingsPayload): VideoGenerationSettingsUpdate {
  const video = payload.video_generation ?? DEFAULT_VIDEO_GENERATION_SETTINGS;
  return {
    enabled: video.enabled,
    provider: video.provider,
    model: video.model,
    defaultAspectRatio: video.default_aspect_ratio,
    defaultDurationSeconds: video.default_duration_seconds,
    defaultResolution: video.default_resolution ?? "",
  };
}

function transcriptionFormFromPayload(payload: SettingsPayload): TranscriptionSettingsUpdate {
  const transcription = payload.transcription ?? DEFAULT_TRANSCRIPTION_SETTINGS;
  return {
    enabled: transcription.enabled,
    provider: transcription.provider,
    model: transcription.model,
    language: transcription.language ?? "",
    maxDurationSec: transcription.max_duration_sec,
    maxUploadMb: transcription.max_upload_mb,
  };
}

function musicGenerationFormFromPayload(payload: SettingsPayload): MusicGenerationSettingsUpdate {
  const music = payload.music_generation ?? DEFAULT_MUSIC_GENERATION_SETTINGS;
  return {
    enabled: music.enabled,
    provider: music.provider,
    model: music.model,
  };
}

function voiceFormFromPayload(payload: SettingsPayload): VoiceSettingsUpdate {
  const voice = payload.voice ?? DEFAULT_VOICE_SETTINGS;
  return {
    ttsProvider: voice.tts_provider,
    ttsModel: voice.tts_model,
    voice: voice.voice,
    autoSpeak: voice.auto_speak,
    responseFormat: voice.response_format,
    realtimeEnabled: voice.realtime_enabled,
  };
}

function networkSafetyFormFromPayload(payload: SettingsPayload): NetworkSafetySettingsUpdate {
  return {
    webuiAllowLocalServiceAccess:
      payload.advanced.webui_allow_local_service_access ??
      payload.advanced.allow_local_preview_access ??
      true,
    webuiDefaultAccessMode: visibleWebuiDefaultAccessMode(
      payload.advanced.webui_default_access_mode,
    ),
  };
}

function pendingRestartSectionsFromPayload(payload: SettingsPayload): PendingRestartSections {
  const sections = payload.restart_required_sections ?? [];
  return {
    runtime: sections.includes("runtime"),
    browser: sections.includes("browser"),
    image: sections.includes("image"),
    video: sections.includes("video"),
  };
}

export function SettingsView({
  theme,
  initialSection = "overview",
  initialSettings = null,
  showSidebar = true,
  onToggleTheme,
  onBackToChat,
  onModelNameChange,
  onSettingsChange,
  skills = [],
  onSkillsChange,
  onWorkspaceSettingsChange,
  onSectionChange,
  onRestart,
  onNativeEngineRestart,
  isRestarting = false,
  hostChromeInset = false,
}: SettingsViewProps) {
  const { t } = useTranslation();
  const { token } = useClient();
  const { account } = useAccount();
  const [settings, setSettings] = useState<SettingsPayload | null>(() => initialSettings);
  const [cliApps, setCliApps] = useState<CliAppsPayload | null>(null);
  const [navinFeatures, setNavinFeatures] = useState<NavinFeaturesPayload | null>(null);
  const featureCatalog = navinFeatures?.features ?? [];
  const [mcpPresets, setMcpPresets] = useState<McpPresetsPayload | null>(null);
  const [automations, setAutomations] = useState<AutomationsPayload | null>(null);
  const [loading, setLoading] = useState(() => initialSettings === null);
  const [cliAppsLoading, setCliAppsLoading] = useState(true);
  const [navinFeaturesLoading, setNavinFeaturesLoading] = useState(true);
  const [mcpPresetsLoading, setMcpPresetsLoading] = useState(true);
  const [automationsLoading, setAutomationsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [modelConfigurationOpen, setModelConfigurationOpen] = useState(false);
  const [modelConfigurationSaving, setModelConfigurationSaving] = useState(false);
  const [modelConfigurationForm, setModelConfigurationForm] = useState<ModelConfigurationDraft>({
    label: "",
    provider: "",
    model: "",
  });
  const [modelImportOpen, setModelImportOpen] = useState(false);
  const [modelImportProvider, setModelImportProvider] = useState("");
  const [modelImportSaving, setModelImportSaving] = useState(false);
  const [modelImportProgress, setModelImportProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [cliAppsAction, setCliAppsAction] = useState<string | null>(null);
  const [navinFeatureAction, setNavinFeatureAction] = useState<string | null>(null);
  const [navinFeatureConfirm, setNavinFeatureConfirm] = useState<NavinFeatureInfo | null>(null);
  const [mcpPresetAction, setMcpPresetAction] = useState<string | null>(null);
  const [providerSaving, setProviderSaving] = useState<string | null>(null);
  /** Shown while a browser sign-in is in flight, so the user can open it by hand. */
  const [oauthAuthorizeUrl, setOauthAuthorizeUrl] = useState<string | null>(null);
  /** Dialog shown once after provider save; banner on Models stays until a model is set. */
  const [modelSetupPromptProvider, setModelSetupPromptProvider] = useState<string | null>(null);
  const [webSearchSaving, setWebSearchSaving] = useState(false);
  const [imageGenerationSaving, setImageGenerationSaving] = useState(false);
  const [videoGenerationSaving, setVideoGenerationSaving] = useState(false);
  const [musicGenerationSaving, setMusicGenerationSaving] = useState(false);
  const [voiceSaving, setVoiceSaving] = useState(false);
  const [networkSafetySaving, setNetworkSafetySaving] = useState(false);
  const [hostEngineApplying, setHostEngineApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Save refusal about the Thinking level, shown on that control rather than the banner. */
  const [reasoningEffortError, setReasoningEffortError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<SettingsSectionKey>(initialSection);
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [providerQuery, setProviderQuery] = useState("");
  const [appsQuery, setAppsQuery] = useState("");
  const [channelsQuery, setChannelsQuery] = useState("");
  const [automationsQuery, setAutomationsQuery] = useState("");
  const [automationsFilter, setAutomationsFilter] = useState<AutomationFilter>("all");
  const [automationsSort, setAutomationsSort] = useState<AutomationSort>("next");
  const [cliAppsMessage, setCliAppsMessage] = useState<string | null>(null);
  const [cliAppsError, setCliAppsError] = useState<string | null>(null);
  const [navinFeaturesError, setNavinFeaturesError] = useState<string | null>(null);
  const [cliAppsFocusName, setCliAppsFocusName] = useState<string | null>(null);
  const [appsKindFilter, setAppsKindFilter] = useState<AppsKindFilter>("all");
  const [mcpMessage, setMcpMessage] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [automationsError, setAutomationsError] = useState<string | null>(null);
  const [automationAction, setAutomationAction] = useState<string | null>(null);
  const [automationPendingDelete, setAutomationPendingDelete] =
    useState<SessionAutomationJob | null>(null);
  const [automationPendingEdit, setAutomationPendingEdit] =
    useState<SessionAutomationJob | null>(null);
  const [mcpFieldValues, setMcpFieldValues] = useState<Record<string, Record<string, string>>>({});
  const [customMcpForm, setCustomMcpForm] = useState<CustomMcpForm>(DEFAULT_CUSTOM_MCP_FORM);
  const [mcpConfigImport, setMcpConfigImport] = useState("");
  const [providerForms, setProviderForms] = useState<Record<string, ProviderForm>>({});
  const [visibleProviderKeys, setVisibleProviderKeys] = useState<Record<string, boolean>>({});
  const [editingProviderKeys, setEditingProviderKeys] = useState<Record<string, boolean>>({});
  const [pendingRestartSections, setPendingRestartSections] = useState<PendingRestartSections>(
    EMPTY_PENDING_RESTART_SECTIONS,
  );
  const [localPrefs, setLocalPrefs] = useState<LocalPreferences>(() => readLocalPreferences());
  const [webSearchForm, setWebSearchForm] = useState<WebSearchSettingsUpdate>(() =>
    initialSettings ? webSearchFormFromPayload(initialSettings) : DEFAULT_WEB_SEARCH_FORM,
  );
  const [imageGenerationForm, setImageGenerationForm] = useState<ImageGenerationSettingsUpdate>(
    () =>
      initialSettings
        ? imageGenerationFormFromPayload(initialSettings)
        : DEFAULT_IMAGE_GENERATION_FORM,
  );
  const [videoGenerationForm, setVideoGenerationForm] = useState<VideoGenerationSettingsUpdate>(
    () =>
      initialSettings
        ? videoGenerationFormFromPayload(initialSettings)
        : DEFAULT_VIDEO_GENERATION_FORM,
  );
  const [transcriptionForm, setTranscriptionForm] = useState<TranscriptionSettingsUpdate>(
    () => initialSettings ? transcriptionFormFromPayload(initialSettings) : DEFAULT_TRANSCRIPTION_FORM,
  );
  const [musicGenerationForm, setMusicGenerationForm] = useState<MusicGenerationSettingsUpdate>(
    () =>
      initialSettings
        ? musicGenerationFormFromPayload(initialSettings)
        : DEFAULT_MUSIC_GENERATION_FORM,
  );
  const [voiceForm, setVoiceForm] = useState<VoiceSettingsUpdate>(
    () => (initialSettings ? voiceFormFromPayload(initialSettings) : DEFAULT_VOICE_FORM),
  );
  const [networkSafetyForm, setNetworkSafetyForm] = useState<NetworkSafetySettingsUpdate>(() =>
    initialSettings ? networkSafetyFormFromPayload(initialSettings) : DEFAULT_NETWORK_SAFETY_FORM,
  );

  useEffect(() => {
    setActiveSection(initialSection);
  }, [initialSection]);

  const selectSection = useCallback(
    (section: SettingsSectionKey) => {
      setActiveSection(section);
      onSectionChange?.(section);
    },
    [onSectionChange],
  );
  const [webSearchKeyVisible, setWebSearchKeyVisible] = useState(false);
  const [webSearchKeyEditing, setWebSearchKeyEditing] = useState(false);
  const [form, setForm] = useState<AgentSettingsDraft>(() =>
    initialSettings ? agentDraftFromPayload(initialSettings) : DEFAULT_AGENT_SETTINGS_DRAFT,
  );

  const text = useCallback(
    (key: string, fallback: string, options?: Record<string, unknown>) =>
      t(key, { defaultValue: fallback, ...(options ?? {}) }),
    [t],
  );

  const modelDirtyRef = useRef(false);
  const speechDirtyRef = useRef({ transcription: false, voice: false });

  const applyPayload = useCallback((payload: SettingsPayload, options?: { syncForm?: boolean; speechSaved?: boolean }) => {
    const syncForm = options?.syncForm !== false;
    setSettings(payload);
    if (syncForm) {
      setForm(agentDraftFromPayload(payload));
    }
    setWebSearchForm((prev) => webSearchFormFromPayload(payload, prev));
    setImageGenerationForm(imageGenerationFormFromPayload(payload));
    setVideoGenerationForm(videoGenerationFormFromPayload(payload));
    if (options?.speechSaved || !speechDirtyRef.current.transcription) {
      setTranscriptionForm(transcriptionFormFromPayload(payload));
    }
    setMusicGenerationForm(musicGenerationFormFromPayload(payload));
    if (options?.speechSaved || !speechDirtyRef.current.voice) {
      setVoiceForm(voiceFormFromPayload(payload));
    }
    setNetworkSafetyForm(networkSafetyFormFromPayload(payload));
    if (payload.restart_required_sections) {
      setPendingRestartSections(pendingRestartSectionsFromPayload(payload));
    }
    onSettingsChange?.(payload);
  }, [onSettingsChange]);

  const applyPayloadRef = useRef(applyPayload);
  applyPayloadRef.current = applyPayload;

  useEffect(() => {
    if (!initialSettings || settings !== null) return;
    applyPayloadRef.current(initialSettings);
    setLoading(false);
  }, [initialSettings, settings]);

  useEffect(() => {
    let cancelled = false;
    const showLoading = settings === null && !initialSettings;
    if (showLoading) setLoading(true);
    fetchSettings(token, "", { syncCatalog: true })
      .then((payload) => {
        if (!cancelled) {
          applyPayloadRef.current(payload, { syncForm: !modelDirtyRef.current });
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled && showLoading) setError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const hasSettings = settings !== null;
  // Both sections mount the token usage table, so both need the refresh:
  // Account used to keep whatever figures were current when Settings opened.
  const usageIsVisible = activeSection === "overview" || activeSection === "account";
  useEffect(() => {
    if (!usageIsVisible || !hasSettings) return;
    let cancelled = false;
    const refresh = () => {
      fetchSettingsUsage(token)
        .then((usage) => {
          if (cancelled) return;
          setSettings((current) => {
            if (!current) return current;
            try {
              if (JSON.stringify(current.usage) === JSON.stringify(usage)) {
                return current;
              }
            } catch {
              // Fall through and apply the fresh payload.
            }
            return { ...current, usage };
          });
        })
        .catch(() => {});
    };
    void refresh();
    const interval = window.setInterval(refresh, 5000);
    const onFocus = () => refresh();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [usageIsVisible, hasSettings, token]);

  useEffect(() => {
    if (activeSection !== "apps" && activeSection !== "tools") return;
    let cancelled = false;
    // MCP catalogue first: presets are static and cheap; CLI can refresh in background.
    setMcpPresetsLoading(true);
    fetchMcpPresets(token)
      .then((payload) => {
        if (!cancelled) {
          setMcpPresets(payload);
          setMcpError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setMcpError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setMcpPresetsLoading(false);
      });

    let retry: number | null = null;
    let retryCount = 0;
    const loadCliApps = (showLoading: boolean) => {
      if (showLoading) setCliAppsLoading(true);
      fetchCliApps(token)
        .then((payload) => {
          if (cancelled) return;
          if (payload.catalog_refresh_pending && retryCount < CLI_APPS_REFRESH_MAX_RETRIES) {
            retryCount += 1;
            retry = window.setTimeout(() => {
              retry = null;
              loadCliApps(false);
            }, CLI_APPS_REFRESH_RETRY_MS);
          }
          setCliApps(payload);
          setCliAppsError(null);
          setCliAppsLoading(false);
        })
        .catch((err) => {
          if (!cancelled) {
            setCliAppsError((err as Error).message);
            setCliAppsLoading(false);
          }
        });
    };
    loadCliApps(true);
    return () => {
      cancelled = true;
      if (retry !== null) window.clearTimeout(retry);
    };
  }, [activeSection, token]);

  useEffect(() => {
    if (activeSection !== "models") return;
    let cancelled = false;
    fetchSettings(token, "", { syncCatalog: true })
      .then((payload) => {
        if (cancelled) return;
        // Catalog sync must not wipe Provider / Thinking edits mid-flight.
        applyPayloadRef.current(payload, { syncForm: !modelDirtyRef.current });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeSection, token]);

  useEffect(() => {
    if (!["tools", "channels", "models", "providers", "browser"].includes(activeSection)) return;
    let cancelled = false;
    setNavinFeaturesLoading(true);
    fetchNavinFeatures(token)
      .then((payload) => {
        if (!cancelled) {
          setNavinFeatures(payload);
          setNavinFeaturesError(null);
        }
      })
      .catch((err) => {
        const message = (err as Error).message;
        if (!cancelled && message !== "HTTP 404") setNavinFeaturesError(message);
      })
      .finally(() => {
        if (!cancelled) setNavinFeaturesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSection, token]);

  const refreshAutomations = useCallback(
    async (showLoading = false) => {
      if (showLoading) setAutomationsLoading(true);
      try {
        const payload = await fetchAutomations(token);
        setAutomations(payload);
        setAutomationsError(null);
      } catch (err) {
        setAutomationsError((err as Error).message);
      } finally {
        if (showLoading) setAutomationsLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    if (activeSection !== "automations") return;
    let cancelled = false;
    const refresh = async (showLoading = false) => {
      if (cancelled) return;
      if (showLoading) setAutomationsLoading(true);
      try {
        const payload = await fetchAutomations(token);
        if (cancelled) return;
        setAutomations(payload);
        setAutomationsError(null);
      } catch (err) {
        if (!cancelled) setAutomationsError((err as Error).message);
      } finally {
        if (!cancelled && showLoading) setAutomationsLoading(false);
      }
    };
    void refresh(true);
    const interval = window.setInterval(() => void refresh(false), 5000);
    const refreshOnFocus = () => {
      if (document.visibilityState !== "hidden") void refresh(false);
    };
    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshOnFocus);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshOnFocus);
    };
  }, [activeSection, token]);

  useEffect(() => {
    writeLocalPreferences(localPrefs);
  }, [localPrefs]);

  useEffect(() => {
    if (!settings) return;
    setProviderForms((prev) => {
      const next = { ...prev };
      for (const provider of settings.providers) {
        const previous = next[provider.name];
        next[provider.name] = {
          apiKey: previous?.apiKey ?? "",
          apiBase: previous?.apiBase ?? provider.api_base ?? provider.resolved_api_base ?? provider.default_api_base ?? "",
          apiType: previous?.apiType ?? provider.api_type ?? "auto",
          authMode: previous?.authMode ?? provider.auth_mode ?? "none",
          endpointRegion: previous?.endpointRegion ?? provider.endpoint_region ?? provider.connection?.default_region ?? "",
          accessPlan: previous?.accessPlan ?? provider.access_plan ?? provider.connection?.default_plan ?? "",
          wireProtocol: previous?.wireProtocol ?? provider.wire_protocol ?? provider.connection?.default_protocol ?? "",
        };
      }
      return next;
    });
  }, [settings]);

  const modelDirty = useMemo(() => {
    if (!settings) return false;
    const activePresetName = modelPresetValue(settings);
    const selectedPreset = settings.model_presets.find((preset) => preset.name === form.modelPreset);
    if (!selectedPreset) return form.modelPreset !== activePresetName;
    const selectedProvider = selectedPreset.is_default
      ? editableDefaultProvider(settings)
      : selectedPreset.provider;
    return (
      form.modelPreset !== activePresetName ||
      form.model !== selectedPreset.model ||
      form.provider !== selectedProvider ||
      form.contextWindowTokens !== normalizeContextWindowTokens(selectedPreset.context_window_tokens) ||
      form.reasoningEffort !== (selectedPreset.reasoning_effort ?? "") ||
      (!selectedPreset.is_default && form.presetLabel.trim() !== selectedPreset.label)
    );
  }, [form, settings]);
  modelDirtyRef.current = modelDirty;

  const runtimeDirty = useMemo(() => {
    if (!settings) return false;
    return form.timezone !== settings.agent.timezone;
  }, [form, settings]);

  const imageGenerationDirty = useMemo(() => {
    if (!settings) return false;
    return (
      imageGenerationForm.enabled !== settings.image_generation.enabled ||
      imageGenerationForm.provider !== settings.image_generation.provider ||
      imageGenerationForm.model !== settings.image_generation.model ||
      imageGenerationForm.defaultAspectRatio !== settings.image_generation.default_aspect_ratio ||
      imageGenerationForm.defaultImageSize !== settings.image_generation.default_image_size ||
      imageGenerationForm.maxImagesPerTurn !== settings.image_generation.max_images_per_turn
    );
  }, [imageGenerationForm, settings]);

  const videoGenerationDirty = useMemo(() => {
    if (!settings) return false;
    const video = settings.video_generation ?? DEFAULT_VIDEO_GENERATION_SETTINGS;
    return (
      videoGenerationForm.enabled !== video.enabled ||
      videoGenerationForm.provider !== video.provider ||
      videoGenerationForm.model !== video.model ||
      videoGenerationForm.defaultAspectRatio !== video.default_aspect_ratio ||
      videoGenerationForm.defaultDurationSeconds !== video.default_duration_seconds ||
      videoGenerationForm.defaultResolution !== (video.default_resolution ?? "")
    );
  }, [settings, videoGenerationForm]);

  const transcriptionDirty = useMemo(() => {
    if (!settings) return false;
    const transcription = settings.transcription ?? DEFAULT_TRANSCRIPTION_SETTINGS;
    return (
      transcriptionForm.enabled !== transcription.enabled ||
      transcriptionForm.provider !== transcription.provider ||
      transcriptionForm.model !== transcription.model ||
      transcriptionForm.language !== (transcription.language ?? "") ||
      transcriptionForm.maxDurationSec !== transcription.max_duration_sec ||
      transcriptionForm.maxUploadMb !== transcription.max_upload_mb
    );
  }, [settings, transcriptionForm]);

  const musicGenerationDirty = useMemo(() => {
    if (!settings) return false;
    const music = settings.music_generation ?? DEFAULT_MUSIC_GENERATION_SETTINGS;
    return (
      musicGenerationForm.enabled !== music.enabled ||
      musicGenerationForm.provider !== music.provider ||
      musicGenerationForm.model !== music.model
    );
  }, [settings, musicGenerationForm]);

  const voiceDirty = useMemo(() => {
    if (!settings) return false;
    const voice = settings.voice ?? DEFAULT_VOICE_SETTINGS;
    return (
      voiceForm.ttsProvider !== voice.tts_provider ||
      voiceForm.ttsModel !== voice.tts_model ||
      voiceForm.voice !== voice.voice ||
      voiceForm.autoSpeak !== voice.auto_speak ||
      voiceForm.responseFormat !== voice.response_format ||
      voiceForm.realtimeEnabled !== voice.realtime_enabled
    );
  }, [settings, voiceForm]);
  speechDirtyRef.current = { transcription: transcriptionDirty, voice: voiceDirty };

  const networkSafetyDirty = useMemo(() => {
    if (!settings) return false;
    const currentLocalServiceAccess =
      settings.advanced.webui_allow_local_service_access ?? settings.advanced.allow_local_preview_access ?? true;
    const currentDefaultAccess = visibleWebuiDefaultAccessMode(settings.advanced.webui_default_access_mode);
    return (
      networkSafetyForm.webuiAllowLocalServiceAccess !== currentLocalServiceAccess ||
      networkSafetyForm.webuiDefaultAccessMode !== currentDefaultAccess
    );
  }, [networkSafetyForm, settings]);

  const configuredModelProviderOptions = useMemo(
    () =>
      settings?.providers
        .filter((provider) => provider.configured && provider.model_selectable !== false)
        .map((provider) => ({ name: provider.name, label: provider.label })) ?? [],
    [settings],
  );

  /** Prefer the Models form selection, then the saved agent provider. */
  const pickConfiguredModelProvider = useCallback(
    (...preferred: Array<string | null | undefined>) => {
      const candidates = [
        ...preferred,
        form.provider,
        settings?.agent.provider,
        settings?.agent.resolved_provider,
      ];
      for (const name of candidates) {
        const trimmed = (name ?? "").trim();
        if (!trimmed || trimmed === "auto") continue;
        const match = configuredModelProviderOptions.find((option) => option.name === trimmed);
        if (match) return match.name;
      }
      return configuredModelProviderOptions[0]?.name ?? "";
    },
    [
      configuredModelProviderOptions,
      form.provider,
      settings?.agent.provider,
      settings?.agent.resolved_provider,
    ],
  );

  const restartViaSettingsSurface = useCallback(async () => {
    const isNativeHost = (settings?.surface ?? settings?.runtime_surface) === "native";
    if (
      isNativeHost &&
      settings?.runtime_capabilities?.can_restart_engine &&
      onNativeEngineRestart
    ) {
      setHostEngineApplying(true);
      try {
        const nextToken = await onNativeEngineRestart();
        const payload = await fetchSettings(nextToken);
        applyPayload(payload);
        setPendingRestartSections(EMPTY_PENDING_RESTART_SECTIONS);
        setError(null);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setHostEngineApplying(false);
      }
      return;
    }
    onRestart?.();
  }, [applyPayload, onNativeEngineRestart, onRestart, settings]);

  const maybeRestartHostEngine = useCallback(
    async (payload: RestartAwarePayload) => {
      const surface = payload.surface ?? payload.runtime_surface ?? settings?.surface ?? settings?.runtime_surface;
      const capabilities = payload.runtime_capabilities ?? settings?.runtime_capabilities;
      const isNativeHost = surface === "native";
      if (
        !payload.requires_restart ||
        !isNativeHost ||
        !capabilities?.can_restart_engine ||
        !onNativeEngineRestart
      ) {
        return;
      }
      setHostEngineApplying(true);
      try {
        const nextToken = await onNativeEngineRestart();
        const refreshed = await fetchSettings(nextToken);
        applyPayload(refreshed);
        setPendingRestartSections(EMPTY_PENDING_RESTART_SECTIONS);
        setError(null);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setHostEngineApplying(false);
      }
    },
    [applyPayload, onNativeEngineRestart, settings],
  );

  const saveModelSettings = async () => {
    if (!settings || !modelDirty || saving) return;
    setSaving(true);
    try {
      const selectedPreset = settings.model_presets.find((preset) => preset.name === form.modelPreset);
      let payload: SettingsPayload;
      if (selectedPreset && !selectedPreset.is_default) {
        const isLocked = Boolean(selectedPreset.routing_only);
        // Router aliases (main / expert) stay identity-locked. Imported and
        // catalog chat rows can change model, e.g. Sonnet 5 → Sonnet 4.5.
        if (isLocked) {
          // Identity stays locked, but Thinking / context window are user prefs.
          const effortChanged =
            form.reasoningEffort !== (selectedPreset.reasoning_effort ?? "");
          const contextChanged =
            form.contextWindowTokens !==
            normalizeContextWindowTokens(selectedPreset.context_window_tokens);
          if (effortChanged || contextChanged) {
            payload = await updateModelConfiguration(token, {
              name: selectedPreset.name,
              ...(contextChanged
                ? { contextWindowTokens: form.contextWindowTokens }
                : {}),
              ...(effortChanged ? { reasoningEffort: form.reasoningEffort } : {}),
            });
          } else {
            payload = await updateSettings(token, { modelPreset: selectedPreset.name });
          }
          if (payload.agent.model_preset !== selectedPreset.name) {
            payload = await updateSettings(token, { modelPreset: selectedPreset.name });
          }
        } else {
          payload = await updateModelConfiguration(token, {
            name: selectedPreset.name,
            label: form.presetLabel.trim(),
            model: form.model,
            provider: form.provider,
            ...(form.contextWindowTokens !== selectedPreset.context_window_tokens
              ? { contextWindowTokens: form.contextWindowTokens }
              : {}),
            ...(form.reasoningEffort !== (selectedPreset.reasoning_effort ?? "")
              ? { reasoningEffort: form.reasoningEffort }
              : {}),
          });
        }
      } else {
        const selectedModality = selectedPreset?.modality ?? "text";
        if (isMediaModality(selectedModality)) {
          setError(
            t("settings.models.mediaCannotBeDefault", {
              defaultValue: "Media models (image, video, audio, music, voice input) cannot be the chat default.",
            }),
          );
          return;
        }
        const defaultModel = defaultPreset(settings)?.model ?? settings.agent.model;
        const defaultProvider = editableDefaultProvider(settings);
        const defaultContextWindowTokens = normalizeContextWindowTokens(
          defaultPreset(settings)?.context_window_tokens ?? settings.agent.context_window_tokens,
        );
        const defaultReasoningEffort =
          defaultPreset(settings)?.reasoning_effort ?? settings.agent.reasoning_effort ?? "";
        payload = await updateSettings(token, {
          modelPreset: form.modelPreset,
          ...(form.model !== defaultModel ? { model: form.model } : {}),
          ...(form.provider !== defaultProvider ? { provider: form.provider } : {}),
          ...(form.contextWindowTokens !== defaultContextWindowTokens
            ? { contextWindowTokens: form.contextWindowTokens }
            : {}),
          ...(form.reasoningEffort !== defaultReasoningEffort
            ? { reasoningEffort: form.reasoningEffort }
            : {}),
        });
      }
      applyPayload(payload);
      onModelNameChange(payload.agent.model || null);
      if (activeModelConfigured(payload)) {
        setModelSetupPromptProvider(null);
      }
      setError(null);
      setReasoningEffortError(null);
    } catch (err) {
      const message = (err as Error).message;
      if (isReasoningEffortError(message)) {
        setReasoningEffortError(message);
        setError(null);
      } else {
        setError(message);
      }
    } finally {
      setSaving(false);
    }
  };

  // The refusal is about the level the user picked for this provider+model;
  // it stops being true as soon as any of the three changes.
  useEffect(() => {
    setReasoningEffortError(null);
  }, [form.reasoningEffort, form.provider, form.model, form.modelPreset]);

  const handleUpdateModelRoute = async (role: string, preset: string) => {
    try {
      const payload = await updateModelRoute(token, role, preset);
      applyPayload(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const openModelConfigurationDialog = () => {
    if (!settings) return;
    const provider = pickConfiguredModelProvider(form.provider);
    setModelConfigurationForm({
      label: "",
      provider,
      model: "",
    });
    setModelConfigurationOpen(true);
  };

  const handleCreateModelConfiguration = async () => {
    if (modelConfigurationSaving) return;
    const label = modelConfigurationForm.label.trim();
    const provider = modelConfigurationForm.provider.trim();
    const model = modelConfigurationForm.model.trim();
    if (!label || !provider || !model) return;
    setModelConfigurationSaving(true);
    try {
      const payload = await createModelConfiguration(token, {
        label,
        provider,
        model,
      });
      applyPayload(payload);
      onModelNameChange(payload.agent.model || null);
      setModelConfigurationOpen(false);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setModelConfigurationSaving(false);
    }
  };

  const openModelImportDialog = () => {
    if (!settings) return;
    setModelImportProvider(pickConfiguredModelProvider(form.provider));
    setModelImportProgress(null);
    setModelImportOpen(true);
  };

  const handleImportModelConfigurations = async (
    provider: string,
    models: ModelConfigurationImportEntry[],
  ) => {
    if (modelImportSaving || !provider || models.length === 0) return;
    // Long selections are split so each request stays under the HTTP URL limit.
    const batches = chunkModelImportEntries(models);
    setModelImportSaving(true);
    setModelImportProgress({ done: 0, total: batches.length });
    try {
      for (const [index, batch] of batches.entries()) {
        const payload = await importModelConfigurations(token, provider, batch);
        applyPayload(payload);
        setModelImportProgress({ done: index + 1, total: batches.length });
      }
      setModelImportOpen(false);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setModelImportSaving(false);
      setModelImportProgress(null);
    }
  };

  const handleDeleteModelConfiguration = async (name: string) => {
    try {
      const payload = await deleteModelConfiguration(token, name);
      applyPayload(payload);
      onModelNameChange(payload.agent.model || null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleToggleModelConfigurationEnabled = async (
    name: string,
    enabled: boolean,
  ) => {
    try {
      const payload = await updateModelConfiguration(token, { name, enabled });
      applyPayload(payload);
      onModelNameChange(payload.agent.model || null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleSetDefaultModelConfiguration = async (name: string) => {
    try {
      const payload = await updateSettings(token, { modelPreset: name });
      applyPayload(payload);
      onModelNameChange(payload.agent.model || null);
      if (activeModelConfigured(payload)) {
        setModelSetupPromptProvider(null);
      }
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const saveRuntimeSettings = async () => {
    if (!settings || !runtimeDirty || saving) return;
    setSaving(true);
    try {
      const payload = await updateSettings(token, {
        timezone: form.timezone,
      });
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
      await onWorkspaceSettingsChange?.();
      await maybeRestartHostEngine(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saveImageGenerationSettings = async () => {
    if (!settings || !imageGenerationDirty || imageGenerationSaving) return;
    setImageGenerationSaving(true);
    try {
      const payload = await updateImageGenerationSettings(token, imageGenerationForm);
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, image: true }));
      }
      await maybeRestartHostEngine(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setImageGenerationSaving(false);
    }
  };

  const saveVideoGenerationSettings = async () => {
    if (!settings || !videoGenerationDirty || videoGenerationSaving) return;
    setVideoGenerationSaving(true);
    try {
      const payload = await updateVideoGenerationSettings(token, videoGenerationForm);
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, video: true }));
      }
      await maybeRestartHostEngine(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setVideoGenerationSaving(false);
    }
  };

  const saveMusicGenerationSettings = async () => {
    if (!settings || !musicGenerationDirty || musicGenerationSaving) return;
    setMusicGenerationSaving(true);
    try {
      const payload = await updateMusicGenerationSettings(token, musicGenerationForm);
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, browser: true }));
      }
      await maybeRestartHostEngine(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMusicGenerationSaving(false);
    }
  };

  const saveLiveSpeechSettings = async (returnToChat = false) => {
    if (!settings || voiceSaving) return;
    if (!voiceDirty && !transcriptionDirty) {
      if (returnToChat) onBackToChat();
      return;
    }
    setVoiceSaving(true);
    try {
      const payload = await updateLiveVoiceSettings(token, transcriptionForm, voiceForm);
      applyPayload(payload, { speechSaved: true });
      setError(null);
      if (returnToChat) onBackToChat();
    } catch {
      setError(t("settings.live.saveFailed"));
    } finally {
      setVoiceSaving(false);
    }
  };

  const saveNetworkSafetySettings = async () => {
    if (!settings || !networkSafetyDirty || networkSafetySaving) return;
    setNetworkSafetySaving(true);
    try {
      const payload = await updateNetworkSafetySettings(token, networkSafetyForm);
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
      await maybeRestartHostEngine(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setNetworkSafetySaving(false);
    }
  };

  const installCapabilities = async (names: string[]): Promise<boolean> => {
    const missing = names.filter(
      (name) => !featureCatalog.find((feature) => feature.name === name)?.installed,
    );
    if (!missing.length) return true;
    setNavinFeatureAction(`enable:${names.join("+")}`);
    setNavinFeaturesError(null);
    try {
      let latest = navinFeatures;
      for (const name of missing) {
        latest = await enableNavinFeature(token, name);
        if (latest.requires_restart) {
          setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
        }
      }
      if (latest) setNavinFeatures(latest);
      return true;
    } catch (err) {
      setNavinFeaturesError((err as Error).message);
      return false;
    } finally {
      setNavinFeatureAction(null);
    }
  };

  const saveProvider = async (providerName: string) => {
    if (providerSaving) return;
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider) return;
    if (provider.auth_type === "oauth") return;
    const providerForm = providerForms[providerName] ?? emptyProviderForm();
    const apiKey = providerForm.apiKey.trim();
    const apiKeyRequired = provider.api_key_required ?? true;
    const authMode = provider.supports_auth_mode
      ? (providerForm.authMode ?? provider.auth_mode ?? "none")
      : undefined;
    if (!provider.configured && apiKeyRequired && !apiKey && authMode !== "none") {
      setError(t("settings.byok.apiKeyRequired"));
      return;
    }
    setProviderSaving(providerName);
    try {
      const supportName = providerName === "bedrock"
        ? "bedrock"
        : providerName === "azure_openai"
          ? "azure"
          : null;
      if (supportName && !(await installCapabilities([supportName]))) return;
      const payload = await updateProviderSettings(token, {
        provider: providerName,
        apiKey: authMode === "none" ? "" : apiKey || undefined,
        apiBase: providerForm.apiBase.trim(),
        apiType: providerForm.apiType,
        ...(authMode ? { authMode } : {}),
        ...(provider.connection
          ? {
              endpointRegion: providerForm.endpointRegion,
              accessPlan: providerForm.accessPlan,
              wireProtocol: providerForm.wireProtocol,
            }
          : {}),
      });
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, image: true }));
      }
      await maybeRestartHostEngine(payload);
      setProviderForms((prev) => ({
        ...prev,
        [providerName]: {
          apiKey: "",
          apiBase: providerForm.apiBase.trim(),
          apiType: providerForm.apiType,
          authMode: authMode ?? providerForm.authMode ?? ("none" as ProviderAuthMode),
          endpointRegion: providerForm.endpointRegion,
          accessPlan: providerForm.accessPlan,
          wireProtocol: providerForm.wireProtocol,
        },
      }));
      setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setError(null);
      // Provider prêt mais aucun modèle : redirect Models + dialog (BYOK).
      // Le rappel (bannière) reste ensuite tant qu'aucun modèle n'est choisi.
      const savedProvider = payload.providers.find((item) => item.name === providerName);
      if (savedProvider?.configured && !activeModelConfigured(payload)) {
        setForm((prev) => ({
          ...prev,
          provider: providerName,
          model: prev.provider === providerName ? prev.model : "",
        }));
        selectSection("models");
        if (!isManagedSubscriber(account)) {
          setModelSetupPromptProvider(providerName);
        }
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setProviderSaving(null);
    }
  };

  const disconnectProvider = async (providerName: string) => {
    if (providerSaving) return;
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider || provider.auth_type === "oauth") return;
    setProviderSaving(providerName);
    try {
      // Clé et endpoint sont effacés explicitement : le backend interprète
      // une valeur vide comme une suppression.
      const payload = await updateProviderSettings(token, {
        provider: providerName,
        apiKey: "",
        apiBase: "",
        ...(provider.supports_auth_mode ? { authMode: "none" as const } : {}),
        ...(provider.connection
          ? { endpointRegion: "", accessPlan: "", wireProtocol: "" }
          : {}),
      });
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, image: true }));
      }
      await maybeRestartHostEngine(payload);
      setProviderForms((prev) => ({
        ...prev,
        [providerName]: emptyProviderForm(),
      }));
      setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setProviderSaving(null);
    }
  };

  /**
   * The server can no longer complete a browser sign-in on its own: it hands
   * back the authorize URL, we open it here (the packaged Linux app has no
   * usable `webbrowser`), then poll until the callback lands.
   */
  const finishOAuthLogin = async (
    providerName: string,
    started: SettingsPayload,
  ): Promise<SettingsPayload> => {
    let state = started.oauth;
    if (!state || state.status !== "pending") return started;
    if (state.authorize_url) {
      setOauthAuthorizeUrl(state.authorize_url);
      await openExternalUrl(token, state.authorize_url).catch(() => undefined);
    }
    const deadline = Date.now() + 5 * 60 * 1000;
    let payload = started;
    while (state?.status === "pending" && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      payload = await pollProviderOAuth(token, providerName);
      state = payload.oauth;
      if (state?.authorize_url) setOauthAuthorizeUrl(state.authorize_url);
    }
    if (state?.status === "error") throw new Error(state.error || "OAuth login failed");
    if (state?.status === "pending") throw new Error("Sign-in timed out. Try again.");
    return payload;
  };

  const runProviderOAuth = async (providerName: string, action: "login" | "logout") => {
    if (providerSaving) return;
    setProviderSaving(providerName);
    setOauthAuthorizeUrl(null);
    try {
      let payload =
        action === "login"
          ? await loginProviderOAuth(token, providerName)
          : await logoutProviderOAuth(token, providerName);
      if (action === "login") payload = await finishOAuthLogin(providerName, payload);
      applyPayload(payload);
      setExpandedProvider(providerName);
      setError(null);
      // Connexion réussie mais aucun modèle : redirect Models + dialog (BYOK).
      const loggedIn =
        action === "login" &&
        payload.providers.find((item) => item.name === providerName)?.configured;
      if (loggedIn && !activeModelConfigured(payload)) {
        setForm((prev) => ({
          ...prev,
          provider: providerName,
          model: prev.provider === providerName ? prev.model : "",
        }));
        selectSection("models");
        if (!isManagedSubscriber(account)) {
          setModelSetupPromptProvider(providerName);
        }
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setProviderSaving(null);
    }
  };

  const saveWebSearch = async () => {
    if (!settings || webSearchSaving) return;
    const provider = settings.web_search.providers.find((item) => item.name === webSearchForm.provider);
    if (!provider) return;
    const apiKey = webSearchForm.apiKey?.trim() ?? "";
    const baseUrl = webSearchForm.baseUrl?.trim() ?? "";
    const hasExistingSecret =
      webSearchProviderAcceptsApiKey(provider) &&
      webSearchForm.provider === settings.web_search.provider &&
      !!settings.web_search.api_key_hint;

    if (webSearchProviderRequiresApiKey(provider) && !apiKey && !hasExistingSecret) {
      setError(t("settings.byok.webSearch.apiKeyRequired"));
      return;
    }
    if (provider.credential === "base_url" && !baseUrl) {
      setError(t("settings.byok.webSearch.baseUrlRequired"));
      return;
    }

    setWebSearchSaving(true);
    try {
      if (provider.name === "olostep" && !(await installCapabilities(["olostep"]))) return;
      const webFetchRestartRequired =
        (webSearchForm.useJinaReader ?? settings.web.fetch.use_jina_reader) !==
        settings.web.fetch.use_jina_reader;
      const update: WebSearchSettingsUpdate = {
        provider: webSearchForm.provider,
        maxResults: webSearchForm.maxResults,
        timeout: webSearchForm.timeout,
        useJinaReader: webSearchForm.useJinaReader,
      };
      if (
        webSearchProviderAcceptsApiKey(provider) &&
        (apiKey || (provider.credential === "optional_api_key" && webSearchKeyEditing))
      ) {
        update.apiKey = apiKey;
      }
      if (provider.credential === "base_url") update.baseUrl = baseUrl;
      const payload = await updateWebSearchSettings(token, update);
      applyPayload(payload);
      if (payload.requires_restart || webFetchRestartRequired) {
        setPendingRestartSections((prev) => ({ ...prev, browser: true }));
      }
      await maybeRestartHostEngine(payload);
      setWebSearchForm((prev) => ({
        provider: payload.web_search.provider,
        apiKey: "",
        baseUrl: payload.web_search.base_url ?? prev.baseUrl ?? "",
        maxResults: payload.web_search.max_results,
        timeout: payload.web_search.timeout,
        useJinaReader: payload.web.fetch.use_jina_reader,
      }));
      setWebSearchKeyVisible(false);
      setWebSearchKeyEditing(false);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWebSearchSaving(false);
    }
  };

  const resetProviderDraft = useCallback((providerName: string) => {
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider) return;
    setProviderForms((prev) => ({
      ...prev,
      [providerName]: {
        apiKey: "",
        apiBase: provider.api_base ?? provider.resolved_api_base ?? provider.default_api_base ?? "",
        apiType: provider.api_type ?? "auto",
        authMode: provider.auth_mode ?? "none",
        endpointRegion: provider.endpoint_region ?? provider.connection?.default_region ?? "",
        accessPlan: provider.access_plan ?? provider.connection?.default_plan ?? "",
        wireProtocol: provider.wire_protocol ?? provider.connection?.default_protocol ?? "",
      },
    }));
    setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
    setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
  }, [settings]);

  const handleToggleProvider = useCallback((providerName: string) => {
    if (expandedProvider) resetProviderDraft(expandedProvider);
    setExpandedProvider(expandedProvider === providerName ? null : providerName);
  }, [expandedProvider, resetProviderDraft]);

  const resetWebSearchDraft = useCallback(() => {
    if (!settings) return;
    setWebSearchForm({
      provider: settings.web_search.provider,
      apiKey: "",
      baseUrl: settings.web_search.base_url ?? "",
      maxResults: settings.web_search.max_results,
      timeout: settings.web_search.timeout,
      useJinaReader: settings.web.fetch.use_jina_reader,
    });
    setWebSearchKeyVisible(false);
    setWebSearchKeyEditing(false);
  }, [settings]);

  const handleWebSearchProviderChange = useCallback((provider: string) => {
    if (!settings) return;
    setWebSearchForm((prev) => ({
      provider,
      apiKey: "",
      baseUrl: provider === settings.web_search.provider ? settings.web_search.base_url ?? "" : "",
      maxResults: prev.maxResults ?? settings.web_search.max_results,
      timeout: prev.timeout ?? settings.web_search.timeout,
      useJinaReader: prev.useJinaReader ?? settings.web.fetch.use_jina_reader,
    }));
    setWebSearchKeyVisible(false);
    setWebSearchKeyEditing(false);
  }, [settings]);

  const toggleProviderKeyVisibility = (providerName: string) => {
    const isVisible = visibleProviderKeys[providerName];
    setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: !isVisible }));
  };

  const toggleProviderKeyEditing = (providerName: string) => {
    setEditingProviderKeys((prev) => {
      const nextEditing = !prev[providerName];
      if (!nextEditing) {
        setProviderForms((forms) => ({
          ...forms,
          [providerName]: {
            ...emptyProviderForm(),
            ...forms[providerName],
            apiKey: "",
          },
        }));
        setVisibleProviderKeys((visible) => ({ ...visible, [providerName]: false }));
      }
      return { ...prev, [providerName]: nextEditing };
    });
  };

  const handleCliAppAction = async (
    action: "install" | "update" | "uninstall" | "test",
    name: string,
  ) => {
    const key = `${action}:${name}`;
    setCliAppsAction(key);
    setCliAppsMessage(null);
    setCliAppsError(null);
    try {
      const payload = await runCliAppAction(token, action, name);
      setCliApps(payload);
      if (action !== "test") {
        notifyCliAppsChanged(payload);
      }
      setCliAppsMessage(payload.last_action?.message ?? null);
      setCliAppsFocusName(action === "uninstall" ? null : name);
    } catch (err) {
      setCliAppsError((err as Error).message);
    } finally {
      setCliAppsAction(null);
    }
  };

  const handleNavinFeatureAction = async (
    action: "enable" | "disable",
    name: string,
    confirmed = false,
  ) => {
    const feature = featureCatalog.find((item) => item.name === name);
    if (action === "enable" && !confirmed && feature && !feature.installed && feature.install_supported) {
      setNavinFeaturesError(null);
      setNavinFeatureConfirm(feature);
      return;
    }
    const key = `${action}:${name}`;
    setNavinFeatureAction(key);
    setNavinFeatureConfirm(null);
    setNavinFeaturesError(null);
    try {
      const payload = action === "enable"
        ? await enableNavinFeature(token, name)
        : await disableNavinFeature(token, name);
      setNavinFeatures(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
    } catch (err) {
      setNavinFeaturesError((err as Error).message);
    } finally {
      setNavinFeatureAction(null);
    }
  };

  const handleAutomationAction = async (
    action: AutomationAction,
    job: SessionAutomationJob,
  ) => {
    const key = `${action}:${job.id}`;
    setAutomationAction(key);
    setAutomationsError(null);
    try {
      const payload = await runAutomationAction(token, action, job.id);
      setAutomations(payload);
      if (action === "delete") setAutomationPendingDelete(null);
      if (action === "run") {
        window.setTimeout(() => void refreshAutomations(false), 1200);
        window.setTimeout(() => void refreshAutomations(false), 4000);
      }
    } catch (err) {
      setAutomationsError((err as Error).message);
    } finally {
      setAutomationAction(null);
    }
  };

  const handleAutomationEdit = async (
    job: SessionAutomationJob,
    values: AutomationUpdatePayload,
  ) => {
    const key = `update:${job.id}`;
    setAutomationAction(key);
    setAutomationsError(null);
    try {
      const payload = await updateAutomation(token, job.id, values);
      setAutomations(payload);
      setAutomationPendingEdit(null);
    } catch (err) {
      setAutomationsError((err as Error).message);
    } finally {
      setAutomationAction(null);
    }
  };

  const handleMcpPresetAction = async (
    action: "enable" | "remove" | "test",
    name: string,
    values: Record<string, string> = {},
  ) => {
    const key = `${action}:${name}`;
    setMcpPresetAction(key);
    setMcpMessage(null);
    setMcpError(null);
    try {
      const payload = await runMcpPresetAction(token, action, name, values);
      setMcpPresets(payload);
      setMcpMessage(payload.last_action?.message ?? null);
      if (action !== "test") {
        notifyMcpPresetsChanged(payload);
      }
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
      await maybeRestartHostEngine(payload);
      if (action === "enable") {
        setMcpFieldValues((prev) => ({ ...prev, [name]: {} }));
      }
    } catch (err) {
      setMcpError((err as Error).message);
    } finally {
      setMcpPresetAction(null);
    }
  };

  const handleSaveCustomMcp = async () => {
    const name = customMcpForm.name.trim();
    const key = `custom:${name || "new"}`;
    setMcpPresetAction(key);
    setMcpMessage(null);
    setMcpError(null);
    try {
      const payload = await saveCustomMcpServer(token, {
        name,
        transport: customMcpForm.transport,
        command: customMcpForm.command,
        args: customMcpForm.args,
        url: customMcpForm.url,
        env: customMcpForm.env,
        headers: customMcpForm.headers,
        tool_timeout: customMcpForm.toolTimeout,
      });
      setMcpPresets(payload);
      setMcpMessage(payload.last_action?.message ?? null);
      notifyMcpPresetsChanged(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
      await maybeRestartHostEngine(payload);
      setCustomMcpForm((prev) => ({ ...DEFAULT_CUSTOM_MCP_FORM, transport: prev.transport }));
    } catch (err) {
      setMcpError((err as Error).message);
    } finally {
      setMcpPresetAction(null);
    }
  };

  const handleImportMcpConfig = async () => {
    setMcpPresetAction("import");
    setMcpMessage(null);
    setMcpError(null);
    try {
      const payload = await importMcpConfig(token, mcpConfigImport);
      setMcpPresets(payload);
      setMcpMessage(payload.last_action?.message ?? null);
      notifyMcpPresetsChanged(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
      await maybeRestartHostEngine(payload);
      setMcpConfigImport("");
    } catch (err) {
      setMcpError((err as Error).message);
    } finally {
      setMcpPresetAction(null);
    }
  };

  const handleMcpToolsChange = async (name: string, enabledTools: string[]) => {
    setMcpPresetAction(`tools:${name}`);
    setMcpMessage(null);
    setMcpError(null);
    try {
      const payload = await updateMcpServerTools(token, name, enabledTools);
      setMcpPresets(payload);
      setMcpMessage(payload.last_action?.message ?? null);
      notifyMcpPresetsChanged(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, runtime: true }));
      }
      await maybeRestartHostEngine(payload);
    } catch (err) {
      setMcpError((err as Error).message);
    } finally {
      setMcpPresetAction(null);
    }
  };

  const renderSection = () => {
    if (!settings) return null;
    switch (activeSection) {
      case "overview":
        return (
          <OverviewSettings
            settings={settings}
            showBrandLogos={localPrefs.brandLogos}
            onSelectSection={selectSection}
          />
        );
      case "account":
        return (
          <AccountSettings
            onOpenProviders={() => selectSection("providers")}
            usage={settings.usage}
          />
        );
      case "appearance":
        return (
          <AppearanceSettings
            theme={theme}
            onToggleTheme={onToggleTheme}
            localPrefs={localPrefs}
            onChangeLocalPrefs={setLocalPrefs}
          />
        );
      case "providers":
        return (
          <ProvidersSettings
            token={token}
            settings={settings}
            navinFeatures={navinFeatures}
            featureAction={navinFeatureAction}
            capabilityError={navinFeaturesError}
            expandedProvider={expandedProvider}
            providerForms={providerForms}
            visibleProviderKeys={visibleProviderKeys}
            editingProviderKeys={editingProviderKeys}
            providerSaving={providerSaving}
            query={providerQuery}
            showBrandLogos={localPrefs.brandLogos}
            onQueryChange={setProviderQuery}
            onToggleProvider={handleToggleProvider}
            onToggleProviderKey={toggleProviderKeyVisibility}
            onToggleProviderKeyEditing={toggleProviderKeyEditing}
            onChangeProviderForm={(provider, value) =>
              setProviderForms((prev) => {
                const merged = {
                  apiKey: prev[provider]?.apiKey ?? "",
                  apiBase: prev[provider]?.apiBase ?? "",
                  apiType: prev[provider]?.apiType ?? ("auto" as ProviderApiType),
                  authMode: prev[provider]?.authMode ?? ("none" as ProviderAuthMode),
                  endpointRegion: prev[provider]?.endpointRegion ?? "",
                  accessPlan: prev[provider]?.accessPlan ?? "",
                  wireProtocol: prev[provider]?.wireProtocol ?? "",
                  ...value,
                };
                return {
                  ...prev,
                  [provider]: {
                    ...merged,
                    authMode: merged.authMode ?? "none",
                  },
                };
              })
            }
            onSaveProvider={saveProvider}
            onDisconnectProvider={disconnectProvider}
            oauthAuthorizeUrl={oauthAuthorizeUrl}
            onProviderOAuthLogin={(provider) => runProviderOAuth(provider, "login")}
            onProviderOAuthLogout={(provider) => runProviderOAuth(provider, "logout")}
            onResetProviderDraft={resetProviderDraft}
            onOllamaConfigured={applyPayload}
            imageProviderRestartPending={pendingRestartSections.image || pendingRestartSections.runtime}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            onOpenAccount={() => selectSection("account")}
          />
        );
      case "models":
        return (
          <ModelsSettings
            token={token}
            form={form}
            setForm={setForm}
            settings={settings}
            dirty={modelDirty}
            saving={saving}
            reasoningEffortError={reasoningEffortError}
            showBrandLogos={localPrefs.brandLogos}
            providerSaving={providerSaving}
            needsModelReminder={
              !isManagedSubscriber(account) &&
              !activeModelConfigured(settings) &&
              settings.providers.some((provider) => provider.configured)
            }
            oauthAuthorizeUrl={oauthAuthorizeUrl}
            onProviderOAuthLogin={(provider) => runProviderOAuth(provider, "login")}
            onSave={saveModelSettings}
            onCreateConfiguration={openModelConfigurationDialog}
            onImportConfigurations={openModelImportDialog}
            onDeleteConfiguration={handleDeleteModelConfiguration}
            onToggleConfigurationEnabled={handleToggleModelConfigurationEnabled}
            onSetDefaultConfiguration={handleSetDefaultModelConfiguration}
            onOpenProviders={() => selectSection("providers")}
            onOpenAccount={() => selectSection("account")}
            onUpdateModelRoute={handleUpdateModelRoute}
          />
        );
      case "image":
        return (
          <ImageGenerationSettings
            settings={settings}
            form={imageGenerationForm}
            dirty={imageGenerationDirty}
            saving={imageGenerationSaving}
            onChangeForm={setImageGenerationForm}
            onSave={saveImageGenerationSettings}
            onOpenProviders={() => selectSection("providers")}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.image}
          />
        );
      case "video":
        return (
          <VideoGenerationSettings
            settings={settings}
            form={videoGenerationForm}
            dirty={videoGenerationDirty}
            saving={videoGenerationSaving}
            onChangeForm={setVideoGenerationForm}
            onSave={saveVideoGenerationSettings}
            onOpenProviders={() => selectSection("providers")}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.video}
          />
        );
      case "voice":
        return (
          <TranscriptionSettings
            settings={settings}
            form={transcriptionForm}
            dirty={transcriptionDirty}
            saving={voiceSaving}
            onChangeForm={setTranscriptionForm}
            onSave={() => { void saveLiveSpeechSettings(); }}
            onSaveAndReturn={() => { void saveLiveSpeechSettings(true); }}
            onBackToChat={onBackToChat}
            onOpenAccount={() => selectSection("account")}
            voiceForm={voiceForm}
            voiceDirty={voiceDirty}
            onChangeVoiceForm={setVoiceForm}
            musicForm={musicGenerationForm}
            musicDirty={musicGenerationDirty}
            musicSaving={musicGenerationSaving}
            onChangeMusicForm={setMusicGenerationForm}
            onSaveMusic={saveMusicGenerationSettings}
            onOpenProviders={() => selectSection("providers")}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.browser}
          />
        );
      case "browser":
        return (
          <WebSettings
            settings={settings}
            form={webSearchForm}
            keyVisible={webSearchKeyVisible}
            keyEditing={webSearchKeyEditing}
            saving={webSearchSaving}
            onChangeForm={setWebSearchForm}
            onChangeProvider={handleWebSearchProviderChange}
            onToggleKey={() => setWebSearchKeyVisible((visible) => !visible)}
            onToggleKeyEditing={() => {
              setWebSearchKeyEditing((editing) => !editing);
              setWebSearchKeyVisible(false);
              setWebSearchForm((prev) => ({ ...prev, apiKey: "" }));
            }}
            onReset={resetWebSearchDraft}
            onSave={saveWebSearch}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.browser}
            olostepFeature={featureCatalog.find((feature) => feature.name === "olostep")}
            olostepInstalling={navinFeatureAction === "enable:olostep"}
            capabilityError={navinFeaturesError}
          />
        );
      case "tools":
        return (
          <ToolsSettings
            pane="mcp"
            token={token}
            navinFeatures={navinFeatures}
            loading={navinFeaturesLoading}
            query={channelsQuery}
            actionKey={navinFeatureAction}
            docsBaseUrl={settings.docs?.base_url}
            showBrandLogos={localPrefs.brandLogos}
            error={navinFeaturesError}
            requiresRestartPending={pendingRestartSections.runtime}
            onQueryChange={setChannelsQuery}
            onAction={handleNavinFeatureAction}
            onFeaturesUpdate={setNavinFeatures}
            onDismissStatus={() => {
              setNavinFeaturesError(null);
              setMcpMessage(null);
              setMcpError(null);
            }}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            mcpPresets={mcpPresets}
            mcpPresetsLoading={mcpPresetsLoading}
            mcpActionKey={mcpPresetAction}
            mcpMessage={mcpMessage}
            mcpError={mcpError}
            mcpFieldValues={mcpFieldValues}
            onMcpFieldChange={(presetName, fieldName, value) => {
              setMcpFieldValues((prev) => ({
                ...prev,
                [presetName]: {
                  ...(prev[presetName] ?? {}),
                  [fieldName]: value,
                },
              }));
            }}
            onMcpAction={handleMcpPresetAction}
            onMcpToolsChange={handleMcpToolsChange}
          />
        );
      case "channels":
        return (
          <ToolsSettings
            pane="channels"
            token={token}
            navinFeatures={navinFeatures}
            loading={navinFeaturesLoading}
            query={channelsQuery}
            actionKey={navinFeatureAction}
            docsBaseUrl={settings.docs?.base_url}
            showBrandLogos={localPrefs.brandLogos}
            error={navinFeaturesError}
            requiresRestartPending={pendingRestartSections.runtime}
            onQueryChange={setChannelsQuery}
            onAction={handleNavinFeatureAction}
            onFeaturesUpdate={setNavinFeatures}
            onDismissStatus={() => {
              setNavinFeaturesError(null);
              setMcpMessage(null);
              setMcpError(null);
            }}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            mcpPresets={mcpPresets}
            mcpPresetsLoading={mcpPresetsLoading}
            mcpActionKey={mcpPresetAction}
            mcpMessage={mcpMessage}
            mcpError={mcpError}
            mcpFieldValues={mcpFieldValues}
            onMcpFieldChange={(presetName, fieldName, value) => {
              setMcpFieldValues((prev) => ({
                ...prev,
                [presetName]: {
                  ...(prev[presetName] ?? {}),
                  [fieldName]: value,
                },
              }));
            }}
            onMcpAction={handleMcpPresetAction}
            onMcpToolsChange={handleMcpToolsChange}
          />
        );
      case "apps":
        return (
          <AppsCatalogSettings
            cliApps={cliApps}
            mcpPresets={mcpPresets}
            cliAppsLoading={cliAppsLoading}
            mcpPresetsLoading={mcpPresetsLoading}
            query={appsQuery}
            filter={appsKindFilter}
            cliActionKey={cliAppsAction}
            mcpActionKey={mcpPresetAction}
            cliMessage={cliAppsMessage}
            cliError={cliAppsError}
            cliFocusName={cliAppsFocusName}
            mcpMessage={mcpMessage}
            mcpError={mcpError}
            mcpFieldValues={mcpFieldValues}
            customMcpForm={customMcpForm}
            mcpConfigImport={mcpConfigImport}
            showBrandLogos={localPrefs.brandLogos}
            requiresRestartPending={pendingRestartSections.runtime}
            onQueryChange={setAppsQuery}
            onFilterChange={setAppsKindFilter}
            onCliAction={handleCliAppAction}
            onMcpAction={handleMcpPresetAction}
            onDismissStatus={() => {
              setCliAppsMessage(null);
              setCliAppsError(null);
              setMcpMessage(null);
              setMcpError(null);
            }}
            onBackToChat={onBackToChat}
            onMcpFieldChange={(presetName, fieldName, value) => {
              setMcpFieldValues((prev) => ({
                ...prev,
                [presetName]: {
                  ...(prev[presetName] ?? {}),
                  [fieldName]: value,
                },
              }));
            }}
            onCustomMcpFormChange={setCustomMcpForm}
            onMcpConfigImportChange={setMcpConfigImport}
            onSaveCustomMcp={handleSaveCustomMcp}
            onImportMcpConfig={handleImportMcpConfig}
            onMcpToolsChange={handleMcpToolsChange}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
          />
        );
      case "automations":
        return (
          <AutomationsSettings
            payload={automations}
            loading={automationsLoading}
            query={automationsQuery}
            filter={automationsFilter}
            sort={automationsSort}
            actionKey={automationAction}
            error={automationsError}
            onQueryChange={setAutomationsQuery}
            onFilterChange={setAutomationsFilter}
            onSortChange={setAutomationsSort}
            onAction={handleAutomationAction}
            onRequestEdit={setAutomationPendingEdit}
            onRequestDelete={setAutomationPendingDelete}
          />
        );
      case "skills":
        return <SkillsCatalogSettings skills={skills} onSkillsChange={onSkillsChange} />;
      case "templates":
        return <AppTemplatesSettings />;
      case "runtime":
        return (
          <RuntimeSettings
            form={form}
            setForm={setForm}
            settings={settings}
            dirty={runtimeDirty}
            saving={saving}
            onSave={saveRuntimeSettings}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.runtime}
          />
        );
      case "computer":
        return <ComputerSettings settings={settings} onUpdated={setSettings}
          onRestart={restartViaSettingsSurface} isRestarting={isRestarting || hostEngineApplying} />;
      case "advanced":
        return (
          <AdvancedSettings
            form={networkSafetyForm}
            settings={settings}
            dirty={networkSafetyDirty}
            saving={networkSafetySaving}
            isNativeHostSurface={(settings.surface ?? settings.runtime_surface) === "native"}
            onChangeForm={setNetworkSafetyForm}
            onSave={saveNetworkSafetySettings}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.runtime}
          />
        );
      case "about":
        return <AboutSettings settings={settings} />;
      default:
        return null;
    }
  };

  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row",
        showSidebar
          ? "bg-[radial-gradient(circle_at_50%_0%,hsl(var(--muted))_0%,hsl(var(--background))_42%)]"
          : "bg-background",
      )}
      data-settings-scroll-host=""
    >
      {showSidebar ? (
        <SettingsSidebar
          activeSection={activeSection}
          onSelectSection={selectSection}
          onBackToChat={onBackToChat}
          hostChromeInset={hostChromeInset}
        />
      ) : null}

      <NewModelConfigurationDialog
        open={modelConfigurationOpen}
        draft={modelConfigurationForm}
        providers={configuredModelProviderOptions}
        saving={modelConfigurationSaving}
        showProviderLogos={localPrefs.brandLogos}
        token={token}
        settings={settings}
        onOpenChange={setModelConfigurationOpen}
        onChangeDraft={setModelConfigurationForm}
        onSave={handleCreateModelConfiguration}
      />

      <ImportModelsDialog
        open={modelImportOpen}
        provider={modelImportProvider}
        providers={configuredModelProviderOptions}
        saving={modelImportSaving}
        progress={modelImportProgress}
        showProviderLogos={localPrefs.brandLogos}
        token={token}
        settings={settings}
        onOpenChange={setModelImportOpen}
        onChangeProvider={setModelImportProvider}
        onImport={(provider, models) => void handleImportModelConfigurations(provider, models)}
      />

      <NavinFeatureInstallDialog
        feature={navinFeatureConfirm}
        installing={navinFeatureAction === `enable:${navinFeatureConfirm?.name ?? ""}`}
        onOpenChange={(open) => {
          if (!open) setNavinFeatureConfirm(null);
        }}
        onConfirm={(feature) => handleNavinFeatureAction("enable", feature.name, true)}
      />

      <Dialog
        open={modelSetupPromptProvider !== null}
        onOpenChange={(open) => {
          // Closing the dialog is fine: the Models banner keeps the reminder.
          if (!open) setModelSetupPromptProvider(null);
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="w-[min(calc(100vw-2rem),26rem)] rounded-[26px]"
          onPointerDownOutside={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>
              {t("settings.providers.modelSetupPrompt.title", {
                defaultValue: "Provider ready",
              })}
            </DialogTitle>
            <DialogDescription>
              {t("settings.providers.modelSetupPrompt.body", {
                provider: providerDisplayLabel(
                  settings?.providers ?? [],
                  modelSetupPromptProvider ?? "",
                ),
                defaultValue:
                  "Pick a model for {{provider}} next - chat will not work until one is selected.",
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              className="rounded-full"
              onClick={() => {
                const providerName = modelSetupPromptProvider;
                setModelSetupPromptProvider(null);
                if (providerName) {
                  setForm((prev) => ({
                    ...prev,
                    provider: providerName,
                    model: prev.provider === providerName ? prev.model : "",
                  }));
                }
                selectSection("models");
              }}
            >
              {t("settings.providers.modelSetupPrompt.chooseModel", {
                defaultValue: "Choose a model",
              })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AutomationDeleteDialog
        job={automationPendingDelete}
        deleting={automationAction === `delete:${automationPendingDelete?.id ?? ""}`}
        onOpenChange={(open) => {
          if (!open) setAutomationPendingDelete(null);
        }}
        onConfirm={(job) => handleAutomationAction("delete", job)}
      />

      <AutomationEditDialog
        job={automationPendingEdit}
        saving={automationAction === `update:${automationPendingEdit?.id ?? ""}`}
        onOpenChange={(open) => {
          if (!open) setAutomationPendingEdit(null);
        }}
        onSave={handleAutomationEdit}
      />

      <main
        className={cn(
          "min-h-0 min-w-0 flex-1 overscroll-contain [scrollbar-gutter:stable]",
          activeSection === "tools" || activeSection === "channels"
            ? "overflow-y-auto xl:overflow-hidden"
            : "overflow-y-auto",
        )}
        data-panel-scroll=""
      >
        <div
          className={cn(
            "mx-auto w-full px-4 pb-6 pt-4 sm:px-8 sm:pb-8",
            activeSection === "tools" ||
              activeSection === "channels" ||
              activeSection === "apps" ||
              activeSection === "models"
              ? "max-w-[1240px] xl:px-10"
              : "max-w-[920px]",
            (activeSection === "tools" || activeSection === "channels") &&
              "flex min-h-full flex-col xl:h-full xl:min-h-0",
            hostChromeInset && SETTINGS_HOST_CHROME_PAD,
          )}
        >
          <div className="mb-7">
            {!showSidebar ? (
              <button
                type="button"
                onClick={onBackToChat}
                className="mb-4 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground lg:hidden"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
                {t("settings.backToChat")}
              </button>
            ) : null}
            {showSidebar ? (
              <p className="mb-2 text-[12px] font-normal text-muted-foreground">
                {t("settings.sidebar.title")}
              </p>
            ) : null}
            {activeSection === "tools" || activeSection === "apps" ? (
              <div
                role="tablist"
                aria-label={text("sidebar.tools", "Tools & Mcp")}
                className="inline-flex rounded-2xl border border-border/60 bg-muted/50 p-1.5"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeSection === "tools"}
                  onClick={() => selectSection("tools")}
                  className={cn(
                    "rounded-xl px-5 py-2.5 text-[15px] font-semibold transition-colors",
                    activeSection === "tools"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {text("sidebar.toolsTab", "Tools")}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeSection === "apps"}
                  onClick={() => selectSection("apps")}
                  className={cn(
                    "rounded-xl px-5 py-2.5 text-[15px] font-semibold transition-colors",
                    activeSection === "apps"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {text("sidebar.apps", "Plugins et Mcp")}
                </button>
              </div>
            ) : activeSection === "skills" || activeSection === "automations" ? (
              <div
                role="tablist"
                aria-label={text("sidebar.skillsLoop", "Skills & Loop")}
                className="inline-flex rounded-2xl border border-border/60 bg-muted/50 p-1.5"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeSection === "skills"}
                  onClick={() => selectSection("skills")}
                  className={cn(
                    "rounded-xl px-5 py-2.5 text-[15px] font-semibold transition-colors",
                    activeSection === "skills"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {text("sidebar.skills.title", "Skills")}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeSection === "automations"}
                  onClick={() => selectSection("automations")}
                  className={cn(
                    "rounded-xl px-5 py-2.5 text-[15px] font-semibold transition-colors",
                    activeSection === "automations"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {text("sidebar.automations", "Loop")}
                </button>
              </div>
            ) : (
              <h1 className="text-[24px] font-normal leading-tight tracking-normal text-foreground sm:text-[28px]">
                {text(`settings.nav.${activeSection}`, titleForSection(activeSection))}
              </h1>
            )}
          </div>

          {loading ? (
            <div className="flex h-48 items-center justify-center rounded-[24px] border border-border/50 bg-card/75 text-sm text-muted-foreground shadow-[0_20px_70px_rgba(15,23,42,0.07)]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("settings.status.loading")}
            </div>
          ) : error && !settings ? (
            <SettingsGroup>
              <SettingsRow title={t("settings.status.loadError")}>
                <span className="max-w-[520px] text-sm text-muted-foreground">{error}</span>
              </SettingsRow>
            </SettingsGroup>
          ) : settings ? (
            <div
              className={cn(
                "space-y-5",
                (activeSection === "tools" || activeSection === "channels") &&
                  "flex min-h-0 flex-1 flex-col xl:overflow-hidden",
              )}
            >
              {error ? (
                <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
                  {error}
                </div>
              ) : null}
              {renderSection()}
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}

const SETTINGS_NAV_ITEMS: Array<{
  key: SettingsSectionKey;
  icon: LucideIcon;
  fallback: string;
  labelKey?: string;
}> = [
  { key: "overview", icon: Activity, fallback: "Overview" },
  { key: "account", icon: CircleUserRound, fallback: "Account" },
  { key: "appearance", icon: Palette, fallback: "Appearance" },
  { key: "providers", icon: KeyRound, fallback: "Providers" },
  { key: "models", icon: SlidersHorizontal, fallback: "Models" },
  { key: "computer", icon: Monitor, fallback: "Computer" },
  { key: "tools", icon: Wrench, fallback: "Tools & Mcp", labelKey: "settings.nav.toolsMcp" },
  { key: "channels", icon: Radio, fallback: "Channels" },
  { key: "skills", icon: Brain, fallback: "Skills & Loop", labelKey: "settings.nav.skillsLoop" },
  { key: "image", icon: ImageIcon, fallback: "Image" },
  { key: "video", icon: Clapperboard, fallback: "Video" },
  { key: "voice", icon: Mic, fallback: "Voice" },
  { key: "browser", icon: Globe2, fallback: "Web" },
  { key: "runtime", icon: Server, fallback: "System" },
  { key: "advanced", icon: ShieldCheck, fallback: "Security" },
  { key: "about", icon: Info, fallback: "About" },
];

function visibleWebuiDefaultAccessMode(mode: string | null | undefined): WebuiDefaultAccessMode {
  return mode === "full" ? "full" : "default";
}

function titleForSection(section: SettingsSectionKey): string {
  if (section === "templates") return "Templates";
  if (section === "skills") return "Skills";
  if (section === "apps") return "Plugins et Mcp";
  if (section === "automations") return "Loop";
  if (section === "tools") return "Tools & Mcp";
  if (section === "channels") return "Channels";
  return SETTINGS_NAV_ITEMS.find((item) => item.key === section)?.fallback ?? "Settings";
}

function SettingsSidebar({
  activeSection,
  onSelectSection,
  onBackToChat,
  hostChromeInset,
}: {
  activeSection: SettingsSectionKey;
  onSelectSection: (section: SettingsSectionKey) => void;
  onBackToChat: () => void;
  hostChromeInset?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <aside
      className={cn(
        "flex w-full shrink-0 flex-col border-b border-border/55 bg-card/62 px-3 pb-2 shadow-[inset_0_-1px_0_rgba(255,255,255,0.55)] backdrop-blur-xl dark:bg-card/45 dark:shadow-none lg:w-[17rem] lg:border-b-0 lg:border-r lg:px-3 lg:pb-4 lg:shadow-[inset_-1px_0_0_rgba(255,255,255,0.55)]",
        hostChromeInset ? SETTINGS_HOST_CHROME_PAD : "pt-4",
      )}
    >
      <button
        type="button"
        onClick={onBackToChat}
        className="host-no-drag mb-2 inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground lg:mb-3"
      >
        <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
        {t("settings.backToChat")}
      </button>
      <div className="mb-3 px-1 lg:mb-4 lg:px-2">
        <h2 className="text-[18px] font-normal tracking-normal text-foreground">
          {t("settings.sidebar.title")}
        </h2>
      </div>

      <nav
        aria-label={t("settings.sidebar.ariaLabel")}
        className="-mx-1 flex snap-x gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden lg:mx-0 lg:block lg:space-y-1 lg:overflow-visible lg:px-0 lg:pb-0"
      >
        {SETTINGS_NAV_ITEMS.map(({ key, icon: Icon, fallback, labelKey }) => {
          const active =
            key === activeSection
            || (key === "tools" && (activeSection === "tools" || activeSection === "apps"))
            || (key === "skills" && (activeSection === "skills" || activeSection === "automations"));
          return (
            <button
              key={key}
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => onSelectSection(key)}
              className={cn(
                "relative flex h-9 w-auto shrink-0 snap-start items-center gap-2 rounded-full px-3 text-left text-[13px] font-medium outline-none transition-colors focus:outline-none focus-visible:outline-none lg:w-full lg:rounded-xl lg:px-2.5",
                active
                  ? "bg-sidebar-accent text-sidebar-foreground"
                  : "text-muted-foreground/78 hover:bg-muted/45 hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
              <span className="truncate">
                {t(labelKey ?? `settings.nav.${key}`, { defaultValue: fallback })}
              </span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

function OverviewSettings({
  settings,
  onSelectSection,
  showBrandLogos,
}: {
  settings: SettingsPayload;
  onSelectSection: (section: SettingsSectionKey) => void;
  showBrandLogos: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const configuredProviders = settings.providers.filter(
    (provider) =>
      provider.configured && !hiddenSettingsProvider(provider.name),
  );
  const activePreset = settings.agent.model_preset || "default";
  const activeProvider = settings.agent.resolved_provider ?? settings.agent.provider;
  const activeProviderConfigured = settingsProviderConfigured(settings, activeProvider);
  const activeProviderLabel = providerDisplayLabel(settings.providers, activeProvider);
  const activeModelReady = activeProviderConfigured && Boolean(settings.agent.model.trim());
  const activeModelValue = activeModelReady
    ? settings.agent.model
    : tx("settings.values.notConfigured", "Not configured");
  const activeModelCaption = activeModelReady
    ? `${activeProvider} · ${activePreset}`
    : activeProviderLabel || settings.agent.model
      ? [activeProviderLabel, settings.agent.model].filter(Boolean).join(" · ")
      : tx("settings.byok.noConfiguredProviders", "No configured providers");
  const webStatus = settings.web.enable
    ? tx("settings.values.enabled", "Enabled")
    : tx("settings.values.disabled", "Disabled");
  const webSearchProvider =
    settings.web_search.providers.find((provider) => provider.name === settings.web_search.provider) ??
    settings.web_search.providers[0];
  const webSearchProviderLabel = providerDisplayLabel(
    settings.web_search.providers,
    settings.web_search.provider,
  );
  const webSearchCredentialStatus =
    webSearchProvider?.credential === "none"
      ? tx("settings.byok.webSearch.noCredentialRequired", "No key required")
      : webSearchProvider?.credential === "optional_api_key"
        ? settings.web_search.api_key_hint
          ? tx("settings.values.configured", "Configured")
          : tx("settings.byok.webSearch.noCredentialRequired", "No key required")
      : webSearchProvider?.credential === "base_url"
        ? settings.web_search.base_url
          ? tx("settings.values.configured", "Configured")
          : tx("settings.values.notConfigured", "Not configured")
        : settings.web_search.api_key_hint
          ? tx("settings.values.configured", "Configured")
          : tx("settings.values.notConfigured", "Not configured");
  const webCaption = `${webSearchProviderLabel} · ${webSearchCredentialStatus}`;
  // An unset media provider has no label, so the separator has to go with it.
  const mediaCaption = (label: string, configured: boolean) =>
    [
      label,
      configured
        ? tx("settings.values.configured", "Configured")
        : tx("settings.values.notConfigured", "Not configured"),
    ]
      .filter(Boolean)
      .join(" · ");
  const imageStatus = settings.image_generation.enabled
    ? tx("settings.values.enabled", "Enabled")
    : tx("settings.values.disabled", "Disabled");
  const imageCaption = mediaCaption(
    providerDisplayLabel(settings.image_generation.providers, settings.image_generation.provider),
    settings.image_generation.provider_configured,
  );
  const videoGeneration = settings.video_generation ?? DEFAULT_VIDEO_GENERATION_SETTINGS;
  const videoStatus = videoGeneration.enabled
    ? tx("settings.values.enabled", "Enabled")
    : tx("settings.values.disabled", "Disabled");
  const videoCaption = mediaCaption(
    providerDisplayLabel(videoGeneration.providers, videoGeneration.provider),
    videoGeneration.provider_configured,
  );
  const transcription = settings.transcription ?? DEFAULT_TRANSCRIPTION_SETTINGS;
  const voiceStatus = transcription.enabled
    ? tx("settings.values.enabled", "Enabled")
    : tx("settings.values.disabled", "Disabled");
  const voiceCaption = mediaCaption(
    providerDisplayLabel(transcription.providers, transcription.provider),
    transcription.provider_configured,
  );
  return (
    <div className="space-y-7">
      <section className="space-y-6">
        <TokenUsageHeatmap usage={settings.usage} timeZone={settings.agent.timezone} />
        <ModelTokenUsageTable usage={settings.usage} />
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.ai", "AI")}</SettingsSectionTitle>
        <SettingsGroup>
          <OverviewListRow
            icon={KeyRound}
            title={tx("settings.overview.providers", "Providers")}
            value={
              configuredProviders.length > 0
                ? t("settings.overview.configuredCount", {
                    defaultValue: "{{count}} configured",
                    count: configuredProviders.length,
                  })
                : tx("settings.byok.noConfiguredProviders", "No configured providers")
            }
            caption={tx(
              "settings.overview.providersCaption",
              "Add API keys or local endpoints before choosing a model.",
            )}
            onClick={() => onSelectSection("providers")}
          />
          <OverviewListRow
            icon={Bot}
            valueLogoProvider={activeProvider}
            title={tx("settings.overview.model", "Current model")}
            value={activeModelValue}
            caption={activeModelCaption}
            showBrandLogos={showBrandLogos}
            onClick={() => onSelectSection("models")}
          />
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.capabilities", "Capabilities")}</SettingsSectionTitle>
        <SettingsGroup>
          <OverviewListRow
            icon={Globe2}
            valueLogoProvider={settings.web_search.provider}
            title={tx("settings.overview.webSearch", "Web search")}
            value={webStatus}
            caption={webCaption}
            showBrandLogos={showBrandLogos}
            onClick={() => onSelectSection("browser")}
          />
          <OverviewListRow
            icon={ImageIcon}
            valueLogoProvider={settings.image_generation.provider}
            title={tx("settings.overview.imageGeneration", "Image generation")}
            value={imageStatus}
            caption={imageCaption}
            showBrandLogos={showBrandLogos}
            onClick={() => onSelectSection("image")}
          />
          <OverviewListRow
            icon={Clapperboard}
            valueLogoProvider={videoGeneration.provider}
            title={tx("settings.overview.videoGeneration", "Video generation")}
            value={videoStatus}
            caption={videoCaption}
            showBrandLogos={showBrandLogos}
            onClick={() => onSelectSection("video")}
          />
          <OverviewListRow
            icon={Mic}
            valueLogoProvider={transcription.provider}
            title={tx("settings.overview.voiceInput", "Voice input")}
            value={voiceStatus}
            caption={voiceCaption}
            showBrandLogos={showBrandLogos}
            onClick={() => onSelectSection("voice")}
          />
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.system", "System")}</SettingsSectionTitle>
        <SettingsGroup>
          <OverviewListRow
            icon={HardDrive}
            title={tx("settings.overview.workspace", "Workspace")}
            value={tx("settings.values.defaultWorkspace", "Default workspace")}
            caption={shortWorkspacePath(settings.runtime.workspace_path)}
            onClick={() => onSelectSection("runtime")}
          />
        </SettingsGroup>
      </section>

    </div>
  );
}

function VersionCheckRow({
  currentVersion,
  preferences,
}: {
  currentVersion?: string;
  preferences?: SettingsPayload["updates"];
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { token } = useClient();
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [result, setResult] = useState<UpdateInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoCheck, setAutoCheck] = useState(preferences?.autoCheck ?? true);
  const [channel, setChannel] = useState<"stable" | "beta">(
    preferences?.channel ?? "stable",
  );

  const handleCheck = async (force = true) => {
    setChecking(true);
    setError(null);
    try {
      setResult(await checkVersion(token, force));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const savePreferences = async (nextAuto: boolean, nextChannel: "stable" | "beta") => {
    setAutoCheck(nextAuto);
    setChannel(nextChannel);
    setError(null);
    try {
      await updatePreferences(token, { autoCheck: nextAuto, channel: nextChannel });
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleInstall = async () => {
    setInstalling(true);
    setError(null);
    try {
      await downloadUpdate(token);
      await installUpdate(token);
    } catch (err) {
      setError((err as Error).message);
      setInstalling(false);
    }
  };

  return (
    <div className="flex min-h-[88px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-start sm:justify-between sm:px-5">
      <div className="min-w-0 space-y-2">
        <div className="text-[14px] font-medium leading-5 text-foreground">
          {tx("settings.about.version", "Version")}
        </div>
        <div className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
          {currentVersion ? `v${currentVersion}` : "navin"}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <label className="inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={autoCheck}
              onChange={(event) => void savePreferences(event.target.checked, channel)}
              className="accent-foreground"
            />
            {tx("settings.about.autoCheck", "Check automatically")}
          </label>
          <span aria-hidden>·</span>
          <button
            type="button"
            onClick={() => void savePreferences(autoCheck, channel === "stable" ? "beta" : "stable")}
            className="underline-offset-2 hover:underline"
          >
            {tx("settings.about.channel", "Channel")}: {channel}
          </button>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleCheck()}
            disabled={checking || installing}
            className="rounded-full"
          >
            {checking ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {checking
              ? tx("settings.about.checking", "Checking...")
              : tx("settings.about.checkForUpdates", "Check for updates")}
          </Button>
          {result?.available && result.supported ? (
            <Button
              size="sm"
              onClick={() => void handleInstall()}
              disabled={installing}
              className="rounded-full"
            >
              {installing ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              {installing
                ? tx("settings.about.installing", "Installing...")
                : tx("settings.about.installRestart", "Install and restart")}
            </Button>
          ) : null}
        </div>
        {result && !result.available ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <Check className="h-3 w-3" aria-hidden />
            {tx("settings.about.upToDate", "You're up to date")}
          </span>
        ) : null}
        {result?.available ? (
          <span className="inline-flex max-w-md items-center gap-1.5 text-right text-[12px] text-foreground">
            <ArrowUpCircle className="h-3 w-3" aria-hidden />
            {t("settings.about.updateAvailable", {
              defaultValue: "Update available v{{version}}",
              version: result.latestVersion,
            })}
            {!result.supported
              ? ` · ${tx("settings.about.manualOnly", "manual update required")}`
              : null}
          </span>
        ) : null}
        {result?.notes ? (
          <span className="max-w-md text-right text-[11px] text-muted-foreground">
            {result.notes}
          </span>
        ) : null}
        {error ? <span className="text-[12px] text-destructive">{error}</span> : null}
      </div>
    </div>
  );
}

function AboutSettings({ settings }: { settings: SettingsPayload }) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [licenseOpen, setLicenseOpen] = useState(false);
  const [noticesOpen, setNoticesOpen] = useState(false);
  const [licenseText, setLicenseText] = useState<string | null>(null);
  const [noticesText, setNoticesText] = useState<string | null>(null);
  const [licenseError, setLicenseError] = useState<string | null>(null);
  const [noticesError, setNoticesError] = useState<string | null>(null);
  const [licenseLoading, setLicenseLoading] = useState(false);
  const [noticesLoading, setNoticesLoading] = useState(false);

  const loadLicense = useCallback(async () => {
    setLicenseLoading(true);
    setLicenseError(null);
    try {
      const res = await fetch("/licenses/LICENSE.txt", { cache: "force-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setLicenseText(await res.text());
    } catch (err) {
      setLicenseError((err as Error).message);
      setLicenseText(null);
    } finally {
      setLicenseLoading(false);
    }
  }, []);

  const loadNotices = useCallback(async () => {
    setNoticesLoading(true);
    setNoticesError(null);
    try {
      const res = await fetch("/licenses/THIRD_PARTY_NOTICES.md", { cache: "force-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setNoticesText(await res.text());
    } catch (err) {
      setNoticesError((err as Error).message);
      setNoticesText(null);
    } finally {
      setNoticesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (licenseOpen && licenseText === null && !licenseLoading && !licenseError) {
      void loadLicense();
    }
  }, [licenseOpen, licenseText, licenseLoading, licenseError, loadLicense]);

  useEffect(() => {
    if (noticesOpen && noticesText === null && !noticesLoading && !noticesError) {
      void loadNotices();
    }
  }, [noticesOpen, noticesText, noticesLoading, noticesError, loadNotices]);

  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{tx("settings.sections.about", "About")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="space-y-3 px-4 py-4 sm:px-5">
            <div>
              <div className="text-[15px] font-semibold text-foreground">Navin</div>
              <div className="mt-1 text-[12.5px] leading-5 text-muted-foreground">
                {tx(
                  "settings.about.productTagline",
                  "Local AI OS by Navinspire IA - navin.live",
                )}
              </div>
              <div className="mt-2 text-[12.5px] leading-5 text-muted-foreground">
                {tx(
                  "settings.about.modelsViaOpenRouter",
                  "Navin subscription models are served through OpenRouter.",
                )}
              </div>
            </div>
            <div className="grid gap-2 text-[12.5px] leading-5 text-muted-foreground sm:grid-cols-2">
              <div>
                <span className="text-foreground/80">
                  {tx("settings.about.publisher", "Publisher")}
                </span>
                <div>Navinspire IA</div>
              </div>
              <div>
                <span className="text-foreground/80">
                  {tx("settings.about.product", "Product")}
                </span>
                <div>Navin</div>
              </div>
              <div>
                <span className="text-foreground/80">
                  {tx("settings.about.website", "Website")}
                </span>
                <div>
                  <a
                    href="https://navin.live"
                    target="_blank"
                    rel="noreferrer"
                    className="text-foreground/90 underline-offset-2 hover:underline"
                  >
                    navin.live
                  </a>
                </div>
              </div>
              <div>
                <span className="text-foreground/80">
                  {tx("settings.about.companySite", "Company")}
                </span>
                <div>
                  <a
                    href="https://navinspire.ai"
                    target="_blank"
                    rel="noreferrer"
                    className="text-foreground/90 underline-offset-2 hover:underline"
                  >
                    navinspire.ai
                  </a>
                </div>
              </div>
            </div>
          </div>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>
          {tx("settings.about.updatesTitle", "Updates")}
        </SettingsSectionTitle>
        <SettingsGroup>
          <VersionCheckRow
            currentVersion={settings.version?.current}
            preferences={settings.updates}
          />
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>
          {tx("settings.about.softwareLicenseTitle", "Software license")}
        </SettingsSectionTitle>
        <SettingsGroup>
          <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-5">
            <div className="min-w-0 space-y-1.5">
              <div className="text-[14px] font-medium text-foreground">
                {tx("settings.about.navinLicenseTitle", "Navin proprietary license")}
              </div>
              <p className="text-[12.5px] leading-5 text-muted-foreground">
                {tx(
                  "settings.about.navinLicenseSummary",
                  "Copyright © 2026 Navinspire IA. All rights reserved. Navin is proprietary software. Official desktop use is allowed under the navin.live Terms of Service and your plan.",
                )}
              </p>
              <div className="flex flex-wrap gap-x-3 gap-y-1 pt-1 text-[12px]">
                <a
                  href="https://navin.live/legal/terms"
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  {tx("settings.about.termsLink", "Terms of Service")}
                </a>
                <a
                  href="https://navin.live/legal/privacy"
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  {tx("settings.about.privacyLink", "Privacy Policy")}
                </a>
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-stretch gap-2 sm:items-end">
              <Button
                size="sm"
                variant="outline"
                className="rounded-full"
                onClick={() => setLicenseOpen(true)}
              >
                <ScrollText className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("settings.about.viewNavinLicense", "View license")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 rounded-full px-3 text-[11px] text-muted-foreground/70 hover:text-muted-foreground"
                onClick={() => setNoticesOpen(true)}
              >
                {tx("settings.about.thirdPartyButton", "Third-party")}
              </Button>
            </div>
          </div>
        </SettingsGroup>
      </section>

      <Dialog open={licenseOpen} onOpenChange={setLicenseOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          <DialogHeader className="border-b border-border px-5 py-4 text-start">
            <DialogTitle>
              {tx("settings.about.navinLicenseTitle", "Navin proprietary license")}
            </DialogTitle>
            <DialogDescription>
              {tx(
                "settings.about.navinLicenseDialogHelp",
                "Navinspire IA proprietary software license for the Navin product.",
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
            {licenseLoading ? (
              <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {tx("settings.about.loadingLicenses", "Loading…")}
              </div>
            ) : null}
            {licenseError ? (
              <div className="space-y-3 text-[13px]">
                <p className="text-destructive">
                  {tx("settings.about.licensesLoadError", "Could not load license notices.")}{" "}
                  ({licenseError})
                </p>
                <Button size="sm" variant="outline" onClick={() => void loadLicense()}>
                  {tx("settings.about.retry", "Retry")}
                </Button>
              </div>
            ) : null}
            {licenseText ? (
              <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-foreground/90">
                {licenseText}
              </pre>
            ) : null}
          </div>
          <DialogFooter className="border-t border-border px-5 py-3 sm:justify-between">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => {
                setLicenseOpen(false);
                setNoticesOpen(true);
              }}
            >
              {tx("settings.about.thirdPartyButton", "Third-party")}
            </Button>
            <Button type="button" variant="outline" onClick={() => setLicenseOpen(false)}>
              {tx("settings.about.close", "Close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={noticesOpen} onOpenChange={setNoticesOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          <DialogHeader className="border-b border-border px-5 py-4 text-start">
            <DialogTitle>
              {tx("settings.about.openSourceLicenses", "Open Source Licenses")}
            </DialogTitle>
            <DialogDescription>
              {tx(
                "settings.about.openSourceLicensesDialogHelp",
                "Copyright and license notices for third-party software redistributed with Navin.",
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
            {noticesLoading ? (
              <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {tx("settings.about.loadingLicenses", "Loading…")}
              </div>
            ) : null}
            {noticesError ? (
              <div className="space-y-3 text-[13px]">
                <p className="text-destructive">
                  {tx("settings.about.licensesLoadError", "Could not load license notices.")}{" "}
                  ({noticesError})
                </p>
                <Button size="sm" variant="outline" onClick={() => void loadNotices()}>
                  {tx("settings.about.retry", "Retry")}
                </Button>
                <p className="text-muted-foreground">
                  {tx(
                    "settings.about.licensesWebFallback",
                    "You can also read them on the website:",
                  )}{" "}
                  <a
                    href="https://navin.live/legal/licenses"
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-foreground underline underline-offset-2"
                  >
                    navin.live/legal/licenses
                  </a>
                </p>
              </div>
            ) : null}
            {noticesText ? (
              <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-foreground/90">
                {noticesText}
              </pre>
            ) : null}
          </div>
          <DialogFooter className="border-t border-border px-5 py-3 sm:justify-between">
            <a
              href="/licenses/THIRD_PARTY_NOTICES.md"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              {tx("settings.about.openRawNotices", "Open raw file")}
            </a>
            <Button type="button" variant="outline" onClick={() => setNoticesOpen(false)}>
              {tx("settings.about.close", "Close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function AppearanceSettings({
  theme,
  onToggleTheme,
  localPrefs,
  onChangeLocalPrefs,
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  localPrefs: LocalPreferences;
  onChangeLocalPrefs: Dispatch<SetStateAction<LocalPreferences>>;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{t("settings.sections.interface")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.theme")}
            description={t("settings.help.theme")}
          >
            <button
              type="button"
              onClick={onToggleTheme}
              className="inline-flex h-8 items-center rounded-full bg-muted p-0.5 text-[12px] font-medium text-muted-foreground"
            >
              <span
                className={cn(
                  "rounded-full px-3 py-1 transition-colors",
                  theme === "light" && "bg-background text-foreground shadow-sm",
                )}
              >
                {t("settings.values.light")}
              </span>
              <span
                className={cn(
                  "rounded-full px-3 py-1 transition-colors",
                  theme === "dark" && "bg-background text-foreground shadow-sm",
                )}
              >
                {t("settings.values.dark")}
              </span>
            </button>
          </SettingsRow>

          <SettingsRow
            title={t("settings.rows.language")}
            description={t("settings.help.language")}
          >
            <LanguageSwitcher />
          </SettingsRow>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.localPreferences", "Local preferences")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.density", "Density")}
            description={tx("settings.help.density", "Stored only in this browser.")}
          >
            <SegmentedControl
              value={localPrefs.density}
              options={[
                { value: "comfortable", label: tx("settings.values.comfortable", "Comfortable") },
                { value: "compact", label: tx("settings.values.compact", "Compact") },
              ]}
              onChange={(density) =>
                onChangeLocalPrefs((prev) => ({ ...prev, density: density as LocalDensity }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.activityMode", "Activity detail")}
            description={tx(
              "settings.help.activityMode",
              "Auto opens only the latest turn. Expanded keeps every turn open. Compact reduces Thinking to its title and shortens the journal. Digest folds every turn to one line that follows the live action.",
            )}
          >
            <SegmentedControl
              value={localPrefs.activityMode}
              options={[
                { value: "auto", label: tx("settings.values.auto", "Auto") },
                { value: "expanded", label: tx("settings.values.expanded", "Expanded") },
                { value: "compact", label: tx("settings.values.compact", "Compact") },
                { value: "digest", label: tx("settings.values.digest", "Digest") },
              ]}
              onChange={(activityMode) =>
                onChangeLocalPrefs((prev) => ({ ...prev, activityMode: activityMode as LocalActivityMode }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.fileEditDisplay", "File edit display")}
            description={tx("settings.help.fileEditDisplay", "Choose whether file edit activity opens as line counts or a diff.")}
          >
            <SegmentedControl
              value={localPrefs.fileEditDisplayMode}
              options={[
                { value: "summary", label: tx("settings.values.summary", "Summary") },
                { value: "diff", label: tx("settings.values.diff", "Diff") },
                { value: "collapsed_diff", label: tx("settings.values.collapsedDiff", "Collapsed diff") },
              ]}
              onChange={(fileEditDisplayMode) =>
                onChangeLocalPrefs((prev) => ({
                  ...prev,
                  fileEditDisplayMode: fileEditDisplayMode as FileEditDisplayMode,
                }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.codeWrap", "Code wrapping")}
            description={tx("settings.help.codeWrap", "Keep long code lines readable on smaller screens.")}
          >
            <ToggleButton
              checked={localPrefs.codeWrap}
              onChange={(codeWrap) => onChangeLocalPrefs((prev) => ({ ...prev, codeWrap }))}
              ariaLabel={tx("settings.rows.codeWrap", "Code wrapping")}
              label={localPrefs.codeWrap ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.brandLogos", "Brand logos")}
            description={tx("settings.help.brandLogos", "Show third-party provider and CLI logos in Settings.")}
          >
            <ToggleButton
              checked={localPrefs.brandLogos}
              onChange={(brandLogos) => onChangeLocalPrefs((prev) => ({ ...prev, brandLogos }))}
              ariaLabel={tx("settings.rows.brandLogos", "Brand logos")}
              label={localPrefs.brandLogos ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
        </SettingsGroup>
      </section>
    </div>
  );
}

function NewModelConfigurationDialog({
  open,
  draft,
  providers,
  saving,
  showProviderLogos,
  token,
  settings,
  onOpenChange,
  onChangeDraft,
  onSave,
}: {
  open: boolean;
  draft: ModelConfigurationDraft;
  providers: Array<{ name: string; label: string }>;
  saving: boolean;
  showProviderLogos: boolean;
  token: string;
  settings: SettingsPayload | null;
  onOpenChange: (open: boolean) => void;
  onChangeDraft: Dispatch<SetStateAction<ModelConfigurationDraft>>;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const canSave = Boolean(draft.label.trim() && draft.provider.trim() && draft.model.trim());

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[520px] rounded-[28px] border-border/55 bg-card/95 p-0 shadow-[0_28px_90px_rgba(15,23,42,0.20)] backdrop-blur-xl dark:border-white/10">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSave();
          }}
        >
          <DialogHeader className="border-b border-border/45 px-5 py-4 text-left">
            <DialogTitle className="text-[18px] font-semibold tracking-[-0.01em]">
              {tx("settings.models.newConfiguration", "New model configuration")}
            </DialogTitle>
            <DialogDescription className="text-[12.5px] leading-5">
              {tx("settings.models.newConfigurationHelp", "Save a provider and model as a one-click option.")}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 px-5 py-5">
            <label className="block">
              <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
                {tx("settings.models.configurationName", "Configuration name")}
              </span>
              <Input
                autoFocus
                value={draft.label}
                placeholder={tx("settings.models.configurationNamePlaceholder", "e.g. Claude Sonnet")}
                onChange={(event) =>
                  onChangeDraft((prev) => ({ ...prev, label: event.target.value }))
                }
                className="h-10 rounded-full px-4 text-[14px]"
              />
            </label>

            <div className="block">
              <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
                {tx("settings.rows.provider", "Provider")}
              </span>
              <ProviderChipSelect
                providers={providers}
                value={draft.provider}
                emptyLabel={tx("settings.byok.noConfiguredProviders", "No configured providers")}
                showProviderLogos={showProviderLogos}
                onChange={(provider) =>
                  onChangeDraft((prev) =>
                    prev.provider === provider ? prev : { ...prev, provider, model: "" },
                  )
                }
              />
            </div>

            <div className="block">
              <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
                {tx("settings.rows.model", "Model")}
              </span>
              {settings ? (
                <ModelIdPicker
                  token={token}
                  settings={settings}
                  provider={draft.provider}
                  value={draft.model}
                  showProviderLogos={showProviderLogos}
                  onChange={(model) =>
                    onChangeDraft((prev) => ({ ...prev, model }))
                  }
                />
              ) : (
                <Input
                  value={draft.model}
                  placeholder={tx("settings.models.searchModels", "Search or type model ID")}
                  onChange={(event) =>
                    onChangeDraft((prev) => ({ ...prev, model: event.target.value }))
                  }
                  className="h-10 rounded-full px-4 text-[14px]"
                />
              )}
            </div>
          </div>

          <DialogFooter className="border-t border-border/45 px-5 py-4 sm:space-x-2">
            <Button
              type="button"
              variant="ghost"
              className="rounded-full"
              disabled={saving}
              onClick={() => onOpenChange(false)}
            >
              {tx("settings.actions.cancel", "Cancel")}
            </Button>
            <Button
              type="submit"
              variant="outline"
              className="rounded-full"
              disabled={!canSave || saving || providers.length === 0}
            >
              {saving ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              {saving ? tx("settings.actions.saving", "Saving...") : tx("settings.actions.save", "Save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ImportModelsDialog({
  open,
  provider,
  providers,
  saving,
  progress,
  showProviderLogos,
  token,
  settings,
  onOpenChange,
  onChangeProvider,
  onImport,
}: {
  open: boolean;
  provider: string;
  providers: Array<{ name: string; label: string }>;
  saving: boolean;
  progress: { done: number; total: number } | null;
  showProviderLogos: boolean;
  token: string;
  settings: SettingsPayload | null;
  onOpenChange: (open: boolean) => void;
  onChangeProvider: (provider: string) => void;
  onImport: (provider: string, models: ModelConfigurationImportEntry[]) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [query, setQuery] = useState("");
  const [payload, setPayload] = useState<ProviderModelsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [freeOnly, setFreeOnly] = useState(false);

  const providerConfigured = settings
    ? settingsProviderConfigured(settings, provider)
    : false;
  const canFetch = Boolean(provider && provider !== "auto" && providerConfigured);

  // Models already saved for this provider cannot be imported twice.
  const existing = useMemo(() => {
    const ids = new Set<string>();
    for (const preset of settings?.model_presets ?? []) {
      if (preset.provider === provider && preset.model) ids.add(preset.model);
    }
    return ids;
  }, [settings, provider]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelected(new Set());
    setFreeOnly(false);
  }, [open, provider]);

  useEffect(() => {
    if (!open || !canFetch) {
      setPayload(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setPayload(null);
    setError(null);
    setLoading(true);
    fetchProviderModels(token, provider)
      .then((next) => {
        if (!cancelled) setPayload(next);
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, canFetch, provider, token]);

  const normalizedQuery = query.trim().toLowerCase();
  const allModels = payload?.models ?? [];
  const freeCount = allModels.filter((model) => model.free).length;
  const visibleModels = allModels.filter((model) => {
    if (freeOnly && !model.free) return false;
    if (!normalizedQuery) return true;
    return [model.id, model.label ?? "", model.description ?? "", model.owned_by ?? ""].some(
      (field) => field.toLowerCase().includes(normalizedQuery),
    );
  });
  const importable = visibleModels.filter((model) => !existing.has(model.id));
  const allVisibleSelected =
    importable.length > 0 && importable.every((model) => selected.has(model.id));

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        for (const model of importable) next.delete(model.id);
      } else {
        for (const model of importable) next.add(model.id);
      }
      return next;
    });
  };

  const submit = () => {
    if (!selected.size || saving) return;
    const entries: ModelConfigurationImportEntry[] = allModels
      .filter((model) => selected.has(model.id))
      .map((model) => ({
        id: model.id,
        label: model.label && model.label !== model.id ? model.label : undefined,
        context_window: model.context_window ?? undefined,
      }));
    onImport(provider, entries);
  };

  const statusMessage = (() => {
    if (!provider) return tx("settings.byok.noConfiguredProviders", "No configured providers");
    if (!providerConfigured) {
      return tx(
        "settings.models.providerNotConfigured",
        "Configure this provider before loading models.",
      );
    }
    if (loading) return null;
    if (error || payload?.status === "error") {
      return payload?.message || error || tx("settings.models.loadFailed", "Model list unavailable.");
    }
    if (payload && payload.status !== "available") {
      return (
        payload.message ||
        tx("settings.models.unsupportedModelList", "Type a model ID manually.")
      );
    }
    return null;
  })();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-[620px] flex-col rounded-[28px] border-border/55 bg-card/95 p-0 shadow-[0_28px_90px_rgba(15,23,42,0.20)] backdrop-blur-xl dark:border-white/10">
        <DialogHeader className="border-b border-border/45 px-5 py-4 text-left">
          <DialogTitle className="text-[18px] font-semibold tracking-[-0.01em]">
            {tx("settings.models.importTitle", "Import models")}
          </DialogTitle>
          <DialogDescription className="text-[12.5px] leading-5">
            {tx(
              "settings.models.importHelp",
              "Pick everything you need from the provider catalog and add it in one go.",
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 px-5 py-4">
          <div className="space-y-2">
            <p className="text-[12px] font-medium text-muted-foreground">
              {tx("settings.rows.provider", "Provider")}
            </p>
            <ProviderChipSelect
              providers={providers}
              value={provider}
              emptyLabel={tx("settings.byok.noConfiguredProviders", "No configured providers")}
              showProviderLogos={showProviderLogos}
              onChange={onChangeProvider}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[180px] flex-1">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={tx("settings.models.searchModels", "Search or type model ID")}
                className="h-9 rounded-full pl-8 pr-3 text-[12.5px]"
              />
            </div>
            {freeCount > 0 ? (
              <button
                type="button"
                aria-pressed={freeOnly}
                onClick={() => setFreeOnly((value) => !value)}
                className={cn(
                  "h-9 shrink-0 rounded-full border px-3 text-[12px] font-medium transition-colors",
                  freeOnly
                    ? "border-foreground bg-foreground text-background"
                    : "border-border text-foreground hover:bg-muted/60",
                )}
              >
                {tx("settings.models.freeFilter", "Free")} ({freeCount})
              </button>
            ) : null}
          </div>

          {statusMessage ? (
            <p className="px-1 text-[12px] leading-5 text-muted-foreground">{statusMessage}</p>
          ) : null}

          {loading ? (
            <div className="flex items-center gap-2 px-1 py-6 text-[12.5px] text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {tx("settings.models.loadingModels", "Loading models...")}
            </div>
          ) : null}

          {!loading && payload?.status === "available" ? (
            <>
              <div className="flex items-center justify-between gap-2 px-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 rounded-full px-2 text-[12px]"
                  disabled={importable.length === 0}
                  onClick={toggleAllVisible}
                >
                  {allVisibleSelected
                    ? tx("settings.models.deselectAll", "Deselect all")
                    : tx("settings.models.selectAll", "Select all")}
                  {importable.length ? ` (${importable.length})` : ""}
                </Button>
                <span className="text-[11.5px] text-muted-foreground">
                  {selected.size} {tx("settings.models.selectedCount", "selected")}
                </span>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto rounded-[16px] border border-border/50 scrollbar-thin scrollbar-track-transparent">
                {visibleModels.length === 0 ? (
                  <p className="px-3 py-4 text-[12px] text-muted-foreground">
                    {tx("settings.models.noModelResults", "No matching models.")}
                  </p>
                ) : (
                  visibleModels.map((model) => {
                    const alreadyAdded = existing.has(model.id);
                    const isSelected = selected.has(model.id);
                    return (
                      <button
                        key={model.id}
                        type="button"
                        disabled={alreadyAdded}
                        onClick={() => toggle(model.id)}
                        className={cn(
                          "flex w-full items-center gap-2.5 border-b border-border/35 px-3 py-2 text-left last:border-b-0",
                          alreadyAdded
                            ? "cursor-default opacity-55"
                            : "hover:bg-muted/45",
                          isSelected && !alreadyAdded && "bg-muted/60",
                        )}
                      >
                        <span
                          className={cn(
                            "grid h-4 w-4 shrink-0 place-items-center rounded-[5px] border",
                            isSelected || alreadyAdded
                              ? "border-foreground bg-foreground text-background"
                              : "border-input",
                          )}
                          aria-hidden
                        >
                          {isSelected || alreadyAdded ? <Check className="h-3 w-3" /> : null}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[12.5px] font-medium text-foreground">
                            {model.label ?? model.id}
                          </span>
                          {model.label && model.label !== model.id ? (
                            <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                              {model.id}
                            </span>
                          ) : null}
                        </span>
                        <span className="ml-1 flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
                          {model.free ? (
                            <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 font-medium text-emerald-600 dark:text-emerald-400">
                              {tx("settings.models.freeBadge", "Free")}
                            </span>
                          ) : null}
                          {model.context_window ? (
                            <span>{formatContextWindow(model.context_window)}</span>
                          ) : null}
                          {alreadyAdded ? (
                            <span>{tx("settings.models.alreadyAdded", "Added")}</span>
                          ) : null}
                        </span>
                      </button>
                    );
                  })
                )}
              </div>
            </>
          ) : null}
        </div>

        <DialogFooter className="border-t border-border/45 px-5 py-4 sm:space-x-2">
          {progress && progress.total > 1 ? (
            <span className="mr-auto text-[11.5px] text-muted-foreground">
              {progress.done}/{progress.total}
            </span>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            className="rounded-full"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            {tx("settings.actions.cancel", "Cancel")}
          </Button>
          <Button
            type="button"
            variant="outline"
            className="rounded-full"
            disabled={selected.size === 0 || saving}
            onClick={submit}
          >
            {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            {saving
              ? tx("settings.actions.saving", "Saving...")
              : `${tx("settings.models.importSelected", "Import selection")}${
                  selected.size ? ` (${selected.size})` : ""
                }`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CapabilityInstallNotice({
  title,
  description,
  installing = false,
}: {
  title: string;
  description: string;
  installing?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 rounded-[14px] border border-border/55 bg-muted/22 px-3.5 py-3">
      {installing ? (
        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
      ) : (
        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      )}
      <div className="min-w-0">
        <p className="text-[12.5px] font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}

function ModelsSettings({
  token,
  form,
  setForm,
  settings,
  dirty,
  saving,
  reasoningEffortError = null,
  showBrandLogos,
  providerSaving,
  needsModelReminder = false,
  oauthAuthorizeUrl,
  onProviderOAuthLogin,
  onSave,
  onCreateConfiguration,
  onImportConfigurations,
  onDeleteConfiguration,
  onToggleConfigurationEnabled,
  onSetDefaultConfiguration,
  onOpenProviders,
  onOpenAccount,
  onUpdateModelRoute,
}: {
  token: string;
  form: AgentSettingsDraft;
  setForm: Dispatch<SetStateAction<AgentSettingsDraft>>;
  settings: SettingsPayload;
  dirty: boolean;
  saving: boolean;
  /** Server refusal of the Thinking level, bound to that control. */
  reasoningEffortError?: string | null;
  showBrandLogos: boolean;
  providerSaving: string | null;
  /** Sticky BYOK reminder until a model is actually selected. */
  needsModelReminder?: boolean;
  /** Address of the sign-in in flight, so the user can open it by hand. */
  oauthAuthorizeUrl?: string | null;
  onProviderOAuthLogin: (provider: string) => void;
  onSave: () => void;
  onCreateConfiguration: () => void;
  onImportConfigurations?: () => void;
  onDeleteConfiguration?: (name: string) => Promise<void> | void;
  onToggleConfigurationEnabled?: (
    name: string,
    enabled: boolean,
  ) => Promise<void> | void;
  onSetDefaultConfiguration?: (name: string) => Promise<void> | void;
  onOpenProviders: () => void;
  onOpenAccount?: () => void;
  onUpdateModelRoute?: (role: string, preset: string) => Promise<void> | void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [activeTab, setActiveTab] = useState<"model" | "routing">("model");
  const showRoutingTab = Boolean(onUpdateModelRoute);
  const configuredProviders = settings.providers.filter(
    (provider) =>
      provider.configured && !hiddenSettingsProvider(provider.name),
  );
  const showAutoProvider = defaultPreset(settings)?.provider === "auto" || form.provider === "auto";
  const selectableProviders = uniqueProviders(configuredProviders);
  const providerOptions = showAutoProvider
    ? [{ name: "auto", label: tx("settings.values.auto", "Auto") }, ...selectableProviders]
    : selectableProviders;
  const providerValue = providerOptions.some((provider) => provider.name === form.provider)
    ? form.provider
    : "";
  const selectedPreset =
    settings.model_presets.find((preset) => preset.name === form.modelPreset) ?? null;
  const selectedPresetLocked = Boolean(
    selectedPreset && !selectedPreset.is_default && selectedPreset.routing_only,
  );
  const selectedProvider = settings.providers.find((provider) => provider.name === form.provider);
  const selectedProviderNeedsSignIn =
    selectedProvider?.auth_type === "oauth" && !selectedProvider.configured;
  const selectedProviderSigningIn = providerSaving === selectedProvider?.name;
  const modelFieldsMissing =
    !form.model.trim() ||
    !form.provider.trim() ||
    Boolean(selectedPreset && !selectedPreset.is_default && !form.presetLabel.trim());
  // The preset row carries the ladder of its saved provider+model. Once the
  // form points at another pair, the ladder has to follow: MiniMax-M3 or
  // Magistral take Auto only, and offering "low" from the previous model is
  // how a level the server refuses got submitted.
  const formProvider = form.provider.trim();
  const formModel = form.model.trim();
  const presetMatchesForm =
    selectedPreset !== null &&
    selectedPreset.provider === formProvider &&
    selectedPreset.model === formModel;
  const liveReasoningEffortKey = `${formProvider}\u0000${formModel}`;
  const [liveReasoningEffort, setLiveReasoningEffort] = useState<{
    key: string;
    values: string[];
  } | null>(null);
  useEffect(() => {
    if (!formModel || presetMatchesForm) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchReasoningEffortValues(token, formProvider, formModel)
        .then((payload) => {
          if (!cancelled) {
            setLiveReasoningEffort({ key: liveReasoningEffortKey, values: payload.values });
          }
        })
        .catch(() => {
          // Keep the preset ladder; the server still refuses a bad level on save.
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [token, formProvider, formModel, presetMatchesForm, liveReasoningEffortKey]);
  const liveLadderApplies =
    !presetMatchesForm && liveReasoningEffort?.key === liveReasoningEffortKey;
  const reasoningEffortValues = liveLadderApplies
    ? liveReasoningEffort.values
    : (selectedPreset?.reasoning_effort_values ?? ["", "low", "medium", "high"]);
  // A level the freshly chosen model does not take drops back to Auto before
  // it can be submitted. Saved presets are left alone: their stored value is
  // never rewritten behind the user's back.
  useEffect(() => {
    if (
      liveLadderApplies &&
      form.reasoningEffort &&
      !reasoningEffortValues.includes(form.reasoningEffort)
    ) {
      setForm((prev) => ({ ...prev, reasoningEffort: "" }));
    }
  }, [liveLadderApplies, reasoningEffortValues, form.reasoningEffort, setForm]);
  const reasoningEffortOptions = reasoningEffortValues.includes(form.reasoningEffort)
    ? reasoningEffortValues
    : [...reasoningEffortValues, form.reasoningEffort];

  const selectPreset = useCallback(
    (modelPreset: string) => {
      const nextPreset = settings.model_presets.find((preset) => preset.name === modelPreset);
      setForm((prev) => ({
        ...prev,
        modelPreset,
        model: nextPreset?.model ?? prev.model,
        provider: nextPreset?.is_default
          ? editableDefaultProvider(settings)
          : nextPreset?.provider ?? prev.provider,
        presetLabel: nextPreset?.label ?? modelPreset,
        contextWindowTokens: normalizeContextWindowTokens(
          nextPreset?.context_window_tokens ?? prev.contextWindowTokens,
        ),
        reasoningEffort: nextPreset?.reasoning_effort ?? "",
      }));
    },
    [setForm, settings],
  );

  const sortedPresets = useMemo(
    () =>
      [...settings.model_presets]
        .filter((preset) => {
          if (preset.routing_only) return false;
          if (
            preset.budget_allowed === false
            && !isMediaModality(preset.modality ?? "text")
            && (preset.source === "managed" || preset.provider === "navin")
          ) {
            return false;
          }
          // Hide the empty synthetic "Default" row when a named preset already
          // drives chat - otherwise it just reads "Not configured" next to the
          // real Free models and looks like something is broken.
          if (
            preset.is_default
            && !(preset.model || "").trim()
            && settings.agent.model_preset
            && settings.agent.model_preset !== "default"
          ) {
            return false;
          }
          return true;
        })
        .sort((a, b) => {
        const activeOf = (preset: (typeof settings.model_presets)[number]) =>
          preset.is_default
            ? !settings.agent.model_preset || settings.agent.model_preset === "default"
            : settings.agent.model_preset === preset.name;
        const aActive = activeOf(a);
        const bActive = activeOf(b);
        if (aActive === bActive) return 0;
        return aActive ? -1 : 1;
      }),
    [settings.agent.model_preset, settings.model_presets],
  );

  const groupedPresets = useMemo(() => {
    const groups = Object.fromEntries(
      SETTINGS_MODEL_SECTIONS.map((section) => [section, [] as typeof sortedPresets]),
    ) as Record<SettingsModelSection, typeof sortedPresets>;
    for (const preset of sortedPresets) {
      groups[settingsModelSection(preset.modality)].push(preset);
    }
    return groups;
  }, [sortedPresets]);

  return (
    <div className="space-y-7">
      {configuredProviders.length === 0 ? (
        <div className="rounded-2xl border border-primary/20 bg-primary/[0.06] px-4 py-4 sm:px-5">
          <p className="text-[14px] font-medium text-foreground">
            {tx("settings.models.needsProviderTitle", "Choose how to run models")}
          </p>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            {tx(
              "settings.models.needsProviderHelp",
              "Add your own API key or a local endpoint under Providers.",
            )}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {onOpenAccount ? (
              <Button size="sm" onClick={onOpenAccount} className="rounded-full">
                <CircleUserRound className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("settings.models.subscribeOrSignIn", "Subscribe or sign in")}
              </Button>
            ) : null}
            <Button size="sm" variant="outline" onClick={onOpenProviders} className="rounded-full">
              <KeyRound className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("settings.nav.providers", "Providers")}
            </Button>
          </div>
        </div>
      ) : null}
      {needsModelReminder ? (
        <div
          role="status"
          className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-4 sm:px-5"
        >
          <p className="text-[14px] font-medium text-foreground">
            {tx("settings.models.needsModelTitle", "Choose a model to continue")}
          </p>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            {tx(
              "settings.models.needsModelHelp",
              "Your provider is ready. Select a model below and save - chat stays blocked until then.",
            )}
          </p>
        </div>
      ) : null}
      {showRoutingTab ? (
        <SegmentedControl
          value={activeTab}
          options={[
            {
              value: "model",
              label: tx("settings.models.tabModel", "Model configuration"),
            },
            {
              value: "routing",
              label: tx("settings.modelRoutes.title", "Task routing"),
            },
          ]}
          onChange={(value) => setActiveTab(value as "model" | "routing")}
        />
      ) : null}
      {activeTab === "model" || !showRoutingTab ? (
      <>
      <SettingsGroup>
        {selectedPreset && !selectedPreset.is_default && !selectedPreset.routing_only ? (
          <SettingsRow title={tx("settings.models.configurationName", "Configuration name")}>
            <Input
              value={form.presetLabel}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, presetLabel: event.target.value }))
              }
              className="h-10 max-w-md rounded-full px-4 text-[14px]"
            />
          </SettingsRow>
        ) : null}

        {selectedPreset && !selectedPreset.is_default && !selectedPreset.routing_only ? (
          <SettingsRow title={tx("settings.rows.model", "Model")}>
            <ModelIdPicker
              token={token}
              settings={settings}
              provider={form.provider}
              value={form.model}
              showProviderLogos={showBrandLogos}
              onChange={(model) => setForm((prev) => ({ ...prev, model }))}
            />
          </SettingsRow>
        ) : null}

        <SettingsRow
          title={t("settings.rows.provider")}
          description={
            selectedPresetLocked
              ? tx(
                  "settings.models.planProviderLocked",
                  "Plan models keep their provider. Use Add model or Import models to pick another provider.",
                )
              : undefined
          }
        >
          <ProviderPicker
            providers={providerOptions}
            value={providerValue}
            emptyLabel={t("settings.byok.noConfiguredProviders")}
            showProviderLogos={showBrandLogos}
            disabled={selectedPresetLocked}
            onChange={(provider) =>
              setForm((prev) => ({
                ...prev,
                provider,
                // Model IDs are provider-specific; keep a stale OpenRouter id
                // would make Import/Add feel "stuck" on the old catalog.
                model: provider === prev.provider ? prev.model : "",
              }))
            }
          />
        </SettingsRow>

        {selectedProviderNeedsSignIn ? (
          <SettingsRow
            title={tx("settings.oauth.signInRequired", "Sign in required")}
            description={tx(
              "settings.oauth.signInBeforeSaving",
              "Sign in before saving this OAuth provider as the active model provider.",
            )}
          >
            <Button
              size="sm"
              variant="outline"
              onClick={() => selectedProvider && onProviderOAuthLogin(selectedProvider.name)}
              disabled={!selectedProvider?.oauth_login_supported || selectedProviderSigningIn}
              className="rounded-full"
            >
              {selectedProviderSigningIn ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              {selectedProviderSigningIn
                ? tx("settings.oauth.signingIn", "Signing in...")
                : tx("settings.oauth.signIn", "Sign in")}
            </Button>
          </SettingsRow>
        ) : null}

        {selectedProviderSigningIn && oauthAuthorizeUrl ? (
          <SettingsRow
            title={tx("settings.oauth.openInBrowser", "Continue in your browser")}
            description={tx(
              "settings.oauth.openInBrowserHint",
              "A browser tab should have opened. If it did not, copy this address:",
            )}
          >
            <a
              href={oauthAuthorizeUrl}
              target="_blank"
              rel="noreferrer"
              className="max-w-full break-all rounded-md bg-muted px-2 py-1 text-[11px] text-primary underline-offset-2 hover:underline"
            >
              {oauthAuthorizeUrl}
            </a>
          </SettingsRow>
        ) : null}

        <SettingsRow title={tx("settings.rows.contextWindow", "Context window")}>
          <SegmentedControl
            value={String(form.contextWindowTokens)}
            options={CONTEXT_WINDOW_TOKEN_OPTIONS.map((tokens) => ({
              value: String(tokens),
              label: contextWindowTokenLabel(tokens),
            }))}
            onChange={(value) =>
              setForm((prev) => ({
                ...prev,
                contextWindowTokens: normalizeContextWindowTokens(Number(value)),
              }))
            }
          />
        </SettingsRow>

        {reasoningEffortOptions.length > 1 || reasoningEffortError ? (
          <SettingsRow title={tx("settings.rows.reasoningEffort", "Thinking")}>
            <div className="flex flex-col items-end gap-1.5">
              <SegmentedControl
                value={form.reasoningEffort}
                options={reasoningEffortOptions.map((value) => ({
                  value,
                  label: reasoningEffortLabel(value, tx),
                }))}
                onChange={(reasoningEffort) =>
                  setForm((prev) => ({ ...prev, reasoningEffort }))
                }
              />
              {reasoningEffortError ? (
                <p
                  role="alert"
                  data-testid="settings-models-reasoning-effort-error"
                  className="max-w-[420px] text-right text-[12px] text-destructive"
                >
                  {reasoningEffortError}
                </p>
              ) : null}
            </div>
          </SettingsRow>
        ) : null}

        <SettingsRow title={tx("settings.models.importModels", "Import models")}>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {onImportConfigurations ? (
              <Button
                size="sm"
                variant="outline"
                onClick={onImportConfigurations}
                className="rounded-full"
              >
                <DownloadCloud className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("settings.models.importModels", "Import models")}
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="outline"
              onClick={onCreateConfiguration}
              className="rounded-full"
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("settings.models.addModel", "Add model")}
            </Button>
          </div>
        </SettingsRow>

        {settings.model_presets.some(
          (preset) =>
            preset.budget_allowed === false
            && !isMediaModality(preset.modality ?? "text")
            && !preset.routing_only
            && (preset.source === "managed" || preset.provider === "navin"),
        ) ? (
          <p className="px-4 text-[12.5px] leading-5 text-muted-foreground text-pretty sm:px-5">
            {tx(
              "settings.models.budgetHiddenNote",
              "Opus 5+, Fable 5+ and GPT 5.6 are paused at this monthly usage. Other plan models stay available until renewal.",
            )}
          </p>
        ) : null}
      </SettingsGroup>

        {SETTINGS_MODEL_SECTIONS.map((section) => {
          const sectionPresets = groupedPresets[section];
          if (sectionPresets.length === 0) return null;
          return (
            <SettingsGroup key={section}>
              <div className="px-4 py-3 sm:px-5">
                <p className="text-[13px] font-semibold text-foreground">
                  {tx(
                    `settings.models.sections.${section}`,
                    section === "text"
                      ? "Text and multimodal"
                      : section === "image"
                        ? "Image"
                        : section === "video"
                          ? "Video"
                          : "Audio",
                  )}
                </p>
              </div>
              {sectionPresets.map((preset) => {
          const modality = preset.modality ?? "text";
          const isMedia = isMediaModality(modality);
          // Chat "Active" only applies to text models - media presets are
          // specialty tools and can never be the account default.
          const isActive = !isMedia && (
            preset.is_default
              ? !settings.agent.model_preset || settings.agent.model_preset === "default"
              : settings.agent.model_preset === preset.name
          );
          const isSelected = form.modelPreset === preset.name;
          const isPlan = Boolean(preset.locked || preset.source === "managed");
          const isEnabled = preset.enabled !== false;
          const budgetBlocked = !isMedia && preset.budget_allowed === false;
          return (
            <div
              key={preset.name}
              className={cn(
                "flex items-stretch gap-1 border-l-2 transition-colors",
                isSelected
                  ? "border-l-foreground bg-muted/35"
                  : "border-l-transparent hover:bg-muted/20",
                !isEnabled && "opacity-55",
              )}
            >
              <button
                type="button"
                onClick={() => selectPreset(preset.name)}
                className="min-w-0 flex-1 px-4 py-3 text-left sm:px-5"
              >
                <p className="flex items-center gap-1.5 text-[13.5px] font-medium text-foreground">
                  <span className="truncate">{preset.label ?? preset.name}</span>
                  {isPlan ? (
                    <span className="shrink-0 rounded-full bg-foreground/8 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {tx("settings.models.planBadge", "Plan")}
                    </span>
                  ) : null}
                  {!isEnabled ? (
                    <span className="shrink-0 rounded-full bg-muted px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {tx("settings.models.hiddenBadge", "Hidden")}
                    </span>
                  ) : null}
                  {isActive ? (
                    <span className="shrink-0 rounded-full bg-emerald-500/12 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
                      {tx("settings.models.active", "Active")}
                    </span>
                  ) : null}
                  {isMedia ? (
                    <span
                      className={cn(
                        "ml-auto shrink-0 rounded px-1 py-px text-[8px] font-semibold lowercase tracking-wide ring-1",
                        modalityBadgeClass(modality),
                      )}
                      title={tx(
                        "settings.models.mediaNotDefault",
                        "Media model - cannot be the chat default",
                      )}
                    >
                      {modalityBadgeLabel(modality)}
                    </span>
                  ) : isVisionChatModel(preset.model) ? (
                    <span
                      className={cn(
                        "ml-auto shrink-0 rounded px-1 py-px text-[8px] font-semibold lowercase tracking-wide ring-1",
                        visionBadgeClass(),
                      )}
                      title={tx(
                        "settings.models.visionBadgeHint",
                        "Vision / multimodal - image, video, audio input",
                      )}
                    >
                      {visionBadgeLabel()}
                    </span>
                  ) : null}
                </p>
                <p className="mt-0.5 truncate text-[12px] text-muted-foreground">
                  {preset.model
                    ? `${preset.provider} · ${preset.model}`
                    : tx("settings.values.notConfigured", "Not configured")}
                </p>
              </button>
              <div className="my-auto mr-2 flex shrink-0 items-center gap-0.5">
                {onSetDefaultConfiguration && !isMedia && Boolean(preset.model) ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (isActive) return;
                      void onSetDefaultConfiguration(preset.name);
                    }}
                    aria-label={
                      isActive
                        ? tx("settings.models.defaultConfiguration", "Default model")
                        : tx("settings.models.setAsDefault", "Set as default")
                    }
                    title={
                      budgetBlocked
                        ? tx(
                            "settings.models.budgetBlockedHelp",
                            "Not available at the current monthly usage",
                          )
                        : isActive
                        ? tx("settings.models.defaultConfiguration", "Default model")
                        : tx(
                            "settings.models.setAsDefaultHelp",
                            "Use this model as the chat default",
                          )
                    }
                    disabled={isActive || budgetBlocked}
                    className={cn(
                      "h-8 w-8 rounded-full p-0",
                      isActive
                        ? "text-amber-500 disabled:opacity-100"
                        : "text-muted-foreground hover:text-amber-500",
                    )}
                  >
                    <Star
                      className="h-3.5 w-3.5"
                      aria-hidden
                      fill={isActive ? "currentColor" : "none"}
                    />
                  </Button>
                ) : null}
                {onToggleConfigurationEnabled && !preset.is_default ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void onToggleConfigurationEnabled(preset.name, !isEnabled)
                    }
                    aria-label={
                      isEnabled
                        ? tx("settings.models.hideInChat", "Hide in chat")
                        : tx("settings.models.showInChat", "Show in chat")
                    }
                    title={
                      isEnabled
                        ? tx(
                            "settings.models.hideInChatHelp",
                            "Hide from the chat model list (does not delete)",
                          )
                        : tx(
                            "settings.models.showInChatHelp",
                            "Show again in the chat model list",
                          )
                    }
                    className="h-8 w-8 rounded-full p-0 text-muted-foreground"
                  >
                    {isEnabled ? (
                      <Eye className="h-3.5 w-3.5" aria-hidden />
                    ) : (
                      <EyeOff className="h-3.5 w-3.5" aria-hidden />
                    )}
                  </Button>
                ) : null}
                {onDeleteConfiguration
                && !isPlan
                && (!preset.is_default || preset.model) ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void onDeleteConfiguration(preset.name)}
                    aria-label={tx("settings.models.deleteConfiguration", "Delete configuration")}
                    title={tx("settings.models.deleteConfiguration", "Delete configuration")}
                    className="h-8 w-8 rounded-full p-0 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                ) : null}
              </div>
            </div>
          );
              })}
            </SettingsGroup>
          );
        })}

      <SettingsGroup>
        <SettingsFooter
          dirty={dirty}
          saving={saving}
          saved={false}
          disabled={selectedProviderNeedsSignIn || modelFieldsMissing}
          message={
            selectedProviderNeedsSignIn
              ? tx(
                  "settings.oauth.signInBeforeSaving",
                  "Sign in before saving this OAuth provider as the active model provider.",
                )
              : undefined
          }
          onSave={onSave}
        />
      </SettingsGroup>
      </>
      ) : null}
      {showRoutingTab && activeTab === "routing" && onUpdateModelRoute ? (
        <section>
          <p className="mb-3 px-1 text-[13px] leading-5 text-muted-foreground">
            {tx(
              "settings.modelRoutes.intro",
              "Assign a model to each task type. Workflows apply it automatically: /blueprint → Planning, /forge and Code → Medium, security audits → Security, studios (RiskLens, SEO…) → their mapped role. You can also switch with /pilot <task>.",
            )}
          </p>
          <div className="mb-4 flex items-start gap-3 rounded-2xl border border-border/50 bg-card px-4 py-3.5 shadow-[0_2px_10px_rgba(15,23,42,0.04)]">
            <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-emerald-500/12 text-emerald-600 dark:text-emerald-400">
              <Zap className="h-3.5 w-3.5" strokeWidth={2} />
            </span>
            <div className="min-w-0 text-[12.5px] leading-5 text-muted-foreground">
              <p className="font-medium text-foreground/85">
                {tx("settings.modelRoutes.savingsTitle", "Why route by task? Real cost savings.")}
              </p>
              <p className="mt-1">
                {tx(
                  "settings.modelRoutes.savingsBody",
                  "Frontier models (Opus, GPT-5…) often cost 10-30x more per token than economy models (Haiku, Flash…). Most day-to-day requests are simple or medium: routing them to a cheaper model can cut your bill by 50-80% with no quality loss where it matters - the hard tasks still get your best model.",
                )}
              </p>
              <p className="mt-1">
                {tx(
                  "settings.modelRoutes.savingsExample",
                  "Example: a 100K-token turn ≈ $1.50 on a frontier model vs ≈ $0.10 on an economy model. Over hundreds of turns per week, the difference adds up fast.",
                )}
              </p>
            </div>
          </div>
          <SettingsGroup>
            {MODEL_ROUTE_ROLES.map(({ role, fallbackLabel, fallbackHelp }) => (
              <SettingsRow
                key={role}
                title={tx(`settings.modelRoutes.roles.${role}`, fallbackLabel)}
                description={tx(`settings.modelRoutes.rolesHelp.${role}`, fallbackHelp)}
              >
                <select
                  value={settings.model_routes?.[role] ?? ""}
                  onChange={(event) => void onUpdateModelRoute(role, event.target.value)}
                  aria-label={tx(`settings.modelRoutes.roles.${role}`, fallbackLabel)}
                  className={cn(
                    "h-9 w-[min(260px,62vw)] rounded-full border border-input bg-background px-3 text-[13px]",
                    "text-foreground shadow-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                >
                  <option value="">
                    {tx("settings.modelRoutes.unset", "Not routed")}
                  </option>
                  {settings.model_presets.filter((preset) =>
                    canBeChatDefault(preset.modality, preset.model)
                    && (role !== "vision" || (preset.vision ?? isVisionChatModel(preset.model)))
                    && (role !== "computer" || (preset.grounding ?? isVisionChatModel(preset.model))),
                  ).map((preset) => (
                    <option key={preset.name} value={preset.name}>
                      {preset.label} · {preset.model}
                    </option>
                  ))}
                </select>
              </SettingsRow>
            ))}
          </SettingsGroup>
        </section>
      ) : null}
    </div>
  );
}

function ProvidersSettings({
  token,
  settings,
  navinFeatures,
  featureAction,
  capabilityError,
  expandedProvider,
  providerForms,
  visibleProviderKeys,
  editingProviderKeys,
  providerSaving,
  query,
  showBrandLogos,
  onQueryChange,
  onToggleProvider,
  onToggleProviderKey,
  onToggleProviderKeyEditing,
  onChangeProviderForm,
  onSaveProvider,
  onDisconnectProvider,
  oauthAuthorizeUrl,
  onProviderOAuthLogin,
  onProviderOAuthLogout,
  onResetProviderDraft,
  onOllamaConfigured,
  imageProviderRestartPending,
  onRestart,
  isRestarting,
  onOpenAccount,
}: {
  token: string;
  settings: SettingsPayload;
  navinFeatures: NavinFeaturesPayload | null;
  featureAction: string | null;
  capabilityError: string | null;
  expandedProvider: string | null;
  providerForms: Record<string, ProviderForm>;
  visibleProviderKeys: Record<string, boolean>;
  editingProviderKeys: Record<string, boolean>;
  providerSaving: string | null;
  query: string;
  showBrandLogos: boolean;
  onQueryChange: (query: string) => void;
  onToggleProvider: (provider: string) => void;
  onToggleProviderKey: (provider: string) => void;
  onToggleProviderKeyEditing: (provider: string) => void;
  onChangeProviderForm: (provider: string, value: Partial<ProviderForm>) => void;
  onSaveProvider: (provider: string) => void;
  onDisconnectProvider: (provider: string) => void;
  /** Address of the sign-in in flight, so the user can open it by hand. */
  oauthAuthorizeUrl?: string | null;
  onProviderOAuthLogin: (provider: string) => void;
  onProviderOAuthLogout: (provider: string) => void;
  onResetProviderDraft: (provider: string) => void;
  onOllamaConfigured: (settings: SettingsPayload) => void;
  imageProviderRestartPending: boolean;
  onRestart?: () => void;
  isRestarting?: boolean;
  onOpenAccount?: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { account } = useAccount();
  const [connectionTests, setConnectionTests] = useState<
    Record<string, ProviderConnectionTestPayload | { status: "testing" }>
  >({});
  const accountConnected = Boolean(account?.connected);
  const configuredProviders = orderUnconfiguredProviders(
    settings.providers.filter((provider) => {
      if (hiddenSettingsProvider(provider.name)) return false;
      if (!provider.configured) return false;
      return true;
    }),
  );
  const unconfiguredProviders = useMemo(
    () =>
      orderUnconfiguredProviders(
        settings.providers.filter((provider) => {
          if (provider.configured || hiddenSettingsProvider(provider.name)) {
            return false;
          }
          return true;
        }),
      ),
    [settings.providers],
  );
  const filteredConfigured = filterProviders(configuredProviders, query);
  const filteredUnconfigured = filterProviders(unconfiguredProviders, query);
  const renderProviderRow = (provider: SettingsPayload["providers"][number]) => {
    const expanded = expandedProvider === provider.name;
    const form = providerForms[provider.name] ?? {
      apiKey: "",
      apiBase: provider.api_base ?? provider.resolved_api_base ?? provider.default_api_base ?? "",
      apiType: provider.api_type ?? "auto",
      authMode: provider.auth_mode ?? "none",
      endpointRegion: provider.endpoint_region ?? provider.connection?.default_region ?? "",
      accessPlan: provider.access_plan ?? provider.connection?.default_plan ?? "",
      wireProtocol: provider.wire_protocol ?? provider.connection?.default_protocol ?? "",
    };
    const connection = provider.connection;
    const connectionTest = connectionTests[provider.name];
    const applyConnectionField = (
      patch: Partial<Pick<ProviderForm, "endpointRegion" | "accessPlan" | "wireProtocol">>,
    ) => {
      if (!connection) {
        onChangeProviderForm(provider.name, patch);
        return;
      }
      const nextRegion = patch.endpointRegion ?? form.endpointRegion;
      const nextPlan = patch.accessPlan ?? form.accessPlan;
      const nextProtocol = patch.wireProtocol ?? form.wireProtocol;
      onChangeProviderForm(provider.name, {
        ...patch,
        apiBase:
          lookupConnectionBase(connection, nextRegion, nextPlan, nextProtocol)
          ?? form.apiBase,
      });
    };
    const runConnectionTest = async () => {
      setConnectionTests((prev) => ({ ...prev, [provider.name]: { status: "testing" } }));
      try {
        const result = await testProviderConnection(token, {
          provider: provider.name,
          apiKey: form.apiKey.trim() || undefined,
          apiBase: form.apiBase.trim(),
          endpointRegion: form.endpointRegion,
          accessPlan: form.accessPlan,
          wireProtocol: form.wireProtocol,
        });
        setConnectionTests((prev) => ({ ...prev, [provider.name]: result }));
      } catch (err) {
        setConnectionTests((prev) => ({
          ...prev,
          [provider.name]: {
            ok: false,
            provider: provider.name,
            label: provider.label,
            status: "error",
            message: (err as Error).message,
            models: [],
            model_count: 0,
            capabilities: [],
            latency_ms: 0,
          },
        }));
      }
    };
    const saving = providerSaving === provider.name;
    const isOauthProvider = provider.auth_type === "oauth";
    const keyVisible = !!visibleProviderKeys[provider.name];
    const editingKey = !provider.configured || !!editingProviderKeys[provider.name];
    const apiKeyRequired = provider.api_key_required ?? true;
    const authMode = form.authMode ?? provider.auth_mode ?? "none";
    const showBearerFields = !provider.supports_auth_mode || authMode === "bearer";
    const apiKey = form.apiKey.trim();
    const apiBase = form.apiBase.trim();
    const missingRequiredApiKey =
      !isOauthProvider && apiKeyRequired && !provider.configured && !apiKey && showBearerFields;
    const missingOptionalCredential =
      !isOauthProvider
      && !apiKeyRequired
      && !provider.configured
      && (
        provider.supports_auth_mode
          ? !apiBase
          : !apiKey && !apiBase
      );
    const supportName = provider.name === "bedrock"
      ? "bedrock"
      : provider.name === "azure_openai"
        ? "azure"
        : null;
    const supportFeature = supportName
      ? (navinFeatures?.features ?? []).find((feature) => feature.name === supportName)
      : null;
    return (
      <div key={provider.name} className="divide-y divide-border/45">
        <button
          type="button"
          onClick={() => onToggleProvider(provider.name)}
          className="flex min-h-[70px] w-full items-center justify-between gap-4 px-4 py-3 text-left transition-colors hover:bg-muted/35 sm:px-5"
        >
          <span className="flex min-w-0 items-center gap-3">
            <ProviderIcon
              provider={provider.name}
              showBrandLogos={showBrandLogos}
            />
            <span className="min-w-0">
              <span className="block truncate text-[15px] font-semibold leading-5 text-foreground">
                {provider.label}
              </span>
              <span className="block truncate text-[12px] text-muted-foreground">
                {provider.name === "navin" || provider.managed
                  ? tx(
                      "settings.providers.navinManagedSubtitle",
                      "Navin subscription",
                    )
                  : provider.api_base || provider.default_api_base || provider.name}
              </span>
            </span>
          </span>
          <StatusPill tone={provider.configured ? "success" : "neutral"}>
            {provider.managed
              ? tx("settings.providers.planConfigured", "Plan")
              : isOauthProvider
              ? provider.configured
                ? tx("settings.oauth.signedIn", "Signed in")
                : tx("settings.oauth.notSignedIn", "Not signed in")
              : provider.configured
                ? t("settings.byok.configured")
                : t("settings.byok.notConfigured")}
          </StatusPill>
        </button>

        {expanded ? (
          <div className="space-y-3 bg-muted/18 px-4 py-4 sm:px-5">
            {provider.managed ? (
              <p className="rounded-[14px] border border-border/45 bg-background/75 px-4 py-3 text-[12.5px] leading-5 text-muted-foreground">
                {tx(
                  "settings.providers.navinManagedHelp",
                  "Configured by your Navin subscription. Plan models stay available here. You can still add z.ai, Anthropic, OpenAI or any other provider below with your own keys.",
                )}
              </p>
            ) : null}
            {provider.name === "ollama" ? (
              <OllamaSetupPanel
                token={token}
                onConfigured={(payload) => {
                  onOllamaConfigured(payload);
                  const ollama = payload.providers.find((row) => row.name === "ollama");
                  if (ollama) {
                    onChangeProviderForm("ollama", {
                      apiBase: ollama.api_base ?? ollama.default_api_base ?? "",
                    });
                  }
                }}
              />
            ) : null}
            {supportFeature && !supportFeature.installed ? (
              <CapabilityInstallNotice
                title={tx("settings.capabilities.providerSupport", "Provider support")}
                description={tx(
                  "settings.capabilities.providerInstallOnSave",
                  "Required support will be installed automatically when you save this provider.",
                )}
                installing={featureAction === `enable:${supportName}`}
              />
            ) : null}
            {supportName && capabilityError ? (
              <p className="text-[12px] text-destructive">{capabilityError}</p>
            ) : null}
            {isOauthProvider ? (
              <div className="flex flex-col gap-3 rounded-[18px] border border-border/45 bg-background/75 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold text-foreground">
                    {tx("settings.oauth.authentication", "OAuth authentication")}
                  </p>
                  <p className="mt-1 truncate text-[12px] text-muted-foreground">
                    {provider.configured
                      ? t("settings.oauth.signedInAs", {
                          account: provider.oauth_account || provider.label,
                          defaultValue: "Signed in as {{account}}",
                        })
                      : tx("settings.oauth.signInHelp", "Sign in from this device; no API key is stored in config.")}
                  </p>
                </div>
                <div className="flex shrink-0 justify-end gap-2">
                  {provider.configured ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onProviderOAuthLogout(provider.name)}
                      disabled={saving}
                      className="rounded-full"
                    >
                      {tx("settings.oauth.signOut", "Sign out")}
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onProviderOAuthLogin(provider.name)}
                    disabled={saving || !provider.oauth_login_supported}
                    className="rounded-full"
                  >
                    {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                    {saving
                      ? tx("settings.oauth.signingIn", "Signing in...")
                      : provider.configured
                        ? tx("settings.oauth.signInAgain", "Sign in again")
                        : tx("settings.oauth.signIn", "Sign in")}
                  </Button>
                </div>
                {saving && oauthAuthorizeUrl ? (
                  <p className="basis-full text-[11px] text-muted-foreground">
                    {tx(
                      "settings.oauth.openInBrowserHint",
                      "A browser tab should have opened. If it did not, copy this address:",
                    )}{" "}
                    <a
                      href={oauthAuthorizeUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="break-all text-primary underline-offset-2 hover:underline"
                    >
                      {oauthAuthorizeUrl}
                    </a>
                  </p>
                ) : null}
              </div>
            ) : provider.managed ? null : (
              <>
            {provider.name === "custom" || provider.name === "custom_anthropic" ? (
              <p className="rounded-[14px] border border-border/45 bg-background/75 px-4 py-3 text-[12.5px] leading-5 text-muted-foreground">
                {provider.name === "custom"
                  ? tx(
                      "settings.providers.customOpenAIHelp",
                      "Any OpenAI-compatible server: paste the base URL (…/v1), then Test connection. Auth can stay None for a local server.",
                    )
                  : tx(
                      "settings.providers.customAnthropicHelp",
                      "Any Anthropic-compatible server: paste the base URL, then Test connection. Messages use the Anthropic protocol, not OpenAI.",
                    )}
              </p>
            ) : null}
            {connection ? (
              <div className="space-y-3">
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tx("settings.connection.region", "China or International")}
                  </span>
                  <select
                    className="h-9 w-full rounded-full border border-input bg-background px-3 text-[13px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                    value={form.endpointRegion || connection.default_region}
                    onChange={(event) => applyConnectionField({ endpointRegion: event.target.value })}
                  >
                    {connection.regions.map((option) => (
                      <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tx("settings.connection.plan", "Pay-as-you-go, Token or Coding")}
                  </span>
                  <select
                    className="h-9 w-full rounded-full border border-input bg-background px-3 text-[13px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                    value={form.accessPlan || connection.default_plan}
                    onChange={(event) => applyConnectionField({ accessPlan: event.target.value })}
                  >
                    {connection.plans.map((option) => (
                      <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tx("settings.connection.protocol", "Protocol")}
                  </span>
                  <select
                    className="h-9 w-full rounded-full border border-input bg-background px-3 text-[13px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                    value={form.wireProtocol || connection.default_protocol}
                    onChange={(event) => applyConnectionField({ wireProtocol: event.target.value })}
                  >
                    {connection.protocols.map((option) => (
                      <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                  </select>
                </label>
                {connection.notes ? (
                  <p className="text-[11.5px] leading-5 text-muted-foreground">{connection.notes}</p>
                ) : null}
                {connection.docs_url ? (
                  <a
                    href={connection.docs_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-block text-[12px] text-primary underline-offset-2 hover:underline"
                  >
                    {tx("settings.connection.docs", "Official documentation")}
                  </a>
                ) : null}
              </div>
            ) : null}
            {provider.supports_auth_mode ? (
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {tx("settings.byok.authentication", "Authentication")}
                </span>
                <select
                  className="h-9 w-full rounded-full border border-input bg-background px-3 text-[13px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                  value={authMode}
                  onChange={(event) => {
                    const nextMode = event.target.value === "bearer" ? "bearer" : "none";
                    onChangeProviderForm(provider.name, {
                      authMode: nextMode,
                      ...(nextMode === "none" ? { apiKey: "" } : {}),
                    });
                  }}
                >
                  <option value="none">
                    {tx("settings.byok.authNone", "None")}
                  </option>
                  <option value="bearer">
                    {tx("settings.byok.authBearer", "Bearer token")}
                  </option>
                </select>
                <span className="block text-[11.5px] text-muted-foreground">
                  {authMode === "bearer"
                    ? tx(
                        "settings.byok.authBearerHelp",
                        "Sent as Authorization: Bearer ... Useful for LM Studio tokens, vLLM --api-key, or a company gateway.",
                      )
                    : tx(
                        "settings.byok.authNoneHelp",
                        "Localhost default - no token required (Ollama / local LM Studio).",
                      )}
                </span>
              </label>
            ) : null}
            {showBearerFields ? (
            <label className="block space-y-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                {provider.supports_auth_mode
                  ? tx("settings.byok.bearerToken", "Bearer token")
                  : t("settings.byok.apiKey")}
              </span>
              <div className="relative">
                {editingKey ? (
                  <>
                    <Input
                      type={keyVisible ? "text" : "password"}
                      value={form.apiKey}
                      onChange={(event) =>
                        onChangeProviderForm(provider.name, { apiKey: event.target.value })
                      }
                      placeholder={
                        provider.configured
                          ? t("settings.byok.apiKeyConfiguredPlaceholder")
                          : t("settings.byok.apiKeyPlaceholder")
                      }
                      className="h-9 rounded-full pr-11 text-[13px]"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => onToggleProviderKey(provider.name)}
                      aria-label={
                        keyVisible
                          ? t("settings.byok.hideApiKey")
                          : t("settings.byok.showApiKey")
                      }
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      {keyVisible ? (
                        <EyeOff className="h-3.5 w-3.5" aria-hidden />
                      ) : (
                        <Eye className="h-3.5 w-3.5" aria-hidden />
                      )}
                    </Button>
                  </>
                ) : (
                  <>
                    <div className="flex h-9 items-center rounded-full border border-input bg-background px-3 pr-11 text-[13px] text-muted-foreground">
                      {provider.api_key_hint ?? t("settings.byok.configuredKeyHint")}
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => onToggleProviderKeyEditing(provider.name)}
                      aria-label={t("settings.actions.edit")}
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  </>
                )}
              </div>
            </label>
            ) : null}
            <label className="block space-y-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                {provider.supports_auth_mode
                  ? tx("settings.byok.endpoint", "Endpoint")
                  : t("settings.byok.apiBase")}
              </span>
              <Input
                value={form.apiBase}
                onChange={(event) =>
                  onChangeProviderForm(provider.name, { apiBase: event.target.value })
                }
                placeholder={provider.default_api_base ?? t("settings.byok.apiBasePlaceholder")}
                className="h-9 rounded-full text-[13px]"
              />
            </label>
            {provider.name === "openai" ? (
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {tx("settings.byok.apiType", "API type")}
                </span>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="outline"
                      className="h-9 w-full justify-between rounded-full px-3 text-[13px]"
                    >
                      <span>
                        {OPENAI_API_TYPE_OPTIONS.find((option) => option.value === form.apiType)?.label ??
                          form.apiType}
                      </span>
                      <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="min-w-[220px]">
                    {OPENAI_API_TYPE_OPTIONS.map((option) => (
                      <DropdownMenuItem
                        key={option.value}
                        onSelect={() => onChangeProviderForm(provider.name, { apiType: option.value })}
                      >
                        {option.label}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </label>
            ) : null}
            <div className="flex items-center justify-between gap-2">
              <div>
                {provider.configured ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onDisconnectProvider(provider.name)}
                    disabled={saving}
                    className="rounded-full text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    {tx("settings.providers.disconnect", "Disconnect")}
                  </Button>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onResetProviderDraft(provider.name)}
                  className="rounded-full"
                >
                  {t("settings.actions.cancel")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void runConnectionTest()}
                  disabled={saving || connectionTest?.status === "testing"}
                  className="rounded-full"
                >
                  {connectionTest?.status === "testing"
                    ? tx("settings.connection.testing", "Testing...")
                    : tx("settings.connection.test", "Test connection")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onSaveProvider(provider.name)}
                  disabled={saving || missingRequiredApiKey || missingOptionalCredential}
                  className="rounded-full"
                >
                  {saving ? t("settings.actions.saving") : tx("settings.providers.saveProvider", "Save provider")}
                </Button>
              </div>
            </div>
            {connectionTest && connectionTest.status !== "testing" ? (
              <div className="rounded-[14px] border border-border/45 bg-background/75 px-4 py-3 text-[12.5px] leading-5">
                <p className={connectionTest.ok ? "text-foreground" : "text-destructive"}>
                  {connectionTest.message
                    || (connectionTest.ok
                      ? tx("settings.connection.ok", "Connection succeeded.")
                      : tx("settings.connection.failed", "Connection failed."))}
                </p>
                {connectionTest.ok ? (
                  <p className="mt-1 text-muted-foreground">
                    {t("settings.connection.summary", {
                      count: connectionTest.model_count,
                      ms: connectionTest.latency_ms,
                      defaultValue: "{{count}} models · {{ms}} ms",
                    })}
                    {connectionTest.capabilities.length
                      ? ` · ${connectionTest.capabilities.join(", ")}`
                      : ""}
                  </p>
                ) : null}
                {connectionTest.quota ? (
                  <p className="mt-1 break-all text-muted-foreground">
                    {tx("settings.connection.quota", "Quota")}: {JSON.stringify(connectionTest.quota)}
                  </p>
                ) : null}
              </div>
            ) : null}
              </>
            )}
          </div>
        ) : null}
      </div>
    );
  };
  return (
    <div className="space-y-6">
      {!accountConnected && onOpenAccount ? (
        <div className="rounded-2xl border border-border/55 bg-card/60 px-4 py-4 sm:px-5">
          <p className="text-[14px] font-medium text-foreground">
            {tx(
              "settings.providers.subscribeTitle",
              "Prefer managed models?",
            )}
          </p>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            {tx(
              "settings.providers.subscribeHelp",
              "Connect your navin.live account to unlock plan models and usage. Or stay here and add your own API keys.",
            )}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" onClick={onOpenAccount} className="rounded-full">
              <CircleUserRound className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("settings.models.subscribeOrSignIn", "Subscribe or sign in")}
            </Button>
          </div>
        </div>
      ) : null}
      <div className="rounded-2xl border border-border/50 bg-gradient-to-br from-primary/[0.07] via-card to-teal/10 px-4 py-5 sm:px-5">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-[0_10px_24px_rgba(3,105,255,0.28)]">
            <KeyRound className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <h3 className="text-[16px] font-semibold tracking-[-0.02em] text-foreground">
              {tx("settings.providers.heroTitle", "Providers")}
            </h3>
            <p className="mt-1 max-w-[42rem] text-[13px] leading-6 text-muted-foreground">
              {t("settings.byok.description")}
            </p>
            <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
              <span className="rounded-full bg-background/80 px-2.5 py-1 font-medium text-foreground ring-1 ring-border/60">
                {t("settings.overview.configuredCount", {
                  defaultValue: "{{count}} configured",
                  count: configuredProviders.length,
                })}
              </span>
              <span className="rounded-full bg-background/60 px-2.5 py-1 text-muted-foreground ring-1 ring-border/50">
                {t("settings.overview.totalProviders", {
                  defaultValue: "{{count}} available",
                  count: settings.providers.length,
                })}
              </span>
            </div>
          </div>
        </div>
      </div>
      {imageProviderRestartPending && onRestart ? (
        <div className="flex min-h-[48px] items-center justify-between gap-3 border-y border-border/55 py-3">
          <p className="text-[13px] leading-5 text-muted-foreground">
            {tx("settings.status.imageProviderRestart", "Provider support changed. Restart when ready.")}
          </p>
          <div className="shrink-0">
            <Button
              size="sm"
              variant="ghost"
              onClick={onRestart}
              disabled={isRestarting}
              className="rounded-full"
            >
              {isRestarting ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              )}
              {isRestarting ? t("app.system.restarting") : t("app.system.restart")}
            </Button>
          </div>
        </div>
      ) : null}
      <div className="relative">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={tx("settings.providers.searchPlaceholder", "Search providers")}
          className="h-11 rounded-2xl border-border/60 bg-card/90 pl-10 text-[13px] shadow-sm"
        />
      </div>
      <ProviderSection
        title={t("settings.byok.configuredSection")}
        count={filteredConfigured.length}
        empty={t("settings.byok.noConfiguredProviders")}
      >
        {filteredConfigured.map(renderProviderRow)}
      </ProviderSection>
      <ProviderSection
        title={t("settings.byok.notConfiguredSection")}
        count={filteredUnconfigured.length}
        empty={tx("settings.providers.noMatches", "No providers match this search.")}
      >
        {filteredUnconfigured.map(renderProviderRow)}
      </ProviderSection>
    </div>
  );
}

function VideoGenerationSettings({
  settings,
  form,
  dirty,
  saving,
  onChangeForm,
  onSave,
  onOpenProviders,
  showBrandLogos,
  onRestart,
  isRestarting,
  requiresRestartPending,
}: {
  settings: SettingsPayload;
  form: VideoGenerationSettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<VideoGenerationSettingsUpdate>>;
  onSave: () => void;
  onOpenProviders: () => void;
  showBrandLogos: boolean;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const videoGeneration = settings.video_generation ?? DEFAULT_VIDEO_GENERATION_SETTINGS;
  const selectedProvider = form.provider
    ? videoGeneration.providers.find((provider) => provider.name === form.provider)
    : undefined;
  const providerConfigured = !!selectedProvider?.configured;
  const missingCredential = form.enabled && !providerConfigured;
  const followsCredential = !!videoGeneration.enabled_auto && form.enabled;
  const aspectOptions = optionRowsWithCurrent(
    VIDEO_ASPECT_RATIO_OPTIONS.map((value) => ({ name: value, label: value })),
    form.defaultAspectRatio,
  );
  const resolutionOptions = optionRowsWithCurrent(
    VIDEO_RESOLUTION_OPTIONS.map((value) => ({
      name: value,
      label: value || tx("settings.values.providerDefault", "Provider default"),
    })),
    form.defaultResolution,
  );

  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{tx("settings.sections.videoGeneration", "Video generation")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.videoGeneration", "Video generation")}
            description={
              followsCredential
                ? tx(
                    "settings.help.videoGenerationAuto",
                    "On because the selected provider has a credential. Turn it off to keep generate_video out of chats.",
                  )
                : tx("settings.help.videoGeneration", "Expose generate_video in chats when a configured video provider is available.")
            }
          >
            <ToggleButton
              checked={form.enabled}
              onChange={(enabled) => onChangeForm((prev) => ({ ...prev, enabled }))}
              ariaLabel={tx("settings.rows.videoGeneration", "Video generation")}
              label={form.enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.videoProvider", "Video provider")}
            description={tx("settings.help.videoProvider", "Choose the provider used by generate_video (Veo via Gemini, Sora via OpenAI, Hailuo via MiniMax).")}
          >
            <ProviderPicker
              providers={videoGeneration.providers}
              value={form.provider}
              emptyLabel={tx("settings.video.selectProvider", "Select provider")}
              showProviderLogos={showBrandLogos}
              onChange={(provider) => onChangeForm((prev) => ({ ...prev, provider, model: "" }))}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.videoProviderStatus", "Provider status")}
            description={tx("settings.help.videoProviderStatus", "Video generation reuses provider credentials from Providers.")}
          >
            <div className="flex flex-wrap items-center justify-end gap-2">
              <StatusPill tone={providerConfigured ? "success" : "neutral"}>
                {providerConfigured
                  ? tx("settings.values.configured", "Configured")
                  : tx("settings.values.notConfigured", "Not configured")}
              </StatusPill>
              {!providerConfigured ? (
                <Button size="sm" variant="outline" onClick={onOpenProviders} className="rounded-full">
                  {tx("settings.video.configureProvider", "Configure provider")}
                </Button>
              ) : null}
            </div>
          </SettingsRow>
          <SettingsRow title={tx("settings.rows.videoProviderBase", "Provider base")}>
            <span className="max-w-[320px] truncate text-right text-[13px] text-muted-foreground">
              {selectedProvider?.api_base || selectedProvider?.default_api_base || selectedProvider?.name || tx("settings.values.notAvailable", "Not available")}
            </span>
          </SettingsRow>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.videoDefaults", "Defaults")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.videoModel", "Video model")}
            description={tx(
              "settings.help.videoModel",
              "Managed list when using Navin, or type any provider model id for BYOK.",
            )}
          >
            <div className="flex w-[min(320px,75vw)] flex-col items-end gap-1.5">
              <MediaModelPicker key={`video:${form.provider}`} kind="video" provider={form.provider}
                configured={providerConfigured} model={form.model}
                onModelChange={(model) => onChangeForm((prev) => ({ ...prev, model }))} />
            </div>
          </SettingsRow>
          {videoGeneration.budget_caution ? (
            <div className="rounded-xl border border-amber-200/80 bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-950">
              {videoGeneration.budget_caution}
            </div>
          ) : null}
          <SettingsRow
            title={tx("settings.rows.defaultAspectRatio", "Default aspect")}
            description={tx("settings.help.defaultVideoAspectRatio", "16:9 for YouTube/web, 9:16 for Reels, TikTok, and Shorts.")}
          >
            <ProviderPicker
              providers={aspectOptions}
              value={form.defaultAspectRatio}
              emptyLabel={tx("settings.video.selectAspect", "Select aspect")}
              onChange={(defaultAspectRatio) =>
                onChangeForm((prev) => ({ ...prev, defaultAspectRatio }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.defaultDuration", "Default duration (s)")}
            description={tx("settings.help.defaultDuration", "Clip length in seconds when the request does not specify one.")}
          >
            <NumberInput
              value={form.defaultDurationSeconds}
              min={1}
              max={60}
              onChange={(defaultDurationSeconds) =>
                onChangeForm((prev) => ({ ...prev, defaultDurationSeconds }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.defaultResolution", "Default resolution")}
            description={tx("settings.help.defaultResolution", "Resolution hint sent to providers that support it.")}
          >
            <ProviderPicker
              providers={resolutionOptions}
              value={form.defaultResolution}
              emptyLabel={tx("settings.video.selectResolution", "Select resolution")}
              onChange={(defaultResolution) =>
                onChangeForm((prev) => ({ ...prev, defaultResolution }))
              }
            />
          </SettingsRow>
          <ReadOnlyRow title={tx("settings.rows.videoSaveDir", "Save directory")} value={videoGeneration.save_dir} />
          <RestartSettingsFooter
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            disabled={missingCredential}
            message={
              missingCredential
                ? tx("settings.video.missingCredential", "Configure this provider before enabling video generation.")
                : undefined
            }
            dirtyMessage={tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")}
            pendingMessage={tx("settings.status.savedRestartApply", "Saved. Restart when ready.")}
            onSave={onSave}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>
    </div>
  );
}

function ImageGenerationSettings({
  settings,
  form,
  dirty,
  saving,
  onChangeForm,
  onSave,
  onOpenProviders,
  showBrandLogos,
  onRestart,
  isRestarting,
  requiresRestartPending,
}: {
  settings: SettingsPayload;
  form: ImageGenerationSettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<ImageGenerationSettingsUpdate>>;
  onSave: () => void;
  onOpenProviders: () => void;
  showBrandLogos: boolean;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  // No fallback on the first row: an unset provider must read as unset, not as
  // whichever provider happens to head the registry.
  const selectedProvider = form.provider
    ? settings.image_generation.providers.find((provider) => provider.name === form.provider)
    : undefined;
  const providerConfigured = !!selectedProvider?.configured;
  const missingCredential = form.enabled && !providerConfigured;
  const followsCredential = !!settings.image_generation.enabled_auto && form.enabled;
  const aspectOptions = optionRowsWithCurrent(
    IMAGE_ASPECT_RATIO_OPTIONS.map((value) => ({ name: value, label: value })),
    form.defaultAspectRatio,
  );
  const sizeOptions = optionRowsWithCurrent(
    IMAGE_SIZE_OPTIONS.map((value) => ({ name: value, label: value })),
    form.defaultImageSize,
  );

  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{tx("settings.sections.imageGeneration", "Image generation")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.imageGeneration", "Image generation")}
            description={
              followsCredential
                ? tx(
                    "settings.help.imageGenerationAuto",
                    "On because the selected provider has a credential. Turn it off to keep generate_image out of chats.",
                  )
                : tx("settings.help.imageGeneration", "Expose generate_image in chats when a configured image provider is available.")
            }
          >
            <ToggleButton
              checked={form.enabled}
              onChange={(enabled) => onChangeForm((prev) => ({ ...prev, enabled }))}
              ariaLabel={tx("settings.rows.imageGeneration", "Image generation")}
              label={form.enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.imageProvider", "Image provider")}
            description={tx("settings.help.imageProvider", "Choose the registry provider used by generate_image.")}
          >
            <ProviderPicker
              providers={settings.image_generation.providers}
              value={form.provider}
              emptyLabel={tx("settings.image.selectProvider", "Select provider")}
              showProviderLogos={showBrandLogos}
              onChange={(provider) => onChangeForm((prev) => ({ ...prev, provider, model: "" }))}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.imageProviderStatus", "Provider status")}
            description={tx("settings.help.imageProviderStatus", "Image generation reuses provider credentials from Providers.")}
          >
            <div className="flex flex-wrap items-center justify-end gap-2">
              <StatusPill tone={providerConfigured ? "success" : "neutral"}>
                {providerConfigured
                  ? tx("settings.values.configured", "Configured")
                  : tx("settings.values.notConfigured", "Not configured")}
              </StatusPill>
              {!providerConfigured ? (
                <Button size="sm" variant="outline" onClick={onOpenProviders} className="rounded-full">
                  {tx("settings.image.configureProvider", "Configure provider")}
                </Button>
              ) : null}
            </div>
          </SettingsRow>
          <SettingsRow title={tx("settings.rows.imageProviderBase", "Provider base")}>
            <span className="max-w-[320px] truncate text-right text-[13px] text-muted-foreground">
              {selectedProvider?.api_base || selectedProvider?.default_api_base || selectedProvider?.name || tx("settings.values.notAvailable", "Not available")}
            </span>
          </SettingsRow>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.imageDefaults", "Defaults")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.imageModel", "Image model")}
            description={tx(
              "settings.help.imageModel",
              "Managed list when using Navin, or type any provider model id for BYOK.",
            )}
          >
            <div className="flex w-[min(320px,75vw)] flex-col items-end gap-1.5">
              <MediaModelPicker key={`image:${form.provider}`} kind="image" provider={form.provider}
                configured={providerConfigured} model={form.model}
                onModelChange={(model) => onChangeForm((prev) => ({ ...prev, model }))} />
              {(() => {
                const hit = settings.image_generation.managed_models?.find(
                  (m) => m.slug === form.model,
                );
                const note =
                  hit?.priceNote
                  || (hit?.unitPriceUsd != null ? `~${hit.unitPriceUsd} $ / image` : null);
                return note ? (
                  <p className="text-right text-[11.5px] text-muted-foreground">{note}</p>
                ) : null;
              })()}
            </div>
          </SettingsRow>
          {settings.image_generation.budget_caution ? (
            <div className="rounded-xl border border-amber-200/80 bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-950">
              {settings.image_generation.budget_caution}
            </div>
          ) : null}
          <SettingsRow
            title={tx("settings.rows.defaultAspectRatio", "Default aspect")}
            description={tx("settings.help.defaultAspectRatio", "Used when the prompt does not choose an aspect ratio.")}
          >
            <ProviderPicker
              providers={aspectOptions}
              value={form.defaultAspectRatio}
              emptyLabel={tx("settings.image.selectAspect", "Select aspect")}
              onChange={(defaultAspectRatio) =>
                onChangeForm((prev) => ({ ...prev, defaultAspectRatio }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.defaultImageSize", "Default size")}
            description={tx("settings.help.defaultImageSize", "Size hint sent to providers that support it.")}
          >
            <ProviderPicker
              providers={sizeOptions}
              value={form.defaultImageSize}
              emptyLabel={tx("settings.image.selectSize", "Select size")}
              onChange={(defaultImageSize) =>
                onChangeForm((prev) => ({ ...prev, defaultImageSize }))
              }
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.maxImagesPerTurn", "Max images per turn")}
            description={tx("settings.help.maxImagesPerTurn", "Upper bound for one generate_image request.")}
          >
            <NumberInput
              value={form.maxImagesPerTurn}
              min={1}
              max={8}
              onChange={(maxImagesPerTurn) =>
                onChangeForm((prev) => ({ ...prev, maxImagesPerTurn }))
              }
            />
          </SettingsRow>
          <ReadOnlyRow title={tx("settings.rows.imageSaveDir", "Save directory")} value={settings.image_generation.save_dir} />
          <RestartSettingsFooter
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            disabled={missingCredential}
            message={
              missingCredential
                ? tx("settings.image.missingCredential", "Configure this provider before enabling image generation.")
                : undefined
            }
            dirtyMessage={tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")}
            pendingMessage={tx("settings.status.savedRestartApply", "Saved. Restart when ready.")}
            onSave={onSave}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>
    </div>
  );
}

function TranscriptionSettings({
  settings,
  form,
  dirty,
  saving,
  onChangeForm,
  onSave,
  onSaveAndReturn,
  onBackToChat,
  onOpenAccount,
  voiceForm,
  voiceDirty,
  onChangeVoiceForm,
  musicForm,
  musicDirty,
  musicSaving,
  onChangeMusicForm,
  onSaveMusic,
  onOpenProviders,
  showBrandLogos,
  onRestart,
  isRestarting,
  requiresRestartPending,
}: {
  settings: SettingsPayload;
  form: TranscriptionSettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<TranscriptionSettingsUpdate>>;
  onSave: () => void;
  onSaveAndReturn: () => void;
  onBackToChat: () => void;
  onOpenAccount: () => void;
  voiceForm: VoiceSettingsUpdate;
  voiceDirty: boolean;
  onChangeVoiceForm: Dispatch<SetStateAction<VoiceSettingsUpdate>>;
  musicForm: MusicGenerationSettingsUpdate;
  musicDirty: boolean;
  musicSaving: boolean;
  onChangeMusicForm: Dispatch<SetStateAction<MusicGenerationSettingsUpdate>>;
  onSaveMusic: () => void;
  onOpenProviders: () => void;
  showBrandLogos: boolean;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const music = settings.music_generation ?? DEFAULT_MUSIC_GENERATION_SETTINGS;
  const selectedMusicProvider = musicForm.provider
    ? music.providers.find((provider) => provider.name === musicForm.provider)
    : undefined;
  const musicConfigured = !!selectedMusicProvider?.configured;

  return (
    <div className="space-y-7">
      <LiveVoiceSettings settings={settings} transcription={form} voice={voiceForm}
        dirty={dirty || voiceDirty} saving={saving} onTranscription={onChangeForm}
        onVoice={onChangeVoiceForm} onSave={onSave} onSaveAndReturn={onSaveAndReturn}
        onBackToChat={onBackToChat} onOpenProviders={onOpenProviders} onOpenAccount={onOpenAccount} />

      <section>
        <SettingsSectionTitle>{tx("settings.sections.music", "Music")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.musicGeneration", "Music generation")}
            description={tx(
              "settings.help.musicGeneration",
              "Generate full songs (Lyria Pro) or 30s clips (Lyria Clip) via generate_music.",
            )}
          >
            <ToggleButton
              checked={musicForm.enabled}
              onChange={(enabled) => onChangeMusicForm((prev) => ({ ...prev, enabled }))}
              ariaLabel={tx("settings.rows.musicGeneration", "Music generation")}
              label={musicForm.enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.musicProvider", "Provider")}
            description={tx(
              "settings.help.musicProvider",
              "Navin uses the managed OpenRouter key. BYOK can use OpenRouter directly.",
            )}
          >
            <ProviderPicker
              providers={music.providers}
              value={musicForm.provider}
              emptyLabel={tx("settings.voice.selectProvider", "Select provider")}
              showProviderLogos={showBrandLogos}
              onChange={(provider) => onChangeMusicForm((prev) => ({ ...prev, provider, model: "" }))}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.musicProviderStatus", "Provider status")}
            description={tx(
              "settings.help.musicProviderStatus",
              "API keys stay under Providers.",
            )}
          >
            <div className="flex flex-wrap items-center justify-end gap-2">
              <StatusPill tone={musicConfigured ? "success" : "neutral"}>
                {musicConfigured
                  ? tx("settings.values.configured", "Configured")
                  : tx("settings.values.notConfigured", "Not configured")}
              </StatusPill>
              {!musicConfigured ? (
                <Button size="sm" variant="outline" onClick={onOpenProviders} className="rounded-full">
                  {tx("settings.voice.configureProvider", "Configure provider")}
                </Button>
              ) : null}
            </div>
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.musicModel", "Model")}
            description={tx(
              "settings.help.musicModel",
              "Lyria Pro = full song (0,08 $). Lyria Clip = 30s (0,04 $).",
            )}
          >
            <div className="flex w-[min(320px,75vw)] flex-col items-end gap-1.5">
              <MediaModelPicker key={`music:${musicForm.provider}`} kind="music" provider={musicForm.provider}
                configured={musicConfigured} model={musicForm.model}
                onModelChange={(model) => onChangeMusicForm((prev) => ({ ...prev, model }))} />
              {(() => {
                const hit = music.managed_models?.find((m) => m.slug === musicForm.model);
                const note = hit?.priceNote
                  || (hit?.unitPriceUsd != null ? `~${hit.unitPriceUsd} $ / génération` : null);
                return note ? (
                  <p className="text-right text-[11.5px] text-muted-foreground">{note}</p>
                ) : null;
              })()}
            </div>
          </SettingsRow>
          {music.budget_caution ? (
            <div className="rounded-xl border border-amber-200/80 bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-950">
              {music.budget_caution}
            </div>
          ) : null}
          <RestartSettingsFooter
            dirty={musicDirty}
            saving={musicSaving}
            pendingRestart={requiresRestartPending}
            dirtyMessage={tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")}
            pendingMessage={tx("settings.status.savedRestartApply", "Saved. Restart when ready.")}
            onSave={onSaveMusic}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>
    </div>
  );
}

function WebSettings({
  settings,
  form,
  keyVisible,
  keyEditing,
  saving,
  onChangeForm,
  onChangeProvider,
  onToggleKey,
  onToggleKeyEditing,
  onReset,
  onSave,
  showBrandLogos,
  onRestart,
  isRestarting,
  requiresRestartPending,
  olostepFeature,
  olostepInstalling,
  capabilityError,
}: {
  settings: SettingsPayload;
  form: WebSearchSettingsUpdate;
  keyVisible: boolean;
  keyEditing: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<WebSearchSettingsUpdate>>;
  onChangeProvider: (provider: string) => void;
  onToggleKey: () => void;
  onToggleKeyEditing: () => void;
  onReset: () => void;
  onSave: () => void;
  showBrandLogos: boolean;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
  olostepFeature?: NavinFeatureInfo;
  olostepInstalling: boolean;
  capabilityError: string | null;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const selectedProvider =
    settings.web_search.providers.find((provider) => provider.name === form.provider) ??
    settings.web_search.providers[0];
  const hasExistingSecret =
    webSearchProviderAcceptsApiKey(selectedProvider) &&
    form.provider === settings.web_search.provider &&
    !!settings.web_search.api_key_hint;
  const showKeyInput = webSearchProviderAcceptsApiKey(selectedProvider) && (!hasExistingSecret || keyEditing);
  const apiKey = form.apiKey?.trim() ?? "";
  const baseUrl = form.baseUrl?.trim() ?? "";
  const effectiveJinaReader = form.useJinaReader ?? settings.web.fetch.use_jina_reader;
  const dirty =
    form.provider !== settings.web_search.provider ||
    apiKey.length > 0 ||
    baseUrl !== (settings.web_search.base_url ?? "") ||
    form.maxResults !== settings.web_search.max_results ||
    form.timeout !== settings.web_search.timeout ||
    effectiveJinaReader !== settings.web.fetch.use_jina_reader;
  const jinaReaderDirty = effectiveJinaReader !== settings.web.fetch.use_jina_reader;
  const missingCredential =
    webSearchProviderRequiresApiKey(selectedProvider)
      ? !apiKey && !hasExistingSecret
      : selectedProvider?.credential === "base_url"
        ? !baseUrl
        : false;

  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{tx("settings.sections.webSearch", "Web search")}</SettingsSectionTitle>
        {form.provider === "olostep" && olostepFeature && !olostepFeature.installed ? (
          <div className="mb-3">
            <CapabilityInstallNotice
              title={tx("settings.capabilities.searchSupport", "Search provider support")}
              description={tx(
                "settings.capabilities.searchInstallOnSave",
                "Olostep support will be installed automatically when you save.",
              )}
              installing={olostepInstalling}
            />
          </div>
        ) : null}
        {capabilityError ? (
          <p className="mb-3 text-[12px] text-destructive">{capabilityError}</p>
        ) : null}
        <SettingsGroup>
          <SettingsRow
            title={t("settings.byok.webSearch.provider")}
            description={t("settings.byok.webSearch.providerHelp")}
          >
            <ProviderPicker
              providers={settings.web_search.providers}
              value={form.provider}
              emptyLabel={t("settings.byok.webSearch.selectProvider")}
              showProviderLogos={showBrandLogos}
              onChange={onChangeProvider}
            />
          </SettingsRow>

          {selectedProvider?.credential === "none" ? (
            <SettingsRow
              title={t("settings.byok.webSearch.credentials")}
              description={t("settings.byok.webSearch.noCredentialHelp")}
            >
              <StatusPill tone="success">{t("settings.byok.webSearch.noCredentialRequired")}</StatusPill>
            </SettingsRow>
          ) : null}

          {webSearchProviderAcceptsApiKey(selectedProvider) ? (
            <SettingsRow
              title={t("settings.byok.apiKey")}
              description={t("settings.byok.webSearch.apiKeyHelp")}
            >
              <div className="relative w-[280px] max-w-full">
                {showKeyInput ? (
                  <>
                    <Input
                      type={keyVisible ? "text" : "password"}
                      value={form.apiKey ?? ""}
                      onChange={(event) =>
                        onChangeForm((prev) => ({ ...prev, apiKey: event.target.value }))
                      }
                      placeholder={
                        hasExistingSecret
                          ? t("settings.byok.apiKeyConfiguredPlaceholder")
                          : t("settings.byok.apiKeyPlaceholder")
                      }
                      className="h-9 rounded-full pr-11 text-[13px]"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={onToggleKey}
                      aria-label={
                        keyVisible ? t("settings.byok.hideApiKey") : t("settings.byok.showApiKey")
                      }
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      {keyVisible ? (
                        <EyeOff className="h-3.5 w-3.5" aria-hidden />
                      ) : (
                        <Eye className="h-3.5 w-3.5" aria-hidden />
                      )}
                    </Button>
                  </>
                ) : (
                  <>
                    <div className="flex h-9 items-center rounded-full border border-input bg-background px-3 pr-11 text-[13px] text-muted-foreground">
                      {settings.web_search.api_key_hint ?? t("settings.byok.configuredKeyHint")}
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={onToggleKeyEditing}
                      aria-label={t("settings.actions.edit")}
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  </>
                )}
              </div>
            </SettingsRow>
          ) : null}

          {selectedProvider?.credential === "base_url" ? (
            <SettingsRow
              title={t("settings.byok.webSearch.baseUrl")}
              description={t("settings.byok.webSearch.baseUrlHelp")}
            >
              <Input
                value={form.baseUrl ?? ""}
                onChange={(event) =>
                  onChangeForm((prev) => ({ ...prev, baseUrl: event.target.value }))
                }
                placeholder={t("settings.byok.webSearch.baseUrlPlaceholder")}
                className="h-9 w-[280px] rounded-full text-[13px]"
              />
            </SettingsRow>
          ) : null}
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.webBehavior", "Behavior")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.maxResults", "Max results")}
            description={tx("settings.help.maxResults", "Results returned by each web_search call.")}
          >
            <NumberInput
              value={form.maxResults ?? settings.web_search.max_results}
              min={1}
              max={10}
              onChange={(maxResults) => onChangeForm((prev) => ({ ...prev, maxResults }))}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.timeout", "Timeout")}
            description={tx("settings.help.timeout", "Seconds before a search provider request times out.")}
          >
            <NumberInput
              value={form.timeout ?? settings.web_search.timeout}
              min={1}
              max={120}
              onChange={(timeout) => onChangeForm((prev) => ({ ...prev, timeout }))}
              suffix="s"
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.jinaReader", "Jina reader")}
            description={tx("settings.help.jinaReader", "Use Jina Reader for web_fetch when available.")}
          >
            <ToggleButton
              checked={effectiveJinaReader}
              onChange={(useJinaReader) => onChangeForm((prev) => ({ ...prev, useJinaReader }))}
              ariaLabel={tx("settings.rows.jinaReader", "Jina reader")}
              label={effectiveJinaReader ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <RestartSettingsFooter
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            disabled={missingCredential}
            message={
              missingCredential
                ? t("settings.byok.webSearch.missingCredential")
                : requiresRestartPending && !dirty
                  ? tx("settings.status.savedRestartApply", "Saved. Restart when ready.")
                  : jinaReaderDirty
                    ? tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")
                    : dirty
                      ? t("settings.byok.webSearch.saveHint")
                      : undefined
            }
            onSave={onSave}
            onRestart={onRestart}
            onReset={onReset}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>
    </div>
  );
}

function AutomationsSettings({
  payload,
  loading,
  query,
  filter,
  sort,
  actionKey,
  error,
  onQueryChange,
  onFilterChange,
  onSortChange,
  onAction,
  onRequestEdit,
  onRequestDelete,
}: {
  payload: AutomationsPayload | null;
  loading: boolean;
  query: string;
  filter: AutomationFilter;
  sort: AutomationSort;
  actionKey: string | null;
  error: string | null;
  onQueryChange: (value: string) => void;
  onFilterChange: (value: AutomationFilter) => void;
  onSortChange: (value: AutomationSort) => void;
  onAction: (action: AutomationAction, job: SessionAutomationJob) => void | Promise<void>;
  onRequestEdit: (job: SessionAutomationJob) => void;
  onRequestDelete: (job: SessionAutomationJob) => void;
}) {
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const jobs = payload?.jobs ?? [];
  const locale = i18n.resolvedLanguage || i18n.language;
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const searchTokens = parseAutomationSearchQuery(query);
    return sortAutomationJobs(jobs, sort)
      .filter((job) => automationMatchesFilter(job, filter))
      .filter((job) => !searchTokens.length || automationMatchesSearch(job, searchTokens));
  }, [filter, jobs, query, sort]);
  const activeCount = jobs.filter((job) => {
    const key = automationStatusKey(job);
    return key === "active" || key === "running";
  }).length;
  const pausedCount = jobs.filter((job) => automationStatusKey(job) === "paused").length;
  const failedCount = jobs.filter(automationNeedsAttention).length;
  const systemCount = jobs.filter((job) => job.protected).length;
  const summaryOptions: Array<{ value: AutomationFilter; label: string; count: number }> = [
    { value: "all", label: tx("settings.automations.filters.all", "All"), count: jobs.length },
    { value: "active", label: tx("settings.automations.filters.active", "Active"), count: activeCount },
    { value: "paused", label: tx("settings.automations.filters.paused", "Paused"), count: pausedCount },
    { value: "failed", label: tx("settings.automations.filters.failed", "Needs attention"), count: failedCount },
    { value: "system", label: tx("settings.automations.filters.system", "System"), count: systemCount },
  ];
  const sortLabel = {
    next: tx("settings.automations.sort.next", "Next run"),
    last: tx("settings.automations.sort.last", "Last run"),
    updated: tx("settings.automations.sort.updated", "Updated"),
    name: tx("settings.automations.sort.name", "Name"),
  } satisfies Record<AutomationSort, string>;
  const selectedJob = filtered.find((job) => job.id === selectedJobId) ?? filtered[0] ?? null;

  useEffect(() => {
    if (!filtered.length) {
      if (selectedJobId !== null) setSelectedJobId(null);
      return;
    }
    if (!selectedJobId || !filtered.some((job) => job.id === selectedJobId)) {
      setSelectedJobId(filtered[0].id);
    }
  }, [filtered, selectedJobId]);

  return (
    <div className="space-y-5">
      <section className="shrink-0">
        <div className="mx-auto flex w-full max-w-[56rem] flex-col gap-3">
          <div className="flex flex-wrap items-center gap-1.5 pb-0.5">
            {summaryOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => onFilterChange(option.value)}
                className={cn(
                  "inline-flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-transparent px-3.5 text-[12px] font-medium text-muted-foreground outline-none transition-colors",
                  filter === option.value
                    ? "border-border/55 bg-background text-foreground shadow-[0_6px_18px_rgba(15,23,42,0.06)] dark:border-white/12 dark:bg-background/70"
                    : "hover:bg-muted/55 hover:text-foreground",
                  automationFilterToneClass(option.value, option.count, filter === option.value),
                )}
              >
                <span>{option.label}</span>
                <span
                  className={cn(
                    "min-w-5 shrink-0 rounded-full bg-muted/60 px-1.5 py-0.5 text-center text-[11px] tabular-nums text-muted-foreground",
                    automationFilterCountClass(option.value, option.count),
                  )}
                >
                  {option.count}
                </span>
              </button>
            ))}
          </div>

          <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
            <div className="relative min-w-0">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/70" />
              <Input
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                placeholder={tx(
                  "settings.automations.search",
                  "Search task, message, linked chat, or schedule",
                )}
                className="h-9 w-full rounded-full border-border/45 bg-background/85 pl-9 text-[13px] shadow-[0_8px_22px_rgba(15,23,42,0.04)] dark:border-white/10 dark:bg-background/40"
              />
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="inline-flex h-9 min-w-[8.5rem] items-center justify-center gap-1.5 whitespace-nowrap rounded-full border border-border/45 bg-background/85 px-3.5 text-[12px] font-medium text-muted-foreground shadow-[0_8px_22px_rgba(15,23,42,0.04)] transition-colors hover:bg-muted/70 hover:text-foreground dark:border-white/10 dark:bg-background/40 sm:w-auto"
                >
                  <ArrowUpDown className="h-3.5 w-3.5" aria-hidden />
                  <span>{sortLabel[sort]}</span>
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-40">
                {(Object.keys(sortLabel) as AutomationSort[]).map((value) => (
                  <DropdownMenuItem key={value} onClick={() => onSortChange(value)}>
                    <span>{sortLabel[value]}</span>
                    {sort === value ? <Check className="ml-auto h-3.5 w-3.5" aria-hidden /> : null}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </section>

      {error ? (
        <div className="flex items-center gap-2 rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
          <CircleAlert className="h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {loading && !payload ? (
        <div className="flex h-44 items-center justify-center rounded-[24px] border border-border/45 bg-card/80 text-[13px] text-muted-foreground shadow-[0_22px_70px_rgba(15,23,42,0.055)]">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          {tx("settings.automations.loading", "Loading automations...")}
        </div>
      ) : filtered.length && selectedJob ? (
        <section className="grid min-h-0 overflow-hidden rounded-[22px] border border-border/45 bg-transparent shadow-none dark:border-white/10 xl:grid-cols-[minmax(16rem,18rem)_minmax(0,1fr)] xl:items-stretch">
          <aside className="flex min-h-0 flex-col overflow-hidden border-b border-border/35 bg-background/36 dark:border-white/10 dark:bg-background/18 xl:border-b-0 xl:border-r">
            <div className="flex shrink-0 items-center justify-between gap-3 px-4 py-3">
              <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
                {tx("settings.automations.queue", "Queue")}
              </h2>
              <span className="rounded-full bg-muted/70 px-2 py-0.5 text-[11px] tabular-nums text-muted-foreground">
                {filtered.length}
              </span>
            </div>
            <div
              className="max-h-[28rem] space-y-1 overflow-y-auto overscroll-contain px-2 pb-2 xl:max-h-[calc(100vh-24rem)]"
              role="list"
              aria-label={tx("settings.automations.queue", "Queue")}
            >
              {filtered.map((job) => (
                <AutomationListItem
                  key={job.id}
                  job={job}
                  locale={locale}
                  selected={job.id === selectedJob.id}
                  onSelect={() => setSelectedJobId(job.id)}
                />
              ))}
            </div>
          </aside>
          <AutomationDetailPanel
            job={selectedJob}
            locale={locale}
            actionKey={actionKey}
            onAction={onAction}
            onRequestEdit={onRequestEdit}
            onRequestDelete={onRequestDelete}
          />
        </section>
      ) : (
        <div className="rounded-[24px] border border-dashed border-border/55 bg-card/60 px-5 py-14 text-center text-[13px] text-muted-foreground">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted/60">
            <Orbit className="h-5 w-5 text-muted-foreground/70" aria-hidden />
          </div>
          <div className="font-medium text-foreground/80">
            {jobs.length
              ? tx("settings.automations.noMatches", "No automations match this view.")
              : tx("settings.automations.empty", "No automations yet.")}
          </div>
          {!jobs.length ? (
            <div className="mx-auto mt-2 max-w-[28rem] text-[12px] leading-5">
              {tx(
                "settings.automations.emptyHint",
                "Create one from where it should run so navin keeps the right context.",
              )}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function AutomationListItem({
  job,
  locale,
  selected,
  onSelect,
}: {
  job: SessionAutomationJob;
  locale: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const status = automationStatus(job, tx);
  const origin = automationOriginLabel(job, tx);
  const nextRun = formatAutomationNext(job, tx);
  const summary = automationSummary(job, tx);

  return (
    <div role="listitem">
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          "group grid w-full grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-[16px] px-3 py-3.5 text-left outline-none transition-colors",
          selected
            ? "bg-background text-foreground shadow-[0_10px_28px_rgba(15,23,42,0.055)] ring-1 ring-border/45 dark:bg-background/45 dark:ring-white/10"
            : "text-muted-foreground hover:bg-muted/45 hover:text-foreground dark:hover:bg-background/24",
        )}
      >
        <span className="min-w-0">
          <span className="flex min-w-0 items-center gap-2.5">
            <span
              className={cn("h-2 w-2 shrink-0 rounded-full", automationStatusDotClass(job))}
              aria-hidden
            />
            <span className="truncate text-[13.5px] font-medium text-foreground">
              {loopLabel(job, tx)}
            </span>
          </span>
          <span className="mt-1.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground">
            {summary}
          </span>
          <span className="mt-2.5 flex min-w-0 items-center gap-2 text-[11.5px] leading-none text-muted-foreground">
            <span className="truncate" title={formatAutomationNextTitle(job, locale, tx)}>
              {nextRun}
            </span>
            <span className="h-1 w-1 shrink-0 rounded-full bg-muted-foreground/35" aria-hidden />
            <span className="truncate">{origin}</span>
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-2 pt-0.5">
          <span className="rounded-full bg-muted/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground dark:bg-background/35">
            {status.label}
          </span>
          {job.delete_after_run ? (
            <span className="rounded-full bg-background/80 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              {tx("settings.automations.oneShot", "One-time")}
            </span>
          ) : null}
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 text-muted-foreground/55 transition-opacity",
              selected ? "opacity-100" : "opacity-0 group-hover:opacity-70",
            )}
            aria-hidden
          />
        </span>
      </button>
    </div>
  );
}

function AutomationDetailPanel({
  job,
  locale,
  actionKey,
  onAction,
  onRequestEdit,
  onRequestDelete,
}: {
  job: SessionAutomationJob;
  locale: string;
  actionKey: string | null;
  onAction: (action: AutomationAction, job: SessionAutomationJob) => void | Promise<void>;
  onRequestEdit: (job: SessionAutomationJob) => void;
  onRequestDelete: (job: SessionAutomationJob) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const status = automationStatus(job, tx);
  const origin = automationOriginLabel(job, tx);
  const originHref = job.origin?.channel === "websocket" && job.origin.session_key
    ? `#/chat/${encodeURIComponent(job.origin.session_key)}`
    : null;
  const created = job.created_at_ms ? fmtDateTime(job.created_at_ms, locale) : null;
  const updated = job.updated_at_ms ? fmtDateTime(job.updated_at_ms, locale) : null;
  const localTrigger = isLocalTriggerAutomation(job);
  const triggerCommand = automationTriggerCommand(job);
  const message = automationDetailText(job, tx);
  const messageLabel = localTrigger
    ? tx("settings.automations.fields.command", "Command")
    : tx("settings.automations.fields.message", "Message");
  const schedule = formatAutomationSchedule(job, locale, tx);
  const [messageExpanded, setMessageExpanded] = useState(false);
  const [commandCopied, setCommandCopied] = useState(false);
  const messageNeedsExpansion = automationMessageNeedsExpansion(message);

  useEffect(() => {
    setMessageExpanded(false);
    setCommandCopied(false);
  }, [job.id]);

  return (
    <article className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-background/42 dark:bg-background/18">
      <div className="shrink-0 border-b border-border/35 px-4 py-3.5 dark:border-white/10 sm:px-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h3 className="min-w-0 truncate text-[18px] font-medium leading-7 text-foreground">
                {loopLabel(job, tx)}
              </h3>
              <AutomationStatusBadge tone={status.tone}>{status.label}</AutomationStatusBadge>
              {job.delete_after_run ? (
                <AutomationStatusBadge>{tx("settings.automations.oneShot", "One-time")}</AutomationStatusBadge>
              ) : null}
            </div>
            <p className="mt-1 truncate text-[12.5px] leading-5 text-muted-foreground">
              {schedule} · {origin}
            </p>
          </div>
          <AutomationActionGroup
            job={job}
            actionKey={actionKey}
            onAction={onAction}
            onRequestEdit={onRequestEdit}
            onRequestDelete={onRequestDelete}
          />
        </div>
      </div>

      <div className="grid min-h-0 min-w-0 flex-1 overflow-hidden lg:grid-cols-[minmax(0,1fr)_14.5rem]">
        <div className="min-h-0 min-w-0 space-y-3 overflow-y-auto overscroll-contain p-4 sm:p-5">
          <section className="rounded-[20px] border border-border/35 bg-background/62 px-4 py-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.58)] dark:border-white/10 dark:bg-background/24">
            <div className="flex items-center justify-between gap-3">
              <div className="text-[11px] font-medium leading-none text-muted-foreground/75">
                {messageLabel}
              </div>
              {localTrigger && triggerCommand ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 shrink-0 rounded-full px-2 text-[11.5px]"
                  onClick={() => {
                    void copyTextToClipboard(triggerCommand).then((ok) => {
                      if (ok) setCommandCopied(true);
                    });
                  }}
                >
                  {commandCopied ? (
                    <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <Clipboard className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  )}
                  {commandCopied
                    ? tx("settings.automations.commandCopied", "Copied")
                    : tx("settings.automations.copyCommand", "Copy")}
                </Button>
              ) : null}
            </div>
            <div
              className={cn(
                "mt-3 whitespace-pre-wrap break-words text-[13px] leading-6 text-foreground/85",
                localTrigger && "font-mono text-[12.5px]",
                !messageExpanded && messageNeedsExpansion && "line-clamp-6",
              )}
            >
              {message}
            </div>
            {messageNeedsExpansion ? (
              <button
                type="button"
                className="mt-3 inline-flex text-[12px] font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                onClick={() => setMessageExpanded((value) => !value)}
              >
                {messageExpanded
                  ? tx("settings.automations.message.showLess", "Show less")
                  : tx("settings.automations.message.showMore", "Show full message")}
              </button>
            ) : null}
          </section>

          <div className="grid gap-3 md:grid-cols-2">
            <AutomationDetail
              label={tx("settings.automations.labels.next", "Next")}
              title={formatAutomationNextTitle(job, locale, tx)}
            >
              {formatAutomationNext(job, tx)}
            </AutomationDetail>
            <AutomationDetail label={tx("settings.automations.labels.origin", "Linked chat")} title={origin}>
              {originHref ? (
                <a
                  className="inline-flex max-w-full items-center gap-1 text-foreground/80 underline-offset-2 hover:underline"
                  href={originHref}
                >
                  <span className="truncate">{origin}</span>
                  <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                </a>
              ) : (
                origin
              )}
            </AutomationDetail>
          </div>

          {job.state.last_error ? (
            <div className="rounded-[16px] border border-destructive/20 bg-destructive/8 px-3 py-2 text-[12px] leading-5 text-destructive">
              {job.state.last_error}
            </div>
          ) : null}
        </div>

        <aside className="min-h-0 overflow-y-auto overscroll-contain border-t border-border/35 bg-muted/20 p-4 text-[12px] text-muted-foreground dark:border-white/10 dark:bg-background/16 lg:border-l lg:border-t-0">
          <div className="grid gap-3">
            <AutomationDetail
              label={tx("settings.automations.labels.schedule", "Schedule")}
              title={schedule}
            >
              {schedule}
            </AutomationDetail>
            <AutomationSpendDetail job={job} tx={tx} locale={locale} />
            <div className="rounded-[18px] bg-background/55 p-3">
              <div className="grid gap-3">
                {created ? (
                  <div>
                    <div className="text-[11px] leading-none text-muted-foreground/75">
                      {tx("settings.automations.labels.created", "Created")}
                    </div>
                    <div className="mt-1.5 text-[12.5px] leading-5 text-foreground/80">{created}</div>
                  </div>
                ) : null}
                {updated ? (
                  <div>
                    <div className="text-[11px] leading-none text-muted-foreground/75">
                      {tx("settings.automations.labels.updated", "Updated")}
                    </div>
                    <div className="mt-1.5 text-[12.5px] leading-5 text-foreground/80">{updated}</div>
                  </div>
                ) : null}
                <div>
                  <div className="text-[11px] leading-none text-muted-foreground/75">ID</div>
                  <div className="mt-1.5 break-all font-mono text-[11.5px] leading-5 text-foreground/70">
                    {job.id}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </article>
  );
}

function AutomationSpendDetail({
  job,
  tx,
  locale,
}: {
  job: SessionAutomationJob;
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string;
  locale: string;
}) {
  const spend = spendState(job);
  const failures = job.state.consecutive_failures ?? 0;
  const pausedReason = job.state.paused_reason;
  const number = new Intl.NumberFormat(locale);

  if (!spend.budget && !spend.used && !failures && !pausedReason) return null;

  return (
    <div className="rounded-[18px] bg-background/55 p-3">
      <div className="text-[11px] leading-none text-muted-foreground/75">
        {tx("loops.detail.spendToday", "Spent today")}
      </div>
      <div className="mt-1.5 text-[12.5px] leading-5 text-foreground/80">
        {spend.budget
          ? tx("loops.detail.spendOfBudget", "{{used}} of {{budget}} tokens", {
              used: number.format(spend.used),
              budget: number.format(spend.budget),
            })
          : tx("loops.detail.spendNoBudget", "{{used}} tokens · no ceiling", {
              used: number.format(spend.used),
            })}
      </div>
      {spend.ratio !== null ? (
        <div
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuenow={Math.round(spend.ratio * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={cn(
              "h-full rounded-full transition-[width]",
              spend.exhausted ? "bg-amber-500" : "bg-foreground/45",
            )}
            style={{ width: `${Math.round(spend.ratio * 100)}%` }}
          />
        </div>
      ) : null}
      {spend.exhausted ? (
        <p className="mt-2 text-[11.5px] leading-4 text-amber-600 dark:text-amber-500">
          {tx("loops.detail.budgetReached", "Ceiling reached; runs resume tomorrow.")}
        </p>
      ) : null}
      {failures ? (
        <p className="mt-2 text-[11.5px] leading-4 text-muted-foreground">
          {tx("loops.detail.failureStreak", "{{count}} failures in a row", { count: failures })}
        </p>
      ) : null}
      {pausedReason ? (
        <p className="mt-2 text-[11.5px] leading-4 text-destructive">
          {tx("loops.detail.autoPaused", "Paused automatically. {{reason}}", {
            reason: pausedReason,
          })}
        </p>
      ) : null}
    </div>
  );
}

function AutomationActionGroup({
  job,
  actionKey,
  onAction,
  onRequestEdit,
  onRequestDelete,
}: {
  job: SessionAutomationJob;
  actionKey: string | null;
  onAction: (action: AutomationAction, job: SessionAutomationJob) => void | Promise<void>;
  onRequestEdit: (job: SessionAutomationJob) => void;
  onRequestDelete: (job: SessionAutomationJob) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  // A system loop keeps its name and message but can be retimed and capped.
  const systemLoop = isConfigurableSystemLoop(job);
  const userOwned = !job.protected;
  const canManage = userOwned || systemLoop;
  const hasLinkedChat = Boolean(job.origin);
  const localTrigger = isLocalTriggerAutomation(job);
  const canRun = userOwned && hasLinkedChat && job.enabled && !job.state.pending && !localTrigger;
  const toggleAction: AutomationAction = job.enabled ? "disable" : "enable";
  const canToggle = canManage && (job.enabled || hasLinkedChat || systemLoop);
  const toggleBusy = actionKey === `${toggleAction}:${job.id}`;

  if (!canManage) {
    return (
      <span className="inline-flex h-9 items-center rounded-full bg-muted px-3 text-[12px] font-medium text-muted-foreground">
        {tx("settings.automations.protected", "Protected")}
      </span>
    );
  }

  return (
    <div className="flex shrink-0 items-center gap-1 rounded-full border border-border/35 bg-background/70 p-1 shadow-[0_10px_26px_rgba(15,23,42,0.055)] dark:border-white/10 dark:bg-background/35">
      <AppsActionButton
        ariaLabel={tx("settings.automations.edit", "Edit")}
        disabled={Boolean(actionKey)}
        onClick={() => onRequestEdit(job)}
      >
        <Pencil className="h-4 w-4" aria-hidden />
      </AppsActionButton>
      {!localTrigger && !systemLoop ? (
        <AppsActionButton
          ariaLabel={tx("settings.automations.runNow", "Run now")}
          busy={actionKey === `run:${job.id}`}
          disabled={!canRun}
          onClick={() => void onAction("run", job)}
        >
          <PlayCircle className="h-4 w-4" aria-hidden />
        </AppsActionButton>
      ) : null}
      <AppsActionButton
        ariaLabel={
          job.enabled
            ? tx("settings.automations.pause", "Pause")
            : tx("settings.automations.resume", "Resume")
        }
        busy={toggleBusy}
        disabled={!canToggle}
        onClick={() => void onAction(toggleAction, job)}
      >
        {job.enabled ? (
          <PauseCircle className="h-4 w-4" aria-hidden />
        ) : (
          <PlayCircle className="h-4 w-4" aria-hidden />
        )}
      </AppsActionButton>
      {!systemLoop ? (
        <AppsActionButton
          ariaLabel={tx("settings.automations.delete", "Delete")}
          tone="danger"
          disabled={Boolean(actionKey)}
          onClick={() => onRequestDelete(job)}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </AppsActionButton>
      ) : null}
    </div>
  );
}

function AutomationStatusBadge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "warning";
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded-full px-2.5 text-[11.5px] font-medium shadow-[inset_0_0_0_1px_rgba(120,72,25,0.055)]",
        tone === "success" &&
          "bg-orange-100/72 text-orange-800 dark:bg-orange-300/12 dark:text-orange-200",
        tone === "warning" &&
          "bg-amber-100/80 text-amber-800 dark:bg-amber-300/14 dark:text-amber-200",
        tone === "neutral" &&
          "bg-white/64 text-muted-foreground dark:bg-background/35 dark:text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

function automationMessageNeedsExpansion(message: string): boolean {
  return message.length > 360 || message.split(/\r?\n/).length > 6;
}

function AutomationDetail({
  label,
  title,
  secondary,
  children,
}: {
  label: string;
  title?: string;
  secondary?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-[17px] bg-background/52 px-3 py-3 shadow-[inset_0_0_0_1px_rgba(15,23,42,0.035)] dark:bg-background/22">
      <div className="text-[11px] font-medium leading-none text-muted-foreground/75">
        {label}
      </div>
      <div className="mt-1.5 min-w-0">
        <div className="line-clamp-2 text-[13px] leading-5 text-foreground/85" title={title}>
          {children}
        </div>
        {secondary ? (
          <div className="mt-0.5 truncate text-[11.5px] leading-4 text-muted-foreground" title={title}>
            {secondary}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function AutomationDeleteDialog({
  job,
  deleting,
  onOpenChange,
  onConfirm,
}: {
  job: SessionAutomationJob | null;
  deleting: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (job: SessionAutomationJob) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  return (
    <Dialog open={Boolean(job)} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(calc(100vw-2rem),26rem)] rounded-[26px]">
        <DialogHeader>
          <DialogTitle>{tx("settings.automations.deleteTitle", "Delete automation")}</DialogTitle>
          <DialogDescription>
            {tx(
              "settings.automations.deleteDescription",
              "This removes {{name}} from automations. Past chat messages stay in the session.",
              { name: job?.name || job?.id || "" },
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={deleting}
            className="rounded-full"
          >
            {tx("settings.automations.cancel", "Cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => job && void onConfirm(job)}
            disabled={!job || deleting}
            className="rounded-full"
          >
            {deleting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
            {tx("settings.automations.delete", "Delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NavinFeatureInstallDialog({
  feature,
  installing,
  onOpenChange,
  onConfirm,
}: {
  feature: NavinFeatureInfo | null;
  installing: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (feature: NavinFeatureInfo) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const name = feature?.display_name || feature?.name || "";
  return (
    <Dialog open={Boolean(feature)} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="w-[min(calc(100vw-2rem),24rem)] gap-0 rounded-[28px] border border-white/70 bg-card/95 p-5 text-center shadow-[0_24px_80px_rgba(15,23,42,0.20)] backdrop-blur-xl sm:rounded-[28px]"
      >
        <DialogHeader className="items-center space-y-0 text-center">
          <DialogTitle className="text-center text-[20px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
            {tx("settings.navinFeatures.installConfirmTitle", "Install support for {{name}}?", { name })}
          </DialogTitle>
          <DialogDescription className="mt-3 max-w-[20rem] text-center text-[14px] leading-6 text-muted-foreground">
            {tx(
              "settings.navinFeatures.installConfirmDescription",
              "navin will add what {{name}} needs, then turn it on. Continue?",
              { name },
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="mt-7 !grid grid-cols-1 gap-3 space-x-0 sm:grid-cols-2 sm:space-x-0">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={installing}
            className="h-11 w-full min-w-0 rounded-full bg-muted/70 px-5 text-[15px] font-semibold text-foreground shadow-none hover:bg-muted"
          >
            {tx("settings.automations.cancel", "Cancel")}
          </Button>
          <Button
            type="button"
            onClick={() => feature && void onConfirm(feature)}
            disabled={!feature || installing}
            className="h-11 w-full min-w-0 !whitespace-normal rounded-full px-5 text-center text-[15px] font-semibold"
          >
            {installing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
            {tx("settings.navinFeatures.installConfirmAction", "Install and enable")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function isLocalTriggerAutomation(job: SessionAutomationJob | null): boolean {
  if (!job) return false;
  return job.kind === "local_trigger"
    || job.payload.kind === "local_trigger"
    || job.schedule.kind === "local";
}

function automationTriggerCommand(job: SessionAutomationJob): string {
  return job.trigger?.command || job.payload.command || job.payload.message || "";
}

function automationSummary(
  job: SessionAutomationJob,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  if (isLocalTriggerAutomation(job)) {
    return automationTriggerCommand(job) || tx("settings.automations.localTrigger", "Local trigger");
  }
  // A system loop has no user-written message, so say what it does instead.
  return job.payload.message || loopPurpose(job, tx) || tx("loops.system.managed", "Built-in loop");
}

function automationDetailText(
  job: SessionAutomationJob,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  return automationSummary(job, tx);
}

function automationNeedsAttention(job: SessionAutomationJob): boolean {
  // A loop the service paused itself is stopped until somebody looks at it,
  // which is exactly what this filter is for.
  return job.state.last_status === "error" || isAutoPaused(job);
}

function automationStatusKey(
  job: SessionAutomationJob,
): "active" | "running" | "paused" | "failed" | "system" | "completed" | "idle" {
  if (job.protected) return "system";
  if (job.state.pending) return "running";
  if (!job.enabled) return "paused";
  if (job.state.last_status === "error") return "failed";
  if (isLocalTriggerAutomation(job)) return "active";
  if (job.delete_after_run && !job.state.next_run_at_ms && job.state.last_status === "ok") {
    return "completed";
  }
  if (!job.state.next_run_at_ms) return "idle";
  return "active";
}

function sortAutomationJobs(jobs: SessionAutomationJob[], sort: AutomationSort): SessionAutomationJob[] {
  const byName = (left: SessionAutomationJob, right: SessionAutomationJob) =>
    (left.name || left.id).localeCompare(right.name || right.id);
  return [...jobs].sort((left, right) => {
    if (sort === "name") return byName(left, right);
    if (sort === "last") {
      return (right.state.last_run_at_ms ?? 0) - (left.state.last_run_at_ms ?? 0) || byName(left, right);
    }
    if (sort === "updated") {
      return (right.updated_at_ms ?? 0) - (left.updated_at_ms ?? 0) || byName(left, right);
    }
    const leftNext = left.state.next_run_at_ms ?? Number.MAX_SAFE_INTEGER;
    const rightNext = right.state.next_run_at_ms ?? Number.MAX_SAFE_INTEGER;
    return leftNext - rightNext || byName(left, right);
  });
}

type AutomationSearchField = "id" | "name" | "message" | "chat" | "cron" | "schedule" | "status";

interface AutomationSearchToken {
  field: AutomationSearchField | null;
  value: string;
}

const AUTOMATION_SEARCH_FIELDS = new Set<AutomationSearchField>([
  "id",
  "name",
  "message",
  "chat",
  "cron",
  "schedule",
  "status",
]);

const AUTOMATION_CHANNEL_LABELS: Record<string, string> = {
  api: "API",
  cli: "CLI",
  discord: "Discord",
  email: "Email",
  matrix: "Matrix",
  msteams: "Microsoft Teams",
  slack: "Slack",
  telegram: "Telegram",
  whatsapp: "WhatsApp",
};

function parseAutomationSearchQuery(query: string): AutomationSearchToken[] {
  return (query.match(/[^\s:]+:"[^"]+"|"[^"]+"|\S+/g) ?? [])
    .map((rawPart): AutomationSearchToken | null => {
      const part = trimAutomationSearchValue(rawPart);
      if (!part) return null;
      const fieldMatch = part.match(/^([A-Za-z]+):(.*)$/);
      if (!fieldMatch) return { field: null, value: part.toLowerCase() };
      const field = fieldMatch[1].toLowerCase() as AutomationSearchField;
      const value = trimAutomationSearchValue(fieldMatch[2]).toLowerCase();
      if (!value) return null;
      return AUTOMATION_SEARCH_FIELDS.has(field)
        ? { field, value }
        : { field: null, value: part.toLowerCase() };
    })
    .filter((token): token is AutomationSearchToken => Boolean(token));
}

function trimAutomationSearchValue(value: string): string {
  return value.trim().replace(/^"|"$/g, "").trim();
}

function automationMatchesSearch(job: SessionAutomationJob, tokens: AutomationSearchToken[]): boolean {
  return tokens.every((token) => automationSearchText(job, token.field).includes(token.value));
}

function automationSearchText(job: SessionAutomationJob, field: AutomationSearchField | null = null): string {
  return automationSearchParts(job, field)
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function automationSearchParts(
  job: SessionAutomationJob,
  field: AutomationSearchField | null,
): Array<string | number | null | undefined> {
  const originParts = automationOriginSearchParts(job);
  const scheduleParts = automationScheduleSearchParts(job);
  if (field === "id") return [job.id];
  if (field === "name") return [job.name, job.id];
  if (field === "message") return [job.payload.message, job.payload.command, job.trigger?.command];
  if (field === "chat") return originParts;
  if (field === "cron" || field === "schedule") return scheduleParts;
  if (field === "status") return [automationStatusKey(job), job.enabled ? "enabled" : "disabled"];
  return [
    job.id,
    job.name,
    job.payload.message,
    job.payload.command,
    job.trigger?.command,
    isLocalTriggerAutomation(job) ? "trigger local" : null,
    ...scheduleParts,
    automationStatusKey(job),
    ...originParts,
  ];
}

function automationOriginSearchParts(job: SessionAutomationJob): Array<string | null | undefined> {
  const origin = job.origin;
  if (!origin) return [];
  const channel = origin.channel.trim().toLowerCase();
  return [
    origin.session_key,
    origin.title,
    origin.preview,
    origin.channel,
    AUTOMATION_CHANNEL_LABELS[channel],
  ];
}

function automationScheduleSearchParts(job: SessionAutomationJob): Array<string | number | null | undefined> {
  const schedule = job.schedule;
  const parts: Array<string | number | null | undefined> = [
    schedule.kind,
    schedule.expr,
    schedule.tz,
    schedule.every_ms,
    schedule.at_ms,
  ];
  if (schedule.kind === "cron" && schedule.expr) {
    parts.push(...automationCronSearchParts(schedule.expr));
  }
  return parts;
}

function automationCronSearchParts(expr: string): string[] {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return [];
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
  const everyDay = dayOfMonth === "*" && month === "*" && dayOfWeek === "*";
  const numericMinute = cronNumericToken(minute, 59);
  const numericHour = cronNumericToken(hour, 23);
  if (numericMinute === null) return [];
  const paddedMinute = String(numericMinute).padStart(2, "0");

  if (numericHour !== null) {
    const time = `${String(numericHour).padStart(2, "0")}:${paddedMinute}`;
    return [time, `:${paddedMinute}`];
  }

  if (everyDay && hour === "*") {
    return [`:${paddedMinute}`, `hourly at :${paddedMinute}`];
  }

  const range = /^(\d{1,2})-(\d{1,2})$/.exec(hour);
  if (!everyDay || !range) return [];
  const start = Number(range[1]);
  const end = Number(range[2]);
  if (start > 23 || end > 23) return [];
  const paddedRange = `${String(start).padStart(2, "0")}-${String(end).padStart(2, "0")}`;
  const rawRange = `${start}-${end}`;
  return [
    paddedRange,
    rawRange,
    `:${paddedMinute}`,
    `${paddedRange} at :${paddedMinute}`,
    `hourly ${paddedRange} at :${paddedMinute}`,
  ];
}

function automationMatchesFilter(job: SessionAutomationJob, filter: AutomationFilter): boolean {
  const status = automationStatusKey(job);
  if (filter === "active") return status === "active" || status === "running";
  if (filter === "paused") return status === "paused";
  if (filter === "failed") return automationNeedsAttention(job);
  if (filter === "system") return Boolean(job.protected);
  return true;
}

const AUTOMATION_FILTER_TONES: Partial<
  Record<AutomationFilter, { text: string; selectedText: string; count: string }>
> = {
  active: {
    text: "text-emerald-600 dark:text-emerald-400",
    selectedText: "text-emerald-700 dark:text-emerald-300",
    count: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
  paused: {
    text: "text-amber-600 dark:text-amber-400",
    selectedText: "text-amber-700 dark:text-amber-300",
    count: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
  failed: {
    text: "text-rose-600 dark:text-rose-400",
    selectedText: "text-rose-700 dark:text-rose-300",
    count: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  },
  system: {
    text: "text-sky-600 dark:text-sky-400",
    selectedText: "text-sky-700 dark:text-sky-300",
    count: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  },
};

function automationFilterToneClass(value: AutomationFilter, count: number, selected: boolean): string {
  const tone = AUTOMATION_FILTER_TONES[value];
  if (count <= 0 || !tone) return "";
  return selected ? tone.selectedText : tone.text;
}

function automationFilterCountClass(value: AutomationFilter, count: number): string {
  const tone = AUTOMATION_FILTER_TONES[value];
  return count > 0 && tone ? tone.count : "";
}

function automationStatus(
  job: SessionAutomationJob,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): { label: string; tone: "neutral" | "success" | "warning" } {
  const status = automationStatusKey(job);
  if (status === "system") return { label: tx("settings.automations.status.system", "System"), tone: "neutral" };
  if (status === "running") {
    return { label: tx("settings.automations.status.running", "Running now"), tone: "warning" };
  }
  if (status === "paused") return { label: tx("settings.automations.status.paused", "Paused"), tone: "neutral" };
  if (status === "failed") {
    return { label: tx("settings.automations.status.failed", "Failed"), tone: "warning" };
  }
  if (status === "completed") {
    return { label: tx("settings.automations.status.completed", "Completed"), tone: "neutral" };
  }
  if (status === "idle") {
    return { label: tx("settings.automations.status.noSchedule", "No schedule"), tone: "neutral" };
  }
  return { label: tx("settings.automations.status.active", "Active"), tone: "success" };
}

function automationOriginLabel(
  job: SessionAutomationJob,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  if (job.protected) return tx("settings.automations.origin.system", "System");
  const origin = job.origin;
  if (!origin) return tx("settings.automations.origin.unknown", "No linked chat");
  if (origin.channel !== "websocket") return automationChannelLabel(origin.channel, tx);
  return origin.title || origin.preview || origin.session_key || automationChannelLabel(origin.channel, tx);
}

function automationChannelLabel(
  channel: string,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  const key = channel.trim().toLowerCase();
  return AUTOMATION_CHANNEL_LABELS[key]
    ? tx(`settings.automations.channels.${key}`, AUTOMATION_CHANNEL_LABELS[key])
    : channel;
}

function formatAutomationSchedule(
  job: SessionAutomationJob,
  locale: string,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  const recurrence = recurrenceOf(job);
  if (recurrence) {
    // The server said which preset this is, so describe that rather than
    // re-reading the expression and possibly disagreeing with the editor.
    const summary = describeRecurrence(recurrence, tx, locale);
    return job.schedule.tz
      ? tx("settings.automations.schedule.withTz", "{{summary}} · {{tz}}", {
          summary,
          tz: job.schedule.tz,
        })
      : summary;
  }
  if (job.schedule.kind === "at" && job.schedule.at_ms) {
    return tx("settings.automations.schedule.at", "At {{time}}", {
      time: fmtDateTime(job.schedule.at_ms, locale),
    });
  }
  if (job.schedule.kind === "every" && job.schedule.every_ms) {
    return tx("settings.automations.schedule.every", "Every {{duration}}", {
      duration: formatAutomationInterval(job.schedule.every_ms, locale),
    });
  }
  if (job.schedule.kind === "cron" && job.schedule.expr) {
    const summary = formatCronScheduleSummary(job.schedule.expr, tx);
    if (summary) {
      return job.schedule.tz
        ? tx("settings.automations.schedule.withTz", "{{summary}} · {{tz}}", {
            summary,
            tz: job.schedule.tz,
          })
        : summary;
    }
    return job.schedule.tz
      ? tx("settings.automations.schedule.cronWithTz", "Cron {{expr}} · {{tz}}", {
          expr: job.schedule.expr,
          tz: job.schedule.tz,
        })
      : tx("settings.automations.schedule.cron", "Cron {{expr}}", { expr: job.schedule.expr });
  }
  if (isLocalTriggerAutomation(job)) {
    return tx("settings.automations.schedule.local", "Local trigger");
  }
  return tx("settings.automations.schedule.custom", "Custom schedule");
}

function formatCronScheduleSummary(
  expr: string,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string | null {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
  const numericMinute = cronNumericToken(minute, 59);
  const numericHour = cronNumericToken(hour, 23);
  const everyDay = dayOfMonth === "*" && month === "*" && dayOfWeek === "*";
  const workdays = dayOfMonth === "*" && month === "*" && ["1-5", "MON-FRI", "mon-fri"].includes(dayOfWeek);

  if (numericMinute !== null && numericHour !== null) {
    const time = `${String(numericHour).padStart(2, "0")}:${String(numericMinute).padStart(2, "0")}`;
    if (everyDay) return tx("settings.automations.schedule.dailyAt", "Daily at {{time}}", { time });
    if (workdays) return tx("settings.automations.schedule.weekdaysAt", "Weekdays at {{time}}", { time });
  }

  if (everyDay && numericMinute !== null && hour === "*") {
    return tx("settings.automations.schedule.hourlyAt", "Hourly at :{{minute}}", {
      minute: String(numericMinute).padStart(2, "0"),
    });
  }

  const range = /^(\d{1,2})-(\d{1,2})$/.exec(hour);
  if (everyDay && numericMinute !== null && range) {
    const start = Number(range[1]);
    const end = Number(range[2]);
    if (start > 23 || end > 23) return null;
    return tx("settings.automations.schedule.hourlyWindow", "Hourly {{start}}-{{end}} at :{{minute}}", {
      start: String(start).padStart(2, "0"),
      end: String(end).padStart(2, "0"),
      minute: String(numericMinute).padStart(2, "0"),
    });
  }

  return null;
}

function cronNumericToken(value: string, max: number): number | null {
  if (!/^\d{1,2}$/.test(value)) return null;
  const parsed = Number(value);
  return parsed <= max ? parsed : null;
}

function formatAutomationNext(
  job: SessionAutomationJob,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  if (!job.enabled) return tx("settings.automations.next.paused", "Paused");
  if (job.state.pending) return tx("settings.automations.next.pending", "Running now");
  if (isLocalTriggerAutomation(job)) {
    return tx("settings.automations.next.local", "Waiting for trigger");
  }
  if (!job.state.next_run_at_ms) return tx("settings.automations.next.none", "No next run");
  return relativeTime(job.state.next_run_at_ms);
}

function formatAutomationNextTitle(
  job: SessionAutomationJob,
  locale: string,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string {
  if (!job.state.next_run_at_ms) return formatAutomationNext(job, tx);
  return fmtDateTime(job.state.next_run_at_ms, locale);
}

function automationStatusDotClass(job: SessionAutomationJob): string {
  const status = automationStatusKey(job);
  if (status === "active" || status === "running") return "bg-orange-500 shadow-[0_0_0_3px_rgba(249,115,22,0.12)]";
  if (status === "failed") return "bg-amber-500 shadow-[0_0_0_3px_rgba(245,158,11,0.13)]";
  if (status === "system") return "bg-muted-foreground/45";
  return "bg-muted-foreground/45";
}

function formatAutomationUnit(
  value: number,
  unit: Intl.NumberFormatOptions["unit"],
  locale: string,
  maximumFractionDigits = 0,
): string {
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit,
    unitDisplay: "long",
    maximumFractionDigits,
  }).format(value);
}

function formatAutomationInterval(ms: number, locale: string): string {
  const units: Array<[Intl.NumberFormatOptions["unit"], number]> = [
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
    ["second", 1000],
  ];
  for (const [unit, size] of units) {
    if (ms >= size && ms % size === 0) return formatAutomationUnit(ms / size, unit, locale);
  }
  const fallbackUnit = ms < 60_000 ? "second" : "minute";
  const fallbackSize = fallbackUnit === "second" ? 1000 : 60_000;
  return formatAutomationUnit(ms / fallbackSize, fallbackUnit, locale, 1);
}

function DismissibleStatusMessage({
  message,
  isError,
  onDismiss,
}: {
  message: string;
  isError: boolean;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 rounded-[12px] border py-2.5 pl-4 pr-2 text-[13px]",
        isError
          ? "border-destructive/20 bg-destructive/5 text-destructive"
          : "border-border/55 bg-muted/35 text-muted-foreground",
      )}
    >
      <span className="min-w-0">{message}</span>
      <button
        type="button"
        aria-label={tx("settings.actions.dismiss", "Dismiss")}
        title={tx("settings.actions.dismiss", "Dismiss")}
        onClick={onDismiss}
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors",
          isError
            ? "text-destructive/70 hover:bg-destructive/10 hover:text-destructive"
            : "text-muted-foreground/70 hover:bg-muted hover:text-foreground",
        )}
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}

function RestartRequiredNotice({
  message,
  onRestart,
  isRestarting,
}: {
  message: string;
  onRestart?: () => void;
  isRestarting?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3 rounded-[12px] border border-amber-500/20 bg-amber-500/8 px-4 py-3 text-[12.5px] text-amber-800 dark:text-amber-200 sm:flex-row sm:items-center sm:justify-between">
      <span>{message}</span>
      {onRestart ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onRestart}
          disabled={isRestarting}
          className="h-8 rounded-full bg-background/80 px-3 text-[12px] font-semibold"
        >
          {isRestarting ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          )}
          {isRestarting ? t("app.system.restarting") : t("app.system.restart")}
        </Button>
      ) : null}
    </div>
  );
}

function mcpSearchText(preset: McpPresetInfo): string {
  return [
    preset.name,
    preset.display_name,
    preset.description,
    preset.note,
    preset.category,
    ...(preset.modules ?? []),
  ]
    .join(" ")
    .toLowerCase();
}

function ToolsSettings({
  pane = "mcp",
  token,
  navinFeatures,
  loading,
  query,
  actionKey,
  docsBaseUrl,
  showBrandLogos,
  error,
  requiresRestartPending,
  onQueryChange,
  onAction,
  onFeaturesUpdate,
  onDismissStatus,
  onRestart,
  isRestarting,
  mcpPresets,
  mcpPresetsLoading,
  mcpActionKey,
  mcpMessage,
  mcpError,
  mcpFieldValues,
  onMcpFieldChange,
  onMcpAction,
  onMcpToolsChange,
}: {
  pane?: ToolsPane;
  token: string;
  navinFeatures: NavinFeaturesPayload | null;
  loading: boolean;
  query: string;
  actionKey: string | null;
  docsBaseUrl?: string;
  showBrandLogos: boolean;
  error: string | null;
  requiresRestartPending: boolean;
  onQueryChange: (value: string) => void;
  onAction: (action: "enable" | "disable", name: string) => void;
  onFeaturesUpdate: (payload: NavinFeaturesPayload) => void;
  onDismissStatus: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
  mcpPresets: McpPresetsPayload | null;
  mcpPresetsLoading: boolean;
  mcpActionKey: string | null;
  mcpMessage: string | null;
  mcpError: string | null;
  mcpFieldValues: Record<string, Record<string, string>>;
  onMcpFieldChange: (presetName: string, fieldName: string, value: string) => void;
  onMcpAction: (action: "enable" | "remove" | "test", name: string, values?: Record<string, string>) => void;
  onMcpToolsChange: (name: string, enabledTools: string[]) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const normalizedQuery = query.trim().toLowerCase();
  const containerRef = useRef<HTMLDivElement>(null);
  const showMcp = pane === "mcp";
  const showChannels = pane === "channels";
  const allPresets = (mcpPresets?.presets ?? []).filter(
    (preset) => !normalizedQuery || mcpSearchText(preset).includes(normalizedQuery),
  );
  const sharedPresets = allPresets
    .filter((preset) => SHARED_MCP_NAMES.has(preset.name))
    .sort((left, right) => {
      if (left.name === "linkedin" && right.name !== "linkedin") return -1;
      if (right.name === "linkedin" && left.name !== "linkedin") return 1;
      return left.display_name.localeCompare(right.display_name);
    });
  const careerPresets = allPresets
    .filter((preset) => isCareerMcp(preset) && !SHARED_MCP_NAMES.has(preset.name))
    .sort((left, right) => left.display_name.localeCompare(right.display_name));
  const tendersPresets = allPresets
    .filter((preset) => isTendersMcp(preset) && !SHARED_MCP_NAMES.has(preset.name))
    .sort((left, right) => left.display_name.localeCompare(right.display_name));
  const otherPresets = allPresets
    .filter((preset) => !isCareerMcp(preset) && !isTendersMcp(preset))
    .sort((left, right) => {
      const byReady = Number(!(left.configured && left.installed)) - Number(!(right.configured && right.installed));
      return byReady || left.display_name.localeCompare(right.display_name);
    });
  const allChannels = mergeSocialChannelFeatures(navinFeatures?.features ?? [])
    .filter((feature) => feature.type === "channel")
    .filter((feature) => !isHiddenToolsChannel(feature.name))
    .filter((feature) => !normalizedQuery || channelSearchText(feature).includes(normalizedQuery))
    .sort((left, right) => {
      const rank = Number(!left.ready) - Number(!right.ready);
      return rank || channelDisplayName(left).localeCompare(channelDisplayName(right));
    });
  const channels = showChannels ? allChannels : [];
  const [channelTab, setChannelTab] = useState<ChannelFilterTab>("all");
  const socialChannels = channels.filter((feature) => isSocialChannel(feature.name) || feature.kind === "social");
  const chatChannels = channels.filter((feature) => channelFilterTab(feature.name) === "chat");
  const otherChannels = channels.filter((feature) => channelFilterTab(feature.name) === "other");
  const visibleSocial = channelTab === "all" || channelTab === "social" ? socialChannels : [];
  const visibleChat = channelTab === "all" || channelTab === "chat" ? chatChannels : [];
  const visibleOther = channelTab === "all" || channelTab === "other" ? otherChannels : [];
  const [selectedChannelName, setSelectedChannelName] = useState<string | null>(null);
  const selectedChannel =
    selectedChannelName
      ? channels.find((feature) => feature.name === selectedChannelName) ?? null
      : null;
  const statusMessage = error || mcpError || (!selectedChannel ? mcpMessage : null);
  const statusIsError = Boolean(error || mcpError);
  const catalogLoading = showChannels
    ? loading && !navinFeatures
    : (loading && !navinFeatures) || (mcpPresetsLoading && !mcpPresets);
  const hasMcp = showMcp && allPresets.length > 0;
  const hasChannels = channels.length > 0;
  const empty = !catalogLoading && !hasMcp && !hasChannels;

  useEffect(() => {
    if (selectedChannelName && !channels.some((feature) => feature.name === selectedChannelName)) {
      setSelectedChannelName(null);
    }
  }, [channels, selectedChannelName]);

  const openChannel = (name: string) => {
    setSelectedChannelName(name);
  };

  return (
    <div
      ref={containerRef}
      className="flex min-h-full flex-1 flex-col xl:min-h-0 xl:overflow-hidden"
    >
      <section className="shrink-0 space-y-5">
        {showChannels ? null : (
          <p className="max-w-[46rem] text-[13px] leading-6 text-muted-foreground">
            {tx(
              "settings.tools.description",
              "Enable official MCP the agent can call. Tenders recommends LinkedIn for buyer research. Career uses Notion, GitHub, and Exa from this page.",
            )}
          </p>
        )}
        <div className="relative min-w-0">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={
              showChannels
                ? tx("settings.tools.searchChannelsPlaceholder", "Search channels")
                : tx("settings.tools.searchMcpPlaceholder", "Search MCP")
            }
            className="h-11 rounded-2xl border-border/60 bg-card/90 pl-10 text-[13px] shadow-sm"
          />
        </div>
        {showChannels ? (
          <div
            role="tablist"
            aria-label={tx("settings.tools.channelFilters", "Channel filters")}
            className="inline-flex flex-wrap rounded-2xl border border-border/60 bg-muted/50 p-1"
          >
            {(
              [
                ["all", tx("settings.tools.filterAll", "All"), channels.length],
                ["social", tx("settings.tools.filterSocial", "Social networks"), socialChannels.length],
                ["chat", tx("settings.tools.filterChat", "Chat"), chatChannels.length],
                ["other", tx("settings.tools.filterOther", "Other"), otherChannels.length],
              ] as const
            ).map(([id, label, count]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={channelTab === id}
                onClick={() => setChannelTab(id)}
                className={cn(
                  "rounded-xl px-3.5 py-1.5 text-[13px] font-semibold transition-colors",
                  channelTab === id
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
                <span className="ml-1.5 text-[11px] font-medium text-muted-foreground">{count}</span>
              </button>
            ))}
          </div>
        ) : null}
      </section>

      {statusMessage ? (
        <div className="mt-3 shrink-0">
          <DismissibleStatusMessage
            message={statusMessage}
            isError={statusIsError}
            onDismiss={onDismissStatus}
          />
        </div>
      ) : null}

      {requiresRestartPending ? (
        <div className="mt-3 shrink-0">
          <RestartRequiredNotice
            message={tx("settings.tools.restartRequired", "Restart Navin to apply updated tool support.")}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </div>
      ) : null}

      <section className="mt-5 flex min-h-0 flex-1 flex-col overflow-hidden">
        {catalogLoading ? (
          <div className="flex h-36 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
            {tx("settings.tools.loading", "Loading tools...")}
          </div>
        ) : empty ? (
          <div className="min-h-0 flex-1 px-3 py-12 text-center text-sm text-muted-foreground">
            {showChannels
              ? tx("settings.tools.emptyChannels", "No channels match this search.")
              : tx("settings.tools.empty", "No tools match this search.")}
          </div>
        ) : (
          <div
            className={cn(
              "grid min-h-0 flex-1 gap-6 overflow-hidden",
              selectedChannel
                ? "grid-cols-[minmax(0,1fr)_minmax(300px,440px)]"
                : "grid-cols-1",
            )}
          >
            <div className="min-h-0 space-y-6 overflow-y-auto overscroll-contain pr-1">
              {hasMcp ? (
                <div className="space-y-4" data-testid="tools-mcp">
                  {sharedPresets.length ? (
                    <div>
                      <div className="mb-2.5 flex items-center gap-2 px-0.5">
                        <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                          {tx("settings.tools.sharedMcp", "Shared MCP")}
                        </h3>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                          {sharedPresets.length}
                        </span>
                      </div>
                      <p className="mb-2.5 max-w-xl px-0.5 text-[12px] leading-5 text-muted-foreground">
                        {tx(
                          "settings.tools.sharedMcpHint",
                          "One LinkedIn session for Tenders (buyer research, never a notice) and Career (jobs and profile). Never scrape. Navin never applies for you. Exa is public web only.",
                        )}
                      </p>
                      <div className="grid gap-2 md:grid-cols-2">
                        {sharedPresets.map((preset) => (
                          <McpAppsCatalogRow
                            key={preset.name}
                            preset={preset}
                            values={mcpFieldValues[preset.name] ?? {}}
                            actionKey={mcpActionKey}
                            showBrandLogos={showBrandLogos}
                            onFieldChange={onMcpFieldChange}
                            onAction={onMcpAction}
                            onToolsChange={onMcpToolsChange}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {tendersPresets.length ? (
                    <div>
                      <div className="mb-2.5 flex items-center gap-2 px-0.5">
                        <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                          {tx("settings.tools.tendersMcp", "Tenders MCP")}
                        </h3>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                          {tendersPresets.length}
                        </span>
                      </div>
                      <p className="mb-2.5 max-w-xl px-0.5 text-[12px] leading-5 text-muted-foreground">
                        {tx(
                          "settings.tools.tendersMcpHint",
                          "Tenders-only servers. LinkedIn and Exa live in Shared MCP above.",
                        )}
                      </p>
                      <div className="grid gap-2 md:grid-cols-2">
                        {tendersPresets.map((preset) => (
                          <McpAppsCatalogRow
                            key={preset.name}
                            preset={preset}
                            values={mcpFieldValues[preset.name] ?? {}}
                            actionKey={mcpActionKey}
                            showBrandLogos={showBrandLogos}
                            onFieldChange={onMcpFieldChange}
                            onAction={onMcpAction}
                            onToolsChange={onMcpToolsChange}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {careerPresets.length ? (
                    <div>
                      <div className="mb-2.5 flex items-center gap-2 px-0.5">
                        <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                          {tx("settings.tools.careerMcp", "Career MCP")}
                        </h3>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                          {careerPresets.length}
                        </span>
                      </div>
                      <p className="mb-2.5 max-w-xl px-0.5 text-[12px] leading-5 text-muted-foreground">
                        {tx(
                          "settings.tools.careerMcpHint",
                          "Career notes and portfolio servers. LinkedIn lives in Shared MCP above. Never scrape. Navin never applies for you.",
                        )}
                      </p>
                      <div className="grid gap-2 md:grid-cols-2">
                        {careerPresets.map((preset) => (
                          <McpAppsCatalogRow
                            key={preset.name}
                            preset={preset}
                            values={mcpFieldValues[preset.name] ?? {}}
                            actionKey={mcpActionKey}
                            showBrandLogos={showBrandLogos}
                            onFieldChange={onMcpFieldChange}
                            onAction={onMcpAction}
                            onToolsChange={onMcpToolsChange}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {otherPresets.length ? (
                    <div>
                      <div className="mb-2.5 flex items-center gap-2 px-0.5">
                        <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                          {tx("settings.tools.otherMcp", "Other MCP")}
                        </h3>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                          {otherPresets.length}
                        </span>
                      </div>
                      <div className="grid gap-2 md:grid-cols-2">
                        {otherPresets.map((preset) => (
                          <McpAppsCatalogRow
                            key={preset.name}
                            preset={preset}
                            values={mcpFieldValues[preset.name] ?? {}}
                            actionKey={mcpActionKey}
                            showBrandLogos={showBrandLogos}
                            onFieldChange={onMcpFieldChange}
                            onAction={onMcpAction}
                            onToolsChange={onMcpToolsChange}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {hasChannels ? (
                <div data-testid="tools-channels" className="space-y-6">
                  {visibleSocial.length ? (
                    <div>
                      {channelTab === "all" ? (
                        <div className="mb-2.5 flex items-center gap-2 px-0.5">
                          <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                            {tx("settings.tools.socialChannelsTitle", "Social networks")}
                          </h3>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                            {visibleSocial.length}
                          </span>
                        </div>
                      ) : null}
                      {channelTab === "social" ? (
                        <p className="mb-2.5 max-w-xl px-0.5 text-[12px] leading-5 text-muted-foreground">
                          {tx(
                            "settings.tools.socialChannelsHint",
                            "Same accounts as Marketing. Connect, turn On, then agents can publish.",
                          )}
                        </p>
                      ) : null}
                      <div className="space-y-2">
                        {visibleSocial.map((feature) => (
                          <ChannelCatalogRow
                            key={feature.name}
                            feature={feature}
                            selected={selectedChannel?.name === feature.name}
                            showBrandLogos={showBrandLogos}
                            onSelect={() => openChannel(feature.name)}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {visibleChat.length ? (
                    <div>
                      {channelTab === "all" ? (
                        <div className="mb-2.5 flex items-center gap-2 px-0.5">
                          <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                            {tx("settings.tools.chatChannelsTitle", "Chat")}
                          </h3>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                            {visibleChat.length}
                          </span>
                        </div>
                      ) : null}
                      <div className="space-y-2">
                        {visibleChat.map((feature) => (
                          <ChannelCatalogRow
                            key={feature.name}
                            feature={feature}
                            selected={selectedChannel?.name === feature.name}
                            showBrandLogos={showBrandLogos}
                            onSelect={() => openChannel(feature.name)}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {visibleOther.length ? (
                    <div>
                      {channelTab === "all" ? (
                        <div className="mb-2.5 flex items-center gap-2 px-0.5">
                          <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                            {tx("settings.tools.otherChannelsTitle", "Other")}
                          </h3>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                            {visibleOther.length}
                          </span>
                        </div>
                      ) : null}
                      <div className="space-y-2">
                        {visibleOther.map((feature) => (
                          <ChannelCatalogRow
                            key={feature.name}
                            feature={feature}
                            selected={selectedChannel?.name === feature.name}
                            showBrandLogos={showBrandLogos}
                            onSelect={() => openChannel(feature.name)}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {!visibleSocial.length && !visibleChat.length && !visibleOther.length ? (
                    <div className="px-3 py-10 text-center text-sm text-muted-foreground">
                      {tx("settings.tools.emptyChannels", "No channels match this search.")}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
            {selectedChannel ? (
              <div className="min-h-0 overflow-y-auto overscroll-contain">
                <ChannelSetupPanel
                  token={token}
                  feature={selectedChannel}
                  actionKey={actionKey}
                  docsBaseUrl={docsBaseUrl}
                  showBrandLogos={showBrandLogos}
                  onAction={onAction}
                  onFeaturesUpdate={onFeaturesUpdate}
                />
              </div>
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}

function AppsCatalogSettings({
  cliApps,
  mcpPresets,
  cliAppsLoading,
  mcpPresetsLoading,
  query,
  filter,
  cliActionKey,
  mcpActionKey,
  cliMessage,
  cliError,
  cliFocusName,
  mcpMessage,
  mcpError,
  mcpFieldValues,
  customMcpForm,
  mcpConfigImport,
  showBrandLogos,
  requiresRestartPending,
  onQueryChange,
  onFilterChange,
  onCliAction,
  onMcpAction,
  onDismissStatus,
  onBackToChat,
  onMcpFieldChange,
  onCustomMcpFormChange,
  onMcpConfigImportChange,
  onSaveCustomMcp,
  onImportMcpConfig,
  onMcpToolsChange,
  onRestart,
  isRestarting,
}: {
  cliApps: CliAppsPayload | null;
  mcpPresets: McpPresetsPayload | null;
  cliAppsLoading: boolean;
  mcpPresetsLoading: boolean;
  query: string;
  filter: AppsKindFilter;
  cliActionKey: string | null;
  mcpActionKey: string | null;
  cliMessage: string | null;
  cliError: string | null;
  cliFocusName: string | null;
  mcpMessage: string | null;
  mcpError: string | null;
  mcpFieldValues: Record<string, Record<string, string>>;
  customMcpForm: CustomMcpForm;
  mcpConfigImport: string;
  showBrandLogos: boolean;
  requiresRestartPending: boolean;
  onQueryChange: (value: string) => void;
  onFilterChange: (value: AppsKindFilter) => void;
  onCliAction: (action: "install" | "update" | "uninstall" | "test", name: string) => void;
  onMcpAction: (action: "enable" | "remove" | "test", name: string, values?: Record<string, string>) => void;
  onDismissStatus: () => void;
  onBackToChat: () => void;
  onMcpFieldChange: (presetName: string, fieldName: string, value: string) => void;
  onCustomMcpFormChange: Dispatch<SetStateAction<CustomMcpForm>>;
  onMcpConfigImportChange: (value: string) => void;
  onSaveCustomMcp: () => void;
  onImportMcpConfig: () => void;
  onMcpToolsChange: (name: string, enabledTools: string[]) => void;
  onRestart?: () => void;
  isRestarting?: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const filterOptions = [
    { value: "all", label: tx("settings.apps.filterCatalog", "All") },
    { value: "mcp", label: tx("settings.apps.filterMcp", "MCP") },
    { value: "cli", label: tx("settings.apps.filterCli", "CLI") },
    { value: "ready", label: tx("settings.apps.filterReady", "Ready") },
  ];
  const normalizedQuery = query.trim().toLowerCase();
  const items: AppsCatalogItem[] = [
    ...(mcpPresets?.presets ?? []).map((preset) => ({
      id: `mcp:${preset.name}`,
      kind: "mcp" as const,
      preset,
    })),
    ...(cliApps?.apps ?? []).map((app) => ({ id: `cli:${app.name}`, kind: "cli" as const, app })),
  ]
    .filter((item) => {
      if (normalizedQuery && !appsSearchText(item).includes(normalizedQuery)) return false;
      if (filter === "all") return true;
      if (filter === "ready") return appsReady(item);
      return item.kind === filter;
    })
    .sort((left, right) => {
      const catalogRank = (item: AppsCatalogItem) => {
        if (item.kind === "mcp" && item.preset.category === "ads") return 0;
        if (item.kind === "mcp") return 1;
        return 2;
      };
      const byKind = catalogRank(left) - catalogRank(right);
      if (byKind) return byKind;
      const byReady = Number(!appsReady(left)) - Number(!appsReady(right));
      return byReady || appsTitle(left).localeCompare(appsTitle(right));
    });
  const focusedApp = cliFocusName
    ? (cliApps?.apps ?? []).find((app) => app.name === cliFocusName && app.installed)
    : null;
  // Show the catalog as soon as either side answers - do not wait on slow CLI refresh.
  const loading = !cliApps && !mcpPresets && (cliAppsLoading || mcpPresetsLoading);
  const refreshing =
    Boolean(cliApps || mcpPresets) && (cliAppsLoading || mcpPresetsLoading);
  const blockingCliError =
    cliError && !(cliApps && /timed out/i.test(cliError)) ? cliError : null;
  const blockingMcpError =
    mcpError && !(mcpPresets && /timed out/i.test(mcpError)) ? mcpError : null;
  const statusMessage =
    blockingCliError ||
    blockingMcpError ||
    (!focusedApp ? cliMessage || mcpMessage : null);
  const statusIsError = Boolean(blockingCliError || blockingMcpError);
  return (
    <div className="space-y-5">
      <McpCustomServerPanel
        form={customMcpForm}
        configImport={mcpConfigImport}
        actionKey={mcpActionKey}
        onFormChange={onCustomMcpFormChange}
        onConfigImportChange={onMcpConfigImportChange}
        onSave={onSaveCustomMcp}
        onImportConfig={onImportMcpConfig}
      />

      <section className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={tx(
              "settings.apps.searchPlaceholder",
              "Search MCP or plugins (ads, google, meta...)",
            )}
            className="h-11 rounded-2xl border-border/60 bg-card/90 pl-10 text-[13px] shadow-sm"
          />
        </div>
        <SegmentedControl
          value={filter}
          options={filterOptions}
          onChange={(value) => onFilterChange(value as AppsKindFilter)}
        />
        {refreshing ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {tx("settings.apps.refreshing", "Refreshing catalog...")}
          </span>
        ) : null}
      </section>

      {statusMessage ? (
        <DismissibleStatusMessage
          message={statusMessage}
          isError={statusIsError}
          onDismiss={onDismissStatus}
        />
      ) : null}

      {focusedApp ? (
        <CliAppReadyPanel app={focusedApp} showBrandLogos={showBrandLogos} onBackToChat={onBackToChat} />
      ) : null}

      {requiresRestartPending ? (
        <RestartRequiredNotice
          message={tx("settings.apps.restartRequired", "Restart navin to apply updated plugins and integrations.")}
          onRestart={onRestart}
          isRestarting={isRestarting}
        />
      ) : null}

      <section>
        {loading ? (
          <div className="flex h-36 items-center justify-center rounded-2xl border border-dashed border-border/60 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
            {tx("settings.apps.loading", "Loading plugins & MCP...")}
          </div>
        ) : items.length ? (
          <div className="grid gap-2 md:grid-cols-2">
            {items.map((item) =>
              item.kind === "cli" ? (
                <CliAppsCatalogRow
                  key={item.id}
                  app={item.app}
                  actionKey={cliActionKey}
                  showBrandLogos={showBrandLogos}
                  onAction={onCliAction}
                />
              ) : (
                <McpAppsCatalogRow
                  key={item.id}
                  preset={item.preset}
                  values={mcpFieldValues[item.preset.name] ?? {}}
                  actionKey={mcpActionKey}
                  showBrandLogos={showBrandLogos}
                  onFieldChange={onMcpFieldChange}
                  onAction={onMcpAction}
                  onToolsChange={onMcpToolsChange}
                />
              ),
            )}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-border/60 bg-card/60 px-3 py-12 text-center text-sm text-muted-foreground">
            <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted/60">
              <Puzzle className="h-5 w-5 text-muted-foreground/70" aria-hidden />
            </div>
            <div className="font-medium text-foreground/80">
              {tx("settings.apps.empty", "No plugins match this view.")}
            </div>
            <p className="mx-auto mt-2 max-w-md text-[12px]">
              {tx(
                "settings.apps.emptyHint",
                "Switch to All or MCP to see the full catalog, or add a custom server above.",
              )}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function CliAppsCatalogRow({
  app,
  actionKey,
  showBrandLogos,
  onAction,
}: {
  app: CliAppInfo;
  actionKey: string | null;
  showBrandLogos: boolean;
  onAction: (action: "install" | "update" | "uninstall" | "test", name: string) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const installBusy = actionKey === `install:${app.name}`;
  const updateBusy = actionKey === `update:${app.name}`;
  const uninstallBusy = actionKey === `uninstall:${app.name}`;
  const testBusy = actionKey === `test:${app.name}`;
  const busy = installBusy || updateBusy || uninstallBusy || testBusy;
  const description = app.description || app.requires || app.entry_point || app.name;

  return (
    <article className="group flex min-w-0 items-center gap-3 rounded-2xl border border-border/45 bg-card/80 px-3.5 py-3.5 shadow-sm transition-colors hover:border-primary/25 hover:bg-primary/[0.03]">
      <CliAppLogo app={app} showBrandLogos={showBrandLogos} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-[15px] font-semibold leading-5 text-foreground">{app.display_name}</h3>
          <AppsTypeBadge>{tx("settings.apps.cliLabel", "Plugin")}</AppsTypeBadge>
        </div>
        <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted-foreground">{description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {app.installed ? (
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <AppsActionButton
                  ariaLabel={tx("settings.cliApps.statusInstalled", "CLI installed")}
                  busy={testBusy || updateBusy}
                  disabled={busy}
                  tone="installed"
                >
                  <Check className="h-4 w-4" aria-hidden />
                </AppsActionButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={busy} onClick={() => onAction("test", app.name)}>
                  <PlayCircle className="mr-2 h-3.5 w-3.5" aria-hidden />
                  {tx("settings.cliApps.test", "Test CLI")}
                </DropdownMenuItem>
                <DropdownMenuItem disabled={busy} onClick={() => onAction("update", app.name)}>
                  <RotateCcw className="mr-2 h-3.5 w-3.5" aria-hidden />
                  {tx("settings.cliApps.update", "Update CLI")}
                </DropdownMenuItem>
                <DropdownMenuItem disabled={busy} onClick={() => onAction("uninstall", app.name)}>
                  <Trash2 className="mr-2 h-3.5 w-3.5" aria-hidden />
                  {tx("settings.cliApps.uninstall", "Uninstall CLI")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <AppsActionButton
              ariaLabel={tx("settings.cliApps.uninstall", "Uninstall CLI")}
              busy={uninstallBusy}
              disabled={busy && !uninstallBusy}
              tone="danger"
              onClick={() => onAction("uninstall", app.name)}
            >
              <Trash2 className="h-4 w-4" aria-hidden />
            </AppsActionButton>
          </>
        ) : app.install_supported ? (
          <AppsActionButton
            ariaLabel={tx("settings.cliApps.install", "Install CLI")}
            busy={installBusy}
            onClick={() => onAction("install", app.name)}
          >
            <Plus className="h-4 w-4" aria-hidden />
          </AppsActionButton>
        ) : (
          <AppsActionButton ariaLabel={tx("settings.cliApps.unavailable", "Unavailable")} disabled>
            <Plus className="h-4 w-4" aria-hidden />
          </AppsActionButton>
        )}
      </div>
    </article>
  );
}

function McpAppsCatalogRow({
  preset,
  values,
  actionKey,
  showBrandLogos,
  onFieldChange,
  onAction,
  onToolsChange,
}: {
  preset: McpPresetInfo;
  values: Record<string, string>;
  actionKey: string | null;
  showBrandLogos: boolean;
  onFieldChange: (presetName: string, fieldName: string, value: string) => void;
  onAction: (action: "enable" | "remove" | "test", name: string, values?: Record<string, string>) => void;
  onToolsChange: (name: string, enabledTools: string[]) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [setupOpen, setSetupOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const enableBusy = actionKey === `enable:${preset.name}`;
  const removeBusy = actionKey === `remove:${preset.name}`;
  const testBusy = actionKey === `test:${preset.name}`;
  const toolsBusy = actionKey === `tools:${preset.name}`;
  const busy = enableBusy || removeBusy || testBusy || toolsBusy;
  const missingFields = preset.required_fields.filter((field) => field.required && !field.configured);
  const hasFields = preset.required_fields.length > 0;
  const needsSetupInput = missingFields.length > 0;
  const readyInstalled = preset.installed && preset.configured;
  const canEnable =
    preset.install_supported &&
    (missingFields.length === 0 || missingFields.every((field) => Boolean(values[field.name]?.trim())));
  const toolNames = preset.tool_names ?? [];
  const enabledTools = preset.enabled_tools ?? ["*"];
  const allowAllTools = enabledTools.includes("*");
  const enabledSet = new Set(allowAllTools ? toolNames : enabledTools);
  const description = preset.description || preset.note || preset.requires || preset.name;
  const statusLabel = mcpPresetStatusLabel(preset.status, tx);

  useEffect(() => {
    if (preset.configured || !preset.install_supported) setSetupOpen(false);
  }, [preset.configured, preset.install_supported]);

  const enableOrOpenSetup = () => {
    if (needsSetupInput || (preset.installed && !preset.configured && hasFields)) {
      setSetupOpen(true);
      return;
    }
    onAction("enable", preset.name, values);
  };
  const submitSetup = () => {
    if (!canEnable) return;
    onAction("enable", preset.name, values);
  };
  const setTools = (next: string[]) => onToolsChange(preset.name, next);
  const toggleTool = (toolName: string) => {
    const next = new Set(allowAllTools ? toolNames : enabledTools);
    if (next.has(toolName)) next.delete(toolName);
    else next.add(toolName);
    const nextValues = Array.from(next);
    setTools(nextValues.length === toolNames.length ? ["*"] : nextValues);
  };

  return (
    <article className="rounded-2xl border border-border/45 bg-card/80 shadow-sm transition-colors hover:border-primary/25 hover:bg-primary/[0.03]">
      <div className="group flex min-w-0 items-center gap-3 px-3.5 py-3.5">
        <McpPresetLogo preset={preset} showBrandLogos={showBrandLogos} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-baseline gap-2">
            <h3 className="truncate text-[15px] font-semibold leading-5 text-foreground">{preset.display_name}</h3>
            {preset.name === "linkedin" ? (
              <span className="shrink-0 rounded-full bg-emerald-700/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:text-emerald-200">
                {tx("settings.mcp.recommended", "Recommended")}
              </span>
            ) : null}
            <AppsTypeBadge>{tx("settings.apps.mcpLabel", "Integration")}</AppsTypeBadge>
          </div>
          <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted-foreground">{description}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {readyInstalled ? (
            <>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <AppsActionButton
                    ariaLabel={statusLabel}
                    busy={testBusy || toolsBusy}
                    disabled={busy}
                    tone="installed"
                  >
                    <Check className="h-4 w-4" aria-hidden />
                  </AppsActionButton>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem disabled={busy} onClick={() => onAction("test", preset.name)}>
                    <PlayCircle className="mr-2 h-3.5 w-3.5" aria-hidden />
                    {tx("settings.mcp.test", "Test")}
                  </DropdownMenuItem>
                  {toolNames.length ? (
                    <DropdownMenuItem disabled={busy} onClick={() => setToolsOpen((open) => !open)}>
                      <SlidersHorizontal className="mr-2 h-3.5 w-3.5" aria-hidden />
                      {tx("settings.mcp.toolScope", "Tools")}
                    </DropdownMenuItem>
                  ) : null}
                  <DropdownMenuItem disabled={busy} onClick={() => onAction("remove", preset.name)}>
                    <Trash2 className="mr-2 h-3.5 w-3.5" aria-hidden />
                    {tx("settings.mcp.remove", "Remove")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <AppsActionButton
                ariaLabel={tx("settings.mcp.remove", "Remove")}
                busy={removeBusy}
                disabled={busy && !removeBusy}
                tone="danger"
                onClick={() => onAction("remove", preset.name)}
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </AppsActionButton>
            </>
          ) : preset.installed && !preset.configured ? (
            <AppsActionButton
              ariaLabel={hasFields ? tx("settings.mcp.configure", "Configure") : tx("settings.mcp.enable", "Enable")}
              busy={enableBusy}
              onClick={() => {
                if (hasFields) setSetupOpen(true);
                else onAction("enable", preset.name, values);
              }}
            >
              <Plus className="h-4 w-4" aria-hidden />
            </AppsActionButton>
          ) : preset.install_supported ? (
            <AppsActionButton
              ariaLabel={needsSetupInput ? tx("settings.mcp.setup", "Set up") : tx("settings.mcp.enable", "Enable")}
              busy={enableBusy}
              onClick={enableOrOpenSetup}
            >
              <Plus className="h-4 w-4" aria-hidden />
            </AppsActionButton>
          ) : null}
        </div>
      </div>

      {setupOpen && preset.install_supported && hasFields ? (
        <div className="mx-3 mb-3 rounded-[14px] border border-border/45 bg-card/85 p-3 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-semibold text-foreground">
                {t("settings.mcp.connectTitle", {
                  name: preset.display_name,
                  defaultValue: "Connect {{name}}",
                })}
              </div>
              <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                {tx("settings.mcp.connectHint", "Add the key from your account settings.")}
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => setSetupOpen(false)}
              className="h-7 rounded-full px-2.5 text-[11.5px] font-semibold text-muted-foreground"
            >
              {tx("actions.cancel", "Cancel")}
            </Button>
          </div>
          <div className="mt-3 grid gap-2">
            {preset.required_fields.map((field) => (
              <label key={field.name} className="min-w-0">
                <span className="mb-1 block text-[11.5px] font-medium text-muted-foreground">
                  {field.label}
                  {field.configured ? (
                    <span className="ml-1 font-normal text-emerald-600 dark:text-emerald-300">
                      {tx("settings.mcp.configured", "configured")}
                    </span>
                  ) : null}
                </span>
                <Input
                  type={field.secret ? "password" : "text"}
                  value={values[field.name] ?? ""}
                  onChange={(event) => onFieldChange(preset.name, field.name, event.target.value)}
                  placeholder={
                    field.configured
                      ? tx("settings.mcp.keepExisting", "Leave blank to keep existing")
                      : field.placeholder
                  }
                  className="h-9 rounded-full bg-background/80 text-[12.5px]"
                />
              </label>
            ))}
          </div>
          <div className="mt-3 flex justify-end">
            <Button
              type="button"
              size="sm"
              disabled={busy || !canEnable}
              onClick={submitSetup}
              className="h-8 rounded-full px-3 text-[12px] font-semibold"
            >
              {enableBusy ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              )}
              {preset.installed
                ? tx("settings.mcp.updateSetup", "Update setup")
                : tx("settings.mcp.saveAndEnable", "Save and enable")}
            </Button>
          </div>
        </div>
      ) : null}

      {toolsOpen && readyInstalled && toolNames.length ? (
        <div className="mx-3 mb-3 rounded-[14px] border border-border/45 bg-card/85 p-3 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[11.5px] font-medium text-muted-foreground">
              {tx("settings.mcp.toolScope", "Tools")}
            </div>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                size="sm"
                variant={allowAllTools ? "default" : "outline"}
                disabled={toolsBusy}
                onClick={() => setTools(["*"])}
                className="h-7 rounded-full px-2.5 text-[11.5px] font-semibold"
              >
                {tx("settings.mcp.allTools", "All")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant={!allowAllTools && enabledSet.size === 0 ? "default" : "outline"}
                disabled={toolsBusy}
                onClick={() => setTools([])}
                className="h-7 rounded-full px-2.5 text-[11.5px] font-semibold"
              >
                {tx("settings.mcp.noTools", "None")}
              </Button>
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {toolNames.map((toolName) => {
              const selected = enabledSet.has(toolName);
              return (
                <button
                  key={toolName}
                  type="button"
                  disabled={toolsBusy}
                  onClick={() => toggleTool(toolName)}
                  className={cn(
                    "max-w-full rounded-full border px-2.5 py-1 font-mono text-[11px] transition-colors",
                    selected
                      ? "border-blue-500/25 bg-blue-500/10 text-blue-700 dark:text-blue-300"
                      : "border-border/55 bg-muted/30 text-muted-foreground hover:bg-muted/60",
                  )}
                >
                  <span className="block max-w-[220px] truncate">{toolName}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </article>
  );
}

function AppsTypeBadge({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium leading-none text-muted-foreground">
      {children}
    </span>
  );
}

const AppsActionButton = forwardRef<HTMLButtonElement, {
  ariaLabel: string;
  busy?: boolean;
  disabled?: boolean;
  tone?: "default" | "installed" | "danger";
  onClick?: () => void;
  children: ReactNode;
}>(function AppsActionButton({
  ariaLabel,
  busy,
  disabled,
  tone = "default",
  onClick,
  children,
}, ref) {
  return (
    <Button
      ref={ref}
      type="button"
      size="icon"
      variant="ghost"
      aria-label={ariaLabel}
      title={ariaLabel}
      disabled={disabled || busy}
      onClick={onClick}
      className={cn(
        "h-9 w-9 rounded-full text-muted-foreground transition-colors",
        tone === "installed" && "bg-transparent hover:bg-muted/70 hover:text-foreground",
        tone === "danger" && "bg-transparent hover:bg-destructive/10 hover:text-destructive",
        tone === "default" && "bg-muted/70 hover:bg-muted hover:text-foreground",
      )}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : children}
    </Button>
  );
});

function appsTitle(item: AppsCatalogItem): string {
  return item.kind === "cli" ? item.app.display_name : item.preset.display_name;
}

function appsReady(item: AppsCatalogItem): boolean {
  return item.kind === "cli" ? item.app.installed : item.preset.installed && item.preset.configured;
}

function appsSearchText(item: AppsCatalogItem): string {
  if (item.kind === "cli") {
    const app = item.app;
    return [
      app.display_name,
      app.name,
      app.category,
      app.description,
      app.requires,
      app.entry_point,
      app.source,
    ]
      .join(" ")
      .toLowerCase();
  }
  const preset = item.preset;
  return [
    preset.display_name,
    preset.name,
    preset.category,
    preset.description,
    preset.requires,
    preset.note,
    preset.transport,
    preset.source ?? "",
  ]
    .join(" ")
      .toLowerCase();
}

function McpCustomServerPanel({
  form,
  configImport,
  actionKey,
  onFormChange,
  onConfigImportChange,
  onSave,
  onImportConfig,
}: {
  form: CustomMcpForm;
  configImport: string;
  actionKey: string | null;
  onFormChange: Dispatch<SetStateAction<CustomMcpForm>>;
  onConfigImportChange: (value: string) => void;
  onSave: () => void;
  onImportConfig: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [activeMode, setActiveMode] = useState<"custom" | "import" | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const customBusy = actionKey?.startsWith("custom:") ?? false;
  const importBusy = actionKey === "import" || actionKey === "import-cursor";
  const remote = form.transport !== "stdio";
  const canSave = Boolean(form.name.trim()) && (remote ? Boolean(form.url.trim()) : Boolean(form.command.trim()));
  const update = <K extends keyof CustomMcpForm>(key: K, value: CustomMcpForm[K]) => {
    onFormChange((prev) => ({ ...prev, [key]: value }));
  };
  const transports: Array<{ value: CustomMcpTransport; label: string }> = [
    { value: "stdio", label: "stdio" },
    { value: "streamableHttp", label: "HTTP" },
    { value: "sse", label: "SSE" },
  ];

  return (
    <section className="overflow-hidden rounded-[16px] border border-border/45 bg-card/72 shadow-[0_10px_30px_rgba(15,23,42,0.045)]">
      <div className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[11px] bg-muted text-muted-foreground">
            <Server className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <h3 className="text-[13px] font-semibold leading-5 text-foreground">
              {tx("settings.mcp.moreOptions", "Add integration")}
            </h3>
            <p className="truncate text-[12px] text-muted-foreground">
              {tx(
                "settings.mcp.moreOptionsSubtitle",
                "Connect a custom tool server or import an existing configuration.",
              )}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:shrink-0">
          <Button
            type="button"
            size="sm"
            variant={activeMode === "custom" ? "default" : "outline"}
            onClick={() => setActiveMode((mode) => (mode === "custom" ? null : "custom"))}
            className="h-8 rounded-full px-3 text-[12px] font-semibold"
          >
            <Server className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("settings.mcp.customAction", "Custom")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={activeMode === "import" ? "default" : "outline"}
            onClick={() => setActiveMode((mode) => (mode === "import" ? null : "import"))}
            className="h-8 rounded-full px-3 text-[12px] font-semibold"
          >
            <Database className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("settings.mcp.importAction", "Import")}
          </Button>
        </div>
      </div>

      {activeMode === "custom" ? (
        <div className="border-t border-border/35 bg-muted/18 px-3 py-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
            <label className="min-w-0 flex-1">
              <span className="mb-1.5 block text-[11.5px] font-medium text-muted-foreground">
                {tx("settings.mcp.serverName", "Server name")}
              </span>
              <Input
                value={form.name}
                onChange={(event) => update("name", event.target.value)}
                placeholder="docs"
                className="h-9 rounded-full bg-background/80 text-[12.5px]"
              />
            </label>
            <div className="min-w-[228px]">
              <span className="mb-1.5 block text-[11.5px] font-medium text-muted-foreground">
                {tx("settings.mcp.transport", "Transport")}
              </span>
              <SegmentedControl
                value={form.transport}
                options={transports}
                onChange={(value) => update("transport", value as CustomMcpTransport)}
              />
            </div>
            {remote ? (
              <label className="min-w-0 flex-[1.4]">
                <span className="mb-1.5 block text-[11.5px] font-medium text-muted-foreground">
                  {tx("settings.mcp.serverUrl", "URL")}
                </span>
                <Input
                  value={form.url}
                  onChange={(event) => update("url", event.target.value)}
                  placeholder={form.transport === "sse" ? "https://example.com/sse" : "https://example.com/mcp"}
                  className="h-9 rounded-full bg-background/80 text-[12.5px]"
                />
              </label>
            ) : (
              <label className="min-w-0 flex-[1.4]">
                <span className="mb-1.5 block text-[11.5px] font-medium text-muted-foreground">
                  {tx("settings.mcp.command", "Command")}
                </span>
                <Input
                  value={form.command}
                  onChange={(event) => update("command", event.target.value)}
                  placeholder="npx"
                  className="h-9 rounded-full bg-background/80 text-[12.5px]"
                />
              </label>
            )}
            <Button
              type="button"
              size="sm"
              onClick={onSave}
              disabled={!canSave || customBusy}
              className="h-9 shrink-0 rounded-full px-4 text-[12.5px] font-semibold"
            >
              {customBusy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />}
              {tx("settings.mcp.saveCustom", "Save MCP")}
            </Button>
          </div>

          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setAdvancedOpen((open) => !open)}
            className="mt-2 h-8 rounded-full px-2 text-[12px] font-medium text-muted-foreground hover:text-foreground"
          >
            <ChevronDown
              className={cn("mr-1.5 h-3.5 w-3.5 transition-transform", advancedOpen ? "rotate-180" : "")}
              aria-hidden
            />
            {advancedOpen
              ? tx("settings.mcp.hideAdvanced", "Hide advanced")
              : tx("settings.mcp.advancedOptions", "Advanced options")}
          </Button>

          {advancedOpen ? (
            <div className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_180px]">
              {!remote ? (
                <label className="min-w-0">
                  <span className="mb-1 block text-[11.5px] font-medium text-muted-foreground">
                    {tx("settings.mcp.args", "Args JSON")}
                  </span>
                  <Textarea
                    value={form.args}
                    onChange={(event) => update("args", event.target.value)}
                    placeholder={'["-y", "docs-mcp"]'}
                    className="min-h-[68px] resize-y rounded-[12px] bg-background/80 font-mono text-[12px]"
                  />
                </label>
              ) : (
                <label className="min-w-0">
                  <span className="mb-1 block text-[11.5px] font-medium text-muted-foreground">
                    {tx("settings.mcp.headers", "Headers JSON")}
                  </span>
                  <Textarea
                    value={form.headers}
                    onChange={(event) => update("headers", event.target.value)}
                    placeholder={'{"Authorization":"Bearer ..."}'}
                    className="min-h-[68px] resize-y rounded-[12px] bg-background/80 font-mono text-[12px]"
                  />
                </label>
              )}
              <label className="min-w-0">
                <span className="mb-1 block text-[11.5px] font-medium text-muted-foreground">
                  {tx("settings.mcp.env", "Env JSON")}
                </span>
                <Textarea
                  value={form.env}
                  onChange={(event) => update("env", event.target.value)}
                  placeholder={'{"API_KEY":"..."}'}
                  className="min-h-[68px] resize-y rounded-[12px] bg-background/80 font-mono text-[12px]"
                />
              </label>
              <label className="min-w-0">
                <span className="mb-1 block text-[11.5px] font-medium text-muted-foreground">
                  {tx("settings.mcp.timeout", "Tool timeout")}
                </span>
                <Input
                  value={form.toolTimeout}
                  onChange={(event) => update("toolTimeout", event.target.value)}
                  inputMode="numeric"
                  className="h-9 rounded-full bg-background/80 text-[12.5px]"
                />
              </label>
            </div>
          ) : null}
        </div>
      ) : null}

      {activeMode === "import" ? (
        <div className="border-t border-border/35 bg-muted/18 px-3 py-3">
          <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
            <label className="min-w-0 flex-1">
              <span className="mb-1.5 block text-[11.5px] font-medium text-muted-foreground">
                {tx("settings.mcp.configImport", "Import mcp.json")}
              </span>
              <Textarea
                value={configImport}
                onChange={(event) => onConfigImportChange(event.target.value)}
                placeholder={'{"mcpServers":{"docs":{"command":"npx","args":["-y","docs-mcp"]}}}'}
                className="min-h-[84px] resize-y rounded-[12px] bg-background/80 font-mono text-[12px]"
              />
            </label>
            <Button
              type="button"
              size="sm"
              onClick={onImportConfig}
              disabled={!configImport.trim() || importBusy}
              className="h-9 shrink-0 rounded-full px-4 text-[12.5px] font-semibold"
            >
              {importBusy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Database className="mr-1.5 h-3.5 w-3.5" aria-hidden />}
              {tx("settings.mcp.importConfig", "Import")}
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function mcpPresetStatusLabel(status: string, tx: (key: string, fallback: string) => string): string {
  switch (status) {
    case "configured":
      return tx("settings.mcp.statusConfigured", "Configured");
    case "missing_credentials":
      return tx("settings.mcp.statusMissingCredentials", "Needs key");
    case "missing_dependency":
      return tx("settings.mcp.statusMissingDependency", "Needs dependency");
    case "coming_soon":
      return tx("settings.mcp.statusComingSoon", "Planned");
    default:
      return tx("settings.mcp.statusNotInstalled", "Not enabled");
  }
}

function McpPresetLogo({ preset, showBrandLogos }: { preset: McpPresetInfo; showBrandLogos: boolean }) {
  const bg = preset.brand_color || "hsl(var(--muted))";
  const logoUrls = useMemo(() => logoFallbackUrls(preset.logo_url), [preset.logo_url]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  const initials = preset.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || preset.name.slice(0, 2).toUpperCase();

  if (showBrandLogos && logoUrl) {
    return (
      <span
        className="grid h-11 w-11 shrink-0 place-items-center rounded-[8px] border border-border/45 bg-background"
        style={{ boxShadow: `inset 0 0 0 1px ${preset.brand_color ?? "transparent"}22` }}
      >
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-6 w-6 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }
  return (
    <span
      className="grid h-11 w-11 shrink-0 place-items-center rounded-[8px] text-[13px] font-semibold text-white"
      style={{ backgroundColor: bg }}
    >
      {initials}
    </span>
  );
}

function CliAppReadyPanel({
  app,
  showBrandLogos,
  onBackToChat,
}: {
  app: CliAppInfo;
  showBrandLogos: boolean;
  onBackToChat: () => void;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const prompt = t("settings.cliApps.readyPrompt", {
    name: app.name,
    defaultValue: "Use @{{name}} to inspect what this CLI can do.",
  });
  const copyPrompt = () => {
    void copyTextOrNotify(prompt).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    });
  };

  return (
    <section
      className={cn(
        "rounded-[12px] border border-border/55 bg-card/88 px-4 py-3",
        "shadow-[0_8px_26px_rgba(15,23,42,0.055)]",
      )}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <CliAppLogo app={app} showBrandLogos={showBrandLogos} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="truncate text-[14px] font-semibold leading-5 text-foreground">
              {app.display_name}
            </h3>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10.5px] font-medium text-muted-foreground">
              <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-300" aria-hidden />
              {t("settings.cliApps.readyStatus", { defaultValue: "Ready" })}
            </span>
          </div>
          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-1.5 text-[12px] text-muted-foreground">
            <span className="font-mono">@{app.name}</span>
            <span aria-hidden>·</span>
            <span className="truncate font-mono">{app.entry_point || app.name}</span>
            <span aria-hidden>·</span>
            <span>{app.category}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={copyPrompt}
            className="h-8 rounded-full px-3 text-[12px] font-medium text-muted-foreground hover:bg-muted/65 hover:text-foreground"
          >
            {copied ? <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden /> : null}
            {copied
              ? t("settings.cliApps.readyCopied", { defaultValue: "Copied" })
              : t("settings.cliApps.readyTry", { name: app.name, defaultValue: "Try @{{name}}" })}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onBackToChat}
            className="h-8 rounded-full px-3 text-[12px] font-semibold"
          >
            {t("settings.cliApps.openChat", { defaultValue: "Open chat" })}
            <ChevronRight className="ml-1.5 h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>
      </div>
    </section>
  );
}

function CliAppLogo({ app, showBrandLogos }: { app: CliAppInfo; showBrandLogos: boolean }) {
  const bg = app.brand_color || "hsl(var(--muted))";
  const logoUrls = useMemo(() => logoFallbackUrls(app.logo_url), [app.logo_url]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  const initials = app.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || app.name.slice(0, 2).toUpperCase();

  if (showBrandLogos && logoUrl) {
    return (
      <span
        className="grid h-11 w-11 shrink-0 place-items-center rounded-[8px] border border-border/45 bg-background"
        style={{ boxShadow: `inset 0 0 0 1px ${app.brand_color ?? "transparent"}22` }}
      >
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-6 w-6 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }
  return (
    <span
      className="grid h-11 w-11 shrink-0 place-items-center rounded-[8px] text-[13px] font-semibold text-white"
      style={{ backgroundColor: bg }}
    >
      {initials}
    </span>
  );
}

function RuntimeSettings({
  form,
  setForm,
  settings,
  dirty,
  saving,
  onSave,
  onRestart,
  isRestarting,
  requiresRestartPending,
}: {
  form: AgentSettingsDraft;
  setForm: Dispatch<SetStateAction<AgentSettingsDraft>>;
  settings: SettingsPayload;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const runtimeSurface = settings.surface ?? settings.runtime_surface;
  const runtimeHost = getRuntimeHost(runtimeSurface, settings.runtime_capabilities);
  const openLogs = runtimeHost.openLogs;
  const exportDiagnostics = runtimeHost.exportDiagnostics;
  const isNativeHost = isNativeRuntime(runtimeSurface);
  const [diagnosticsPath, setDiagnosticsPath] = useState<string | null>(null);
  const [hostActionMessage, setHostActionMessage] = useState<{
    target: "logs" | "diagnostics";
    message: string;
  } | null>(null);
  const [hostActionBusy, setHostActionBusy] =
    useState<"logs" | "diagnostics" | null>(null);
  const engineState = isRestarting
    ? tx("settings.values.restartingEngine", "Restarting")
    : settings.apply_state?.status === "pending"
      ? tx("settings.values.pending", "Pending")
      : tx("settings.values.ready", "Ready");
  const runHostAction = async (
    target: "logs" | "diagnostics",
    action: (() => Promise<string | void>) | undefined,
    successMessage: (result: string | void) => string,
    failureMessage: string,
  ) => {
    if (!action) {
      setHostActionMessage({
        target,
        message: tx(
          "settings.status.hostApiUnavailable",
          "Host actions are only available inside the native app.",
        ),
      });
      return;
    }
    setHostActionBusy(target);
    setHostActionMessage(null);
    try {
      const result = await action();
      setHostActionMessage({ target, message: successMessage(result) });
    } catch {
      setHostActionMessage({ target, message: failureMessage });
    } finally {
      setHostActionBusy(null);
    }
  };
  const restartActionLabel = isNativeHost
    ? tx("app.system.restartEngine", "Restart engine")
    : t("app.system.restart");
  const restartingActionLabel = isNativeHost
    ? tx("app.system.restartingEngine", "Restarting engine...")
    : t("app.system.restarting");
  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{tx("settings.sections.identity", "Identity")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.language")}
            description={t("settings.help.language")}
          >
            <LanguageSwitcher />
          </SettingsRow>
          <SettingsRow title={tx("settings.rows.timezone", "Timezone")} description={tx("settings.help.timezone", "Used for schedules and time-aware replies.")}>
            <TimezonePicker
              value={form.timezone}
              onChange={(timezone) => setForm((prev) => ({ ...prev, timezone }))}
            />
          </SettingsRow>
          <RestartSettingsFooter
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            dirtyMessage={
              isNativeHost
                ? tx("settings.status.hostRestartAfterSaving", "Save changes and navin will restart its engine.")
                : tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")
            }
            pendingMessage={
              isNativeHost
                ? tx("settings.status.hostRestartPending", "Saved. Restarting engine when ready.")
                : tx("settings.status.savedRestartApply", "Saved. Restart when ready.")
            }
            onSave={onSave}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>

      {isNativeHost ? (
        <section>
          <SettingsSectionTitle>{tx("settings.sections.nativeHost", "Native host")}</SettingsSectionTitle>
          <SettingsGroup>
            <ReadOnlyRow title={tx("settings.rows.engine", "Engine")} value={engineState} />
            {settings.runtime_capabilities?.can_open_logs ? (
              <SettingsRow
                title={tx("settings.rows.logs", "Logs")}
                description={
                  hostActionMessage?.target === "logs"
                    ? hostActionMessage.message
                    : tx("settings.help.logs", "Open the native engine log folder.")
                }
              >
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    void runHostAction(
                      "logs",
                      openLogs,
                      () => tx("settings.status.logsOpened", "Opened logs folder."),
                      tx("settings.status.logsOpenFailed", "Could not open logs folder."),
                    )
                  }
                  disabled={hostActionBusy !== null}
                  className="rounded-full"
                >
                  {hostActionBusy === "logs"
                    ? tx("settings.actions.opening", "Opening...")
                    : tx("settings.actions.open", "Open")}
                </Button>
              </SettingsRow>
            ) : null}
            {settings.runtime_capabilities?.can_export_diagnostics ? (
              <SettingsRow
                title={tx("settings.rows.diagnostics", "Diagnostics")}
                description={
                  hostActionMessage?.target === "diagnostics"
                    ? hostActionMessage.message
                    : diagnosticsPath
                    ? diagnosticsPath
                    : tx("settings.help.diagnostics", "Export a small runtime report for support.")
                }
              >
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    void runHostAction(
                      "diagnostics",
                      exportDiagnostics ? async () => {
                        const path = await exportDiagnostics();
                        setDiagnosticsPath(path);
                        return path;
                      } : undefined,
                      (path) =>
                        t("settings.status.diagnosticsExported", {
                          path: String(path ?? ""),
                          defaultValue: "Diagnostics exported to {{path}}.",
                        }),
                      tx("settings.status.diagnosticsExportFailed", "Could not export diagnostics."),
                    )
                  }
                  disabled={hostActionBusy !== null}
                  className="rounded-full"
                >
                  {hostActionBusy === "diagnostics"
                    ? tx("settings.actions.exporting", "Exporting...")
                    : tx("settings.actions.export", "Export")}
                </Button>
              </SettingsRow>
            ) : null}
          </SettingsGroup>
        </section>
      ) : null}

      <section>
        <SettingsSectionTitle>{t("settings.sections.system")}</SettingsSectionTitle>
        <SettingsGroup>
          <ReadOnlyRow
            title={tx("settings.rows.workspacePath", "Default workspace")}
            value={settings.runtime.workspace_path}
          />
          {onRestart && !requiresRestartPending ? (
            <SettingsRow
              title={t("settings.rows.restart")}
              description={t("app.system.restartHint")}
            >
              <Button
                size="sm"
                variant="outline"
                onClick={onRestart}
                disabled={isRestarting}
                className="rounded-full"
              >
                {isRestarting ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                )}
                {isRestarting ? restartingActionLabel : restartActionLabel}
              </Button>
            </SettingsRow>
          ) : null}
        </SettingsGroup>
      </section>
    </div>
  );
}

/**
 * Machine-wide kill-switches for board task git automation. Per-project
 * consent lives in the Tasks panel (Autonomy toggle); these override it.
 */
function BoardGitSettings({ settings }: { settings: SettingsPayload }) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { token } = useClient();
  const [autoBranch, setAutoBranch] = useState(
    settings.board_git?.auto_branch_enabled ?? true,
  );
  const [openPr, setOpenPr] = useState(settings.board_git?.open_pr_enabled ?? true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setAutoBranch(settings.board_git?.auto_branch_enabled ?? true);
    setOpenPr(settings.board_git?.open_pr_enabled ?? true);
  }, [settings.board_git]);

  const persist = async (update: { boardAutoBranch?: boolean; boardOpenPr?: boolean }) => {
    if (!token) return;
    setSaving(true);
    try {
      const payload = await updateSettings(token, update);
      setAutoBranch(payload.board_git?.auto_branch_enabled ?? true);
      setOpenPr(payload.board_git?.open_pr_enabled ?? true);
    } catch {
      // Revert to the last known server state on failure.
      setAutoBranch(settings.board_git?.auto_branch_enabled ?? true);
      setOpenPr(settings.board_git?.open_pr_enabled ?? true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section>
      <SettingsSectionTitle>{tx("settings.sections.boardGit", "Git")}</SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow
          title={tx("settings.rows.boardAutoBranch", "Task auto-branch")}
          description={tx(
            "settings.help.boardAutoBranch",
            "Let projects with Autonomy enabled create an isolated navin/task-<id> branch per claimed task. Turning this off disables it for every project on this machine.",
          )}
        >
          <ToggleButton
            checked={autoBranch}
            disabled={saving}
            onChange={(next) => {
              setAutoBranch(next);
              void persist({ boardAutoBranch: next });
            }}
            label={tx("settings.rows.boardAutoBranch", "Task auto-branch")}
          />
        </SettingsRow>
        <SettingsRow
          title={tx("settings.rows.boardOpenPr", "Pull request on task done")}
          description={tx(
            "settings.help.boardOpenPr",
            "Let projects with Autonomy enabled push the task branch and open a pull request when a task reaches done. Turning this off disables it for every project on this machine.",
          )}
        >
          <ToggleButton
            checked={openPr}
            disabled={saving}
            onChange={(next) => {
              setOpenPr(next);
              void persist({ boardOpenPr: next });
            }}
            label={tx("settings.rows.boardOpenPr", "Pull request on task done")}
          />
        </SettingsRow>
      </SettingsGroup>
      <ForgeTokensSettings settings={settings} />
    </section>
  );
}

/**
 * Per-host forge tokens.
 *
 * Opening a pull request used to require the `gh` CLI, which meant nothing
 * worked on GitLab or Forgejo and nothing worked on a machine without it.
 * One token per host is all the REST road needs, so this is where a
 * self-hosted Forgejo or a company GitLab becomes usable from the Code panel.
 */
function ForgeTokensSettings({ settings }: { settings: SettingsPayload }) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { token } = useClient();
  const [rows, setRows] = useState(settings.forge?.hosts ?? []);
  const [envTokens, setEnvTokens] = useState(settings.forge?.env_tokens ?? []);
  const [host, setHost] = useState("");
  const [secret, setSecret] = useState("");
  const [kind, setKind] = useState<ForgeKind | "auto">("auto");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(settings.forge?.hosts ?? []);
    setEnvTokens(settings.forge?.env_tokens ?? []);
  }, [settings.forge]);

  const persist = async (update: SettingsUpdate, key: string) => {
    if (!token) return;
    setBusy(key);
    setError(null);
    try {
      const payload = await updateSettings(token, update);
      setRows(payload.forge?.hosts ?? []);
      setEnvTokens(payload.forge?.env_tokens ?? []);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const addHost = async () => {
    const cleaned = host.trim();
    if (!cleaned) return;
    const ok = await persist(
      { forgeHost: cleaned, forgeToken: secret.trim(), forgeKind: kind },
      "add",
    );
    if (ok) {
      setHost("");
      setSecret("");
      setKind("auto");
    }
  };

  const kindOptions: Array<ForgeKind | "auto"> = [
    "auto",
    ...(settings.forge?.kinds ?? ["github", "gitlab", "forgejo"]),
  ];

  return (
    <div className="mt-4">
      <SettingsSectionTitle>
        {tx("settings.sections.forgeTokens", "Forge tokens")}
      </SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow
          title={tx("settings.rows.forgeToken", "Add a forge")}
          description={tx(
            "settings.help.forgeToken",
            "One access token per host lets Create PR work on GitHub, GitLab and Forgejo/Gitea without installing the gh CLI. The token stays on this machine and is never shown again.",
          )}
        />
        <div className="flex flex-col gap-2 px-4 py-3.5 sm:px-5">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={host}
              onChange={(event) => setHost(event.target.value)}
              placeholder={tx(
                "settings.rows.forgeHostPlaceholder",
                "forgejo.example.com",
              )}
              className="h-9 w-[220px] rounded-full text-[13px]"
              aria-label={tx("settings.rows.forgeHost", "Host")}
            />
            <Input
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              type="password"
              autoComplete="off"
              placeholder={tx("settings.rows.forgeTokenPlaceholder", "Access token")}
              className="h-9 w-[220px] rounded-full text-[13px]"
              aria-label={tx("settings.rows.forgeTokenLabel", "Token")}
            />
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value as ForgeKind | "auto")}
              className="h-9 rounded-full border border-input bg-background px-3 text-[13px]"
              aria-label={tx("settings.rows.forgeKind", "Forge type")}
            >
              {kindOptions.map((option) => (
                <option key={option} value={option}>
                  {option === "auto"
                    ? tx("settings.rows.forgeKindAuto", "Detect automatically")
                    : option}
                </option>
              ))}
            </select>
            <Button
              type="button"
              onClick={() => void addHost()}
              disabled={busy !== null || !host.trim()}
              className="h-9 rounded-full text-[13px]"
            >
              {busy === "add" ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
              )}
              {tx("settings.actions.forgeAdd", "Save token")}
            </Button>
          </div>
          {error ? (
            <p className="text-[12px] text-destructive">{error}</p>
          ) : null}
        </div>

        {rows.map((row) => (
          <SettingsRow
            key={row.host}
            title={row.host}
            description={
              row.token_configured
                ? `${row.kind}${row.kind_pinned ? "" : " (auto)"} · ${
                    row.token_hint ?? "••••"
                  }`
                : `${row.kind}${row.kind_pinned ? "" : " (auto)"} · ${tx(
                    "settings.rows.forgeNoToken",
                    "no token stored",
                  )}`
            }
          >
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={busy !== null}
              onClick={() =>
                void persist({ forgeHost: row.host, forgeRemove: true }, row.host)
              }
              aria-label={tx("settings.actions.forgeRemove", "Remove forge token")}
              className="h-8 w-8 rounded-full text-muted-foreground hover:bg-muted hover:text-destructive"
            >
              {busy === row.host ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              )}
            </Button>
          </SettingsRow>
        ))}

        {envTokens.length > 0 ? (
          <SettingsRow
            title={tx("settings.rows.forgeEnvTokens", "From the environment")}
            description={tx(
              "settings.help.forgeEnvTokens",
              "These variables are already set on this machine and are used when no token is stored above.",
            )}
          >
            <span className="font-mono text-[12px] text-muted-foreground">
              {envTokens.map((entry) => entry.name).join(", ")}
            </span>
          </SettingsRow>
        ) : null}
      </SettingsGroup>
    </div>
  );
}

function AdvancedSettings({
  form,
  settings,
  dirty,
  saving,
  requiresRestartPending,
  isNativeHostSurface,
  onChangeForm,
  onSave,
  onRestart,
  isRestarting,
}: {
  form: NetworkSafetySettingsUpdate;
  settings: SettingsPayload;
  dirty: boolean;
  saving: boolean;
  requiresRestartPending: boolean;
  isNativeHostSurface: boolean;
  onChangeForm: Dispatch<SetStateAction<NetworkSafetySettingsUpdate>>;
  onSave: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>
          {isNativeHostSurface
            ? tx("settings.sections.hostSafety", "App safety")
            : tx("settings.sections.webuiSafety", "Web safety")}
        </SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.localServiceAccess", "Local Service Access")}
            description={tx(
              isNativeHostSurface ? "settings.help.localServiceAccessNative" : "settings.help.localServiceAccess",
              isNativeHostSurface
                ? "Allow Full Access shell commands to reach services on this Mac."
                : "Allow Full Access shell commands to reach localhost services.",
            )}
          >
            <ToggleButton
              checked={form.webuiAllowLocalServiceAccess}
              onChange={(webuiAllowLocalServiceAccess) =>
                onChangeForm((prev) => ({ ...prev, webuiAllowLocalServiceAccess }))
              }
              ariaLabel={tx("settings.rows.localServiceAccess", "Local Service Access")}
              label={form.webuiAllowLocalServiceAccess ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <SettingsRow
            title={tx("settings.rows.webuiDefaultAccess", "Default access")}
            description={tx(
              isNativeHostSurface ? "settings.help.webuiDefaultAccessNative" : "settings.help.webuiDefaultAccess",
              isNativeHostSurface
                ? "This is a different setting from Restrict to workspace.\nDefault Permission: the chat stays in the chosen folder, with confirmation for risky actions.\nFull Access: the agent can go anywhere on this Mac. Click Save. This mostly applies to new chats. A chat already open keeps its previous mode."
                : "This is a different setting from Restrict to workspace.\nDefault Permission: the chat stays in the chosen folder, with confirmation for risky actions.\nFull Access: the agent can go anywhere on this machine. Click Save. This mostly applies to new chats. A chat already open keeps its previous mode.",
            )}
          >
            <SegmentedControl
              value={form.webuiDefaultAccessMode}
              options={[
                { value: "default", label: tx("settings.values.defaultPermission", "Default Permission") },
                { value: "full", label: tx("settings.values.fullAccess", "Full Access") },
              ]}
              onChange={(webuiDefaultAccessMode) =>
                onChangeForm((prev) => ({
                  ...prev,
                  webuiDefaultAccessMode: webuiDefaultAccessMode as WebuiDefaultAccessMode,
                }))
              }
            />
          </SettingsRow>
          <RestartSettingsFooter
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            onSave={onSave}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>
          {tx("settings.sections.agentPermissions", "Agent permissions")}
        </SettingsSectionTitle>
        <ExecPolicySettings />
      </section>

      <BoardGitSettings settings={settings} />

      <p className="max-w-3xl px-1 text-sm leading-6 text-muted-foreground">
        {tx(
          "settings.help.securityManagedControls",
          "Web fetches always protect local, private, and metadata services. Core channel safety stays in config.json.",
        )}
      </p>
    </div>
  );
}

function TimezonePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (timezone: string) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [query, setQuery] = useState("");
  const options = useMemo(() => timezoneOptions(value), [value]);
  const filteredOptions = useMemo(() => filterTimezoneOptions(options, query), [options, query]);

  return (
    <DropdownMenu onOpenChange={(open) => !open && setQuery("")}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className={cn(
            "h-8 w-[220px] justify-between rounded-full border-input bg-background px-3 text-[13px] font-normal shadow-none",
            "hover:bg-accent/55 focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <span className="truncate">{value || tx("settings.timezone.select", "Select timezone")}</span>
          <ChevronDown className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-[340px] max-w-[calc(100vw-2rem)]"
      >
        <div className="sticky top-0 z-10 bg-popover px-1 pb-1">
          <div className="flex h-9 items-center gap-2 rounded-full border border-input bg-background px-3">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <Input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
              placeholder={tx("settings.timezone.search", "Search timezone")}
              className="h-7 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
            />
          </div>
        </div>
        <div
          className="mt-1 max-h-[18rem] overflow-y-auto pr-0.5 scrollbar-thin scrollbar-track-transparent"
          data-testid="timezone-picker-list"
        >
          {filteredOptions.length ? (
            filteredOptions.map((option) => {
              const selected = option.name === value;
              return (
                <DropdownMenuItem
                  key={option.name}
                  onSelect={() => onChange(option.name)}
                  className={cn(
                    "flex h-9 cursor-default items-center justify-between gap-3 rounded-[12px] px-2.5 text-[13px]",
                    "focus:bg-muted/85 focus:text-foreground",
                    selected && "bg-muted/80 text-foreground focus:bg-muted",
                  )}
                >
                  <span className="min-w-0 truncate font-medium text-foreground">{option.name}</span>
                  <span className="ml-auto flex shrink-0 items-center gap-2">
                    <span className="text-[11.5px] font-medium text-muted-foreground/80">
                      {option.offset}
                    </span>
                    {selected ? <Check className="h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
                  </span>
                </DropdownMenuItem>
              );
            })
          ) : (
            <div className="px-3 py-5 text-center text-[12px] text-muted-foreground">
              {tx("settings.timezone.empty", "No matching timezones.")}
            </div>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ProviderChipSelect({
  providers,
  value,
  emptyLabel,
  showProviderLogos = false,
  onChange,
}: {
  providers: Array<{ name: string; label: string }>;
  value: string;
  emptyLabel: string;
  showProviderLogos?: boolean;
  onChange: (provider: string) => void;
}) {
  if (providers.length === 0) {
    return (
      <p className="text-[12.5px] text-muted-foreground">{emptyLabel}</p>
    );
  }

  return (
    <div
      role="radiogroup"
      aria-label={emptyLabel}
      className="flex flex-wrap gap-1.5"
    >
      {providers.map((provider) => {
        const selected = provider.name === value;
        return (
          <button
            key={provider.name}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => {
              if (provider.name !== value) onChange(provider.name);
            }}
            className={cn(
              "inline-flex h-8 max-w-full items-center gap-1.5 rounded-full border px-2.5 text-[12.5px] font-medium transition-colors",
              selected
                ? "border-foreground bg-foreground text-background"
                : "border-border bg-background text-foreground hover:bg-muted/60",
            )}
          >
            {showProviderLogos ? (
              <ProviderPickerIcon
                provider={provider.name}
                showBrandLogos={showProviderLogos}
              />
            ) : null}
            <span className="truncate">{provider.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function ProviderPicker({
  providers,
  value,
  emptyLabel,
  showProviderLogos = false,
  disabled = false,
  onChange,
}: {
  providers: Array<{ name: string; label: string }>;
  value: string;
  emptyLabel: string;
  showProviderLogos?: boolean;
  disabled?: boolean;
  onChange: (provider: string) => void;
}) {
  const options = providers.map((provider) => ({ key: provider.name, text: provider.label }));
  const renderProvider = (option?: { key: string | number; text: string }) => option ? (
    <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      {showProviderLogos ? <ProviderPickerIcon provider={String(option.key)} showBrandLogos /> : null}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{option.text}</span>
    </span>
  ) : null;
  return (
    <MediaSettingsSurface>
      <FluentDropdown ariaLabel={emptyLabel} placeholder={emptyLabel}
        selectedKey={providers.some((provider) => provider.name === value) ? value : null}
        options={options} disabled={disabled || !providers.length}
        styles={{ root: { width: 210, maxWidth: "100%" } }}
        calloutProps={{ calloutMaxHeight: 288 }}
        onRenderOption={renderProvider} onRenderTitle={(items) => renderProvider(items?.[0])}
        onChange={(_, option) => { if (option && option.key !== value) onChange(String(option.key)); }} />
    </MediaSettingsSurface>
  );
}

function ModelIdPicker({
  token,
  settings,
  provider,
  value,
  showProviderLogos,
  onChange,
}: {
  token: string;
  settings: SettingsPayload;
  provider: string;
  value: string;
  showProviderLogos: boolean;
  onChange: (model: string) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [payload, setPayload] = useState<ProviderModelsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const effectiveProvider =
    provider === "auto" ? settings.agent.resolved_provider ?? provider : provider;
  const hasConcreteProvider = Boolean(effectiveProvider && effectiveProvider !== "auto");
  const providerRow = settingsProviderRow(settings, effectiveProvider);
  const providerConfigured = settingsProviderConfigured(settings, effectiveProvider);
  const providerRequiresConfiguration = hasConcreteProvider && !providerConfigured;
  const providerHasBuiltinModels = providerRow?.model_catalog === "builtin";
  const providerUsesManualModelIds =
    hasConcreteProvider &&
    providerConfigured &&
    providerRow?.auth_type === "oauth" &&
    !providerHasBuiltinModels;
  const canFetchModels =
    hasConcreteProvider && providerConfigured && !providerUsesManualModelIds;
  const normalizedQuery = query.trim().toLowerCase();
  const providerModels = payload?.models ?? [];
  const visibleModels = providerModels
    .filter((model) => {
      if (!normalizedQuery) return true;
      return [model.id, model.label ?? "", model.description ?? "", model.owned_by ?? ""]
        .some((field) => field.toLowerCase().includes(normalizedQuery));
    })
    .slice(0, 80);
  const isCatalog = payload?.catalog_kind === "catalog";
  const defersModelList = DEFERRED_MODEL_LIST_PROVIDERS.has(effectiveProvider);
  const hasDeferredSearchQuery =
    normalizedQuery.length >= DEFERRED_MODEL_LIST_QUERY_MIN_LENGTH;
  const shouldFetchModels =
    canFetchModels && (!defersModelList || hasDeferredSearchQuery);
  const waitingForModelSearch =
    open && canFetchModels && defersModelList && !hasDeferredSearchQuery;
  const hasModelList = payload?.status === "available";
  const showModels = Boolean(hasModelList && payload && (!isCatalog || normalizedQuery));
  const customCandidate = query.trim();
  const allowCustomModel = !providerRequiresConfiguration;
  const exactQueryMatch = providerModels.some((model) => model.id === customCandidate);
  const providerModelCount = payload?.model_count ?? providerModels.length;
  const modelUnconfigured = !value.trim() || !providerConfigured;

  useEffect(() => {
    if (!open) return;
    setQuery(providerUsesManualModelIds || !hasConcreteProvider ? value : "");
  }, [open, effectiveProvider, hasConcreteProvider, providerUsesManualModelIds, value]);

  useEffect(() => {
    if (!open || !shouldFetchModels) {
      setPayload(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setPayload(null);
    setError(null);
    setLoading(true);
    fetchProviderModels(token, effectiveProvider)
      .then((nextPayload) => {
        if (!cancelled) setPayload(nextPayload);
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveProvider, open, shouldFetchModels, token]);

  const selectModel = (model: string) => {
    onChange(model);
    setOpen(false);
  };

  const renderModelRow = (
    model: ProviderModelsPayload["models"][number],
    options: { selected?: boolean } = {},
  ) => (
    <DropdownMenuItem
      key={model.id}
      onSelect={() => selectModel(model.id)}
      className={cn(
        "flex cursor-default items-center justify-between gap-2 rounded-[12px] px-2 py-1.5 text-[12px]",
        "focus:bg-muted/85 focus:text-foreground",
        options.selected && "bg-muted/80 text-foreground focus:bg-muted",
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <ProviderPickerIcon
          provider={effectiveProvider}
          showBrandLogos={showProviderLogos}
          unconfigured={!providerConfigured}
        />
        <span className="min-w-0">
          <span className="block truncate font-medium text-foreground">
            {model.label ?? model.id}
          </span>
          {model.description || (model.label && model.label !== model.id) ? (
            <span className="mt-0.5 block truncate text-[10.5px] text-muted-foreground">
              {[model.label && model.label !== model.id ? model.id : null, model.description]
                .filter(Boolean)
                .join(" · ")}
            </span>
          ) : null}
        </span>
      </span>
      <span className="ml-2 flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
        {model.context_window ? <span>{formatContextWindow(model.context_window)}</span> : null}
        {options.selected ? <Check className="h-3.5 w-3.5 text-foreground" aria-hidden /> : null}
      </span>
    </DropdownMenuItem>
  );

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className={cn(
            "h-9 w-[min(360px,70vw)] justify-between rounded-full border-input bg-background px-3 text-[12px] font-normal shadow-none",
            "hover:bg-accent/55 focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <ProviderPickerIcon
              provider={effectiveProvider}
              showBrandLogos={showProviderLogos}
              unconfigured={modelUnconfigured}
            />
            <span
              className={cn(
                "min-w-0 truncate font-medium",
                value ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {value || tx("settings.models.selectModel", "Select model")}
            </span>
          </span>
          <ChevronDown className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-[360px] max-w-[calc(100vw-2rem)] p-1.5"
      >
        <div className="p-1 pb-1.5">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
              placeholder={tx("settings.models.searchModels", "Search or type model ID")}
              className="h-8 rounded-full pl-8 pr-3 text-[12px]"
            />
          </div>
        </div>

        {providerRequiresConfiguration ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {tx("settings.models.providerNotConfigured", "Configure this provider before loading models.")}
          </div>
        ) : providerUsesManualModelIds ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {tx("settings.models.unsupportedModelList", "Type a model ID manually.")}
          </div>
        ) : !canFetchModels ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {tx("settings.models.autoProviderCustomOnly", "Auto provider mode uses custom model IDs.")}
          </div>
        ) : waitingForModelSearch ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {tx("settings.models.searchCatalog", "Search provider catalog to choose a model.")}
          </div>
        ) : loading ? (
          <div className="flex items-center gap-2 px-2 py-1.5 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {tx("settings.models.loadingModels", "Loading models...")}
          </div>
        ) : error || payload?.status === "error" ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {payload?.message || error || tx("settings.models.loadFailed", "Model list unavailable.")}
          </div>
        ) : payload?.status === "not_configured" ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {tx("settings.models.providerNotConfigured", "Configure this provider before loading models.")}
          </div>
        ) : payload?.status === "unsupported" || payload?.status === "missing_api_base" ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {payload.message || tx("settings.models.unsupportedModelList", "Type a model ID manually.")}
          </div>
        ) : isCatalog && !normalizedQuery ? (
          <div className="px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
            {tx("settings.models.searchCatalog", "Search provider catalog to choose a model.")}
            {providerModelCount ? ` ${providerModelCount} ${tx("settings.models.modelsAvailable", "available")}.` : ""}
          </div>
        ) : null}

        {showModels && visibleModels.length ? (
          <div className="max-h-[16rem] overflow-y-auto pr-0.5 scrollbar-thin scrollbar-track-transparent">
            {visibleModels.map((model) =>
              renderModelRow(model, { selected: model.id === value }),
            )}
          </div>
        ) : showModels ? (
          <div className="px-2 py-1.5 text-[11px] text-muted-foreground">
            {tx("settings.models.noModelResults", "No matching models.")}
          </div>
        ) : null}

        {allowCustomModel && customCandidate && !exactQueryMatch && customCandidate !== value ? (
          <>
            {showModels ? <DropdownMenuSeparator /> : null}
            <DropdownMenuItem
              onSelect={() => selectModel(customCandidate)}
              className="flex cursor-default items-center gap-2 rounded-[12px] px-2 py-1.5 text-[12px] focus:bg-muted/85"
            >
              <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-muted/80 text-muted-foreground">
                <Pencil className="h-3 w-3" aria-hidden />
              </span>
              <span className="min-w-0 truncate">
                {tx("settings.models.useCustomModel", "Use")}{" "}
                <span className="font-medium text-foreground">“{customCandidate}”</span>
              </span>
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) {
    const value = tokens / 1_000_000;
    return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    const value = tokens / 1_000;
    return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}K`;
  }
  return String(tokens);
}

function ProviderPickerIcon({
  provider,
  showBrandLogos,
  unconfigured = false,
}: {
  provider: string;
  showBrandLogos: boolean;
  unconfigured?: boolean;
}) {
  const brand = providerBrand(provider);
  const Icon = PROVIDER_ICONS[provider] ?? Hexagon;
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(brand?.logoUrls);

  if (unconfigured) {
    return (
      <span
        data-testid="provider-picker-unconfigured-icon"
        className="grid h-5 w-5 shrink-0 place-items-center text-amber-700 dark:text-amber-200"
        aria-hidden
      >
        <CircleAlert className="h-4 w-4" strokeWidth={1.8} />
      </span>
    );
  }

  if (showBrandLogos && logoUrl) {
    return (
      <span
        data-testid={`provider-picker-logo-${provider}`}
        className="grid h-5 w-5 shrink-0 place-items-center overflow-hidden rounded-md border border-border/35 bg-background shadow-[inset_0_0_0_1px_rgba(0,0,0,0.02)]"
        style={{ boxShadow: `inset 0 0 0 1px ${(brand?.color ?? "#6B7280")}22` }}
        aria-hidden
      >
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-3.5 w-3.5 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }

  if (showBrandLogos && brand) {
    return (
      <span
        data-testid={`provider-picker-logo-fallback-${provider}`}
        className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-[7.5px] font-semibold text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.18)]"
        style={{ backgroundColor: brand.color }}
        aria-hidden
      >
        {brand.initials}
      </span>
    );
  }

  return (
    <span
      className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground"
      aria-hidden
    >
      <Icon className="h-3 w-3" strokeWidth={2} />
    </span>
  );
}

function ProviderSection({
  title,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <ByokSectionHeader title={title} count={count} />
      <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.07)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.22)]">
        {count > 0 ? (
          <div className="divide-y divide-border/45">{children}</div>
        ) : (
          <ByokEmptyState>{empty}</ByokEmptyState>
        )}
      </div>
    </section>
  );
}

function ByokSectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center justify-between px-1">
      <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
        {title}
      </h2>
      <span className="rounded-full bg-muted px-2 py-0.5 text-[11.5px] font-medium text-muted-foreground">
        {count}
      </span>
    </div>
  );
}

function ByokEmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-[18px] border border-dashed border-border/65 bg-card/45 px-4 py-5 text-[13px] text-muted-foreground">
      {children}
    </div>
  );
}

function orderUnconfiguredProviders(
  providers: SettingsPayload["providers"],
): SettingsPayload["providers"] {
  return providers
    .filter((provider) => !RETIRED_LLM_PROVIDERS.has(provider.name))
    .map((provider, index) => ({ provider, index }))
    .sort((left, right) => {
      const rank = providerVisibilityRank(left.provider) - providerVisibilityRank(right.provider);
      return rank || left.index - right.index;
    })
    .map(({ provider }) => provider);
}

function uniqueProviders(
  providers: SettingsPayload["providers"],
): SettingsPayload["providers"] {
  const seen = new Set<string>();
  return providers.filter((provider) => {
    if (seen.has(provider.name)) return false;
    seen.add(provider.name);
    return true;
  });
}

function providerVisibilityRank(provider: SettingsPayload["providers"][number]): number {
  const rank = PROVIDER_DISPLAY_ORDER.get(provider.name);
  if (rank !== undefined) return rank;
  if ((provider.api_key_required ?? true) === false) return 900;
  return 1000;
}

function filterProviders(
  providers: SettingsPayload["providers"],
  query: string,
): SettingsPayload["providers"] {
  const active = providers.filter((provider) => !RETIRED_LLM_PROVIDERS.has(provider.name));
  const normalized = query.trim().toLowerCase();
  if (!normalized) return active;
  return active.filter((provider) =>
    `${provider.name} ${provider.label} ${provider.api_base ?? ""} ${provider.default_api_base ?? ""}`
      .toLowerCase()
      .includes(normalized),
  );
}

interface TimezoneOption {
  name: string;
  offset: string;
  searchText: string;
}

function timezoneOptions(current: string): TimezoneOption[] {
  return timezonesWithCurrent(current).map((name) => {
    const offset = timezoneOffset(name);
    return {
      name,
      offset,
      searchText: `${name} ${name.replace(/_/g, " ")} ${offset}`.toLowerCase(),
    };
  });
}

function timezonesWithCurrent(current: string): string[] {
  const intl = Intl as typeof Intl & {
    supportedValuesOf?: (key: "timeZone") => string[];
  };
  let values: string[];
  try {
    values = intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    values = [];
  }
  const deduped = new Set([...FALLBACK_TIMEZONES, ...values, current].filter(Boolean));
  return Array.from(deduped).sort((left, right) => {
    if (left === "UTC") return -1;
    if (right === "UTC") return 1;
    return left.localeCompare(right);
  });
}

function filterTimezoneOptions(options: TimezoneOption[], query: string): TimezoneOption[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return options;
  return options.filter((option) => option.searchText.includes(normalized));
}

function timezoneOffset(timezone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      timeZoneName: "shortOffset",
      hour: "2-digit",
      minute: "2-digit",
    }).formatToParts(new Date());
    const value = parts.find((part) => part.type === "timeZoneName")?.value;
    return value ? value.replace(/^GMT$/, "UTC").replace(/^GMT/, "UTC") : "UTC";
  } catch {
    return "Custom timezone";
  }
}

function optionRowsWithCurrent(
  options: Array<{ name: string; label: string }>,
  value: string,
): Array<{ name: string; label: string }> {
  if (!value || options.some((option) => option.name === value)) return options;
  return [{ name: value, label: value }, ...options];
}

const PROVIDER_ICONS: Record<string, LucideIcon> = {
  custom: Hexagon,
  custom_anthropic: Brain,
  qwen: Gem,
  hunyuan: Cloud,
  qianfan: Database,
  stepfun: Orbit,
  volcengine: Zap,
  kimi_coding: Moon,
  openrouter: Route,
  omniroute: Route,
  anthropic: Brain,
  openai: Bot,
  deepseek: Waves,
  zai: Grid3X3,
  zhipu: Grid3X3,
  moonshot: Moon,
  minimax: Zap,
  groq: Cpu,
  huggingface: Layers,
  gemini: Gem,
  mistral: Orbit,
  siliconflow: Layers,
  azure_openai: Cloud,
  bedrock: Database,
  bocha: Search,
  brave: Search,
  duckduckgo: Search,
  exa: Search,
  jina: Search,
  kagi: Search,
  olostep: Search,
  searxng: Search,
  tavily: Search,
  vllm: Cpu,
  ollama: Cpu,
  lm_studio: Cpu,
  atomic_chat: Cpu,
  ovms: Cpu,
  nvidia: Zap,
};

function ProviderIcon({
  provider,
  showBrandLogos,
}: {
  provider: string;
  showBrandLogos: boolean;
}) {
  const brand = providerBrand(provider);
  const Icon = PROVIDER_ICONS[provider] ?? Hexagon;
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(brand?.logoUrls);

  if (showBrandLogos && logoUrl) {
    return (
      <span
        data-testid={`provider-logo-${provider}`}
        className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-[14px] border border-border/45 bg-background shadow-[inset_0_0_0_1px_rgba(0,0,0,0.025)]"
        style={{ boxShadow: `inset 0 0 0 1px ${(brand?.color ?? "#6B7280")}22` }}
      >
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-6 w-6 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }
  if (showBrandLogos && brand) {
    return (
      <span
        data-testid={`provider-logo-fallback-${provider}`}
        className="grid h-10 w-10 shrink-0 place-items-center rounded-[14px] text-[11px] font-semibold text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.18)]"
        style={{ backgroundColor: brand.color }}
        aria-hidden
      >
        {brand.initials}
      </span>
    );
  }
  return (
    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-muted text-foreground/82 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.025)] dark:bg-muted/70">
      <Icon className="h-5 w-5" strokeWidth={2} aria-hidden />
    </span>
  );
}

function OverviewRowIcon({
  icon: Icon,
}: {
  icon: LucideIcon;
}) {
  return (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[12px] bg-muted text-foreground/82 transition-colors group-hover:bg-muted/80 dark:bg-muted/70">
      <Icon className="h-4 w-4" aria-hidden />
    </span>
  );
}

function OverviewValueLogo({
  provider,
  showBrandLogos,
}: {
  provider: string | null | undefined;
  showBrandLogos: boolean;
}) {
  const brand = provider ? providerBrand(provider) : null;
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(brand?.logoUrls);

  if (!provider || !showBrandLogos || !brand) return null;

  if (logoUrl) {
    return (
      <span
        data-testid={`overview-logo-${provider}`}
        className="grid h-5 w-5 shrink-0 place-items-center overflow-hidden rounded-md border border-border/35 bg-background shadow-[inset_0_0_0_1px_rgba(0,0,0,0.02)]"
        style={{ boxShadow: `inset 0 0 0 1px ${brand.color}22` }}
        aria-hidden
      >
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-3.5 w-3.5 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }

  return (
    <span
      data-testid={`overview-logo-fallback-${provider}`}
      className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-[7.5px] font-semibold text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.18)]"
      style={{ backgroundColor: brand.color }}
      aria-hidden
    >
      {brand.initials}
    </span>
  );
}

function OverviewListRow({
  icon: Icon,
  valueLogoProvider,
  title,
  value,
  caption,
  showBrandLogos = false,
  onClick,
}: {
  icon: LucideIcon;
  valueLogoProvider?: string | null;
  title: string;
  value: string;
  caption: string;
  showBrandLogos?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex min-h-[68px] w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-muted/30 sm:px-5"
    >
      <OverviewRowIcon icon={Icon} />
      <span className="min-w-0 flex-1">
        <span className="block text-[14px] font-medium leading-5 text-foreground">{title}</span>
        <span className="mt-0.5 block truncate text-[12px] leading-5 text-muted-foreground">{caption}</span>
      </span>
      <span className="ml-auto flex min-w-0 max-w-[48%] items-center gap-2">
        <OverviewValueLogo provider={valueLogoProvider} showBrandLogos={showBrandLogos} />
        <span className="truncate text-right text-[13px] leading-5 text-muted-foreground">
          {value}
        </span>
        <ChevronRight
          className="h-4 w-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5"
          aria-hidden
        />
      </span>
    </button>
  );
}

function SettingsSectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2 px-1 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
      {children}
    </h2>
  );
}

function SettingsGroup({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]">
      <div className="divide-y divide-border/45">{children}</div>
    </div>
  );
}

function SettingsRow({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0">
        <div className="text-[14px] font-medium leading-5 text-foreground">{title}</div>
        {description ? (
          <div className="mt-0.5 max-w-[40rem] whitespace-pre-line text-[12px] leading-5 text-muted-foreground">
            {description}
          </div>
        ) : null}
      </div>
      {children ? <div className="min-w-0 sm:ml-6 sm:shrink-0">{children}</div> : null}
    </div>
  );
}

function ReadOnlyRow({
  title,
  value,
  description,
}: {
  title: string;
  value: string;
  description?: string;
}) {
  return (
    <SettingsRow title={title} description={description}>
      <span className="block max-w-full truncate text-left text-[13px] text-muted-foreground sm:max-w-[320px] sm:text-right">
        {value}
      </span>
    </SettingsRow>
  );
}


function RestartSettingsFooter({
  dirty,
  saving,
  pendingRestart,
  disabled = false,
  message,
  dirtyMessage,
  pendingMessage,
  onSave,
  onRestart,
  onReset,
  isRestarting,
}: {
  dirty: boolean;
  saving: boolean;
  pendingRestart: boolean;
  disabled?: boolean;
  message?: string;
  dirtyMessage?: string;
  pendingMessage?: string;
  onSave: () => void;
  onRestart?: () => void;
  onReset?: () => void;
  isRestarting?: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const isNativeHost = isNativeRuntime();
  const restartLabel = isNativeHost
    ? tx("app.system.restartEngine", "Restart engine")
    : t("app.system.restart");
  const restartingLabel = isNativeHost
    ? tx("app.system.restartingEngine", "Restarting engine...")
    : t("app.system.restarting");
  const statusMessage =
    message ??
    (pendingRestart && !dirty
      ? pendingMessage ?? tx("settings.status.savedRestartApply", "Saved. Restart when ready.")
      : dirty
        ? dirtyMessage ?? t("settings.status.unsaved")
        : undefined);
  const statusTone = disabled ? "danger" : dirty || pendingRestart ? "accent" : undefined;

  return (
    <div className="flex min-h-[58px] flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0 text-[13px] leading-5 text-muted-foreground">
        <SettingsStatusMessage tone={statusTone}>{statusMessage}</SettingsStatusMessage>
      </div>
      <div className="flex w-full shrink-0 flex-wrap justify-end gap-2 sm:w-auto">
        {pendingRestart && !dirty && onRestart ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onRestart}
            disabled={isRestarting}
            className="rounded-full"
          >
            {isRestarting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {isRestarting ? restartingLabel : restartLabel}
          </Button>
        ) : null}
        {onReset ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onReset}
            disabled={!dirty || saving}
            className="rounded-full"
          >
            {t("settings.actions.cancel")}
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="outline"
          onClick={onSave}
          disabled={!dirty || disabled || saving}
          className="rounded-full"
        >
          {saving ? t("settings.actions.saving") : t("settings.actions.save")}
        </Button>
      </div>
    </div>
  );
}

function SettingsFooter({
  dirty,
  saving,
  saved,
  disabled = false,
  message,
  onSave,
}: {
  dirty: boolean;
  saving: boolean;
  saved: boolean;
  disabled?: boolean;
  message?: string;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const statusMessage = message ?? (dirty
    ? t("settings.status.unsaved")
    : saved
      ? t("settings.status.savedRestart")
      : tx("settings.status.upToDate", "Up to date."));
  return (
    <div className="flex min-h-[58px] flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="text-[13px] text-muted-foreground">
        <SettingsStatusMessage tone={disabled ? "danger" : dirty || saved ? "accent" : undefined}>
          {statusMessage}
        </SettingsStatusMessage>
      </div>
      <div className="flex justify-end">
        <Button size="sm" variant="outline" onClick={onSave} disabled={!dirty || disabled || saving} className="rounded-full">
          {saving ? t("settings.actions.saving") : t("settings.actions.save")}
        </Button>
      </div>
    </div>
  );
}

function SettingsStatusMessage({
  children,
  tone,
}: {
  children?: ReactNode;
  tone?: "accent" | "danger";
}) {
  if (!children) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2",
        tone === "accent" && "font-medium text-blue-600 dark:text-blue-300",
        tone === "danger" && "font-medium text-destructive",
      )}
    >
      {tone ? (
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            tone === "accent" &&
              "bg-blue-500 shadow-[0_0_0_3px_rgba(59,130,246,0.14)] dark:bg-blue-400 dark:shadow-[0_0_0_3px_rgba(96,165,250,0.18)]",
            tone === "danger" && "bg-destructive/70",
          )}
          aria-hidden
        />
      ) : null}
      <span>{children}</span>
    </span>
  );
}

function StatusPill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning";
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-[260px] items-center rounded-full px-2.5 py-1 text-[12px] font-medium",
        tone === "success" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
        tone === "warning" && "bg-amber-500/10 text-amber-700 dark:text-amber-300",
        tone === "neutral" && "bg-muted text-muted-foreground",
      )}
    >
      <span className="truncate">{children}</span>
    </span>
  );
}

function SegmentedControl({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="inline-flex min-h-8 max-w-full flex-wrap items-center gap-0.5 rounded-full bg-muted p-0.5 text-[12px] font-medium text-muted-foreground">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-full px-3 py-1 transition-colors",
            value === option.value && "bg-background text-foreground shadow-sm",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function NumberInput({
  value,
  min,
  max,
  onChange,
  suffix,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  suffix?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <Input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => {
          const parsed = Number(event.target.value);
          if (Number.isFinite(parsed)) onChange(parsed);
        }}
        className="h-8 w-24 max-w-full rounded-full text-[13px]"
      />
      {suffix ? <span className="text-[12px] text-muted-foreground">{suffix}</span> : null}
    </div>
  );
}
