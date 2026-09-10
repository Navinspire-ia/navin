// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { CareerOpportunity } from "@/lib/career-api";
import { offerContracts, offerDayRate, offerExperience, offerSalary } from "@/lib/career-facts";

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
  /** Board-style filters: contract kinds, experience band, freshness, mission length, pay floors. */
  contracts: string[];
  experiences: string[];
  postedWithin: "" | "1" | "7" | "14" | "30";
  minDuration: string;
  maxDuration: string;
  minDayRate: string;
  minSalary: string;
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
  contracts: string[];
  experiences: string[];
}

export const POSTED_WITHIN_DAYS = ["1", "7", "14", "30"] as const;

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
    contracts: [],
    experiences: [],
    postedWithin: "",
    minDuration: "",
    maxDuration: "",
    minDayRate: "",
    minSalary: "",
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
  if (filter.contracts?.length) n += 1;
  if (filter.experiences?.length) n += 1;
  if (filter.postedWithin) n += 1;
  if (filter.minDuration || filter.maxDuration) n += 1;
  if (filter.minDayRate) n += 1;
  if (filter.minSalary) n += 1;
  return n;
}

/** Months a mission runs, from the structured field or the posted "6 months" / "1 year" label. */
export function offerDurationMonths(row: CareerOpportunity): number {
  const structured = Number(row.duration_months);
  if (Number.isFinite(structured) && structured > 0) return structured;
  const label = String(row.duration || "").trim();
  const match = label.match(/(\d+(?:[.,]\d+)?)\s*(mois|months?|ans?|years?|semaines?|weeks?|jours?|days?)/i);
  if (!match) return 0;
  const count = Number(match[1].replace(",", "."));
  if (!Number.isFinite(count) || count <= 0) return 0;
  const unit = match[2].toLowerCase();
  if (/^(an|year)/.test(unit)) return Math.round(count * 12);
  if (/^(semaine|week)/.test(unit)) return Math.max(1, Math.round(count / 4));
  if (/^(jour|day)/.test(unit)) return Math.max(1, Math.round(count / 21));
  return Math.round(count);
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
  const contracts = new Set<string>();
  const experiences = new Set<string>();
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
    for (const kind of offerContracts(row)) contracts.add(kind);
    const level = offerExperience(row);
    if (level) experiences.add(level);
  }
  const contractOrder = ["contractor", "permanent", "fixed-term", "part-time", "temporary", "internship", "apprenticeship"];
  const experienceOrder = ["junior", "mid", "senior", "expert"];
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
    contracts: [...contracts].sort((left, right) => contractOrder.indexOf(left) - contractOrder.indexOf(right)),
    experiences: [...experiences].sort((left, right) => experienceOrder.indexOf(left) - experienceOrder.indexOf(right)),
  };
}

function postedWithinFloor(days: string, now: Date = new Date()): string {
  const count = Number(days);
  if (!Number.isFinite(count) || count <= 0) return "";
  const floor = new Date(now.getTime() - count * 24 * 60 * 60 * 1000);
  return floor.toISOString().slice(0, 10);
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
  const minDuration = asNumber(filter.minDuration || "");
  const maxDuration = asNumber(filter.maxDuration || "");
  const minDayRate = asNumber(filter.minDayRate || "");
  const minSalary = asNumber(filter.minSalary || "");
  const freshFloor = postedWithinFloor(filter.postedWithin || "");
  const pickedContracts = filter.contracts || [];
  const pickedExperiences = filter.experiences || [];
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
    if (pickedContracts.length) {
      const kinds = offerContracts(row);
      if (!pickedContracts.some((kind) => kinds.includes(kind))) return false;
    }
    if (pickedExperiences.length && !pickedExperiences.includes(offerExperience(row))) return false;
    if (freshFloor) {
      const day = offerIsoDay(row.posted_at);
      if (!day || day < freshFloor) return false;
    }
    if (minDuration != null || maxDuration != null) {
      const months = offerDurationMonths(row);
      if (!months) return false;
      if (minDuration != null && months < minDuration) return false;
      if (maxDuration != null && months > maxDuration) return false;
    }
    if (minDayRate != null) {
      const day = offerDayRate(row);
      const top = day.max || day.min;
      if (!top || top < minDayRate) return false;
    }
    if (minSalary != null) {
      const year = offerSalary(row);
      const top = year.max || year.min;
      if (!top || top < minSalary) return false;
    }
    return true;
  });
}
