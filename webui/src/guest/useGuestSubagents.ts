// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { createSubagentCardStore, type SubagentCardStore } from "@/components/thread/ParallelSubagentsPanel";
import { useSubagentCardReconcile } from "@/hooks/useSubagentCardReconcile";
import type { NavinClient, SubagentRecord } from "@/lib/navin-client";

/** Wide enough to keep the subagents beside the chat without covering it. */
export const SUBAGENTS_SIDEBAR_QUERY = "(min-width: 1100px)";
/** Below this the panel covers the whole screen. */
const PANEL_BESIDE_QUERY = "(min-width: 768px)";
/** The relay may still be connecting when the page opens. */
const PROBE_ATTEMPTS = 10;
const PROBE_RETRY_MS = 2000;

function matches(query: string, fallback: boolean): boolean {
  return window.matchMedia?.(query).matches ?? fallback;
}

export interface GuestSubagents {
  store: SubagentCardStore;
  /** The journal already holds agents for this chat (some may have finished before the guest joined). */
  hasHistory: boolean;
  /** Live cards or a journal: the session has subagents to show. */
  exists: boolean;
  open: boolean;
  includeCompleted: boolean;
  /** The agent whose work fills the chat column, if any. */
  viewing: SubagentRecord | null;
  toggle: () => void;
  openHistory: () => void;
  close: () => void;
  select: (agent: SubagentRecord) => void;
  back: () => void;
}

/**
 * The subagents of the shared chat, for the guest page: their live cards,
 * the right sidebar that lists them as soon as there are any, and the agent
 * whose whole work is read in the chat column.
 */
export function useGuestSubagents(
  client: Pick<NavinClient, "querySubagents" | "onSubagentProgress">,
  chatId: string,
): GuestSubagents {
  const [store] = useState(createSubagentCardStore);
  const { cards } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [hasHistory, setHasHistory] = useState(false);
  const [open, setOpen] = useState(false);
  const [includeCompleted, setIncludeCompleted] = useState(false);
  const [viewing, setViewing] = useState<SubagentRecord | null>(null);
  const autoOpened = useRef(false);

  useEffect(() => {
    store.reset();
    setHasHistory(false);
    setOpen(false);
    setIncludeCompleted(false);
    setViewing(null);
    autoOpened.current = false;
  }, [chatId, store]);

  useEffect(() => client.onSubagentProgress((update) => {
    if (update.chatId && update.chatId !== chatId) return;
    store.upsert(update);
  }), [chatId, client, store]);

  useSubagentCardReconcile(client, chatId, store);

  // Agents that ran before the guest joined send no live frame: ask the journal.
  useEffect(() => {
    let live = true;
    let attempts = 0;
    let timer = 0;
    const probe = () => {
      client.querySubagents<unknown[]>(chatId, "list", { offset: 0, limit: 1 })
        .then((rows) => { if (live) setHasHistory(Array.isArray(rows) && rows.length > 0); })
        .catch(() => { if (live && ++attempts < PROBE_ATTEMPTS) timer = window.setTimeout(probe, PROBE_RETRY_MS); });
    };
    probe();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [chatId, client]);

  const active = cards.some((card) => !card.done);
  const exists = hasHistory || cards.length > 0;

  // Beside the chat as soon as the session has agents, once per chat: a
  // sidebar the guest closed stays closed. Phones keep the header button.
  useEffect(() => {
    if (!exists || autoOpened.current) return;
    autoOpened.current = true;
    if (!matches(SUBAGENTS_SIDEBAR_QUERY, false)) return;
    setIncludeCompleted(!active);
    setOpen(true);
  }, [exists, active]);

  const toggle = useCallback(() => {
    setIncludeCompleted(!store.getSnapshot().cards.some((card) => !card.done));
    setOpen((value) => !value);
  }, [store]);
  const openHistory = useCallback(() => {
    setIncludeCompleted(true);
    setOpen(true);
  }, []);
  const close = useCallback(() => {
    setOpen(false);
    setIncludeCompleted(false);
  }, []);
  const select = useCallback((agent: SubagentRecord) => {
    setViewing(agent);
    // A full-screen panel would hide the reading it just opened.
    if (!matches(PANEL_BESIDE_QUERY, true)) setOpen(false);
  }, []);
  const back = useCallback(() => setViewing(null), []);

  return { store, hasHistory, exists, open, includeCompleted, viewing, toggle, openHistory, close, select, back };
}
