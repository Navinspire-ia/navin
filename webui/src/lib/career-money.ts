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
