import { useCallback, useEffect, useRef, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import { toMediaAttachment } from "@/lib/media";
import {
  mergeToolProgressEvents,
  mergeUniqueToolTraceLines,
  normalizeToolProgressEvents,
  toolTraceLinesFromEvents,
} from "@/lib/tool-traces";
import { hasPendingAgentActivity } from "@/lib/activity-timeline";
import { dropExpired, removeApproval, type PendingApproval } from "@/lib/approvals";
import { dropExpiredChoices, removeChoice, type PendingChoice } from "@/lib/choices";
import type { StreamError } from "@/lib/navin-client";
import type {
  InboundEvent,
  OutboundCliAppMention,
  OutboundFileMention,
  OutboundMcpPresetMention,
  UIDocumentTemplateAttachment,
  UIMediaTemplateAttachment,
  OutboundMedia,
  GoalStateWsPayload,
  TaskProgressData,
  ToolProgressEvent,
  UIMediaAttachment,
  UIFileEdit,
  UIMessage,
  UITurnPhase,
  WorkspaceScopePayload,
} from "@/lib/types";

function taskProgressFromAgentUi(blob: unknown): TaskProgressData | null {
  if (!blob || typeof blob !== "object") return null;
  const kind = (blob as { kind?: unknown }).kind;
  if (kind !== "task_progress") return null;
  const raw = (blob as { data?: unknown }).data;
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  const label = typeof data.label === "string" && data.label.trim()
    ? data.label.trim()
    : "Working…";
  const out: TaskProgressData = { label };
  if (typeof data.call_id === "string") out.call_id = data.call_id;
  if (typeof data.percent === "number" && Number.isFinite(data.percent)) {
    out.percent = Math.max(0, Math.min(100, data.percent));
  }
  if (typeof data.indeterminate === "boolean") out.indeterminate = data.indeterminate;
  else if (out.percent == null) out.indeterminate = true;
  if (typeof data.eta_s === "number" && Number.isFinite(data.eta_s)) out.eta_s = data.eta_s;
  if (typeof data.step === "string") out.step = data.step;
  if (typeof data.step_index === "number") out.step_index = data.step_index;
  if (typeof data.steps_total === "number") out.steps_total = data.steps_total;
  return out;
}

function enrichToolEventsWithTaskProgress(
  events: ToolProgressEvent[],
  progress: TaskProgressData | null,
): ToolProgressEvent[] {
  if (!progress || !events.length) return events;
  return events.map((event) => {
    if (progress.call_id && event.call_id && progress.call_id !== event.call_id) {
      return event;
    }
    return {
      ...event,
      ...(progress.percent != null ? { percent: progress.percent } : {}),
      ...(progress.eta_s != null ? { eta_s: progress.eta_s } : {}),
      ...(progress.label ? { label: progress.label } : {}),
      ...(progress.indeterminate != null ? { indeterminate: progress.indeterminate } : {}),
    };
  });
}

interface StreamBuffer {
  /** ID of the assistant message currently receiving deltas (cleared on ``stream_end``). */
  messageId: string;
}

interface ActiveAssistantCursor {
  id: string;
  index: number;
}

type StreamModelFields = Pick<UIMessage, "modelName" | "modelLabel" | "taskRole">;

type PendingStreamEvent =
  | { kind: "delta"; text: string; turn: UIMessageTurnFields; model?: StreamModelFields }
  | { kind: "reasoning"; text: string; turn: UIMessageTurnFields; model?: StreamModelFields };

type UIMessageTurnFields = Pick<UIMessage, "turnId" | "turnPhase" | "turnSeq">;

const FILE_EDIT_TOOL_NAMES = new Set(["write_file", "edit_file", "apply_patch"]);
const STREAM_END_IDLE_DELAY_MS = 1000;
/** After a reconnect, how long a supposedly-running turn may stay silent
 * before the stale streaming lock is cleared (gateway restarts kill turns
 * without ever sending ``turn_end``). */
const RECONNECT_STALE_TURN_GRACE_MS = 8000;

/**
 * Typewriter smoothing for streamed text.
 *
 * Network deltas arrive in irregular bursts - one character, then forty, then
 * nothing for 300 ms - and rendering them raw reads as nervous stuttering.
 * Instead of applying whole deltas as they land, each animation frame reveals
 * a bounded slice of the backlog: at least MIN_CHARS (a steady floor of about
 * 240 chars/s at 60 fps), plus a share of whatever is queued so a large burst
 * catches up in a fraction of a second instead of lagging forever. Boundary
 * events (stream_end, tool calls, complete messages) still flush everything
 * instantly, so smoothing never delays real state.
 */
const STREAM_REVEAL_MIN_CHARS_PER_FRAME = 4;
const STREAM_REVEAL_CATCHUP_FRACTION = 8;

function turnFieldsFromEvent(
  ev: { turn_id?: string; turn_phase?: UITurnPhase; turn_seq?: number },
  fallbackPhase?: UITurnPhase,
): UIMessageTurnFields {
  const fields: UIMessageTurnFields = {};
  if (typeof ev.turn_id === "string" && ev.turn_id.length > 0) {
    fields.turnId = ev.turn_id;
  }
  const phase = ev.turn_phase ?? fallbackPhase;
  if (phase) fields.turnPhase = phase;
  if (typeof ev.turn_seq === "number" && Number.isFinite(ev.turn_seq)) {
    fields.turnSeq = ev.turn_seq;
  }
  return fields;
}

function matchesTurn(message: UIMessage, turn: UIMessageTurnFields): boolean {
  return !turn.turnId || !message.turnId || message.turnId === turn.turnId;
}

/** Find a still-open streamed assistant turn. Closed stream segments stay visible
 * as streaming until ``turn_end`` for visual continuity, but they must not
 * receive later delta segments. */
function findStreamingAssistantIndex(
  prev: UIMessage[],
  closedStreamIds: ReadonlySet<string>,
  turn: UIMessageTurnFields = {},
): number | null {
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    const m = prev[i];
    if (m.kind === "trace") continue;
    if (
      m.role === "assistant"
      && m.isStreaming
      && !closedStreamIds.has(m.id)
      && matchesTurn(m, turn)
    ) return i;
    if (m.role === "user") break;
  }
  return null;
}

/**
 * Append a reasoning chunk to the last open reasoning stream in ``prev``.
 *
 * Lookup rule: reasoning can only extend the current reasoning placeholder.
 * Once ordinary answer text has appeared, the next reasoning chunk starts a
 * fresh Thought block so streamed output stays in arrival order:
 * Thought -> answer -> Thought -> answer.
 */
function attachReasoningChunk(
  prev: UIMessage[],
  chunk: string,
  segments?: {
    ensure: () => string;
  },
  turn: UIMessageTurnFields = {},
  model: StreamModelFields = {},
): UIMessage[] {
  const modelPatch = {
    ...(!model.modelName ? {} : { modelName: model.modelName }),
    ...(!model.modelLabel ? {} : { modelLabel: model.modelLabel }),
    ...(!model.taskRole ? {} : { taskRole: model.taskRole }),
  };
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    const candidate = prev[i];
    // A user turn is a hard boundary: reasoning after it belongs to the new
    // assistant turn, never to an earlier assistant reply.
    if (candidate.role === "user") break;
    // A trace row (e.g. Used tools) is also a phase boundary. Reasoning after
    // tools belongs to the next assistant iteration, not the assistant turn
    // that produced those tool calls.
    if (candidate.kind === "trace") break;
    if (candidate.role !== "assistant") continue;
    if (!matchesTurn(candidate, turn)) break;
    const activitySegmentId = candidate.activitySegmentId ?? segments?.ensure();
    const hasAnswer = candidate.content.length > 0;
    if (hasAnswer) break;
    if (
      candidate.reasoningStreaming
      || candidate.reasoning !== undefined
      || candidate.isStreaming
    ) {
      const merged: UIMessage = {
        ...candidate,
        reasoning: (candidate.reasoning ?? "") + chunk,
        reasoningStreaming: true,
        ...(activitySegmentId ? { activitySegmentId } : {}),
        ...turn,
        ...(!candidate.modelName && modelPatch.modelName ? { modelName: modelPatch.modelName } : {}),
        ...(!candidate.modelLabel && modelPatch.modelLabel ? { modelLabel: modelPatch.modelLabel } : {}),
        ...(!candidate.taskRole && modelPatch.taskRole ? { taskRole: modelPatch.taskRole } : {}),
      };
      return [...prev.slice(0, i), merged, ...prev.slice(i + 1)];
    }
    break;
  }
  const activitySegmentId = segments?.ensure();
  return [
    ...prev,
    {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      isStreaming: true,
      reasoning: chunk,
      reasoningStreaming: true,
      ...(activitySegmentId ? { activitySegmentId } : {}),
      ...turn,
      ...modelPatch,
      createdAt: Date.now(),
    },
  ];
}

/**
 * Find the most recent assistant placeholder that an incoming answer
 * delta should adopt instead of spawning a parallel row. We look for an
 * empty-content assistant turn that is still marked ``isStreaming`` -
 * typically created earlier by ``reasoning_delta``. Anything else means
 * the model already produced an answer in a previous turn, so the new
 * delta belongs in a fresh row.
 */
function findActiveAssistantPlaceholderIndex(
  prev: UIMessage[],
  turn: UIMessageTurnFields = {},
): number | null {
  const last = prev[prev.length - 1];
  if (!last) return null;
  if (last.role !== "assistant" || last.kind === "trace") return null;
  if (last.content.length > 0) return null;
  if (!last.isStreaming) return null;
  if (!matchesTurn(last, turn)) return null;
  return prev.length - 1;
}

function replaceMessageAt(prev: UIMessage[], index: number, message: UIMessage): UIMessage[] {
  const next = prev.slice();
  next[index] = message;
  return next;
}

/**
 * Close the active reasoning stream segment, if any. Idempotent: a
 * ``reasoning_end`` with no preceding deltas is a harmless no-op.
 */
function closeReasoningStream(prev: UIMessage[]): UIMessage[] {
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    const candidate = prev[i];
    if (!candidate.reasoningStreaming) continue;
    const latencyMs =
      candidate.latencyMs === undefined
      && Number.isFinite(candidate.createdAt)
      && candidate.createdAt > 1_000_000_000_000
        ? Math.max(0, Math.round(Date.now() - candidate.createdAt))
        : candidate.latencyMs;
    const merged: UIMessage = {
      ...candidate,
      reasoningStreaming: false,
      ...(latencyMs !== undefined ? { latencyMs } : {}),
    };
    return [...prev.slice(0, i), merged, ...prev.slice(i + 1)];
  }
  return prev;
}

function isReasoningOnlyPlaceholder(message: UIMessage): boolean {
  return (
    message.role === "assistant"
    && message.kind !== "trace"
    && message.content.trim().length === 0
    && !!message.reasoning
    && !message.reasoningStreaming
    && !message.media?.length
  );
}

function isToolTrace(message: UIMessage | undefined): boolean {
  return message?.kind === "trace";
}

function pruneReasoningOnlyPlaceholders(prev: UIMessage[]): UIMessage[] {
  return prev.filter((message, index) => {
    if (!isReasoningOnlyPlaceholder(message)) return true;
    // A reasoning-only assistant row immediately followed by tool traces is
    // the live equivalent of a persisted assistant tool-call message with
    // empty content, reasoning_content, and tool_calls. Keep it so live render
    // and history replay stay isomorphic.
    return isToolTrace(prev[index + 1]);
  });
}

function stampLastAssistantLatency(
  prev: UIMessage[],
  latencyMs: number,
  turnId?: string,
  phaseTimingsMs?: Record<string, number>,
  model?: { modelName?: string; modelLabel?: string; taskRole?: string },
): UIMessage[] {
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    const m = prev[i];
    if (
      m.role === "assistant"
      && m.kind !== "trace"
      && (!turnId || !m.turnId || m.turnId === turnId)
    ) {
      const merged: UIMessage = {
        ...m,
        latencyMs,
        isStreaming: false,
        ...(phaseTimingsMs && Object.keys(phaseTimingsMs).length > 0
          ? { phaseTimingsMs }
          : {}),
        ...(model?.modelName && !m.modelName ? { modelName: model.modelName } : {}),
        ...(model?.modelLabel && !m.modelLabel ? { modelLabel: model.modelLabel } : {}),
        ...(model?.taskRole && !m.taskRole ? { taskRole: model.taskRole } : {}),
      };
      return [...prev.slice(0, i), merged, ...prev.slice(i + 1)];
    }
  }
  return prev;
}

function modelFieldsFromEvent(ev: {
  model_name?: string;
  model_label?: string;
  task_role?: string;
}): Pick<UIMessage, "modelName" | "modelLabel" | "taskRole"> {
  const out: Pick<UIMessage, "modelName" | "modelLabel" | "taskRole"> = {};
  if (typeof ev.model_name === "string" && ev.model_name.trim()) {
    out.modelName = ev.model_name.trim();
  }
  if (typeof ev.model_label === "string" && ev.model_label.trim()) {
    out.modelLabel = ev.model_label.trim();
  }
  if (typeof ev.task_role === "string" && ev.task_role.trim()) {
    out.taskRole = ev.task_role.trim();
  }
  return out;
}

function absorbCompleteAssistantMessage(
  prev: UIMessage[],
  message: Omit<UIMessage, "id" | "role" | "createdAt">,
): UIMessage[] {
  const last = prev[prev.length - 1];
  if (!last || !isReasoningOnlyPlaceholder(last) || !matchesTurn(last, message)) {
    return [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "assistant",
        createdAt: Date.now(),
        ...message,
      },
    ];
  }
  return [
    ...prev.slice(0, -1),
    {
      ...last,
      ...message,
      isStreaming: false,
      reasoningStreaming: false,
    },
  ];
}

function fileEditKey(edit: Pick<UIFileEdit, "call_id" | "tool" | "path">): string {
  if (edit.call_id && edit.path) return `${edit.call_id}|${edit.tool}|${edit.path}`;
  if (edit.call_id) return `${edit.call_id}|${edit.tool}`;
  return `${edit.tool}|${edit.path}`;
}

function fileEditToolEventKey(edit: Pick<UIFileEdit, "call_id" | "tool" | "path">): string {
  if (edit.call_id) return `${edit.call_id}|${edit.tool}`;
  return fileEditKey(edit);
}

function toolEventFileEditKey(event: ToolProgressEvent): string | null {
  const fn = (event as { function?: { name?: unknown } }).function;
  const name = typeof event.name === "string"
    ? event.name
    : typeof fn?.name === "string"
      ? fn.name
      : "";
  const callId = typeof event.call_id === "string" ? event.call_id : "";
  if (!name || !callId || !FILE_EDIT_TOOL_NAMES.has(name)) return null;
  return `${callId}|${name}`;
}

function hasFileEditForToolEvent(messages: UIMessage[], event: ToolProgressEvent): boolean {
  const key = toolEventFileEditKey(event);
  if (!key) return false;
  return messages.some((message) =>
    message.fileEdits?.some((edit) => fileEditToolEventKey(edit) === key),
  );
}

function filterCoveredFileEditToolEvents(
  messages: UIMessage[],
  events: ToolProgressEvent[],
): ToolProgressEvent[] {
  if (events.length === 0) return events;
  return events.filter((event) => !hasFileEditForToolEvent(messages, event));
}

function stripCoveredFileEditToolHints(message: UIMessage, edits: UIFileEdit[]): UIMessage {
  const incomingKeys = new Set(edits.map(fileEditToolEventKey));
  const events = message.toolEvents ?? [];
  if (!events.length || incomingKeys.size === 0) return message;

  const removedTraceLines = new Set<string>();
  const keptEvents: ToolProgressEvent[] = [];
  let changed = false;
  for (const event of events) {
    const key = toolEventFileEditKey(event);
    if (key && incomingKeys.has(key)) {
      changed = true;
      for (const line of toolTraceLinesFromEvents([event])) {
        removedTraceLines.add(line);
      }
      continue;
    }
    keptEvents.push(event);
  }
  if (!changed) return message;

  const previousTraces = message.traces?.length
    ? message.traces
    : message.content
      ? [message.content]
      : [];
  const nextTraces = previousTraces.filter((line) => !removedTraceLines.has(line));
  return {
    ...message,
    traces: nextTraces,
    content: nextTraces[nextTraces.length - 1] ?? "",
    toolEvents: keptEvents.length ? keptEvents : undefined,
  };
}

function traceMessageIsEmpty(message: UIMessage): boolean {
  const traces = message.traces;
  const hasTrace = traces?.length
    ? traces.some((line) => line.trim().length > 0)
    : (message.content ?? "").trim().length > 0;
  return (
    message.kind === "trace"
    && !hasTrace
    && !message.toolEvents?.length
    && !message.fileEdits?.length
    && !message.media?.length
  );
}

function stripCoveredFileEditToolHintsFromMessages(
  messages: UIMessage[],
  edits: UIFileEdit[],
  turn: UIMessageTurnFields,
): UIMessage[] {
  if (edits.length === 0) return messages;
  let next = messages;
  for (let i = next.length - 1; i >= 0; i -= 1) {
    const candidate = next[i];
    if (candidate.role === "user") break;
    if (candidate.kind !== "trace") continue;
    if (!matchesTurn(candidate, turn)) continue;
    const cleaned = stripCoveredFileEditToolHints(candidate, edits);
    if (cleaned === candidate) continue;
    if (next === messages) next = [...messages];
    if (traceMessageIsEmpty(cleaned)) {
      next.splice(i, 1);
    } else {
      next[i] = cleaned;
    }
  }
  return next;
}

function normalizeFileEdit(edit: UIFileEdit): UIFileEdit | null {
  if (!edit || !edit.tool || (!edit.path && !edit.pending)) return null;
  const inferredStatus =
    edit.phase === "error"
      ? "error"
      : edit.phase === "end"
        ? "done"
        : "editing";
  const normalized: UIFileEdit = {
    ...edit,
    call_id: edit.call_id || `${edit.tool}:${edit.path}`,
    added: Number.isFinite(edit.added) ? Math.max(0, Math.round(edit.added)) : 0,
    deleted: Number.isFinite(edit.deleted) ? Math.max(0, Math.round(edit.deleted)) : 0,
    status: edit.status === "error" || edit.status === "done" || edit.status === "editing"
      ? edit.status
      : inferredStatus,
  };
  if (edit.pending && !edit.path) normalized.pending = true;
  return normalized;
}

function mergeFileEdits(existing: UIFileEdit[] | undefined, incoming: UIFileEdit[]): UIFileEdit[] {
  const next = [...(existing ?? [])];
  const indexByKey = new Map(next.map((edit, index) => [fileEditKey(edit), index]));
  for (const raw of incoming) {
    const edit = normalizeFileEdit(raw);
    if (!edit) continue;
    const key = fileEditKey(edit);
    let existingIndex = indexByKey.get(key);
    if (existingIndex === undefined && edit.path) {
      const eventKey = fileEditToolEventKey(edit);
      const pendingIndex = next.findIndex((existing) =>
        !existing.path && existing.pending && fileEditToolEventKey(existing) === eventKey,
      );
      if (pendingIndex >= 0) existingIndex = pendingIndex;
    }
    if (existingIndex === undefined) {
      indexByKey.set(key, next.length);
      next.push(edit);
      continue;
    }
    const merged = { ...next[existingIndex], ...edit };
    if (edit.path && !edit.pending) delete merged.pending;
    next[existingIndex] = merged;
    indexByKey.set(key, existingIndex);
  }
  return next;
}

function findFileEditTraceIndex(
  prev: UIMessage[],
  segmentId: string | null,
  incoming: UIFileEdit[],
): number | null {
  const incomingKeys = new Set(incoming.map(fileEditKey));
  const incomingToolEventKeys = new Set(incoming.map(fileEditToolEventKey));
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    const candidate = prev[i];
    if (candidate.role === "user") break;
    if (candidate.kind !== "trace") continue;
    if (segmentId && candidate.activitySegmentId === segmentId) return i;
    for (const existing of candidate.fileEdits ?? []) {
      if (
        incomingKeys.has(fileEditKey(existing))
        || (
          !existing.path
          && existing.pending
          && incomingToolEventKeys.has(fileEditToolEventKey(existing))
        )
      ) return i;
    }
  }
  return null;
}

/**
 * Subscribe to a chat by ID. Returns the in-memory message list for the chat,
 * a streaming flag, and a ``send`` function. Initial history must be seeded
 * separately (e.g. via ``fetchWebuiThread``) since the server only replays
 * live events.
 */
/** Payload passed to ``send`` when the user attaches one or more files.
 *
 * ``media`` is handed to the wire client verbatim; ``preview`` powers the
 * optimistic user bubble. Keeping the two separate lets the bubble re-use the
 * local data URL even after the server persists the file under a different
 * name. */
export interface SendAttachment {
  media: OutboundMedia;
  preview: UIMediaAttachment;
}

export interface SendOptions {
  cliApps?: OutboundCliAppMention[];
  mcpPresets?: OutboundMcpPresetMention[];
  fileMentions?: OutboundFileMention[];
  documentTemplate?: UIDocumentTemplateAttachment;
  mediaTemplate?: UIMediaTemplateAttachment;
  /** Media references attached to the turn (max 6, combined by the model). */
  mediaTemplates?: UIMediaTemplateAttachment[];
  workspaceScope?: WorkspaceScopePayload | null;
  /** Model preset pinned to this conversation (per-turn override). */
  modelPreset?: string;
  /** Active WebUI shell module (code, seo, marketing, …) for skill/command scoping. */
  productModule?: string;
  /** Open Code workbench tabs for agent context packing. */
  openFiles?: string[];
  sideChannel?: boolean;
  finalizeActiveTurn?: boolean;
}

function eventExtendsModelActivity(ev: InboundEvent): boolean {
  if (
    ev.event === "delta"
    || ev.event === "reasoning_delta"
    || ev.event === "file_edit"
    || ev.event === "approval_request"
    || ev.event === "choice_request"
    || ev.event === "goal_status"
  ) {
    if (ev.event === "goal_status") return ev.status === "running";
    return true;
  }
  return ev.event === "message"
    && (ev.kind === "tool_hint" || ev.kind === "progress" || ev.kind === "reasoning");
}

function finalizeStreamedTurn(
  prev: UIMessage[],
  turn: UIMessageTurnFields = {},
): UIMessage[] {
  return prev.map((m) =>
    m.isStreaming && matchesTurn(m, turn)
      ? { ...m, isStreaming: false, reasoningStreaming: false }
      : m,
  );
}

function eventTurnId(ev: InboundEvent): string | undefined {
  return "turn_id" in ev && typeof ev.turn_id === "string" ? ev.turn_id : undefined;
}

export interface ContextCompactionNotice {
  /** "consolidation" (token pressure mid-conversation) or "idle" (auto-compact). */
  kind: string;
  messagesArchived: number;
  tokensBefore?: number;
  tokensAfter?: number;
  /** Message count when the notice arrived, used to anchor the divider in the thread. */
  afterMessageCount: number;
  at: number;
}

export interface CheckpointNotice {
  /** Checkpoint name in the store, e.g. "auto-2026-08-22-190412". */
  name: string;
  /** Message count when the notice arrived, used to anchor the divider. */
  afterMessageCount: number;
  at: number;
}

export function useNavinStream(
  chatId: string | null,
  initialMessages: UIMessage[] = [],
  hasPendingToolCalls = false,
  onTurnEnd?: () => void,
  onUserMessage?: (chatId: string, text: string) => void,
  currentModel?: { modelName?: string | null; modelLabel?: string | null } | null,
): {
  messages: UIMessage[];
  isStreaming: boolean;
  /** Unix epoch seconds when the current user turn started (WebSocket ``goal_status``). */
  runStartedAt: number | null;
  /** Latest sustained goal for this ``chatId`` (``goal_state`` WS events). */
  goalState: GoalStateWsPayload | undefined;
  /** Latest ``agent_ui.task_progress`` for the active turn (bars / ETA strip). */
  activeTaskProgress: TaskProgressData | null;
  send: (content: string, images?: SendAttachment[], options?: SendOptions) => void;
  transcribeAudio: (dataUrl: string, options?: { durationMs?: number }) => Promise<string>;
  stop: () => void;
  setMessages: React.Dispatch<React.SetStateAction<UIMessage[]>>;
  /** Latest transport-level fault raised since the last ``dismissStreamError``.
   * ``null`` when there is nothing to show. */
  streamError: StreamError | null;
  /** Clear the current ``streamError`` (e.g. after the user dismisses the
   * notification or starts a fresh action). */
  dismissStreamError: () => void;
  /** Latest context compaction reported by the backend for this chat. */
  contextCompaction: ContextCompactionNotice | null;
  /** Latest restore point snapshotted for this chat (one per user turn). */
  checkpointNotice: CheckpointNotice | null;
  /** Approvals this chat is waiting on, oldest first. Each one holds a tool call. */
  pendingApprovals: PendingApproval[];
  /** Answer one, which releases the suspended tool call. */
  respondToApproval: (requestId: string, allowed: boolean, remember?: boolean) => void;
  pendingChoices: PendingChoice[];
  respondToChoice: (requestId: string, optionId: string, skipped?: boolean, customText?: string) => void;
} {
  const { client } = useClient();
  const currentModelRef = useRef(currentModel);
  currentModelRef.current = currentModel;

  const turnModelStamp = useCallback((): Pick<UIMessage, "modelName" | "modelLabel"> => {
    const cur = currentModelRef.current;
    const out: Pick<UIMessage, "modelName" | "modelLabel"> = {};
    if (cur?.modelName?.trim()) out.modelName = cur.modelName.trim();
    if (cur?.modelLabel?.trim()) out.modelLabel = cur.modelLabel.trim();
    return out;
  }, []);

  const [messages, setMessages] = useState<UIMessage[]>(initialMessages);
  const [contextCompaction, setContextCompaction] = useState<ContextCompactionNotice | null>(null);
  const [checkpointNotice, setCheckpointNotice] = useState<CheckpointNotice | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [pendingChoices, setPendingChoices] = useState<PendingChoice[]>([]);
  const messagesLengthRef = useRef(initialMessages.length);
  /** If history ends in unfinished agent activity, keep the loading spinner alive. */
  const initialStreaming = hasPendingAgentActivity(initialMessages);
  const [isStreaming, setIsStreaming] = useState(initialStreaming || hasPendingToolCalls);
  /** Unix epoch seconds when the current user turn started; cleared on ``idle``. */
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [goalState, setGoalState] = useState<GoalStateWsPayload | undefined>(undefined);
  const [activeTaskProgress, setActiveTaskProgress] = useState<TaskProgressData | null>(null);
  const [streamError, setStreamError] = useState<StreamError | null>(null);
  const buffer = useRef<StreamBuffer | null>(null);
  const activeAssistantRef = useRef<ActiveAssistantCursor | null>(null);
  const closedAssistantStreamIdsRef = useRef<Set<string>>(new Set());
  const activitySegmentRef = useRef<string | null>(null);
  const fileEditSegmentRef = useRef<string | null>(null);
  const activitySegmentCounterRef = useRef(0);
  const pendingStreamEventsRef = useRef<PendingStreamEvent[]>([]);
  const streamFrameRef = useRef<number | null>(null);
  const suppressStreamUntilTurnEndRef = useRef(false);
  const sideChannelTurnIdsRef = useRef<Set<string>>(new Set());
  /** Mirrors ``runStartedAt`` for the stream_end idle timer (avoids stale closures). */
  const runStartedAtRef = useRef<number | null>(null);
  /** Wall clock of the last chat event; proves the turn is alive after a reconnect. */
  const lastChatEventAtRef = useRef(0);
  /** Timer that defers ``isStreaming = false`` after ``stream_end``.
   *
   * When the model finishes a text segment and calls a tool, the server
   * sends ``stream_end`` but the agent is still working while the tool
   * executes. Defer the flag reset briefly, and never clear it while
   * ``goal_status: running`` says the turn is still alive. */
  const streamEndTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    messagesLengthRef.current = messages.length;
  }, [messages]);

  useEffect(() => {
    runStartedAtRef.current = runStartedAt;
  }, [runStartedAt]);

  useEffect(() => {
    setContextCompaction(null);
    setCheckpointNotice(null);
  }, [chatId]);

  useEffect(() => {
    return client.onError((err) => setStreamError(err));
  }, [client]);

  const dismissStreamError = useCallback(() => setStreamError(null), []);

  const clearPendingStreamWork = useCallback(() => {
    if (streamFrameRef.current !== null) {
      window.cancelAnimationFrame(streamFrameRef.current);
      streamFrameRef.current = null;
    }
    pendingStreamEventsRef.current = [];
  }, []);

  const cancelStreamEndTimer = useCallback(() => {
    if (streamEndTimerRef.current === null) return;
    clearTimeout(streamEndTimerRef.current);
    streamEndTimerRef.current = null;
  }, []);

  /** A gateway restart kills the in-flight turn without ever sending
   * ``turn_end``: after the socket reconnects, nothing would unlock the
   * composer ("Model is responding…" forever). Once the socket reopens,
   * give a surviving turn a short grace to show any sign of life, then
   * clear the stale streaming lock - a genuinely live turn re-locks it
   * on its very next event. */
  useEffect(() => {
    let wasDown = false;
    let recoveryTimer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = client.onStatus((status) => {
      if (status === "reconnecting" || status === "error") {
        wasDown = true;
        return;
      }
      if (status !== "open" || !wasDown) return;
      wasDown = false;
      const reconnectedAt = Date.now();
      if (recoveryTimer) clearTimeout(recoveryTimer);
      recoveryTimer = setTimeout(() => {
        recoveryTimer = null;
        if (lastChatEventAtRef.current >= reconnectedAt) return;
        // No event since the reconnect, including the goal_status hydration
        // the server replays for a genuinely live turn. A run lock held from
        // before the drop is therefore stale (gateway restarted mid-turn):
        // trusting it kept the "Still running" spinner forever.
        runStartedAtRef.current = null;
        setRunStartedAt(null);
        cancelStreamEndTimer();
        setIsStreaming(false);
        setMessages((prev) =>
          prev.some((m) => m.isStreaming)
            ? prev.map((m) =>
                m.isStreaming
                  ? { ...m, isStreaming: false, reasoningStreaming: false }
                  : m,
              )
            : prev,
        );
      }, RECONNECT_STALE_TURN_GRACE_MS);
    });
    return () => {
      unsubscribe();
      if (recoveryTimer) clearTimeout(recoveryTimer);
    };
  }, [cancelStreamEndTimer, client]);

  const isSideChannelEvent = useCallback((ev: InboundEvent) => {
    const turnId = eventTurnId(ev);
    return turnId !== undefined && sideChannelTurnIdsRef.current.has(turnId);
  }, []);

  const scheduleStreamEndTimer = useCallback((turn: UIMessageTurnFields = {}) => {
    cancelStreamEndTimer();
    streamEndTimerRef.current = setTimeout(() => {
      streamEndTimerRef.current = null;
      // Server still reports an active turn (long tool / image gen / shell).
      // Keep the Working indicator until turn_end or goal_status idle.
      if (runStartedAtRef.current != null) return;
      setIsStreaming(false);
      setMessages((prev) => finalizeStreamedTurn(prev, turn));
    }, STREAM_END_IDLE_DELAY_MS);
  }, [cancelStreamEndTimer]);

  const createActivitySegmentId = useCallback((activate = true) => {
    activitySegmentCounterRef.current += 1;
    const id = `activity-${activitySegmentCounterRef.current}`;
    if (activate) activitySegmentRef.current = id;
    return id;
  }, []);

  const freshActivitySegmentId = useCallback(
    () => createActivitySegmentId(true),
    [createActivitySegmentId],
  );

  const detachedActivitySegmentId = useCallback(
    () => createActivitySegmentId(false),
    [createActivitySegmentId],
  );

  const ensureActivitySegmentId = useCallback(() => {
    if (activitySegmentRef.current) return activitySegmentRef.current;
    return freshActivitySegmentId();
  }, [freshActivitySegmentId]);

  const clearActivitySegment = useCallback(() => {
    activitySegmentRef.current = null;
    fileEditSegmentRef.current = null;
  }, []);

  const closeActiveAssistantStream = useCallback(() => {
    const closedStreamId = buffer.current?.messageId ?? activeAssistantRef.current?.id;
    if (closedStreamId) closedAssistantStreamIdsRef.current.add(closedStreamId);
    buffer.current = null;
    activeAssistantRef.current = null;
    return !!closedStreamId;
  }, []);

  const resolveActiveAssistantIndex = useCallback((
    prev: UIMessage[],
    turn: UIMessageTurnFields = {},
  ): number | null => {
    const cursor = activeAssistantRef.current;
    if (!cursor) return null;
    const indexed = prev[cursor.index];
    if (
      indexed?.id === cursor.id
      && indexed.role === "assistant"
      && indexed.kind !== "trace"
      && indexed.isStreaming
      && matchesTurn(indexed, turn)
    ) {
      return cursor.index;
    }
    const idx = prev.findIndex((m) => m.id === cursor.id);
    if (idx === -1) {
      activeAssistantRef.current = null;
      return null;
    }
    const found = prev[idx];
    if (
      found.role !== "assistant"
      || found.kind === "trace"
      || !found.isStreaming
      || !matchesTurn(found, turn)
    ) {
      activeAssistantRef.current = null;
      return null;
    }
    activeAssistantRef.current = { id: cursor.id, index: idx };
    return idx;
  }, []);

  const appendAnswerChunk = useCallback(
    (prev: UIMessage[], chunk: string, turn: UIMessageTurnFields = {}): UIMessage[] => {
      let next = prev;
      let targetIndex = resolveActiveAssistantIndex(next, turn);

      if (targetIndex === null) {
        targetIndex = findActiveAssistantPlaceholderIndex(next, turn);
      }
      if (targetIndex === null) {
        targetIndex = findStreamingAssistantIndex(next, closedAssistantStreamIdsRef.current, turn);
      }
      if (targetIndex === null) {
        const id = crypto.randomUUID();
        next = [
          ...next,
          {
            id,
            role: "assistant",
            content: "",
            isStreaming: true,
            createdAt: Date.now(),
            ...turnModelStamp(),
          },
        ];
        targetIndex = next.length - 1;
      }

      const target = next[targetIndex];
      const stamp = turnModelStamp();
      const merged: UIMessage = {
        ...target,
        content: target.content + chunk,
        isStreaming: true,
        ...turn,
        ...(!target.modelName && stamp.modelName ? { modelName: stamp.modelName } : {}),
        ...(!target.modelLabel && stamp.modelLabel ? { modelLabel: stamp.modelLabel } : {}),
      };
      closedAssistantStreamIdsRef.current.delete(merged.id);
      activeAssistantRef.current = { id: merged.id, index: targetIndex };
      buffer.current = { messageId: merged.id };
      return replaceMessageAt(next, targetIndex, merged);
    },
    [resolveActiveAssistantIndex, turnModelStamp],
  );

  const applyPendingStreamEvents = useCallback(
    (prev: UIMessage[], events: PendingStreamEvent[]): UIMessage[] => {
      let next = prev;
      for (const event of events) {
        if (event.kind === "delta") {
          next = appendAnswerChunk(next, event.text, event.turn);
        } else {
          if (closeActiveAssistantStream()) clearActivitySegment();
          next = attachReasoningChunk(
            next,
            event.text,
            { ensure: ensureActivitySegmentId },
            event.turn,
            event.model,
          );
        }
      }
      return next;
    },
    [appendAnswerChunk, clearActivitySegment, closeActiveAssistantStream, ensureActivitySegmentId, turnModelStamp],
  );

  const flushPendingStreamEvents = useCallback((options?: {
    closeAnswerSegment?: boolean;
    finalAnswerText?: string;
    turn?: UIMessageTurnFields;
  }) => {
    if (streamFrameRef.current !== null) {
      window.cancelAnimationFrame(streamFrameRef.current);
      streamFrameRef.current = null;
    }
    const events = pendingStreamEventsRef.current;
    const finalAnswerText = options?.finalAnswerText;
    const turn = options?.turn ?? {};
    if (events.length === 0 && finalAnswerText === undefined) {
      if (options?.closeAnswerSegment) closeActiveAssistantStream();
      return;
    }
    pendingStreamEventsRef.current = [];
    setMessages((prev) => {
      let next = events.length > 0 ? applyPendingStreamEvents(prev, events) : prev;
      if (finalAnswerText !== undefined) {
        const targetIndex =
          resolveActiveAssistantIndex(next, turn)
          ?? findStreamingAssistantIndex(next, closedAssistantStreamIdsRef.current, turn);
          if (targetIndex !== null) {
            const target = next[targetIndex];
            next = replaceMessageAt(next, targetIndex, {
              ...target,
              content: finalAnswerText,
              isStreaming: true,
              ...turn,
            });
          } else {
            const id = crypto.randomUUID();
            closedAssistantStreamIdsRef.current.add(id);
            next = [
              ...next,
              {
                id,
                role: "assistant",
                content: finalAnswerText,
                isStreaming: true,
                ...turn,
                ...turnModelStamp(),
                createdAt: Date.now(),
              },
            ];
          }
        }
      if (options?.closeAnswerSegment) closeActiveAssistantStream();
      return next;
    });
  }, [applyPendingStreamEvents, closeActiveAssistantStream, resolveActiveAssistantIndex, turnModelStamp]);

  const schedulePendingStreamFlush = useCallback(() => {
    if (streamFrameRef.current !== null) return;
    streamFrameRef.current = window.requestAnimationFrame(() => {
      streamFrameRef.current = null;
      const queue = pendingStreamEventsRef.current;
      if (queue.length === 0) return;

      // Reveal a bounded slice of the backlog this frame (see constants above).
      const backlog = queue.reduce((sum, event) => sum + event.text.length, 0);
      let budget = Math.max(
        STREAM_REVEAL_MIN_CHARS_PER_FRAME,
        Math.ceil(backlog / STREAM_REVEAL_CATCHUP_FRACTION),
      );
      const consumed: PendingStreamEvent[] = [];
      while (queue.length > 0 && budget > 0) {
        const head = queue[0];
        if (head.text.length <= budget) {
          consumed.push(head);
          budget -= head.text.length;
          queue.shift();
        } else {
          consumed.push({ ...head, text: head.text.slice(0, budget) });
          queue[0] = { ...head, text: head.text.slice(budget) };
          budget = 0;
        }
      }
      if (consumed.length > 0) {
        setMessages((prev) => applyPendingStreamEvents(prev, consumed));
      }
      if (queue.length > 0) schedulePendingStreamFlush();
    });
  }, [applyPendingStreamEvents]);

  // Reset local state when switching chats. Do not reset on every
  // ``initialMessages`` update: a brand-new chat can receive an empty/404
  // history response after the optimistic first message has already rendered.
  useEffect(() => {
    setMessages(initialMessages);
    setIsStreaming(
      hasPendingAgentActivity(initialMessages) || hasPendingToolCalls,
    );
    setStreamError(null);
    const startedAt = chatId ? client.getRunStartedAt(chatId) : null;
    runStartedAtRef.current = startedAt;
    setRunStartedAt(startedAt);
    setGoalState(chatId ? client.getGoalState(chatId) : undefined);
    setPendingApprovals(chatId ? client.getPendingApprovals(chatId) : []);
    setPendingChoices(chatId ? client.getPendingChoices(chatId) : []);
    buffer.current = null;
    activeAssistantRef.current = null;
    closedAssistantStreamIdsRef.current.clear();
    clearActivitySegment();
    clearPendingStreamWork();
    sideChannelTurnIdsRef.current.clear();
    suppressStreamUntilTurnEndRef.current = false;
    cancelStreamEndTimer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId, client, cancelStreamEndTimer, clearActivitySegment, clearPendingStreamWork]);

  useEffect(() => {
    if (hasPendingToolCalls) setIsStreaming(true);
  }, [hasPendingToolCalls]);

  // The backend refuses on its own when a request expires and says so with a
  // close frame, but that frame needs a live socket to arrive. Ticking locally
  // means a dropped connection retires the card instead of leaving a dead button.
  useEffect(() => {
    if (pendingApprovals.length === 0) return;
    const timer = setInterval(() => {
      setPendingApprovals((prev) => {
        const live = dropExpired(prev, Date.now());
        return live.length === prev.length ? prev : live;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [pendingApprovals.length]);

  useEffect(() => {
    if (pendingChoices.length === 0) return;
    const timer = setInterval(() => {
      setPendingChoices((prev) => {
        const live = dropExpiredChoices(prev, Date.now());
        return live.length === prev.length ? prev : live;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [pendingChoices.length]);

  useEffect(() => {
    if (!chatId) return;

    const handle = (ev: InboundEvent) => {
      lastChatEventAtRef.current = Date.now();
      const sideChannelEvent = isSideChannelEvent(ev);
      // Any live agent activity (tools, file edits, progress, approvals…)
      // must keep/revive the Working spinner - not only cancel a pending
      // stream_end timer. Long tools otherwise look finished after 1s.
      if (!sideChannelEvent && eventExtendsModelActivity(ev)) {
        cancelStreamEndTimer();
        setIsStreaming(true);
      }

      if (ev.event === "delta") {
        if (suppressStreamUntilTurnEndRef.current) return;
        const chunk = typeof ev.text === "string" ? ev.text : "";
        if (!chunk) return;
        clearActivitySegment();
        setIsStreaming(true);
        pendingStreamEventsRef.current.push({
          kind: "delta",
          text: chunk,
          turn: turnFieldsFromEvent(ev, "answer"),
          model: modelFieldsFromEvent(ev),
        });
        schedulePendingStreamFlush();
        return;
      }

      if (ev.event === "reasoning_delta") {
        if (suppressStreamUntilTurnEndRef.current) return;
        const chunk = ev.text;
        if (!chunk) return;
        if (fileEditSegmentRef.current) clearActivitySegment();
        setIsStreaming(true);
        pendingStreamEventsRef.current.push({
          kind: "reasoning",
          text: chunk,
          turn: turnFieldsFromEvent(ev, "reasoning"),
          model: modelFieldsFromEvent(ev),
        });
        schedulePendingStreamFlush();
        return;
      }

      if (ev.event === "stream_end") {
        const turn = turnFieldsFromEvent(ev, "answer");
        flushPendingStreamEvents({
          closeAnswerSegment: true,
          ...(typeof ev.text === "string" ? { finalAnswerText: ev.text } : {}),
          turn,
        });
        if (suppressStreamUntilTurnEndRef.current) return;
        scheduleStreamEndTimer(turn);
        return;
      }

      const shouldCloseAnswerBeforeEvent =
        ev.event === "file_edit"
        || (
          ev.event === "message"
          && (ev.kind === "tool_hint" || ev.kind === "progress")
        );
      flushPendingStreamEvents({ closeAnswerSegment: shouldCloseAnswerBeforeEvent });

      if (ev.event === "reasoning_end") {
        if (suppressStreamUntilTurnEndRef.current) return;
        setMessages((prev) => closeReasoningStream(prev));
        return;
      }

      if (ev.event === "goal_state") {
        setGoalState(ev.goal_state);
        return;
      }

      if (ev.event === "goal_status") {
        if (ev.status === "running" && typeof ev.started_at === "number") {
          runStartedAtRef.current = ev.started_at;
          setRunStartedAt(ev.started_at);
          setIsStreaming(true);
        } else {
          const wasRunning = runStartedAtRef.current != null;
          runStartedAtRef.current = null;
          setRunStartedAt(null);
          // Soft-release if turn_end is delayed/missing after the server
          // marks the turn idle (avoids a stuck Working spinner).
          if (wasRunning) {
            scheduleStreamEndTimer(turnFieldsFromEvent(ev as { turn_id?: string }));
          }
        }
        return;
      }

      if (ev.event === "approval_request") {
        // The client keeps the authoritative list, so a card outlives a chat
        // switch and a replayed frame cannot resurrect an answered one.
        setPendingApprovals(client.getPendingApprovals(ev.chat_id));
        return;
      }

      if (ev.event === "approval_closed") {
        setPendingApprovals(client.getPendingApprovals(ev.chat_id));
        return;
      }

      if (ev.event === "choice_request" || ev.event === "choice_closed") {
        setPendingChoices(client.getPendingChoices(ev.chat_id));
        return;
      }

      if (ev.event === "checkpoint_saved") {
        // Only the latest restore point shows in the transcript; the
        // Checkpoints panel in Code keeps the full history.
        setCheckpointNotice({
          name: typeof ev.name === "string" ? ev.name : "",
          afterMessageCount: messagesLengthRef.current,
          at: Date.now(),
        });
        return;
      }

      if (ev.event === "context_compacted") {
        setContextCompaction({
          kind: ev.kind || "consolidation",
          messagesArchived: typeof ev.messages_archived === "number" ? ev.messages_archived : 0,
          tokensBefore: typeof ev.tokens_before === "number" ? ev.tokens_before : undefined,
          tokensAfter: typeof ev.tokens_after === "number" ? ev.tokens_after : undefined,
          afterMessageCount: messagesLengthRef.current,
          at: Date.now(),
        });
        return;
      }

      if (ev.event === "turn_end") {
        if ("goal_state" in ev && ev.goal_state != null && typeof ev.goal_state === "object") {
          setGoalState(ev.goal_state);
        }
        runStartedAtRef.current = null;
        setRunStartedAt(null);
        setActiveTaskProgress(null);
        // A card that outlived its turn is unanswerable: the tool that asked has
        // already been told no. The client drops them, this follows - choices
        // included, or a question card stays stuck on screen after the turn.
        setPendingApprovals([]);
        setPendingChoices([]);
        // Definitive signal that the turn is fully complete.  Cancel any
        // pending debounce timer and stop the loading indicator immediately.
        cancelStreamEndTimer();
        setIsStreaming(false);
        setMessages((prev) => {
          let finalized = prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m));
          finalized = pruneReasoningOnlyPlaceholders(finalized);
          if (typeof ev.latency_ms === "number" && ev.latency_ms >= 0) {
            const phaseTimings =
              ev.phase_timings_ms
              && typeof ev.phase_timings_ms === "object"
              && !Array.isArray(ev.phase_timings_ms)
                ? Object.fromEntries(
                    Object.entries(ev.phase_timings_ms).filter(
                      ([, v]) => typeof v === "number" && Number.isFinite(v),
                    ),
                  ) as Record<string, number>
                : undefined;
            finalized = stampLastAssistantLatency(
              finalized,
              Math.round(ev.latency_ms),
              ev.turn_id,
              phaseTimings,
              modelFieldsFromEvent(ev),
            );
          } else {
            const model = modelFieldsFromEvent(ev);
            if (model.modelName || model.modelLabel || model.taskRole) {
              for (let i = finalized.length - 1; i >= 0; i -= 1) {
                const m = finalized[i];
                if (
                  m.role === "assistant"
                  && m.kind !== "trace"
                  && (!ev.turn_id || !m.turnId || m.turnId === ev.turn_id)
                ) {
                  finalized = [
                    ...finalized.slice(0, i),
                    {
                      ...m,
                      ...(model.modelName && !m.modelName ? { modelName: model.modelName } : {}),
                      ...(model.modelLabel && !m.modelLabel ? { modelLabel: model.modelLabel } : {}),
                      ...(model.taskRole && !m.taskRole ? { taskRole: model.taskRole } : {}),
                    },
                    ...finalized.slice(i + 1),
                  ];
                  break;
                }
              }
            }
          }
          buffer.current = null;
          activeAssistantRef.current = null;
          clearActivitySegment();
          closedAssistantStreamIdsRef.current.clear();
          return finalized;
        });
        suppressStreamUntilTurnEndRef.current = false;
        onTurnEnd?.();
        return;
      }

      if (ev.event === "message") {
        if (
          suppressStreamUntilTurnEndRef.current &&
          (ev.kind === "tool_hint" || ev.kind === "progress" || ev.kind === "reasoning")
        ) {
          return;
        }
        // Back-compat: a legacy ``kind: "reasoning"`` message (no streaming
        // partner) is treated as one complete delta + immediate end so the
        // bubble renders identically to the streaming path.
        if (ev.kind === "reasoning") {
          const line = ev.text;
          if (!line) return;
          if (fileEditSegmentRef.current) clearActivitySegment();
          setMessages((prev) => closeReasoningStream(attachReasoningChunk(
            prev,
            line,
            { ensure: ensureActivitySegmentId },
            turnFieldsFromEvent(ev, "reasoning"),
          )));
          return;
        }
        // Intermediate agent breadcrumbs (tool-call hints, raw progress).
        // Attach them to the last trace row if it was the last emitted item
        // so a sequence of calls collapses into one compact trace group.
        if (ev.kind === "tool_hint" || ev.kind === "progress") {
          const progress = taskProgressFromAgentUi(ev.agent_ui);
          if (progress) setActiveTaskProgress(progress);
          const structuredEvents = enrichToolEventsWithTaskProgress(
            normalizeToolProgressEvents(ev.tool_events),
            progress,
          );
          const turn = turnFieldsFromEvent(ev, "activity");
          // Progress-only frames (no tool_events) still update the strip.
          if (structuredEvents.length === 0 && progress) {
            return;
          }
          setMessages((prev) => {
            const segmentId = ensureActivitySegmentId();
            const base = prev;
            const visibleStructuredEvents = filterCoveredFileEditToolEvents(base, structuredEvents);
            const structuredLines = toolTraceLinesFromEvents(visibleStructuredEvents);
            const lines = structuredLines.length > 0
              ? structuredLines
              : structuredEvents.length > 0
                ? []
                : ev.text
                  ? [ev.text]
                  : [];
            if (lines.length === 0) return base;
            const last = base[base.length - 1];
            if (
              last
              && last.kind === "trace"
              && !last.isStreaming
              && (!last.activitySegmentId || last.activitySegmentId === segmentId)
            ) {
              const previousTraces = last.traces?.length
                ? last.traces
                : last.content
                  ? [last.content]
                  : [];
              const mergedLines = visibleStructuredEvents.length > 0
                ? mergeUniqueToolTraceLines(previousTraces, structuredLines)
                : null;
              const merged: UIMessage = {
                ...last,
                traces: mergedLines ? mergedLines.traces : [...previousTraces, ...lines],
                content: mergedLines
                  ? mergedLines.traces[mergedLines.traces.length - 1]
                  : lines[lines.length - 1],
                toolEvents: visibleStructuredEvents.length
                  ? mergeToolProgressEvents(last.toolEvents, visibleStructuredEvents)
                  : last.toolEvents,
                activitySegmentId: last.activitySegmentId ?? segmentId,
                ...turn,
              };
              return [...base.slice(0, -1), merged];
            }
            return [
              ...base,
              {
                id: crypto.randomUUID(),
                role: "tool",
                kind: "trace",
                content: lines[lines.length - 1],
                traces: lines,
                ...(visibleStructuredEvents.length ? { toolEvents: visibleStructuredEvents } : {}),
                activitySegmentId: segmentId,
                ...turn,
                createdAt: Date.now(),
              },
            ];
          });
          return;
        }

        const media = ev.media_urls?.length
          ? ev.media_urls.map((m) => toMediaAttachment(m))
          : ev.media?.map((url) => toMediaAttachment({ url }));
        const hasMedia = !!media && media.length > 0;
        if (sideChannelEvent) {
          setMessages((prev) => absorbCompleteAssistantMessage(prev, {
            content: ev.text,
            ...(hasMedia ? { media } : {}),
            ...(ev.source ? { source: ev.source } : {}),
            ...modelFieldsFromEvent(ev),
            ...turnFieldsFromEvent(ev, "answer"),
          }));
          if (typeof ev.turn_id === "string") sideChannelTurnIdsRef.current.delete(ev.turn_id);
          return;
        }

        // A complete (non-streamed) assistant message. If a stream was in
        // flight, drop the placeholder so we don't render the text twice.
        // Streaming state is closed by ``stream_end`` when present, or by
        // ``turn_end`` for non-streamed and tool-heavy turns.
        clearActivitySegment();
        setMessages((prev) => {
          const activeId = buffer.current?.messageId;
          buffer.current = null;
          activeAssistantRef.current = null;
          const filtered = activeId ? prev.filter((m) => m.id !== activeId) : prev;
          const content = ev.text;
          const lat =
            typeof ev.latency_ms === "number" && ev.latency_ms >= 0
              ? Math.round(ev.latency_ms)
              : undefined;
          return absorbCompleteAssistantMessage(filtered, {
            content,
            ...(hasMedia ? { media } : {}),
            ...(lat !== undefined ? { latencyMs: lat } : {}),
            ...(ev.source ? { source: ev.source } : {}),
            ...modelFieldsFromEvent(ev),
            ...turnFieldsFromEvent(ev, "answer"),
          });
        });
        if (hasMedia) {
          suppressStreamUntilTurnEndRef.current = true;
        }
        return;
      }
      if (ev.event === "file_edit") {
        const edits = Array.isArray(ev.edits) ? ev.edits : [];
        if (edits.length === 0) return;
        const normalized = mergeFileEdits(undefined, edits);
        if (normalized.length === 0) return;
        const turn = turnFieldsFromEvent(ev, "activity");
        const opensFileEditPhase = normalized.some(
          (edit) => edit.status === "editing" || edit.phase === "start",
        );
        let eventSegmentId = fileEditSegmentRef.current;
        if (!eventSegmentId && opensFileEditPhase) {
          eventSegmentId = detachedActivitySegmentId();
          fileEditSegmentRef.current = eventSegmentId;
        }
        setMessages((prev) => {
          let segmentId = eventSegmentId;
          const base = stripCoveredFileEditToolHintsFromMessages(prev, normalized, turn);
          const targetIndex = findFileEditTraceIndex(base, segmentId, normalized);
          if (targetIndex !== null) {
            const target = base[targetIndex];
            segmentId = target.activitySegmentId ?? segmentId ?? detachedActivitySegmentId();
            if (opensFileEditPhase) fileEditSegmentRef.current = segmentId;
            const merged: UIMessage = {
              ...target,
              fileEdits: mergeFileEdits(target.fileEdits, normalized),
              activitySegmentId: segmentId,
              ...turn,
            };
            return replaceMessageAt(base, targetIndex, merged);
          }
          segmentId = segmentId ?? detachedActivitySegmentId();
          if (opensFileEditPhase) fileEditSegmentRef.current = segmentId;
          return [
            ...base,
            {
              id: crypto.randomUUID(),
              role: "tool",
              kind: "trace",
              content: "",
              traces: [],
              fileEdits: normalized,
              activitySegmentId: segmentId,
              ...turn,
              createdAt: Date.now(),
            },
          ];
        });
        return;
      }
      // ``attached`` / ``error`` frames aren't actionable here; the client
      // shell handles them separately.
    };

    const unsub = client.onChat(chatId, handle);
    return () => {
      unsub();
      buffer.current = null;
      activeAssistantRef.current = null;
      closedAssistantStreamIdsRef.current.clear();
      clearActivitySegment();
      clearPendingStreamWork();
      cancelStreamEndTimer();
    };
  }, [
    cancelStreamEndTimer,
    chatId,
    client,
    clearActivitySegment,
    clearPendingStreamWork,
    detachedActivitySegmentId,
    ensureActivitySegmentId,
    flushPendingStreamEvents,
    isSideChannelEvent,
    onTurnEnd,
    schedulePendingStreamFlush,
    scheduleStreamEndTimer,
  ]);

  const send = useCallback(
    (content: string, images?: SendAttachment[], options?: SendOptions) => {
      if (!chatId) return;
      const hasAttachments = !!images && images.length > 0;
      // Text is optional when files are attached - the agent will still see
      // them via ``media`` paths.
      if (!hasAttachments && !content.trim()) return;

      const sideChannel = options?.sideChannel === true;
      if (!sideChannel && content.trim()) {
        onUserMessage?.(chatId, content.trim());
      }
      const finalizeActiveTurn = options?.finalizeActiveTurn === true;
      flushPendingStreamEvents();
      if (finalizeActiveTurn) {
        cancelStreamEndTimer();
        setIsStreaming(false);
      }
      const turnId = crypto.randomUUID();
      if (sideChannel) sideChannelTurnIdsRef.current.add(turnId);
      const previews = hasAttachments ? images!.map((i) => i.preview) : undefined;
      setMessages((prev) => {
        if (!sideChannel || finalizeActiveTurn) {
          buffer.current = null;
          activeAssistantRef.current = null;
          closedAssistantStreamIdsRef.current.clear();
          clearActivitySegment();
          suppressStreamUntilTurnEndRef.current = false;
        }
        const base = finalizeActiveTurn ? finalizeStreamedTurn(prev) : prev;
        return [
          ...(sideChannel ? base : pruneReasoningOnlyPlaceholders(base)),
          {
            id: crypto.randomUUID(),
            role: "user",
            content,
            turnId,
            turnPhase: "user",
            turnSeq: 0,
            createdAt: Date.now(),
            ...(previews ? { media: previews } : {}),
            ...(options?.cliApps?.length ? { cliApps: options.cliApps } : {}),
            ...(options?.mcpPresets?.length ? { mcpPresets: options.mcpPresets } : {}),
            ...(options?.documentTemplate
              ? { documentTemplate: options.documentTemplate }
              : {}),
            ...(options?.mediaTemplate
              ? { mediaTemplate: options.mediaTemplate }
              : {}),
            ...(options?.mediaTemplates?.length
              ? { mediaTemplates: options.mediaTemplates }
              : {}),
          },
        ];
      });
      if (!sideChannel) {
        setIsStreaming(true);
        // Don't wait for goal_status:running (which only fires once BUILD finishes).
        // Seed the elapsed clock immediately so the first-message wait never looks frozen.
        if (runStartedAtRef.current == null) {
          const startedAt = Date.now() / 1000;
          runStartedAtRef.current = startedAt;
          setRunStartedAt(startedAt);
        }
      }
      const wireMedia = hasAttachments ? images!.map((i) => i.media) : undefined;
      const { documentTemplate, mediaTemplate, mediaTemplates, ...restOptions } =
        options ?? {};
      const wireOptions = {
        ...restOptions,
        turnId,
        ...(documentTemplate
          ? {
              documentTemplate: {
                category: documentTemplate.category,
                name: documentTemplate.name,
              },
            }
          : {}),
        ...(mediaTemplate
          ? {
              mediaTemplate: {
                id: mediaTemplate.id,
                title: mediaTemplate.title,
              },
            }
          : {}),
        ...(mediaTemplates?.length
          ? {
              mediaTemplates: mediaTemplates.map((item) => ({
                id: item.id,
                title: item.title,
              })),
            }
          : {}),
      };
      delete wireOptions.sideChannel;
      delete wireOptions.finalizeActiveTurn;
      client.sendMessage(chatId, content, wireMedia, wireOptions);
    },
    [cancelStreamEndTimer, chatId, clearActivitySegment, client, flushPendingStreamEvents, onUserMessage],
  );

  const stop = useCallback(() => {
    if (!chatId) return;
    flushPendingStreamEvents();
    cancelStreamEndTimer();
    runStartedAtRef.current = null;
    setRunStartedAt(null);
    setActiveTaskProgress(null);
    setIsStreaming(false);
    setMessages((prev) => {
      buffer.current = null;
      activeAssistantRef.current = null;
      closedAssistantStreamIdsRef.current.clear();
      clearActivitySegment();
      return prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m));
    });
    suppressStreamUntilTurnEndRef.current = false;
    client.sendMessage(chatId, "/stop");
  }, [cancelStreamEndTimer, chatId, clearActivitySegment, client, flushPendingStreamEvents]);

  const transcribeAudio = useCallback(
    (dataUrl: string, options?: { durationMs?: number }) =>
      client.transcribeAudio(dataUrl, options),
    [client],
  );

  const respondToApproval = useCallback(
    (requestId: string, allowed: boolean, remember = false) => {
      // Drop the card now rather than on the close frame: the backend only sends
      // that once the suspended tool has resumed, and a button that stays live
      // after a click invites a second, contradictory answer.
      setPendingApprovals((prev) => removeApproval(prev, requestId));
      client.sendApprovalDecision(requestId, allowed, remember);
    },
    [client],
  );

  const respondToChoice = useCallback(
    (requestId: string, optionId: string, skipped = false, customText = "") => {
      setPendingChoices((prev) => removeChoice(prev, requestId));
      client.sendChoiceAnswer(requestId, optionId, skipped, customText);
    },
    [client],
  );

  return {
    messages,
    isStreaming,
    runStartedAt,
    goalState,
    activeTaskProgress,
    send,
    transcribeAudio,
    stop,
    setMessages,
    streamError,
    dismissStreamError,
    contextCompaction,
    checkpointNotice,
    pendingApprovals,
    respondToApproval,
    pendingChoices,
    respondToChoice,
  };
}
