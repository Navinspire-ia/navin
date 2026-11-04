// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The Live behaves like a colleague: a plan, a review, a security report or a
 * long answer is never read out in full. The realtime voice is given the
 * answer and says its gist at once (liveBriefing); the pipeline, which only
 * reads text aloud, says the answer's own opening (answerOpening), what it is
 * ("a plan in 6 steps") and offers the rest, and a yes asks the agent for a
 * spoken summary.
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

/** Longest answer handed to the voice to brief; the rest stays in the chat. */
export const BRIEFING_MAX_CHARS = 6_000;

const BRIEFED: Record<DigestKind, (count: number) => string> = {
  plan: (count) => `a plan in ${count} steps`,
  review: (count) => `a review with ${count} findings`,
  security: (count) => `a security check with ${count} points`,
  recommendation: (count) => `${count} recommendations`,
  long: () => "a long answer",
};

/**
 * What the realtime voice gets when the agent finishes a plan, a review or a
 * long answer: the answer itself (code left out, bounded) and how to brief it.
 * Offered a summary instead, the voice only said "the agent found the answer,
 * but it is a bit long" (2026-11-04). Nova briefs 6,000 characters in half a
 * second (measured on the managed route).
 */
export function liveBriefing(digest: LiveDigest, content: string): string {
  let text = String(content ?? "").replace(FENCED, " [code in the chat] ").replace(/\n{3,}/g, "\n\n").trim();
  if (text.length > BRIEFING_MAX_CHARS) text = `${text.slice(0, BRIEFING_MAX_CHARS).trimEnd()}\n[the rest is in the chat]`;
  return [
    `The agent finished ${BRIEFED[digest.kind](digest.count)}, shown in full in the chat. Its text follows between the markers.`,
    "Tell the user its gist now, in your own words and in two to four short spoken sentences: the outcome first, then what is critical, risky or failed, then what the agent needs from the user.",
    "Never read it word for word, never read lists, code, file names, paths or logs, and do not offer to sum it up: this is the summary.",
    "End with the next step you propose, in one sentence.",
    "<<<",
    text,
    ">>>",
  ].join("\n");
}

/** Longest opening said before the offer of the rest (pipeline). */
export const OPENING_MAX_CHARS = 280;
/** Lines that are structure (heading, list, table, quote, code, rule), not the answer's words. */
const STRUCTURE = /^(?:#{1,6}\s|\d{1,3}[.)]\s|[-*+•]\s|\||>|```|~~~|(?:[-*_]\s*){3,}$)/;

/** Sentences of one line of prose; "2.4.7" or "e.g.x" is not a sentence end. */
function splitSentences(prose: string): string[] {
  const out: string[] = [];
  let start = 0;
  for (let index = 0; index < prose.length; index += 1) {
    if (/[.!?…]/.test(prose[index]) && (index + 1 === prose.length || prose[index + 1] === " ")) {
      out.push(prose.slice(start, index + 1).trim());
      start = index + 1;
    }
  }
  const tail = prose.slice(start).trim();
  if (tail) out.push(tail);
  return out.filter(Boolean);
}

/**
 * The answer's own first words for the pipeline voice: its first paragraph of
 * prose, two sentences at most. Said before the offer of the rest instead of
 * "Done. It is a long answer", which told the user nothing (2026-11-04).
 */
export function answerOpening(content: string | null | undefined): string | null {
  const paragraph: string[] = [];
  for (const raw of String(content ?? "").replace(FENCED, "\n").split("\n")) {
    const line = raw.trim();
    if (!line || STRUCTURE.test(line)) {
      if (paragraph.length) break;
      continue;
    }
    paragraph.push(line);
  }
  const prose = paragraph.join(" ").replace(/\s+/g, " ").trim();
  if (!prose) return null;
  let opening = "";
  for (const sentence of splitSentences(prose).slice(0, 2)) {
    const next = opening ? `${opening} ${sentence}` : sentence;
    if (opening && next.length > OPENING_MAX_CHARS) break;
    opening = next;
  }
  if (opening.length > OPENING_MAX_CHARS) opening = `${opening.slice(0, OPENING_MAX_CHARS).replace(/\s+\S*$/, "")}...`;
  return opening;
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
