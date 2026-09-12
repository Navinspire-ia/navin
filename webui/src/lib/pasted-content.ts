// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Collapse long pasted text the way Cursor chips a paste.
 *
 * The composer and the bubble show ``[Pasted Content 11448 chars]``.
 * The full body stays in a sidecar map and is expanded again before send.
 */

export const PASTED_CONTENT_THRESHOLD = 1000;
export const PASTE_PREFIX_KEEP_CHARS = 240;
export const PASTE_TOKEN_RE = /\[Pasted Content (\d+) chars(?: #([A-Za-z0-9]+))?\]/g;

export type PastedContentMap = Record<string, string>;

export function pastedContentLabel(chars: number, suffix?: string): string {
  return suffix
    ? `[Pasted Content ${chars} chars #${suffix}]`
    : `[Pasted Content ${chars} chars]`;
}

export function shouldCollapsePastedText(
  text: string,
  threshold = PASTED_CONTENT_THRESHOLD,
): boolean {
  return (text ?? "").length >= threshold;
}

export function allocatePasteToken(chars: number, existing: PastedContentMap): string {
  const base = pastedContentLabel(chars);
  if (!(base in existing)) return base;
  let index = 2;
  while (true) {
    const token = pastedContentLabel(chars, String(index));
    if (!(token in existing)) return token;
    index += 1;
  }
}

export function expandPastedContent(
  display: string,
  pastes?: PastedContentMap | null,
): string {
  if (!display || !pastes || Object.keys(pastes).length === 0) return display ?? "";
  PASTE_TOKEN_RE.lastIndex = 0;
  return display.replace(PASTE_TOKEN_RE, (token) => pastes[token] ?? token);
}

export function splitLongUserText(
  text: string,
  threshold = PASTED_CONTENT_THRESHOLD,
  prefixLimit = PASTE_PREFIX_KEEP_CHARS,
): { prefix: string; rest: string | null } {
  const raw = text ?? "";
  if (raw.length < threshold) return { prefix: raw, rest: null };
  let paragraph = raw;
  let rest = "";
  const double = raw.indexOf("\n\n");
  if (double >= 0) {
    paragraph = raw.slice(0, double);
    rest = raw.slice(double + 2);
  } else {
    const single = raw.indexOf("\n");
    if (single >= 0) {
      paragraph = raw.slice(0, single);
      rest = raw.slice(single + 1);
    }
  }
  if (rest && paragraph.length <= prefixLimit && rest.length >= threshold) {
    return { prefix: paragraph, rest };
  }
  return { prefix: "", rest: raw };
}

export function collapseTextForComposer(
  text: string,
  pastes: PastedContentMap = {},
): { text: string; pastes: PastedContentMap } {
  const store = { ...pastes };
  const raw = text ?? "";
  PASTE_TOKEN_RE.lastIndex = 0;
  if (PASTE_TOKEN_RE.test(raw)) return { text: raw, pastes: store };
  if (!shouldCollapsePastedText(raw)) return { text: raw, pastes: store };
  const { prefix, rest } = splitLongUserText(raw);
  const body = rest ?? raw;
  const token = allocatePasteToken(body.length, store);
  store[token] = body;
  return {
    text: prefix ? `${prefix}\n${token}` : token,
    pastes: store,
  };
}

export function usedPastedContent(
  text: string,
  pastes: PastedContentMap,
): PastedContentMap {
  const used: PastedContentMap = {};
  for (const [token, body] of Object.entries(pastes)) {
    if (text.includes(token)) used[token] = body;
  }
  return used;
}

export function splitPastedContentSegments(
  text: string,
): Array<{ kind: "text" | "paste"; text: string }> {
  if (!text) return [];
  const parts: Array<{ kind: "text" | "paste"; text: string }> = [];
  const matcher = new RegExp(PASTE_TOKEN_RE.source, "g");
  let cursor = 0;
  let match: RegExpExecArray | null = matcher.exec(text);
  while (match) {
    if (match.index > cursor) {
      parts.push({ kind: "text", text: text.slice(cursor, match.index) });
    }
    parts.push({ kind: "paste", text: match[0] });
    cursor = match.index + match[0].length;
    match = matcher.exec(text);
  }
  if (cursor < text.length) {
    parts.push({ kind: "text", text: text.slice(cursor) });
  }
  return parts;
}

/** Recover a sudden dump (WebKitGTK often skips the paste event). */
export function extractInsertedText(previous: string, next: string): string | null {
  const before = previous ?? "";
  const after = next ?? "";
  if (after.length < before.length + PASTED_CONTENT_THRESHOLD) return null;
  if (after.startsWith(before)) return after.slice(before.length);
  if (after.endsWith(before)) return after.slice(0, after.length - before.length);
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) {
    start += 1;
  }
  let endBefore = before.length;
  let endAfter = after.length;
  while (
    endBefore > start
    && endAfter > start
    && before[endBefore - 1] === after[endAfter - 1]
  ) {
    endBefore -= 1;
    endAfter -= 1;
  }
  const inserted = after.slice(start, endAfter);
  return inserted.length >= PASTED_CONTENT_THRESHOLD ? inserted : null;
}

export function collapseUserDisplayLabel(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return "";
  const { prefix, rest } = splitLongUserText(trimmed);
  if (!rest) return trimmed;
  const chip = pastedContentLabel(rest.length);
  return prefix ? `${prefix} ${chip}` : chip;
}
