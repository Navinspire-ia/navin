// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type {
  ApiServicePayload,
  AutomationsPayload,
  AutomationUpdatePayload,
  ChannelConfigurePayload,
  ChannelLoginPayload,
  ChannelValidationPayload,
  ChatSummary,
  CliAppsPayload,
  ComputerDiagnostics,
  ComputerModelsPayload,
  ContextUsagePayload,
  FileDiagnosticsPayload,
  FilePreviewPayload,
  ForgeKind,
  WorkspaceDiagnosticsPayload,
  FsListPayload,
  FsRootsPayload,
  GitBranchPayload,
  GitChangesPayload,
  GitCommitDetailPayload,
  GitCommitPayload,
  GitBlamePayload,
  GitDiffPayload,
  GitLogPayload,
  GitClonePayload,
  GitStatusPayload,
  DebugStatePayload,
  GithubCiStatusPayload,
  GithubFixCiPayload,
  GithubPrCreatePayload,
  GithubPrSyncPayload,
  GithubPrViewPayload,
  FileCreatePayload,
  FileDeletePayload,
  FilePastePayload,
  FileRenamePayload,
  FileSavePayload,
  ProjectReplacePayload,
  ProjectSearchPayload,
  FileTreePayload,
  ImageGenerationSettingsUpdate,
  MusicGenerationSettingsUpdate,
  VideoGenerationSettingsUpdate,
  McpPresetsPayload,
  NavinFeaturesPayload,
  ModelConfigurationCreate,
  ModelConfigurationImportEntry,
  ModelConfigurationImportResult,
  ModelConfigurationUpdate,
  NetworkSafetySettingsUpdate,
  PairingPayload,
  DocumentTemplatesPayload,
  MediaTemplatesPayload,
  PluginPacksPayload,
  ProviderModelsPayload,
  OllamaSetupPayload,
  OmniRouteSetupPayload,
  ProviderConnectionTestPayload,
  ProviderSettingsUpdate,
  ReviewActionPayload,
  ReviewChangesPayload,
  ReviewFilePayload,
  SessionDeleteResult,
  SessionAutomationsPayload,
  SettingsPayload,
  SettingsUpdate,
  SidebarStatePayload,
  ExecApprovalMode,
  ExecPolicyPayload,
  MetagraphPayload,
  InlineCompletionPayload,
  InlineEditPayload,
  ProjectAuditPayload,
  ProjectFileSearchPayload,
  ProjectSymbolsPayload,
  SkillDetail,
  SkillSetupResult,
  SkillsPayload,
  SkillApplyScope,
  WorkspaceSkillsDiscoverPayload,
  WorkspaceSkillsImportPayload,
  AppTemplateActionResult,
  AppTemplateSummary,
  AppTemplatesPayload,
  SlashCommand,
  SlashCommandLifecycle,
  TranscriptionSettingsUpdate,
  VoiceSettingsUpdate,
  WebSearchSettingsUpdate,
  WorkspacesPayload,
  WebuiThreadPersistedPayload,
  WorkspaceScopePayload,
} from "./types";
import i18n from "@/i18n";
import {
  GatewayUnreachableError,
  fetchWithTimeout,
  isTransportError,
  isUnreachableError,
  transportMessage,
} from "./http";
import { ApiError } from "./api-error";
import { noteGatewayAnswered, noteGatewayStalled } from "./gateway-pace";
import { isRetryableRead } from "./read-routes";

/** Prefer a gateway `error` field over a raw JSON body dumped into the UI. */
function apiErrorMessage(status: number, text: string): string {
  const raw = (text || "").trim();
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { error?: unknown; message?: unknown };
      const labeled =
        (typeof parsed.error === "string" && parsed.error.trim()) ||
        (typeof parsed.message === "string" && parsed.message.trim());
      if (labeled) {
        if (labeled === "internal server error") {
          return transportMessage("internal");
        }
        return labeled;
      }
    } catch {
      /* keep the raw body when it is not JSON */
    }
    if (raw.startsWith("{") && raw.includes("internal server error")) {
      return transportMessage("internal");
    }
    return raw;
  }
  return transportMessage("httpStatus", { status });
}
import { reportRequestFailure } from "./notification-bus";
import { isDesktopShell, isInternalDesktopUrl, isNavinShellUrl, tauriInvoke, tauriOpenUrl } from "./desktop";
import { shouldUseWindowOpenFallback } from "./external-url";
import {
  fileNameFromPath,
  saveDownload,
  startHttpAttachmentDownload,
} from "./save-blob";

const API_READ_TIMEOUT_MS = 20_000;
const API_SLOW_TIMEOUT_MS = 180_000;
/** Diarization rewrites a long transcript chunk by chunk, so it runs longer. */
const API_DIARIZATION_TIMEOUT_MS = 900_000;
const SLASH_COMMAND_LIFECYCLES = new Set<SlashCommandLifecycle>([
  "side_channel",
  "finalize_active_turn",
  "stop_active_turn",
  "agent_turn",
  "agent_turn_with_args",
]);

function isSlashCommandLifecycle(
  value: unknown,
): value is SlashCommandLifecycle {
  return (
    typeof value === "string" &&
    SLASH_COMMAND_LIFECYCLES.has(value as SlashCommandLifecycle)
  );
}
const CHANNEL_VALUES_HEADER = "X-Navin-Channel-Values";
const API_SERVICE_VALUES_HEADER = "X-Navin-API-Service-Values";

export { ApiError } from "./api-error";
// Shared with feature-scoped API modules (notes-api.ts) so they reuse the same
// auth, timeout, failure reporting and chunked-header body transport.
export { request as apiRequest, bodyHeaders as apiBodyHeaders };

/**
 * Call a gateway HTTP route.
 *
 * Every route is a GET, including the ones that change state - arguments go in
 * the query string. That is not a style choice: the gateway is a websockets
 * server that answers HTTP from its handshake hook, and the library rejects any
 * method other than GET while parsing the request line, before navin sees the
 * request at all. A POST therefore never reaches routing and surfaces as an
 * opaque 500 with nothing in the response to explain it.
 *
 * So do not pass a method here. Two consequences worth knowing: a payload has to
 * fit the 8KB request line (see chunkModelImportEntries for how bulk operations
 * deal with it), and these GETs are not safe to retry blindly.
 */
type UnauthorizedRefresher = () => Promise<string | null>;

let unauthorizedRefresher: UnauthorizedRefresher | null = null;
let unauthorizedRefreshInFlight: Promise<string | null> | null = null;

/** Called by App when the gateway token is stale after a restart. */
export function setUnauthorizedRefresher(fn: UnauthorizedRefresher | null): void {
  unauthorizedRefresher = fn;
  if (!fn) unauthorizedRefreshInFlight = null;
}

async function refreshStaleApiToken(staleToken: string): Promise<string | null> {
  if (!unauthorizedRefresher) return null;
  if (!unauthorizedRefreshInFlight) {
    unauthorizedRefreshInFlight = unauthorizedRefresher().finally(() => {
      unauthorizedRefreshInFlight = null;
    });
  }
  const next = await unauthorizedRefreshInFlight;
  return next && next !== staleToken ? next : null;
}

/**
 * Extra attempts a pure read gets when the engine stalls, and how long each
 * one may wait. The gateway's first minute after a restart is the usual case:
 * every panel asks at once and the first answers take 30 to 70 seconds. One
 * 20 s attempt then read as "the IDE is broken"; three growing attempts ride
 * it out, and the status bar says the engine is slow meanwhile.
 */
const READ_RETRIES = 2;
const READ_RETRY_PAUSE_MS = [400, 1200];
const READ_TIMEOUT_GROWTH = 1.5;
const READ_TIMEOUT_CAP_MS = 45_000;

function attemptTimeout(timeoutMs: number, attempt: number): number {
  if (timeoutMs <= 0 || attempt === 0) return timeoutMs;
  return Math.min(READ_TIMEOUT_CAP_MS, Math.round(timeoutMs * READ_TIMEOUT_GROWTH ** attempt));
}

function pause(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    }
    signal?.addEventListener("abort", done, { once: true });
  });
}

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
  timeoutMs: number = 0,
): Promise<T> {
  const retries = timeoutMs > 0 && isRetryableRead(url) ? READ_RETRIES : 0;
  let attempt = 0;
  for (;;) {
    try {
      const value = await sendRequest<T>(url, token, init, attemptTimeout(timeoutMs, attempt));
      noteGatewayAnswered();
      return value;
    } catch (error) {
      const transport = isTransportError(error);
      if (transport) noteGatewayStalled(isUnreachableError(error) ? "unreachable" : "timeout");
      if (transport && attempt < retries && !init?.signal?.aborted) {
        attempt += 1;
        await pause(READ_RETRY_PAUSE_MS[attempt - 1] ?? 1200, init?.signal);
        if (!init?.signal?.aborted) continue;
      }
      // Reported centrally so a failure is visible even at the many call sites
      // that only log it, and rethrown so existing inline handling still runs.
      reportRequestFailure(url, error);
      throw error;
    }
  }
}

async function sendRequest<T>(
  url: string,
  token: string,
  init?: RequestInit,
  timeoutMs: number = 0,
  allowRefresh: boolean = true,
): Promise<T> {
  let res: Response;
  try {
    res = await fetchWithTimeout(
      url,
      {
        ...(init ?? {}),
        headers: {
          ...(init?.headers ?? {}),
          Authorization: `Bearer ${token}`,
        },
        credentials: "same-origin",
      },
      timeoutMs,
    );
  } catch (error) {
    // fetch reports a refused connection as a bare TypeError("Failed to
    // fetch"); panels print that verbatim. Name the engine instead.
    if (error instanceof TypeError && !init?.signal?.aborted) {
      throw new GatewayUnreachableError(url, error);
    }
    throw error;
  }
  if (res.status === 401 && allowRefresh) {
    const next = await refreshStaleApiToken(token);
    if (next) {
      return sendRequest<T>(url, next, init, timeoutMs, false);
    }
  }
  if (!res.ok) {
    const text =
      typeof res.text === "function" ? (await res.text()).trim() : "";
    throw new ApiError(res.status, apiErrorMessage(res.status, text));
  }
  const contentType = res.headers?.get?.("content-type") ?? "";
  if (contentType && !contentType.toLowerCase().includes("application/json")) {
    const text = typeof res.text === "function" ? await res.text() : "";
    const isHtml = text.trimStart().toLowerCase().startsWith("<!doctype");
    throw new ApiError(
      res.status,
      isHtml ? transportMessage("htmlInsteadOfJson") : transportMessage("nonJson"),
    );
  }
  return (await res.json()) as T;
}

function mcpValuesHeader(
  values: Record<string, unknown>,
): HeadersInit | undefined {
  const payload: Record<string, unknown> = {};
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) payload[key] = trimmed;
      return;
    }
    payload[key] = value;
  });
  if (!Object.keys(payload).length) return undefined;
  return { "X-Navin-MCP-Values": JSON.stringify(payload) };
}

function automationValuesHeader(values: AutomationUpdatePayload): HeadersInit {
  return {
    "X-Navin-Automation-Values": encodeURIComponent(JSON.stringify(values)),
  };
}

function splitKey(key: string): { channel: string; chatId: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { channel: "", chatId: key };
  return { channel: key.slice(0, idx), chatId: key.slice(idx + 1) };
}

export async function listSessions(
  token: string,
  base: string = "",
): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    title?: string;
    preview?: string;
    run_started_at?: number | null;
    workspace_scope?: WorkspaceScopePayload | null;
  };
  const body = await request<{ sessions: Row[] }>(
    `${base}/api/sessions`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.sessions.map((s) => ({
    key: s.key,
    ...splitKey(s.key),
    createdAt: s.created_at,
    updatedAt: s.updated_at,
    title: s.title ?? "",
    preview: s.preview ?? "",
    runStartedAt: s.run_started_at ?? null,
    workspaceScope: s.workspace_scope ?? null,
  }));
}

/** Disk-backed WebUI display thread snapshot (separate from agent session). */
export interface FetchWebuiThreadOptions {
  limit?: number;
  direction?: "latest";
  before?: string | null;
}

export async function fetchWebuiThread(
  token: string,
  key: string,
  optionsOrBase?: FetchWebuiThreadOptions | string,
  base: string = "",
): Promise<WebuiThreadPersistedPayload | null> {
  const options = typeof optionsOrBase === "string" ? undefined : optionsOrBase;
  const resolvedBase = typeof optionsOrBase === "string" ? optionsOrBase : base;
  const params = new URLSearchParams();
  if (options?.limit !== undefined) params.set("limit", String(options.limit));
  if (options?.direction) params.set("direction", options.direction);
  if (options?.before) params.set("before", options.before);
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  const url = `${resolvedBase}/api/sessions/${encodeURIComponent(key)}/webui-thread${suffix}`;
  const res = await fetchWithTimeout(url, {
    headers: { Authorization: `Bearer ${token}` },
    credentials: "same-origin",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return (await res.json()) as WebuiThreadPersistedPayload;
}

function appendFileRoot(query: URLSearchParams, root?: string | null): void {
  const value = (root ?? "").trim();
  if (value) query.set("root", value);
}

export async function fetchFilePreview(
  token: string,
  key: string,
  path: string,
  base: string = "",
  root?: string | null,
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  appendFileRoot(query, root);
  return request<FilePreviewPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchDevFilePreview(
  token: string,
  key: string,
  path: string,
  base: string = "",
  root?: string | null,
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("any", "1");
  appendFileRoot(query, root);
  return request<FilePreviewPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Absolute URL for workspace file download (token in query for <a>/<video>). */
export function workspaceFileDownloadUrl(
  token: string,
  key: string,
  path: string,
  base: string = "",
  root?: string | null,
): string {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("token", token);
  appendFileRoot(query, root);
  return `${base}/api/sessions/${encodeURIComponent(key)}/file-download?${query}`;
}

export async function downloadWorkspaceFile(
  token: string,
  key: string,
  path: string,
  filename?: string,
  base: string = "",
  root?: string | null,
): Promise<void> {
  const query = new URLSearchParams();
  query.set("path", path);
  // Keep token in the query as well: <a>/<video> cannot send Authorization.
  query.set("token", token);
  appendFileRoot(query, root);
  const url = `${base}/api/sessions/${encodeURIComponent(key)}/file-download?${query}`;
  const res = await fetchWithTimeout(
    url,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
    API_SLOW_TIMEOUT_MS,
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.text()) || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail.trim() || `HTTP ${res.status}`);
  }
  const name = filename || fileNameFromPath(path);
  const blob = await res.blob();
  try {
    await saveDownload(blob, name);
  } catch {
    // Desktop WebViews that cannot write a blob still honour an HTTP
    // attachment once the shell's on_download handler is registered.
    if (isDesktopShell()) {
      startHttpAttachmentDownload(url);
      return;
    }
    throw new ApiError(0, `Could not save ${name}`);
  }
}

/**
 * Fetch a workspace file as a Blob, for rendering rather than saving.
 *
 * The download route answers with `Content-Disposition: attachment`, which an
 * iframe honours by downloading instead of displaying. Reading the bytes here
 * and rendering them from a blob URL keeps that header - and the route's
 * refusal to serve anything inline - exactly as it is.
 */
export async function fetchWorkspaceFileBlob(
  token: string,
  key: string,
  path: string,
  base: string = "",
  root?: string | null,
  route: "file-download" | "file-render" = "file-download",
): Promise<Blob> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("token", token);
  appendFileRoot(query, root);
  const url = `${base}/api/sessions/${encodeURIComponent(key)}/${route}?${query}`;
  const res = await fetchWithTimeout(
    url,
    { headers: { Authorization: `Bearer ${token}` } },
    API_SLOW_TIMEOUT_MS,
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.text()) || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail.trim() || `HTTP ${res.status}`);
  }
  return res.blob();
}

const FILE_BODY_CHUNK_CHARS = 6000;

/** Encode a string into the chunked base64 headers the gateway expects. */
export function bodyHeaders(content: string): Record<string, string> {
  const bytes = new TextEncoder().encode(content);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  const b64 = btoa(binary);
  // An empty body still travels as one empty chunk: the gateway reads
  // "no chunk at all" as "missing file content", which made a new rule
  // (created blank) and an emptied file impossible to save.
  const headers: Record<string, string> = { "X-Navin-File-Body-0": "" };
  let index = 0;
  for (let i = 0; i < b64.length; i += FILE_BODY_CHUNK_CHARS) {
    headers[`X-Navin-File-Body-${index}`] = b64.slice(
      i,
      i + FILE_BODY_CHUNK_CHARS,
    );
    index += 1;
  }
  return headers;
}

/** Mirrors MAX_FILE_SAVE_BYTES in navin/webui/file_preview.py. */
export const MAX_FILE_SAVE_BYTES = 2 * 1024 * 1024;

export async function saveWorkspaceFile(
  token: string,
  key: string,
  path: string,
  content: string,
  base: string = "",
): Promise<FileSavePayload> {
  // Fail here with a clear message rather than letting the transport answer
  // an opaque 431: the content travels as chunked request headers, so both
  // the gateway and the dev proxy enforce hard header-size ceilings.
  if (new TextEncoder().encode(content).length > MAX_FILE_SAVE_BYTES) {
    throw new ApiError(
      413,
      i18n.t("errors.fileSaveTooLarge", {
        max: Math.floor(MAX_FILE_SAVE_BYTES / (1024 * 1024)),
        defaultValue: "File is too large to save from the editor (max {{max}} MB)",
      }),
    );
  }
  const query = new URLSearchParams();
  query.set("path", path);
  return request<FileSavePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-save?${query}`,
    token,
    // The gateway's HTTP layer (websockets process_request) only accepts GET;
    // content travels in chunked base64 headers like the skills API.
    { headers: bodyHeaders(content) },
    API_READ_TIMEOUT_MS,
  );
}

export type FileFormatPayload = {
  path: string;
  tool: string;
  formatted: string;
  changed: boolean;
};

/**
 * Format Document: run the project's formatter (Prettier, ruff, gofmt,
 * rustfmt) over the current buffer. The file on disk is not touched; the
 * caller applies `formatted` to the editor draft.
 */
export async function formatWorkspaceFile(
  token: string,
  key: string,
  path: string,
  content: string,
  base: string = "",
): Promise<FileFormatPayload> {
  if (new TextEncoder().encode(content).length > MAX_FILE_SAVE_BYTES) {
    throw new ApiError(
      413,
      i18n.t("errors.fileSaveTooLarge", {
        max: Math.floor(MAX_FILE_SAVE_BYTES / (1024 * 1024)),
        defaultValue: "File is too large to save from the editor (max {{max}} MB)",
      }),
    );
  }
  const query = new URLSearchParams();
  query.set("path", path);
  return request<FileFormatPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-format?${query}`,
    token,
    { headers: bodyHeaders(content) },
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Create an empty file or a folder in the project.
 *
 * `path` may be nested (`src/components/Button.tsx`); the gateway creates the
 * missing parents rather than failing on the first one.
 */
export async function createWorkspaceEntry(
  token: string,
  key: string,
  path: string,
  kind: "file" | "directory",
  base: string = "",
): Promise<FileCreatePayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("kind", kind);
  return request<FileCreatePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-create?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Delete a file, or a folder and everything under it. There is no undo. */
export async function deleteWorkspaceEntry(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<FileDeletePayload> {
  const query = new URLSearchParams({ path });
  return request<FileDeletePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-delete?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Copy `path` into `parent`, or move it there when `move` is set. A name
 * already taken in the destination grows a "copy" suffix rather than being
 * overwritten, so pasting beside the original is how you duplicate.
 */
export async function pasteWorkspaceEntry(
  token: string,
  key: string,
  path: string,
  parent: string | null,
  move: boolean,
  base: string = "",
): Promise<FilePastePayload> {
  const query = new URLSearchParams({ path });
  if (parent) query.set("parent", parent);
  if (move) query.set("move", "1");
  return request<FilePastePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-paste?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Rename in place, or move when `name` contains a separator. */
export async function renameWorkspaceEntry(
  token: string,
  key: string,
  path: string,
  name: string,
  base: string = "",
): Promise<FileRenamePayload> {
  const query = new URLSearchParams({ path, name });
  return request<FileRenamePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-rename?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Ghost-text continuation for the caret between `prefix` and `suffix`. */
export async function fetchInlineCompletion(
  token: string,
  key: string,
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
  signal?: AbortSignal,
  base: string = "",
): Promise<InlineCompletionPayload> {
  const query = new URLSearchParams({ mode: "complete", path: args.path });
  if (args.language) query.set("language", args.language);
  return request<InlineCompletionPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/assist?${query}`,
    token,
    {
      headers: bodyHeaders(
        JSON.stringify({
          prefix: args.prefix,
          suffix: args.suffix,
          ...(args.recentEdits?.length
            ? { recent_edits: args.recentEdits }
            : {}),
        }),
      ),
      signal,
    },
    API_READ_TIMEOUT_MS,
  );
}

/** Rewrite a selection according to a natural-language instruction (Cmd+K). */
export async function fetchInlineEdit(
  token: string,
  key: string,
  args: {
    path: string;
    selection: string;
    instruction: string;
    prefix?: string;
    suffix?: string;
    language?: string;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<InlineEditPayload> {
  const query = new URLSearchParams({ mode: "edit", path: args.path });
  if (args.language) query.set("language", args.language);
  return request<InlineEditPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/assist?${query}`,
    token,
    {
      headers: bodyHeaders(
        JSON.stringify({
          selection: args.selection,
          instruction: args.instruction,
          prefix: args.prefix ?? "",
          suffix: args.suffix ?? "",
        }),
      ),
      signal,
    },
    API_SLOW_TIMEOUT_MS,
  );
}

export type MeetingSynthesisPayload = {
  markdown: string;
  model?: string;
  route?: string;
  latency_ms?: number;
};

/** Meeting synthesis lives on the desk; a chat session is optional. */
function meetingApiPath(key: string | null | undefined, mode: string): string {
  const suffix = `/meeting?mode=${mode}`;
  if (key) return `/api/sessions/${encodeURIComponent(key)}${suffix}`;
  return `/api${suffix}`;
}

/** Template-driven minutes for one meeting, computed without the agent loop. */
export async function fetchMeetingReport(
  token: string,
  key: string | null | undefined,
  args: {
    title: string;
    templateName: string;
    templateInstructions: string;
    transcript: string;
    notes?: string;
    speakers?: string[];
    language?: string;
    /** Real meeting date/time (ISO) so the minutes carry the true date. */
    meetingDate?: string;
    /** Measured audio duration in minutes, when the desk knows it. */
    durationMin?: number;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<MeetingSynthesisPayload> {
  return request<MeetingSynthesisPayload>(
    `${base}${meetingApiPath(key, "report")}`,
    token,
    {
      headers: bodyHeaders(
        JSON.stringify({
          title: args.title,
          template_name: args.templateName,
          template_instructions: args.templateInstructions,
          transcript: args.transcript,
          notes: args.notes ?? "",
          speakers: args.speakers ?? [],
          language: args.language ?? "",
          meeting_date: args.meetingDate ?? "",
          duration_min: args.durationMin ?? null,
        }),
      ),
      signal,
    },
    API_SLOW_TIMEOUT_MS,
  );
}

/** Speaker-labelled transcript plus the roster the model could confirm. */
export async function fetchMeetingSpeakers(
  token: string,
  key: string | null | undefined,
  args: { transcript: string; speakers?: string[]; language?: string },
  signal?: AbortSignal,
  base: string = "",
): Promise<
  MeetingSynthesisPayload & { speakers?: string[]; truncated?: boolean }
> {
  return request<
    MeetingSynthesisPayload & { speakers?: string[]; truncated?: boolean }
  >(
    `${base}${meetingApiPath(key, "speakers")}`,
    token,
    {
      headers: bodyHeaders(
        JSON.stringify({
          transcript: args.transcript,
          speakers: args.speakers ?? [],
          language: args.language ?? "",
        }),
      ),
      signal,
    },
    API_DIARIZATION_TIMEOUT_MS,
  );
}

/** Grounded answer about one meeting, computed without the agent loop. */
export async function fetchMeetingAnswer(
  token: string,
  key: string | null | undefined,
  args: {
    question: string;
    title: string;
    transcript: string;
    notes?: string;
    summary?: string;
    speakers?: string[];
    language?: string;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<MeetingSynthesisPayload> {
  return request<MeetingSynthesisPayload>(
    `${base}${meetingApiPath(key, "ask")}`,
    token,
    {
      headers: bodyHeaders(
        JSON.stringify({
          question: args.question,
          title: args.title,
          transcript: args.transcript,
          notes: args.notes ?? "",
          summary: args.summary ?? "",
          speakers: args.speakers ?? [],
          language: args.language ?? "",
        }),
      ),
      signal,
    },
    API_SLOW_TIMEOUT_MS,
  );
}

/** One transcribed audio slice captured by the autonomous meeting bot. */
export type MeetingBotSegment = {
  offset_ms: number;
  duration_ms: number;
  text: string;
};

export type MeetingBotStatusPayload = {
  bot_id: string;
  platform: string;
  state:
    | "starting"
    | "joining"
    | "waiting"
    | "live"
    | "ended"
    | "interrupted"
    | "error";
  error: string;
  sources: number;
  elapsed_s: number;
  /** Absolute segment count; pass it back as `cursor` to get only new ones. */
  cursor: number;
  segments: MeetingBotSegment[];
};

/** Start / poll / stop the headless-browser bot that joins the conference. */
export async function fetchMeetingBot(
  token: string,
  key: string | null | undefined,
  action: "start" | "status" | "stop",
  args: {
    botId: string;
    url?: string;
    name?: string;
    language?: string;
    cursor?: number;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<MeetingBotStatusPayload> {
  return request<MeetingBotStatusPayload>(
    `${base}${meetingApiPath(key, `bot_${action}`)}`,
    token,
    {
      headers: bodyHeaders(
        JSON.stringify({
          bot_id: args.botId,
          url: args.url ?? "",
          name: args.name ?? "",
          language: args.language ?? "",
          cursor: args.cursor ?? 0,
        }),
      ),
      signal,
    },
    API_SLOW_TIMEOUT_MS,
  );
}

export type MeetingDiskMeta = {
  id: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  date?: string;
  status?: string;
  [key: string]: unknown;
};

export type MeetingDiskRecord = {
  id: string;
  meta: MeetingDiskMeta;
  transcript: string;
  notes: string;
  summary: string;
  chat: unknown[];
};

export type MeetingDiskIndex = Pick<
  MeetingDiskMeta,
  "id" | "title" | "created_at" | "updated_at" | "date" | "status"
> & { score?: number };

export type MeetingAudioSegment = {
  id: string;
  meeting_id: string;
  file: string;
  bytes: number;
  created_at: string;
  mime?: string;
  offset_ms?: number;
  duration_ms?: number;
};

export type MeetingBinaryPayload = {
  filename: string;
  content_type: string;
  data_base64: string;
  bytes: number;
};

async function fetchMeetingAction<T>(
  token: string,
  key: string | null | undefined,
  mode: string,
  body: Record<string, unknown> = {},
  signal?: AbortSignal,
  base: string = "",
): Promise<T> {
  return request<T>(
    `${base}${meetingApiPath(key, mode)}`,
    token,
    { headers: bodyHeaders(JSON.stringify(body)), signal },
    API_SLOW_TIMEOUT_MS,
  );
}

export type MeetingListPage = {
  meetings: MeetingDiskIndex[];
  /** Full records for the page, present when `full` was requested. */
  records?: MeetingDiskRecord[];
  offset?: number;
  limit?: number;
  total?: number;
};

export const meetingStoreApi = {
  list: (
    token: string,
    key?: string | null,
    signal?: AbortSignal,
    base?: string,
    options: { full?: boolean; limit?: number; offset?: number } = {},
  ) =>
    fetchMeetingAction<MeetingListPage>(
      token,
      key,
      "store_list",
      {
        ...(options.full ? { full: true } : {}),
        ...(options.limit ? { limit: options.limit } : {}),
        ...(options.offset ? { offset: options.offset } : {}),
      },
      signal,
      base,
    ),
  /** Every meeting on disk, page by page, as full records. */
  listAll: async (
    token: string,
    key: string | null | undefined,
    signal?: AbortSignal,
    base?: string,
  ): Promise<MeetingDiskRecord[]> => {
    const pageSize = 100;
    const out: MeetingDiskRecord[] = [];
    for (let offset = 0; ; offset += pageSize) {
      const page = await meetingStoreApi.list(token, key, signal, base, {
        full: true,
        limit: pageSize,
        offset,
      });
      const records = page.records ?? [];
      out.push(...records);
      const total = typeof page.total === "number" ? page.total : null;
      if (records.length < pageSize || (total !== null && out.length >= total)) break;
    }
    return out;
  },
  get: (
    token: string,
    key: string | null | undefined,
    id: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingDiskRecord>(
    token, key, "store_get", { id }, signal, base,
  ),
  create: (
    token: string,
    key: string | null | undefined,
    record: Record<string, unknown>,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingDiskRecord>(
    token, key, "store_create", { record }, signal, base,
  ),
  update: (
    token: string,
    key: string | null | undefined,
    id: string,
    changes: Record<string, unknown>,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingDiskRecord>(
    token, key, "store_update", { id, changes }, signal, base,
  ),
  delete: (
    token: string,
    key: string | null | undefined,
    id: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<{ ok: true; id: string }>(
    token, key, "store_delete", { id }, signal, base,
  ),
  search: (
    token: string,
    key: string | null | undefined,
    query: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<{ meetings: MeetingDiskIndex[] }>(
    token, key, "search", { query }, signal, base,
  ),
  migrateV2: (
    token: string,
    key: string | null | undefined,
    meetings: unknown[],
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<{
    imported: string[];
    skipped: string[];
    already_applied: boolean;
  }>(
    token,
    key,
    "migrate",
    {
      payload: { meetings },
      migration_id: "browser-v2",
      version: 1,
    },
    signal,
    base,
  ),
  saveAudio: (
    token: string,
    key: string | null | undefined,
    meetingId: string,
    dataUrl: string,
    metadata: Record<string, unknown>,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingAudioSegment>(
    token,
    key,
    "audio",
    { meeting_id: meetingId, action: "save", data_url: dataUrl, metadata },
    signal,
    base,
  ),
  listAudio: (
    token: string,
    key: string | null | undefined,
    meetingId: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<{ segments: MeetingAudioSegment[] }>(
    token,
    key,
    "audio",
    { meeting_id: meetingId, action: "list" },
    signal,
    base,
  ),
  getAudio: (
    token: string,
    key: string | null | undefined,
    meetingId: string,
    segmentId: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingBinaryPayload>(
    token,
    key,
    "audio",
    { meeting_id: meetingId, segment_id: segmentId, action: "get" },
    signal,
    base,
  ),
  emergencyExport: (
    token: string,
    key?: string | null,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingBinaryPayload>(
    token, key, "emergency_export", {}, signal, base,
  ),
  translate: (
    token: string,
    key: string | null | undefined,
    kind: "transcript" | "report",
    text: string,
    targetLanguage: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingSynthesisPayload & { text: string }>(
    token,
    key,
    `translate_${kind}`,
    { text, target_language: targetLanguage },
    signal,
    base,
  ),
  cleanup: (
    token: string,
    key: string | null | undefined,
    transcript: string,
    language: string,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<{
    text: string;
    model?: string;
    accuracy: string;
    acoustic_diarization: boolean;
  }>(
    token, key, "cleanup", { transcript, language }, signal, base,
  ),
  docx: (
    token: string,
    key: string | null | undefined,
    body: { title: string; report: string; transcript: string; notes: string },
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingBinaryPayload>(
    token, key, "docx", body, signal, base,
  ),
  calendar: (
    token: string,
    key?: string | null,
    calendar?: Record<string, unknown>,
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<Record<string, unknown>>(
    token, key, "calendar", calendar ? { calendar } : {}, signal, base,
  ),
  calendarSync: (
    token: string,
    key: string | null | undefined,
    provider: string,
    events: unknown[],
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<{
    provider: string;
    configured: boolean;
    synced: boolean;
    fallback: string;
    ics: string;
  }>(
    token, key, "calendar_sync", { provider, events }, signal, base,
  ),
  /** File the meeting into the Notes module (creates or refreshes the linked note). */
  toNote: (
    token: string,
    key: string | null | undefined,
    id: string,
    options: { includeTranscript?: boolean } = {},
    signal?: AbortSignal,
    base?: string,
  ) => fetchMeetingAction<MeetingNoteLink>(
    token,
    key,
    "to_note",
    { id, include_transcript: Boolean(options.includeTranscript) },
    signal,
    base,
  ),
};

export type MeetingNoteLink = {
  note_id: string;
  title: string;
  path: string;
  folder: string;
  created: boolean;
  action_items: number;
  meeting_id: string;
};

export async function fetchFileTree(
  token: string,
  key: string,
  path?: string,
  base: string = "",
): Promise<FileTreePayload> {
  const query = new URLSearchParams();
  if (path) query.set("path", path);
  const suffix = query.toString() ? `?${query}` : "";
  return request<FileTreePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-tree${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type CollectedTest = {
  id: string;
  run_target: string;
  file: string;
  suite: string;
  name: string;
};

export type TestSuiteInfo = {
  runner: string;
  language: string;
  available: boolean;
  workdir: string;
  reason: string;
  collect_supported: boolean;
  tests: CollectedTest[];
  error: string;
  duration_ms: number;
};

export type TestCollectionPayload = { root: string; suites: TestSuiteInfo[] };

export type TestRunFailure = {
  name: string;
  classname: string;
  file: string;
  line: number;
  kind: string;
  message: string;
};

export type TestRunOutcome = {
  runner: string;
  ran: boolean;
  ok: boolean;
  passed: number;
  failed: number;
  skipped: number;
  total: number;
  duration_ms: number;
  exit_code: number | null;
  skipped_reason: string;
  failures: TestRunFailure[];
};

export type TestRunPayload = {
  root: string;
  runner: string;
  target: string | null;
  outcomes: TestRunOutcome[];
};

/** Collection imports test modules; minutes on a monorepo, so give it the
 * same generous budget as a run instead of the default read timeout. */
const TEST_EXPLORER_TIMEOUT_MS = 900_000;

export async function fetchTestCollection(
  token: string,
  key: string,
  base: string = "",
): Promise<TestCollectionPayload> {
  return request<TestCollectionPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/tests?mode=collect`,
    token,
    undefined,
    TEST_EXPLORER_TIMEOUT_MS,
  );
}

export async function runExplorerTests(
  token: string,
  key: string,
  runner: string,
  target?: string,
  base: string = "",
): Promise<TestRunPayload> {
  const query = new URLSearchParams({ mode: "run", runner });
  if (target) query.set("target", target);
  return request<TestRunPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/tests?${query}`,
    token,
    undefined,
    TEST_EXPLORER_TIMEOUT_MS,
  );
}

export async function fetchFilePreviewAvailability(
  token: string,
  key: string,
  path: string,
  base: string = "",
  root?: string | null,
): Promise<boolean> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("probe", "1");
  appendFileRoot(query, root);
  const payload = await request<{ available?: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return payload.available !== false;
}

export async function fetchSessionAutomations(
  token: string,
  key: string,
  base: string = "",
): Promise<SessionAutomationsPayload> {
  return request<SessionAutomationsPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchAutomations(
  token: string,
  base: string = "",
): Promise<AutomationsPayload> {
  return request<AutomationsPayload>(
    `${base}/api/webui/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runAutomationAction(
  token: string,
  action: "enable" | "disable" | "delete" | "run",
  id: string,
  base: string = "",
): Promise<AutomationsPayload> {
  const query = new URLSearchParams();
  query.set("id", id);
  return request<AutomationsPayload>(
    `${base}/api/webui/automations/${action}?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateAutomation(
  token: string,
  id: string,
  values: AutomationUpdatePayload,
  base: string = "",
): Promise<AutomationsPayload> {
  const query = new URLSearchParams();
  query.set("id", id);
  return request<AutomationsPayload>(
    `${base}/api/webui/automations/update?${query}`,
    token,
    {
      headers: automationValuesHeader(values),
    },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSkills(
  token: string,
  base: string = "",
): Promise<SkillsPayload> {
  return request<SkillsPayload>(
    `${base}/api/webui/skills`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchAppTemplates(
  token: string,
  base: string = "",
): Promise<AppTemplatesPayload> {
  return request<AppTemplatesPayload>(
    `${base}/api/webui/app-templates`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchAppTemplateDetail(
  token: string,
  slug: string,
  base: string = "",
): Promise<AppTemplateSummary> {
  return request<AppTemplateSummary>(
    `${base}/api/webui/app-templates/${encodeURIComponent(slug)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function installAppTemplate(
  token: string,
  slug: string,
  base: string = "",
): Promise<AppTemplateActionResult> {
  const query = new URLSearchParams();
  query.set("slug", slug);
  return request<AppTemplateActionResult>(
    `${base}/api/webui/app-templates/install?${query}`,
    token,
    undefined,
    180_000,
  );
}

export async function createAppTemplate(
  token: string,
  slug: string,
  dest: string,
  base: string = "",
): Promise<AppTemplateActionResult> {
  const query = new URLSearchParams();
  query.set("slug", slug);
  query.set("dest", dest);
  try {
    return await request<AppTemplateActionResult>(
      `${base}/api/webui/app-templates/create?${query}`,
      token,
      undefined,
      180_000,
    );
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) {
      throw error;
    }
    return request<AppTemplateActionResult>(
      `${base}/api/webui/app-templates/install?${query}`,
      token,
      undefined,
      180_000,
    );
  }
}

export async function fetchSkillDetail(
  token: string,
  name: string,
  base: string = "",
): Promise<SkillDetail> {
  return request<SkillDetail>(
    `${base}/api/webui/skills/${encodeURIComponent(name)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

function skillBodyHeader(
  markdown: string | undefined,
): HeadersInit | undefined {
  if (!markdown?.trim()) return undefined;
  // HTTP headers only allow ISO-8859-1; base64-encode UTF-8 so emoji and
  // typographic characters survive the transport.
  const bytes = new TextEncoder().encode(markdown);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return { "X-Navin-Skill-Body": `b64:${btoa(binary)}` };
}

export async function createSkill(
  token: string,
  input: { name: string; description: string; markdown?: string },
  base: string = "",
): Promise<SkillDetail> {
  const query = new URLSearchParams();
  query.set("name", input.name);
  query.set("description", input.description);
  return request<SkillDetail>(
    `${base}/api/webui/skills/create?${query}`,
    token,
    { headers: skillBodyHeader(input.markdown) },
  );
}

export async function updateSkill(
  token: string,
  name: string,
  markdown: string,
  base: string = "",
): Promise<SkillDetail> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<SkillDetail>(
    `${base}/api/webui/skills/update?${query}`,
    token,
    { headers: skillBodyHeader(markdown) },
  );
}

export async function deleteSkill(
  token: string,
  name: string,
  base: string = "",
): Promise<SkillsPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<SkillsPayload>(
    `${base}/api/webui/skills/delete?${query}`,
    token,
  );
}

export async function discoverWorkspaceSkills(
  token: string,
  projectPath?: string | null,
  base: string = "",
  scope?: SkillApplyScope,
): Promise<WorkspaceSkillsDiscoverPayload> {
  const query = new URLSearchParams();
  if (projectPath?.trim()) query.set("path", projectPath.trim());
  if (scope) query.set("scope", scope);
  const suffix = query.size ? `?${query}` : "";
  return request<WorkspaceSkillsDiscoverPayload>(
    `${base}/api/webui/skills/discover${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function importWorkspaceSkills(
  token: string,
  input: { projectPath?: string | null; names?: string[]; scope?: SkillApplyScope },
  base: string = "",
): Promise<WorkspaceSkillsImportPayload> {
  const query = new URLSearchParams();
  if (input.projectPath?.trim()) query.set("path", input.projectPath.trim());
  if (input.names?.length) query.set("names", input.names.join(","));
  if (input.scope) query.set("scope", input.scope);
  const suffix = query.size ? `?${query}` : "";
  return request<WorkspaceSkillsImportPayload>(
    `${base}/api/webui/skills/import-workspace${suffix}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function setupSkill(
  token: string,
  name: string,
  option?: string,
  base: string = "",
): Promise<SkillSetupResult> {
  const query = new URLSearchParams();
  query.set("name", name);
  if (option) query.set("option", option);
  // Package installs can take minutes; allow up to 10 minutes.
  return request<SkillSetupResult>(
    `${base}/api/webui/skills/setup?${query}`,
    token,
    undefined,
    620_000,
  );
}

/** Server-side local app discovery for Preview (avoids browser CORS). */
export async function fetchDiscoverPreviewUrl(
  token: string,
  opts?: { url?: string; preferredPort?: number },
  base: string = "",
): Promise<{ url: string | null; source: string | null }> {
  const query = new URLSearchParams();
  if (opts?.url) query.set("url", opts.url);
  if (opts?.preferredPort != null) {
    query.set("preferred_port", String(opts.preferredPort));
  }
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ url: string | null; source: string | null }>(
    `${base}/api/webui/preview-discover${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type PreviewProxyPayload = {
  targetPort: number;
  proxyPort: number;
  url: string;
};

export type PreviewLogEntry = {
  id: number;
  level: string;
  text: string;
  ts?: number | null;
  url?: string;
  method?: string;
  status?: number;
  durationMs?: number;
};

export type PreviewLogsPayload = {
  port: number;
  entries: PreviewLogEntry[];
  counts: Record<string, number>;
  digest: string;
};

/** Starts (or reuses) the local telemetry injection proxy for a preview port. */
export async function startPreviewProxy(
  token: string,
  port: number,
  base: string = "",
): Promise<PreviewProxyPayload> {
  const query = new URLSearchParams();
  query.set("port", String(port));
  return request<PreviewProxyPayload>(
    `${base}/api/webui/preview-proxy/start?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Server-side screenshot of a loopback preview URL via headless Chromium.
 * Reliable for any page (unlike the in-iframe rasterizer, which taints on
 * cross-origin assets).
 */
export async function capturePreviewScreenshot(
  token: string,
  url: string,
  options: { width?: number; height?: number; fullPage?: boolean } = {},
  base: string = "",
): Promise<{ dataUrl: string; bytes: number }> {
  const query = new URLSearchParams();
  query.set("url", url);
  if (options.width) query.set("width", String(options.width));
  if (options.height) query.set("height", String(options.height));
  if (options.fullPage) query.set("full", "1");
  return request<{ dataUrl: string; bytes: number }>(
    `${base}/api/webui/preview-screenshot?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchPreviewLogs(
  token: string,
  port: number,
  after: number = 0,
  base: string = "",
): Promise<PreviewLogsPayload> {
  const query = new URLSearchParams();
  query.set("port", String(port));
  if (after > 0) query.set("after", String(after));
  return request<PreviewLogsPayload>(
    `${base}/api/webui/preview-logs?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function clearPreviewLogs(
  token: string,
  port: number,
  base: string = "",
): Promise<{ status: string; port: number }> {
  const query = new URLSearchParams();
  query.set("port", String(port));
  return request<{ status: string; port: number }>(
    `${base}/api/webui/preview-logs/clear?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type BackgroundProcess = {
  id: string;
  command: string;
  cwd: string;
  elapsed_s: number;
  idle_s: number;
  returncode: number | null;
  owner: string | null;
  tail: string;
};

export async function fetchProcesses(
  token: string,
  base: string = "",
): Promise<{ processes: BackgroundProcess[] }> {
  return request<{ processes: BackgroundProcess[] }>(
    `${base}/api/webui/processes`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function killProcess(
  token: string,
  id: string,
  base: string = "",
): Promise<{ status: string; id: string }> {
  const query = new URLSearchParams();
  query.set("id", id);
  return request<{ status: string; id: string }>(
    `${base}/api/webui/processes/kill?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type MigrationScanPayload = {
  mcp: { origin: string; path: string; servers: string[] }[];
  nativeRules: string[];
  copilotInstructions: string | null;
  vscodeSettings: string | null;
};

export type MigrationImportPayload = {
  mcp: { imported: string[]; skipped: string[]; invalid: string[] };
  copilot: { imported: boolean; reason?: string; rule?: string };
};

export async function scanMigrationSources(
  token: string,
  base: string = "",
): Promise<MigrationScanPayload> {
  return request<MigrationScanPayload>(
    `${base}/api/webui/migration/scan`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runMigrationImport(
  token: string,
  base: string = "",
): Promise<MigrationImportPayload> {
  return request<MigrationImportPayload>(
    `${base}/api/webui/migration/import`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFsRoots(
  token: string,
  base: string = "",
): Promise<FsRootsPayload> {
  return request<FsRootsPayload>(
    `${base}/api/webui/fs/roots`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFsList(
  token: string,
  path: string,
  options?: { hidden?: boolean },
  base: string = "",
): Promise<FsListPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  if (options?.hidden) query.set("hidden", "1");
  return request<FsListPayload>(
    `${base}/api/webui/fs/list?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Create a folder under the Projects root (or an explicit parent).
 *
 * Idempotent: an existing folder comes back with `created: false` so the
 * caller can simply open it. The gateway validates the name for every host
 * (Linux, WSL, Windows, macOS).
 */
export async function createProjectFolder(
  token: string,
  name: string,
  parent?: string,
  base: string = "",
  options?: { sessionKey?: string },
): Promise<{ path: string; name: string; created: boolean }> {
  const cleaned = name.trim();
  const query = new URLSearchParams();
  query.set("name", cleaned);
  if (parent?.trim()) query.set("parent", parent.trim());
  try {
    return await request<{ path: string; name: string; created: boolean }>(
      `${base}/api/webui/fs/mkdir?${query}`,
      token,
      undefined,
      API_READ_TIMEOUT_MS,
    );
  } catch (error) {
    // Packaged desktops shipped the +folder button before the dedicated
    // mkdir route. The session file-create endpoint has been there longer
    // and can create a sibling under NavinProjects when the session is not
    // locked to a single project.
    if (
      !(error instanceof ApiError) ||
      error.status !== 404 ||
      !/API route not found/i.test(error.message)
    ) {
      throw error;
    }
    return createProjectFolderViaSession(
      token,
      cleaned,
      parent,
      base,
      options?.sessionKey,
    );
  }
}

async function createProjectFolderViaSession(
  token: string,
  name: string,
  parent: string | undefined,
  base: string,
  sessionKey?: string,
): Promise<{ path: string; name: string; created: boolean }> {
  const workspaces = await fetchWorkspaces(token, base);
  const projectsRoot = workspaces.default_scope.project_path.trim();
  if (!projectsRoot) {
    throw new ApiError(400, "Projects folder is not configured");
  }
  const targetParent = (parent?.trim() || projectsRoot).replace(/[\\/]+$/, "");
  const created = await createWorkspaceEntry(
    token,
    await resolveFolderCreateSessionKey(token, sessionKey, base),
    `${targetParent}/${name}`,
    "directory",
    base,
  );
  return { path: created.path, name, created: created.created };
}

async function resolveFolderCreateSessionKey(
  token: string,
  sessionKey: string | undefined,
  base: string,
): Promise<string> {
  if (sessionKey?.trim()) return sessionKey.trim();
  const sessions = await listSessions(token, base);
  const key = sessions[0]?.key?.trim();
  if (!key) {
    throw new ApiError(
      409,
      "Open a chat first, then create the folder again.",
    );
  }
  return key;
}

export async function fetchGitStatus(
  token: string,
  path: string,
  base: string = "",
): Promise<GitStatusPayload> {
  const query = new URLSearchParams({ path });
  return request<GitStatusPayload>(
    `${base}/api/webui/git/status?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Clone a remote Git repo into Projects (or an explicit parent folder). */
export async function cloneGitProject(
  token: string,
  args: { url: string; parent?: string; name?: string },
  base: string = "",
): Promise<GitClonePayload> {
  const query = new URLSearchParams();
  query.set("url", args.url.trim());
  if (args.parent?.trim()) query.set("parent", args.parent.trim());
  if (args.name?.trim()) query.set("name", args.name.trim());
  return request<GitClonePayload>(
    `${base}/api/webui/git/clone?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchProjectAudit(
  token: string,
  path: string,
  base: string = "",
): Promise<ProjectAuditPayload> {
  const query = new URLSearchParams({ path });
  return request<ProjectAuditPayload>(
    `${base}/api/webui/project-audit?${query}`,
    token,
    undefined,
    // Full-tree static analysis: large projects legitimately take a while.
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchFileDiagnostics(
  token: string,
  path: string,
  root: string = "",
  base: string = "",
): Promise<FileDiagnosticsPayload> {
  const query = new URLSearchParams({ path });
  // Tree paths are project-relative; the gateway resolves them against root.
  if (root) query.set("root", root);
  return request<FileDiagnosticsPayload>(
    `${base}/api/webui/diagnostics?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchWorkspaceDiagnostics(
  token: string,
  rootPath: string,
  options?: { seeds?: string[] },
  base: string = "",
): Promise<WorkspaceDiagnosticsPayload> {
  const query = new URLSearchParams({ path: rootPath });
  if (options?.seeds?.length) {
    query.set("seeds", options.seeds.join(","));
  }
  return request<WorkspaceDiagnosticsPayload>(
    `${base}/api/webui/diagnostics/workspace?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchPluginPacks(
  token: string,
  base: string = "",
): Promise<PluginPacksPayload> {
  return request<PluginPacksPayload>(
    `${base}/api/webui/plugins`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function installPluginPack(
  token: string,
  input: {
    source: "git" | "path" | "npx" | "upload";
    location?: string;
    name?: string;
    filename?: string;
    archiveBase64?: string;
    files?: Array<{ path: string; contentBase64: string }>;
    scope?: SkillApplyScope;
    projectPath?: string | null;
  },
  base: string = "",
): Promise<PluginPacksPayload> {
  const query = new URLSearchParams();
  query.set("source", input.source);
  if (input.location) query.set("location", input.location);
  if (input.name) query.set("name", input.name);
  if (input.scope) query.set("scope", input.scope);
  if (input.projectPath?.trim()) query.set("path", input.projectPath.trim());
  const body =
    input.source === "upload"
      ? bodyHeaders(
          JSON.stringify({
            name: input.name,
            filename: input.filename,
            archiveBase64: input.archiveBase64,
            files: input.files,
          }),
        )
      : undefined;
  return request<PluginPacksPayload>(
    `${base}/api/webui/plugins/install?${query}`,
    token,
    body ? { headers: body } : undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function removePluginPack(
  token: string,
  name: string,
  base: string = "",
): Promise<PluginPacksPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<PluginPacksPayload>(
    `${base}/api/webui/plugins/remove?${query}`,
    token,
  );
}

export async function setPluginPackEnabled(
  token: string,
  name: string,
  enabled: boolean,
  base: string = "",
): Promise<PluginPacksPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  const action = enabled ? "enable" : "disable";
  return request<PluginPacksPayload>(
    `${base}/api/webui/plugins/${action}?${query}`,
    token,
  );
}

export async function deleteSession(
  token: string,
  key: string,
  optionsOrBase?: { deleteAutomations?: boolean } | string,
  base: string = "",
): Promise<SessionDeleteResult> {
  const options = typeof optionsOrBase === "string" ? undefined : optionsOrBase;
  const resolvedBase = typeof optionsOrBase === "string" ? optionsOrBase : base;
  const query = new URLSearchParams();
  if (options?.deleteAutomations) query.set("delete_automations", "true");
  const suffix = query.toString() ? `?${query}` : "";
  return request<SessionDeleteResult>(
    `${resolvedBase}/api/sessions/${encodeURIComponent(key)}/delete${suffix}`,
    token,
  );
}

export async function fetchSettings(
  token: string,
  base: string = "",
  options?: { syncCatalog?: boolean },
): Promise<SettingsPayload> {
  const sync = options?.syncCatalog ? "?sync_catalog=1" : "";
  return request<SettingsPayload>(
    `${base}/api/settings${sync}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface ReasoningEffortValuesPayload {
  provider: string;
  model: string;
  /** "" is Auto; the rest are the levels this provider+model accepts. */
  values: string[];
}

/**
 * Thinking ladder for the provider+model pair the Models form is editing.
 *
 * The preset rows only carry the ladder of their saved pair; once the user
 * picks another model the levels shown must follow, or a level the new model
 * rejects gets submitted and refused on save.
 */
export async function fetchReasoningEffortValues(
  token: string,
  provider: string,
  model: string,
  base: string = "",
): Promise<ReasoningEffortValuesPayload> {
  const query = new URLSearchParams({ provider, model });
  return request<ReasoningEffortValuesPayload>(
    `${base}/api/settings/reasoning-effort-values?${query.toString()}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Whether a settings save was refused because of the Thinking level. */
export function isReasoningEffortError(message: string): boolean {
  return /\breasoning_effort\b/i.test(message);
}

export async function fetchSettingsUsage(
  token: string,
  base: string = "",
): Promise<NonNullable<SettingsPayload["usage"]>> {
  return request<NonNullable<SettingsPayload["usage"]>>(
    `${base}/api/settings/usage`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface UpdateInfo {
  currentVersion: string;
  latestVersion?: string;
  available: boolean;
  configured: boolean;
  supported?: boolean;
  installKind: string;
  channel: "stable" | "beta";
  notes?: string;
  mandatory?: boolean;
  /** Why this install cannot replace itself, when `supported` is false. */
  reason?: string;
}

export interface UpdateStatus extends Partial<UpdateInfo> {
  state:
    | "idle"
    | "available"
    | "downloading"
    | "ready"
    | "installing"
    | "restarting"
    | "error";
  progress: number;
  downloadedBytes: number;
  totalBytes: number;
  error?: string | null;
}

export async function checkVersion(
  token: string,
  force: boolean = false,
  base: string = "",
): Promise<UpdateInfo> {
  return request<UpdateInfo>(
    `${base}/api/settings/version-check?force=${force ? "1" : "0"}`,
    token,
    undefined,
    10_000,
  );
}

export async function fetchUpdateStatus(
  token: string,
  base: string = "",
): Promise<UpdateStatus> {
  return request<UpdateStatus>(`${base}/api/settings/update-status`, token);
}

export async function downloadUpdate(
  token: string,
  base: string = "",
): Promise<UpdateInfo> {
  return request<UpdateInfo>(
    `${base}/api/settings/update-download`,
    token,
    undefined,
    15 * 60_000,
  );
}

export async function installUpdate(
  token: string,
  base: string = "",
): Promise<UpdateStatus> {
  return request<UpdateStatus>(
    `${base}/api/settings/update-install`,
    token,
    undefined,
    15 * 60_000,
  );
}

export async function updatePreferences(
  token: string,
  values: {
    autoCheck?: boolean;
    channel?: "stable" | "beta";
    skippedVersion?: string;
  },
  base: string = "",
): Promise<{
  autoCheck: boolean;
  channel: "stable" | "beta";
  skippedVersion: string;
}> {
  const params = new URLSearchParams();
  if (values.autoCheck !== undefined)
    params.set("autoCheck", String(values.autoCheck));
  if (values.channel) params.set("channel", values.channel);
  if (values.skippedVersion !== undefined)
    params.set("skippedVersion", values.skippedVersion);
  return request(`${base}/api/settings/update-preferences?${params}`, token);
}

export interface RuntimeHealth {
  ok: boolean;
  pressure: boolean;
  level: "ok" | "warning" | "critical" | string;
  message?: string | null;
  label?: string | null;
  reasons?: string[];
  memory?: { usedRatio?: number | null; availableGb?: number | null };
  disk?: { usedRatio?: number | null; freeGb?: number | null; path?: string };
  pid?: number;
}

export async function fetchRuntimeHealth(
  token: string,
  base: string = "",
): Promise<RuntimeHealth> {
  return request<RuntimeHealth>(
    `${base}/api/webui/runtime/health`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type MarketingQAVerdict = "PASS" | "WARN" | "BLOCK";

export interface MarketingQAProviderReadiness {
  name: string;
  adapter: boolean;
  credentials: boolean;
  model: string;
  ready: boolean;
  error: string;
}

export interface MarketingQAReadiness {
  schema_version: number;
  workspace: string;
  ready: boolean;
  dependency: { name: string; ready: boolean };
  providers: MarketingQAProviderReadiness[];
}

export interface MarketingQAAsset {
  path: string;
  name: string;
  size: number;
  updated_at: string;
}

export interface MarketingQAFinding {
  check: string;
  source: string;
  status: MarketingQAVerdict;
  score: number;
  evidence: string[];
  recommendation: string;
}

export interface MarketingQAOverride {
  report_id: string;
  created_at: string;
  actor: string;
  reason: string;
  machine_verdict: MarketingQAVerdict;
  prior_effective_verdict: MarketingQAVerdict;
  verdict: MarketingQAVerdict;
}

export interface MarketingQAReportSummary {
  id: string;
  created_at: string;
  candidate: {
    path: string;
    width?: number | null;
    height?: number | null;
  };
  verdict: MarketingQAVerdict;
  effective_verdict: MarketingQAVerdict;
  score: number;
  finding_count: number;
  human_override?: MarketingQAOverride | null;
}

export interface MarketingQAReport extends MarketingQAReportSummary {
  schema_version: number;
  references: string[];
  claims: string[];
  requirements: Record<string, unknown>;
  provider: {
    provider: string | null;
    model: string | null;
    usage: Record<string, number>;
    cost_usd: number | null;
  };
  scores: { deterministic: number; vision: number; overall: number };
  findings: MarketingQAFinding[];
  overrides?: MarketingQAOverride[];
  pass: boolean;
}

export interface MarketingQARunPayload {
  candidate: string;
  references?: string[];
  claims?: string[];
  requirements?: Record<string, unknown>;
}

function marketingQAWorkspaceQuery(path?: string | null): URLSearchParams {
  const query = new URLSearchParams();
  if (path?.trim()) query.set("path", path.trim());
  return query;
}

export async function fetchMarketingQAReadiness(
  token: string,
  path?: string | null,
  base: string = "",
): Promise<MarketingQAReadiness> {
  const query = marketingQAWorkspaceQuery(path);
  return request<MarketingQAReadiness>(
    `${base}/api/webui/marketing-qa/readiness?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMarketingQAAssets(
  token: string,
  path?: string | null,
  base: string = "",
): Promise<{ count: number; assets: MarketingQAAsset[] }> {
  const query = marketingQAWorkspaceQuery(path);
  return request(
    `${base}/api/webui/marketing-qa/assets?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMarketingQAReports(
  token: string,
  path?: string | null,
  base: string = "",
): Promise<{ count: number; reports: MarketingQAReportSummary[] }> {
  const query = marketingQAWorkspaceQuery(path);
  return request(
    `${base}/api/webui/marketing-qa/reports?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMarketingQAReport(
  token: string,
  reportId: string,
  path?: string | null,
  base: string = "",
): Promise<MarketingQAReport> {
  const query = marketingQAWorkspaceQuery(path);
  return request(
    `${base}/api/webui/marketing-qa/reports/${encodeURIComponent(reportId)}?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runMarketingQA(
  token: string,
  payload: MarketingQARunPayload,
  path?: string | null,
  base: string = "",
): Promise<MarketingQAReport> {
  const query = marketingQAWorkspaceQuery(path);
  return request(
    `${base}/api/webui/marketing-qa/run?${query}`,
    token,
    { headers: bodyHeaders(JSON.stringify(payload)) },
    API_SLOW_TIMEOUT_MS,
  );
}

export async function overrideMarketingQAReport(
  token: string,
  reportId: string,
  reason: string,
  verdict: MarketingQAVerdict = "PASS",
  path?: string | null,
  base: string = "",
): Promise<MarketingQAReport> {
  const query = marketingQAWorkspaceQuery(path);
  return request(
    `${base}/api/webui/marketing-qa/reports/${encodeURIComponent(reportId)}/override?${query}`,
    token,
    { headers: bodyHeaders(JSON.stringify({ reason, verdict })) },
    API_READ_TIMEOUT_MS,
  );
}

export type MontageCheckStatus = "ok" | "warn" | "missing";

export interface MontageDoctorCheck {
  name: string;
  status: MontageCheckStatus;
  detail: string;
  fix?: string;
}

export interface MontageMediaToolStatus {
  tool: string;
  provider: string;
  model: string;
  enabled: boolean;
  ready: boolean;
  managed: boolean;
}

export interface MontageProfile {
  id: string;
  label: string;
  width: number;
  height: number;
  aspect_ratio: string;
  default_package: boolean;
}

export interface MontagePackageRow {
  id: string;
  label: string;
  category: string;
  tier: "builtin" | "system" | "lazy" | string;
  description: string;
  size_mb: number;
  optional?: boolean;
  installed?: boolean;
  ready?: boolean;
  installable?: boolean;
  ui_hidden?: boolean;
  needs_action?: boolean;
  detail?: string;
  install_hint?: string;
  signup_url?: string;
}

export interface MontageStatusPayload {
  doctor: {
    toolchain: Record<string, unknown>;
    ready_for_ai: boolean;
    ready_for_composition: boolean;
    checks: MontageDoctorCheck[];
  };
  ready_for_composition: boolean;
  ready_for_ai: boolean;
  ready_for_package: boolean;
  media: {
    plan: string;
    managed_key_active: boolean;
    ready_for_ai: boolean;
    tools: Record<string, MontageMediaToolStatus>;
  };
  packages?: {
    items: MontagePackageRow[];
    stock?: Record<string, unknown>;
    ffmpeg?: { present: boolean; path?: string | null; installable?: boolean };
    hyperframes?: { present: boolean; path?: string | null };
    remotion?: { present: boolean; path?: string | null; optional?: boolean };
  };
  profiles: MontageProfile[];
  assets?: { exists: boolean; count: number; montage_dir: string } | null;
  workspace?: string | null;
  montage_home?: string;
}

export interface MontageAsset {
  path: string;
  name: string;
  kind: "video" | "image" | "audio" | "document" | "file" | string;
  /** What the timeline can do with it: a cut-able clip, a still, or nothing. */
  visual?: "image" | "video" | null;
  bucket: string;
  size: number;
  mtime: number;
  /** Present once the file has been measured (probe), never guessed. */
  duration?: number | null;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  has_audio?: boolean | null;
}

export interface MontageMediaInfo {
  path: string;
  kind: "video" | "image" | "audio" | "file" | string;
  size_bytes: number;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_video: boolean;
  has_audio: boolean;
  video_codec: string | null;
  audio_codec: string | null;
  container: string | null;
  source: "ffprobe" | "ffmpeg" | "none" | string;
}

export interface MontageJobProgress {
  fraction: number | null;
  out_time_s: number;
  frame?: number | null;
  fps?: number | null;
  speed?: number | null;
  done?: boolean;
  updated_at?: string;
}

export type MontageJobStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | string;

export interface MontageJobStep {
  name: string;
  status: MontageJobStatus;
  attempts: number;
  latency_s: number;
  cost: number;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface MontageJob {
  version?: number;
  id: string;
  operation: "assemble" | "timeline-render" | "lipsync" | string;
  status: MontageJobStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  payload: Record<string, unknown>;
  steps: MontageJobStep[];
  latency_s: number;
  cost: number;
  error: string | null;
  progress?: MontageJobProgress | null;
  cancel_requested?: boolean;
  /** True while the gateway still runs this job (cancel is possible). */
  active?: boolean;
}

/** Compact job view broadcast on the websocket (`montage_updated`, kind=job). */
export interface MontageJobSummary {
  id: string;
  operation: string;
  status: MontageJobStatus;
  progress?: MontageJobProgress | null;
  error?: string | null;
  output?: string | null;
  timeline?: string | null;
  created_at?: string;
  updated_at?: string;
  completed_at?: string | null;
  latency_s?: number | null;
  cancel_requested?: boolean;
}

export interface MontageJobsPayload {
  count: number;
  jobs: MontageJob[];
}

/** The rendered output of a montage job, as a workspace-relative path. */
export function montageJobOutput(job: MontageJob | MontageJobSummary): string | null {
  if ("steps" in job && Array.isArray(job.steps)) {
    const last = job.steps.at(-1);
    const fromResult = last?.result?.output;
    if (typeof fromResult === "string" && fromResult) return fromResult;
    const fromPayload = job.payload?.output;
    return typeof fromPayload === "string" && fromPayload ? fromPayload : null;
  }
  const output = (job as MontageJobSummary).output;
  return typeof output === "string" && output ? output : null;
}

export function montageJobIsTerminal(status: MontageJobStatus | null | undefined): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export interface MontageAssetsPayload {
  root: string;
  montage_dir: string;
  exists: boolean;
  count: number;
  assets: MontageAsset[];
  truncated: boolean;
}

export interface MontageTimelineVisual {
  path: string;
  kind: "image" | "video";
  duration: number | null;
  start: number | null;
  end: number | null;
}

export interface MontageTimeline {
  schema_version: 1;
  name: string;
  visuals: MontageTimelineVisual[];
  output: string;
  music: string | null;
  voice: string | null;
  subtitles: string | null;
  width: number;
  height: number;
  fps: number;
  music_gain_db: number;
  transition: string;
  transition_duration: number;
  extra_metadata: Record<string, string>;
}

export interface MontageTimelineSummary {
  name: string;
  schema_version: number;
  visuals: number;
  output: string;
  mtime: number;
}

export interface MontageTimelineListPayload {
  timelines: MontageTimelineSummary[];
  count: number;
}

export interface MontageTimelineResult {
  ok?: boolean;
  name: string;
  path?: string;
  mime?: string;
  size_bytes?: number;
  status?: string;
  output?: string;
  [key: string]: unknown;
}

export type MontageWorkspaceOptions = { sessionKey?: string; path?: string };

function montageWorkspaceQuery(options?: MontageWorkspaceOptions): URLSearchParams {
  const query = new URLSearchParams();
  if (options?.sessionKey) query.set("session_key", options.sessionKey);
  if (options?.path) query.set("path", options.path);
  return query;
}

function montageTimelineUrl(
  base: string,
  name?: string,
  action?: "put" | "delete" | "preview" | "render",
  options?: MontageWorkspaceOptions,
): string {
  const path = name
    ? `/api/webui/montage/timelines/${encodeURIComponent(name)}${action ? `/${action}` : ""}`
    : "/api/webui/montage/timelines";
  const query = montageWorkspaceQuery(options);
  return `${base}${path}${query.size ? `?${query}` : ""}`;
}

export function listMontageTimelines(
  token: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageTimelineListPayload> {
  return request(
    montageTimelineUrl(base, undefined, undefined, options),
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export function fetchMontageTimeline(
  token: string,
  name: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageTimeline> {
  return request(
    montageTimelineUrl(base, name, undefined, options),
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export function saveMontageTimeline(
  token: string,
  timeline: MontageTimeline,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageTimeline> {
  return request(
    montageTimelineUrl(base, timeline.name, "put", options),
    token,
    { headers: bodyHeaders(JSON.stringify(timeline)) },
    API_READ_TIMEOUT_MS,
  );
}

export function deleteMontageTimeline(
  token: string,
  name: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageTimelineResult> {
  return request(
    montageTimelineUrl(base, name, "delete", options),
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export function previewMontageTimeline(
  token: string,
  name: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageTimelineResult> {
  return request(
    montageTimelineUrl(base, name, "preview", options),
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

/**
 * Start rendering a saved timeline. By default the gateway answers at once with
 * the job manifest (status pending/running) and encodes in the background: follow
 * it with `fetchMontageJob` / `onMontageUpdate` and stop it with `cancelMontageJob`.
 * `wait: true` keeps the request open until ffmpeg finishes.
 */
export function renderMontageTimeline(
  token: string,
  name: string,
  options?: MontageWorkspaceOptions & { output?: string; wait?: boolean },
  base = "",
): Promise<MontageJob> {
  const query = montageWorkspaceQuery(options);
  if (options?.output) query.set("output", options.output);
  if (options?.wait) query.set("wait", "1");
  const path = `${base}/api/webui/montage/timelines/${encodeURIComponent(name)}/render`;
  return request(
    `${path}${query.size ? `?${query}` : ""}`,
    token,
    undefined,
    options?.wait ? MONTAGE_SETUP_TIMEOUT_MS : API_SLOW_TIMEOUT_MS,
  );
}

function montageJobUrl(
  base: string,
  jobId: string,
  action: "cancel" | "resume" | undefined,
  options?: MontageWorkspaceOptions,
  extra?: Record<string, string>,
): string {
  const query = montageWorkspaceQuery(options);
  for (const [key, value] of Object.entries(extra ?? {})) query.set(key, value);
  const path = `${base}/api/webui/montage/jobs/${encodeURIComponent(jobId)}${action ? `/${action}` : ""}`;
  return `${path}${query.size ? `?${query}` : ""}`;
}

export function listMontageJobs(
  token: string,
  options?: MontageWorkspaceOptions & { status?: string; limit?: number },
  base = "",
): Promise<MontageJobsPayload> {
  const query = montageWorkspaceQuery(options);
  if (options?.status) query.set("status", options.status);
  if (options?.limit) query.set("limit", String(options.limit));
  return request(
    `${base}/api/webui/montage/jobs${query.size ? `?${query}` : ""}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export function fetchMontageJob(
  token: string,
  jobId: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageJob> {
  return request(montageJobUrl(base, jobId, undefined, options), token, undefined, API_READ_TIMEOUT_MS);
}

/** Kill the running ffmpeg (or flip a stale manifest) and mark the job cancelled. */
export function cancelMontageJob(
  token: string,
  jobId: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageJob> {
  return request(montageJobUrl(base, jobId, "cancel", options), token, undefined, API_READ_TIMEOUT_MS);
}

/** Re-run the failed / cancelled steps of a job in the background. */
export function resumeMontageJob(
  token: string,
  jobId: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageJob> {
  return request(
    montageJobUrl(base, jobId, "resume", options, { wait: "0" }),
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

/** Measure a workspace media file: real duration, dimensions, fps, audio. */
export function probeMontageMedia(
  token: string,
  file: string,
  options?: MontageWorkspaceOptions,
  base = "",
): Promise<MontageMediaInfo> {
  const query = montageWorkspaceQuery(options);
  query.set("file", file);
  return request(`${base}/api/webui/montage/probe?${query}`, token, undefined, API_SLOW_TIMEOUT_MS);
}

export interface MontageSetupResult {
  ok: boolean;
  home?: string;
  hyperframes?: string | null;
  path?: string | null;
  chrome?: string | null;
  logs?: string[];
  skipped?: boolean;
  error?: string;
  fix?: string;
  package?: string;
  note?: string;
}

export type MontageSetupStreamEvent = {
  type: "start" | "log" | "done" | "status" | string;
  id?: string;
  command?: string;
  cwd?: string;
  data?: string;
  exit_code?: number | null;
  setup?: MontageSetupResult;
  status?: MontageStatusPayload;
};

export interface MontageSetupPayload {
  setup: MontageSetupResult;
  status: MontageStatusPayload;
  /** NDJSON events when called with ``stream: true`` (fallback if WS feed is missed). */
  events?: MontageSetupStreamEvent[];
}

export async function fetchMontageStatus(
  token: string,
  options?: { sessionKey?: string; path?: string },
  base: string = "",
): Promise<MontageStatusPayload> {
  const query = new URLSearchParams();
  if (options?.sessionKey) query.set("session_key", options.sessionKey);
  if (options?.path) query.set("path", options.path);
  const suffix = query.size > 0 ? `?${query}` : "";
  return request<MontageStatusPayload>(
    `${base}/api/webui/montage/status${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMontageAssets(
  token: string,
  options?: { sessionKey?: string; path?: string },
  base: string = "",
): Promise<MontageAssetsPayload> {
  const query = new URLSearchParams();
  if (options?.sessionKey) query.set("session_key", options.sessionKey);
  if (options?.path) query.set("path", options.path);
  const suffix = query.size > 0 ? `?${query}` : "";
  return request<MontageAssetsPayload>(
    `${base}/api/webui/montage/assets${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Montage npm / ffmpeg installs can take several minutes on a cold cache. */
const MONTAGE_SETUP_TIMEOUT_MS = 30 * 60_000;

export async function runMontageSetup(
  token: string,
  options?: {
    force?: boolean;
    package?: string;
    sessionKey?: string;
    path?: string;
    /** When true, response is NDJSON (also streams live agent_exec on the WS). */
    stream?: boolean;
  },
  base: string = "",
): Promise<MontageSetupPayload> {
  const query = new URLSearchParams();
  if (options?.force) query.set("force", "1");
  if (options?.package) query.set("package", options.package);
  if (options?.sessionKey) query.set("session_key", options.sessionKey);
  if (options?.path) query.set("path", options.path);
  if (options?.stream) query.set("stream", "1");
  const suffix = query.size > 0 ? `?${query}` : "";
  const url = `${base}/api/webui/montage/setup${suffix}`;
  if (!options?.stream) {
    return request<MontageSetupPayload>(
      url,
      token,
      undefined,
      MONTAGE_SETUP_TIMEOUT_MS,
    );
  }
  // Stream path: consume NDJSON for the final status event while live logs
  // also arrive on the chat websocket as agent_exec frames.
  const res = await fetchWithTimeout(
    url,
    {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "same-origin",
    },
    MONTAGE_SETUP_TIMEOUT_MS,
  );
  if (!res.ok) {
    const text =
      typeof res.text === "function" ? (await res.text()).trim() : "";
    throw new ApiError(res.status, text || `HTTP ${res.status}`);
  }
  const raw = await res.text();
  let setup: MontageSetupPayload["setup"] | null = null;
  let status: MontageStatusPayload | null = null;
  const events: MontageSetupStreamEvent[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const row = JSON.parse(trimmed) as MontageSetupStreamEvent;
      events.push(row);
      if (row.type === "status") {
        if (row.setup) setup = row.setup;
        if (row.status) status = row.status;
      }
    } catch {
      // ignore non-JSON tails
    }
  }
  if (!setup || !status) {
    throw new ApiError(
      500,
      "Montage setup stream ended without a status event.",
    );
  }
  return { setup, status, events };
}

export async function fetchWorkspaces(
  token: string,
  base: string = "",
): Promise<WorkspacesPayload> {
  return request<WorkspacesPayload>(
    `${base}/api/workspaces`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchDocumentTemplates(
  token: string,
  base: string = "",
): Promise<DocumentTemplatesPayload> {
  return request<DocumentTemplatesPayload>(
    `${base}/api/document-templates`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMediaTemplates(
  token: string,
  studio?: "marketing" | "montage",
  base: string = "",
): Promise<MediaTemplatesPayload> {
  const query = studio ? `?studio=${encodeURIComponent(studio)}` : "";
  return request<MediaTemplatesPayload>(
    `${base}/api/media-templates${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type { MediaTemplateItem } from "@/lib/types";

export async function fetchCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchInstalledCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps?installed_only=1`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchNavinFeatures(
  token: string,
  base: string = "",
): Promise<NavinFeaturesPayload> {
  return request<NavinFeaturesPayload>(
    `${base}/api/settings/navin-features`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchApiService(
  token: string,
  base: string = "",
): Promise<ApiServicePayload> {
  return request<ApiServicePayload>(`${base}/api/settings/api-service`, token);
}

export async function startApiService(
  token: string,
  values: { host: string; port: number; timeout: number; apiKey?: string },
  base: string = "",
): Promise<ApiServicePayload> {
  const query = new URLSearchParams({
    host: values.host,
    port: String(values.port),
    timeout: String(values.timeout),
  });
  const headers =
    values.apiKey === undefined
      ? undefined
      : {
          [API_SERVICE_VALUES_HEADER]: JSON.stringify({
            api_key: values.apiKey,
          }),
        };
  return request<ApiServicePayload>(
    `${base}/api/settings/api-service/start?${query}`,
    token,
    { headers },
  );
}

export async function stopApiService(
  token: string,
  base: string = "",
): Promise<ApiServicePayload> {
  return request<ApiServicePayload>(
    `${base}/api/settings/api-service/stop`,
    token,
  );
}

export async function enableNavinFeature(
  token: string,
  name: string,
  options: { instanceId?: string } = {},
  base: string = "",
): Promise<NavinFeaturesPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  if (options.instanceId) query.set("instance_id", options.instanceId);
  return request<NavinFeaturesPayload>(
    `${base}/api/settings/navin-features/enable?${query}`,
    token,
  );
}

export async function disableNavinFeature(
  token: string,
  name: string,
  options: { instanceId?: string } = {},
  base: string = "",
): Promise<NavinFeaturesPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  if (options.instanceId) query.set("instance_id", options.instanceId);
  return request<NavinFeaturesPayload>(
    `${base}/api/settings/navin-features/disable?${query}`,
    token,
  );
}

export async function fetchPairingRequests(
  token: string,
  base: string = "",
): Promise<PairingPayload> {
  return request<PairingPayload>(
    `${base}/api/settings/pairing`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runPairingAction(
  token: string,
  action: "approve" | "deny",
  code: string,
  base: string = "",
): Promise<PairingPayload> {
  const query = new URLSearchParams();
  query.set("code", code);
  return request<PairingPayload>(
    `${base}/api/settings/pairing/${action}?${query}`,
    token,
  );
}

export async function configureChannel(
  token: string,
  name: string,
  values: Record<string, string>,
  options: { enable?: boolean; instanceId?: string } = {},
  base: string = "",
): Promise<ChannelConfigurePayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  if (options.enable !== undefined) query.set("enable", String(options.enable));
  if (options.instanceId) query.set("instance_id", options.instanceId);
  return request<ChannelConfigurePayload>(
    `${base}/api/settings/channels/configure?${query}`,
    token,
    {
      headers: {
        [CHANNEL_VALUES_HEADER]: JSON.stringify(values),
      },
    },
  );
}

export async function validateChannel(
  token: string,
  name: string,
  values: Record<string, string> = {},
  options: { instanceId?: string } = {},
  base: string = "",
): Promise<ChannelValidationPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  if (options.instanceId) query.set("instance_id", options.instanceId);
  return request<ChannelValidationPayload>(
    `${base}/api/settings/channels/validate?${query}`,
    token,
    {
      headers: {
        [CHANNEL_VALUES_HEADER]: JSON.stringify(values),
      },
    },
  );
}

export async function startChannelLogin(
  token: string,
  name: string,
  options: { force?: boolean } = {},
  base: string = "",
): Promise<ChannelLoginPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  if (options.force) query.set("force", "true");
  return request<ChannelLoginPayload>(
    `${base}/api/settings/channels/login/start?${query}`,
    token,
  );
}

export async function fetchChannelLoginStatus(
  token: string,
  name: string,
  base: string = "",
): Promise<ChannelLoginPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<ChannelLoginPayload>(
    `${base}/api/settings/channels/login/status?${query}`,
    token,
  );
}

export async function cancelChannelLogin(
  token: string,
  name: string,
  base: string = "",
): Promise<ChannelLoginPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<ChannelLoginPayload>(
    `${base}/api/settings/channels/login/cancel?${query}`,
    token,
  );
}

export async function runCliAppAction(
  token: string,
  action: "install" | "update" | "uninstall" | "test",
  name: string,
  base: string = "",
): Promise<CliAppsPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps/${action}?${query}`,
    token,
  );
}

export async function fetchMcpPresets(
  token: string,
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchProviderModels(
  token: string,
  provider: string,
  base: string = "",
  options?: { modality?: import("./types").MediaModelKind },
): Promise<ProviderModelsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  if (options?.modality) query.set("modality", options.modality);
  return request<ProviderModelsPayload>(
    `${base}/api/settings/provider-models?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchOllamaSetup(
  token: string,
  base: string = "",
): Promise<OllamaSetupPayload> {
  return request<OllamaSetupPayload>(
    `${base}/api/settings/ollama`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runOllamaSetupAction(
  token: string,
  action: "install" | "start" | "pull" | "configure",
  options: { model?: string; makeActive?: boolean } = {},
  base: string = "",
): Promise<OllamaSetupPayload> {
  const query = new URLSearchParams();
  if (options.model) query.set("model", options.model);
  if (options.makeActive === false) query.set("make_active", "0");
  const suffix = query.toString() ? `?${query}` : "";
  // Pulls can take a long time for multi-GB models.
  const timeoutMs =
    action === "pull" || action === "install"
      ? 30 * 60_000
      : API_READ_TIMEOUT_MS;
  return request<OllamaSetupPayload>(
    `${base}/api/settings/ollama/${action}${suffix}`,
    token,
    undefined,
    timeoutMs,
  );
}

export async function fetchOmniRouteSetup(
  token: string,
  base: string = "",
): Promise<OmniRouteSetupPayload> {
  return request<OmniRouteSetupPayload>(
    `${base}/api/settings/omniroute`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runOmniRouteSetupAction(
  token: string,
  action: "install" | "start" | "configure",
  options: { model?: string; makeActive?: boolean } = {},
  base: string = "",
): Promise<OmniRouteSetupPayload> {
  const query = new URLSearchParams();
  if (options.model) query.set("model", options.model);
  if (options.makeActive === false) query.set("make_active", "0");
  const suffix = query.toString() ? `?${query}` : "";
  // npm install pulls a Next.js app plus native deps; the start action waits
  // for the server to answer on port 20128.
  const timeoutMs =
    action === "install" ? 20 * 60_000 : action === "start" ? 90_000 : API_READ_TIMEOUT_MS;
  return request<OmniRouteSetupPayload>(
    `${base}/api/settings/omniroute/${action}${suffix}`,
    token,
    undefined,
    timeoutMs,
  );
}

export async function runMcpPresetAction(
  token: string,
  action: "enable" | "remove" | "test",
  name: string,
  values: Record<string, string> = {},
  base: string = "",
): Promise<McpPresetsPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets/${action}?${query}`,
    token,
    { headers: mcpValuesHeader(values) },
  );
}

export async function saveCustomMcpServer(
  token: string,
  values: Record<string, string>,
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets/custom`,
    token,
    { headers: mcpValuesHeader(values) },
  );
}

export async function importMcpConfig(
  token: string,
  config: string,
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets/import`,
    token,
    { headers: mcpValuesHeader({ config }) },
  );
}

export async function updateMcpServerTools(
  token: string,
  name: string,
  enabledTools: string[],
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets/tools`,
    token,
    { headers: mcpValuesHeader({ name, enabled_tools: enabledTools }) },
  );
}

export async function listSlashCommands(
  token: string,
  base: string = "",
  module?: string | null,
): Promise<SlashCommand[]> {
  type Row = {
    command: string;
    title: string;
    description: string;
    icon: string;
    arg_hint?: string;
    lifecycle?: unknown;
    accepts_args?: unknown;
  };
  const query =
    module && module.trim()
      ? `?module=${encodeURIComponent(module.trim())}`
      : "";
  const body = await request<{ commands: Row[] }>(
    `${base}/api/commands${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.commands.flatMap((command) => {
    if (!isSlashCommandLifecycle(command.lifecycle)) return [];
    return [
      {
        command: command.command,
        title: command.title,
        description: command.description,
        icon: command.icon,
        argHint: command.arg_hint ?? "",
        lifecycle: command.lifecycle,
        acceptsArgs: command.accepts_args === true,
      },
    ];
  });
}

export async function fetchSidebarState(
  token: string,
  base: string = "",
): Promise<SidebarStatePayload> {
  return request<SidebarStatePayload>(
    `${base}/api/webui/sidebar-state`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSidebarState(
  token: string,
  state: SidebarStatePayload,
  base: string = "",
): Promise<SidebarStatePayload> {
  // Body goes in chunked headers: a GET query blows past OS URL buffers
  // (ERR_NO_BUFFER_SPACE) once module_by_key / recent_projects grow.
  return request<SidebarStatePayload>(
    `${base}/api/webui/sidebar-state/update`,
    token,
    { headers: bodyHeaders(JSON.stringify(state)) },
  );
}

export type OnboardingStatePayload = {
  completed: boolean;
  completedAt?: string;
  language?: string;
  path?: "account" | "byok" | "free" | "omniroute" | "ollama" | "skip";
  demoOpened?: boolean;
};

export async function fetchOnboardingState(
  token: string,
  base: string = "",
): Promise<OnboardingStatePayload> {
  return request<OnboardingStatePayload>(
    `${base}/api/webui/onboarding`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateOnboardingState(
  token: string,
  state: OnboardingStatePayload,
  base: string = "",
): Promise<OnboardingStatePayload> {
  return request<OnboardingStatePayload>(
    `${base}/api/webui/onboarding/update`,
    token,
    { headers: bodyHeaders(JSON.stringify(state)) },
  );
}

export type BoardTaskStatus =
  | "backlog"
  | "planned"
  | "in_progress"
  | "review"
  | "audit"
  | "fix"
  | "blocked"
  | "cancelled"
  | "done";

export type BoardActorType = "human" | "agent" | "subagent";

export type BoardAssignee = { type: BoardActorType; name: string };

export type BoardComment = {
  author: string;
  author_type: BoardActorType;
  text: string;
  ts: string;
};

export type BoardTask = {
  id: string;
  title: string;
  description: string;
  status: BoardTaskStatus;
  priority: "low" | "medium" | "high" | "critical";
  labels: string[];
  assignee: BoardAssignee | null;
  milestone_id: string | null;
  depends_on: string[];
  created_by: BoardAssignee;
  created_at: string;
  updated_at: string;
  comments: BoardComment[];
  /** Git / GitHub trail written by the autonomy hooks. */
  branch?: string | null;
  pr_url?: string | null;
  issue_url?: string | null;
};

export type BoardMilestone = {
  id: string;
  title: string;
  description: string;
  target_date: string | null;
  status: "planned" | "active" | "done";
  created_at: string;
  updated_at: string;
};

export type BoardActivityEntry = {
  ts: string;
  kind: string;
  actor: string;
  actor_type: BoardActorType;
  task_id?: string;
  milestone_id?: string;
  detail?: string;
};

export type BoardTaskPlan = {
  /** Dependency ids still unfinished; empty means nothing stands in the way. */
  blocked_by: string[];
  /** Every dependency is done, so the task can start now. */
  ready: boolean;
  /** How many open tasks transitively wait on this one. */
  dependents: number;
  on_critical_path: boolean;
};

export type BoardPlan = {
  tasks: Record<string, BoardTaskPlan>;
  ready_queue: string[];
  blocked: Array<{ id: string; blocked_by: string[]; status: BoardTaskStatus }>;
  /** Longest chain of open work, dependencies first. */
  critical_path: string[];
  in_progress: string[];
  open_count: number;
  done_count: number;
  cycles: string[][];
  dangling: Record<string, string[]>;
};

export type BoardMilestoneProgress = {
  total: number;
  done: number;
  open: number;
  blocked: number;
};

export type SessionPlanItem = {
  id: string;
  title: string;
  /** First lines of the task description, empty when the task has none. */
  description?: string;
  status: BoardTaskStatus;
  priority: string;
  done: boolean;
  /** Someone is on it: in_progress, review or audit. */
  active: boolean;
  blocked: boolean;
  /** Stopped mid-run via /stop. */
  cancelled?: boolean;
  acceptance?: string;
  validation?: string;
  retry_count?: number;
};

/**
 * The narrow plan for one conversation: only the tasks this run touched.
 *
 * Distinct from `BoardPlan`, which answers for the whole project. Null when the
 * run has not touched the board, which means no panel rather than an empty one.
 */
/** One concrete gap the pre-Build plan judge found (P2-7). */
export type SessionPlanQualityGap = {
  kind: string;
  task_id?: string | null;
  message: string;
};

/** Verdict of the deterministic plan quality judge, shown before Build. */
export type SessionPlanQuality = {
  status: "ready" | "gaps";
  gaps: SessionPlanQualityGap[];
};

export type SessionPlan = {
  items: SessionPlanItem[];
  current_id: string;
  /** Title of the task the heading should name. */
  title: string;
  done_count: number;
  total_count: number;
  complete: boolean;
  /** Pre-Build quality judge: "ready" or the concrete gaps (P2-7). */
  quality?: SessionPlanQuality;
  /** Mission ledger fields when `.navin/board/mission.json` exists. */
  goal?: string;
  version?: number;
  ledger_status?: string;
  stall_count?: number;
  loop_detected?: boolean;
  replan_needed?: boolean;
  pause_reason?: string;
  acceptance_criteria?: string[];
  last_change?: { version?: number; reason?: string };
  token_budget?: number;
  tokens_used?: number;
};

/** Mission ledger (``.navin/board/mission.json``) when attached to a board fetch. */
export type BoardMission = {
  goal?: string;
  version?: number;
  status?: string;
  constraints?: string[];
  facts?: string[];
  missing_info?: string[];
  acceptance_criteria?: string[];
  pause_reason?: string;
};

/** Per-project autonomy consent and toggles (``.navin/board/settings.json``). */
export type BoardAutonomy = {
  enabled: boolean;
  consented_at: string | null;
  auto_branch: boolean;
  open_pr_on_done: boolean;
  sync_github_issues: boolean;
  fix_issues: boolean;
  autopilot_loop: boolean;
  /** Tracker for this project (`owner/name`), null when it uses its own remote. */
  issues_repo?: string | null;
  effective?: {
    auto_branch: boolean;
    open_pr_on_done: boolean;
    sync_github_issues: boolean;
    fix_issues: boolean;
    chain_ready_tasks: boolean;
  };
  global?: {
    auto_branch_enabled: boolean;
    open_pr_enabled: boolean;
  };
};

export type BoardPayload = {
  schema_version: number;
  project_path: string;
  statuses: BoardTaskStatus[];
  priorities: string[];
  tasks: BoardTask[];
  milestones: BoardMilestone[];
  activity: BoardActivityEntry[];
  plan?: BoardPlan;
  milestone_progress?: Record<string, BoardMilestoneProgress>;
  session_plan?: SessionPlan | null;
  mission?: BoardMission | null;
  autonomy?: BoardAutonomy | null;
};

export type BoardUpdateOp =
  | {
      action: "create_task";
      task: Partial<BoardTask> & { title: string };
      actor?: string;
    }
  | {
      action: "update_task";
      task_id: string;
      fields: Partial<BoardTask>;
      actor?: string;
    }
  | { action: "comment_task"; task_id: string; text: string; actor?: string }
  | { action: "delete_task"; task_id: string; actor?: string }
  | {
      action: "create_milestone";
      milestone: Partial<BoardMilestone> & { title: string };
      actor?: string;
    }
  | {
      action: "update_milestone";
      milestone_id: string;
      fields: Partial<BoardMilestone>;
      actor?: string;
    }
  | { action: "delete_milestone"; milestone_id: string; actor?: string }
  | { action: "sync_github"; actor?: string; repo?: string | null }
  | { action: "pause_mission"; reason?: string; actor?: string }
  | { action: "resume_mission"; actor?: string }
  | {
      action: "update_mission";
      fields: {
        goal?: string;
        constraints?: string[];
        facts?: string[];
        missing_info?: string[];
        acceptance_criteria?: string[];
        status?: string;
      };
      actor?: string;
    };

export async function fetchBoard(
  token: string,
  key: string,
  base: string = "",
): Promise<BoardPayload> {
  return request<BoardPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/board`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type ProjectBrainFileKey = "soul" | "user" | "memory";
export type ProjectContinuityFileKey = "resume" | "decisions";
export type ProjectBrainEditorKey =
  | ProjectBrainFileKey
  | ProjectContinuityFileKey;

export type ProjectBrainFile = {
  path: string;
  exists: boolean;
  content: string;
  bytes: number;
};

export type ProjectRuleEntry = {
  path: string;
  name: string;
  exists: boolean;
  bytes: number;
  content: string;
};

export type ProjectBrainPayload = {
  project_path: string;
  project_name: string;
  created: string[];
  shared: boolean;
  files: Record<ProjectBrainFileKey, ProjectBrainFile>;
  continuity?: Record<"resume" | "decisions", ProjectBrainFile>;
  pack?: {
    path: string;
    exists: boolean;
    manifest: Record<string, unknown> | null;
  };
  constraints: string[];
  rules?: ProjectRuleEntry[];
  rules_dir?: string;
  drift: {
    supported: boolean;
    violations: Array<
      | string
      | {
          constraint?: string;
          severity?: string;
          paths?: string[];
          tokens?: string[];
          detail?: string;
          in_commit_message?: boolean;
        }
    >;
    note: string;
    source?: string;
    paths_checked?: string[];
  };
};

export type ProjectRulesPayload = {
  project_path: string;
  rules_dir: string;
  rules: ProjectRuleEntry[];
  total: number;
};

export async function fetchProjectBrain(
  token: string,
  key: string,
  base: string = "",
): Promise<ProjectBrainPayload> {
  return request<ProjectBrainPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/project-brain`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type ResumeSeedPayload = {
  project_path: string;
  project_name: string;
  seed: string;
  brief: string;
  git: string[];
  decisions: string[];
  constraints: string[];
  tasks: Array<{
    id: string;
    title: string;
    status: string;
    branch?: string;
    pr_url?: string;
    head_sha?: string;
  }>;
  actionable: boolean;
};

export async function fetchResumeSeed(
  token: string,
  key: string,
  base: string = "",
): Promise<ResumeSeedPayload> {
  return request<ResumeSeedPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/resume-seed`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function leaveHandoff(
  token: string,
  key: string,
  body: string,
  base: string = "",
): Promise<{ saved: boolean; path: string; chars: number }> {
  return request<{ saved: boolean; path: string; chars: number }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/leave-handoff`,
    token,
    { headers: bodyHeaders(body) },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchProjectRules(
  token: string,
  key: string,
  base: string = "",
): Promise<ProjectRulesPayload> {
  return request<ProjectRulesPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/project-rules`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function saveProjectRule(
  token: string,
  key: string,
  name: string,
  content: string,
  base: string = "",
): Promise<{ saved: boolean; rule: ProjectRuleEntry }> {
  const params = new URLSearchParams();
  params.set("name", name);
  return request<{ saved: boolean; rule: ProjectRuleEntry }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/project-rules/save?${params}`,
    token,
    { headers: bodyHeaders(content) },
    API_READ_TIMEOUT_MS,
  );
}

export async function updateBoard(
  token: string,
  key: string,
  op: BoardUpdateOp,
  base: string = "",
): Promise<BoardPayload> {
  const params = new URLSearchParams();
  params.set("op", JSON.stringify(op));
  return request<BoardPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/board/update?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type GithubIssue = {
  number: number;
  title: string;
  body: string;
  url: string;
  state: "open" | "closed" | string;
  labels: string[];
  author: string | null;
  assignees: string[];
  created_at: string | null;
  updated_at: string | null;
  comments: number;
};

export type GithubCliInstall = {
  needed: boolean;
  platform: "macos" | "linux" | "wsl" | "windows";
  flavor: string;
  label: string;
  command: string;
  auth_command: string;
  docs_url: string;
  alternatives: Array<{ id: string; label: string; command: string }>;
};

/** What to add when the forge refuses the panel for lack of credentials. */
export type ForgeTokenSetup = {
  host: string;
  forge: ForgeKind;
  /** Env vars the backend reads for this host, most specific first. */
  env: string[];
};

export type GithubIssuesPayload = {
  ok: boolean;
  project_path: string;
  state: string;
  /** Tracker the issues come from (`owner/name`), null for the project's remote. */
  repo: string | null;
  issues: GithubIssue[];
  detail: string;
  /** Present when the host has no ``gh``: OS-specific install steps. */
  install?: GithubCliInstall | null;
  /** Which forge answers this panel: issues exist on all three, worded alike. */
  forge?: ForgeKind;
  forge_label?: string;
  host?: string;
  /** `owner/repo` actually queried, subgroups included on GitLab. */
  remote_repo?: string | null;
  /** Present when no token is configured for `host`. */
  token_setup?: ForgeTokenSetup | null;
};

export async function fetchGithubIssues(
  token: string,
  key: string,
  state: "open" | "closed" | "all" = "open",
  base: string = "",
  projectPath?: string | null,
): Promise<GithubIssuesPayload> {
  const params = new URLSearchParams();
  params.set("state", state);
  // Prefer the UI-selected project over the session workspace: a chat bound
  // to a parent folder without remotes used to poison Issues.
  if (projectPath?.trim()) params.set("project", projectPath.trim());
  return request<GithubIssuesPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/issues?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Point this project's Issues panel at another repository (`owner/name`, or a
 * github.com URL), or pass an empty string to fall back to its own git remote.
 * Persisted in `<project>/.navin/board/settings.json`.
 */
export async function setGithubIssuesRepo(
  token: string,
  key: string,
  repo: string,
  base: string = "",
  projectPath?: string | null,
): Promise<{ ok: boolean; project_path: string; repo: string | null }> {
  const params = new URLSearchParams();
  params.set("repo", repo.trim());
  if (projectPath?.trim()) params.set("project", projectPath.trim());
  return request<{ ok: boolean; project_path: string; repo: string | null }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/issues/repo?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateBoardAutonomy(
  token: string,
  key: string,
  fields: Partial<
    Pick<
      BoardAutonomy,
      | "enabled"
      | "auto_branch"
      | "open_pr_on_done"
      | "sync_github_issues"
      | "fix_issues"
      | "autopilot_loop"
    >
  >,
  base: string = "",
): Promise<BoardAutonomy> {
  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(fields));
  return request<BoardAutonomy>(
    `${base}/api/sessions/${encodeURIComponent(key)}/board/autonomy?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Project opt-in for the cognition sidecar (`.navin/cognition.json`). */
export type CognitionState = {
  enabled: boolean;
  /** Journal one compact line per finished turn in .navin/memory/episodes.jsonl. */
  episodes: boolean;
  /** Register the read-only `recall` tool that searches the journal and MEMORY.md. */
  recall: boolean;
  journal_bytes: number;
  settings_file: string;
  /** Whether the serving agent has the tool right now; null when the gateway cannot tell. */
  recall_registered: boolean | null;
  /** True when recall is wanted but the agent does not have it yet. */
  recall_requires_restart: boolean;
};

export type CognitionFields = Partial<Pick<CognitionState, "enabled" | "episodes" | "recall">>;

export async function fetchCognition(
  token: string,
  key: string,
  base: string = "",
): Promise<CognitionState> {
  return request<CognitionState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/cognition`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateCognition(
  token: string,
  key: string,
  fields: CognitionFields,
  base: string = "",
): Promise<CognitionState> {
  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(fields));
  return request<CognitionState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/cognition?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** One skill draft of the evolution corridor (`.navin/skills-draft/<name>`). */
export type AgiDraft = {
  name: string;
  status:
    | "drafting"
    | "examining"
    | "eligible"
    | "flat"
    | "rejected"
    | "promoted"
    | "retired"
    | "discarded";
  /** Best exam score out of 20, null before the first exam. */
  score: number | null;
  baseline_score: number | null;
  verdict: "up" | "flat" | "down" | null;
  suites: Record<string, "up" | "flat" | "down">;
  eligible: boolean;
  attempts: number;
  battery_version: string | null;
  origin: string | null;
  trigger: string | null;
  created_at: string;
  updated_at: string;
  promoted_at: string | null;
  published_at: string | null;
  forced_by: string | null;
  note: string | null;
  in_project: boolean;
  in_harness: boolean;
  paths: { draft: string; project: string };
};

export type AgiJournalEntry = { ts: string; event: string; name?: string } & Record<string, unknown>;

/** Project flag for skills evolution (`.navin/skills-evolve.json`) plus what exists on disk. */
export type AgiState = {
  enabled: boolean;
  /** Navin may write skill drafts on its own. */
  draft: boolean;
  /** An eligible draft enters .navin/skills without a click. */
  promote_project: boolean;
  /** Offer the human "publish for every project" button. */
  publish_harness: boolean;
  failure_threshold: number;
  max_attempts: number;
  exam_model: "lexical" | "llm";
  author: "auto" | "template" | "llm";
  settings_file: string;
  drafts_dir: string;
  harness_dir: string;
  battery: {
    version: string | null;
    revision?: number;
    total_cases?: number;
    suites: Array<{ id: string; title: string; cases: number }>;
    error?: string;
  };
  drafts: AgiDraft[];
  pending_jobs: Array<{ name: string | null; ts: string | null; source: string | null }>;
  runner_alive: boolean;
  journal: AgiJournalEntry[];
};

export type AgiFields = Partial<
  Pick<AgiState, "enabled" | "draft" | "promote_project" | "publish_harness">
>;

export type AgiAction =
  | "draft"
  | "run"
  | "exam"
  | "promote"
  | "force"
  | "publish"
  | "rollback"
  | "discard"
  | "guard";

export type AgiActionResult = {
  ok: boolean;
  action: AgiAction;
  result: unknown;
  state: AgiState;
};

export async function fetchAgi(token: string, key: string, base: string = ""): Promise<AgiState> {
  return request<AgiState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/agi`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateAgi(
  token: string,
  key: string,
  fields: AgiFields,
  base: string = "",
): Promise<AgiState> {
  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(fields));
  return request<AgiState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/agi?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Press one AGI button. `publish` and `force` are human-only on the gateway:
 * the panel always sends `actor=human`, the engine has no route here.
 */
export async function agiAction(
  token: string,
  key: string,
  action: AgiAction,
  options: { name?: string; brief?: Record<string, unknown>; base?: string } = {},
): Promise<AgiActionResult> {
  const base = options.base ?? "";
  const params = new URLSearchParams();
  params.set("action", action);
  params.set("actor", "human");
  if (options.name) params.set("name", options.name);
  if (options.brief) params.set("brief", JSON.stringify(options.brief));
  return request<AgiActionResult>(
    `${base}/api/sessions/${encodeURIComponent(key)}/agi/action?${params}`,
    token,
    undefined,
    // Exams run in the request: give the corridor time to answer.
    120_000,
  );
}

export async function fetchAgiDraft(
  token: string,
  key: string,
  name: string,
  base: string = "",
): Promise<{ name: string; markdown: string }> {
  const params = new URLSearchParams();
  params.set("name", name);
  return request<{ name: string; markdown: string }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/agi/draft?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Observation classes the world model predicts (`navin.world_model.trajectory.CLASSES`). */
export type WorldClass = "ok" | "changed" | "empty" | "not_found" | "denied" | "timeout" | "error";

export type WorldVerdict = "up" | "flat" | "down";

/** One exam line: baseline -> checkpoint on a frozen held-out version. */
export type WorldScore = {
  ts: string | null;
  checkpoint: number | null;
  heldout_version: string | null;
  n: number | null;
  log_loss: number | null;
  error_rate: number | null;
  ece: number | null;
  baseline_kind: "always_ok" | "majority" | null;
  baseline_log_loss: number | null;
  baseline_error_rate: number | null;
  verdict: WorldVerdict | null;
  verdict_vs_active: WorldVerdict | null;
  activated: boolean | null;
  exam: boolean;
};

export type WorldCheckpoint = {
  number: number;
  trained_at: string | null;
  rows: number | null;
  heldout_version: string | null;
  metrics: { n: number; log_loss: number; error_rate: number; ece: number } | null;
  baseline: { kind: string; n: number; log_loss: number; error_rate: number; ece: number } | null;
  verdict_vs_baseline: WorldVerdict | null;
  verdict_vs_active: WorldVerdict | null;
  actor: string | null;
  keys: number;
  active: boolean;
  previous: boolean;
};

export type WorldBelief = {
  key: string;
  cls: WorldClass;
  confidence: number;
  support: number;
  text: string;
};

export type WorldGate = {
  open: boolean;
  reasons: string[];
  checkpoint: number | null;
  heldout_version: string | null;
  exam_verdict: WorldVerdict | null;
  ab_verdict: "gain" | "no_gain" | "regress" | null;
};

export type WorldAb = {
  ts: string;
  checkpoint: number;
  heldout_version: string;
  threshold: number;
  n: number;
  useless_calls: number;
  flagged: number;
  avoided: number;
  false_alarms: number;
  precision: number | null;
  avoided_share: number;
  verdict: "gain" | "no_gain" | "regress";
};

export type WorldTrajectory = {
  ts: string;
  tool: string;
  key: string;
  cls: WorldClass;
  ok: boolean;
  ms: number | null;
  obs: string;
};

/** Project flag for the world model (`.navin/world-model.json`) plus what exists on disk. */
export type WorldState = {
  enabled: boolean;
  /** One secret-free line per tool call, written after the call. */
  log: boolean;
  /** Training job when enough new calls arrived, never inside a turn. */
  train: boolean;
  /** Live advice; refused by the gateway while the gate is closed. */
  advise: boolean;
  /** Refresh .navin/BELIEFS.md after each checkpoint. */
  beliefs: boolean;
  /** Later human mode: flag repeated read-only calls. */
  skip_hint: boolean;
  confidence_threshold: number;
  train_every: number;
  min_rows: number;
  settings_file: string;
  world_dir: string;
  beliefs_file: string;
  classes: WorldClass[];
  fields: string[];
  rows: number;
  journal_bytes: number;
  recent: WorldTrajectory[];
  heldout: { version: string | null; rows: number; frozen_at?: string; classes?: Record<string, number>; error?: string };
  checkpoints: WorldCheckpoint[];
  active: { checkpoint: number; previous: number | null; heldout_version: string; activated_at: string } | null;
  score: WorldScore | null;
  scoreboard: WorldScore[];
  gate: WorldGate;
  ab: WorldAb | null;
  ab_history: WorldAb[];
  live: { advised: number; window: number; precision: number | null; logged: number; logged_precision: number | null };
  /** Confident beliefs of the active head, minus the discarded ones. */
  belief_items: WorldBelief[];
  beliefs_ignored: string[];
  beliefs_markdown: string | null;
  train_state: { rows_total?: number; last_train_at?: string; last_checkpoint?: number };
  runner_alive: boolean;
  writer_alive: boolean;
  journal: AgiJournalEntry[];
  /**
   * Whether the serving agent has the world_predict tool right now (the
   * gateway re-syncs it on every read). null when the gateway has no registry.
   */
  tool_registered?: boolean | null;
};

export type WorldFields = Partial<
  Pick<WorldState, "enabled" | "log" | "train" | "advise" | "beliefs" | "skip_hint">
>;

export type WorldAction =
  | "train"
  | "run"
  | "exam"
  | "freeze"
  | "rollback"
  | "ab"
  | "beliefs"
  | "discard_belief"
  | "restore_beliefs";

export type WorldActionResult = {
  ok: boolean;
  action: WorldAction;
  result: unknown;
  state: WorldState;
};

export async function fetchWorld(token: string, key: string, base: string = ""): Promise<WorldState> {
  return request<WorldState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/world`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateWorld(
  token: string,
  key: string,
  fields: WorldFields,
  base: string = "",
): Promise<WorldState> {
  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(fields));
  return request<WorldState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/world?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Press one world model button. The human-only ones (train, freeze, rollback,
 * discard_belief) need `actor=human` on the gateway: the panel always sends it.
 */
export async function worldAction(
  token: string,
  key: string,
  action: WorldAction,
  options: { key?: string; base?: string } = {},
): Promise<WorldActionResult> {
  const base = options.base ?? "";
  const params = new URLSearchParams();
  params.set("action", action);
  params.set("actor", "human");
  if (options.key) params.set("key", options.key);
  return request<WorldActionResult>(
    `${base}/api/sessions/${encodeURIComponent(key)}/world/action?${params}`,
    token,
    undefined,
    // Training runs in the request under its own budget: give it time.
    120_000,
  );
}

/** Per-suite verdict block of the policy exam (`navin.policy.model.Verdict`). */
export type PolicyVerdict = {
  overall: WorldVerdict;
  suites: Record<string, WorldVerdict>;
  reference_score: number;
  candidate_score: number;
  eligible: boolean;
  regressed: boolean;
};

export type PolicySuiteMetrics = { n: number; accuracy: number; log_loss: number };

export type PolicyMetrics = {
  n: number;
  accuracy: number;
  log_loss: number;
  score: number;
  suites: Record<string, PolicySuiteMetrics>;
};

/** One exam line of the policy scoreboard: reference -> adapter on a frozen held-out version. */
export type PolicyScore = {
  ts: string | null;
  checkpoint: number | null;
  heldout_version: string | null;
  n: number | null;
  accuracy: number | null;
  log_loss: number | null;
  score: number | null;
  suites: Record<string, PolicySuiteMetrics> | null;
  baseline_kind: "majority" | "uniform" | null;
  baseline_accuracy: number | null;
  baseline_score: number | null;
  verdict_vs_baseline: PolicyVerdict | null;
  verdict_vs_active: PolicyVerdict | null;
  reference: "active" | "baseline" | null;
  activated: boolean | null;
  exam: boolean;
};

export type PolicyCheckpoint = {
  number: number;
  trained_at: string | null;
  rows: number | null;
  episodes: number | null;
  battery_version: string | null;
  heldout_version: string | null;
  metrics: PolicyMetrics | null;
  baseline: ({ kind: string } & PolicyMetrics) | null;
  verdict_vs_baseline: PolicyVerdict | null;
  verdict_vs_active: PolicyVerdict | null;
  actor: string | null;
  source: string;
  actions: number;
  active: boolean;
  previous: boolean;
  forced: boolean;
};

export type PolicyGate = {
  open: boolean;
  reasons: string[];
  checkpoint: number | null;
  heldout_version: string | null;
  exam_verdict: WorldVerdict | null;
  ab_verdict: "gain" | "no_gain" | "regress" | null;
  forced: boolean;
};

export type PolicyAb = {
  ts: string;
  checkpoint: number;
  heldout_version: string;
  threshold: number;
  n: number;
  flagged: number;
  hits: number;
  precision: number | null;
  coverage: number;
  baseline_accuracy: number;
  verdict: "gain" | "no_gain" | "regress";
};

/** One step of an eval episode (`navin.policy.trajectory.Step`). */
export type PolicyStep = {
  id: string;
  ts: string;
  episode: string;
  suite: string;
  case: string;
  split: "train" | "heldout";
  step: number;
  intent: string;
  prev: string[];
  s3: WorldClass | null;
  action: string;
  akey: string;
  obs: WorldClass;
  reward: number;
  terminal: boolean;
};

/** What the world model radar (S3.3) says; the policy refuses to run without it. */
export type PolicyRadar = {
  up: boolean;
  reasons: string[];
  checkpoint: number | null;
  heldout_version: string | null;
  verdict: WorldVerdict | null;
};

export type PolicyBatterySuite = { id: string; title: string; cases: number; train: number; heldout: number };

export type PolicyPublished = {
  name: string;
  file: string;
  published_at: string;
  from: string;
  checkpoint: number;
  battery_version: string;
  heldout_version: string;
  metrics: PolicyMetrics | null;
  note: string;
};

/** Project flag for policy learning (`.navin/policy.json`) plus what exists on disk. */
export type PolicyState = {
  enabled: boolean;
  /** One line per step of an eval episode, written by the training process. */
  log: boolean;
  /** Training job every train_every turns, in a child process. */
  train: boolean;
  /** Live proposal; refused by the gateway while the gate is closed. */
  steer: boolean;
  confidence_threshold: number;
  train_every: number;
  min_rows: number;
  settings_file: string;
  policy_dir: string;
  fields: string[];
  radar: PolicyRadar;
  battery: {
    version: string | null;
    revision?: number;
    total_cases?: number;
    project_cases?: number;
    suites: PolicyBatterySuite[];
    error?: string;
  };
  rows: number;
  episodes: number;
  journal_bytes: number;
  recent: PolicyStep[];
  heldout: {
    version: string | null;
    rows: number;
    frozen_at?: string;
    battery_version?: string;
    episodes?: number;
    passed_episodes?: number;
    suites?: Record<string, number>;
    error?: string;
  };
  checkpoints: PolicyCheckpoint[];
  active: {
    checkpoint: number;
    previous: number | null;
    heldout_version: string;
    activated_at: string;
    actor?: string;
    forced?: boolean;
    rolled_back_from?: number;
  } | null;
  score: PolicyScore | null;
  scoreboard: PolicyScore[];
  gate: PolicyGate;
  ab: PolicyAb | null;
  ab_history: PolicyAb[];
  live: { suggested: number; window: number; precision: number | null; logged: number; logged_precision: number | null };
  published: PolicyPublished[];
  train_state: { rows_total?: number; last_train_at?: string; last_checkpoint?: number; battery_version?: string };
  turns_since_train: number;
  runner_alive: boolean;
  writer_alive: boolean;
  journal: AgiJournalEntry[];
  /**
   * Whether the serving agent has the policy_next tool right now (the
   * gateway re-syncs it on every read). null when the gateway has no registry.
   */
  tool_registered?: boolean | null;
};

export type PolicyFields = Partial<Pick<PolicyState, "enabled" | "log" | "train" | "steer">>;

export type PolicyAction =
  | "train"
  | "run"
  | "exam"
  | "freeze"
  | "rollback"
  | "force"
  | "ab"
  | "publish"
  | "unpublish"
  | "adopt";

export type PolicyActionResult = {
  ok: boolean;
  action: PolicyAction;
  result: unknown;
  state: PolicyState;
};

export async function fetchPolicy(token: string, key: string, base: string = ""): Promise<PolicyState> {
  return request<PolicyState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/policy`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updatePolicy(
  token: string,
  key: string,
  fields: PolicyFields,
  base: string = "",
): Promise<PolicyState> {
  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(fields));
  return request<PolicyState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/policy?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Press one policy button. The human-only ones (train, freeze, rollback,
 * force, publish, unpublish, adopt) need `actor=human` on the gateway: the
 * panel always sends it. `name` carries a published adapter name (adopt,
 * unpublish) or a note (publish); `number` picks the adapter to force.
 */
export async function policyAction(
  token: string,
  key: string,
  action: PolicyAction,
  options: { name?: string; number?: number; base?: string } = {},
): Promise<PolicyActionResult> {
  const base = options.base ?? "";
  const params = new URLSearchParams();
  params.set("action", action);
  params.set("actor", "human");
  if (options.name) params.set("name", options.name);
  if (typeof options.number === "number") params.set("number", String(options.number));
  return request<PolicyActionResult>(
    `${base}/api/sessions/${encodeURIComponent(key)}/policy/action?${params}`,
    token,
    undefined,
    // Training runs the battery in a child process under its own budget: give it time.
    200_000,
  );
}

// ---------------------------------------------------------------------------
// Transfer protocol (S5): a hidden exam plus a shutdown dossier, never a mode.
// ---------------------------------------------------------------------------

export type TransferCheck = { id: string; ok: boolean; detail: string };

export type TransferPrereqs = { ok: boolean; checks: TransferCheck[]; reasons: string[] };

export type TransferFamilyScore = {
  items: number | null;
  passed: number | null;
  pass_rate: number | null;
  bar: number | null;
  verdict: "pass" | "fail" | "collapse" | "too_few" | string | null;
};

export type TransferCampaign = {
  id: string | null;
  ts: string | null;
  suites_version: string | null;
  verdict: "pass" | "fail" | string | null;
  stopped_at: string | null;
  not_run: string[];
  replay_of: string | null;
  isolation: { skills: boolean; recall: boolean; steer: boolean; hooks: boolean; s4_untouched: boolean } | null;
  families: Record<string, TransferFamilyScore>;
  duration_ms: number | null;
};

export type TransferSafetyCheck = TransferCheck & { section: string };

export type TransferDrill = { ts: string; actor: string; ok: boolean; restart: string; checks: TransferSafetyCheck[] };

export type TransferSafety = {
  ts: string;
  actor: string;
  mode: { steer_on: boolean; policy_enabled: boolean };
  ok: boolean;
  holes: string[];
  checks: TransferSafetyCheck[];
  drill: TransferDrill | null;
};

export type TransferClaim = {
  /** The protocol's answer. Never a claim: whether one may even be discussed. */
  status: "forbidden" | "discussable";
  transfer: "not_run" | "fail" | "pass" | "void";
  safety: "not_run" | "fail" | "pass";
  drill: "not_run" | "fail" | "pass";
  reasons: string[];
};

/** One rung of the ladder: what a switch waits for, in plain words. */
export type LadderRung = {
  id: "skills" | "world" | "policy" | "transfer";
  label: string;
  state: "off" | "on" | "locked";
  waits: string;
  reasons: string[];
};

export type TransferSuites = {
  dir: string;
  default_dir: string;
  outside: boolean;
  placement_error: string | null;
  frozen: boolean;
  version: string | null;
  lock: { frozen_at: string; authors: string[]; attester: string; trainers: string[]; junior_baseline: Record<string, number> } | null;
  tampered: string | null;
  /** Item counts per family: the panel never sees an item. */
  families: Record<string, number>;
  error: string | null;
};

/** Project flag for the transfer protocol (`.navin/transfer.json`) plus what exists on disk. */
export type TransferState = {
  enabled: boolean;
  suites_dir: string | null;
  settings_file: string;
  transfer_dir: string;
  fields: string[];
  protocol: {
    version: string;
    families: { id: string; title: string; about: string }[];
    min_items_per_family: number;
    junior_bar: number;
    collapse: number;
    budget: { max_tool_calls: number; timeout_s: number; max_tokens: number };
    rules: string[];
  };
  prereqs: TransferPrereqs;
  /** The four rungs (skills, world, policy, transfer) with what each locked one waits for. */
  ladder?: LadderRung[];
  families: string[];
  suites: TransferSuites | null;
  campaign: TransferCampaign | null;
  campaigns: TransferCampaign[];
  safety: TransferSafety | null;
  claim: TransferClaim;
  journal: AgiJournalEntry[];
};

export type TransferFields = Partial<Pick<TransferState, "enabled" | "suites_dir">>;

export type TransferAction = "campaign" | "replay" | "safety" | "kill_drill" | "verify";

export type TransferActionResult = {
  ok: boolean;
  action: TransferAction;
  result: unknown;
  state: TransferState;
};

export async function fetchTransfer(token: string, key: string, base: string = ""): Promise<TransferState> {
  return request<TransferState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/transfer`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateTransfer(
  token: string,
  key: string,
  fields: TransferFields,
  base: string = "",
): Promise<TransferState> {
  const params = new URLSearchParams();
  params.set("fields", JSON.stringify(fields));
  return request<TransferState>(
    `${base}/api/sessions/${encodeURIComponent(key)}/transfer?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Press one transfer button. The human-only ones (campaign, replay,
 * kill_drill) need `actor=human` on the gateway: the panel always sends it.
 * `name` carries the campaign id to replay. `freeze` is CLI-only (it needs
 * the authors and the attester on record).
 */
export async function transferAction(
  token: string,
  key: string,
  action: TransferAction,
  options: { name?: string; base?: string } = {},
): Promise<TransferActionResult> {
  const base = options.base ?? "";
  const params = new URLSearchParams();
  params.set("action", action);
  params.set("actor", "human");
  if (options.name) params.set("name", options.name);
  return request<TransferActionResult>(
    `${base}/api/sessions/${encodeURIComponent(key)}/transfer/action?${params}`,
    token,
    undefined,
    // A campaign runs every secret item in a child process: hours, not seconds.
    4 * 3_600_000,
  );
}

export async function fetchMetagraph(
  token: string,
  key: string,
  options: { view?: "files" | "packages"; aspect?: number; base?: string } = {},
): Promise<MetagraphPayload> {
  const base = options.base ?? "";
  const params = new URLSearchParams();
  if (options.view === "packages") params.set("view", "packages");
  if (typeof options.aspect === "number" && Number.isFinite(options.aspect)) {
    params.set("aspect", String(options.aspect));
  }
  const qs = params.toString();
  return request<MetagraphPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/metagraph${qs ? `?${qs}` : ""}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchProjectSymbols(
  token: string,
  key: string,
  query: string,
  options: { path?: string; limit?: number } = {},
  base: string = "",
): Promise<ProjectSymbolsPayload> {
  const params = new URLSearchParams({ q: query });
  if (options.path) params.set("path", options.path);
  if (options.limit) params.set("limit", String(options.limit));
  return request<ProjectSymbolsPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/symbols?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Outline panel: document symbols for one file via the code index. */
export async function fetchFileOutline(
  token: string,
  key: string,
  path: string,
  options: { limit?: number } = {},
  base: string = "",
): Promise<ProjectSymbolsPayload & { path: string; source?: string }> {
  const params = new URLSearchParams({ outline: "1", path });
  if (options.limit) params.set("limit", String(options.limit));
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/symbols?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** F12 / Ctrl-click: resolve a symbol to definition locations via the code index. */
export async function fetchGotoDefinition(
  token: string,
  key: string,
  symbol: string,
  options: { path?: string; limit?: number } = {},
  base: string = "",
): Promise<{
  symbol: string;
  items: { name: string; path: string; line: number; kind?: string }[];
  total: number;
}> {
  const params = new URLSearchParams({ symbol });
  if (options.path) params.set("path", options.path);
  if (options.limit) params.set("limit", String(options.limit));
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/symbols?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export type LspHoverPayload = {
  path: string;
  line: number;
  col: number;
  contents: string;
  source: string;
};

export type LspDefinitionPayload = {
  path: string;
  line: number;
  col: number;
  items: {
    name: string;
    path: string;
    line: number;
    col?: number;
    kind?: string;
  }[];
  total: number;
  source: string;
};

export type LspCompletionItem = {
  label: string;
  kind?: string;
  detail?: string;
  insert_text?: string;
  documentation?: string;
};

export type LspCompletionPayload = {
  path: string;
  line: number;
  col: number;
  items: LspCompletionItem[];
  total: number;
  source: string;
};

export type LspSignatureParameter = {
  label: string;
  documentation?: string;
};

export type LspSignature = {
  label: string;
  documentation?: string;
  parameters: LspSignatureParameter[];
};

export type LspSignaturePayload = {
  path: string;
  line: number;
  col: number;
  source: string;
  active_signature: number;
  active_parameter: number;
  signatures: LspSignature[];
};

export type LspCodeActionEdit = {
  line: number;
  col: number;
  end_line: number;
  end_col: number;
  new_text: string;
};

export type LspCodeAction = {
  title: string;
  kind?: string;
  is_preferred?: boolean;
  edits: Record<string, LspCodeActionEdit[]>;
  command?: string;
};

export type LspCodeActionsPayload = {
  path: string;
  line: number;
  col: number;
  end_line: number;
  end_col: number;
  items: LspCodeAction[];
  total: number;
  source: string;
};

export type LspRenamePayload = {
  path: string;
  line: number;
  col: number;
  symbol: string;
  new_name: string;
  files: string[];
  total: number;
  unseen: string[];
  applied: boolean;
  written?: string[];
  message: string;
};

/** Hover at caret (LSP, index fallback). */
export async function fetchLspHover(
  token: string,
  key: string,
  args: { path: string; line: number; col: number; content?: string },
  signal?: AbortSignal,
  base: string = "",
): Promise<LspHoverPayload> {
  const params = new URLSearchParams({
    action: "hover",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
  });
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    {
      ...(signal ? { signal } : {}),
      ...(args.content !== undefined ? { headers: bodyHeaders(args.content) } : {}),
    },
    API_READ_TIMEOUT_MS,
  );
}

/** Go-to-definition at caret position (LSP, then index). */
export async function fetchLspDefinition(
  token: string,
  key: string,
  args: { path: string; line: number; col: number; limit?: number; content?: string },
  base: string = "",
): Promise<LspDefinitionPayload> {
  const params = new URLSearchParams({
    action: "definition",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
  });
  if (args.limit) params.set("limit", String(args.limit));
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    args.content !== undefined ? { headers: bodyHeaders(args.content) } : undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Find references at caret (LSP, then index). */
export async function fetchLspReferences(
  token: string,
  key: string,
  args: { path: string; line: number; col: number; limit?: number; content?: string },
  base: string = "",
): Promise<LspDefinitionPayload & { symbol?: string }> {
  const params = new URLSearchParams({
    action: "references",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
  });
  if (args.limit) params.set("limit", String(args.limit));
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    args.content !== undefined ? { headers: bodyHeaders(args.content) } : undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Completions at caret (language server). */
export async function fetchLspCompletion(
  token: string,
  key: string,
  args: {
    path: string;
    line: number;
    col: number;
    trigger?: string;
    limit?: number;
    content?: string;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<LspCompletionPayload> {
  const params = new URLSearchParams({
    action: "completion",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
  });
  if (args.trigger) params.set("trigger", args.trigger);
  if (args.limit) params.set("limit", String(args.limit));
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    {
      ...(signal ? { signal } : {}),
      ...(args.content !== undefined ? { headers: bodyHeaders(args.content) } : {}),
    },
    API_READ_TIMEOUT_MS,
  );
}

/** Parameter hints at caret (language server). */
export async function fetchLspSignatureHelp(
  token: string,
  key: string,
  args: {
    path: string;
    line: number;
    col: number;
    trigger?: string;
    content?: string;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<LspSignaturePayload> {
  const params = new URLSearchParams({
    action: "signature",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
  });
  if (args.trigger) params.set("trigger", args.trigger);
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    {
      ...(signal ? { signal } : {}),
      ...(args.content !== undefined ? { headers: bodyHeaders(args.content) } : {}),
    },
    API_READ_TIMEOUT_MS,
  );
}

/** Quick-fixes / refactors at caret or selection. */
export async function fetchLspCodeActions(
  token: string,
  key: string,
  args: {
    path: string;
    line: number;
    col: number;
    endLine?: number;
    endCol?: number;
    limit?: number;
    content?: string;
  },
  signal?: AbortSignal,
  base: string = "",
): Promise<LspCodeActionsPayload> {
  const params = new URLSearchParams({
    action: "codeaction",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
  });
  if (args.endLine) params.set("end_line", String(args.endLine));
  if (args.endCol) params.set("end_col", String(args.endCol));
  if (args.limit) params.set("limit", String(args.limit));
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    {
      ...(signal ? { signal } : {}),
      ...(args.content !== undefined ? { headers: bodyHeaders(args.content) } : {}),
    },
    API_READ_TIMEOUT_MS,
  );
}

/** Preview or apply a symbol rename via the language server. */
export async function fetchLspRename(
  token: string,
  key: string,
  args: {
    path: string;
    line: number;
    col: number;
    newName: string;
    apply?: boolean;
    content?: string;
  },
  base: string = "",
): Promise<LspRenamePayload> {
  const params = new URLSearchParams({
    action: "rename",
    path: args.path,
    line: String(args.line),
    col: String(args.col),
    new_name: args.newName,
  });
  if (args.apply) params.set("apply", "true");
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/lsp?${params}`,
    token,
    args.content !== undefined ? { headers: bodyHeaders(args.content) } : undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export type LspServerEntry = {
  name: string;
  displayName: string;
  marketplace: string;
  suffixes: string[];
  languages: string[];
  notes: string;
  runtime: string;
  installed: boolean;
  version: string;
  ready: boolean;
  detail: string;
};

export type LspServersPayload = {
  extensions: LspServerEntry[];
  others: LspServerEntry[];
  nodeAvailable: boolean;
  target: string;
};

export type LspServerInstallOptions = {
  /** Open VSX id, for an extension outside the curated list. */
  extension?: string;
  entrypoint?: string;
  /** Comma-separated file suffixes, dot included. */
  suffixes?: string;
  languageId?: string;
  version?: string;
  native?: boolean;
  platformSpecific?: boolean;
};

function lspServerParams(
  name: string,
  key: string | null,
  options: LspServerInstallOptions = {},
): URLSearchParams {
  const params = new URLSearchParams({ name });
  if (key) params.set("key", key);
  for (const [field, value] of Object.entries(options)) {
    if (value === undefined || value === "" || value === false) continue;
    params.set(field, value === true ? "true" : String(value));
  }
  return params;
}

/** Language servers installable from VS Code extensions, and their state. */
export async function fetchLspServers(
  token: string,
  key: string | null,
  base: string = "",
): Promise<LspServersPayload> {
  const params = new URLSearchParams();
  if (key) params.set("key", key);
  return request<LspServersPayload>(
    `${base}/api/webui/lsp-servers?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Download and register one language server. */
export async function installLspServer(
  token: string,
  name: string,
  key: string | null,
  options: LspServerInstallOptions = {},
  base: string = "",
): Promise<LspServersPayload> {
  return request<LspServersPayload>(
    `${base}/api/webui/lsp-servers/install?${lspServerParams(name, key, options)}`,
    token,
    undefined,
    // Extensions run to tens of megabytes and are unpacked before answering.
    API_SLOW_TIMEOUT_MS,
  );
}

export type MarketplaceExtension = {
  /** Open VSX id, as ``publisher/name``. */
  slug: string;
  displayName: string;
  description: string;
  version: string;
  downloads: number;
  /** Absolute Open VSX url, empty when the extension publishes no icon. */
  icon: string;
  /** The server name it is installed under, empty when it is not installed. */
  installedAs: string;
  installed: boolean;
  catalogued: boolean;
};

export type MarketplaceSearchPayload = {
  results: MarketplaceExtension[];
  query: string;
};

/** Search the Open VSX registry. */
export async function searchLspMarketplace(
  token: string,
  query: string,
  key: string | null,
  base: string = "",
): Promise<MarketplaceSearchPayload> {
  const params = new URLSearchParams({ q: query });
  if (key) params.set("key", key);
  return request<MarketplaceSearchPayload>(
    `${base}/api/webui/lsp-servers/search?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * Install any extension, letting the server work out what is inside it.
 *
 * Slower than a catalogue install: after downloading, each candidate server is
 * started until one answers an LSP handshake.
 */
export async function installDetectedLspServer(
  token: string,
  extension: string,
  key: string | null,
  base: string = "",
): Promise<LspServersPayload> {
  const params = new URLSearchParams({ extension });
  if (key) params.set("key", key);
  return request<LspServersPayload>(
    `${base}/api/webui/lsp-servers/install-detected?${params}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

/** Remove a language server installed from an extension. */
export async function uninstallLspServer(
  token: string,
  name: string,
  key: string | null,
  base: string = "",
): Promise<LspServersPayload> {
  return request<LspServersPayload>(
    `${base}/api/webui/lsp-servers/uninstall?${lspServerParams(name, key)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Fuzzy file and folder lookup for the composer's @-mention palette. */
export async function fetchProjectFiles(
  token: string,
  key: string,
  query: string,
  options: { limit?: number } = {},
  base: string = "",
): Promise<ProjectFileSearchPayload> {
  const params = new URLSearchParams({ q: query });
  if (options.limit) params.set("limit", String(options.limit));
  return request<ProjectFileSearchPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-search?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export interface SearchFilters {
  regex?: boolean;
  caseSensitive?: boolean;
  include?: string;
  exclude?: string;
  /** Embedding / codebase semantic search (requires tools.semanticSearch). */
  semantic?: boolean;
}

/** Build query string for project search / replace (exported for unit tests). */
export function searchParams(
  query: string,
  options?: SearchFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  params.set("q", query);
  if (options?.semantic) params.set("semantic", "1");
  if (options?.regex) params.set("regex", "1");
  if (options?.caseSensitive) params.set("case", "1");
  if (options?.include?.trim()) params.set("include", options.include.trim());
  if (options?.exclude?.trim()) params.set("exclude", options.exclude.trim());
  return params;
}

export async function searchProject(
  token: string,
  key: string,
  query: string,
  options?: SearchFilters,
  base: string = "",
): Promise<ProjectSearchPayload> {
  return request<ProjectSearchPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/search?${searchParams(query, options)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Rewrite every match, or only those in `paths`. Writes to disk. */
export async function replaceInProject(
  token: string,
  key: string,
  query: string,
  replacement: string,
  options?: SearchFilters & { paths?: string[] },
  base: string = "",
): Promise<ProjectReplacePayload> {
  const params = searchParams(query, options);
  params.set("replace", replacement);
  for (const path of options?.paths ?? []) params.append("path", path);
  return request<ProjectReplacePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/replace?${params}`,
    token,
    undefined,
    // A replace-all rescans the project and rewrites every hit, so it is closer
    // to an indexing pass than to a read.
    API_SLOW_TIMEOUT_MS,
  );
}

/** Multitask: run a queued composer prompt in a parallel subagent now,
 * instead of waiting for the current turn to finish. */
/** Longer than the bus ack (20s) so a 504 arrives instead of a client abort. */
const API_MULTITASK_TIMEOUT_MS = 25_000;

export async function spawnMultitaskPrompt(
  token: string,
  key: string,
  prompt: string,
  label: string = "",
  base: string = "",
  clientKey: string = "",
): Promise<{ ok: boolean; detail?: string }> {
  return request<{ ok: boolean; detail?: string }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/multitask-spawn`,
    token,
    // The gateway's HTTP layer only accepts GET; the prompt travels in
    // chunked base64 headers like file saves do.
    { headers: bodyHeaders(JSON.stringify({ prompt, label, client_key: clientKey })) },
    API_MULTITASK_TIMEOUT_MS,
  );
}

export type WorkspaceCheckpoint = {
  id: string;
  ts: number;
  reason: string;
  label: string;
};

export type CheckpointDiffPayload = {
  id: string;
  files: { status: string; path: string }[];
  patch: string;
  truncated: boolean;
};

export async function fetchCheckpoints(
  token: string,
  key: string,
  base: string = "",
): Promise<{ checkpoints: WorkspaceCheckpoint[] }> {
  return request<{ checkpoints: WorkspaceCheckpoint[] }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/checkpoints`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function createCheckpoint(
  token: string,
  key: string,
  label: string = "",
  base: string = "",
): Promise<{ created: boolean; id: string }> {
  const query = new URLSearchParams();
  if (label) query.set("label", label);
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ created: boolean; id: string }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/checkpoints/create${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCheckpointDiff(
  token: string,
  key: string,
  id: string,
  base: string = "",
): Promise<CheckpointDiffPayload> {
  const query = new URLSearchParams();
  query.set("id", id);
  return request<CheckpointDiffPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/checkpoints/diff?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function restoreCheckpoint(
  token: string,
  key: string,
  id: string,
  base: string = "",
): Promise<{ restored: string; safety: string; deletedFiles: number }> {
  const query = new URLSearchParams();
  query.set("id", id);
  return request<{ restored: string; safety: string; deletedFiles: number }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/checkpoints/restore?${query}`,
    token,
    undefined,
    // Restores rewrite the whole tree; give large projects extra headroom.
    60_000,
  );
}

export async function fetchGitChanges(
  token: string,
  key: string,
  base: string = "",
): Promise<GitChangesPayload> {
  return request<GitChangesPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-changes`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Multi-branch commit history for the source-control timeline graph. */
export async function fetchGitLog(
  token: string,
  key: string,
  limit: number = 200,
  base: string = "",
): Promise<GitLogPayload> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  return request<GitLogPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-log?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Full message and per-file stats for one commit of the timeline. */
export async function fetchGitCommitDetail(
  token: string,
  key: string,
  hash: string,
  base: string = "",
): Promise<GitCommitDetailPayload> {
  const params = new URLSearchParams();
  params.set("commit", hash);
  return request<GitCommitDetailPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-log?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** AI commit message from the pending working-tree diff (source-control IA button). */
export async function generateGitCommitMessage(
  token: string,
  key: string,
  paths: string[] = [],
  base: string = "",
  lang: string = "en",
): Promise<{ message: string; model?: string; files?: string[] }> {
  const query = new URLSearchParams();
  if (lang) query.set("lang", lang);
  for (const path of paths) {
    if (path) query.append("path", path);
  }
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ message: string; model?: string; files?: string[] }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-commit-message${suffix}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

/** One-click Commit / Commit & Push from the source-control panel. */
export async function commitGitChanges(
  token: string,
  key: string,
  message: string,
  push: boolean,
  commit: boolean = true,
  base: string = "",
  options: { paths?: string[]; stagedOnly?: boolean; amend?: boolean } = {},
): Promise<GitCommitPayload> {
  const query = new URLSearchParams();
  query.set("message", message);
  if (push) query.set("push", "1");
  if (!commit) query.set("commit", "0");
  if (options.stagedOnly) query.set("staged_only", "1");
  if (options.amend) query.set("amend", "1");
  for (const path of options.paths ?? []) {
    if (path) query.append("path", path);
  }
  return request<GitCommitPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-commit?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function stageGitPaths(
  token: string,
  key: string,
  paths: string[],
  stage: boolean = true,
  base: string = "",
  options: { all?: boolean } = {},
): Promise<GitChangesPayload> {
  const query = new URLSearchParams();
  if (!stage) query.set("stage", "0");
  if (options.all) query.set("all", "1");
  for (const path of paths) {
    if (path) query.append("path", path);
  }
  return request<GitChangesPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-stage?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function pullGitChanges(
  token: string,
  key: string,
  base: string = "",
  options: { rebase?: boolean } = {},
): Promise<
  GitCommitPayload & {
    pulled?: boolean;
    merge_in_progress?: boolean;
    rebase_in_progress?: boolean;
  }
> {
  const query = options.rebase ? "?rebase=1" : "";
  return request<
    GitCommitPayload & {
      pulled?: boolean;
      merge_in_progress?: boolean;
      rebase_in_progress?: boolean;
    }
  >(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-pull${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function discardGitPaths(
  token: string,
  key: string,
  paths?: string[],
  base: string = "",
): Promise<GitChangesPayload> {
  const query = new URLSearchParams();
  for (const path of paths ?? []) {
    if (path) query.append("path", path);
  }
  const suffix = query.toString() ? `?${query}` : "";
  return request<GitChangesPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-discard${suffix}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function stashGitChanges(
  token: string,
  key: string,
  base: string = "",
  op: "push" | "pop" | "list" | "apply" | "drop" = "push",
  index?: number,
): Promise<
  GitChangesPayload & {
    stashed?: boolean;
    popped?: boolean;
    applied?: boolean;
    dropped?: boolean;
  }
> {
  const query = new URLSearchParams();
  if (op !== "push") query.set("op", op);
  if (index !== undefined) query.set("index", String(index));
  const suffix = query.toString() ? `?${query}` : "";
  return request<
    GitChangesPayload & {
      stashed?: boolean;
      popped?: boolean;
      applied?: boolean;
      dropped?: boolean;
    }
  >(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-stash${suffix}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function undoLastGitCommit(
  token: string,
  key: string,
  base: string = "",
): Promise<GitChangesPayload & { undone?: boolean }> {
  return request<GitChangesPayload & { undone?: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-undo-commit`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function syncGitChanges(
  token: string,
  key: string,
  base: string = "",
): Promise<GitCommitPayload & { synced?: boolean; pulled?: boolean }> {
  return request<GitCommitPayload & { synced?: boolean; pulled?: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-sync`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchGitRemote(
  token: string,
  key: string,
  base: string = "",
): Promise<GitChangesPayload & { fetched?: boolean }> {
  return request<GitChangesPayload & { fetched?: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-fetch`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function gitBranchOp(
  token: string,
  key: string,
  op: "list" | "create" | "checkout",
  name?: string,
  base: string = "",
): Promise<GitBranchPayload> {
  const query = new URLSearchParams();
  query.set("op", op);
  if (name) query.set("name", name);
  return request<GitBranchPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-branch?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function gitConflictAction(
  token: string,
  key: string,
  action: "abort" | "continue",
  base: string = "",
): Promise<GitChangesPayload & { ok?: boolean; action?: string }> {
  const query = new URLSearchParams();
  query.set("action", action);
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-conflict?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchGithubPr(
  token: string,
  key: string,
  base: string = "",
): Promise<GithubPrViewPayload> {
  return request<GithubPrViewPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/pr`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function createGithubPr(
  token: string,
  key: string,
  options: { title?: string; body?: string; draft?: boolean } = {},
  base: string = "",
): Promise<GithubPrCreatePayload> {
  const query = new URLSearchParams();
  if (options.title) query.set("title", options.title);
  if (options.body) query.set("body", options.body);
  if (options.draft === false) query.set("draft", "0");
  return request<GithubPrCreatePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/pr/create?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function fetchGithubChecks(
  token: string,
  key: string,
  base: string = "",
): Promise<GithubCiStatusPayload> {
  return request<GithubCiStatusPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/checks`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFixCiPrompt(
  token: string,
  key: string,
  base: string = "",
  mode: "fix" | "explain" = "fix",
): Promise<GithubFixCiPayload> {
  const query = mode === "explain" ? "?mode=explain" : "";
  return request<GithubFixCiPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/fix-ci${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export type ReviewPromptPayload = {
  available: boolean;
  branch: string;
  files: { path: string; status: string }[];
  prompt: string;
  detail: string;
};

/** Structured seed for the pre-commit "Review changes" agent action. */
export async function fetchReviewPrompt(
  token: string,
  key: string,
  base: string = "",
): Promise<ReviewPromptPayload> {
  return request<ReviewPromptPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/review-prompt`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

/** Merged-PR suggestions for board tasks (does not auto-close). */
export async function fetchGithubPrSync(
  token: string,
  key: string,
  base: string = "",
): Promise<GithubPrSyncPayload> {
  return request<GithubPrSyncPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/github/pr-sync`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

/** DAP workbench ops (start/stop/step/breakpoints/stack/variables). */
export async function debugOp(
  token: string,
  key: string,
  op: string,
  params: Record<string, unknown> = {},
  base: string = "",
): Promise<DebugStatePayload> {
  const query = new URLSearchParams();
  query.set("op", op);
  const bodyKeys = new Set(["lines", "args", "expression", "expr"]);
  const body: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (bodyKeys.has(name) || typeof value === "object") {
      body[name] = value;
      continue;
    }
    query.set(name, String(value));
  }
  const init =
    Object.keys(body).length > 0
      ? { headers: bodyHeaders(JSON.stringify(body)) }
      : undefined;
  return request<DebugStatePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/debug?${query}`,
    token,
    init,
    API_SLOW_TIMEOUT_MS,
  );
}

/** Estimated context-window fill for the chat, shown in the Dev footer. */
export async function fetchContextUsage(
  token: string,
  key: string,
  base: string = "",
): Promise<ContextUsagePayload> {
  return request<ContextUsagePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/context-usage`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Diffs `file` against HEAD, or against `options.against` when given. */
export async function fetchGitDiff(
  token: string,
  key: string,
  file: string,
  options?: { against?: string | null; ignoreWhitespace?: boolean },
  base: string = "",
): Promise<GitDiffPayload> {
  const params = new URLSearchParams();
  params.set("file", file);
  if (options?.against) params.set("against", options.against);
  if (options?.ignoreWhitespace) params.set("ignore_whitespace", "1");
  return request<GitDiffPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-diff?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Per-line git blame for the editor's current-line attribution strip. */
export async function fetchGitBlame(
  token: string,
  key: string,
  file: string,
  base: string = "",
  root?: string | null,
): Promise<GitBlamePayload> {
  const params = new URLSearchParams({ file });
  appendFileRoot(params, root);
  return request<GitBlamePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-blame?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchReviewChanges(
  token: string,
  key: string,
  base: string = "",
): Promise<ReviewChangesPayload> {
  return request<ReviewChangesPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/review-changes`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchReviewFile(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<ReviewFilePayload> {
  const params = new URLSearchParams();
  params.set("path", path);
  return request<ReviewFilePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/review-file?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function postReviewAction(
  token: string,
  key: string,
  action: "accept" | "reject",
  path?: string | null,
  base: string = "",
  /** Hunk id from ReviewFilePayload.hunks; narrows the action to that hunk. */
  hunk?: string | null,
): Promise<ReviewActionPayload> {
  const params = new URLSearchParams();
  params.set("action", action);
  if (path) params.set("path", path);
  if (hunk) params.set("hunk", hunk);
  return request<ReviewActionPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/review-action?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

// -- navin.live account (Cursor-style sign-in) -------------------------------

export interface AccountUsagePayload {
  budget_micro_usd: number;
  spent_micro_usd: number;
  used_percent: number;
  remaining_equivalent_tokens: number;
  mode: string;
  period_end?: number | null;
}

export interface AccountPayload {
  connected: boolean;
  plan: string;
  plan_label: string;
  /** Prix mensuel public de l'offre (affichage seul ; null si inconnu). */
  plan_price_usd?: number | null;
  email: string;
  name: string;
  server_url: string;
  managed_key_active: boolean;
  status?: string;
  error?: string;
  org_id?: string | null;
  org_role?: string | null;
  seat_count?: number | null;
  /** Unix seconds - subscription period end (renewal). */
  period_end?: number | null;
  /** Managed budget from navin.live (same source as the dashboard). */
  usage?: AccountUsagePayload | null;
}

export async function fetchAccount(
  token: string,
  options?: { refresh?: boolean },
  base: string = "",
): Promise<AccountPayload> {
  const suffix = options?.refresh ? "?refresh=1" : "";
  return request<AccountPayload>(
    `${base}/api/webui/account${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** URL of the navin.live connect page for this gateway (opens in a browser). */
export async function fetchAccountConnectUrl(
  token: string,
  base: string = "",
  options?: { open?: boolean; locale?: string },
): Promise<{ url: string; opened?: boolean }> {
  const params = new URLSearchParams();
  if (options?.open) params.set("open", "1");
  if (options?.locale) params.set("locale", options.locale);
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  return request<{ url: string; opened?: boolean }>(
    `${base}/api/webui/account/connect-url${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/**
 * URL of the OpenRouter OAuth PKCE page for this gateway (opens in a browser).
 * The key minted at the end belongs to the user's OpenRouter account.
 */
export async function fetchOpenRouterConnectUrl(
  token: string,
  base: string = "",
  options?: { open?: boolean; locale?: string },
): Promise<{ url: string; opened?: boolean }> {
  const params = new URLSearchParams();
  if (options?.open) params.set("open", "1");
  if (options?.locale) params.set("locale", options.locale);
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  return request<{ url: string; opened?: boolean }>(
    `${base}/api/webui/openrouter/connect-url${suffix}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchOpenRouterStatus(
  token: string,
  base: string = "",
): Promise<{ connected: boolean; free_models?: string[] }> {
  return request<{ connected: boolean; free_models?: string[] }>(
    `${base}/api/webui/openrouter/status`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function activateAccountLicense(
  token: string,
  licenseKey: string,
  base: string = "",
): Promise<AccountPayload> {
  const query = new URLSearchParams({ licenseKey });
  return request<AccountPayload>(
    `${base}/api/webui/account/activate?${query}`,
    token,
    undefined,
    API_SLOW_TIMEOUT_MS,
  );
}

export async function logoutAccount(
  token: string,
  base: string = "",
): Promise<AccountPayload> {
  return request<AccountPayload>(
    `${base}/api/webui/account/logout`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

/** Desktop WebView: the opener plugin, when the shell granted it to localhost.

Tried on every OS. Linux AppImage used to skip it (the plugin was a no-op
there) and go straight to the gateway ``xdg-open``; trying the plugin first
is harmless and covers the hosts where it now works.
*/
export async function openViaDesktopShell(url: string): Promise<boolean> {
  const openUrl = tauriOpenUrl();
  if (openUrl) {
    try {
      await openUrl(url);
      return true;
    } catch {
      // Scope or opener failure: try the IPC command, then the gateway.
    }
  }
  const invoke = tauriInvoke();
  if (!invoke) return false;
  try {
    await invoke("plugin:opener|open_url", { url });
    return true;
  } catch {
    return false;
  }
}

/** Open a URL in the OS browser (desktop WebView blocks ``window.open`` / ``target=_blank``). */
export async function openExternalUrl(
  token: string,
  url: string,
  base: string = "",
): Promise<{ opened: boolean; url: string }> {
  // tauri.localhost only exists inside the webview. Handing it to xdg-open
  // is what opened a blank Chrome tab when Code spawned a window.
  if (isInternalDesktopUrl(url) || isNavinShellUrl(url)) return { opened: false, url };
  if (await openViaDesktopShell(url)) return { opened: true, url };
  const query = new URLSearchParams({ url });
  try {
    return await request<{ opened: boolean; url: string }>(
      `${base}/api/webui/open-url?${query}`,
      token,
      undefined,
      API_READ_TIMEOUT_MS,
    );
  } catch {
    return { opened: false, url };
  }
}

/** Open an official http(s) URL in the OS browser. Never ``window.open`` in Tauri. */
export async function openInOsBrowser(
  token: string,
  url: string,
  base: string = "",
): Promise<{ opened: boolean; url: string }> {
  const result = await openExternalUrl(token, url, base);
  if (result.opened) return result;
  if (shouldUseWindowOpenFallback(isDesktopShell()) && typeof window !== "undefined") {
    const popup = window.open(url, "_blank", "noopener,noreferrer");
    return { opened: Boolean(popup), url };
  }
  return { opened: false, url };
}

export async function fetchExecPolicy(
  token: string,
  base: string = "",
): Promise<ExecPolicyPayload> {
  return request<ExecPolicyPayload>(
    `${base}/api/webui/exec-policy`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateExecPolicy(
  token: string,
  state: {
    allow_patterns?: string[];
    deny_patterns?: string[];
    restrict_to_workspace?: boolean;
    exec_enabled?: boolean;
    builtin_deny_enabled?: boolean;
    approval_mode?: ExecApprovalMode;
  },
  base: string = "",
): Promise<ExecPolicyPayload> {
  const query = new URLSearchParams();
  query.set("state", JSON.stringify(state));
  return request<ExecPolicyPayload>(
    `${base}/api/webui/exec-policy/update?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSettings(
  token: string,
  update: SettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  if (update.modelPreset !== undefined) {
    query.set("model_preset", update.modelPreset ?? "default");
  }
  if (update.model !== undefined) query.set("model", update.model);
  if (update.provider !== undefined) query.set("provider", update.provider);
  if (update.contextWindowTokens !== undefined) {
    query.set("context_window_tokens", String(update.contextWindowTokens));
  }
  if (update.timezone !== undefined) query.set("timezone", update.timezone);
  if (update.botName !== undefined) query.set("bot_name", update.botName);
  if (update.botIcon !== undefined) query.set("bot_icon", update.botIcon);
  if (update.reasoningEffort !== undefined) {
    query.set("reasoning_effort", update.reasoningEffort);
  }
  if (update.toolHintMaxLength !== undefined) {
    query.set("tool_hint_max_length", String(update.toolHintMaxLength));
  }
  if (update.browserHeadless !== undefined) {
    query.set("browser_headless", String(update.browserHeadless));
  }
  if (update.browserLiveView !== undefined) {
    query.set("browser_live_view", String(update.browserLiveView));
  }
  if (update.computerEnabled !== undefined) {
    query.set("computer_enabled", String(update.computerEnabled));
  }
  if (update.computerAsk !== undefined) query.set("computer_ask", update.computerAsk);
  if (update.computerSessionMode !== undefined) {
    query.set("computer_session_mode", update.computerSessionMode);
  }
  if (update.computerLiveView !== undefined) {
    query.set("computer_live_view", String(update.computerLiveView));
  }
  if (update.computerAuditLog !== undefined) {
    query.set("computer_audit_log", String(update.computerAuditLog));
  }
  if (update.computerAnthropicNative !== undefined) {
    query.set("computer_anthropic_native", String(update.computerAnthropicNative));
  }
  for (const [key, parameter] of [
    ["computerBackend", "computer_backend"],
    ["computerDisplay", "computer_display"],
    ["computerAuditScreenshots", "computer_audit_screenshots"],
    ["computerSettleMs", "computer_settle_ms"],
    ["computerTypeDelayMs", "computer_type_delay_ms"],
    ["computerMaxActionsPerTurn", "computer_max_actions_per_turn"],
    ["computerScreenshotMaxWidth", "computer_screenshot_max_width"],
    ["computerScreenshotMaxHeight", "computer_screenshot_max_height"],
    ["computerUserTakeoverPx", "computer_user_takeover_px"],
    ["computerFailsafeCorner", "computer_failsafe_corner"],
    ["computerProtectedApps", "computer_protected_apps"],
    ["computerAllowedApps", "computer_allowed_apps"],
    ["computerBlockedApps", "computer_blocked_apps"],
    ["computerAskApps", "computer_ask_apps"],
  ] as const) {
    const value = update[key];
    if (value !== undefined) query.set(parameter, Array.isArray(value) ? JSON.stringify(value) : String(value));
  }
  if (update.boardAutoBranch !== undefined) {
    query.set("board_auto_branch", String(update.boardAutoBranch));
  }
  if (update.boardOpenPr !== undefined) {
    query.set("board_open_pr", String(update.boardOpenPr));
  }
  if (update.forgeHost !== undefined) {
    query.set("forge_host", update.forgeHost);
    if (update.forgeToken !== undefined) query.set("forge_token", update.forgeToken);
    if (update.forgeKind !== undefined) query.set("forge_kind", update.forgeKind);
    if (update.forgeRemove !== undefined) {
      query.set("forge_remove", String(update.forgeRemove));
    }
  }
  return request<SettingsPayload>(
    `${base}/api/settings/update?${query}`,
    token,
  );
}

export async function createModelConfiguration(
  token: string,
  configuration: ModelConfigurationCreate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  if (configuration.name !== undefined) query.set("name", configuration.name);
  query.set("label", configuration.label);
  query.set("provider", configuration.provider);
  query.set("model", configuration.model);
  return request<SettingsPayload>(
    `${base}/api/settings/model-configurations/create?${query}`,
    token,
  );
}

/** Largest `models` JSON payload per request: the HTTP request line caps at 8 KB. */
const MODEL_IMPORT_QUERY_BUDGET = 4000;

/** Split a selection into request-sized batches so long lists stay under the URL limit. */
export function chunkModelImportEntries(
  entries: ModelConfigurationImportEntry[],
): ModelConfigurationImportEntry[][] {
  const batches: ModelConfigurationImportEntry[][] = [];
  let current: ModelConfigurationImportEntry[] = [];
  for (const entry of entries) {
    const candidate = [...current, entry];
    if (
      current.length > 0 &&
      encodeURIComponent(JSON.stringify(candidate)).length >
        MODEL_IMPORT_QUERY_BUDGET
    ) {
      batches.push(current);
      current = [entry];
      continue;
    }
    current = candidate;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

export async function importModelConfigurations(
  token: string,
  provider: string,
  models: ModelConfigurationImportEntry[],
  base: string = "",
): Promise<
  SettingsPayload & { model_import?: ModelConfigurationImportResult }
> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  query.set("models", JSON.stringify(models));
  return request<
    SettingsPayload & { model_import?: ModelConfigurationImportResult }
  >(`${base}/api/settings/model-configurations/import?${query}`, token);
}

export async function updateModelConfiguration(
  token: string,
  configuration: ModelConfigurationUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("name", configuration.name);
  if (configuration.label !== undefined)
    query.set("label", configuration.label);
  if (configuration.provider !== undefined)
    query.set("provider", configuration.provider);
  if (configuration.model !== undefined)
    query.set("model", configuration.model);
  if (configuration.contextWindowTokens !== undefined) {
    query.set(
      "context_window_tokens",
      String(configuration.contextWindowTokens),
    );
  }
  if (configuration.reasoningEffort !== undefined) {
    query.set("reasoning_effort", configuration.reasoningEffort);
  }
  if (configuration.enabled !== undefined) {
    query.set("enabled", configuration.enabled ? "true" : "false");
  }
  return request<SettingsPayload>(
    `${base}/api/settings/model-configurations/update?${query}`,
    token,
  );
}

export async function deleteModelConfiguration(
  token: string,
  name: string,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<SettingsPayload>(
    `${base}/api/settings/model-configurations/delete?${query}`,
    token,
  );
}

export function fetchComputerModels(token: string, provider: string): Promise<ComputerModelsPayload> {
  const query = new URLSearchParams({ provider });
  return request<ComputerModelsPayload>(`/api/settings/computer/models?${query}`, token, undefined, 90_000);
}

export function updateComputerModel(token: string, provider: string, model: string): Promise<SettingsPayload> {
  const query = new URLSearchParams({ provider, model });
  return request<SettingsPayload>(`/api/settings/computer/model?${query}`, token, undefined, 90_000);
}

export function fetchComputerDiagnostics(token: string, passive = false): Promise<ComputerDiagnostics> {
  return request<ComputerDiagnostics>(`/api/settings/computer/doctor${passive ? "?passive=true" : ""}`, token, undefined, 90_000);
}

export function requestComputerPermission(
  token: string, kind: "all" | "screen_recording" | "accessibility" | "automation",
): Promise<ComputerDiagnostics> {
  return request<ComputerDiagnostics>(`/api/settings/computer/permissions?kind=${kind}`, token, undefined, 90_000);
}

export function setComputerStopped(token: string, stopped: boolean): Promise<{ stopped: string | null }> {
  return request<{ stopped: string | null }>(`/api/settings/computer/${stopped ? "stop" : "go"}`, token);
}

export async function updateModelRoute(
  token: string,
  role: string,
  preset: string,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("role", role);
  query.set("preset", preset);
  return request<SettingsPayload>(
    `${base}/api/settings/model-routes/update?${query}`,
    token,
  );
}

export async function updateProviderSettings(
  token: string,
  update: ProviderSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", update.provider);
  if (update.apiKey !== undefined) query.set("api_key", update.apiKey);
  if (update.apiBase !== undefined) query.set("api_base", update.apiBase);
  if (update.apiType !== undefined) query.set("api_type", update.apiType);
  if (update.authMode !== undefined) query.set("auth_mode", update.authMode);
  if (update.endpointRegion !== undefined) query.set("endpoint_region", update.endpointRegion);
  if (update.accessPlan !== undefined) query.set("access_plan", update.accessPlan);
  if (update.wireProtocol !== undefined) query.set("wire_protocol", update.wireProtocol);
  return request<SettingsPayload>(
    `${base}/api/settings/provider/update?${query}`,
    token,
  );
}

export async function testProviderConnection(
  token: string,
  update: ProviderSettingsUpdate,
  base: string = "",
): Promise<ProviderConnectionTestPayload> {
  const query = new URLSearchParams();
  query.set("provider", update.provider);
  if (update.apiKey !== undefined) query.set("api_key", update.apiKey);
  if (update.apiBase !== undefined) query.set("api_base", update.apiBase);
  if (update.endpointRegion !== undefined) query.set("endpoint_region", update.endpointRegion);
  if (update.accessPlan !== undefined) query.set("access_plan", update.accessPlan);
  if (update.wireProtocol !== undefined) query.set("wire_protocol", update.wireProtocol);
  return request<ProviderConnectionTestPayload>(
    `${base}/api/settings/provider/test?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function loginProviderOAuth(
  token: string,
  provider: string,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  return request<SettingsPayload>(
    `${base}/api/settings/provider/oauth-login?${query}`,
    token,
  );
}

/** Poll a sign-in, optionally handing over a manually pasted callback URL. */
export async function pollProviderOAuth(
  token: string,
  provider: string,
  code?: string,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  if (code) query.set("code", code);
  return request<SettingsPayload>(
    `${base}/api/settings/provider/oauth-status?${query}`,
    token,
  );
}

export async function logoutProviderOAuth(
  token: string,
  provider: string,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  return request<SettingsPayload>(
    `${base}/api/settings/provider/oauth-logout?${query}`,
    token,
  );
}

export async function updateWebSearchSettings(
  token: string,
  update: WebSearchSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", update.provider);
  if (update.apiKey !== undefined) query.set("api_key", update.apiKey);
  if (update.baseUrl !== undefined) query.set("base_url", update.baseUrl);
  if (update.maxResults !== undefined)
    query.set("max_results", String(update.maxResults));
  if (update.timeout !== undefined)
    query.set("timeout", String(update.timeout));
  if (update.useJinaReader !== undefined) {
    query.set("use_jina_reader", String(update.useJinaReader));
  }
  return request<SettingsPayload>(
    `${base}/api/settings/web-search/update?${query}`,
    token,
  );
}

export async function updateNetworkSafetySettings(
  token: string,
  update: NetworkSafetySettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set(
    "webui_allow_local_service_access",
    String(update.webuiAllowLocalServiceAccess),
  );
  query.set("webui_default_access_mode", update.webuiDefaultAccessMode);
  return request<SettingsPayload>(
    `${base}/api/settings/network-safety/update?${query}`,
    token,
  );
}

export async function updateImageGenerationSettings(
  token: string,
  update: ImageGenerationSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("enabled", String(update.enabled));
  query.set("provider", update.provider);
  query.set("model", update.model);
  query.set("default_aspect_ratio", update.defaultAspectRatio);
  query.set("default_image_size", update.defaultImageSize);
  query.set("max_images_per_turn", String(update.maxImagesPerTurn));
  return request<SettingsPayload>(
    `${base}/api/settings/image-generation/update?${query}`,
    token,
  );
}

export async function updateVideoGenerationSettings(
  token: string,
  update: VideoGenerationSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("enabled", String(update.enabled));
  query.set("provider", update.provider);
  query.set("model", update.model);
  query.set("default_aspect_ratio", update.defaultAspectRatio);
  query.set("default_duration_seconds", String(update.defaultDurationSeconds));
  query.set("default_resolution", update.defaultResolution);
  return request<SettingsPayload>(
    `${base}/api/settings/video-generation/update?${query}`,
    token,
  );
}

export async function updateMusicGenerationSettings(
  token: string,
  update: MusicGenerationSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("enabled", String(update.enabled));
  query.set("provider", update.provider);
  query.set("model", update.model);
  return request<SettingsPayload>(
    `${base}/api/settings/music-generation/update?${query}`,
    token,
  );
}

export async function updateTranscriptionSettings(
  token: string,
  update: TranscriptionSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("enabled", String(update.enabled));
  query.set("provider", update.provider);
  query.set("model", update.model);
  query.set("language", update.language);
  query.set("max_duration_sec", String(update.maxDurationSec));
  query.set("max_upload_mb", String(update.maxUploadMb));
  return request<SettingsPayload>(
    `${base}/api/settings/transcription/update?${query}`,
    token,
  );
}

export async function updateVoiceSettings(
  token: string,
  update: VoiceSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("tts_provider", update.ttsProvider);
  query.set("tts_model", update.ttsModel);
  query.set("voice", update.voice);
  query.set("auto_speak", String(update.autoSpeak));
  query.set("response_format", update.responseFormat);
  query.set(
    "realtime_enabled",
    update.realtimeEnabled === null ? "auto" : String(update.realtimeEnabled),
  );
  return request<SettingsPayload>(
    `${base}/api/settings/voice/update?${query}`,
    token,
  );
}

export async function previewVoice(
  token: string,
  value: { provider: string; model: string; voice: string; text: string },
  base = "",
): Promise<{ audio_base64: string; mime: string; provider: string; model: string; voice: string }> {
  const query = new URLSearchParams(value);
  return request(`${base}/api/settings/voice/preview?${query}`, token, undefined, 75_000);
}

export async function updateLiveVoiceSettings(
  token: string,
  transcription: TranscriptionSettingsUpdate,
  voice: VoiceSettingsUpdate,
  base = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams({
    enabled: String(transcription.enabled),
    provider: transcription.provider,
    model: transcription.model,
    language: transcription.language,
    max_duration_sec: String(transcription.maxDurationSec),
    max_upload_mb: String(transcription.maxUploadMb),
    tts_provider: voice.ttsProvider,
    tts_model: voice.ttsModel,
    voice: voice.voice,
    auto_speak: String(voice.autoSpeak),
    response_format: voice.responseFormat,
    realtime_enabled: voice.realtimeEnabled === null ? "auto" : String(voice.realtimeEnabled),
  });
  return request<SettingsPayload>(`${base}/api/settings/voice/live/update?${query}`, token);
}

export type CrmKind =
  | "companies"
  | "contacts"
  | "leads"
  | "opportunities"
  | "activities"
  | "products"
  | "opportunity_lines"
  | "files";

export type CrmRecord = Record<string, unknown> & { id: string };

export type CrmSettings = {
  companyName: string;
  legalName: string;
  industry: string;
  country: string;
  website: string;
  phone: string;
  email: string;
  currency: string;
  currencySymbol: string;
  locale: string;
  configured: boolean;
  role?: string | null;
  canWrite?: boolean;
};

export type CrmDashboard = {
  pipelineTotal: number;
  openCount: number;
  wonThisMonth: number;
  conversion: number;
  forecast: number;
  funnel: Record<string, number>;
  todo: CrmRecord[];
  topDeals: CrmRecord[];
  followups?: CrmRecord[];
  counts: Record<string, number>;
  currency?: string;
  locale?: string;
  currencySymbol?: string;
  settingsConfigured?: boolean;
  settings?: CrmSettings;
};

export type CrmInsights = {
  id: string;
  kind: string;
  health: number;
  tone: string;
  lastContactDays: number | null;
  nextAction: string;
  risk: string;
  suggestion: string;
  activityCount: number;
};

export type CrmMember = {
  id: string;
  identity: string;
  email: string;
  handle: string;
  userId: string;
  displayName: string;
  role: "owner" | "admin" | "member" | "viewer" | string;
  createdAt: number;
};

export type CrmInvite = {
  id: string;
  identity: string;
  email: string;
  handle: string;
  userId: string;
  role: string;
  status: string;
  invitedBy: string;
  createdAt: number;
};

export type CrmAudit = {
  id: string;
  action: string;
  kind: string;
  recordId: string;
  actor: string;
  detail: string;
  createdAt: number;
};

export type CrmChannelStatus = {
  channel: string;
  enabled: boolean;
  ready: boolean;
  hint: string;
};

function crmUrl(key: string, action: string, extra: Record<string, string> = {}, base = "") {
  const query = new URLSearchParams();
  query.set("action", action);
  for (const [name, value] of Object.entries(extra)) {
    if (value) query.set(name, value);
  }
  return `${base}/api/sessions/${encodeURIComponent(key)}/crm?${query}`;
}

export async function fetchCrmList(
  token: string,
  key: string,
  kind: CrmKind,
  base = "",
): Promise<{ kind: CrmKind; records: CrmRecord[] }> {
  return request(crmUrl(key, "list", { kind }, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function createCrmRecord(
  token: string,
  key: string,
  kind: CrmKind,
  body: Record<string, unknown>,
  base = "",
): Promise<{ record: CrmRecord }> {
  return request(
    crmUrl(key, "create", { kind }, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function updateCrmRecord(
  token: string,
  key: string,
  kind: CrmKind,
  id: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{ record: CrmRecord }> {
  return request(
    crmUrl(key, "update", { kind, id }, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function deleteCrmRecord(
  token: string,
  key: string,
  kind: CrmKind,
  id: string,
  actor = "",
  base = "",
): Promise<{ ok: boolean; id: string }> {
  return request(
    crmUrl(key, "delete", { kind, id, actor }, base),
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmSettings(
  token: string,
  key: string,
  actor = "",
  base = "",
): Promise<CrmSettings> {
  return request(crmUrl(key, "settings", { actor }, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function updateCrmSettings(
  token: string,
  key: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{ settings: CrmSettings }> {
  return request(
    crmUrl(key, "update_settings", {}, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export type CrmConvertBody = {
  owner?: string;
  actor?: string;
  opportunityName?: string;
  companyId?: string;
  contactId?: string;
  amount?: number;
  currency?: string;
  stage?: string;
  expectedCloseDate?: string;
  ownerId?: string;
};

export async function convertCrmLead(
  token: string,
  key: string,
  id: string,
  ownerOrBody: string | CrmConvertBody = "",
  base = "",
): Promise<{
  lead: CrmRecord;
  company: CrmRecord | null;
  contact: CrmRecord | null;
  opportunity: CrmRecord;
  created: boolean;
}> {
  const body: CrmConvertBody =
    typeof ownerOrBody === "string" ? { owner: ownerOrBody } : { ...ownerOrBody };
  return request(
    crmUrl(key, "convert", { id }, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmDashboard(
  token: string,
  key: string,
  base = "",
): Promise<CrmDashboard> {
  return request(crmUrl(key, "dashboard", {}, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchCrmInsights(
  token: string,
  key: string,
  kind: CrmKind,
  id: string,
  base = "",
): Promise<CrmInsights> {
  return request(crmUrl(key, "insights", { kind, id }, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchCrmTimeline(
  token: string,
  key: string,
  kind: CrmKind,
  id: string,
  base = "",
): Promise<{ records: CrmRecord[] }> {
  return request(crmUrl(key, "timeline", { kind, id }, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchCrmAudit(
  token: string,
  key: string,
  kind = "",
  id = "",
  base = "",
): Promise<{ records: CrmAudit[] }> {
  return request(crmUrl(key, "audit", { kind, id }, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchCrmMembers(
  token: string,
  key: string,
  actor = "",
  base = "",
): Promise<{ members: CrmMember[]; invites: CrmInvite[]; source: string; sync?: string }> {
  return request(crmUrl(key, "members", { actor }, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function inviteCrmMember(
  token: string,
  key: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{ invite: CrmInvite; created: boolean }> {
  return request(
    crmUrl(key, "invite", {}, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function acceptCrmInvite(
  token: string,
  key: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{ ok: boolean; status: string; member: CrmMember | null }> {
  return request(
    crmUrl(key, "accept", {}, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function setCrmMemberRole(
  token: string,
  key: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{ member: CrmMember }> {
  return request(
    crmUrl(key, "role", {}, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function kickCrmMember(
  token: string,
  key: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{ ok: boolean; id: string }> {
  return request(
    crmUrl(key, "kick", {}, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmProducts(
  token: string,
  key: string,
  base = "",
): Promise<{ records: CrmRecord[] }> {
  return request(crmUrl(key, "products", {}, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchCrmLines(
  token: string,
  key: string,
  opportunityId: string,
  base = "",
): Promise<{ records: CrmRecord[] }> {
  return request(
    crmUrl(key, "lines", { id: opportunityId }, base),
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmFollowups(
  token: string,
  key: string,
  create = false,
  actor = "",
  base = "",
): Promise<{ items: Array<{ opportunity: CrmRecord; reason: string; days: number | null }>; created: CrmRecord[] }> {
  return request(
    crmUrl(key, "followups", create ? { create: "1", actor } : { actor }, base),
    token,
    { headers: bodyHeaders(JSON.stringify({ create, actor, days: 7 })) },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmCalendar(
  token: string,
  key: string,
  start: number,
  end: number,
  base = "",
): Promise<{ records: CrmRecord[] }> {
  return request(
    crmUrl(key, "calendar", { start: String(start), end: String(end) }, base),
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmChannels(
  token: string,
  key: string,
  base = "",
): Promise<{ email: CrmChannelStatus; whatsapp: CrmChannelStatus; teams: CrmChannelStatus }> {
  return request(crmUrl(key, "channels", {}, base), token, undefined, API_READ_TIMEOUT_MS);
}

export async function crmOutreach(
  token: string,
  key: string,
  body: Record<string, unknown>,
  base = "",
): Promise<{
  sent: boolean;
  prepared: boolean;
  channel: string;
  error: string;
  activity: CrmRecord;
  status: CrmChannelStatus;
}> {
  return request(
    crmUrl(key, "outreach", {}, base),
    token,
    { headers: bodyHeaders(JSON.stringify(body)) },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCrmSync(
  token: string,
  key: string,
  base = "",
): Promise<{ path: string; wal: boolean; memberCount: number; lastWrite: number; cloud: boolean; note: string }> {
  return request(crmUrl(key, "sync", {}, base), token, undefined, API_READ_TIMEOUT_MS);
}
