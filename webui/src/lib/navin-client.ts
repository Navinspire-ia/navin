import type {
  ArtifactRecord,
  AssistMeta,
  ConnectionStatus,
  InboundEvent,
  Outbound,
  OutboundCliAppMention,
  OutboundFileMention,
  OutboundMcpPresetMention,
  OutboundMedia,
  GoalStateWsPayload,
  MobilePreviewEvent,
  MobilePreviewStatus,
  PresenceMember,
  TerminalEvent,
  TerminalShellInfo,
  WorkspaceScopePayload,
} from "./types";
import { addApproval, dropExpired, removeApproval, type PendingApproval } from "./approvals";
import {
  addChoice,
  dropExpiredChoices,
  removeChoice,
  type PendingChoice,
} from "./choices";
import { shouldResolvePendingNewChat } from "./project-session";
import { createHostWebSocket } from "./runtime";

/** WebSocket readyState constants, referenced by value to stay portable
 * across runtimes that don't expose a global ``WebSocket`` (tests, SSR). */
const WS_OPEN = 1;
const WS_CLOSING = 2;
const HOST_SOCKET_URL_PREFIX = "navin-host://";

function createDefaultSocket(url: string): WebSocket {
  if (url.startsWith(HOST_SOCKET_URL_PREFIX)) {
    return createHostWebSocket(url);
  }
  return new WebSocket(url);
}

/** Inbound WebSocket ``console.log`` / parse-failure ``console.warn``.
 *
 * - **Dev** (non-production bundle): **on by default** - messages appear at default log level.
 * - **Production**: off unless ``localStorage.setItem('navin_debug_ws','1')`` (or ``true``).
 * - **Silence anywhere**: ``localStorage.setItem('navin_debug_ws','0')`` (or ``false`` / ``off``).
 * Values are read on every frame; no reload needed.
 */
function wsInboundDebugEnabled(): boolean {
  if (typeof globalThis === "undefined") return false;
  try {
    if (import.meta.env.MODE === "test") return false;
    const ls = (globalThis as unknown as { localStorage?: Storage }).localStorage;
    const raw = ls?.getItem("navin_debug_ws")?.trim().toLowerCase() ?? "";
    if (raw === "0" || raw === "false" || raw === "off" || raw === "no") {
      return false;
    }
    if (raw === "1" || raw === "true" || raw === "on" || raw === "yes") {
      return true;
    }
    return !import.meta.env.PROD;
  } catch {
    return !import.meta.env.PROD;
  }
}

/** Shorten streaming text fields so logging stays usable for huge deltas. */
function summarizeInboundWsPayload(ev: InboundEvent): unknown {
  const kind = (ev as { event?: string }).event;
  if (kind !== "delta" && kind !== "reasoning_delta") return ev;
  const row = { ...(ev as object) } as Record<string, unknown>;
  const text = typeof row.text === "string" ? row.text : "";
  const max = 240;
  if (text.length > max) {
    row.text = `${text.slice(0, max)}… (${text.length} chars)`;
  }
  return row;
}

type Unsubscribe = () => void;
type EventHandler = (ev: InboundEvent) => void;
type StatusHandler = (status: ConnectionStatus) => void;
type RuntimeModelUpdateMeta = {
  /** Chat whose turn selected the model; null on older servers. */
  chatId?: string | null;
  reason?: string | null;
  previousModel?: string | null;
  usedPercent?: number | null;
};
type RuntimeModelHandler = (
  modelName: string | null,
  modelPreset?: string | null,
  meta?: RuntimeModelUpdateMeta,
) => void;
type BoardUpdateHandler = (projectPath: string | null) => void;
/** A Montage change broadcast by the gateway (timeline saved, job progress, new assets). */
export interface MontageUpdate {
  projectPath: string | null;
  kind: "timeline" | "job" | "assets";
  name: string | null;
  job: import("./api").MontageJobSummary | null;
}
type MontageUpdateHandler = (update: MontageUpdate) => void;
export interface MetagraphUpdate {
  projectPath: string | null;
  generation: number;
  view: string;
  diff?: import("./types").MetagraphDiff;
}
type MetagraphUpdateHandler = (update: MetagraphUpdate) => void;
export interface TerminalOpenRequest {
  chatId: string;
  shell?: string;
  cwd?: string;
}
type TerminalOpenRequestHandler = (request: TerminalOpenRequest) => void;
export interface AgentExecUpdate {
  chatId: string;
  /** Stable id of the exec command; one read-only terminal tab per id. */
  id: string;
  phase: "start" | "output" | "exit";
  command?: string;
  cwd?: string;
  background: boolean;
  data?: string;
  exitCode?: number | null;
  /** OS sandbox backend the command runs in ("native", "bwrap"); absent when unconfined. */
  sandbox?: string;
  /** The user approved lifting the sandbox for this one command. */
  sandboxLifted?: boolean;
}
type AgentExecHandler = (update: AgentExecUpdate) => void;
export interface AgentBrowserUpdate {
  chatId: string;
  /** Stable id of the agent's live browser session. */
  id: string;
  phase: "start" | "frame" | "action" | "exit";
  url?: string;
  title?: string;
  action?: string;
  /** Base64 JPEG screencast frame (phase="frame"). */
  data?: string;
  width?: number;
  height?: number;
}
type AgentBrowserHandler = (update: AgentBrowserUpdate) => void;
export interface EditorOpenRequest {
  chatId: string;
  path: string;
  kind: "file" | "folder";
  line?: number;
}
type EditorOpenRequestHandler = (request: EditorOpenRequest) => void;
export type ComposerModeRequestMode =
  | "ask"
  | "plan"
  | "agent"
  | "review"
  | "security"
  | "debug"
  | "montage";
export interface ComposerModeRequest {
  chatId: string;
  mode: ComposerModeRequestMode;
}
type ComposerModeRequestHandler = (request: ComposerModeRequest) => void;
/** Product modules the shell can open (ids from navin.command.modules). */
export const PRODUCT_MODULES = [
  "risklens",
  "code",
  "scraping",
  "content",
  "marketing",
  "ads",
  "seo",
  "leads",
  "tenders",
  "career",
  "trading",
  "meeting",
  "ops",
  "notes",
  "crm",
] as const;
export type ProductModuleId = (typeof PRODUCT_MODULES)[number];
export interface ProductModuleRequest {
  chatId: string;
  module: ProductModuleId;
}
type ProductModuleRequestHandler = (request: ProductModuleRequest) => void;
export interface SubagentProgressUpdate {
  chatId: string;
  taskId: string;
  label: string;
  phase: string;
  statusLine: string;
  model?: string;
  iteration: number;
  done: boolean;
  error?: string;
  taskDescription?: string;
  /** Set on a replay after a refresh, where the frame's arrival time says
   * nothing about how long the subagent has actually been running. */
  startedMsAgo?: number;
}
type SubagentProgressHandler = (update: SubagentProgressUpdate) => void;
export interface PreviewOpenRequest {
  chatId: string;
  kind: "web" | "mobile";
  url?: string;
}
type PreviewOpenRequestHandler = (request: PreviewOpenRequest) => void;
export interface FilePreviewOpenRequest {
  chatId: string;
  path: string;
}
type FilePreviewOpenRequestHandler = (request: FilePreviewOpenRequest) => void;
export interface ArtifactUpsertPayload {
  chatId: string;
  artifact: ArtifactRecord;
  /** Replayed from disk on attach, not freshly produced by the agent. */
  restored?: boolean;
}
export interface ArtifactSelectPayload {
  chatId: string;
  artifactId: string;
}
type ArtifactUpsertHandler = (payload: ArtifactUpsertPayload) => void;
type ArtifactSelectHandler = (payload: ArtifactSelectPayload) => void;
export interface ServerNotification {
  title: string;
  detail?: string;
  level?: string;
  source?: string;
  key?: string;
  chatId?: string | null;
}
type NotificationHandler = (notification: ServerNotification) => void;
type SessionUpdateScope = "metadata" | "thread" | string;
type SessionUpdateHandler = (
  chatId: string,
  scope?: SessionUpdateScope,
  workspaceScope?: WorkspaceScopePayload,
) => void;
type RunStatusHandler = (chatId: string, startedAt: number | null) => void;
type PresenceHandler = (members: PresenceMember[]) => void;

const PENDING_INBOUND_CRITICAL_EVENTS = new Set([
  "approval_request",
  "approval_closed",
  "choice_request",
  "choice_closed",
  "goal_status",
  "goal_state_sync",
  "session_updated",
  "stream_end",
  "turn_end",
]);

function pendingInboundEventIsCritical(ev: InboundEvent): boolean {
  return PENDING_INBOUND_CRITICAL_EVENTS.has(ev.event);
}

/** Structured errors surfaced to the UI.
 *
 * Most entries are transport-level or protocol-level faults. Workspace scope
 * rejections are server application errors promoted here because they affect
 * controls outside the message stream and must be visible immediately.
 */
export type StreamError =
  /** Server rejected the inbound frame as too large (WS close code 1009).
   * This is the transport fallback after text and attachment policies have
   * already been checked independently. */
  | { kind: "message_too_big" }
  | { kind: "workspace_scope_rejected"; reason?: string; chatId?: string };

type ErrorHandler = (error: StreamError) => void;

interface PendingNewChat {
  resolve: (chatId: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

interface PendingTranscription {
  resolve: (text: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export interface NavinClientOptions {
  url: string;
  reconnect?: boolean;
  /** Called when a connection drops so the app can refresh its token. */
  onReauth?: () => Promise<string | null>;
  /** Inject a custom WebSocket factory (used by unit tests). */
  socketFactory?: (url: string) => WebSocket;
  /** Delay-cap for reconnect backoff (ms). */
  maxBackoffMs?: number;
  /** Initial reconnect delay before exponential growth (ms). */
  baseBackoffMs?: number;
  /** Random source injected by deterministic unit tests. */
  random?: () => number;
}

export type WebSocketCloseAction = "retry" | "reauth" | "stop";

/** Classify standard and common private close codes before reconnecting. */
export function classifyWebSocketClose(code?: number): WebSocketCloseAction {
  if (code === 1000 || code === 1002 || code === 1003 || code === 1007 || code === 1009) {
    return "stop";
  }
  if (code === 1008 || code === 4001 || code === 4003) return "reauth";
  return "retry";
}

/** Exponential backoff with bounded 20 percent jitter. */
export function reconnectDelayMs(
  attempt: number,
  baseMs: number,
  capMs: number,
  random: () => number = Math.random,
): number {
  const exponential = Math.min(baseMs * 2 ** Math.max(0, attempt), capMs);
  const jitter = 0.8 + Math.min(1, Math.max(0, random())) * 0.4;
  return Math.round(Math.min(exponential * jitter, capMs));
}

/**
 * Singleton WebSocket client that multiplexes chat streams.
 *
 * One socket carries many chat_ids: the server tags every outbound event with
 * ``chat_id``, and this class fans those events out to handlers registered
 * per chat. Reconnects are transparent and re-attach every known chat_id.
 */
export class NavinClient {
  private socket: WebSocket | null = null;
  private statusHandlers = new Set<StatusHandler>();
  private runtimeModelHandlers = new Set<RuntimeModelHandler>();
  private boardUpdateHandlers = new Set<BoardUpdateHandler>();
  private montageUpdateHandlers = new Set<MontageUpdateHandler>();
  private metagraphUpdateHandlers = new Set<MetagraphUpdateHandler>();
  private accountUpdatedHandlers = new Set<() => void>();
  private terminalOpenRequestHandlers = new Set<TerminalOpenRequestHandler>();
  private agentExecHandlers = new Set<AgentExecHandler>();
  private agentBrowserHandlers = new Set<AgentBrowserHandler>();
  private editorOpenRequestHandlers = new Set<EditorOpenRequestHandler>();
  private composerModeRequestHandlers = new Set<ComposerModeRequestHandler>();
  private productModuleRequestHandlers = new Set<ProductModuleRequestHandler>();
  private subagentProgressHandlers = new Set<SubagentProgressHandler>();
  private previewOpenRequestHandlers = new Set<PreviewOpenRequestHandler>();
  private filePreviewOpenRequestHandlers = new Set<FilePreviewOpenRequestHandler>();
  private artifactUpsertHandlers = new Set<ArtifactUpsertHandler>();
  private artifactSelectHandlers = new Set<ArtifactSelectHandler>();
  private notificationHandlers = new Set<NotificationHandler>();
  private sessionUpdateHandlers = new Set<SessionUpdateHandler>();
  private runStatusHandlers = new Set<RunStatusHandler>();
  private errorHandlers = new Set<ErrorHandler>();
  // chat_id -> handlers listening on it
  private chatHandlers = new Map<string, Set<EventHandler>>();
  private presenceByChat = new Map<string, PresenceMember[]>();
  private presenceHandlers = new Map<string, Set<PresenceHandler>>();
  /** Org role announced on the ready frame (viewer = read-only). */
  private orgRole: string | null = null;
  /** Inbound frames received while no subscriber is registered (e.g. user switched away). */
  private pendingInboundByChat = new Map<string, InboundEvent[]>();
  private static readonly PENDING_INBOUND_MAX = 2000;
  /** Chats whose bounded live buffer overflowed and need canonical HTTP history. */
  private pendingInboundOverflowChats = new Set<string>();
  // chat_ids we've attached to since connect; re-attached after reconnects
  private knownChats = new Set<string>();
  /** Wall-clock run strip: updated from ``goal_status`` even with no ``onChat`` subscriber. */
  private runStartedAtByChatId = new Map<string, number>();
  /** Latest ``goal_state`` snapshot per ``chat_id`` (multi-session isolation). */
  private goalStateByChatId = new Map<string, GoalStateWsPayload>();
  /** Approvals a chat is still waiting on, so a card survives a chat switch. */
  private approvalsByChatId = new Map<string, PendingApproval[]>();
  /** Requests already settled, so a replayed frame cannot resurrect a card. */
  private settledApprovals = new Set<string>();
  private static readonly SETTLED_APPROVALS_MAX = 200;
  private choicesByChatId = new Map<string, PendingChoice[]>();
  private settledChoices = new Set<string>();
  private static readonly SETTLED_CHOICES_MAX = 200;
  private pendingNewChat: PendingNewChat | null = null;
  private pendingTranscriptions = new Map<string, PendingTranscription>();
  private pendingVoiceStarts = new Map<
    string,
    {
      resolve: (sessionId: string) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private voiceSessionHandlers = new Set<(ev: InboundEvent) => void>();
  // terminal_id -> handler for terminal_* events (WebUI Dev mode)
  private terminalHandlers = new Map<string, (ev: TerminalEvent) => void>();
  private assistHandlers = new Map<
    string,
    {
      onPartial?: (text: string) => void;
      onMeta?: (meta: AssistMeta) => void;
      resolve: (completion: string) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private mobilePreviewHandlers = new Map<string, (ev: MobilePreviewEvent) => void>();
  private pendingShellRequests: Array<{
    resolve: (shells: TerminalShellInfo[]) => void;
    timer: ReturnType<typeof setTimeout>;
  }> = [];
  private pendingMobileStatusRequests: Array<{
    resolve: (status: MobilePreviewStatus) => void;
    timer: ReturnType<typeof setTimeout>;
  }> = [];
  private pendingMobileAvdRequests: Array<{
    resolve: (result: { ok: boolean; detail?: string | null }) => void;
    timer: ReturnType<typeof setTimeout>;
  }> = [];
  // Frames queued while the socket is not yet OPEN
  private sendQueue: Outbound[] = [];
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly shouldReconnect: boolean;
  private readonly baseBackoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly random: () => number;
  private socketFactory: (url: string) => WebSocket;
  private currentUrl: string;
  private status_: ConnectionStatus = "idle";
  private readyChatId: string | null = null;
  // Set by ``close()`` so the onclose handler knows the drop was intentional
  // and must not schedule a reconnect or flip status back to "reconnecting".
  private intentionallyClosed = false;

  constructor(private options: NavinClientOptions) {
    this.shouldReconnect = options.reconnect ?? true;
    this.baseBackoffMs = options.baseBackoffMs ?? 500;
    this.maxBackoffMs = options.maxBackoffMs ?? 15_000;
    this.random = options.random ?? Math.random;
    this.socketFactory = options.socketFactory ?? createDefaultSocket;
    this.currentUrl = options.url;
  }

  get status(): ConnectionStatus {
    return this.status_;
  }

  get defaultChatId(): string | null {
    return this.readyChatId;
  }

  /** Org role from the last ``ready`` frame (admin / member / viewer / host). */
  getOrgRole(): string | null {
    return this.orgRole;
  }

  /** Subscribe to the presence roster for a chat. */
  onPresence(chatId: string, handler: PresenceHandler): Unsubscribe {
    let handlers = this.presenceHandlers.get(chatId);
    if (!handlers) {
      handlers = new Set();
      this.presenceHandlers.set(chatId, handlers);
    }
    handlers.add(handler);
    handler(this.presenceByChat.get(chatId) ?? []);
    return () => {
      handlers?.delete(handler);
      if (handlers && handlers.size === 0) {
        this.presenceHandlers.delete(chatId);
      }
    };
  }

  private emitPresence(chatId: string): void {
    const members = this.presenceByChat.get(chatId) ?? [];
    const handlers = this.presenceHandlers.get(chatId);
    if (!handlers) return;
    for (const handler of handlers) handler(members);
  }

  private applyPresenceEvent(ev: InboundEvent): void {
    if (
      ev.event !== "presence_sync" &&
      ev.event !== "presence_join" &&
      ev.event !== "presence_leave"
    ) {
      return;
    }
    const chatId = ev.chat_id;
    if (!chatId) return;
    if (ev.event === "presence_sync") {
      this.presenceByChat.set(chatId, Array.isArray(ev.members) ? [...ev.members] : []);
      this.emitPresence(chatId);
      return;
    }
    const current = [...(this.presenceByChat.get(chatId) ?? [])];
    const member = ev.member;
    if (!member?.member_id) return;
    const idx = current.findIndex((m) => m.member_id === member.member_id);
    if (ev.event === "presence_join") {
      if (idx >= 0) current[idx] = member;
      else current.push(member);
    } else if (idx >= 0) {
      current.splice(idx, 1);
    }
    this.presenceByChat.set(chatId, current);
    this.emitPresence(chatId);
  }

  /** Swap the URL (e.g. after fetching a fresh token) then reconnect. */
  updateUrl(url: string, socketFactory?: (url: string) => WebSocket): void {
    this.currentUrl = url;
    if (socketFactory) {
      this.socketFactory = socketFactory;
    }
  }

  onStatus(handler: StatusHandler): Unsubscribe {
    this.statusHandlers.add(handler);
    handler(this.status_);
    return () => {
      this.statusHandlers.delete(handler);
    };
  }

  onRuntimeModelUpdate(handler: RuntimeModelHandler): Unsubscribe {
    this.runtimeModelHandlers.add(handler);
    return () => {
      this.runtimeModelHandlers.delete(handler);
    };
  }

  onBoardUpdate(handler: BoardUpdateHandler): Unsubscribe {
    this.boardUpdateHandlers.add(handler);
    return () => {
      this.boardUpdateHandlers.delete(handler);
    };
  }

  onMontageUpdate(handler: MontageUpdateHandler): Unsubscribe {
    this.montageUpdateHandlers.add(handler);
    return () => {
      this.montageUpdateHandlers.delete(handler);
    };
  }

  onMetagraphUpdate(handler: MetagraphUpdateHandler): Unsubscribe {
    this.metagraphUpdateHandlers.add(handler);
    return () => {
      this.metagraphUpdateHandlers.delete(handler);
    };
  }

  /** navin.live account changed gateway-side (sign-in, plan change, sign-out). */
  onAccountUpdated(handler: () => void): Unsubscribe {
    this.accountUpdatedHandlers.add(handler);
    return () => {
      this.accountUpdatedHandlers.delete(handler);
    };
  }

  /** The agent asked the editor to open its integrated terminal panel. */
  onTerminalOpenRequest(handler: TerminalOpenRequestHandler): Unsubscribe {
    this.terminalOpenRequestHandlers.add(handler);
    return () => {
      this.terminalOpenRequestHandlers.delete(handler);
    };
  }

  /** Live feed of a command the agent runs via exec (read-only terminal tabs). */
  onAgentExec(handler: AgentExecHandler): Unsubscribe {
    this.agentExecHandlers.add(handler);
    return () => {
      this.agentExecHandlers.delete(handler);
    };
  }

  /** Live mirror of the agent's headless browser (screencast frames). */
  onAgentBrowser(handler: AgentBrowserHandler): Unsubscribe {
    this.agentBrowserHandlers.add(handler);
    return () => {
      this.agentBrowserHandlers.delete(handler);
    };
  }

  /** The agent asked the editor to open a file tab or reveal a folder. */
  onEditorOpenRequest(handler: EditorOpenRequestHandler): Unsubscribe {
    this.editorOpenRequestHandlers.add(handler);
    return () => {
      this.editorOpenRequestHandlers.delete(handler);
    };
  }

  /** The agent asked to switch composer turn mode (Plan/Agent/Review/…). */
  onComposerModeRequest(handler: ComposerModeRequestHandler): Unsubscribe {
    this.composerModeRequestHandlers.add(handler);
    return () => {
      this.composerModeRequestHandlers.delete(handler);
    };
  }

  /** The agent asked the shell to open a product module (Code, SEO, …). */
  onProductModuleRequest(handler: ProductModuleRequestHandler): Unsubscribe {
    this.productModuleRequestHandlers.add(handler);
    return () => {
      this.productModuleRequestHandlers.delete(handler);
    };
  }

  /** Live updates for parallel background subagents (spawn). */
  onSubagentProgress(handler: SubagentProgressHandler): Unsubscribe {
    this.subagentProgressHandlers.add(handler);
    return () => {
      this.subagentProgressHandlers.delete(handler);
    };
  }

  /** The agent asked the Dev workbench to open Preview (web) or Mobile. */
  onPreviewOpenRequest(handler: PreviewOpenRequestHandler): Unsubscribe {
    this.previewOpenRequestHandlers.add(handler);
    return () => {
      this.previewOpenRequestHandlers.delete(handler);
    };
  }

  /** The agent asked the WebUI to open a workspace file in File Preview. */
  onFilePreviewOpenRequest(handler: FilePreviewOpenRequestHandler): Unsubscribe {
    this.filePreviewOpenRequestHandlers.add(handler);
    return () => {
      this.filePreviewOpenRequestHandlers.delete(handler);
    };
  }

  /** Artifact Canvas create/update from the agent or hydrate-on-attach. */
  onArtifactUpsert(handler: ArtifactUpsertHandler): Unsubscribe {
    this.artifactUpsertHandlers.add(handler);
    return () => {
      this.artifactUpsertHandlers.delete(handler);
    };
  }

  /** Focus one artifact in the Artifact Canvas. */
  onArtifactSelect(handler: ArtifactSelectHandler): Unsubscribe {
    this.artifactSelectHandlers.add(handler);
    return () => {
      this.artifactSelectHandlers.delete(handler);
    };
  }

  onNotification(handler: NotificationHandler): Unsubscribe {
    this.notificationHandlers.add(handler);
    return () => {
      this.notificationHandlers.delete(handler);
    };
  }

  onSessionUpdate(handler: SessionUpdateHandler): Unsubscribe {
    this.sessionUpdateHandlers.add(handler);
    return () => {
      this.sessionUpdateHandlers.delete(handler);
    };
  }

  onRunStatus(handler: RunStatusHandler): Unsubscribe {
    this.runStatusHandlers.add(handler);
    for (const [chatId, startedAt] of this.runStartedAtByChatId) {
      handler(chatId, startedAt);
    }
    return () => {
      this.runStatusHandlers.delete(handler);
    };
  }

  /** Subscribe to transport-level faults (see :type:`StreamError`). */
  onError(handler: ErrorHandler): Unsubscribe {
    this.errorHandlers.add(handler);
    return () => {
      this.errorHandlers.delete(handler);
    };
  }

  /** Last ``goal_status`` ``started_at`` (unix sec) for *chatId*, if the turn is running. */
  getRunStartedAt(chatId: string): number | null {
    const v = this.runStartedAtByChatId.get(chatId);
    return v === undefined ? null : v;
  }

  /** Last ``goal_state`` payload for *chatId*, if any frame has arrived this connection. */
  getGoalState(chatId: string): GoalStateWsPayload | undefined {
    return this.goalStateByChatId.get(chatId);
  }

  /** Approvals *chatId* is still waiting on, oldest first. */
  getPendingApprovals(chatId: string): PendingApproval[] {
    const pending = this.approvalsByChatId.get(chatId);
    if (pending === undefined) return [];
    const live = dropExpired(pending, Date.now());
    if (live.length !== pending.length) this.approvalsByChatId.set(chatId, live);
    return [...live];
  }

  getPendingChoices(chatId: string): PendingChoice[] {
    const pending = this.choicesByChatId.get(chatId);
    if (pending === undefined) return [];
    const live = dropExpiredChoices(pending, Date.now());
    if (live.length !== pending.length) this.choicesByChatId.set(chatId, live);
    return [...live];
  }

  sendChoiceAnswer(requestId: string, optionId: string, skipped = false, customText = ""): void {
    this.settleChoice(requestId);
    const typed = customText.trim();
    this.queueSend({
      type: "choice_answer",
      request_id: requestId,
      option_id: optionId,
      skipped: Boolean(skipped),
      ...(typed ? { custom_text: typed } : {}),
    });
  }

  /** Answer a pending approval, releasing the tool call that is waiting on it. */
  sendApprovalDecision(requestId: string, allowed: boolean, remember: boolean): void {
    this.settleApproval(requestId);
    this.queueSend({
      type: "approval_decision",
      request_id: requestId,
      allowed: Boolean(allowed),
      remember: Boolean(remember),
    });
  }

  private recordApproval(chatId: string, ev: InboundEvent): void {
    if (ev.event === "approval_request") {
      const pending = this.approvalsByChatId.get(chatId) ?? [];
      this.approvalsByChatId.set(
        chatId,
        addApproval(pending, ev, Date.now(), this.settledApprovals),
      );
      return;
    }
    if (ev.event === "approval_closed") {
      this.settleApproval(ev.request_id);
      const pending = this.approvalsByChatId.get(chatId);
      if (pending !== undefined) {
        this.approvalsByChatId.set(chatId, removeApproval(pending, ev.request_id));
      }
      return;
    }
    // A turn cannot end with a question still open: the tool that asked is gone,
    // so an answer would have nowhere to go.
    if (ev.event === "turn_end") this.approvalsByChatId.delete(chatId);
  }

  private recordChoice(chatId: string, ev: InboundEvent): void {
    if (ev.event === "choice_request") {
      const pending = this.choicesByChatId.get(chatId) ?? [];
      this.choicesByChatId.set(
        chatId,
        addChoice(pending, ev, Date.now(), this.settledChoices),
      );
      return;
    }
    if (ev.event === "choice_closed") {
      this.settleChoice(ev.request_id);
      const pending = this.choicesByChatId.get(chatId);
      if (pending !== undefined) {
        this.choicesByChatId.set(chatId, removeChoice(pending, ev.request_id));
      }
      return;
    }
    if (ev.event === "turn_end") this.choicesByChatId.delete(chatId);
  }

  private settleChoice(requestId: string): void {
    if (!requestId) return;
    this.settledChoices.add(requestId);
    if (this.settledChoices.size > NavinClient.SETTLED_CHOICES_MAX) {
      const excess = this.settledChoices.size - NavinClient.SETTLED_CHOICES_MAX;
      for (const id of [...this.settledChoices].slice(0, excess)) {
        this.settledChoices.delete(id);
      }
    }
  }

  private settleApproval(requestId: string): void {
    if (!requestId) return;
    this.settledApprovals.add(requestId);
    if (this.settledApprovals.size > NavinClient.SETTLED_APPROVALS_MAX) {
      // Insertion-ordered, so the oldest ids go first. They are only needed for
      // as long as a replayed frame could still mention them.
      const excess = this.settledApprovals.size - NavinClient.SETTLED_APPROVALS_MAX;
      for (const id of [...this.settledApprovals].slice(0, excess)) {
        this.settledApprovals.delete(id);
      }
    }
  }

  private recordGoalStatusForRunStrip(chatId: string, ev: InboundEvent): void {
    if (ev.event === "turn_end") {
      if (this.runStartedAtByChatId.has(chatId)) {
        this.runStartedAtByChatId.delete(chatId);
        this.emitRunStatus(chatId, null);
      }
      return;
    }
    if (ev.event !== "goal_status") return;
    if (ev.status === "running" && typeof ev.started_at === "number") {
      const previous = this.runStartedAtByChatId.get(chatId);
      this.runStartedAtByChatId.set(chatId, ev.started_at);
      if (previous !== ev.started_at) this.emitRunStatus(chatId, ev.started_at);
    } else if (this.runStartedAtByChatId.has(chatId)) {
      this.runStartedAtByChatId.delete(chatId);
      this.emitRunStatus(chatId, null);
    }
  }

  private recordGoalStateSnapshot(chatId: string, ev: InboundEvent): void {
    if (ev.event === "goal_state") {
      this.goalStateByChatId.set(chatId, ev.goal_state);
      return;
    }
    if (ev.event === "turn_end" && ev.goal_state != null && typeof ev.goal_state === "object") {
      this.goalStateByChatId.set(chatId, ev.goal_state);
    }
  }

  /** Subscribe to events for a given chat_id. Auto-attaches on the next open. */
  onChat(chatId: string, handler: EventHandler): Unsubscribe {
    let handlers = this.chatHandlers.get(chatId);
    if (!handlers) {
      handlers = new Set();
      this.chatHandlers.set(chatId, handlers);
    }
    handlers.add(handler);
    const pending = this.pendingInboundByChat.get(chatId);
    const needsCanonicalResync = this.pendingInboundOverflowChats.delete(chatId);
    if (pending !== undefined && pending.length > 0) {
      const flushed = pending.splice(0);
      this.pendingInboundByChat.delete(chatId);
      for (const ev of flushed) {
        handler(ev);
      }
    }
    this.attach(chatId);
    if (needsCanonicalResync) {
      // The bounded live buffer deliberately shed replaceable progress/delta
      // frames. Reload the durable transcript after the subscriber is installed
      // so the UI converges instead of silently presenting a partial turn.
      queueMicrotask(() => this.emitSessionUpdate(chatId, "thread"));
    }
    return () => {
      const current = this.chatHandlers.get(chatId);
      if (!current) return;
      current.delete(handler);
      if (current.size === 0) this.chatHandlers.delete(chatId);
    };
  }

  connect(): void {
    if (this.socket && this.socket.readyState < WS_CLOSING) return;
    this.intentionallyClosed = false;
    this.setStatus("connecting");
    let sock: WebSocket;
    try {
      sock = this.socketFactory(this.currentUrl);
    } catch {
      this.socket = null;
      if (this.shouldReconnect) {
        this.scheduleReconnect("retry");
      } else {
        this.setStatus("error");
      }
      return;
    }
    this.socket = sock;
    sock.onopen = () => this.handleOpen();
    sock.onmessage = (ev) => this.handleMessage(ev);
    sock.onerror = () => this.setStatus("error");
    sock.onclose = (ev) => this.handleClose(ev);
  }

  close(): void {
    this.intentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const sock = this.socket;
    this.socket = null;
    try {
      sock?.close();
    } catch {
      // ignore
    }
    this.setStatus("closed");
  }

  /** Ask the server to provision a new chat_id; resolves with the assigned id. */
  newChat(timeoutMs: number = 5_000, workspaceScope?: WorkspaceScopePayload | null): Promise<string> {
    if (this.pendingNewChat) {
      return Promise.reject(new Error("newChat already in flight"));
    }
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingNewChat = null;
        reject(new Error("newChat timed out"));
      }, timeoutMs);
      this.pendingNewChat = { resolve, reject, timer };
      this.queueSend({
        type: "new_chat",
        ...(workspaceScope ? { workspace_scope: workspaceScope } : {}),
      });
    });
  }

  transcribeAudio(
    dataUrl: string,
    options?: { durationMs?: number; timeoutMs?: number },
  ): Promise<string> {
    const requestId = crypto.randomUUID();
    const timeoutMs = options?.timeoutMs ?? 120_000;
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingTranscriptions.delete(requestId);
        reject(new Error("transcription timed out"));
      }, timeoutMs);
      this.pendingTranscriptions.set(requestId, { resolve, reject, timer });
      this.queueSend({
        type: "transcribe_audio",
        request_id: requestId,
        data_url: dataUrl,
        ...(options?.durationMs !== undefined ? { duration_ms: options.durationMs } : {}),
      });
    });
  }

  // -- Realtime voice session (Pro+/Team) ---------------------------------

  onVoiceSessionEvent(handler: (ev: InboundEvent) => void): Unsubscribe {
    this.voiceSessionHandlers.add(handler);
    return () => {
      this.voiceSessionHandlers.delete(handler);
    };
  }

  startVoiceSession(options?: { sessionId?: string; timeoutMs?: number }): Promise<string> {
    const requestId = crypto.randomUUID();
    const timeoutMs = options?.timeoutMs ?? 30_000;
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingVoiceStarts.delete(requestId);
        reject(new Error("voice_session_start timed out"));
      }, timeoutMs);
      this.pendingVoiceStarts.set(requestId, { resolve, reject, timer });
      this.queueSend({
        type: "voice_session_start",
        request_id: requestId,
        ...(options?.sessionId ? { session_id: options.sessionId } : {}),
      });
    });
  }

  sendVoiceAudioChunk(
    sessionId: string,
    options: {
      dataUrl?: string;
      durationMs?: number;
      final?: boolean;
      speakText?: string;
      bargeIn?: boolean;
    } = {},
  ): string {
    const requestId = crypto.randomUUID();
    this.queueSend({
      type: "voice_audio_chunk",
      request_id: requestId,
      session_id: sessionId,
      ...(options.dataUrl ? { data_url: options.dataUrl } : {}),
      ...(options.durationMs !== undefined ? { duration_ms: options.durationMs } : {}),
      ...(options.final !== undefined ? { final: options.final } : {}),
      ...(options.speakText ? { speak_text: options.speakText } : {}),
      ...(options.bargeIn ? { barge_in: true, cancel_tts: true } : {}),
    });
    return requestId;
  }

  endVoiceSession(
    sessionId: string,
    options?: { speakText?: string; bargeIn?: boolean },
  ): string {
    const requestId = crypto.randomUUID();
    this.queueSend({
      type: "voice_session_end",
      request_id: requestId,
      session_id: sessionId,
      ...(options?.speakText ? { speak_text: options.speakText } : {}),
      ...(options?.bargeIn ? { barge_in: true, cancel_tts: true } : {}),
    });
    return requestId;
  }

  // -- Interactive terminals (Dev mode) -----------------------------------

  requestTerminalShells(timeoutMs: number = 8_000): Promise<TerminalShellInfo[]> {
    return new Promise<TerminalShellInfo[]>((resolve, reject) => {
      const entry = {
        resolve,
        timer: setTimeout(() => {
          this.pendingShellRequests = this.pendingShellRequests.filter((e) => e !== entry);
          reject(new Error("terminal shells request timed out"));
        }, timeoutMs),
      };
      this.pendingShellRequests.push(entry);
      this.queueSend({ type: "terminal_shells" });
    });
  }

  onTerminal(terminalId: string, handler: (ev: TerminalEvent) => void): Unsubscribe {
    this.terminalHandlers.set(terminalId, handler);
    return () => {
      if (this.terminalHandlers.get(terminalId) === handler) {
        this.terminalHandlers.delete(terminalId);
      }
    };
  }

  openTerminal(options: {
    terminalId: string;
    shell?: string;
    chatId?: string;
    cols?: number;
    rows?: number;
    /** Folder to start in; ignored by the server unless inside the project. */
    cwd?: string;
    /** Isolated terminal: the shell runs inside the agent's OS sandbox. */
    sandbox?: boolean;
  }): void {
    this.queueSend({
      type: "terminal_open",
      terminal_id: options.terminalId,
      ...(options.shell ? { shell: options.shell } : {}),
      ...(options.chatId ? { chat_id: options.chatId } : {}),
      ...(options.cols ? { cols: options.cols } : {}),
      ...(options.rows ? { rows: options.rows } : {}),
      ...(options.cwd ? { cwd: options.cwd } : {}),
      ...(options.sandbox ? { sandbox: true } : {}),
    });
  }

  sendTerminalInput(terminalId: string, dataB64: string): void {
    this.queueSend({ type: "terminal_input", terminal_id: terminalId, data: dataB64 });
  }

  resizeTerminal(terminalId: string, cols: number, rows: number): void {
    this.queueSend({ type: "terminal_resize", terminal_id: terminalId, cols, rows });
  }

  closeTerminal(terminalId: string): void {
    this.terminalHandlers.delete(terminalId);
    this.queueSend({ type: "terminal_close", terminal_id: terminalId });
  }

  /**
   * Stream a Tab/ghost completion over the socket (token deltas + final text).
   * Falls back to rejecting when the socket is not open so callers can use HTTP.
   */
  requestAssistComplete(
    args: {
      path: string;
      prefix: string;
      suffix: string;
      language?: string;
      recentEdits?: {
        path: string;
        line: number;
        removed: string;
        inserted: string;
      }[];
    },
    options: {
      onPartial?: (text: string) => void;
      onMeta?: (meta: AssistMeta) => void;
      signal?: AbortSignal;
      timeoutMs?: number;
    } = {},
  ): Promise<string> {
    if (!this.socket || this.socket.readyState !== WS_OPEN) {
      return Promise.reject(new Error("socket not open"));
    }
    const requestId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `assist-${Date.now()}-${Math.random().toString(16).slice(2)}`;

    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.assistHandlers.delete(requestId);
        this.queueSend({ type: "assist_cancel", request_id: requestId });
        reject(new Error("assist timed out"));
      }, options.timeoutMs ?? 20_000);

      const abort = () => {
        clearTimeout(timer);
        this.assistHandlers.delete(requestId);
        this.queueSend({ type: "assist_cancel", request_id: requestId });
        reject(new DOMException("Aborted", "AbortError"));
      };
      if (options.signal?.aborted) {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      options.signal?.addEventListener("abort", abort, { once: true });

      this.assistHandlers.set(requestId, {
        onPartial: options.onPartial,
        onMeta: options.onMeta,
        resolve: (completion) => {
          clearTimeout(timer);
          options.signal?.removeEventListener("abort", abort);
          this.assistHandlers.delete(requestId);
          resolve(completion);
        },
        reject: (error) => {
          clearTimeout(timer);
          options.signal?.removeEventListener("abort", abort);
          this.assistHandlers.delete(requestId);
          reject(error);
        },
        timer,
      });

      this.queueSend({
        type: "assist_complete",
        request_id: requestId,
        path: args.path,
        prefix: args.prefix,
        suffix: args.suffix,
        ...(args.language ? { language: args.language } : {}),
        ...(args.recentEdits?.length ? { recent_edits: args.recentEdits } : {}),
      });
    });
  }

  /**
   * Stream a Cmd+K rewrite over the socket (token deltas + final replacement).
   * Falls back to rejecting when the socket is not open so callers can use HTTP.
   */
  requestAssistEdit(
    args: {
      path: string;
      selection: string;
      instruction: string;
      prefix?: string;
      suffix?: string;
      language?: string;
    },
    options: {
      onPartial?: (text: string) => void;
      onMeta?: (meta: AssistMeta) => void;
      signal?: AbortSignal;
      timeoutMs?: number;
    } = {},
  ): Promise<string> {
    if (!this.socket || this.socket.readyState !== WS_OPEN) {
      return Promise.reject(new Error("socket not open"));
    }
    const requestId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `assist-edit-${Date.now()}-${Math.random().toString(16).slice(2)}`;

    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.assistHandlers.delete(requestId);
        this.queueSend({ type: "assist_cancel", request_id: requestId });
        reject(new Error("assist timed out"));
      }, options.timeoutMs ?? 45_000);

      const abort = () => {
        clearTimeout(timer);
        this.assistHandlers.delete(requestId);
        this.queueSend({ type: "assist_cancel", request_id: requestId });
        reject(new DOMException("Aborted", "AbortError"));
      };
      if (options.signal?.aborted) {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      options.signal?.addEventListener("abort", abort, { once: true });

      this.assistHandlers.set(requestId, {
        onPartial: options.onPartial,
        onMeta: options.onMeta,
        resolve: (text) => {
          clearTimeout(timer);
          options.signal?.removeEventListener("abort", abort);
          this.assistHandlers.delete(requestId);
          resolve(text);
        },
        reject: (error) => {
          clearTimeout(timer);
          options.signal?.removeEventListener("abort", abort);
          this.assistHandlers.delete(requestId);
          reject(error);
        },
        timer,
      });

      this.queueSend({
        type: "assist_edit",
        request_id: requestId,
        path: args.path,
        selection: args.selection,
        instruction: args.instruction,
        prefix: args.prefix ?? "",
        suffix: args.suffix ?? "",
        ...(args.language ? { language: args.language } : {}),
      });
    });
  }

  onMobilePreview(
    previewId: string,
    handler: (ev: MobilePreviewEvent) => void,
  ): Unsubscribe {
    this.mobilePreviewHandlers.set(previewId, handler);
    return () => {
      if (this.mobilePreviewHandlers.get(previewId) === handler) {
        this.mobilePreviewHandlers.delete(previewId);
      }
    };
  }

  requestMobilePreviewStatus(
    options: { timeoutMs?: number; install?: boolean } | number = 12_000,
  ): Promise<MobilePreviewStatus> {
    const timeoutMs =
      typeof options === "number" ? options : (options.timeoutMs ?? 12_000);
    const install = typeof options === "number" ? false : Boolean(options.install);
    return new Promise<MobilePreviewStatus>((resolve, reject) => {
      const entry = {
        resolve,
        timer: setTimeout(() => {
          this.pendingMobileStatusRequests = this.pendingMobileStatusRequests.filter(
            (e) => e !== entry,
          );
          reject(new Error("mobile preview status timed out"));
        }, timeoutMs),
      };
      this.pendingMobileStatusRequests.push(entry);
      this.queueSend({
        type: "mobile_preview_status",
        ...(install ? { install: true } : {}),
      });
    });
  }

  /** Launch an AVD from the Mobile panel (restart a closed emulator). */
  startMobileAvd(
    avd: string,
    timeoutMs = 30_000,
  ): Promise<{ ok: boolean; detail?: string | null }> {
    return new Promise((resolve, reject) => {
      const entry = {
        resolve,
        timer: setTimeout(() => {
          this.pendingMobileAvdRequests = this.pendingMobileAvdRequests.filter(
            (e) => e !== entry,
          );
          reject(new Error("mobile avd start timed out"));
        }, timeoutMs),
      };
      this.pendingMobileAvdRequests.push(entry);
      this.queueSend({ type: "mobile_preview_start_avd", avd });
    });
  }

  openMobilePreview(options: {
    previewId: string;
    serial?: string;
    fps?: number;
  }): void {
    this.queueSend({
      type: "mobile_preview_open",
      preview_id: options.previewId,
      ...(options.serial ? { serial: options.serial } : {}),
      ...(options.fps != null ? { fps: options.fps } : {}),
    });
  }

  closeMobilePreview(previewId: string): void {
    this.mobilePreviewHandlers.delete(previewId);
    this.queueSend({ type: "mobile_preview_close", preview_id: previewId });
  }

  mobilePreviewTap(previewId: string, x: number, y: number): void {
    this.queueSend({ type: "mobile_preview_tap", preview_id: previewId, x, y });
  }

  /**
   * Drive the agent's browser from its live view.
   *
   * The point is given in the streamed frame's own pixels together with that
   * frame's size, because the stream is scaled and the page behind it is not.
   */
  agentBrowserInput(
    chatId: string,
    action: "click" | "move" | "scroll" | "text" | "key",
    payload: {
      x?: number;
      y?: number;
      width?: number;
      height?: number;
      dx?: number;
      dy?: number;
      text?: string;
      key?: string;
      count?: number;
    } = {},
  ): void {
    this.queueSend({ type: "agent_browser_input", chat_id: chatId, action, ...payload });
  }

  /** Close the browser this chat opened, freeing Chromium. */
  agentBrowserClose(chatId: string): void {
    this.queueSend({ type: "agent_browser_close", chat_id: chatId });
  }

  mobilePreviewSwipe(
    previewId: string,
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    durationMs = 300,
  ): void {
    this.queueSend({
      type: "mobile_preview_swipe",
      preview_id: previewId,
      x1,
      y1,
      x2,
      y2,
      duration_ms: durationMs,
    });
  }

  mobilePreviewKey(previewId: string, keycode: string): void {
    this.queueSend({
      type: "mobile_preview_key",
      preview_id: previewId,
      keycode,
    });
  }

  mobilePreviewText(previewId: string, text: string): void {
    this.queueSend({
      type: "mobile_preview_text",
      preview_id: previewId,
      text,
    });
  }

  mobilePreviewUiDump(previewId: string): void {
    this.queueSend({ type: "mobile_preview_ui_dump", preview_id: previewId });
  }

  /** Ask the server to create a non-destructive fork before a user-message index. */
  forkChat(
    sourceChatId: string,
    beforeUserIndex: number,
    title?: string,
    timeoutMs: number = 5_000,
  ): Promise<string> {
    if (this.pendingNewChat) {
      return Promise.reject(new Error("newChat already in flight"));
    }
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingNewChat = null;
        reject(new Error("forkChat timed out"));
      }, timeoutMs);
      this.pendingNewChat = { resolve, reject, timer };
      this.queueSend({
        type: "fork_chat",
        source_chat_id: sourceChatId,
        before_user_index: beforeUserIndex,
        ...(title?.trim() ? { title: title.trim() } : {}),
      });
    });
  }

  attach(chatId: string): void {
    this.knownChats.add(chatId);
    if (this.socket?.readyState === WS_OPEN) {
      this.queueSend({ type: "attach", chat_id: chatId });
    }
  }

  sendMessage(
    chatId: string,
    content: string,
    media?: OutboundMedia[],
    options?: {
      cliApps?: OutboundCliAppMention[];
      mcpPresets?: OutboundMcpPresetMention[];
      fileMentions?: OutboundFileMention[];
      documentTemplate?: { category: string; name: string };
      mediaTemplate?: { id: string; title?: string };
      mediaTemplates?: { id: string; title?: string }[];
      workspaceScope?: WorkspaceScopePayload | null;
      turnId?: string;
      modelPreset?: string;
      productModule?: string;
      /** Absolute/relative paths of open editor tabs (Code workbench). */
      openFiles?: string[];
    },
  ): void {
    this.knownChats.add(chatId);
    const frame: Outbound = {
      type: "message",
      chat_id: chatId,
      content,
      ...(media && media.length > 0 ? { media } : {}),
      ...(options?.cliApps?.length ? { cli_apps: options.cliApps } : {}),
      ...(options?.mcpPresets?.length ? { mcp_presets: options.mcpPresets } : {}),
      ...(options?.fileMentions?.length ? { file_mentions: options.fileMentions } : {}),
      ...(options?.documentTemplate
        ? { document_template: options.documentTemplate }
        : {}),
      ...(options?.mediaTemplate
        ? { media_template: options.mediaTemplate }
        : {}),
      ...(options?.mediaTemplates?.length
        ? { media_templates: options.mediaTemplates }
        : {}),
      ...(options?.workspaceScope ? { workspace_scope: options.workspaceScope } : {}),
      ...(options?.turnId ? { turn_id: options.turnId } : {}),
      ...(options?.modelPreset ? { model_preset: options.modelPreset } : {}),
      ...(options?.productModule
        ? { product_module: options.productModule }
        : {}),
      ...(options?.openFiles?.length ? { open_files: options.openFiles } : {}),
      webui: true,
    };
    this.queueSend(frame);
  }

  setWorkspaceScope(chatId: string, workspaceScope: WorkspaceScopePayload): void {
    this.knownChats.add(chatId);
    this.queueSend({
      type: "set_workspace_scope",
      chat_id: chatId,
      workspace_scope: workspaceScope,
    });
  }

  // -- internals ---------------------------------------------------------

  private setStatus(status: ConnectionStatus): void {
    if (this.status_ === status) return;
    this.status_ = status;
    for (const handler of this.statusHandlers) handler(status);
  }

  private clearRunStatusesForReconnect(): void {
    if (this.runStartedAtByChatId.size === 0) return;
    const chatIds = [...this.runStartedAtByChatId.keys()];
    this.runStartedAtByChatId.clear();
    for (const chatId of chatIds) this.emitRunStatus(chatId, null);
  }

  private handleOpen(): void {
    this.setStatus("open");
    // Re-attach every known chat_id so deliveries continue routing after a drop.
    for (const chatId of this.knownChats) {
      this.rawSend({ type: "attach", chat_id: chatId });
    }
    // Flush anything queued during reconnect.
    const queued = this.sendQueue.splice(0);
    for (const frame of queued) this.rawSend(frame);
  }

  private handleMessage(ev: MessageEvent): void {
    let parsed: InboundEvent;
    try {
      parsed = JSON.parse(typeof ev.data === "string" ? ev.data : "") as InboundEvent;
    } catch {
      if (wsInboundDebugEnabled()) {
        const raw = typeof ev.data === "string" ? ev.data : String(ev.data);
        console.warn(
          "[navin ws inbound] invalid JSON",
          raw.length > 400 ? `${raw.slice(0, 400)}… (${raw.length} chars)` : raw,
        );
      }
      return;
    }

    if (wsInboundDebugEnabled()) {
      console.log("[navin ws inbound]", summarizeInboundWsPayload(parsed));
    }

    if (parsed.event === "ready") {
      // Authentication and protocol setup completed. Resetting on TCP open
      // alone would collapse repeated auth failures back to the shortest delay.
      this.reconnectAttempts = 0;
      this.readyChatId = parsed.chat_id;
      this.knownChats.add(parsed.chat_id);
      if (typeof parsed.org_role === "string" && parsed.org_role.trim()) {
        this.orgRole = parsed.org_role.trim();
      }
      return;
    }

    if (
      parsed.event === "presence_sync" ||
      parsed.event === "presence_join" ||
      parsed.event === "presence_leave"
    ) {
      this.applyPresenceEvent(parsed);
      return;
    }

    if (parsed.event === "member_cursor") {
      // Optional cursor fan-out; no UI consumer yet.
      return;
    }

    if (parsed.event === "attached") {
      // ``attach`` marks the id known before the round-trip; ``new_chat`` /
      // ``fork_chat`` do not. Only an unknown id may settle pendingNewChat -
      // otherwise a concurrent attach steals the promise and the real create
      // still lands as an orphan empty chat.
      const wasKnown = this.knownChats.has(parsed.chat_id);
      this.knownChats.add(parsed.chat_id);
      const pending = this.pendingNewChat;
      if (pending && shouldResolvePendingNewChat(true, wasKnown)) {
        clearTimeout(pending.timer);
        pending.resolve(parsed.chat_id);
        this.pendingNewChat = null;
      }
      this.dispatch(parsed.chat_id, parsed);
      return;
    }

    if (parsed.event === "runtime_model_updated") {
      const frame = parsed as {
        chat_id?: string;
        model_name?: string;
        model_preset?: string | null;
        reason?: string;
        previous_model?: string;
        used_percent?: number;
      };
      this.emitRuntimeModelUpdate(frame.model_name || null, frame.model_preset ?? null, {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : null,
        reason: typeof frame.reason === "string" ? frame.reason : null,
        previousModel: typeof frame.previous_model === "string" ? frame.previous_model : null,
        usedPercent: typeof frame.used_percent === "number" ? frame.used_percent : null,
      });
      return;
    }

    if (parsed.event === "account_updated") {
      for (const handler of this.accountUpdatedHandlers) {
        handler();
      }
      return;
    }

    if (parsed.event === "board_updated") {
      const projectPath = (parsed as { project_path?: string }).project_path ?? null;
      for (const handler of this.boardUpdateHandlers) {
        handler(projectPath);
      }
      return;
    }

    if (parsed.event === "montage_updated") {
      const frame = parsed as {
        project_path?: string;
        kind?: string;
        name?: string;
        job?: MontageUpdate["job"];
      };
      const update: MontageUpdate = {
        projectPath: frame.project_path ?? null,
        kind:
          frame.kind === "job" || frame.kind === "assets" || frame.kind === "timeline"
            ? frame.kind
            : "timeline",
        name: frame.name ?? null,
        job: frame.job ?? null,
      };
      for (const handler of this.montageUpdateHandlers) {
        handler(update);
      }
      return;
    }

    if (parsed.event === "metagraph_updated") {
      const frame = parsed as {
        project_path?: string;
        generation?: number;
        view?: string;
        diff?: import("./types").MetagraphDiff;
      };
      const update: MetagraphUpdate = {
        projectPath: frame.project_path ?? null,
        generation: typeof frame.generation === "number" ? frame.generation : 0,
        view: typeof frame.view === "string" ? frame.view : "files",
        ...(frame.diff ? { diff: frame.diff } : {}),
      };
      for (const handler of this.metagraphUpdateHandlers) {
        handler(update);
      }
      return;
    }

    if (parsed.event === "terminal_open_request") {
      const frame = parsed as unknown as { chat_id?: string; shell?: string; cwd?: string };
      const request: TerminalOpenRequest = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        ...(typeof frame.shell === "string" && frame.shell ? { shell: frame.shell } : {}),
        ...(typeof frame.cwd === "string" && frame.cwd ? { cwd: frame.cwd } : {}),
      };
      for (const handler of this.terminalOpenRequestHandlers) {
        handler(request);
      }
      return;
    }

    if (parsed.event === "agent_exec") {
      const frame = parsed as unknown as {
        chat_id?: string;
        id?: string;
        phase?: string;
        command?: string;
        cwd?: string;
        background?: boolean;
        data?: string;
        exit_code?: number;
        sandbox?: string;
        sandbox_lifted?: boolean;
      };
      if (typeof frame.id !== "string" || !frame.id) return;
      const phase =
        frame.phase === "start" || frame.phase === "exit" ? frame.phase : "output";
      const update: AgentExecUpdate = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        id: frame.id,
        phase,
        background: Boolean(frame.background),
        ...(typeof frame.command === "string" && frame.command
          ? { command: frame.command }
          : {}),
        ...(typeof frame.cwd === "string" && frame.cwd ? { cwd: frame.cwd } : {}),
        ...(typeof frame.data === "string" && frame.data ? { data: frame.data } : {}),
        ...(typeof frame.exit_code === "number" ? { exitCode: frame.exit_code } : {}),
        ...(typeof frame.sandbox === "string" && frame.sandbox ? { sandbox: frame.sandbox } : {}),
        ...(frame.sandbox_lifted === true ? { sandboxLifted: true } : {}),
      };
      for (const handler of this.agentExecHandlers) {
        handler(update);
      }
      return;
    }

    if (parsed.event === "agent_browser") {
      const frame = parsed as unknown as {
        chat_id?: string;
        id?: string;
        phase?: string;
        url?: string;
        title?: string;
        action?: string;
        data?: string;
        width?: number;
        height?: number;
      };
      if (typeof frame.id !== "string" || !frame.id) return;
      const phase =
        frame.phase === "start" || frame.phase === "action" || frame.phase === "exit"
          ? frame.phase
          : "frame";
      const update: AgentBrowserUpdate = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        id: frame.id,
        phase,
        ...(typeof frame.url === "string" && frame.url ? { url: frame.url } : {}),
        ...(typeof frame.title === "string" && frame.title
          ? { title: frame.title }
          : {}),
        ...(typeof frame.action === "string" && frame.action
          ? { action: frame.action }
          : {}),
        ...(typeof frame.data === "string" && frame.data ? { data: frame.data } : {}),
        ...(typeof frame.width === "number" ? { width: frame.width } : {}),
        ...(typeof frame.height === "number" ? { height: frame.height } : {}),
      };
      for (const handler of this.agentBrowserHandlers) {
        handler(update);
      }
      return;
    }

    if (parsed.event === "editor_open_request") {
      const frame = parsed as unknown as {
        chat_id?: string;
        path?: string;
        kind?: string;
        line?: number;
      };
      if (typeof frame.path !== "string" || !frame.path) return;
      const request: EditorOpenRequest = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        path: frame.path,
        kind: frame.kind === "folder" ? "folder" : "file",
        ...(typeof frame.line === "number" && frame.line >= 1
          ? { line: frame.line }
          : {}),
      };
      for (const handler of this.editorOpenRequestHandlers) {
        handler(request);
      }
      return;
    }

    if (parsed.event === "composer_mode_request") {
      const frame = parsed as unknown as {
        chat_id?: string;
        mode?: string;
      };
      const mode = typeof frame.mode === "string" ? frame.mode.trim().toLowerCase() : "";
      // "ask" included: the agent may hand a turn back to read-only Q&A and
      // the badge must follow, exactly like it follows a switch to Agent.
      if (
        mode !== "ask"
        && mode !== "plan"
        && mode !== "agent"
        && mode !== "review"
        && mode !== "security"
        && mode !== "debug"
        && mode !== "montage"
      ) {
        return;
      }
      const request: ComposerModeRequest = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        mode,
      };
      for (const handler of this.composerModeRequestHandlers) {
        handler(request);
      }
      return;
    }

    if (parsed.event === "product_module_request") {
      const frame = parsed as unknown as {
        chat_id?: string;
        module?: string;
      };
      const module = typeof frame.module === "string"
        ? frame.module.trim().toLowerCase()
        : "";
      if (!(PRODUCT_MODULES as readonly string[]).includes(module)) return;
      const request: ProductModuleRequest = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        module: module as ProductModuleId,
      };
      for (const handler of this.productModuleRequestHandlers) {
        handler(request);
      }
      return;
    }

    if (parsed.event === "subagent_progress") {
      const frame = parsed as unknown as {
        chat_id?: string;
        task_id?: string;
        label?: string;
        phase?: string;
        status_line?: string;
        model?: string;
        iteration?: number;
        done?: boolean;
        error?: string;
        task_description?: string;
        started_ms_ago?: number;
      };
      const taskId = typeof frame.task_id === "string" ? frame.task_id.trim() : "";
      if (!taskId) return;
      const update: SubagentProgressUpdate = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        taskId,
        label:
          typeof frame.label === "string" && frame.label.trim()
            ? frame.label.trim()
            : taskId,
        phase:
          typeof frame.phase === "string" && frame.phase.trim()
            ? frame.phase.trim()
            : "initializing",
        statusLine:
          typeof frame.status_line === "string" && frame.status_line.trim()
            ? frame.status_line.trim()
            : "Working…",
        model: typeof frame.model === "string" ? frame.model : undefined,
        iteration: typeof frame.iteration === "number" ? frame.iteration : 0,
        done: Boolean(frame.done),
        error: typeof frame.error === "string" ? frame.error : undefined,
        taskDescription:
          typeof frame.task_description === "string"
            ? frame.task_description
            : undefined,
        startedMsAgo:
          typeof frame.started_ms_ago === "number" && frame.started_ms_ago >= 0
            ? frame.started_ms_ago
            : undefined,
      };
      for (const handler of this.subagentProgressHandlers) {
        handler(update);
      }
      return;
    }

    if (parsed.event === "preview_open_request") {
      const frame = parsed as unknown as {
        chat_id?: string;
        kind?: string;
        url?: string;
      };
      const kind = frame.kind === "mobile" ? "mobile" : "web";
      if (kind === "web" && (typeof frame.url !== "string" || !frame.url.trim())) {
        return;
      }
      const request: PreviewOpenRequest = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        kind,
        ...(kind === "web" && typeof frame.url === "string"
          ? { url: frame.url.trim() }
          : {}),
      };
      for (const handler of this.previewOpenRequestHandlers) {
        handler(request);
      }
      return;
    }

    if (parsed.event === "file_preview_open_request") {
      const frame = parsed as unknown as {
        chat_id?: string;
        path?: string;
      };
      if (typeof frame.path !== "string" || !frame.path.trim()) return;
      const request: FilePreviewOpenRequest = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        path: frame.path.trim(),
      };
      for (const handler of this.filePreviewOpenRequestHandlers) {
        handler(request);
      }
      return;
    }

    if (parsed.event === "artifact_upsert") {
      const frame = parsed as unknown as {
        chat_id?: string;
        artifact?: ArtifactRecord;
        restored?: boolean;
      };
      const artifact = frame.artifact;
      if (!artifact || typeof artifact !== "object") return;
      if (typeof artifact.id !== "string" || !artifact.id.trim()) return;
      if (typeof artifact.title !== "string") return;
      if (typeof artifact.type !== "string" || !artifact.type.trim()) return;
      const payload: ArtifactUpsertPayload = {
        restored: frame.restored === true,
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        artifact: {
          ...artifact,
          id: artifact.id.trim(),
          title: artifact.title,
          type: artifact.type,
          content: typeof artifact.content === "string" ? artifact.content : "",
        },
      };
      for (const handler of this.artifactUpsertHandlers) {
        handler(payload);
      }
      return;
    }

    if (parsed.event === "artifact_select") {
      const frame = parsed as unknown as {
        chat_id?: string;
        artifact_id?: string;
      };
      if (typeof frame.artifact_id !== "string" || !frame.artifact_id.trim()) return;
      const payload: ArtifactSelectPayload = {
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : "",
        artifactId: frame.artifact_id.trim(),
      };
      for (const handler of this.artifactSelectHandlers) {
        handler(payload);
      }
      return;
    }

    if (parsed.event === "notification") {
      const frame = parsed as unknown as Record<string, unknown>;
      const title = typeof frame.title === "string" ? frame.title.trim() : "";
      if (!title) return;
      const notification: ServerNotification = {
        title,
        detail: typeof frame.detail === "string" ? frame.detail : undefined,
        level: typeof frame.level === "string" ? frame.level : undefined,
        source: typeof frame.source === "string" ? frame.source : undefined,
        key: typeof frame.key === "string" ? frame.key : undefined,
        chatId: typeof frame.chat_id === "string" ? frame.chat_id : null,
      };
      for (const handler of this.notificationHandlers) {
        handler(notification);
      }
      return;
    }

    if (parsed.event === "terminal_shells") {
      const shells = (parsed as { shells?: TerminalShellInfo[] }).shells ?? [];
      for (const entry of this.pendingShellRequests.splice(0)) {
        clearTimeout(entry.timer);
        entry.resolve(shells);
      }
      return;
    }

    if (
      parsed.event === "assist_delta" ||
      parsed.event === "assist_done" ||
      parsed.event === "assist_error"
    ) {
      const requestId = String((parsed as { request_id?: string }).request_id || "");
      const handler = requestId ? this.assistHandlers.get(requestId) : undefined;
      if (!handler) return;
      if (parsed.event === "assist_delta") {
        const text = String((parsed as { text?: string }).text || "");
        if (text) handler.onPartial?.(text);
        return;
      }
      if (parsed.event === "assist_error") {
        handler.reject(
          new Error(String((parsed as { detail?: string }).detail || "assist failed")),
        );
        return;
      }
      if ((parsed as { cancelled?: boolean }).cancelled) {
        handler.resolve("");
        return;
      }
      const done = parsed as {
        completion?: string;
        replacement?: string;
        model?: string;
        route?: string;
        latency_ms?: number;
        ttft_ms?: number;
      };
      handler.onMeta?.({
        ...(done.model ? { model: done.model } : {}),
        ...(done.route ? { route: done.route } : {}),
        ...(typeof done.latency_ms === "number" ? { latency_ms: done.latency_ms } : {}),
        ...(typeof done.ttft_ms === "number" ? { ttft_ms: done.ttft_ms } : {}),
      });
      handler.resolve(String(done.replacement || done.completion || ""));
      return;
    }

    if (
      parsed.event === "terminal_opened" ||
      parsed.event === "terminal_output" ||
      parsed.event === "terminal_exit" ||
      parsed.event === "terminal_error"
    ) {
      const ev = parsed as unknown as TerminalEvent;
      if (ev.terminal_id) {
        this.terminalHandlers.get(ev.terminal_id)?.(ev);
      }
      return;
    }

    if (parsed.event === "mobile_preview_status") {
      const raw = parsed as MobilePreviewStatus & { event: string };
      const status: MobilePreviewStatus = {
        ready: Boolean(raw.ready),
        platform: raw.platform ?? null,
        stacks: raw.stacks ?? [],
        adb: raw.adb ?? null,
        sdk: raw.sdk ?? null,
        source: raw.source ?? null,
        devices: raw.devices ?? [],
        pending_devices: raw.pending_devices ?? [],
        avds: raw.avds ?? [],
        error: raw.error ?? null,
        help: raw.help ?? null,
        fixes: raw.fixes ?? [],
        can_auto_install: Boolean(raw.can_auto_install),
        acceleration_ok: raw.acceleration_ok,
        acceleration_detail: raw.acceleration_detail ?? null,
        stable: raw.stable,
        block_local_emulator: raw.block_local_emulator,
        recommendations: raw.recommendations ?? [],
        guide: raw.guide ?? null,
        ios: raw.ios ?? null,
        install_attempt: raw.install_attempt ?? null,
      };
      for (const entry of this.pendingMobileStatusRequests.splice(0)) {
        clearTimeout(entry.timer);
        entry.resolve(status);
      }
      return;
    }

    if (parsed.event === "mobile_preview_avd_started") {
      const raw = parsed as { ok?: boolean; detail?: string | null };
      for (const entry of this.pendingMobileAvdRequests.splice(0)) {
        clearTimeout(entry.timer);
        entry.resolve({ ok: Boolean(raw.ok), detail: raw.detail ?? null });
      }
      return;
    }

    if (
      parsed.event === "mobile_preview_opened" ||
      parsed.event === "mobile_preview_frame" ||
      parsed.event === "mobile_preview_logs" ||
      parsed.event === "mobile_preview_ui_dump" ||
      parsed.event === "mobile_preview_error" ||
      parsed.event === "mobile_preview_exit"
    ) {
      const ev = parsed as unknown as MobilePreviewEvent;
      if ("preview_id" in ev && ev.preview_id) {
        this.mobilePreviewHandlers.get(ev.preview_id)?.(ev);
      }
      return;
    }

    if (parsed.event === "transcription_result") {
      this.resolveTranscription(parsed.request_id, parsed.text);
      return;
    }

    if (parsed.event === "transcription_error") {
      this.rejectTranscription(parsed.request_id, parsed.detail || "error");
      return;
    }

    if (
      parsed.event === "voice_session_started"
      || parsed.event === "voice_session_ended"
      || parsed.event === "transcript_partial"
      || parsed.event === "tts_audio"
      || parsed.event === "tts_cancelled"
      || parsed.event === "voice_session_error"
    ) {
      if (parsed.event === "voice_session_started" && parsed.request_id) {
        const pending = this.pendingVoiceStarts.get(parsed.request_id);
        if (pending) {
          clearTimeout(pending.timer);
          this.pendingVoiceStarts.delete(parsed.request_id);
          pending.resolve(parsed.session_id);
        }
      }
      if (parsed.event === "voice_session_error" && parsed.request_id) {
        const pending = this.pendingVoiceStarts.get(parsed.request_id);
        if (pending) {
          clearTimeout(pending.timer);
          this.pendingVoiceStarts.delete(parsed.request_id);
          pending.reject(new Error(parsed.detail || "voice_session_error"));
        }
      }
      for (const handler of this.voiceSessionHandlers) {
        try {
          handler(parsed);
        } catch {
          // best-effort
        }
      }
      return;
    }

    if (parsed.event === "session_updated") {
      this.emitSessionUpdate(parsed.chat_id, parsed.scope, parsed.workspace_scope);
      return;
    }

    if (parsed.event === "error" && parsed.detail === "workspace_scope_rejected") {
      this.emitError({
        kind: "workspace_scope_rejected",
        reason: parsed.reason,
        chatId: parsed.chat_id,
      });
      if (this.pendingNewChat) {
        clearTimeout(this.pendingNewChat.timer);
        this.pendingNewChat.reject(new Error(`workspace_scope_rejected:${parsed.reason || ""}`));
        this.pendingNewChat = null;
      }
    }

    if (parsed.event === "error" && this.pendingNewChat) {
      clearTimeout(this.pendingNewChat.timer);
      const detail = typeof parsed.detail === "string" ? parsed.detail : "server error";
      const reason = typeof parsed.reason === "string" && parsed.reason ? `:${parsed.reason}` : "";
      this.pendingNewChat.reject(new Error(`${detail}${reason}`));
      this.pendingNewChat = null;
    }

    const chatId = (parsed as { chat_id?: string }).chat_id;
    if (chatId) {
      this.recordGoalStatusForRunStrip(chatId, parsed);
      this.recordGoalStateSnapshot(chatId, parsed);
      this.recordApproval(chatId, parsed);
      this.recordChoice(chatId, parsed);
      this.dispatch(chatId, parsed);
    }
  }

  private emitRuntimeModelUpdate(
    modelName: string | null,
    modelPreset?: string | null,
    meta?: RuntimeModelUpdateMeta,
  ): void {
    for (const handler of this.runtimeModelHandlers) {
      handler(modelName, modelPreset, meta);
    }
  }

  private emitSessionUpdate(
    chatId: string,
    scope?: SessionUpdateScope,
    workspaceScope?: WorkspaceScopePayload,
  ): void {
    for (const handler of this.sessionUpdateHandlers) {
      handler(chatId, scope, workspaceScope);
    }
  }

  private emitRunStatus(chatId: string, startedAt: number | null): void {
    for (const handler of this.runStatusHandlers) {
      handler(chatId, startedAt);
    }
  }

  private dispatch(chatId: string, ev: InboundEvent): void {
    const handlers = this.chatHandlers.get(chatId);
    if (handlers !== undefined && handlers.size > 0) {
      for (const h of handlers) {
        h(ev);
      }
      return;
    }
    let q = this.pendingInboundByChat.get(chatId);
    if (!q) {
      q = [];
      this.pendingInboundByChat.set(chatId, q);
    }
    q.push(ev);
    while (q.length > NavinClient.PENDING_INBOUND_MAX) {
      const replaceable = q.findIndex((item) => !pendingInboundEventIsCritical(item));
      q.splice(replaceable >= 0 ? replaceable : 0, 1);
      this.pendingInboundOverflowChats.add(chatId);
    }
  }

  private handleClose(event?: { code?: number }): void {
    this.socket = null;
    if (this.pendingNewChat) {
      clearTimeout(this.pendingNewChat.timer);
      this.pendingNewChat.reject(new Error("socket closed"));
      this.pendingNewChat = null;
    }
    this.rejectAllTranscriptions("socket closed");
    // Surface structured reasons *before* reconnect logic so the UI can
    // display the error even while the client transparently reconnects.
    // Browsers populate ``CloseEvent.code`` with the wire-level close code;
    // 1009 = Message Too Big (server's max frame guard).
    if (event?.code === 1009) {
      this.emitError({ kind: "message_too_big" });
    }
    const action = classifyWebSocketClose(event?.code);
    if (this.intentionallyClosed || !this.shouldReconnect || action === "stop") {
      this.setStatus("closed");
      return;
    }
    this.scheduleReconnect(action);
  }

  private emitError(error: StreamError): void {
    // Isolate subscribers so a throwing handler cannot abort the surrounding
    // ``handleClose`` flow (which still owes us a reconnect decision + status
    // update). We deliberately swallow here: error reporting is best-effort
    // and must never be allowed to compound the failure it's reporting.
    for (const handler of this.errorHandlers) {
      try {
        handler(error);
      } catch {
        // best-effort: subscriber fault must not stall transport bookkeeping
      }
    }
  }

  private resolveTranscription(requestId: string, text: string): void {
    const pending = this.pendingTranscriptions.get(requestId);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pendingTranscriptions.delete(requestId);
    pending.resolve(text);
  }

  private rejectTranscription(requestId: string | undefined, detail: string): void {
    if (!requestId) {
      this.rejectAllTranscriptions(detail);
      return;
    }
    const pending = this.pendingTranscriptions.get(requestId);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pendingTranscriptions.delete(requestId);
    pending.reject(new Error(detail));
  }

  private rejectAllTranscriptions(detail: string): void {
    for (const [requestId, pending] of this.pendingTranscriptions) {
      clearTimeout(pending.timer);
      pending.reject(new Error(detail));
      this.pendingTranscriptions.delete(requestId);
    }
  }

  private scheduleReconnect(action: Exclude<WebSocketCloseAction, "stop">): void {
    if (this.reconnectTimer || this.intentionallyClosed) return;
    this.clearRunStatusesForReconnect();
    this.setStatus("reconnecting");
    const attempt = this.reconnectAttempts++;
    const delay = reconnectDelayMs(
      attempt,
      this.baseBackoffMs,
      this.maxBackoffMs,
      this.random,
    );
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      if (this.intentionallyClosed) return;
      let refreshed = false;
      if (this.options.onReauth) {
        try {
          const url = await this.options.onReauth();
          if (this.intentionallyClosed) return;
          if (url) {
            this.currentUrl = url;
            refreshed = true;
          }
        } catch {
          // Generic network failures may retry the current URL.
        }
      }
      if (action === "reauth" && this.options.onReauth && !refreshed) {
        this.scheduleReconnect("reauth");
        return;
      }
      this.connect();
    }, delay);
  }

  private queueSend(frame: Outbound): void {
    if (this.socket?.readyState === WS_OPEN) {
      this.rawSend(frame);
    } else {
      this.sendQueue.push(frame);
    }
  }

  private rawSend(frame: Outbound): void {
    if (!this.socket) return;
    try {
      this.socket.send(JSON.stringify(frame));
    } catch {
      // Send failure will materialize as a close; queue the frame for retry.
      this.sendQueue.push(frame);
    }
  }
}
