// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  AlertCircle,
  BookOpen,
  Check,
  ChevronRight,
  Copy,
  FileText,
  FolderSearch,
  GitBranch,
  Globe,
  Image as ImageIcon,
  ListTodo,
  Loader2,
  Pencil,
  Search,
  Server,
  SquareTerminal,
  Terminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { cliAppInitials, mcpPresetInitials } from "@/components/CliAppMentionText";
import { FileReferenceChip } from "@/components/FileReferenceChip";
import { ComputerSetupNotice } from "@/components/settings/ComputerSetupNotice";
import { ActivityEvidencePreview } from "@/components/thread/activity/ActivityEvidencePreview";
import { DiffPair } from "@/components/thread/activity/DiffPair";
import { SandboxBadge, ShellRunCard, type ShellRunSummary } from "@/components/thread/activity/ShellRunCard";
import {
  JOURNAL_DETAIL_LIST_MAX,
  JOURNAL_PREVIEW_MAX,
  formatActivityDuration,
  journalHasDetails,
  journalTaskLink,
  journalToneForEntry,
  type ActivityJournalDigest,
  type ActivityJournalEntry,
  type ActivityJournalTone,
  type ActivityPhaseKey,
  type ActivityPhaseShare,
} from "@/components/thread/activity/activityJournalModel";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import { logoFallbackUrls } from "@/lib/provider-brand";
import { computerSetupIssue } from "@/lib/computer-setup";
import type { CliAppInfo, McpPresetInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { requestOpenBoardTask, type BoardTaskFocusRequest } from "@/lib/workbench-events";

export const JOURNAL_TARGET_CLASS = "text-foreground/92 font-medium [text-wrap:pretty]";
export const JOURNAL_META_CLASS = "text-foreground/58";

/**
 * One hue per operation family, shared with the header digest so green always
 * means exploration and blue always means reading. Values live in globals.css
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
  error: "text-[hsl(var(--tone-error))]",
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
  error: "border-[hsl(var(--tone-error))]/40",
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
  error: "bg-[hsl(var(--tone-error))]",
};

const JOURNAL_TONE_ICON: Record<ActivityJournalTone, LucideIcon> = {
  explore: FolderSearch,
  read: FileText,
  search: Search,
  edit: Pencil,
  shell: Terminal,
  cli: SquareTerminal,
  mcp: Server,
  skill: BookOpen,
  task: ListTodo,
  git: GitBranch,
  browser: Globe,
  media: ImageIcon,
  tool: Wrench,
  error: AlertCircle,
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
  previewMax = JOURNAL_PREVIEW_MAX,
}: ActivityJournalProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  if (entries.length === 0) return null;
  const limit = Math.max(1, previewMax);
  const hidden = showAll ? 0 : Math.max(0, entries.length - limit);
  const visible = hidden > 0
    ? streaming
      ? entries.slice(entries.length - limit)
      : entries.slice(0, limit)
    : entries;
  const canToggle = entries.length > limit;
  const toggle = canToggle ? (
    <button
      type="button"
      onClick={() => setShowAll((current) => !current)}
      aria-expanded={showAll}
      data-testid="activity-journal-open"
      className={cn(
        "inline-flex min-h-7 w-fit cursor-pointer items-center gap-1 rounded-md px-1 py-0.5 text-left text-[12.5px]",
        "text-foreground/70 transition-colors duration-200 hover:text-foreground",
        "active:scale-[0.96] motion-reduce:active:scale-100",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <ChevronRight
        aria-hidden
        className={cn(
          "h-3.5 w-3.5 transition-transform duration-200",
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
      className="ml-1 mt-1.5 flex flex-col gap-0.5 pl-1 text-[13px] leading-6 antialiased"
      data-testid="activity-task-log"
    >
      {streaming && hidden > 0 ? toggle : null}
      {visible.map((entry) => (
        <JournalRow
          key={entry.id}
          entry={entry}
          streaming={streaming}
          onOpenFilePreview={onOpenFilePreview}
          cliApp={entry.kind === "cli" && entry.name ? cliAppsByName?.get(entry.name.toLowerCase()) : undefined}
          mcpPreset={
            entry.kind === "mcp" && entry.mcpRun
              ? mcpPresetsByName?.get(entry.mcpRun.presetName.toLowerCase())
              : undefined
          }
        />
      ))}
      {!(streaming && hidden > 0) ? toggle : null}
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
      className={cn("inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[12px]", className)}
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
      <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[11px] leading-4 text-foreground/55 tabular-nums">
        {phases.map((phase) => (
          <span key={phase.key} className="inline-flex items-center gap-1">
            <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", PHASE_BG[phase.key])} />
            <span>{t(PHASE_LABEL[phase.key].key, { defaultValue: PHASE_LABEL[phase.key].defaultValue })}</span>
            <span className="text-foreground/75">{formatActivityDuration(phase.ms)}</span>
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
      className="inline-flex min-w-0 items-center gap-1.5 truncate text-[12px]"
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

function JournalRow({
  entry,
  streaming,
  onOpenFilePreview,
  cliApp,
  mcpPreset,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  onOpenFilePreview?: (path: string) => void;
  cliApp?: CliAppInfo;
  mcpPreset?: McpPresetInfo;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const reduceMotion = useReducedMotion();
  const tone = journalToneForEntry(entry);
  const toneClass = JOURNAL_TONE_CLASS[tone] || JOURNAL_TONE_CLASS.tool;
  const Icon = JOURNAL_TONE_ICON[tone] || Wrench;
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
          "group/journal-row flex min-h-7 items-start gap-1.5 rounded-md px-1 py-0.5",
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
              "-ml-0.5 mt-1 inline-flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <ChevronRight
              aria-hidden
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-200",
                toneClass,
                open && "rotate-90",
              )}
            />
          </button>
        ) : (
          <span
            aria-hidden
            className="-ml-0.5 mt-1 inline-flex h-4 w-4 shrink-0 items-center justify-center"
            data-testid="activity-journal-dot"
          >
            <span className={cn("h-1.5 w-1.5 rounded-full opacity-70", JOURNAL_TONE_DOT[tone])} />
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
        ) : (
          <Icon aria-hidden className={cn("mt-1 h-3.5 w-3.5 shrink-0", toneClass)} />
        )}
        <span className="min-w-0 flex-1 pt-px">
          <JournalLine entry={entry} streaming={streaming} onOpenFilePreview={onOpenFilePreview} />
        </span>
        <JournalAside entry={entry} streaming={streaming} />
      </div>
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
              "mb-1 ml-[1.125rem] border-l-2 pl-2.5",
              JOURNAL_TONE_BORDER[tone] || JOURNAL_TONE_BORDER.tool,
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

/** Outcome at the end of the row: spinner, check, failure, duration, diff. */
function JournalAside({ entry, streaming }: { entry: ActivityJournalEntry; streaming: boolean }) {
  const { t } = useTranslation();
  const running = entry.status === "running" && streaming;
  const failed = entry.status === "error";
  const showsOutcome = entry.kind === "shell"
    || entry.kind === "cli"
    || entry.kind === "mcp"
    || entry.kind === "tool"
    || entry.kind === "edit";
  const duration = entry.durationMs !== undefined && entry.durationMs >= 1000
    ? formatActivityDuration(entry.durationMs)
    : null;
  return (
    <span
      className="ml-auto inline-flex shrink-0 items-center gap-1.5 pt-1 text-[11px] leading-4 tabular-nums"
      data-testid="activity-journal-aside"
    >
      {entry.kind === "edit" && entry.hasStats && !running ? (
        <DiffPair added={entry.added ?? 0} deleted={entry.deleted ?? 0} />
      ) : null}
      {duration && !running ? (
        <span className="text-foreground/45">{duration}</span>
      ) : null}
      {running ? (
        <Loader2
          aria-label={t("message.activityJournalRunning", { defaultValue: "Running" })}
          className={cn("h-3.5 w-3.5 animate-spin", JOURNAL_TONE_CLASS[journalToneForEntry(entry)])}
        />
      ) : failed ? (
        <span className={cn("inline-flex items-center gap-1 font-medium", JOURNAL_TONE_CLASS.error)}>
          <AlertCircle aria-hidden className="h-3.5 w-3.5" />
          {t("message.activityJournalFailedTag", { defaultValue: "Failed" })}
        </span>
      ) : entry.recovered ? (
        <span className={cn("inline-flex items-center gap-1 font-medium", JOURNAL_TONE_CLASS.task)}>
          <Check aria-hidden className="h-3.5 w-3.5" />
          {t("message.activityJournalRecovered", { defaultValue: "Failed, then fixed" })}
        </span>
      ) : showsOutcome ? (
        <Check
          aria-label={t("message.activityJournalDone", { defaultValue: "Done" })}
          className="h-3.5 w-3.5 text-[hsl(var(--tone-explore))]/70"
        />
      ) : null}
    </span>
  );
}

function CountBadge({ count }: { count?: number }) {
  if (!count || count <= 1) return null;
  return (
    <span
      className="rounded-full bg-foreground/[0.07] px-1.5 text-[11px] font-medium tabular-nums text-foreground/70"
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
        textClassName="text-[13px] font-medium"
        testId="activity-journal-file"
      />
    );
  }
  return <span className={cn(JOURNAL_TARGET_CLASS, className)} title={path}>{file || path}</span>;
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
        "cursor-pointer rounded-[3px] text-left underline-offset-[3px] decoration-[hsl(var(--tone-task))]/50",
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
  onOpenFilePreview,
}: {
  entry: ActivityJournalEntry;
  streaming: boolean;
  /** Header use: no chips or copy controls, one line. */
  compact?: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const toneClass = JOURNAL_TONE_CLASS[journalToneForEntry(entry)] || JOURNAL_TONE_CLASS.tool;
  const verbClass = cn("shrink-0 font-medium", toneClass);
  const live = Boolean(entry.live || (streaming && entry.status === "running"));
  const wrap = compact
    ? "inline-flex min-w-0 items-baseline gap-x-1.5 truncate"
    : "flex min-w-0 flex-wrap items-baseline gap-x-1.5";
  const targetClass = compact ? cn(JOURNAL_TARGET_CLASS, "truncate") : JOURNAL_TARGET_CLASS;
  const fileTarget = compact
    ? <span className={targetClass}>{entry.file || entry.path}</span>
    : <FileTarget path={entry.path} file={entry.file} onOpenFilePreview={onOpenFilePreview} />;

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
        {fileTarget}
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
        {entry.query ? <span className={targetClass}>{entry.query}</span> : null}
      </span>
    );
  }

  if (entry.kind === "shell" || entry.kind === "cli") {
    const verb = entry.status === "error"
      ? t("message.activityJournalVerbFailed", { defaultValue: "Failed" })
      : live
        ? t("message.activityJournalVerbRunning", { defaultValue: "Running" })
        : t("message.activityJournalVerbRan", { defaultValue: "Ran" });
    const target = entry.kind === "cli"
      ? `@${entry.name}${entry.detail ? ` ${entry.detail}` : ""}`
      : entry.command ?? "";
    const fullCommand = entry.kind === "shell" ? entry.shellRun?.command ?? entry.command ?? "" : "";
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
        <span className={cn(targetClass, "font-mono text-[12px]")}>{target}</span>
        {!compact && fullCommand ? <CopyCommandButton command={fullCommand} /> : null}
        {!compact ? <CountBadge count={entry.count} /> : null}
        {!compact && entry.attempts && entry.attempts > 1 ? (
          <span className={JOURNAL_META_CLASS}>
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
        {entry.tool ? <span className={cn(JOURNAL_META_CLASS, "font-mono text-[12px]")}>{entry.tool}</span> : null}
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
        {fileTarget}
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
  return (
    <span className={wrap}>
      <span className={verbClass}>{verb}</span>
      {taskLink ? (
        <TaskTarget
          link={taskLink}
          label={entry.target || `#${(entry.taskId ?? "").slice(0, 8)}`}
          className={targetClass}
        />
      ) : entry.target ? (
        <span className={targetClass}>{entry.target}</span>
      ) : null}
      {!compact ? <CountBadge count={entry.count} /> : null}
    </span>
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

  if (entry.kind === "shell") {
    const run: ShellRunSummary = entry.shellRun ?? {
      key: entry.id,
      command: entry.command ?? "",
      status: entry.status,
      output: entry.output,
      error: entry.error,
    };
    return (
      <div className="py-1">
        <ShellRunCard run={run} active={streaming} />
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
    <div className="flex flex-col gap-2 py-1 text-[12px] leading-5">
      {entry.path ? (
        <DetailBlock label={t("message.activityJournalPath", { defaultValue: "Path" })}>
          <PathLine path={entry.path} onOpenFilePreview={onOpenFilePreview} />
        </DetailBlock>
      ) : null}
      {entry.kind === "search" && entry.query ? (
        <DetailBlock label={t("message.activityJournalSearchesHeading", { defaultValue: "Searches" })}>
          <span className="text-foreground/88 [text-wrap:pretty]">{entry.query}</span>
          {entry.tool ? <span className="ml-1.5 font-mono text-[11px] text-foreground/45">{entry.tool}</span> : null}
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
          <pre className={cn(OUTPUT_PRE_CLASS, "text-[hsl(var(--tone-error))]/90")}>
            {entry.error.slice(0, 2000)}
          </pre>
        </DetailBlock>
      ) : null}
      {entry.output && entry.kind !== "cli" ? (
        <DetailBlock label={t("message.activityJournalOutput", { defaultValue: "Output" })}>
          <pre className={OUTPUT_PRE_CLASS}>{tailText(entry.tool === "computer" ? entry.output.replace(/screenshot/gi, "Screen") : entry.output, 4000)}</pre>
        </DetailBlock>
      ) : null}
      {entry.evidence?.length ? <ActivityEvidencePreview evidence={entry.evidence} /> : null}
    </div>
  );
}

const OUTPUT_PRE_CLASS = cn(
  "max-h-48 overflow-auto rounded-md px-2 py-1.5 font-mono text-[11px] leading-4",
  "whitespace-pre-wrap break-words text-foreground/80",
  "shadow-[0_0_0_1px_rgba(0,0,0,0.06)] dark:shadow-[0_0_0_1px_rgba(255,255,255,0.08)]",
  "bg-black/[0.03] dark:bg-white/[0.04]",
);

function tailText(text: string, maxChars: number): string {
  const normalized = text.replace(/\r\n/g, "\n").trimEnd();
  if (normalized.length <= maxChars) return normalized;
  const tail = normalized.slice(-maxChars);
  const firstBreak = tail.indexOf("\n");
  return `…${firstBreak >= 0 ? tail.slice(firstBreak + 1) : tail}`;
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
      <div className="text-[11px] font-medium text-foreground/48">{label}</div>
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
      <div className="text-[11px] font-medium text-foreground/48">
        {label}
        <span className="ml-1 tabular-nums text-foreground/35">{items.length}</span>
      </div>
      <ul className="max-h-52 overflow-y-auto pr-1">
        {visible.map((item) => (
          <li key={item} className="py-px">
            {renderItem(item)}
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <p className="text-[11px] text-foreground/45">
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
        textClassName="font-mono text-[12px]"
        testId="activity-journal-file"
      />
    );
  }
  const base = path.split(/[\\/]/).pop() || path;
  const dir = path.length > base.length ? path.slice(0, path.length - base.length) : "";
  return (
    <span className="font-mono text-[12px] [text-wrap:pretty]" title={path}>
      {dir ? <span className="text-foreground/42">{dir}</span> : null}
      <span className="text-foreground/90">{base}</span>
    </span>
  );
}
