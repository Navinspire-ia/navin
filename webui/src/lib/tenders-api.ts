// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { apiBodyHeaders, apiRequest } from "@/lib/api";
import { externalHttpUrl } from "@/lib/external-url";
import { studioSessionQuery } from "@/lib/studio-request";
import type { DocumentGeneration } from "@/lib/document-generation";

export type TenderExportKind = "docx" | "pptx" | "diagram_html";

/** In-IDE desk. Desktop shells must keep this hash inside the WebView. */
export const TENDERS_DESK_HASH = "#/tenders";

export function tendersDeskHash(input: { notice?: string; chat?: string; pane?: "home" | "tenders" } = {}): string {
  const params = new URLSearchParams();
  const chat = (input.chat || "").trim();
  const notice = (input.notice || "").trim();
  if (chat) params.set("chat", chat);
  if (notice) params.set("notice", notice);
  if (!notice && input.pane === "tenders") params.set("pane", "tenders");
  const query = params.toString();
  return query ? `${TENDERS_DESK_HASH}?${query}` : TENDERS_DESK_HASH;
}

/** Official portal URL that must leave the IDE (OS browser). Never a hash. */
export function officialTenderHref(href: string): string | null {
  return externalHttpUrl(href);
}

export type TenderStage =
  | "discovered"
  | "matched"
  | "scored"
  | "analysed"
  | "go"
  | "no-go"
  | "drafting"
  | "validating"
  | "submitted"
  | "clarification"
  | "shortlisted"
  | "negotiation"
  | "won"
  | "lost";

export interface TenderSource {
  id: string;
  name: string;
  country: string;
  zone?: string;
  priority: string;
  ingest: string;
  coverage?: string;
  covered_by?: string;
  access?: string;
  url: string;
  api?: string;
  notes?: string;
}

export interface TenderNotice {
  id: string;
  source_id: string;
  country: string;
  buyer?: string;
  title: string;
  description?: string;
  sector?: string;
  cpv?: string;
  budget?: number | null;
  currency?: string;
  deadline?: string;
  publication_date?: string;
  source_url?: string;
  reference?: string;
  status?: string;
  city?: string;
  eligibility?: string;
  submission_method?: string;
  stage: TenderStage | string;
  score?: number | null;
  score_breakdown?: Record<string, number | null | undefined>;
  go?: boolean | null;
  go_pct?: number;
  go_reason?: string;
  go_note?: string;
  effort_days?: number | null;
  analysis?: Record<string, unknown>;
  response?: Record<string, unknown> & {
    cover?: string;
    toc?: string;
    letter?: string;
    executive_summary?: string;
    company?: string;
    need?: string;
    approach?: string;
    vision?: string;
    functional?: string;
    architecture?: string;
    methodology?: string;
    followup_kpi?: string;
    raci_risks?: string;
    governance?: string;
    planning?: string;
    staffing?: string;
    references?: string;
    compliance_matrix?: string;
    financial_schedule?: string;
    revision_notes?: string;
    language?: string;
    model?: string;
    used_files?: string[];
    from_file?: boolean;
    pack_ready?: boolean;
    generation?: DocumentGeneration;
    review_needed?: boolean;
    submission_ready?: boolean;
    exports?: {
      docx?: { file_id?: string; name?: string; mime?: string };
      pptx?: { file_id?: string; name?: string; mime?: string };
      diagram_html?: { file_id?: string; name?: string; mime?: string };
      diagram_svg?: { file_id?: string; name?: string; mime?: string };
    };
  };
  response_reviews?: { t?: number; remarks?: string; model?: string }[];
  cdc_text?: string;
  enriched?: boolean;
  mail?: { kind?: string; body?: string; to?: string; subject?: string; sent?: boolean }[];
  crm_opportunity_id?: string;
  alerts_sent?: string[];
  favorite?: boolean;
  archived?: boolean;
  archived_at?: number | null;
  fetched_at?: number;
}

/** One desk task and the Settings model route it runs on. */
export interface TenderModelRoute {
  task: string;
  role: string;
  preset?: string;
  model?: string;
}

export type TenderLoopKind = "daily" | "weekdays" | "weekend" | "weekly" | "monthly";

export interface TenderLoopSchedule {
  kind: TenderLoopKind;
  hour: number;
  minute: number;
  weekday?: number;
  day?: number | "last";
  tz?: string | null;
  expr?: string;
}

export interface TenderLoop {
  enabled?: boolean;
  phase?: string;
  next_due?: number;
  last_tick?: number;
  last_watch?: number;
  last_result?: string;
  cycle?: number;
  schedule?: TenderLoopSchedule | null;
  added?: number;
  alerts?: number;
}

export interface TenderFollowUpEvent {
  id: string;
  key: string;
  kind: "go" | "deadline" | "relance" | string;
  title?: string;
  days?: number;
}

export interface TenderFileRef {
  file_id?: string;
  kind?: string;
  name?: string;
  excerpt?: string;
  extract?: string;
  path?: string;
  chars?: number;
  title?: string;
  client?: string;
  year?: string;
  country?: string;
  amount?: number | null;
}

export function asTemplateFiles(value?: TenderFileRef | TenderFileRef[] | null): TenderFileRef[] {
  if (!value) return [];
  if (Array.isArray(value)) {
    return value.filter((row) => row && (row.file_id || row.name || row.title));
  }
  return value.file_id || value.name || value.title ? [value] : [];
}

export interface TenderSite {
  id?: string;
  kind?: "hq" | "branch" | string;
  name?: string;
  country?: string;
  address?: string;
  city?: string;
  headcount?: number;
}

export interface TenderPartner {
  name?: string;
  country?: string;
  role?: string;
}

export interface TenderCustomSource {
  id?: string;
  name?: string;
  url?: string;
  api?: string;
  country?: string;
  zone?: string;
  ingest?: string;
  has_key?: boolean;
}

export interface TenderProfile {
  name?: string;
  legal_name?: string;
  specialty?: string;
  strengths?: string[];
  country?: string;
  phone?: string;
  email?: string;
  website?: string;
  currency?: string;
  locale?: string;
  crafts?: string[];
  countries?: string[];
  languages?: string[];
  tender_types?: string[];
  project_types?: string[];
  partners?: TenderPartner[];
  sites?: TenderSite[];
  headcount?: number;
  min_budget?: number;
  max_budget?: number;
  min_deadline_days?: number;
  min_score?: number;
  archive_after_days?: number;
  delete_after_days?: number;
  send_mode?: "draft" | "approval" | "autonomous" | string;
  mail?: { gmail?: boolean; outlook?: boolean; watch?: boolean };
  channels?: {
    telegram?: boolean;
    whatsapp?: boolean;
    email?: boolean;
    teams?: boolean;
    slack?: boolean;
    telegram_to?: string;
    whatsapp_to?: string;
    email_to?: string;
    teams_to?: string;
    slack_to?: string;
  };
  source_ids?: string[];
  custom_sources?: TenderCustomSource[];
  enabled_sources?: string[];
  templates?: {
    word?: TenderFileRef | TenderFileRef[] | null;
    ppt?: TenderFileRef | TenderFileRef[] | null;
    reuse_slides?: TenderFileRef[];
  };
  references?: TenderFileRef[];
  documents?: Record<string, unknown>[];
  team?: Record<string, unknown>[];
  price_book?: Record<string, unknown>[];
  legal_clauses?: string;
  methodology?: string;
  wizard_complete?: boolean;
  wizard_step?: number;
  templates_skipped?: boolean;
  sites_skipped?: boolean;
  references_skipped?: boolean;
  channels_skipped?: boolean;
  has_sam_key?: boolean;
}

export interface TenderZoneGroup {
  zone: string;
  sources: TenderSource[];
}

export interface AlertReceipt {
  channel: string;
  destination?: string;
  status: "new" | "pending" | "sending" | "accepted" | "failed" | "uncertain" | "cancelled";
  message_id?: string;
  message_ids?: string[];
  acknowledged_at?: number;
  error?: string;
}

export interface AlertDeliveryResult {
  event_id?: string;
  event_type?: string;
  complete?: boolean;
  confirmed?: boolean;
  receipts?: Record<string, AlertReceipt>;
  error?: string;
}

export interface AlertDeliverySnapshot {
  events: (AlertDeliveryResult & { title: string; created_at: number })[];
  pending: number;
  failed: number;
  uncertain: number;
  accepted: number;
  error?: string;
}

export interface TenderDesk {
  profile: TenderProfile;
  wizard_ready?: boolean;
  catalog_by_zone?: TenderZoneGroup[];
  tenders: TenderNotice[];
  kpis: {
    open?: number;
    new?: number;
    qualified?: number;
    value?: number;
    weighted_value?: number;
    deadline_7d?: number;
    drafting?: number;
    submitted?: number;
    won?: number;
    lost?: number;
    win_rate?: number | null;
    headline?: string;
    by_stage?: Record<string, number>;
    countries?: Record<string, { count?: number; won?: number; value?: number }>;
  };
  sources: TenderSource[];
  catalog: TenderSource[];
  needs_catalog?: {
    tender_types?: { id: string; label: string; label_fr: string; keywords?: string; cpv?: string }[];
    domains?: { id: string; label: string; label_fr: string; keywords?: string; cpv?: string }[];
    project_types?: { id: string; label: string; label_fr: string; keywords?: string; cpv?: string }[];
  };
  queries: { country: string; lang: string; query: string }[];
  discoveries: { host: string; url?: string; hits?: number; added?: boolean; last_title?: string }[];
  journal: { kind?: string; text?: string; t?: number }[];
  stages: string[];
  send_modes: string[];
  channels?: Record<string, { ready?: boolean; enabled?: boolean; hint?: string; channel?: string }>;
  alert_deliveries?: AlertDeliverySnapshot;
  files?: Record<string, string>;
  book?: string;
  models?: { enabled?: boolean; routed?: number; tasks?: TenderModelRoute[] };
  follow_up?: { pending?: number; events?: TenderFollowUpEvent[] };
  loop?: TenderLoop;
  loop_tick?: { reason?: string; did_work?: boolean; phase?: string };
  collect?: { source_id: string; ok: boolean; detail?: string; count?: number; kind?: string }[];
  notify?: AlertDeliveryResult;
  watch?: { count?: number; sent?: AlertDeliveryResult; digest?: string; delivered?: boolean };
  crm?: { created?: number; updated?: number; total?: number };
  send?: { to?: string; subject?: string; via?: string };
  retention?: {
    archive_after_days?: number;
    delete_after_days?: number;
    favorites?: number;
    archived?: number;
  };
  deleted?: { id?: string; title?: string };
  file?: { file_id?: string; text?: string; excerpt?: string; name?: string; title?: string };
  download?: { file_id?: string; name?: string; mime?: string; data?: string; kind?: string; size?: number };
  stack?: {
    tool?: string;
    desk?: string;
    skills?: string[];
    tools?: string[];
    mcp?: {
      id?: string;
      name?: string;
      role?: string;
      docs?: string;
      recommended?: boolean;
      command?: string;
      login?: string[];
      official?: string[];
      tools?: string[];
      jobs?: string[];
      confirm?: string[];
      groups?: Record<string, string[]>;
    }[];
    connectors?: { id?: string; name?: string; live?: boolean; needs_key?: boolean }[];
    catalog?: number;
    rules?: string;
  };
}

function tendersUrl(action: string): string {
  return `/api/tenders?action=${encodeURIComponent(action)}${studioSessionQuery()}`;
}

export async function fetchTendersDesk(token: string): Promise<TenderDesk> {
  return apiRequest<TenderDesk>(tendersUrl("snapshot"), token, {
    headers: apiBodyHeaders("{}"),
  });
}

export async function postTenders(
  token: string,
  action: string,
  body: Record<string, unknown> = {},
): Promise<TenderDesk> {
  return apiRequest<TenderDesk>(tendersUrl(action), token, {
    headers: apiBodyHeaders(JSON.stringify(body)),
  });
}

export function triggerTenderDownload(pack: { name?: string; mime?: string; data?: string }): void {
  if (!pack.data || !pack.name) return;
  const binary = atob(pack.data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: pack.mime || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = pack.name;
  link.click();
  URL.revokeObjectURL(url);
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      const comma = text.indexOf(",");
      resolve(comma >= 0 ? text.slice(comma + 1) : text);
    };
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}
