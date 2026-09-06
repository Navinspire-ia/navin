import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FileReferenceChip } from "@/components/FileReferenceChip";
import { StreamingLabelSheen } from "@/components/MessageBubble";
import type { ShellRunSummary, ShellRunStatus } from "@/components/thread/activity/ShellRunCard";
import { DiffPair } from "@/components/thread/activity/DiffPair";
import { FileEditGroup, hasVisibleDiffStats, type FileEditSummary } from "@/components/thread/activity/FileEditRow";
import { ReasoningCard } from "@/components/thread/activity/ReasoningRow";
import {
  ActivityDigest,
  ActivityJournal,
  ActivityPhaseBar,
  JournalNowLine,
} from "@/components/thread/activity/ActivityJournal";
import {
  ACTIVITY_PHASE_ORDER,
  JOURNAL_PREVIEW_MAX,
  READ_TOOL_NAMES,
  SEARCH_TOOL_RE,
  buildJournalTimeline,
  currentJournalEntry,
  formatActivityDuration,
  journalQueryFromArgs,
  pathFromToolArgs,
  summarizeJournal,
  toolLeaf,
  type ActivityJournalStatus,
  type ActivityPhaseKey,
  type ActivityPhaseShare,
  type JournalStep,
} from "@/components/thread/activity/activityJournalModel";
import type {
  CliRunStatus,
  CliRunSummary,
  McpRunStatus,
  McpRunSummary,
} from "@/components/thread/activity/runSummaries";
import {
  activityEvidenceFromMessageMedia,
  activityEvidenceFromToolEvent,
  isAgentActivityMember,
  isReasoningOnlyAssistant,
  type ActivityEvidence,
} from "@/lib/activity-timeline";
import { useActivityMode } from "@/hooks/useActivityMode";
import { useFileEditDisplayMode } from "@/hooks/useFileEditDisplayMode";
import { hasRenderableFileDiff } from "@/lib/file-diff";
import type { FileEditDisplayMode } from "@/lib/local-preferences";
import { shortenArgValue } from "@/lib/short-path";
import { toolEventOutputText } from "@/lib/activity-preview";
import { formatToolCallTrace } from "@/lib/tool-traces";
import { cn } from "@/lib/utils";
import { taskHintFromMessages, taskHintTitle } from "@/lib/task-route-label";
import type { CliAppInfo, McpPresetInfo, ToolProgressEvent, UIFileEdit, UIMessage } from "@/lib/types";

export { isAgentActivityMember, isReasoningOnlyAssistant };

interface ActivityCounts {
  reasoningSteps: number;
  toolCalls: number;
  cliCount: number;
  mcpCount: number;
  fileCount: number;
  added: number;
  deleted: number;
  hasDiffStats: boolean;
  hasEditingFiles: boolean;
  hasFailedFiles: boolean;
  hasDeletedFiles: boolean;
  primaryFilePath?: string;
  primaryFileTooltipPath?: string;
  primaryCliName?: string;
  primaryCliStatus?: CliRunStatus;
  primaryMcpName?: string;
  primaryMcpDisplayName?: string;
  primaryMcpStatus?: McpRunStatus;
}

function countActivity(
  messages: UIMessage[],
  fileEdits: FileEditSummary[],
  cliRuns: CliRunSummary[],
  mcpRuns: McpRunSummary[],
): ActivityCounts {
  let reasoningSteps = 0;
  let toolCalls = 0;
  const cliCount = cliRuns.length;
  const mcpCount = mcpRuns.length;
  const primaryCli = cliRuns[cliRuns.length - 1];
  const primaryCliName = primaryCli?.name;
  const primaryCliStatus = primaryCli?.status;
  const primaryMcp = mcpRuns[mcpRuns.length - 1];
  for (const m of messages) {
    if (isReasoningOnlyAssistant(m)) {
      reasoningSteps += 1;
      continue;
    }
    if (m.kind === "trace") {
      const lines = traceLines(m);
      for (const line of lines) {
        if (!isCliRunTraceLine(line) && !isMcpRunTraceLine(line)) {
          toolCalls += 1;
        }
      }
    }
  }
  let added = 0;
  let deleted = 0;
  let hasDiffStats = false;
  let hasEditingFiles = false;
  let failedFileCount = 0;
  let deletedFileCount = 0;
  let primaryFilePath: string | undefined;
  let primaryFileTooltipPath: string | undefined;
  for (const edit of fileEdits) {
    primaryFilePath = edit.path;
    primaryFileTooltipPath = edit.absolute_path || edit.path;
    if (edit.status === "editing") {
      hasEditingFiles = true;
    }
    if (edit.status === "error") {
      failedFileCount += 1;
    }
    if (edit.operation === "delete") {
      deletedFileCount += 1;
    }
    if (edit.status === "error" || edit.binary) {
      continue;
    }
    if (!hasVisibleDiffStats(edit)) {
      continue;
    }
    hasDiffStats = true;
    added += edit.added;
    deleted += edit.deleted;
  }
  return {
    reasoningSteps,
    toolCalls,
    cliCount,
    mcpCount,
    fileCount: fileEdits.length,
    added,
    deleted,
    hasDiffStats,
    hasEditingFiles,
    hasFailedFiles: fileEdits.length > 0 && failedFileCount === fileEdits.length,
    hasDeletedFiles: fileEdits.length > 0 && deletedFileCount === fileEdits.length,
    primaryFilePath,
    primaryFileTooltipPath,
    primaryCliName,
    primaryCliStatus,
    primaryMcpName: primaryMcp?.presetName,
    primaryMcpDisplayName: primaryMcp?.displayName,
    primaryMcpStatus: primaryMcp?.status,
  };
}

interface AgentActivityClusterProps {
  messages: UIMessage[];
  /** True while the session turn is still running (drives “Working…” copy + header sheen). */
  isTurnStreaming: boolean;
  hasBodyBelow: boolean;
  /** Persisted end-to-end turn latency from the assistant answer, used for history replay. */
  turnLatencyMs?: number;
  /** When this block began: the user's message for the first block of a
   *  turn, otherwise its first step (see TurnUnit). */
  startedAtMs?: number;
  /** When this block ended (the next explanation / answer, or the turn end).
   *  Lets a turn with several blocks show each one's own duration. */
  endedAtMs?: number;
  cliApps?: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  onOpenFilePreview?: (path: string) => void;
  liveTaskHint?: { modelLabel?: string; taskRole?: string; routed?: boolean };
  /**
   * Whether this is the most recent turn. Every finished turn folds to a
   * one-line colored digest so a long thread stays readable; the latest one
   * only stays open when the turn ended without an answer below it.
   */
  isLatestTurn?: boolean;
}

/**
 * Activity for one agent turn: a header with duration and what is happening
 * right now, one compact Thinking card, then the journal - a chronological,
 * color-coded list of everything the agent did, each row opening on its own
 * full card. Live, the block is open so each step can be followed; once the
 * turn is done it slides shut to its header line and a click reopens it.
 */
export const AgentActivityCluster = memo(function AgentActivityCluster({
  messages,
  isTurnStreaming,
  hasBodyBelow,
  turnLatencyMs,
  startedAtMs,
  endedAtMs,
  cliApps = [],
  mcpPresets = [],
  onOpenFilePreview,
  liveTaskHint,
  isLatestTurn = true,
}: AgentActivityClusterProps) {
  const { t } = useTranslation();
  const fileEditDisplayMode = useFileEditDisplayMode();
  const activityMode = useActivityMode();
  const fileEdits = useMemo(
    () => summarizeFileEdits(collectFileEdits(messages), isTurnStreaming),
    [messages, isTurnStreaming],
  );
  const cliRuns = useMemo(() => collectCliRuns(messages), [messages]);
  const mcpRuns = useMemo(() => collectMcpRuns(messages), [messages]);
  const cliAppsByName = useMemo(
    () => new Map(cliApps.map((app) => [app.name.toLowerCase(), app])),
    [cliApps],
  );
  const mcpPresetsByName = useMemo(
    () => new Map(mcpPresets.map((preset) => [preset.name.toLowerCase(), preset])),
    [mcpPresets],
  );
  const {
    reasoningSteps,
    toolCalls,
    cliCount,
    mcpCount,
    fileCount,
    added,
    deleted,
    hasDiffStats,
    hasEditingFiles,
    hasFailedFiles,
    hasDeletedFiles,
    primaryFilePath,
    primaryFileTooltipPath,
    primaryCliName,
    primaryCliStatus,
    primaryMcpDisplayName,
    primaryMcpStatus,
  } = countActivity(messages, fileEdits, cliRuns, mcpRuns);
  const hasPendingFileEdit = fileEdits.some((edit) => edit.pending);
  const hasNonReasoningActivity = toolCalls > 0 || cliCount > 0 || mcpCount > 0 || fileCount > 0;
  const reasoningMessages = useMemo(
    () => messages.filter(isReasoningOnlyAssistant),
    [messages],
  );

  const journal = useMemo(
    () => buildJournalTimeline(
      collectJournalSteps(messages, isTurnStreaming),
      { streaming: isTurnStreaming },
    ),
    [messages, isTurnStreaming],
  );
  const digest = useMemo(() => summarizeJournal(journal), [journal]);
  const nowEntry = isTurnStreaming ? currentJournalEntry(journal) : undefined;

  // Live: open, so every step can be followed as it happens. Done: folded to
  // one line ("Worked for 54s · digest"), the details one click away. A turn
  // that ends without an answer keeps its latest block open - that block is
  // then all the user got. The "Activity detail" preference moves the
  // default (expanded: always open, digest: always folded). A manual toggle
  // wins until unmount.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const defaultOpen = activityMode === "expanded"
    ? true
    : activityMode === "digest"
      ? false
      : isTurnStreaming || (isLatestTurn && !hasBodyBelow);
  const bodyOpen = userOpen ?? defaultOpen;
  const toggleBody = () => setUserOpen(!bodyOpen);
  const compactActivity = activityMode === "compact";
  const [now, setNow] = useState(() => Date.now());
  // The fold-on-completion is the one moment the header swaps its live line
  // for the digest; only that swap animates, history mounts still.
  const settled = useJustSettled(isTurnStreaming);
  const reduceMotion = useReducedMotion();

  const hasLiveEditingFiles = isTurnStreaming && hasEditingFiles;
  const singleFilePath = fileCount === 1 ? primaryFilePath : undefined;
  const singleFileTooltipPath = fileCount === 1 ? primaryFileTooltipPath : undefined;
  const hasVisibleActivity = reasoningSteps > 0 || toolCalls > 0 || cliCount > 0 || mcpCount > 0 || fileCount > 0;
  const hasOnlyFileActivity = fileCount > 0 && messages.every(messageHasOnlyFileActivity);
  const durationMs = activityDurationMs(
    messages,
    isTurnStreaming,
    now,
    turnLatencyMs,
    startedAtMs,
    endedAtMs,
  );
  const activityDuration = formatActivityDuration(durationMs);
  // Where the time went, once the turn is over. Live values would jitter.
  const phaseSplit = useMemo(
    () => (isTurnStreaming ? [] : activityPhaseSplit(messages, durationMs, startedAtMs)),
    [messages, isTurnStreaming, durationMs, startedAtMs],
  );
  const showPhaseSplit = !isTurnStreaming && durationMs >= 5_000 && phaseSplit.length >= 2;
  const thoughtLabel = hasNonReasoningActivity
    ? isTurnStreaming
      ? t("message.activityWorkingFor", {
          duration: activityDuration,
          defaultValue: "Working for {{duration}}",
        })
      : durationMs <= 0
        ? t("message.activityWorked", { defaultValue: "Worked" })
      : t("message.activityWorkedFor", {
          duration: activityDuration,
          defaultValue: "Worked for {{duration}}",
        })
    : isTurnStreaming
      ? t("message.activityThinkingFor", {
          duration: activityDuration,
          defaultValue: "Thinking for {{duration}}",
        })
      : durationMs <= 0
        ? t("message.activityThought", { defaultValue: "Thought" })
      : t("message.activityThoughtFor", {
          duration: activityDuration,
          defaultValue: "Thought for {{duration}}",
        });
  const taskHint = taskHintFromMessages(messages, t, liveTaskHint);

  const fileActivitySummary = fileCount > 0
    ? hasPendingFileEdit && !singleFilePath
      ? t("message.fileActivityPreparing", { defaultValue: "Preparing edit…" })
      : singleFilePath
      ? t(fileActivitySummaryKey(hasLiveEditingFiles, hasFailedFiles, hasDeletedFiles), {
          file: shortFileName(singleFilePath),
          defaultValue: `${fileActivityVerb(hasLiveEditingFiles, hasFailedFiles, hasDeletedFiles)} {{file}}`,
        })
      : t(fileActivityManySummaryKey(hasLiveEditingFiles, hasFailedFiles, hasDeletedFiles), {
          count: fileCount,
          defaultValue: `${fileActivityVerb(hasLiveEditingFiles, hasFailedFiles, hasDeletedFiles)} {{count}} changes`,
        })
    : "";

  const cliActivitySummary = cliCount > 0
    ? cliCount === 1 && primaryCliName
      ? t(cliActivitySummaryKey(primaryCliStatus, isTurnStreaming), {
          name: primaryCliName,
          defaultValue: cliActivitySummaryDefault(primaryCliStatus, isTurnStreaming),
        })
      : t(cliActivityManySummaryKey(cliRuns, isTurnStreaming), {
          count: cliCount,
          defaultValue: cliActivityManySummaryDefault(cliRuns, isTurnStreaming),
        })
    : "";

  const mcpActivitySummary = mcpCount > 0
    ? mcpCount === 1 && primaryMcpDisplayName
      ? t(mcpActivitySummaryKey(primaryMcpStatus, isTurnStreaming), {
          name: primaryMcpDisplayName,
          defaultValue: mcpActivitySummaryDefault(primaryMcpStatus, isTurnStreaming),
        })
      : t(mcpActivityManySummaryKey(mcpRuns, isTurnStreaming), {
          count: mcpCount,
          defaultValue: mcpActivityManySummaryDefault(mcpRuns, isTurnStreaming),
        })
    : "";

  // Prefer turn-wide step/tool counts in the header. File edits stay listed in
  // the body - putting "Edited N files" in the header hid the real stats and
  // encouraged one header per file when the timeline was split.
  const summary = isTurnStreaming
    ? reasoningSteps > 0 || toolCalls > 0
      ? t("message.agentActivityLiveSummary", {
          reasoning: reasoningSteps,
          tools: toolCalls,
          defaultValue: "Working… · {{reasoning}} steps · {{tools}} tool calls",
        })
      : cliCount > 0
        ? cliActivitySummary
      : mcpCount > 0
        ? mcpActivitySummary
      : fileCount > 0
        ? fileActivitySummary
      : t("message.agentActivityLiveToolsOnly", {
          tools: toolCalls,
          defaultValue: "Working… · {{tools}} tool calls",
        })
    : reasoningSteps > 0 || toolCalls > 0
      ? t("message.agentActivitySummary", {
          reasoning: reasoningSteps,
          tools: toolCalls,
          defaultValue: "{{reasoning}} steps · {{tools}} tool calls",
        })
      : cliCount > 0
        ? cliActivitySummary
      : mcpCount > 0
        ? mcpActivitySummary
      : fileCount > 0
        ? fileActivitySummary
      : t("message.agentActivityToolsOnly", {
          tools: toolCalls,
          defaultValue: "{{tools}} tool calls",
        });

  useEffect(() => {
    if (!isTurnStreaming) return undefined;
    const interval = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(interval);
  }, [isTurnStreaming]);

  if (!hasVisibleActivity) return null;

  if (hasOnlyFileActivity) {
    return (
      <FileEditFlatActivity
        edits={fileEdits}
        active={isTurnStreaming}
        open={bodyOpen}
        onToggle={toggleBody}
        hasBodyBelow={hasBodyBelow}
        summary={fileActivitySummary || summary}
        singleFilePath={singleFilePath}
        singleFileTooltipPath={singleFileTooltipPath}
        hasLiveEditingFiles={hasLiveEditingFiles}
        hasFailedFiles={hasFailedFiles}
        hasDeletedFiles={hasDeletedFiles}
        added={added}
        deleted={deleted}
        hasDiffStats={hasDiffStats}
        fileEditDisplayMode={fileEditDisplayMode}
        onOpenFilePreview={onOpenFilePreview}
        taskHint={taskHint}
      />
    );
  }

  const showDigest = !bodyOpen && hasNonReasoningActivity;

  return (
    <div className={cn("w-full", hasBodyBelow && "mb-2")} data-testid="agent-activity-cluster" data-open={bodyOpen}>
      {/* Chip stays a sibling of the toggle (never nested): its preview /
          download controls are real <button>s and React forbids button-in-button. */}
      <div
        className={cn(
          "group flex max-w-full items-center gap-1.5 rounded-md px-1 py-1",
          "text-[12px] text-muted-foreground/72",
        )}
      >
        <button
          type="button"
          onClick={toggleBody}
          className={cn(
            "inline-flex min-w-0 items-center gap-1.5 rounded-md text-left",
            "transition-colors hover:text-muted-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
          aria-expanded={bodyOpen}
          aria-label={summary || thoughtLabel}
          data-testid="activity-header-toggle"
        >
          <StreamingLabelSheen
            active={isTurnStreaming}
            className="min-w-0 shrink-0"
          >
            {singleFilePath ? fileActivityVerb(hasLiveEditingFiles, hasFailedFiles, hasDeletedFiles) : thoughtLabel}
          </StreamingLabelSheen>
          {taskHint ? (
            <span
              className="min-w-0 truncate text-[11px] font-normal text-muted-foreground/55"
              data-testid="activity-task-model"
              title={taskHintTitle(t)}
            >
              {taskHint}
            </span>
          ) : null}
          {/* Live: the current action, colored, so the user follows without
              scrolling. Folded: a colored digest of the whole turn. Open and
              done: the concrete step/tool counts. */}
          {isTurnStreaming && nowEntry ? (
            <>
              <span className="shrink-0 text-muted-foreground/40">·</span>
              <JournalNowLine entry={nowEntry} />
            </>
          ) : showDigest ? (
            <motion.span
              key="digest"
              className="inline-flex min-w-0 items-center gap-1.5"
              initial={settled && !reduceMotion ? { opacity: 0, x: -6 } : false}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.28, ease: "easeOut" }}
            >
              <span className="shrink-0 text-muted-foreground/40">·</span>
              <ActivityDigest digest={digest} />
            </motion.span>
          ) : summary && !singleFilePath ? (
            <span className="min-w-0 truncate text-left text-muted-foreground/85">
              · {summary}
            </span>
          ) : null}
          {fileCount > 0 && hasDiffStats ? (
            <span className="inline-flex min-w-0 shrink-0 items-center gap-1 text-muted-foreground/85">
              <DiffPair added={added} deleted={deleted} />
            </span>
          ) : null}
          <ChevronRight
            aria-hidden
            className={cn(
              "h-3.5 w-3.5 shrink-0 transition-transform duration-200",
              bodyOpen && "rotate-90",
            )}
          />
        </button>
        {singleFilePath ? (
          <FileReferenceChip
            path={singleFilePath}
            tooltipPath={singleFileTooltipPath}
            previewPath={singleFileTooltipPath || singleFilePath}
            onOpen={onOpenFilePreview}
            active={hasLiveEditingFiles}
            className="-my-0.5 min-w-0"
            textClassName="text-[12px]"
            testId="activity-header-file-reference"
          />
        ) : null}
      </div>

      {/* Everything below the header slides shut once the turn is over; the
          header keeps the digest and the +/- totals, so what happened stays
          readable at a glance and the full journal is one click away. */}
      <ActivityFold open={bodyOpen} testId="activity-cluster-body">
        {showPhaseSplit ? (
          <ActivityPhaseBar phases={phaseSplit} className="ml-1 mt-1 pl-1" />
        ) : null}

        {reasoningMessages.length > 0 ? (
          <div className="ml-1 mt-1 pl-1" data-testid="activity-reasoning-visible">
            <ReasoningCard
              parts={reasoningMessages.map((message) => message.reasoning ?? "")}
              streaming={
                isTurnStreaming
                && reasoningMessages.some((message) => !!message.reasoningStreaming)
              }
              duration={activityDuration}
              compact={compactActivity}
              onOpenFilePreview={onOpenFilePreview}
            />
          </div>
        ) : null}

        <ActivityJournal
          entries={journal}
          streaming={isTurnStreaming}
          onOpenFilePreview={onOpenFilePreview}
          cliAppsByName={cliAppsByName}
          mcpPresetsByName={mcpPresetsByName}
          previewMax={compactActivity ? JOURNAL_PREVIEW_MAX_COMPACT : JOURNAL_PREVIEW_MAX}
        />

        {fileEdits.length ? (
          <div className="ml-1 mt-1 pl-1">
            <FileEditGroup
              edits={fileEdits}
              displayMode={fileEditDisplayMode}
              onOpenFilePreview={onOpenFilePreview}
            />
          </div>
        ) : null}
      </ActivityFold>
    </div>
  );
});

const FOLD_MOTION = { type: "spring", duration: 0.42, bounce: 0 } as const;

/**
 * The body of an activity block. Slides shut (height + fade) when the turn
 * settles and slides open on demand; history mounts in its final state.
 */
export function ActivityFold({
  open,
  children,
  testId,
}: {
  open: boolean;
  children: ReactNode;
  testId?: string;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          key="activity-fold"
          initial={reduceMotion ? false : { height: 0, opacity: 0, overflow: "hidden" }}
          animate={{ height: "auto", opacity: 1, transitionEnd: { overflow: "visible" } }}
          exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0, overflow: "hidden" }}
          transition={reduceMotion ? { duration: 0.1 } : FOLD_MOTION}
          data-testid={testId}
        >
          {children}
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

/** True during the render where a live block finishes (streaming true -> false),
 *  so the element mounted by that render can animate in. */
function useJustSettled(streaming: boolean): boolean {
  const previous = useRef(streaming);
  const justSettled = previous.current && !streaming;
  useEffect(() => {
    previous.current = streaming;
  }, [streaming]);
  return justSettled;
}

function messageHasOnlyFileActivity(message: UIMessage): boolean {
  if (message.kind !== "trace" || !message.fileEdits?.length) return false;
  return traceLines(message).every((line) => !line.trim() || isFileEditTraceLine(line));
}

function FileEditFlatActivity({
  edits,
  active,
  open,
  onToggle,
  hasBodyBelow,
  summary,
  singleFilePath,
  singleFileTooltipPath,
  hasLiveEditingFiles,
  hasFailedFiles,
  hasDeletedFiles,
  added,
  deleted,
  hasDiffStats,
  fileEditDisplayMode,
  onOpenFilePreview,
  taskHint,
}: {
  edits: FileEditSummary[];
  active: boolean;
  /** Rows shown; folded to the header line once the turn is over. */
  open: boolean;
  onToggle: () => void;
  hasBodyBelow: boolean;
  summary: string;
  singleFilePath?: string;
  singleFileTooltipPath?: string;
  hasLiveEditingFiles: boolean;
  hasFailedFiles: boolean;
  hasDeletedFiles: boolean;
  added: number;
  deleted: number;
  hasDiffStats: boolean;
  fileEditDisplayMode: FileEditDisplayMode;
  onOpenFilePreview?: (path: string) => void;
  taskHint?: string;
}) {
  const { t } = useTranslation();
  const diffOnlyRows = edits.length === 1
    && !!singleFilePath
    && fileEditDisplayMode !== "summary"
    && edits.some((edit) => (
      edit.status !== "editing"
      && edit.status !== "error"
      && hasRenderableFileDiff(edit.diff)
    ));
  const showRows = edits.length > 1
    || edits.some((edit) => edit.status === "error" || edit.pending)
    || (
      fileEditDisplayMode !== "summary"
      && edits.some((edit) => hasRenderableFileDiff(edit.diff))
    );
  const rowsOpen = showRows && open;
  const headerLabel = (
    <>
      <StreamingLabelSheen active={active} className="min-w-0 shrink-0">
        {singleFilePath
          ? fileActivityVerb(hasLiveEditingFiles, hasFailedFiles, hasDeletedFiles)
          : summary}
      </StreamingLabelSheen>
      {taskHint ? (
        <span
          className="min-w-0 truncate text-[11px] font-normal text-muted-foreground/55"
          data-testid="activity-task-model"
          title={taskHintTitle(t)}
        >
          {taskHint}
        </span>
      ) : null}
      {hasDiffStats ? (
        <span className="inline-flex min-w-0 shrink-0 items-center gap-1 text-muted-foreground/85">
          <DiffPair added={added} deleted={deleted} />
        </span>
      ) : null}
    </>
  );
  return (
    <div className={cn("w-full", hasBodyBelow && "mb-2")} aria-label={summary} data-testid="file-edit-activity">
      {/* Chip stays a sibling of the toggle: its controls are real buttons. */}
      <div
        className={cn(
          "group flex max-w-full items-center gap-1.5 rounded-md px-1 py-1",
          "text-[12px] text-muted-foreground/72",
        )}
      >
        {showRows ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={rowsOpen}
            aria-label={summary}
            data-testid="file-edit-activity-toggle"
            className={cn(
              "inline-flex min-w-0 items-center gap-1.5 rounded-md text-left",
              "transition-colors hover:text-muted-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            {headerLabel}
            <ChevronRight
              aria-hidden
              className={cn(
                "h-3.5 w-3.5 shrink-0 transition-transform duration-200",
                rowsOpen && "rotate-90",
              )}
            />
          </button>
        ) : (
          headerLabel
        )}
        {singleFilePath ? (
          <FileReferenceChip
            path={singleFilePath}
            tooltipPath={singleFileTooltipPath}
            previewPath={singleFileTooltipPath || singleFilePath}
            onOpen={onOpenFilePreview}
            active={hasLiveEditingFiles}
            className="-my-0.5 min-w-0"
            textClassName="text-[12px]"
            testId="activity-header-file-reference"
          />
        ) : null}
      </div>
      {showRows ? (
        <ActivityFold open={rowsOpen} testId="file-edit-activity-body">
          <div className="mt-0.5 pl-4">
            <FileEditGroup
              edits={edits}
              displayMode={fileEditDisplayMode}
              density={diffOnlyRows ? "diff-only" : "default"}
              onOpenFilePreview={onOpenFilePreview}
            />
          </div>
        </ActivityFold>
      ) : null}
    </div>
  );
}

function shortFileName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

export function activityDurationMs(
  messages: UIMessage[],
  active: boolean,
  now: number,
  completedLatencyMs?: number,
  activeStartedAtMs?: number,
  endedAtMs?: number,
): number {
  if (!active) {
    // A block's own span wins over the turn-wide latency: a turn that
    // alternates work and explanations has several blocks, and each header
    // should read its own time, not "Worked for 48m" on all of them.
    if (
      isTimestamp(activeStartedAtMs)
      && isTimestamp(endedAtMs)
      && endedAtMs >= activeStartedAtMs
    ) {
      return Math.round(endedAtMs - activeStartedAtMs);
    }
    if (Number.isFinite(completedLatencyMs) && completedLatencyMs! >= 0) {
      return Math.round(completedLatencyMs!);
    }
  }
  const timestamps = messages
    .map((message) => message.createdAt)
    .filter((value) => Number.isFinite(value));
  if (!timestamps.length) return 0;
  const first = active && Number.isFinite(activeStartedAtMs)
    ? activeStartedAtMs!
    : Math.min(...timestamps);
  const last = active && first > 1_000_000_000_000
    ? now
    : Math.max(...timestamps);
  return Math.max(0, last - first);
}

function isTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function traceLines(message: UIMessage): string[] {
  if (message.traces?.length) return message.traces;
  return message.content.trim() ? [message.content] : [];
}

const JOURNAL_PREVIEW_MAX_COMPACT = 4;

// ---------------------------------------------------------------------------
// Time by phase: message timestamps split across what each message did.
// ---------------------------------------------------------------------------

function phaseForTraceLine(line: string): ActivityPhaseKey | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  const match = /^([a-zA-Z0-9_.-]+)\(/.exec(trimmed);
  if (!match) return "tool";
  const name = match[1];
  const leaf = toolLeaf(name);
  if (READ_TOOL_NAMES.has(leaf) || SEARCH_TOOL_RE.test(leaf) || leaf === "list_dir") return "explore";
  if (isFileEditTraceLine(trimmed)) return "edit";
  if (isShellTraceName(name) || isCliRunTraceLine(trimmed)) return "command";
  return "tool";
}

/**
 * Split the turn's wall time by activity. Each activity message owns the
 * time until the next one starts (the last one runs to the turn end); a
 * reasoning message is thinking, a trace message shares its span across
 * its tool calls. Approximate by design, but consistent live and in history.
 */
export function activityPhaseSplit(
  messages: UIMessage[],
  totalMs: number,
  startedAtMs?: number,
): ActivityPhaseShare[] {
  if (!(totalMs > 0)) return [];
  const activity = messages.filter(
    (message) =>
      (isReasoningOnlyAssistant(message) || message.kind === "trace")
      && Number.isFinite(message.createdAt)
      && message.createdAt > 0,
  );
  if (!activity.length) return [];
  const firstAt = activity[0].createdAt;
  const start = typeof startedAtMs === "number" && startedAtMs > 1_000_000_000_000
    ? Math.min(startedAtMs, firstAt)
    : firstAt;
  const end = start + totalMs;
  const totals: Record<ActivityPhaseKey, number> = { thinking: 0, explore: 0, edit: 0, command: 0, tool: 0 };
  // Time before the first activity message is model thinking that produced it.
  totals.thinking += Math.max(0, firstAt - start);
  activity.forEach((message, index) => {
    const next = activity[index + 1]?.createdAt ?? end;
    const span = Math.max(0, Math.min(next, end) - message.createdAt);
    if (span <= 0) return;
    if (isReasoningOnlyAssistant(message)) {
      totals.thinking += span;
      return;
    }
    const phases = traceLines(message)
      .map(phaseForTraceLine)
      .filter((phase): phase is ActivityPhaseKey => phase !== null);
    if (!phases.length) {
      totals.tool += span;
      return;
    }
    const share = span / phases.length;
    for (const phase of phases) totals[phase] += share;
  });
  return ACTIVITY_PHASE_ORDER
    .map((key) => ({ key, ms: Math.round(totals[key]) }))
    .filter((phase) => phase.ms >= 500);
}

// ---------------------------------------------------------------------------
// Journal steps: one ordered pass over trace lines + structured events.
// ---------------------------------------------------------------------------

function journalStatusFromEvent(
  event: ToolProgressEvent | undefined,
  streaming: boolean,
  isTail: boolean,
): ActivityJournalStatus {
  if (event) {
    if (event.phase === "error") return "error";
    if (event.phase === "end") return "done";
    return streaming ? "running" : "done";
  }
  return streaming && isTail ? "running" : "done";
}

function eventDurationMs(event: ToolProgressEvent | undefined): number | undefined {
  if (!event) return undefined;
  if (typeof event.client_started_at !== "number" || typeof event.client_ended_at !== "number") {
    return undefined;
  }
  return Math.max(0, event.client_ended_at - event.client_started_at);
}

function shellRunDurationMs(run: ShellRunSummary): number | undefined {
  if (typeof run.startedAt !== "number" || typeof run.endedAt !== "number") return undefined;
  return Math.max(0, run.endedAt - run.startedAt);
}

function shellStep(run: ShellRunSummary, fallbackDuration?: number): JournalStep {
  return {
    kind: "shell",
    key: run.key,
    command: run.command,
    status: run.status,
    output: run.output,
    error: run.error,
    durationMs: shellRunDurationMs(run) ?? fallbackDuration,
    run,
  };
}

function cliStep(run: CliRunSummary, durationMs?: number): JournalStep {
  return {
    kind: "cli",
    key: run.key,
    name: run.name,
    args: formatCliArgs(run),
    status: run.status,
    error: run.error,
    durationMs,
    run,
  };
}

function mcpStep(run: McpRunSummary, durationMs?: number): JournalStep {
  return {
    kind: "mcp",
    key: run.key,
    displayName: run.displayName,
    toolName: run.toolName,
    argsPreview: run.argsPreview || undefined,
    status: run.status,
    error: run.error,
    durationMs,
    run,
  };
}

function editStep(edit: FileEditSummary): JournalStep {
  return {
    kind: "edit",
    key: edit.key,
    path: edit.path,
    status: edit.status === "error" ? "error" : edit.status === "editing" ? "running" : "done",
    operation: edit.operation === "delete" ? "delete" : "edit",
    added: edit.added,
    deleted: edit.deleted,
    hasStats: !edit.binary && edit.status !== "error" && hasVisibleDiffStats(edit),
  };
}

function takeEdit(queue: Map<string, FileEditSummary[]>, path: string): FileEditSummary | undefined {
  const exact = path ? queue.get(path) : undefined;
  if (exact?.length) return exact.shift();
  for (const [key, edits] of queue) {
    if (!edits.length) continue;
    if (!path || key.endsWith(path) || path.endsWith(key)) return edits.shift();
  }
  return undefined;
}

export function collectJournalSteps(messages: UIMessage[], streaming: boolean): JournalStep[] {
  const steps: JournalStep[] = [];
  const traceMessages = messages.filter((message) => message.kind === "trace");
  traceMessages.forEach((message, messageIndex) => {
    const lastMessage = messageIndex === traceMessages.length - 1;
    const lines = traceLines(message);
    const eventByLine = toolEventByTraceLine(message);
    const shellByLine = shellRunMapByTraceLine(message);
    const cliByLine = cliRunMapByTraceLine(message);
    const mcpByLine = mcpRunMapByTraceLine(message);
    const evidenceByLine = toolEvidenceByTraceLine(message);
    const editQueue = new Map<string, FileEditSummary[]>();
    for (const edit of summarizeFileEdits(message.fileEdits ?? [], streaming)) {
      if (!edit.path) continue;
      const queue = editQueue.get(edit.path) ?? [];
      queue.push(edit);
      editQueue.set(edit.path, queue);
    }
    const usedRunKeys = new Set<string>();

    lines.forEach((line, index) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const isTail = lastMessage && index === lines.length - 1;
      const match = /^([a-zA-Z0-9_.-]+)\((.*)\)$/.exec(trimmed);
      if (!match) {
        steps.push({
          kind: "tool",
          name: "",
          args: "",
          text: trimmed,
          status: streaming && isTail ? "running" : "done",
        });
        return;
      }
      const name = match[1];
      const args = match[2] ?? "";
      const leaf = toolLeaf(name);
      const event = eventByLine.get(line);
      const status = journalStatusFromEvent(event, streaming, isTail);
      const durationMs = eventDurationMs(event);

      if (READ_TOOL_NAMES.has(leaf)) {
        steps.push({ kind: "read", path: pathFromToolArgs(args), status, durationMs });
        return;
      }
      if (isShellTraceName(name)) {
        const run = shellByLine.get(line)
          ?? parseShellRunTrace(line, streaming && isTail ? "running" : "done");
        if (run) {
          usedRunKeys.add(run.key);
          steps.push(shellStep(run, durationMs));
        }
        return;
      }
      if (isCliRunTraceLine(trimmed)) {
        const run = cliByLine.get(line)
          ?? parseCliRunTrace(line, streaming && isTail ? "running" : "done");
        if (run) {
          usedRunKeys.add(run.key);
          steps.push(cliStep(run, durationMs));
        }
        return;
      }
      if (isMcpRunTraceLine(trimmed)) {
        const run = mcpByLine.get(line)
          ?? parseMcpRunTrace(line, streaming && isTail ? "running" : "done");
        if (run) {
          usedRunKeys.add(run.key);
          steps.push(mcpStep(run, durationMs));
        }
        return;
      }
      if (isFileEditTraceLine(trimmed)) {
        const path = pathFromToolArgs(args);
        const edit = takeEdit(editQueue, path);
        if (edit) {
          steps.push(editStep(edit));
        } else if (path) {
          steps.push({
            kind: "edit",
            key: `${message.id}:${index}`,
            path,
            status,
            operation: /"operation"\s*:\s*"delete"/.test(args) ? "delete" : "edit",
            added: 0,
            deleted: 0,
            hasStats: false,
          });
        }
        return;
      }
      if (SEARCH_TOOL_RE.test(leaf)) {
        steps.push({
          kind: "search",
          query: journalQueryFromArgs(args),
          tool: leaf,
          status,
          durationMs,
        });
        return;
      }
      const outputText = event && event.phase !== "error" ? toolEventOutputText(event) : "";
      steps.push({
        kind: "tool",
        name,
        args,
        status,
        durationMs,
        output: outputText || undefined,
        error: event ? cliRunError(event) : undefined,
        evidence: evidenceByLine.get(line),
      });
    });

    // Structured runs that never produced a trace line still count.
    for (const run of shellByLine.values()) {
      if (!usedRunKeys.has(run.key)) steps.push(shellStep(run));
    }
    for (const run of cliByLine.values()) {
      if (!usedRunKeys.has(run.key)) steps.push(cliStep(run));
    }
    for (const run of mcpByLine.values()) {
      if (!usedRunKeys.has(run.key)) steps.push(mcpStep(run));
    }
    for (const queue of editQueue.values()) {
      for (const edit of queue) steps.push(editStep(edit));
    }
    const media = activityEvidenceFromMessageMedia(message);
    if (media.length) steps.push({ kind: "media", evidence: media });
  });
  return steps;
}

function toolEventByTraceLine(message: UIMessage): Map<string, ToolProgressEvent> {
  const map = new Map<string, ToolProgressEvent>();
  for (const event of message.toolEvents ?? []) {
    const line = formatToolCallTrace(event);
    if (!line) continue;
    const existing = map.get(line);
    if (!existing) {
      map.set(line, event);
      continue;
    }
    const incomingRank = CLI_RUN_STATUS_RANK[cliRunStatusFromPhase(event.phase)];
    const existingRank = CLI_RUN_STATUS_RANK[cliRunStatusFromPhase(existing.phase)];
    map.set(line, incomingRank >= existingRank ? { ...existing, ...event } : existing);
  }
  return map;
}

function toolEvidenceByTraceLine(message: UIMessage): Map<string, ActivityEvidence[]> {
  const map = new Map<string, ActivityEvidence[]>();
  for (const event of message.toolEvents ?? []) {
    const evidence = activityEvidenceFromToolEvent(event);
    if (!evidence.length) continue;
    const line = formatToolCallTrace(event);
    if (!line) continue;
    const existing = map.get(line) ?? [];
    map.set(line, [...existing, ...evidence]);
  }
  return map;
}

function isShellTraceName(name: string): boolean {
  const compact = name.toLowerCase().split(".").pop() || name.toLowerCase();
  return new Set([
    "exec",
    "exec_command",
    "execute_command",
    "run_command",
    "run_shell",
    "shell",
    "terminal",
    "bash",
    "sh",
  ]).has(compact);
}

function shellCommandFromArgs(args: string): string {
  const compactArgs = args.trim();
  if (!compactArgs) return "";
  try {
    const parsed = JSON.parse(compactArgs) as unknown;
    if (typeof parsed === "string") return parsed;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "";
    const record = parsed as Record<string, unknown>;
    for (const key of ["command", "cmd", "script", "input"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value;
    }
  } catch {
    return compactArgs.replace(/^["']|["']$/g, "");
  }
  return "";
}

function redactShellCommand(command: string): string {
  return command
    .replace(/\b((?:[A-Z0-9_]*)(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASS|AUTH)(?:[A-Z0-9_]*))=(?:"[^"]*"|'[^']*'|[^\s]+)/gi, "$1=••••")
    .replace(/\b(Bearer)\s+[A-Za-z0-9._~+/=-]+/gi, "$1 ••••")
    .replace(/(--(?:api-?key|token|secret|password)(?:=|\s+))(?:"[^"]*"|'[^']*'|[^\s]+)/gi, "$1••••")
    .replace(/([?&](?:api_?key|token|secret|password)=)[^&\s]+/gi, "$1••••");
}

const CLI_RUN_TOOL_NAMES = new Set(["run_cli_app", "cli_anything_run"]);
const CLI_RUN_STATUS_RANK: Record<CliRunStatus, number> = { running: 1, done: 2, error: 3 };
const MCP_RUN_STATUS_RANK: Record<McpRunStatus, number> = { running: 1, done: 2, error: 3 };
const MCP_TOOL_NAME_RE = /^mcp_([a-z0-9_-]+?)_(.+)$/i;

function isCliRunTraceLine(line: string): boolean {
  return /^(run_cli_app|cli_anything_run)\(/.test(line.trim());
}

function isMcpRunTraceLine(line: string): boolean {
  return MCP_TOOL_NAME_RE.test(line.trim().split("(", 1)[0] ?? "");
}

function isFileEditTraceLine(line: string): boolean {
  return /^(write_file|edit_file|apply_patch)\(/.test(line.trim());
}

function parseCliRunTrace(line: string, status: CliRunStatus = "running"): CliRunSummary | null {
  const match = /^(run_cli_app|cli_anything_run)\((.*)\)$/.exec(line.trim());
  if (!match) return null;
  const argsText = match[2].trim();
  let argsObject: unknown = {};
  if (argsText) {
    try {
      argsObject = JSON.parse(argsText);
    } catch {
      return {
        key: line,
        name: "cli",
        args: [argsText],
        json: false,
        status,
      };
    }
  }
  return cliRunFromArguments(argsObject, { key: line, status });
}

function parseToolEventArguments(event: ToolProgressEvent): unknown {
  const fnArgs = (event as { function?: { arguments?: unknown } }).function?.arguments;
  const raw = fnArgs ?? event.arguments;
  if (typeof raw !== "string") return raw ?? {};
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return { args: [raw] };
  }
}

function cliRunStatusFromPhase(phase: unknown): CliRunStatus {
  if (phase === "error") return "error";
  if (phase === "end") return "done";
  return "running";
}

function cliRunError(event: ToolProgressEvent): string | undefined {
  const error = event.error;
  if (typeof error === "string") return error;
  if (error && typeof error === "object") return JSON.stringify(error);
  return undefined;
}

function toolEventName(event: ToolProgressEvent): string {
  return typeof (event as { function?: { name?: unknown } }).function?.name === "string"
    ? String((event as { function?: { name?: unknown } }).function?.name)
    : typeof event.name === "string"
      ? event.name
      : "";
}

function cliRunFromArguments(
  argsObject: unknown,
  options: { key: string; status: CliRunStatus; error?: string },
): CliRunSummary {
  if (!argsObject || typeof argsObject !== "object" || Array.isArray(argsObject)) {
    return {
      key: options.key,
      name: "cli",
      args: [],
      json: false,
      status: options.status,
      error: options.error,
    };
  }
  const record = argsObject as Record<string, unknown>;
  const appName = typeof record.name === "string" && record.name.trim()
    ? record.name.trim()
    : "cli";
  const rawArgs = Array.isArray(record.args) ? record.args : [];
  const cliArgs = rawArgs.filter((item): item is string => typeof item === "string");
  return {
    key: options.key,
    name: appName,
    args: cliArgs,
    json: record.json === true || record.json === "true",
    workingDir: typeof record.working_dir === "string" ? record.working_dir : undefined,
    status: options.status,
    error: options.error,
  };
}

function cliRunFromEvent(event: ToolProgressEvent): CliRunSummary | null {
  const name = toolEventName(event);
  if (!CLI_RUN_TOOL_NAMES.has(name)) return null;
  const argsObject = parseToolEventArguments(event);
  const key = event.call_id ? `call:${event.call_id}` : `${name}:${JSON.stringify(argsObject)}`;
  return cliRunFromArguments(argsObject, {
    key,
    status: cliRunStatusFromPhase(event.phase),
    error: cliRunError(event),
  });
}

function cliRunMapByTraceLine(message: UIMessage): Map<string, CliRunSummary> {
  const runsByLine = new Map<string, CliRunSummary>();
  for (const event of message.toolEvents ?? []) {
    const run = cliRunFromEvent(event);
    if (!run) continue;
    const line = formatToolCallTrace(event);
    if (!line) continue;
    runsByLine.set(line, mergeCliRun(runsByLine.get(line), run));
  }
  return runsByLine;
}

function mergeCliRun(existing: CliRunSummary | undefined, incoming: CliRunSummary): CliRunSummary {
  if (!existing) return incoming;
  return CLI_RUN_STATUS_RANK[incoming.status] >= CLI_RUN_STATUS_RANK[existing.status]
    ? { ...existing, ...incoming }
    : existing;
}

function collectCliRuns(messages: UIMessage[]): CliRunSummary[] {
  const runsByKey = new Map<string, CliRunSummary>();
  for (const message of messages) {
    if (message.kind !== "trace") continue;
    let hasStructuredCliRun = false;
    for (const event of message.toolEvents ?? []) {
      const run = cliRunFromEvent(event);
      if (!run) continue;
      hasStructuredCliRun = true;
      runsByKey.set(run.key, mergeCliRun(runsByKey.get(run.key), run));
    }
    if (hasStructuredCliRun) continue;
    for (const line of traceLines(message)) {
      const run = parseCliRunTrace(line);
      if (!run || runsByKey.has(run.key)) continue;
      runsByKey.set(run.key, run);
    }
  }
  return [...runsByKey.values()];
}

function titleFromPresetName(name: string): string {
  return name
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ") || name;
}

/**
 * A row shows the shortened form and carries the full one as its tooltip, so
 * every formatter below takes the same flag rather than being written twice.
 */
function previewScalar(value: unknown, shorten = true): string | null {
  if (typeof value === "string" && value.trim()) {
    return shorten ? shortenArgValue(value.trim()) : value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

function previewMcpArgs(argsObject: unknown, shorten = true): string {
  if (!argsObject || typeof argsObject !== "object" || Array.isArray(argsObject)) {
    return previewScalar(argsObject, shorten) ?? "";
  }
  const record = argsObject as Record<string, unknown>;
  for (const key of ["url", "query", "q", "path", "name", "id", "title", "message", "text"]) {
    const preview = previewScalar(record[key], shorten);
    if (preview) return `${key}: ${preview}`;
  }
  const entries = Object.entries(record)
    .filter(([, value]) => previewScalar(value, shorten) !== null)
    .slice(0, 2)
    .map(([key, value]) => `${key}: ${previewScalar(value, shorten)}`);
  return entries.join(" · ");
}

function mcpRunFromToolName(
  toolName: string,
  argsObject: unknown,
  options: { key: string; status: McpRunStatus; error?: string },
): McpRunSummary | null {
  const match = MCP_TOOL_NAME_RE.exec(toolName);
  if (!match) return null;
  const presetName = match[1].toLowerCase();
  return {
    key: options.key,
    presetName,
    displayName: titleFromPresetName(presetName),
    toolName: match[2],
    argsPreview: previewMcpArgs(argsObject),
    status: options.status,
    error: options.error,
  };
}

function parseMcpRunTrace(line: string, status: McpRunStatus = "running"): McpRunSummary | null {
  const match = /^([a-z0-9_-]+)\((.*)\)$/i.exec(line.trim());
  if (!match || !MCP_TOOL_NAME_RE.test(match[1])) return null;
  const argsText = match[2].trim();
  let argsObject: unknown = {};
  if (argsText) {
    try {
      argsObject = JSON.parse(argsText);
    } catch {
      argsObject = argsText;
    }
  }
  return mcpRunFromToolName(match[1], argsObject, { key: line, status });
}

function mcpRunFromEvent(event: ToolProgressEvent): McpRunSummary | null {
  const name = toolEventName(event);
  if (!MCP_TOOL_NAME_RE.test(name)) return null;
  const argsObject = parseToolEventArguments(event);
  const key = event.call_id ? `call:${event.call_id}` : `${name}:${JSON.stringify(argsObject)}`;
  return mcpRunFromToolName(name, argsObject, {
    key,
    status: cliRunStatusFromPhase(event.phase),
    error: cliRunError(event),
  });
}

function mcpRunMapByTraceLine(message: UIMessage): Map<string, McpRunSummary> {
  const runsByLine = new Map<string, McpRunSummary>();
  for (const event of message.toolEvents ?? []) {
    const run = mcpRunFromEvent(event);
    if (!run) continue;
    const line = formatToolCallTrace(event);
    if (!line) continue;
    runsByLine.set(line, mergeMcpRun(runsByLine.get(line), run));
  }
  return runsByLine;
}

function mergeMcpRun(existing: McpRunSummary | undefined, incoming: McpRunSummary): McpRunSummary {
  if (!existing) return incoming;
  return MCP_RUN_STATUS_RANK[incoming.status] >= MCP_RUN_STATUS_RANK[existing.status]
    ? { ...existing, ...incoming }
    : existing;
}

function collectMcpRuns(messages: UIMessage[]): McpRunSummary[] {
  const runsByKey = new Map<string, McpRunSummary>();
  for (const message of messages) {
    if (message.kind !== "trace") continue;
    let hasStructuredMcpRun = false;
    for (const event of message.toolEvents ?? []) {
      const run = mcpRunFromEvent(event);
      if (!run) continue;
      hasStructuredMcpRun = true;
      runsByKey.set(run.key, mergeMcpRun(runsByKey.get(run.key), run));
    }
    if (hasStructuredMcpRun) continue;
    for (const line of traceLines(message)) {
      const run = parseMcpRunTrace(line);
      if (!run || runsByKey.has(run.key)) continue;
      runsByKey.set(run.key, run);
    }
  }
  return [...runsByKey.values()];
}

function displayCliArg(arg: string): string {
  return /\s/.test(arg) ? JSON.stringify(arg) : arg;
}

function formatCliArgs(run: CliRunSummary): string {
  const args = [...(run.json ? ["--json"] : []), ...run.args].map(displayCliArg);
  return args.join(" ");
}

function cliActivitySummaryKey(status: CliRunStatus | undefined, active: boolean): string {
  if (status === "error") return "message.cliActivityFailedOne";
  return active && status === "running" ? "message.cliActivityRunningOne" : "message.cliActivityRanOne";
}

function cliActivitySummaryDefault(status: CliRunStatus | undefined, active: boolean): string {
  if (status === "error") return "Failed @{{name}}";
  return `${active && status === "running" ? "Running" : "Ran"} @{{name}}`;
}

function cliActivityManySummaryKey(runs: CliRunSummary[], active: boolean): string {
  if (runs.some((run) => run.status === "error")) return "message.cliActivityFailedMany";
  return active && runs.some((run) => run.status === "running")
    ? "message.cliActivityRunningMany"
    : "message.cliActivityRanMany";
}

function cliActivityManySummaryDefault(runs: CliRunSummary[], active: boolean): string {
  if (runs.some((run) => run.status === "error")) return "{{count}} CLI apps failed";
  return `${active && runs.some((run) => run.status === "running") ? "Running" : "Ran"} {{count}} CLI apps`;
}

function mcpActivitySummaryKey(status: McpRunStatus | undefined, active: boolean): string {
  if (status === "error") return "message.mcpActivityFailedOne";
  return active && status === "running" ? "message.mcpActivityRunningOne" : "message.mcpActivityRanOne";
}

function mcpActivitySummaryDefault(status: McpRunStatus | undefined, active: boolean): string {
  if (status === "error") return "Failed {{name}}";
  return `${active && status === "running" ? "Calling" : "Called"} {{name}}`;
}

function mcpActivityManySummaryKey(runs: McpRunSummary[], active: boolean): string {
  if (runs.some((run) => run.status === "error")) return "message.mcpActivityFailedMany";
  return active && runs.some((run) => run.status === "running")
    ? "message.mcpActivityRunningMany"
    : "message.mcpActivityRanMany";
}

function mcpActivityManySummaryDefault(runs: McpRunSummary[], active: boolean): string {
  if (runs.some((run) => run.status === "error")) return "{{count}} MCP calls failed";
  return `${active && runs.some((run) => run.status === "running") ? "Calling" : "Called"} {{count}} MCP tools`;
}

function fileActivityVerb(editing: boolean, failed: boolean, deleted: boolean): string {
  if (failed) return "Failed";
  if (deleted) return editing ? "Deleting" : "Deleted";
  return editing ? "Editing" : "Edited";
}

function fileActivitySummaryKey(editing: boolean, failed: boolean, deleted: boolean): string {
  if (failed) return "message.fileActivityFailedOne";
  if (deleted) return editing ? "message.fileActivityDeletingOne" : "message.fileActivityDeletedOne";
  return editing ? "message.fileActivityEditingOne" : "message.fileActivityEditedOne";
}

function fileActivityManySummaryKey(editing: boolean, failed: boolean, deleted: boolean): string {
  if (failed) return "message.fileActivityFailedMany";
  if (deleted) return editing ? "message.fileActivityDeletingMany" : "message.fileActivityDeletedMany";
  return editing ? "message.fileActivityEditingMany" : "message.fileActivityEditedMany";
}

function fileEditCallKey(edit: UIFileEdit): string {
  if (edit.call_id && edit.path) return `${edit.call_id}|${edit.tool}|${edit.path}`;
  if (edit.call_id) return `${edit.call_id}|${edit.tool}`;
  return `${edit.tool}|${edit.path}`;
}

function collectFileEdits(messages: UIMessage[]): UIFileEdit[] {
  const edits: UIFileEdit[] = [];
  for (const message of messages) {
    if (message.kind === "trace" && message.fileEdits?.length) {
      edits.push(...message.fileEdits);
    }
  }
  return edits;
}

function latestFileEditEvents(edits: UIFileEdit[]): UIFileEdit[] {
  const order: string[] = [];
  const byKey = new Map<string, UIFileEdit>();
  for (const edit of edits) {
    const key = fileEditCallKey(edit);
    if (!byKey.has(key)) order.push(key);
    byKey.set(key, edit);
  }
  return order.map((key) => byKey.get(key)).filter(Boolean) as UIFileEdit[];
}

function summarizeFileEdits(edits: UIFileEdit[], active: boolean): FileEditSummary[] {
  return latestFileEditEvents(edits).flatMap((edit) => {
    const editing = active && edit.status === "editing";
    const failed = edit.status === "error";
    if (!edit.path && edit.pending && !editing) return [];
    if (!edit.path && !editing && !failed) return [];

    const status: UIFileEdit["status"] = editing
      ? "editing"
      : failed
        ? "error"
        : "done";
    const binary = !!edit.binary;
    const diff = hasRenderableFileDiff(edit.diff) ? edit.diff : undefined;
    return [{
      key: fileEditCallKey(edit),
      path: edit.path || "",
      absolute_path: edit.absolute_path,
      added: binary ? 0 : edit.added,
      deleted: binary ? 0 : edit.deleted,
      approximate: active && !!edit.approximate,
      binary,
      status,
      operation: edit.operation,
      pending: !!edit.pending && !edit.path,
      error: edit.error,
      diff,
    }];
  });
}

// ---------------------------------------------------------------------------
// Shell runs: Cursor-style terminal cards for exec/build/install commands.
// ---------------------------------------------------------------------------

function shellCommandFromArgsObject(argsObject: unknown): string {
  if (typeof argsObject === "string") return argsObject;
  if (!argsObject || typeof argsObject !== "object" || Array.isArray(argsObject)) return "";
  const record = argsObject as Record<string, unknown>;
  for (const key of ["command", "cmd", "script", "input"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function stringToolResult(result: unknown): string | undefined {
  if (typeof result === "string") return result;
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const content = (result as Record<string, unknown>).content;
    if (typeof content === "string") return content;
  }
  return undefined;
}

function liveToolOutput(event: ToolProgressEvent): string | undefined {
  return typeof event.output === "string" && event.output ? event.output : undefined;
}

function shellRunFromEvent(event: ToolProgressEvent): ShellRunSummary | null {
  const name = toolEventName(event);
  if (!isShellTraceName(name)) return null;
  const argsObject = parseToolEventArguments(event);
  const command = shellCommandFromArgsObject(argsObject) || name;
  const key = event.call_id ? `call:${event.call_id}` : `${name}:${JSON.stringify(argsObject)}`;
  return {
    key,
    command: redactShellCommand(command),
    status: cliRunStatusFromPhase(event.phase) as ShellRunStatus,
    output: stringToolResult(event.result) ?? liveToolOutput(event),
    error: cliRunError(event),
    startedAt: typeof event.client_started_at === "number" ? event.client_started_at : undefined,
    endedAt: typeof event.client_ended_at === "number" ? event.client_ended_at : undefined,
    percent: typeof event.percent === "number" ? event.percent : undefined,
    indeterminate: typeof event.indeterminate === "boolean" ? event.indeterminate : undefined,
    etaSeconds: typeof event.eta_s === "number" ? event.eta_s : undefined,
    label: typeof event.label === "string" ? event.label : undefined,
    // Only set when the frame carries the fact, so merging a later frame
    // (end/error) never erases what the start-of-run meta frame said.
    ...(typeof event.sandbox === "string" && event.sandbox ? { sandbox: event.sandbox } : {}),
    ...(event.sandbox_lifted === true ? { sandboxLifted: true } : {}),
  };
}

function parseShellRunTrace(line: string, status: ShellRunStatus = "done"): ShellRunSummary | null {
  const match = /^([a-zA-Z0-9_.-]+)\((.*)\)$/.exec(line.trim());
  if (!match || !isShellTraceName(match[1])) return null;
  const command = shellCommandFromArgs(match[2]);
  if (!command) return null;
  return { key: line, command: redactShellCommand(command), status };
}

function mergeShellRun(existing: ShellRunSummary | undefined, incoming: ShellRunSummary): ShellRunSummary {
  if (!existing) return incoming;
  if (CLI_RUN_STATUS_RANK[incoming.status] < CLI_RUN_STATUS_RANK[existing.status]) {
    return {
      ...existing,
      percent: incoming.percent ?? existing.percent,
      indeterminate: incoming.indeterminate ?? existing.indeterminate,
      etaSeconds: incoming.etaSeconds ?? existing.etaSeconds,
      label: incoming.label ?? existing.label,
      output: incoming.output || existing.output,
      sandbox: existing.sandbox ?? incoming.sandbox,
      sandboxLifted: existing.sandboxLifted ?? incoming.sandboxLifted,
    };
  }
  return {
    ...existing,
    ...incoming,
    percent: incoming.percent ?? existing.percent,
    indeterminate: incoming.indeterminate ?? existing.indeterminate,
    etaSeconds: incoming.etaSeconds ?? existing.etaSeconds,
    label: incoming.label ?? existing.label,
    output: incoming.output ?? existing.output,
    sandbox: incoming.sandbox ?? existing.sandbox,
    sandboxLifted: incoming.sandboxLifted ?? existing.sandboxLifted,
  };
}

function shellRunMapByTraceLine(message: UIMessage): Map<string, ShellRunSummary> {
  const runsByLine = new Map<string, ShellRunSummary>();
  for (const event of message.toolEvents ?? []) {
    const run = shellRunFromEvent(event);
    if (!run) continue;
    const line = formatToolCallTrace(event);
    if (!line) continue;
    runsByLine.set(line, mergeShellRun(runsByLine.get(line), run));
  }
  return runsByLine;
}

export function collectShellRuns(messages: UIMessage[]): ShellRunSummary[] {
  const runsByKey = new Map<string, ShellRunSummary>();
  for (const message of messages) {
    if (message.kind !== "trace") continue;
    let hasStructuredShellRun = false;
    for (const event of message.toolEvents ?? []) {
      const run = shellRunFromEvent(event);
      if (!run) continue;
      hasStructuredShellRun = true;
      runsByKey.set(run.key, mergeShellRun(runsByKey.get(run.key), run));
    }
    if (hasStructuredShellRun) continue;
    for (const line of traceLines(message)) {
      const run = parseShellRunTrace(line);
      if (!run || runsByKey.has(run.key)) continue;
      runsByKey.set(run.key, run);
    }
  }
  return [...runsByKey.values()];
}
