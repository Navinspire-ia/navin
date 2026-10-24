// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cn } from "@/lib/utils";

/**
 * OpenCode-style reasoning: muted body (not near-white), dusty violet
 * bold/headings, quiet emerald code. Pair with `.reasoning-prose` in
 * globals.css so this beats `dark:prose-invert`.
 */
export const REASONING_PROSE_CLASS = cn(
  "reasoning-prose antialiased min-w-0 text-[14.5px] leading-7 [text-wrap:pretty]",
  "text-muted-foreground",
  "prose-p:my-4 prose-p:leading-7 prose-li:my-1.5 prose-ul:my-4 prose-ol:my-4",
  "prose-headings:mt-4 prose-headings:mb-2 prose-headings:font-medium",
  "prose-h1:text-[17px] prose-h2:text-[16px] prose-h3:text-[15.5px] prose-h4:text-[15px]",
  "prose-pre:my-3",
);

export const REASONING_RAIL_CLASS =
  "border-s-2 border-[hsl(var(--composer-ask))]/40 ps-3";

export const REASONING_LABEL_CLASS =
  "text-[hsl(var(--composer-ask-fg))] dark:text-[hsl(var(--composer-ask))]";

export function mergeReasoningParts(parts: string[]): string {
  return parts.map((part) => part.trim()).filter(Boolean).join("\n\n");
}

/**
 * Markdown noise out, line breaks kept: "Let me check:\n1. ...\n2. ..." must
 * still read as a list in the collapsed preview, not as one run-on line.
 */
function stripReasoningMarkup(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^#{1,6}\s+/gm, "")
    // Emphasis markers out; list bullets ("* item") and snake_case stay.
    .replace(/(^|\s)\*(?=\s)/gm, "$1\u2022")
    .replace(/\*\*|__(?=\S)|(?<=\S)__|`/g, "")
    .replace(/(^|[\s(])[*_](?=\S)|(?<=\S)[*_](?=[\s).,:;!?]|$)/gm, "$1")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

const LIST_LINE_RE = /^(?:\d+[.)]|[-+•])\s/m;

/** Short paragraphs that still carry meaning: list items and their lead-in. */
function keepsShortParagraph(paragraph: string): boolean {
  return LIST_LINE_RE.test(paragraph) || /:$/.test(paragraph);
}

function clipReasoningSummary(value: string, maxChars: number, tail = false): string {
  if (value.length <= maxChars) return value;
  if (tail) {
    const cut = value.slice(value.length - (maxChars - 1));
    const atSpace = cut.search(/\s/);
    const ceiling = Math.floor(maxChars * 0.55);
    return `…${(atSpace >= 0 && atSpace < ceiling ? cut.slice(atSpace) : cut).trim()}`;
  }
  const cut = value.slice(0, maxChars - 1);
  const atSpace = Math.max(cut.lastIndexOf(" "), cut.lastIndexOf("\n"));
  const floor = Math.floor(maxChars * 0.45);
  return `${(atSpace > floor ? cut.slice(0, atSpace) : cut).trim()}…`;
}

export const REASONING_SUMMARY_MAX_CHARS = 480;
const REASONING_SUMMARY_MIN_CHARS = 280;

/**
 * Collapsed resume of a thinking trace: latest paragraphs, walking backward
 * until the text is long enough to read without expanding. While the trace is
 * still streaming, an over-long paragraph is clipped from its head so the
 * newest words stay visible instead of freezing on the paragraph's start.
 */
export function reasoningSummary(
  text: string,
  maxChars = REASONING_SUMMARY_MAX_CHARS,
  streaming = false,
): string {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((part) => stripReasoningMarkup(part))
    .filter((part) => part.length > 40 || (part.length > 0 && keepsShortParagraph(part)));
  if (paragraphs.length === 0) {
    return clipReasoningSummary(stripReasoningMarkup(text), maxChars, streaming);
  }
  const picked: string[] = [];
  for (let i = paragraphs.length - 1; i >= 0; i -= 1) {
    const candidate = [paragraphs[i], ...picked];
    const joined = candidate.join("\n");
    if (picked.length > 0 && joined.length > maxChars) break;
    picked.unshift(paragraphs[i]);
    if (joined.length >= Math.min(REASONING_SUMMARY_MIN_CHARS, maxChars)) break;
    if (picked.length >= 4) break;
  }
  return clipReasoningSummary(picked.join("\n"), maxChars, streaming);
}
