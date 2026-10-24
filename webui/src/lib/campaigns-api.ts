// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Campaigns Studio client: one function per gateway action (see campaigns-types.ts). */

import { apiBodyHeaders, apiRequest } from "@/lib/api";
import type {
  Attachment,
  Campaign,
  CampaignDashboard,
  CampaignPreview,
  CampaignSettings,
  CampaignStatus,
  Contact,
  ContactList,
  CrmFilters,
  CrmKind,
  CrmPreview,
  EnrichResult,
  ImportPreview,
  ImportResult,
  Paged,
  ProspectCriteria,
  ProspectProviders,
  ProspectSearch,
  Reply,
  ReplyClass,
  ReplyStatus,
  Send,
  SendStatus,
  Template,
  WhatsAppTemplate,
} from "@/lib/campaigns-types";

export const CAMPAIGNS_DESK_HASH = "#/campaigns";

type Query = Record<string, string | number | undefined | null>;

/** `/api/campaigns?action=...` with the non-empty query values appended. */
export function campaignsUrl(action: string, query: Query = {}): string {
  const params = new URLSearchParams({ action });
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  return `/api/campaigns?${params.toString()}`;
}

/** Every call sends a body (possibly `{}`): the gateway reads it from headers. */
export function campaignsCall<T>(
  token: string,
  action: string,
  body: unknown = {},
  query: Query = {},
): Promise<T> {
  return apiRequest<T>(campaignsUrl(action, query), token, {
    headers: apiBodyHeaders(JSON.stringify(body ?? {})),
  });
}

// -- Dashboard and loop -------------------------------------------------------

export const fetchCampaignDashboard = (token: string) =>
  campaignsCall<CampaignDashboard>(token, "dashboard");

export const runCampaignLoop = (token: string) =>
  campaignsCall<CampaignDashboard["loop"]>(token, "loop.run");

// -- Contacts -----------------------------------------------------------------

export interface ContactQuery {
  q?: string;
  list_id?: string;
  status?: string;
  offset?: number;
  limit?: number;
}

export const listContacts = (token: string, query: ContactQuery = {}) =>
  campaignsCall<Paged<Contact>>(token, "contacts.list", {}, { ...query });

export const saveContact = (token: string, contact: Partial<Contact>) =>
  campaignsCall<Contact>(token, "contacts.save", contact);

export const deleteContacts = (token: string, ids: string[]) =>
  campaignsCall<{ deleted: number }>(token, "contacts.delete", { ids });

export const exportContacts = (token: string, listId = "") =>
  campaignsCall<{ csv: string; filename: string }>(token, "contacts.export", {}, { list_id: listId });

// -- Import -------------------------------------------------------------------

export const previewImport = (token: string, filename: string, dataB64: string) =>
  campaignsCall<ImportPreview>(token, "import.preview", { filename, data_b64: dataB64 });

export interface ImportCommit {
  import_id: string;
  mapping: Record<string, string>;
  list_id?: string;
  new_list_name?: string;
  tags?: string[];
}

export const commitImport = (token: string, body: ImportCommit) =>
  campaignsCall<ImportResult>(token, "import.commit", body);

export const importFromLeads = (token: string, body: { list_id?: string; new_list_name?: string }) =>
  campaignsCall<ImportResult>(token, "import.leads", body);

// -- CRM audiences (per project: the active chat's session key) ----------------

export const previewCrmAudience = (token: string, sessionKey: string, kinds: CrmKind[], filters: CrmFilters) =>
  campaignsCall<CrmPreview>(token, "crm.preview", { kinds, filters }, { session_key: sessionKey });

export const importCrmAudience = (
  token: string,
  sessionKey: string,
  body: { kinds: CrmKind[]; filters: CrmFilters; list_id?: string; new_list_name?: string; tags?: string[] },
) => campaignsCall<ImportResult>(token, "crm.import", body, { session_key: sessionKey });

// -- Lists --------------------------------------------------------------------

// -- Prospects and enrichment (Leads desk engine and keys) ---------------------

export const fetchProspectProviders = (token: string) =>
  campaignsCall<ProspectProviders>(token, "prospects.providers");

export const searchProspects = (token: string, criteria: ProspectCriteria) =>
  campaignsCall<ProspectSearch>(token, "prospects.search", { criteria });

export interface ProspectImport {
  search_id: string;
  /** Row indexes to import; omitted = every row. */
  indexes?: number[];
  only_with_email?: boolean;
  list_id?: string;
  new_list_name?: string;
  tags?: string[];
}

export const importProspects = (token: string, body: ProspectImport) =>
  campaignsCall<ImportResult>(token, "prospects.import", body);

export interface EnrichOptions {
  find_email: boolean;
  verify: boolean;
  company: boolean;
}

export const enrichContacts = (token: string, ids: string[], options: EnrichOptions) =>
  campaignsCall<EnrichResult>(token, "contacts.enrich", { ids, ...options });

export const listLists = (token: string) => campaignsCall<ContactList[]>(token, "lists.list");

export const saveList = (token: string, list: Partial<ContactList>) =>
  campaignsCall<ContactList>(token, "lists.save", list);

export const deleteList = (token: string, id: string) =>
  campaignsCall<{ deleted: number }>(token, "lists.delete", { id });

export const addToList = (token: string, listId: string, contactIds: string[]) =>
  campaignsCall<ContactList>(token, "lists.add", { list_id: listId, contact_ids: contactIds });

export const removeFromList = (token: string, listId: string, contactIds: string[]) =>
  campaignsCall<ContactList>(token, "lists.remove", { list_id: listId, contact_ids: contactIds });

// -- Templates ----------------------------------------------------------------

export const listTemplates = (token: string) => campaignsCall<Template[]>(token, "templates.list");

export const saveTemplate = (token: string, template: Partial<Template>) =>
  campaignsCall<Template>(token, "templates.save", template);

export const deleteTemplate = (token: string, id: string) =>
  campaignsCall<{ deleted: number }>(token, "templates.delete", { id });

// -- Campaigns ----------------------------------------------------------------

export const listCampaigns = (token: string, status: CampaignStatus | "" = "") =>
  campaignsCall<Campaign[]>(token, "campaigns.list", {}, { status });

export const getCampaign = (token: string, id: string) =>
  campaignsCall<Campaign>(token, "campaigns.get", {}, { id });

export const saveCampaign = (token: string, campaign: Partial<Campaign>) =>
  campaignsCall<Campaign>(token, "campaigns.save", campaign);

export const deleteCampaign = (token: string, id: string) =>
  campaignsCall<{ deleted: number }>(token, "campaigns.delete", { id });

export const duplicateCampaign = (token: string, id: string) =>
  campaignsCall<Campaign>(token, "campaigns.duplicate", { id });

export const previewCampaign = (token: string, campaign: Partial<Campaign>, contactId = "") =>
  campaignsCall<CampaignPreview>(token, "campaigns.preview", {
    campaign,
    ...(contactId ? { contact_id: contactId } : {}),
  });

export const testCampaign = (token: string, campaign: Partial<Campaign>, to: string[]) =>
  campaignsCall<{ sent: string[] }>(token, "campaigns.test", { campaign, to });

/** `scheduleAt` empty = send now. */
export const scheduleCampaign = (token: string, id: string, scheduleAt: string) =>
  campaignsCall<Campaign>(token, "campaigns.schedule", { id, schedule_at: scheduleAt });

export const pauseCampaign = (token: string, id: string) =>
  campaignsCall<Campaign>(token, "campaigns.pause", { id });

export const resumeCampaign = (token: string, id: string) =>
  campaignsCall<Campaign>(token, "campaigns.resume", { id });

export const cancelCampaign = (token: string, id: string) =>
  campaignsCall<Campaign>(token, "campaigns.cancel", { id });

export const listSends = (
  token: string,
  id: string,
  query: { status?: SendStatus | ""; offset?: number; limit?: number } = {},
) => campaignsCall<Paged<Send>>(token, "campaigns.sends", {}, { id, ...query });

// -- Attachments --------------------------------------------------------------

export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

export const uploadAttachment = (token: string, filename: string, dataB64: string) =>
  campaignsCall<Attachment>(token, "attachments.upload", { filename, data_b64: dataB64 });

/** A browser File as plain base64 (no data: prefix). */
export function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

// -- Replies ------------------------------------------------------------------

export interface ReplyQuery {
  status?: ReplyStatus | "";
  classification?: ReplyClass | "";
  campaign_id?: string;
  offset?: number;
  limit?: number;
}

export const listReplies = (token: string, query: ReplyQuery = {}) =>
  campaignsCall<Paged<Reply>>(token, "replies.list", {}, { ...query });

export const updateReply = (
  token: string,
  patch: Partial<Pick<Reply, "classification" | "status" | "assigned_to" | "handoff_brief" | "draft_reply">> & {
    id: string;
  },
) => campaignsCall<Reply>(token, "replies.update", patch);

export const sendReply = (token: string, id: string, body: string, subject = "") =>
  campaignsCall<Reply>(token, "replies.send", { id, body, ...(subject ? { subject } : {}) });

// -- Settings and WhatsApp ----------------------------------------------------

export const fetchCampaignSettings = (token: string) =>
  campaignsCall<CampaignSettings>(token, "settings.get");

export const saveCampaignSettings = (token: string, settings: Partial<CampaignSettings>) =>
  campaignsCall<CampaignSettings>(token, "settings.save", settings);

export const testWhatsApp = (token: string, to: string) =>
  campaignsCall<{ ok: boolean; detail: string }>(token, "whatsapp.test", { to });

export const listWhatsAppTemplates = (token: string) =>
  campaignsCall<WhatsAppTemplate[]>(token, "whatsapp.templates");

/** Save the CSV text the gateway returned as a file download. */
export function downloadText(filename: string, text: string, mime = "text/csv;charset=utf-8"): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "export.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
