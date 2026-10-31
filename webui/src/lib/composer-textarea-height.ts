// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Auto-grow for the chat composer textarea.
 *
 * The textarea's height follows its `scrollHeight`, capped. That measure is
 * only meaningful once the element has a real width: while the chat column is
 * collapsed or still animating open (Studio <-> Code, chat toggled back in),
 * the placeholder wraps into many lines inside a few pixels and `scrollHeight`
 * reports a box 200 px tall. Since the value has not changed nothing re-fits
 * it, and the user lands on a huge empty input. `fitTextareaHeight` refuses to
 * measure an element without width, and `observeTextareaWidth` re-fits when the
 * width settles.
 */

export const COMPOSER_TEXTAREA_MAX_PX = 260;

/**
 * Below this content width the textarea is not laid out for real (collapsed
 * column, `width: 0` shell, mid-transition). No usable composer is narrower.
 */
export const COMPOSER_MIN_MEASURABLE_WIDTH_PX = 48;

type Measurable = {
  clientWidth: number;
  scrollHeight: number;
  style: { height: string };
  parentElement?: { offsetHeight: number; style: { minHeight: string } } | null;
  /** Real textareas: an empty one is measured without its placeholder. */
  value?: string;
  placeholder?: string;
};

/**
 * Width available to the text itself. `clientWidth` includes the horizontal
 * padding, so a `width: 0` box still reports ~28 px; subtract the padding when
 * computed styles are available (they are not in unit tests).
 */
export function textareaContentWidth(el: Measurable): number {
  const getStyle = (globalThis as { getComputedStyle?: (e: Element) => CSSStyleDeclaration })
    .getComputedStyle;
  if (
    typeof getStyle !== "function" ||
    typeof Element === "undefined" ||
    !(el instanceof Element)
  ) {
    return el.clientWidth;
  }
  const cs = getStyle(el);
  const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
  return el.clientWidth - pad;
}

/**
 * Fit *el* to its content. Returns the applied height in px, or `null` when
 * the element has no usable layout width yet (nothing is written in that case).
 */
export function fitTextareaHeight(
  el: Measurable | null | undefined,
  max: number = COMPOSER_TEXTAREA_MAX_PX,
): number | null {
  if (!el) return null;
  if (textareaContentWidth(el) < COMPOSER_MIN_MEASURABLE_WIDTH_PX) return null;
  // WebKit (macOS Safari and the Desktop app) paints the collapsed
  // `height: auto` box between the two writes, so the composer jumped on
  // every keystroke. The parent keeps its height while the textarea is
  // measured, so nothing around it moves.
  const parent = el.parentElement ?? null;
  const previousMin = parent ? parent.style.minHeight : "";
  if (parent) parent.style.minHeight = `${parent.offsetHeight}px`;
  const previous = el.style.height;
  // Chromium (WebView2) and WebKit count a wrapped placeholder in
  // `scrollHeight`. The placeholder changes while the agent works, so the
  // empty composer grew a line, shrank at the first key and grew again once
  // the message was queued: the whole chat moved while typing. An empty
  // composer is one line tall whatever its placeholder says.
  const placeholder = el.value === "" && el.placeholder ? el.placeholder : null;
  if (placeholder !== null) el.placeholder = "";
  el.style.height = "auto";
  const next = Math.min(el.scrollHeight, max);
  if (placeholder !== null) el.placeholder = placeholder;
  const nextPx = `${next}px`;
  el.style.height = previous === nextPx ? previous : nextPx;
  if (parent) parent.style.minHeight = previousMin;
  return next;
}

/**
 * Re-fit the textarea whenever its width changes (column opening, window
 * resize, chat panel toggled). Returns a disposer. No-op where ResizeObserver
 * is unavailable.
 */
export function observeTextareaWidth(
  el: (Measurable & Element) | null | undefined,
  refit: () => void,
): () => void {
  if (!el || typeof ResizeObserver === "undefined") return () => {};
  let lastWidth = el.clientWidth;
  const observer = new ResizeObserver(() => {
    const width = el.clientWidth;
    if (width === lastWidth) return;
    lastWidth = width;
    refit();
  });
  observer.observe(el);
  return () => observer.disconnect();
}
