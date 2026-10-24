/** Collaboration Mode: the named team that works a session together. */

export type TeamRole =
  | "architect"
  | "developer"
  | "tester"
  | "security"
  | "reviewer"
  | "reverse"
  | "seo"
  | "marketing"
  | "sales"
  | "ads"
  | "campaigns"
  | "designer"
  | "devops"
  | "infra"
  | "custom";
export type TeamSandbox = "workspace" | "worktree" | "readonly";
export type TeamPattern =
  | "dev_tester"
  | "dual_planning"
  | "adversarial_review"
  | "parallel_solutions"
  | "pipeline"
  | "debate";

export interface TeamMember {
  id: string;
  name: string;
  role: TeamRole;
  title: string;
  specialty: string;
  email: string;
  avatar: string;
  color: string;
  model_preset: string;
  reasoning_effort: string | null;
  sandbox: TeamSandbox;
  prompt: string;
  tools: string[];
  skills: string[];
  enabled: boolean;
}

export interface CollabSettings {
  /** The human the team reports to by email (Settings > Channels > Email sends). */
  manager_name?: string;
  manager_email?: string;
  /** More people the team may email; the manager is always reachable. */
  email_contacts?: Array<{ name: string; email: string }>;
  /** Off: manager + contacts only. On: any address (rate-limited). */
  email_allow_any?: boolean;
  enabled: boolean;
  pattern: TeamPattern;
  max_rounds: number;
  auto_feedback: boolean;
  human_approval_critical: boolean;
  budget_tokens: number;
  members: TeamMember[];
  /** Named teams a chat can work with; a teammate may be in several. */
  teams: TeamGroup[];
  /** Team id a new chat starts with. */
  default_team: string;
}

export interface TeamGroup {
  id: string;
  name: string;
  /** Teammate ids. */
  members: string[];
}

export interface CollabRoleDefaults {
  key: TeamRole;
  tools: string[];
  skills: string[];
  prompt: string;
  sandbox: TeamSandbox;
  effort: string;
}

export interface CollabCatalog {
  roles: CollabRoleDefaults[];
  tools: string[];
  skills: Array<{ name: string; description: string }>;
  presets: Array<{ name: string; label: string; model: string }>;
  avatars: string[];
  sandboxes: TeamSandbox[];
  patterns: TeamPattern[];
  efforts: string[];
}

export interface CollabPayload {
  collab: CollabSettings;
  catalog: CollabCatalog;
  /** Whether the Email channel (SMTP) can send, and where to set it up. */
  email?: { ready: boolean; hint: string };
}

/** Who speaks in the team view. id "lead" is Navin, id "user" is the user. */
export interface TeamIdentity {
  id: string;
  name: string;
  title?: string;
  role?: string;
  avatar?: string;
  color?: string;
  email?: string;
}

export type TeamEntryKind = "assign" | "message" | "result" | "user" | "note" | "email";

export interface TeamFeedEntry {
  chatId: string;
  entryId: string;
  kind: TeamEntryKind;
  sender: TeamIdentity;
  target: TeamIdentity | null;
  content: string;
  taskId?: string;
  status?: string;
  ts: number;
}

export function parseTeamIdentity(raw: unknown): TeamIdentity | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const id = typeof value.id === "string" ? value.id : "";
  const name = typeof value.name === "string" ? value.name : "";
  if (!id && !name) return null;
  const text = (key: string) => (typeof value[key] === "string" && value[key] ? String(value[key]) : undefined);
  return {
    id: id || name,
    name: name || id,
    title: text("title"),
    role: text("role"),
    avatar: text("avatar"),
    color: text("color"),
    email: text("email"),
  };
}

/** One ``team_message`` frame (or a ``team_feed`` row) into a feed entry. */
export function parseTeamEntry(raw: unknown, chatId = ""): TeamFeedEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const entryId = typeof value.entry_id === "string" ? value.entry_id : "";
  const sender = parseTeamIdentity(value.sender);
  if (!entryId || !sender) return null;
  const kinds: TeamEntryKind[] = ["assign", "message", "result", "user", "note", "email"];
  const kind = kinds.includes(value.kind as TeamEntryKind) ? (value.kind as TeamEntryKind) : "message";
  return {
    chatId: typeof value.chat_id === "string" ? value.chat_id : chatId,
    entryId,
    kind,
    sender,
    target: parseTeamIdentity(value.target),
    content: typeof value.content === "string" ? value.content : "",
    taskId: typeof value.task_id === "string" ? value.task_id : undefined,
    status: typeof value.status === "string" ? value.status : undefined,
    ts: typeof value.ts === "number" ? value.ts : Date.now() / 1000,
  };
}

/** Merge entries by id, oldest first, capped like the gateway's feed. */
export function mergeTeamFeed(current: TeamFeedEntry[], incoming: TeamFeedEntry[], cap = 300): TeamFeedEntry[] {
  if (!incoming.length) return current;
  const seen = new Set(current.map((entry) => entry.entryId));
  const merged = [...current];
  for (const entry of incoming) {
    if (seen.has(entry.entryId)) continue;
    seen.add(entry.entryId);
    merged.push(entry);
  }
  merged.sort((a, b) => a.ts - b.ts);
  return merged.length > cap ? merged.slice(merged.length - cap) : merged;
}
