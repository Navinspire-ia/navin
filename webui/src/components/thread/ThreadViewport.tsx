// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  forwardRef,
  type ReactNode,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ArrowDown, ChevronUp, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PromptRail } from "@/components/thread/PromptRail";
import { ThreadMessages } from "@/components/thread/ThreadMessages";
import { isAgentActivityMember } from "@/components/thread/AgentActivityCluster";
import type { SelectedModelRoute } from "@/components/thread/QuotaLimitCard";
import { Button } from "@/components/ui/button";
import {
  findPromptElement,
  jumpToPrompt,
  promptTop,
} from "@/components/thread/promptNavigation";
import { cn } from "@/lib/utils";
import type { CheckpointNotice, ContextCompactionNotice } from "@/hooks/useNavinStream";
import type { CliAppInfo, McpPresetInfo, SlashCommand, UIMessage } from "@/lib/types";

/**
 * Move an anchor from the full history into the rendered window.
 *
 * The transcript only renders its tail, so a count taken over every message
 * has to shift by however many are hidden. Null means no anchor - the block
 * trails the last message, which is what a live run wants. An anchor that has
 * scrolled out of the window lands on the first visible turn, never back at
 * the end: the point of anchoring is that it stops following the bottom.
 */
export function visibleAnchorMessageCount(
  anchor: number | null | undefined,
  hiddenMessageCount: number,
): number | null {
  if (anchor == null) return null;
  return Math.max(1, anchor - hiddenMessageCount);
}

/**
 * Whether a soft keyboard just took space and may therefore override the
 * "user is reading history" guard and pull the thread back to the bottom.
 *
 * Keyed on the measured inset, never on composer focus. Focus is the resting
 * state on desktop (the composer autofocuses and wheel scrolling does not move
 * focus), so keying on it meant every viewport scroll, window resize, focusin
 * and focusout yanked the reader back to the bottom a few frames after they
 * scrolled up, on idle chats as much as live ones.
 */
export function softKeyboardForcesScrollToBottom(
  keyboardInsetBottom: number,
  hasMessages: boolean,
): boolean {
  return hasMessages && keyboardInsetBottom > 0;
}

/**
 * Distance-to-bottom classification behind the reading guard: past this the
 * thread stops following new content until the reader comes back down.
 */
export function isNearBottom(
  scrollHeight: number,
  scrollTop: number,
  clientHeight: number,
): boolean {
  return scrollHeight - scrollTop - clientHeight < NEAR_BOTTOM_PX;
}

/**
 * Position a scroll of ours landed on, and when, so its echo can be ignored.
 * `bottom` marks a scroll-to-tail: its echo may arrive after the content has
 * grown again, which must read as "catch up", not "the reader left".
 */
export type ProgrammaticScrollMark = { top: number; at: number; bottom?: boolean } | null;

/**
 * Whether this scroll event is the echo of a jump we just performed.
 *
 * The echo must not be read as the user leaving the bottom, or the thread
 * would stop following the turn it was just asked to follow. The mark expires:
 * without a deadline a jump that never moved scrollTop left the mark behind,
 * and a genuine scroll minutes later that happened to land within tolerance
 * was silently swallowed.
 */
export function isProgrammaticScroll(
  mark: ProgrammaticScrollMark,
  scrollTop: number,
  now: number,
): boolean {
  if (mark === null) return false;
  if (now - mark.at > PROGRAMMATIC_SCROLL_TTL_MS) return false;
  return Math.abs(scrollTop - mark.top) < PROGRAMMATIC_SCROLL_TOLERANCE_PX;
}

/**
 * What a scroll event means for the follow-the-tail behaviour.
 *
 * - `user`: the reader moved the viewport; following continues only if they
 *   are still near the bottom.
 * - `echo`: the event is the trace of a jump we made; it says nothing about
 *   the reader's intent.
 * - `catch-up`: we scrolled to the tail, and by the time the event fired the
 *   thread had grown past the near-bottom band (a tool result, a burst of
 *   deltas). scrollTop never moved, so this is not the reader leaving: scroll
 *   to the tail again. Reading it as `user` is how following died mid-turn
 *   the moment a long tool result landed.
 */
export type ScrollEventMeaning = "user" | "echo" | "catch-up";

export function classifyScrollEvent(
  mark: ProgrammaticScrollMark,
  scrollTop: number,
  now: number,
  near: boolean,
): ScrollEventMeaning {
  if (!isProgrammaticScroll(mark, scrollTop, now)) return "user";
  if (mark?.bottom && !near) return "catch-up";
  return "echo";
}

export interface ThreadViewportHandle {
  jumpToUserPrompt: (promptId: string) => void;
  jumpToMessage: (messageId: string) => void;
  cancelAutoScroll: () => void;
}

interface ThreadViewportProps {
  messages: UIMessage[];
  isStreaming: boolean;
  assistantName?: string;
  selectedModelRoute?: SelectedModelRoute | null;
  liveTaskHint?: { modelLabel?: string; taskRole?: string; routed?: boolean };
  composer: ReactNode;
  /** Live blocks that belong to the conversation flow (plan, task progress,
   * subagents): rendered inline after the last message, Cursor-style, instead
   * of as panels stacked above the composer. */
  transcriptTail?: ReactNode;
  /** A block that lives inside the flow instead of after it: the plan card,
   *  which trails the last message while a run is live and then freezes into
   *  the turn that produced it. */
  pinnedBlock?: ReactNode;
  pinnedBlockAfterMessageCount?: number | null;
  emptyState?: ReactNode;
  scrollToBottomSignal?: number;
  scrollToLatestUserPromptSignal?: number;
  conversationKey?: string | null;
  showScrollToBottomButton?: boolean;
  cliApps?: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  slashCommands?: SlashCommand[];
  forkBoundaryMessageCount?: number | null;
  /** Latest automatic context compaction reported by the backend. */
  contextCompaction?: ContextCompactionNotice | null;
  /** Latest restore point snapshotted by the backend (one per user turn). */
  checkpointNotice?: CheckpointNotice | null;
  hasMoreBefore?: boolean;
  loadingOlder?: boolean;
  userMessageOffset?: number;
  onLoadOlder?: () => Promise<void> | void;
  onOpenFilePreview?: (path: string) => void;
  onForkFromMessage?: (beforeUserIndex: number) => void;
  onRevertResubmit?: (beforeUserIndex: number, content: string) => void | Promise<void>;
}

export const NEAR_BOTTOM_PX = 48;
const NEAR_TOP_PX = 96;
const PROGRAMMATIC_SCROLL_TOLERANCE_PX = 2;
const PROGRAMMATIC_SCROLL_TTL_MS = 250;
const DEFAULT_SCROLL_BUTTON_BOTTOM_PX = 192;
const SCROLL_BUTTON_COMPOSER_GAP_PX = 16;
const SOFT_KEYBOARD_MIN_INSET_PX = 80;
const KEYBOARD_SCROLL_FRAMES = 18;
export const INITIAL_HISTORY_WINDOW = 160;
export const HISTORY_WINDOW_INCREMENT = 120;

export function windowMessages(messages: UIMessage[], visibleCount: number): UIMessage[] {
  if (messages.length <= visibleCount) return messages;
  let start = Math.max(0, messages.length - visibleCount);
  while (
    start > 0
    && isAgentActivityMember(messages[start])
    && isAgentActivityMember(messages[start - 1])
  ) {
    start -= 1;
  }
  return messages.slice(start);
}

function isKeyboardEditableElement(element: Element | null): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false;
  if (element.isContentEditable) return true;
  if (element instanceof HTMLTextAreaElement) return true;
  if (!(element instanceof HTMLInputElement)) return false;
  return ![
    "button",
    "checkbox",
    "color",
    "file",
    "hidden",
    "image",
    "radio",
    "range",
    "reset",
    "submit",
  ].includes(element.type);
}

function readSoftKeyboardInsetBottom(container: HTMLElement | null): number {
  const viewport = window.visualViewport;
  if (!viewport) return 0;
  const active = document.activeElement;
  if (!isKeyboardEditableElement(active) || !container?.contains(active)) return 0;
  const layoutHeight = window.innerHeight || document.documentElement.clientHeight;
  const inset = layoutHeight - viewport.height - viewport.offsetTop;
  return inset >= SOFT_KEYBOARD_MIN_INSET_PX ? Math.ceil(inset) : 0;
}

export const ThreadViewport = forwardRef<ThreadViewportHandle, ThreadViewportProps>(function ThreadViewport({
  messages,
  isStreaming,
  assistantName,
  selectedModelRoute = null,
  liveTaskHint,
  composer,
  transcriptTail = null,
  pinnedBlock = null,
  pinnedBlockAfterMessageCount = null,
  emptyState,
  scrollToBottomSignal = 0,
  scrollToLatestUserPromptSignal = 0,
  conversationKey = null,
  showScrollToBottomButton = true,
  cliApps = [],
  mcpPresets = [],
  slashCommands = [],
  forkBoundaryMessageCount = null,
  contextCompaction = null,
  checkpointNotice = null,
  hasMoreBefore = false,
  loadingOlder = false,
  userMessageOffset = 0,
  onLoadOlder,
  onOpenFilePreview,
  onForkFromMessage,
  onRevertResubmit,
}, ref) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const composerDockRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const lastConversationKeyRef = useRef<string | null>(conversationKey);
  const pendingConversationScrollRef = useRef(true);
  const pendingPromptJumpRef = useRef<string | null>(null);
  const scrollFrameIdsRef = useRef<number[]>([]);
  const programmaticPromptScrollRef = useRef<ProgrammaticScrollMark>(null);
  const handledLatestPromptSignalRef = useRef(0);
  const activeTurnPromptRef = useRef<string | null>(null);
  const restoreScrollAfterPrependRef =
    useRef<{ height: number; top: number } | null>(null);
  /** User scrolled away from the bottom; do not auto-yank until they return or we reset (new chat / send). */
  const userReadingHistoryRef = useRef(false);
  const [atBottom, setAtBottom] = useState(true);
  const [composerDockHeight, setComposerDockHeight] = useState(0);
  const [keyboardInsetBottom, setKeyboardInsetBottom] = useState(0);
  const [visibleMessageCount, setVisibleMessageCount] =
    useState(INITIAL_HISTORY_WINDOW);
  const [revealPromptId, setRevealPromptId] = useState<string | null>(null);
  const hasMessages = messages.length > 0;
  const visibleMessages = useMemo(
    () => windowMessages(messages, visibleMessageCount),
    [messages, visibleMessageCount],
  );
  const hiddenMessageCount = messages.length - visibleMessages.length;
  const hiddenUserMessageCount =
    userMessageOffset
    + (hiddenMessageCount > 0
      ? messages.slice(0, hiddenMessageCount).filter((message) => message.role === "user").length
      : 0);
  const visibleForkBoundaryMessageCount =
    forkBoundaryMessageCount !== null && forkBoundaryMessageCount > hiddenMessageCount
      ? forkBoundaryMessageCount - hiddenMessageCount
      : null;
  // Keep the compact banner even when afterMessageCount is 0 (empty chat /
  // idle archive) or equals the visible length - previously `> hidden` hid
  // legitimate notices and made compaction look broken.
  const visiblePinnedBlockAfterMessageCount = visibleAnchorMessageCount(
    pinnedBlockAfterMessageCount,
    hiddenMessageCount,
  );
  const visibleContextCompaction =
    contextCompaction
      ? {
          ...contextCompaction,
          afterMessageCount: Math.max(
            0,
            contextCompaction.afterMessageCount - hiddenMessageCount,
          ),
        }
      : null;
  const visibleCheckpointNotice =
    checkpointNotice
      ? {
          ...checkpointNotice,
          afterMessageCount: Math.max(
            0,
            checkpointNotice.afterMessageCount - hiddenMessageCount,
          ),
        }
      : null;
  const scrollButtonBottom =
    keyboardInsetBottom
    + (composerDockHeight > 0
      ? composerDockHeight + SCROLL_BUTTON_COMPOSER_GAP_PX
      : DEFAULT_SCROLL_BUTTON_BOTTOM_PX);
  const scrollViewportStyle =
    keyboardInsetBottom > 0 ? { bottom: keyboardInsetBottom } : undefined;

  const cancelScheduledBottomScroll = useCallback(() => {
    for (const id of scrollFrameIdsRef.current) {
      window.cancelAnimationFrame(id);
    }
    scrollFrameIdsRef.current = [];
  }, []);

  const markProgrammaticPromptScroll = useCallback((top: number) => {
    programmaticPromptScrollRef.current = { top, at: Date.now() };
  }, []);

  const scrollToBottomNow = useCallback((smooth = false) => {
    const el = scrollRef.current;
    const marker = bottomRef.current;
    const behavior: ScrollBehavior = smooth ? "smooth" : "auto";
    if (el) {
      const top = Math.max(0, el.scrollHeight - el.clientHeight);
      // Leave a trace, or the scroll listener reads our own echo as the
      // reader scrolling away when the thread grows before the event fires.
      // A smooth scroll lands frames later; only an instant one has a known
      // landing position to recognise.
      if (!smooth) {
        programmaticPromptScrollRef.current = { top, at: Date.now(), bottom: true };
      }
      try {
        el.scrollTo?.({ top, behavior });
        if (!smooth) el.scrollTop = top;
      } catch {
        try {
          el.scrollTop = top;
        } catch {
          // Test DOMs can expose read-only scrollTop; browsers keep this writable.
        }
      }
    } else if (marker) {
      marker.scrollIntoView({ block: "end", behavior });
    }
    userReadingHistoryRef.current = false;
    setAtBottom(true);
  }, []);

  const scrollToPromptTopNow = useCallback((promptId: string) => {
    const el = scrollRef.current;
    if (!el) return false;
    const target = findPromptElement(el, promptId);
    if (!target) return false;
    const top = Math.max(0, promptTop(el, target) - 16);
    markProgrammaticPromptScroll(top);
    try {
      el.scrollTo?.({ top, behavior: "auto" });
      el.scrollTop = top;
    } catch {
      try {
        el.scrollTop = top;
      } catch {
        // Test DOMs can expose read-only scrollTop; browsers keep this writable.
      }
    }
    const near = el.scrollHeight - top - el.clientHeight < NEAR_BOTTOM_PX;
    userReadingHistoryRef.current = false;
    setAtBottom(near);
    return true;
  }, [markProgrammaticPromptScroll]);

  const scrollToBottom = useCallback(
    (smooth = false, frames = 1, options?: { force?: boolean }) => {
      const force = options?.force ?? false;
      cancelScheduledBottomScroll();
      const run = () => {
        if (!force && userReadingHistoryRef.current) return;
        scrollToBottomNow(smooth);
      };
      const scheduleNext = (remainingFrames: number) => {
        if (remainingFrames <= 0) return;
        const id = window.requestAnimationFrame(() => {
          scrollFrameIdsRef.current = scrollFrameIdsRef.current.filter((frameId) => frameId !== id);
          if (!force && userReadingHistoryRef.current) return;
          scrollToBottomNow(smooth);
          scheduleNext(remainingFrames - 1);
        });
        scrollFrameIdsRef.current.push(id);
      };
      run();
      scheduleNext(frames - 1);
    },
    [cancelScheduledBottomScroll, scrollToBottomNow],
  );

  const loadEarlierMessages = useCallback(() => {
    const el = scrollRef.current;
    if (el) {
      restoreScrollAfterPrependRef.current = {
        height: el.scrollHeight,
        top: el.scrollTop,
      };
    }
    userReadingHistoryRef.current = true;
    activeTurnPromptRef.current = null;
    setAtBottom(false);
    if (hiddenMessageCount > 0) {
      setVisibleMessageCount((count) =>
        Math.min(messages.length, count + HISTORY_WINDOW_INCREMENT),
      );
      return;
    }
    if (hasMoreBefore && onLoadOlder && !loadingOlder) {
      setVisibleMessageCount((count) => count + HISTORY_WINDOW_INCREMENT);
      void onLoadOlder();
    }
  }, [hasMoreBefore, hiddenMessageCount, loadingOlder, messages.length, onLoadOlder]);

  const maybeLoadEarlierFromScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !hasMessages || pendingConversationScrollRef.current) return;
    if (!userReadingHistoryRef.current) return;
    if (el.scrollTop > NEAR_TOP_PX) return;
    if (hiddenMessageCount <= 0 && !hasMoreBefore) return;
    loadEarlierMessages();
  }, [hasMessages, hasMoreBefore, hiddenMessageCount, loadEarlierMessages]);

  const jumpToUserPrompt = useCallback((promptId: string) => {
    setRevealPromptId(promptId);
    const scrollEl = scrollRef.current;
    if (scrollEl && findPromptElement(scrollEl, promptId)) {
      jumpToPrompt(scrollEl, promptId);
      return;
    }
    const index = messages.findIndex((message) => message.id === promptId);
    if (index < 0) return;
    pendingPromptJumpRef.current = promptId;
    userReadingHistoryRef.current = true;
    activeTurnPromptRef.current = null;
    setAtBottom(false);
    setVisibleMessageCount((count) => Math.max(count, messages.length - index));
  }, [messages]);

  useImperativeHandle(
    ref,
    () => ({
      jumpToUserPrompt,
      jumpToMessage: jumpToUserPrompt,
      cancelAutoScroll: cancelScheduledBottomScroll,
    }),
    [cancelScheduledBottomScroll, jumpToUserPrompt],
  );

  const measureComposerDock = useCallback(() => {
    const el = composerDockRef.current;
    if (!el) return;
    const height = el.getBoundingClientRect().height || el.offsetHeight;
    setComposerDockHeight((current) =>
      Math.abs(current - height) < 1 ? current : height,
    );
  }, []);

  useLayoutEffect(() => {
    const updateKeyboardInset = () => {
      const scrollEl = scrollRef.current;
      const next = readSoftKeyboardInsetBottom(scrollEl);
      setKeyboardInsetBottom((current) =>
        Math.abs(current - next) < 1 ? current : next,
      );
      if (softKeyboardForcesScrollToBottom(next, hasMessages)) {
        userReadingHistoryRef.current = false;
        scrollToBottom(false, KEYBOARD_SCROLL_FRAMES, { force: true });
      }
    };
    updateKeyboardInset();
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", updateKeyboardInset);
    viewport?.addEventListener("scroll", updateKeyboardInset);
    window.addEventListener("resize", updateKeyboardInset);
    document.addEventListener("focusin", updateKeyboardInset);
    document.addEventListener("focusout", updateKeyboardInset);
    return () => {
      viewport?.removeEventListener("resize", updateKeyboardInset);
      viewport?.removeEventListener("scroll", updateKeyboardInset);
      window.removeEventListener("resize", updateKeyboardInset);
      document.removeEventListener("focusin", updateKeyboardInset);
      document.removeEventListener("focusout", updateKeyboardInset);
    };
  }, [hasMessages, scrollToBottom]);

  useLayoutEffect(() => {
    if (keyboardInsetBottom > 0) {
      userReadingHistoryRef.current = false;
      scrollToBottom(false, KEYBOARD_SCROLL_FRAMES, { force: true });
      return;
    }
    if (userReadingHistoryRef.current) return;
    scrollToBottom(false, 4);
  }, [keyboardInsetBottom, scrollToBottom]);

  useEffect(() => {
    const scrollEl = scrollRef.current;
    if (!scrollEl) return;

    const onComposerFocus = () => {
      const active = document.activeElement;
      if (!hasMessages || !isKeyboardEditableElement(active) || !scrollEl.contains(active)) return;
      // Clicking into the composer to type is not a request to leave the
      // history you were reading, so this follows the guard instead of
      // overriding it. A soft keyboard opening still forces the scroll, from
      // the keyboardInsetBottom effect above.
      scrollToBottom(false, KEYBOARD_SCROLL_FRAMES);
    };

    document.addEventListener("focusin", onComposerFocus);
    return () => document.removeEventListener("focusin", onComposerFocus);
  }, [hasMessages, scrollToBottom]);

  useEffect(() => {
    if (scrollToBottomSignal <= 0) return;
    userReadingHistoryRef.current = false;
    scrollToBottom(false, 8);
  }, [scrollToBottomSignal, scrollToBottom]);

  useLayoutEffect(() => {
    if (scrollToLatestUserPromptSignal <= handledLatestPromptSignalRef.current) return;
    const latest = messages[messages.length - 1];
    if (!latest || latest.role !== "user") return;
    handledLatestPromptSignalRef.current = scrollToLatestUserPromptSignal;
    // Send must yank even if a previous zoom/layout glitch marked the user
    // as "reading history". Otherwise the new bubble stays off-screen and
    // Linux users have to scroll by hand - Windows never hits that path.
    userReadingHistoryRef.current = false;
    activeTurnPromptRef.current = latest.id;
    if (!scrollToPromptTopNow(latest.id)) {
      scrollToBottom(false, 8, { force: true });
      return;
    }
    scrollToBottom(false, 6, { force: true });
  }, [
    messages,
    scrollToBottom,
    scrollToLatestUserPromptSignal,
    scrollToPromptTopNow,
  ]);

  useLayoutEffect(() => {
    if (lastConversationKeyRef.current === conversationKey) return;
    lastConversationKeyRef.current = conversationKey;
    pendingConversationScrollRef.current = true;
    userReadingHistoryRef.current = false;
    activeTurnPromptRef.current = null;
    setAtBottom(true);
    setVisibleMessageCount(INITIAL_HISTORY_WINDOW);
  }, [conversationKey]);

  // Follow the tail for as long as the reader has not left it. Every delta,
  // tool result and late message goes through here, whoever started the turn:
  // a send from this window, a scheduled job, a board task, a reload
  // mid-stream. The only thing that stops it is the reader scrolling up
  // (userReadingHistoryRef, maintained by the scroll listener), and the only
  // thing that resumes it is the reader coming back down, or a send.
  useLayoutEffect(() => {
    if (!hasMessages || userReadingHistoryRef.current) return;
    if (activeTurnPromptRef.current) {
      const promptId = activeTurnPromptRef.current;
      const promptIndex = messages.findIndex((message) => message.id === promptId);
      if (promptIndex < 0) {
        activeTurnPromptRef.current = null;
      } else {
        // The prompt just sent sits pinned at the top until the agent has
        // produced something to read below it.
        const hasAgentOutput = messages
          .slice(promptIndex + 1)
          .some((message) => message.role !== "user");
        if (!hasAgentOutput) return;
      }
    }
    scrollToBottom(false, isStreaming ? 3 : 1);
  }, [hasMessages, isStreaming, messages, scrollToBottom]);

  useLayoutEffect(() => {
    const pending = restoreScrollAfterPrependRef.current;
    if (!pending) return;
    const el = scrollRef.current;
    restoreScrollAfterPrependRef.current = null;
    if (!el) return;
    const delta = el.scrollHeight - pending.height;
    const nextTop = pending.top + delta;
    try {
      el.scrollTop = nextTop;
    } catch {
      try {
        el.scrollTo?.({ top: nextTop, behavior: "auto" });
      } catch {
        // Test DOMs can expose read-only scrollTop; browsers keep this writable.
      }
    }
  }, [visibleMessages.length, messages.length]);

  useLayoutEffect(() => {
    const promptId = pendingPromptJumpRef.current;
    const scrollEl = scrollRef.current;
    if (!promptId || !scrollEl || !findPromptElement(scrollEl, promptId)) return;
    pendingPromptJumpRef.current = null;
    const frame = window.requestAnimationFrame(() => jumpToPrompt(scrollEl, promptId));
    return () => window.cancelAnimationFrame(frame);
  }, [revealPromptId, visibleMessages.length]);

  useLayoutEffect(() => {
    if (!pendingConversationScrollRef.current) return;
    if (!conversationKey) {
      pendingConversationScrollRef.current = false;
      scrollToBottom(false, 4);
      return;
    }
    scrollToBottom(false, 8);
    if (!hasMessages) return;
    pendingConversationScrollRef.current = false;
  }, [conversationKey, hasMessages, messages, scrollToBottom]);

  useLayoutEffect(() => {
    measureComposerDock();
  }, [composer, hasMessages, measureComposerDock]);

  useLayoutEffect(() => {
    if (!hasMessages || userReadingHistoryRef.current) return;
    const promptId = activeTurnPromptRef.current;
    if (promptId && scrollToPromptTopNow(promptId)) return;
    scrollToBottom(false, 2);
  }, [composerDockHeight, hasMessages, scrollToBottom, scrollToPromptTopNow]);

  // The thread grows without a `messages` change and without a scroll event:
  // markdown that finishes rendering a frame later, a tool card unfolding, an
  // image arriving, the live transcript tail. None of the effects above see
  // it, so the viewport sat 137px above the tail for over a second in a
  // measured stream. Size is the one signal every growth source shares.
  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (userReadingHistoryRef.current) return;
      const el = scrollRef.current;
      if (!el || isNearBottom(el.scrollHeight, el.scrollTop, el.clientHeight)) return;
      scrollToBottomNow(false);
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [hasMessages, scrollToBottomNow]);

  useEffect(() => cancelScheduledBottomScroll, [cancelScheduledBottomScroll]);

  useEffect(() => {
    const target = composerDockRef.current;
    if (!target || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => measureComposerDock());
    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMessages, measureComposerDock]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = (allowHistoryLoad = true) => {
      const near = isNearBottom(el.scrollHeight, el.scrollTop, el.clientHeight);
      const meaning = classifyScrollEvent(
        programmaticPromptScrollRef.current,
        el.scrollTop,
        Date.now(),
        near,
      );
      programmaticPromptScrollRef.current = null;
      if (meaning === "catch-up") {
        // Our scroll to the tail landed, then the thread grew under it.
        // The reader never moved: go to the tail again instead of deciding
        // they left it.
        if (!userReadingHistoryRef.current) scrollToBottomNow(false);
        return;
      }
      setAtBottom(near);
      if (meaning === "echo") {
        if (near) userReadingHistoryRef.current = false;
        return;
      }
      userReadingHistoryRef.current = !near;
      if (!near) activeTurnPromptRef.current = null;
      if (allowHistoryLoad && !near) maybeLoadEarlierFromScroll();
    };

    onScroll(false);
    const handleScroll = () => onScroll(true);
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [maybeLoadEarlierFromScroll, scrollToBottomNow]);

  return (
    <div className="thread-viewport relative flex min-h-0 flex-1 overflow-hidden">
      <div
        ref={scrollRef}
        className={cn(
          "thread-viewport-scrollbar absolute inset-0 overflow-y-auto scroll-auto scrollbar-thin",
          "[&::-webkit-scrollbar]:w-1.5",
          "[&::-webkit-scrollbar-thumb]:rounded-full",
          "[&::-webkit-scrollbar-thumb]:bg-muted-foreground/30",
          "[&::-webkit-scrollbar-track]:bg-transparent",
        )}
        style={scrollViewportStyle}
      >
        {hasMessages ? (
          <div ref={contentRef} className="mx-auto flex min-h-full w-full max-w-[64rem] flex-col">
            <div
              data-testid="thread-message-region"
              className="flex min-h-0 flex-1 flex-col justify-start px-3 pb-4 pt-4 sm:px-4"
            >
              <div className="mx-auto w-full max-w-[49.5rem]">
                {hiddenMessageCount > 0 || hasMoreBefore ? (
                  <div className="flex justify-center pb-3">
                    <button
                      type="button"
                      onClick={loadEarlierMessages}
                      disabled={loadingOlder}
                      className={cn(
                        "flex items-center gap-1.5 rounded-full border border-border/60",
                        "bg-background/90 px-3 py-1 text-[11px] text-muted-foreground",
                        "shadow-sm backdrop-blur transition-colors hover:text-foreground",
                        loadingOlder && "opacity-70",
                      )}
                    >
                      {loadingOlder ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                      ) : (
                        <ChevronUp className="h-3 w-3" aria-hidden />
                      )}
                      {loadingOlder
                        ? t("thread.loadingOlder", "Loading earlier messages...")
                        : hiddenMessageCount > 0
                          ? t("thread.showOlderCount", "Show earlier messages ({{count}})", {
                              count: hiddenMessageCount,
                            })
                          : t("thread.showOlder", "Show earlier messages")}
                    </button>
                  </div>
                ) : null}
                <ThreadMessages
                  messages={visibleMessages}
                  isStreaming={isStreaming}
                  assistantName={assistantName}
                  selectedModelRoute={selectedModelRoute}
                  liveTaskHint={liveTaskHint}
                  hiddenUserMessageCount={hiddenUserMessageCount}
                  cliApps={cliApps}
                  mcpPresets={mcpPresets}
                  slashCommands={slashCommands}
                  forkBoundaryMessageCount={visibleForkBoundaryMessageCount}
                  contextCompaction={visibleContextCompaction}
                  checkpointNotice={visibleCheckpointNotice}
                  pinnedBlock={pinnedBlock}
                  pinnedBlockAfterMessageCount={visiblePinnedBlockAfterMessageCount}
                  onOpenFilePreview={onOpenFilePreview}
                  onForkFromMessage={onForkFromMessage}
                  onRevertResubmit={onRevertResubmit}
                  scrollParentRef={scrollRef}
                  revealPromptId={revealPromptId}
                />
                {transcriptTail}
              </div>
            </div>

            <div
              ref={composerDockRef}
              data-testid="thread-composer-dock"
              className="sticky bottom-0 z-10 bg-background"
            >
              <div className="px-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:px-4">
                {composer}
              </div>
            </div>
          </div>
        ) : (
          <div
            ref={contentRef}
            className="mx-auto flex min-h-full w-full max-w-[72rem] flex-col px-3 pb-[env(safe-area-inset-bottom)] sm:px-4"
          >
            <div className="flex w-full flex-1 items-center justify-center py-6 sm:py-12">
              <div className="relative flex w-full max-w-[58rem] flex-col items-center gap-5 sm:block">
                <div className="flex justify-center sm:absolute sm:inset-x-0 sm:bottom-[calc(100%+1.5rem)]">
                  {emptyState}
                </div>
                <div className="w-full max-w-full min-w-0">{composer}</div>
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} aria-hidden className="h-px" />
      </div>

      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-background to-transparent"
      />

      {hasMessages ? (
        <PromptRail
          messages={visibleMessages}
          scrollRef={scrollRef}
          bottomOffset={scrollButtonBottom}
        />
      ) : null}

      {showScrollToBottomButton && !atBottom && (
        <div
          className="absolute left-1/2 z-20 -translate-x-1/2"
          style={{ bottom: scrollButtonBottom }}
        >
          <Button
            variant="outline"
            size="icon"
            onClick={() => scrollToBottom(true, 1, { force: true })}
            className={cn(
              "h-8 w-8 rounded-full shadow-md",
              "bg-background/90 backdrop-blur",
              "animate-in fade-in-0 zoom-in-95",
            )}
            aria-label={t("thread.scrollToBottom")}
          >
            <ArrowDown className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
});
