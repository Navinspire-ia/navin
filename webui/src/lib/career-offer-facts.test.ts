import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import { offerFacts, type OfferFactCopy } from "@/lib/career-facts";

const COPY: OfferFactCopy = {
  unknown: "non renseigne",
  remote: "Remote",
  office: "Presentiel",
  country: "Pays",
  city: "Ville",
  pay: "TJM",
  duration: "Duree",
  workRemote: "Full remote",
  workHybrid: "Hybrid",
  workOnsite: "On site",
  perDay: "/ jour",
};

function value(facts: ReturnType<typeof offerFacts>, id: string): string {
  return facts.find((fact) => fact.id === id)?.value || "";
}

function job(partial: Partial<CareerOpportunity> = {}): CareerOpportunity {
  return {
    id: "job-1",
    source: "remotive",
    title: "Golang Engineer",
    stage: "matched",
    ...partial,
  };
}

describe("offerFacts", () => {
  it("keeps unknown as non renseigne and never invents a city or rate", () => {
    const facts = offerFacts(job({ title: "Golang Engineer" }), COPY);
    expect(value(facts, "remote")).toBe("non renseigne");
    expect(value(facts, "office")).toBe("non renseigne");
    expect(value(facts, "country")).toBe("non renseigne");
    expect(value(facts, "city")).toBe("non renseigne");
    expect(value(facts, "pay")).toBe("non renseigne");
    expect(value(facts, "duration")).toBe("non renseigne");
  });

  it("reads stored remote, country, city, rate and duration", () => {
    const facts = offerFacts(
      job({
        remote: "remote",
        country: "FR",
        location: "Paris, France",
        compensation: 650,
        currency: "EUR",
        duration: "6 months",
        hybrid_days_min: 0,
        hybrid_days_max: 0,
      }),
      COPY,
      "fr-FR",
      "freelance",
    );
    expect(value(facts, "remote")).toBe("Full remote");
    expect(value(facts, "country")).toBe("FR");
    expect(value(facts, "city")).toBe("Paris");
    expect(value(facts, "pay")).toMatch(/650/);
    expect(value(facts, "pay")).toMatch(/jour/);
    expect(value(facts, "duration")).toBe("6 months");
    expect(value(facts, "office")).toBe("non renseigne");
  });

  it("parses hybrid office days, TJM and mission length from the description", () => {
    const facts = offerFacts(
      job({
        description:
          "<p>Hybrid 2 days on site in Lyon. TJM 700 EUR. Mission 6 months.</p>",
        country: "FR",
        location: "Lyon, FR",
      }),
      COPY,
    );
    expect(value(facts, "remote")).toBe("Hybrid");
    expect(value(facts, "office")).toBe("2");
    expect(value(facts, "city")).toBe("Lyon");
    expect(value(facts, "pay")).toMatch(/700/);
    expect(value(facts, "duration")).toMatch(/6 months/i);
  });

  it("does not treat Remote as a city", () => {
    const facts = offerFacts(job({ location: "Remote", country: "REMOTE" }), COPY);
    expect(value(facts, "city")).toBe("non renseigne");
    expect(value(facts, "country")).toBe("non renseigne");
  });
});
