// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { CareerOpportunity, CareerProfile, CareerTrack } from "@/lib/career-api";

/** Billable days a freelancer can realistically invoice in a month. */
export const BILLABLE_DAYS = 18;

/** Below this, a "salary" figure is a monthly amount, not a yearly package. */
const YEARLY_SALARY_FLOOR = 15000;

export type CareerMoney = {
  /** Monthly income the stored goal is worth, 0 when nothing is set yet. */
  monthly: number;
  /** Same goal over a year. */
  yearly: number;
  /** The raw goal the user typed, in its own unit. */
  goal: number;
  /** "day" for a freelance rate, "month" or "year" for a salary. */
  unit: "day" | "month" | "year";
  currency: string;
  /** Offers whose posted pay reaches the goal. */
  atOrAboveGoal: number;
  /** Offers that posted any pay at all, so the count above stays readable. */
  withPay: number;
  /** Offers scored 80 or more. */
  strong: number;
  /** Offers ready to send or already sent. */
  inFlight: number;
};

export function monthlyFromSalary(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  return value >= YEARLY_SALARY_FLOOR ? value / 12 : value;
}

function compensationMonthly(row: CareerOpportunity, track: CareerTrack | string): number {
  const raw = Number(row.compensation || 0);
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  const rowTrack = String(row.track || track);
  if (rowTrack === "freelance") return raw * BILLABLE_DAYS;
  return monthlyFromSalary(raw);
}

/**
 * Turn the stored goal and pipeline into money figures.
 *
 * Everything here comes from what the user typed or what an offer actually
 * posted. Offers without a pay line are counted apart, never guessed.
 */
export function careerMoney(
  profile: CareerProfile,
  rows: CareerOpportunity[],
  track: CareerTrack | string,
): CareerMoney {
  const currency = String(profile.currency || "EUR");
  const isFreelance = track === "freelance";
  const goal = Number((isFreelance ? profile.min_rate : profile.min_salary) || 0);
  const monthly = isFreelance ? goal * BILLABLE_DAYS : monthlyFromSalary(goal);
  const unit: CareerMoney["unit"] = isFreelance
    ? "day"
    : goal >= YEARLY_SALARY_FLOOR
      ? "year"
      : "month";

  let atOrAboveGoal = 0;
  let withPay = 0;
  let strong = 0;
  let inFlight = 0;
  for (const row of rows) {
    if (Number(row.match_score || 0) >= 80) strong += 1;
    if (["ready", "applied", "replied", "interview", "offer", "won"].includes(String(row.stage))) {
      inFlight += 1;
    }
    const pay = compensationMonthly(row, track);
    if (pay <= 0) continue;
    withPay += 1;
    if (monthly <= 0 || pay >= monthly) atOrAboveGoal += 1;
  }

  return {
    monthly: Math.round(monthly),
    yearly: Math.round(monthly * 12),
    goal,
    unit,
    currency,
    atOrAboveGoal,
    withPay,
    strong,
    inFlight,
  };
}

/** "€" for EUR, "£" for GBP, "$" for USD, the code itself when Intl has no short symbol. */
export function currencySymbol(currency: string, locale = "fr"): string {
  const code = String(currency || "").trim().toUpperCase();
  if (!code) return "";
  try {
    const parts = new Intl.NumberFormat(locale || "fr", {
      style: "currency",
      currency: code,
      currencyDisplay: "narrowSymbol",
      maximumFractionDigits: 0,
    }).formatToParts(1);
    const symbol = parts.find((part) => part.type === "currency")?.value || code;
    return symbol.trim();
  } catch {
    return code;
  }
}

function compactAmount(value: number, unit: "day" | "year"): string {
  if (unit === "year" && value >= 1000) {
    const thousands = value / 1000;
    const text = Number.isInteger(thousands) ? String(thousands) : thousands.toFixed(1).replace(/\.0$/, "");
    return `${text}k`;
  }
  return String(Math.round(value));
}

/**
 * The posted range the way the boards print it: "400-600 €/j", "40k-45k €/an",
 * "350-450 £/day". Currency is the offer's own; the suffix comes from the copy.
 */
export function formatPayRange(
  min: number | null | undefined,
  max: number | null | undefined,
  currency: string,
  unit: "day" | "year",
  suffix: string,
  locale = "fr",
): string {
  const low = Number(min);
  const high = Number(max);
  const hasLow = Number.isFinite(low) && low > 0;
  const hasHigh = Number.isFinite(high) && high > 0;
  if (!hasLow && !hasHigh) return "";
  const parts: string[] = [];
  if (hasLow) parts.push(compactAmount(low, unit));
  if (hasHigh && (!hasLow || Math.round(high) !== Math.round(low))) parts.push(compactAmount(high, unit));
  const symbol = currencySymbol(currency, locale);
  const range = parts.join("-");
  return `${range}${symbol ? ` ${symbol}` : ""}${suffix}`.trim();
}

export function formatMoney(value: number, currency: string, locale: string): string {
  if (!Number.isFinite(value) || value <= 0) return "";
  try {
    return new Intl.NumberFormat(locale || "fr", {
      style: "currency",
      currency: currency || "EUR",
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${Math.round(value)} ${currency || "EUR"}`;
  }
}
