// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import { offerFacts, type OfferFactCopy } from "@/lib/career-facts";

const COPY: OfferFactCopy = {
  unknown: "Not listed",
  remote: "Remote",
  office: "Office days",
  country: "Country",
  city: "City",
  pay: "Pay",
  duration: "Duration",
  workRemote: "Full remote",
  workHybrid: "Hybrid",
  workOnsite: "On site",
  perDay: "/ day",
};

function job(partial: Partial<CareerOpportunity> & { id: string }): CareerOpportunity {
  return {
    source: "web",
    title: "Data Engineer",
    stage: "discovered",
    ...partial,
  };
}

describe("career offer facts", () => {
  it("shows posted city, country, remote mode, pay and duration without guessing missing fields", () => {
    const facts = offerFacts(
      job({
        id: "a",
        location: "Paris",
        country: "FR",
        remote: "remote",
        compensation: 700,
        currency: "EUR",
        duration: "6 months",
        track: "freelance",
      }),
      COPY,
      "fr-FR",
      "freelance",
    );
    expect(facts.find((row) => row.id === "city")?.value).toBe("Paris");
    expect(facts.find((row) => row.id === "country")?.value).toBe("FR");
    expect(facts.find((row) => row.id === "remote")?.value).toBe("Full remote");
    expect(facts.find((row) => row.id === "office")?.value).toBe("Not listed");
    expect(facts.find((row) => row.id === "pay")?.value).toContain("700");
    expect(facts.find((row) => row.id === "duration")?.value).toBe("6 months");
  });

  it("prints hybrid office days when the offer posted a range", () => {
    const facts = offerFacts(
      job({ id: "b", remote: "hybrid", hybrid_days_min: 2, hybrid_days_max: 3 }),
      COPY,
      "fr-FR",
      "jobs",
    );
    expect(facts.find((row) => row.id === "remote")?.value).toBe("Hybrid");
    expect(facts.find((row) => row.id === "office")?.value).toBe("2-3");
  });
});
