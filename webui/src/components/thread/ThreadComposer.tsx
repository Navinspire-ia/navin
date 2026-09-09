// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

import { MarkdownText, preloadMarkdownText } from "@/components/MarkdownText";
import {
  CliAppMentionToken,
  FileMentionToken,
  McpPresetMentionToken,
  cliAppInitials,
  mcpPresetInitials,
  mentionToken,
  splitCapabilityMentionSegments,
  type CapabilityMentionSegment,
} from "@/components/CliAppMentionText";
import { INLINE_TOKEN_HIGHLIGHT_COLOR } from "@/components/InlineTokenHighlight";
import {
  Activity,
  ArrowUp,
  AudioLines,
  BadgeDollarSign,
  BarChart3,
  BookOpen,
  Brain,
  Bug,
  Check,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Clapperboard,
  CornerDownLeft,
  Ellipsis,
  EyeOff,
  FileAudio,
  FileText,
  FileVideo,
  Flag,
  Gauge,
  Hammer,
  History,
  ImageIcon,
  KeyRound,
  ListPlus,
  Loader2,
  Waypoints,
  Megaphone,
  Map as MapIcon,
  Mic,
  Package,
  Paperclip,
  RotateCw,
  Route,
  Search,
  SearchCode,
  Shield,
  ShieldCheck,
  Smartphone,
  Square,
  SquarePen,
  Target,
  Trash2,
  TrendingUp,
  Undo2,
  Users,
  Wrench,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { FileTypeIcon, FolderTypeIcon } from "@/components/dev/FileTypeIcon";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  applyComposerTurnMode,
  ComposerModeMenu,
  composerModeAccent,
  isSimpleStartNowPrompt,
  modeFromOutgoingContent,
  type ComposerTurnMode,
} from "@/components/thread/ComposerModeMenu";
import {
  DocumentTemplateChip,
  DocumentTemplatePicker,
} from "@/components/thread/DocumentTemplatePicker";
import { commitOnPointerDown, commitOnSelect } from "@/components/thread/menuChoice";
import {
  WorkspaceProjectPicker,
  workspaceProjectPickerVisible,
} from "@/components/thread/WorkspaceControls";
import {
  DEFAULT_REASONING_EFFORT_VALUES,
  reasoningEffortShortLabel,
} from "@/lib/reasoning-effort";
import {
  ACCEPT_ATTR,
  MAX_ATTACHMENTS_PER_MESSAGE,
  isAudioAttachment,
  isVideoAttachment,
  useAttachedImages,
  type AttachedImage,
  type AttachmentError,
  type RestoredReadyImage,
} from "@/hooks/useAttachedImages";
import { useClipboardAndDrop } from "@/hooks/useClipboardAndDrop";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import type { SendAttachment, SendOptions } from "@/hooks/useNavinStream";
import { useVoiceRecorder, type VoiceRecorderErrorKey } from "@/hooks/useVoiceRecorder";
import type { LiveVoiceState } from "@/lib/live-voice";
import type {
  CliAppInfo,
  GoalStateWsPayload,
  McpPresetInfo,
  DocumentTemplateInfo,
  OutboundCliAppMention,
  OutboundFileMention,
  OutboundMcpPresetMention,
  ProjectFileMatch,
  SlashCommand,
  SkillSummary,
  WebUIIngressLimits,
  WorkspaceScopePayload,
  WorkspacesPayload,
} from "@/lib/types";
import { ApiError, fetchProjectFiles, spawnMultitaskPrompt } from "@/lib/api";
import { fetchReusableMediaFile, onMediaReuse } from "@/lib/media-reuse";
import { useClient } from "@/providers/ClientProvider";
import {
  inferProviderFromModelName,
  logoFallbackUrls,
  providerBrand,
} from "@/lib/provider-brand";
import {
  QUEUED_PROMPTS_LIMIT,
  failedMultitaskTargets,
  firstMultitaskError,
  planMultitaskDispatch,
  queuedPromptAttachmentCount,
  queuedPromptIsMultitaskable,
  queuedPromptIsSendable,
  queuedPromptLabel,
  queuedPromptsStorageKey,
  readQueuedPrompts,
  reorderQueuedPrompts,
  restoreFailedQueuedPrompts,
  shiftQueuedPrompt,
  storeQueuedPrompts,
  type QueuedPrompt,
  type QueuedPromptImage,
  type QueuedPromptOptions,
} from "@/lib/queued-prompts";
import {
  clearComposerDraft,
  readComposerDraft,
  writeComposerDraft,
} from "@/lib/composer-draft";
import {
  isSideChannelLifecycle,
  slashCommandLifecycle,
} from "@/lib/slash-command";
import { composerPrimaryAction } from "@/lib/composer-primary-action";
import { fitTextareaHeight, observeTextareaWidth } from "@/lib/composer-textarea-height";
import { cn } from "@/lib/utils";
import {
  canHidePickerOption,
  isVisionChatModel,
  modalityBadgeClass,
  modalityBadgeLabel,
  visiblePickerOptions,
  visionBadgeClass,
  visionBadgeLabel,
} from "@/lib/model-modality";
import { bindMenuListWheel, modelPickerListMaxPx } from "@/lib/model-picker-scroll";
import { AUTO_MODEL_PRESET } from "@/lib/model-routing-summary";

const VOICE_SHORTCUT_CODE = "KeyD";
const VOICE_SHORTCUT_ARIA = "Control+Shift+D";
type VoiceShortcutPlatform = "apple" | "chromeos" | "linux" | "other" | "windows";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function utf8Bytes(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function isVoiceShortcutDown(event: KeyboardEvent): boolean {
  return (
    event.code === VOICE_SHORTCUT_CODE
    && event.ctrlKey
    && event.shiftKey
    && !event.altKey
    && !event.metaKey
  );
}

function isVoiceShortcutRelease(event: KeyboardEvent): boolean {
  return (
    event.code === VOICE_SHORTCUT_CODE
    || event.key === "Control"
    || event.key === "Shift"
  );
}

function getVoiceShortcutPlatform(): VoiceShortcutPlatform {
  if (typeof navigator === "undefined") return "other";
  const userAgentData = (navigator as Navigator & { userAgentData?: { platform?: string } })
    .userAgentData;
  const platform = [
    userAgentData?.platform,
    navigator.platform,
    navigator.userAgent,
  ].filter(Boolean).join(" ").toLowerCase();
  const isIpadPretendingToBeMac =
    navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  if (isIpadPretendingToBeMac || /mac|iphone|ipad|ipod/.test(platform)) return "apple";
  if (/win/.test(platform)) return "windows";
  if (/cros/.test(platform)) return "chromeos";
  if (/linux|x11|android/.test(platform)) return "linux";
  return "other";
}

function getVoiceShortcutLabel(): string {
  switch (getVoiceShortcutPlatform()) {
    case "apple":
      return "⌃⇧D";
    case "chromeos":
    case "linux":
    case "windows":
    case "other":
      return "Ctrl ⇧ D";
  }
}

export interface ComposerModelOption {
  /** Model preset name ("default" for the implicit default). */
  name: string;
  label: string;
  model: string;
  provider: string | null;
  active: boolean;
  /** Managed Navin catalog vs user BYOK / custom preset. */
  source?: "managed" | "byok";
  modality?: "text" | "image" | "video" | "audio" | "music" | "stt";
  unitPriceUsd?: number | null;
  billingUnit?: string | null;
  billingRate?: number | null;
  /** Provider has no API key: picking opens provider settings instead. */
  needsKey?: boolean;
  /** Account default preset: cannot be hidden from the picker. */
  isDefault?: boolean;
}

/**
 * Task routing as it applies to this chat. `auto` is true when no model is
 * pinned, so the routes in `swaps` may replace the default model per task.
 */
export interface ComposerModelRouting {
  auto: boolean;
  swaps: Array<{ role: string; roleLabel: string; presetName: string; modelLabel: string }>;
  onOpenRouting?: () => void;
}

interface ThreadComposerProps {
  onSend: (content: string, images?: SendAttachment[], options?: SendOptions) => void;
  disabled?: boolean;
  placeholder?: string;
  /**
   * Text to drop into the input without sending.
   * Default appends so @file mentions keep what the user already typed.
   * `replace` overwrites the draft (Studio section changes).
   * `files` registers the paths named in `text` as real attachments.
   */
  seed?: {
    text: string;
    nonce: number;
    files?: ProjectFileMatch[];
    replace?: boolean;
    mediaTemplate?: { id: string; title?: string; kind?: string; format?: string };
    /** Full reference selection (max 6); replaces the current one. */
    mediaTemplates?: { id: string; title?: string; kind?: string; format?: string }[];
    localFiles?: File[];
  } | null;
  /**
   * Fired once a seed has landed in the input. The owner must drop the seed
   * from its state: this composer remounts when an empty chat becomes a real
   * thread, and a still-present seed would be re-applied by the new instance
   * (the sent prompt reappearing in the input).
   */
  onSeedConsumed?: (nonce: number) => void;
  isStreaming?: boolean;
  modelLabel?: string | null;
  modelProvider?: string | null;
  modelProviderLabel?: string | null;
  modelNeedsSetup?: boolean;
  onModelBadgeClick?: () => void;
  /** Configured model presets; when provided the badge becomes a picker. */
  modelOptions?: ComposerModelOption[];
  /** Monthly usage % when plan models were hidden from this picker. */
  budgetFilterPercent?: number | null;
  onModelSelect?: (presetName: string) => void;
  /** Task routing state for this chat; null when no route changes the model. */
  modelRouting?: ComposerModelRouting | null;
  /** Hide a preset from this picker (same as Settings "Hide in chat"). */
  onHideModel?: (presetName: string) => void;
  /** Fired when the model dropdown opens/closes (sync catalog on open). */
  onModelPickerOpenChange?: (open: boolean) => void;
  /** Thinking / reasoning effort for the active model (Cursor-style). */
  effortValue?: string;
  effortOptions?: string[];
  onEffortSelect?: (effort: string) => void;
  effortBusy?: boolean;
  variant?: "thread" | "hero";
  slashCommands?: SlashCommand[];
  cliApps?: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  documentTemplates?: DocumentTemplateInfo[];
  selectedDocumentTemplate?: DocumentTemplateInfo | null;
  onSelectDocumentTemplate?: (template: DocumentTemplateInfo | null) => void;
  /**
   * Session-level control (session loops) rendered right after the attach and
   * document-template buttons, for layouts without a thread header.
   */
  sessionAction?: ReactNode;
  skills?: SkillSummary[];
  onStop?: () => void;
  onTranscribeAudio?: (dataUrl: string, options?: { durationMs?: number }) => Promise<string>;
  /**
   * Live voice conversation toggle (listen + speak with the agent). Owned by
   * ThreadShell, which also renders the status bar above this composer.
   */
  liveVoice?: { state: LiveVoiceState; toggle: () => void; disabled?: boolean } | null;
  /** Unix seconds from server; turn elapsed timer above input while set. */
  runStartedAt?: number | null;
  /** Sustained objective for this chat (WebSocket ``goal_state``). */
  goalState?: GoalStateWsPayload;
  workspaceScope?: WorkspaceScopePayload | null;
  workspaceDefaultScope?: WorkspaceScopePayload | null;
  workspaceControls?: WorkspacesPayload["controls"] | null;
  workspaceScopeDisabled?: boolean;
  workspaceError?: string | null;
  onWorkspaceScopeChange?: (scope: WorkspaceScopePayload) => void;
  onNewChat?: () => void;
  pendingQueueKey?: string | null;
  transcriptionProvider?: string | null;
  ingressLimits?: WebUIIngressLimits | null;
  /** Session key (``websocket:<chatId>``) used to resolve @file mentions. */
  sessionKey?: string | null;
  /** Controlled Plan / Agent mode (owned by ThreadShell for Build handoff). */
  turnMode?: ComposerTurnMode;
  onTurnModeChange?: (mode: ComposerTurnMode) => void;
}

const COMMAND_ICONS: Record<string, LucideIcon> = {
  activity: Activity,
  "badge-dollar-sign": BadgeDollarSign,
  "bar-chart-3": BarChart3,
  "book-open": BookOpen,
  brain: Brain,
  bug: Bug,
  "circle-help": CircleHelp,
  "file-text": FileText,
  flag: Flag,
  gauge: Gauge,
  hammer: Hammer,
  history: History,
  map: MapIcon,
  megaphone: Megaphone,
  clapperboard: Clapperboard,
  package: Package,
  "rotate-cw": RotateCw,
  route: Route,
  "search-code": SearchCode,
  shield: Shield,
  "shield-check": ShieldCheck,
  smartphone: Smartphone,
  sparkles: Zap,
  square: Square,
  "square-pen": SquarePen,
  target: Target,
  mic: Mic,
  "trending-up": TrendingUp,
  "undo-2": Undo2,
  users: Users,
  wrench: Wrench,
  zap: Zap,
};

const MENTION_CANDIDATE_LIMIT = 8;
const FILE_MENTION_CANDIDATE_LIMIT = 6;
const FILE_MENTION_DEBOUNCE_MS = 120;

const SLASH_PALETTE_GAP_PX = 8;
const SLASH_PALETTE_MAX_HEIGHT_PX = 288;
const SLASH_PALETTE_MIN_HEIGHT_PX = 144;
const SLASH_PALETTE_CHROME_PX = 12;
const SLASH_RECENTS_STORAGE_KEY = "navin.webui.slashCommandRecents";
const SLASH_RECENTS_LIMIT = 5;
const QUEUED_PROMPT_FLUSH_DELAY_MS = 150;

function VoiceRecordingMeter({
  ariaLabel,
  className,
  elapsedLabel,
  isHero,
  levels,
}: {
  ariaLabel: string;
  className?: string;
  elapsedLabel: string;
  isHero: boolean;
  levels: number[];
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-2 text-neutral-700 dark:text-white",
        isHero ? "h-8" : "h-9",
        className,
      )}
      aria-live="polite"
      aria-label={ariaLabel}
    >
      <span className="flex h-5 min-w-0 flex-1 items-center justify-between overflow-hidden" aria-hidden>
        {levels.map((height, index) => (
          <span
            key={index}
            className="w-[2px] rounded-full bg-current opacity-85 transition-[height] duration-75 ease-linear motion-reduce:transition-none"
            style={{ height }}
          />
        ))}
      </span>
      <span className="min-w-[2.1rem] text-right text-[12px] font-medium tabular-nums text-muted-foreground">
        {elapsedLabel}
      </span>
    </div>
  );
}

type SlashPalettePlacement = "above" | "below";

interface SlashPaletteLayout {
  placement: SlashPalettePlacement;
  maxHeight: number;
}

interface CliAppMentionQuery {
  query: string;
  start: number;
  end: number;
}

type MentionCandidate =
  | { kind: "cli"; name: string; app: CliAppInfo }
  | { kind: "mcp"; name: string; preset: McpPresetInfo }
  | { kind: "file"; name: string; file: ProjectFileMatch };

interface SlashPaletteCommand {
  command: string;
  title: string;
  description: string;
  icon: string;
  argHint?: string;
  detail: string;
  badge?: string;
  recent: boolean;
}

function slashCommandI18nKey(command: string): string {
  return command.replace(/^\//, "").replace(/-/g, "_");
}

function readSlashRecents(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(SLASH_RECENTS_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string").slice(0, SLASH_RECENTS_LIMIT)
      : [];
  } catch {
    return [];
  }
}

function storeSlashRecents(commands: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      SLASH_RECENTS_STORAGE_KEY,
      JSON.stringify(commands.slice(0, SLASH_RECENTS_LIMIT)),
    );
  } catch {
    // localStorage may be unavailable in private contexts; command insertion still works.
  }
}

function readyImagesToQueuedImages(
  images: Array<AttachedImage & { dataUrl: string }>,
): QueuedPromptImage[] {
  return images.map((img) => ({
    dataUrl: img.dataUrl,
    kind: img.kind,
    name: img.file.name,
  }));
}

function queuedImagesToSendImages(images?: QueuedPromptImage[]): SendAttachment[] | undefined {
  if (!images?.length) return undefined;
  return images.map((img) => ({
    media: {
      data_url: img.dataUrl,
      ...(img.name ? { name: img.name } : {}),
    },
    preview: {
      kind: img.kind ?? (img.dataUrl.startsWith("data:image/") ? "image" : "file"),
      url: img.dataUrl,
      ...(img.name ? { name: img.name } : {}),
    },
  }));
}

function suppressNativeDragPreview(dataTransfer: DataTransfer): void {
  if (typeof document === "undefined" || typeof dataTransfer.setDragImage !== "function") {
    return;
  }
  const ghost = document.createElement("div");
  ghost.style.position = "fixed";
  ghost.style.left = "-9999px";
  ghost.style.top = "-9999px";
  ghost.style.width = "1px";
  ghost.style.height = "1px";
  ghost.style.opacity = "0";
  document.body.appendChild(ghost);
  try {
    dataTransfer.setDragImage(ghost, 0, 0);
  } catch {
    ghost.remove();
    return;
  }
  window.setTimeout(() => ghost.remove(), 0);
}

function visualViewportBounds(): { top: number; bottom: number; height: number } {
  const viewport = window.visualViewport;
  if (!viewport) {
    return { top: 0, bottom: window.innerHeight, height: window.innerHeight };
  }
  const top = Math.max(0, viewport.offsetTop);
  const height = Math.max(0, viewport.height);
  return { top, bottom: top + height, height };
}

function getVisibleBounds(el: HTMLElement): { top: number; bottom: number } {
  const viewport = visualViewportBounds();
  let top = viewport.top;
  let bottom = viewport.bottom;
  let parent = el.parentElement;

  while (parent) {
    const style = window.getComputedStyle(parent);
    if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
      const rect = parent.getBoundingClientRect();
      top = Math.max(top, rect.top);
      bottom = Math.min(bottom, rect.bottom);
    }
    parent = parent.parentElement;
  }

  return { top, bottom };
}

function goalStateStripPreview(
  goal: GoalStateWsPayload | undefined,
  t: (key: string) => string,
): string | null {
  if (!goal?.active) return null;
  const summary = goal.ui_summary?.trim();
  if (summary) return summary;
  const obj = goal.objective?.trim();
  if (obj) return obj.length > 72 ? `${obj.slice(0, 72)}…` : obj;
  return t("thread.composer.goalStateFallback");
}

const GOAL_PANEL_VIEWPORT_TOP_PAD = 20;
const GOAL_PANEL_GAP_ABOVE_STRIP_PX = 10;
const GOAL_PANEL_MIN_HEIGHT_PX = 112;
const GOAL_PANEL_MAX_VIEWPORT_RATIO = 0.62;

function measureGoalPanelMaxCssHeight(stripTopY: number): number {
  const viewport = visualViewportBounds();
  const spaceAboveStrip =
    stripTopY - viewport.top - GOAL_PANEL_VIEWPORT_TOP_PAD - GOAL_PANEL_GAP_ABOVE_STRIP_PX;
  return Math.min(
    Math.max(spaceAboveStrip, GOAL_PANEL_MIN_HEIGHT_PX),
    Math.floor(viewport.height * GOAL_PANEL_MAX_VIEWPORT_RATIO),
  );
}

function buildGoalMarkdownBody(summary: string, objective: string): string {
  const s = summary.trim();
  const o = objective.trim();
  if (s && o) return `${s}\n\n---\n\n${o}`;
  return o || s;
}

function cliAppMentionPayload(app: CliAppInfo): OutboundCliAppMention {
  return {
    name: app.name,
    display_name: app.display_name,
    category: app.category,
    entry_point: app.entry_point,
    logo_url: app.logo_url ?? null,
    brand_color: app.brand_color ?? null,
  };
}

function fileMentionPayload(file: ProjectFileMatch): OutboundFileMention {
  if (file.kind !== "symbol") return { path: file.path, kind: file.kind };
  return { path: file.path, kind: "symbol", name: file.name, line: file.line ?? 0 };
}

function mcpPresetMentionPayload(preset: McpPresetInfo): OutboundMcpPresetMention {
  return {
    name: preset.name,
    display_name: preset.display_name,
    category: preset.category,
    transport: preset.transport,
    status: preset.status,
    configured: preset.configured,
    logo_url: preset.logo_url ?? null,
    brand_color: preset.brand_color ?? null,
  };
}

function RunElapsedStrip({
  goalState,
}: {
  startedAt: number | null;
  goalState?: GoalStateWsPayload;
}) {
  const { t } = useTranslation();
  const [goalPanelOpen, setGoalPanelOpen] = useState(false);
  const stripLabel = goalStateStripPreview(goalState, t);
  const showGoal = !!stripLabel?.trim();
  // Elapsed "Running · 2s" stays out of the chat chrome. The turn still
  // streams, tools still run, Stop still works - only that status copy is hidden.
  const active = showGoal;
  const [renderStrip, setRenderStrip] = useState(active);
  const [leaving, setLeaving] = useState(false);
  const stripWrapperRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const expandToggleRef = useRef<HTMLButtonElement>(null);
  const stripSnapshotRef = useRef<{
    goalState?: GoalStateWsPayload;
    stripLabel: string | null;
  } | null>(null);
  const [panelMaxPx, setPanelMaxPx] = useState(280);

  if (active) {
    stripSnapshotRef.current = { goalState, stripLabel };
  }

  useEffect(() => {
    if (active) {
      setRenderStrip(true);
      setLeaving(false);
      return;
    }
    setGoalPanelOpen(false);
    if (!renderStrip) return;
    setLeaving(true);
    const id = window.setTimeout(() => {
      setRenderStrip(false);
      setLeaving(false);
    }, 180);
    return () => window.clearTimeout(id);
  }, [active, renderStrip]);

  const display = active
    ? { goalState, stripLabel }
    : stripSnapshotRef.current;
  const displayGoalState = display?.goalState;
  const displayStripLabel = display?.stripLabel ?? null;
  const displayShowGoal = !!displayStripLabel?.trim();

  const objectiveFull = displayGoalState?.objective?.trim() ?? "";
  const summaryFull = displayGoalState?.ui_summary?.trim() ?? "";
  const canExpandGoal = !!(active && displayGoalState?.active && (objectiveFull || summaryFull));

  const markdownBody =
    objectiveFull || summaryFull
      ? buildGoalMarkdownBody(summaryFull, objectiveFull)
      : "";

  useLayoutEffect(() => {
    if (!goalPanelOpen) return;

    function relayout(): void {
      const el = stripWrapperRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      setPanelMaxPx(measureGoalPanelMaxCssHeight(top));
    }

    relayout();

    preloadMarkdownText();
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => relayout())
        : null;
    if (stripWrapperRef.current && ro) {
      ro.observe(stripWrapperRef.current);
    }
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", relayout);
    viewport?.addEventListener("scroll", relayout);
    window.addEventListener("resize", relayout);
    window.addEventListener("scroll", relayout, true);
    return () => {
      ro?.disconnect();
      viewport?.removeEventListener("resize", relayout);
      viewport?.removeEventListener("scroll", relayout);
      window.removeEventListener("resize", relayout);
      window.removeEventListener("scroll", relayout, true);
    };
  }, [goalPanelOpen]);

  useEffect(() => {
    if (!goalPanelOpen) return;

    function onPointerDown(ev: MouseEvent): void {
      const target = ev.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      if (expandToggleRef.current?.contains(target)) return;
      setGoalPanelOpen(false);
    }

    function onKey(ev: KeyboardEvent): void {
      if (ev.key === "Escape") setGoalPanelOpen(false);
    }

    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [goalPanelOpen]);

  if (!renderStrip || !display) return null;

  const ariaLabel = displayShowGoal ? displayStripLabel ?? "" : "";

  return (
    <div
      ref={stripWrapperRef}
      className="composer-status-strip relative z-30"
      data-state={leaving ? "exit" : "enter"}
    >
      {goalPanelOpen && canExpandGoal && markdownBody ? (
        <div
          ref={panelRef}
          id="navin-goal-panel-root"
          role="dialog"
          aria-modal="false"
          aria-labelledby="navin-goal-panel-title"
          tabIndex={-1}
          className={cn(
            "absolute bottom-[calc(100%+8px)] left-3 right-3 z-[50] flex max-w-none flex-col overflow-hidden",
            "rounded-2xl border border-black/[0.08] bg-card shadow-[0_12px_40px_rgba(15,23,42,0.14)]",
            "backdrop-blur-sm dark:border-white/[0.1] dark:shadow-[0_16px_48px_rgba(0,0,0,0.45)]",
          )}
          style={{ maxHeight: `${Math.round(panelMaxPx)}px` }}
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-black/[0.06] px-3 py-2 dark:border-white/[0.08]">
            <h2
              id="navin-goal-panel-title"
              className="min-w-0 truncate text-[13px] font-semibold tracking-tight text-foreground"
            >
              {t("thread.composer.goalStateSheetTitle")}
            </h2>
            <button
              type="button"
              className={cn(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                "text-muted-foreground transition-colors hover:bg-muted/65 hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
              aria-label={t("thread.composer.goalStateCloseAria")}
              onClick={() => setGoalPanelOpen(false)}
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
          <div
            id="navin-goal-panel-scroll"
            className="min-h-0 flex-1 overflow-y-auto scrollbar-thin px-3 pb-3 pt-2"
          >
            <MarkdownText className="max-w-none text-[13.5px] leading-relaxed text-foreground/90">
              {markdownBody}
            </MarkdownText>
          </div>
        </div>
      ) : null}
      <div
        className="flex min-h-[36px] items-center gap-2 border-b border-black/[0.04] px-3 py-2 dark:border-white/[0.06]"
        role="status"
        aria-label={ariaLabel}
      >
        <Target className="h-4 w-4 shrink-0 text-primary/75" aria-hidden />
        <span className="flex min-w-0 flex-1 items-center gap-1.5 text-[12px] font-medium text-foreground/75">
          {displayShowGoal ? (
            <span className="truncate">
              {t("thread.composer.goalStateStrip", { label: displayStripLabel })}
            </span>
          ) : null}
        </span>
        {canExpandGoal ? (
          <button
            ref={expandToggleRef}
            type="button"
            className={cn(
              "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
              "text-muted-foreground transition-colors hover:bg-muted/55 hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
            aria-expanded={goalPanelOpen}
            aria-controls={goalPanelOpen ? "navin-goal-panel-root" : undefined}
            aria-label={t("thread.composer.goalStateExpandAria")}
            title={t("thread.composer.goalStateExpandAria")}
            onClick={() => setGoalPanelOpen((o) => !o)}
          >
            {goalPanelOpen ? (
              <ChevronDown className="h-4 w-4" aria-hidden />
            ) : (
              <ChevronUp className="h-4 w-4" aria-hidden />
            )}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ThreadComposerImpl({
  onSend,
  disabled,
  placeholder,
  seed = null,
  onSeedConsumed,
  isStreaming = false,
  modelLabel = null,
  modelProvider = null,
  modelProviderLabel = null,
  modelNeedsSetup = false,
  onModelBadgeClick,
  modelOptions = [],
  budgetFilterPercent = null,
  onModelSelect,
  modelRouting = null,
  onHideModel,
  onModelPickerOpenChange,
  effortValue = "",
  effortOptions = [],
  onEffortSelect,
  variant = "thread",
  slashCommands = [],
  cliApps = [],
  mcpPresets = [],
  documentTemplates = [],
  selectedDocumentTemplate = null,
  onSelectDocumentTemplate,
  sessionAction,
  skills = [],
  onStop,
  onTranscribeAudio,
  liveVoice = null,
  runStartedAt = null,
  goalState,
  workspaceScope = null,
  workspaceDefaultScope = null,
  workspaceControls = null,
  workspaceScopeDisabled = false,
  workspaceError = null,
  onWorkspaceScopeChange,
  onNewChat,
  pendingQueueKey = null,
  transcriptionProvider = null,
  ingressLimits = null,
  sessionKey = null,
  turnMode: turnModeProp,
  onTurnModeChange,
}: ThreadComposerProps) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [internalTurnMode, setInternalTurnMode] =
    useState<ComposerTurnMode>("agent");
  const turnMode = turnModeProp ?? internalTurnMode;
  const setTurnMode = useCallback(
    (mode: ComposerTurnMode) => {
      if (onTurnModeChange) onTurnModeChange(mode);
      else setInternalTurnMode(mode);
    },
    [onTurnModeChange],
  );
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [slashMenuDismissed, setSlashMenuDismissed] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [cliAppMenuDismissed, setCliAppMenuDismissed] = useState(false);
  const [selectedCliAppIndex, setSelectedCliAppIndex] = useState(0);
  const [cursorPosition, setCursorPosition] = useState(0);
  const [recentSlashCommands, setRecentSlashCommands] = useState<string[]>(() => readSlashRecents());
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);
  const [documentLanguage, setDocumentLanguage] = useState("");
  const [selectedMediaTemplates, setSelectedMediaTemplates] = useState<
    { id: string; title?: string; kind?: string; format?: string }[]
  >([]);
  const setSelectedDocumentTemplate = useCallback(
    (template: DocumentTemplateInfo | null) => {
      if (
        !template
        || template.category !== selectedDocumentTemplate?.category
        || template.name !== selectedDocumentTemplate?.name
      ) {
        setDocumentLanguage("");
      }
      onSelectDocumentTemplate?.(template);
    },
    [onSelectDocumentTemplate, selectedDocumentTemplate],
  );
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** Side-panel chats (code / studio) are often ~500px - densify like Cursor. */
  const [toolbarCompact, setToolbarCompact] = useState(false);
  const chipRefs = useRef(new Map<string, HTMLButtonElement>());
  const queuedPromptCounterRef = useRef(0);
  // Only the prompt queued by the immediately preceding Enter can use the second-Enter shortcut.
  const secondEnterPromptIdRef = useRef<string | null>(null);
  const draggedQueuedPromptIdRef = useRef<string | null>(null);
  const previousPendingQueueKeyRef = useRef(pendingQueueKey);
  const wasStreamingRef = useRef(isStreaming);
  const skipNextQueuedFlushRef = useRef(false);
  const multitaskInFlightRef = useRef(false);
  const skipQueuedPromptPersistRef = useRef(false);
  const voiceShortcutDownRef = useRef(false);
  const isHero = variant === "hero";
  useEffect(() => {
    const el = formRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const update = () => setToolbarCompact(el.clientWidth < 460);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const voiceShortcutLabel = useMemo(getVoiceShortcutLabel, []);
  const queuedPromptStorageKey = useMemo(
    () => queuedPromptsStorageKey(pendingQueueKey),
    [pendingQueueKey],
  );
  useEffect(() => {
    secondEnterPromptIdRef.current = null;
    skipQueuedPromptPersistRef.current = true;
    setQueuedPrompts(queuedPromptStorageKey ? readQueuedPrompts(queuedPromptStorageKey) : []);
  }, [queuedPromptStorageKey]);

  useEffect(() => {
    if (!queuedPromptStorageKey) return;
    if (skipQueuedPromptPersistRef.current) {
      skipQueuedPromptPersistRef.current = false;
      return;
    }
    storeQueuedPrompts(queuedPromptStorageKey, queuedPrompts);
  }, [queuedPromptStorageKey, queuedPrompts]);

  const resolvedPlaceholder = isStreaming
    ? t("thread.composer.placeholderStreaming")
    : placeholder ?? t("thread.composer.placeholderThread");

  const maxAttachments = ingressLimits?.attachments.max_count
    ?? MAX_ATTACHMENTS_PER_MESSAGE;
  const maxTextBytes = ingressLimits?.message.max_text_bytes ?? 64 * 1024;
  const { images, enqueue, remove, clear, restoreReadyImages, encoding, full } =
    useAttachedImages({ ingressLimits });

  const formatRejection = useCallback(
    (reason: AttachmentError): string => {
      const key = `thread.composer.imageRejected.${reason}`;
      const fallback = reason === "too_many_attachments"
        ? `Max ${maxAttachments} attachments per message`
        : reason === "empty_file"
          ? "Empty files cannot be attached"
          : reason === "total_too_large"
            ? "Attachments are too large together - remove some or use smaller files"
            : reason === "transport_too_large"
              ? "This attachment would exceed the gateway transport limit"
              : reason === "too_large"
                ? "File is too large"
                : "Unsupported file type";
      return t(key, { max: maxAttachments, defaultValue: fallback });
    },
    [maxAttachments, t],
  );

  const textTooLargeMessage = useCallback(
    () => t("thread.composer.textTooLarge", {
      max: formatBytes(maxTextBytes),
      defaultValue: `Message text is too large (max ${formatBytes(maxTextBytes)})`,
    }),
    [maxTextBytes, t],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      secondEnterPromptIdRef.current = null;
      const { rejected } = enqueue(files);
      if (rejected.length > 0) {
        setInlineError(formatRejection(rejected[0].reason));
      } else {
        setInlineError(null);
      }
    },
    [enqueue, formatRejection],
  );

  const {
    isDragging,
    onPaste,
    onDragEnter,
    onDragOver,
    onDragLeave,
    onDrop,
  } = useClipboardAndDrop(addFiles);

  // A media tile elsewhere in the transcript asked to reuse an image the agent
  // already delivered. Re-reading the bytes turns it into an ordinary
  // attachment, so limits, encoding, and the chip row all apply unchanged.
  useEffect(() => onMediaReuse((request) => {
    void (async () => {
      try {
        addFiles([await fetchReusableMediaFile(request)]);
      } catch {
        setInlineError(t("thread.composer.mediaReuseFailed", {
          defaultValue: "Could not attach this image - try downloading it instead",
        }));
      }
    })();
  }), [addFiles, t]);

  useEffect(() => {
    if (disabled) return;
    const el = textareaRef.current;
    if (!el) return;
    const id = requestAnimationFrame(() => el.focus());
    return () => cancelAnimationFrame(id);
  }, [disabled, sessionKey]);

  const readyImages = useMemo(
    () => images.filter((img): img is AttachedImage & { dataUrl: string } =>
      img.status === "ready" && typeof img.dataUrl === "string",
    ),
    [images],
  );
  const hasErrors = images.some((img) => img.status === "error");

  const hasComposerContent = value.trim().length > 0 || readyImages.length > 0;
  const canSend =
    !disabled
    && !modelNeedsSetup
    && !encoding
    && !hasErrors
    && hasComposerContent;
  const canOpenModelSettings = Boolean(modelNeedsSetup && onModelBadgeClick && !disabled);
  const canQueueGuidance =
    isStreaming
    && !disabled
    && !modelNeedsSetup
    && !encoding
    && !hasErrors
    && hasComposerContent
    && !value.trimStart().startsWith("/");

  const slashQuery = useMemo(() => {
    if (disabled || slashMenuDismissed || !value.startsWith("/")) return null;
    const commandToken = value.slice(1);
    if (/\s/.test(commandToken)) return null;
    return commandToken.toLowerCase();
  }, [disabled, slashMenuDismissed, value]);

  const skillQuery = useMemo(() => {
    if (disabled || slashMenuDismissed) return null;
    const caret = Math.min(Math.max(cursorPosition, 0), value.length);
    const beforeCaret = value.slice(0, caret);
    const match = /\$([A-Za-z0-9_-]*)$/i.exec(beforeCaret);
    if (!match) return null;
    return {
      end: caret,
      start: match.index,
      text: match[1].toLowerCase(),
    };
  }, [cursorPosition, disabled, slashMenuDismissed, value]);

  const visibleSlashCommands = useMemo(() => {
    if (!(isStreaming && onStop)) return slashCommands;
    const stopCommand = slashCommands.find((command) => command.command === "/stop");
    if (!stopCommand) return slashCommands;
    return [
      stopCommand,
      ...slashCommands.filter((command) => command.command !== "/stop"),
    ];
  }, [isStreaming, onStop, slashCommands]);

  const filteredSlashCommands = useMemo<SlashPaletteCommand[]>(() => {
    if (skillQuery !== null) {
      const query = skillQuery.text;
      return skills
        .filter((skill) => skill.available)
        .filter((skill) => {
          const haystack = [
            skill.name,
            skill.description,
          ].join(" ").toLowerCase();
          return haystack.includes(query);
        })
        .map((skill) => {
          const command = `$${skill.name}`;
          const description = skill.description || skill.name;
          return {
            command,
            title: skill.name,
            description,
            detail: description,
            icon: "brain",
            recent: recentSlashCommands.includes(command),
          };
        })
        .slice(0, 8);
    }
    if (slashQuery === null) return [];
    const withDetails = visibleSlashCommands
      .filter((command) => {
        if (
          slashQuery === ""
          && (
            command.command === "/restart"
            || (command.command === "/stop" && !(isStreaming && onStop))
          )
        ) {
          return false;
        }
        const commandKey = slashCommandI18nKey(command.command);
        const title = t(`thread.composer.slash.commands.${commandKey}.title`, {
          defaultValue: command.title,
        });
        const description = t(`thread.composer.slash.commands.${commandKey}.description`, {
          defaultValue: command.description,
        });
        const haystack = [
          command.command,
          command.title,
          command.description,
          command.argHint ?? "",
          title,
          description,
        ].join(" ").toLowerCase();
        return haystack.includes(slashQuery);
      })
      .map((command) => {
        const commandKey = slashCommandI18nKey(command.command);
        const description = t(`thread.composer.slash.commands.${commandKey}.description`, {
          defaultValue: command.description,
        });
        let detail = description;
        let badge: string | undefined;
        if (command.command === "/model" && modelLabel) {
          detail = modelLabel;
          badge = t("thread.composer.slash.badges.current");
        } else if (command.command === "/goal") {
          detail = goalState?.active
            ? t("thread.composer.slash.details.goalActive")
            : t("thread.composer.slash.details.goalReady");
        } else if (command.command === "/stop" && isStreaming) {
          detail = t("thread.composer.slash.details.stopRunning");
        } else if (command.command === "/history") {
          detail = t("thread.composer.slash.details.history");
        }
        return {
          ...command,
          detail,
          badge,
          recent: recentSlashCommands.includes(command.command),
        };
      })
      .sort((a, b) => {
        if (isStreaming) {
          if (a.command === "/stop") return -1;
          if (b.command === "/stop") return 1;
        }
        if (slashQuery !== "") return 0;
        const aRecent = recentSlashCommands.indexOf(a.command);
        const bRecent = recentSlashCommands.indexOf(b.command);
        if (aRecent !== -1 || bRecent !== -1) {
          if (aRecent === -1) return 1;
          if (bRecent === -1) return -1;
          return aRecent - bRecent;
        }
        return 0;
      });

    return withDetails
      .slice(0, 8);
  }, [goalState?.active, isStreaming, modelLabel, recentSlashCommands, skills, skillQuery, slashQuery, t, visibleSlashCommands]);

  const showSlashMenu = filteredSlashCommands.length > 0;
  const cliAppMention = useMemo<CliAppMentionQuery | null>(() => {
    if (disabled || cliAppMenuDismissed) return null;
    const caret = Math.min(Math.max(cursorPosition, 0), value.length);
    const beforeCaret = value.slice(0, caret);
    // Path characters are accepted so "@src/lib/api.ts" stays one mention
    // instead of ending at the first separator. The boundary set mirrors
    // MENTION_TOKEN_RE so anything offered here also renders as a mention.
    const match = /(?:^|[\s([{,;])@([A-Za-z0-9_./-]*)$/.exec(beforeCaret);
    if (!match) return null;
    const raw = match[1];
    return {
      query: raw.toLowerCase(),
      start: caret - raw.length - 1,
      end: caret,
    };
  }, [cliAppMenuDismissed, cursorPosition, disabled, value]);

  const { token: clientToken } = useClient();

  const [fileMatches, setFileMatches] = useState<ProjectFileMatch[]>([]);
  const fileMatchCacheRef = useRef<Map<string, ProjectFileMatch[]>>(new Map());
  const mentionQuery = cliAppMention?.query ?? null;

  // Files come from the server, so the lookup is debounced and cached by
  // query: a mention is typed one keystroke at a time.
  useEffect(() => {
    if (!clientToken || !sessionKey || !mentionQuery) {
      setFileMatches([]);
      return;
    }
    const cached = fileMatchCacheRef.current.get(mentionQuery);
    if (cached) {
      setFileMatches(cached);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void fetchProjectFiles(clientToken, sessionKey, mentionQuery, {
        limit: FILE_MENTION_CANDIDATE_LIMIT,
      })
        .then((payload) => {
          const items = payload.items ?? [];
          if (fileMatchCacheRef.current.size > 200) fileMatchCacheRef.current.clear();
          fileMatchCacheRef.current.set(mentionQuery, items);
          if (!cancelled) setFileMatches(items);
        })
        .catch(() => {
          // File mentions are an enhancement: on failure the palette simply
          // keeps offering apps and presets.
          if (!cancelled) setFileMatches([]);
        });
    }, FILE_MENTION_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [clientToken, mentionQuery, sessionKey]);

  const filteredMentionCandidates = useMemo<MentionCandidate[]>(() => {
    if (!cliAppMention) return [];
    const cliCandidates: MentionCandidate[] = cliApps
      .filter((app) => app.installed)
      .filter((app) => {
        const haystack = [
          app.name,
          app.display_name,
          app.category,
          app.description,
          app.entry_point,
        ].join(" ").toLowerCase();
        return haystack.includes(cliAppMention.query);
      })
      .map((app) => ({ kind: "cli", name: app.name, app }));
    const mcpCandidates: MentionCandidate[] = mcpPresets
      .filter((preset) => preset.installed && preset.configured)
      .filter((preset) => {
        const haystack = [
          preset.name,
          preset.display_name,
          preset.category,
          preset.description,
          preset.transport,
        ].join(" ").toLowerCase();
        return haystack.includes(cliAppMention.query);
      })
      .map((preset) => ({ kind: "mcp", name: preset.name, preset }));
    const fileCandidates: MentionCandidate[] = fileMatches.map((file) => ({
      kind: "file",
      name: mentionToken(file),
      file,
    }));
    // A query carrying a separator or an extension is unambiguously a path, so
    // files lead; otherwise capabilities keep their existing precedence.
    const looksLikePath = /[./]/.test(cliAppMention.query);
    const ordered = looksLikePath
      ? [...fileCandidates, ...cliCandidates, ...mcpCandidates]
      : [...cliCandidates, ...mcpCandidates, ...fileCandidates];
    return ordered.slice(0, MENTION_CANDIDATE_LIMIT);
  }, [cliAppMention, cliApps, fileMatches, mcpPresets]);

  const showCliAppMenu = filteredMentionCandidates.length > 0;
  const showAnyPalette = showSlashMenu || showCliAppMenu;

  // Only paths the user actually picked from the palette count as attachments,
  // so an ordinary "@someone" in prose is never mistaken for a file.
  const [pickedFiles, setPickedFiles] = useState<ProjectFileMatch[]>([]);
  const mentionSegments = useMemo(
    () => splitCapabilityMentionSegments(value, cliApps, mcpPresets, pickedFiles),
    [cliApps, mcpPresets, pickedFiles, value],
  );
  const hasMentionDecorations = mentionSegments.some(
    (segment) => segment.kind !== "text",
  );
  const activeFileMentions = useMemo(() => {
    const seen = new Set<string>();
    return mentionSegments.flatMap((segment) => {
      if (segment.kind !== "file" || seen.has(segment.file.path)) return [];
      seen.add(segment.file.path);
      return [segment.file];
    });
  }, [mentionSegments]);
  const activeCliMentionApps = useMemo(() => {
    const seen = new Set<string>();
    return mentionSegments.flatMap((segment) => {
      if (segment.kind !== "cli" || seen.has(segment.app.name)) return [];
      seen.add(segment.app.name);
      return [segment.app];
    });
  }, [mentionSegments]);
  const activeMcpPresetMentions = useMemo(() => {
    const seen = new Set<string>();
    return mentionSegments.flatMap((segment) => {
      if (segment.kind !== "mcp" || seen.has(segment.preset.name)) return [];
      seen.add(segment.preset.name);
      return [segment.preset];
    });
  }, [mentionSegments]);
  const [slashPaletteLayout, setSlashPaletteLayout] = useState<SlashPaletteLayout>({
    placement: "above",
    maxHeight: SLASH_PALETTE_MAX_HEIGHT_PX,
  });

  useEffect(() => {
    setSelectedCommandIndex(0);
  }, [slashQuery]);

  useEffect(() => {
    setSelectedCliAppIndex(0);
  }, [cliAppMention?.query]);

  useEffect(() => {
    if (selectedCommandIndex >= filteredSlashCommands.length) {
      setSelectedCommandIndex(0);
    }
  }, [filteredSlashCommands.length, selectedCommandIndex]);

  useEffect(() => {
    if (selectedCliAppIndex >= filteredMentionCandidates.length) {
      setSelectedCliAppIndex(0);
    }
  }, [filteredMentionCandidates.length, selectedCliAppIndex]);

  useEffect(() => {
    if (!showAnyPalette) return;

    const dismissOnPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && formRef.current?.contains(target)) return;
      setSlashMenuDismissed(true);
      setCliAppMenuDismissed(true);
    };

    document.addEventListener("pointerdown", dismissOnPointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", dismissOnPointerDown, true);
    };
  }, [showAnyPalette]);

  useLayoutEffect(() => {
    if (!showAnyPalette) return;

    const updateLayout = () => {
      const form = formRef.current;
      if (!form) return;
      const rect = form.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return;

      const bounds = getVisibleBounds(form);
      const spaceAbove = Math.max(0, rect.top - bounds.top - SLASH_PALETTE_GAP_PX);
      const spaceBelow = Math.max(0, bounds.bottom - rect.bottom - SLASH_PALETTE_GAP_PX);
      const placement: SlashPalettePlacement =
        spaceAbove >= SLASH_PALETTE_MIN_HEIGHT_PX || spaceAbove >= spaceBelow
          ? "above"
          : "below";
      const available = placement === "above" ? spaceAbove : spaceBelow;
      const maxHeight = Math.min(SLASH_PALETTE_MAX_HEIGHT_PX, available);

      setSlashPaletteLayout((current) =>
        current.placement === placement && current.maxHeight === maxHeight
          ? current
          : { placement, maxHeight },
      );
    };

    updateLayout();
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", updateLayout);
    viewport?.addEventListener("scroll", updateLayout);
    window.addEventListener("resize", updateLayout);
    document.addEventListener("scroll", updateLayout, true);
    return () => {
      viewport?.removeEventListener("resize", updateLayout);
      viewport?.removeEventListener("scroll", updateLayout);
      window.removeEventListener("resize", updateLayout);
      document.removeEventListener("scroll", updateLayout, true);
    };
  }, [filteredMentionCandidates.length, filteredSlashCommands.length, showAnyPalette]);

  const resizeTextarea = useCallback(() => {
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      fitTextareaHeight(el);
      el.focus();
    });
  }, []);

  // Height always follows the committed value. The per-callsite resize calls
  // above measure inside a rAF that can run before React commits the new text
  // (seeded ops actions landed in a tiny scrolling box); measuring after the
  // DOM update in a layout effect makes the growth deterministic.
  useLayoutEffect(() => {
    fitTextareaHeight(textareaRef.current);
  }, [value]);

  // Switching Studio <-> Code (or toggling the chat column) lays the composer
  // out at a few pixels wide first: measured then, the wrapped placeholder made
  // the box ~200 px tall and nothing re-fit it since the text had not changed.
  // Re-fit whenever the textarea's width settles.
  useEffect(() => {
    return observeTextareaWidth(textareaRef.current, () => {
      fitTextareaHeight(textareaRef.current);
    });
  }, []);

  // "Add to chat" from elsewhere in the app: land the text in the input and
  // leave the cursor after it, so the next thing typed is the actual question.
  const consumedSeedNonceRef = useRef(0);
  useEffect(() => {
    if (!seed || seed.nonce === consumedSeedNonceRef.current) return;
    consumedSeedNonceRef.current = seed.nonce;
    onSeedConsumed?.(seed.nonce);
    const seeded = seed.files ?? [];
    if (seeded.length) {
      setPickedFiles((prev) => {
        const known = new Set(prev.map((file) => file.path));
        return [...prev, ...seeded.filter((file) => !known.has(file.path))];
      });
    }
    if (seed.mediaTemplates) {
      setSelectedMediaTemplates(seed.mediaTemplates.filter((item) => item.id).slice(0, 6));
    } else if (seed.mediaTemplate?.id) {
      const incoming = seed.mediaTemplate;
      setSelectedMediaTemplates((prev) =>
        prev.some((item) => item.id === incoming.id)
          ? prev
          : [...prev, incoming].slice(0, 6),
      );
    }
    if (seed.localFiles?.length) {
      addFiles(seed.localFiles);
    }
    if (!seed.text && !seed.replace) {
      return;
    }
    let caret = 0;
    setValue((current) => {
      const next = seed.replace
        ? seed.text
        : `${current}${current && !current.endsWith("\n") && !current.endsWith(" ") ? " " : ""}${seed.text}`;
      caret = next.length;
      return next;
    });
    requestAnimationFrame(() => {
      setCursorPosition(caret);
      const el = textareaRef.current;
      if (!el) return;
      fitTextareaHeight(el);
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  }, [addFiles, seed, onSeedConsumed]);

  // Runs before paint so switching sessions never flashes the previous chat's
  // draft. Restore any saved draft for the chat we just opened.
  useLayoutEffect(() => {
    if (previousPendingQueueKeyRef.current === pendingQueueKey) return;
    previousPendingQueueKeyRef.current = pendingQueueKey;
    secondEnterPromptIdRef.current = null;
    setValue(readComposerDraft(pendingQueueKey));
    setInlineError(null);
    setSlashMenuDismissed(false);
    setCliAppMenuDismissed(false);
    setCursorPosition(0);
    clear();
    requestAnimationFrame(() => {
      fitTextareaHeight(textareaRef.current);
    });
  }, [clear, pendingQueueKey]);

  // Persist the free-text draft so a gateway/app restart does not lose prompts
  // the user was typing (or had parked before the model stalled).
  useEffect(() => {
    if (!pendingQueueKey) return;
    const handle = window.setTimeout(() => {
      writeComposerDraft(pendingQueueKey, value);
    }, 300);
    return () => window.clearTimeout(handle);
  }, [pendingQueueKey, value]);

  const appendTranscription = useCallback((text: string) => {
    const transcript = text.trim();
    if (!transcript) return;
    secondEnterPromptIdRef.current = null;
    setValue((current) => {
      if (!current.trim()) return transcript;
      const separator = /[\s\n]$/.test(current) ? "" : " ";
      return `${current}${separator}${transcript}`;
    });
    setSlashMenuDismissed(false);
    setCliAppMenuDismissed(false);
    setInlineError(null);
    resizeTextarea();
  }, [resizeTextarea]);

  const clearInlineError = useCallback(() => setInlineError(null), []);
  const setVoiceError = useCallback((key: VoiceRecorderErrorKey) => {
    setInlineError(t(`thread.composer.voiceErrors.${key}`));
  }, [t]);
  const voiceRecorder = useVoiceRecorder({
    disabled,
    onClearError: clearInlineError,
    onError: setVoiceError,
    onTranscript: appendTranscription,
    onTranscribeAudio,
    wantsWav: transcriptionProvider === "xiaomi_mimo",
  });

  useEffect(() => {
    if (!onTranscribeAudio) return;

    function onKeyDown(event: KeyboardEvent): void {
      if (!isVoiceShortcutDown(event) || event.repeat || voiceShortcutDownRef.current) return;
      event.preventDefault();
      secondEnterPromptIdRef.current = null;
      voiceShortcutDownRef.current = true;
      voiceRecorder.beginShortcutHold();
    }

    function onKeyUp(event: KeyboardEvent): void {
      if (!voiceShortcutDownRef.current || !isVoiceShortcutRelease(event)) return;
      event.preventDefault();
      voiceShortcutDownRef.current = false;
      voiceRecorder.endShortcutHold();
    }

    function onWindowBlur(): void {
      if (!voiceShortcutDownRef.current) return;
      voiceShortcutDownRef.current = false;
      voiceRecorder.endShortcutHold();
    }

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onWindowBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onWindowBlur);
    };
  }, [onTranscribeAudio, voiceRecorder.beginShortcutHold, voiceRecorder.endShortcutHold]);

  const chooseSlashCommand = useCallback(
    (command: SlashPaletteCommand) => {
      if (command.command === "/stop" && isStreaming && onStop) {
        onStop();
        setValue("");
        setSlashMenuDismissed(true);
        setCliAppMenuDismissed(false);
        setInlineError(null);
        resizeTextarea();
        return;
      }

      const nextRecents = [
        command.command,
        ...recentSlashCommands.filter((item) => item !== command.command),
      ].slice(0, SLASH_RECENTS_LIMIT);
      setRecentSlashCommands(nextRecents);
      storeSlashRecents(nextRecents);

      if (skillQuery !== null) {
        const suffix = value.slice(skillQuery.end);
        const inserted = `${command.command}${suffix.startsWith(" ") ? "" : " "}`;
        const next = `${value.slice(0, skillQuery.start)}${inserted}${suffix}`;
        const nextCursor = skillQuery.start + inserted.length;
        setValue(next);
        setCursorPosition(nextCursor);
        requestAnimationFrame(() => {
          const el = textareaRef.current;
          if (!el) return;
          el.focus();
          el.setSelectionRange(nextCursor, nextCursor);
        });
      } else {
        setValue(command.argHint ? `${command.command} ` : command.command);
      }
      setSlashMenuDismissed(true);
      setCliAppMenuDismissed(false);
      setInlineError(null);
      resizeTextarea();
    },
    [isStreaming, onStop, recentSlashCommands, resizeTextarea, skillQuery, value],
  );

  const chooseMentionCandidate = useCallback(
    (candidate: MentionCandidate) => {
      if (!cliAppMention) return;
      const suffix = value.slice(cliAppMention.end);
      if (candidate.kind === "file") {
        const file = candidate.file;
        setPickedFiles((current) =>
          current.some((item) => item.path === file.path) ? current : [...current, file],
        );
      }
      const mention = `@${candidate.name}${suffix.startsWith(" ") ? "" : " "}`;
      const next = `${value.slice(0, cliAppMention.start)}${mention}${suffix}`;
      const nextCursor = cliAppMention.start + mention.length;
      setValue(next);
      setCursorPosition(nextCursor);
      setCliAppMenuDismissed(true);
      setSlashMenuDismissed(false);
      setInlineError(null);
      resizeTextarea();
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(nextCursor, nextCursor);
      });
    },
    [cliAppMention, resizeTextarea, value],
  );

  const clearComposerText = useCallback(() => {
    setValue("");
    clearComposerDraft(pendingQueueKey);
    setInlineError(null);
    setSlashMenuDismissed(false);
    setCliAppMenuDismissed(false);
    setCursorPosition(0);
    setPickedFiles([]);
    resizeTextarea();
  }, [pendingQueueKey, resizeTextarea]);

  /** Build the turn attachments from the current composer state.
   * Returns null when the turn is not sendable yet (inline error is set). */
  const collectTurnOptions = useCallback((): { options: QueuedPromptOptions | undefined } | null => {
    const cliApps = activeCliMentionApps.map(cliAppMentionPayload);
    const mcpPresets = activeMcpPresetMentions.map(mcpPresetMentionPayload);
    const fileMentions = activeFileMentions.map(fileMentionPayload);
    const template = selectedDocumentTemplate;
    const language = documentLanguage.trim();
    if (template && !language) {
      setInlineError(
        t("thread.composer.documentTemplate.languageRequired", {
          defaultValue: "Select the document language before sending.",
        }),
      );
      return null;
    }
    const media = selectedMediaTemplates;
    const hasAny =
      cliApps.length > 0
      || mcpPresets.length > 0
      || fileMentions.length > 0
      || !!template
      || media.length > 0;
    if (!hasAny) return { options: undefined };
    return {
      options: {
        ...(cliApps.length > 0 ? { cliApps } : {}),
        ...(mcpPresets.length > 0 ? { mcpPresets } : {}),
        ...(fileMentions.length > 0 ? { fileMentions } : {}),
        ...(template
          ? {
              documentTemplate: {
                category: template.category,
                name: template.name,
                title: template.title,
                language,
              },
            }
          : {}),
        ...(media.length > 0
          ? { mediaTemplate: media[0], mediaTemplates: media }
          : {}),
      },
    };
  }, [
    activeCliMentionApps,
    activeFileMentions,
    activeMcpPresetMentions,
    documentLanguage,
    selectedDocumentTemplate,
    selectedMediaTemplates,
    t,
  ]);

  const queueGuidancePrompt = useCallback(() => {
    const text = value.trim();
    if (!canQueueGuidance || (!text && readyImages.length === 0)) return;
    if (utf8Bytes(text) > maxTextBytes) {
      setInlineError(textTooLargeMessage());
      return;
    }
    if (queuedPrompts.length >= QUEUED_PROMPTS_LIMIT) {
      setInlineError(
        t("thread.composer.queued.full", {
          defaultValue: "The queue is full ({{max}} messages). Send or remove one first.",
          max: QUEUED_PROMPTS_LIMIT,
        }),
      );
      return;
    }
    const collected = collectTurnOptions();
    if (!collected) return;
    const queuedImages = readyImagesToQueuedImages(readyImages);
    queuedPromptCounterRef.current += 1;
    const id = `queued-prompt-${Date.now()}-${queuedPromptCounterRef.current}`;
    secondEnterPromptIdRef.current = id;
    setQueuedPrompts((items) => [
      ...items,
      {
        id,
        text,
        ...(queuedImages.length > 0 ? { images: queuedImages } : {}),
        ...(collected.options ? { options: collected.options } : {}),
      },
    ]);
    setSelectedDocumentTemplate(null);
    setSelectedMediaTemplates([]);
    setDocumentLanguage("");
    clear();
    clearComposerText();
  }, [
    canQueueGuidance,
    clear,
    clearComposerText,
    collectTurnOptions,
    maxTextBytes,
    queuedPrompts.length,
    readyImages,
    t,
    textTooLargeMessage,
    value,
  ]);

  const removeQueuedPrompt = useCallback((id: string) => {
    secondEnterPromptIdRef.current = null;
    setQueuedPrompts((items) => items.filter((item) => item.id !== id));
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  const editQueuedPrompt = useCallback((prompt: QueuedPrompt) => {
    secondEnterPromptIdRef.current = null;
    setQueuedPrompts((items) => items.filter((item) => item.id !== prompt.id));
    setValue(prompt.text);
    setInlineError(null);
    setSlashMenuDismissed(false);
    setCliAppMenuDismissed(false);
    setCursorPosition(prompt.text.length);
    if (prompt.images?.length) {
      restoreReadyImages(prompt.images as RestoredReadyImage[]);
    } else {
      clear();
    }
    // Mentions are re-derived from the restored text; only the document
    // template lives outside it and has to be put back explicitly.
    const queuedTemplate = prompt.options?.documentTemplate;
    if (queuedTemplate) {
      const match = documentTemplates.find(
        (candidate) =>
          candidate.category === queuedTemplate.category && candidate.name === queuedTemplate.name,
      );
      if (match) {
        setSelectedDocumentTemplate(match);
        setDocumentLanguage(queuedTemplate.language ?? "");
      }
    }
    resizeTextarea();
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(prompt.text.length, prompt.text.length);
    });
  }, [clear, documentTemplates, resizeTextarea, restoreReadyImages, setSelectedDocumentTemplate]);

  const moveQueuedPrompt = useCallback((dragId: string, targetId: string) => {
    if (dragId === targetId) return;
    secondEnterPromptIdRef.current = null;
    setQueuedPrompts((items) => reorderQueuedPrompts(items, dragId, targetId));
  }, []);

  const moveQueuedPromptBy = useCallback((id: string, delta: number) => {
    secondEnterPromptIdRef.current = null;
    setQueuedPrompts((items) => shiftQueuedPrompt(items, id, delta));
  }, []);

  const clearQueuedPrompts = useCallback(() => {
    secondEnterPromptIdRef.current = null;
    setQueuedPrompts([]);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  const sendQueuedPrompt = useCallback(
    (prompt: QueuedPrompt) => {
      secondEnterPromptIdRef.current = null;
      const text = prompt.text.trim();
      const queuedImages = queuedImagesToSendImages(prompt.images);
      setQueuedPrompts((items) => items.filter((item) => item.id !== prompt.id));
      if (text || queuedImages?.length) {
        onSend(text, queuedImages, prompt.options);
      }
      requestAnimationFrame(() => textareaRef.current?.focus());
    },
    [onSend],
  );

  const sendNextQueuedPrompt = useCallback(() => {
    if (queuedPrompts.length === 0) return;
    const nextPrompt = queuedPrompts.find(queuedPromptIsSendable);
    if (!nextPrompt) {
      setQueuedPrompts([]);
      return;
    }
    setQueuedPrompts((items) => items.filter((item) => item.id !== nextPrompt.id));
    onSend(
      nextPrompt.text.trim(),
      queuedImagesToSendImages(nextPrompt.images),
      nextPrompt.options,
    );
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [onSend, queuedPrompts]);

  // Multitask: dispatch every text-only queued prompt to a parallel subagent
  // right now instead of waiting for the current turn. Prompts carrying image
  // or file attachments stay queued (a spawn carries plain text only).
  const [multitaskBusy, setMultitaskBusy] = useState(false);
  const multitaskQueuedPrompts = useCallback(async () => {
    if (!clientToken || !sessionKey || multitaskInFlightRef.current) return;
    const { targets } = planMultitaskDispatch(queuedPrompts);
    if (targets.length === 0) return;
    const targetIds = new Set(targets.map((prompt) => prompt.id));
    secondEnterPromptIdRef.current = null;
    // Leave the queue before the HTTP round-trips so the 150ms sequential
    // drain cannot also send the same prompt as a normal turn. Do not set
    // skipNextQueuedFlushRef: leftover attachment prompts must still flush
    // when the current turn ends, and a Stop already paused the queue.
    multitaskInFlightRef.current = true;
    setQueuedPrompts((items) => items.filter((item) => !targetIds.has(item.id)));
    setMultitaskBusy(true);
    setInlineError(null);
    try {
      const results = await Promise.allSettled(
        targets.map((prompt) =>
          spawnMultitaskPrompt(
            clientToken,
            sessionKey,
            prompt.text.trim(),
            "",
            "",
            prompt.id,
          ),
        ),
      );
      const failed = failedMultitaskTargets(targets, results);
      if (failed.length > 0) {
        setQueuedPrompts((items) => restoreFailedQueuedPrompts(items, failed));
      }
      const reason = firstMultitaskError(results);
      if (reason) {
        setInlineError(
          reason instanceof ApiError
            ? reason.message
            : t("thread.composer.queued.multitaskFailed", {
                defaultValue: "Could not start the parallel task.",
              }),
        );
      }
    } finally {
      multitaskInFlightRef.current = false;
      setMultitaskBusy(false);
    }
  }, [clientToken, queuedPrompts, sessionKey, t]);
  const multitaskableCount = useMemo(
    () => queuedPrompts.filter(queuedPromptIsMultitaskable).length,
    [queuedPrompts],
  );

  // Drain the queue one prompt per turn, and keep draining until it is empty:
  // each send starts a new turn, whose end re-runs this effect. The delay is a
  // safety net for a send that never starts a turn, so the queue cannot stall.
  useEffect(() => {
    wasStreamingRef.current = isStreaming;
    if (isStreaming) {
      // A new turn re-arms the queue after a Stop paused it.
      skipNextQueuedFlushRef.current = false;
      return;
    }
    secondEnterPromptIdRef.current = null;
    if (queuedPrompts.length === 0 || skipNextQueuedFlushRef.current) return;
    const timer = window.setTimeout(sendNextQueuedPrompt, QUEUED_PROMPT_FLUSH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [isStreaming, queuedPrompts, sendNextQueuedPrompt]);

  const handleStop = useCallback(() => {
    secondEnterPromptIdRef.current = null;
    if (queuedPrompts.length > 0) {
      skipNextQueuedFlushRef.current = true;
    }
    onStop?.();
  }, [onStop, queuedPrompts.length]);

  const submit = useCallback(() => {
    if (modelNeedsSetup) {
      onModelBadgeClick?.();
      return;
    }
    // WebKitGTK can paint pasted text in the textarea without firing
    // onChange. Read the live DOM so Enter sends what the user sees.
    const live = textareaRef.current?.value ?? value;
    if (live !== value) setValue(live);
    const trimmed = live.trim();
    const canSendLive =
      !disabled
      && !modelNeedsSetup
      && !encoding
      && !hasErrors
      && (trimmed.length > 0 || readyImages.length > 0);
    if (!canSendLive) return;
    // Plan mode routes free text through /blueprint so the agent designs
    // first; explicit slash commands (including /forge) stay untouched.
    const content = applyComposerTurnMode(trimmed, turnMode, {
      startNow:
        Boolean(selectedDocumentTemplate)
        || isSimpleStartNowPrompt(trimmed),
    });
    // Keep the toggle in sync with explicit workflow commands so Build /forge
    // and /blueprint flips are one-click and visible in the UI.
    const inferred = modeFromOutgoingContent(content);
    if (inferred && inferred !== turnMode) setTurnMode(inferred);
    if (utf8Bytes(content) > maxTextBytes) {
      setInlineError(textTooLargeMessage());
      return;
    }
    // Share the same ``data:`` URL with both the wire payload and the
    // optimistic bubble preview: data URLs are self-contained (no blob
    // lifetime, safe under React StrictMode double-mount) and keep the bubble
    // in sync with whatever the backend actually sees.
    const payload: SendAttachment[] | undefined =
      readyImages.length > 0
        ? readyImages.map((img) => ({
            media: {
              data_url: img.dataUrl,
              name: img.file.name,
            },
            preview: { kind: img.kind, url: img.dataUrl, name: img.file.name },
          }))
        : undefined;
    const collected = collectTurnOptions();
    if (!collected) return;
    const options = collected.options;
    const hasPlainTextCommandPayload = payload === undefined && options === undefined;
    const slashLifecycle = hasPlainTextCommandPayload
      ? slashCommandLifecycle(content, slashCommands)
      : null;
    if (
      slashLifecycle === "stop_active_turn"
      && isStreaming
      && onStop
    ) {
      handleStop();
      clear();
      clearComposerText();
      return;
    }
    const isSlashSideChannel = isSideChannelLifecycle(slashLifecycle);
    const finalizeActiveTurn =
      slashLifecycle === "finalize_active_turn";
    onSend(
      content,
      payload,
      isSlashSideChannel
        ? {
            ...options,
            sideChannel: true,
            ...(finalizeActiveTurn ? { finalizeActiveTurn } : {}),
          }
        : options,
    );
    setSelectedDocumentTemplate(null);
    setSelectedMediaTemplates([]);
    setDocumentLanguage("");
    // Bubble owns the data URL copy; safe to revoke every staged blob
    // preview here without affecting the rendered message.
    clear();
    clearComposerText();
  }, [
    activeCliMentionApps,
    activeMcpPresetMentions,
    clear,
    clearComposerText,
    disabled,
    encoding,
    handleStop,
    hasErrors,
    isStreaming,
    maxTextBytes,
    modelNeedsSetup,
    onModelBadgeClick,
    onSend,
    onStop,
    readyImages,
    documentLanguage,
    selectedDocumentTemplate,
    slashCommands,
    t,
    textTooLargeMessage,
    setTurnMode,
    turnMode,
    value,
  ]);

  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (showCliAppMenu) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedCliAppIndex((idx) => (idx + 1) % filteredMentionCandidates.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedCliAppIndex(
          (idx) => (idx - 1 + filteredMentionCandidates.length) % filteredMentionCandidates.length,
        );
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        chooseMentionCandidate(filteredMentionCandidates[selectedCliAppIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setCliAppMenuDismissed(true);
        return;
      }
    }
    if (showSlashMenu) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedCommandIndex((idx) => (idx + 1) % filteredSlashCommands.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedCommandIndex(
          (idx) => (idx - 1 + filteredSlashCommands.length) % filteredSlashCommands.length,
        );
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        chooseSlashCommand(filteredSlashCommands[selectedCommandIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashMenuDismissed(true);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      const live = e.currentTarget.value;
      if (live !== value) setValue(live);
      if (canQueueGuidance) {
        if (!e.repeat) queueGuidancePrompt();
        return;
      }
      const secondEnterPrompt = queuedPrompts.find(
        (prompt) => prompt.id === secondEnterPromptIdRef.current,
      );
      if (
        isStreaming
        && value.length === 0
        && images.length === 0
        && !e.altKey
        && !e.ctrlKey
        && !e.metaKey
        && secondEnterPrompt
      ) {
        if (!e.repeat) sendQueuedPrompt(secondEnterPrompt);
        return;
      }
      secondEnterPromptIdRef.current = null;
      submit();
    }
  };

  const onInput: React.FormEventHandler<HTMLTextAreaElement> = (e) => {
    const el = e.currentTarget;
    if (el.value !== value) {
      secondEnterPromptIdRef.current = null;
      setValue(el.value);
      setSlashMenuDismissed(false);
      setCliAppMenuDismissed(false);
      setCursorPosition(el.selectionStart ?? el.value.length);
    }
    fitTextareaHeight(el);
  };

  const onFilePick: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    addFiles(files);
  };

  const removeChip = useCallback(
    (id: string) => {
      const { nextFocusId } = remove(id);
      setInlineError(null);
      requestAnimationFrame(() => {
        const el = nextFocusId ? chipRefs.current.get(nextFocusId) : null;
        if (el) {
          el.focus();
        } else {
          textareaRef.current?.focus();
        }
      });
    },
    [remove],
  );

  const onChipKey = useCallback(
    (id: string) => (e: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (
        e.key === "Delete" ||
        e.key === "Backspace" ||
        e.key === "Enter" ||
        e.key === " "
      ) {
        e.preventDefault();
        removeChip(id);
      }
    },
    [removeChip],
  );

  const attachButtonDisabled = disabled || full;
  // While the live conversation is on, the microphone is already open: the
  // push-to-talk dictation button would only compete with it.
  const liveVoiceActive =
    liveVoice !== null && liveVoice.state !== "off" && liveVoice.state !== "error";
  const showVoiceButton = Boolean(onTranscribeAudio) && !liveVoiceActive;
  const liveVoiceLabel = liveVoiceActive
    ? t("thread.composer.liveVoice.toggleOff", { defaultValue: "End voice conversation" })
    : t("thread.composer.liveVoice.toggleOn", { defaultValue: "Voice conversation" });
  const voiceRecordingStatusLabel = t("thread.composer.voice.recordingStatus", {
    time: voiceRecorder.elapsedLabel,
    defaultValue: `Recording ${voiceRecorder.elapsedLabel}`,
  });
  const voiceButtonLabel =
    voiceRecorder.state === "recording"
      ? t("thread.composer.voice.stop")
      : voiceRecorder.state === "transcribing"
        ? t("thread.composer.voice.transcribing")
        : t("thread.composer.tools.voice");
  const voiceButtonTooltip =
    voiceRecorder.state === "recording"
      ? t("thread.composer.voice.stop")
      : voiceRecorder.state === "transcribing"
        ? t("thread.composer.voice.transcribing")
        : t("thread.composer.voice.hint");
  // A typed message must never turn into a stop: with content present the
  // round button queues/sends it, and Stop only acts on an empty composer.
  const primaryAction = composerPrimaryAction({
    isStreaming,
    hasStopHandler: !!onStop,
    hasComposerContent,
    canQueueGuidance,
    modelNeedsSetup,
  });
  const showStopButton = primaryAction === "stop";
  const queueButtonLabel = t("thread.composer.queued.add", {
    defaultValue: "Queue this message",
  });
  const relaxedHeroInput = isHero && images.length === 0 && !isStreaming;
  const modeAccent = composerModeAccent(turnMode);
  const inputTextClasses = cn(
    "w-full resize-none bg-transparent",
    // 16px below sm keeps mobile browsers from zooming on focus; wider screens
    // get the compact thread size.
    isHero
      ? cn(
          "min-h-[78px] px-4 text-[16px] leading-6 sm:px-5 sm:text-[14px] sm:leading-[22px]",
          relaxedHeroInput ? "pb-2 pt-[27px]" : "pb-1.5 pt-4",
        )
      : "min-h-[50px] px-3.5 pb-1.5 pt-3 text-[16px] leading-5 sm:px-4 sm:text-[13px] sm:leading-[20px]",
  );

  // Attach + document template sit on the right of the workspace row when that
  // row is on screen, and fall back to the toolbar when it is not (thread
  // composer, no project picker): the buttons must never disappear.
  const workspaceRowVisible = workspaceProjectPickerVisible({
    isHero,
    defaultScope: workspaceDefaultScope,
    controls: workspaceControls,
    onChange: onWorkspaceScopeChange,
  });
  const attachmentTools = voiceRecorder.isRecording ? null : (
    <>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        disabled={attachButtonDisabled}
        aria-label={t("thread.composer.attachImage")}
        onClick={() => fileInputRef.current?.click()}
        className="h-7 w-7 shrink-0 rounded-md border-transparent text-muted-foreground hover:bg-muted/65 hover:text-foreground"
      >
        <Paperclip className="h-3.5 w-3.5" />
      </Button>
      <DocumentTemplatePicker
        templates={documentTemplates}
        selected={selectedDocumentTemplate}
        onSelect={setSelectedDocumentTemplate}
        disabled={disabled}
        isHero
      />
      {sessionAction}
    </>
  );

  return (
    <form
      ref={formRef}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={cn("relative w-full", isHero ? "px-0" : "px-1 pb-1.5 pt-1 sm:px-0")}
    >
      {showSlashMenu ? (
        <SlashCommandPalette
          commands={filteredSlashCommands}
          selectedIndex={selectedCommandIndex}
          layout={slashPaletteLayout}
          isHero={isHero}
          onHover={setSelectedCommandIndex}
          onChoose={chooseSlashCommand}
        />
      ) : null}
      {showCliAppMenu ? (
        <CliAppMentionPalette
          candidates={filteredMentionCandidates}
          selectedIndex={selectedCliAppIndex}
          layout={slashPaletteLayout}
          isHero={isHero}
          onHover={setSelectedCliAppIndex}
          onChoose={chooseMentionCandidate}
        />
      ) : null}
      {queuedPrompts.length > 0 ? (
        <div
          className={cn("relative z-20 mx-auto w-full", isHero ? "max-w-[58rem]" : "max-w-[49.5rem]")}
          data-testid="composer-queue-slot"
        >
          <QueuedPromptStack
            prompts={queuedPrompts}
            isHero={isHero}
            labels={{
              label: t("thread.composer.queued.label"),
              count: t("thread.composer.queued.count", {
                defaultValue: "{{count}} queued messages",
                count: queuedPrompts.length,
              }),
              clearAll: t("thread.composer.queued.clearAll", { defaultValue: "Clear all" }),
              guide: t("thread.composer.queued.guide", { defaultValue: "Send now" }),
              guideHint: t("thread.composer.queued.guideHint", {
                defaultValue: "Send this message to the agent now. Enter on an empty composer sends the last queued one.",
              }),
              delete: t("thread.composer.queued.delete", { defaultValue: "Delete" }),
              drag: t("thread.composer.queued.drag"),
              edit: t("thread.composer.queued.edit", { defaultValue: "Edit" }),
              moveUp: t("thread.composer.queued.moveUp", { defaultValue: "Move up" }),
              moveDown: t("thread.composer.queued.moveDown", { defaultValue: "Move down" }),
              more: t("thread.composer.queued.more", { defaultValue: "More" }),
              attachments: t("thread.composer.queued.attachments", {
                defaultValue: "Attachments kept with this message",
              }),
              attachmentOnly: t("thread.composer.queued.attachmentOnly", {
                defaultValue: "File attachment",
              }),
              multitask: t("thread.composer.queued.multitask", {
                defaultValue: "Start Multitasking",
              }),
              multitaskHint: t("thread.composer.queued.multitaskHint", {
                defaultValue: "Run every queued text message now, each in its own parallel agent and git worktree",
              }),
              multitaskUnavailable: t("thread.composer.queued.multitaskUnavailable", {
                defaultValue: "Messages with attachments run one after another once this turn ends",
              }),
              collapse: t("thread.composer.queued.collapse", {
                defaultValue: "Hide the list (messages stay queued)",
              }),
              expand: t("thread.composer.queued.expand", { defaultValue: "Show the list" }),
            }}
            multitaskAvailable={Boolean(clientToken && sessionKey)}
            multitaskCount={multitaskableCount}
            multitaskBusy={multitaskBusy}
            onMultitask={() => void multitaskQueuedPrompts()}
            onGuide={sendQueuedPrompt}
            onDelete={removeQueuedPrompt}
            onEdit={editQueuedPrompt}
            onMove={moveQueuedPromptBy}
            onClearAll={clearQueuedPrompts}
            onDragStart={(id) => {
              secondEnterPromptIdRef.current = null;
              draggedQueuedPromptIdRef.current = id;
            }}
            onDragEnd={() => {
              draggedQueuedPromptIdRef.current = null;
            }}
            onDrop={(targetId) => {
              const dragId = draggedQueuedPromptIdRef.current;
              if (dragId) moveQueuedPrompt(dragId, targetId);
            }}
          />
        </div>
      ) : null}
      <div
        className={cn(
          "group/composer relative mx-auto flex w-full flex-col overflow-visible transition-all duration-200",
          "after:pointer-events-none after:absolute after:inset-[-1px] after:rounded-[inherit] after:border after:opacity-0 after:transition-opacity after:duration-200 focus-within:after:opacity-100",
          modeAccent
            ? "after:border-[hsl(var(--composer-mode)/0.65)]"
            : "after:border-blue-300/75 dark:after:border-blue-400/55",
          isHero
            ? "max-w-[58rem] rounded-[28px] border border-black/[0.035] bg-card shadow-[0_20px_55px_rgba(15,23,42,0.08)] dark:border-white/[0.06] dark:shadow-[0_24px_55px_rgba(0,0,0,0.34)]"
            : "max-w-[49.5rem] rounded-[22px] border border-black/[0.035] bg-card shadow-[0_12px_30px_rgba(15,23,42,0.07)] dark:border-white/[0.06] dark:shadow-[0_16px_34px_rgba(0,0,0,0.28)]",
          modeAccent
            ? cn(
                `composer-mode-${modeAccent}`,
                "composer-mode-shell focus-within:border-[hsl(var(--composer-mode)/0.8)]",
              )
            : "focus-within:border-blue-300/75 dark:focus-within:border-blue-400/55",
          disabled && "opacity-60",
          isDragging && "ring-2 ring-primary/40 motion-reduce:ring-0 motion-reduce:border-primary",
          goalState?.active &&
            turnMode === "agent" &&
            "goal-shell-glow ring-1 ring-sky-400/35 motion-reduce:ring-sky-400/25 dark:ring-sky-400/45",
        )}
      >
        {selectedMediaTemplates.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 px-3 pt-3">
            {selectedMediaTemplates.map((mediaRef) => (
              <span
                key={mediaRef.id}
                className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/30 px-2.5 py-1 text-[12px]"
                data-testid="media-template-chip"
              >
                {mediaRef.title || mediaRef.id}
                {mediaRef.format ? (
                  <span className="text-muted-foreground">{mediaRef.format}</span>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    setSelectedMediaTemplates((prev) =>
                      prev.filter((item) => item.id !== mediaRef.id),
                    )
                  }
                  className="text-muted-foreground hover:text-foreground"
                  aria-label={t("thread.composer.remove", { defaultValue: "Remove" })}
                >
                  ×
                </button>
              </span>
            ))}
            {selectedMediaTemplates.length > 1 ? (
              <span className="text-[11px] text-muted-foreground">
                {selectedMediaTemplates.length}/6
              </span>
            ) : null}
          </div>
        ) : null}
        {selectedDocumentTemplate ? (
          <div className="flex flex-wrap items-center gap-2 px-3 pt-3">
            <DocumentTemplateChip
              template={selectedDocumentTemplate}
              onRemove={() => setSelectedDocumentTemplate(null)}
            />
            <label className="flex min-w-[13rem] flex-1 items-center gap-2 rounded-xl border border-border/60 bg-muted/25 px-2.5 py-1.5">
              <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
                {t("thread.composer.documentTemplate.languageLabel", {
                  defaultValue: "Language",
                })}
              </span>
              <input
                list="composer-document-language-suggestions"
                value={documentLanguage}
                onChange={(event) => {
                  setDocumentLanguage(event.target.value);
                  setInlineError(null);
                }}
                placeholder={t("thread.composer.documentTemplate.languagePlaceholder", {
                  defaultValue: "French, Arabic, Turkish…",
                })}
                className="min-w-0 flex-1 bg-transparent text-[12px] text-foreground outline-none placeholder:text-muted-foreground/65"
                required
                aria-label={t("thread.composer.documentTemplate.languageLabel", {
                  defaultValue: "Document language",
                })}
              />
              <datalist id="composer-document-language-suggestions">
                <option value="Français" />
                <option value="English" />
                <option value="العربية" />
                <option value="Türkçe" />
                <option value="Español" />
                <option value="Italiano" />
                <option value="Deutsch" />
                <option value="Nederlands" />
              </datalist>
            </label>
          </div>
        ) : null}
        {images.length > 0 ? (
          <div
            className="flex flex-wrap gap-2 px-3 pt-3"
            aria-label={t("thread.composer.attachImage")}
          >
            {images.map((img) => (
              <AttachmentChip
                key={img.id}
                image={img}
                labelRemove={t("thread.composer.remove")}
                labelEncoding={t("thread.composer.encoding")}
                normalizedHint={(orig, current) =>
                  t("thread.composer.normalizedSizeHint", {
                    orig: formatBytes(orig),
                    current: formatBytes(current),
                  })
                }
                formatError={formatRejection}
                onRemove={() => removeChip(img.id)}
                onKeyDown={onChipKey(img.id)}
                registerRef={(el) => {
                  if (el) chipRefs.current.set(img.id, el);
                  else chipRefs.current.delete(img.id);
                }}
              />
            ))}
          </div>
        ) : null}
        <RunElapsedStrip startedAt={runStartedAt} goalState={goalState} />
        <div className="relative">
          {hasMentionDecorations ? (
            <ComposerCliMentionOverlay
              segments={mentionSegments}
              isHero={isHero}
              className={inputTextClasses}
            />
          ) : null}
          <textarea
            ref={textareaRef}
            spellCheck={false}
            autoCorrect="off"
            autoCapitalize="off"
            value={value}
            onChange={(e) => {
              secondEnterPromptIdRef.current = null;
              setValue(e.target.value);
              setSlashMenuDismissed(false);
              setCliAppMenuDismissed(false);
              setCursorPosition(e.target.selectionStart ?? e.target.value.length);
            }}
            onBlur={() => {
              secondEnterPromptIdRef.current = null;
            }}
            onInput={onInput}
            onKeyDown={onKeyDown}
            onKeyUp={(e) => setCursorPosition(e.currentTarget.selectionStart ?? e.currentTarget.value.length)}
            onSelect={(e) => setCursorPosition(e.currentTarget.selectionStart ?? e.currentTarget.value.length)}
            onClick={(e) => setCursorPosition(e.currentTarget.selectionStart ?? e.currentTarget.value.length)}
            onPaste={onPaste}
            rows={1}
            placeholder={resolvedPlaceholder}
            disabled={disabled}
            aria-label={t("thread.composer.inputAria")}
            className={cn(
              inputTextClasses,
              "relative z-10 caret-foreground placeholder:text-muted-foreground/70",
              "focus:outline-none focus-visible:outline-none",
              "disabled:cursor-not-allowed",
              hasMentionDecorations && "text-transparent selection:bg-primary/20",
            )}
          />
        </div>
        {inlineError ? (
          <div
            role="alert"
            className={cn(
              "mx-3 mb-1 rounded-md border border-amber-500/35 bg-amber-500/8 px-2.5 py-1",
              "text-[11.5px] font-medium text-amber-700 dark:text-amber-300",
            )}
          >
            {inlineError}
          </div>
        ) : null}
        {/* Style Cursor : Mode + Modèle + Think à gauche ; icônes + envoi à droite. */}
        <div
          className={cn(
            "flex flex-nowrap items-center justify-between gap-x-2",
            isHero ? "px-3 pb-3.5 sm:px-4" : toolbarCompact ? "px-2 pb-1.5" : "px-2.5 pb-2 sm:px-3",
          )}
        >
          <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden">
            <ComposerModeMenu
              mode={turnMode}
              disabled={disabled || isStreaming}
              isHero={isHero}
              compact={toolbarCompact}
              onChange={setTurnMode}
            />
            {modelLabel && !voiceRecorder.isRecording ? (
              !modelNeedsSetup && onModelSelect && modelOptions.length > 0 ? (
                <ComposerModelPicker
                  label={modelLabel}
                  provider={modelProvider}
                  providerLabel={modelProviderLabel}
                  isHero={isHero}
                  compact={toolbarCompact}
                  options={modelOptions}
                  routing={modelRouting}
                  budgetFilterPercent={budgetFilterPercent}
                  disabled={disabled}
                  onSelect={onModelSelect}
                  onHide={onHideModel}
                  onOpenChange={onModelPickerOpenChange}
                  onManageModels={onModelBadgeClick}
                />
              ) : (
                <ComposerModelBadge
                  label={modelLabel}
                  provider={modelProvider}
                  providerLabel={modelProviderLabel}
                  needsSetup={modelNeedsSetup}
                  isHero={isHero}
                  compact={toolbarCompact}
                  onClick={modelNeedsSetup ? onModelBadgeClick : undefined}
                />
              )
            ) : null}
            {!voiceRecorder.isRecording && onEffortSelect && effortOptions.length > 1 ? (
              <ComposerEffortPicker
                value={effortValue}
                options={effortOptions}
                disabled={disabled || modelNeedsSetup}
                isHero={isHero}
                compact={toolbarCompact}
                onSelect={onEffortSelect}
              />
            ) : null}
            {voiceRecorder.isRecording ? (
              <VoiceRecordingMeter
                ariaLabel={voiceRecordingStatusLabel}
                className="mx-1 min-w-[6rem] flex-1"
                elapsedLabel={voiceRecorder.elapsedLabel}
                isHero={isHero}
                levels={voiceRecorder.levels}
              />
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT_ATTR}
              multiple
              hidden
              onChange={onFilePick}
            />
            {workspaceRowVisible ? null : attachmentTools}
            {showVoiceButton ? (
              <TooltipProvider delayDuration={220} skipDelayDuration={80}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      disabled={voiceRecorder.buttonDisabled}
                      aria-label={voiceButtonLabel}
                      aria-keyshortcuts={VOICE_SHORTCUT_ARIA}
                      title={voiceButtonTooltip}
                      onPointerDown={voiceRecorder.beginPress}
                      onPointerUp={voiceRecorder.endPress}
                      onPointerCancel={voiceRecorder.endPress}
                      onClick={voiceRecorder.handleClick}
                      className={cn(
                        "h-7 w-7 shrink-0 rounded-md border-transparent text-muted-foreground hover:bg-muted/65 hover:text-foreground",
                        voiceRecorder.isRecording &&
                          "rounded-full bg-red-500 text-white shadow-[0_8px_20px_rgba(239,68,68,0.22)] hover:bg-red-500 hover:text-white",
                      )}
                    >
                      {voiceRecorder.state === "transcribing" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : voiceRecorder.isRecording ? (
                        <Square className="h-3 w-3" fill="currentColor" />
                      ) : (
                        <Mic className="h-3.5 w-3.5" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent
                    side="top"
                    align="center"
                    className="flex items-center gap-2 rounded-full border border-border/70 bg-background px-3 py-1.5 text-[13px] font-medium text-foreground shadow-[0_8px_24px_rgba(15,23,42,0.13)] dark:border-white/10 dark:bg-neutral-900 dark:text-white"
                  >
                    <span>{voiceButtonTooltip}</span>
                    {voiceRecorder.state === "idle" ? (
                      <kbd className="rounded-full bg-muted px-2 py-0.5 font-sans text-[12px] font-semibold leading-none text-muted-foreground dark:bg-white/10 dark:text-white/80">
                        {voiceShortcutLabel}
                      </kbd>
                    ) : null}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : null}
            {liveVoice && !voiceRecorder.isRecording ? (
              <TooltipProvider delayDuration={220} skipDelayDuration={80}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      disabled={disabled || liveVoice.disabled || liveVoice.state === "starting"}
                      aria-label={liveVoiceLabel}
                      aria-pressed={liveVoiceActive}
                      data-live-voice-toggle
                      onClick={liveVoice.toggle}
                      className={cn(
                        "relative h-7 w-7 shrink-0 rounded-md border-transparent text-muted-foreground hover:bg-muted/65 hover:text-foreground",
                        liveVoiceActive &&
                          "rounded-full bg-primary text-primary-foreground shadow-[0_8px_20px_rgba(37,99,235,0.28)] hover:bg-primary hover:text-primary-foreground",
                      )}
                    >
                      {liveVoice.state === "starting" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <AudioLines className="h-3.5 w-3.5" />
                      )}
                      {liveVoiceActive && liveVoice.state === "listening" ? (
                        <span
                          aria-hidden
                          className="absolute inset-0 -z-10 animate-ping rounded-full bg-primary/35 motion-reduce:hidden"
                        />
                      ) : null}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent
                    side="top"
                    align="center"
                    className="max-w-[16rem] rounded-xl border border-border/70 bg-background px-3 py-2 text-[12px] text-foreground shadow-[0_8px_24px_rgba(15,23,42,0.13)] dark:border-white/10 dark:bg-neutral-900 dark:text-white"
                  >
                    <span className="block font-medium">{liveVoiceLabel}</span>
                    {!liveVoiceActive ? (
                      <span className="mt-0.5 block text-[11.5px] text-muted-foreground">
                        {t("thread.composer.liveVoice.hint", {
                          defaultValue:
                            "Talk with the agent: it listens, explains what it will do, asks when unsure and tells you when it is done.",
                        })}
                      </span>
                    ) : null}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : null}
            {canQueueGuidance ? (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={queueButtonLabel}
                title={queueButtonLabel}
                onClick={queueGuidancePrompt}
                className="h-7 w-7 shrink-0 rounded-md border-transparent text-muted-foreground hover:bg-muted/65 hover:text-foreground"
              >
                <ListPlus className="h-3.5 w-3.5" />
              </Button>
            ) : null}
            <Button
              type={primaryAction === "send" ? "submit" : "button"}
              size="icon"
              disabled={
                showStopButton
                  ? disabled
                  : primaryAction === "queue"
                    ? disabled
                    : !canSend && !canOpenModelSettings
              }
              aria-label={
                showStopButton
                  ? t("thread.composer.stop")
                  : primaryAction === "queue"
                    ? queueButtonLabel
                    : primaryAction === "configure"
                      ? t("thread.composer.configureModel", { defaultValue: "Configure model" })
                      : t("thread.composer.send")
              }
              onClick={
                showStopButton
                  ? handleStop
                  : primaryAction === "queue"
                    ? queueGuidancePrompt
                    : primaryAction === "configure"
                      ? onModelBadgeClick
                      : undefined
              }
              className={cn(
                "h-7 w-7 shrink-0 rounded-full transition-transform",
                showStopButton
                  ? "border border-border/70 bg-card text-foreground/85 hover:bg-muted/65 hover:text-foreground disabled:text-muted-foreground/50"
                  : "border border-foreground bg-foreground text-background shadow-[0_3px_10px_rgba(15,23,42,0.18)] hover:bg-foreground/90 disabled:border-foreground disabled:bg-foreground disabled:text-background",
                (canSend || canOpenModelSettings || showStopButton) && "hover:scale-[1.03] active:scale-95",
              )}
            >
              {showStopButton ? (
                <Square className="h-3 w-3 fill-current stroke-current" />
              ) : primaryAction === "queue" ? (
                <ArrowUp className="h-3.5 w-3.5" />
              ) : isStreaming ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <ArrowUp className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>
        {/* Which folder this chat will work in, chosen before the first send. */}
        <WorkspaceProjectPicker
          isHero={isHero}
          disabled={disabled || workspaceScopeDisabled}
          scope={workspaceScope}
          defaultScope={workspaceDefaultScope}
          controls={workspaceControls}
          error={workspaceError}
          onChange={onWorkspaceScopeChange}
          onNewChat={onNewChat}
          trailing={workspaceRowVisible ? attachmentTools : null}
        />
      </div>
    </form>
  );
}

// The composer holds the draft the user is typing, and it is mounted as a
// sibling of the message list. Without this the stream re-rendered it on every
// token - a few hundred nodes with textarea measurement and palette layout -
// which is what made the caret stutter while an agent was answering. Every prop
// it receives is a stable reference, so the shallow compare holds for a whole
// turn.
export const ThreadComposer = memo(ThreadComposerImpl);

interface QueuedPromptLabels {
  label: string;
  count: string;
  clearAll: string;
  guide: string;
  guideHint: string;
  delete: string;
  drag: string;
  edit: string;
  moveUp: string;
  moveDown: string;
  more: string;
  attachments: string;
  attachmentOnly: string;
  multitask: string;
  multitaskHint: string;
  multitaskUnavailable: string;
  collapse: string;
  expand: string;
}

const QUEUED_ROW_HEIGHT_PX = 26;
const QUEUED_HEADER_HEIGHT_PX = 32;
const QUEUED_LIST_MAX_HEIGHT_PX = 7 * QUEUED_ROW_HEIGHT_PX + 6;

/**
 * Messages typed while a turn runs, Cursor-style: one line per message, a
 * count in the header, "Start Multitasking" to fan them out to parallel
 * agents, and per-row actions (send now / edit / delete / more) that appear
 * on hover so the list stays quiet. Exported for the render test only.
 */
export function QueuedPromptStack({
  prompts,
  isHero,
  labels,
  multitaskAvailable,
  multitaskCount,
  multitaskBusy,
  onMultitask,
  onGuide,
  onDelete,
  onEdit,
  onMove,
  onClearAll,
  onDragStart,
  onDragEnd,
  onDrop,
}: {
  prompts: QueuedPrompt[];
  isHero: boolean;
  labels: QueuedPromptLabels;
  /** A session and a token exist, so a spawn can be requested at all. */
  multitaskAvailable: boolean;
  /** Text-only prompts; the others wait for the sequential drain. */
  multitaskCount: number;
  multitaskBusy: boolean;
  onMultitask: () => void;
  onGuide: (prompt: QueuedPrompt) => void;
  onDelete: (id: string) => void;
  onEdit: (prompt: QueuedPrompt) => void;
  onMove: (id: string, delta: number) => void;
  onClearAll: () => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  onDrop: (targetId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  // Upper bound for the enter/exit slide animation, not a layout height: the
  // fill-mode keeps the last keyframe's max-height, so it must cover the
  // header plus the scrolling list at its cap.
  const stripMaxHeight = collapsed
    ? QUEUED_HEADER_HEIGHT_PX
    : QUEUED_HEADER_HEIGHT_PX + Math.min(QUEUED_LIST_MAX_HEIGHT_PX, prompts.length * QUEUED_ROW_HEIGHT_PX + 6);
  const multitaskDisabled = multitaskBusy || multitaskCount === 0;

  return (
    <div
      role="group"
      data-state="enter"
      data-testid="composer-queue"
      data-collapsed={collapsed || undefined}
      className={cn(
        "composer-status-strip relative mb-1.5 overflow-hidden rounded-[12px]",
        "border border-black/[0.06] bg-card",
        "shadow-[0_6px_18px_rgba(15,23,42,0.05)]",
        "dark:border-white/[0.09] dark:shadow-[0_8px_22px_rgba(0,0,0,0.26)]",
      )}
      style={{ "--composer-strip-max-height": `${stripMaxHeight}px` } as CSSProperties}
      aria-label={labels.label}
    >
      <div className="flex h-8 items-center gap-1 pl-3 pr-1.5">
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          title={collapsed ? labels.expand : labels.collapse}
          data-testid="composer-queue-toggle"
          className={cn(
            "flex min-w-0 flex-1 items-center gap-1.5 rounded-md text-left",
            "text-[13px] font-medium text-foreground/80 transition-colors hover:text-foreground",
          )}
        >
          <span className="truncate">{labels.count}</span>
          {collapsed ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" aria-hidden />
          ) : null}
        </button>
        {multitaskAvailable ? (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <button
                    type="button"
                    onClick={onMultitask}
                    disabled={multitaskDisabled}
                    aria-disabled={multitaskDisabled || undefined}
                    data-testid="composer-queue-multitask"
                    className={cn(
                      "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md px-1.5 text-[12.5px] font-medium",
                      "text-foreground/80 transition-colors hover:bg-muted/70 hover:text-foreground",
                      "disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent",
                      "dark:hover:bg-white/[0.07]",
                    )}
                  >
                    {multitaskBusy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Waypoints className="h-3.5 w-3.5" aria-hidden />
                    )}
                    {labels.multitask}
                  </button>
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" align="end" className="max-w-[18rem] text-pretty">
                {multitaskCount > 0 || multitaskBusy ? labels.multitaskHint : labels.multitaskUnavailable}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : null}
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? labels.expand : labels.collapse}
          title={collapsed ? labels.expand : labels.collapse}
          data-testid="composer-queue-close"
          className={cn(
            "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
            "text-muted-foreground/70 transition-colors hover:bg-muted/70 hover:text-foreground dark:hover:bg-white/[0.07]",
          )}
        >
          {collapsed ? (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <X className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>
      {!collapsed ? (
        <div
          className="flex flex-col overflow-y-auto px-1 pb-1"
          style={{ maxHeight: QUEUED_LIST_MAX_HEIGHT_PX }}
          data-testid="composer-queue-list"
        >
          {prompts.map((prompt, index) => (
            <QueuedPromptRow
              key={prompt.id}
              prompt={prompt}
              isHero={isHero}
              labels={labels}
              canMoveUp={index > 0}
              canMoveDown={index < prompts.length - 1}
              canClearAll={prompts.length > 1}
              onGuide={onGuide}
              onDelete={onDelete}
              onEdit={onEdit}
              onMove={onMove}
              onClearAll={onClearAll}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              onDrop={onDrop}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Left slot of a queued row: the first image as a thumbnail, else a clip. */
function QueuedPromptLead({ prompt, title }: { prompt: QueuedPrompt; title: string }) {
  const image = prompt.images?.find((candidate) => candidate.kind !== "file");
  const attachmentCount = queuedPromptAttachmentCount(prompt);
  if (attachmentCount === 0) return null;
  const extra = attachmentCount - (image ? 1 : 0);
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 text-muted-foreground/70"
      title={title}
      aria-label={title}
      data-testid="composer-queue-lead"
    >
      {image ? (
        <img
          src={image.dataUrl}
          alt=""
          draggable={false}
          className="h-[18px] w-[18px] rounded-[4px] border border-black/[0.08] object-cover dark:border-white/[0.12]"
        />
      ) : (
        <Paperclip className="h-3.5 w-3.5" aria-hidden />
      )}
      {extra > 0 ? <span className="text-[10.5px] tabular-nums">+{extra}</span> : null}
    </span>
  );
}

function QueuedPromptRow({
  prompt,
  isHero,
  labels,
  canMoveUp,
  canMoveDown,
  canClearAll,
  onGuide,
  onDelete,
  onEdit,
  onMove,
  onClearAll,
  onDragStart,
  onDragEnd,
  onDrop,
}: {
  prompt: QueuedPrompt;
  isHero: boolean;
  labels: QueuedPromptLabels;
  canMoveUp: boolean;
  canMoveDown: boolean;
  canClearAll: boolean;
  onGuide: (prompt: QueuedPrompt) => void;
  onDelete: (id: string) => void;
  onEdit: (prompt: QueuedPrompt) => void;
  onMove: (id: string, delta: number) => void;
  onClearAll: () => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  onDrop: (targetId: string) => void;
}) {
  const displayLabel = queuedPromptLabel(prompt, labels.attachmentOnly);
  const actionClass = cn(
    "inline-flex h-[22px] shrink-0 items-center justify-center rounded-md text-muted-foreground",
    "transition-colors hover:bg-background/90 hover:text-foreground dark:hover:bg-white/[0.08]",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  );

  return (
    <div
      data-queued-prompt-row="true"
      draggable
      aria-label={labels.drag}
      onDragStart={(event) => {
        if ((event.target as HTMLElement).closest("button, a, [role=menuitem]")) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", prompt.id);
        suppressNativeDragPreview(event.dataTransfer);
        onDragStart(prompt.id);
      }}
      onDragEnter={(event) => {
        event.preventDefault();
        onDrop(prompt.id);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      }}
      onDrop={(event) => {
        event.preventDefault();
        onDrop(prompt.id);
      }}
      onDragEnd={onDragEnd}
      className={cn(
        "queued-prompt-row group/queued flex h-[26px] items-center gap-2 rounded-[7px] pl-2 pr-1",
        "text-[13px] transition-colors hover:bg-muted/60 focus-within:bg-muted/60",
        "dark:hover:bg-white/[0.06] dark:focus-within:bg-white/[0.06]",
        isHero && "text-[13.5px]",
      )}
    >
      <QueuedPromptLead prompt={prompt} title={labels.attachments} />
      <p
        title={displayLabel}
        className="min-w-0 flex-1 truncate font-normal leading-[26px] text-foreground/90 antialiased"
      >
        {displayLabel}
      </p>
      <div
        className={cn(
          "flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity",
          "group-hover/queued:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100",
        )}
      >
        <button
          type="button"
          onClick={() => onGuide(prompt)}
          title={labels.guideHint}
          data-testid="composer-queue-send"
          className={cn(actionClass, "gap-1 px-1.5 text-[12px] font-medium")}
        >
          {labels.guide}
          <CornerDownLeft className="h-3 w-3" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={labels.edit}
          title={labels.edit}
          onClick={() => onEdit(prompt)}
          className={cn(actionClass, "w-6")}
        >
          <SquarePen className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={labels.delete}
          title={labels.delete}
          onClick={() => onDelete(prompt.id)}
          data-testid="composer-queue-delete"
          className={cn(actionClass, "w-6 hover:text-destructive")}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={labels.more}
              title={labels.more}
              className={cn(actionClass, "w-6")}
            >
              <Ellipsis className="h-3.5 w-3.5" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-[10rem] text-[12.5px]">
            <DropdownMenuItem disabled={!canMoveUp} onSelect={() => onMove(prompt.id, -1)}>
              <ChevronUp className="mr-2 h-3.5 w-3.5" aria-hidden />
              {labels.moveUp}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={!canMoveDown} onSelect={() => onMove(prompt.id, 1)}>
              <ChevronDown className="mr-2 h-3.5 w-3.5" aria-hidden />
              {labels.moveDown}
            </DropdownMenuItem>
            {canClearAll ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  onSelect={onClearAll}
                >
                  <Trash2 className="mr-2 h-3.5 w-3.5" aria-hidden />
                  {labels.clearAll}
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

function ModelOptionLogo({
  provider,
  model,
  source,
}: {
  provider: string | null;
  model: string;
  source?: "managed" | "byok";
}) {
  const inferred = provider || inferProviderFromModelName(model);
  const brand = providerBrand(inferred);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(brand?.logoUrls);
  const byok = source === "byok";
  return (
    <span className="relative grid h-5 w-5 shrink-0 place-items-center" aria-hidden>
      <span
        className="grid h-5 w-5 place-items-center overflow-hidden rounded-full border bg-background"
        style={{ borderColor: brand ? `${brand.color}28` : undefined }}
      >
        {logoUrl ? (
          <img
            src={logoUrl}
            alt=""
            decoding="async"
            loading="lazy"
            className="h-3.5 w-3.5 object-contain"
            onLoad={onLogoLoad}
            onError={onLogoError}
          />
        ) : brand ? (
          <span
            className="grid h-full w-full place-items-center rounded-full text-[8px] text-white"
            style={{ backgroundColor: brand.color }}
          >
            {brand.initials.slice(0, 2)}
          </span>
        ) : (
          <Brain className="h-3 w-3 text-muted-foreground/65" />
        )}
      </span>
      {byok ? (
        <span
          className="absolute -bottom-0.5 -right-0.5 grid h-3 w-3 place-items-center rounded-full border border-background bg-muted text-foreground"
          title="BYOK"
        >
          <KeyRound className="h-2 w-2" />
        </span>
      ) : null}
    </span>
  );
}

function ComposerModelPicker({
  label,
  provider,
  providerLabel,
  isHero,
  compact = false,
  options,
  routing = null,
  budgetFilterPercent = null,
  disabled,
  onSelect,
  onHide,
  onOpenChange,
  onManageModels,
}: {
  label: string;
  provider?: string | null;
  providerLabel?: string | null;
  isHero: boolean;
  compact?: boolean;
  options: ComposerModelOption[];
  routing?: ComposerModelRouting | null;
  budgetFilterPercent?: number | null;
  disabled?: boolean;
  onSelect: (presetName: string) => void;
  onHide?: (presetName: string) => void;
  onOpenChange?: (open: boolean) => void;
  onManageModels?: () => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [listMaxPx, setListMaxPx] = useState(240);
  const searchRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inferredProvider = provider || inferProviderFromModelName(label);
  const brand = providerBrand(inferredProvider);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(brand?.logoUrls);
  // Prefer the active chat (text) row - media tools can also be "active"
  // for their specialty slot and must not steal the badge label/logo.
  const activeOption =
    options.find((option) => option.active && (!option.modality || option.modality === "text"))
    ?? options.find((option) => option.active);
  const byokActive = activeOption?.source === "byok";
  // Auto: no pin, Task routing may swap the default model per task. Say it on
  // the trigger, otherwise the user reads "GLM" here and "Nemotron" in the
  // trace and has no idea why.
  const routingAuto = Boolean(routing?.auto && routing.swaps.length);
  const autoWord = t("thread.composer.modelAuto", { defaultValue: "Auto-route" });
  const routesSummary = routing
    ? routing.swaps
        .slice(0, 4)
        .map((swap) => `${swap.roleLabel} -> ${swap.modelLabel}`)
        .join(" · ")
      + (routing.swaps.length > 4 ? ` · +${routing.swaps.length - 4}` : "")
    : "";
  const title = providerLabel
    ? `${label} · ${providerLabel}`
    : label;
  const titleWithSource = byokActive
    ? `${title} · ${t("thread.composer.modelSourceByok", { defaultValue: "Your API key" })}`
    : activeOption?.source === "managed"
      ? `${title} · ${t("thread.composer.modelSourceManaged", { defaultValue: "Included in plan" })}`
      : title;
  const triggerTitle = routingAuto
    ? t("thread.composer.modelAutoTitle", {
        defaultValue:
          "Auto-route: {{model}} by default, Task routing may switch per task ({{routes}}). Pick a model to lock it for this chat.",
        model: label,
        routes: routesSummary,
      })
    : routing && !routing.auto
      ? `${titleWithSource} · ${t("thread.composer.modelPinnedTitle", {
          defaultValue: "Locked for this chat (Task routing off)",
        })}`
      : titleWithSource;
  const dense = compact || isHero;
  const visibleOptions = useMemo(
    () => visiblePickerOptions(options, query),
    [options, query],
  );
  const detachListWheel = useRef<(() => void) | null>(null);
  const setListNode = useCallback((node: HTMLDivElement | null) => {
    detachListWheel.current?.();
    detachListWheel.current = node ? bindMenuListWheel(node) : null;
  }, []);
  useEffect(() => () => {
    detachListWheel.current?.();
    detachListWheel.current = null;
  }, []);

  const measureList = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    const viewportTop = window.visualViewport?.offsetTop ?? 0;
    setListMaxPx(modelPickerListMaxPx(rect?.top ?? 280, budgetFilterPercent != null, viewportTop));
  }, [budgetFilterPercent]);

  const handleOpenChange = useCallback((open: boolean) => {
    if (!open) {
      setQuery("");
    } else {
      measureList();
      window.requestAnimationFrame(() => {
        searchRef.current?.focus({ preventScroll: true });
      });
    }
    onOpenChange?.(open);
  }, [measureList, onOpenChange]);

  return (
    <DropdownMenu onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          ref={triggerRef}
          type="button"
          data-navin-debug="model"
          data-model-routing={routing ? (routingAuto ? "auto" : "pinned") : undefined}
          title={triggerTitle}
          aria-label={t("thread.composer.modelPickerAria", { defaultValue: "Choose model" })}
          className={cn(
            "inline-flex min-w-0 items-center rounded-[10px] border border-transparent bg-transparent font-medium text-foreground/85",
            "outline-none transition-colors",
            "cursor-pointer hover:bg-foreground/[0.045] hover:text-foreground focus-visible:outline-none dark:hover:bg-white/[0.06]",
            "disabled:pointer-events-none disabled:opacity-55",
            dense
              ? "h-7 max-w-[min(15rem,45vw)] gap-1 px-1.5 text-[12px]"
              : "h-8 max-w-[min(18rem,45vw)] gap-1.5 px-2 text-[12.5px]",
          )}
        >
          <span className="relative grid shrink-0 place-items-center" aria-hidden>
            <span
              className="grid place-items-center overflow-hidden rounded-full border bg-background"
              style={{
                borderColor: brand ? `${brand.color}28` : undefined,
                boxShadow: brand ? `inset 0 0 0 1px ${brand.color}18` : undefined,
                height: 16,
                width: 16,
              }}
            >
              {logoUrl ? (
                <img
                  src={logoUrl}
                  alt=""
                  decoding="async"
                  loading="lazy"
                  className="h-2.5 w-2.5 object-contain"
                  onLoad={onLogoLoad}
                  onError={onLogoError}
                />
              ) : brand ? (
                <span
                  className="grid h-full w-full place-items-center rounded-full text-[7px] text-white"
                  style={{ backgroundColor: brand.color }}
                >
                  {brand.initials.slice(0, 2)}
                </span>
              ) : (
                <Brain className="h-2.5 w-2.5 text-muted-foreground/65" />
              )}
            </span>
            {byokActive ? (
              <span className="absolute -bottom-0.5 -right-0.5 grid h-2.5 w-2.5 place-items-center rounded-full border border-background bg-muted text-foreground">
                <KeyRound className="h-1.5 w-1.5" />
              </span>
            ) : null}
          </span>
          {routingAuto ? (
            <span className="flex min-w-0 items-baseline gap-1">
              <span className="shrink-0">{autoWord}</span>
              <span className="truncate text-muted-foreground">· {label}</span>
            </span>
          ) : (
            <span className="truncate">{label}</span>
          )}
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground opacity-80" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={8}
        collisionPadding={12}
        onWheel={(event) => event.stopPropagation()}
        className="flex w-[min(21rem,calc(100vw-2rem))] flex-col rounded-2xl p-0"
        style={{
          maxHeight: listMaxPx + 160,
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        <div className="shrink-0 bg-popover px-1.5 pt-1.5">
        {budgetFilterPercent != null ? (
          <p className="px-1 pb-1.5 pt-1 text-[11px] leading-4 text-muted-foreground text-pretty">
            {t("thread.composer.budgetFilterNote", {
              defaultValue:
                "At {{percent}}% usage, Opus 5+, Fable 5+ and GPT 5.6 are paused. Other plan models stay available until renewal.",
              percent: budgetFilterPercent,
            })}
          </p>
        ) : null}
        <div className="pb-1.5 pt-0.5">
          <label className="flex items-center gap-1.5 rounded-xl border border-border/70 bg-muted/40 px-2">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <input
              ref={searchRef}
              type="text"
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="off"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
              onPointerDown={(event) => event.stopPropagation()}
              placeholder={t("thread.composer.searchModels", {
                defaultValue: "Search models",
              })}
              aria-label={t("thread.composer.searchModels", {
                defaultValue: "Search models",
              })}
              className="h-8 min-w-0 flex-1 bg-transparent text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70"
            />
            {query ? (
              <button
                type="button"
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => setQuery("")}
                className="grid h-5 w-5 place-items-center rounded-md text-muted-foreground hover:bg-foreground/8 hover:text-foreground"
                aria-label={t("thread.composer.clearModelSearch", { defaultValue: "Clear search" })}
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            ) : null}
          </label>
        </div>
        </div>
        <div
          ref={setListNode}
          data-model-picker-scroll=""
          className="min-h-0 flex-1 px-1.5"
          style={{
            height: listMaxPx,
            maxHeight: listMaxPx,
            overflowY: "auto",
            overscrollBehavior: "contain",
          }}
        >
          {routing && routing.swaps.length > 0 && !query ? (
            <DropdownMenuItem
              data-testid="model-picker-auto"
              data-active={routingAuto ? "true" : undefined}
              onPointerDown={(event) => {
                commitOnPointerDown(event, () => {
                  if (!routingAuto) onSelect(AUTO_MODEL_PRESET);
                });
              }}
              onSelect={() => {
                commitOnSelect(() => {
                  if (!routingAuto) onSelect(AUTO_MODEL_PRESET);
                });
              }}
              className="mb-1 flex min-h-[44px] min-w-0 cursor-default items-center gap-2.5 rounded-xl border border-border/60 bg-muted/30 px-2.5 py-2"
            >
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-foreground/[0.06] text-foreground/80">
                <Route className="h-3.5 w-3.5" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-foreground">
                  {t("thread.composer.modelAutoRow", {
                    defaultValue: "Auto-route (by task)",
                  })}
                </span>
                <span className="block truncate text-[11px] text-muted-foreground" title={routesSummary}>
                  {routesSummary}
                </span>
              </span>
              {routingAuto ? <Check className="h-4 w-4 shrink-0 text-foreground/80" aria-hidden /> : null}
            </DropdownMenuItem>
          ) : null}
          {visibleOptions.length === 0 ? (
            <p className="px-2.5 py-3 text-[12.5px] text-muted-foreground">
              {t("thread.composer.noMatchingModels", {
                defaultValue: "No matching models",
              })}
            </p>
          ) : null}
          {visibleOptions.map((option) => {
            const canHide = Boolean(onHide && canHidePickerOption(option));
            return (
              <div key={option.name} className="flex items-center gap-0.5">
                <DropdownMenuItem
                  onPointerDown={(event) => {
                    commitOnPointerDown(event, () => {
                      // In Auto the default row is only the fallback: picking
                      // it is a real pin, so let the click through.
                      if (!option.active || routingAuto) onSelect(option.name);
                    });
                  }}
                  onSelect={() => {
                    commitOnSelect(() => {
                      if (!option.active || routingAuto) onSelect(option.name);
                    });
                  }}
                  className="flex min-h-[44px] min-w-0 flex-1 cursor-default items-center gap-2.5 rounded-xl px-2.5 py-2"
                >
                  <ModelOptionLogo
                    provider={option.provider}
                    model={option.model}
                    source={option.source}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className="block min-w-0 truncate text-[13px] font-medium text-foreground">
                        {option.label}
                      </span>
                      {option.needsKey ? (
                        <span
                          className="shrink-0 rounded px-1 py-px text-[9px] font-medium uppercase tracking-wide text-amber-600 ring-1 ring-amber-500/40 dark:text-amber-400"
                          title={t("thread.composer.modelNeedsKey", {
                            defaultValue: "API key required - selecting opens provider settings",
                          })}
                        >
                          {t("thread.composer.modelNeedsKeyBadge", { defaultValue: "Key required" })}
                        </span>
                      ) : option.source === "byok" ? (
                        <span
                          className="shrink-0 rounded px-1 py-px text-[9px] font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-border"
                          title={t("thread.composer.modelSourceByok", {
                            defaultValue: "Your API key",
                          })}
                        >
                          BYOK
                        </span>
                      ) : option.source === "managed" ? (
                        <span
                          className="shrink-0 rounded px-1 py-px text-[9px] font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-border"
                          title={t("thread.composer.modelSourceManaged", {
                            defaultValue: "Included in plan",
                          })}
                        >
                          Plan
                        </span>
                      ) : null}
                      {option.modality && option.modality !== "text" ? (
                        <span
                          className={cn(
                            "ml-auto shrink-0 rounded px-1 py-px text-[8px] font-semibold lowercase tracking-wide ring-1",
                            modalityBadgeClass(option.modality),
                          )}
                          title={t("thread.composer.mediaModelNotDefault", {
                            defaultValue: "Media model - cannot be the chat default",
                          })}
                        >
                          {modalityBadgeLabel(option.modality)}
                        </span>
                      ) : isVisionChatModel(option.model) ? (
                        <span
                          className={cn(
                            "ml-auto shrink-0 rounded px-1 py-px text-[8px] font-semibold lowercase tracking-wide ring-1",
                            visionBadgeClass(),
                          )}
                          title={t("thread.composer.visionModel", {
                            defaultValue: "Vision / multimodal - image, video, audio input",
                          })}
                        >
                          {visionBadgeLabel()}
                        </span>
                      ) : null}
                    </span>
                    {(option.billingRate != null || option.unitPriceUsd != null)
                    && option.modality
                    && option.modality !== "text" ? (
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {option.modality === "video"
                        && option.billingUnit === "video_second"
                        && option.billingRate != null
                          ? `~${option.billingRate} $/s`
                          : option.modality === "video"
                            ? `~${option.unitPriceUsd} $ / clip`
                            : option.modality === "image"
                              ? `~${option.unitPriceUsd} $ / image`
                              : option.modality === "audio"
                                ? `~${option.unitPriceUsd} $ / 1M car.`
                                : option.modality === "music"
                                  ? `~${option.unitPriceUsd} $ / piste`
                                  : option.modality === "stt"
                                    ? `~${option.unitPriceUsd} $ / min`
                                    : `~${option.unitPriceUsd} $`}
                      </span>
                    ) : option.model && option.model !== option.label ? (
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {option.model}
                      </span>
                    ) : null}
                  </span>
                  {option.active && routingAuto && (!option.modality || option.modality === "text") ? (
                    <span className="shrink-0 rounded px-1 py-px text-[9px] font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-border">
                      {t("thread.composer.modelDefaultTag", { defaultValue: "Default" })}
                    </span>
                  ) : option.active ? (
                    <Check className="h-4 w-4 shrink-0 text-foreground/80" aria-hidden />
                  ) : null}
                </DropdownMenuItem>
                {onHide ? (
                  canHide ? (
                    <button
                      type="button"
                      title={t("thread.composer.hideModel", { defaultValue: "Hide from list" })}
                      aria-label={t("thread.composer.hideModel", { defaultValue: "Hide from list" })}
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onHide(option.name);
                      }}
                      className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-lg text-muted-foreground/75 transition-colors hover:bg-foreground/8 hover:text-foreground"
                    >
                      <EyeOff className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  ) : (
                    <span className="mr-1 h-7 w-7 shrink-0" aria-hidden />
                  )
                ) : null}
              </div>
            );
          })}
        </div>
        {routing && routing.swaps.length > 0 ? (
          <div className="shrink-0 px-1.5">
            <DropdownMenuSeparator />
            <p className="px-2.5 pb-1 pt-1.5 text-[11px] leading-4 text-muted-foreground text-pretty">
              {t("thread.composer.modelRoutingNote", {
                defaultValue:
                  "A model picked here is locked for this chat. Auto-route lets Settings > Models > Task routing choose the model per task.",
              })}
              {routing.onOpenRouting ? (
                <>
                  {" "}
                  <button
                    type="button"
                    onPointerDown={(event) => event.preventDefault()}
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      routing.onOpenRouting?.();
                    }}
                    className="underline decoration-border underline-offset-2 hover:text-foreground"
                  >
                    {t("thread.composer.modelRoutingEdit", { defaultValue: "Edit routes" })}
                  </button>
                </>
              ) : null}
            </p>
          </div>
        ) : null}
        {onManageModels ? (
          <div className="shrink-0 px-1.5 pb-1.5">
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onPointerDown={(event) => {
                commitOnPointerDown(event, onManageModels);
              }}
              onSelect={() => {
                commitOnSelect(onManageModels);
              }}
              className="flex h-9 cursor-default items-center gap-2.5 rounded-xl px-2.5 text-[12.5px] text-muted-foreground focus:text-foreground"
            >
              <Wrench className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {t("thread.composer.manageModels", { defaultValue: "Manage models…" })}
            </DropdownMenuItem>
          </div>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ComposerEffortPicker({
  value,
  options,
  disabled,
  isHero,
  compact = false,
  onSelect,
}: {
  value: string;
  options: string[];
  disabled?: boolean;
  isHero: boolean;
  compact?: boolean;
  onSelect: (effort: string) => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const values = options.length > 0 ? options : [...DEFAULT_REASONING_EFFORT_VALUES];
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    setPending(null);
  }, [value]);
  const shown = pending ?? value;
  const label = reasoningEffortShortLabel(shown, tx);
  const dense = compact || isHero;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          type="button"
          data-navin-debug="effort"
          title={t("thread.composer.effortPickerTitle", {
            defaultValue: "Thinking effort",
          })}
          aria-label={t("thread.composer.effortPickerAria", {
            defaultValue: "Choose thinking effort",
          })}
          className={cn(
            "inline-flex shrink-0 items-center rounded-[10px] border border-transparent bg-transparent font-medium text-foreground/85",
            "outline-none transition-colors",
            "cursor-pointer hover:bg-foreground/[0.045] hover:text-foreground focus-visible:outline-none dark:hover:bg-white/[0.06]",
            "disabled:pointer-events-none disabled:opacity-55",
            dense ? "h-7 gap-1 px-1.5 text-[12px]" : "h-8 gap-1 px-2 text-[12.5px]",
          )}
        >
          <span className="truncate">{label}</span>
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground opacity-80" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={8}
        className="w-[min(12rem,calc(100vw-2rem))] rounded-2xl"
      >
        <div className="px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("thread.composer.effortSection", { defaultValue: "Effort" })}
        </div>
        {values.map((option) => {
          const active = option === shown;
          const pick = () => {
            if (active) return;
            setPending(option);
            onSelect(option);
          };
          return (
            <DropdownMenuItem
              key={option || "auto"}
              onPointerDown={(event) => {
                commitOnPointerDown(event, pick);
              }}
              onSelect={() => {
                commitOnSelect(pick);
              }}
              className="flex h-9 cursor-default items-center gap-2 rounded-xl px-2.5 text-[13px]"
            >
              <span className="min-w-0 flex-1 truncate">
                {reasoningEffortShortLabel(option, tx)}
              </span>
              {active ? <Check className="h-4 w-4 shrink-0 text-foreground/80" aria-hidden /> : null}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ComposerModelBadge({
  label,
  provider,
  providerLabel,
  needsSetup,
  isHero,
  compact = false,
  onClick,
}: {
  label: string;
  provider?: string | null;
  providerLabel?: string | null;
  needsSetup?: boolean;
  isHero: boolean;
  compact?: boolean;
  onClick?: () => void;
}) {
  const inferredProvider = needsSetup ? null : provider || inferProviderFromModelName(label);
  const brand = providerBrand(inferredProvider);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(brand?.logoUrls);
  const showLogo = !!logoUrl;
  const title = providerLabel ? `${label} · ${providerLabel}` : label;
  const interactive = Boolean(onClick);
  const Container = interactive ? "button" : "span";
  const dense = compact || isHero;

  return (
    <Container
      title={title}
      type={interactive ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "inline-flex min-w-0 items-center rounded-[10px] border border-transparent bg-transparent font-medium text-foreground/85",
        interactive && "cursor-pointer hover:bg-foreground/[0.045] hover:text-foreground dark:hover:bg-white/[0.06]",
        needsSetup && "border-foreground/25 bg-muted/70 text-foreground",
        dense
          ? "h-7 max-w-[min(15rem,45vw)] gap-1 px-1.5 text-[12px]"
          : "h-8 max-w-[min(18rem,45vw)] gap-1.5 px-2 text-[12.5px]",
      )}
    >
      <span
        data-testid={needsSetup ? "composer-model-setup-icon" : inferredProvider ? `composer-model-logo-${inferredProvider}` : "composer-model-logo"}
        className={cn(
          "grid h-4 w-4 shrink-0 place-items-center overflow-hidden",
          needsSetup
            ? "text-amber-800 dark:text-amber-200"
            : "rounded-full border bg-background",
        )}
        style={{
          borderColor: !needsSetup && brand ? `${brand.color}28` : undefined,
          boxShadow: !needsSetup && brand ? `inset 0 0 0 1px ${brand.color}18` : undefined,
        }}
        aria-hidden
      >
        {needsSetup ? (
          <CircleHelp className="h-3 w-3" strokeWidth={1.8} />
        ) : showLogo ? (
          <img
            src={logoUrl}
            alt=""
            decoding="async"
            loading="lazy"
            className="h-2.5 w-2.5 object-contain"
            onLoad={onLogoLoad}
            onError={onLogoError}
          />
        ) : brand ? (
          <span
            className="grid h-full w-full place-items-center rounded-full text-[7px] text-white"
            style={{ backgroundColor: brand.color }}
          >
            {brand.initials.slice(0, 2)}
          </span>
        ) : (
          <Brain className="h-2.5 w-2.5 text-muted-foreground/65" />
        )}
      </span>
      <span className="truncate">{label}</span>
    </Container>
  );
}

function ComposerCliMentionOverlay({
  segments,
  isHero,
  className,
}: {
  segments: CapabilityMentionSegment[];
  isHero: boolean;
  className: string;
}) {
  return (
    <div
      aria-hidden
      className={cn(
        className,
        "pointer-events-none absolute inset-0 z-0 overflow-hidden whitespace-pre-wrap break-words text-foreground",
      )}
    >
      {segments.map((segment, index) => {
        if (segment.kind === "text") {
          return <span key={`text-${index}`}>{segment.text}</span>;
        }
        if (segment.kind === "cli") return (
          <CliAppMentionToken
            key={`cli-${segment.app.name}-${index}`}
            app={segment.app}
            label={segment.text}
            variant="composer"
            isHero={isHero}
          />
        );
        if (segment.kind === "file") return (
          <FileMentionToken
            key={`file-${segment.file.path}-${index}`}
            file={segment.file}
            label={segment.text}
            variant="composer"
          />
        );
        return (
          <McpPresetMentionToken
            key={`mcp-${segment.preset.name}-${index}`}
            preset={segment.preset}
            label={segment.text}
            variant="composer"
            isHero={isHero}
          />
        );
      })}
    </div>
  );
}
interface SlashCommandPaletteProps {
  commands: SlashPaletteCommand[];
  selectedIndex: number;
  layout: SlashPaletteLayout;
  isHero: boolean;
  onHover: (index: number) => void;
  onChoose: (command: SlashPaletteCommand) => void;
}

interface CliAppMentionPaletteProps {
  candidates: MentionCandidate[];
  selectedIndex: number;
  layout: SlashPaletteLayout;
  isHero: boolean;
  onHover: (index: number) => void;
  onChoose: (candidate: MentionCandidate) => void;
}

function useSelectedOptionScroll(selectedIndex: number) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const option = container.querySelector<HTMLElement>(
      `[data-palette-index="${selectedIndex}"]`,
    );
    if (typeof option?.scrollIntoView === "function") {
      option.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIndex]);

  return containerRef;
}

function CliAppMentionPalette({
  candidates,
  selectedIndex,
  layout,
  isHero,
  onHover,
  onChoose,
}: CliAppMentionPaletteProps) {
  const { t } = useTranslation();
  const listMaxHeight = Math.max(
    0,
    layout.maxHeight - SLASH_PALETTE_CHROME_PX,
  );
  const listRef = useSelectedOptionScroll(selectedIndex);
  return (
    <div
      role="listbox"
      aria-label={t("thread.composer.mentions.ariaLabel")}
      style={{ maxHeight: layout.maxHeight }}
      className={cn(
        "absolute left-1/2 z-30 w-[calc(100%-0.5rem)] -translate-x-1/2 overflow-hidden rounded-[22px] border",
        layout.placement === "above" ? "bottom-full mb-2" : "top-full mt-2",
        "border-border/70 bg-popover p-2 text-popover-foreground shadow-[0_20px_60px_rgba(15,23,42,0.12)]",
        "dark:border-white/10 dark:shadow-[0_24px_60px_rgba(0,0,0,0.42)]",
        isHero ? "max-w-[58rem]" : "max-w-[49.5rem]",
      )}
    >
      <div className="px-2 pb-1.5 pt-0.5 text-[13px] font-semibold text-muted-foreground/78">
        {t("thread.composer.mentions.label")}
      </div>
      <div ref={listRef} className="overflow-y-auto" style={{ maxHeight: listMaxHeight }}>
        {candidates.map((candidate, index) => {
          const selected = index === selectedIndex;
          const name = candidate.name;
          const displayName = candidate.kind === "cli"
            ? candidate.app.display_name
            : candidate.kind === "mcp"
              ? candidate.preset.display_name
              : candidate.file.name;
          const typeLabel = candidate.kind === "cli"
            ? t("thread.composer.mentions.cliBadge")
            : candidate.kind === "mcp"
              ? t("thread.composer.mentions.mcpBadge")
              : candidate.file.kind === "directory"
                ? t("thread.composer.mentions.folderBadge", { defaultValue: "Folder" })
                : t("thread.composer.mentions.fileBadge", { defaultValue: "File" });
          const ariaDescription = candidate.kind === "cli"
            ? t("thread.composer.mentions.cliDescription", { name })
            : candidate.kind === "mcp"
              ? t("thread.composer.mentions.mcpDescription", { name })
              : t("thread.composer.mentions.fileDescription", {
                  path: candidate.file.path,
                  defaultValue: `Attach ${candidate.file.path} to this message`,
                });
          return (
            <button
              key={`${candidate.kind}-${name}`}
              type="button"
              role="option"
              data-palette-index={index}
              aria-selected={selected}
              aria-label={`${displayName} @${name} ${ariaDescription} ${typeLabel}`}
              onMouseEnter={() => onHover(index)}
              onMouseDown={(e) => {
                e.preventDefault();
                onChoose(candidate);
              }}
              className={cn(
                "flex min-h-10 w-full items-center gap-2.5 rounded-[13px] px-2.5 py-1.5 text-left transition-colors",
                selected
                  ? "bg-foreground/[0.055] text-foreground"
                  : "text-foreground/90 hover:bg-foreground/[0.04]",
              )}
            >
              <MentionCandidateLogo candidate={candidate} selected={selected} />
              <span className="flex min-w-0 flex-1 items-baseline gap-2">
                <span className="min-w-0 truncate text-[15px] font-medium tracking-normal text-foreground">
                  {displayName}
                </span>
                <span className="truncate text-[15px] font-normal tracking-normal text-muted-foreground/72">
                  @{name}
                </span>
              </span>
              <span
                className={cn(
                  "ml-2 shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-normal",
                  "bg-muted text-muted-foreground",
                )}
              >
                {typeLabel}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MentionCandidateLogo({
  candidate,
  selected,
}: {
  candidate: MentionCandidate;
  selected: boolean;
}) {
  const color = (candidate.kind === "cli"
    ? candidate.app.brand_color
    : candidate.kind === "mcp"
      ? candidate.preset.brand_color
      : null) || INLINE_TOKEN_HIGHLIGHT_COLOR;
  const rawLogoUrl = candidate.kind === "cli"
    ? candidate.app.logo_url
    : candidate.kind === "mcp"
      ? candidate.preset.logo_url
      : null;
  const logoUrls = useMemo(() => logoFallbackUrls(rawLogoUrl), [rawLogoUrl]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);

  if (candidate.kind === "file") {
    return (
      <span
        className={cn(
          "flex h-5 w-5 shrink-0 items-center justify-center rounded-[5px] border border-border/60",
          selected ? "bg-background/55" : "bg-muted/40",
        )}
        aria-hidden
      >
        {candidate.file.kind === "directory" ? (
          <FolderTypeIcon name={candidate.file.name} className="h-3 w-3" />
        ) : (
          <FileTypeIcon name={candidate.file.name} className="h-3 w-3" />
        )}
      </span>
    );
  }

  if (logoUrl) {
    return (
      <span
        className={cn(
          "flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded-[5px]",
          selected ? "bg-background/55" : "bg-transparent",
        )}
      >
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-5 w-5 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      </span>
    );
  }
  return (
    <span
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[5px] text-[7.5px] font-semibold text-white"
      style={{ backgroundColor: color }}
    >
      {candidate.kind === "cli"
        ? cliAppInitials(candidate.app)
        : candidate.kind === "mcp"
          ? mcpPresetInitials(candidate.preset)
          : null}
    </span>
  );
}

function SlashCommandPalette({
  commands,
  selectedIndex,
  layout,
  isHero,
  onHover,
  onChoose,
}: SlashCommandPaletteProps) {
  const { t } = useTranslation();
  const listMaxHeight = Math.max(
    0,
    layout.maxHeight - SLASH_PALETTE_CHROME_PX,
  );
  const listRef = useSelectedOptionScroll(selectedIndex);
  return (
    <div
      role="listbox"
      aria-label={t("thread.composer.slash.ariaLabel")}
      style={{ maxHeight: layout.maxHeight }}
      className={cn(
        "absolute left-1/2 z-30 w-[calc(100%-0.5rem)] -translate-x-1/2 overflow-hidden rounded-[18px] border",
        layout.placement === "above" ? "bottom-full mb-2" : "top-full mt-2",
        "border-border/65 bg-popover p-1.5 text-popover-foreground shadow-[0_18px_55px_rgba(15,23,42,0.16)]",
        "dark:border-white/10 dark:shadow-[0_22px_55px_rgba(0,0,0,0.45)]",
        isHero ? "max-w-[58rem]" : "max-w-[49.5rem]",
      )}
    >
      <div ref={listRef} className="overflow-y-auto pr-0.5" style={{ maxHeight: listMaxHeight }}>
        {commands.map((command, index) => {
          const Icon = COMMAND_ICONS[command.icon] ?? CircleHelp;
          const selected = index === selectedIndex;
          const commandKey = slashCommandI18nKey(command.command);
          const title = t(`thread.composer.slash.commands.${commandKey}.title`, {
            defaultValue: command.title,
          });
          const description = t(`thread.composer.slash.commands.${commandKey}.description`, {
            defaultValue: command.description,
          });
          return (
            <button
              key={command.command}
              type="button"
              role="option"
              data-palette-index={index}
              aria-selected={selected}
              onMouseEnter={() => onHover(index)}
              onMouseDown={(e) => {
                e.preventDefault();
                onChoose(command);
              }}
              className={cn(
                "flex min-h-[44px] w-full items-center gap-3 rounded-[13px] px-3 py-2 text-left transition-colors",
                selected
                  ? "bg-foreground/[0.065] text-foreground dark:bg-white/[0.09]"
                  : "text-foreground/86 hover:bg-foreground/[0.045] dark:hover:bg-white/[0.065]",
              )}
            >
              <span
                className={cn(
                  "flex h-7 w-7 shrink-0 items-center justify-center text-muted-foreground transition-colors",
                  selected && "text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
                <span className="min-w-0 truncate text-[13.5px] font-semibold tracking-normal text-foreground">
                  {title}
                </span>
                <span className="min-w-0 truncate text-[13px] text-muted-foreground">
                  {command.detail || description}
                </span>
              </span>
              <span className="ml-2 flex max-w-[42%] shrink-0 items-center gap-1.5 sm:max-w-none">
                {command.badge || command.recent ? (
                  <span className="hidden rounded-full bg-foreground/[0.055] px-2 py-1 text-[11px] font-medium text-muted-foreground sm:inline-flex">
                    {command.badge ?? t("thread.composer.slash.badges.recent")}
                  </span>
                ) : null}
                <span className="font-mono text-[12px] text-muted-foreground/60">
                  {command.argHint ? `${command.command} ${command.argHint}` : command.command}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface AttachmentChipProps {
  image: AttachedImage;
  labelRemove: string;
  labelEncoding: string;
  normalizedHint: (origBytes: number, currentBytes: number) => string;
  formatError: (reason: AttachmentError) => string;
  onRemove: () => void;
  onKeyDown: (e: ReactKeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
}

function AttachmentChip({
  image,
  labelRemove,
  labelEncoding,
  normalizedHint,
  formatError,
  onRemove,
  onKeyDown,
  registerRef,
}: AttachmentChipProps) {
  const sizeLabel =
    image.status === "ready" && image.normalized && image.encodedBytes
      ? normalizedHint(image.file.size, image.encodedBytes)
      : formatBytes(image.file.size);
  const tone =
    image.status === "error"
      ? "border-amber-500/35 bg-amber-500/5 text-amber-700 dark:text-amber-300"
      : "border-border/70 bg-muted/60";

  return (
    <div
      className={cn(
        "group relative flex items-center gap-2 rounded-[12px] border px-2 py-1.5",
        "transition-colors motion-reduce:transition-none",
        tone,
      )}
      data-testid="composer-chip"
    >
      <div className="relative h-10 w-10 overflow-hidden rounded-md bg-background">
        {image.kind === "image" && image.previewUrl ? (
          <img
            src={image.previewUrl}
            alt=""
            aria-hidden
            loading="eager"
            draggable={false}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            {image.kind === "image" ? (
              <ImageIcon className="h-4 w-4 text-muted-foreground" aria-hidden />
            ) : isAudioAttachment(image.file) ? (
              <FileAudio className="h-4 w-4 text-muted-foreground" aria-hidden />
            ) : isVideoAttachment(image.file) ? (
              <FileVideo className="h-4 w-4 text-muted-foreground" aria-hidden />
            ) : (
              <FileText className="h-4 w-4 text-muted-foreground" aria-hidden />
            )}
          </div>
        )}
        {image.status === "encoding" ? (
          <div
            className="absolute inset-0 flex items-center justify-center bg-background/60"
            aria-label={labelEncoding}
          >
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
          </div>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col text-[11.5px] leading-4">
        <span className="max-w-[min(14rem,calc(100vw-8rem))] truncate font-medium" title={image.file.name}>
          {image.file.name}
        </span>
        <span className="truncate text-muted-foreground">
          {image.status === "error" && image.error
            ? formatError(image.error)
            : sizeLabel}
        </span>
      </div>
      <button
        type="button"
        ref={registerRef}
        onClick={onRemove}
        onKeyDown={onKeyDown}
        aria-label={labelRemove}
        className={cn(
          "ml-1 grid h-5 w-5 flex-none place-items-center rounded-full",
          "text-muted-foreground/80 hover:bg-foreground/8 hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/30",
        )}
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}
