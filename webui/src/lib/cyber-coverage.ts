// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Coverage matrix of the Navin Cyber AppSec referential (302 controls, 24
 * categories): types, client for `/api/webui/security-audit`, and the pure
 * helpers the Cyber coverage panel uses (grouping, filtering, formatting).
 */

import { apiRequest } from "@/lib/api";

export type ControlStatus =
  | "passed"
  | "failed"
  | "inconclusive"
  | "blocked"
  | "not_tested"
  | "not_applicable";

export const CONTROL_STATUSES: readonly ControlStatus[] = [
  "passed",
  "failed",
  "inconclusive",
  "blocked",
  "not_tested",
  "not_applicable",
];

export interface CoverageTool {
  name: string;
  version: string;
  status: string;
}

export interface CoverageControl {
  id: string;
  category: string;
  task: string;
  applicability: string;
  applicability_detected: boolean;
  verification_mode: "read_only" | "hybrid" | "staging_active";
  status: ControlStatus;
  capability: "automated" | "partial" | "manual" | "missing";
  manual_review: boolean;
  executed_at: string;
  tools: CoverageTool[];
  target: string;
  environment: string;
  evidence_refs: string[];
  findings: string[];
  findings_count: number;
  confidence: string;
  risk: "critical" | "high" | "medium" | "low" | "none";
  remediation: string;
  reason: string;
}

export interface CoverageCategory {
  id: string;
  number: number;
  name: { fr: string; en: string; ar: string };
  applicability: string;
  verification_mode: string;
  total: number;
  applicable: number;
  passed: number;
  failed: number;
  inconclusive: number;
  blocked: number;
  not_tested: number;
  not_applicable: number;
}

export interface CoverageSummary {
  total: number;
  applicable: number;
  not_applicable: number;
  passed: number;
  failed: number;
  inconclusive: number;
  blocked: number;
  not_tested: number;
  verified: number;
  verified_coverage: number;
  manual_review: number;
  audit_complete: boolean;
  complete_blockers: number;
}

export interface MatrixPayload {
  referential: { id: string; version: string; title: string; controls: number; categories: number };
  audit_id: string;
  audited_at: string;
  root: string;
  levels_reached: string[];
  summary: CoverageSummary;
  applicability: Array<{ tag: string; detected: boolean; reason: string }>;
  categories: CoverageCategory[];
  controls: CoverageControl[];
  complete_blockers: string[];
  warning: string;
}

export interface MatrixExport {
  filename: string;
  content: string;
  mime: string;
}

export interface RunOptions {
  scanners: boolean;
  offline: boolean;
  baseUrl?: string;
  databaseEphemeral?: boolean;
}

const READ_TIMEOUT_MS = 60_000;
/** A full audit runs scanners and can take several minutes. */
const RUN_TIMEOUT_MS = 30 * 60_000;

export function securityAuditUrl(action: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams({ action });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  return `/api/webui/security-audit?${query.toString()}`;
}

export async function fetchCoverageMatrix(token: string, path: string): Promise<MatrixPayload | null> {
  const res = await apiRequest<{ matrix: MatrixPayload | null }>(
    securityAuditUrl("matrix", { path }),
    token,
    undefined,
    READ_TIMEOUT_MS,
  );
  return res?.matrix ?? null;
}

export async function runCoverageAudit(
  token: string,
  path: string,
  options: RunOptions,
): Promise<MatrixPayload> {
  const res = await apiRequest<{ matrix: MatrixPayload }>(
    securityAuditUrl("run", {
      path,
      scanners: String(options.scanners),
      offline: String(options.offline),
      base_url: options.baseUrl?.trim() || undefined,
      database_ephemeral: options.databaseEphemeral ? "true" : undefined,
    }),
    token,
    undefined,
    RUN_TIMEOUT_MS,
  );
  return res.matrix;
}

export async function exportCoverageMatrix(
  token: string,
  path: string,
  format: "json" | "md",
): Promise<MatrixExport> {
  return apiRequest<MatrixExport>(
    securityAuditUrl("matrix_export", { path, format }),
    token,
    undefined,
    READ_TIMEOUT_MS,
  );
}

/** "87.5 %" style with one decimal, safe on missing values. */
export function formatPercent(value: number | null | undefined, locale?: string): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  const clamped = Math.max(0, Math.min(100, n));
  return `${clamped.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}

/** Category display name: fr/en/ar from the payload, English otherwise. */
export function categoryName(category: Pick<CoverageCategory, "name" | "id">, lang: string): string {
  const base = (lang || "en").slice(0, 2).toLowerCase();
  const key = base === "fr" || base === "ar" ? base : "en";
  return category.name?.[key] || category.name?.en || category.id;
}

export interface ControlFilter {
  query?: string;
  category?: string;
  statuses?: ReadonlySet<ControlStatus>;
}

export function filterControls(controls: CoverageControl[], filter: ControlFilter): CoverageControl[] {
  const query = (filter.query ?? "").trim().toLowerCase();
  return controls.filter((control) => {
    if (filter.category && control.category !== filter.category) return false;
    if (filter.statuses && filter.statuses.size > 0 && !filter.statuses.has(control.status)) return false;
    if (!query) return true;
    const haystack = [control.id, control.task, control.reason, ...control.tools.map((tool) => tool.name)]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

/** Categories in referential order, each with its (already filtered) controls. */
export function groupByCategory(
  categories: CoverageCategory[],
  controls: CoverageControl[],
): Array<{ category: CoverageCategory; controls: CoverageControl[] }> {
  const byId = new Map<string, CoverageControl[]>();
  for (const control of controls) {
    const list = byId.get(control.category) ?? [];
    list.push(control);
    byId.set(control.category, list);
  }
  return [...categories]
    .sort((a, b) => a.number - b.number)
    .map((category) => ({ category, controls: byId.get(category.id) ?? [] }));
}

/** Filename for an export: the server's name, sanitized, with a fallback. */
export function exportFilename(serverName: string | undefined, auditId: string, format: "json" | "md"): string {
  const cleaned = (serverName ?? "").replace(/[\\/:*?"<>|]+/g, "_").trim();
  if (cleaned) return cleaned;
  const id = (auditId || "audit").replace(/[^A-Za-z0-9._-]+/g, "_");
  return `navin-cyber-coverage-${id}.${format}`;
}

/** mm:ss elapsed time for the running state. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
