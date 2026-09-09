// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Markdown editing for the Team composer.
 *
 * Pure text and caret math so the toolbar can be unit tested without a DOM.
 * Every helper takes the textarea value plus its selection and returns the
 * next value with the selection the caller should restore.
 */

export type ComposerSelection = {
  value: string;
  start: number;
  end: number;
};

export type ComposerEdit = {
  value: string;
  start: number;
  end: number;
};

export type ComposerAction =
  | "bold"
  | "italic"
  | "underline"
  | "strike"
  | "highlight"
  | "code"
  | "codeBlock"
  | "quote"
  | "bullet"
  | "ordered"
  | "link";

const BULLET_RE = /^(\s*)[-*]\s+/;
const ORDERED_RE = /^(\s*)\d+\.\s+/;
const QUOTE_RE = /^(\s*)>\s?/;

function clampRange(sel: ComposerSelection): Required<ComposerSelection> {
  const value = sel.value || "";
  const start = Math.max(0, Math.min(sel.start ?? 0, value.length));
  const end = Math.max(start, Math.min(sel.end ?? start, value.length));
  return { value, start, end };
}

const INLINE_MARKS = ["**", "~~", "==", "`", "_", "*"] as const;

/** Walk past other inline markers so stacked formats unwrap cleanly. */
function skipOtherMarks(
  value: string,
  index: number,
  direction: -1 | 1,
  keep: string,
): number {
  let at = index;
  while (true) {
    let stepped = false;
    for (const mark of INLINE_MARKS) {
      if (mark === keep) continue;
      if (direction < 0) {
        if (at >= mark.length && value.slice(at - mark.length, at) === mark) {
          at -= mark.length;
          stepped = true;
          break;
        }
      } else if (value.slice(at, at + mark.length) === mark) {
        at += mark.length;
        stepped = true;
        break;
      }
    }
    if (!stepped) return at;
  }
}

/**
 * Wrap the selection, or unwrap it when the markers are already there.
 * The markers may sit inside the selection or just around it, because a
 * second click on Bold usually happens while the word is still selected.
 */
export function toggleWrap(
  sel: ComposerSelection,
  open: string,
  close: string = open,
  placeholder = "",
): ComposerEdit {
  const { value, start, end } = clampRange(sel);
  const selected = value.slice(start, end);

  if (
    selected.length >= open.length + close.length
    && selected.startsWith(open)
    && selected.endsWith(close)
  ) {
    const inner = selected.slice(open.length, selected.length - close.length);
    return {
      value: value.slice(0, start) + inner + value.slice(end),
      start,
      end: start + inner.length,
    };
  }

  const before = value.slice(Math.max(0, start - open.length), start);
  const after = value.slice(end, end + close.length);
  if (selected && before === open && after === close) {
    const from = start - open.length;
    return {
      value: value.slice(0, from) + selected + value.slice(end + close.length),
      start: from,
      end: from + selected.length,
    };
  }

  // Already wrapped, with other marks between the caret and this pair:
  // **~~_hhhh_~~** + Bold should drop the outer ** rather than nest ****.
  const left = skipOtherMarks(value, start, -1, open);
  const right = skipOtherMarks(value, end, 1, close);
  if (
    selected
    && left >= open.length
    && value.slice(left - open.length, left) === open
    && value.slice(right, right + close.length) === close
  ) {
    const from = left - open.length;
    return {
      value: value.slice(0, from) + value.slice(left, right) + value.slice(right + close.length),
      start: start - open.length,
      end: end - open.length,
    };
  }

  const body = selected || placeholder;
  const bodyStart = start + open.length;
  return {
    value: value.slice(0, start) + open + body + close + value.slice(end),
    start: bodyStart,
    end: bodyStart + body.length,
  };
}

function lineBounds(value: string, start: number, end: number): { from: number; to: number } {
  const from = value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
  const found = value.indexOf("\n", end);
  return { from, to: found < 0 ? value.length : found };
}

/**
 * Add or remove a per-line marker over every line the selection touches.
 * The whole block toggles off only when every line already carries it, so a
 * mixed selection is normalised upwards rather than half cleared.
 */
export function togglePrefix(
  sel: ComposerSelection,
  prefix: string | ((index: number) => string),
  matcher: RegExp,
): ComposerEdit {
  const { value, start, end } = clampRange(sel);
  const { from, to } = lineBounds(value, start, end);
  const lines = value.slice(from, to).split("\n");
  const all = lines.every((line) => matcher.test(line));
  const next = lines
    .map((line, index) => {
      if (all) return line.replace(matcher, "$1");
      const indent = /^\s*/.exec(line)?.[0] ?? "";
      const mark = typeof prefix === "function" ? prefix(index) : prefix;
      return indent + mark + line.slice(indent.length);
    })
    .join("\n");
  return {
    value: value.slice(0, from) + next + value.slice(to),
    start: from,
    end: from + next.length,
  };
}

export function toggleBulletList(sel: ComposerSelection): ComposerEdit {
  return togglePrefix(sel, "- ", BULLET_RE);
}

export function toggleOrderedList(sel: ComposerSelection): ComposerEdit {
  return togglePrefix(sel, (index) => `${index + 1}. `, ORDERED_RE);
}

export function toggleQuote(sel: ComposerSelection): ComposerEdit {
  return togglePrefix(sel, "> ", QUOTE_RE);
}

const URL_RE = /^(https?:\/\/|www\.)\S+$/i;

/** A selected URL becomes the target, anything else becomes the label. */
export function insertLink(sel: ComposerSelection, label = "text", target = "url"): ComposerEdit {
  const { value, start, end } = clampRange(sel);
  const selected = value.slice(start, end);
  const isUrl = URL_RE.test(selected.trim());
  const text = isUrl ? label : selected || label;
  const href = isUrl ? selected.trim() : target;
  const snippet = `[${text}](${href})`;
  const focusStart = isUrl ? start + 1 : start + 1 + text.length + 2;
  const focusLength = isUrl ? text.length : href.length;
  return {
    value: value.slice(0, start) + snippet + value.slice(end),
    start: focusStart,
    end: focusStart + focusLength,
  };
}

/** Fenced block on its own lines, whatever the caret sat next to. */
export function insertCodeBlock(sel: ComposerSelection): ComposerEdit {
  const { value, start, end } = clampRange(sel);
  const body = value.slice(start, end);
  const open = `${start > 0 && value[start - 1] !== "\n" ? "\n" : ""}\`\`\`\n`;
  const close = `\n\`\`\`${end < value.length && value[end] !== "\n" ? "\n" : ""}`;
  const bodyStart = start + open.length;
  return {
    value: value.slice(0, start) + open + body + close + value.slice(end),
    start: bodyStart,
    end: bodyStart + body.length,
  };
}

/** Drop text where the caret is and leave the caret right after it. */
export function insertAtCaret(sel: ComposerSelection, text: string): ComposerEdit {
  const { value, start, end } = clampRange(sel);
  const at = start + text.length;
  return {
    value: value.slice(0, start) + text + value.slice(end),
    start: at,
    end: at,
  };
}

/**
 * Replace an arbitrary range, used by the mention menu which knows the exact
 * span of the ``@token`` it is completing.
 */
export function replaceRange(
  value: string,
  from: number,
  to: number,
  text: string,
): ComposerEdit {
  const safeFrom = Math.max(0, Math.min(from, value.length));
  const safeTo = Math.max(safeFrom, Math.min(to, value.length));
  const at = safeFrom + text.length;
  return {
    value: value.slice(0, safeFrom) + text + value.slice(safeTo),
    start: at,
    end: at,
  };
}

export function applyComposerFormat(
  sel: ComposerSelection,
  action: ComposerAction,
): ComposerEdit {
  switch (action) {
    case "bold":
      return toggleWrap(sel, "**", "**", "bold");
    case "italic":
      return toggleWrap(sel, "*", "*", "italic");
    case "underline":
      return toggleWrap(sel, "<u>", "</u>", "underline");
    case "strike":
      return toggleWrap(sel, "~~", "~~", "strike");
    case "highlight":
      return toggleWrap(sel, "==", "==", "highlight");
    case "code":
      return toggleWrap(sel, "`", "`", "code");
    case "codeBlock":
      return insertCodeBlock(sel);
    case "quote":
      return toggleQuote(sel);
    case "bullet":
      return toggleBulletList(sel);
    case "ordered":
      return toggleOrderedList(sel);
    case "link":
      return insertLink(sel);
    default:
      return clampRange(sel);
  }
}
