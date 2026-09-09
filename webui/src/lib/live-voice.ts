/**
 * Live voice conversation helpers (pure, unit-tested).
 *
 * The chat renders markdown; a voice does not. These helpers turn an
 * assistant reply into something a TTS engine can read like a colleague
 * talking, split it into synthesis-sized chunks, and map what the user said
 * back onto the question cards the agent raises.
 */

import { OTHER_CHOICE_ID, type ChoiceOption, type PendingChoice } from "@/lib/choices";
import type { InboundEvent } from "@/lib/types";

export type LiveVoiceState =
  | "off"
  | "starting"
  | "listening"
  | "muted"
  | "speaking"
  | "working"
  | "error";

export type LiveVoiceErrorKey =
  | "failed"
  | "noMicrophone"
  | "notConfigured"
  | "permission"
  | "planRequired"
  | "sessionLost"
  | "unsupported";

/** Longest spoken reply before the voice hands the rest over to the chat. */
export const LIVE_VOICE_MAX_SPEECH_CHARS = 1_400;
/** One synthesis request per chunk: short chunks mean the first words arrive fast. */
export const LIVE_VOICE_CHUNK_CHARS = 420;
/**
 * Opening chunk sizes. Every chunk is requested at once and synthesis time
 * grows with the text (measured: about 2.5 s plus up to 1.3 s per second of
 * speech when several run in parallel, at roughly 20 chars per second), so
 * each chunk may only be a little longer than everything played before it:
 * 80 chars start talking in 3-4 s, then 100, 180 and 320 follow without a
 * gap, and the rest goes out in 420-char chunks.
 */
export const LIVE_VOICE_CHUNK_SCHEDULE: readonly number[] = [80, 100, 180, 320];
/** A run at least this long earns a completion chime before the final words. */
export const LIVE_VOICE_LONG_RUN_MS = 20_000;

export interface SpeechTextStrings {
  /** Replaces a fenced code block, e.g. "code block, see the chat". */
  codeOmitted: string;
  /** Appended when the spoken text was cut, e.g. "The rest is in the chat." */
  restInChat: string;
  /** Replaces a bare URL, e.g. "link". */
  link: string;
  language?: string;
}

const SPOKEN_TECHNOLOGIES = new Set([
  "react", "reactjs", "vue", "angular", "svelte", "next.js", "nextjs", "node", "node.js",
  "sql", "nosql", "mysql", "postgres", "postgresql", "sqlite", "mongodb", "redis",
  "html", "css", "javascript", "typescript", "js", "ts", "python", "rust", "go",
  "java", "c#", "c++", "php", "api", "rest", "graphql", "http", "https", "stt", "tts",
]);
const TECH_JOIN_RE = /(?<![\w/.])([a-z][\w.+#-]*)\s*[/+]\s*([a-z][\w.+#-]*)(?![\w/.])/gi;

/** Normalize technical shorthand for speech without rewriting URLs or paths. */
export function spokenTechnicalText(text: string, language = "en"): string {
  const conjunction = ({ fr: "et", en: "and", es: "y", de: "und", it: "e", pt: "e", ar: "و" } as Record<string, string>)[language.split("-")[0]];
  if (!conjunction) return text;
  let current = text;
  for (let pass = 0; pass < 3; pass += 1) {
    const next = current.replace(TECH_JOIN_RE, (whole, left: string, right: string) => {
      const name = right.replace(/\.+$/, "");
      return SPOKEN_TECHNOLOGIES.has(left.toLowerCase()) && SPOKEN_TECHNOLOGIES.has(name.toLowerCase())
        ? `${left} ${conjunction} ${name}${right.slice(name.length)}` : whole;
    });
    if (next === current) break;
    current = next;
  }
  return current.replace(/(?<![\w/.-])(?:SQL|API|HTML|CSS|STT|TTS|HTTPS|HTTP)(?![\w/-]|\.[\w]|:\/\/)/gi,
    (term) => term.toUpperCase().split("").join(" "));
}

/** Progress written for the person, excluding tool logs and reasoning. */
export function spokenProgressFromEvent(event: InboundEvent): { text: string; turnId: string } | null {
  if (event.event !== "message" || event.kind !== "progress" || !event.text?.trim()
      || event.tool_events?.length || event.agent_ui) return null;
  return { text: event.text, turnId: event.turn_id ?? "" };
}

export function isSpokenStop(text: string): boolean {
  return /^(?:stop|arr[eê]te(?:-toi)?|annule(?: la t[aâ]che)?|cancel(?: the task)?|stop working)(?:\s*,?\s*(?:please|s['’]il te pla[iî]t|s['’]il vous pla[iî]t))?[.!?\s]*$/i.test(text.trim());
}

const FENCED_CODE_RE = /```[\s\S]*?```|~~~[\s\S]*?~~~/g;
const HTML_TAG_RE = /<\/?[a-zA-Z][^>]*>/g;
const IMAGE_RE = /!\[([^\]]*)\]\([^)]*\)/g;
const LINK_RE = /\[([^\]]+)\]\([^)]*\)/g;
const BARE_URL_RE = /\bhttps?:\/\/[^\s)]+|\bwww\.[^\s)]+/gi;
const INLINE_CODE_RE = /`([^`\n]+)`/g;
const HEADING_RE = /^\s{0,3}#{1,6}\s+/;
const BLOCKQUOTE_RE = /^\s*>+\s?/;
const LIST_MARKER_RE = /^\s*(?:[-*+•]|\d{1,3}[.)])\s+/;
const TASK_BOX_RE = /^\[( |x|X)\]\s*/;
const HORIZONTAL_RULE_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const EMPHASIS_RE = /(\*\*|__|\*|_|~~)(?=\S)([\s\S]*?\S)\1/g;
const SENTENCE_END_RE = /[.!?…:;]$/;

function stripEmphasis(text: string): string {
  let previous = "";
  let current = text;
  // Nested markers (***bold italic***) need more than one pass.
  while (previous !== current) {
    previous = current;
    current = current.replace(EMPHASIS_RE, "$2");
  }
  return current;
}

function tableRowToSpeech(line: string): string {
  const cells = line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim())
    .filter(Boolean);
  return cells.join(", ");
}

function endSentence(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return "";
  return SENTENCE_END_RE.test(trimmed) ? trimmed : `${trimmed}.`;
}

/**
 * Turn an assistant markdown reply into spoken prose.
 *
 * Code blocks become one short phrase, links keep their label, tables read
 * as comma lists, list markers and emphasis disappear, and every line ends
 * as a sentence so the voice pauses where the eye would. Long replies stop
 * at a sentence boundary and say that the rest is in the chat.
 */
export function speechTextFromMarkdown(
  markdown: string,
  strings: SpeechTextStrings,
  options: { maxChars?: number } = {},
): string {
  const maxChars = Math.max(80, options.maxChars ?? LIVE_VOICE_MAX_SPEECH_CHARS);
  if (!markdown || !markdown.trim()) return "";

  let codeMentioned = false;
  let text = markdown.replace(/\r\n?/g, "\n").replace(FENCED_CODE_RE, () => {
    if (codeMentioned) return "\n";
    codeMentioned = true;
    return `\n(${strings.codeOmitted})\n`;
  });
  text = text.replace(HTML_TAG_RE, " ");
  text = text.replace(IMAGE_RE, "$1");
  text = text.replace(LINK_RE, "$1");
  text = text.replace(BARE_URL_RE, strings.link);
  text = text.replace(INLINE_CODE_RE, "$1");

  const sentences: string[] = [];
  for (const rawLine of text.split("\n")) {
    let line = rawLine.trim();
    if (!line) continue;
    if (HORIZONTAL_RULE_RE.test(line) || TABLE_SEPARATOR_RE.test(line)) continue;
    if (line.includes("|") && line.split("|").length >= 3) {
      line = tableRowToSpeech(line);
    }
    line = line.replace(HEADING_RE, "").replace(BLOCKQUOTE_RE, "");
    line = line.replace(LIST_MARKER_RE, "").replace(TASK_BOX_RE, "");
    line = stripEmphasis(line);
    line = line.replace(/\s+/g, " ").trim();
    if (!line) continue;
    sentences.push(endSentence(line));
  }

  const full = spokenTechnicalText(sentences.join(" ").replace(/\s+/g, " ").trim(), strings.language);
  if (full.length <= maxChars) return full;

  const cutAt = lastSentenceBoundary(full, maxChars);
  const kept = full.slice(0, cutAt).trim();
  return `${endSentence(kept)} ${strings.restInChat}`.trim();
}

function lastSentenceBoundary(text: string, limit: number): number {
  const window = text.slice(0, limit);
  const floor = Math.floor(limit * 0.55);
  for (let index = window.length - 1; index >= floor; index -= 1) {
    if (/[.!?…]/.test(window[index]) && (index === window.length - 1 || window[index + 1] === " ")) {
      return index + 1;
    }
  }
  const space = window.lastIndexOf(" ");
  return space > floor ? space : limit;
}

/**
 * Split spoken text into synthesis chunks, cutting between sentences when
 * possible. The opening chunk is kept short (a sentence, or a clause of a long
 * one) so the voice starts within a few seconds; the rest is synthesized in
 * parallel while it plays.
 */
export function chunkSpeechText(
  text: string,
  maxChars = LIVE_VOICE_CHUNK_CHARS,
  schedule: readonly number[] = LIVE_VOICE_CHUNK_SCHEDULE,
): string[] {
  let rest = text.replace(/\s+/g, " ").trim();
  if (!rest) return [];
  const chunks: string[] = [];
  for (const size of schedule) {
    const limit = Math.min(size, maxChars);
    if (rest.length <= limit) {
      chunks.push(rest);
      return chunks;
    }
    const chunk = leadingChunk(rest, limit, maxChars);
    chunks.push(chunk);
    rest = rest.slice(chunk.length).trim();
    if (!rest) return chunks;
  }
  return [...chunks, ...chunkRest(rest, maxChars)];
}

const CHUNK_SENTENCE_END_RE = /[.!?…]/;
const CHUNK_CLAUSE_END_RE = /[,;:]/;

function boundaryAt(text: string, index: number, re: RegExp): boolean {
  return re.test(text[index]) && (index === text.length - 1 || text[index + 1] === " ");
}

/**
 * Cut the next chunk near ``softMax``: at the end of a sentence, else of a
 * clause. A sentence with neither in reach is not cut mid-word; it extends to
 * the first boundary before ``hardMax``, and only then falls back to a space.
 */
function leadingChunk(text: string, softMax: number, hardMax: number): string {
  const floor = Math.floor(softMax * 0.3);
  const last = Math.min(text.length - 1, softMax - 1);
  for (const re of [CHUNK_SENTENCE_END_RE, CHUNK_CLAUSE_END_RE]) {
    for (let index = last; index >= floor; index -= 1) {
      if (boundaryAt(text, index, re)) return text.slice(0, index + 1).trim();
    }
  }
  const reach = Math.min(text.length - 1, hardMax - 1);
  for (let index = last + 1; index <= reach; index += 1) {
    if (
      boundaryAt(text, index, CHUNK_SENTENCE_END_RE) ||
      boundaryAt(text, index, CHUNK_CLAUSE_END_RE)
    ) {
      return text.slice(0, index + 1).trim();
    }
  }
  const space = text.slice(0, softMax).lastIndexOf(" ");
  return text.slice(0, space > floor ? space : softMax).trim();
}

function chunkRest(normalized: string, maxChars: number): string[] {
  if (!normalized) return [];
  if (normalized.length <= maxChars) return [normalized];

  const pieces = normalized.split(/(?<=[.!?…])\s+/);
  const chunks: string[] = [];
  let current = "";
  const push = () => {
    if (current.trim()) chunks.push(current.trim());
    current = "";
  };
  for (const piece of pieces) {
    if (piece.length > maxChars) {
      push();
      for (const part of hardSplit(piece, maxChars)) chunks.push(part);
      continue;
    }
    if (current && current.length + 1 + piece.length > maxChars) push();
    current = current ? `${current} ${piece}` : piece;
  }
  push();
  return chunks;
}

function hardSplit(text: string, maxChars: number): string[] {
  const out: string[] = [];
  let rest = text;
  while (rest.length > maxChars) {
    const window = rest.slice(0, maxChars);
    const comma = Math.max(window.lastIndexOf(", "), window.lastIndexOf("; "));
    const space = window.lastIndexOf(" ");
    const at = comma > maxChars * 0.4 ? comma + 1 : space > maxChars * 0.4 ? space : maxChars;
    out.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) out.push(rest);
  return out;
}

/** Letter shown next to each option in the choice card (A, B, C...). */
export function choiceLetter(index: number): string {
  return String.fromCharCode(65 + index);
}

export interface ChoiceSpeechStrings {
  /** e.g. "Question" */
  question: string;
  /** e.g. "Option {{letter}}" -> rendered by the caller; here a function. */
  option: (letter: string) => string;
  /** e.g. "Recommended" */
  recommended: string;
  /** e.g. "You can also answer freely." */
  other: string;
}

/** What the voice says when the agent raises a question card. */
export function speechForChoice(choice: PendingChoice, strings: ChoiceSpeechStrings): string {
  const parts: string[] = [`${strings.question}: ${endSentence(choice.question)}`];
  choice.options.forEach((option, index) => {
    const detail = option.detail ? `, ${option.detail}` : "";
    const flag = option.recommended ? ` (${strings.recommended})` : "";
    parts.push(endSentence(`${strings.option(choiceLetter(index))}: ${option.label}${detail}${flag}`));
  });
  parts.push(endSentence(strings.other));
  return parts.join(" ");
}

export type ChoiceSpeechMatch =
  | { kind: "option"; optionId: string }
  | { kind: "skip" }
  | { kind: "other"; text: string };

const STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "it",
  "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "en", "au", "aux",
  "pour", "avec", "sur", "dans", "est", "ce", "cette", "ca", "je", "tu", "on", "que",
  "option", "choix", "reponse", "answer", "prends", "prend", "take", "choose", "choisis",
  "plutot", "rather", "please", "stp", "svp", "merci", "thanks", "ok", "okay", "oui", "yes",
]);

const ORDINALS: Array<[RegExp, number]> = [
  [/\b(?:premiere?|first|1(?:er|ere|st)?|numero un|number one)\b/, 0],
  [/\b(?:deuxieme|seconde?|second|2(?:e|eme|nd)?|deux|two)\b/, 1],
  [/\b(?:troisieme|third|3(?:e|eme|rd)?|trois|three)\b/, 2],
  [/\b(?:quatrieme|fourth|4(?:e|eme|th)?|quatre|four)\b/, 3],
  [/\b(?:cinquieme|fifth|5(?:e|eme|th)?|cinq|five)\b/, 4],
  [/\b(?:sixieme|sixth|6(?:e|eme|th)?|six)\b/, 5],
];

const SKIP_RE =
  /\b(?:skip|passe|passer|passons|comme tu veux|comme tu sens|a toi de voir|tu choisis|tu decides|you choose|you decide|whatever you think|up to you|recommand(?:e|ee)|recommended|ton choix|your call|par defaut|default)\b/;

export function normalizeSpeech(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokens(text: string): string[] {
  return normalizeSpeech(text)
    .split(" ")
    .filter((token) => token && !STOPWORDS.has(token));
}

// Words that wrap a lettered answer without changing it: "la reponse B, stp".
const LETTER_FRAME_WORDS = new Set([
  "la", "le", "l", "the", "option", "choix", "reponse", "answer", "lettre", "letter",
  "je", "i", "on", "prends", "prend", "take", "choose", "choisis", "part", "sur", "go",
  "with", "for", "pour", "avec", "stp", "svp", "please", "merci", "thanks", "s", "il",
  "te", "vous", "plait",
]);

function letterMatch(normalized: string, count: number): number | null {
  const meaningful = normalized.split(" ").filter((token) => token && !LETTER_FRAME_WORDS.has(token));
  if (meaningful.length !== 1 || !/^[a-h]$/.test(meaningful[0])) return null;
  const index = meaningful[0].charCodeAt(0) - 97;
  return index < count ? index : null;
}

function labelScore(transcript: string, option: ChoiceOption): number {
  const normalizedTranscript = normalizeSpeech(transcript);
  const normalizedLabel = normalizeSpeech(option.label);
  if (!normalizedLabel) return 0;
  if (normalizedTranscript === normalizedLabel) return 1;
  if (normalizedTranscript.includes(normalizedLabel) && normalizedLabel.length >= 3) return 0.95;
  if (normalizedLabel.includes(normalizedTranscript) && normalizedTranscript.length >= 4) return 0.85;
  const labelTokens = tokens(option.label);
  if (labelTokens.length === 0) return 0;
  const said = new Set(tokens(transcript));
  const hits = labelTokens.filter((token) => said.has(token)).length;
  return hits / labelTokens.length;
}

/**
 * Map a spoken answer to a pending question card.
 *
 * Letters ("B", "option C"), ordinals ("the second one", "la premiere"),
 * skip phrases ("as you prefer") and option labels are recognised; anything
 * else becomes a free-text answer, exactly like typing in the Other field.
 */
export function matchChoiceFromSpeech(
  transcript: string,
  choice: PendingChoice,
): ChoiceSpeechMatch | null {
  const raw = transcript.trim();
  if (!raw) return null;
  const normalized = normalizeSpeech(raw);
  if (!normalized) return null;
  const options = choice.options;
  const wordCount = normalized.split(" ").length;

  const letter = letterMatch(normalized, options.length);
  if (letter !== null) return { kind: "option", optionId: options[letter].id };

  // Ordinals only count in short answers ("la deuxieme", "the second one"):
  // inside a full sentence "deux" is usually a quantity, not a pick.
  if (wordCount <= 4) {
    if (/\b(?:derniere?|last)\b/.test(normalized) && options.length > 0) {
      return { kind: "option", optionId: options[options.length - 1].id };
    }
    for (const [pattern, index] of ORDINALS) {
      if (pattern.test(normalized) && index < options.length) {
        return { kind: "option", optionId: options[index].id };
      }
    }
  }
  if (wordCount <= 6 && SKIP_RE.test(normalized)) {
    return { kind: "skip" };
  }

  let best: { option: ChoiceOption; score: number } | null = null;
  for (const option of options) {
    const score = labelScore(raw, option);
    if (!best || score > best.score) best = { option, score };
  }
  if (best && best.score >= 0.6) return { kind: "option", optionId: best.option.id };

  return { kind: "other", text: raw };
}

export function choiceAnswerFromMatch(
  match: ChoiceSpeechMatch,
  choice: PendingChoice,
): { optionId: string; skipped: boolean; customText: string } {
  if (match.kind === "option") return { optionId: match.optionId, skipped: false, customText: "" };
  if (match.kind === "skip") {
    const fallback = choice.recommendedId || choice.options[0]?.id || "";
    return { optionId: fallback, skipped: true, customText: "" };
  }
  return { optionId: OTHER_CHOICE_ID, skipped: false, customText: match.text };
}

// Whisper-family models invent these on silence or breath noise; sending
// them as chat messages would start a turn the user never asked for.
const STT_NOISE_RE =
  /^(?:sous[- ]titres? (?:realises? )?par (?:la communaute )?(?:d )?amara ?org|thank you\.?|thanks for watching\.?|merci d avoir regarde\.?|you\.?|bye\.?|\.+|…)$/;

// Back-channel words. They are what STT hears in a breath or a key click, and
// what a person says while listening. Only worth a turn when the assistant just
// asked something (then "oui" / "yeah" is the answer).
const ACK_WORDS = new Set([
  "yeah", "yes", "yep", "no", "nope", "ok", "okay", "oh", "ah", "uh", "um", "hmm", "hm", "mm", "mhm",
  "oui", "non", "ouais", "euh", "hum", "bah", "ben", "bon", "ca", "so", "and", "et", "voila",
  "merci", "thanks", "d", "accord", "right", "sure", "cool", "super", "great", "nice", "hein",
]);

function acknowledgmentOnly(normalized: string): boolean {
  const words = normalized.split(" ").filter(Boolean);
  return words.length > 0 && words.length <= 3 && words.every((word) => ACK_WORDS.has(word));
}

/** True when the assistant's last words call for a yes / no style reply. */
export function answerExpected(assistantText: string | undefined): boolean {
  if (!assistantText) return false;
  return /[?？]\s*$/.test(assistantText.trim());
}

/**
 * Key used to speak a given text once per session. Message ids are not stable
 * (streaming placeholder swapped for the final message, history reloads), so
 * the spoken text itself is the identity.
 */
export function speechDedupeKey(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

/**
 * True when a transcript is worth sending: not an STT hallucination, not a
 * stray syllable, and not a bare "yeah" / "hmm" unless an answer is expected.
 */
export function transcriptIsMeaningful(
  text: string,
  options: { answerExpected?: boolean } = {},
): boolean {
  const normalized = normalizeSpeech(text);
  if (!normalized) return false;
  if (STT_NOISE_RE.test(normalized)) return false;
  const letters = normalized.replace(/[^a-z0-9]/g, "");
  if (letters.length < 2) return false;
  if (!options.answerExpected && acknowledgmentOnly(normalized)) return false;
  return true;
}

/** Whether a finished run deserves the "I am done" chime and OS notification. */
export function completionCueWanted(
  runDurationMs: number | null,
  threshold = LIVE_VOICE_LONG_RUN_MS,
): boolean {
  return runDurationMs !== null && runDurationMs >= threshold;
}

/** Overall status shown in the voice bar, derived from the session pieces. */
export function liveVoiceStateFrom(input: {
  enabled: boolean;
  sessionState: "idle" | "starting" | "listening" | "error";
  muted: boolean;
  ttsPlaying: boolean;
  agentWorking: boolean;
  error: LiveVoiceErrorKey | null;
}): LiveVoiceState {
  if (input.error) return "error";
  if (!input.enabled) return "off";
  if (input.sessionState === "starting") return "starting";
  if (input.sessionState === "error") return "error";
  if (input.ttsPlaying) return "speaking";
  if (input.agentWorking) return "working";
  if (input.muted) return "muted";
  return "listening";
}
