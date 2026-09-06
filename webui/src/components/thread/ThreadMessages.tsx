import {
  Fragment,
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useTranslation } from "react-i18next";
import { Archive, History, Loader2 } from "lucide-react";
import { MessageBubble } from "@/components/MessageBubble";
import { AgentActivityCluster } from "@/components/thread/AgentActivityCluster";
import { formatElapsed } from "@/components/thread/ParallelSubagentsPanel";
import {
  refusalOutdatedForSelection,
  type SelectedModelRoute,
} from "@/components/thread/QuotaLimitCard";
import { normalizeActivityTimeline, type TurnUnit } from "@/lib/activity-timeline";
import type { CheckpointNotice, ContextCompactionNotice } from "@/hooks/useNavinStream";
import { formatTaskModelHint, messageTaskHint } from "@/lib/task-route-label";
import type { CliAppInfo, McpPresetInfo, SlashCommand, UIMessage } from "@/lib/types";
import { requestOpenCheckpoints } from "@/lib/workbench-events";
import { useVirtualWindow } from "@/lib/virtualWindow";

interface ThreadMessagesProps {
  messages: UIMessage[];
  /** When true, agent turn still in flight - keeps activity timeline expanded. */
  isStreaming?: boolean;
  /** Selected model name, used as a live fallback before the turn stamps a route. */
  assistantName?: string;
  /** Provider behind the selected model; a refusal about another one is history. */
  selectedModelRoute?: SelectedModelRoute | null;
  liveTaskHint?: { modelLabel?: string; taskRole?: string; routed?: boolean };
  hiddenUserMessageCount?: number;
  cliApps?: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  slashCommands?: SlashCommand[];
  forkBoundaryMessageCount?: number | null;
  /** Latest automatic context compaction reported by the backend. */
  contextCompaction?: ContextCompactionNotice | null;
  /** Latest restore point snapshotted by the backend (one per user turn). */
  checkpointNotice?: CheckpointNotice | null;
  /** A block that belongs to the flow rather than after it - the plan card.
   *  Anchored after this many messages so it stays with the turn that produced
   *  it; pass null to keep it trailing the last message while a run is live. */
  pinnedBlock?: ReactNode;
  pinnedBlockAfterMessageCount?: number | null;
  onOpenFilePreview?: (path: string) => void;
  onForkFromMessage?: (beforeUserIndex: number) => void;
  onRevertResubmit?: (beforeUserIndex: number, content: string) => void | Promise<void>;
  /** Scroll parent used to window long threads; omit to render every unit. */
  scrollParentRef?: RefObject<HTMLElement | null>;
  /** Keep this user prompt mounted so jump-to-prompt can find its node. */
  revealPromptId?: string | null;
}

export type DisplayUnit = TurnUnit;

export function buildDisplayUnits(
  messages: UIMessage[],
  isStreaming = false,
): DisplayUnit[] {
  return normalizeActivityTimeline(messages, {
    preserveTrailingActivity: isStreaming,
  });
}

/** User / fork indexes for each unit, computed once so a virtual window can skip rows. */
export function unitActionIndexes(
  units: DisplayUnit[],
  hiddenUserMessageCount: number,
  copyFlags: boolean[],
): { userIndex: Array<number | undefined>; forkIndex: Array<number | undefined> } {
  const userIndex: Array<number | undefined> = new Array(units.length);
  const forkIndex: Array<number | undefined> = new Array(units.length);
  let nextUserIndex = hiddenUserMessageCount;
  for (let i = 0; i < units.length; i += 1) {
    const unit = units[i];
    if (unit.type === "message" && unit.message.role === "user") {
      userIndex[i] = nextUserIndex;
      nextUserIndex += 1;
    } else if (
      unit.type === "message"
      && unit.message.role === "assistant"
      && copyFlags[i]
    ) {
      forkIndex[i] = nextUserIndex;
    }
  }
  return { userIndex, forkIndex };
}

export function promptUnitIndex(units: DisplayUnit[], promptId: string | null | undefined): number {
  if (!promptId) return -1;
  return units.findIndex(
    (unit) => unit.type === "message" && unit.message.id === promptId,
  );
}

export function assistantCopyFlags(units: DisplayUnit[]): boolean[] {
  const flags = new Array<boolean>(units.length).fill(true);
  let hasLaterUnitBeforeUser = false;
  for (let i = units.length - 1; i >= 0; i -= 1) {
    const unit = units[i];
    if (unit.type === "message" && unit.message.role === "user") {
      hasLaterUnitBeforeUser = false;
      continue;
    }
    if (unit.type === "message" && unit.message.role === "assistant") {
      flags[i] = !hasLaterUnitBeforeUser;
    }
    hasLaterUnitBeforeUser = true;
  }
  return flags;
}

const EMPTY_CLI_APPS: CliAppInfo[] = [];
const EMPTY_MCP_PRESETS: McpPresetInfo[] = [];
const EMPTY_SLASH_COMMANDS: SlashCommand[] = [];

/** One visible unit. Memo so a scroll tick that does not change this row skips the bubble. */
const ThreadUnitRow = memo(function ThreadUnitRow({
  unit,
  index,
  marginTop,
  userPromptId,
  hasBodyBelow,
  isTurnStreaming,
  isLatestTurn,
  showCopyAction,
  superseded,
  nextAssistant,
  liveTaskHint,
  cliApps,
  mcpPresets,
  slashCommands,
  onOpenFilePreview,
  onForkFromMessage,
  onRevertResubmit,
  forkIndex,
  userIndexForEdit,
}: {
  unit: DisplayUnit;
  index: number;
  marginTop: string;
  userPromptId?: string;
  hasBodyBelow: boolean;
  isTurnStreaming: boolean;
  isLatestTurn: boolean;
  showCopyAction: boolean;
  superseded: boolean;
  nextAssistant?: UIMessage;
  liveTaskHint?: { modelLabel?: string; taskRole?: string; routed?: boolean };
  cliApps: CliAppInfo[];
  mcpPresets: McpPresetInfo[];
  slashCommands: SlashCommand[];
  onOpenFilePreview?: (path: string) => void;
  onForkFromMessage?: (beforeUserIndex: number) => void;
  onRevertResubmit?: (beforeUserIndex: number, content: string) => void | Promise<void>;
  forkIndex?: number;
  userIndexForEdit?: number;
}) {
  const handleFork = useCallback(() => {
    if (forkIndex !== undefined) onForkFromMessage?.(forkIndex);
  }, [forkIndex, onForkFromMessage]);
  const handleRevert = useCallback(
    (content: string) => {
      if (userIndexForEdit === undefined) return;
      return onRevertResubmit?.(userIndexForEdit, content);
    },
    [onRevertResubmit, userIndexForEdit],
  );

  return (
    <div
      className={marginTop}
      data-user-prompt-id={userPromptId}
      data-message-id={unit.type === "message" ? unit.message.id : undefined}
      data-virtual-index={index}
    >
      {unit.type === "activity" ? (
        <AgentActivityCluster
          messages={unit.messages}
          isTurnStreaming={isTurnStreaming}
          isLatestTurn={isLatestTurn}
          hasBodyBelow={hasBodyBelow}
          turnLatencyMs={unit.turnLatencyMs}
          startedAtMs={unit.startedAtMs}
          endedAtMs={unit.endedAtMs}
          cliApps={cliApps}
          mcpPresets={mcpPresets}
          onOpenFilePreview={onOpenFilePreview}
          liveTaskHint={isTurnStreaming ? liveTaskHint : messageTaskHint(nextAssistant)}
        />
      ) : (
        <MessageBubble
          message={unit.message}
          showCopyAction={showCopyAction}
          cliApps={cliApps}
          mcpPresets={mcpPresets}
          slashCommands={slashCommands}
          onOpenFilePreview={onOpenFilePreview}
          onForkFromHere={forkIndex !== undefined ? handleFork : undefined}
          onRevertResubmit={userIndexForEdit !== undefined ? handleRevert : undefined}
          superseded={superseded}
        />
      )}
    </div>
  );
});

export function ThreadMessages({
  messages,
  isStreaming = false,
  assistantName = "",
  selectedModelRoute = null,
  liveTaskHint,
  hiddenUserMessageCount = 0,
  cliApps = EMPTY_CLI_APPS,
  mcpPresets = EMPTY_MCP_PRESETS,
  slashCommands = EMPTY_SLASH_COMMANDS,
  forkBoundaryMessageCount = null,
  contextCompaction = null,
  checkpointNotice = null,
  pinnedBlock = null,
  pinnedBlockAfterMessageCount = null,
  onOpenFilePreview,
  onForkFromMessage,
  onRevertResubmit,
  scrollParentRef,
  revealPromptId = null,
}: ThreadMessagesProps) {
  const { t } = useTranslation();
  const units = useMemo(() => buildDisplayUnits(messages, isStreaming), [isStreaming, messages]);
  const forkBoundaryAfterUnitIndex = useMemo(
    () => unitIndexAfterMessageCount(units, forkBoundaryMessageCount),
    [forkBoundaryMessageCount, units],
  );
  const compactionAfterUnitIndex = useMemo(
    () => unitIndexAfterMessageCount(units, contextCompaction?.afterMessageCount ?? null),
    [contextCompaction, units],
  );
  const checkpointAfterUnitIndex = useMemo(
    () => unitIndexAfterMessageCount(units, checkpointNotice?.afterMessageCount ?? null),
    [checkpointNotice, units],
  );
  // No anchor yet means the run is still live, and the block trails the last
  // message the way it always has.
  const pinnedBlockAfterUnitIndex = useMemo(
    () =>
      unitIndexAfterMessageCount(
        units,
        pinnedBlockAfterMessageCount ?? messages.length,
      ),
    [messages.length, pinnedBlockAfterMessageCount, units],
  );
  const copyFlags = useMemo(() => assistantCopyFlags(units), [units]);
  const actionIndexes = useMemo(
    () => unitActionIndexes(units, hiddenUserMessageCount, copyFlags),
    [copyFlags, hiddenUserMessageCount, units],
  );
  const liveActivityClusterIndices = useMemo(
    () => isStreaming ? currentActivityClusterIndices(units) : new Set<number>(),
    [isStreaming, units],
  );
  const unitKeys = useMemo(() => unitKeysForDisplay(units), [units]);
  const pinnedPromptIndex = useMemo(
    () => promptUnitIndex(units, revealPromptId),
    [revealPromptId, units],
  );
  const pinIndexes = useMemo(
    () => (pinnedPromptIndex >= 0 ? [pinnedPromptIndex] : undefined),
    [pinnedPromptIndex],
  );
  const virtual = useVirtualWindow({
    count: units.length,
    scrollParentRef,
    enabled: Boolean(scrollParentRef),
    pinIndexes,
  });
  const start = virtual.window?.start ?? 0;
  const end = virtual.window?.end ?? units.length;

  const lastUnit = units[units.length - 1];
  const latestActivityIndex = useMemo(() => lastActivityUnitIndex(units), [units]);
  const lastUserUnitIndex = useMemo(() => lastUserMessageUnitIndex(units), [units]);
  const awaitingFirstOutput =
    isStreaming
    && (!lastUnit || (lastUnit.type === "message" && lastUnit.message.role === "user"));

  return (
    <div ref={virtual.listRef} className="flex w-full flex-col">
      {virtual.window && virtual.window.paddingTop > 0 ? (
        <div aria-hidden style={{ height: virtual.window.paddingTop }} data-testid="thread-virtual-pad-top" />
      ) : null}
      {units.slice(start, end).map((unit, offset) => {
        const index = start + offset;
        const prev = units[index - 1];
        const marginTop =
          index > 0
            ? marginAfterPrevUnit(prev)
            : "";
        const next = units[index + 1];
        const hasBodyBelow =
          unit.type === "activity"
          && next?.type === "message"
          && next.message.role === "assistant";

        const userPromptId =
          unit.type === "message" && unit.message.role === "user"
            ? unit.message.id
            : undefined;
        const userIndexForEdit = actionIndexes.userIndex[index];
        const forkIndex = actionIndexes.forkIndex[index];

        return (
          <Fragment key={unitKeys[index]}>
            <ThreadUnitRow
              unit={unit}
              index={index}
              marginTop={marginTop}
              userPromptId={userPromptId}
              hasBodyBelow={hasBodyBelow}
              isTurnStreaming={liveActivityClusterIndices.has(index)}
              isLatestTurn={index === latestActivityIndex}
              showCopyAction={
                unit.type === "message" && unit.message.role === "assistant"
                  ? copyFlags[index]
                  : true
              }
              superseded={
                unit.type === "message"
                && unit.message.role === "assistant"
                && (index < lastUserUnitIndex
                  || refusalOutdatedForSelection(unit.message.content, selectedModelRoute))
              }
              nextAssistant={
                next?.type === "message" && next.message.role === "assistant"
                  ? next.message
                  : undefined
              }
              liveTaskHint={
                liveActivityClusterIndices.has(index) ? liveTaskHint : undefined
              }
              cliApps={cliApps}
              mcpPresets={mcpPresets}
              slashCommands={slashCommands}
              onOpenFilePreview={onOpenFilePreview}
              onForkFromMessage={onForkFromMessage}
              onRevertResubmit={onRevertResubmit}
              forkIndex={forkIndex}
              userIndexForEdit={userIndexForEdit}
            />
            {index === forkBoundaryAfterUnitIndex ? (
              <ForkBoundaryDivider label={t("thread.forkedFromHistory")} />
            ) : null}
            {contextCompaction && index === compactionAfterUnitIndex ? (
              <ContextCompactionDivider notice={contextCompaction} />
            ) : null}
            {checkpointNotice && index === checkpointAfterUnitIndex ? (
              <CheckpointDivider />
            ) : null}
            {pinnedBlock && index === pinnedBlockAfterUnitIndex ? pinnedBlock : null}
          </Fragment>
        );
      })}
      {virtual.window && virtual.window.paddingBottom > 0 ? (
        <div aria-hidden style={{ height: virtual.window.paddingBottom }} data-testid="thread-virtual-pad-bottom" />
      ) : null}
      {contextCompaction && compactionAfterUnitIndex === null ? (
        <ContextCompactionDivider notice={contextCompaction} />
      ) : null}
      {pinnedBlock && pinnedBlockAfterUnitIndex === null ? pinnedBlock : null}
      {awaitingFirstOutput ? (
        <AwaitingFirstOutputIndicator
          taskHint={formatTaskModelHint(
            liveTaskHint?.modelLabel || assistantName,
            liveTaskHint?.taskRole,
            t,
            Boolean(liveTaskHint?.routed),
          )}
        />
      ) : null}
    </div>
  );
}

/** Live status while the agent has accepted the turn but not emitted yet.
 *  First messages (and cold model starts) can sit quiet for a while - without
 *  an elapsed clock it looks frozen. */
export function awaitingFirstOutputPhase(
  elapsedMs: number,
): "thinking" | "working" | "slow" {
  if (elapsedMs < 15_000) return "thinking";
  if (elapsedMs < 60_000) return "working";
  return "slow";
}

function AwaitingFirstOutputIndicator({
  taskHint = "",
}: {
  taskHint?: string;
}) {
  const { t } = useTranslation();
  const startedAtRef = useRef(Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    startedAtRef.current = Date.now();
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const elapsedMs = Math.max(0, now - startedAtRef.current);
  const elapsed = formatElapsed(elapsedMs);
  const phase = awaitingFirstOutputPhase(elapsedMs);

  // Duration lives in ONE place. "Working · 42s" plus a second "42s"
  // produced the double timer on the status line.
  const label =
    phase === "thinking"
      ? t("thread.assistantThinking", { defaultValue: "Thinking…" })
      : phase === "working"
        ? t("thread.assistantWorkingFor", { defaultValue: "Working" })
        : t("thread.assistantStillRunning", {
            duration: elapsed,
            defaultValue:
              "Still running · {{duration}} - not stuck yet. Use Stop if you want to cancel.",
          });

  return (
    <div className="mt-5" data-testid="assistant-awaiting-indicator">
      <div className="inline-flex max-w-full flex-col gap-1.5">
        <span className="inline-flex items-center gap-2 text-[13.5px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary/70" aria-hidden />
          <span className="min-w-0 animate-pulse">{label}</span>
          {phase !== "slow" ? (
            <span
              className="shrink-0 tabular-nums text-[12px] text-muted-foreground/70"
              aria-hidden
            >
              {elapsed}
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1" aria-hidden>
            <PulseDot delay="0ms" />
            <PulseDot delay="150ms" />
            <PulseDot delay="300ms" />
          </span>
        </span>
        {taskHint ? (
          <span
            className="pl-5 text-[11px] text-muted-foreground/55"
            data-testid="activity-task-model"
          >
            {taskHint}
          </span>
        ) : null}
        {phase === "slow" ? (
          <span className="text-[12px] text-muted-foreground/70">
            {t("thread.assistantAwaitingHint", {
              defaultValue:
                "The model is still preparing the first tokens (cold start or a heavy first step).",
            })}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function PulseDot({ delay }: { delay: string }) {
  return (
    <span
      style={{ animationDelay: delay }}
      className="inline-block h-1 w-1 animate-bounce rounded-full bg-muted-foreground/60"
    />
  );
}

function unitIndexAfterMessageCount(
  units: DisplayUnit[],
  messageCount: number | null | undefined,
): number | null {
  if (messageCount == null || messageCount <= 0) return null;
  let seen = 0;
  for (let i = 0; i < units.length; i += 1) {
    const unit = units[i];
    seen += unit.type === "activity" ? unit.messages.length : 1;
    if (seen >= messageCount) return i;
  }
  return null;
}

function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

function CheckpointDivider() {
  const { t } = useTranslation();
  return (
    <div
      className="my-4 flex items-center gap-3 text-[11px] text-muted-foreground/70"
      data-testid="thread-checkpoint-divider"
    >
      <span aria-hidden className="h-px flex-1 bg-border/60" />
      <span className="inline-flex shrink-0 items-center gap-1.5">
        <History className="h-3 w-3" aria-hidden />
        <span>
          {t("thread.checkpointSaved", {
            defaultValue: "Restore point saved",
          })}
        </span>
        <button
          type="button"
          onClick={requestOpenCheckpoints}
          className="rounded px-1 py-0.5 font-medium text-foreground/70 underline decoration-dotted underline-offset-2 transition-colors hover:bg-accent hover:text-foreground"
          data-testid="thread-checkpoint-open"
        >
          {t("thread.checkpointOpen", { defaultValue: "View checkpoints" })}
        </button>
      </span>
      <span aria-hidden className="h-px flex-1 bg-border/60" />
    </div>
  );
}

function ContextCompactionDivider({ notice }: { notice: ContextCompactionNotice }) {
  const { t } = useTranslation();
  const label = notice.kind === "idle"
    ? t("thread.contextCompactedIdle", {
        defaultValue: "Older conversation summarized after inactivity",
      })
    : t("thread.contextCompacted", {
        defaultValue: "Conversation summarized to stay within the context window",
      });
  const parts: string[] = [];
  if (notice.messagesArchived > 0) {
    parts.push(
      t("thread.contextCompactedMessages", {
        defaultValue: "{{count}} older messages archived",
        count: notice.messagesArchived,
      }),
    );
  }
  if (typeof notice.tokensBefore === "number" && typeof notice.tokensAfter === "number") {
    parts.push(`${formatTokenCount(notice.tokensBefore)} → ${formatTokenCount(notice.tokensAfter)} tokens`);
  }
  return (
    <div className="my-5 flex items-center gap-3 text-[11px] text-muted-foreground/80">
      <span aria-hidden className="h-px flex-1 bg-border/70" />
      <span className="inline-flex shrink-0 items-center gap-1.5">
        <Archive className="h-3 w-3" aria-hidden />
        <span>
          {label}
          {parts.length > 0 ? ` (${parts.join(", ")})` : ""}
        </span>
      </span>
      <span aria-hidden className="h-px flex-1 bg-border/70" />
    </div>
  );
}

function ForkBoundaryDivider({ label }: { label: string }) {
  return (
    <div className="my-5 flex items-center gap-3 text-[11px] text-muted-foreground/80">
      <span aria-hidden className="h-px flex-1 bg-border/70" />
      <span className="shrink-0">{label}</span>
      <span aria-hidden className="h-px flex-1 bg-border/70" />
    </div>
  );
}

/** The most recent activity cluster: the only one that stays unfolded by default. */
export function lastActivityUnitIndex(units: DisplayUnit[]): number {
  for (let i = units.length - 1; i >= 0; i -= 1) {
    if (units[i].type === "activity") return i;
  }
  return -1;
}

/** Index of the most recent user prompt; assistant rows before it are history. */
export function lastUserMessageUnitIndex(units: DisplayUnit[]): number {
  for (let i = units.length - 1; i >= 0; i -= 1) {
    const unit = units[i];
    if (unit.type === "message" && unit.message.role === "user") return i;
  }
  return -1;
}

function currentActivityClusterIndices(units: DisplayUnit[]): Set<number> {
  const indices = new Set<number>();
  let markedCurrentActivity = false;
  for (let i = units.length - 1; i >= 0; i -= 1) {
    const unit = units[i];
    if (unit.type === "activity") {
      if (!markedCurrentActivity) {
        indices.add(i);
        markedCurrentActivity = true;
      }
      continue;
    }
    if (unit.message.role === "assistant" && unit.message.isStreaming) continue;
    if (unit.message.role === "user") break;
  }
  return indices;
}

export function unitKeysForDisplay(units: DisplayUnit[]): string[] {
  const occurrences = new Map<string, number>();
  return units.map((unit, index) => {
    const base = unitKeyBase(unit, index);
    if (!base.startsWith("turn-") || base.endsWith("-user")) return base;
    const next = (occurrences.get(base) ?? 0) + 1;
    occurrences.set(base, next);
    return `${base}-${next}`;
  });
}

function unitKeyBase(unit: DisplayUnit, index: number): string {
  if (unit.type === "activity") {
    const anchor = unit.messages[0];
    const turnKey = stableTurnMessageKey(anchor, "activity");
    if (turnKey) return turnKey;
    const anchorId = anchor?.id;
    return anchorId != null ? `activity-${anchorId}` : `activity-idx-${index}`;
  }
  const turnKey = stableTurnMessageKey(unit.message, "message");
  if (turnKey) return turnKey;
  return unit.message.id;
}

function stableTurnMessageKey(
  message: UIMessage | undefined,
  unitKind: "activity" | "message",
): string | null {
  if (!message?.turnId) return null;
  if (message.role === "user") return `turn-${message.turnId}-user`;
  // Do not put turnPhase / kind in the key. Those fields change as the turn
  // progresses (activity -> assistant, missing phase -> filled phase). A new
  // key remounts the bubble and replays fade-in, which reads as the chat
  // blinking.
  if (unitKind === "activity" || message.kind === "trace") {
    return `turn-${message.turnId}-activity`;
  }
  return `turn-${message.turnId}-assistant`;
}

function marginAfterPrevUnit(prev: DisplayUnit): string {
  if (prev.type === "activity") {
    return "mt-4";
  }
  const p = prev.message;
  const denseP =
    p.kind === "trace"
    || (
      p.role === "assistant"
      && p.content.trim().length === 0
      && (!!p.reasoning || !!p.reasoningStreaming)
    );
  if (denseP) {
    return "mt-2";
  }
  return "mt-5";
}
