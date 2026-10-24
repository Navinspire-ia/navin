// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ShellRunSummary } from "@/components/thread/activity/ShellRunCard";
import type { CliRunSummary, McpRunSummary } from "@/components/thread/activity/runSummaries";
import {
  actionFromToolArgs,
  activityLabelForTool,
  GENERIC_ACTIVITY_LABELS,
  isSpawnTool,
  spawnNameFromArgs,
  spawnTaskLineFromArgs,
} from "@/lib/activity-labels";
import type { ActivityEvidence } from "@/lib/activity-timeline";
import { computerSetupIssue } from "@/lib/computer-setup";
import type { UIInteractionEcho } from "@/lib/types";

export type ActivityJournalKind =
  | "explore"
  | "read"
  | "search"
  | "shell"
  | "cli"
  | "mcp"
  | "edit"
  | "tool"
  | "media"
  | "reasoning";

export type ActivityJournalTone =
  | ActivityJournalKind
  | "skill"
  | "task"
  | "git"
  | "browser"
  | "error";

/**
 * ``interrupted``: the call started but the turn ended (stopped, crashed,
 * cut off) before it returned. Neither done nor failed - shown neutral.
 */
export type ActivityJournalStatus = "running" | "done" | "error" | "interrupted";

export type ActivityJournalFact = {
  label: string;
  value: string;
};

/**
 * One raw thing the agent did, in the order it happened. The cluster walks
 * trace lines and structured tool events to produce these; the builder below
 * turns them into readable journal rows (explore runs merged, retries folded).
 */
export type JournalStep =
  | {
      kind: "read";
      path: string;
      status: ActivityJournalStatus;
      durationMs?: number;
    }
  | {
      kind: "search";
      query: string;
      tool: string;
      status: ActivityJournalStatus;
      durationMs?: number;
    }
  | {
      kind: "shell";
      key: string;
      command: string;
      status: ActivityJournalStatus;
      output?: string;
      error?: string;
      durationMs?: number;
      run?: ShellRunSummary;
    }
  | {
      kind: "cli";
      key: string;
      name: string;
      args: string;
      status: ActivityJournalStatus;
      error?: string;
      durationMs?: number;
      run?: CliRunSummary;
    }
  | {
      kind: "mcp";
      key: string;
      displayName: string;
      toolName: string;
      argsPreview?: string;
      status: ActivityJournalStatus;
      error?: string;
      durationMs?: number;
      run?: McpRunSummary;
    }
  | {
      kind: "edit";
      key: string;
      path: string;
      status: ActivityJournalStatus;
      operation?: "edit" | "delete";
      added: number;
      deleted: number;
      hasStats: boolean;
    }
  | {
      kind: "tool";
      name: string;
      args: string;
      status: ActivityJournalStatus;
      durationMs?: number;
      output?: string;
      error?: string;
      evidence?: ActivityEvidence[];
      /** Plain progress text that was not a tool call. */
      text?: string;
      /** ask_user: the settled card, the authoritative answer. */
      echo?: UIInteractionEcho;
    }
  | {
      /** The user allowed or denied an action the agent asked to run. */
      kind: "approval";
      key: string;
      action: string;
      allowed: boolean;
    }
  | {
      kind: "media";
      evidence: ActivityEvidence[];
    }
  | {
      /** A thinking segment, kept in time order between the tool calls. */
      kind: "reasoning";
      key: string;
      text: string;
      status: ActivityJournalStatus;
    };

export type ActivityJournalEntry = {
  id: string;
  kind: ActivityJournalKind;
  tone: ActivityJournalTone;
  seq: number;
  status: ActivityJournalStatus;
  live?: boolean;
  durationMs?: number;
  /** Adjacent identical actions folded into one row. */
  count?: number;
  // explore
  files?: number;
  searches?: number;
  filesList?: string[];
  queriesList?: string[];
  operations?: Extract<JournalStep, { kind: "read" | "search" }>[];
  // read / edit
  file?: string;
  path?: string;
  added?: number;
  deleted?: number;
  hasStats?: boolean;
  operation?: "edit" | "delete";
  // search
  query?: string;
  // shell
  /** One-line display form of the command (first line, spaces collapsed). */
  command?: string;
  /** The command as run, for the tooltip, the copy button and merging. */
  fullCommand?: string;
  /** Lines after the first one in a multi-line command (heredocs). */
  commandExtraLines?: number;
  /** Identity of the call (call id or trace line) the row stands for. */
  runKey?: string;
  output?: string;
  error?: string;
  shellRun?: ShellRunSummary;
  /** A failed command re-run with the same text until it passed. */
  recovered?: boolean;
  attempts?: number;
  // cli / mcp
  name?: string;
  detail?: string;
  tool?: string;
  cliRun?: CliRunSummary;
  mcpRun?: McpRunSummary;
  // tool
  labelKey?: string;
  defaultValue?: string;
  target?: string;
  /** Board task id when the row points at a card on the board. */
  taskId?: string;
  facts?: ActivityJournalFact[];
  occurrences?: string[];
  evidence?: ActivityEvidence[];
  // ask_user
  answer?: AskUserAnswer;
  // approval
  approval?: { allowed: boolean };
  // reasoning
  text?: string;
};

export type AskUserAnswer = {
  /** ``other``: the result is not an answer (no card, an error): never shown as one. */
  kind: "chose" | "custom" | "skipped" | "timeout" | "stopped" | "other";
  text: string;
};

/** Answer kinds that are a real reply from the user (shown with the arrow). */
export function isUserAnswer(answer: AskUserAnswer | undefined): boolean {
  return Boolean(answer && answer.text && (answer.kind === "chose" || answer.kind === "custom"));
}

/** Where a turn's wall time went, by what the agent was doing. */
export type ActivityPhaseKey = "thinking" | "explore" | "edit" | "command" | "tool";

export interface ActivityPhaseShare {
  key: ActivityPhaseKey;
  ms: number;
}

export const ACTIVITY_PHASE_ORDER: ActivityPhaseKey[] = ["thinking", "explore", "edit", "command", "tool"];

export type ActivityJournalDigest = {
  files: number;
  searches: number;
  edits: number;
  commands: number;
  tools: number;
  errors: number;
  setup?: number;
  recovered: number;
};

export const JOURNAL_PREVIEW_MAX = 8;
export const JOURNAL_DETAIL_LIST_MAX = 40;

export const READ_TOOL_NAMES = new Set(["read_file", "read", "open_file"]);
export const SEARCH_TOOL_RE = /search|grep|find_files|glob/i;

const TOOL_TONE_BY_LEAF: Record<string, ActivityJournalTone> = {
  skill: "skill",
  context: "skill",
  notes: "skill",
  board: "task",
  create_goal: "task",
  update_goal: "task",
  cron: "task",
  git: "git",
  pr_comments: "git",
  browser: "browser",
  scrape: "browser",
  web_fetch: "browser",
  fetch_url: "browser",
  fetch: "browser",
  list_dir: "explore",
  open_file_preview: "read",
  open_preview: "read",
  open_in_editor: "read",
};

const JOURNAL_FACT_KEYS = [
  "action",
  "title",
  "name",
  "skill",
  "label",
  "agent",
  "task",
  "path",
  "file",
  "file_path",
  "query",
  "pattern",
  "glob",
  "url",
  "command",
  "cwd",
  "id",
] as const;

const JOURNAL_TARGET_KEYS = [
  "question",
  "title",
  "name",
  "skill",
  "label",
  "agent",
  "task",
  "path",
  "file",
  "file_path",
  "query",
  "pattern",
  "url",
  "command",
] as const;

export function toolLeaf(name: string): string {
  return name.toLowerCase().split(".").pop() || name.toLowerCase();
}

export function journalToneForTool(name: string): ActivityJournalTone {
  return TOOL_TONE_BY_LEAF[toolLeaf(name)] ?? "tool";
}

/**
 * A failure is a step, not a verdict: agents probe with commands that fail on
 * purpose and retry a bad edit call. Whether the task worked is the final
 * message's job, so no row is ever painted or labelled as failed. Only a
 * missing computer setup stays visible, since the user has to act on it.
 */
export function isReportedFailure(entry: Pick<ActivityJournalEntry, "status" | "kind" | "tool" | "error">): boolean {
  return entry.status === "error" && Boolean(computerSetupIssue(entry.tool, entry.error));
}

/** A failed step that is not a command changed nothing: it is not shown. */
function isQuietFailure(step: JournalStep): boolean {
  if (!("status" in step) || step.status !== "error" || step.kind === "shell" || step.kind === "reasoning") return false;
  const tool = step.kind === "tool" || step.kind === "cli" ? step.name : step.kind === "mcp" ? step.toolName : "";
  // A question to the user is never hidden: its answer steers the turn.
  if (step.kind === "tool" && toolLeaf(step.name) === "ask_user") return false;
  const error = "error" in step ? step.error : undefined;
  return !computerSetupIssue(tool, error);
}

export function journalToneForEntry(entry: ActivityJournalEntry): ActivityJournalTone {
  if (isReportedFailure(entry)) return "error";
  return entry.tone;
}

export function fileBaseName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

export function clipJournalText(value: string, max = 72): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  return `${compact.slice(0, max - 1).trim()}…`;
}

function isCallKey(key: string | undefined): boolean {
  return Boolean(key?.startsWith("call:"));
}

/** Spaces collapsed, for telling two runs of one command apart from two commands. */
function normalizeCommand(command: string): string {
  return command.replace(/\s+/g, " ").trim();
}

function commandLines(command: string): string[] {
  return command
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/**
 * The clean answer of an ``ask_user`` call, read from the tool result the
 * agent got back (see navin/agent/choice.py ChoiceAnswer.as_text).
 */
export function parseAskUserAnswer(output: string | undefined | null): AskUserAnswer | undefined {
  const text = (output ?? "").replace(/\r\n?/g, "\n").trim();
  if (!text) return undefined;
  const custom = /They wrote this instead:\s*\n([\s\S]*?)(?:\nFollow that instruction[\s\S]*)?$/.exec(text);
  if (custom) {
    const answer = custom[1].trim();
    if (answer) return { kind: "custom", text: answer };
  }
  const chose = /^The user chose "([^"]*)"(?: \(([\s\S]*))?$/.exec(text);
  if (chose) {
    // The label may hold parentheses itself ("Fast (beta)"): it ends at the
    // first "): " (a detail follows) or at the closing ")." of the line.
    const rest = chose[2] ?? "";
    const detailAt = rest.indexOf("): ");
    const label = (detailAt >= 0 ? rest.slice(0, detailAt) : rest.replace(/\)\.?\s*$/, "")).trim();
    const answer = label || chose[1].trim();
    if (answer) return { kind: "chose", text: answer };
  }
  const recommended = /Recommended:\s*(\S+?)(?:\s+-\s+([^\n]*?))?\s*(?:\([^\n]*\))?\.?$/.exec(text);
  if (/^The user skipped/.test(text)) {
    return { kind: "skipped", text: (recommended?.[2] || recommended?.[1] || "").trim() };
  }
  if (/^The user did not answer in time/.test(text)) {
    return { kind: "timeout", text: (recommended?.[2] || recommended?.[1] || "").trim() };
  }
  if (/^The turn was stopped before a choice was made/.test(text)) {
    return { kind: "stopped", text: "" };
  }
  const first = text.split("\n").map((line) => line.trim()).find(Boolean) ?? "";
  return first ? { kind: "other", text: clipJournalText(first, 200) } : undefined;
}

/** The answer as the settled card recorded it (beats parsing the tool result). */
export function askUserAnswerFromEcho(echo: UIInteractionEcho | undefined): AskUserAnswer | undefined {
  if (!echo || echo.type !== "choice") return undefined;
  if (echo.customText?.trim()) return { kind: "custom", text: echo.customText.trim() };
  const label = (echo.label ?? "").trim();
  if (echo.stopped) return { kind: "stopped", text: "" };
  if (echo.timedOut) return { kind: "timeout", text: label };
  if (echo.skipped) return { kind: "skipped", text: label };
  return label ? { kind: "chose", text: label } : undefined;
}

type DurationTranslate = (key: string, options: Record<string, unknown>) => string;

/**
 * "45s", "3m 20s", "2h 5m". Localized through i18n keys when ``t`` is given
 * (digits stay Latin in every language, like the rest of the app).
 */
export function formatActivityDuration(ms: number, t?: DurationTranslate): string {
  const seconds = ms > 0 && ms < 1000 ? 1 : Math.max(0, Math.round(ms / 1000));
  const say = (key: string, defaultValue: string, values: Record<string, number>) => (
    t
      ? t(`message.activityDuration.${key}`, { ...values, defaultValue })
      : defaultValue.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values[name] ?? ""))
  );
  if (seconds < 60) return say("seconds", "{{s}}s", { s: seconds });
  const minutesTotal = Math.floor(seconds / 60);
  if (minutesTotal < 60) {
    const rest = seconds % 60;
    return rest
      ? say("minutesSeconds", "{{m}}m {{s}}s", { m: minutesTotal, s: rest })
      : say("minutes", "{{m}}m", { m: minutesTotal });
  }
  const hours = Math.floor(minutesTotal / 60);
  const minutes = minutesTotal % 60;
  return minutes
    ? say("hoursMinutes", "{{h}}h {{m}}m", { h: hours, m: minutes })
    : say("hours", "{{h}}h", { h: hours });
}

function parseArgsRecord(args: string): Record<string, unknown> | null {
  const compact = args.trim();
  if (!compact) return null;
  try {
    const parsed = JSON.parse(compact) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function pathFromToolArgs(args: string): string {
  const record = parseArgsRecord(args);
  if (!record) return "";
  for (const key of ["path", "file", "file_path", "filename"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/**
 * Every file a read names: one path, or each entry of a batch read
 * (`{"files": [{"path": ...}, ...]}`). Trace text is often cut mid-argument,
 * so complete `"path": "..."` pairs are recovered when the JSON does not parse.
 */
export function pathsFromToolArgs(args: string): string[] {
  const record = parseArgsRecord(args);
  if (record) {
    const files = record.files;
    if (Array.isArray(files)) {
      const paths = files
        .map((item) => (typeof item === "string" ? item : item && typeof item === "object" ? (item as Record<string, unknown>).path : ""))
        .filter((value): value is string => typeof value === "string" && Boolean(value.trim()))
        .map((value) => value.trim());
      if (paths.length) return paths;
    }
    const single = pathFromToolArgs(args);
    return single ? [single] : [];
  }
  const found = [...args.matchAll(/"(?:path|file_path|file)"\s*:\s*"((?:[^"\\]|\\.)+)"/g)].map((match) => match[1].trim());
  return [...new Set(found.filter(Boolean))];
}

/** Human target only - skip redundant `action: status` noise. */
export function journalTargetFromArgs(args: string): string {
  const compact = args.trim();
  if (!compact) return "";
  const record = parseArgsRecord(compact);
  if (!record) {
    try {
      const parsed = JSON.parse(compact) as unknown;
      if (parsed && typeof parsed === "object") return "";
    } catch {
      // Non-JSON progress text is its own target.
    }
    return clipJournalText(compact.replace(/^["']|["']$/g, ""));
  }
  for (const key of JOURNAL_TARGET_KEYS) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return key === "url" ? clipJournalText(displayUrl(value)) : clipJournalText(value);
    }
  }
  return "";
}

export function journalUrlFromArgs(args: string): string {
  const record = parseArgsRecord(args);
  if (!record) return "";
  for (const key of ["url", "uri", "href", "link"]) {
    const value = record[key];
    if (typeof value === "string" && /^https?:\/\//i.test(value.trim())) return value.trim();
  }
  return "";
}

function displayUrl(value: string): string {
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/^www\./i, "");
    const path = url.pathname && url.pathname !== "/" ? url.pathname : "";
    return `${host}${path}`;
  } catch {
    return value;
  }
}

function factValue(value: unknown): string {
  if (typeof value === "string" && value.trim()) return clipJournalText(value.trim(), 160);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const items = value
      .map((item) => factValue(item))
      .filter(Boolean)
      .slice(0, 4);
    return items.join(", ");
  }
  return "";
}

export function journalFactsFromArgs(args: string): ActivityJournalFact[] {
  const compact = args.trim();
  if (!compact) return [];
  const record = parseArgsRecord(compact);
  if (!record) {
    return [{ label: "args", value: clipJournalText(compact, 160) }];
  }
  const facts: ActivityJournalFact[] = [];
  const seen = new Set<string>();
  for (const key of JOURNAL_FACT_KEYS) {
    const value = factValue(record[key]);
    if (!value) continue;
    facts.push({ label: key, value });
    seen.add(key);
    if (facts.length >= 8) return facts;
  }
  for (const [key, raw] of Object.entries(record)) {
    if (seen.has(key) || key === "json" || key === "content") continue;
    const value = factValue(raw);
    if (!value) continue;
    facts.push({ label: key, value });
    if (facts.length >= 8) break;
  }
  return facts;
}

export function journalTaskIdFromArgs(args: string): string {
  const record = parseArgsRecord(args);
  if (!record) return "";
  for (const key of ["task_id", "id"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/** Rows that can jump to a card on the board: the board tool, with a title or id. */
export function journalTaskLink(entry: ActivityJournalEntry): { id?: string; title?: string } | null {
  if (entry.kind !== "tool" || entry.tool !== "board") return null;
  if (!entry.taskId && !entry.target) return null;
  return {
    ...(entry.taskId ? { id: entry.taskId } : {}),
    ...(entry.target ? { title: entry.target } : {}),
  };
}

/** The question of an ``ask_user`` call, on one line. */
export function journalQuestionFromArgs(args: string): string {
  const record = parseArgsRecord(args);
  const value = record?.question;
  return typeof value === "string" && value.trim() ? clipJournalText(value, 240) : "";
}

export function journalQueryFromArgs(args: string): string {
  const record = parseArgsRecord(args);
  if (!record) return "";
  for (const key of ["query", "pattern", "glob", "needle", "search", "q"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return clipJournalText(value.trim(), 96);
  }
  return "";
}

// ---------------------------------------------------------------------------
// Builder: ordered steps -> readable rows
// ---------------------------------------------------------------------------

type ExploreAccumulator = {
  seq: number;
  paths: string[];
  pathSet: Set<string>;
  pathlessReads: number;
  searches: number;
  queries: string[];
  querySet: Set<string>;
  operations: Extract<JournalStep, { kind: "read" | "search" }>[];
  status: ActivityJournalStatus;
  durationMs: number;
};

function newExplore(seq: number): ExploreAccumulator {
  return {
    seq,
    paths: [],
    pathSet: new Set(),
    pathlessReads: 0,
    searches: 0,
    queries: [],
    querySet: new Set(),
    operations: [],
    status: "done",
    durationMs: 0,
  };
}

function flushExplore(acc: ExploreAccumulator | null, entries: ActivityJournalEntry[]) {
  if (!acc) return;
  const files = acc.paths.length + acc.pathlessReads;
  const live = acc.status === "running";
  const durationMs = acc.durationMs > 0 ? acc.durationMs : undefined;
  entries.push({
    id: `explore:${acc.seq}`,
    kind: "explore",
    tone: "explore",
    seq: acc.seq,
    status: acc.status,
    live,
    durationMs,
    files,
    searches: acc.searches,
    filesList: acc.paths.length ? [...acc.paths] : undefined,
    queriesList: acc.queries.length ? [...acc.queries] : undefined,
    operations: acc.operations,
  });
}

function sumDuration(a?: number, b?: number): number | undefined {
  if (a === undefined && b === undefined) return undefined;
  return (a ?? 0) + (b ?? 0);
}

export function buildJournalTimeline(
  steps: JournalStep[],
  options: { streaming: boolean },
): ActivityJournalEntry[] {
  const entries: ActivityJournalEntry[] = [];
  let explore: ExploreAccumulator | null = null;

  steps.forEach((step, seq) => {
    if (isQuietFailure(step)) return;
    if (step.kind === "read" || step.kind === "search") {
      if (!explore) explore = newExplore(seq);
      // A later successful read must not hide a failed or still-running
      // operation elsewhere in this group.
      if (
        step.status === "error"
        || (explore.status !== "error" && step.status === "running")
        || (explore.status === "done" && step.status === "interrupted")
      ) {
        explore.status = step.status;
      }
      explore.operations.push(step);
      explore.durationMs += step.durationMs ?? 0;
      if (step.kind === "read") {
        const path = step.path.trim();
        if (path) {
          if (!explore.pathSet.has(path)) {
            explore.pathSet.add(path);
            explore.paths.push(path);
          }
        } else {
          explore.pathlessReads += 1;
        }
      } else {
        explore.searches += 1;
        const query = step.query.trim();
        if (query && !explore.querySet.has(query)) {
          explore.querySet.add(query);
          explore.queries.push(query);
        }
      }
      return;
    }

    flushExplore(explore, entries);
    explore = null;
    const previous = entries[entries.length - 1];

    if (step.kind === "reasoning") {
      const text = step.text.trim();
      if (previous?.kind === "reasoning") {
        // Back-to-back thinking with nothing in between reads as one thought.
        previous.text = [previous.text, text].filter(Boolean).join("\n\n");
        previous.status = step.status;
        previous.live = step.status === "running";
        return;
      }
      if (!text && step.status !== "running") return;
      entries.push({
        id: `reasoning:${step.key}`,
        kind: "reasoning",
        tone: "reasoning",
        seq,
        status: step.status,
        live: step.status === "running",
        text,
      });
      return;
    }

    if (step.kind === "shell") {
      const lines = commandLines(step.command);
      const display = clipJournalText(lines[0] ?? step.command, 240);
      if (
        previous?.kind === "shell"
        && (previous.runKey === step.key
          || (previous.status === "running" && step.status !== "running"
            // Two calls with their own ids are two runs, even of one command.
            && !(isCallKey(previous.runKey) && isCallKey(step.key))
            && normalizeCommand(previous.fullCommand ?? "") === normalizeCommand(step.command)))
      ) {
        // The same call seen twice (start then end, or a trace line and its
        // structured event): one row that moves from running to its outcome.
        previous.status = step.status;
        previous.live = step.status === "running";
        previous.shellRun = step.run ?? previous.shellRun;
        previous.output = step.output ?? previous.output;
        previous.error = step.error ?? previous.error;
        previous.durationMs = step.durationMs ?? previous.durationMs;
        return;
      }
      if (previous?.kind === "shell" && normalizeCommand(previous.fullCommand ?? "") === normalizeCommand(step.command)) {
        if (previous.status === "error" && step.status !== "error") {
          previous.status = step.status;
          previous.live = step.status === "running";
          previous.recovered = true;
          previous.attempts = (previous.attempts ?? 1) + 1;
          previous.runKey = step.key;
          previous.shellRun = step.run ?? previous.shellRun;
          previous.output = step.output ?? previous.output;
          previous.error = undefined;
          previous.durationMs = sumDuration(previous.durationMs, step.durationMs);
          return;
        }
        if (previous.status === "done" && step.status === "done") {
          previous.count = (previous.count ?? 1) + 1;
          previous.runKey = step.key;
          previous.durationMs = sumDuration(previous.durationMs, step.durationMs);
          previous.shellRun = step.run ?? previous.shellRun;
          previous.output = step.output ?? previous.output;
          return;
        }
      }
      entries.push({
        id: `shell:${step.key}:${seq}`,
        kind: "shell",
        tone: "shell",
        seq,
        status: step.status,
        live: step.status === "running",
        durationMs: step.durationMs,
        command: display,
        fullCommand: step.command,
        commandExtraLines: Math.max(0, lines.length - 1),
        runKey: step.key,
        output: step.output,
        error: step.error,
        shellRun: step.run,
      });
      return;
    }

    if (step.kind === "cli") {
      entries.push({
        id: `cli:${step.key}:${seq}`,
        kind: "cli",
        tone: "cli",
        seq,
        status: step.status,
        live: step.status === "running",
        durationMs: step.durationMs,
        name: step.name,
        detail: clipJournalText(step.args, 96),
        error: step.error,
        cliRun: step.run,
      });
      return;
    }

    if (step.kind === "mcp") {
      entries.push({
        id: `mcp:${step.key}:${seq}`,
        kind: "mcp",
        tone: "mcp",
        seq,
        status: step.status,
        live: step.status === "running",
        durationMs: step.durationMs,
        name: step.displayName,
        tool: step.toolName,
        detail: step.argsPreview,
        error: step.error,
        mcpRun: step.run,
      });
      return;
    }

    if (step.kind === "edit") {
      if (previous?.kind === "edit" && previous.path === step.path && previous.operation === step.operation) {
        previous.count = (previous.count ?? 1) + 1;
        previous.status = step.status;
        previous.live = step.status === "running";
        previous.added = (previous.added ?? 0) + step.added;
        previous.deleted = (previous.deleted ?? 0) + step.deleted;
        previous.hasStats = Boolean(previous.hasStats || step.hasStats);
        return;
      }
      entries.push({
        id: `edit:${step.key}:${seq}`,
        kind: "edit",
        tone: "edit",
        seq,
        status: step.status,
        live: step.status === "running",
        file: fileBaseName(step.path),
        path: step.path,
        operation: step.operation === "delete" ? "delete" : "edit",
        added: step.added,
        deleted: step.deleted,
        hasStats: step.hasStats,
      });
      return;
    }

    if (step.kind === "approval") {
      entries.push({
        id: `approval:${step.key}`,
        kind: "tool",
        tone: "task",
        seq,
        status: "done",
        labelKey: step.allowed ? "message.activityJournalApprovalAllowed" : "message.activityJournalApprovalDenied",
        defaultValue: step.allowed ? "Allowed" : "Denied",
        target: clipJournalText(step.action, 200),
        tool: "approval",
        approval: { allowed: step.allowed },
      });
      return;
    }

    if (step.kind === "media") {
      entries.push({
        id: `media:${seq}`,
        kind: "media",
        tone: "browser",
        seq,
        status: "done",
        evidence: step.evidence,
      });
      return;
    }

    // Generic tool
    const leaf = toolLeaf(step.name);
    const action = actionFromToolArgs(step.args);
    const url = journalUrlFromArgs(step.args);
    const mapped = step.name ? activityLabelForTool(step.name, action) : null;
    const spawn = isSpawnTool(step.name) && action !== "results";
    const spawnName = spawn ? spawnNameFromArgs(step.args) : "";
    const spawnTask = spawn ? spawnTaskLineFromArgs(step.args) : "";
    const label = mapped
      ?? (step.name && (/fetch|read|open/i.test(leaf) || url)
        ? GENERIC_ACTIVITY_LABELS.reading
        : step.name
          ? GENERIC_ACTIVITY_LABELS.using
          : GENERIC_ACTIVITY_LABELS.working);
    const target = step.text
      ? clipJournalText(step.text)
      : spawn
        ? clipJournalText(spawnName || spawnTask, 200)
        : leaf === "ask_user"
          ? journalQuestionFromArgs(step.args)
          : journalTargetFromArgs(step.args) || (!mapped && step.name ? leaf : "");
    const answer = leaf === "ask_user"
      ? askUserAnswerFromEcho(step.echo) ?? parseAskUserAnswer(step.output)
      : undefined;
    const tone: ActivityJournalTone = url ? "browser" : journalToneForTool(step.name || "");
    const facts = step.text ? [] : journalFactsFromArgs(step.args);
    if (leaf === "computer") {
      for (const fact of facts) {
        if (fact.label === "action" && fact.value === "screenshot") fact.value = "screen";
      }
    }
    const taskId = leaf === "board" && !step.text ? journalTaskIdFromArgs(step.args) : "";
    const occurrence = facts
      .filter((fact) => fact.label !== "action")
      .map((fact) => fact.value)
      .join(" · ");
    if (
      previous?.kind === "tool"
      && previous.labelKey === label.key
      && previous.target === target
      && !previous.evidence?.length
      && !step.evidence?.length
      && !spawn
      && leaf !== "ask_user"
    ) {
      previous.count = (previous.count ?? 1) + 1;
      previous.status = step.status;
      previous.live = step.status === "running";
      previous.durationMs = sumDuration(previous.durationMs, step.durationMs);
      previous.output = step.output || previous.output;
      previous.error = step.error ?? previous.error;
      previous.taskId = previous.taskId ?? (taskId || undefined);
      if (occurrence) {
        previous.occurrences = [...(previous.occurrences ?? []), occurrence];
      }
      return;
    }
    entries.push({
      id: `tool:${label.key}:${seq}`,
      kind: "tool",
      tone,
      seq,
      status: step.status,
      live: step.status === "running",
      durationMs: step.durationMs,
      labelKey: label.key,
      defaultValue: label.defaultValue,
      target,
      taskId: taskId || undefined,
      tool: leaf || undefined,
      facts,
      occurrences: occurrence ? [occurrence] : undefined,
      output: step.output,
      error: step.error,
      evidence: step.evidence,
      ...(spawn && spawnName && spawnTask ? { detail: clipJournalText(spawnTask, 200) } : {}),
      ...(answer ? { answer } : {}),
    });
  });

  flushExplore(explore, entries);

  if (!options.streaming) {
    for (const entry of entries) {
      // The turn is over: thinking that was still open simply ended, a call
      // that never returned was stopped - neither a check nor a cross.
      if (entry.status === "running") entry.status = entry.kind === "reasoning" ? "done" : "interrupted";
      entry.live = false;
    }
  }
  return entries;
}

export function summarizeJournal(entries: ActivityJournalEntry[]): ActivityJournalDigest {
  const digest: ActivityJournalDigest = {
    files: 0,
    searches: 0,
    edits: 0,
    commands: 0,
    tools: 0,
    errors: 0,
    recovered: 0,
  };
  for (const entry of entries) {
    const count = entry.count ?? 1;
    switch (entry.kind) {
      case "explore":
        digest.files += entry.files ?? 0;
        digest.searches += entry.searches ?? 0;
        break;
      case "read":
        digest.files += 1;
        break;
      case "search":
        digest.searches += 1;
        break;
      case "edit":
        digest.edits += 1;
        break;
      case "shell":
      case "cli":
        digest.commands += count;
        break;
      case "mcp":
      case "tool":
        // An approval is the user's answer, not something the agent ran.
        if (entry.tool !== "approval") digest.tools += count;
        break;
      default:
        break;
    }
    if (isReportedFailure(entry)) {
      if (computerSetupIssue(entry.tool, entry.error)) digest.setup = (digest.setup ?? 0) + 1;
      else digest.errors += 1;
    }
    if (entry.recovered) digest.recovered += 1;
  }
  return digest;
}

/**
 * The row to surface in the header while the turn is live: only a step that
 * is actually running. A finished one would read as current work.
 */
export function currentJournalEntry(entries: ActivityJournalEntry[]): ActivityJournalEntry | undefined {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].status === "running" && entries[i].kind !== "reasoning") return entries[i];
  }
  return undefined;
}

export function journalHasDetails(entry: ActivityJournalEntry): boolean {
  if (entry.kind === "reasoning") return Boolean(entry.text);
  return Boolean(
    entry.filesList?.length
    || entry.operations?.length
    || entry.queriesList?.length
    || entry.facts?.length
    || entry.occurrences?.length
    || entry.evidence?.length
    || entry.path
    || entry.command
    || entry.output
    || entry.error
    || entry.shellRun
    || entry.cliRun
    || entry.mcpRun
    || entry.query
    || entry.detail,
  );
}

/** The sections a finished turn is read in: what was looked at, changed, run, and what is left. */
export type JournalPhaseKey = "explore" | "edit" | "agents" | "command" | "check" | "test" | "attention";

export const JOURNAL_PHASE_ORDER: JournalPhaseKey[] = ["explore", "edit", "agents", "command", "check", "test", "attention"];

/** Tools that only look around the project: they read as exploration, not commands. */
const EXPLORE_TOOLS = new Set(["list_dir", "find_files", "glob", "code_index", "lsp", "tree"]);

const TEST_COMMAND_RE =
  /(^|[\s;&|(])(pytest|py\.test|vitest|jest|mocha|ava|unittest|phpunit|rspec|playwright\s+test|go\s+test|cargo\s+test|dotnet\s+test|(npm|pnpm|yarn|bun)(\s+run)?\s+test(:\S+)?)(?=$|[\s;&|)])/i;
const CHECK_COMMAND_RE =
  /(^|[\s;&|(])(ruff|eslint|tsc|mypy|pyright|flake8|pylint|black\s+--check|prettier\s+--check|compileall|clippy|cargo\s+(check|clippy|build)|go\s+(vet|build)|(npm|pnpm|yarn|bun)(\s+run)?\s+(lint|build|typecheck|check)(:\S+)?)(?=$|[\s;&|)])/i;

/** What a command or tool call was for: running tests, checking (lint, types, build), or anything else. */
export function commandPurpose(entry: ActivityJournalEntry): "test" | "check" | "command" {
  const tool = entry.tool ?? "";
  if (tool === "test_run") return "test";
  if (tool === "verify") return "check";
  const text = entry.kind === "shell" || entry.kind === "cli" ? entry.fullCommand || entry.command || "" : "";
  if (text && TEST_COMMAND_RE.test(text)) return "test";
  if (text && CHECK_COMMAND_RE.test(text)) return "check";
  return "command";
}

export type JournalPhase = {
  key: JournalPhaseKey;
  entries: ActivityJournalEntry[];
  /** Steps of the section that ended in error. */
  errors: number;
  /** What the section counts: operations, files, commands or open points. */
  count: number;
  /** Edits only: line totals across the section. */
  added: number;
  deleted: number;
  running: boolean;
};

function phaseOf(entry: ActivityJournalEntry): Exclude<JournalPhaseKey, "attention"> {
  switch (entry.kind) {
    case "explore":
    case "read":
    case "search":
      return "explore";
    case "edit":
      return "edit";
    default:
      if (entry.kind === "tool" && entry.tool === "spawn") return "agents";
      if (entry.kind === "tool" && EXPLORE_TOOLS.has(entry.tool ?? "")) return "explore";
      return commandPurpose(entry);
  }
}

/** A step that ended badly and was not put right later in the turn. */
export function needsAttention(entry: ActivityJournalEntry): boolean {
  return entry.status === "error" && !entry.recovered;
}

/**
 * The journal grouped by section, in reading order, empty sections dropped.
 * A step that needs attention stays in its own section and is listed again
 * under "attention", so the open points read on their own.
 */
export function groupJournalPhases(entries: ActivityJournalEntry[]): JournalPhase[] {
  const phases = new Map<JournalPhaseKey, JournalPhase>(
    JOURNAL_PHASE_ORDER.map((key) => [key, { key, entries: [], errors: 0, count: 0, added: 0, deleted: 0, running: false }]),
  );
  for (const entry of entries) {
    // Thinking between the calls is not a step of the work: the sections list
    // what was read, changed and run. It stays in the live journal.
    if (entry.kind === "reasoning") continue;
    const keys: JournalPhaseKey[] = [phaseOf(entry)];
    if (needsAttention(entry)) keys.push("attention");
    for (const key of keys) {
      const phase = phases.get(key)!;
      phase.entries.push(entry);
      if (entry.status === "running") phase.running = true;
      if (entry.status === "error") phase.errors += 1;
      if (key === "explore") {
        if (entry.kind === "explore") phase.count += (entry.files ?? 0) + (entry.searches ?? 0) || 1;
        else phase.count += 1;
      } else if (key === "edit") {
        phase.count += 1;
        phase.added += entry.added ?? 0;
        phase.deleted += entry.deleted ?? 0;
      } else if (key === "command" || key === "check" || key === "test") {
        if (entry.tool !== "approval") phase.count += entry.count ?? 1;
      } else {
        phase.count += 1;
      }
    }
  }
  return JOURNAL_PHASE_ORDER
    .map((key) => phases.get(key)!)
    .filter((phase) => phase.entries.length > 0);
}
