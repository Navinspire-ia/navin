// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import i18n from "@/i18n";
import {
  ApiError,
  deleteSession as apiDeleteSession,
  fetchSessionAutomations,
  fetchWebuiThread,
  listSessions,
} from "@/lib/api";
import { hasPendingAgentActivity } from "@/lib/activity-timeline";
import { deriveTitle } from "@/lib/format";
import { isListedSidebarSession } from "@/lib/project-session";
import type {
  ChatSummary,
  SessionAutomationJob,
  SessionDeleteResult,
  UIMessage,
  WebuiThreadPersistedPayload,
  WorkspaceScopePayload,
} from "@/lib/types";

const EMPTY_MESSAGES: UIMessage[] = [];
const INITIAL_HISTORY_PAGE_LIMIT = 160;
/** Waits between retries of a conversation load (slow or restarting engine). */
export const THREAD_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000];

/**
 * Load a conversation, retrying while the engine is slow or restarting.
 * A 404 or 4xx is final; timeouts, network errors and 5xx are retried.
 */
export async function fetchThreadWithRetry<T>(
  load: () => Promise<T>,
  isCancelled: () => boolean = () => false,
  delays: readonly number[] = THREAD_RETRY_DELAYS_MS,
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await load();
    } catch (error) {
      const status = error instanceof ApiError ? error.status : 0;
      const transient = status === 0 || status >= 500 || status === 408 || status === 429;
      if (!transient || attempt >= delays.length || isCancelled()) throw error;
      await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
      if (isCancelled()) throw error;
    }
  }
}
const OLDER_HISTORY_PAGE_LIMIT = 120;
/** A jump from the prompt list loads at most this many older pages. */
const MAX_PROMPT_JUMP_PAGES = 50;
export const SESSION_UPDATE_DEBOUNCE_MS = 1200;
export const SESSION_UPDATE_MAX_WAIT_MS = 4000;

function sameSessionRow(a: ChatSummary, b: ChatSummary): boolean {
  return (
    a.key === b.key &&
    a.channel === b.channel &&
    a.chatId === b.chatId &&
    a.title === b.title &&
    a.preview === b.preview &&
    a.updatedAt === b.updatedAt &&
    a.createdAt === b.createdAt &&
    a.runStartedAt === b.runStartedAt &&
    JSON.stringify(a.workspaceScope ?? null) === JSON.stringify(b.workspaceScope ?? null)
  );
}

/**
 * Keep object identity for rows that did not change, and the previous array
 * when nothing changed at all, so a background refresh does not re-render
 * (and re-mount icons in) every sidebar row.
 */
export function reconcileSessionRows(
  prev: ChatSummary[],
  next: ChatSummary[],
): ChatSummary[] {
  const byKey = new Map(prev.map((row) => [row.key, row]));
  let changed = prev.length !== next.length;
  const merged = next.map((row, index) => {
    const old = byKey.get(row.key);
    const keep = old && sameSessionRow(old, row) ? old : row;
    if (keep !== prev[index]) changed = true;
    return keep;
  });
  return changed ? merged : prev;
}
const CHAT_CREATE_TIMEOUT_MS = 60_000;
const HISTORY_CACHE_MAX = 20;

interface CachedHistory {
  messages: UIMessage[];
  hasPendingToolCalls: boolean;
  forkBoundaryMessageCount: number | null;
  beforeCursor: string | null;
  hasMoreBefore: boolean;
  userMessageOffset: number;
}

/** Bounded LRU of the last loaded transcript per chat, so switching back renders instantly. */
const historyCache = new Map<string, CachedHistory>();

function readHistoryCache(key: string): CachedHistory | undefined {
  const hit = historyCache.get(key);
  if (hit) {
    historyCache.delete(key);
    historyCache.set(key, hit);
  }
  return hit;
}

function writeHistoryCache(key: string, entry: CachedHistory): void {
  historyCache.delete(key);
  historyCache.set(key, entry);
  while (historyCache.size > HISTORY_CACHE_MAX) {
    const oldest = historyCache.keys().next().value;
    if (oldest === undefined) break;
    historyCache.delete(oldest);
  }
}

/** Test helper: drop every cached transcript. */
export function clearSessionHistoryCache(): void {
  historyCache.clear();
}

function persistedMessagesToUi(messages: UIMessage[]): UIMessage[] {
  return messages.map((m, idx) => ({
    ...m,
    id: m.id ?? `hist-${idx}`,
    createdAt: typeof m.createdAt === "number" ? m.createdAt : Date.now(),
  }));
}

function hasPendingToolCallsFromThread(
  body: Awaited<ReturnType<typeof fetchWebuiThread>>,
  messages: UIMessage[],
): boolean {
  if (typeof body?.has_pending_tool_calls === "boolean") {
    return body.has_pending_tool_calls;
  }
  return hasPendingAgentActivity(messages);
}

interface HistoryState {
  key: string | null;
  messages: UIMessage[];
  loading: boolean;
  loadingOlder: boolean;
  error: string | null;
  hasPendingToolCalls: boolean;
  forkBoundaryMessageCount: number | null;
  beforeCursor: string | null;
  hasMoreBefore: boolean;
  userMessageOffset: number;
  version: number;
}

/** One older page put in front of what is loaded; an empty page ends the history. */
function prependOlderPage(
  prev: HistoryState,
  body: WebuiThreadPersistedPayload | null,
): HistoryState {
  if (!body?.messages?.length) {
    return {
      ...prev,
      loadingOlder: false,
      hasMoreBefore: false,
      beforeCursor: null,
    };
  }
  const older = persistedMessagesToUi(body.messages);
  const olderBoundary = typeof body.fork_boundary_message_count === "number"
    ? Math.max(0, Math.min(body.fork_boundary_message_count, older.length))
    : null;
  const shiftedBoundary = prev.forkBoundaryMessageCount === null
    ? null
    : prev.forkBoundaryMessageCount + older.length;
  const nextMessages = [...older, ...prev.messages];
  return {
    ...prev,
    messages: nextMessages,
    loadingOlder: false,
    error: null,
    hasPendingToolCalls: hasPendingAgentActivity(nextMessages),
    forkBoundaryMessageCount: olderBoundary ?? shiftedBoundary,
    beforeCursor: body.page?.before_cursor ?? null,
    hasMoreBefore: body.page?.has_more_before === true,
    userMessageOffset: Math.max(0, body.page?.user_message_offset ?? 0),
    version: prev.version + 1,
  };
}

/** Sidebar state: fetches the full session list and exposes create / delete actions. */
export function useSessions(): {
  sessions: ChatSummary[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  noteUserMessage: (chatId: string, text: string) => void;
  createChat: (workspaceScope?: WorkspaceScopePayload | null) => Promise<string>;
  forkChat: (
    sourceChatId: string,
    beforeUserIndex: number,
    title?: string,
    workspaceScope?: WorkspaceScopePayload | null,
  ) => Promise<string>;
  deleteChat: (
    key: string,
    options?: { deleteAutomations?: boolean },
  ) => Promise<SessionDeleteResult>;
  getSessionAutomations: (key: string) => Promise<SessionAutomationJob[]>;
} {
  const { client, token } = useClient();
  const [sessions, setSessions] = useState<ChatSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef(token);
  const optimisticKeysRef = useRef<Set<string>>(new Set());
  const creatingChatRef = useRef<Promise<string> | null>(null);
  tokenRef.current = token;

  const refreshInFlightRef = useRef<Promise<void> | null>(null);
  const refreshQueuedRef = useRef(false);

  const runRefresh = useCallback(async (background: boolean) => {
    try {
      if (!background) setLoading(true);
      const rows = (await listSessions(tokenRef.current)).filter(
        isListedSidebarSession,
      );
      const serverKeys = new Set(rows.map((row) => row.key));
      setSessions((prev) =>
        reconcileSessionRows(
          prev,
          [
            ...rows,
            ...prev.filter(
              (session) =>
                optimisticKeysRef.current.has(session.key) &&
                !serverKeys.has(session.key),
            ),
          ],
        ),
      );
      for (const key of Array.from(optimisticKeysRef.current)) {
        if (serverKeys.has(key)) optimisticKeysRef.current.delete(key);
      }
      setError((prev) => (prev === null ? prev : null));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError((prev) => (prev === message ? prev : message));
    } finally {
      if (!background) setLoading(false);
    }
  }, []);

  /**
   * Refresh the sidebar list. Concurrent calls coalesce: while one request is
   * in flight, further calls queue a single follow-up instead of stacking
   * parallel `/api/sessions` reads (each one a server thread) when many
   * sessions report updates at once.
   */
  const refresh = useCallback(
    async (options?: { background?: boolean }) => {
      const background = options?.background === true;
      if (refreshInFlightRef.current) {
        refreshQueuedRef.current = true;
        return refreshInFlightRef.current;
      }
      const run = (async () => {
        try {
          await runRefresh(background);
          while (refreshQueuedRef.current) {
            refreshQueuedRef.current = false;
            await runRefresh(true);
          }
        } finally {
          refreshInFlightRef.current = null;
        }
      })();
      refreshInFlightRef.current = run;
      return run;
    },
    [runRefresh],
  );

  /** Update sidebar / header title as soon as the user sends the first message. */
  const noteUserMessage = useCallback((chatId: string, text: string) => {
    const key = `websocket:${chatId}`;
    const preview = text.replace(/\s+/g, " ").trim();
    if (!preview) return;
    const title = deriveTitle(preview, "");
    setSessions((prev) => {
      const idx = prev.findIndex((session) => session.key === key);
      if (idx < 0) return prev;
      const current = prev[idx];
      if (current.title?.trim() || (current.preview?.trim() && !title)) {
        // Keep an existing generated title; still refresh preview if empty.
        if (current.preview?.trim()) return prev;
        const next = [...prev];
        next[idx] = {
          ...current,
          preview,
          updatedAt: new Date().toISOString(),
        };
        return next;
      }
      const next = [...prev];
      next[idx] = {
        ...current,
        preview,
        title: title || current.title,
        updatedAt: new Date().toISOString(),
      };
      return next;
    });
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    // Trailing debounce with a max wait: a pure debounce never fires while
    // many parallel sessions keep emitting updates, and an undebounced
    // listener would refetch the whole list on every event.
    let timer: ReturnType<typeof setTimeout> | null = null;
    let firstPendingAt: number | null = null;
    const fire = () => {
      timer = null;
      firstPendingAt = null;
      void refresh({ background: true });
    };
    const unsubscribe = client.onSessionUpdate(() => {
      const now = Date.now();
      if (firstPendingAt == null) firstPendingAt = now;
      if (timer != null) clearTimeout(timer);
      const waited = now - firstPendingAt;
      const delay = Math.max(
        0,
        Math.min(SESSION_UPDATE_DEBOUNCE_MS, SESSION_UPDATE_MAX_WAIT_MS - waited),
      );
      timer = setTimeout(fire, delay);
    });
    return () => {
      if (timer != null) clearTimeout(timer);
      unsubscribe();
    };
  }, [client, refresh]);

  const createChat = useCallback(async (workspaceScope?: WorkspaceScopePayload | null): Promise<string> => {
    if (creatingChatRef.current) return creatingChatRef.current;
    const run = (async () => {
      const chatId = await client.newChat(CHAT_CREATE_TIMEOUT_MS, workspaceScope);
      const key = `websocket:${chatId}`;
      optimisticKeysRef.current.add(key);
      // Optimistic insert; a subsequent refresh will replace it with the
      // authoritative row once the server persists the session.
      setSessions((prev) => [
        {
          key,
          channel: "websocket",
          chatId,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          title: "",
          preview: "",
          workspaceScope: workspaceScope ?? null,
        },
        ...prev.filter((s) => s.key !== key),
      ]);
      return chatId;
    })();
    creatingChatRef.current = run;
    try {
      return await run;
    } finally {
      creatingChatRef.current = null;
    }
  }, [client]);

  const forkChat = useCallback(async (
    sourceChatId: string,
    beforeUserIndex: number,
    title?: string,
    workspaceScope?: WorkspaceScopePayload | null,
  ): Promise<string> => {
    const chatId = await client.forkChat(
      sourceChatId,
      beforeUserIndex,
      title,
      CHAT_CREATE_TIMEOUT_MS,
    );
    const key = `websocket:${chatId}`;
    optimisticKeysRef.current.add(key);
    setSessions((prev) => [
      {
        key,
        channel: "websocket",
        chatId,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        title: title ?? "",
        preview: "",
        workspaceScope: workspaceScope ?? null,
      },
      ...prev.filter((s) => s.key !== key),
    ]);
    return chatId;
  }, [client]);

  const deleteChat = useCallback(
    async (key: string, options?: { deleteAutomations?: boolean }) => {
      const result = await apiDeleteSession(tokenRef.current, key, options);
      if (!result.deleted) return result;
      optimisticKeysRef.current.delete(key);
      setSessions((prev) => prev.filter((s) => s.key !== key));
      return result;
    },
    [],
  );

  const getSessionAutomations = useCallback(async (key: string) => {
    const result = await fetchSessionAutomations(tokenRef.current, key);
    return result.jobs;
  }, []);

  return {
    sessions,
    loading,
    error,
    refresh,
    noteUserMessage,
    createChat,
    forkChat,
    deleteChat,
    getSessionAutomations,
  };
}

/** Lazy-load a session's on-disk messages the first time the UI displays it. */
export function useSessionHistory(key: string | null): {
  messages: UIMessage[];
  loading: boolean;
  loadingOlder: boolean;
  error: string | null;
  refresh: () => void;
  loadOlder: () => Promise<void>;
  /**
   * Load older pages until the prompt at ``userIndex`` (counted over the
   * whole chat) is in, for a jump from the prompt list. Resolves false when
   * it could not be reached (load failed, chat switched or reloaded).
   */
  loadOlderUntil: (userIndex: number) => Promise<boolean>;
  hasMoreBefore: boolean;
  userMessageOffset: number;
  version: number;
  forkBoundaryMessageCount: number | null;
  /** ``true`` when the replayed transcript ends with a trace row (turn still in flight). */
  hasPendingToolCalls: boolean;
} {
  const { token } = useClient();
  const loadingOlderRef = useRef(false);
  const [refreshSeq, setRefreshSeq] = useState(0);
  const refresh = useCallback(() => {
    setRefreshSeq((value) => value + 1);
  }, []);
  const [state, setState] = useState<HistoryState>({
    key: null,
    messages: [],
    loading: false,
    loadingOlder: false,
    error: null,
    hasPendingToolCalls: false,
    forkBoundaryMessageCount: null,
    beforeCursor: null,
    hasMoreBefore: false,
    userMessageOffset: 0,
    version: 0,
  });

  useEffect(() => {
    if (!key) {
      setState({
        key: null,
        messages: [],
        loading: false,
        loadingOlder: false,
        error: null,
        hasPendingToolCalls: false,
        forkBoundaryMessageCount: null,
        beforeCursor: null,
        hasMoreBefore: false,
        userMessageOffset: 0,
        version: 0,
      });
      return;
    }
    let cancelled = false;
    const cached = readHistoryCache(key);
    // Mark the new key as loading immediately so callers never see stale
    // messages from the previous session during the render right after a switch.
    // A cached transcript renders at once; the fetch below refreshes it.
    setState((prev) => prev.key === key
      ? { ...prev, loading: true, loadingOlder: false, error: null }
      : cached
        ? {
            key,
            ...cached,
            loading: false,
            loadingOlder: false,
            error: null,
            version: 1,
          }
        : {
          key,
          messages: [],
          loading: true,
          loadingOlder: false,
          error: null,
          hasPendingToolCalls: false,
          forkBoundaryMessageCount: null,
          beforeCursor: null,
          hasMoreBefore: false,
          userMessageOffset: 0,
          version: 0,
        });
    (async () => {
      try {
        const body = await fetchThreadWithRetry(
          () => fetchWebuiThread(token, key, {
            limit: INITIAL_HISTORY_PAGE_LIMIT,
            direction: "latest",
          }),
          () => cancelled,
        );
        if (cancelled) return;
        if (!body?.messages?.length) {
          setState((prev) => ({
            key,
            messages: [],
            loading: false,
            loadingOlder: false,
            error: null,
            hasPendingToolCalls: false,
            forkBoundaryMessageCount: null,
            beforeCursor: null,
            hasMoreBefore: false,
            userMessageOffset: 0,
            version: prev.key === key ? prev.version + 1 : 1,
          }));
          return;
        }
        const ui = persistedMessagesToUi(body.messages);
        const hasPending = hasPendingToolCallsFromThread(body, ui);
        const forkBoundary = typeof body.fork_boundary_message_count === "number"
          ? Math.max(0, Math.min(body.fork_boundary_message_count, ui.length))
          : null;
        setState((prev) => ({
          key,
          messages: ui,
          loading: false,
          loadingOlder: false,
          error: null,
          hasPendingToolCalls: hasPending,
          forkBoundaryMessageCount: forkBoundary,
          beforeCursor: body.page?.before_cursor ?? null,
          hasMoreBefore: body.page?.has_more_before === true,
          userMessageOffset: Math.max(0, body.page?.user_message_offset ?? 0),
          version: prev.key === key ? prev.version + 1 : 1,
        }));
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setState((prev) => ({
            key,
            messages: [],
            loading: false,
            loadingOlder: false,
            error: null,
            hasPendingToolCalls: false,
            forkBoundaryMessageCount: null,
            beforeCursor: null,
            hasMoreBefore: false,
            userMessageOffset: 0,
            version: prev.key === key ? prev.version + 1 : 1,
          }));
        } else {
          // Keep what is already on screen (cache or previous load): a slow
          // or restarting engine must not blank the conversation.
          setState((prev) => prev.key === key && prev.messages.length > 0
            ? { ...prev, loading: false, loadingOlder: false }
            : {
            key,
            messages: [],
            loading: false,
            loadingOlder: false,
            error: (e as Error).message,
            hasPendingToolCalls: false,
            forkBoundaryMessageCount: null,
            beforeCursor: null,
            hasMoreBefore: false,
            userMessageOffset: 0,
            version: prev.key === key ? prev.version : 0,
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key, token, refreshSeq]);

  useEffect(() => {
    if (!state.key || state.loading || state.error) return;
    writeHistoryCache(state.key, {
      messages: state.messages,
      hasPendingToolCalls: state.hasPendingToolCalls,
      forkBoundaryMessageCount: state.forkBoundaryMessageCount,
      beforeCursor: state.beforeCursor,
      hasMoreBefore: state.hasMoreBefore,
      userMessageOffset: state.userMessageOffset,
    });
  }, [state]);

  const loadOlder = useCallback(async () => {
    if (!key || loadingOlderRef.current) return;
    const before = state.key === key ? state.beforeCursor : null;
    if (!before || !state.hasMoreBefore) return;
    loadingOlderRef.current = true;
    setState((prev) => prev.key === key ? { ...prev, loadingOlder: true, error: null } : prev);
    try {
      const body = await fetchWebuiThread(token, key, {
        limit: OLDER_HISTORY_PAGE_LIMIT,
        before,
      });
      setState((prev) => (prev.key === key ? prependOlderPage(prev, body) : prev));
    } catch (e) {
      setState((prev) => prev.key === key
        ? {
            ...prev,
            loadingOlder: false,
            error: (e as Error).message,
          }
        : prev);
    } finally {
      loadingOlderRef.current = false;
    }
  }, [
    key,
    state.beforeCursor,
    state.hasMoreBefore,
    state.key,
    token,
  ]);

  const stateRef = useRef(state);
  stateRef.current = state;
  const loadOlderUntil = useCallback(async (userIndex: number): Promise<boolean> => {
    if (!key || loadingOlderRef.current) return false;
    const start = stateRef.current;
    if (start.key !== key) return false;
    if (start.userMessageOffset <= userIndex) return true;
    if (!start.beforeCursor || !start.hasMoreBefore) return false;
    loadingOlderRef.current = true;
    setState((prev) => prev.key === key ? { ...prev, loadingOlder: true, error: null } : prev);
    const pages: Array<WebuiThreadPersistedPayload | null> = [];
    let before: string | null = start.beforeCursor;
    let offset = start.userMessageOffset;
    try {
      for (let page = 0; page < MAX_PROMPT_JUMP_PAGES && before && offset > userIndex; page += 1) {
        const body = await fetchWebuiThread(token, key, {
          limit: OLDER_HISTORY_PAGE_LIMIT,
          before,
        });
        pages.push(body);
        if (!body?.messages?.length) break;
        offset = Math.max(0, body.page?.user_message_offset ?? 0);
        before = body.page?.has_more_before === true ? body.page?.before_cursor ?? null : null;
      }
    } catch (e) {
      setState((prev) => prev.key === key
        ? { ...prev, loadingOlder: false, error: (e as Error).message }
        : prev);
      return false;
    } finally {
      loadingOlderRef.current = false;
    }
    // A reload or a switch meanwhile replaced the pages the cursor came from.
    const current = stateRef.current;
    if (current.key !== key || current.beforeCursor !== start.beforeCursor) {
      setState((prev) => prev.key === key ? { ...prev, loadingOlder: false } : prev);
      return false;
    }
    setState((prev) => {
      if (prev.key !== key || prev.beforeCursor !== start.beforeCursor) {
        return prev.key === key ? { ...prev, loadingOlder: false } : prev;
      }
      return pages.reduce<HistoryState>(prependOlderPage, prev);
    });
    return offset <= userIndex;
  }, [key, token]);

  if (!key) {
    return {
      messages: EMPTY_MESSAGES,
      loading: false,
      loadingOlder: false,
      error: null,
      refresh,
      loadOlder,
      loadOlderUntil,
      hasMoreBefore: false,
      userMessageOffset: 0,
      version: 0,
      forkBoundaryMessageCount: null,
      hasPendingToolCalls: false,
    };
  }

  // Even before the effect above commits its loading state, never surface the
  // previous session's payload for a brand-new key.
  if (state.key !== key) {
    const cachedNow = historyCache.get(key);
    if (cachedNow) {
      return {
        messages: cachedNow.messages,
        loading: false,
        loadingOlder: false,
        error: null,
        refresh,
        loadOlder,
        loadOlderUntil,
        hasMoreBefore: cachedNow.hasMoreBefore,
        userMessageOffset: cachedNow.userMessageOffset,
        version: 1,
        forkBoundaryMessageCount: cachedNow.forkBoundaryMessageCount,
        hasPendingToolCalls: cachedNow.hasPendingToolCalls,
      };
    }
    return {
      messages: EMPTY_MESSAGES,
      loading: true,
      loadingOlder: false,
      error: null,
      refresh,
      loadOlder,
      loadOlderUntil,
      hasMoreBefore: false,
      userMessageOffset: 0,
      version: 0,
      forkBoundaryMessageCount: null,
      hasPendingToolCalls: false,
    };
  }

  return {
    messages: state.messages,
    loading: state.loading,
    loadingOlder: state.loadingOlder,
    error: state.error,
    refresh,
    loadOlder,
    loadOlderUntil,
    hasMoreBefore: state.hasMoreBefore,
    userMessageOffset: state.userMessageOffset,
    version: state.version,
    forkBoundaryMessageCount: state.forkBoundaryMessageCount,
    hasPendingToolCalls: state.hasPendingToolCalls,
  };
}

/** Produce a compact display title for a session. */
export function sessionTitle(
  session: ChatSummary,
  firstUserMessage?: string,
): string {
  return deriveTitle(
    session.title || firstUserMessage || session.preview,
    i18n.t("chat.newChat"),
  );
}
