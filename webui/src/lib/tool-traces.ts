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
  const seen = new Set(previousTraces);
  const traces = [...previousTraces];
  let added = false;
  for (const line of lines) {
    if (seen.has(line)) continue;
    seen.add(line);
    traces.push(line);
    added = true;
  }
  return { traces, added };
}
