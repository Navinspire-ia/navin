// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ToolProgressEvent } from "@/lib/types";

export function formatToolCallTrace(call: unknown): string | null {
  if (!call || typeof call !== "object") return null;
  const item = call as {
    name?: unknown;
    arguments?: unknown;
    function?: { name?: unknown; arguments?: unknown };
  };
  const name =
    typeof item.function?.name === "string"
      ? item.function.name
      : typeof item.name === "string"
        ? item.name
        : "";
  if (!name) return null;
  const args = item.function?.arguments ?? item.arguments;
  if (typeof args === "string" && args.trim()) return `${name}(${args})`;
  if (args && typeof args === "object") return `${name}(${JSON.stringify(args)})`;
  return `${name}()`;
}

const TRACE_CALL_RE = /^([a-zA-Z0-9_.-]+)\(([\s\S]*)\)$/;

/**
 * A format-independent key for one tool call trace line. History replay
 * (Python ``json.dumps``) and the live stream (``JSON.stringify``) used to
 * write the same call with different spacing, so exact-string lookups missed
 * and the same command showed up twice. Parsing the arguments and writing
 * them back compactly makes both forms meet on one key.
 */
export function canonicalToolTraceKey(line: string): string {
  const cached = canonicalKeyCache.get(line);
  if (cached !== undefined) return cached;
  const key = computeCanonicalToolTraceKey(line);
  // Bounded: a long session writes thousands of distinct lines; dropping the
  // whole cache now and then is cheaper than tracking recency.
  if (canonicalKeyCache.size >= CANONICAL_KEY_CACHE_MAX) canonicalKeyCache.clear();
  canonicalKeyCache.set(line, key);
  return key;
}

const CANONICAL_KEY_CACHE_MAX = 20_000;
const canonicalKeyCache = new Map<string, string>();

function computeCanonicalToolTraceKey(line: string): string {
  const trimmed = line.trim();
  const match = TRACE_CALL_RE.exec(trimmed);
  if (!match) return trimmed;
  const name = match[1];
  const args = match[2].trim();
  if (!args) return `${name}()`;
  try {
    return `${name}(${JSON.stringify(JSON.parse(args))})`;
  } catch {
    return `${name}(${args})`;
  }
}

const eventKeyCache = new WeakMap<object, string | null>();

/** Canonical trace key for a structured tool event, or null without a name. */
export function toolEventTraceKey(event: unknown): string | null {
  const cacheable = Boolean(event) && typeof event === "object";
  if (cacheable) {
    const cached = eventKeyCache.get(event as object);
    if (cached !== undefined) return cached;
  }
  const line = formatToolCallTrace(event);
  const key = line ? canonicalToolTraceKey(line) : null;
  if (cacheable) eventKeyCache.set(event as object, key);
  return key;
}

/** The call id of a structured tool event, or "" when it has none. */
export function toolEventCallId(event: unknown): string {
  const callId = (event as { call_id?: unknown } | null | undefined)?.call_id;
  return typeof callId === "string" ? callId : "";
}

const VALID_PHASES = new Set(["start", "output", "end", "error"]);
const PHASE_RANK: Record<string, number> = { start: 1, output: 2, end: 3, error: 4 };

export function normalizeToolProgressEvents(events: unknown): ToolProgressEvent[] {
  if (!Array.isArray(events)) return [];
  const now = Date.now();
  const out: ToolProgressEvent[] = [];
  for (const event of events) {
    if (!event || typeof event !== "object") continue;
    const record = event as ToolProgressEvent;
    const phase = record.phase;
    if (!(phase && typeof phase === "string" && VALID_PHASES.has(phase))) continue;
    const name = typeof record.name === "string" ? record.name : "";
    const functionName =
      typeof (record as { function?: { name?: unknown } }).function?.name === "string"
        ? String((record as { function?: { name?: unknown } }).function?.name)
        : "";
    if (!name && !functionName) continue;
    // Stamp arrival times so activity rows can show per-step durations.
    const stamped: ToolProgressEvent = { ...record };
    if (phase === "start" || phase === "output") {
      if (stamped.client_started_at === undefined) stamped.client_started_at = now;
    } else if (stamped.client_ended_at === undefined) {
      stamped.client_ended_at = now;
    }
    out.push(stamped);
  }
  return out;
}

function toolEventKey(event: ToolProgressEvent): string {
  if (event.call_id) return `call:${event.call_id}`;
  return formatToolCallTrace(event) ?? JSON.stringify(event);
}

export function mergeToolProgressEvents(
  previous: ToolProgressEvent[] | undefined,
  incoming: ToolProgressEvent[],
): ToolProgressEvent[] {
  if (!previous?.length) return incoming;
  if (!incoming.length) return previous;
  const next = [...previous];
  const indexByKey = new Map(next.map((event, index) => [toolEventKey(event), index]));
  for (const event of incoming) {
    const key = toolEventKey(event);
    const existingIndex = indexByKey.get(key);
    if (existingIndex === undefined) {
      indexByKey.set(key, next.length);
      next.push(event);
      continue;
    }
    const existing = next[existingIndex];
    const incomingRank = PHASE_RANK[String(event.phase)] ?? 0;
    const existingRank = PHASE_RANK[String(existing.phase)] ?? 0;
    if (incomingRank < existingRank) continue;
    const merged: ToolProgressEvent = { ...existing, ...event };
    // Keep the earliest start stamp: live "output" frames are stamped on
    // arrival, later than the real "start" frame they follow.
    if (
      typeof existing.client_started_at === "number"
      && (typeof event.client_started_at !== "number"
        || existing.client_started_at < event.client_started_at)
    ) {
      merged.client_started_at = existing.client_started_at;
    }
    next[existingIndex] = merged;
  }
  return next;
}

export function toolTraceLinesFromEvents(events: unknown): string[] {
  const seen = new Set<string>();
  const lines: string[] = [];
  for (const event of normalizeToolProgressEvents(events)) {
    const callId = (event as { call_id?: unknown }).call_id;
    if (callId && typeof callId === "string") {
      if (seen.has(callId)) continue;
      seen.add(callId);
    }
    const line = formatToolCallTrace(event);
    if (!line) continue;
    lines.push(line);
  }
  return lines;
}

export function mergeUniqueToolTraceLines(
  previousTraces: string[],
  lines: string[],
): { traces: string[]; added: boolean } {
  // Semantic dedupe: the same call written with other JSON spacing is the
  // same call, not a second one. Keys are cached per line, so a tool frame
  // does not re-parse every earlier trace of the block.
  const seen = new Set<string>();
  for (const line of previousTraces) seen.add(canonicalToolTraceKey(line));
  const traces = [...previousTraces];
  let added = false;
  for (const line of lines) {
    const key = canonicalToolTraceKey(line);
    if (seen.has(key)) continue;
    seen.add(key);
    traces.push(line);
    added = true;
  }
  return { traces, added };
}

/**
 * Add the trace lines of new structured events to a block. Identity is the
 * call id, not the text: running the same command twice is two calls and
 * keeps two lines. The n-th call of one canonical line is matched to the
 * n-th line already written for it (history replay or a progress line may
 * have written it first), so a call is never listed twice either.
 */
export function mergeToolTraceLinesForEvents(
  previousTraces: string[],
  previousEvents: readonly ToolProgressEvent[] | undefined,
  incomingEvents: readonly ToolProgressEvent[],
): { traces: string[]; added: boolean } {
  const lineCount = new Map<string, number>();
  for (const line of previousTraces) {
    const key = canonicalToolTraceKey(line);
    lineCount.set(key, (lineCount.get(key) ?? 0) + 1);
  }
  const knownCalls = new Set<string>();
  const callCount = new Map<string, number>();
  for (const event of previousEvents ?? []) {
    const id = toolEventCallId(event);
    if (!id || knownCalls.has(id)) continue;
    knownCalls.add(id);
    const key = toolEventTraceKey(event);
    if (key) callCount.set(key, (callCount.get(key) ?? 0) + 1);
  }
  const traces = [...previousTraces];
  let added = false;
  for (const event of incomingEvents) {
    const line = formatToolCallTrace(event);
    if (!line) continue;
    const key = canonicalToolTraceKey(line);
    const id = toolEventCallId(event);
    const lines = lineCount.get(key) ?? 0;
    if (id) {
      if (knownCalls.has(id)) continue;
      knownCalls.add(id);
      const calls = (callCount.get(key) ?? 0) + 1;
      callCount.set(key, calls);
      if (lines >= calls) continue;
    } else if (lines > 0) {
      continue;
    }
    lineCount.set(key, lines + 1);
    traces.push(line);
    added = true;
  }
  return { traces, added };
}
