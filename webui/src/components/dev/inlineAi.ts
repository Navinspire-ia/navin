// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Inline AI for the code editor: ghost-text completion and a Cmd+K trigger.
 *
 * Ghost text is a decoration, never document content, so an unaccepted
 * suggestion can never be saved to disk or reach `onChange`. It is inserted only
 * when the user presses Tab, and any edit, cursor move or Escape discards it.
 */

import { StateEffect, StateField, type Extension } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  WidgetType,
  keymap,
  type ViewUpdate,
} from "@codemirror/view";

/** Context handed to the completion fetcher. */
export interface CompletionRequest {
  prefix: string;
  suffix: string;
}

export type CompletionFetcher = (
  request: CompletionRequest,
  signal: AbortSignal,
  /** Optional progressive ghost updates while the model streams. */
  onPartial?: (text: string) => void,
) => Promise<string>;

/** Adaptive idle band from the Phase 1 plan (80-120 ms). */
const IDLE_DELAY_MIN_MS = 80;
const IDLE_DELAY_MAX_MS = 120;
/** @deprecated alias - median of the adaptive band for tests/docs. */
const IDLE_DELAY_MS = IDLE_DELAY_MAX_MS;
const MAX_CONTEXT_CHARS = 8000;
/** Ignore suggestions this long: they are the model writing a file, not a hint. */
const MAX_SUGGESTION_CHARS = 1200;
/** Prefix/suffix cache so identical caret contexts skip a model round-trip. */
const COMPLETION_CACHE_MAX = 48;
const completionCache = new Map<string, string>();

function completionCacheKey(prefix: string, suffix: string): string {
  return `${prefix.slice(-240)}\0${suffix.slice(0, 120)}`;
}

function readCompletionCache(prefix: string, suffix: string): string | null {
  const key = completionCacheKey(prefix, suffix);
  const hit = completionCache.get(key);
  if (hit == null) return null;
  // Refresh LRU order.
  completionCache.delete(key);
  completionCache.set(key, hit);
  return hit;
}

function writeCompletionCache(prefix: string, suffix: string, text: string): void {
  const key = completionCacheKey(prefix, suffix);
  if (completionCache.has(key)) completionCache.delete(key);
  completionCache.set(key, text);
  while (completionCache.size > COMPLETION_CACHE_MAX) {
    const oldest = completionCache.keys().next().value;
    if (oldest === undefined) break;
    completionCache.delete(oldest);
  }
}

interface GhostState {
  text: string;
  from: number;
}

const setGhost = StateEffect.define<GhostState | null>();

class GhostWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }

  eq(other: GhostWidget) {
    return other.text === this.text;
  }

  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-ghost-text";
    span.textContent = this.text;
    // Suggested text is not real content, so keep it out of the a11y tree and
    // out of any copy of the document.
    span.setAttribute("aria-hidden", "true");
    return span;
  }

  ignoreEvent() {
    return false;
  }
}

const ghostField = StateField.define<GhostState | null>({
  create() {
    return null;
  },
  update(current, transaction) {
    for (const effect of transaction.effects) {
      if (effect.is(setGhost)) return effect.value;
    }
    // A suggestion is only valid for the state it was computed against.
    if (transaction.docChanged || transaction.selection) return null;
    return current;
  },
  provide: (field) =>
    EditorView.decorations.from(field, (state) => {
      if (!state || !state.text) return Decoration.none;
      return Decoration.set([
        Decoration.widget({
          widget: new GhostWidget(state.text),
          side: 1,
        }).range(state.from),
      ]);
    }),
});

export function currentGhostText(view: EditorView): string {
  return view.state.field(ghostField, false)?.text ?? "";
}

function acceptGhost(view: EditorView): boolean {
  const ghost = view.state.field(ghostField, false);
  if (!ghost || !ghost.text) return false;
  view.dispatch({
    changes: { from: ghost.from, insert: ghost.text },
    selection: { anchor: ghost.from + ghost.text.length },
    effects: setGhost.of(null),
    userEvent: "input.complete",
  });
  return true;
}

function dismissGhost(view: EditorView): boolean {
  if (!view.state.field(ghostField, false)) return false;
  view.dispatch({ effects: setGhost.of(null) });
  return true;
}

/** Accept the first line only, for stepping through a long suggestion. */
function acceptGhostLine(view: EditorView): boolean {
  const ghost = view.state.field(ghostField, false);
  if (!ghost || !ghost.text) return false;
  const newline = ghost.text.indexOf("\n");
  if (newline < 0) return acceptGhost(view);
  const head = ghost.text.slice(0, newline + 1);
  const rest = ghost.text.slice(newline + 1);
  view.dispatch({
    changes: { from: ghost.from, insert: head },
    selection: { anchor: ghost.from + head.length },
    effects: rest
      ? setGhost.of({ text: rest, from: ghost.from + head.length })
      : setGhost.of(null),
    userEvent: "input.complete",
  });
  return true;
}

/** Accept the next word / identifier of the suggestion (Cursor Tab-word feel). */
function acceptGhostWord(view: EditorView): boolean {
  const ghost = view.state.field(ghostField, false);
  if (!ghost || !ghost.text) return false;
  const match = /^(?:\s+|[A-Za-z0-9_]+|[^\sA-Za-z0-9_]+)/.exec(ghost.text);
  if (!match) return acceptGhost(view);
  const head = match[0];
  const rest = ghost.text.slice(head.length);
  view.dispatch({
    changes: { from: ghost.from, insert: head },
    selection: { anchor: ghost.from + head.length },
    effects: rest
      ? setGhost.of({ text: rest, from: ghost.from + head.length })
      : setGhost.of(null),
    userEvent: "input.complete",
  });
  return true;
}

const ghostTheme = EditorView.baseTheme({
  ".cm-ghost-text": {
    opacity: "0.55",
    color: "hsl(var(--muted-foreground))",
    fontStyle: "italic",
    whiteSpace: "pre",
    display: "inline-block",
    verticalAlign: "top",
    pointerEvents: "none",
  },
});

/**
 * Ghost-text completion driven by `fetch`.
 *
 * Requests are debounced and the in-flight one is aborted whenever the user
 * keeps typing, so a slow model never applies a stale suggestion.
 */
export function inlineCompletion(options: {
  fetch: CompletionFetcher;
  enabled?: () => boolean;
  onError?: (error: unknown) => void;
}): Extension {
  const plugin = ViewPlugin.fromClass(
    class {
      private timer: number | null = null;
      private controller: AbortController | null = null;
      /** Guards against re-requesting for a position already answered. */
      private lastRequested = -1;
      /** Wall time of the previous doc change - drives adaptive idle. */
      private lastEditAt = 0;

      constructor(readonly view: EditorView) {}

      update(update: ViewUpdate) {
        if (!update.docChanged && !update.selectionSet) return;
        // Our own insertion must not immediately request another suggestion.
        const isAccept = update.transactions.some((transaction) =>
          transaction.isUserEvent("input.complete"),
        );
        this.cancel();
        if (isAccept) return;
        if (!update.docChanged) return;
        this.schedule();
      }

      private idleDelayMs(): number {
        const now = Date.now();
        const gap = this.lastEditAt ? now - this.lastEditAt : IDLE_DELAY_MAX_MS;
        this.lastEditAt = now;
        // Rapid typing → fire sooner after the pause; slow typing → wait longer.
        return gap < 180 ? IDLE_DELAY_MIN_MS : IDLE_DELAY_MAX_MS;
      }

      private schedule() {
        const delay = this.idleDelayMs();
        this.timer = window.setTimeout(() => {
          this.timer = null;
          void this.run();
        }, delay);
      }

      private async run() {
        const view = this.view;
        if (options.enabled && !options.enabled()) return;
        const { state } = view;
        const selection = state.selection.main;
        // Only complete from a plain caret at the end of typed text.
        if (!selection.empty) return;
        const position = selection.head;
        if (position === this.lastRequested) return;

        const line = state.doc.lineAt(position);
        const beforeCaretOnLine = state.doc.sliceString(line.from, position);
        // Requesting mid-word or on a blank fresh line produces noise.
        if (!beforeCaretOnLine.trim()) return;

        const prefix = state.doc.sliceString(
          Math.max(0, position - MAX_CONTEXT_CHARS),
          position,
        );
        const suffix = state.doc.sliceString(
          position,
          Math.min(state.doc.length, position + MAX_CONTEXT_CHARS),
        );
        const cached = readCompletionCache(prefix, suffix);
        if (cached) {
          this.lastRequested = position;
          const suggestion = cached.replace(/\s+$/, "");
          if (suggestion && suggestion.length <= MAX_SUGGESTION_CHARS) {
            view.dispatch({
              effects: setGhost.of({ text: suggestion, from: position }),
            });
          }
          return;
        }

        const controller = new AbortController();
        this.controller = controller;
        this.lastRequested = position;
        try {
          const paint = (raw: string) => {
            if (controller.signal.aborted) return;
            if (view.state.selection.main.head !== position) return;
            const suggestion = raw.replace(/\s+$/, "");
            if (!suggestion || suggestion.length > MAX_SUGGESTION_CHARS) return;
            view.dispatch({
              effects: setGhost.of({ text: suggestion, from: position }),
            });
          };
          const text = await options.fetch(
            { prefix, suffix },
            controller.signal,
            paint,
          );
          if (controller.signal.aborted) return;
          paint(text);
          const trimmed = text.replace(/\s+$/, "");
          if (trimmed && trimmed.length <= MAX_SUGGESTION_CHARS) {
            writeCompletionCache(prefix, suffix, trimmed);
          }
        } catch (error) {
          if (!controller.signal.aborted) options.onError?.(error);
        } finally {
          if (this.controller === controller) this.controller = null;
        }
      }

      private cancel() {
        if (this.timer !== null) {
          window.clearTimeout(this.timer);
          this.timer = null;
        }
        this.controller?.abort();
        this.controller = null;
        this.lastRequested = -1;
      }

      destroy() {
        this.cancel();
      }
    },
  );

  return [
    ghostField,
    ghostTheme,
    plugin,
    // Must outrank the default Tab (indent) and Escape bindings, but only
    // claims the key when a suggestion is actually showing.
    keymap.of([
      // Phase 1 plan: Tab = word, Mod-→ = line, Mod-Enter = all.
      { key: "Tab", run: acceptGhostWord },
      { key: "Mod-ArrowRight", run: acceptGhostLine },
      { key: "Mod-Enter", run: acceptGhost },
      { key: "Escape", run: dismissGhost },
    ]),
  ];
}

/** Test helpers - keep accept/dismiss logic covered without mounting an editor. */
export const __inlineAiTest = {
  IDLE_DELAY_MS,
  IDLE_DELAY_MIN_MS,
  IDLE_DELAY_MAX_MS,
  MAX_CONTEXT_CHARS,
  MAX_SUGGESTION_CHARS,
  COMPLETION_CACHE_MAX,
  acceptGhost,
  acceptGhostLine,
  acceptGhostWord,
  dismissGhost,
  setGhost,
  ghostField,
  readCompletionCache,
  writeCompletionCache,
  adaptiveIdleDelayMs(gapMs: number): number {
    return gapMs < 180 ? IDLE_DELAY_MIN_MS : IDLE_DELAY_MAX_MS;
  },
  clearCompletionCache() {
    completionCache.clear();
  },
};

/** Selection captured when the user presses Cmd+K. */
export interface InlineEditTarget {
  from: number;
  to: number;
  selection: string;
  prefix: string;
  suffix: string;
  /** Viewport coordinates for positioning the prompt, when available. */
  top: number;
  left: number;
}

/**
 * Binds Cmd+K to `onTrigger`, handing it the current selection.
 *
 * When nothing is selected the whole current line is used, which matches what
 * users expect from "edit this bit" without forcing them to select first.
 */
export function inlineEditKeymap(
  onTrigger: (target: InlineEditTarget) => void,
): Extension {
  return keymap.of([
    {
      key: "Mod-k",
      preventDefault: true,
      run(view) {
        const { state } = view;
        const range = state.selection.main;
        let from = range.from;
        let to = range.to;
        if (range.empty) {
          const line = state.doc.lineAt(range.head);
          from = line.from;
          to = line.to;
        }
        const coords = view.coordsAtPos(from);
        onTrigger({
          from,
          to,
          selection: state.doc.sliceString(from, to),
          prefix: state.doc.sliceString(Math.max(0, from - MAX_CONTEXT_CHARS), from),
          suffix: state.doc.sliceString(
            to,
            Math.min(state.doc.length, to + MAX_CONTEXT_CHARS),
          ),
          top: coords?.bottom ?? 0,
          left: coords?.left ?? 0,
        });
        return true;
      },
    },
  ]);
}

/** Replace a range and select the result, so the edit is reviewable. */
export function applyInlineEdit(
  view: EditorView,
  target: { from: number; to: number },
  replacement: string,
): void {
  const clampedTo = Math.min(target.to, view.state.doc.length);
  const from = Math.min(target.from, clampedTo);
  view.dispatch({
    changes: { from, to: clampedTo, insert: replacement },
    selection: { anchor: from, head: from + replacement.length },
    userEvent: "input.aiEdit",
    scrollIntoView: true,
  });
  view.focus();
}
