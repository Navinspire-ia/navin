// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { formatUsagePercent, usagePercent } from "./usage-percent";

describe("usagePercent", () => {
  it("reads the exact spend, not the site's whole-percent rounding", () => {
    expect(usagePercent({ budget_micro_usd: 160_000_000, spent_micro_usd: 760_000, used_percent: 0 })).toBeCloseTo(0.475);
  });

  it("falls back to the site's percent without a budget, and stays within 0-100", () => {
    expect(usagePercent({ used_percent: 42 })).toBe(42);
    expect(usagePercent({ budget_micro_usd: 10, spent_micro_usd: 50 })).toBe(100);
    expect(usagePercent(null)).toBe(0);
  });
});

describe("formatUsagePercent", () => {
  it("keeps one decimal under 10 % and marks a trace", () => {
    expect(formatUsagePercent(0.475)).toMatch(/^0[.,]5$/);
    expect(formatUsagePercent(0.04)).toBe("<0.1");
    expect(formatUsagePercent(0)).toBe("0");
    expect(formatUsagePercent(42.4)).toBe("42");
  });
});
