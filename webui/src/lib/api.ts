import type {
  ApiServicePayload,
  AutomationsPayload,
  AutomationUpdatePayload,
  ChannelConfigurePayload,
  ChannelValidationPayload,
  ChatSummary,
  CliAppsPayload,
  FileDiagnosticsPayload,
  FilePreviewPayload,
  FsListPayload,
  FsRootsPayload,
  GitChangesPayload,
  GitDiffPayload,
  GitStatusPayload,
  FileSavePayload,
  ProjectSearchPayload,
  FileTreePayload,
  ImageGenerationSettingsUpdate,
  VideoGenerationSettingsUpdate,
  McpPresetsPayload,
  NavinFeaturesPayload,
  ModelConfigurationCreate,
  ModelConfigurationUpdate,
  NetworkSafetySettingsUpdate,
  PairingPayload,
  PluginPacksPayload,
  ProviderModelsPayload,
  ProviderSettingsUpdate,
  SessionDeleteResult,
  SessionAutomationsPayload,
  SettingsPayload,
  SettingsUpdate,
  SidebarStatePayload,
  ExecPolicyPayload,
  MetagraphPayload,
  TeamRosterPayload,
  SkillDetail,
  SkillsPayload,
  SlashCommand,
  SlashCommandLifecycle,
  TranscriptionSettingsUpdate,
  WebSearchSettingsUpdate,
  WorkspacesPayload,
  WebuiThreadPersistedPayload,
  WorkspaceScopePayload,
} from "./types";
import { fetchWithTimeout } from "./http";

const API_READ_TIMEOUT_MS = 20_000;
const API_SLOW_TIMEOUT_MS = 180_000;
const SLASH_COMMAND_LIFECYCLES = new Set<SlashCommandLifecycle>([
  "side_channel",
  "finalize_active_turn",
  "stop_active_turn",
  "agent_turn",
  "agent_turn_with_args",
]);

function isSlashCommandLifecycle(value: unknown): value is SlashCommandLifecycle {
  return (
    typeof value === "string"
    && SLASH_COMMAND_LIFECYCLES.has(value as SlashCommandLifecycle)
  );
}
const CHANNEL_VALUES_HEADER = "X-Navin-Channel-Values";
const API_SERVICE_VALUES_HEADER = "X-Navin-API-Service-Values";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
  timeoutMs: number = 0,
): Promise<T> {
  const res = await fetchWithTimeout(
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
  if (!res.ok) {
    const text = typeof res.text === "function" ? (await res.text()).trim() : "";
    throw new ApiError(res.status, text || `HTTP ${res.status}`);
  }
  const contentType = res.headers?.get?.("content-type") ?? "";
  if (contentType && !contentType.toLowerCase().includes("application/json")) {
    const text = typeof res.text === "function" ? await res.text() : "";
    const isHtml = text.trimStart().toLowerCase().startsWith("<!doctype");
    throw new ApiError(
      res.status,
      isHtml
        ? "Gateway returned WebUI HTML instead of JSON. Restart navin gateway and try again."
        : "Gateway returned a non-JSON response.",
    );
  }
  return (await res.json()) as T;
}

function mcpValuesHeader(values: Record<string, unknown>): HeadersInit | undefined {
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
  return { "X-Navin-Automation-Values": encodeURIComponent(JSON.stringify(values)) };
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

export async function fetchFilePreview(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
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
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("any", "1");
  return request<FilePreviewPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-preview?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

const FILE_BODY_CHUNK_CHARS = 6000;

export async function saveWorkspaceFile(
  token: string,
  key: string,
  path: string,
  content: string,
  base: string = "",
): Promise<FileSavePayload> {
  const bytes = new TextEncoder().encode(content);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  const b64 = btoa(binary);
  const headers: Record<string, string> = {};
  let index = 0;
  for (let i = 0; i < b64.length; i += FILE_BODY_CHUNK_CHARS) {
    headers[`X-Navin-File-Body-${index}`] = b64.slice(i, i + FILE_BODY_CHUNK_CHARS);
    index += 1;
  }
  const query = new URLSearchParams();
  query.set("path", path);
  return request<FileSavePayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/file-save?${query}`,
    token,
    // The gateway's HTTP layer (websockets process_request) only accepts GET;
    // content travels in chunked base64 headers like the skills API.
    { headers },
    API_READ_TIMEOUT_MS,
  );
}

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

export async function fetchFilePreviewAvailability(
  token: string,
  key: string,
  path: string,
  base: string = "",
): Promise<boolean> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("probe", "1");
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

function skillBodyHeader(markdown: string | undefined): HeadersInit | undefined {
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
  return request<SkillsPayload>(`${base}/api/webui/skills/delete?${query}`, token);
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

export async function fetchFileDiagnostics(
  token: string,
  path: string,
  base: string = "",
): Promise<FileDiagnosticsPayload> {
  const query = new URLSearchParams({ path });
  return request<FileDiagnosticsPayload>(
    `${base}/api/webui/diagnostics?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
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
  input: { source: "git" | "path"; location: string; name?: string },
  base: string = "",
): Promise<PluginPacksPayload> {
  const query = new URLSearchParams();
  query.set("source", input.source);
  query.set("location", input.location);
  if (input.name) query.set("name", input.name);
  return request<PluginPacksPayload>(
    `${base}/api/webui/plugins/install?${query}`,
    token,
    undefined,
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
  return request<PluginPacksPayload>(`${base}/api/webui/plugins/remove?${query}`, token);
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
  return request<PluginPacksPayload>(`${base}/api/webui/plugins/${action}?${query}`, token);
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
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    `${base}/api/settings`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
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

export interface VersionCheckResult {
  updateAvailable: {
    currentVersion: string;
    latestVersion: string;
    pypiUrl?: string;
  } | null;
}

export async function checkVersion(
  token: string,
  base: string = "",
): Promise<VersionCheckResult> {
  return request<VersionCheckResult>(
    `${base}/api/settings/version-check`,
    token,
    undefined,
    10_000,
  );
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

export async function fetchApiService(token: string, base: string = ""): Promise<ApiServicePayload> {
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
  const headers = values.apiKey === undefined
    ? undefined
    : { [API_SERVICE_VALUES_HEADER]: JSON.stringify({ api_key: values.apiKey }) };
  return request<ApiServicePayload>(
    `${base}/api/settings/api-service/start?${query}`,
    token,
    { headers },
  );
}

export async function stopApiService(token: string, base: string = ""): Promise<ApiServicePayload> {
  return request<ApiServicePayload>(`${base}/api/settings/api-service/stop`, token);
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

export async function runCliAppAction(
  token: string,
  action: "install" | "update" | "uninstall" | "test",
  name: string,
  base: string = "",
): Promise<CliAppsPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<CliAppsPayload>(`${base}/api/settings/cli-apps/${action}?${query}`, token);
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
): Promise<ProviderModelsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  return request<ProviderModelsPayload>(
    `${base}/api/settings/provider-models?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
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
  const body = await request<{ commands: Row[] }>(
    `${base}/api/commands`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
  return body.commands
    .flatMap((command) => {
      if (!isSlashCommandLifecycle(command.lifecycle)) return [];
      return [{
        command: command.command,
        title: command.title,
        description: command.description,
        icon: command.icon,
        argHint: command.arg_hint ?? "",
        lifecycle: command.lifecycle,
        acceptsArgs: command.accepts_args === true,
      }];
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
  const query = new URLSearchParams();
  query.set("state", JSON.stringify(state));
  return request<SidebarStatePayload>(
    `${base}/api/webui/sidebar-state/update?${query}`,
    token,
  );
}

export async function fetchTeamRoster(
  token: string,
  base: string = "",
): Promise<TeamRosterPayload> {
  return request<TeamRosterPayload>(
    `${base}/api/webui/team-roster`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateTeamRoster(
  token: string,
  roster: TeamRosterPayload,
  base: string = "",
): Promise<TeamRosterPayload> {
  const query = new URLSearchParams();
  query.set("roster", JSON.stringify(roster));
  return request<TeamRosterPayload>(
    `${base}/api/webui/team-roster/update?${query}`,
    token,
  );
}

export async function fetchMetagraph(
  token: string,
  key: string,
  base: string = "",
): Promise<MetagraphPayload> {
  return request<MetagraphPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/metagraph`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function searchProject(
  token: string,
  key: string,
  query: string,
  options?: { regex?: boolean; caseSensitive?: boolean },
  base: string = "",
): Promise<ProjectSearchPayload> {
  const params = new URLSearchParams();
  params.set("q", query);
  if (options?.regex) params.set("regex", "1");
  if (options?.caseSensitive) params.set("case", "1");
  return request<ProjectSearchPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/search?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
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

export async function fetchGitDiff(
  token: string,
  key: string,
  file: string,
  base: string = "",
): Promise<GitDiffPayload> {
  const params = new URLSearchParams();
  params.set("file", file);
  return request<GitDiffPayload>(
    `${base}/api/sessions/${encodeURIComponent(key)}/git-diff?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
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
  if (update.toolHintMaxLength !== undefined) {
    query.set("tool_hint_max_length", String(update.toolHintMaxLength));
  }
  return request<SettingsPayload>(`${base}/api/settings/update?${query}`, token);
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

export async function updateModelConfiguration(
  token: string,
  configuration: ModelConfigurationUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("name", configuration.name);
  if (configuration.label !== undefined) query.set("label", configuration.label);
  if (configuration.provider !== undefined) query.set("provider", configuration.provider);
  if (configuration.model !== undefined) query.set("model", configuration.model);
  if (configuration.contextWindowTokens !== undefined) {
    query.set("context_window_tokens", String(configuration.contextWindowTokens));
  }
  return request<SettingsPayload>(
    `${base}/api/settings/model-configurations/update?${query}`,
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
  return request<SettingsPayload>(
    `${base}/api/settings/provider/update?${query}`,
    token,
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
  if (update.maxResults !== undefined) query.set("max_results", String(update.maxResults));
  if (update.timeout !== undefined) query.set("timeout", String(update.timeout));
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
  query.set("webui_allow_local_service_access", String(update.webuiAllowLocalServiceAccess));
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
