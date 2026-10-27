// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useMemo, useSyncExternalStore } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import {
  StatusIcon,
  SubagentActionButtons,
  subagentActionsFor,
  subagentCardState,
  subagentStatusLine,
  useSubagentActions,
  type CardState,
  type ParallelSubagentCard,
  type SubagentActionHandler,
  type SubagentCardStore,
} from "./ParallelSubagentsPanel";

/** Rows shown before "+N more". */
export const STATUS_BAR_ROWS = 3;

const ATTENTION_RANK: Record<CardState, number> = {
  error: 0,
  stopped: 0,
  approval: 1,
  running: 2,
  queued: 2,
  ok: 3,
};

/** Cards that need the user first; finished ok cards are only counted. */
export function subagentAttentionOrder(cards: ParallelSubagentCard[]): ParallelSubagentCard[] {
  return cards
    .map((card, index) => ({ card, index, rank: ATTENTION_RANK[subagentCardState(card)] }))
    .filter((entry) => entry.rank < 3)
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.card);
}

/**
 * One line above the composer that keeps every subagent in sight: counts,
 * and the failed, waiting or running ones with their actions.
 */
export function SubagentStatusBar({
  store,
  onAction,
  canManage = true,
  onOpenPanel,
  className,
}: {
  store: SubagentCardStore;
  onAction?: SubagentActionHandler;
  canManage?: boolean;
  onOpenPanel?: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const reduce = useReducedMotion();
  const { cards } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const attention = useMemo(() => subagentAttentionOrder(cards), [cards]);
  const counts = useMemo(() => {
    const c = { running: 0, queued: 0, approval: 0, failed: 0, done: 0 };
    for (const card of cards) {
      const state = subagentCardState(card);
      if (state === "running") c.running += 1;
      else if (state === "queued") c.queued += 1;
      else if (state === "approval") c.approval += 1;
      else if (state === "ok") c.done += 1;
      else c.failed += 1;
    }
    return c;
  }, [cards]);
  const focusCard = useCallback(
    (taskId: string) => {
      const expanded = new Set(store.getSnapshot().expanded);
      expanded.add(taskId);
      store.setExpanded(expanded);
      const target = `subagent-card-${taskId}`;
      const el = Array.from(document.querySelectorAll<HTMLElement>("[data-testid^='subagent-card-']")).find(
        (node) => node.dataset.testid === target,
      );
      if (el && typeof el.scrollIntoView === "function") {
        el.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      }
    },
    [reduce, store],
  );
  if (cards.length === 0) return null;
  const finished = counts.failed + counts.done;
  const chips: Array<[string, string, string]> = [];
  if (counts.running) chips.push(["running", t("thread.subagents.chipRunning", { defaultValue: "{{count}} running", count: counts.running }), "text-muted-foreground"]);
  if (counts.queued) chips.push(["queued", t("thread.subagents.chipQueued", { defaultValue: "{{count}} queued", count: counts.queued }), "text-muted-foreground"]);
  if (counts.approval) chips.push(["approval", t("thread.subagents.chipApproval", { defaultValue: "{{count}} awaiting approval", count: counts.approval }), "text-amber-600 dark:text-amber-400"]);
  if (counts.failed) chips.push(["failed", t("thread.subagents.chipFailed", { defaultValue: "{{count}} failed", count: counts.failed }), "text-red-600 dark:text-red-400"]);
  if (counts.done) chips.push(["done", t("thread.subagents.chipDone", { defaultValue: "{{count}} done", count: counts.done }), "text-emerald-600 dark:text-emerald-400"]);
  const rows = attention.slice(0, STATUS_BAR_ROWS);
  const more = attention.length - rows.length;

  return (
    <div
      data-testid="subagent-status-bar"
      role="status"
      aria-live="polite"
      className={cn("mb-1.5 rounded-lg border border-border/60 bg-background/80 px-2 py-1 text-[12px]", className)}
    >
      <div className="flex min-h-6 items-center gap-2">
        <Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="font-medium text-foreground/90">
          {t("thread.subagents.barTitle", { defaultValue: "Subagents" })}
        </span>
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
          {chips.map(([key, label, tone]) => (
            <span key={key} data-chip={key} className={cn("rounded-full bg-muted/60 px-1.5 tabular-nums", tone)}>
              {label}
            </span>
          ))}
        </span>
        {finished > 0 ? (
          <button
            type="button"
            data-testid="subagent-status-dismiss-finished"
            onClick={() => store.dismissFinished()}
            className="inline-flex h-6 shrink-0 items-center rounded-md px-1.5 font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            {t("thread.subagents.dismissFinished", { defaultValue: "Dismiss finished" })}
          </button>
        ) : null}
      </div>
      <AnimatePresence initial={false}>
        {rows.map((card) => (
          <motion.div
            key={card.taskId}
            layout={!reduce}
            initial={reduce ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
            transition={{ duration: reduce ? 0 : 0.16, ease: [0.2, 0, 0, 1] }}
            className="overflow-hidden"
          >
            <StatusBarRow card={card} onAction={onAction} canManage={canManage} onFocus={focusCard} />
          </motion.div>
        ))}
      </AnimatePresence>
      {more > 0 ? (
        <button
          type="button"
          data-testid="subagent-status-more"
          data-hidden-count={more}
          onClick={onOpenPanel}
          className="inline-flex h-6 items-center rounded-md px-1.5 font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          {t("thread.subagents.moreCount", { defaultValue: "+{{count}} more", count: more })}
        </button>
      ) : null}
    </div>
  );
}

function StatusBarRow({
  card,
  onAction,
  canManage,
  onFocus,
}: {
  card: ParallelSubagentCard;
  onAction?: SubagentActionHandler;
  canManage: boolean;
  onFocus: (taskId: string) => void;
}) {
  const { t } = useTranslation();
  const state = subagentCardState(card);
  const actions = onAction ? subagentActionsFor(state, canManage) : [];
  const { busy, error, run } = useSubagentActions(card, onAction);
  return (
    <div data-testid={`subagent-status-row-${card.taskId}`} data-state={state}>
      <div className="flex min-h-6 items-center gap-2">
        <span className="grid h-4 w-4 shrink-0 place-items-center">
          <StatusIcon state={state} size="sm" />
        </span>
        <button
          type="button"
          onClick={() => onFocus(card.taskId)}
          className="min-w-0 max-w-[40%] shrink truncate rounded text-start font-medium text-foreground/90 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          {card.label}
        </button>
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            state === "error" ? "text-red-600/90 dark:text-red-400/90" : "text-muted-foreground",
          )}
        >
          {subagentStatusLine(card, t)}
          {state === "approval"
            ? ` · ${t("thread.subagents.answerInChat", { defaultValue: "Answer the request in the chat" })}`
            : ""}
        </span>
        <SubagentActionButtons card={card} actions={actions} busy={busy} onRun={(a) => void run(a)} />
      </div>
      {error ? (
        <p role="alert" className="ps-6 text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}
