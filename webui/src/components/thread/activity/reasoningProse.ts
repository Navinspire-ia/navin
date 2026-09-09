// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cn } from "@/lib/utils";

/**
 * OpenCode-style reasoning: muted body (not near-white), dusty violet
 * bold/headings, quiet emerald code. Pair with `.reasoning-prose` in
 * globals.css so this beats `dark:prose-invert`.
 */
export const REASONING_PROSE_CLASS = cn(
  "reasoning-prose antialiased min-w-0 text-[13.5px] leading-7 [text-wrap:pretty]",
  "text-muted-foreground",
  "prose-p:my-4 prose-p:leading-7 prose-li:my-1.5 prose-ul:my-4 prose-ol:my-4",
  "prose-headings:mt-4 prose-headings:mb-2 prose-headings:font-medium",
  "prose-h1:text-[15px] prose-h2:text-[14px] prose-h3:text-[13.5px] prose-h4:text-[13px]",
  "prose-pre:my-3",
);

export const REASONING_RAIL_CLASS =
  "border-l-2 border-[hsl(var(--composer-ask))]/40 pl-3";

export const REASONING_LABEL_CLASS =
  "text-[hsl(var(--composer-ask-fg))] dark:text-[hsl(var(--composer-ask))]";

export function mergeReasoningParts(parts: string[]): string {
  return parts.map((part) => part.trim()).filter(Boolean).join("\n\n");
}

function stripReasoningMarkup(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function clipReasoningSummary(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  const cut = value.slice(0, maxChars - 1);
  const atSpace = cut.lastIndexOf(" ");
  const floor = Math.floor(maxChars * 0.45);
  return `${(atSpace > floor ? cut.slice(0, atSpace) : cut).trim()}…`;
}

export const REASONING_SUMMARY_MAX_CHARS = 480;
const REASONING_SUMMARY_MIN_CHARS = 280;

/**
 * Collapsed resume of a thinking trace: latest paragraphs, walking backward
 * until the text is long enough to read without expanding.
 */
export function reasoningSummary(
  text: string,
  maxChars = REASONING_SUMMARY_MAX_CHARS,
): string {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((part) => stripReasoningMarkup(part))
    .filter((part) => part.length > 40);
  if (paragraphs.length === 0) {
    return clipReasoningSummary(stripReasoningMarkup(text), maxChars);
  }
  const picked: string[] = [];
  for (let i = paragraphs.length - 1; i >= 0; i -= 1) {
    const candidate = [paragraphs[i], ...picked];
    const joined = candidate.join(" ");
    if (picked.length > 0 && joined.length > maxChars) break;
    picked.unshift(paragraphs[i]);
    if (joined.length >= Math.min(REASONING_SUMMARY_MIN_CHARS, maxChars)) break;
    if (picked.length >= 4) break;
  }
  return clipReasoningSummary(picked.join(" "), maxChars);
}
