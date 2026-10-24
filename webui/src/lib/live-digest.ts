// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The Live behaves like a colleague: a plan, a review, a security report or a
 * long answer is never read out in full. The voice says what it is ("a plan in
 * 6 steps") and offers to sum it up; a yes asks the agent for a spoken summary.
 */

export type DigestKind = "plan" | "review" | "security" | "recommendation" | "long";

export interface LiveDigest {
  kind: DigestKind;
  /** Top-level steps / points (0 when the message has no list). */
  count: number;
}

const FENCED = /```[\s\S]*?```|~~~[\s\S]*?~~~/g;
const TOP_ITEM = /^ {0,3}(?:\d{1,3}[.)]|[-*+•])\s+\S/gm;
const HEADING = /^\s{0,3}#{1,6}\s+(.+)$/gm;
const KINDS: Array<[DigestKind, RegExp]> = [
  ["security", /\b(?:s[ée]curit[ée]|security|vuln[ée]rabilit|vulnerab|owasp|cve-\d|injection|xss|csrf|secrets?\b)/i],
  ["review", /\b(?:revue|review|relecture|code review|findings?|constats?)\b/i],
  ["plan", /\b(?:plan|[ée]tapes?|steps?|roadmap|blueprint|phases?|milestones?|jalons?)\b/i],
  ["recommendation", /\b(?:recommand|recommend|suggestions?|conseils?|next steps|prochaines [ée]tapes)/i],
];
/** Below this, a message is read as it is. */
export const DIGEST_MIN_CHARS = 700;

export function liveDigest(content: string | null | undefined): LiveDigest | null {
  const text = String(content ?? "").replace(FENCED, " ");
  const plain = text.replace(/\s+/g, " ").trim();
  if (!plain) return null;
  const count = (text.match(TOP_ITEM) ?? []).length;
  const headings = [...text.matchAll(HEADING)].map((match) => match[1]).join(" \n ");
  // Headings and the opening say what the message is; the body may mention
  // anything ("a step", "a plan") in passing.
  const signature = `${headings} \n ${plain.slice(0, 280)}`;
  const kind = KINDS.find(([, pattern]) => pattern.test(signature))?.[0] ?? "long";
  const structured = kind !== "long" && count >= 3;
  if (!structured && plain.length < DIGEST_MIN_CHARS && count < 5) return null;
  return structured ? { kind, count } : { kind: "long", count: 0 };
}

/** The last finished assistant message, the one the Live offers to sum up. */
export function lastAssistantText(
  messages: ReadonlyArray<{ role: string; kind?: string; isStreaming?: boolean; content: string }>,
): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "user") return null;
    if (message.role !== "assistant" || message.kind === "trace" || message.isStreaming) continue;
    return message.content;
  }
  return null;
}

const YES = /^(?:oui|ouais|ok|okay|d'?accord|vas[- ]?y|go|yes|yeah|yep|sure|please|s'?il te pla[iî]t|volontiers|carr[ée]ment|bien s[uû]r|r[ée]sume|r[ée]sume[- ]moi|explique|explique[- ]moi|summari[sz]e|tell me|نعم|أجل|حسنا|تفضل|لخص)(?=$|[\s,.!?،؟])/i;
const NO = /^(?:non|nan|no|nope|pas besoin|pas maintenant|plus tard|c'?est bon|not now|no thanks|لا|ليس الآن)(?=$|[\s,.!?،؟])/i;

/** "oui" / "vas-y" / "résume" accept the offer, "non" / "plus tard" decline it. */
export function offerAnswer(raw: string): "yes" | "no" | null {
  const text = raw.trim().toLowerCase().replace(/^[\s,.!?]+/, "");
  if (NO.test(text)) return "no";
  if (YES.test(text)) return "yes";
  return null;
}
