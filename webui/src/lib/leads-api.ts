// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { apiBodyHeaders, apiRequest } from "@/lib/api";
import { externalHttpUrl } from "@/lib/external-url";

export const LEADS_DESK_HASH = "#/leads";

export function leadsDeskHash(input: { pane?: "home" | "book"; lead?: string; chat?: string } = {}): string {
  const params = new URLSearchParams();
  const chat = (input.chat || "").trim();
  const lead = (input.lead || "").trim();
  if (chat) params.set("chat", chat);
  if (lead) params.set("lead", lead);
  if (!lead && input.pane === "book") params.set("pane", "book");
  const query = params.toString();
  return query ? `${LEADS_DESK_HASH}?${query}` : LEADS_DESK_HASH;
}

/** Official company / docs / LinkedIn URL that must leave the Tauri WebView. */
export function officialLeadHref(href: string): string | null {
  const raw = href.trim();
  if (!raw) return null;
  if (/^https?:\/\//i.test(raw)) return externalHttpUrl(raw);
  // Hunt rows often store "acme.com" or "linkedin.com/in/x" without a scheme.
  if (canPrefixHttps(raw)) return externalHttpUrl(`https://${raw}`);
  return null;
}

function canPrefixHttps(raw: string): boolean {
  if (
    raw.startsWith("#")
    || raw.startsWith("/")
    || raw.includes("://")
    || raw.includes(" ")
    || raw.includes("\\")
  ) {
    return false;
  }
  return /^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}([/:?#].*)?$/i.test(raw);
}

export type LeadStage =
  | "new"
  | "qualified"
  | "contacted"
  | "replied"
  | "meeting"
  | "opportunity"
  | "won";

export type LeadProvider = {
  id: string;
  name: string;
  tier?: number;
  region?: string;
  kind?: string;
  docs?: string;
  notes?: string;
  configured?: boolean;
  hint?: string;
  needs_key?: boolean;
  wired?: boolean;
  live?: boolean | null;
  error?: string;
};

export type LeadBant = {
  fit?: number;
  need?: number;
  timing?: number;
  authority?: number;
  budget?: number;
};

export type LeadSignal = {
  kind?: string;
  text?: string;
};

export type LeadDraft = {
  subject?: string;
  body?: string;
  lang?: string;
  model?: string;
  kind?: string;
};

export type LeadSequenceStep = {
  n?: number;
  day?: number;
  channel?: string;
  label?: string;
  status?: string;
  due_at?: number;
  sent_at?: number;
  sent_channel?: string;
  draft?: LeadDraft;
};

export type LeadSequence = {
  name?: string;
  started_at?: number;
  step?: number;
  steps?: LeadSequenceStep[];
  stopped?: string;
  stopped_at?: number;
};

/** The step a human (or the autonomous loop) sends next: first unsent step, drafted or not. */
export function leadNextStep(row: LeadRow): LeadSequenceStep | null {
  if (row.sequence?.stopped) return null;
  const steps = [...(row.sequence?.steps || [])].sort((a, b) => (Number(a.n) || 0) - (Number(b.n) || 0));
  return steps.find((step) => String(step.status || "") !== "sent") || null;
}

export type LeadSource = "web" | "osm" | "hiring";
export const LEAD_SOURCES: LeadSource[] = ["web", "osm", "hiring"];
export type LeadExecutionMode = "approval" | "autonomous";

export type LeadRow = {
  id: string;
  company: string;
  domain?: string;
  website?: string;
  person?: string;
  first_name?: string;
  last_name?: string;
  role?: string;
  email?: string;
  email_status?: string;
  phone?: string;
  linkedin_url?: string;
  country?: string;
  sector?: string;
  size?: string;
  signal?: string;
  source?: string;
  confidence?: string;
  score?: number;
  tier?: string;
  stage?: LeadStage | string;
  bant?: LeadBant;
  why?: string[];
  next_action?: string;
  signals?: LeadSignal[];
  disqualify?: string[];
  sequence?: LeadSequence;
  gaps?: string[];
  archived?: boolean;
  alerts_sent?: string[];
  created_at?: number;
  updated_at?: number;
  crm_lead_id?: string;
  extra?: Record<string, unknown>;
};

export function leadHasDueStep(row: LeadRow, now = Date.now() / 1000): boolean {
  if (row.sequence?.stopped) return false;
  const steps = [...(row.sequence?.steps || [])].sort((a, b) => (Number(a.n) || 0) - (Number(b.n) || 0));
  for (const step of steps) {
    if (String(step.status || "") === "sent") continue;
    const dueAt = Number(step.due_at || 0);
    return dueAt > 0 && dueAt <= now;
  }
  return false;
}

export function leadOutreachDest(row: LeadRow, channel: string): string {
  const kind = String(channel || "").trim().toLowerCase();
  if (kind === "email") return String(row.email || "").trim();
  if (kind === "whatsapp") return String(row.phone || "").trim();
  const extra = row.extra || {};
  return String(extra[`${kind}_to`] || extra[kind] || "").trim();
}

export function leadHasSequence(row: LeadRow): boolean {
  return (row.sequence?.steps || []).length > 0;
}

export function leadInOutreach(row: LeadRow): boolean {
  if (["contacted", "replied", "meeting", "opportunity", "won"].includes(String(row.stage || ""))) {
    return true;
  }
  return leadHasSequence(row);
}

export function leadOutreachWhy(
  row: LeadRow,
  channel: string,
  ready: Record<string, LeadChannelReady> = {},
): string {
  const kind = String(channel || "").trim().toLowerCase();
  if (!leadOutreachDest(row, kind)) {
    if (kind === "email") return "need-email";
    if (kind === "whatsapp") return "need-phone";
    return "need-dest";
  }
  const status = ready[kind];
  if (!status?.ready) return "need-channel";
  return "";
}

export function leadHasBuyingSignal(row: LeadRow): boolean {
  return (row.signals || []).some((item) => ["funding", "hiring", "news"].includes(String(item.kind || "")));
}

/** Short, stable label for the origin of a row; the UI shows it as a badge. */
export function leadSourceLabel(source?: string): string {
  const key = String(source || "").trim().toLowerCase();
  const labels: Record<string, string> = {
    sirene: "SIRENE",
    companies_house: "Companies House",
    opencorporates: "OpenCorporates",
    web: "Web",
    osm: "OpenStreetMap",
    hiring: "Hiring",
    apollo: "Apollo",
    places: "Places",
    crunchbase: "Crunchbase",
    manual: "Manual",
  };
  return labels[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "");
}

/** Browser-side CSV download; no server file, nothing leaves the machine. */
export function downloadTextFile(text: string, filename: string, mime = "text/csv;charset=utf-8"): void {
  if (typeof document === "undefined" || typeof URL === "undefined" || typeof Blob === "undefined") return;
  const blob = new Blob([text], { type: mime });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1_000);
}

export type LeadsChannels = {
  email?: boolean;
  teams?: boolean;
  whatsapp?: boolean;
  telegram?: boolean;
  email_to?: string;
  teams_to?: string;
  whatsapp_to?: string;
  telegram_to?: string;
};

export type LeadChannelReady = {
  channel?: string;
  enabled?: boolean;
  ready?: boolean;
  hint?: string;
};

export type LeadLoopKind = "daily" | "weekdays" | "weekend" | "weekly" | "monthly";

export type LeadLoopSchedule = {
  kind: LeadLoopKind;
  hour: number;
  minute: number;
  weekday?: number;
  day?: number | "last";
  tz?: string | null;
  expr?: string;
};

export type LeadLoop = {
  enabled?: boolean;
  phase?: string;
  next_due?: number;
  last_tick?: number;
  last_watch?: number;
  last_result?: string;
  cycle?: number;
  schedule?: LeadLoopSchedule | null;
  added?: number;
  scanned?: number;
  alerts?: number;
  sent?: number;
  drafts_ready?: number;
};

export type LeadsProfile = {
  icp_name?: string;
  countries?: string[];
  sector?: string;
  size_min?: number;
  size_max?: number;
  titles?: string[];
  count?: number;
  offer?: string;
  cities?: string[];
  keywords?: string[];
  signals?: string[];
  sources?: LeadSource[] | string[];
  execution_mode?: LeadExecutionMode | string;
  daily_send_cap?: number;
  sender_name?: string;
  language?: string;
  wizard_ready?: boolean;
  channels?: LeadsChannels;
};

export type LeadsSequencesResult = {
  drafted?: number;
  sent?: number;
  ready?: number;
  stopped?: number;
  blocked?: string;
  autonomous?: boolean;
  enrolled?: number;
};

export type LeadsExport = { csv: string; rows: number; filename: string };

export type LeadsDesk = {
  profile: LeadsProfile;
  leads: LeadRow[];
  loop?: LeadLoop;
  kpis: {
    total?: number;
    strong?: number;
    verified?: number;
    with_email?: number;
    pipeline?: number;
    due?: number;
    signals?: number;
    sources?: Record<string, number>;
    sent_steps?: number;
    headline?: string;
    queue?: Array<{
      id?: string;
      company?: string;
      score?: number;
      next_action?: string;
      due?: boolean;
    }>;
  };
  lookalike?: { seed?: string; found?: number; added?: number; kept?: number };
  sequence?: LeadSequence;
  stages: string[];
  providers: LeadProvider[];
  catalog?: LeadProvider[];
  channels?: { configured?: LeadsChannels; ready?: Record<string, LeadChannelReady> };
  outreach?: {
    sent?: boolean;
    prepared?: boolean;
    channel?: string;
    to?: string;
    subject?: string;
    body?: string;
  };
  wizard_ready?: boolean;
  tagline?: string;
  files?: Record<string, string>;
  hunt?: { found?: number; added?: number; kept?: number; by_source?: Record<string, number>; optout?: number };
  crm?: { id?: string; name?: string };
  sequences?: LeadsSequencesResult;
  draft?: LeadDraft;
  optout?: { count?: number; stopped?: number };
};

/** CRM lives on a real project folder, never the NavinProjects container. */
export function usableCrmProject(path?: string | null): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  const name = raw.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "";
  if (name.toLowerCase() === "navinprojects") return "";
  return raw;
}

export function crmProjectLabel(path?: string | null): string {
  const ok = usableCrmProject(path);
  if (!ok) return "";
  return ok.replace(/\\/g, "/").split("/").filter(Boolean).pop() || ok;
}

export function resolveLeadsCrmProject(input: {
  projectPath?: string | null;
  lastDevPath?: string | null;
  recentProjects?: Array<{ path?: string | null }>;
  extraPaths?: Array<string | null | undefined>;
}): string {
  const candidates = [
    input.projectPath,
    input.lastDevPath,
    ...(input.extraPaths || []),
    ...(input.recentProjects || []).map((row) => row.path),
  ];
  for (const raw of candidates) {
    const ok = usableCrmProject(raw);
    if (ok) return ok;
  }
  return "";
}

export function emptyLeadsDesk(): LeadsDesk {
  return {
    profile: {
      icp_name: "",
      countries: ["FR"],
      sector: "",
      size_min: 20,
      size_max: 200,
      titles: ["CEO", "CTO"],
      count: 50,
      offer: "",
      cities: [],
      keywords: [],
      signals: [],
      sources: ["web", "osm", "hiring"],
      execution_mode: "approval",
      daily_send_cap: 20,
      sender_name: "",
      wizard_ready: false,
    },
    leads: [],
    kpis: { total: 0, strong: 0, verified: 0, with_email: 0, pipeline: 0, due: 0, signals: 0, queue: [] },
    stages: ["new", "qualified", "contacted", "replied", "meeting", "opportunity", "won"],
    providers: [],
    wizard_ready: false,
  };
}

function leadsUrl(action: string): string {
  return `/api/leads?action=${encodeURIComponent(action)}`;
}

export async function fetchLeadsDesk(token: string): Promise<LeadsDesk> {
  return apiRequest<LeadsDesk>(leadsUrl("snapshot"), token, {
    headers: apiBodyHeaders("{}"),
  });
}

export async function postLeads(
  token: string,
  action: string,
  body: Record<string, unknown> = {},
): Promise<LeadsDesk> {
  return apiRequest<LeadsDesk>(leadsUrl(action), token, {
    headers: apiBodyHeaders(JSON.stringify(body)),
  });
}

export async function fetchLeadsExport(
  token: string,
  filter: { tier?: string; stage?: string } = {},
): Promise<LeadsExport> {
  return apiRequest<LeadsExport>(leadsUrl("export"), token, {
    headers: apiBodyHeaders(JSON.stringify(filter)),
  });
}
