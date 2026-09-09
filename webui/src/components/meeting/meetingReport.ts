// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Local analysis and document builders for the Meeting desk.
 *
 * Everything here runs in the browser with no model call, so a meeting always
 * has a readable report: metadata, speaker turns, detected decisions and
 * actions, then the model summary on top when one exists. The same structure
 * feeds the on-screen preview, the Markdown export, and the printable HTML.
 */

export type MeetingSource = {
  title: string;
  createdAt: string;
  updatedAt: string;
  transcript: string;
  notes: string;
  summary: string;
  speakers: string[];
  templateName: string;
  calendarUid?: string;
  chatLog?: string;
  /** Real start of the meeting (ISO). Falls back to createdAt when absent. */
  startedAt?: string | null;
  /** Accumulated audio duration in seconds, measured, not estimated. */
  durationSec?: number | null;
};

export type MeetingStats = {
  words: number;
  characters: number;
  sentences: number;
  turns: number;
  speakers: number;
  spokenMinutes: number;
  readingMinutes: number;
  /** Measured duration in seconds (audio or last timecode); null = unknown. */
  durationSec: number | null;
  language: "fr" | "en" | "unknown";
};

export type TranscriptTurn = {
  id: string;
  speaker: string | null;
  text: string;
  /** Timecode of the turn ("03:15" or "1:02:40") when the capture knows it. */
  time: string | null;
};

export type HighlightKind =
  | "decision"
  | "action"
  | "proposal"
  | "opinion"
  | "risk"
  | "question";

export type Highlight = {
  id: string;
  kind: HighlightKind;
  text: string;
  owner: string | null;
  due: string | null;
};

export type ReportSection = {
  id: string;
  title: string;
  body: string;
};

export type MeetingReport = {
  source: MeetingSource;
  stats: MeetingStats;
  turns: TranscriptTurn[];
  highlights: Highlight[];
  sections: ReportSection[];
  /** True when there is not enough material to say anything useful. */
  thin: boolean;
};

export type ReportLabels = {
  report: string;
  metadata: string;
  summary: string;
  highlights: string;
  transcript: string;
  notes: string;
  speakers: string;
  questions: string;
  template: string;
  date: string;
  created: string;
  updated: string;
  words: string;
  duration: string;
  language: string;
  calendar: string;
  kind: string;
  detail: string;
  owner: string;
  due: string;
  decision: string;
  action: string;
  proposal: string;
  opinion: string;
  risk: string;
  question: string;
  unassigned: string;
  noDue: string;
  none: string;
  generatedBy: string;
};

const WORDS_PER_MINUTE_SPOKEN = 150;
const WORDS_PER_MINUTE_READ = 220;
const MAX_PARAGRAPH_SENTENCES = 3;
const MAX_PARAGRAPH_CHARS = 340;
/** Below this, a transcript cannot support minutes and we say so. */
const THIN_WORD_COUNT = 25;

const DECISION_HINTS = [
  "on décide",
  "on decide",
  "décision",
  "decision",
  "on part sur",
  "on valide",
  "validé",
  "acté",
  "on retient",
  "d'accord pour",
  "we decided",
  "we agreed",
  "let's go with",
  "approved",
  "sign off",
  "signed off",
];

const ACTION_HINTS = [
  "je m'occupe",
  "je vais",
  "on doit",
  "il faut",
  "à faire",
  "a faire",
  "tu peux",
  "peux-tu",
  "prépare",
  "envoie",
  "relance",
  "todo",
  "action item",
  "i will",
  "we need to",
  "we should",
  "please",
  "can you",
  "will send",
  "follow up",
  "next step",
];

const RISK_HINTS = [
  "risque",
  "problème",
  "probleme",
  "bloqué",
  "bloquant",
  "retard",
  "attention",
  "danger",
  "blocker",
  "blocked",
  "risk",
  "issue",
  "concern",
  "delay",
];

const PROPOSAL_HINTS = [
  "je propose",
  "on propose",
  "proposition",
  "on pourrait",
  "pourquoi ne pas",
  "je suggère",
  "je suggere",
  "suggestion",
  "et si on",
  "une piste serait",
  "i suggest",
  "i propose",
  "we could",
  "what if we",
  "how about",
  "proposal",
  "my suggestion",
];

const OPINION_HINTS = [
  "à mon avis",
  "a mon avis",
  "je pense que",
  "je crois que",
  "selon moi",
  "je trouve que",
  "pour ma part",
  "personnellement",
  "je ne suis pas convaincu",
  "je suis d'accord",
  "je ne suis pas d'accord",
  "in my opinion",
  "i think that",
  "i believe",
  "my view",
  "personally",
  "i agree",
  "i disagree",
];

const DUE_PATTERNS: RegExp[] = [
  /\b(?:d'ici|avant le|pour le|au plus tard le)\s+[^.,;!?]{2,32}/i,
  /\b(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)(?:\s+prochain)?\b/i,
  /\b(?:demain|après-demain|apres-demain|cette semaine|semaine prochaine|fin de semaine|fin du mois)\b/i,
  /\bby\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|end of (?:the )?(?:week|month))\b/i,
  /\b(?:tomorrow|next week|end of (?:the )?(?:week|month))\b/i,
  /\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b/,
];

const FRENCH_HINTS = [
  " le ",
  " la ",
  " les ",
  " des ",
  " une ",
  " est ",
  " nous ",
  " vous ",
  " pour ",
  " avec ",
  "ç",
  "é",
  "è",
  "ê",
  "à",
];

const ENGLISH_HINTS = [
  " the ",
  " and ",
  " is ",
  " we ",
  " you ",
  " for ",
  " with ",
  " that ",
  " this ",
  " have ",
];

function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/u).length;
}

function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?…])\s+|\n+/u)
    .map((part) => part.trim())
    .filter(Boolean);
}

function detectLanguage(text: string): MeetingStats["language"] {
  const sample = ` ${text.slice(0, 4000).toLowerCase()} `;
  if (!sample.trim()) return "unknown";
  const fr = FRENCH_HINTS.reduce(
    (total, hint) => total + (sample.split(hint).length - 1),
    0,
  );
  const en = ENGLISH_HINTS.reduce(
    (total, hint) => total + (sample.split(hint).length - 1),
    0,
  );
  if (fr === en) return "unknown";
  return fr > en ? "fr" : "en";
}

/** `[mm:ss]` or `[h:mm:ss]` at the start of a captured transcript line. */
const TIMECODE_PREFIX = /^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*/u;

/** Milliseconds since the meeting start, as a `[mm:ss]` transcript prefix. */
export function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function timecodeToSeconds(time: string): number {
  const parts = time.split(":").map((part) => Number.parseInt(part, 10));
  if (parts.some((part) => Number.isNaN(part))) return 0;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

/** Split an optional leading timecode off a transcript line. */
export function splitTimecode(line: string): { time: string | null; rest: string } {
  const match = line.match(TIMECODE_PREFIX);
  if (!match) return { time: null, rest: line };
  return { time: match[1], rest: line.slice(match[0].length) };
}

/** A leading `Name:` is a speaker label only when it looks like one. */
function speakerLabel(candidate: string, known: string[]): string | null {
  const name = candidate.trim();
  if (!name || name.length > 40) return null;
  if (/^speaker\s*\d+$/i.test(name)) return name;
  if (known.some((row) => row.toLowerCase() === name.toLowerCase()))
    return name;
  const words = name.split(/\s+/u);
  if (words.length > 3) return null;
  if (!/^[\p{Lu}]/u.test(name)) return null;
  if (/[.!?]$/.test(name)) return null;
  return name;
}

/** Split a transcript into labelled turns, or into readable paragraphs. */
export function buildTurns(
  transcript: string,
  speakers: string[],
): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];
  let buffer: string[] = [];
  let bufferChars = 0;
  let bufferTime: string | null = null;
  let index = 0;

  const flush = () => {
    if (!buffer.length) return;
    turns.push({
      id: `turn-${index++}`,
      speaker: null,
      text: buffer.join(" "),
      time: bufferTime,
    });
    buffer = [];
    bufferChars = 0;
    bufferTime = null;
  };

  for (const line of transcript.split(/\n+/u)) {
    const { time, rest } = splitTimecode(line.trim());
    const row = rest.trim();
    if (!row) continue;
    const match = row.match(/^([^:]{1,40}):\s+(.+)$/u);
    const label = match ? speakerLabel(match[1], speakers) : null;
    if (label && match) {
      flush();
      turns.push({
        id: `turn-${index++}`,
        speaker: label,
        text: match[2].trim(),
        time,
      });
      continue;
    }
    if (time && !buffer.length) bufferTime = time;
    for (const sentence of splitSentences(row)) {
      buffer.push(sentence);
      bufferChars += sentence.length;
      if (
        buffer.length >= MAX_PARAGRAPH_SENTENCES ||
        bufferChars >= MAX_PARAGRAPH_CHARS
      ) {
        flush();
      }
    }
  }
  flush();
  return turns;
}

/** Labels actually used at the start of turns, in order of appearance. */
export function rosterFromTranscript(
  transcript: string,
  known: string[] = [],
): string[] {
  const out: string[] = [];
  for (const line of (transcript || "").split(/\n/u)) {
    const { rest } = splitTimecode(line.trim());
    const match = rest.match(/^\s*([^:]{1,40}):\s+\S/u);
    if (!match) continue;
    const label = speakerLabel(match[1], known);
    if (label && !out.includes(label)) out.push(label);
  }
  return out;
}

/**
 * Rename a speaker everywhere it labels a turn.
 *
 * Only line-leading labels are touched, so a name mentioned inside a sentence
 * keeps the words that were actually spoken.
 */
export function renameSpeaker(
  transcript: string,
  from: string,
  to: string,
): string {
  const previous = from.trim();
  const next = to.trim();
  if (!previous || !next || previous === next) return transcript;
  return (transcript || "")
    .split(/\n/u)
    .map((line) => {
      // Keep an optional [mm:ss] timecode untouched in front of the label.
      const match = line.match(
        /^(\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?)([^:]{1,40}):(\s+)(.*)$/u,
      );
      if (!match) return line;
      if (match[2].trim().toLowerCase() !== previous.toLowerCase()) return line;
      return `${match[1]}${next}:${match[3]}${match[4]}`;
    })
    .join("\n");
}

function matchDue(sentence: string): string | null {
  for (const pattern of DUE_PATTERNS) {
    const found = sentence.match(pattern);
    if (found) return found[0].trim();
  }
  return null;
}

function matchOwner(
  sentence: string,
  speaker: string | null,
  known: string[],
): string | null {
  for (const name of known) {
    if (!name.trim()) continue;
    if (new RegExp(`\\b${escapeRegExp(name)}\\b`, "iu").test(sentence))
      return name;
  }
  return speaker;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function hasHint(haystack: string, hints: string[]): boolean {
  return hints.some((hint) => haystack.includes(hint));
}

/** Keyword-based pass over the turns: cheap, local, and never invents text. */
export function detectHighlights(
  turns: TranscriptTurn[],
  speakers: string[],
): Highlight[] {
  const out: Highlight[] = [];
  const seen = new Set<string>();
  let index = 0;

  for (const turn of turns) {
    for (const sentence of splitSentences(turn.text)) {
      const clean = sentence.trim();
      if (clean.length < 12) continue;
      const lower = ` ${clean.toLowerCase()} `;
      let kind: HighlightKind | null = null;
      if (hasHint(lower, DECISION_HINTS)) kind = "decision";
      else if (hasHint(lower, ACTION_HINTS)) kind = "action";
      else if (hasHint(lower, PROPOSAL_HINTS)) kind = "proposal";
      else if (hasHint(lower, RISK_HINTS)) kind = "risk";
      else if (hasHint(lower, OPINION_HINTS)) kind = "opinion";
      else if (clean.endsWith("?")) kind = "question";
      if (!kind) continue;
      const key = `${kind}:${clean.toLowerCase()}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        id: `highlight-${index++}`,
        kind,
        text: clean,
        owner:
          kind === "action"
            ? matchOwner(clean, turn.speaker, speakers)
            : turn.speaker,
        due: kind === "action" ? matchDue(clean) : null,
      });
    }
  }
  return out;
}

/** Split model Markdown on its level-2 headings so the preview can lay it out. */
export function parseSections(markdown: string): ReportSection[] {
  const body = (markdown || "").trim();
  if (!body) return [];
  const lines = body.split(/\n/u);
  const sections: ReportSection[] = [];
  let title = "";
  let buffer: string[] = [];
  let index = 0;

  const flush = () => {
    const text = buffer.join("\n").trim();
    if (!title && !text) return;
    sections.push({ id: `section-${index++}`, title, body: text });
    buffer = [];
  };

  for (const line of lines) {
    const heading = line.match(/^#{2,3}\s+(.*)$/u);
    if (heading) {
      flush();
      title = heading[1].trim();
      continue;
    }
    buffer.push(line);
  }
  flush();
  return sections.filter((row) => row.title || row.body);
}

export function analyzeMeeting(source: MeetingSource): MeetingReport {
  const transcript = source.transcript.trim();
  const notes = source.notes.trim();
  const turns = buildTurns(transcript, source.speakers);
  const highlights = detectHighlights(turns, source.speakers);
  const words = countWords(transcript) + countWords(notes);
  const labelled = new Set(
    turns
      .map((turn) => turn.speaker)
      .filter((name): name is string => Boolean(name)),
  );
  source.speakers.forEach((name) => {
    if (name.trim()) labelled.add(name.trim());
  });

  // Prefer measured audio duration; fall back to the last turn's timecode.
  const lastTimecodeSec = turns.reduce(
    (max, turn) => (turn.time ? Math.max(max, timecodeToSeconds(turn.time)) : max),
    0,
  );
  const durationSec =
    source.durationSec && source.durationSec > 0
      ? Math.round(source.durationSec)
      : lastTimecodeSec > 0
        ? lastTimecodeSec
        : null;

  const stats: MeetingStats = {
    words,
    characters: transcript.length + notes.length,
    sentences: splitSentences(transcript).length,
    turns: turns.length,
    speakers: labelled.size,
    spokenMinutes: Math.max(
      transcript ? 1 : 0,
      Math.round(countWords(transcript) / WORDS_PER_MINUTE_SPOKEN),
    ),
    readingMinutes: Math.max(
      words ? 1 : 0,
      Math.round(words / WORDS_PER_MINUTE_READ),
    ),
    durationSec,
    language: detectLanguage(`${transcript}\n${notes}`),
  };

  return {
    source,
    stats,
    turns,
    highlights,
    sections: parseSections(source.summary),
    thin:
      countWords(transcript) < THIN_WORD_COUNT &&
      countWords(notes) < THIN_WORD_COUNT,
  };
}

function formatDate(value: string, locale: string): string {
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  } catch {
    return value;
  }
}

function highlightLabel(kind: HighlightKind, labels: ReportLabels): string {
  if (kind === "decision") return labels.decision;
  if (kind === "action") return labels.action;
  if (kind === "proposal") return labels.proposal;
  if (kind === "opinion") return labels.opinion;
  if (kind === "risk") return labels.risk;
  return labels.question;
}

/** "47 min" or "1 h 05" from a measured duration in seconds. */
export function formatDurationLabel(durationSec: number): string {
  const minutes = Math.max(1, Math.round(durationSec / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${String(rest).padStart(2, "0")}` : `${hours} h`;
}

function escapeCell(value: string): string {
  return value.replace(/\|/gu, "\\|").replace(/\n+/gu, " ").trim();
}

/** The Markdown export: the exact document the preview shows. */
export function buildReportMarkdown(
  report: MeetingReport,
  labels: ReportLabels,
  locale: string,
): string {
  const { source, stats, highlights, turns } = report;
  const lines: string[] = [`# ${source.title}`, ""];

  lines.push(`## ${labels.metadata}`, "");
  lines.push(`| | |`, `| --- | --- |`);
  lines.push(
    `| ${labels.date} | ${formatDate(source.startedAt || source.createdAt, locale)} |`,
  );
  lines.push(`| ${labels.updated} | ${formatDate(source.updatedAt, locale)} |`);
  lines.push(`| ${labels.template} | ${escapeCell(source.templateName)} |`);
  lines.push(
    `| ${labels.speakers} | ${escapeCell(source.speakers.join(", ") || labels.none)} |`,
  );
  lines.push(`| ${labels.words} | ${stats.words} |`);
  lines.push(
    `| ${labels.duration} | ${
      stats.durationSec
        ? formatDurationLabel(stats.durationSec)
        : `~${stats.spokenMinutes} min`
    } |`,
  );
  if (stats.language !== "unknown") {
    lines.push(`| ${labels.language} | ${stats.language.toUpperCase()} |`);
  }
  if (source.calendarUid) {
    lines.push(`| ${labels.calendar} | ${escapeCell(source.calendarUid)} |`);
  }
  lines.push("");

  if (source.summary.trim()) {
    lines.push(`## ${labels.summary}`, "", source.summary.trim(), "");
  }

  const actionable = highlights.filter((row) => row.kind !== "question");
  if (actionable.length) {
    lines.push(`## ${labels.highlights}`, "");
    lines.push(
      `| ${labels.kind} | ${labels.detail} | ${labels.owner} | ${labels.due} |`,
    );
    lines.push(`| --- | --- | --- | --- |`);
    for (const row of actionable) {
      lines.push(
        `| ${highlightLabel(row.kind, labels)} | ${escapeCell(row.text)} | ` +
          `${escapeCell(row.owner || labels.unassigned)} | ${escapeCell(row.due || labels.noDue)} |`,
      );
    }
    lines.push("");
  }

  const questions = highlights.filter((row) => row.kind === "question");
  if (questions.length) {
    lines.push(`## ${labels.questions}`, "");
    questions.forEach((row) => lines.push(`- ${row.text}`));
    lines.push("");
  }

  if (source.notes.trim()) {
    lines.push(`## ${labels.notes}`, "", source.notes.trim(), "");
  }

  if (turns.length) {
    lines.push(`## ${labels.transcript}`, "");
    for (const turn of turns) {
      const time = turn.time ? `\`[${turn.time}]\` ` : "";
      lines.push(
        turn.speaker
          ? `${time}**${turn.speaker}:** ${turn.text}`
          : `${time}${turn.text}`,
        "",
      );
    }
  }

  return `${lines.join("\n").trim()}\n`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/gu, "&amp;")
    .replace(/</gu, "&lt;")
    .replace(/>/gu, "&gt;")
    .replace(/"/gu, "&quot;");
}

function inlineMarkdown(value: string): string {
  return escapeHtml(value)
    .replace(/`([^`]+)`/gu, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/gu, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/gu, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/gu, '<a href="$2">$1</a>');
}

/** Minimal Markdown to HTML: enough for minutes, with no runtime dependency. */
export function markdownToHtml(markdown: string): string {
  const out: string[] = [];
  const lines = (markdown || "").split(/\n/u);
  let list: "ul" | "ol" | null = null;
  let table: string[][] | null = null;
  let paragraph: string[] = [];

  const closeParagraph = () => {
    if (!paragraph.length) return;
    out.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (!list) return;
    out.push(`</${list}>`);
    list = null;
  };
  const closeTable = () => {
    if (!table || !table.length) {
      table = null;
      return;
    }
    const [head, ...rows] = table;
    const headHtml = head
      .map((cell) => `<th>${inlineMarkdown(cell)}</th>`)
      .join("");
    const bodyHtml = rows
      .map(
        (row) =>
          `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`,
      )
      .join("");
    out.push(
      `<table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`,
    );
    table = null;
  };
  const closeAll = () => {
    closeParagraph();
    closeList();
    closeTable();
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      closeAll();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/u);
    if (heading) {
      closeAll();
      const level = Math.min(heading[1].length + 1, 5);
      out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/u.test(line)) {
      closeParagraph();
      closeList();
      const cells = line
        .trim()
        .replace(/^\||\|$/gu, "")
        .split("|")
        .map((cell) => cell.trim());
      if (cells.every((cell) => /^:?-{2,}:?$/u.test(cell))) continue;
      table = table ?? [];
      table.push(cells);
      continue;
    }
    closeTable();
    const bullet = line.match(/^\s*[-*]\s+(.*)$/u);
    if (bullet) {
      closeParagraph();
      if (list !== "ul") {
        closeList();
        out.push("<ul>");
        list = "ul";
      }
      out.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
      continue;
    }
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/u);
    if (numbered) {
      closeParagraph();
      if (list !== "ol") {
        closeList();
        out.push("<ol>");
        list = "ol";
      }
      out.push(`<li>${inlineMarkdown(numbered[1])}</li>`);
      continue;
    }
    const quote = line.match(/^\s*>\s?(.*)$/u);
    if (quote) {
      closeParagraph();
      closeList();
      out.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }
    closeList();
    paragraph.push(line.trim());
  }
  closeAll();
  return out.join("\n");
}

const PRINT_CSS = `
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 48px 56px 64px;
  background: #fff;
  color: #14161a;
  font: 15px/1.65 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}
h1 { font-size: 30px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.02em; }
h2 { font-size: 19px; margin: 34px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #e6e8ec; }
h3 { font-size: 16px; margin: 22px 0 8px; }
p { margin: 0 0 12px; }
ul, ol { margin: 0 0 12px; padding-left: 22px; }
li { margin: 0 0 5px; }
blockquote { margin: 0 0 12px; padding: 8px 14px; border-left: 3px solid #c9ced6; color: #444c58; background: #f7f8fa; }
code { font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #f2f4f7; padding: 1px 5px; border-radius: 4px; }
table { width: 100%; border-collapse: collapse; margin: 0 0 16px; font-size: 14px; }
th, td { border: 1px solid #e2e5ea; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #f6f7f9; font-weight: 600; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 18px 0 26px; }
.meta div { border: 1px solid #e6e8ec; border-radius: 10px; padding: 9px 12px; }
.meta dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #6b7280; margin: 0 0 3px; }
.meta dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.subtitle { color: #6b7280; margin: 0 0 4px; font-size: 13px; }
.turn { margin: 0 0 14px; }
.turn .who { margin: 0; font-weight: 600; font-size: 13px; }
.turn .speech { margin: 2px 0 0; }
.turn .tc { margin-left: 8px; color: #6b7280; font-weight: 400; font-size: 11.5px; font-variant-numeric: tabular-nums; background: #f3f4f6; border-radius: 6px; padding: 1px 6px; }
.turn.s0 .who { color: hsl(211 72% 40%); }
.turn.s1 .who { color: hsl(152 72% 32%); }
.turn.s2 .who { color: hsl(268 60% 48%); }
.turn.s3 .who { color: hsl(32 85% 38%); }
.turn.s4 .who { color: hsl(340 70% 44%); }
.turn.s5 .who { color: hsl(187 80% 30%); }
.turn.s6 .who { color: hsl(84 65% 30%); }
.turn.s7 .who { color: hsl(300 60% 42%); }
.footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid #e6e8ec; color: #6b7280; font-size: 12px; }
@media print {
  body { padding: 0; font-size: 12pt; }
  h2 { page-break-after: avoid; }
  table, .turn, blockquote { page-break-inside: avoid; }
}
`;

/** Self-contained printable report: open it and use the browser Print to PDF. */
export function buildReportHtml(
  report: MeetingReport,
  labels: ReportLabels,
  locale: string,
): string {
  const { source, stats, highlights, turns } = report;
  const cards: Array<[string, string]> = [
    [labels.date, formatDate(source.startedAt || source.createdAt, locale)],
    [labels.template, source.templateName],
    [labels.speakers, source.speakers.join(", ") || labels.none],
    [labels.words, String(stats.words)],
    [
      labels.duration,
      stats.durationSec
        ? formatDurationLabel(stats.durationSec)
        : `~${stats.spokenMinutes} min`,
    ],
  ];
  if (stats.language !== "unknown")
    cards.push([labels.language, stats.language.toUpperCase()]);
  if (source.calendarUid) cards.push([labels.calendar, source.calendarUid]);

  const parts: string[] = [
    `<h1>${escapeHtml(source.title)}</h1>`,
    `<p class="subtitle">${escapeHtml(labels.report)} · ${escapeHtml(
      formatDate(source.updatedAt, locale),
    )}</p>`,
    `<dl class="meta">${cards
      .map(
        ([term, value]) =>
          `<div><dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd></div>`,
      )
      .join("")}</dl>`,
  ];

  if (source.summary.trim()) {
    parts.push(
      `<h2>${escapeHtml(labels.summary)}</h2>`,
      markdownToHtml(source.summary),
    );
  }

  const actionable = highlights.filter((row) => row.kind !== "question");
  if (actionable.length) {
    parts.push(`<h2>${escapeHtml(labels.highlights)}</h2>`);
    parts.push(
      `<table><thead><tr><th>${escapeHtml(labels.kind)}</th><th>${escapeHtml(
        labels.detail,
      )}</th><th>${escapeHtml(labels.owner)}</th><th>${escapeHtml(
        labels.due,
      )}</th></tr></thead><tbody>${actionable
        .map(
          (row) =>
            `<tr><td>${escapeHtml(highlightLabel(row.kind, labels))}</td><td>${escapeHtml(
              row.text,
            )}</td><td>${escapeHtml(row.owner || labels.unassigned)}</td><td>${escapeHtml(
              row.due || labels.noDue,
            )}</td></tr>`,
        )
        .join("")}</tbody></table>`,
    );
  }

  const questions = highlights.filter((row) => row.kind === "question");
  if (questions.length) {
    parts.push(`<h2>${escapeHtml(labels.questions)}</h2>`);
    parts.push(
      `<ul>${questions.map((row) => `<li>${escapeHtml(row.text)}</li>`).join("")}</ul>`,
    );
  }

  if (source.notes.trim()) {
    parts.push(
      `<h2>${escapeHtml(labels.notes)}</h2>`,
      markdownToHtml(source.notes),
    );
  }

  if (turns.length) {
    // Stable colour class per speaker, in order of first appearance, matching
    // the on-screen transcript palette.
    const colorIndex = new Map<string, number>();
    for (const turn of turns) {
      if (turn.speaker && !colorIndex.has(turn.speaker)) {
        colorIndex.set(turn.speaker, colorIndex.size);
      }
    }
    parts.push(`<h2>${escapeHtml(labels.transcript)}</h2>`);
    parts.push(
      turns
        .map((turn) => {
          const idx = turn.speaker ? (colorIndex.get(turn.speaker) ?? 0) : null;
          const cls = idx === null ? "turn" : `turn s${idx % 8}`;
          const tc = turn.time
            ? ` <span class="tc">${escapeHtml(turn.time)}</span>`
            : "";
          const who = turn.speaker
            ? `<p class="who">${escapeHtml(turn.speaker)}${tc}</p>`
            : tc
              ? `<p class="who">${tc.trim()}</p>`
              : "";
          return `<div class="${cls}">${who}<p class="speech">${escapeHtml(
            turn.text,
          )}</p></div>`;
        })
        .join("\n"),
    );
  }
  parts.push(`<p class="footer">${escapeHtml(labels.generatedBy)}</p>`);

  return [
    "<!doctype html>",
    `<html lang="${escapeHtml(locale.slice(0, 2) || "en")}">`,
    "<head>",
    '<meta charset="utf-8" />',
    '<meta name="viewport" content="width=device-width, initial-scale=1" />',
    `<title>${escapeHtml(source.title)}</title>`,
    `<style>${PRINT_CSS}</style>`,
    "</head>",
    "<body>",
    parts.join("\n"),
    "</body>",
    "</html>",
  ].join("\n");
}
