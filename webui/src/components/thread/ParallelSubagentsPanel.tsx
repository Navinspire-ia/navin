// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { Check, Clock, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SubagentProgressUpdate } from "@/lib/navin-client";
import { cn } from "@/lib/utils";

export function isQueuedCard(card: Pick<SubagentProgressUpdate, "done" | "phase">): boolean {
  return !card.done && card.phase === "queued";
}

export type ParallelSubagentCard = SubagentProgressUpdate & {
  updatedAt: number;
  /** Wall clock start, kept across updates so the elapsed time never resets
   * and a replayed card does not claim to have just started. */
  startedAt: number;
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

/** Drop finished cards once they have been on screen long enough to read. */
export function pruneFinishedSubagentCards(
  cards: ParallelSubagentCard[],
  now: number = Date.now(),
): ParallelSubagentCard[] {
  return cards.filter(
    (card) => !card.done || now - card.updatedAt < FINISHED_CARD_LINGER_MS,
  );
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

/**
 * Cursor-style stacked cards for parallel background subagents.
 * Shown above the composer while one or more ``spawn`` tasks are in flight.
 */
export function ParallelSubagentsPanel({
  cards,
  className,
}: {
  cards: ParallelSubagentCard[];
  className?: string;
}) {
  const { t } = useTranslation();
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
      className={cn(
        "mt-1.5 w-full space-y-0.5",
        className,
      )}
    >
      {cards.map((card) => (
        <SubagentCard key={card.taskId} card={card} now={now} />
      ))}
      <p className="px-1 text-[11.5px] text-muted-foreground">{waiting}</p>
    </div>
  );
}

function SubagentCard({
  card,
  now,
}: {
  card: ParallelSubagentCard;
  now: number;
}) {
  const { t } = useTranslation();
  const failed = card.done && (card.phase === "error" || Boolean(card.error));
  const queued = isQueuedCard(card);
  // A finished card freezes on its own duration; a running one keeps counting.
  // A queued one has not started: showing a rising clock would look like work.
  const elapsed = queued
    ? ""
    : formatElapsed((card.done ? card.updatedAt : now) - card.startedAt);
  const meta = [card.model, elapsed].filter(Boolean).join(" · ");
  const statusLine =
    queued || card.statusLine === "Queued - waiting for a free slot"
      ? t("thread.subagents.queued", {
          defaultValue: "Queued - waiting for a free slot",
        })
      : card.statusLine === "Preparing isolated checkout…"
        ? t("thread.subagents.isolating", {
            defaultValue: "Preparing isolated checkout…",
          })
        : card.statusLine === "Working…"
        ? t("thread.subagents.working", { defaultValue: "Working…" })
        : card.statusLine;

  // Flat inline row, not a card: these live in the conversation flow.
  return (
    <div
      data-testid={`subagent-card-${card.taskId}`}
      data-phase={card.phase}
      className="flex items-start gap-2 rounded-md px-1 py-1 transition-colors hover:bg-muted/30"
      title={card.taskDescription || undefined}
    >
      <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center">
        {queued ? (
          <Clock
            className="h-3.5 w-3.5 text-muted-foreground/70"
            aria-hidden
          />
        ) : !card.done ? (
          <Loader2
            className="h-3.5 w-3.5 animate-spin text-muted-foreground/70"
            aria-hidden
          />
        ) : failed ? (
          <X className="h-3.5 w-3.5 text-muted-foreground/60" aria-hidden />
        ) : (
          <Check className="h-3.5 w-3.5 text-emerald-500/80" aria-hidden />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-foreground/85">
            <span className="text-muted-foreground/70">
              {t("thread.subagents.prefix", { defaultValue: "Subagent" })}
              {" · "}
            </span>
            {card.label}
          </p>
          {meta ? (
            <span className="shrink-0 text-[10.5px] tabular-nums text-muted-foreground/70">
              {meta}
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground/75">
          {statusLine}
        </p>
      </div>
    </div>
  );
}
