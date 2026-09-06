import type { CareerOpportunity } from "@/lib/career-api";

/** Facet value for an empty country / domain / company / source. */
export const NONE_FACET = "__none__";

export interface OfferFacetFilter {
  query: string;
  countries: string[];
  domains: string[];
  companies: string[];
  sources: string[];
  tracks: string[];
  remotes: string[];
  stages: string[];
  currencies: string[];
  languages: string[];
  stacks: string[];
  buckets: string[];
  postedFrom: string;
  postedTo: string;
  arrivedFrom: string;
  arrivedTo: string;
  minScore: string;
  maxScore: string;
  minPay: string;
  maxPay: string;
}

export interface OfferFacets {
  countries: string[];
  domains: string[];
  companies: string[];
  sources: string[];
  tracks: string[];
  remotes: string[];
  stages: string[];
  currencies: string[];
  languages: string[];
  stacks: string[];
  buckets: string[];
}

const DOMAIN_HINTS = [
  "data",
  "devops",
  "frontend",
  "backend",
  "fullstack",
  "mobile",
  "android",
  "ios",
  "marketing",
  "design",
  "product",
  "sales",
  "security",
  "qa",
  "ai",
  "ml",
  "cloud",
  "finance",
  "hr",
  "support",
  "writer",
  "legal",
];

export function emptyOfferFilter(): OfferFacetFilter {
  return {
    query: "",
    countries: [],
    domains: [],
    companies: [],
    sources: [],
    tracks: [],
    remotes: [],
    stages: [],
    currencies: [],
    languages: [],
    stacks: [],
    buckets: [],
    postedFrom: "",
    postedTo: "",
    arrivedFrom: "",
    arrivedTo: "",
    minScore: "",
    maxScore: "",
    minPay: "",
    maxPay: "",
  };
}

export function offerFilterActive(filter: OfferFacetFilter): boolean {
  return offerFilterCount(filter) > 0;
}

export function offerFilterCount(filter: OfferFacetFilter): number {
  let n = 0;
  if (filter.query.trim()) n += 1;
  if (filter.countries.length) n += 1;
  if (filter.domains.length) n += 1;
  if (filter.companies.length) n += 1;
  if (filter.sources.length) n += 1;
  if (filter.tracks.length) n += 1;
  if (filter.remotes.length) n += 1;
  if (filter.stages.length) n += 1;
  if (filter.currencies.length) n += 1;
  if (filter.languages.length) n += 1;
  if (filter.stacks.length) n += 1;
  if (filter.buckets.length) n += 1;
  if (filter.postedFrom || filter.postedTo) n += 1;
  if (filter.arrivedFrom || filter.arrivedTo) n += 1;
  if (filter.minScore || filter.maxScore) n += 1;
  if (filter.minPay || filter.maxPay) n += 1;
  return n;
}

export function offerIsoDay(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return new Date(value * 1000).toISOString().slice(0, 10);
  }
  const text = String(value || "").trim();
  const match = text.match(/(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : "";
}

export function offerDomain(row: CareerOpportunity, hints: string[] = []): string {
  const stack = (row.stack || []).map((item) => String(item).trim()).filter(Boolean);
  if (stack[0]) return stack[0];
  const hay = `${row.title || ""} ${row.description || ""}`.toLowerCase();
  for (const hint of [...hints, ...DOMAIN_HINTS]) {
    const token = hint.trim();
    if (token && hay.includes(token.toLowerCase())) return token;
  }
  return "";
}

export function offerRemote(row: CareerOpportunity): string {
  const raw = String(row.remote || "").trim().toLowerCase();
  if (["remote", "yes", "true"].includes(raw)) return "remote";
  if (raw === "hybrid") return "hybrid";
  if (["onsite", "on-site", "office"].includes(raw)) return "onsite";
  return raw || "";
}

export function offerFacets(rows: CareerOpportunity[], hints: string[] = []): OfferFacets {
  const countries = new Set<string>();
  const domains = new Set<string>();
  const companies = new Set<string>();
  const sources = new Set<string>();
  const tracks = new Set<string>();
  const remotes = new Set<string>();
  const stages = new Set<string>();
  const currencies = new Set<string>();
  const languages = new Set<string>();
  const stacks = new Set<string>();
  const buckets = new Set<string>();
  for (const row of rows) {
    countries.add((row.country || "").trim().toUpperCase() || NONE_FACET);
    domains.add(offerDomain(row, hints) || NONE_FACET);
    companies.add((row.company || "").trim() || NONE_FACET);
    sources.add((row.source || "").trim() || NONE_FACET);
    const track = String(row.track || "").trim();
    if (track) tracks.add(track);
    const remote = offerRemote(row);
    if (remote) remotes.add(remote);
    const stage = String(row.stage || "").trim();
    if (stage) stages.add(stage);
    const currency = (row.currency || "").trim().toUpperCase();
    if (currency) currencies.add(currency);
    for (const lang of row.languages || []) {
      const code = String(lang).trim().toLowerCase();
      if (code) languages.add(code);
    }
    for (const item of row.stack || []) {
      const token = String(item).trim();
      if (token) stacks.add(token);
    }
    const bucket = String(row.bucket || "").trim();
    if (bucket) buckets.add(bucket);
  }
  const byLabel = (left: string, right: string) => {
    if (left === NONE_FACET) return 1;
    if (right === NONE_FACET) return -1;
    return left.localeCompare(right);
  };
  return {
    countries: [...countries].sort(byLabel),
    domains: [...domains].sort(byLabel),
    companies: [...companies].sort(byLabel),
    sources: [...sources].sort(byLabel),
    tracks: [...tracks].sort(),
    remotes: [...remotes].sort(),
    stages: [...stages].sort(),
    currencies: [...currencies].sort(),
    languages: [...languages].sort(),
    stacks: [...stacks].sort(byLabel),
    buckets: [...buckets].sort(),
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

export function applyOfferFilter(
  rows: CareerOpportunity[],
  filter: OfferFacetFilter,
  hints: string[] = [],
): CareerOpportunity[] {
  const minScore = asNumber(filter.minScore);
  const maxScore = asNumber(filter.maxScore);
  const minPay = asNumber(filter.minPay);
  const maxPay = asNumber(filter.maxPay);
  const needle = filter.query.trim().toLowerCase();
  return rows.filter((row) => {
    if (needle) {
      const hay = [
        row.id,
        row.title,
        row.company,
        row.location,
        row.country,
        row.source,
        row.description,
        row.url,
        row.track,
        row.stage,
        row.remote,
        (row.stack || []).join(" "),
        (row.languages || []).join(" "),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    if (!matchesPicked((row.country || "").toUpperCase(), filter.countries)) return false;
    if (!matchesPicked(offerDomain(row, hints), filter.domains)) return false;
    if (!matchesPicked(row.company || "", filter.companies)) return false;
    if (!matchesPicked(row.source || "", filter.sources)) return false;
    if (filter.tracks.length && !filter.tracks.includes(String(row.track || ""))) return false;
    if (filter.remotes.length && !filter.remotes.includes(offerRemote(row))) return false;
    if (filter.stages.length && !filter.stages.includes(String(row.stage || ""))) return false;
    if (filter.currencies.length && !filter.currencies.includes((row.currency || "").toUpperCase())) {
      return false;
    }
    if (filter.languages.length) {
      const langs = (row.languages || []).map((item) => String(item).trim().toLowerCase());
      if (!filter.languages.some((item) => langs.includes(item))) return false;
    }
    if (filter.stacks.length) {
      const tokens = (row.stack || []).map((item) => String(item).trim());
      if (!filter.stacks.some((item) => tokens.includes(item))) return false;
    }
    if (filter.buckets.length && !filter.buckets.includes(String(row.bucket || ""))) return false;
    if (!inIsoRange(offerIsoDay(row.posted_at), filter.postedFrom, filter.postedTo)) return false;
    if (!inIsoRange(offerIsoDay(row.created_at), filter.arrivedFrom, filter.arrivedTo)) return false;
    if (minScore != null && (row.match_score == null || row.match_score < minScore)) return false;
    if (maxScore != null && (row.match_score == null || row.match_score > maxScore)) return false;
    if (minPay != null && (row.compensation == null || row.compensation < minPay)) return false;
    if (maxPay != null && (row.compensation == null || row.compensation > maxPay)) return false;
    return true;
  });
}
