import CodeMirror from "@uiw/react-codemirror";
import { vscodeDark, vscodeLight } from "@uiw/codemirror-theme-vscode";
import {
  autocompletion,
  completeAnyWord,
  type Completion,
  type CompletionContext,
  type CompletionResult,
  type CompletionSource,
} from "@codemirror/autocomplete";
import { highlightingFor, language as languageFacet } from "@codemirror/language";
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import {
  EditorSelection,
  EditorState,
  RangeSet,
  RangeSetBuilder,
  type Extension,
  type Text,
} from "@codemirror/state";
import {
  Decoration,
  EditorView,
  GutterMarker,
  WidgetType,
  gutter,
  hoverTooltip,
  keymap,
  lineNumberMarkers,
  lineNumberWidgetMarker,
  type DecorationSet,
} from "@codemirror/view";
import { highlightCode } from "@lezer/highlight";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  FileDiagnostic,
  GitBlameLine,
  ProjectSymbol,
  ReviewHunk,
} from "@/lib/types";
import type {
  LspCodeAction,
  LspCompletionItem,
  LspSignaturePayload,
} from "@/lib/api";
import { compactRelativeTime } from "@/lib/format";

import { isUsefulHoverText } from "./hoverInfo";
import { EDITOR_LINE_HEIGHT_PX, editorLineMetrics } from "./editorLineMetrics";
import {
  addedLineNumbers,
  computeReviewBlocks,
  removedBlockRowCount,
  removedGutterRows,
  type ReviewBlock,
} from "./reviewDiffModel";
import {
  applyInlineEdit,
  inlineCompletion,
  inlineEditKeymap,
  type CompletionFetcher,
  type InlineEditTarget,
} from "./inlineAi";
import { cachedLanguageSupport } from "./editorLanguage";
import { multiCursorBasicSetup, multiCursorExtensions } from "./multiCursor";
import {
  loadVimExtension,
  readVimModeEnabled,
  writeVimModeEnabled,
} from "./vimMode";
import {
  loadCmdkFileEdits,
  rememberCmdkFileEdit,
  type CmdkFileEdit,
} from "./cmdkFileHistory";
import {
  buildInlineEditHunks,
  composeInlineEdit,
  countChangedHunks,
  defaultAcceptedHunkIds,
  type InlineEditHunk,
} from "./inlineEditHunks";

const CMDK_HISTORY_KEY = "navin.dev.cmdkHistory";

class BreakpointMarker extends GutterMarker {
  toDOM() {
    const el = document.createElement("div");
    el.className = "cm-navin-breakpoint";
    el.textContent = "●";
    return el;
  }
}

const BREAKPOINT_MARKER = new BreakpointMarker();

export type RenamePreviewResult = {
  total: number;
  files: string[];
  message?: string;
  applied?: boolean;
};

function loadCmdkHistory(): string[] {
  try {
    const raw = localStorage.getItem(CMDK_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      .slice(0, 12);
  } catch {
    return [];
  }
}

function persistCmdkHistory(items: string[]): void {
  try {
    localStorage.setItem(CMDK_HISTORY_KEY, JSON.stringify(items.slice(0, 12)));
  } catch {
    /* ignore quota / private mode */
  }
}

export type EditorSymbolPos = {
  symbol: string;
  line: number;
  col: number;
};

function posToLineCol(doc: Text, pos: number): { line: number; col: number } {
  const line = doc.lineAt(pos);
  return { line: line.number, col: pos - line.from + 1 };
}

/**
 * Built once, deliberately.
 *
 * `basicSetup` is a dependency of the reconfigure effect inside
 * `useCodeMirror`, so handing it a fresh object on every render tears down and
 * rebuilds every gutter, view plugin and state field. That costs the undo
 * history and the fold state, and repaints the editor: the visible flicker.
 */
const BASIC_SETUP = {
  lineNumbers: true,
  foldGutter: true,
  highlightActiveLine: true,
  highlightSelectionMatches: true,
  ...multiCursorBasicSetup,
  // Configured explicitly in `extensions` so project symbols from the
  // code index participate alongside the language's own completions.
  autocompletion: false,
};

const MULTI_CURSOR_EXTENSIONS = multiCursorExtensions();

/**
 * Keeps the previous reference for as long as the contents are equal.
 *
 * The workbench polls git, review state and the debugger, and each poll hands
 * down freshly built arrays that usually say exactly what the last ones said.
 * Those new identities would rebuild the extension list and reconfigure the
 * editor, which costs the undo history and the fold state. Values here are
 * small - a handful of diagnostics, hunks or breakpoint lines - so comparing
 * them is far cheaper than the rebuild it avoids.
 */
function useContentStable<T>(value: T): T {
  const key = JSON.stringify(value ?? null);
  const held = useRef({ key, value });
  if (held.current.key !== key) held.current = { key, value };
  return held.current.value;
}

function docPosition(doc: Text, line: number, col: number): number {
  const lineNumber = Math.min(Math.max(line, 1), doc.lines);
  const info = doc.line(lineNumber);
  return Math.min(info.from + Math.max(col - 1, 0), info.to);
}

// ---------------------------------------------------------------------------
// Pending agent-edit review, drawn like a unified diff inside the editor:
// added lines green with a "+" and their new number, removed lines red with a
// "-" and their old number, unchanged lines untouched.
// ---------------------------------------------------------------------------

const REVIEW_MAX_CHARS = 400_000;
/** Removed lines drawn per block before folding the rest into a footer. */
const REVIEW_DELETED_MAX_LINES = 200;
/** Removed blocks larger than this are shown as plain text, not highlighted. */
const REVIEW_HIGHLIGHT_MAX_CHARS = 60_000;

/**
 * Removed text is not in the document, so the language parser runs on it
 * separately and the classes come from the same highlight style the editor
 * uses: the red lines read like the code around them, as in a diff view.
 */
function fillHighlightedRows(view: EditorView, lines: string[], rows: HTMLElement[]): void {
  const text = lines.join("\n");
  const lang = view.state.facet(languageFacet);
  let highlighted = false;
  if (lang && text.length <= REVIEW_HIGHLIGHT_MAX_CHARS) {
    try {
      let index = 0;
      highlightCode(
        text,
        lang.parser.parse(text),
        { style: (tags) => highlightingFor(view.state, tags) },
        (code, classes) => {
          const row = rows[index];
          if (!row) return;
          const span = document.createElement("span");
          if (classes) span.className = classes;
          span.textContent = code;
          row.appendChild(span);
        },
        () => {
          index += 1;
        },
      );
      highlighted = true;
    } catch {
      // A parser that chokes on a fragment just loses its colors.
    }
  }
  rows.forEach((row, i) => {
    if (!highlighted) row.textContent = lines[i] ?? "";
    if (!row.firstChild || row.textContent === "") row.textContent = "\u00a0";
  });
}

class DeletedLinesWidget extends WidgetType {
  constructor(
    readonly lines: string[],
    readonly oldStart: number,
  ) {
    super();
  }

  eq(other: DeletedLinesWidget) {
    return (
      other.oldStart === this.oldStart &&
      other.lines.length === this.lines.length &&
      other.lines.every((line, i) => line === this.lines[i])
    );
  }

  toDOM(view: EditorView) {
    const el = document.createElement("div");
    el.className = "cm-review-deleted";
    el.setAttribute("aria-hidden", "true");
    const shown = this.lines.slice(0, REVIEW_DELETED_MAX_LINES);
    const rows = shown.map(() => {
      const row = document.createElement("div");
      row.className = "cm-review-deleted-line";
      el.appendChild(row);
      return row;
    });
    fillHighlightedRows(view, shown, rows);
    if (this.lines.length > REVIEW_DELETED_MAX_LINES) {
      const more = document.createElement("div");
      more.className = "cm-review-deleted-line cm-review-deleted-more";
      more.textContent = `… +${this.lines.length - REVIEW_DELETED_MAX_LINES}`;
      el.appendChild(more);
    }
    return el;
  }

  get estimatedHeight() {
    return removedBlockRowCount(this.lines.length, REVIEW_DELETED_MAX_LINES) * EDITOR_LINE_HEIGHT_PX;
  }
}

/** Old line numbers and "-" signs, one row per line of the removed block. */
class DeletedGutterMarker extends GutterMarker {
  elementClass = "cm-review-gutter-deleted";

  constructor(readonly widget: DeletedLinesWidget) {
    super();
  }

  eq(other: DeletedGutterMarker) {
    return other.widget.eq(this.widget);
  }

  toDOM() {
    const el = document.createElement("div");
    el.className = "cm-review-gutter-deleted-rows";
    const { oldStart, lines } = this.widget;
    for (const row of removedGutterRows(oldStart, lines.length, REVIEW_DELETED_MAX_LINES)) {
      const line = document.createElement("div");
      line.className = "cm-review-gutter-deleted-line";
      line.textContent = String(row.number);
      const sign = document.createElement("span");
      sign.className = "cm-review-gutter-sign";
      sign.textContent = "-";
      line.appendChild(sign);
      el.appendChild(line);
    }
    if (this.widget.lines.length > REVIEW_DELETED_MAX_LINES) {
      const footer = document.createElement("div");
      footer.className = "cm-review-gutter-deleted-line";
      footer.textContent = "\u00a0";
      el.appendChild(footer);
    }
    return el;
  }
}

/**
 * Tints the number cell of an added line; the "+" is drawn by the theme. The
 * line-number gutter drops the number itself as soon as a marker brings DOM,
 * so this one deliberately has no `toDOM`.
 */
class AddedGutterMarker extends GutterMarker {
  elementClass = "cm-review-gutter-added";

  eq() {
    return true;
  }
}

const ADDED_GUTTER_MARKER = new AddedGutterMarker();

const addedLineDeco = Decoration.line({ class: "cm-review-added" });

// The diff is needed by the line decorations and by the gutter, and again on
// every caret move for the active-hunk bar. One computation per document.
let reviewBlocksMemo: { baseline: string; doc: Text; blocks: ReviewBlock[] } | null = null;

function reviewBlocksFor(baseline: string, doc: Text): ReviewBlock[] {
  if (reviewBlocksMemo && reviewBlocksMemo.baseline === baseline && reviewBlocksMemo.doc === doc) {
    return reviewBlocksMemo.blocks;
  }
  const current = doc.toString();
  const blocks =
    baseline.length > REVIEW_MAX_CHARS || current.length > REVIEW_MAX_CHARS
      ? []
      : computeReviewBlocks(baseline, current);
  reviewBlocksMemo = { baseline, doc, blocks };
  return blocks;
}

function buildAddedGutterMarkers(baseline: string, doc: Text): RangeSet<GutterMarker> {
  const builder = new RangeSetBuilder<GutterMarker>();
  for (const line of addedLineNumbers(reviewBlocksFor(baseline, doc))) {
    if (line > doc.lines) break;
    const from = doc.line(line).from;
    builder.add(from, from, ADDED_GUTTER_MARKER);
  }
  return builder.finish();
}

const reviewGutter: Extension = [
  lineNumberWidgetMarker.of((_view, widget) =>
    widget instanceof DeletedLinesWidget ? new DeletedGutterMarker(widget) : null,
  ),
];

export type HunkAction = (hunkId: string, action: "accept" | "reject") => void;

/** 0-based first/last line of a hunk in the current document (inclusive). */
function hunkLineSpan(hunk: ReviewHunk): { start: number; end: number } {
  const start = Math.max(0, hunk.new_start);
  const span = Math.max(hunk.new_count, 1);
  return { start, end: start + span - 1 };
}

/** Hunk containing the caret line, else the nearest hunk at or below it. */
function hunkNearLine(hunks: readonly ReviewHunk[], line0: number): ReviewHunk | null {
  if (!hunks.length) return null;
  const ordered = [...hunks].sort((a, b) => a.new_start - b.new_start);
  for (const hunk of ordered) {
    const { start, end } = hunkLineSpan(hunk);
    if (line0 >= start && line0 <= end) return hunk;
  }
  for (const hunk of ordered) {
    if (hunkLineSpan(hunk).start >= line0) return hunk;
  }
  return ordered[ordered.length - 1] ?? null;
}

function revealHunk(view: EditorView, hunk: ReviewHunk): void {
  const doc = view.state.doc;
  const lineNo = Math.min(Math.max(hunk.new_start + 1, 1), doc.lines);
  const info = doc.line(lineNo);
  view.dispatch({
    selection: EditorSelection.cursor(info.from),
    effects: EditorView.scrollIntoView(info.from, { y: "center" }),
  });
}

/** Next hunk strictly below the caret line; wraps to the first hunk. */
function nextHunkAfterLine(hunks: readonly ReviewHunk[], line0: number): ReviewHunk | null {
  if (!hunks.length) return null;
  const ordered = [...hunks].sort((a, b) => a.new_start - b.new_start);
  for (const hunk of ordered) {
    if (hunkLineSpan(hunk).start > line0) return hunk;
  }
  return ordered[0] ?? null;
}

/** Previous hunk strictly above the caret line; wraps to the last hunk. */
function prevHunkBeforeLine(hunks: readonly ReviewHunk[], line0: number): ReviewHunk | null {
  if (!hunks.length) return null;
  const ordered = [...hunks].sort((a, b) => a.new_start - b.new_start);
  for (let i = ordered.length - 1; i >= 0; i -= 1) {
    const hunk = ordered[i]!;
    if (hunkLineSpan(hunk).end < line0) return hunk;
  }
  return ordered[ordered.length - 1] ?? null;
}

class HunkToolbarWidget extends WidgetType {
  constructor(
    readonly hunk: ReviewHunk,
    readonly onAction: HunkAction,
    readonly labels: { accept: string; reject: string },
    readonly titles: { accept: string; reject: string },
    readonly active: boolean,
  ) {
    super();
  }

  eq(other: HunkToolbarWidget) {
    return (
      other.hunk.id === this.hunk.id &&
      other.active === this.active &&
      other.labels.accept === this.labels.accept &&
      other.labels.reject === this.labels.reject
    );
  }

  toDOM() {
    const el = document.createElement("div");
    el.className = this.active
      ? "cm-review-hunk-bar cm-review-hunk-bar-active"
      : "cm-review-hunk-bar";

    const stat = document.createElement("span");
    stat.className = "cm-review-hunk-stat";
    stat.textContent = `+${this.hunk.added} -${this.hunk.deleted}`;
    el.appendChild(stat);

    for (const kind of ["accept", "reject"] as const) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `cm-review-hunk-btn cm-review-hunk-${kind}`;
      button.textContent = this.labels[kind];
      button.title = this.titles[kind];
      // The editor claims mousedown to move the caret, which would blur the
      // button before its click ever fires.
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.onAction(this.hunk.id, kind);
      });
      el.appendChild(button);
    }
    return el;
  }

  ignoreEvent() {
    return true;
  }

  get estimatedHeight() {
    return 22;
  }
}

function buildReviewDecorations(
  baseline: string,
  doc: Text,
  hunkBars?: {
    hunks: ReviewHunk[];
    onAction: HunkAction;
    labels: { accept: string; reject: string };
    titles: { accept: string; reject: string };
    activeId: string | null;
  },
): DecorationSet {
  const blocks = reviewBlocksFor(baseline, doc);
  if (!blocks.length && !hunkBars?.hunks.length) return Decoration.none;

  type Entry = { pos: number; side: number; deco: Decoration };
  const entries: Entry[] = [];
  for (const block of blocks) {
    if (block.kind === "added") {
      for (let i = 0; i < block.count; i += 1) {
        const n = block.line + i;
        if (n <= doc.lines) {
          entries.push({ pos: doc.line(n).from, side: 0, deco: addedLineDeco });
        }
      }
    } else {
      const widget = Decoration.widget({
        widget: new DeletedLinesWidget(block.lines, block.oldStart),
        block: true,
        side: -10,
      });
      const pos = block.anchor <= doc.lines ? doc.line(block.anchor).from : doc.length;
      entries.push({ pos, side: -10, deco: widget });
    }
  }

  if (hunkBars) {
    for (const hunk of hunkBars.hunks) {
      // new_start is 0-based over the current document; CodeMirror lines are
      // 1-based. A pure deletion has new_count 0 and still needs a bar, anchored
      // to the line the removed text used to precede.
      const lineNo = Math.min(Math.max(hunk.new_start + 1, 1), doc.lines);
      const pos = doc.line(lineNo).from;
      entries.push({
        pos,
        // Behind the deleted-lines widget (-10) so the controls sit above the
        // whole hunk rather than between its removals and its additions.
        side: -20,
        deco: Decoration.widget({
          widget: new HunkToolbarWidget(
            hunk,
            hunkBars.onAction,
            hunkBars.labels,
            hunkBars.titles,
            hunk.id === hunkBars.activeId,
          ),
          block: true,
          side: -20,
        }),
      });
    }
  }

  entries.sort((a, b) => a.pos - b.pos || a.side - b.side);
  const builder = new RangeSetBuilder<Decoration>();
  for (const entry of entries) {
    builder.add(entry.pos, entry.pos, entry.deco);
  }
  return builder.finish();
}

const REVIEW_ROW_HEIGHT = `${EDITOR_LINE_HEIGHT_PX}px`;

const reviewTheme = EditorView.baseTheme({
  ".cm-review-added": {
    backgroundColor: "rgba(16, 185, 129, 0.14)",
  },
  "&dark .cm-review-added": {
    backgroundColor: "rgba(16, 185, 129, 0.17)",
  },
  ".cm-review-deleted": {
    backgroundColor: "rgba(244, 63, 94, 0.12)",
    fontFamily: "inherit",
    whiteSpace: "pre",
    overflow: "hidden",
  },
  "&dark .cm-review-deleted": {
    backgroundColor: "rgba(244, 63, 94, 0.17)",
  },
  ".cm-review-deleted-line": {
    // Same box as `.cm-line` so the red rows line up with the code and with
    // their gutter numbers.
    padding: "0 4px 0 8px",
    lineHeight: REVIEW_ROW_HEIGHT,
    minHeight: REVIEW_ROW_HEIGHT,
  },
  ".cm-review-deleted-more": {
    fontStyle: "italic",
    opacity: "0.6",
  },
  // Line numbers grow a right-hand slot for the "+" / "-" signs, so numbers
  // stay right-aligned in one column whether or not a line changed.
  ".cm-lineNumbers .cm-gutterElement": {
    position: "relative",
    paddingRight: "16px",
  },
  ".cm-review-gutter-sign": {
    position: "absolute",
    right: "4px",
    top: "0",
    fontWeight: "600",
  },
  ".cm-lineNumbers .cm-gutterElement.cm-review-gutter-added": {
    backgroundColor: "rgba(16, 185, 129, 0.28)",
    color: "rgb(4, 120, 87)",
  },
  ".cm-lineNumbers .cm-gutterElement.cm-review-gutter-added::after": {
    content: '"+"',
    position: "absolute",
    right: "4px",
    top: "0",
    fontWeight: "600",
  },
  "&dark .cm-lineNumbers .cm-gutterElement.cm-review-gutter-added": {
    color: "rgb(110, 231, 183)",
  },
  ".cm-lineNumbers .cm-gutterElement.cm-review-gutter-deleted": {
    padding: "0",
    backgroundColor: "rgba(244, 63, 94, 0.26)",
    color: "rgb(190, 18, 60)",
  },
  "&dark .cm-lineNumbers .cm-gutterElement.cm-review-gutter-deleted": {
    color: "rgb(253, 164, 175)",
  },
  ".cm-review-gutter-deleted-line": {
    position: "relative",
    padding: "0 16px 0 5px",
    textAlign: "right",
    lineHeight: REVIEW_ROW_HEIGHT,
    minHeight: REVIEW_ROW_HEIGHT,
    whiteSpace: "nowrap",
  },
  ".cm-review-hunk-bar": {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    padding: "1px 6px",
    fontFamily: "system-ui, sans-serif",
    fontSize: "10.5px",
    lineHeight: "18px",
    // The bar belongs to the hunk below it, so it reads as a header rather than
    // a floating control detached from what it acts on.
    borderTop: "1px solid rgba(120, 120, 120, 0.22)",
    backgroundColor: "rgba(120, 120, 120, 0.07)",
    userSelect: "none",
  },
  ".cm-review-hunk-bar-active": {
    borderTopColor: "rgba(59, 130, 246, 0.55)",
    backgroundColor: "rgba(59, 130, 246, 0.12)",
  },
  ".cm-review-hunk-stat": {
    opacity: "0.65",
    fontVariantNumeric: "tabular-nums",
  },
  ".cm-review-hunk-btn": {
    cursor: "pointer",
    border: "1px solid rgba(120, 120, 120, 0.35)",
    borderRadius: "4px",
    padding: "0 6px",
    background: "transparent",
    color: "inherit",
    font: "inherit",
  },
  ".cm-review-hunk-accept:hover": {
    backgroundColor: "rgba(16, 185, 129, 0.18)",
    borderColor: "rgba(16, 185, 129, 0.55)",
  },
  ".cm-review-hunk-reject:hover": {
    backgroundColor: "rgba(239, 68, 68, 0.18)",
    borderColor: "rgba(239, 68, 68, 0.55)",
  },
});

// ---------------------------------------------------------------------------
// Completion: project symbols from the code index, merged with the language's
// own completions and the words already present in the buffer.
// ---------------------------------------------------------------------------

/** Maps index symbol kinds onto CodeMirror's completion icon vocabulary. */
const COMPLETION_TYPES: Record<string, string> = {
  function: "function",
  method: "method",
  class: "class",
  component: "class",
  interface: "interface",
  type: "type",
  enum: "enum",
  struct: "class",
  trait: "interface",
  protocol: "interface",
  record: "class",
  constant: "constant",
  variable: "variable",
  module: "namespace",
  namespace: "namespace",
  object: "variable",
};

const SYMBOL_QUERY_MIN_CHARS = 1;
const SYMBOL_COMPLETION_LIMIT = 60;

function toCompletions(symbols: ProjectSymbol[]): Completion[] {
  const seen = new Set<string>();
  const out: Completion[] = [];
  for (const symbol of symbols) {
    const key = `${symbol.name}:${symbol.kind}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      label: symbol.name,
      type: COMPLETION_TYPES[symbol.kind] ?? "text",
      detail: symbol.kind,
      info: symbol.detail
        ? `${symbol.detail}\n\n${symbol.path}:${symbol.line}`
        : `${symbol.path}:${symbol.line}`,
      // In-scope symbols sort above the rest of the project.
      boost: symbol.in_scope ? 1 : -1,
    });
  }
  return out;
}

function toLspCompletions(items: LspCompletionItem[]): Completion[] {
  const seen = new Set<string>();
  const out: Completion[] = [];
  for (const item of items) {
    const label = item.label.trim();
    if (!label || seen.has(label)) continue;
    seen.add(label);
    out.push({
      label,
      type: COMPLETION_TYPES[item.kind ?? ""] ?? item.kind ?? "text",
      detail: item.detail || item.kind || "",
      info: item.documentation || undefined,
      apply: item.insert_text && item.insert_text !== label ? item.insert_text : undefined,
    });
  }
  return out;
}

function lspCompletionSource(
  lookup: (
    args: { line: number; col: number; trigger?: string },
    signal: AbortSignal,
  ) => Promise<LspCompletionItem[]>,
): (context: CompletionContext) => Promise<CompletionResult | null> {
  return async (context) => {
    const word = context.matchBefore(/[\w$]+/);
    if ((!word || word.from === word.to) && !context.explicit) return null;
    const { line, col } = posToLineCol(context.state.doc, context.pos);
    const controller = new AbortController();
    if (typeof context.addEventListener === "function") {
      context.addEventListener("abort", () => controller.abort());
    }
    let items: LspCompletionItem[];
    try {
      items = await lookup({ line, col }, controller.signal);
    } catch {
      return null;
    }
    if (context.aborted || !items.length) return null;
    const options = toLspCompletions(items);
    if (!options.length) return null;
    return { from: word?.from ?? context.pos, options, validFor: /^[\w$]*$/ };
  };
}

function applyWorkspaceEditsToView(
  view: EditorView,
  filePath: string | undefined,
  edits: Record<string, Array<{
    line: number;
    col: number;
    end_line: number;
    end_col: number;
    new_text: string;
  }>>,
): boolean {
  if (!filePath) return false;
  const rows =
    edits[filePath]
    ?? Object.entries(edits).find(([path]) => path === filePath || path.endsWith(`/${filePath}`))?.[1];
  if (!rows?.length) return false;
  const sorted = [...rows].sort((left, right) => {
    if (left.line !== right.line) return right.line - left.line;
    return right.col - left.col;
  });
  view.dispatch({
    changes: sorted.map((edit) => ({
      from: docPosition(view.state.doc, edit.line, edit.col),
      to: docPosition(view.state.doc, edit.end_line, edit.end_col),
      insert: edit.new_text,
    })),
  });
  return true;
}

function projectSymbolSource(
  lookup: (query: string) => Promise<ProjectSymbol[]>,
): (context: CompletionContext) => Promise<CompletionResult | null> {
  return async (context) => {
    const word = context.matchBefore(/[\w$]+/);
    if (!word || word.text.length < SYMBOL_QUERY_MIN_CHARS) return null;
    if (word.from === word.to && !context.explicit) return null;
    let symbols: ProjectSymbol[];
    try {
      symbols = await lookup(word.text);
    } catch {
      // Completion must never break typing; fall back to built-in sources.
      return null;
    }
    if (context.aborted) return null;
    const options = toCompletions(symbols).slice(0, SYMBOL_COMPLETION_LIMIT);
    if (!options.length) return null;
    return { from: word.from, options, validFor: /^[\w$]*$/ };
  };
}

function toCmDiagnostics(doc: Text, rows: FileDiagnostic[]): Diagnostic[] {
  const out: Diagnostic[] = [];
  for (const row of rows) {
    const from = docPosition(doc, row.line, row.col);
    let to = docPosition(doc, row.end_line, row.end_col);
    if (to <= from) to = Math.min(from + 1, doc.length);
    out.push({
      from,
      to,
      severity: row.severity === "error" ? "error" : "warning",
      message: row.code ? `${row.code}: ${row.message}` : row.message,
    });
  }
  return out;
}

export default function CodeEditor({
  value,
  language,
  isDark,
  readOnly,
  diagnostics,
  reveal,
  reviewBaseline,
  reviewHunks,
  onHunkAction,
  onLookupSymbols,
  onGotoDefinition,
  onFindReferences,
  onHoverInfo,
  onLspCompletions,
  onSignatureHelp,
  onCodeActions,
  onRenameSymbol,
  onRequestCompletion,
  onRequestEdit,
  onAddSelectionToChat,
  onFormatDocument,
  blameByLine,
  filePath,
  breakpointLines,
  stoppedLine,
  onToggleBreakpoint,
  onChange,
  onSave,
}: {
  value: string;
  language: string;
  isDark: boolean;
  readOnly?: boolean;
  /** Absolute or project path - keys per-file Cmd+K history. */
  filePath?: string;
  /** 1-based breakpoint lines for the gutter. */
  breakpointLines?: number[];
  /** 1-based line where the debuggee is stopped. */
  stoppedLine?: number | null;
  /** Click gutter to toggle a breakpoint. */
  onToggleBreakpoint?: (line: number) => void;
  diagnostics?: FileDiagnostic[];
  reveal?: { line: number; nonce: number } | null;
  /** 1-based line → git blame entry for the current-line attribution strip. */
  blameByLine?: Record<number, GitBlameLine> | null;
  /** Pre-agent-edit content: enables Cursor-style change highlighting. */
  reviewBaseline?: string | null;
  /**
   * Server-computed hunks for the pending change. Supplying these together with
   * `onHunkAction` puts accept/reject controls above each one. Positions come
   * from the server's view of the file, so the caller must withhold them while
   * the buffer holds unsaved edits.
   */
  reviewHunks?: ReviewHunk[] | null;
  onHunkAction?: HunkAction;
  /** Resolves a prefix to project symbols; omit to disable index completion. */
  onLookupSymbols?: (query: string) => Promise<ProjectSymbol[]>;
  /** F12 / Ctrl-click: jump to definition at caret. */
  onGotoDefinition?: (target: EditorSymbolPos) => void;
  /** Shift+F12: find references at caret. */
  onFindReferences?: (target: EditorSymbolPos) => void;
  /** Hover tooltip contents for a caret position. */
  onHoverInfo?: (
    target: { line: number; col: number },
    signal: AbortSignal,
  ) => Promise<string>;
  /** Language-server completions at the caret. */
  onLspCompletions?: (
    target: { line: number; col: number; trigger?: string },
    signal: AbortSignal,
  ) => Promise<LspCompletionItem[]>;
  /** Parameter hints after `(` or `,`. */
  onSignatureHelp?: (
    target: { line: number; col: number; trigger?: string },
    signal: AbortSignal,
  ) => Promise<LspSignaturePayload | null>;
  /** Ctrl/Cmd+. quick-fixes for the caret or selection. */
  onCodeActions?: (
    target: { line: number; col: number; endLine: number; endCol: number },
    signal: AbortSignal,
  ) => Promise<LspCodeAction[]>;
  /** F2 rename: preview (apply=false) then apply (apply=true) via LSP. */
  onRenameSymbol?: (
    target: EditorSymbolPos & { newName: string; apply?: boolean },
  ) => Promise<RenamePreviewResult>;
  /** Returns ghost-text to show at the caret; omit to disable inline AI. */
  onRequestCompletion?: CompletionFetcher;
  /** Rewrites a selection from an instruction; omit to disable Cmd+K. */
  onRequestEdit?: (
    args: { selection: string; instruction: string; prefix: string; suffix: string },
    signal: AbortSignal,
    onPartial?: (text: string) => void,
  ) => Promise<string>;
  /** Mod-L: hand the current selection to the chat composer (Cursor parity). */
  onAddSelectionToChat?: (payload: {
    text: string;
    fromLine: number;
    toLine: number;
  }) => void;
  /** Shift-Alt-F: run the project formatter over the buffer (VS Code parity). */
  onFormatDocument?: () => void;
  onChange: (next: string) => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  // The workbench rebuilds these on every one of its renders, and its polls
  // (git status, review, debug state) render it every few seconds. Pinning the
  // identity here rather than at each call site keeps the editor stable no
  // matter how the parent chooses to pass them.
  const onChangeRef = useRef(onChange);
  const onToggleBreakpointRef = useRef(onToggleBreakpoint);
  const onHunkActionRef = useRef(onHunkAction);
  useEffect(() => {
    onChangeRef.current = onChange;
    onToggleBreakpointRef.current = onToggleBreakpoint;
    onHunkActionRef.current = onHunkAction;
  });

  const handleChange = useCallback((next: string) => {
    onChangeRef.current(next);
  }, []);

  // Presence, not identity, is what changes the extension set.
  const canToggleBreakpoint = Boolean(onToggleBreakpoint);
  const canActOnHunks = Boolean(onHunkAction);

  const handleToggleBreakpoint = useCallback((line: number) => {
    onToggleBreakpointRef.current?.(line);
  }, []);

  const handleHunkAction = useCallback<HunkAction>((hunkId, action) => {
    onHunkActionRef.current?.(hunkId, action);
  }, []);

  const stableBreakpointLines = useContentStable(breakpointLines ?? []);
  const stableDiagnostics = useContentStable(diagnostics);
  const stableReviewHunks = useContentStable(reviewHunks);

  const viewRef = useRef<EditorView | null>(null);
  const [editTarget, setEditTarget] = useState<InlineEditTarget | null>(null);
  const [instruction, setInstruction] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState("");
  const [editPreview, setEditPreview] = useState("");
  const [editReady, setEditReady] = useState(false);
  const [editHunks, setEditHunks] = useState<InlineEditHunk[]>([]);
  const [acceptedHunkIds, setAcceptedHunkIds] = useState<Set<string>>(() => new Set());
  const [editHistory, setEditHistory] = useState<string[]>(() => loadCmdkHistory());
  const [historyIndex, setHistoryIndex] = useState(-1);
  const editAbortRef = useRef<AbortController | null>(null);
  const promptInputRef = useRef<HTMLInputElement | null>(null);
  const [renameTarget, setRenameTarget] = useState<EditorSymbolPos | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState("");
  const [renamePreview, setRenamePreview] = useState<RenamePreviewResult | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const [signatureHint, setSignatureHint] = useState<{
    payload: LspSignaturePayload;
    top: number;
    left: number;
  } | null>(null);
  const [codeActionMenu, setCodeActionMenu] = useState<{
    items: LspCodeAction[];
    top: number;
    left: number;
  } | null>(null);
  const signatureSeqRef = useRef(0);
  const onSignatureHelpRef = useRef(onSignatureHelp);
  const onCodeActionsRef = useRef(onCodeActions);
  useEffect(() => {
    onSignatureHelpRef.current = onSignatureHelp;
    onCodeActionsRef.current = onCodeActions;
  });
  const [caretLine, setCaretLine] = useState(1);
  const [fileEditHistory, setFileEditHistory] = useState<CmdkFileEdit[]>([]);
  const [vimEnabled, setVimEnabled] = useState(() => readVimModeEnabled());
  const [vimExtension, setVimExtension] = useState<Extension | null>(null);

  useEffect(() => {
    if (!vimEnabled) {
      setVimExtension(null);
      return;
    }
    let cancelled = false;
    void loadVimExtension()
      .then((ext) => {
        if (!cancelled) setVimExtension(ext);
      })
      .catch(() => {
        if (!cancelled) {
          setVimExtension(null);
          setVimEnabled(false);
          writeVimModeEnabled(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [vimEnabled]);

  const closeEditPrompt = useCallback(() => {
    editAbortRef.current?.abort();
    editAbortRef.current = null;
    setEditTarget(null);
    setInstruction("");
    setEditError("");
    setEditBusy(false);
    setEditPreview("");
    setEditReady(false);
    setEditHunks([]);
    setAcceptedHunkIds(new Set());
    setHistoryIndex(-1);
    viewRef.current?.focus();
  }, []);

  const rememberInstruction = useCallback((text: string) => {
    setEditHistory((prev) => {
      const next = [text, ...prev.filter((item) => item !== text)].slice(0, 12);
      persistCmdkHistory(next);
      return next;
    });
  }, []);

  const syncEditHunks = useCallback((selection: string, replacement: string) => {
    const hunks = buildInlineEditHunks(selection, replacement);
    setEditHunks(hunks);
    setAcceptedHunkIds(defaultAcceptedHunkIds(hunks));
  }, []);

  const closeRenamePrompt = useCallback(() => {
    setRenameTarget(null);
    setRenameValue("");
    setRenameError("");
    setRenameBusy(false);
    setRenamePreview(null);
    viewRef.current?.focus();
  }, []);

  const previewRename = useCallback(async () => {
    const target = renameTarget;
    const next = renameValue.trim();
    if (!target || !onRenameSymbol || !next) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(next)) {
      setRenameError(tx("dev.rename.invalid", "Invalid identifier"));
      return;
    }
    setRenameBusy(true);
    setRenameError("");
    try {
      const preview = await onRenameSymbol({
        ...target,
        newName: next,
        apply: false,
      });
      if (!preview.total) {
        setRenameError(preview.message || tx("dev.rename.empty", "nothing to rename"));
        setRenamePreview(null);
        return;
      }
      setRenamePreview(preview);
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : "rename failed");
      setRenamePreview(null);
    } finally {
      setRenameBusy(false);
    }
  }, [onRenameSymbol, renameTarget, renameValue, tx]);

  const applyRename = useCallback(async () => {
    const target = renameTarget;
    const next = renameValue.trim();
    if (!target || !onRenameSymbol || !next || !renamePreview) return;
    setRenameBusy(true);
    setRenameError("");
    try {
      await onRenameSymbol({ ...target, newName: next, apply: true });
      closeRenamePrompt();
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : "rename failed");
    } finally {
      setRenameBusy(false);
    }
  }, [closeRenamePrompt, onRenameSymbol, renamePreview, renameTarget, renameValue]);

  useEffect(() => {
    if (!renameTarget) return;
    const id = window.setTimeout(() => renameInputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [renameTarget]);

  useEffect(() => {
    if (!filePath || !editTarget) return;
    setFileEditHistory(loadCmdkFileEdits(filePath));
  }, [editTarget, filePath]);
  const runInlineEdit = useCallback(async () => {
    const target = editTarget;
    const text = instruction.trim();
    if (!target || !onRequestEdit || !text) return;
    editAbortRef.current?.abort();
    const controller = new AbortController();
    editAbortRef.current = controller;
    setEditBusy(true);
    setEditReady(false);
    setEditPreview("");
    setEditHunks([]);
    setAcceptedHunkIds(new Set());
    setEditError("");
    rememberInstruction(text);
    try {
      const replacement = await onRequestEdit(
        {
          selection: target.selection,
          instruction: text,
          prefix: target.prefix,
          suffix: target.suffix,
        },
        controller.signal,
        (partial) => {
          if (!controller.signal.aborted) {
            setEditPreview(partial);
            syncEditHunks(target.selection, partial);
          }
        },
      );
      if (controller.signal.aborted) return;
      if (!replacement.trim()) {
        setEditError(tx("dev.edit.empty", "the model returned no replacement code"));
        setEditReady(false);
        return;
      }
      setEditPreview(replacement);
      syncEditHunks(target.selection, replacement);
      setEditReady(true);
    } catch (error) {
      if (controller.signal.aborted) return;
      setEditError(error instanceof Error ? error.message : "the edit failed");
      setEditReady(false);
    } finally {
      if (editAbortRef.current === controller) editAbortRef.current = null;
      setEditBusy(false);
    }
  }, [editTarget, instruction, onRequestEdit, rememberInstruction, syncEditHunks, tx]);

  const composedEditPreview = useMemo(() => {
    if (!editHunks.length) return editPreview;
    return composeInlineEdit(editHunks, acceptedHunkIds);
  }, [acceptedHunkIds, editHunks, editPreview]);

  const acceptInlineEdit = useCallback(() => {
    const view = viewRef.current;
    const target = editTarget;
    const replacement = composedEditPreview;
    if (!view || !target || !replacement) return;
    if (filePath) {
      setFileEditHistory(
        rememberCmdkFileEdit(filePath, {
          instruction: instruction.trim(),
          before: target.selection,
          after: replacement,
        }),
      );
    }
    applyInlineEdit(view, target, replacement);
    closeEditPrompt();
  }, [closeEditPrompt, composedEditPreview, editTarget, filePath, instruction]);

  const restoreFileEdit = useCallback(
    (entry: CmdkFileEdit) => {
      const view = viewRef.current;
      const target = editTarget;
      if (!view || !target) return;
      applyInlineEdit(view, target, entry.after);
      closeEditPrompt();
    },
    [closeEditPrompt, editTarget],
  );

  const toggleEditHunk = useCallback((id: string) => {
    setAcceptedHunkIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const stopInlineEdit = useCallback(() => {
    editAbortRef.current?.abort();
    editAbortRef.current = null;
    setEditBusy(false);
  }, []);

  useEffect(() => {
    if (editTarget) promptInputRef.current?.focus();
  }, [editTarget]);

  // Each reveal request is honoured exactly once. The caller rebuilds the
  // `reveal` object on every render, so without the nonce guard the caret was
  // dragged back and the view re-centred on every poll and every keystroke,
  // which made the file unusable after a "go to definition".
  const appliedRevealRef = useRef<number | null>(null);
  useEffect(() => {
    if (!reveal) return;
    if (appliedRevealRef.current === reveal.nonce) return;
    const view = viewRef.current;
    if (!view) return;
    const doc = view.state.doc;
    // An empty buffer means the file has not arrived yet; `value` is in the
    // dependencies so this runs again once it does.
    if (doc.length === 0 && reveal.line > 1) return;
    appliedRevealRef.current = reveal.nonce;
    const lineNumber = Math.min(Math.max(reveal.line, 1), doc.lines);
    const info = doc.line(lineNumber);
    view.dispatch({
      selection: EditorSelection.cursor(info.from),
      effects: EditorView.scrollIntoView(info.from, { y: "center" }),
    });
  }, [reveal, value]);

  const extensions = useMemo(() => {
    const list = [...MULTI_CURSOR_EXTENSIONS];
    list.push(editorLineMetrics());
    list.push(
      EditorView.contentAttributes.of({
        spellcheck: "false",
        autocorrect: "off",
        autocapitalize: "off",
      }),
    );
    if (vimExtension) list.push(vimExtension);
    const lang = cachedLanguageSupport(language);
    if (lang) list.push(lang);
    if (canToggleBreakpoint) {
      const lines = new Set(stableBreakpointLines);
      const toggle = handleToggleBreakpoint;
      list.push(
        gutter({
          class: "cm-navin-breakpoint-gutter",
          markers: (view) => {
            const builder = new RangeSetBuilder<GutterMarker>();
            for (const line of lines) {
              if (line < 1 || line > view.state.doc.lines) continue;
              const info = view.state.doc.line(line);
              builder.add(info.from, info.from, BREAKPOINT_MARKER);
            }
            return builder.finish();
          },
          domEventHandlers: {
            mousedown(view, block) {
              const line = view.state.doc.lineAt(block.from).number;
              toggle(line);
              return true;
            },
          },
        }),
        EditorView.theme({
          ".cm-navin-breakpoint-gutter": {
            width: "14px",
            cursor: "pointer",
          },
          ".cm-navin-breakpoint": {
            color: "#ef4444",
            fontSize: "9px",
            lineHeight: "1",
            transform: "translateY(1px)",
          },
          ".cm-navin-stopped-line": {
            backgroundColor: "rgba(234, 179, 8, 0.18)",
          },
        }),
      );
      if (stoppedLine && stoppedLine > 0) {
        const lineNo = stoppedLine;
        list.push(
          EditorView.decorations.compute(["doc"], (state) => {
            if (lineNo < 1 || lineNo > state.doc.lines) {
              return Decoration.none;
            }
            const info = state.doc.line(lineNo);
            return Decoration.set([
              Decoration.line({ class: "cm-navin-stopped-line" }).range(info.from),
            ]);
          }),
        );
      }
    }
    if (stableDiagnostics && stableDiagnostics.length) {
      const rows = stableDiagnostics;
      list.push(
        lintGutter(),
        linter((view) => toCmDiagnostics(view.state.doc, rows), { delay: 0 }),
      );
    }
    if (reviewBaseline != null) {
      const baseline = reviewBaseline;
      const hunkList =
        stableReviewHunks && stableReviewHunks.length ? stableReviewHunks : null;
      const action = canActOnHunks ? handleHunkAction : null;
      list.push(
        reviewTheme,
        reviewGutter,
        lineNumberMarkers.compute(["doc"], (state) =>
          buildAddedGutterMarkers(baseline, state.doc),
        ),
        EditorView.decorations.compute(["doc", "selection"], (state) => {
          if (!hunkList || !action) {
            return buildReviewDecorations(baseline, state.doc);
          }
          const line0 = state.doc.lineAt(state.selection.main.head).number - 1;
          const active = hunkNearLine(hunkList, line0);
          return buildReviewDecorations(baseline, state.doc, {
            hunks: hunkList,
            onAction: action,
            labels: {
              accept: tx("dev.review.acceptHunk", "Accept"),
              reject: tx("dev.review.rejectHunk", "Reject"),
            },
            titles: {
              accept: tx("dev.review.acceptHunkHint", "Accept hunk (Ctrl/Cmd+Y)"),
              reject: tx("dev.review.rejectHunkHint", "Reject hunk (Ctrl/Cmd+N)"),
            },
            activeId: active?.id ?? null,
          });
        }),
      );
    }
    list.push(
      autocompletion({
        activateOnTyping: true,
        closeOnBlur: true,
        maxRenderedOptions: 60,
      }),
    );
    // Registering sources as language data (rather than `override`) keeps the
    // language's own completions instead of replacing them.
    const extraSources: CompletionSource[] = [completeAnyWord];
    if (onLookupSymbols) {
      extraSources.unshift(projectSymbolSource(onLookupSymbols));
    }
    if (onLspCompletions) {
      extraSources.unshift(lspCompletionSource(onLspCompletions));
    }
    list.push(
      EditorState.languageData.of(() =>
        extraSources.map((autocomplete) => ({ autocomplete })),
      ),
    );
    if (onRequestCompletion && !readOnly) {
      list.push(
        inlineCompletion({
          fetch: onRequestCompletion,
          // A read-only or unfocused buffer should not spend model calls.
          enabled: () => !readOnly && viewRef.current?.hasFocus === true,
        }),
      );
    }
    if (onRequestEdit && !readOnly) {
      list.push(
        inlineEditKeymap((target) => {
          setEditError("");
          setInstruction("");
          setEditTarget(target);
        }),
      );
    }
    list.push(
      EditorView.theme({
        ".cm-lsp-hover": {
          maxWidth: "28rem",
          maxHeight: "14rem",
          overflow: "auto",
          padding: "6px 8px",
          fontSize: "12px",
          lineHeight: "1.45",
          whiteSpace: "pre-wrap",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        },
        ".cm-lsp-signature": {
          maxWidth: "36rem",
          padding: "6px 8px",
          fontSize: "12px",
          lineHeight: "1.45",
          whiteSpace: "pre-wrap",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        },
      }),
    );
    if (onHoverInfo) {
      const hover = onHoverInfo;
      list.push(
        hoverTooltip(
          async (view, pos) => {
            const word = view.state.wordAt(pos);
            if (!word) return null;
            const symbol = view.state.doc.sliceString(word.from, word.to);
            if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(symbol)) return null;
            const { line, col } = posToLineCol(view.state.doc, pos);
            const controller = new AbortController();
            let text = "";
            try {
              text = (await hover({ line, col }, controller.signal)).trim();
            } catch {
              return null;
            }
            if (!isUsefulHoverText(text, symbol, view.state.doc.lineAt(pos).text)) {
              return null;
            }
            return {
              pos: word.from,
              end: word.to,
              create() {
                const dom = document.createElement("div");
                dom.className = "cm-lsp-hover";
                dom.textContent = text;
                return { dom };
              },
            };
          },
          { hoverTime: 350 },
        ),
      );
    }

    const navBindings: {
      key: string;
      run: (view: EditorView) => boolean;
    }[] = [];
    if (onGotoDefinition) {
      const goto = onGotoDefinition;
      const jump = (view: EditorView): boolean => {
        const pos = view.state.selection.main.head;
        const word = view.state.wordAt(pos);
        if (!word) return false;
        const symbol = view.state.doc.sliceString(word.from, word.to);
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(symbol)) return false;
        const { line, col } = posToLineCol(view.state.doc, pos);
        goto({ symbol, line, col });
        return true;
      };
      navBindings.push({ key: "F12", run: jump });
    }
    if (onFindReferences) {
      const findRefs = onFindReferences;
      navBindings.push({
        key: "Shift-F12",
        run(view) {
          const pos = view.state.selection.main.head;
          const word = view.state.wordAt(pos);
          if (!word) return false;
          const symbol = view.state.doc.sliceString(word.from, word.to);
          if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(symbol)) return false;
          const { line, col } = posToLineCol(view.state.doc, pos);
          findRefs({ symbol, line, col });
          return true;
        },
      });
    }
    if (onSignatureHelp && !readOnly) {
      const fetchHelp = onSignatureHelp;
      list.push(
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          let last = "";
          update.changes.iterChanges((_fromA, _toA, _fromB, _toB, inserted) => {
            const text = inserted.toString();
            if (text) last = text[text.length - 1] ?? "";
          });
          if (last === ")" || last === ";" || last === "\n") {
            setSignatureHint(null);
            return;
          }
          if (last !== "(" && last !== ",") return;
          const view = update.view;
          const { line, col } = posToLineCol(view.state.doc, view.state.selection.main.head);
          const seq = ++signatureSeqRef.current;
          const controller = new AbortController();
          void fetchHelp({ line, col, trigger: last }, controller.signal)
            .then((payload) => {
              if (seq !== signatureSeqRef.current) return;
              if (!payload?.signatures?.length) {
                setSignatureHint(null);
                return;
              }
              const coords = view.coordsAtPos(view.state.selection.main.head);
              const parent = view.dom.getBoundingClientRect();
              setSignatureHint({
                payload,
                top: (coords?.bottom ?? parent.top) - parent.top + 6,
                left: (coords?.left ?? parent.left) - parent.left,
              });
            })
            .catch(() => {
              if (seq === signatureSeqRef.current) setSignatureHint(null);
            });
        }),
      );
    }
    if (onCodeActions && !readOnly) {
      const fetchActions = onCodeActions;
      navBindings.push({
        key: "Mod-.",
        run(view) {
          const range = view.state.selection.main;
          const from = posToLineCol(view.state.doc, range.from);
          const to = posToLineCol(view.state.doc, range.to);
          const controller = new AbortController();
          void fetchActions(
            { line: from.line, col: from.col, endLine: to.line, endCol: to.col },
            controller.signal,
          )
            .then((items) => {
              if (!items.length) {
                setCodeActionMenu(null);
                return;
              }
              const coords = view.coordsAtPos(range.head);
              const parent = view.dom.getBoundingClientRect();
              setCodeActionMenu({
                items,
                top: (coords?.bottom ?? parent.top) - parent.top + 6,
                left: (coords?.left ?? parent.left) - parent.left,
              });
            })
            .catch(() => setCodeActionMenu(null));
          return true;
        },
      });
    }
    if (onRenameSymbol && !readOnly) {
      navBindings.push({
        key: "F2",
        run(view) {
          const pos = view.state.selection.main.head;
          const word = view.state.wordAt(pos);
          if (!word) return false;
          const symbol = view.state.doc.sliceString(word.from, word.to);
          if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(symbol)) return false;
          const { line, col } = posToLineCol(view.state.doc, pos);
          setEditTarget(null);
          setRenameError("");
          setRenameValue(symbol);
          setRenameTarget({ symbol, line, col });
          return true;
        },
      });
    }
    if (onAddSelectionToChat) {
      const add = onAddSelectionToChat;
      navBindings.push({
        key: "Mod-l",
        run(view) {
          const sel = view.state.selection.main;
          if (sel.empty) return false;
          add({
            text: view.state.sliceDoc(sel.from, sel.to),
            fromLine: view.state.doc.lineAt(sel.from).number,
            toLine: view.state.doc.lineAt(sel.to).number,
          });
          return true;
        },
      });
    }
    if (onFormatDocument && !readOnly) {
      const format = onFormatDocument;
      navBindings.push({
        key: "Shift-Alt-f",
        run() {
          format();
          return true;
        },
      });
    }
    if (stableReviewHunks && stableReviewHunks.length && canActOnHunks) {
      const hunks = stableReviewHunks;
      const act = handleHunkAction;
      const caretLine0 = (view: EditorView) =>
        view.state.doc.lineAt(view.state.selection.main.head).number - 1;
      navBindings.push(
        {
          key: "F7",
          run(view) {
            const next = nextHunkAfterLine(hunks, caretLine0(view));
            if (!next) return false;
            revealHunk(view, next);
            return true;
          },
        },
        {
          key: "Shift-F7",
          run(view) {
            const prev = prevHunkBeforeLine(hunks, caretLine0(view));
            if (!prev) return false;
            revealHunk(view, prev);
            return true;
          },
        },
        {
          key: "Mod-y",
          run(view) {
            const hunk = hunkNearLine(hunks, caretLine0(view));
            if (!hunk) return false;
            act(hunk.id, "accept");
            return true;
          },
        },
        {
          key: "Mod-n",
          run(view) {
            const hunk = hunkNearLine(hunks, caretLine0(view));
            if (!hunk) return false;
            act(hunk.id, "reject");
            return true;
          },
        },
      );
    }
    if (navBindings.length) {
      list.push(keymap.of(navBindings));
    }
    list.push(
      EditorView.updateListener.of((update) => {
        if (!update.selectionSet && !update.focusChanged && !update.docChanged) {
          return;
        }
        const line = update.state.doc.lineAt(update.state.selection.main.head).number;
        setCaretLine(line);
      }),
    );
    if (onGotoDefinition) {
      const goto = onGotoDefinition;
      list.push(
        EditorView.domEventHandlers({
          click(event, view) {
            if (!(event.metaKey || event.ctrlKey)) return false;
            const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
            if (pos == null) return false;
            const word = view.state.wordAt(pos);
            if (!word) return false;
            const symbol = view.state.doc.sliceString(word.from, word.to);
            if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(symbol)) return false;
            const { line, col } = posToLineCol(view.state.doc, pos);
            event.preventDefault();
            goto({ symbol, line, col });
            return true;
          },
        }),
      );
    }
    return list;
  }, [
    stableBreakpointLines,
    stableDiagnostics,
    language,
    onLookupSymbols,
    onLspCompletions,
    onGotoDefinition,
    onFindReferences,
    onHoverInfo,
    onSignatureHelp,
    onCodeActions,
    onRenameSymbol,
    onRequestCompletion,
    onRequestEdit,
    onAddSelectionToChat,
    onFormatDocument,
    canToggleBreakpoint,
    handleToggleBreakpoint,
    readOnly,
    reviewBaseline,
    stableReviewHunks,
    canActOnHunks,
    handleHunkAction,
    stoppedLine,
    tx,
    vimExtension,
  ]);

  return (
    <div
      className={[
        "relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
        "[&_.cm-theme]:min-h-0 [&_.cm-theme]:flex-1 [&_.cm-theme]:overflow-hidden",
        "[&_.cm-editor]:h-full [&_.cm-editor]:min-h-0 [&_.cm-editor]:isolate",
        "[&_.cm-scroller]:overflow-auto [&_.cm-scroller]:overscroll-contain",
      ].join(" ")}
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          onSave();
        }
      }}
    >
      {editTarget ? (
        <div className="absolute inset-x-0 top-0 z-20 border-b border-border bg-popover/95 p-2 shadow-lg backdrop-blur">
          <div className="flex items-center gap-2">
            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-primary">
              Edit
            </span>
            <input
              ref={promptInputRef}
              value={instruction}
              disabled={editBusy}
              placeholder={
                editTarget.selection.trim()
                  ? "Describe the change to the selected code..."
                  : "Describe the code to insert here..."
              }
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
              onChange={(event) => {
                setInstruction(event.target.value);
                setHistoryIndex(-1);
                if (editReady) {
                  setEditReady(false);
                  setEditPreview("");
                  setEditHunks([]);
                  setAcceptedHunkIds(new Set());
                }
              }}
              onKeyDown={(event) => {
                if (event.key === "ArrowUp" && !instruction && editHistory.length) {
                  event.preventDefault();
                  const next = Math.min(historyIndex + 1, editHistory.length - 1);
                  setHistoryIndex(next);
                  setInstruction(editHistory[next] ?? "");
                  return;
                }
                if (event.key === "ArrowDown" && historyIndex >= 0) {
                  event.preventDefault();
                  const next = historyIndex - 1;
                  setHistoryIndex(next);
                  setInstruction(next < 0 ? "" : (editHistory[next] ?? ""));
                  return;
                }
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (editReady && composedEditPreview.trim()) {
                    acceptInlineEdit();
                  } else if (!editBusy) {
                    void runInlineEdit();
                  }
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  closeEditPrompt();
                }
              }}
            />
            {editBusy ? (
              <button
                type="button"
                onClick={stopInlineEdit}
                className="shrink-0 rounded border border-border px-2 py-0.5 text-xs hover:bg-accent"
              >
                {tx("dev.edit.stop", "Stop")}
              </button>
            ) : editReady ? (
              <>
                <button
                  type="button"
                  disabled={!composedEditPreview.trim()}
                  onClick={acceptInlineEdit}
                  className="shrink-0 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 hover:bg-emerald-500/20 disabled:opacity-50 dark:text-emerald-300"
                >
                  {tx("dev.edit.apply", "Apply")}
                </button>
                <button
                  type="button"
                  disabled={!instruction.trim()}
                  onClick={() => void runInlineEdit()}
                  className="shrink-0 rounded border border-border px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-50"
                >
                  {tx("dev.edit.retry", "Retry")}
                </button>
              </>
            ) : (
              <button
                type="button"
                disabled={!instruction.trim()}
                onClick={() => void runInlineEdit()}
                className="shrink-0 rounded border border-border px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-50"
              >
                {tx("dev.edit.generate", "Generate")}
              </button>
            )}
            <button
              type="button"
              onClick={closeEditPrompt}
              className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent"
            >
              {tx("dev.edit.discard", "Discard")}
            </button>
          </div>
          <p className="mt-1 truncate text-[11px] text-muted-foreground">
            {editError ? (
              <span className="text-destructive">{editError}</span>
            ) : editBusy ? (
              tx("dev.edit.streaming", "Streaming preview...")
            ) : editReady ? (
              countChangedHunks(editHunks) > 1
                ? tx(
                    "dev.edit.readyHunks",
                    "Preview ready - toggle hunks, Enter/Apply to insert, Retry to regenerate",
                  )
                : tx(
                    "dev.edit.ready",
                    "Preview ready - Enter/Apply to insert, Retry to regenerate, Discard to cancel",
                  )
            ) : (
              `${editTarget.selection.split("\n").length} line(s) selected - Enter to generate, ↑ for history`
            )}
          </p>
          {!editBusy && !editReady && fileEditHistory.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {fileEditHistory.slice(0, 5).map((entry) => (
                <button
                  key={`${entry.at}-${entry.instruction}`}
                  type="button"
                  onClick={() => {
                    setInstruction(entry.instruction);
                    setEditPreview(entry.after);
                    syncEditHunks(editTarget.selection, entry.after);
                    setEditReady(true);
                  }}
                  className="max-w-[12rem] truncate rounded border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground"
                  title={tx("dev.edit.reuseHistory", "Reuse this past Cmd+K edit")}
                >
                  {entry.instruction}
                </button>
              ))}
              {fileEditHistory[0] ? (
                <button
                  type="button"
                  onClick={() => restoreFileEdit(fileEditHistory[0]!)}
                  className="rounded border border-border/60 px-1.5 py-0.5 text-[10px] hover:bg-accent"
                >
                  {tx("dev.edit.reapplyLast", "Re-apply last")}
                </button>
              ) : null}
            </div>
          ) : null}
          {editHunks.some((h) => h.kind === "change") ? (
            <div className="mt-2 max-h-48 space-y-1.5 overflow-auto rounded-md border border-border/60 bg-background/80 p-1.5">
              {editHunks
                .filter((hunk) => hunk.kind === "change")
                .map((hunk, index) => {
                  const on = acceptedHunkIds.has(hunk.id);
                  return (
                    <button
                      key={hunk.id}
                      type="button"
                      disabled={editBusy}
                      onClick={() => toggleEditHunk(hunk.id)}
                      className={[
                        "block w-full rounded border px-2 py-1 text-left font-mono text-[11px] leading-relaxed transition-colors",
                        on
                          ? "border-emerald-500/35 bg-emerald-500/5"
                          : "border-border/50 bg-muted/20 opacity-60",
                      ].join(" ")}
                    >
                      <span className="mb-0.5 flex items-center justify-between text-[10px] font-sans font-medium uppercase tracking-wide text-muted-foreground">
                        <span>
                          {tx("dev.edit.hunk", "Hunk")} {index + 1}
                        </span>
                        <span>{on ? tx("dev.edit.hunkOn", "on") : tx("dev.edit.hunkOff", "off")}</span>
                      </span>
                      {hunk.oldText ? (
                        <pre className="whitespace-pre-wrap text-red-600/90 dark:text-red-300/90">
                          {hunk.oldText
                            .split("\n")
                            .filter((line, i, arr) => !(i === arr.length - 1 && line === ""))
                            .map((line) => `- ${line}`)
                            .join("\n")}
                        </pre>
                      ) : null}
                      {hunk.newText ? (
                        <pre className="whitespace-pre-wrap text-emerald-700 dark:text-emerald-300">
                          {hunk.newText
                            .split("\n")
                            .filter((line, i, arr) => !(i === arr.length - 1 && line === ""))
                            .map((line) => `+ ${line}`)
                            .join("\n")}
                        </pre>
                      ) : null}
                    </button>
                  );
                })}
              {editBusy ? (
                <span className="px-1 font-mono text-[11px] text-muted-foreground animate-pulse">▍</span>
              ) : null}
            </div>
          ) : editPreview ? (
            <pre className="mt-2 max-h-40 overflow-auto rounded-md border border-border/60 bg-background/80 p-2 font-mono text-[11px] leading-relaxed text-foreground/90">
              {editPreview}
              {editBusy ? (
                <span className="animate-pulse text-muted-foreground">▍</span>
              ) : null}
            </pre>
          ) : null}
        </div>
      ) : null}
      {renameTarget ? (
        <div className="absolute inset-x-0 top-0 z-20 border-b border-border bg-popover/95 p-2 shadow-lg backdrop-blur">
          <div className="flex items-center gap-2">
            <span className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
              {tx("dev.rename.label", "Rename")}
            </span>
            <input
              ref={renameInputRef}
              value={renameValue}
              disabled={renameBusy || !!renamePreview}
              placeholder={renameTarget.symbol}
              className="min-w-0 flex-1 bg-transparent font-mono text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
              onChange={(event) => {
                setRenameValue(event.target.value);
                setRenamePreview(null);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (renamePreview) void applyRename();
                  else void previewRename();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  closeRenamePrompt();
                }
              }}
            />
            {renamePreview ? (
              <button
                type="button"
                disabled={renameBusy}
                onClick={() => void applyRename()}
                className="shrink-0 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 hover:bg-emerald-500/20 disabled:opacity-50 dark:text-emerald-300"
              >
                {renameBusy
                  ? tx("dev.rename.working", "Working...")
                  : tx("dev.rename.confirm", "Confirm")}
              </button>
            ) : (
              <button
                type="button"
                disabled={renameBusy || !renameValue.trim()}
                onClick={() => void previewRename()}
                className="shrink-0 rounded border border-border px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-50"
              >
                {renameBusy
                  ? tx("dev.rename.working", "Working...")
                  : tx("dev.rename.preview", "Preview")}
              </button>
            )}
            <button
              type="button"
              onClick={closeRenamePrompt}
              className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent"
            >
              Esc
            </button>
          </div>
          <p className="mt-1 truncate text-[11px] text-muted-foreground">
            {renameError ? (
              <span className="text-destructive">{renameError}</span>
            ) : renamePreview ? (
              tx("dev.rename.previewReady", "Preview: {{total}} edit(s) in {{files}} file(s) - Confirm to write").replace(
                "{{total}}",
                String(renamePreview.total),
              ).replace("{{files}}", String(renamePreview.files.length))
            ) : (
              tx(
                "dev.rename.hint",
                "F2 rename via language server - Enter to preview across the project",
              )
            )}
          </p>
          {renamePreview?.files?.length ? (
            <ul className="mt-1 max-h-28 overflow-auto rounded border border-border/50 bg-background/70 px-2 py-1 font-mono text-[11px]">
              {renamePreview.files.map((file) => (
                <li key={file} className="truncate text-foreground/85">
                  {file}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {blameByLine && blameByLine[caretLine] ? (
        <div
          className="flex shrink-0 items-center gap-2 border-b border-border/40 bg-muted/20 px-3 py-1 font-mono text-[11px] text-muted-foreground"
          title={
            [
              blameByLine[caretLine]?.author,
              blameByLine[caretLine]?.email,
              blameByLine[caretLine]?.commit,
              blameByLine[caretLine]?.summary,
            ]
              .filter(Boolean)
              .join(" · ")
          }
        >
          <span className="truncate text-foreground/80">
            {blameByLine[caretLine]?.author || "unknown"}
          </span>
          {blameByLine[caretLine]?.date ? (
            <span className="shrink-0">
              {compactRelativeTime(blameByLine[caretLine]?.date)}
            </span>
          ) : null}
          <span className="shrink-0 text-muted-foreground/80">
            {blameByLine[caretLine]?.commit}
          </span>
          {blameByLine[caretLine]?.summary ? (
            <span className="min-w-0 flex-1 truncate">
              {blameByLine[caretLine]?.summary}
            </span>
          ) : null}
        </div>
      ) : null}
      {signatureHint ? (
        <div
          className="cm-lsp-signature pointer-events-none absolute z-30 rounded border border-border bg-popover text-popover-foreground shadow-md"
          style={{ top: signatureHint.top, left: signatureHint.left }}
          data-testid="lsp-signature-help"
        >
          {(() => {
            const active =
              signatureHint.payload.signatures[signatureHint.payload.active_signature]
              ?? signatureHint.payload.signatures[0];
            if (!active) return null;
            const param = active.parameters[signatureHint.payload.active_parameter];
            return (
              <>
                <div>{active.label}</div>
                {param?.label ? (
                  <div className="mt-1 text-primary">{param.label}</div>
                ) : null}
                {active.documentation ? (
                  <div className="mt-1 text-muted-foreground">{active.documentation}</div>
                ) : null}
              </>
            );
          })()}
        </div>
      ) : null}
      {codeActionMenu ? (
        <div
          className="absolute z-30 min-w-[16rem] overflow-hidden rounded border border-border bg-popover text-popover-foreground shadow-md"
          style={{ top: codeActionMenu.top, left: codeActionMenu.left }}
          data-testid="lsp-code-actions"
        >
          {codeActionMenu.items.map((action, index) => (
            <button
              key={`${action.title}-${index}`}
              type="button"
              className="block w-full truncate px-2 py-1.5 text-left text-[12px] hover:bg-accent"
              onClick={() => {
                const view = viewRef.current;
                if (view) {
                  applyWorkspaceEditsToView(view, filePath, action.edits);
                }
                setCodeActionMenu(null);
              }}
            >
              <span className="font-medium">{action.title}</span>
              {action.kind ? (
                <span className="ml-2 text-muted-foreground">{action.kind}</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
      <CodeMirror
        value={value}
        height="100%"
        theme={isDark ? vscodeDark : vscodeLight}
        extensions={extensions}
        readOnly={readOnly}
        onCreateEditor={(view) => {
          viewRef.current = view;
          setCaretLine(view.state.doc.lineAt(view.state.selection.main.head).number);
          view.requestMeasure();
          void document.fonts?.ready.then(() => view.requestMeasure());
        }}
        onChange={handleChange}
        basicSetup={BASIC_SETUP}
      />
      <div className="flex shrink-0 items-center gap-2 border-t border-border/40 bg-muted/15 px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
        <button
          type="button"
          onClick={() => {
            const next = !vimEnabled;
            setVimEnabled(next);
            writeVimModeEnabled(next);
          }}
          className={[
            "rounded px-1.5 py-0.5 font-semibold tracking-wide transition-colors",
            vimEnabled
              ? "bg-foreground text-background"
              : "hover:bg-muted hover:text-foreground",
          ].join(" ")}
          title={
            vimEnabled
              ? tx("dev.editor.vimOff", "Disable Vim keybindings")
              : tx("dev.editor.vimOn", "Enable Vim keybindings")
          }
          aria-pressed={vimEnabled}
          aria-label={tx("dev.editor.vim", "Vim mode")}
        >
          Vim
        </button>
        {vimEnabled && !vimExtension ? (
          <span className="text-muted-foreground/70">
            {tx("dev.editor.vimLoading", "loading…")}
          </span>
        ) : null}
        <span className="ml-auto tabular-nums">Ln {caretLine}</span>
      </div>
    </div>
  );
}
