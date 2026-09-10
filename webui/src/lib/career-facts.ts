// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { CareerOpportunity, CareerTrack } from "@/lib/career-api";
import { formatMoney, formatPayRange } from "@/lib/career-money";
import { stripHtml } from "@/lib/plain-text";

/** Table-like facts on offer and application cards. Missing values stay unknown. */
export type OfferFact = { id: string; label: string; value: string };

export type OfferFactCopy = {
  unknown: string;
  remote: string;
  office: string;
  country: string;
  city: string;
  pay: string;
  duration: string;
  workRemote: string;
  workHybrid: string;
  workOnsite: string;
  perDay: string;
  /** Optional: the boards' card lines (Free-Work style). Missing copy hides the fact. */
  perYear?: string;
  dayRate?: string;
  salary?: string;
  contract?: string;
  experience?: string;
  start?: string;
  startAsap?: string;
  contracts?: Partial<Record<string, string>>;
  experiences?: Partial<Record<string, string>>;
};

export const CONTRACT_KINDS = [
  "contractor",
  "permanent",
  "fixed-term",
  "part-time",
  "temporary",
  "internship",
  "apprenticeship",
] as const;

export const EXPERIENCE_LEVELS = ["junior", "mid", "senior", "expert"] as const;

const DEFAULT_CONTRACT_LABELS: Record<string, string> = {
  contractor: "Freelance",
  permanent: "Permanent",
  "fixed-term": "Fixed-term",
  "part-time": "Part-time",
  temporary: "Temporary",
  internship: "Internship",
  apprenticeship: "Apprenticeship",
};

const DEFAULT_EXPERIENCE_LABELS: Record<string, string> = {
  junior: "Junior (0-2 years)",
  mid: "Confirmed (3-5 years)",
  senior: "Senior (6-10 years)",
  expert: "Expert (10+ years)",
};

export function contractLabel(kind: string, copy?: OfferFactCopy): string {
  const key = String(kind || "").trim().toLowerCase();
  return copy?.contracts?.[key] || DEFAULT_CONTRACT_LABELS[key] || key;
}

export function experienceLabel(level: string, copy?: OfferFactCopy): string {
  const key = String(level || "").trim().toLowerCase();
  return copy?.experiences?.[key] || DEFAULT_EXPERIENCE_LABELS[key] || key;
}

/** Contract kinds on a row, structured first, the posted employment_type as fallback. */
export function offerContracts(row: CareerOpportunity): string[] {
  const listed = (row.contracts || []).map((item) => String(item).trim().toLowerCase()).filter(Boolean);
  if (listed.length) return [...new Set(listed)];
  const raw = String(row.employment_type || "").toLowerCase();
  const out: string[] = [];
  if (/contractor|freelance|contract\b|mission|ind[ée]pendant/.test(raw)) out.push("contractor");
  if (/permanent|full[ -]?time|\bcdi\b/.test(raw)) out.push("permanent");
  if (/fixed|\bcdd\b|temporary|interim/.test(raw)) out.push("fixed-term");
  if (/part[ -]?time/.test(raw)) out.push("part-time");
  if (/intern/.test(raw)) out.push("internship");
  if (/apprentice|alternance/.test(raw)) out.push("apprenticeship");
  return out;
}

export function offerExperience(row: CareerOpportunity): string {
  const level = String(row.experience_level || "").trim().toLowerCase();
  if (level && (EXPERIENCE_LEVELS as readonly string[]).includes(level)) return level;
  const years = Number(row.experience_years_min);
  if (Number.isFinite(years) && years >= 0 && row.experience_years_min != null) return levelForYears(years);
  // Legacy rows only carry the posted seniority text ("Senior", "3+ years", "Confirme").
  const raw = String(row.seniority || "").toLowerCase();
  if (!raw) return "";
  const posted = raw.match(/(\d{1,2})\s*\+?\s*(?:years?|ans?|yrs?)/);
  if (posted) return levelForYears(Number(posted[1]));
  if (/\b(expert|principal|lead|staff|architect)\b/.test(raw)) return "expert";
  if (/\b(senior|sr\.?)\b/.test(raw)) return "senior";
  if (/\b(junior|jr\.?|entry|graduate|debutant|d[ée]butant)\b/.test(raw)) return "junior";
  if (/\b(mid|intermediate|confirm[ée]?|regular)\b/.test(raw)) return "mid";
  return "";
}

function levelForYears(years: number): string {
  if (years <= 2) return "junior";
  if (years <= 5) return "mid";
  if (years <= 10) return "senior";
  return "expert";
}

function positive(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function offerDayRate(row: CareerOpportunity): { min: number; max: number } {
  return { min: positive(row.daily_rate_min), max: positive(row.daily_rate_max) };
}

export function offerSalary(row: CareerOpportunity): { min: number; max: number } {
  return { min: positive(row.salary_min), max: positive(row.salary_max) };
}

function offerCity(location: string | undefined, unknown: string): string {
  const raw = String(location || "").trim();
  if (!raw) return unknown;
  const first = raw.split(",")[0].trim();
  if (!first || /^remote$/i.test(first) || /^worldwide$/i.test(first)) return unknown;
  return first;
}

function offerCountryValue(country: string | undefined, unknown: string): string {
  const raw = String(country || "").trim();
  if (!raw || /^remote$/i.test(raw)) return unknown;
  return raw;
}

export function offerRemoteLabel(remote: string | undefined, copy: OfferFactCopy): string {
  const key = String(remote || "")
    .trim()
    .toLowerCase();
  if (["remote", "yes", "true", "fully_remote", "full_remote", "fully-remote"].includes(key)) {
    return copy.workRemote;
  }
  if (key === "hybrid" || key === "hybride") return copy.workHybrid;
  if (["onsite", "on-site", "on_site", "office"].includes(key)) return copy.workOnsite;
  return copy.unknown;
}

function parsedFacts(row: CareerOpportunity): {
  remote: string;
  officeDays: string;
  pay: number;
  currency: string;
  duration: string;
} {
  const text = stripHtml(row.description || "");
  let remote = String(row.remote || "")
    .trim()
    .toLowerCase();
  if (!remote && /\bhybrid\b/i.test(text)) remote = "hybrid";
  else if (!remote && /\b(remote|teletravail|work from home)\b/i.test(text)) remote = "remote";

  const min = Number(row.hybrid_days_min);
  const max = Number(row.hybrid_days_max);
  const hasMin = Number.isFinite(min) && min > 0;
  const hasMax = Number.isFinite(max) && max > 0;
  let officeDays = "";
  if (hasMin && hasMax && min !== max) officeDays = `${min}-${max}`;
  else if (hasMin || hasMax) officeDays = String(hasMin ? min : max);
  if (!officeDays) {
    const hybridDays = text.match(/\bhybrid\s+(\d+)\s+days?\b/i);
    const siteDays = text.match(
      /(\d)\s*(?:-\s*(\d)\s*)?(?:days?|jours?)\s*(?:on[ -]?site|in (?:the )?office|presentiel|présentiel|bureau)/i,
    );
    if (hybridDays) officeDays = hybridDays[1];
    else if (siteDays) officeDays = siteDays[2] ? `${siteDays[1]}-${siteDays[2]}` : siteDays[1];
  }

  let pay = Number(row.compensation);
  if (!Number.isFinite(pay) || pay <= 0) pay = 0;
  let currency = String(row.currency || "").trim();
  if (pay <= 0) {
    const tjm = text.match(/\btjm\s+(\d{3,4})\s*(eur|usd|gbp|chf)?\b/i);
    if (tjm) {
      pay = Number(tjm[1]);
      currency = currency || (tjm[2] || "EUR").toUpperCase();
    }
  }

  let duration = String(row.duration || "").trim();
  if (!duration) {
    const match =
      text.match(/\b(?:mission|duration|duree|durée)\s+(\d+)\s*(months?|mois)\b/i) ||
      text.match(/\b(\d+)\s*(months?|mois)\b/i);
    if (match) {
      const n = match[1];
      const unit = /mois/i.test(match[2]) ? "months" : match[2].toLowerCase();
      duration = `${n} ${unit}`;
    }
  }

  return { remote, officeDays, pay, currency, duration };
}

function startLabel(value: string | undefined, copy: OfferFactCopy): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (/^(asap|immediate|immédiat|immediat|d[eè]s que possible)/i.test(raw)) return copy.startAsap || raw;
  return raw.slice(0, 10);
}

export function offerFacts(
  row: CareerOpportunity,
  copy: OfferFactCopy,
  locale = "fr",
  track?: CareerTrack | string,
): OfferFact[] {
  const parsed = parsedFacts(row);
  const rowTrack = String(row.track || track || "");
  const currency = String(row.currency || parsed.currency || "");
  const facts: OfferFact[] = [];

  const kinds = offerContracts(row);
  if (copy.contract) {
    facts.push({
      id: "contract",
      label: copy.contract,
      value: kinds.length ? kinds.map((kind) => contractLabel(kind, copy)).join(" · ") : copy.unknown,
    });
  }
  const start = startLabel(row.start_date, copy);
  if (copy.start && start) facts.push({ id: "start", label: copy.start, value: start });
  facts.push({ id: "duration", label: copy.duration, value: parsed.duration || copy.unknown });

  const day = offerDayRate(row);
  const year = offerSalary(row);
  const dayText = formatPayRange(day.min, day.max, currency, "day", copy.perDay, locale);
  const yearText = formatPayRange(year.min, year.max, currency, "year", copy.perYear || "", locale);
  const hasRange = Boolean(dayText || yearText);
  if (hasRange && copy.salary && copy.dayRate) {
    if (yearText || rowTrack === "jobs") facts.push({ id: "salary", label: copy.salary, value: yearText || copy.unknown });
    if (dayText || rowTrack === "freelance") facts.push({ id: "dayrate", label: copy.dayRate, value: dayText || copy.unknown });
  } else {
    const raw = Number(row.compensation) > 0 ? Number(row.compensation) : parsed.pay;
    let pay = copy.unknown;
    if (Number.isFinite(raw) && raw > 0) {
      const money = formatMoney(raw, currency || "EUR", locale);
      if (money) {
        const glue = copy.perDay.startsWith("/") ? "" : " ";
        pay = rowTrack === "freelance" ? `${money}${glue}${copy.perDay}`.trim() : money;
      }
    }
    facts.push({ id: "pay", label: copy.pay, value: pay });
  }

  facts.push({ id: "remote", label: copy.remote, value: offerRemoteLabel(parsed.remote || row.remote, copy) });
  facts.push({ id: "office", label: copy.office, value: parsed.officeDays || copy.unknown });
  facts.push({ id: "country", label: copy.country, value: offerCountryValue(row.country, copy.unknown) });
  facts.push({ id: "city", label: copy.city, value: offerCity(row.location, copy.unknown) });
  const level = offerExperience(row);
  if (copy.experience && level) facts.push({ id: "experience", label: copy.experience, value: experienceLabel(level, copy) });
  return facts;
}
