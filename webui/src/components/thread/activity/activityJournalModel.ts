// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ShellRunSummary } from "@/components/thread/activity/ShellRunCard";
import type { CliRunSummary, McpRunSummary } from "@/components/thread/activity/runSummaries";
import {
  actionFromToolArgs,
  activityLabelForTool,
  GENERIC_ACTIVITY_LABELS,
} from "@/lib/activity-labels";
import type { ActivityEvidence } from "@/lib/activity-timeline";
import { computerSetupIssue } from "@/lib/computer-setup";

export type ActivityJournalKind =
  | "explore"
  | "read"
  | "search"
  | "shell"
  | "cli"
  | "mcp"
  | "edit"
  | "tool"
  | "media";

export type ActivityJournalTone =
  | ActivityJournalKind
  | "skill"
  | "task"
  | "git"
  | "browser"
  | "error";

export type ActivityJournalStatus = "running" | "done" | "error";

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
    }
  | {
      kind: "media";
      evidence: ActivityEvidence[];
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
  command?: string;
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
};

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

export function journalToneForEntry(entry: ActivityJournalEntry): ActivityJournalTone {
  if (entry.status === "error") return "error";
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

export function formatActivityDuration(ms: number): string {
  const seconds = ms > 0 && ms < 1000 ? 1 : Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
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
  reads: number;
  pathlessReads: number;
  searches: number;
  queries: string[];
  querySet: Set<string>;
  lastPath: string;
  searchTool: string;
  status: ActivityJournalStatus;
  durationMs: number;
};

function newExplore(seq: number): ExploreAccumulator {
  return {
    seq,
    paths: [],
    pathSet: new Set(),
    reads: 0,
    pathlessReads: 0,
    searches: 0,
    queries: [],
    querySet: new Set(),
    lastPath: "",
    searchTool: "",
    status: "done",
    durationMs: 0,
  };
}

function flushExplore(acc: ExploreAccumulator | null, entries: ActivityJournalEntry[]) {
  if (!acc) return;
  const files = acc.paths.length + acc.pathlessReads;
  const live = acc.status === "running";
  const durationMs = acc.durationMs > 0 ? acc.durationMs : undefined;
  if (files === 1 && acc.searches === 0) {
    entries.push({
      id: `read:${acc.seq}`,
      kind: "read",
      tone: "read",
      seq: acc.seq,
      status: acc.status,
      live,
      durationMs,
      file: fileBaseName(acc.lastPath || ""),
      path: acc.lastPath || undefined,
      count: acc.reads > 1 ? acc.reads : undefined,
    });
    return;
  }
  if (files === 0 && acc.searches === 1) {
    entries.push({
      id: `search:${acc.seq}`,
      kind: "search",
      tone: "explore",
      seq: acc.seq,
      status: acc.status,
      live,
      durationMs,
      query: acc.queries[0] ?? "",
      tool: acc.searchTool || undefined,
    });
    return;
  }
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
    if (step.kind === "read" || step.kind === "search") {
      if (!explore) explore = newExplore(seq);
      explore.status = step.status;
      explore.durationMs += step.durationMs ?? 0;
      if (step.kind === "read") {
        explore.reads += 1;
        const path = step.path.trim();
        if (path) {
          explore.lastPath = path;
          if (!explore.pathSet.has(path)) {
            explore.pathSet.add(path);
            explore.paths.push(path);
          }
        } else {
          explore.pathlessReads += 1;
        }
      } else {
        explore.searches += 1;
        explore.searchTool = step.tool;
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

    if (step.kind === "shell") {
      if (previous?.kind === "shell" && previous.command === step.command) {
        if (previous.status === "error" && step.status !== "error") {
          previous.status = step.status;
          previous.live = step.status === "running";
          previous.recovered = true;
          previous.attempts = (previous.attempts ?? 1) + 1;
          previous.shellRun = step.run ?? previous.shellRun;
          previous.output = step.output ?? previous.output;
          previous.error = undefined;
          previous.durationMs = sumDuration(previous.durationMs, step.durationMs);
          return;
        }
        if (previous.status === "done" && step.status === "done") {
          previous.count = (previous.count ?? 1) + 1;
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
        command: clipJournalText(step.command, 96),
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
    const label = mapped
      ?? (step.name && (/fetch|read|open/i.test(leaf) || url)
        ? GENERIC_ACTIVITY_LABELS.reading
        : step.name
          ? GENERIC_ACTIVITY_LABELS.using
          : GENERIC_ACTIVITY_LABELS.working);
    const target = step.text
      ? clipJournalText(step.text)
      : journalTargetFromArgs(step.args) || (!mapped && step.name ? leaf : "");
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
    });
  });

  flushExplore(explore, entries);

  if (!options.streaming) {
    for (const entry of entries) {
      if (entry.status === "running") entry.status = "done";
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
        digest.tools += count;
        break;
      default:
        break;
    }
    if (entry.status === "error") {
      if (computerSetupIssue(entry.tool, entry.error)) digest.setup = (digest.setup ?? 0) + 1;
      else digest.errors += 1;
    }
    if (entry.recovered) digest.recovered += 1;
  }
  return digest;
}

/** The row to surface in the header while the turn is live. */
export function currentJournalEntry(entries: ActivityJournalEntry[]): ActivityJournalEntry | undefined {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].status === "running") return entries[i];
  }
  return entries[entries.length - 1];
}

export function journalHasDetails(entry: ActivityJournalEntry): boolean {
  return Boolean(
    entry.filesList?.length
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
