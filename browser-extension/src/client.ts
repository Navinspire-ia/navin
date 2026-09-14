// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { extensionServerAddress } from "../../webui/src/lib/browser-extension-address";

export type RecordKind = "mission" | "job" | "candidate" | "lead" | "tender";
export interface ImportRecord {
  request_id: string; kind: RecordKind; title: string; name: string; headline: string; company: string;
  description: string; country: string; location: string; currency: string; remote: string; posted_at: string;
  deadline: string; need_type: string; skills: string[]; email: string; phone: string; website: string;
  availability: string; daily_rate_min: number | null; daily_rate_max: number | null; budget_min: number | null;
  budget_max: number | null; salary_min: number | null; salary_max: number | null; url: string; capture_mode: string;
  reviewed?: boolean;
  detected_kind?: RecordKind;
}
export interface Capture { records?: ImportRecord[]; error?: string; linkedin?: boolean; unstructured?: boolean; truncated?: boolean; profile_list?: boolean }
export interface Criteria {
  mode?: string; roles?: string[]; skills?: string[]; countries?: string[]; city?: string;
  currency?: string; buy_rate_max?: number; sale_rate_remote?: number; sale_rate_onsite?: number;
  min_score?: number; work_mode?: string; track?: string;
  max_age_days?: number; salary_max?: number;
}
export interface AnalysisResult {
  request_id: string; record?: ImportRecord; status: "recommended" | "review" | "excluded" | "duplicate" | "invalid";
  score?: number | null; reasons?: Array<{ key: string; status: string; detail: string }>; error?: string;
  destination?: string;
}
export interface Analysis {
  criteria: Criteria; profiles: Criteria; revision: string; configured: boolean; results?: AnalysisResult[];
}
export interface Connection { base: string; token: string; label: string }
// Privileged APIs used inside extension pages, isolated from the host page.
interface ExtensionAPI {
  storage: { local: { get(key: string | string[]): Promise<{ connection?: Connection; destination?: string }>; set(value: Record<string, unknown>): Promise<void>; remove(key: string): Promise<void> };
    session: { get(key: string): Promise<Record<string, Capture>>; set(value: Record<string, unknown>): Promise<void>; remove(key: string): Promise<void> } };
  permissions: { request(permissions: { origins: string[] }): Promise<boolean>; remove(permissions: { origins: string[] }): Promise<boolean> };
}
export const api = ((globalThis as unknown as { browser?: ExtensionAPI; chrome?: ExtensionAPI }).browser
  || (globalThis as unknown as { chrome: ExtensionAPI }).chrome);

export function navinBase(raw: string): string {
  return extensionServerAddress(raw);
}

export function bodyHeaders(body: unknown): Record<string, string> {
  const bytes = new TextEncoder().encode(JSON.stringify(body));
  if (bytes.length > 1000000) throw new Error("L'envoi dépasse 1 Mo. Envoyez moins de fiches à la fois.");
  let binary = "";
  for (let index = 0; index < bytes.length; index += 8192) binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
  const encoded = btoa(binary), headers: Record<string, string> = {};
  for (let index = 0; index < encoded.length; index += 6000) headers[`X-Navin-File-Body-${index / 6000}`] = encoded.slice(index, index + 6000);
  return headers;
}

export async function request(base: string, token: string, action: string, body = {}): Promise<Record<string, unknown>> {
  const response = await fetch(`${navinBase(base)}/api/browser-bridge?action=${encodeURIComponent(action)}`, {
    headers: { ...bodyHeaders(body), ...(token ? { "X-Navin-Browser-Token": token } : {}) },
    credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", signal: AbortSignal.timeout(25000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(String(result.error || `Navin : erreur ${response.status}`));
  return result;
}

export function blankRecord(): ImportRecord {
  return { request_id: crypto.randomUUID(), kind: "mission", title: "", name: "", headline: "", company: "", description: "", country: "", location: "", currency: "", remote: "", posted_at: "", deadline: "", need_type: "", skills: [], email: "", phone: "", website: "", availability: "", daily_rate_min: null, daily_rate_max: null, budget_min: null, budget_max: null, salary_min: null, salary_max: null, url: "", capture_mode: "manual" };
}

export function routeRecords(rows: ImportRecord[], destination: string): ImportRecord[] {
  return rows.map(row => ({ ...row, detected_kind: row.detected_kind || row.kind,
    kind: destination === "leads" ? "lead" : row.detected_kind || row.kind }));
}

// Auto-discovery: probe the loopback ports the Navin gateway uses by default.
// Any JSON answer (even 401) identifies a Navin instance; other services on the
// port either do not answer this path or do not return JSON.
export async function discoverLocalNavin(): Promise<string> {
  const candidates: string[] = [];
  for (const host of ["127.0.0.1", "localhost"]) for (const port of [8765, 8766, 8767, 8768, 8769, 8770]) candidates.push(`http://${host}:${port}`);
  for (const base of candidates) {
    try {
      const response = await fetch(`${base}/api/browser-bridge?action=status`, {
        headers: bodyHeaders({}), credentials: "omit", cache: "no-store", redirect: "error",
        referrerPolicy: "no-referrer", signal: AbortSignal.timeout(1500),
      });
      await response.json();
      return base;
    } catch { /* Not Navin or not reachable: try the next candidate. */ }
  }
  return "";
}

export async function autoPair(base: string, label: string): Promise<Connection> {
  const result = await request(base, "", "pair", { label });
  const device = result.device as { label: string };
  return { base, token: String(result.token), label: device.label || label };
}
