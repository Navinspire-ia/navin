// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  AlertCircle,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  CircleDashed,
  Copy,
  FilePen,
  FileText,
  FolderSearch,
  GitBranch,
  Globe,
  Image as ImageIcon,
  ListChecks,
  Loader2,
  MessageCircleQuestion,
  Network,
  Orbit,
  Plug,
  Search,
  ShieldCheck,
  SquareTerminal,
  Users,
  Wrench,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { cliAppInitials, mcpPresetInitials } from "@/components/CliAppMentionText";
import { FileReferenceChip } from "@/components/FileReferenceChip";
import { ComputerSetupNotice } from "@/components/settings/ComputerSetupNotice";
import { ActivityEvidencePreview } from "@/components/thread/activity/ActivityEvidencePreview";
import { ACTIVITY_DETAIL_PAGE_SIZE, ActivityPagination } from "@/components/thread/activity/ActivityPagination";
import { DiffPair } from "@/components/thread/activity/DiffPair";
import { ReasoningJournalBody } from "@/components/thread/activity/ReasoningRow";
import { SandboxBadge, ShellRunCard, type ShellRunSummary } from "@/components/thread/activity/ShellRunCard";
import { REASONING_LABEL_CLASS, reasoningSummary } from "@/components/thread/activity/reasoningProse";
import {
  JOURNAL_DETAIL_LIST_MAX,
  JOURNAL_PREVIEW_MAX,
  formatActivityDuration,
  indexBackgroundNotice,
  isIndexTool,
  journalHasDetails,
  journalTaskLink,
  isReportedFailure,
  journalToneForEntry,
  type ActivityJournalDigest,
  type ActivityJournalEntry,
  type ActivityJournalTone,
  type ActivityPhaseKey,
  type ActivityPhaseShare,
} from "@/components/thread/activity/activityJournalModel";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import {
  type ActivityVerbosity,
  loadActivityVerbosity,
  previewMaxFor,
  saveActivityVerbosity,
} from "@/lib/activity-verbosity";
import { firstOutputLine, outputPreviewLines } from "@/lib/activity-preview";
import { logoFallbackUrls } from "@/lib/provider-brand";
import { computerSetupIssue } from "@/lib/computer-setup";
import type { CliAppInfo, McpPresetInfo } from "@/lib/types";
import { HighlightedOutput, RunOutputFooter } from "@/components/thread/activity/HighlightedOutput";
import { hasRunOutputNote, tidyRunOutput } from "@/lib/ansi";
import { cn } from "@/lib/utils";
import { requestOpenBoardTask, type BoardTaskFocusRequest } from "@/lib/workbench-events";

export const JOURNAL_TARGET_CLASS = "text-foreground/92 font-medium";
export const JOURNAL_META_CLASS = "text-foreground/58";
export const JOURNAL_PLAIN_VERB_CLASS = "text-foreground/72";

/**
 * Shared with the header digest: blue for search/exploration and sky for
 * reading. Values live in globals.css
 * (`--tone-*`) so light and dark keep 4.5:1 contrast.
 */
export const JOURNAL_TONE_CLASS: Record<ActivityJournalTone, string> = {
  explore: "text-[hsl(var(--tone-explore))]",
  read: "text-[hsl(var(--tone-read))]",
  search: "text-[hsl(var(--tone-explore))]",
  edit: "text-[hsl(var(--tone-edit))]",
  shell: "text-[hsl(var(--tone-shell))]",
  cli: "text-[hsl(var(--tone-cli))]",
  mcp: "text-[hsl(var(--tone-mcp))]",
  skill: "text-[hsl(var(--tone-skill))]",
  task: "text-[hsl(var(--tone-task))]",
  git: "text-[hsl(var(--tone-git))]",
  browser: "text-[hsl(var(--tone-browser))]",
  media: "text-[hsl(var(--tone-browser))]",
  tool: "text-[hsl(var(--tone-tool))]",
  index: "text-[hsl(var(--tone-index))]",
  error: "text-[hsl(var(--tone-error))]",
  reasoning: REASONING_LABEL_CLASS,
};

const JOURNAL_TONE_BORDER: Record<ActivityJournalTone, string> = {
  explore: "border-[hsl(var(--tone-explore))]/40",
  read: "border-[hsl(var(--tone-read))]/40",
  search: "border-[hsl(var(--tone-explore))]/40",
  edit: "border-[hsl(var(--tone-edit))]/40",
  shell: "border-[hsl(var(--tone-shell))]/40",
  cli: "border-[hsl(var(--tone-cli))]/40",
  mcp: "border-[hsl(var(--tone-mcp))]/40",
  skill: "border-[hsl(var(--tone-skill))]/40",
  task: "border-[hsl(var(--tone-task))]/40",
  git: "border-[hsl(var(--tone-git))]/40",
  browser: "border-[hsl(var(--tone-browser))]/40",
  media: "border-[hsl(var(--tone-browser))]/40",
  tool: "border-[hsl(var(--tone-tool))]/40",
  index: "border-[hsl(var(--tone-index))]/40",
  error: "border-[hsl(var(--tone-error))]/40",
  reasoning: "border-[hsl(var(--composer-ask))]/40",
};

const JOURNAL_TONE_DOT: Record<ActivityJournalTone, string> = {
  explore: "bg-[hsl(var(--tone-explore))]",
  read: "bg-[hsl(var(--tone-read))]",
  search: "bg-[hsl(var(--tone-explore))]",
  edit: "bg-[hsl(var(--tone-edit))]",
  shell: "bg-[hsl(var(--tone-shell))]",
  cli: "bg-[hsl(var(--tone-cli))]",
  mcp: "bg-[hsl(var(--tone-mcp))]",
  skill: "bg-[hsl(var(--tone-skill))]",
  task: "bg-[hsl(var(--tone-task))]",
  git: "bg-[hsl(var(--tone-git))]",
  browser: "bg-[hsl(var(--tone-browser))]",
  media: "bg-[hsl(var(--tone-browser))]",
  tool: "bg-[hsl(var(--tone-tool))]",
  index: "bg-[hsl(var(--tone-index))]",
  error: "bg-[hsl(var(--tone-error))]",
  reasoning: "bg-[hsl(var(--composer-ask))]",
};

const DETAIL_MOTION = {
  type: "spring" as const,
  duration: 0.3,
  bounce: 0,
};

const FILE_CHIP_CLASS =
  "[&_[role=button]]:text-foreground/92 [&_[role=button]:hover]:text-foreground";

interface ActivityJournalProps {
  entries: ActivityJournalEntry[];
  streaming: boolean;
  onOpenFilePreview?: (path: string) => void;
  cliAppsByName?: Map<string, CliAppInfo>;
  mcpPresetsByName?: Map<string, McpPresetInfo>;
  /** Rows shown before the fold; the "compact" activity preference lowers it. */
  previewMax?: number;
  /** The "compact" activity preference: no preview lines under thinking rows. */
  compact?: boolean;
}

/**
 * The journal is the timeline: one colored row per thing the agent did, in
 * order, each opening on its own full card. While the turn runs the window
 * follows the tail so the newest action is always in view; once it is done
 * the first rows lead and the rest sit behind one button.
 */
export function ActivityJournal({
  entries,
  streaming,
  onOpenFilePreview,
  cliAppsByName,
  mcpPresetsByName,
  previewMax: previewMaxProp = JOURNAL_PREVIEW_MAX,
  compact = false,
}: ActivityJournalProps) {
  const { t } = useTranslation();
  const [verbosity, setVerbosity] = useState<ActivityVerbosity>(loadActivityVerbosity);
  const reduceMotion = useReducedMotion();
  const previewMax = previewMaxFor(verbosity, previewMaxProp);
  const [showAll, setShowAll] = useState(false);
  const [requestedPage, setPage] = useState<number | null>(null);
  if (entries.length === 0) return null;
  const lastPage = Math.ceil(entries.length / ACTIVITY_DETAIL_PAGE_SIZE) - 1;
  const page = Math.min(requestedPage ?? (streaming ? lastPage : 0), lastPage);
  const limit = Math.max(1, previewMax);
  const hidden = showAll ? 0 : Math.max(0, entries.length - limit);
  const visible = showAll
    ? entries.slice(page * ACTIVITY_DETAIL_PAGE_SIZE, (page + 1) * ACTIVITY_DETAIL_PAGE_SIZE)
    : hidden > 0
    ? streaming
      ? entries.slice(entries.length - limit)
      : entries.slice(0, limit)
    : entries;
  const canToggle = entries.length > limit;
  const toggle = canToggle ? (
    <button
      type="button"
      onClick={() => { setShowAll((current) => !current); setPage(null); }}
      aria-expanded={showAll}
      data-testid="activity-journal-open"
      className={cn(
        "inline-flex min-h-7 w-fit cursor-pointer items-center gap-1 rounded-md px-1 py-0.5 text-start text-[14.5px]",
        "text-foreground/70 transition-colors duration-200 hover:text-foreground",
        "active:scale-[0.96] motion-reduce:active:scale-100",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <ChevronRight
        aria-hidden
        className={cn(
          "rtl:[&:not(.rotate-90)]:rotate-180 h-3.5 w-3.5 transition-transform duration-200",
          showAll && "rotate-90",
        )}
      />
      <span>
        {showAll
          ? t("message.activityJournalShowLess", { defaultValue: "Show less" })
          : streaming
            ? t("message.activityJournalEarlier", {
                count: hidden,
                defaultValue: "{{count}} earlier steps",
              })
            : t("message.activityJournalShowAll", {
                count: hidden,
                defaultValue: "Show all · {{count}} more",
              })}
      </span>
    </button>
  ) : null;

  return (
    <div
      className="ms-1 mt-1.5 flex flex-col gap-0.5 ps-1 text-[14.5px] leading-6 antialiased"
      data-testid="activity-task-log"
    >
      {streaming && hidden > 0 ? toggle : null}
      {showAll ? <ActivityPagination page={page} total={entries.length} onPageChange={setPage} /> : null}
      {visible.length > VIRTUALIZE_AFTER ? (
        <VirtualJournalRows
          entries={visible}
          render={(entry) => (
            <JournalRow
              entry={entry}
              streaming={streaming}
              compact={compact}
              onOpenFilePreview={onOpenFilePreview}
              cliApp={entry.kind === "cli" && entry.name ? cliAppsByName?.get(entry.name.toLowerCase()) : undefined}
              mcpPreset={
                entry.kind === "mcp" && entry.mcpRun
                  ? mcpPresetsByName?.get(entry.mcpRun.presetName.toLowerCase())
                  : undefined
              }
            />
          )}
        />
      ) : visible.map((entry) => (
        <motion.div
          key={entry.id}
          initial={reduceMotion ? false : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          data-testid="activity-journal-row-motion"
        >
        <JournalRow
          entry={entry}
          streaming={streaming}
          compact={compact}
          onOpenFilePreview={onOpenFilePreview}
          cliApp={entry.kind === "cli" && entry.name ? cliAppsByName?.get(entry.name.toLowerCase()) : undefined}
          mcpPreset={
            entry.kind === "mcp" && entry.mcpRun
              ? mcpPresetsByName?.get(entry.mcpRun.presetName.toLowerCase())
              : undefined
          }
        />
        </motion.div>
      ))}
      {!(streaming && hidden > 0) ? toggle : null}
      {entries.length > 2 ? (
        <div className="mt-1 flex gap-1" role="radiogroup" data-testid="activity-verbosity">
          {([
            ["essential", t("message.activityModeEssential", { defaultValue: "Essential" })],
            ["standard", t("message.activityModeStandard", { defaultValue: "Standard" })],
            ["debug", t("message.activityModeDebug", { defaultValue: "Debug" })],
          ] as const).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={verbosity === mode}
              data-testid={`activity-verbosity-${mode}`}
              onClick={() => {
                setVerbosity(mode);
                saveActivityVerbosity(mode);
              }}
              className={cn(
                "rounded-md px-2 py-0.5 text-[12px] transition-colors",
                verbosity === mode ? "bg-foreground/[0.08] text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Above this many rows (Debug mode on long turns) only the rows on screen are mounted. */
export const VIRTUALIZE_AFTER = 150;

function VirtualJournalRows({
  entries,
  render,
}: {
  entries: ActivityJournalEntry[];
  render: (entry: ActivityJournalEntry) => ReactNode;
}) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 30,
    overscan: 12,
  });
  return (
    <div ref={parentRef} className="max-h-[70vh] overflow-y-auto" data-testid="activity-journal-virtual">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((item) => (
          <div
            key={entries[item.index].id}
            ref={virtualizer.measureElement}
            data-index={item.index}
            style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${item.start}px)` }}
          >
            {render(entries[item.index])}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Colored counters for a collapsed (past) turn: what happened, at a glance. */
export function ActivityDigest({
  digest,
  className,
}: {
  digest: ActivityJournalDigest;
  className?: string;
}) {
  const { t } = useTranslation();
  const parts: Array<{ key: string; tone: ActivityJournalTone; label: string }> = [];
  if (digest.files > 0) {
    parts.push({
      key: "files",
      tone: "explore",
      label: t("message.activityDigestFiles", { count: digest.files, defaultValue: "{{count}} files" }),
    });
  }
  if (digest.searches > 0) {
    parts.push({
      key: "searches",
      tone: "explore",
      label: t("message.activityDigestSearches", { count: digest.searches, defaultValue: "{{count}} searches" }),
    });
  }
  if (digest.edits > 0) {
    parts.push({
      key: "edits",
      tone: "edit",
      label: t("message.activityDigestEdits", { count: digest.edits, defaultValue: "{{count}} edits" }),
    });
  }
  if (digest.commands > 0) {
    parts.push({
      key: "commands",
      tone: "shell",
      label: t("message.activityDigestCommands", { count: digest.commands, defaultValue: "{{count}} commands" }),
    });
  }
  if (digest.tools > 0) {
    parts.push({
      key: "tools",
      tone: "tool",
      label: t("message.activityDigestTools", { count: digest.tools, defaultValue: "{{count}} tools" }),
    });
  }
  if (digest.recovered > 0) {
    parts.push({
      key: "recovered",
      tone: "task",
      label: t("message.activityDigestRecovered", { count: digest.recovered, defaultValue: "{{count}} fixed" }),
    });
  }
  if (digest.setup) {
    parts.push({ key: "setup", tone: "tool", label: t("settings.computer.setupNeeded") });
  }
  if (digest.errors > 0) {
    parts.push({
      key: "errors",
      tone: "error",
      label: t("message.activityDigestErrors", { count: digest.errors, defaultValue: "{{count}} failed" }),
    });
  }
  if (!parts.length) return null;
  return (
    <span
      className={cn("inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[14px]", className)}
      data-testid="activity-digest"
    >
      {parts.map((part, index) => (
        <span key={part.key} className="inline-flex items-center gap-1.5">
          {index > 0 ? <span className="text-foreground/30">·</span> : null}
          <span className={cn("inline-flex items-center gap-1 font-medium tabular-nums", JOURNAL_TONE_CLASS[part.tone])}>
            <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", JOURNAL_TONE_DOT[part.tone])} />
            {part.label}
          </span>
        </span>
      ))}
    </span>
  );
}

const PHASE_BG: Record<ActivityPhaseKey, string> = {
  thinking: "bg-[hsl(var(--composer-ask))]",
  explore: "bg-[hsl(var(--tone-explore))]",
  edit: "bg-[hsl(var(--tone-edit))]",
  command: "bg-[hsl(var(--tone-shell))]",
  tool: "bg-[hsl(var(--tone-tool))]",
};

const PHASE_LABEL: Record<ActivityPhaseKey, { key: string; defaultValue: string }> = {
  thinking: { key: "message.activityPhaseThinking", defaultValue: "Thinking" },
  explore: { key: "message.activityPhaseExplore", defaultValue: "Exploring" },
  edit: { key: "message.activityPhaseEdit", defaultValue: "Editing" },
  command: { key: "message.activityPhaseCommand", defaultValue: "Commands" },
  tool: { key: "message.activityPhaseTool", defaultValue: "Tools" },
};

/**
 * Where the turn's time went: one thin bar, segments in phase colors, and a
 * legend with the duration of each. Answers "13 minutes doing what?" without
 * opening anything.
 */
export function ActivityPhaseBar({
  phases,
  className,
}: {
  phases: ActivityPhaseShare[];
  className?: string;
}) {
  const { t } = useTranslation();
  const total = phases.reduce((sum, phase) => sum + phase.ms, 0);
  if (total <= 0 || phases.length === 0) return null;
  return (
    <div
      className={cn("flex flex-col gap-1", className)}
      data-testid="activity-phase-split"
      aria-label={t("message.activityPhaseTitle", { defaultValue: "Time by phase" })}
    >
      <div className="flex h-[3px] w-full max-w-[30rem] gap-px overflow-hidden rounded-full bg-foreground/[0.06]">
        {phases.map((phase) => (
          <span
            key={phase.key}
            className={cn("h-full", PHASE_BG[phase.key])}
            style={{ width: `${Math.max(1, (phase.ms / total) * 100)}%` }}
            data-phase={phase.key}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[13px] leading-4 text-foreground/55 tabular-nums">
        {phases.map((phase) => (
          <span key={phase.key} className="inline-flex items-center gap-1">
            <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", PHASE_BG[phase.key])} />
            <span>{t(PHASE_LABEL[phase.key].key, { defaultValue: PHASE_LABEL[phase.key].defaultValue })}</span>
            <span className="text-foreground/75">{formatActivityDuration(phase.ms, t)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** What the agent is doing right now, for the header while the turn runs. */
export function JournalNowLine({ entry }: { entry: ActivityJournalEntry }) {
  const tone = journalToneForEntry(entry);
  return (
    <span
      className="inline-flex min-w-0 items-center gap-1.5 truncate text-[14px]"
      data-testid="activity-now-line"
      data-tone={tone}
    >
      <span
        aria-hidden
        className={cn("h-1.5 w-1.5 shrink-0 rounded-full motion-safe:animate-pulse", JOURNAL_TONE_DOT[tone])}
      />
      <span className="min-w-0 truncate">
        <JournalLine entry={entry} streaming compact />
      </span>
    </span>
  );
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest("button, a, input, textarea, select, [role=button]"));
}

/**
 * The one status mark at the head of a row, Claude Code style: a spinner while
 * the step runs, a green check once it is done, a red cross when it returned
 * an error. Icon only - no "Failed" wording, the final message says whether
 * the task worked.
 */
/**
 * The mark of the index and graph tools, next to their verb: an orbit for the
 * code index, a network for the project graph, in the index colour. It spins
 * while the step runs (the index is being read or built) and settles once
 * done, so the project's intelligence at work is visible at a glance.
 */
function IndexMark({ tool, live, className }: { tool: string | undefined; live: boolean; className?: string }) {
  const { t } = useTranslation();
  const Icon = tool === "metagraph" ? Network : Orbit;
  return (
    <Icon
      aria-label={live
        ? t("message.activityJournalIndexWorking", { defaultValue: "Project index at work" })
        : t("message.activityJournalIndexUsed", { defaultValue: "Project index" })}
      className={cn(
        "h-3.5 w-3.5 shrink-0 self-center",
        JOURNAL_TONE_CLASS.index,
        live && "motion-safe:animate-spin [animation-duration:1.6s]",
        className,
      )}
      strokeWidth={2}
      data-testid="activity-index-mark"
      data-live={live ? "true" : undefined}
    />
  );
}

/** What a row did, as a quiet icon: the section view carries status on its header. */
function JournalKindIcon({ entry }: { entry: ActivityJournalEntry }) {
  const tool = entry.tool ?? "";
  if (entry.kind === "tool" && isIndexTool(tool)) {
    // Even in the quiet section view the index keeps its colour: it is the
    // one tool worth telling apart from plain file reads.
    return <IndexMark tool={tool} live={false} />;
  }
  const Icon = entry.kind === "read"
    ? FileText
    : entry.kind === "search"
      ? Search
      : entry.kind === "explore"
        ? FolderSearch
        : entry.kind === "reasoning"
          ? Brain
          : entry.kind === "edit"
            ? FilePen
            : entry.kind === "shell" || entry.kind === "cli"
              ? SquareTerminal
              : entry.kind === "mcp"
                ? Plug
                : entry.kind === "media"
                  ? ImageIcon
                  : tool === "approval"
                    ? ShieldCheck
                    : tool === "ask_user"
                      ? MessageCircleQuestion
                      : /spawn|team|subagent|agent/.test(tool)
                        ? Users
                        : /board|task|goal|ledger|plan/.test(tool)
                          ? ListChecks
                          : /skill/.test(tool)
                            ? BookOpen
                            : /git/.test(tool)
                              ? GitBranch
                              : /browser|web|fetch|scrape/.test(tool)
                                ? Globe
                                : /list_dir|find|glob/.test(tool)
                                  ? FolderSearch
                                  : Wrench;
  return <Icon aria-hidden className="h-3.5 w-3.5 shrink-0 text-foreground/55" strokeWidth={1.8} data-testid="activity-journal-kind-icon" />;
}

function JournalStatusIcon({ entry, streaming }: { entry: ActivityJournalEntry; streaming: boolean }) {
  const { t } = useTranslation();
  const running = entry.status === "running" && streaming;
  if (entry.kind === "reasoning") {
    return running ? (
      <CircleDashed
        aria-label={t("message.reasoningStreaming", { defaultValue: "Thinking…" })}
        className="h-3.5 w-3.5 shrink-0 animate-spin text-[hsl(var(--composer-ask))]/70 motion-reduce:animate-none"
        strokeWidth={1.8}
      />
    ) : (
      <Brain aria-hidden className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--composer-ask))]/60" />
    );
  }
  if (running) {
    return (
      <Loader2
        aria-label={t("message.activityJournalRunning", { defaultValue: "Running" })}
        className={cn("h-3.5 w-3.5 shrink-0 animate-spin", JOURNAL_TONE_CLASS[journalToneForEntry(entry)])}
      />
    );
  }
  if (entry.status === "error") {
    return (
      <X
        aria-label={t("message.activityJournalStatusError", { defaultValue: "Returned an error" })}
        className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--tone-error))]"
        strokeWidth={2.6}
        data-testid="activity-journal-status-error"
      />
    );
  }
  return (
    <Check
      aria-label={t("message.activityJournalDone", { defaultValue: "Done" })}
      className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--tone-edit))]"
      strokeWidth={2.6}
      data-testid="activity-journal-status-done"
    />
  );
}

export function JournalRow({
  entry,
  streaming,
  compact = false,
  plain = false,
  onOpenFilePreview,
  cliApp,
  mcpPreset,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  compact?: boolean;
  /** Section view: one quiet line (kind icon, no tone color, no inline preview); details on click. */
  plain?: boolean;
  onOpenFilePreview?: (path: string) => void;
  cliApp?: CliAppInfo;
  mcpPreset?: McpPresetInfo;
}) {
  const { t } = useTranslation();
  // Keep exploration and execution visible without an extra click. The index
  // and graph tools open too: their query and what the index answered (or
  // that it is still being built) are the point of the row.
  const [open, setOpen] = useState(() => !plain && (
    entry.kind === "explore"
    || entry.kind === "shell"
    || ["test_run", "verify", "project"].includes(entry.tool ?? "")
    || (entry.kind === "tool" && isIndexTool(entry.tool))
  ));
  const reduceMotion = useReducedMotion();
  const tone = journalToneForEntry(entry);
  const canExpand = journalHasDetails(entry);
  const expandLabel = open
    ? t("message.activityJournalHideDetail", { defaultValue: "Hide details" })
    : t("message.activityJournalShowDetail", { defaultValue: "Show details" });
  const toggle = () => setOpen((current) => !current);
  const onRowClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!canExpand) return;
    if (isInteractiveTarget(event.target)) return;
    if (typeof window !== "undefined" && window.getSelection()?.toString()) return;
    toggle();
  };

  const setupIssue = entry.status === "error" ? computerSetupIssue(entry.tool, entry.error) : null;
  if (setupIssue) {
    return <div data-kind={entry.kind} data-tone="tool" data-status="setup" data-testid="activity-journal-row">
      <ComputerSetupNotice issue={setupIssue} />
    </div>;
  }

  // Commands keep their colored state (check / cross) even in the quiet view.
  const quietIcon = plain
    && entry.kind !== "shell" && entry.kind !== "cli" && entry.kind !== "mcp"
    && entry.status !== "error" && !(entry.status === "running" && streaming);
  const statusIcon = quietIcon
    ? <JournalKindIcon entry={entry} />
    : <JournalStatusIcon entry={entry} streaming={streaming} />;

  return (
    <div
      data-kind={entry.kind}
      data-tone={tone}
      data-status={entry.status}
      data-testid="activity-journal-row"
    >
      <div
        onClick={onRowClick}
        className={cn(
          "group/journal-row flex min-h-7 min-w-0 items-center gap-2 rounded-md px-1 py-0.5",
          canExpand && "cursor-pointer transition-colors duration-200 hover:bg-foreground/[0.04]",
        )}
      >
        {canExpand ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={expandLabel}
            onClick={(event) => {
              event.stopPropagation();
              toggle();
            }}
            data-testid="activity-journal-toggle"
            className={cn(
              "-ms-0.5 inline-flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            {statusIcon}
          </button>
        ) : (
          <span
            className="-ms-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center"
            data-testid="activity-journal-dot"
          >
            {statusIcon}
          </span>
        )}
        {entry.kind === "cli" && cliApp ? (
          <BrandMark
            color={cliApp.brand_color || "#0891B2"}
            logo={cliApp.logo_url}
            initials={cliAppInitials(cliApp).slice(0, 2)}
            active={Boolean(entry.live)}
            testId={`activity-cli-logo-${entry.name?.toLowerCase() ?? "cli"}`}
          />
        ) : entry.kind === "mcp" && mcpPreset ? (
          <BrandMark
            color={mcpPreset.brand_color || "#6D5DF6"}
            logo={mcpPreset.logo_url}
            initials={mcpPresetInitials(mcpPreset).slice(0, 2)}
            active={Boolean(entry.live)}
            testId={`activity-mcp-logo-${entry.mcpRun?.presetName.toLowerCase() ?? "mcp"}`}
          />
        ) : null}
        <span className="min-w-0 flex-1 overflow-hidden">
          <JournalLine entry={entry} streaming={streaming} plain={plain} onOpenFilePreview={onOpenFilePreview} />
        </span>
        <JournalAside entry={entry} streaming={streaming} open={open} canExpand={canExpand} />
      </div>
      {plain || (open && canExpand && entry.tool !== "ask_user") ? null : (
        <JournalPreview
          entry={entry}
          streaming={streaming}
          compact={compact}
          onShowMore={canExpand && !open ? () => setOpen(true) : undefined}
        />
      )}
      <AnimatePresence initial={false}>
        {open && canExpand ? (
          <motion.div
            key={`${entry.id}-detail`}
            initial={reduceMotion ? false : { opacity: 0, y: -8, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={
              reduceMotion
                ? { opacity: 0 }
                : { opacity: 0, y: -8, filter: "blur(4px)", transition: { duration: 0.15 } }
            }
            transition={DETAIL_MOTION}
            className={cn(
              "mb-1 ms-[1.375rem] ps-2.5",
              entry.kind !== "shell" && entry.kind !== "reasoning" && "border-s",
              entry.kind === "explore" || plain ? "border-border/50" : JOURNAL_TONE_BORDER[tone] || JOURNAL_TONE_BORDER.tool,
            )}
            data-testid="activity-journal-detail"
          >
            <JournalDetails
              entry={entry}
              streaming={streaming}
              onOpenFilePreview={onOpenFilePreview}
              mcpPreset={mcpPreset}
            />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

const PREVIEW_LINE_CLASS = "block min-w-0 truncate whitespace-pre";
/** Two `leading-6` lines, fixed: a live preview must not change the row height. */
const LIVE_PREVIEW_TWO_LINES = "h-12 overflow-hidden";

/** Generic tool output worth a preview line: plain words, not a JSON dump. */
function toolPreviewText(entry: ActivityJournalEntry): string {
  if (entry.kind !== "tool" || !entry.output || entry.answer || entry.detail) return "";
  if (entry.tool === "computer" || entry.evidence?.length) return "";
  const first = firstOutputLine(entry.output);
  if (!first || /^[[{<]/.test(first)) return "";
  return first;
}

/**
 * Up to two muted lines under a row: the end of a command's output, the
 * answer to a question, a thought in progress. "Show more" opens the row.
 */
function JournalPreview({
  entry,
  streaming,
  compact,
  onShowMore,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  compact: boolean;
  onShowMore?: () => void;
}) {
  const { t } = useTranslation();
  const showMore = onShowMore ? (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onShowMore();
      }}
      data-testid="activity-journal-show-more"
      className={cn(
        "mt-0.5 inline-flex w-fit cursor-pointer rounded px-0.5 text-[13px] leading-5 text-foreground/50",
        "transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      {t("message.activityJournalShowMore", { defaultValue: "Show more" })}
    </button>
  ) : null;
  const wrapClass = "ms-[1.625rem] flex min-w-0 flex-col pb-0.5 pe-1";

  if (entry.kind === "reasoning") {
    if (compact || !entry.text) return null;
    const live = streaming && entry.status === "running";
    const full = reasoningSummary(entry.text, live ? 220 : 320, live);
    // Live, the newest lines are the ones worth reading.
    const summary = live ? full.split("\n").slice(-2).join("\n") : full;
    if (!summary) return null;
    return (
      <div className={wrapClass} data-testid="activity-reasoning-preview" data-live={live ? "true" : undefined}>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onShowMore?.();
          }}
          className={cn(
            "block w-full cursor-pointer text-start",
            // Live, the tail flips between one and two lines at every token;
            // the followed thread moved with it and the screen shook. Two
            // fixed lines while it streams, the real height once it settles.
            live && LIVE_PREVIEW_TWO_LINES,
          )}
        >
          <span className="line-clamp-1 whitespace-pre-line text-[14px] leading-6 text-muted-foreground antialiased">
            {summary}
          </span>
        </button>
      </div>
    );
  }

  if (entry.kind === "tool" && entry.tool === "ask_user") {
    const answer = entry.answer;
    if (!answer) return null;
    const text = answer.kind === "skipped"
      ? answer.text
        ? t("message.activityJournalAskSkipped", { label: answer.text, defaultValue: "Skipped, went with {{label}}" })
        : t("message.activityJournalAskSkippedPlain", { defaultValue: "Skipped" })
      : answer.kind === "timeout"
        ? answer.text
          ? t("message.activityJournalAskTimeout", { label: answer.text, defaultValue: "No answer, went with {{label}}" })
          : t("message.activityJournalAskTimeoutPlain", { defaultValue: "No answer" })
        : answer.kind === "stopped"
          ? t("message.activityJournalAskStopped", { defaultValue: "Stopped before an answer" })
          : answer.text;
    if (!text) return null;
    return (
      <div className={wrapClass} data-testid="activity-ask-answer">
        <span className="flex min-w-0 items-baseline gap-1.5 text-[14px] leading-6">
          <span aria-hidden className="inline-block shrink-0 text-foreground/45 rtl:-scale-x-100">→</span>
          <span className="min-w-0 truncate font-medium text-foreground/85" title={text}>{text}</span>
        </span>
      </div>
    );
  }

  if (entry.kind === "shell") {
    const run = entry.shellRun;
    const failed = entry.status === "error";
    const source = failed
      ? run?.error || entry.error || run?.output || entry.output
      : run?.output ?? entry.output;
    const { lines, total } = outputPreviewLines(source, 2);
    if (!lines.length) return null;
    const live = streaming && entry.status === "running";
    return (
      <div className={wrapClass} data-testid="activity-journal-preview" data-live={live ? "true" : undefined}>
        <pre
          dir="ltr"
          className={cn(
            "m-0 min-w-0 font-mono text-[13px] leading-5 text-foreground/50",
            // Folded while it runs, a command's tail goes 2 lines, 1, 2 as
            // output arrives: keep the two-line slot so the thread holds still.
            live && "min-h-10",
          )}
        >
          {lines.map((line, index) => (
            <span key={index} className={PREVIEW_LINE_CLASS}>{line}</span>
          ))}
        </pre>
        {total > lines.length ? showMore : null}
      </div>
    );
  }

  const notice = indexBackgroundNotice(entry);
  if (notice) {
    return (
      <div className={wrapClass} data-testid="activity-index-notice" data-live={streaming ? "true" : undefined}>
        <IndexNotice entry={entry} notice={notice} live={streaming} />
      </div>
    );
  }

  const text = toolPreviewText(entry);
  if (!text) return null;
  return (
    <div className={wrapClass} data-testid="activity-journal-preview">
      <span className={cn(PREVIEW_LINE_CLASS, "text-[13px] leading-5 text-foreground/50")}>{text}</span>
    </div>
  );
}

/**
 * The index coming online, as a highlighted line rather than muted output:
 * the colour of the index tools, a spinning orbit while the turn runs. Shown
 * under a folded row and at the top of its open details.
 */
function IndexNotice({ entry, notice, live }: { entry: ActivityJournalEntry; notice: string; live: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex w-fit max-w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[13px] font-medium leading-5",
        JOURNAL_TONE_CLASS.index,
        "bg-[hsl(var(--tone-index))]/10 ring-1 ring-inset ring-[hsl(var(--tone-index))]/30",
      )}
      title={notice}
    >
      <IndexMark tool={entry.tool} live={live} className="h-3 w-3" />
      <span className="min-w-0 truncate">{notice}</span>
    </span>
  );
}

function BrandMark({
  color,
  logo,
  initials,
  active,
  testId,
}: {
  color: string;
  logo?: string | null;
  initials: string;
  active: boolean;
  testId: string;
}) {
  const logoUrls = useMemo(() => logoFallbackUrls(logo ?? undefined), [logo]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  return (
    <span
      data-testid={testId}
      className={cn(
        "mt-[3px] grid h-4 w-4 shrink-0 place-items-center overflow-hidden rounded-[4px] text-[6.5px] font-semibold text-white",
        active && "motion-safe:animate-pulse",
      )}
      style={{
        backgroundColor: logoUrl ? "hsl(var(--background))" : color,
        boxShadow: `0 0 0 1px ${alphaColor(color, 30)}`,
      }}
      aria-hidden
    >
      {logoUrl ? (
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-[78%] w-[78%] object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      ) : (
        initials
      )}
    </span>
  );
}

function alphaColor(color: string, percent: number): string {
  if (/^#[0-9a-f]{6}$/i.test(color)) {
    const alpha = Math.round((percent / 100) * 255)
      .toString(16)
      .padStart(2, "0");
    return `${color}${alpha}`;
  }
  return `color-mix(in srgb, ${color} ${percent}%, transparent)`;
}

/** Facts at the end of the row: duration, diff, a recovered retry, the chevron. */
function JournalAside({
  entry,
  streaming,
  open = false,
  canExpand = false,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  open?: boolean;
  canExpand?: boolean;
}) {
  const { t } = useTranslation();
  const running = entry.status === "running" && streaming;
  const failed = isReportedFailure(entry);
  const duration = entry.durationMs !== undefined && entry.durationMs >= 1000
    ? formatActivityDuration(entry.durationMs, t)
    : null;
  return (
    <span
      className="ms-auto inline-flex shrink-0 items-center gap-1.5 text-[13px] leading-4 tabular-nums"
      data-testid="activity-journal-aside"
    >
      {entry.kind === "edit" && entry.hasStats && !running ? (
        <DiffPair added={entry.added ?? 0} deleted={entry.deleted ?? 0} />
      ) : null}
      {duration && !running ? (
        <span className="text-foreground/45">{duration}</span>
      ) : null}
      {failed ? (
        <span className={cn("inline-flex items-center gap-1 font-medium", JOURNAL_TONE_CLASS.error)}>
          <AlertCircle aria-hidden className="h-3.5 w-3.5" />
          {t("settings.computer.setupNeeded")}
        </span>
      ) : entry.recovered ? (
        <span className={cn("inline-flex items-center gap-1 font-medium", JOURNAL_TONE_CLASS.task)}>
          {t("message.activityJournalRecovered", { defaultValue: "Fixed" })}
        </span>
      ) : null}
      {canExpand ? (
        <ChevronRight
          aria-hidden
          className={cn(
            "rtl:[&:not(.rotate-90)]:rotate-180 h-3.5 w-3.5 text-foreground/30 opacity-0 transition-[transform,opacity] duration-200",
            "group-hover/journal-row:opacity-100",
            open && "rotate-90 opacity-100",
          )}
        />
      ) : null}
    </span>
  );
}

function CountBadge({ count }: { count?: number }) {
  if (!count || count <= 1) return null;
  return (
    <span
      className="rounded-full bg-foreground/[0.07] px-1.5 text-[13px] font-medium tabular-nums text-foreground/70"
      data-testid="activity-journal-count"
    >
      ×{count}
    </span>
  );
}

function FileTarget({
  path,
  file,
  onOpenFilePreview,
  className,
}: {
  path?: string;
  file?: string;
  onOpenFilePreview?: (path: string) => void;
  className?: string;
}) {
  if (path && onOpenFilePreview) {
    return (
      <FileReferenceChip
        path={path}
        previewPath={path}
        onOpen={onOpenFilePreview}
        showActions={false}
        className={cn(FILE_CHIP_CLASS, className)}
        textClassName="text-[14.5px] font-medium"
        testId="activity-journal-file"
      />
    );
  }
  return <span className={cn(JOURNAL_TARGET_CLASS, className)} title={path}>{file || path}</span>;
}

/** Section view: the file name as plain text, still opening its preview. */
function PlainFileTarget({
  path,
  file,
  onOpenFilePreview,
}: {
  path?: string;
  file?: string;
  onOpenFilePreview?: (path: string) => void;
}) {
  const label = file || (path ? path.split(/[\\/]/).pop() : "") || path;
  if (path && onOpenFilePreview) {
    return (
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onOpenFilePreview(path);
        }}
        title={path}
        className={cn(
          JOURNAL_TARGET_CLASS,
          "min-w-0 truncate rounded-sm font-normal underline-offset-2 hover:underline",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
        data-testid="activity-journal-file"
      >
        {label}
      </button>
    );
  }
  return <span className={cn(JOURNAL_TARGET_CLASS, "font-normal")} title={path}>{label}</span>;
}

/** A task title that jumps to its card on the board. */
function TaskTarget({
  link,
  label,
  className,
}: {
  link: BoardTaskFocusRequest;
  label: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const title = t("message.activityJournalOpenTask", { defaultValue: "Open on the board" });
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        requestOpenBoardTask(link);
      }}
      title={title}
      aria-label={`${label} - ${title}`}
      data-testid="activity-journal-task"
      className={cn(
        JOURNAL_TARGET_CLASS,
        "cursor-pointer rounded-[3px] text-start underline-offset-[3px] decoration-[hsl(var(--tone-task))]/50",
        "transition-colors hover:text-foreground hover:underline",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      {label}
    </button>
  );
}

function CopyCommandButton({ command }: { command: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  const copy = async (event: MouseEvent) => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  const label = copied
    ? t("message.activityJournalCopied", { defaultValue: "Copied" })
    : t("message.activityJournalCopy", { defaultValue: "Copy command" });
  return (
    <button
      type="button"
      onClick={(event) => void copy(event)}
      aria-label={label}
      title={label}
      data-testid="activity-journal-copy"
      className={cn(
        "inline-flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded align-middle",
        "text-foreground/40 transition-[color,opacity] duration-200 hover:text-foreground",
        "opacity-60 group-hover/journal-row:opacity-100 focus-visible:opacity-100",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        copied && "text-[hsl(var(--tone-explore))] opacity-100",
      )}
    >
      {copied ? <Check aria-hidden className="h-3 w-3" /> : <Copy aria-hidden className="h-3 w-3" />}
    </button>
  );
}

function JournalLine({
  entry,
  streaming,
  compact = false,
  plain = false,
  onOpenFilePreview,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  /** Header use: no chips or copy controls, one line. */
  compact?: boolean;
  /** Section view: the verb reads as plain text, no tone color. */
  plain?: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const toneClass = plain
    ? JOURNAL_PLAIN_VERB_CLASS
    : JOURNAL_TONE_CLASS[journalToneForEntry(entry)] || JOURNAL_TONE_CLASS.tool;
  const verbClass = cn("shrink-0", plain ? "font-normal" : "font-medium", toneClass);
  const live = Boolean(entry.live || (streaming && entry.status === "running"));
  // One line, always: the target truncates with an ellipsis instead of
  // wrapping under the verb; the full text is in the tooltip.
  const wrap = compact
    ? "inline-flex min-w-0 items-baseline gap-x-1.5 truncate"
    : "flex min-w-0 items-baseline gap-x-1.5 overflow-hidden whitespace-nowrap";
  const targetClass = cn(JOURNAL_TARGET_CLASS, "min-w-0 truncate");
  const fileTarget = compact
    ? <span className={targetClass}>{entry.file || entry.path}</span>
    : plain
      ? <PlainFileTarget path={entry.path} file={entry.file} onOpenFilePreview={onOpenFilePreview} />
      : <FileTarget path={entry.path} file={entry.file} onOpenFilePreview={onOpenFilePreview} />;

  if (entry.kind === "reasoning") {
    return (
      <span className={wrap}>
        <span className={cn("shrink-0", plain ? cn("font-normal", JOURNAL_PLAIN_VERB_CLASS) : cn("font-medium", REASONING_LABEL_CLASS))}>
          {live
            ? t("message.reasoningStreaming", { defaultValue: "Thinking…" })
            : t("message.reasoningDone", { defaultValue: "Thought" })}
        </span>
      </span>
    );
  }

  if (entry.kind === "explore") {
    const files = entry.files ?? 0;
    const searches = entry.searches ?? 0;
    const verb = live
      ? t("message.activityJournalVerbExploring", { defaultValue: "Exploring" })
      : t("message.activityJournalVerbExplored", { defaultValue: "Explored" });
    return (
      <span className={wrap}>
        <span className={verbClass}>{verb}</span>
        {files > 0 ? (
          <span>
            <span className={cn(JOURNAL_TARGET_CLASS, "tabular-nums")}>{files}</span>
            {" "}
            <span className={JOURNAL_META_CLASS}>
              {t("message.activityJournalFiles", { count: files, defaultValue: "files" })}
            </span>
          </span>
        ) : null}
        {files > 0 && searches > 0 ? <span className={JOURNAL_META_CLASS}>,</span> : null}
        {searches > 0 ? (
          <span>
            <span className={cn(JOURNAL_TARGET_CLASS, "tabular-nums")}>{searches}</span>
            {" "}
            <span className={JOURNAL_META_CLASS}>
              {t("message.activityJournalSearches", { count: searches, defaultValue: "searches" })}
            </span>
          </span>
        ) : null}
      </span>
    );
  }

  if (entry.kind === "read") {
    return (
      <span className={wrap}>
        <span className={verbClass}>
          {live
            ? t("message.activityJournalVerbReading", { defaultValue: "Reading" })
            : t("message.activityJournalVerbRead", { defaultValue: "Read" })}
        </span>
        <span className="min-w-0 truncate">{fileTarget}</span>
        {!compact ? <CountBadge count={entry.count} /> : null}
      </span>
    );
  }

  if (entry.kind === "search") {
    return (
      <span className={wrap}>
        <span className={verbClass}>
          {live
            ? t("message.activityJournalVerbSearching", { defaultValue: "Searching" })
            : t("message.activityJournalVerbSearched", { defaultValue: "Searched" })}
        </span>
        {entry.query ? <span className={targetClass} title={entry.query}>{entry.query}</span> : null}
      </span>
    );
  }

  if (entry.kind === "shell" || entry.kind === "cli") {
    const verb = isReportedFailure(entry)
      ? t("settings.computer.setupNeeded")
      : live
        ? t("message.activityJournalVerbRunning", { defaultValue: "Running" })
        : t("message.activityJournalVerbRan", { defaultValue: "Ran" });
    const target = entry.kind === "cli"
      ? `@${entry.name}${entry.detail ? ` ${entry.detail}` : ""}`
      : entry.command ?? "";
    const fullCommand = entry.kind === "shell"
      ? entry.shellRun?.command ?? entry.fullCommand ?? entry.command ?? ""
      : "";
    const extraLines = entry.kind === "shell" ? entry.commandExtraLines ?? 0 : 0;
    return (
      <span className={wrap}>
        <span className={verbClass}>{verb}</span>
        {entry.kind === "shell" ? (
          <SandboxBadge
            sandbox={entry.shellRun?.sandbox}
            lifted={entry.shellRun?.sandboxLifted}
            compact={compact}
          />
        ) : null}
        <span
          dir="ltr"
          className={cn(targetClass, "font-mono text-[14px]")}
          title={fullCommand || target}
          data-testid="activity-journal-command"
        >
          {target}
        </span>
        {!compact && extraLines > 0 ? (
          <span className={cn(JOURNAL_META_CLASS, "shrink-0 text-[13px]")}>
            {t("message.activityJournalMoreLines", { count: extraLines, defaultValue: "+{{count}} lines" })}
          </span>
        ) : null}
        {!compact && fullCommand ? <CopyCommandButton command={fullCommand} /> : null}
        {!compact ? <CountBadge count={entry.count} /> : null}
        {!compact && entry.attempts && entry.attempts > 1 ? (
          <span className={cn(JOURNAL_META_CLASS, "shrink-0")}>
            {t("message.activityJournalAttempts", {
              count: entry.attempts,
              defaultValue: "{{count}} attempts",
            })}
          </span>
        ) : null}
      </span>
    );
  }

  if (entry.kind === "mcp") {
    return (
      <span className={wrap}>
        <span className={verbClass}>
          {live
            ? t("message.activityJournalVerbCalling", { defaultValue: "Calling" })
            : t("message.activityJournalVerbUsed", { defaultValue: "Used" })}
        </span>
        <span className={targetClass}>{entry.name}</span>
        {entry.tool ? <span className={cn(JOURNAL_META_CLASS, "min-w-0 truncate font-mono text-[14px]")}>{entry.tool}</span> : null}
      </span>
    );
  }

  if (entry.kind === "edit") {
    const verb = entry.operation === "delete"
      ? t("message.activityJournalVerbDeleted", { defaultValue: "Deleted" })
      : live
        ? t("message.activityJournalVerbEditing", { defaultValue: "Editing" })
        : t("message.activityJournalVerbEdited", { defaultValue: "Edited" });
    return (
      <span className={wrap}>
        <span className={verbClass}>{verb}</span>
        <span className="min-w-0 truncate">{fileTarget}</span>
        {!compact ? <CountBadge count={entry.count} /> : null}
      </span>
    );
  }

  if (entry.kind === "media") {
    return (
      <span className={wrap}>
        <span className={verbClass}>
          {t("message.activityJournalMedia", { defaultValue: "Found media" })}
        </span>
        {entry.evidence?.length ? (
          <span className={cn(JOURNAL_META_CLASS, "tabular-nums")}>{entry.evidence.length}</span>
        ) : null}
      </span>
    );
  }

  const verb = t(entry.labelKey || "message.activityTool.using", {
    defaultValue: entry.defaultValue || "Using",
  });
  const taskLink = compact ? null : journalTaskLink(entry);
  const indexTool = entry.kind === "tool" && isIndexTool(entry.tool);
  return (
    <span className={wrap}>
      <span className={cn(verbClass, indexTool && !plain && "font-semibold")}>{verb}</span>
      {indexTool ? <IndexMark tool={entry.tool} live={live} /> : null}
      {taskLink ? (
        <TaskTarget
          link={taskLink}
          label={entry.target || `#${(entry.taskId ?? "").slice(0, 8)}`}
          className={targetClass}
        />
      ) : entry.target ? (
        <span className={targetClass} title={entry.target}>{entry.target}</span>
      ) : null}
      {entry.detail && entry.kind === "tool" ? (
        <span className={cn(JOURNAL_META_CLASS, "min-w-0 truncate")} title={entry.detail}>
          {entry.detail}
        </span>
      ) : null}
      {!compact ? <CountBadge count={entry.count} /> : null}
    </span>
  );
}

function ExploreOperations({ entry, streaming, onOpenFilePreview }: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const [requestedPage, setPage] = useState(0);
  const operations = entry.operations ?? [];
  const page = Math.min(requestedPage, Math.max(0, Math.ceil(operations.length / ACTIVITY_DETAIL_PAGE_SIZE) - 1));
  const start = page * ACTIVITY_DETAIL_PAGE_SIZE;
  return (
    <div className="py-0.5">
      <ul className="space-y-1 text-[14px] leading-5">
        {operations.slice(start, start + ACTIVITY_DETAIL_PAGE_SIZE).map((operation, index) => {
          const reading = operation.kind === "read";
          return (
            <li key={start + index} className="flex min-w-0 items-baseline gap-2" data-testid="activity-explore-operation" data-status={operation.status}>
              <span className={cn("shrink-0 font-medium", JOURNAL_TONE_CLASS[reading ? "read" : "search"])}>
                {reading
                  ? t("message.activityJournalVerbRead", { defaultValue: "Read" })
                  : t("message.activityJournalOperationSearch", { defaultValue: "Search" })}
              </span>
              <span className="min-w-0 flex-1 break-words text-foreground/85">
                {reading
                  ? <PathLine path={operation.path} onOpenFilePreview={onOpenFilePreview} />
                  : operation.query || operation.tool}
              </span>
              {operation.status === "error" ? (
                <span className={JOURNAL_TONE_CLASS.error}>{t("settings.computer.setupNeeded")}</span>
              ) : streaming && operation.status === "running" ? (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin motion-reduce:animate-none" aria-label={t("message.shellRunRunning", { defaultValue: "Running" })} />
              ) : null}
            </li>
          );
        })}
      </ul>
      <ActivityPagination page={page} total={operations.length} onPageChange={setPage} />
    </div>
  );
}

function JournalDetails({
  entry,
  streaming,
  onOpenFilePreview,
  mcpPreset,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  onOpenFilePreview?: (path: string) => void;
  mcpPreset?: McpPresetInfo;
}) {
  const { t } = useTranslation();

  if (entry.kind === "explore" && entry.operations?.length) {
    return <ExploreOperations entry={entry} streaming={streaming} onOpenFilePreview={onOpenFilePreview} />;
  }

  if (entry.kind === "reasoning") {
    return (
      <ReasoningJournalBody
        text={entry.text ?? ""}
        streaming={streaming && entry.status === "running"}
        onOpenFilePreview={onOpenFilePreview}
      />
    );
  }

  if (entry.kind === "shell") {
    const run: ShellRunSummary = entry.shellRun ?? {
      key: entry.id,
      command: entry.fullCommand ?? entry.command ?? "",
      // The card has no stopped state of its own: a stopped run shows as done.
      status: entry.status === "interrupted" ? "done" : entry.status,
      output: entry.output,
      error: entry.error,
    };
    // The row above already names the command: the card only adds what the
    // one-line row cannot hold (the full multi-line script, the output).
    return (
      <div className="py-1">
        <ShellRunCard run={run} active={streaming} hideCommand />
      </div>
    );
  }

  const facts = entry.kind === "cli"
    ? [
        { label: "name", value: `@${entry.name ?? ""}` },
        ...(entry.detail ? [{ label: "args", value: entry.detail }] : []),
        ...(entry.cliRun?.workingDir ? [{ label: "cwd", value: entry.cliRun.workingDir }] : []),
      ]
    : entry.kind === "mcp"
      ? [
          { label: "name", value: mcpPreset?.display_name || entry.name || "" },
          ...(entry.tool ? [{ label: "tool", value: entry.tool }] : []),
          ...(entry.detail ? [{ label: "args", value: entry.detail }] : []),
        ]
      : entry.facts ?? [];

  return (
    <div className="flex flex-col gap-2 py-1 text-[14px] leading-5">
      {entry.path ? (
        <DetailBlock label={t("message.activityJournalPath", { defaultValue: "Path" })}>
          <PathLine path={entry.path} onOpenFilePreview={onOpenFilePreview} />
        </DetailBlock>
      ) : null}
      {entry.kind === "search" && entry.query ? (
        <DetailBlock label={t("message.activityJournalSearchesHeading", { defaultValue: "Searches" })}>
          <span className="text-foreground/88 [text-wrap:pretty]">{entry.query}</span>
          {entry.tool ? <span className="ms-1.5 font-mono text-[13px] text-foreground/45">{entry.tool}</span> : null}
        </DetailBlock>
      ) : null}
      {entry.filesList?.length ? (
        <DetailList
          label={t("message.activityJournalFilesHeading", { defaultValue: "Files" })}
          items={entry.filesList}
          renderItem={(item) => <PathLine path={item} onOpenFilePreview={onOpenFilePreview} />}
        />
      ) : null}
      {entry.queriesList?.length ? (
        <DetailList
          label={t("message.activityJournalSearchesHeading", { defaultValue: "Searches" })}
          items={entry.queriesList}
          renderItem={(item) => <span className="text-foreground/88 [text-wrap:pretty]">{item}</span>}
        />
      ) : null}
      {facts.length ? (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 gap-y-0.5">
          {facts.map((fact) => (
            <FactRow key={`${fact.label}:${fact.value}`} fact={fact} />
          ))}
        </dl>
      ) : null}
      {entry.occurrences && entry.occurrences.length > 1 ? (
        <DetailList
          label={t("message.activityJournalOccurrences", { defaultValue: "Calls" })}
          items={entry.occurrences}
          renderItem={(item) => <span className="text-foreground/85 [text-wrap:pretty]">{item}</span>}
        />
      ) : null}
      {entry.error ? (
        <DetailBlock label={t("message.activityJournalError", { defaultValue: "Error" })}>
          <pre dir="ltr" className={cn(OUTPUT_PRE_CLASS, "text-[hsl(var(--tone-error))]/90")}>
            {entry.error.slice(0, 2000)}
          </pre>
        </DetailBlock>
      ) : null}
      {indexBackgroundNotice(entry) ? (
        <div className="pb-1">
          <IndexNotice entry={entry} notice={indexBackgroundNotice(entry)} live={streaming && entry.status === "running"} />
        </div>
      ) : null}
      {entry.output && entry.kind !== "cli" ? (
        <DetailBlock label={t("message.activityJournalOutput", { defaultValue: "Output" })}>
          <JournalOutput text={entry.tool === "computer" ? entry.output.replace(/screenshot/gi, "Screen") : entry.output} />
        </DetailBlock>
      ) : null}
      {entry.evidence?.length ? <ActivityEvidencePreview evidence={entry.evidence} /> : null}
    </div>
  );
}

const OUTPUT_PRE_CLASS = cn(
  "max-h-48 overflow-auto rounded-md px-2 py-1.5 font-mono text-[13px] leading-5",
  "whitespace-pre-wrap break-words text-foreground/80",
  "shadow-[0_0_0_1px_rgba(0,0,0,0.06)] dark:shadow-[0_0_0_1px_rgba(255,255,255,0.08)]",
  "bg-black/[0.03] dark:bg-white/[0.04]",
);

function JournalOutput({ text }: { text: string }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLPreElement>(null);
  const tidy = useMemo(() => tidyRunOutput(text), [text]);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [tidy.body]);
  if (!tidy.body && !hasRunOutputNote(tidy)) {
    return (
      <p className="text-[13px] text-foreground/50">
        {t("message.shellRunNoOutput", { defaultValue: "No output" })}
      </p>
    );
  }
  return (
    <>
      {tidy.body ? (
        <pre ref={ref} dir="ltr" tabIndex={0} className={OUTPUT_PRE_CLASS}><HighlightedOutput text={tidy.body} /></pre>
      ) : null}
      <RunOutputFooter run={tidy} variant="plain" />
    </>
  );
}

function FactRow({ fact }: { fact: { label: string; value: string } }) {
  const { t } = useTranslation();
  const label = t(`message.activityJournalFact.${fact.label}`, {
    defaultValue: fact.label,
  });
  return (
    <>
      <dt className="text-foreground/48">{label}</dt>
      <dd className="min-w-0 text-foreground/90 [text-wrap:pretty]">{fact.value}</dd>
    </>
  );
}

function DetailBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="text-[13px] font-medium text-foreground/48">{label}</div>
      {children}
    </div>
  );
}

function DetailList({
  label,
  items,
  renderItem,
}: {
  label: string;
  items: string[];
  renderItem: (item: string) => ReactNode;
}) {
  const { t } = useTranslation();
  const hidden = Math.max(0, items.length - JOURNAL_DETAIL_LIST_MAX);
  const visible = hidden > 0 ? items.slice(0, JOURNAL_DETAIL_LIST_MAX) : items;
  return (
    <div className="flex flex-col gap-0.5">
      <div className="text-[13px] font-medium text-foreground/48">
        {label}
        <span className="ms-1 tabular-nums text-foreground/35">{items.length}</span>
      </div>
      <ul className="max-h-52 overflow-y-auto pe-1">
        {visible.map((item) => (
          <li key={item} className="py-px">
            {renderItem(item)}
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <p className="text-[13px] text-foreground/45">
          {t("message.activityJournalListMore", {
            count: hidden,
            defaultValue: "+{{count}} more",
          })}
        </p>
      ) : null}
    </div>
  );
}

function PathLine({
  path,
  onOpenFilePreview,
}: {
  path: string;
  onOpenFilePreview?: (path: string) => void;
}) {
  if (onOpenFilePreview) {
    return (
      <FileReferenceChip
        path={path}
        previewPath={path}
        display="path"
        onOpen={onOpenFilePreview}
        showActions={false}
        className={FILE_CHIP_CLASS}
        textClassName="font-mono text-[14px]"
        testId="activity-journal-file"
      />
    );
  }
  const base = path.split(/[\\/]/).pop() || path;
  const dir = path.length > base.length ? path.slice(0, path.length - base.length) : "";
  return (
    <span dir="ltr" className="font-mono text-[14px] [text-wrap:pretty]" title={path}>
      {dir ? <span className="text-foreground/42">{dir}</span> : null}
      <span className="text-foreground/90">{base}</span>
    </span>
  );
}
