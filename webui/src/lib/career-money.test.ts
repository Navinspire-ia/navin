// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import {
  BILLABLE_DAYS,
  careerMoney,
  currencySymbol,
  formatMoney,
  formatPayRange,
  monthlyFromSalary,
} from "@/lib/career-money";

function offer(row: Partial<CareerOpportunity>): CareerOpportunity {
  return { id: row.id || "job-1", source: "remotive", title: "Role", stage: "discovered", ...row };
}

describe("career money", () => {
  it("turns a daily rate into a monthly goal", () => {
    const money = careerMoney({ min_rate: 650, currency: "EUR" }, [], "freelance");
    expect(money.monthly).toBe(650 * BILLABLE_DAYS);
    expect(money.yearly).toBe(650 * BILLABLE_DAYS * 12);
    expect(money.unit).toBe("day");
  });

  it("reads a salary as yearly above the floor and monthly below it", () => {
    expect(monthlyFromSalary(60000)).toBe(5000);
    expect(monthlyFromSalary(4000)).toBe(4000);
    expect(careerMoney({ min_salary: 60000 }, [], "jobs").unit).toBe("year");
    expect(careerMoney({ min_salary: 4000 }, [], "jobs").unit).toBe("month");
  });

  it("counts only offers that posted a pay line", () => {
    const money = careerMoney({ min_rate: 650 }, [
      offer({ id: "a", compensation: 700, track: "freelance" }),
      offer({ id: "b", compensation: 500, track: "freelance" }),
      offer({ id: "c" }),
    ], "freelance");
    expect(money.withPay).toBe(2);
    expect(money.atOrAboveGoal).toBe(1);
  });

  it("counts strong matches and everything already in flight", () => {
    const money = careerMoney({ min_rate: 650 }, [
      offer({ id: "a", match_score: 86, stage: "matched" }),
      offer({ id: "b", match_score: 40, stage: "applied" }),
      offer({ id: "c", match_score: 91, stage: "interview" }),
    ], "freelance");
    expect(money.strong).toBe(2);
    expect(money.inFlight).toBe(2);
  });

  it("keeps zero when no goal is set yet", () => {
    const money = careerMoney({}, [offer({ compensation: 700, track: "freelance" })], "freelance");
    expect(money.monthly).toBe(0);
    expect(money.atOrAboveGoal).toBe(1);
    expect(formatMoney(money.monthly, "EUR", "fr")).toBe("");
  });

  it("formats a readable amount", () => {
    expect(formatMoney(11700, "EUR", "fr")).toContain("11");
    expect(formatMoney(0, "EUR", "fr")).toBe("");
  });

  it("prints board-style pay ranges in the offer currency", () => {
    expect(formatPayRange(400, 600, "EUR", "day", "/j")).toBe("400-600 €/j");
    expect(formatPayRange(40000, 45000, "EUR", "year", "/an")).toBe("40k-45k €/an");
    expect(formatPayRange(550, null, "GBP", "day", "/day", "en")).toBe("550 £/day");
    expect(formatPayRange(null, 120000, "USD", "year", "/yr", "en")).toBe("120k $/yr");
    expect(formatPayRange(30000, 35000, "AED", "year", "/an")).toContain("30k-35k");
    expect(formatPayRange(null, null, "EUR", "day", "/j")).toBe("");
    expect(currencySymbol("EUR")).toBe("€");
    expect(currencySymbol("GBP", "en")).toBe("£");
    expect(currencySymbol("XYZ")).toBe("XYZ");
  });
});
