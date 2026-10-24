/** SEO desk: GET /api/seo?action=..., JSON bodies as chunked headers. */
import { apiBodyHeaders, apiRequest } from "@/lib/api";

export type SeoSeverity = "critical" | "high" | "medium" | "low" | "info";

export interface SeoFinding {
  code: string;
  category: string;
  severity: SeoSeverity;
  title: string;
  message: string;
  url?: string;
  recommendation: string;
  confidence?: string;
  evidence: Array<{ kind: string; value: unknown; source: string }>;
}

export interface SeoHealth {
  score: number;
  grade: string;
  page_count: number;
  finding_count: number;
  counts: Partial<Record<SeoSeverity, number>>;
  penalties: Record<string, number>;
}

export interface SeoPerformanceBlock {
  status: "ok" | "data_gap" | string;
  service: string;
  reason?: string;
  performance_score?: number | null;
  metrics?: Record<string, unknown> | null;
}

export interface SeoReport {
  health: SeoHealth;
  findings: SeoFinding[];
  pages: Array<{ url: string; final_url?: string; status?: number; title?: string; meta_description?: string; h1?: string[]; canonical?: string; error?: string }>;
  data_gaps: Array<{ service: string; reason: string }>;
  metadata: { url?: string; origin?: string; performance?: { psi?: SeoPerformanceBlock; crux?: SeoPerformanceBlock } };
}

export interface SeoAuditEntry {
  id: string;
  at: string;
  url: string;
  score: number;
  grade: string;
  findings: number;
  counts: Partial<Record<SeoSeverity, number>>;
  pages: number;
  performance_score: number | null;
  duration_s: number;
}

export interface SeoSite {
  site: string;
  url: string;
  audits: SeoAuditEntry[];
  last_audit_id: string;
  last_audit_at: string;
  last_score: number;
  last_grade: string;
}

export interface SeoKeyState {
  configured: boolean;
  source: "settings" | "environment" | "";
  hint: string;
}

export type SeoKeyName = "psi_api_key" | "crux_api_key" | "dataforseo_login" | "dataforseo_password" | "semrush_api_key";

export type SeoKeys = Record<SeoKeyName, SeoKeyState> & { psi_ready: boolean; crux_ready: boolean; serp_ready: boolean };

export interface SeoSnapshot {
  sites: SeoSite[];
  keys: SeoKeys;
  serp_keywords: string[];
  limits: { max_pages: number; max_depth: number };
}

export interface SerpSnapshot {
  captured_at: string;
  keyword: string;
  location: string;
  language: string;
  source: string;
  results: Array<{ position: number; url: string; title: string; result_type: string }>;
}

export interface DeskFile {
  filename: string;
  mime: string;
  content: string;
}

export function seoUrl(action: string): string {
  return `/api/seo?action=${encodeURIComponent(action)}`;
}

export function seoCall<T>(token: string, action: string, body: Record<string, unknown> = {}): Promise<T> {
  return apiRequest<T>(seoUrl(action), token, { headers: apiBodyHeaders(JSON.stringify(body)) });
}

export const fetchSeoSnapshot = (token: string) => seoCall<SeoSnapshot>(token, "snapshot");
export const runSeoAudit = (token: string, url: string, maxPages?: number) =>
  seoCall<{ site: string; audit_id: string; entry: SeoAuditEntry; report: SeoReport }>(token, "pipeline", {
    url,
    ...(maxPages ? { max_pages: maxPages } : {}),
  });
export const fetchSeoAudit = (token: string, site: string, auditId?: string) =>
  seoCall<{ site: string; audit_id: string; report: SeoReport }>(token, "audit.get", { site, audit_id: auditId });
export const fetchSeoHistory = (token: string, site: string) =>
  seoCall<{ site: string; audits: SeoAuditEntry[] }>(token, "history", { site });
export const exportSeoReport = (token: string, site: string, format: "html" | "md" | "json", auditId?: string) =>
  seoCall<DeskFile>(token, "report", { site, format, audit_id: auditId });
export const deleteSeoSite = (token: string, site: string) => seoCall<{ deleted: string }>(token, "site.delete", { site });
export const saveSeoKeys = (token: string, values: Partial<Record<SeoKeyName, string>>, clear: SeoKeyName[] = []) =>
  seoCall<{ keys: SeoKeys }>(token, "keys.save", { ...values, clear });
export const takeSerpSnapshot = (token: string, keyword: string, location?: string, language?: string) =>
  seoCall<{ status: string; snapshot?: SerpSnapshot; reason?: string }>(token, "serp_snapshot", {
    keyword,
    location,
    language,
  });
export const fetchSerpHistory = (token: string, keyword?: string) =>
  seoCall<{ snapshots: SerpSnapshot[] }>(token, "serp_history", { keyword });

/** Save a desk file (report, export) through the browser. */
export function downloadDeskFile(file: DeskFile): void {
  const blob = new Blob([file.content], { type: `${file.mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
