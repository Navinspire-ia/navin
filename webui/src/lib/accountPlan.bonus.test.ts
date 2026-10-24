import { describe, expect, it } from "vitest";

import { bonusLine } from "@/lib/accountPlan";

const t = (key: string, options?: Record<string, unknown>) =>
  String(options?.defaultValue ?? key)
    .replace("{{amount}}", String(options?.amount ?? ""))
    .replace("{{date}}", String(options?.date ?? ""));

describe("bonusLine", () => {
  it("shows only the bonus and its end date, never the plan's budget", () => {
    const line = bonusLine(t, {
      usage: { extra_budget_micro_usd: 1_000_000_000, extra_budget_until: "2027-01-15T12:00:00.000Z" },
    });
    expect(line).toContain("Bonus +");
    expect(line).toContain("1,000");
    expect(line).toContain("2027");
    expect(line).not.toContain("62");
  });

  it("is empty without a bonus", () => {
    expect(bonusLine(t, { usage: { extra_budget_micro_usd: 0 } })).toBe("");
    expect(bonusLine(t, null)).toBe("");
  });

  it("has no date when the bonus has no end", () => {
    expect(bonusLine(t, { usage: { extra_budget_micro_usd: 50_000_000, extra_budget_until: null } })).toBe("Bonus +$50");
  });
});
