/** Ads desk: GET /api/ads?action=..., JSON bodies as chunked headers. */
import { apiBodyHeaders, apiRequest } from "@/lib/api";
import type { DeskFile } from "@/lib/seo-desk-api";

export interface AdsExport {
  id: string;
  filename: string;
  platform: string;
  rows: number;
  levels: string[];
  unknown_columns: string[];
  warnings: string[];
  uploaded_at: string;
  active: boolean;
}

export interface AdsMetrics {
  impressions: number;
  clicks: number;
  cost: number;
  conversions: number;
  conversion_value: number;
  ctr?: number | null;
  cpc?: number | null;
  cpm?: number | null;
  cpa?: number | null;
  cvr?: number | null;
  roas?: number | null;
  spend_share?: number | null;
}

export interface AdsEntity {
  level: string;
  key: string;
  platform: string;
  campaign?: string;
  ad_group?: string;
  ad?: string;
  keyword?: string;
  search_term?: string;
  status?: string;
  metrics: AdsMetrics;
}

export interface AdsFinding {
  code: string;
  category: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  title: string;
  message: string;
  platform: string;
  level: string;
  entity: Record<string, string>;
  impact: Record<string, number>;
  recommendation: string;
  change_ids: string[];
}

export interface AdsHealth {
  score: number;
  grade: string;
  finding_count: number;
  change_count: number;
  wasted_cost: number;
  waste_share: number;
  estimated_monthly_savings: number;
  counts: Record<string, number>;
}

export interface AdsAnalysis {
  platforms: string[];
  currency: string;
  period: Record<string, unknown>;
  row_count: number;
  totals: AdsMetrics;
  entities: Record<string, { count: number; top_by_spend: AdsEntity[] }>;
  findings: AdsFinding[];
  data_gaps: Array<{ service: string; reason: string }>;
  health: AdsHealth;
  summary: string[];
  analyzed_at: string;
  export_ids: string[];
}

export type AdsChangeStatus = "proposed" | "approved" | "rejected" | "applied";

export interface AdsChange {
  id: string;
  platform: string;
  action: string;
  level: string;
  target: Record<string, string>;
  params: Record<string, unknown>;
  rationale: string;
  finding_code: string;
  estimated_monthly_savings?: number | null;
  status: AdsChangeStatus;
  note?: string;
  applied_proof?: string;
  updated_at?: string;
}

export interface AdsChangesPayload {
  changes: AdsChange[];
  count: number;
  by_status: Partial<Record<AdsChangeStatus, number>>;
}

export interface AdsSnapshot {
  exports: AdsExport[];
  analysis: AdsAnalysis | null;
  changes: { count: number; by_status: Partial<Record<AdsChangeStatus, number>> };
  settings: { currency: string; monthly_budget: number | null };
}

export function adsUrl(action: string): string {
  return `/api/ads?action=${encodeURIComponent(action)}`;
}

export function adsCall<T>(token: string, action: string, body: Record<string, unknown> = {}): Promise<T> {
  return apiRequest<T>(adsUrl(action), token, { headers: apiBodyHeaders(JSON.stringify(body)) });
}

export const fetchAdsSnapshot = (token: string) => adsCall<AdsSnapshot>(token, "snapshot");
export const ingestAdsExport = (token: string, filename: string, dataB64: string, platform = "auto") =>
  adsCall<{ export: AdsExport }>(token, "ingest", { filename, data_b64: dataB64, platform });
export const deleteAdsExport = (token: string, id: string) => adsCall<{ deleted: string }>(token, "exports.delete", { id });
export const toggleAdsExport = (token: string, id: string, active: boolean) =>
  adsCall<{ export: AdsExport }>(token, "exports.toggle", { id, active });
export const analyzeAds = (token: string, body: { currency?: string; monthly_budget?: number | null; export_ids?: string[] }) =>
  adsCall<{ analysis: AdsAnalysis; changes: AdsSnapshot["changes"] }>(token, "analyze", body);
export const fetchAdsChanges = (token: string, status?: AdsChangeStatus) =>
  adsCall<AdsChangesPayload>(token, "changes.list", status ? { status } : {});
export const setAdsChangeStatus = (
  token: string,
  verb: "approve" | "reject" | "mark_applied" | "reset",
  ids: string[],
  extra: { note?: string; proof?: string } = {},
) => adsCall<AdsChangesPayload>(token, `changes.${verb}`, { ids, ...extra });
export const exportAdsChanges = (token: string, format: "csv" | "google_editor" | "microsoft_bulk", ids?: string[]) =>
  adsCall<DeskFile & { rows: number }>(token, "export_changes", { format, ...(ids?.length ? { ids } : {}) });
export const exportAdsReport = (token: string, format: "html" | "md" | "json") =>
  adsCall<DeskFile>(token, "report", { format });

/** Read a dropped file as base64 (no data: prefix). */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.slice(value.indexOf(",") + 1) : value);
    };
    reader.readAsDataURL(file);
  });
}
