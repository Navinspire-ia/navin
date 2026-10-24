// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Where a Multitask agent's work went.
 *
 * Each Multitask prompt works in its own copy of the project. When it
 * finishes, the engine merges its changes into the user's files if that is
 * safe and this card says so briefly. When it is not (a conflict, or the user
 * has unsaved edits on the same files), nothing is touched and the card asks:
 * Resolve (the agent merges them), Keep separate, or Discard.
 * Drop-in for the thread shell: `<WorktreeMergeCards chatId={chatId} onResolve={send} />`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, GitMerge, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  applyMergeEvent,
  mergeFiles,
  needsDecision,
  worktreeMergeFromEvent,
  type MergeAction,
  type WorktreeMergeCard as MergeCard,
} from "@/lib/worktree-merge";
import { useClient } from "@/providers/ClientProvider";

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };
const MERGED_VISIBLE_MS = 8000;
const FILES_SHOWN = 6;

interface CardProps {
  card: MergeCard;
  canManage: boolean;
  onAction: (card: MergeCard, action: MergeAction) => void;
}

export function WorktreeMergeCard({ card, canManage, onAction }: CardProps) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [busy, setBusy] = useState(false);
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    String(t(`thread.worktreeMerge.${key}`, { defaultValue: fallback, ...(values ?? {}) }));
  const files = mergeFiles(card);
  const count = files.length;

  useEffect(() => setBusy(false), [card.taskId, card.status]);

  const act = useCallback(
    (action: MergeAction) => {
      if (busy) return;
      setBusy(true);
      onAction(card, action);
    },
    [busy, card, onAction],
  );

  if (!needsDecision(card)) {
    return (
      <motion.div
        role="status"
        data-testid="worktree-merge-merged"
        initial={reduceMotion ? false : { opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        exit={reduceMotion ? undefined : { opacity: 0, y: 4 }}
        transition={spring}
        className="mb-2 flex min-w-0 items-center gap-2 rounded-xl border border-border/60 bg-card/90 px-3 py-2 text-[13px]"
      >
        <Check className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-300" aria-hidden />
        <span className="min-w-0 truncate text-foreground" dir="auto">
          {tx("merged", "Changes from {{label}} merged into your files: {{count}} file(s)", {
            label: card.label,
            count: card.files.length,
          })}
        </span>
      </motion.div>
    );
  }

  const why =
    card.reason === "uncommitted"
      ? tx("whyUncommitted", "{{count}} file(s) also have unsaved changes of yours", { count })
      : card.reason === "conflict"
        ? tx("whyConflict", "{{count}} file(s) conflict", { count })
        : card.reason === "in_progress"
          ? tx("whyInProgress", "Another merge is in progress in this project")
          : tx("whyUnknown", "The merge could not be checked safely");

  return (
    <motion.div
      role="alertdialog"
      aria-labelledby={`worktree-merge-${card.taskId}`}
      data-testid="worktree-merge-card"
      initial={reduceMotion ? false : { opacity: 0, y: 8, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={reduceMotion ? undefined : { opacity: 0, y: 4 }}
      transition={spring}
      className="mb-2 flex w-full min-w-0 max-w-full flex-col gap-2 overflow-hidden rounded-xl border border-amber-500/40 bg-card/95 px-3 py-2.5 text-[14px]"
    >
      <div className="flex min-w-0 items-start gap-2">
        <GitMerge className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
        <div className="min-w-0 flex-1">
          <p id={`worktree-merge-${card.taskId}`} className="text-[14px] font-medium text-foreground" dir="auto">
            {tx("title", "Changes from {{label}} could not be merged automatically", { label: card.label })}
          </p>
          <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground" dir="auto">
            {why}. {tx("untouched", "Nothing in your files was changed.")}
          </p>
          {count > 0 ? (
            <ul className="mt-1 flex flex-wrap gap-1" data-testid="worktree-merge-files">
              {files.slice(0, FILES_SHOWN).map((path) => (
                <li key={path}>
                  <code dir="ltr" className="rounded-md bg-muted/60 px-1.5 py-0.5 font-mono text-[12px] text-foreground/85">
                    {path}
                  </code>
                </li>
              ))}
              {count > FILES_SHOWN ? (
                <li className="text-[12px] text-muted-foreground">
                  {tx("more", "+{{count}} more", { count: count - FILES_SHOWN })}
                </li>
              ) : null}
            </ul>
          ) : null}
        </div>
      </div>
      {canManage ? (
        <div className={cn("flex flex-wrap items-center gap-1.5", busy && "pointer-events-none opacity-60")}>
          <Button type="button" size="sm" className="h-8 rounded-full text-[12.5px]" disabled={busy}
            onClick={() => act("resolve")} data-testid="worktree-merge-resolve">
            <GitMerge className="me-1 h-3.5 w-3.5" aria-hidden />
            {tx("resolve", "Resolve")}
          </Button>
          <Button type="button" variant="outline" size="sm" className="h-8 rounded-full text-[12.5px]" disabled={busy}
            onClick={() => act("keep")} data-testid="worktree-merge-keep">
            {tx("keep", "Keep separate")}
          </Button>
          <Button type="button" variant="ghost" size="sm" className="h-8 rounded-full text-[12.5px] text-muted-foreground" disabled={busy}
            onClick={() => act("discard")} data-testid="worktree-merge-discard">
            <X className="me-1 h-3.5 w-3.5" aria-hidden />
            {tx("discard", "Discard")}
          </Button>
        </div>
      ) : null}
    </motion.div>
  );
}

/** Listens on the chat for merge outcomes; at most a few cards, newest last. */
export function WorktreeMergeCards({
  chatId,
  canManage = true,
  onResolve,
  className,
}: {
  chatId: string | null | undefined;
  canManage?: boolean;
  onResolve?: (prompt: string) => void;
  className?: string;
}) {
  const { client } = useClient();
  const [cards, setCards] = useState<MergeCard[]>([]);
  const resolving = useRef(new Set<string>());
  const onResolveRef = useRef(onResolve);
  onResolveRef.current = onResolve;

  useEffect(() => {
    setCards([]);
    resolving.current.clear();
    if (!client || !chatId) return;
    return client.onChat(chatId, (ev) => {
      const card = worktreeMergeFromEvent(ev);
      if (!card) return;
      // Only the window that clicked Resolve sends the message to the agent.
      if (card.status === "resolving" && resolving.current.delete(card.taskId) && card.resolvePrompt) {
        onResolveRef.current?.(card.resolvePrompt);
      }
      if (card.status === "gone") resolving.current.delete(card.taskId);
      setCards((current) => applyMergeEvent(current, card));
    });
  }, [chatId, client]);

  // A "merged" note is information, not a question: it leaves on its own.
  const mergedKey = cards.filter((card) => !needsDecision(card)).map((card) => card.taskId).join(",");
  useEffect(() => {
    if (!mergedKey) return;
    const timer = window.setTimeout(
      () => setCards((current) => current.filter((card) => needsDecision(card))),
      MERGED_VISIBLE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [mergedKey]);

  const onAction = useCallback(
    (card: MergeCard, action: MergeAction) => {
      if (!chatId) return;
      if (action === "resolve") resolving.current.add(card.taskId);
      client.sendWorktreeMergeAction(chatId, card.taskId, action);
    },
    [chatId, client],
  );

  if (!cards.length) return null;
  return (
    <div className={cn("relative mx-auto w-full min-w-0 max-w-[49.5rem]", className)} data-testid="worktree-merge-cards">
      <AnimatePresence initial={false}>
        {cards.slice(-3).map((card) => (
          <WorktreeMergeCard key={card.taskId} card={card} canManage={canManage} onAction={onAction} />
        ))}
      </AnimatePresence>
    </div>
  );
}
