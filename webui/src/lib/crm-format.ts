// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { format } from "date-fns";
import { enUS, fr } from "date-fns/locale";

import type { CrmKind, CrmRecord } from "@/lib/api";

export type CrmEditorKind = Extract<
  CrmKind,
  "contacts" | "companies" | "leads" | "opportunities" | "activities" | "products"
>;

export const CRM_TABS = [
  "dashboard",
  "contacts",
  "companies",
  "leads",
  "opportunities",
  "activities",
  "products",
] as const;

export type CrmTab = (typeof CRM_TABS)[number];

export const OPP_STAGES = ["nouveau", "qualifie", "proposition", "negociation", "gagne", "perdu"] as const;
export const LEAD_STAGES = ["nouveau", "contacte", "qualifie", "converti", "perdu"] as const;
export const CONTACT_STATUSES = ["actif", "inactif"] as const;
export const ACTIVITY_KINDS = [
  "appel",
  "email",
  "whatsapp",
  "reunion",
  "tache",
  "note",
  "document",
  "teams",
] as const;

export function money(value: number, currency = "EUR", locale = "fr-FR") {
  const amount = Number.isFinite(value) ? value : 0;
  try {
    return new Intl.NumberFormat(locale || "fr-FR", {
      style: "currency",
      currency: currency || "EUR",
      maximumFractionDigits: 2,
      minimumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${new Intl.NumberFormat(locale || "fr-FR").format(amount)} ${currency || "EUR"}`;
  }
}

export function displayText(value: unknown): string {
  if (value == null || value === false) return "";
  const text = String(value).trim();
  if (!text || text === "null" || text === "undefined") return "";
  return text;
}

export function contactName(row: CrmRecord | undefined) {
  if (!row) return "";
  const full = `${displayText(row.firstName)} ${displayText(row.lastName)}`.trim();
  return full || displayText(row.name) || displayText(row.email) || displayText(row.id);
}

export function companyName(row: CrmRecord | undefined) {
  if (!row) return "";
  return displayText(row.name) || displayText(row.legalName) || displayText(row.company) || "";
}

export function opportunityName(row: CrmRecord | undefined) {
  if (!row) return "";
  return displayText(row.name) || displayText(row.title) || "";
}

export function leadName(row: CrmRecord | undefined) {
  if (!row) return "";
  return displayText(row.name) || contactName(row);
}

export function linkedName(
  rows: CrmRecord[] | undefined,
  id: unknown,
  nameOf: (row: CrmRecord) => string,
) {
  const key = displayText(id);
  if (!key) return "";
  const row = (rows || []).find((item) => item.id === key);
  return row ? nameOf(row) : "";
}

export function tabFromHash(): CrmTab {
  if (typeof window === "undefined") return "dashboard";
  const raw = window.location.hash.replace(/^#/, "");
  const path = raw.split("?")[0] || "";
  const parts = path.split("/").filter(Boolean);
  const sub = (parts[1] || "") as CrmTab;
  return CRM_TABS.includes(sub) ? sub : "dashboard";
}

export function queryFromHash(): URLSearchParams {
  if (typeof window === "undefined") return new URLSearchParams();
  const raw = window.location.hash.replace(/^#/, "");
  return new URLSearchParams(raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "");
}

export function crmHash(
  tab: CrmTab,
  params: URLSearchParams,
  currentQuery?: URLSearchParams,
): string {
  const clean = new URLSearchParams(params);
  for (const [key, value] of [...clean.entries()]) {
    if (!value) clean.delete(key);
  }
  const chat = currentQuery?.get("chat")?.trim();
  if (chat && !clean.has("chat")) clean.set("chat", chat);
  const path = tab === "dashboard" ? "/crm" : `/crm/${tab}`;
  return `#${path}${clean.toString() ? `?${clean}` : ""}`;
}

export function writeCrmHash(tab: CrmTab, params: URLSearchParams) {
  if (typeof window === "undefined") return;
  const next = crmHash(tab, params, queryFromHash());
  if (window.location.hash !== next) {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${next}`,
    );
  }
}

export function dayStart(date: Date) {
  return Math.floor(new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() / 1000);
}

export function unixToLocal(unix?: number | string | null): string {
  const value = Number(unix || 0);
  if (!value) return "";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function localToUnix(value: string): number {
  if (!value.trim()) return 0;
  const stamp = new Date(value).getTime();
  return Number.isFinite(stamp) ? Math.floor(stamp / 1000) : 0;
}

export function formatWhen(unix?: number | string | null, locale = "fr-FR"): string {
  const value = Number(unix || 0);
  if (!value) return "";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "";
  try {
    return format(date, "PPp", {
      locale: String(locale || "").toLowerCase().startsWith("fr") ? fr : enUS,
    });
  } catch {
    return date.toLocaleString(locale || "fr-FR", {
      dateStyle: "short",
      timeStyle: "short",
    });
  }
}
