import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FilePreviewAvailabilityProvider } from "@/components/FilePreviewAvailabilityContext";
import { FilePreviewPanel } from "@/components/FilePreviewPanel";
import { FileWorkspaceActionsProvider } from "@/components/FileWorkspaceActionsContext";
import { PendingReviewPanel } from "@/components/review/PendingReviewPanel";
import { ArtifactCanvas } from "@/components/thread/ArtifactCanvas";
import { resolveArtifactPlacement } from "@/lib/artifact-placement";
import { PromptNavigator } from "@/components/thread/PromptNavigator";
import {
  SessionInfoPopover,
  createLoopComposerSeed,
} from "@/components/thread/SessionInfoPopover";
import {
  isComposerTurnMode,
  modeFromOutgoingContent,
  readComposerTurnMode,
  writeComposerTurnMode,
  type ComposerTurnMode,
} from "@/components/thread/ComposerModeMenu";
import { ThreadComposer, type ComposerModelOption } from "@/components/thread/ThreadComposer";
import {
  FINISHED_CARD_LINGER_MS,
  ParallelSubagentsPanel,
  pruneFinishedSubagentCards,
  upsertSubagentCard,
  type ParallelSubagentCard,
} from "@/components/thread/ParallelSubagentsPanel";
import { TaskProgressStrip } from "@/components/thread/TaskProgressStrip";
import { ThreadPlanPanel } from "@/components/thread/ThreadPlanPanel";
import { ConversationFindBar } from "@/components/ConversationFindBar";
import { ThreadHeader } from "@/components/thread/ThreadHeader";
import { useConversationFind } from "@/hooks/useConversationFind";
import { PresenceAvatars } from "@/components/thread/PresenceAvatars";
import { ViewerReadOnlyBanner } from "@/components/thread/ViewerReadOnlyBanner";
import { usePresence } from "@/hooks/usePresence";
import { useOrgRole } from "@/hooks/useOrgRole";
import { usePendingReview } from "@/hooks/usePendingReview";
import { proveProjectPath } from "@/components/dev/proveChange";
import { useProveChange } from "@/hooks/useProveChange";
import { codePanelHref } from "@/lib/code-panel-route";
import { ApprovalPrompt } from "@/components/thread/ApprovalPrompt";
import { ChoicePrompt } from "@/components/thread/ChoicePrompt";
import { ConnectionStatusBanner } from "@/components/thread/ConnectionStatusBanner";
import type { SelectedModelRoute } from "@/components/thread/QuotaLimitCard";
import { StreamErrorNotice } from "@/components/thread/StreamErrorNotice";
import { ThreadViewport, type ThreadViewportHandle } from "@/components/thread/ThreadViewport";
import { useNavinStream, type SendAttachment, type SendOptions } from "@/hooks/useNavinStream";
import { useSessionHistory } from "@/hooks/useSessions";
import { artifactsNotInEditor } from "@/lib/artifact-dedup";
import { resolveChipPathFromEdits } from "@/lib/file-chip-paths";
import {
  getDevOpenFileEntries,
  getDevOpenFiles,
  subscribeDevOpenFiles,
} from "@/lib/dev-open-files";
import { readLastDevContext } from "@/lib/last-dev-context";
import {
  ApiError,
  fetchFilePreviewAvailability,
  fetchDocumentTemplates,
  fetchInstalledCliApps,
  fetchMcpPresets,
  fetchSettings,
  listSlashCommands,
  updateImageGenerationSettings,
  updateMusicGenerationSettings,
  updateTranscriptionSettings,
  updateModelConfiguration,
  updateSettings,
  updateVideoGenerationSettings,
  updateVoiceSettings,
  type SessionPlan,
} from "@/lib/api";
import {
  canBeChatDefault,
  DEFAULT_CHAT_MODEL,
  DEFAULT_CHAT_PRESET,
  isMediaModality,
  isMediaModelSlug,
  MONTAGE_DEFAULT_MODEL,
  MONTAGE_DEFAULT_PRESET,
} from "@/lib/model-modality";
import {
  CLI_APPS_CHANGED_EVENT,
  installedCliAppsFromPayload,
  isCliAppsPayload,
} from "@/lib/cli-app-events";
import {
  MCP_PRESETS_CHANGED_EVENT,
  installedMcpPresetsFromPayload,
  isMcpPresetsPayload,
} from "@/lib/mcp-preset-events";
import { inferProviderFromModelName, providerDisplayLabel } from "@/lib/provider-brand";
import {
  loadLastModelPick,
  loadThreadModelPreset,
  saveLastModelPick,
  saveThreadModelPreset,
} from "@/lib/thread-model-preset";
import {
  AUTO_MODEL_PRESET,
  isAutoModelPreset,
  routeSwaps,
  type RouteSwap,
} from "@/lib/model-routing-summary";
import { taskRoleShortLabel } from "@/lib/task-route-label";
import { DEFAULT_REASONING_EFFORT_VALUES } from "@/lib/reasoning-effort";
import type {
  ArtifactRecord,
  ChatSummary,
  DocumentTemplateInfo,
  ProjectFileMatch,
  SettingsPayload,
  SlashCommand,
  SkillSummary,
  UIMessage,
  WorkspaceScopePayload,
  WorkspacesPayload,
} from "@/lib/types";
import { normalizeLegacyLongTaskMessages } from "@/lib/thread-display-compat";
import { scrubSubagentUiMessages } from "@/lib/subagent-channel-display";
import { truncateMiddle } from "@/lib/short-path";
import { formatToolCallTrace } from "@/lib/tool-traces";
import { useClient } from "@/providers/ClientProvider";
import { useNotifications } from "@/providers/NotificationProvider";
import { useAccount } from "@/hooks/useAccount";

function projectWebuiThreadMessages(messages: UIMessage[]): UIMessage[] {
  return scrubSubagentUiMessages(normalizeLegacyLongTaskMessages(messages));
}

type MessageShape = Pick<UIMessage, "role" | "kind" | "content">;

function sameMessageShape(a: MessageShape, b: MessageShape): boolean {
  return (
    a.role === b.role
    && (a.kind ?? "") === (b.kind ?? "")
    && a.content === b.content
  );
}

function durableMessageShape(message: UIMessage): MessageShape | null {
  if (message.kind === "trace") return null;
  if (message.role !== "user" && message.role !== "assistant") return null;
  if (message.role === "assistant" && !message.content.trim() && !message.media?.length) {
    return null;
  }
  return {
    role: message.role,
    kind: message.kind,
    content: message.content,
  };
}

const NO_SKILLS: SkillSummary[] = [];

function preservesDurableMessages(current: UIMessage[], snapshot: UIMessage[]): boolean {
  // Canonical history refreshes can race with live websocket messages after fork/send.
  // Never accept a refreshed snapshot that drops a user/assistant message already shown.
  const expected = current
    .map(durableMessageShape)
    .filter((message): message is MessageShape => message !== null);
  if (expected.length === 0) return true;
  const candidates = snapshot
    .map(durableMessageShape)
    .filter((message): message is MessageShape => message !== null);

  let cursor = 0;
  for (const message of expected) {
    let found = false;
    while (cursor < candidates.length) {
      const candidate = candidates[cursor];
      cursor += 1;
      if (sameMessageShape(message, candidate)) {
        found = true;
        break;
      }
    }
    if (!found) return false;
  }
  return true;
}

function isStaleThreadSnapshot(current: UIMessage[], snapshot: UIMessage[]): boolean {
  if (current.length === 0) return false;
  if (snapshot.length === 0) return true;
  if (!preservesDurableMessages(current, snapshot)) return true;
  if (snapshot.length >= current.length) return false;
  return snapshot.every((message, index) => sameMessageShape(current[index], message));
}

const FILE_PREVIEW_DEFAULT_WIDTH = 544;
const FILE_PREVIEW_MIN_WIDTH = 360;
const FILE_PREVIEW_MAX_WIDTH = 860;
const FILE_PREVIEW_MIN_MAIN_WIDTH = 420;
const FILE_PREVIEW_CLOSE_ANIMATION_MS = 320;

const ARTIFACT_CANVAS_DEFAULT_WIDTH = 520;
const ARTIFACT_CANVAS_MIN_WIDTH = 320;
const ARTIFACT_CANVAS_MAX_WIDTH = 820;
const ARTIFACT_CANVAS_MIN_MAIN_WIDTH = 360;
const ARTIFACT_CANVAS_CLOSE_ANIMATION_MS = 320;

function clampArtifactCanvasWidth(width: number, maxWidth: number): number {
  return Math.min(Math.max(width, ARTIFACT_CANVAS_MIN_WIDTH), maxWidth);
}

function maxArtifactCanvasWidth(containerWidth: number): number {
  return Math.max(
    ARTIFACT_CANVAS_MIN_WIDTH,
    Math.min(ARTIFACT_CANVAS_MAX_WIDTH, containerWidth - ARTIFACT_CANVAS_MIN_MAIN_WIDTH),
  );
}

type FilePreviewAvailabilityCacheEntry = {
  available?: boolean;
  promise: Promise<boolean>;
  revision: number;
};

function clampFilePreviewWidth(width: number, maxWidth: number): number {
  return Math.min(Math.max(width, FILE_PREVIEW_MIN_WIDTH), maxWidth);
}

function maxFilePreviewWidth(containerWidth: number): number {
  return Math.max(
    FILE_PREVIEW_MIN_WIDTH,
    Math.min(FILE_PREVIEW_MAX_WIDTH, containerWidth - FILE_PREVIEW_MIN_MAIN_WIDTH),
  );
}

interface ThreadShellProps {
  session: ChatSummary | null;
  title: string;
  onToggleSidebar: () => void;
  onGoHome?: () => void;
  onNewChat?: () => void;
  onCreateChat?: (
    workspaceScope?: WorkspaceScopePayload | null,
    options?: { keepWorkbench?: boolean },
  ) => Promise<string | null>;
  onForkChat?: (sourceChatId: string, beforeUserIndex: number) => Promise<string | null>;
  onRevertResubmit?: (beforeUserIndex: number, content: string) => void | Promise<void>;
  onTurnEnd?: () => void;
  onUserMessage?: (chatId: string, text: string) => void;
  hideSidebarToggleForHostChrome?: boolean;
  hostChromeTitleInset?: boolean;
  hideHeader?: boolean;
  /**
   * Where to render the artifact canvas. When the shell sits next to a
   * workbench, results belong in that centre area, not in this column.
   */
  artifactCanvasHost?: HTMLElement | null;
  headerLeadingActions?: ReactNode;
  workspaceScope?: WorkspaceScopePayload | null;
  workspaceDefaultScope?: WorkspaceScopePayload | null;
  workspaceControls?: WorkspacesPayload["controls"] | null;
  workspaceScopeDisabled?: boolean;
  workspaceError?: string | null;
  onWorkspaceScopeChange?: (scope: WorkspaceScopePayload) => void;
  settingsSnapshot?: SettingsPayload | null;
  onSettingsChange?: (settings: SettingsPayload) => void;
  onOpenModelSettings?: () => void;
  skills?: SkillSummary[];
  onOpenFileInEditor?: (
    path: string,
    options?: { mode?: "preview" | "code" | "diff" },
  ) => void;
  /**
   * True when a workbench (Code, …) is on screen next to the chat. Clicking a
   * file in the transcript hands off to that editor only when it is visible.
   */
  workbenchVisible?: boolean;
  autoSendRequest?: {
    text: string;
    nonce: number;
    documentTemplate?: { category: string; name: string; title?: string };
  } | null;
  /** Text to drop into the composer without sending it. */
  composerSeed?: {
    text: string;
    nonce: number;
    files?: ProjectFileMatch[];
    replace?: boolean;
    mediaTemplate?: { id: string; title?: string; kind?: string; format?: string };
    mediaTemplates?: { id: string; title?: string; kind?: string; format?: string }[];
    localFiles?: File[];
  } | null;
  /**
   * The composer landed `composerSeed` in the input: the owner must clear it,
   * otherwise a composer remount (empty chat -> real thread) re-applies it.
   */
  onComposerSeedConsumed?: (nonce: number) => void;
  /**
   * Active product shell module (`code`, `seo`, `marketing`, …).
   * Scopes slash-command palette and per-turn skill loading.
   */
  productModule?: string | null;
}

function toModelBadgeLabel(modelName: string | null): string | null {
  if (!modelName) return null;
  const trimmed = modelName.trim();
  if (!trimmed) return null;
  const leaf = trimmed.split("/").pop() ?? trimmed;
  return leaf || trimmed;
}

interface ModelBadgeInfo {
  label: string | null;
  provider: string | null;
  providerLabel: string | null;
  needsSetup: boolean;
}

function presetAllowedForBudget(
  preset: SettingsPayload["model_presets"][number] | null | undefined,
): boolean {
  if (!preset) return false;
  // BYOK / custom providers are never usage-gated.
  if (preset.source === "byok") return true;
  if ((preset.provider || "").toLowerCase() !== "navin") return true;
  if (preset.budget_allowed === false) {
    const modality = preset.modality ?? "text";
    return modality !== "text";
  }
  return true;
}

function presetLabelForSlug(
  settings: SettingsPayload | null,
  slug: string | null | undefined,
  presetName?: string | null,
): string {
  const rows = (settings?.model_presets ?? []).filter(
    (preset) => preset.model === slug || preset.name === presetName,
  );
  // The account default row is often just called "Default"; a sibling preset
  // on the same model carries the real name ("GLM 5.3 Flash").
  const named = rows.find(
    (preset) =>
      preset.label && preset.name !== "default" && !/^default$/i.test(preset.label.trim()),
  );
  const row = named ?? rows[0];
  if (row?.label && !/^default$/i.test(row.label.trim())) return row.label;
  const raw = (slug || row?.model || presetName || "").trim();
  return raw.split("/").pop()?.replace(/:free$/i, "") || raw;
}

function activeModelPreset(settings: SettingsPayload | null): SettingsPayload["model_presets"][number] | null {
  if (!settings) return null;
  const configured = settings.agent.model_preset || "default";
  const configuredRow =
    settings.model_presets.find((preset) => preset.name === configured) ?? null;
  if (
    configuredRow
    && canBeChatDefault(configuredRow.modality, configuredRow.model)
    && presetAllowedForBudget(configuredRow)
  ) {
    return configuredRow;
  }
  // Global default stuck on Lyria/etc.: prefer the catalog default, else any text row.
  const grok = settings.model_presets.find(
    (preset) =>
      (preset.name === DEFAULT_CHAT_PRESET || preset.model === DEFAULT_CHAT_MODEL)
      && canBeChatDefault(preset.modality, preset.model)
      && presetAllowedForBudget(preset),
  );
  if (grok) return grok;
  return (
    settings.model_presets.find(
      (preset) =>
        preset.active
        && canBeChatDefault(preset.modality, preset.model)
        && presetAllowedForBudget(preset),
    )
    ?? settings.model_presets.find(
      (preset) =>
        canBeChatDefault(preset.modality, preset.model)
        && presetAllowedForBudget(preset),
    )
    ?? null
  );
}

function resolvedModelProvider(settings: SettingsPayload | null, modelName: string | null): string | null {
  const preset = activeModelPreset(settings);
  const rawProvider = preset?.provider || settings?.agent.provider || null;
  if (rawProvider === "auto") {
    return settings?.agent.resolved_provider || inferProviderFromModelName(modelName) || null;
  }
  return rawProvider || inferProviderFromModelName(modelName);
}

function providerRowConfigured(
  settings: SettingsPayload | null,
  provider: string | null,
): SettingsPayload["providers"][number] | null {
  if (!settings || !provider) return null;
  return settings.providers.find((item) => item.name === provider) ?? null;
}

function toModelBadgeInfo(
  modelName: string | null,
  settings: SettingsPayload | null,
  threadPreset?: SettingsPayload["model_presets"][number] | null,
): ModelBadgeInfo {
  // A preset pinned to this conversation wins over the global default.
  const preset = threadPreset ?? activeModelPreset(settings);
  const model =
    threadPreset?.model
    || modelName
    || preset?.model
    || settings?.agent.model
    || null;
  const label =
    (threadPreset?.label || preset?.label || "").trim()
    || toModelBadgeLabel(model);

  let provider = threadPreset
    ? (threadPreset.provider !== "auto" && threadPreset.provider)
      || inferProviderFromModelName(model)
    : resolvedModelProvider(settings, model);

  let providerRow = providerRowConfigured(settings, provider);

  // Plan key lives on ``navin``; legacy presets may still say ``openrouter``.
  // Prefer the configured Navin slot so the badge shows the model name.
  if (provider && (!providerRow || !providerRow.configured)) {
    const navin = providerRowConfigured(settings, "navin");
    const managedPreset =
      preset?.provider === "navin"
      || preset?.source === "managed"
      || Boolean(preset?.locked)
      || provider === "openrouter"
      || provider === "navin";
    if (navin?.configured && managedPreset) {
      provider = "navin";
      providerRow = navin;
    }
  }

  const providerReady = Boolean(
    (providerRow && providerRow.configured)
    || (settings?.agent.has_api_key
      && (provider === settings.agent.provider
        || provider === settings.agent.resolved_provider
        || provider === "navin")),
  );
  // Only block the badge when there is no model at all. A selected model
  // must keep its name visible even if the provider row is still settling.
  const needsSetup = Boolean(settings && !model);

  return {
    label,
    provider,
    providerLabel: provider ? providerDisplayLabel(settings?.providers ?? [], provider) : null,
    needsSetup: needsSetup || (Boolean(model) && !providerReady),
  };
}

const HERO_GREETING_MODULES = [
  "chat",
  "code",
  "scraping",
  "content",
  "marketing",
  "montage",
  "ads",
  "seo",
  "leads",
  "career",
  "trading",
  "meeting",
  "risklens",
  "ops",
  "crm",
] as const;

type HeroGreetingModule = (typeof HERO_GREETING_MODULES)[number];

const HERO_GREETING_VARIANTS = ["a", "b", "c", "d"] as const;

function heroGreetingModule(productModule: string | null | undefined): HeroGreetingModule {
  const raw = (productModule || "chat").trim().toLowerCase();
  return (HERO_GREETING_MODULES as readonly string[]).includes(raw)
    ? (raw as HeroGreetingModule)
    : "chat";
}

function heroGreetingKeysForModule(module: HeroGreetingModule): string[] {
  return HERO_GREETING_VARIANTS.map((variant) => `thread.empty.greetings.${module}.${variant}`);
}

function randomHeroGreetingKey(productModule: string | null | undefined): string {
  const keys = heroGreetingKeysForModule(heroGreetingModule(productModule));
  const index = Math.floor(Math.random() * keys.length);
  return keys[index] ?? keys[0] ?? "thread.empty.greetings.chat.a";
}

interface PendingFirstMessage {
  content: string;
  images?: SendAttachment[];
  options?: SendOptions;
}

interface InstalledSettingItemsOptions<Payload, Item> {
  token: string;
  eventName: string;
  fetchPayload: (token: string) => Promise<Payload>;
  isPayload: (value: unknown) => value is Payload;
  selectItems: (payload: Payload) => Item[];
}

function useInstalledSettingItems<Payload, Item>({
  token,
  eventName,
  fetchPayload,
  isPayload,
  selectItems,
}: InstalledSettingItemsOptions<Payload, Item>): Item[] {
  const [items, setItems] = useState<Item[]>([]);

  const refresh = useCallback(async (isCancelled?: () => boolean) => {
    try {
      const payload = await fetchPayload(token);
      if (!isCancelled?.()) setItems(selectItems(payload));
    } catch {
      // Keep the last successful catalog during transient focus/visibility refresh failures.
    }
  }, [fetchPayload, selectItems, token]);

  useEffect(() => {
    let cancelled = false;
    void refresh(() => cancelled);

    const refreshOnFocus = () => {
      if (document.visibilityState === "hidden") return;
      void refresh();
    };
    const refreshOnChanged = (event: Event) => {
      const payload = (event as CustomEvent<unknown>).detail;
      if (isPayload(payload)) {
        setItems(selectItems(payload));
        return;
      }
      void refresh();
    };

    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshOnFocus);
    window.addEventListener(eventName, refreshOnChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshOnFocus);
      window.removeEventListener(eventName, refreshOnChanged);
    };
  }, [eventName, isPayload, refresh, selectItems]);

  return items;
}

export function ThreadShell({
  session,
  title,
  onToggleSidebar,
  onNewChat,
  onCreateChat,
  onForkChat,
  onRevertResubmit,
  onTurnEnd,
  onUserMessage,
  hideSidebarToggleForHostChrome = false,
  hostChromeTitleInset = false,
  hideHeader = false,
  artifactCanvasHost = null,
  headerLeadingActions,
  workspaceScope = null,
  workspaceDefaultScope = null,
  workspaceControls = null,
  workspaceScopeDisabled = false,
  workspaceError = null,
  onWorkspaceScopeChange,
  settingsSnapshot = null,
  onSettingsChange,
  onOpenModelSettings,
  // A literal default would hand the memoized composer a new array on every
  // render and defeat the shallow compare.
  skills = NO_SKILLS,
  onOpenFileInEditor,
  workbenchVisible = false,
  autoSendRequest = null,
  composerSeed = null,
  onComposerSeedConsumed,
  productModule = null,
}: ThreadShellProps) {
  const { t } = useTranslation();
  const chatId = session?.chatId ?? null;
  const historyKey = session?.key ?? null;
  const {
    messages: historical,
    loading,
    loadingOlder,
    loadOlder,
    hasMoreBefore,
    userMessageOffset,
    hasPendingToolCalls,
    refresh: refreshHistory,
    version: historyVersion,
    forkBoundaryMessageCount,
  } = useSessionHistory(historyKey);
  const { client, ingressLimits, modelName, token } = useClient();
  const { notify } = useNotifications();
  const { account } = useAccount();
  const presenceMembers = usePresence(chatId);
  const orgRole = useOrgRole();
  const isViewer = orgRole === "viewer";
  const [booting, setBooting] = useState(false);
  const [composerTurnMode, setComposerTurnMode] = useState<ComposerTurnMode>(
    () => {
      const moduleId = (productModule || "").trim().toLowerCase();
      if (moduleId === "code" || moduleId === "dev") return "agent";
      return readComposerTurnMode();
    },
  );
  // Code is always agent. Tchat (#/new, productModule null) keeps ask when
  // written before navigate; other modules keep their stored preference.
  useEffect(() => {
    const moduleId = (productModule || "").trim().toLowerCase();
    if (moduleId === "code" || moduleId === "dev") {
      setComposerTurnMode("agent");
      writeComposerTurnMode("agent");
      return;
    }
    if (!chatId && !moduleId) {
      setComposerTurnMode(readComposerTurnMode());
    }
  }, [chatId, productModule]);
  const [loopComposerSeed, setLoopComposerSeed] = useState<{
    text: string;
    nonce: number;
  } | null>(null);
  const handleCreateLoop = useCallback(() => {
    setLoopComposerSeed({
      text: createLoopComposerSeed(productModule, t),
      nonce: Date.now(),
    });
  }, [productModule, t]);
  const effectiveComposerSeed = useMemo(() => {
    if (loopComposerSeed && composerSeed) {
      return loopComposerSeed.nonce >= composerSeed.nonce
        ? loopComposerSeed
        : composerSeed;
    }
    return loopComposerSeed ?? composerSeed;
  }, [composerSeed, loopComposerSeed]);
  const handleComposerSeedConsumed = useCallback(
    (nonce: number) => {
      setLoopComposerSeed((cur) => (cur && cur.nonce === nonce ? null : cur));
      onComposerSeedConsumed?.(nonce);
    },
    [onComposerSeedConsumed],
  );
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([]);
  const cliApps = useInstalledSettingItems({
    token,
    eventName: CLI_APPS_CHANGED_EVENT,
    fetchPayload: fetchInstalledCliApps,
    isPayload: isCliAppsPayload,
    selectItems: installedCliAppsFromPayload,
  });
  const mcpPresets = useInstalledSettingItems({
    token,
    eventName: MCP_PRESETS_CHANGED_EVENT,
    fetchPayload: fetchMcpPresets,
    isPayload: isMcpPresetsPayload,
    selectItems: installedMcpPresetsFromPayload,
  });
  const [documentTemplates, setDocumentTemplates] = useState<DocumentTemplateInfo[]>([]);
  const [selectedDocumentTemplate, setSelectedDocumentTemplate] =
    useState<DocumentTemplateInfo | null>(null);
  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    fetchDocumentTemplates(token)
      .then((payload) => {
        if (cancelled || !Array.isArray(payload?.categories)) return;
        // Preview URLs feed <img src>; the API token must ride the query string.
        setDocumentTemplates(
          payload.categories.flatMap((category) =>
            (category.templates ?? []).map((template) =>
              template.preview_url
                ? {
                    ...template,
                    preview_url: `${template.preview_url}?token=${encodeURIComponent(token)}`,
                  }
                : template,
            ),
          ),
        );
      })
      .catch(() => {
        if (!cancelled) setDocumentTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);
  const [settings, setSettings] = useState<SettingsPayload | null>(settingsSnapshot);
  const [heroGreetingKey, setHeroGreetingKey] = useState(() => randomHeroGreetingKey(productModule));
  const [scrollToBottomSignal, setScrollToBottomSignal] = useState(0);
  const [scrollToLatestUserPromptSignal, setScrollToLatestUserPromptSignal] = useState(0);
  const [filePreviewPath, setFilePreviewPath] = useState<string | null>(null);
  const [filePreviewClosing, setFilePreviewClosing] = useState(false);
  const [filePreviewWidth, setFilePreviewWidth] = useState(FILE_PREVIEW_DEFAULT_WIDTH);
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [artifactCanvasOpen, setArtifactCanvasOpen] = useState(false);
  const [artifactCanvasClosing, setArtifactCanvasClosing] = useState(false);
  const [artifactCanvasWidth, setArtifactCanvasWidth] = useState(ARTIFACT_CANVAS_DEFAULT_WIDTH);
  const [subagentCards, setSubagentCards] = useState<ParallelSubagentCard[]>([]);
  const shellRef = useRef<HTMLElement | null>(null);
  const filePreviewWidthRef = useRef(FILE_PREVIEW_DEFAULT_WIDTH);
  const filePreviewCloseTimerRef = useRef<number | null>(null);
  const artifactCanvasWidthRef = useRef(ARTIFACT_CANVAS_DEFAULT_WIDTH);
  const artifactCanvasCloseTimerRef = useRef<number | null>(null);
  const pendingFirstRef = useRef<PendingFirstMessage | null>(null);
  const [pendingFirstTargetChatId, setPendingFirstTargetChatId] = useState<string | null>(null);

  useEffect(() => {
    if (pendingFirstRef.current) return;
    setBooting(false);
  }, [historyKey]);

  // Leaving the landing desk (`#/new`) or abandoning a mid-create must never
  // keep the composer stuck on "Opening a new chat...".
  useEffect(() => {
    if (chatId) return;
    pendingFirstRef.current = null;
    setPendingFirstTargetChatId(null);
    setBooting(false);
  }, [chatId]);

  // If chat creation hangs or never binds, do not leave the composer grayed
  // on "Opening a new chat..." forever.
  useEffect(() => {
    if (!booting) return;
    const timer = window.setTimeout(() => {
      pendingFirstRef.current = null;
      setPendingFirstTargetChatId(null);
      setBooting(false);
    }, 8000);
    return () => window.clearTimeout(timer);
  }, [booting]);
  const viewportRef = useRef<ThreadViewportHandle | null>(null);
  const messageCacheRef = useRef<Map<string, UIMessage[]>>(new Map());
  /** Last chatId we associated with the in-memory thread (for cache-on-switch). */
  const prevChatIdForCacheRef = useRef<string | null>(null);
  /** Skip one message-cache write right after chatId changes (messages may not match yet). */
  const skipLayoutCacheRef = useRef(false);
  const appliedHistoryVersionRef = useRef<Map<string, number>>(new Map());
  const pendingCanonicalHydrateRef = useRef<Set<string>>(new Set());
  const sessionKeyByChatIdRef = useRef<Map<string, string>>(new Map());
  const bottomScrolledChatIdRef = useRef<string | null>(null);

  const initial = useMemo(() => {
    if (!chatId) return historical;
    return messageCacheRef.current.get(chatId) ?? historical;
  }, [chatId, historical]);
  const handleTurnEnd = useCallback(() => {
    onTurnEnd?.();
  }, [onTurnEnd]);
  const {
    messages,
    isStreaming,
    runStartedAt,
    goalState,
    activeTaskProgress,
    send,
    transcribeAudio,
    stop,
    setMessages,
    streamError,
    dismissStreamError,
    contextCompaction,
    checkpointNotice,
    pendingApprovals,
    respondToApproval,
    pendingChoices,
    respondToChoice,
  } = useNavinStream(chatId, initial, hasPendingToolCalls, handleTurnEnd, onUserMessage, {
    modelName,
    modelLabel: toModelBadgeInfo(modelName, settings, null).label,
  });

  useEffect(() => {
    if (chatId && historyKey) sessionKeyByChatIdRef.current.set(chatId, historyKey);
  }, [chatId, historyKey]);

  useEffect(() => {
    filePreviewWidthRef.current = filePreviewWidth;
  }, [filePreviewWidth]);

  useEffect(() => {
    artifactCanvasWidthRef.current = artifactCanvasWidth;
  }, [artifactCanvasWidth]);

  useEffect(() => {
    if (filePreviewCloseTimerRef.current !== null) {
      window.clearTimeout(filePreviewCloseTimerRef.current);
      filePreviewCloseTimerRef.current = null;
    }
    setFilePreviewClosing(false);
    setFilePreviewPath(null);
    if (artifactCanvasCloseTimerRef.current !== null) {
      window.clearTimeout(artifactCanvasCloseTimerRef.current);
      artifactCanvasCloseTimerRef.current = null;
    }
    setArtifactCanvasClosing(false);
    setArtifactCanvasOpen(false);
    setArtifacts([]);
    setSelectedArtifactId(null);
  }, [historyKey]);

  useEffect(() => {
    return () => {
      if (filePreviewCloseTimerRef.current !== null) {
        window.clearTimeout(filePreviewCloseTimerRef.current);
      }
      if (artifactCanvasCloseTimerRef.current !== null) {
        window.clearTimeout(artifactCanvasCloseTimerRef.current);
      }
    };
  }, []);

  const displayMessages = useMemo(() => projectWebuiThreadMessages(messages), [messages]);
  // Chips in prose carry what the model typed (`schema.sql`); the file_edit
  // rows of this thread carry where the tool wrote it. Resolve through them
  // first, so a file written outside the folder the editor shows, or one of
  // two same-named files, still opens instead of ending as "not on disk".
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const resolveChipPath = useCallback(
    (path: string) => resolveChipPathFromEdits(path, messagesRef.current),
    [],
  );
  const jumpToFoundMessage = useCallback((id: string) => {
    viewportRef.current?.jumpToMessage(id);
  }, []);
  const conversationFind = useConversationFind(displayMessages, jumpToFoundMessage, () => {
    const node = shellRef.current;
    return !node || node.getClientRects().length > 0;
  });
  const filePreviewAvailabilityCache = useMemo(
    () => new Map<string, FilePreviewAvailabilityCacheEntry>(),
    [historyKey, token],
  );
  const filePreviewAvailabilityRevision = displayMessages.length;
  const fileSearchRoot = useMemo(() => {
    const sessionRoot = workspaceScope?.project_path?.trim() || null;
    const codeRoot = readLastDevContext()?.projectPath?.trim() || null;
    return codeRoot || sessionRoot;
  }, [workspaceScope?.project_path]);
  const resolveFilePreviewAvailability = useCallback((rawPath: string) => {
    if (!historyKey) return Promise.resolve(false);
    const path = resolveChipPath(rawPath);
    const cached = filePreviewAvailabilityCache.get(path);
    if (
      cached
      && (cached.available !== false || cached.revision === filePreviewAvailabilityRevision)
    ) {
      return cached.promise;
    }
    const pending = fetchFilePreviewAvailability(
      token,
      historyKey,
      path,
      "",
      fileSearchRoot,
    ).catch(
      (error: unknown) => {
        if (error instanceof ApiError) {
          if (error.status === 404 && /API route not found/i.test(error.message)) {
            return true;
          }
          if ([400, 403, 404, 415].includes(error.status)) return false;
        }
        return false;
      },
    );
    const entry: FilePreviewAvailabilityCacheEntry = {
      promise: pending,
      revision: filePreviewAvailabilityRevision,
    };
    filePreviewAvailabilityCache.set(path, entry);
    void pending.then((available) => {
      if (filePreviewAvailabilityCache.get(path) === entry) {
        entry.available = available;
      }
    });
    return pending;
  }, [
    filePreviewAvailabilityCache,
    filePreviewAvailabilityRevision,
    fileSearchRoot,
    historyKey,
    resolveChipPath,
    token,
  ]);

  const showHeroComposer = messages.length === 0 && !loading;
  // Model pinned to this conversation. A chat that was never pinned opens on
  // the model picked last, so New Chat keeps the same choice without the
  // account default (and therefore other sessions) being touched.
  const [threadModelPreset, setThreadModelPreset] = useState<string | null>(() =>
    (chatId ? loadThreadModelPreset(chatId) : null) ?? loadLastModelPick(),
  );
  const threadModelPresetRef = useRef(threadModelPreset);
  useEffect(() => {
    threadModelPresetRef.current = threadModelPreset;
  }, [threadModelPreset]);
  useEffect(() => {
    setThreadModelPreset((chatId ? loadThreadModelPreset(chatId) : null) ?? loadLastModelPick());
  }, [chatId]);
  const [liveTaskHint, setLiveTaskHint] = useState<{
    modelLabel?: string;
    taskRole?: string;
    routed?: boolean;
  } | null>(null);
  // Route swaps already explained in this window, per chat and task role.
  const announcedRouteSwapsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    setLiveTaskHint(null);
  }, [chatId]);
  useEffect(() => {
    if (!isStreaming) setLiveTaskHint(null);
  }, [isStreaming]);
  // "auto": the user handed this chat back to Task routing. No pin is sent
  // as a model, but the sentinel travels so the gateway drops its sticky pin.
  const threadAutoMode = isAutoModelPreset(threadModelPreset);
  const threadPresetRow = useMemo(() => {
    if (!threadModelPreset || threadAutoMode || !settings) return null;
    const row =
      settings.model_presets.find((preset) => preset.name === threadModelPreset) ?? null;
    // A stale pin can point at a media preset (older builds allowed it);
    // media models can never run the chat turn, so ignore such pins.
    if (row && !canBeChatDefault(row.modality, row.model)) return null;
    if (row && !presetAllowedForBudget(row)) return null;
    return row;
  }, [settings, threadAutoMode, threadModelPreset]);
  // Routes that will swap the default model when this chat has no pin.
  const routingSwaps = useMemo<RouteSwap[]>(() => routeSwaps(settings, t), [settings, t]);
  const composerRouting = useMemo(
    () =>
      routingSwaps.length
        ? {
            auto: !threadPresetRow,
            swaps: routingSwaps,
            onOpenRouting: onOpenModelSettings,
          }
        : null,
    [onOpenModelSettings, routingSwaps, threadPresetRow],
  );
  const modelBadge = useMemo(
    () => toModelBadgeInfo(modelName, settings, threadPresetRow),
    [modelName, settings, threadPresetRow],
  );
  // Stale preset labels (DeepSeek, etc.) stay after disconnect; never show them
  // as a ready model. Composer gets a setup CTA; history keeps the real name.
  const modelBadgeLabel = modelBadge.needsSetup
    ? t("thread.composer.configureModel", { defaultValue: "Configure model" })
    : modelBadge.label
      ?? null;
  const assistantModelName = modelBadge.label ?? "";
  const selectedModelRoute = useMemo<SelectedModelRoute>(
    () => ({ provider: modelBadge.provider, providerLabel: modelBadge.providerLabel }),
    [modelBadge.provider, modelBadge.providerLabel],
  );
  const modelOptions = useMemo<ComposerModelOption[]>(() => {
    if (!settings) return [];
    const activeName = threadPresetRow?.name ?? (settings.agent.model_preset || "default");
    const activeImage = settings.image_generation.model;
    const activeVideo = settings.video_generation?.model;
    const activeAudio = settings.voice?.tts_model;
    const activeMusic = settings.music_generation?.model;
    const activeStt = settings.transcription?.model;
    const providerConfigured = (name: string | null | undefined): boolean => {
      if (!name || name === "auto") return true;
      const row = settings.providers.find((item) => item.name === name);
      return Boolean(row?.configured);
    };
    return settings.model_presets
      .filter((preset) => preset.model)
      .filter((preset) => !preset.routing_only)
      .filter((preset) => preset.enabled !== false || preset.name === activeName)
      .filter((preset) => canBeChatDefault(preset.modality, preset.model))
      .filter((preset) => presetAllowedForBudget(preset))
      .map((preset) => {
        // Stale music/video rows may omit modality and default to "text".
        let modality = preset.modality ?? "text";
        if (!isMediaModality(modality) && isMediaModelSlug(preset.model)) {
          if (preset.model === activeMusic || /lyria/i.test(preset.model)) modality = "music";
          else if (preset.model === activeImage) modality = "image";
          else if (preset.model === activeVideo) modality = "video";
          else if (preset.model === activeAudio) modality = "audio";
          else if (preset.model === activeStt) modality = "stt";
          else if (/veo-|seedance|kling|hailuo/i.test(preset.model)) modality = "video";
          else if (/seedream|flash-image|pro-image/i.test(preset.model)) modality = "image";
          else if (/tts|grok-voice/i.test(preset.model)) modality = "audio";
          else if (/transcribe|asr-|parakeet|nova-3|grok-stt/i.test(preset.model)) {
            modality = "stt";
          } else modality = "music";
        }
        const active =
          modality === "image"
            ? preset.model === activeImage
            : modality === "video"
              ? preset.model === activeVideo
              : modality === "audio"
                ? preset.model === activeAudio
                : modality === "music"
                  ? preset.model === activeMusic
                  : modality === "stt"
                    ? preset.model === activeStt
                    : preset.name === activeName;
        return {
          name: preset.name,
          label: preset.label,
          model: preset.model,
          provider: preset.provider === "auto" ? null : preset.provider,
          active,
          source:
            preset.source
            ?? (preset.locked || preset.provider === "navin" ? "managed" : "byok"),
          modality,
          unitPriceUsd: preset.unit_price_usd ?? null,
          billingUnit: preset.billing_unit ?? null,
          billingRate: preset.billing_rate ?? null,
          // A BYOK preset whose provider has no key cannot run a turn:
          // picking it routes to provider settings instead of becoming
          // the default and bricking the composer.
          needsKey: modality === "text" && !providerConfigured(preset.provider),
          isDefault: Boolean(preset.is_default) || preset.name === "default",
        };
      });
  }, [settings, threadPresetRow]);

  const budgetFilterPercent = useMemo(() => {
    const hidden = (settings?.model_presets ?? []).some(
      (preset) =>
        preset.budget_allowed === false
        && (preset.modality ?? "text") === "text"
        && !preset.routing_only
        && (preset.source === "managed" || preset.provider === "navin"),
    );
    if (!hidden) return null;
    const pct = account?.usage?.used_percent;
    return typeof pct === "number" && pct > 0 ? pct : 80;
  }, [account?.usage?.used_percent, settings]);

  const healAccountChatDefault = useCallback(() => {
    if (!settings || !token) return;
    const grok =
      settings.model_presets.find(
        (preset) =>
          preset.name === DEFAULT_CHAT_PRESET
          || preset.model === DEFAULT_CHAT_MODEL,
      ) ?? null;
    if (!grok?.model || !canBeChatDefault(grok.modality, grok.model)) {
      return;
    }
    // Heal media leftovers only. GLM 5.3 Flash is the chat default and
    // Grok 4.6 the Montage default; neither must be snapped away.
    const globalBad =
      isMediaModelSlug(settings.agent.model)
      || !canBeChatDefault(
        settings.model_presets.find((p) => p.name === settings.agent.model_preset)?.modality,
        settings.agent.model,
      );
    if (!globalBad) return;
    void updateSettings(token, { modelPreset: grok.name })
      .then((next) => setSettings(next))
      .catch((err) => console.error("Failed to restore Grok default", err));
  }, [settings, token]);

  const preferMontageChatModel = useCallback(() => {
    if (!settings) return;
    const grok =
      settings.model_presets.find(
        (preset) =>
          preset.name === MONTAGE_DEFAULT_PRESET
          || preset.model === MONTAGE_DEFAULT_MODEL,
      ) ?? null;
    if (!grok?.model || !canBeChatDefault(grok.modality, grok.model)) return;
    if (threadModelPreset === grok.name) return;
    // Montage only: pin the thread. Never promote Grok to the account default
    // from here - Plus+ already uses it as the catalog default.
    setThreadModelPreset(grok.name);
    if (chatId) saveThreadModelPreset(chatId, grok.name);
  }, [chatId, settings, threadModelPreset]);

  const clearMontageThreadPin = useCallback(() => {
    if (threadModelPreset !== MONTAGE_DEFAULT_PRESET) return;
    setThreadModelPreset(null);
    if (chatId) saveThreadModelPreset(chatId, null);
  }, [chatId, threadModelPreset]);

  const handleComposerTurnModeChange = useCallback(
    (mode: ComposerTurnMode) => {
      const previous = composerTurnMode;
      setComposerTurnMode(mode);
      writeComposerTurnMode(mode);
      if (mode === "montage") {
        preferMontageChatModel();
      } else if (previous === "montage") {
        clearMontageThreadPin();
        healAccountChatDefault();
      }
    },
    [
      clearMontageThreadPin,
      composerTurnMode,
      healAccountChatDefault,
      preferMontageChatModel,
    ],
  );

  useEffect(() => {
    const moduleId = (productModule || "").trim().toLowerCase();
    if (composerTurnMode === "montage" || moduleId === "montage") {
      preferMontageChatModel();
      return;
    }
    healAccountChatDefault();
  }, [
    composerTurnMode,
    healAccountChatDefault,
    preferMontageChatModel,
    productModule,
  ]);

  useEffect(() => {
    if (showHeroComposer) {
      setHeroGreetingKey(randomHeroGreetingKey(productModule));
    }
  }, [showHeroComposer, productModule]);

  const withSendDefaults = useCallback(
    (options?: SendOptions): SendOptions | undefined => {
      const moduleId = (productModule || "").trim().toLowerCase();
      const isCode = moduleId === "code" || moduleId === "dev";
      const openFiles = isCode ? getDevOpenFiles() : [];
      const openFilesOpt = openFiles.length ? openFiles : undefined;
      const modelPreset = threadPresetRow?.name ?? (threadAutoMode ? AUTO_MODEL_PRESET : null);
      if (!workspaceScope && !modelPreset && !productModule && !openFilesOpt) {
        return options;
      }
      return {
        ...(options ?? {}),
        ...(workspaceScope ? { workspaceScope } : {}),
        ...(modelPreset ? { modelPreset } : {}),
        ...(productModule ? { productModule } : {}),
        ...(openFilesOpt ? { openFiles: openFilesOpt } : {}),
      };
    },
    [productModule, threadAutoMode, threadPresetRow, workspaceScope],
  );

  const refreshModelSettings = useCallback(async (opts?: { syncCatalog?: boolean }) => {
    try {
      setSettings(await fetchSettings(token, "", { syncCatalog: opts?.syncCatalog }));
    } catch {
      if (!settingsSnapshot) setSettings(null);
    }
  }, [settingsSnapshot, token]);

  const handleModelPickerOpen = useCallback(
    (open: boolean) => {
      if (open) void refreshModelSettings({ syncCatalog: true });
    },
    [refreshModelSettings],
  );

  const handleModelSelect = useCallback(
    (presetName: string) => {
      if (isAutoModelPreset(presetName)) {
        // Back to Task routing for this chat (and for new chats): the next
        // message carries the sentinel so the gateway forgets its sticky pin.
        setThreadModelPreset(AUTO_MODEL_PRESET);
        if (chatId) saveThreadModelPreset(chatId, AUTO_MODEL_PRESET);
        saveLastModelPick(AUTO_MODEL_PRESET);
        return;
      }
      const preset = settings?.model_presets.find((row) => row.name === presetName);
      let modality = preset?.modality ?? "text";
      if (!token) return;
      if (preset?.model && !isMediaModality(modality) && isMediaModelSlug(preset.model)) {
        // Stale preset without modality - route as media, never chat default.
        if (/lyria/i.test(preset.model)) modality = "music";
        else if (/veo-|seedance|kling|hailuo/i.test(preset.model)) modality = "video";
        else if (/seedream|flash-image|pro-image/i.test(preset.model)) modality = "image";
        else if (/tts|grok-voice/i.test(preset.model)) modality = "audio";
        else if (/transcribe|asr-|parakeet|nova-3|grok-stt/i.test(preset.model)) {
          modality = "stt";
        } else modality = "music";
      }
      if (modality === "text" && preset && !presetAllowedForBudget(preset)) {
        return;
      }

      if (modality === "image" && settings && preset?.model) {
        void updateImageGenerationSettings(token, {
          enabled: settings.image_generation.enabled,
          provider: settings.image_generation.provider || "",
          model: preset.model,
          defaultAspectRatio: settings.image_generation.default_aspect_ratio,
          defaultImageSize: settings.image_generation.default_image_size,
          maxImagesPerTurn: settings.image_generation.max_images_per_turn,
        })
          .then((next) => setSettings(next))
          .catch((err) => {
            console.error("Failed to update image model", err);
          });
        return;
      }

      if (modality === "video" && settings && preset?.model) {
        const video = settings.video_generation;
        if (!video) return;
        void updateVideoGenerationSettings(token, {
          enabled: video.enabled,
          provider: video.provider || "",
          model: preset.model,
          defaultAspectRatio: video.default_aspect_ratio,
          defaultDurationSeconds: video.default_duration_seconds,
          defaultResolution: video.default_resolution,
        })
          .then((next) => setSettings(next))
          .catch((err) => {
            console.error("Failed to update video model", err);
          });
        return;
      }

      if (modality === "audio" && settings && preset?.model) {
        const voice = settings.voice;
        if (!voice) return;
        void updateVoiceSettings(token, {
          ttsProvider: voice.tts_provider || "",
          ttsModel: preset.model,
          voice: voice.voice || "eve",
          autoSpeak: voice.auto_speak,
          responseFormat: voice.response_format,
          realtimeEnabled: voice.realtime_enabled,
        })
          .then((next) => setSettings(next))
          .catch((err) => {
            console.error("Failed to update TTS model", err);
          });
        return;
      }

      if (modality === "music" && settings && preset?.model) {
        const music = settings.music_generation;
        void updateMusicGenerationSettings(token, {
          enabled: music?.enabled ?? true,
          provider: music?.provider || "",
          model: preset.model,
        })
          .then((next) => setSettings(next))
          .catch((err) => {
            console.error("Failed to update music model", err);
          });
        return;
      }

      if (modality === "stt" && settings && preset?.model) {
        const transcription = settings.transcription;
        void updateTranscriptionSettings(token, {
          enabled: transcription?.enabled ?? true,
          provider: transcription?.provider || "",
          model: preset.model,
          language: transcription?.language ?? "",
          maxDurationSec: transcription?.max_duration_sec ?? 120,
          maxUploadMb: transcription?.max_upload_mb ?? 25,
        })
          .then((next) => setSettings(next))
          .catch((err) => {
            console.error("Failed to update transcription model", err);
          });
        return;
      }

      // A media preset must never become the chat runtime (a music model
      // cannot hold a conversation). Anything non-text stops here.
      if (!canBeChatDefault(modality, preset?.model)) return;

      // A provider without a key cannot run the turn: send the user to
      // configure it and keep the current working model untouched.
      const providerName = preset?.provider && preset.provider !== "auto" ? preset.provider : null;
      if (providerName) {
        const row = settings?.providers.find((item) => item.name === providerName);
        if (row && !row.configured) {
          onOpenModelSettings?.();
          return;
        }
      }

      // Text models only: pin to this chat, and remember the pick so the next
      // chat opens on it. The account default stays where Settings → Models
      // left it, otherwise this click would move every other session too.
      setThreadModelPreset(presetName);
      if (chatId) saveThreadModelPreset(chatId, presetName);
      saveLastModelPick(presetName);
    },
    [chatId, onOpenModelSettings, settings, token],
  );

  const handleHideModel = useCallback(
    (presetName: string) => {
      if (!token || !presetName || presetName === "default") return;
      void updateModelConfiguration(token, { name: presetName, enabled: false })
        .then((next) => setSettings(next))
        .catch((err) => {
          console.error("Failed to hide model", err);
        });
    },
    [token],
  );

  const [effortBusy, setEffortBusy] = useState(false);
  const activeEffortPreset = threadPresetRow ?? activeModelPreset(settings);
  const effortValue = activeEffortPreset?.reasoning_effort ?? settings?.agent.reasoning_effort ?? "";
  const effortOptions = useMemo(() => {
    const raw =
      activeEffortPreset?.reasoning_effort_values
      ?? (settings?.agent ? [...DEFAULT_REASONING_EFFORT_VALUES] : null);
    if (!raw || raw.length === 0) return [...DEFAULT_REASONING_EFFORT_VALUES];
    return raw;
  }, [activeEffortPreset, settings]);

  const handleEffortSelect = useCallback(
    async (effort: string) => {
      const presetName = activeEffortPreset?.name;
      if (!presetName || !token) return;
      setEffortBusy(true);
      try {
        // Prefer /settings/update for the active chat model: it updates both
        // agents.defaults and the locked Navin/plan preset. Per-preset updates
        // cover a thread pinned to a non-default model.
        const activeName = settings?.agent.model_preset || "default";
        const next =
          presetName === "default" || presetName === activeName
            ? await updateSettings(token, { reasoningEffort: effort })
            : await updateModelConfiguration(token, {
                name: presetName,
                reasoningEffort: effort,
              });
        setSettings(next);
        onSettingsChange?.(next);
      } catch (err) {
        console.error("Failed to update reasoning effort", err);
      } finally {
        setEffortBusy(false);
      }
    },
    [activeEffortPreset?.name, onSettingsChange, settings?.agent.model_preset, token],
  );

  const handleEffortSelectSync = useCallback(
    (effort: string) => {
      void handleEffortSelect(effort);
    },
    [handleEffortSelect],
  );

  useEffect(() => {
    if (settingsSnapshot) {
      setSettings(settingsSnapshot);
      return;
    }
    void refreshModelSettings();
  }, [refreshModelSettings, settingsSnapshot]);

  useEffect(() => {
    return client.onRuntimeModelUpdate((modelName, modelPreset, meta) => {
      // The frame reaches every window. Another chat's budget swap must not
      // repin this thread onto a model its user never picked.
      if (meta?.chatId && meta.chatId !== "*" && chatId && meta.chatId !== chatId) return;
      const reason = meta?.reason || "";
      if (reason.startsWith("task:")) {
        const role = reason.slice("task:".length).trim();
        if (role) {
          setLiveTaskHint({
            modelLabel: presetLabelForSlug(settings, modelName, modelPreset),
            taskRole: role,
          });
        }
        return;
      }
      if (reason.startsWith("route:")) {
        // Task routing swapped the model shown in the composer for another
        // one. Label the turn, and say so once per chat and role, with the
        // one-click way out (pin the default model here).
        const role = reason.slice("route:".length).trim();
        const toLabel = presetLabelForSlug(settings, modelName, modelPreset);
        if (role) setLiveTaskHint({ modelLabel: toLabel, taskRole: role, routed: true });
        const announceKey = `${chatId ?? "*"}:${role}`;
        if (!role || announcedRouteSwapsRef.current.has(announceKey)) return;
        announcedRouteSwapsRef.current.add(announceKey);
        const defaultRow = activeModelPreset(settings);
        const fromLabel =
          presetLabelForSlug(settings, meta?.previousModel || defaultRow?.model, defaultRow?.name)
          || t("thread.routing.defaultModel", { defaultValue: "your default model" });
        const roleLabel = taskRoleShortLabel(role, t);
        notify({
          level: "info",
          source: "session",
          key: `route-model-swap:${announceKey}`,
          chatId,
          toast: true,
          title: t("thread.routing.switchedTitle", {
            defaultValue: "{{model}} answers this message",
            model: toLabel,
          }),
          detail: t("thread.routing.switchedDetail", {
            defaultValue:
              "Task routing sends \"{{task}}\" requests to {{to}} instead of {{from}}. Pick a model in the composer to lock it for this chat, or change the routes in Settings > Models > Task routing.",
            task: roleLabel,
            to: toLabel,
            from: fromLabel,
          }),
          ...(defaultRow
            ? {
                action: {
                  label: t("thread.routing.keepDefaultHere", {
                    defaultValue: "Always {{model}} in this chat",
                    model: fromLabel,
                  }),
                  run: () => handleModelSelect(defaultRow.name),
                },
              }
            : {}),
        });
        return;
      }
      void refreshModelSettings();
      if (reason !== "budget") return;
      if (modelPreset && chatId) {
        setThreadModelPreset(modelPreset);
        saveThreadModelPreset(chatId, modelPreset);
      }
      const toLabel = presetLabelForSlug(settings, modelName, modelPreset);
      const fromLabel = presetLabelForSlug(settings, meta?.previousModel);
      const percent = meta?.usedPercent ?? budgetFilterPercent ?? 80;
      notify({
        level: "info",
        source: "session",
        key: "budget-model-switch",
        title: t("thread.budget.switchedTitle", {
          defaultValue: "Switched to {{model}}",
          model: toLabel,
        }),
        detail: t("thread.budget.switchedDetail", {
          defaultValue:
            "{{from}} is paused at {{percent}}% monthly usage. This reply uses {{to}}.",
          from: fromLabel || t("thread.budget.previousModel", { defaultValue: "The previous model" }),
          to: toLabel,
          percent,
        }),
      });
    });
  }, [
    budgetFilterPercent,
    chatId,
    client,
    handleModelSelect,
    notify,
    refreshModelSettings,
    settings,
    t,
  ]);

  useEffect(() => {
    if (!chatId || loading) return;
    const cached = messageCacheRef.current.get(chatId);
    const appliedVersion = appliedHistoryVersionRef.current.get(chatId) ?? 0;
    const hasPendingCanonicalHydrate = pendingCanonicalHydrateRef.current.has(chatId);
    const hasNewCanonicalHistory = hasPendingCanonicalHydrate && historyVersion > appliedVersion;
    // When the user switches away and back, keep the local in-memory thread
    // state (including not-yet-persisted messages) instead of replacing it with
    // whatever the history endpoint currently knows about. Once a fresh
    // canonical replay arrives (e.g. after ``session_updated`` refresh), prefer it
    // so rendering converges to the same shape as a manual refresh.
    setMessages((prev) => {
      const normalizedHistory = projectWebuiThreadMessages(historical);
      const keepLiveMessages = (messagesToKeep: UIMessage[]) => {
        const projected = projectWebuiThreadMessages(messagesToKeep);
        messageCacheRef.current.set(chatId, projected);
        return projected;
      };
      if (hasNewCanonicalHistory && historical.length > 0) {
        if (isStaleThreadSnapshot(prev, normalizedHistory)) return keepLiveMessages(prev);
        pendingCanonicalHydrateRef.current.delete(chatId);
        appliedHistoryVersionRef.current.set(chatId, historyVersion);
        messageCacheRef.current.set(chatId, normalizedHistory);
        return normalizedHistory;
      }
      if (cached && cached.length > 0) {
        const normalizedCached = projectWebuiThreadMessages(cached);
        if (
          normalizedHistory.length > normalizedCached.length
          && !isStaleThreadSnapshot(prev, normalizedHistory)
        ) {
          messageCacheRef.current.set(chatId, normalizedHistory);
          appliedHistoryVersionRef.current.set(chatId, historyVersion);
          return normalizedHistory;
        }
        if (isStaleThreadSnapshot(prev, normalizedCached)) return keepLiveMessages(prev);
        return normalizedCached;
      }
      if (isStaleThreadSnapshot(prev, normalizedHistory)) return keepLiveMessages(prev);
      appliedHistoryVersionRef.current.set(chatId, historyVersion);
      if (normalizedHistory.length > 0) messageCacheRef.current.set(chatId, normalizedHistory);
      return normalizedHistory;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, chatId, historical, historyVersion]);

  useEffect(() => {
    if (!chatId) return;
    return client.onSessionUpdate((updatedChatId, scope) => {
      if (updatedChatId !== chatId) return;
      if (scope === "metadata") return;
      viewportRef.current?.cancelAutoScroll();
      pendingCanonicalHydrateRef.current.add(chatId);
      refreshHistory();
    });
  }, [chatId, client, refreshHistory]);

  useEffect(() => {
    if (!chatId) {
      bottomScrolledChatIdRef.current = null;
      return;
    }
    if (loading || bottomScrolledChatIdRef.current === chatId) return;
    bottomScrolledChatIdRef.current = chatId;
    setScrollToBottomSignal((value) => value + 1);
  }, [chatId, loading]);

  useEffect(() => {
    if (chatId) return;
    setMessages(projectWebuiThreadMessages(historical));
  }, [chatId, historical, setMessages]);

  useLayoutEffect(() => {
    if (chatId) {
      const prev = prevChatIdForCacheRef.current;
      if (prev && prev !== chatId) {
        messageCacheRef.current.set(prev, projectWebuiThreadMessages(messages));
        skipLayoutCacheRef.current = true;
      }
      prevChatIdForCacheRef.current = chatId;
    } else {
      if (prevChatIdForCacheRef.current) {
        messageCacheRef.current.set(
          prevChatIdForCacheRef.current,
          projectWebuiThreadMessages(messages),
        );
        skipLayoutCacheRef.current = true;
      }
      prevChatIdForCacheRef.current = null;
    }
  }, [chatId, messages]);

  // Persist thread to in-memory cache after paint so ``useNavinStream``'s chat switch
  // ``useEffect`` reset has flushed; ``skipLayoutCacheRef`` drops the first run that still
  // sees the *previous* chat's ``messages`` (avoids stale rows leaking across sessions).
  useEffect(() => {
    if (!chatId) {
      return;
    }
    if (skipLayoutCacheRef.current) {
      skipLayoutCacheRef.current = false;
      return;
    }
    if (loading) {
      return;
    }
    messageCacheRef.current.set(chatId, projectWebuiThreadMessages(messages));
  }, [chatId, loading, messages]);

  // The landing composer queues the first message while `new_chat` is in flight.
  // Only the chat created for that send may consume it; selecting another chat
  // while creation is pending must not leak the message there.
  useEffect(() => {
    if (!chatId || pendingFirstTargetChatId !== chatId) return;
    const pending = pendingFirstRef.current;
    if (!pending) {
      setPendingFirstTargetChatId(null);
      return;
    }
    pendingFirstRef.current = null;
    setPendingFirstTargetChatId(null);
    setScrollToLatestUserPromptSignal((value) => value + 1);
    send(pending.content, pending.images, pending.options);
    setBooting(false);
  }, [chatId, pendingFirstTargetChatId, send]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const commands = await listSlashCommands(token, "", productModule);
        if (!cancelled) setSlashCommands(commands);
      } catch {
        if (!cancelled) setSlashCommands([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [productModule, token]);

  const handleWelcomeSend = useCallback(
    async (content: string, images?: SendAttachment[], options?: SendOptions) => {
      if (booting || isViewer) return;
      const sendOptions = withSendDefaults(options);
      if (chatId) {
        setScrollToLatestUserPromptSignal((value) => value + 1);
        send(content, images, sendOptions);
        return;
      }
      setBooting(true);
      pendingFirstRef.current = { content, images, options: sendOptions };
      setPendingFirstTargetChatId(null);
      try {
        // Typing the first message on a workbench welcome screen belongs to
        // that workbench; only explicit "New chat" leaves for Tchat.
        const newId = await onCreateChat?.(workspaceScope, { keepWorkbench: true });
        if (!newId) {
          pendingFirstRef.current = null;
          setPendingFirstTargetChatId(null);
          setBooting(false);
          return;
        }
        // A model picked on the landing composer belongs to the chat being born.
        if (threadModelPresetRef.current) {
          saveThreadModelPreset(newId, threadModelPresetRef.current);
        }
        setPendingFirstTargetChatId(newId);
      } catch {
        pendingFirstRef.current = null;
        setPendingFirstTargetChatId(null);
        setBooting(false);
      }
    },
    [booting, chatId, isViewer, onCreateChat, send, withSendDefaults, workspaceScope],
  );

  const handleComposerNewChat = useCallback(() => {
    if (isViewer) return;
    if (onNewChat) {
      onNewChat();
      return;
    }
    void onCreateChat?.(workspaceScope);
  }, [isViewer, onCreateChat, onNewChat, workspaceScope]);

  const handleThreadSend = useCallback(
    (content: string, images?: SendAttachment[], options?: SendOptions) => {
      if (isViewer) return;
      setScrollToLatestUserPromptSignal((value) => value + 1);
      send(content, images, withSendDefaults(options));
    },
    [isViewer, send, withSendDefaults],
  );

  /** Cursor-style Build: flip to Agent, then execute the plan via /forge. */
  const handlePlanBuild = useCallback(
    (plan: SessionPlan) => {
      if (isStreaming || booting || isViewer) return;
      // Same gate as the panel: a plan with steps left can always be
      // (re)launched while idle - including one interrupted by /stop.
      if (plan.complete) return;
      handleComposerTurnModeChange("agent");
      const pending = plan.items
        .filter((item) => !item.done)
        .map((item) => `- ${item.title}`)
        .join("\n");
      const content = pending
        ? `/forge Implement the approved plan.\n\n${pending}`
        : "/forge Implement the approved plan.";
      if (chatId) {
        handleThreadSend(content);
      } else {
        void handleWelcomeSend(content);
      }
    },
    [
      booting,
      chatId,
      handleComposerTurnModeChange,
      handleThreadSend,
      handleWelcomeSend,
      isViewer,
      isStreaming,
    ],
  );

  // One-click project actions (Dev view): programmatic sends routed through the
  // same paths as manual composer submissions.
  const consumedAutoSendNonceRef = useRef(0);
  useEffect(() => {
    if (!autoSendRequest || autoSendRequest.nonce === consumedAutoSendNonceRef.current) {
      return;
    }
    if (isViewer) {
      consumedAutoSendNonceRef.current = autoSendRequest.nonce;
      return;
    }
    // Wait until the agent is idle: the effect re-runs when isStreaming/booting
    // change, so the action is queued instead of silently dropped.
    if (isStreaming || booting) return;
    consumedAutoSendNonceRef.current = autoSendRequest.nonce;
    // Tint composer immediately when Actions send /inspect, /fortify, /debug…
    const inferredMode = modeFromOutgoingContent(autoSendRequest.text);
    if (inferredMode) handleComposerTurnModeChange(inferredMode);
    // Template priority: explicit pick in the studio dialog, then the one
    // attached in the composer - exactly like a manual submission would.
    const attachedTemplate =
      autoSendRequest.documentTemplate ??
      (selectedDocumentTemplate
        ? {
            category: selectedDocumentTemplate.category,
            name: selectedDocumentTemplate.name,
            title: selectedDocumentTemplate.title,
          }
        : null);
    const options: SendOptions | undefined = attachedTemplate
      ? { documentTemplate: attachedTemplate }
      : undefined;
    if (chatId) {
      handleThreadSend(autoSendRequest.text, undefined, options);
    } else {
      void handleWelcomeSend(autoSendRequest.text, undefined, options);
    }
    if (!autoSendRequest.documentTemplate && selectedDocumentTemplate) {
      setSelectedDocumentTemplate(null);
    }
  }, [
    autoSendRequest,
    booting,
    chatId,
    handleComposerTurnModeChange,
    handleThreadSend,
    handleWelcomeSend,
    isViewer,
    isStreaming,
    selectedDocumentTemplate,
  ]);

  // Agent tool set_composer_mode (+ workflow auto-emit): sync menu + accent color.
  useEffect(() => {
    return client.onComposerModeRequest((request) => {
      if (chatId && request.chatId && request.chatId !== chatId) return;
      if (!isComposerTurnMode(request.mode)) return;
      handleComposerTurnModeChange(request.mode);
    });
  }, [chatId, client, handleComposerTurnModeChange]);

  // Parallel spawn subagents: live Cursor-style cards in the transcript.
  // Cleared on chat switch, then refilled by the gateway's replay on attach,
  // so a subagent still working stays visible across a refresh.
  useEffect(() => {
    setSubagentCards([]);
  }, [chatId]);

  useEffect(() => {
    return client.onSubagentProgress((update) => {
      if (chatId && update.chatId && update.chatId !== chatId) return;
      setSubagentCards((prev) => upsertSubagentCard(prev, update));
    });
  }, [chatId, client]);

  // Drop finished cards once they have been readable for a moment, so the
  // panel clears without the outcome flashing past.
  useEffect(() => {
    if (!subagentCards.some((c) => c.done)) return;
    const timer = window.setTimeout(() => {
      setSubagentCards((prev) => pruneFinishedSubagentCards(prev));
    }, FINISHED_CARD_LINGER_MS);
    return () => window.clearTimeout(timer);
  }, [subagentCards]);

  const openFilePreviewPanel = useCallback((path: string) => {
    const trimmed = path.trim();
    if (!trimmed) return;
    if (filePreviewCloseTimerRef.current !== null) {
      window.clearTimeout(filePreviewCloseTimerRef.current);
      filePreviewCloseTimerRef.current = null;
    }
    setFilePreviewClosing(false);
    setFilePreviewPath(trimmed);
  }, []);

  // Handing a file to the Dev editor navigates the whole shell to the Code
  // view. That is only right when Code is already the visible workbench: from
  // a studio (Marketing, Montage, ...) it would replace the studio the user is
  // working in, so media deliverables looked like they "landed in Code".
  const codeWorkbenchVisible =
    workbenchVisible && (productModule || "").trim().toLowerCase() === "code";

  const handleOpenFilePreview = useCallback((rawPath: string) => {
    const path = resolveChipPath(rawPath);
    // Hand off to the workbench editor only when it is actually on screen. From
    // the chat view it is not, so sending the file there read as a dead button:
    // the file landed in an editor the user could not see. There, the chat-side
    // panel is the visible surface, which is what "open beside" means here.
    if (onOpenFileInEditor && codeWorkbenchVisible) {
      setFilePreviewPath(null);
      setFilePreviewClosing(false);
      onOpenFileInEditor(path);
      return;
    }
    openFilePreviewPanel(path);
  }, [codeWorkbenchVisible, onOpenFileInEditor, openFilePreviewPanel, resolveChipPath]);

  // Agent-driven previews (open_file_preview, freshly written HTML reports):
  // in Code they open as a rendered tab in the left workbench; in a studio
  // they open in the chat-side panel so the active studio stays on screen.
  const openAgentPreview = useCallback((rawPath: string) => {
    const path = resolveChipPath(rawPath);
    if (workbenchVisible && !codeWorkbenchVisible) {
      openFilePreviewPanel(path);
      return;
    }
    if (onOpenFileInEditor) {
      // Drop any legacy side panel so results only show on the left.
      setFilePreviewPath(null);
      setFilePreviewClosing(false);
      onOpenFileInEditor(path, { mode: "preview" });
      return;
    }
    openFilePreviewPanel(path);
  }, [codeWorkbenchVisible, onOpenFileInEditor, openFilePreviewPanel, resolveChipPath, workbenchVisible]);

  // Agent ``open_file_preview`` + auto-open studio HTML reports when written.
  useEffect(() => {
    if (!client || !chatId) return;
    const unsubRequest = client.onFilePreviewOpenRequest((request) => {
      if (request.chatId && request.chatId !== chatId) return;
      openAgentPreview(request.path);
    });
    const unsubChat = client.onChat(chatId, (ev) => {
      if (ev.event !== "file_edit") return;
      for (const edit of ev.edits ?? []) {
        if (edit.status !== "done" || edit.operation === "delete") continue;
        const base = edit.path.replace(/\\/g, "/").split("/").pop() ?? "";
        if (
          /^(risklens|seo|marketing|montage|ads|campaign|leads|scrape|review|security|debug)-report-.+\.html$/i.test(
            base,
          )
        ) {
          openAgentPreview(edit.path);
          break;
        }
      }
    });
    return () => {
      unsubRequest();
      unsubChat();
    };
  }, [chatId, client, openAgentPreview]);

  // Artifact Canvas: present_artifact + hydrate-on-attach + auto-detect fences.
  useEffect(() => {
    if (!client || !chatId) return;
    const unsubUpsert = client.onArtifactUpsert((payload) => {
      if (payload.chatId && payload.chatId !== chatId) return;
      const next = payload.artifact;
      setArtifacts((prev) => {
        const idx = prev.findIndex((item) => item.id === next.id);
        if (idx < 0) return [...prev, next];
        const copy = prev.slice();
        copy[idx] = { ...copy[idx], ...next };
        return copy;
      });
      // Restored artifacts rebuild the tab strip but must not pop the canvas
      // open: reattaching to a chat (switching workspace, reconnecting) is not
      // a reason to cover the conversation with a panel the user had closed.
      if (payload.restored) {
        setSelectedArtifactId((current) => current ?? next.id);
        return;
      }
      if (artifactCanvasCloseTimerRef.current !== null) {
        window.clearTimeout(artifactCanvasCloseTimerRef.current);
        artifactCanvasCloseTimerRef.current = null;
      }
      setArtifactCanvasClosing(false);
      setArtifactCanvasOpen(true);
      setSelectedArtifactId((current) => current ?? next.id);
    });
    const unsubSelect = client.onArtifactSelect((payload) => {
      if (payload.chatId && payload.chatId !== chatId) return;
      setSelectedArtifactId(payload.artifactId);
      if (artifactCanvasCloseTimerRef.current !== null) {
        window.clearTimeout(artifactCanvasCloseTimerRef.current);
        artifactCanvasCloseTimerRef.current = null;
      }
      setArtifactCanvasClosing(false);
      setArtifactCanvasOpen(true);
    });
    return () => {
      unsubUpsert();
      unsubSelect();
    };
  }, [chatId, client]);

  // In Code, a result the editor is already rendering must not be rendered a
  // second time beside it. Everywhere else this is a no-op: only the Code
  // workbench publishes open tabs, so the list is empty and nothing is
  // dropped.
  const devOpenFiles = useSyncExternalStore(subscribeDevOpenFiles, getDevOpenFileEntries);
  const canvasArtifacts = useMemo(
    () => artifactsNotInEditor(artifacts, devOpenFiles),
    [artifacts, devOpenFiles],
  );

  const artifactPlacement = resolveArtifactPlacement({
    open: artifactCanvasOpen,
    artifactCount: canvasArtifacts.length,
    hasWorkbenchHost: Boolean(artifactCanvasHost),
  });

  const handleCloseArtifactCanvas = useCallback(() => {
    if (!artifactCanvasOpen || artifactCanvasClosing) return;
    setArtifactCanvasClosing(true);
    artifactCanvasCloseTimerRef.current = window.setTimeout(() => {
      artifactCanvasCloseTimerRef.current = null;
      setArtifactCanvasOpen(false);
      setArtifactCanvasClosing(false);
    }, ARTIFACT_CANVAS_CLOSE_ANIMATION_MS);
  }, [artifactCanvasClosing, artifactCanvasOpen]);

  const handleArtifactCanvasResizeStart = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const panel = event.currentTarget.closest<HTMLElement>("[data-artifact-canvas-panel]");
    const shellRect = shellRef.current?.getBoundingClientRect();
    const rightEdge = shellRect?.right ?? window.innerWidth;
    const maxWidth = maxArtifactCanvasWidth(shellRect?.width ?? window.innerWidth);
    const originalBodyCursor = document.body.style.cursor;
    const originalBodyUserSelect = document.body.style.userSelect;
    const originalPanelTransition = panel?.style.transition ?? "";
    let nextWidth = artifactCanvasWidthRef.current;
    let frame: number | null = null;

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    if (panel) panel.style.transition = "none";

    const applyWidth = (clientX: number) => {
      nextWidth = clampArtifactCanvasWidth(rightEdge - clientX, maxWidth);
      artifactCanvasWidthRef.current = nextWidth;
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        panel?.style.setProperty("--artifact-canvas-width", `${nextWidth}px`);
        panel?.style.setProperty("--artifact-canvas-slot-width", `${nextWidth}px`);
      });
    };
    const handlePointerMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      applyWidth(moveEvent.clientX);
    };
    const handlePointerUp = () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      panel?.style.setProperty("--artifact-canvas-width", `${nextWidth}px`);
      panel?.style.setProperty("--artifact-canvas-slot-width", `${nextWidth}px`);
      if (panel) panel.style.transition = originalPanelTransition;
      setArtifactCanvasWidth(nextWidth);
      document.body.style.cursor = originalBodyCursor;
      document.body.style.userSelect = originalBodyUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }, []);

  const fileWorkspaceActions = useMemo(
    () => (
      historyKey
        ? {
          sessionKey: historyKey,
          token,
          projectPath: fileSearchRoot,
          onOpenPreview: handleOpenFilePreview,
          resolvePath: resolveChipPath,
        }
        : null
    ),
    [fileSearchRoot, handleOpenFilePreview, historyKey, resolveChipPath, token],
  );

  const handleCloseFilePreview = useCallback(() => {
    if (!filePreviewPath || filePreviewClosing) return;
    setFilePreviewClosing(true);
    filePreviewCloseTimerRef.current = window.setTimeout(() => {
      filePreviewCloseTimerRef.current = null;
      setFilePreviewPath(null);
      setFilePreviewClosing(false);
    }, FILE_PREVIEW_CLOSE_ANIMATION_MS);
  }, [filePreviewClosing, filePreviewPath]);

  const handleFilePreviewResizeStart = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const panel = event.currentTarget.closest<HTMLElement>("[data-file-preview-panel]");
    const shellRect = shellRef.current?.getBoundingClientRect();
    const rightEdge = shellRect?.right ?? window.innerWidth;
    const maxWidth = maxFilePreviewWidth(shellRect?.width ?? window.innerWidth);
    const originalBodyCursor = document.body.style.cursor;
    const originalBodyUserSelect = document.body.style.userSelect;
    const originalPanelTransition = panel?.style.transition ?? "";
    let nextWidth = filePreviewWidthRef.current;
    let frame: number | null = null;

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    if (panel) panel.style.transition = "none";

    const applyWidth = (clientX: number) => {
      nextWidth = clampFilePreviewWidth(rightEdge - clientX, maxWidth);
      filePreviewWidthRef.current = nextWidth;
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        panel?.style.setProperty("--file-preview-width", `${nextWidth}px`);
        panel?.style.setProperty("--file-preview-slot-width", `${nextWidth}px`);
      });
    };
    const handlePointerMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      applyWidth(moveEvent.clientX);
    };
    const handlePointerUp = () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      panel?.style.setProperty("--file-preview-width", `${nextWidth}px`);
      panel?.style.setProperty("--file-preview-slot-width", `${nextWidth}px`);
      if (panel) panel.style.transition = originalPanelTransition;
      setFilePreviewWidth(nextWidth);
      document.body.style.cursor = originalBodyCursor;
      document.body.style.userSelect = originalBodyUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };

    applyWidth(event.clientX);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
  }, []);

  useEffect(() => {
    if (!filePreviewPath) return;
    const clampToShell = () => {
      const shellWidth = shellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      const maxWidth = maxFilePreviewWidth(shellWidth);
      const nextWidth = clampFilePreviewWidth(filePreviewWidthRef.current, maxWidth);
      filePreviewWidthRef.current = nextWidth;
      setFilePreviewWidth(nextWidth);
    };
    clampToShell();
    window.addEventListener("resize", clampToShell);
    return () => {
      window.removeEventListener("resize", clampToShell);
    };
  }, [filePreviewPath]);

  // The canvas opens at 520px inside a chat column that is 497px by default, and
  // it is shrink-0 while the message list is flex-1 min-w-0: without this the
  // conversation is squeezed to zero and the canvas looks like it replaced the
  // chat. The file preview above has always clamped on open; this one only
  // clamped while dragging its handle, which is the one moment it was already
  // wide enough to be visible.
  useEffect(() => {
    if (!artifactCanvasOpen) return;
    const clampToShell = () => {
      const shellWidth = shellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      const maxWidth = maxArtifactCanvasWidth(shellWidth);
      const nextWidth = clampArtifactCanvasWidth(artifactCanvasWidthRef.current, maxWidth);
      artifactCanvasWidthRef.current = nextWidth;
      setArtifactCanvasWidth(nextWidth);
    };
    clampToShell();
    window.addEventListener("resize", clampToShell);
    return () => {
      window.removeEventListener("resize", clampToShell);
    };
  }, [artifactCanvasOpen]);

  const handleForkFromMessage = useCallback(
    async (beforeUserIndex: number) => {
      if (!chatId || !onForkChat) return;
      const forkedChatId = await onForkChat(chatId, beforeUserIndex);
      if (!forkedChatId) return;
      messageCacheRef.current.delete(forkedChatId);
      appliedHistoryVersionRef.current.delete(forkedChatId);
      pendingCanonicalHydrateRef.current.add(forkedChatId);
    },
    [chatId, onForkChat],
  );

  const handleRevertResubmit = useCallback(
    async (beforeUserIndex: number, content: string) => {
      if (!onRevertResubmit) return;
      await onRevertResubmit(beforeUserIndex, content);
    },
    [onRevertResubmit],
  );

  // Latest tool action of the run, surfaced in the plan panel so the current
  // step shows what is actually executing instead of a bare title.
  const livePlanActivity = useMemo(() => {
    if (!isStreaming) return null;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (message.role === "user") break;
      const events = message.toolEvents;
      if (events?.length) {
        for (let j = events.length - 1; j >= 0; j -= 1) {
          const event = events[j];
          const label = typeof event.label === "string" ? event.label.trim() : "";
          if (label) return label;
          const name = typeof event.name === "string" ? event.name.trim() : "";
          if (!name) continue;
          const formatted = formatToolCallTrace(event);
          if (formatted) return truncateMiddle(formatted, 140);
          return name;
        }
      }
      if (message.kind === "trace" && message.content?.trim()) {
        return truncateMiddle(message.content.trim(), 140);
      }
    }
    return null;
  }, [isStreaming, messages]);

  const [planAnchorMessageCount, setPlanAnchorMessageCount] = useState<number | null>(null);
  const planAnchorCandidateRef = useRef(0);
  planAnchorCandidateRef.current = displayMessages.length;
  const planWasStreamingRef = useRef(isStreaming);
  useEffect(() => {
    if (planWasStreamingRef.current === isStreaming) return;
    planWasStreamingRef.current = isStreaming;
    // Live: no anchor, so the card trails the thread. Finished: pin it here.
    setPlanAnchorMessageCount(isStreaming ? null : planAnchorCandidateRef.current);
  }, [isStreaming]);
  useEffect(() => {
    setPlanAnchorMessageCount(null);
  }, [historyKey]);

  // In the conversation flow (after the last message), not stacked above the
  // composer: the plan and live execution read as part of the exchange,
  // Cursor-style, and scroll away with it instead of walling off the input.
  const transcriptTail = (
    <>
      <ParallelSubagentsPanel cards={subagentCards} />
      <TaskProgressStrip progress={isStreaming ? activeTaskProgress : null} />
    </>
  );

  // The plan follows the end of the thread while the run is live, so Stop and
  // the live activity stay under the reader's eye. The moment the run ends its
  // anchor freezes and the card belongs to that turn: the next exchange pushes
  // it up the transcript instead of dragging it along above the composer.
  const planBlock = (
    <ThreadPlanPanel
      sessionKey={historyKey}
      activity={livePlanActivity}
      isStreaming={isStreaming}
      onBuild={handlePlanBuild}
      onStop={stop}
    />
  );

  const pendingReview = usePendingReview(historyKey);
  // Chat workspace first. Last Code folder is only a fallback when this
  // chat has no project yet, so file-space edits still have a root.
  const proveHref = codePanelHref("evolve", historyKey);
  const proveChange = useProveChange(
    proveProjectPath(
      workspaceScope?.project_path,
      readLastDevContext()?.projectPath,
    ),
    {
      onStarted: () => {
        window.location.hash = proveHref;
      },
    },
  );

  // Without the thread header (workbench layouts) the session loops control
  // sits in the composer, right after attach and document template, sized like
  // those 28px tool buttons.
  const composerSessionAction = hideHeader && historyKey ? (
    <SessionInfoPopover
      sessionKey={historyKey}
      token={token}
      title={title}
      productModule={productModule}
      onCreateLoop={handleCreateLoop}
      compact
      triggerClassName="h-7 w-7 shrink-0 rounded-md text-muted-foreground hover:bg-muted/65 hover:text-foreground [&>svg]:h-3.5 [&>svg]:w-3.5"
    />
  ) : undefined;

  const composer = (
    <>
      <ConnectionStatusBanner />
      {streamError ? (
        <StreamErrorNotice
          error={streamError}
          onDismiss={dismissStreamError}
        />
      ) : null}
      {/* Agent edits waiting for a decision, right where the user answers:
          one collapsed row by default, the file list on demand. */}
      {/* Oldest first, and one at a time: each card holds a suspended tool call,
          and answering the wrong one of a stack is worse than answering slowly. */}
      {!isViewer && pendingApprovals.length > 0 ? (
        <ApprovalPrompt
          request={pendingApprovals[0]}
          queue={pendingApprovals}
          onRespond={respondToApproval}
        />
      ) : null}
      {!isViewer && pendingChoices.length > 0 ? (
        <ChoicePrompt request={pendingChoices[0]} onRespond={respondToChoice} />
      ) : null}
      {session && chatId ? (
        <div
          className={
            showHeroComposer
              ? "relative mx-auto w-full max-w-[58rem]"
              : "relative mx-auto w-full max-w-[49.5rem]"
          }
        >
          {!isViewer ? (
            <PendingReviewPanel
              changes={pendingReview.changes}
              busy={pendingReview.busy}
              error={pendingReview.error}
              onAction={pendingReview.apply}
              onOpenFile={(path) => {
                if (onOpenFileInEditor && codeWorkbenchVisible) {
                  setFilePreviewPath(null);
                  setFilePreviewClosing(false);
                  onOpenFileInEditor(path, { mode: "diff" });
                  return;
                }
                handleOpenFilePreview(path);
              }}
              tucked
              prove={proveChange}
              proveHref={proveHref}
            />
          ) : null}
          <div className="relative z-10">
        <ThreadComposer
          key={chatId}
          onSend={handleThreadSend}
          disabled={isViewer}
          isStreaming={isStreaming}
          placeholder={
            showHeroComposer
              ? t("thread.composer.placeholderHero")
              : t("thread.composer.placeholderThread")
          }
          modelLabel={modelBadgeLabel}
          modelProvider={modelBadge.provider}
          modelProviderLabel={modelBadge.providerLabel}
          modelNeedsSetup={modelBadge.needsSetup}
          onModelBadgeClick={onOpenModelSettings}
          modelOptions={modelOptions}
          budgetFilterPercent={budgetFilterPercent}
          onModelSelect={handleModelSelect}
          modelRouting={composerRouting}
          onHideModel={handleHideModel}
          onModelPickerOpenChange={handleModelPickerOpen}
          effortValue={effortValue ?? ""}
          effortOptions={effortOptions}
          onEffortSelect={handleEffortSelectSync}
          effortBusy={effortBusy}
          variant={showHeroComposer ? "hero" : "thread"}
          slashCommands={slashCommands}
          cliApps={cliApps}
          mcpPresets={mcpPresets}
          documentTemplates={documentTemplates}
          selectedDocumentTemplate={selectedDocumentTemplate}
          onSelectDocumentTemplate={setSelectedDocumentTemplate}
          sessionAction={composerSessionAction}
          skills={skills}
          onStop={stop}
          onTranscribeAudio={transcribeAudio}
          runStartedAt={runStartedAt}
          goalState={goalState}
          workspaceScope={workspaceScope}
          workspaceDefaultScope={workspaceDefaultScope}
          workspaceControls={workspaceControls}
          workspaceScopeDisabled={workspaceScopeDisabled}
          workspaceError={workspaceError}
          onWorkspaceScopeChange={onWorkspaceScopeChange}
          onNewChat={handleComposerNewChat}
          pendingQueueKey={chatId}
          transcriptionProvider={settingsSnapshot?.transcription?.provider}
          ingressLimits={ingressLimits}
          sessionKey={historyKey}
          seed={effectiveComposerSeed}
          onSeedConsumed={handleComposerSeedConsumed}
          turnMode={composerTurnMode}
          onTurnModeChange={handleComposerTurnModeChange}
        />
          </div>
        </div>
      ) : (
        <ThreadComposer
          key={historyKey ?? "welcome"}
          onSend={handleWelcomeSend}
          disabled={isViewer}
          isStreaming={isStreaming}
          placeholder={
            booting
              ? t("thread.composer.placeholderOpening")
              : t("thread.composer.placeholderHero")
          }
          modelLabel={modelBadgeLabel}
          modelProvider={modelBadge.provider}
          modelProviderLabel={modelBadge.providerLabel}
          modelNeedsSetup={modelBadge.needsSetup}
          onModelBadgeClick={onOpenModelSettings}
          modelOptions={modelOptions}
          budgetFilterPercent={budgetFilterPercent}
          onModelSelect={handleModelSelect}
          modelRouting={composerRouting}
          onHideModel={handleHideModel}
          onModelPickerOpenChange={handleModelPickerOpen}
          effortValue={effortValue ?? ""}
          effortOptions={effortOptions}
          onEffortSelect={handleEffortSelectSync}
          effortBusy={effortBusy}
          variant="hero"
          slashCommands={slashCommands}
          cliApps={cliApps}
          mcpPresets={mcpPresets}
          documentTemplates={documentTemplates}
          selectedDocumentTemplate={selectedDocumentTemplate}
          onSelectDocumentTemplate={setSelectedDocumentTemplate}
          sessionAction={composerSessionAction}
          skills={skills}
          runStartedAt={runStartedAt}
          onTranscribeAudio={transcribeAudio}
          goalState={goalState}
          workspaceScope={workspaceScope}
          workspaceDefaultScope={workspaceDefaultScope}
          workspaceControls={workspaceControls}
          workspaceScopeDisabled={workspaceScopeDisabled}
          workspaceError={workspaceError}
          onWorkspaceScopeChange={onWorkspaceScopeChange}
          onNewChat={handleComposerNewChat}
          transcriptionProvider={settingsSnapshot?.transcription?.provider}
          ingressLimits={ingressLimits}
          sessionKey={historyKey}
          seed={effectiveComposerSeed}
          onSeedConsumed={handleComposerSeedConsumed}
          turnMode={composerTurnMode}
          onTurnModeChange={handleComposerTurnModeChange}
        />
      )}
    </>
  );

  const emptyState = loading ? (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      {t("thread.loadingConversation")}
    </div>
  ) : (
    <div className="flex w-full flex-col items-center text-center animate-in fade-in-0 slide-in-from-bottom-2 duration-500">
      <h1 className="max-w-[44rem] text-balance text-[34px] font-normal leading-[1.08] tracking-normal text-foreground sm:text-[48px] sm:leading-tight">
        {t(heroGreetingKey)}
      </h1>
    </div>
  );
  const sessionInfoAction = historyKey ? (
    <SessionInfoPopover
      sessionKey={historyKey}
      token={token}
      title={title}
      productModule={productModule}
      onCreateLoop={handleCreateLoop}
    />
  ) : undefined;
  const promptNavigatorAction = historyKey ? (
    <PromptNavigator
      messages={displayMessages}
      onJumpToPrompt={(promptId) => viewportRef.current?.jumpToUserPrompt(promptId)}
    />
  ) : undefined;

  return (
    <section ref={shellRef} className="relative flex min-h-0 flex-1 overflow-hidden">
      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        {!hideHeader ? (
          <ThreadHeader
            title={title}
            onToggleSidebar={onToggleSidebar}
            hideSidebarToggleForHostChrome={hideSidebarToggleForHostChrome}
            hostChromeTitleInset={hostChromeTitleInset}
            minimal={!session && !loading}
            promptNavigatorAction={promptNavigatorAction}
            sessionInfoAction={sessionInfoAction}
            findAction={
              session ? (
                conversationFind.open ? (
                  <ConversationFindBar
                    compact
                    query={conversationFind.query}
                    onQuery={conversationFind.setQuery}
                    index={conversationFind.index}
                    total={conversationFind.total}
                    onPrev={conversationFind.goPrev}
                    onNext={conversationFind.goNext}
                    onClose={conversationFind.closeFind}
                    inputRef={conversationFind.inputRef}
                  />
                ) : (
                  <button
                    type="button"
                    onClick={conversationFind.openFind}
                    className="host-no-drag inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground/80 hover:bg-accent/40 hover:text-foreground"
                    aria-label={t("thread.find.placeholder", {
                      defaultValue: "Search in conversation",
                    })}
                  >
                    <Search className="h-4 w-4" />
                  </button>
                )
              ) : undefined
            }
            leadingActions={headerLeadingActions}
            presenceAction={
              presenceMembers.length > 0 ? (
                <PresenceAvatars members={presenceMembers} />
              ) : undefined
            }
          />
        ) : null}
        {isViewer ? <ViewerReadOnlyBanner /> : null}
        <FileWorkspaceActionsProvider value={fileWorkspaceActions}>
          <FilePreviewAvailabilityProvider
            resolve={historyKey ? resolveFilePreviewAvailability : undefined}
          >
            <ThreadViewport
              ref={viewportRef}
              messages={displayMessages}
              isStreaming={isStreaming}
              assistantName={assistantModelName}
              selectedModelRoute={selectedModelRoute}
              liveTaskHint={liveTaskHint ?? undefined}
              emptyState={emptyState}
              composer={composer}
              transcriptTail={transcriptTail}
              pinnedBlock={planBlock}
              pinnedBlockAfterMessageCount={planAnchorMessageCount}
              scrollToBottomSignal={scrollToBottomSignal}
              scrollToLatestUserPromptSignal={scrollToLatestUserPromptSignal}
              conversationKey={historyKey ?? chatId}
              showScrollToBottomButton={!!session}
              cliApps={cliApps}
              mcpPresets={mcpPresets}
              slashCommands={slashCommands}
              forkBoundaryMessageCount={forkBoundaryMessageCount}
              contextCompaction={contextCompaction}
              checkpointNotice={checkpointNotice}
              hasMoreBefore={hasMoreBefore}
              loadingOlder={loadingOlder}
              userMessageOffset={userMessageOffset}
              onLoadOlder={loadOlder}
              onOpenFilePreview={historyKey ? handleOpenFilePreview : undefined}
              onForkFromMessage={onForkChat ? handleForkFromMessage : undefined}
              onRevertResubmit={onRevertResubmit ? handleRevertResubmit : undefined}
            />
          </FilePreviewAvailabilityProvider>
        </FileWorkspaceActionsProvider>
      </div>
      {filePreviewPath && historyKey ? (
        <FilePreviewPanel
          sessionKey={historyKey}
          path={filePreviewPath}
          token={token}
          projectRoot={fileSearchRoot}
          desktopWidth={filePreviewWidth}
          isClosing={filePreviewClosing}
          onResizeStart={handleFilePreviewResizeStart}
          onClose={handleCloseFilePreview}
        />
      ) : null}
      {artifactPlacement !== "hidden"
        ? artifactPlacement === "workbench" && artifactCanvasHost
          // Beside a workbench: render into the centre area, full size. The
          // host is click-through until this lands in it.
          ? createPortal(
              <div className="flex min-h-0 flex-1 flex-col">
                <ArtifactCanvas
                  artifacts={canvasArtifacts}
                  selectedId={selectedArtifactId}
                  isClosing={artifactCanvasClosing}
                  fill
                  onSelect={setSelectedArtifactId}
                  onClose={handleCloseArtifactCanvas}
                />
              </div>,
              artifactCanvasHost,
            )
          : (
            // Plain chat view: no workbench to render into, so the panel keeps
            // its resizable side-by-side behaviour.
            <ArtifactCanvas
              artifacts={canvasArtifacts}
              selectedId={selectedArtifactId}
              desktopWidth={artifactCanvasWidth}
              isClosing={artifactCanvasClosing}
              onSelect={setSelectedArtifactId}
              onClose={handleCloseArtifactCanvas}
              onResizeStart={handleArtifactCanvasResizeStart}
            />
          )
        : null}
    </section>
  );
}
