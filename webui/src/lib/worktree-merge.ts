// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Multitask merge cards: where a parallel agent's work went.
 *
 * Contract (websocket):
 *   in  `worktree_merge` {task_id, label, status, files[], conflicts[], reason, branch, resolve_prompt}
 *       status: merged | conflict | blocked | unknown (needs a decision)
 *               resolving | kept | discarded | gone (the card is over)
 *   out `worktree_merge_action` {chat_id, task_id, action: resolve|keep|discard}
 */

import type { InboundEvent } from "./types";

export type MergeAction = "resolve" | "keep" | "discard";

export interface WorktreeMergeCard {
  taskId: string;
  label: string;
  status: string;
  files: string[];
  conflicts: string[];
  reason: string;
  branch: string;
  resolvePrompt: string;
}

const ATTENTION = new Set(["conflict", "blocked", "unknown"]);
const CLOSED = new Set(["resolving", "kept", "discarded", "gone"]);

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function worktreeMergeFromEvent(ev: InboundEvent): WorktreeMergeCard | null {
  if (ev.event !== "worktree_merge" || typeof ev.task_id !== "string" || !ev.task_id) return null;
  return {
    taskId: ev.task_id,
    label: typeof ev.label === "string" && ev.label.trim() ? ev.label.trim() : ev.task_id,
    status: typeof ev.status === "string" ? ev.status : "",
    files: strings(ev.files),
    conflicts: strings(ev.conflicts),
    reason: typeof ev.reason === "string" ? ev.reason : "",
    branch: typeof ev.branch === "string" ? ev.branch : "",
    resolvePrompt: typeof ev.resolve_prompt === "string" ? ev.resolve_prompt : "",
  };
}

export function needsDecision(card: WorktreeMergeCard): boolean {
  return ATTENTION.has(card.status);
}

/** Apply one event to the visible cards: newest last, one per task. */
export function applyMergeEvent(cards: WorktreeMergeCard[], card: WorktreeMergeCard): WorktreeMergeCard[] {
  const rest = cards.filter((item) => item.taskId !== card.taskId);
  if (CLOSED.has(card.status)) return rest;
  if (card.status === "merged" || needsDecision(card)) return [...rest, card];
  return rest;
}

/** The files a card is about: the conflicting ones when there are any. */
export function mergeFiles(card: WorktreeMergeCard): string[] {
  return card.conflicts.length ? card.conflicts : card.files;
}
