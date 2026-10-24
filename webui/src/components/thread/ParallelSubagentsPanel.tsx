// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronRight, CircleSlash, Clock, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import type { SubagentProgressUpdate, SubagentStep } from "@/lib/navin-client";
import { cn } from "@/lib/utils";
import { TeamBotAvatar } from "@/components/team/TeamBotAvatar";

/** Subagent rows shown before the "Show more" control. */
export const SUBAGENT_VISIBLE_MAX = 6;

export function isQueuedCard(card: Pick<SubagentProgressUpdate, "done" | "phase">): boolean {
  return !card.done && card.phase === "queued";
}

export type ParallelSubagentCard = SubagentProgressUpdate & {
  updatedAt: number;
  /** Wall clock start, kept across updates so the elapsed time never resets
   * and a replayed card does not claim to have just started. */
  startedAt: number;
  /** When the card first arrived done; the linger counts from here, so a
   * late duplicate frame does not keep a finished card on screen. */
  finishedAt?: number;
};

/** Keep a finished card readable for a moment instead of blinking out. */
export const FINISHED_CARD_LINGER_MS = 12_000;

/**
 * Merge a progress frame into the card list, keeping one card per task.
 *
 * ``startedAt`` survives updates on purpose: the gateway replays running
 * subagents when a client attaches, and a card whose clock restarted on every
 * frame would tell the user a ten-minute task had just begun.
 */
export function upsertSubagentCard(
  prev: ParallelSubagentCard[],
  update: SubagentProgressUpdate,
  now: number = Date.now(),
): ParallelSubagentCard[] {
  const idx = prev.findIndex((c) => c.taskId === update.taskId);
  const existing = idx === -1 ? undefined : prev[idx];
  const next: ParallelSubagentCard = {
    ...update,
    updatedAt: now,
    startedAt: startedAtForUpdate(existing, update, now),
    finishedAt: update.done ? (existing?.finishedAt ?? now) : undefined,
  };
  if (idx === -1) return [...prev, next];
  const copy = prev.slice();
  copy[idx] = next;
  return copy;
}

function startedAtForUpdate(
  existing: ParallelSubagentCard | undefined,
  update: SubagentProgressUpdate,
  now: number,
): number {
  const fromReplay = now - (update.startedMsAgo ?? 0);
  // The gateway restarts the clock when a queued spawn actually begins, so a
  // wait must not be billed as work. Keeping the enqueue time across that
  // transition would also jump backwards after a refresh, which reads as a bug.
  if (existing && existing.phase === "queued" && update.phase !== "queued") {
    return fromReplay;
  }
  return existing?.startedAt ?? fromReplay;
}

/**
 * Drop finished cards once they have been on screen long enough to read.
 *
 * A card the user has opened (``keep``) stays: removing it under their eyes
 * would close the details they are reading. Returns ``cards`` itself when
 * nothing goes, so a caller holding it in state does not re-render for nothing.
 */
export function pruneFinishedSubagentCards(
  cards: ParallelSubagentCard[],
  now: number = Date.now(),
  keep?: ReadonlySet<string>,
): ParallelSubagentCard[] {
  const next = cards.filter(
    (card) =>
      !card.done ||
      now - finishedAtOf(card) < FINISHED_CARD_LINGER_MS ||
      Boolean(keep?.has(card.taskId)),
  );
  return next.length === cards.length ? cards : next;
}

function finishedAtOf(card: ParallelSubagentCard): number {
  return card.finishedAt ?? card.updatedAt;
}

/**
 * When the next finished card is due to go (epoch ms), or null if none is.
 *
 * Taken from the cards' own finish times, so frames from cards still running
 * leave it unchanged: a timer restarted on every frame never fired while a
 * sibling kept working, and finished cards stayed up for the whole wave.
 */
export function nextSubagentPruneAt(
  cards: ParallelSubagentCard[],
  keep?: ReadonlySet<string>,
): number | null {
  let due: number | null = null;
  for (const card of cards) {
    if (!card.done || keep?.has(card.taskId)) continue;
    const at = finishedAtOf(card) + FINISHED_CARD_LINGER_MS;
    if (due === null || at < due) due = at;
  }
  return due;
}

export function isFailedCard(
  card: Pick<SubagentProgressUpdate, "done" | "phase" | "error">,
): boolean {
  return card.done && (card.phase === "error" || Boolean(card.error));
}

/** Stopped on request (/stop, a cancelled wave): not a failure, drawn neutral. */
export function isCancelledCard(
  card: Pick<SubagentProgressUpdate, "done" | "phase" | "error" | "errorKind">,
): boolean {
  return isFailedCard(card) && card.errorKind === "cancelled";
}

const STEP_KINDS = new Set([
  "read",
  "write",
  "edit",
  "list",
  "find",
  "search",
  "run",
  "web_search",
  "fetch",
  "patch",
]);

const ERROR_KINDS = new Set(["timeout", "stalled", "cancelled", "limit", "other"]);

/** "Wrote ar.json", localized from the step's stable kind. */
export function subagentStepLabel(step: SubagentStep, t: TFunction): string {
  const kind = STEP_KINDS.has(step.kind) ? step.kind : "tool";
  const name = step.name || "tool";
  const verb = t(`thread.subagents.step.${kind}.${step.status}`, {
    defaultValue: step.verb || name,
    name,
  });
  return [verb, step.target].filter(Boolean).join(" ").trim();
}

/**
 * Short readable reason for a stopped card, never a raw traceback.
 *
 * An unknown or missing kind reads as a generic "Stopped on an error": the
 * gateway's own text for those can carry class names, paths or hosts.
 */
export function subagentErrorReason(
  card: Pick<SubagentProgressUpdate, "errorKind" | "statusLine">,
  t: TFunction,
): string {
  const kind = card.errorKind && ERROR_KINDS.has(card.errorKind) ? card.errorKind : "other";
  return t(`thread.subagents.error.${kind}`, {
    defaultValue: kind === "other" ? "Stopped on an error" : card.statusLine,
  });
}

/** The one line under a card's title, localized where the gateway's is not. */
export function subagentStatusLine(card: ParallelSubagentCard, t: TFunction): string {
  if (isQueuedCard(card) || card.statusLine === "Queued - waiting for a free slot") {
    return t("thread.subagents.queued", {
      defaultValue: "Queued - waiting for a free slot",
    });
  }
  if (isFailedCard(card)) return subagentErrorReason(card, t);
  if (card.done) {
    return t("thread.subagents.completed", { defaultValue: "Completed" });
  }
  if (card.phase === "thinking") {
    return t("thread.subagents.thinking", { defaultValue: "Thinking…" });
  }
  if (card.phase === "responding" || card.phase === "final_response") {
    return t("thread.subagents.writingResponse", { defaultValue: "Writing response…" });
  }
  if (card.phase === "awaiting_tools" || card.phase === "tools_completed") {
    // A failed call is usually retried right away: the live line names what
    // is running or last went right, failures belong to the final result.
    const steps = card.steps ?? [];
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      if (steps[i].status !== "error") return subagentStepLabel(steps[i], t);
    }
  }
  switch (card.statusLine) {
    case "Preparing isolated checkout…":
      return t("thread.subagents.isolating", {
        defaultValue: "Preparing isolated checkout…",
      });
    case "Starting…":
      return t("thread.subagents.starting", { defaultValue: "Starting…" });
    case "Writing response…":
      return t("thread.subagents.writingResponse", {
        defaultValue: "Writing response…",
      });
    case "Thinking…":
      return t("thread.subagents.thinking", { defaultValue: "Thinking…" });
    case "Working…":
      return t("thread.subagents.working", { defaultValue: "Working…" });
    default:
      return card.statusLine;
  }
}

/** "1.2k", "335k": a token count readable at a glance. */
export function formatTokens(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  if (tokens < 1_000_000) {
    const k = tokens / 1000;
    return `${k < 10 ? k.toFixed(1).replace(/\.0$/, "") : Math.round(k)}k`;
  }
  const m = tokens / 1_000_000;
  return `${m < 10 ? m.toFixed(1).replace(/\.0$/, "") : Math.round(m)}M`;
}

/** Compact elapsed time: "12s", "3m", "1h 4m". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/** Re-render once a second, but only while something is still running. */
function useElapsedClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export type SubagentCardsSnapshot = {
  cards: ParallelSubagentCard[];
  /** Cards the user opened: they must not linger out while being read. */
  expanded: ReadonlySet<string>;
};

export type SubagentCardStore = {
  getSnapshot: () => SubagentCardsSnapshot;
  subscribe: (listener: () => void) => () => void;
  upsert: (update: SubagentProgressUpdate) => void;
  prune: (now?: number) => void;
  setExpanded: (next: ReadonlySet<string>) => void;
  reset: () => void;
};

/**
 * The subagent cards of one thread, outside React state.
 *
 * A busy wave sends several frames a second. Held in the thread shell's
 * state, each frame re-rendered the whole conversation; here only the panel
 * that reads the store does. The shell still owns the store, so frames that
 * arrive while the panel is not mounted (history still loading after a chat
 * switch, when the gateway replays running work) are kept.
 */
export function createSubagentCardStore(): SubagentCardStore {
  let snapshot: SubagentCardsSnapshot = { cards: [], expanded: new Set() };
  const listeners = new Set<() => void>();
  const set = (next: SubagentCardsSnapshot) => {
    if (next.cards === snapshot.cards && next.expanded === snapshot.expanded) return;
    snapshot = next;
    for (const listener of listeners) listener();
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    upsert: (update) =>
      set({ ...snapshot, cards: upsertSubagentCard(snapshot.cards, update) }),
    prune: (now = Date.now()) =>
      set({
        ...snapshot,
        cards: pruneFinishedSubagentCards(snapshot.cards, now, snapshot.expanded),
      }),
    setExpanded: (expanded) => set({ ...snapshot, expanded }),
    reset: () => set({ cards: [], expanded: new Set() }),
  };
}

/** The panel for a thread's card store, which also clears finished cards. */
export function LiveSubagentsPanel({
  store,
  className,
}: {
  store: SubagentCardStore;
  className?: string;
}) {
  const { cards, expanded } = useSyncExternalStore(store.subscribe, store.getSnapshot);

  // Drop finished cards once they have been readable for a moment. The
  // deadline comes from the finish times alone, so frames from cards still
  // running neither restart nor postpone it. An opened card stays until it
  // is closed, then lingers like any other.
  const pruneAt = useMemo(() => nextSubagentPruneAt(cards, expanded), [cards, expanded]);
  useEffect(() => {
    if (pruneAt === null) return;
    const timer = window.setTimeout(
      () => store.prune(),
      // A hair past the deadline, so the prune never runs a tick early and
      // keeps the card with no new deadline left to wake it.
      Math.max(0, pruneAt - Date.now()) + 50,
    );
    return () => window.clearTimeout(timer);
  }, [pruneAt, store]);

  return (
    <ParallelSubagentsPanel
      cards={cards}
      className={className}
      expandedIds={expanded}
      onExpandedChange={store.setExpanded}
    />
  );
}

/**
 * Cursor-style stacked cards for parallel background subagents.
 * Shown above the composer while one or more ``spawn`` tasks are in flight.
 *
 * Cards open on click to show what the subagent is doing. The open set can be
 * owned by the parent (``expandedIds`` / ``onExpandedChange``) so it can keep
 * an opened card from lingering out; otherwise the panel keeps it itself.
 */
export function ParallelSubagentsPanel({
  cards,
  className,
  expandedIds,
  onExpandedChange,
}: {
  cards: ParallelSubagentCard[];
  className?: string;
  expandedIds?: ReadonlySet<string>;
  onExpandedChange?: (next: ReadonlySet<string>) => void;
}) {
  const { t } = useTranslation();
  const [localExpanded, setLocalExpanded] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const expanded = expandedIds ?? localExpanded;
  const [showAll, setShowAll] = useState(false);
  const toggle = useCallback(
    (taskId: string) => {
      const next = new Set(expanded);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      if (onExpandedChange) onExpandedChange(next);
      if (!expandedIds) setLocalExpanded(next);
    },
    [expanded, expandedIds, onExpandedChange],
  );
  const queued = cards.filter(isQueuedCard).length;
  const working = cards.filter((c) => !c.done && !isQueuedCard(c)).length;
  const inFlight = queued + working;
  const now = useElapsedClock(inFlight > 0);
  if (cards.length === 0) return null;

  const waiting =
    working > 0 && queued > 0
      ? t("thread.subagents.mixed", {
          defaultValue: "{{running}} running, {{queued}} queued",
          running: working,
          queued,
        })
      : queued > 1
        ? t("thread.subagents.queued_other", {
            defaultValue: "{{count}} queued, waiting for a slot",
            count: queued,
          })
        : queued === 1
          ? t("thread.subagents.queuedOne", {
              defaultValue: "1 queued, waiting for a slot",
            })
          : working > 1
            ? t("thread.subagents.waiting_other", {
                defaultValue: "Waiting for {{count}} subagents",
                count: working,
              })
            : working === 1
              ? t("thread.subagents.waiting", {
                  defaultValue: "Waiting for subagent",
                })
              : t("thread.subagents.allDone", {
                  defaultValue: "Subagents finished",
                });

  return (
    <div
      data-testid="parallel-subagents-panel"
      className={cn("mt-1.5 w-full space-y-0.5", className)}
    >
      {(showAll ? cards : cards.slice(0, SUBAGENT_VISIBLE_MAX)).map((card) => (
        <SubagentCard
          key={card.taskId}
          card={card}
          // Only a running card's clock moves; a finished or queued one keeps
          // a constant prop so its memoized row skips the per-second tick.
          now={card.done || isQueuedCard(card) ? 0 : now}
          expanded={expanded.has(card.taskId)}
          onToggle={toggle}
        />
      ))}
      {cards.length > SUBAGENT_VISIBLE_MAX ? (
        <button
          type="button"
          data-testid="parallel-subagents-show-more"
          aria-expanded={showAll}
          data-hidden-count={cards.length - SUBAGENT_VISIBLE_MAX}
          onClick={() => setShowAll((value) => !value)}
          className="rounded px-1 text-[12px] font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {showAll
            ? t("thread.subagents.showLess", { defaultValue: "Show less" })
            : t("thread.subagents.showMore", {
                defaultValue: "Show more ({{count}})",
                count: cards.length - SUBAGENT_VISIBLE_MAX,
              })}
        </button>
      ) : null}
      <p className="px-1 pt-0.5 text-[12px] text-muted-foreground">{waiting}</p>
    </div>
  );
}

type CardState = "queued" | "running" | "ok" | "error" | "stopped";

function StatusIcon({
  state,
  size = "md",
}: {
  state: CardState;
  size?: "md" | "sm";
}) {
  const cls = size === "md" ? "h-4 w-4" : "h-3.5 w-3.5";
  if (state === "queued") {
    return <Clock className={cn(cls, "text-muted-foreground/70")} aria-hidden />;
  }
  if (state === "running") {
    return (
      <Loader2 className={cn(cls, "animate-spin text-muted-foreground/70")} aria-hidden />
    );
  }
  if (state === "stopped") {
    return <CircleSlash className={cn(cls, "text-muted-foreground/70")} aria-hidden />;
  }
  if (state === "error") {
    return <X className={cn(cls, "text-red-500")} strokeWidth={2.5} aria-hidden />;
  }
  return <Check className={cn(cls, "text-emerald-500")} strokeWidth={2.5} aria-hidden />;
}

const SubagentCard = memo(function SubagentCard({
  card,
  now,
  expanded,
  onToggle,
}: {
  card: ParallelSubagentCard;
  now: number;
  expanded: boolean;
  onToggle: (taskId: string) => void;
}) {
  const { t } = useTranslation();
  const detailsId = useId();
  const failed = isFailedCard(card);
  const cancelled = isCancelledCard(card);
  const queued = isQueuedCard(card);
  // A finished card freezes on its own duration; a running one keeps counting.
  // A queued one has not started: showing a rising clock would look like work.
  const elapsed = queued
    ? ""
    : formatElapsed((card.done ? (card.finishedAt ?? card.updatedAt) : now) - card.startedAt);
  const meta = [card.model, elapsed].filter(Boolean).join(" · ");
  const statusLine = subagentStatusLine(card, t);
  const state: CardState = queued
    ? "queued"
    : !card.done
      ? "running"
      : cancelled
        ? "stopped"
        : failed
          ? "error"
          : "ok";
  const stateLabel = queued
    ? t("thread.subagents.queued", { defaultValue: "Queued - waiting for a free slot" })
    : !card.done
      ? t("thread.subagents.statusRunning", { defaultValue: "Running" })
      : failed
        ? t("thread.subagents.statusStopped", { defaultValue: "Stopped" })
        : t("thread.subagents.statusDone", { defaultValue: "Done" });

  // Flat inline row, not a card: these live in the conversation flow.
  // Only phrasing content inside the button (spans), and the details are a
  // sibling region it points at only while that region exists.
  return (
    <div
      data-testid={`subagent-card-${card.taskId}`}
      data-phase={card.phase}
      data-expanded={expanded ? "true" : "false"}
      className={cn(
        "rounded-md transition-colors",
        expanded ? "bg-muted/30" : "hover:bg-muted/30",
      )}
    >
      <button
        type="button"
        onClick={() => onToggle(card.taskId)}
        aria-expanded={expanded}
        aria-controls={expanded ? detailsId : undefined}
        title={
          expanded
            ? t("thread.subagents.hideDetails", { defaultValue: "Hide details" })
            : t("thread.subagents.showDetails", { defaultValue: "Show details" })
        }
        className="flex w-full items-start gap-2 rounded-md px-1.5 py-1.5 text-start outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center">
          <StatusIcon state={state} />
          <span className="sr-only">{stateLabel}</span>
        </span>
        <span className="block min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-[13.5px] font-medium text-foreground/90">
              {card.member ? (
                // A Collaboration Mode teammate: its face instead of "Subagent".
                <TeamBotAvatar avatar={card.member.avatar} color={card.member.color} size={18} active={state === "running"} />
              ) : (
                <span className="text-muted-foreground/75">
                  {t("thread.subagents.prefix", { defaultValue: "Subagent" })}
                  {" · "}
                </span>
              )}
              <span className="truncate">{card.label}</span>
            </span>
            {meta ? (
              <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground/75">
                {meta}
              </span>
            ) : null}
          </span>
          <span
            className={cn(
              "mt-0.5 block truncate text-[13px]",
              failed && !cancelled
                ? "text-red-600/90 dark:text-red-400/90"
                : "text-muted-foreground/85",
            )}
          >
            {statusLine}
          </span>
        </span>
        <ChevronRight
          className={cn(
            "rtl:[&:not(.rotate-90)]:rotate-180 mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/60 transition-transform duration-150",
            expanded && "rotate-90",
          )}
          aria-hidden
        />
      </button>
      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div
            id={detailsId}
            key="details"
            role="region"
            aria-label={card.label}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: [0.2, 0, 0, 1] }}
            className="overflow-hidden"
          >
            <SubagentDetails
              card={card}
              elapsed={elapsed}
              failed={failed}
              cancelled={cancelled}
            />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
});

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-[12px] text-muted-foreground/75">{label}</dt>
      <dd className="min-w-0 truncate text-[13px] tabular-nums text-foreground/85">
        {children}
      </dd>
    </>
  );
}

function SubagentDetails({
  card,
  elapsed,
  failed,
  cancelled,
}: {
  card: ParallelSubagentCard;
  elapsed: string;
  failed: boolean;
  cancelled: boolean;
}) {
  const { t } = useTranslation();
  const steps = card.steps ?? [];
  return (
    <div
      data-testid={`subagent-details-${card.taskId}`}
      className="space-y-2.5 pb-2.5 ps-[30px] pe-2 pt-0.5"
    >
      {card.taskDescription ? (
        <div>
          <p className="text-[12px] text-muted-foreground/75">
            {t("thread.subagents.detailsTask", { defaultValue: "Task" })}
          </p>
          <p className="mt-0.5 line-clamp-4 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-foreground/85">
            {card.taskDescription}
          </p>
        </div>
      ) : null}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        {card.model ? (
          <DetailRow label={t("thread.subagents.detailsModel", { defaultValue: "Model" })}>
            {card.model}
          </DetailRow>
        ) : null}
        {elapsed ? (
          <DetailRow label={t("thread.subagents.detailsElapsed", { defaultValue: "Elapsed" })}>
            {elapsed}
          </DetailRow>
        ) : null}
        {card.tokens ? (
          <DetailRow label={t("thread.subagents.detailsTokens", { defaultValue: "Tokens" })}>
            {formatTokens(card.tokens)}
          </DetailRow>
        ) : null}
        {card.iteration ? (
          <DetailRow
            label={t("thread.subagents.detailsIteration", { defaultValue: "Step" })}
          >
            {card.iteration}
          </DetailRow>
        ) : null}
        {failed ? (
          <DetailRow label={t("thread.subagents.detailsReason", { defaultValue: "Reason" })}>
            <span
              className={
                cancelled ? "text-foreground/85" : "text-red-600/90 dark:text-red-400/90"
              }
            >
              {subagentErrorReason(card, t)}
            </span>
          </DetailRow>
        ) : null}
      </dl>
      <div>
        <p className="text-[12px] text-muted-foreground/75">
          {t("thread.subagents.detailsSteps", { defaultValue: "Recent steps" })}
        </p>
        {steps.length ? (
          <ul className="mt-1 space-y-0.5">
            {steps.map((step, index) => (
              <li
                // Steps have no id; position is stable within one frame.
                key={`${index}-${step.kind}-${step.target}`}
                data-status={step.status}
                className="flex items-center gap-2 text-[13px] text-foreground/85"
              >
                <span className="grid h-4 w-4 shrink-0 place-items-center">
                  <StatusIcon state={step.status} size="sm" />
                </span>
                <span className="min-w-0 truncate">{subagentStepLabel(step, t)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[13px] text-muted-foreground/75">
            {t("thread.subagents.detailsNoSteps", { defaultValue: "No steps yet" })}
          </p>
        )}
      </div>
    </div>
  );
}
