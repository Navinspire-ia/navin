// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import {
  contractLabel,
  offerContracts,
  offerDayRate,
  offerExperience,
  offerFacts,
  offerSalary,
  type OfferFactCopy,
} from "@/lib/career-facts";

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

const BOARD_COPY: OfferFactCopy = {
  ...COPY,
  perDay: "/j",
  perYear: "/an",
  dayRate: "TJM",
  salary: "Salaire",
  contract: "Contrat",
  experience: "Experience",
  start: "Demarrage",
  startAsap: "Des que possible",
  contracts: { contractor: "Freelance", permanent: "CDI", "fixed-term": "CDD" },
  experiences: { senior: "Senior (6-10 ans)" },
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

  it("prints the board card: contract pills, start, duration, day rate and salary in the market currency", () => {
    const row = job({
      id: "c",
      country: "FR",
      currency: "EUR",
      contracts: ["contractor", "permanent"],
      start_date: "2026-10-01",
      duration: "1 year",
      duration_months: 12,
      daily_rate_min: 400,
      daily_rate_max: 600,
      salary_min: 40000,
      salary_max: 45000,
      experience_level: "senior",
      remote: "hybrid",
      location: "Lyon",
    });
    const facts = offerFacts(row, BOARD_COPY, "fr-FR", "freelance");
    const byId = Object.fromEntries(facts.map((fact) => [fact.id, fact.value]));
    expect(facts.map((fact) => fact.id)).toEqual([
      "contract",
      "start",
      "duration",
      "salary",
      "dayrate",
      "remote",
      "office",
      "country",
      "city",
      "experience",
    ]);
    expect(byId.contract).toBe("Freelance · CDI");
    expect(byId.start).toContain("2026");
    expect(byId.duration).toBe("1 year");
    expect(byId.dayrate).toBe("400-600 €/j");
    expect(byId.salary).toBe("40k-45k €/an");
    expect(byId.experience).toBe("Senior (6-10 ans)");
    expect(offerContracts(row)).toEqual(["contractor", "permanent"]);
    expect(offerExperience(row)).toBe("senior");
    expect(offerDayRate(row)).toEqual({ min: 400, max: 600 });
    expect(offerSalary(row)).toEqual({ min: 40000, max: 45000 });
    expect(contractLabel("fixed-term", BOARD_COPY)).toBe("CDD");
    expect(contractLabel("part-time")).toBe("Part-time");
  });

  it("keeps the posted currency per market and falls back to the raw employment type", () => {
    const gulf = job({
      id: "d",
      country: "AE",
      currency: "AED",
      salary_min: 30000,
      salary_max: 35000,
      employment_type: "Full-time, Permanent",
      start_date: "asap",
      seniority: "3 years",
    });
    const facts = offerFacts(gulf, BOARD_COPY, "en-GB", "jobs");
    const byId = Object.fromEntries(facts.map((fact) => [fact.id, fact.value]));
    expect(byId.salary).toContain("30k-35k");
    expect(byId.salary).toContain("/an");
    expect(byId.salary).not.toContain("€");
    expect(byId.start).toBe("Des que possible");
    expect(offerContracts(gulf)).toEqual(["permanent"]);
    expect(offerExperience(gulf)).toBe("mid");
    const dayOnly = job({ id: "e", country: "GB", currency: "GBP", daily_rate_min: 550, track: "freelance" });
    const dayFacts = Object.fromEntries(
      offerFacts(dayOnly, BOARD_COPY, "en-GB", "freelance").map((fact) => [fact.id, fact.value]),
    );
    expect(dayFacts.dayrate).toBe("550 £/j");
    expect(dayFacts.salary).toBeUndefined();
  });
});
