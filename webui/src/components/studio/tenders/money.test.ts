// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { formatTenderMoney, moneyHeroKind } from "@/components/studio/tenders/money";

describe("tenders money hero", () => {
  it("speaks money first, then count", () => {
    expect(formatTenderMoney(2_400_000, "EUR")).toBe("2.4 M EUR");
    expect(formatTenderMoney(180_000, "EUR")).toBe("180 k EUR");
    expect(moneyHeroKind({ collected: 0, inPlay: 0, weighted: 0 })).toBe("empty");
    expect(moneyHeroKind({ collected: 91, inPlay: 0, weighted: 0 })).toBe("screened");
    expect(moneyHeroKind({ collected: 12, inPlay: 3, weighted: 2_400_000 })).toBe("play-money");
    expect(moneyHeroKind({ collected: 12, inPlay: 3, weighted: 0 })).toBe("play-count");
  });
});
