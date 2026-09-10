// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { leadHasBuyingSignal, leadHasDueStep, leadInOutreach, type LeadRow } from "@/lib/leads-api";

/** Facet key for rows whose value is missing. */
export const NONE_FACET = "__none__";

export type LeadBucket = "all" | "strong" | "email" | "verified" | "pipeline" | "due" | "signals";
export const LEAD_BUCKETS: LeadBucket[] = ["all", "strong", "email", "verified", "due", "signals", "pipeline"];

export type LeadEmailState = "any" | "has" | "verified" | "none";
export const SCORE_FLOORS = ["80", "55", "30"] as const;

export type LeadFacetFilter = {
  countries: string[];
  sources: string[];
  sectors: string[];
  stages: string[];
  /** "" or one of SCORE_FLOORS (any number works). */
  minScore: string;
  email: LeadEmailState;
};

export type LeadFacets = {
  countries: string[];
  sources: string[];
  sectors: string[];
  stages: string[];
};

export function emptyLeadFilter(): LeadFacetFilter {
  return { countries: [], sources: [], sectors: [], stages: [], minScore: "", email: "any" };
}

export function leadFilterActive(filter: LeadFacetFilter): boolean {
  return leadFilterCount(filter) > 0;
}

export function leadFilterCount(filter: LeadFacetFilter): number {
  return (
    filter.countries.length +
    filter.sources.length +
    filter.sectors.length +
    filter.stages.length +
    (filter.minScore ? 1 : 0) +
    (filter.email !== "any" ? 1 : 0)
  );
}

export function isLiveLead(row: LeadRow): boolean {
  return !row.archived && !row.extra?.archived;
}

export function bucketRows(rows: LeadRow[], bucket: LeadBucket): LeadRow[] {
  const live = rows.filter(isLiveLead);
  if (bucket === "strong") return live.filter((row) => (row.score || 0) >= 80);
  if (bucket === "email") return live.filter((row) => Boolean(row.email));
  if (bucket === "verified") return live.filter((row) => row.email_status === "verified");
  if (bucket === "due") return live.filter((row) => leadHasDueStep(row));
  if (bucket === "signals") return live.filter((row) => leadHasBuyingSignal(row));
  if (bucket === "pipeline") return live.filter((row) => leadInOutreach(row));
  return live;
}

export function bucketCounts(rows: LeadRow[]): Record<LeadBucket, number> {
  const out = {} as Record<LeadBucket, number>;
  for (const bucket of LEAD_BUCKETS) out[bucket] = bucketRows(rows, bucket).length;
  return out;
}

function facetValue(value: unknown): string {
  const text = String(value || "").trim();
  return text || NONE_FACET;
}

export function leadCountry(row: LeadRow): string {
  return facetValue(row.country);
}

export function leadSource(row: LeadRow): string {
  return facetValue(String(row.source || "").toLowerCase());
}

export function leadSector(row: LeadRow): string {
  return facetValue(row.sector);
}

export function leadStage(row: LeadRow): string {
  return String(row.stage || "new").trim().toLowerCase() || "new";
}

function sortedFacet(values: Set<string>): string[] {
  return [...values].sort((left, right) => {
    if (left === NONE_FACET) return 1;
    if (right === NONE_FACET) return -1;
    return left.localeCompare(right);
  });
}

export function leadFacets(rows: LeadRow[]): LeadFacets {
  const countries = new Set<string>();
  const sources = new Set<string>();
  const sectors = new Set<string>();
  const stages = new Set<string>();
  for (const row of rows) {
    countries.add(leadCountry(row));
    sources.add(leadSource(row));
    sectors.add(leadSector(row));
    stages.add(leadStage(row));
  }
  return {
    countries: sortedFacet(countries),
    sources: sortedFacet(sources),
    sectors: sortedFacet(sectors),
    stages: sortedFacet(stages),
  };
}

/** Everything a free-text search can hit on a lead. */
export function leadSearchHay(row: LeadRow): string {
  return [
    row.company,
    row.person,
    row.first_name,
    row.last_name,
    row.role,
    row.email,
    row.domain,
    row.website,
    row.country,
    row.sector,
    row.size,
    row.signal,
    row.source,
    row.stage,
    row.next_action,
    ...(row.why || []),
    ...(row.signals || []).map((signal) => signal.text || signal.kind || ""),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function applyLeadFilter(rows: LeadRow[], filter: LeadFacetFilter, query = ""): LeadRow[] {
  const needle = query.trim().toLowerCase();
  const minScore = filter.minScore ? Number(filter.minScore) : NaN;
  return rows.filter((row) => {
    if (filter.countries.length && !filter.countries.includes(leadCountry(row))) return false;
    if (filter.sources.length && !filter.sources.includes(leadSource(row))) return false;
    if (filter.sectors.length && !filter.sectors.includes(leadSector(row))) return false;
    if (filter.stages.length && !filter.stages.includes(leadStage(row))) return false;
    if (!Number.isNaN(minScore) && (row.score || 0) < minScore) return false;
    if (filter.email === "has" && !row.email) return false;
    if (filter.email === "verified" && row.email_status !== "verified") return false;
    if (filter.email === "none" && row.email) return false;
    if (needle && !leadSearchHay(row).includes(needle)) return false;
    return true;
  });
}
