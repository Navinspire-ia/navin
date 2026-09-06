import type { TenderNotice } from "@/lib/tenders-api";

/** Facet value for an empty country / domain / buyer / source. */
export const NONE_FACET = "__none__";

export type NoticeGoFilter = "" | "go" | "nogo" | "unscored";
export type NoticeDeadlineState = "" | "has" | "none";

export interface NoticeFacetFilter {
  countries: string[];
  sectors: string[];
  buyers: string[];
  sources: string[];
  currencies: string[];
  stages: string[];
  deadlineFrom: string;
  deadlineTo: string;
  publishedFrom: string;
  publishedTo: string;
  arrivedFrom: string;
  arrivedTo: string;
  minScore: string;
  maxScore: string;
  minBudget: string;
  maxBudget: string;
  go: NoticeGoFilter;
  deadlineState: NoticeDeadlineState;
}

export interface NoticeFacets {
  countries: string[];
  sectors: string[];
  buyers: string[];
  sources: string[];
  currencies: string[];
  stages: string[];
}

export function emptyFacetFilter(): NoticeFacetFilter {
  return {
    countries: [],
    sectors: [],
    buyers: [],
    sources: [],
    currencies: [],
    stages: [],
    deadlineFrom: "",
    deadlineTo: "",
    publishedFrom: "",
    publishedTo: "",
    arrivedFrom: "",
    arrivedTo: "",
    minScore: "",
    maxScore: "",
    minBudget: "",
    maxBudget: "",
    go: "",
    deadlineState: "",
  };
}

export function facetFilterActive(filter: NoticeFacetFilter): boolean {
  return facetFilterCount(filter) > 0;
}

export function facetFilterCount(filter: NoticeFacetFilter): number {
  let n = 0;
  if (filter.countries.length) n += 1;
  if (filter.sectors.length) n += 1;
  if (filter.buyers.length) n += 1;
  if (filter.sources.length) n += 1;
  if (filter.currencies.length) n += 1;
  if (filter.stages.length) n += 1;
  if (filter.deadlineFrom || filter.deadlineTo) n += 1;
  if (filter.publishedFrom || filter.publishedTo) n += 1;
  if (filter.arrivedFrom || filter.arrivedTo) n += 1;
  if (filter.minScore || filter.maxScore) n += 1;
  if (filter.minBudget || filter.maxBudget) n += 1;
  if (filter.go) n += 1;
  if (filter.deadlineState) n += 1;
  return n;
}

export function noticeIsoDay(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return new Date(value * 1000).toISOString().slice(0, 10);
  }
  const text = String(value || "").trim();
  const match = text.match(/(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : "";
}

export function noticeDomain(row: TenderNotice, crafts: string[] = []): string {
  const sector = (row.sector || "").trim();
  if (sector) return sector;
  const cpv = (row.cpv || "").trim();
  if (cpv) return cpv;
  const hay = [row.title, row.description, row.go_reason, row.go_note]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  for (const craft of crafts) {
    const token = craft.trim();
    if (token && hay.includes(token.toLowerCase())) return token;
  }
  return "";
}

export function noticeFacets(rows: TenderNotice[], crafts: string[] = []): NoticeFacets {
  const countries = new Set<string>();
  const sectors = new Set<string>();
  const buyers = new Set<string>();
  const sources = new Set<string>();
  const currencies = new Set<string>();
  const stages = new Set<string>();
  for (const row of rows) {
    countries.add((row.country || "").trim().toUpperCase() || NONE_FACET);
    const domain = noticeDomain(row, crafts);
    sectors.add(domain || NONE_FACET);
    buyers.add((row.buyer || "").trim() || NONE_FACET);
    sources.add((row.source_id || "").trim() || NONE_FACET);
    const currency = (row.currency || "").trim().toUpperCase();
    if (currency) currencies.add(currency);
    const stage = String(row.stage || "").trim();
    if (stage) stages.add(stage);
  }
  const byLabel = (left: string, right: string) => {
    if (left === NONE_FACET) return 1;
    if (right === NONE_FACET) return -1;
    return left.localeCompare(right);
  };
  return {
    countries: [...countries].sort(byLabel),
    sectors: [...sectors].sort(byLabel),
    buyers: [...buyers].sort(byLabel),
    sources: [...sources].sort(byLabel),
    currencies: [...currencies].sort(),
    stages: [...stages].sort(),
  };
}

function inIsoRange(day: string, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!day) return false;
  if (from && day < from) return false;
  if (to && day > to) return false;
  return true;
}

function asNumber(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function matchesPicked(value: string, picked: string[]): boolean {
  if (!picked.length) return true;
  const key = value.trim() || NONE_FACET;
  return picked.includes(key);
}

export function applyFacetFilter(
  rows: TenderNotice[],
  filter: NoticeFacetFilter,
  crafts: string[] = [],
): TenderNotice[] {
  const minScore = asNumber(filter.minScore);
  const maxScore = asNumber(filter.maxScore);
  const minBudget = asNumber(filter.minBudget);
  const maxBudget = asNumber(filter.maxBudget);
  return rows.filter((row) => {
    if (!matchesPicked((row.country || "").toUpperCase(), filter.countries)) return false;
    if (!matchesPicked(noticeDomain(row, crafts), filter.sectors)) return false;
    if (!matchesPicked(row.buyer || "", filter.buyers)) return false;
    if (!matchesPicked(row.source_id || "", filter.sources)) return false;
    if (filter.currencies.length && !filter.currencies.includes((row.currency || "").toUpperCase())) {
      return false;
    }
    if (filter.stages.length && !filter.stages.includes(String(row.stage || ""))) return false;
    if (!inIsoRange(noticeIsoDay(row.deadline), filter.deadlineFrom, filter.deadlineTo)) return false;
    if (!inIsoRange(noticeIsoDay(row.publication_date), filter.publishedFrom, filter.publishedTo)) {
      return false;
    }
    if (!inIsoRange(noticeIsoDay(row.fetched_at), filter.arrivedFrom, filter.arrivedTo)) return false;
    if (minScore != null && (row.score == null || row.score < minScore)) return false;
    if (maxScore != null && (row.score == null || row.score > maxScore)) return false;
    if (minBudget != null && (row.budget == null || row.budget < minBudget)) return false;
    if (maxBudget != null && (row.budget == null || row.budget > maxBudget)) return false;
    if (filter.go === "go" && row.go !== true) return false;
    if (filter.go === "nogo" && row.go !== false) return false;
    if (filter.go === "unscored" && row.go != null) return false;
    const hasDeadline = Boolean(noticeIsoDay(row.deadline));
    if (filter.deadlineState === "has" && !hasDeadline) return false;
    if (filter.deadlineState === "none" && hasDeadline) return false;
    return true;
  });
}
