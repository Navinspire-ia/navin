import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import {
  NONE_FACET,
  applyOfferFilter,
  emptyOfferFilter,
  offerDomain,
  offerFacets,
  offerFilterActive,
  offerFilterCount,
  offerIsoDay,
} from "@/lib/career-filters";

function offer(partial: Partial<CareerOpportunity> & { id: string }): CareerOpportunity {
  return {
    source: "remotive",
    title: partial.title || partial.id,
    stage: "discovered",
    ...partial,
  };
}

describe("career result filters", () => {
  const rows = [
    offer({
      id: "fr-data",
      title: "Data Engineer",
      company: "Acme",
      country: "FR",
      source: "remotive",
      stack: ["Python", "Spark"],
      remote: "remote",
      track: "freelance",
      stage: "matched",
      currency: "EUR",
      languages: ["fr", "en"],
      bucket: "perfect",
      match_score: 88,
      compensation: 650,
      posted_at: "2026-08-10",
      created_at: Date.parse("2026-08-12T00:00:00Z") / 1000,
    }),
    offer({
      id: "ae-front",
      title: "Frontend Engineer",
      company: "Gulf Labs",
      country: "AE",
      source: "web-job-search",
      stack: ["React"],
      remote: "hybrid",
      track: "jobs",
      stage: "discovered",
      currency: "AED",
      languages: ["en"],
      bucket: "good",
      match_score: 62,
      compensation: 18000,
      posted_at: "2026-08-20",
      created_at: Date.parse("2026-08-21T00:00:00Z") / 1000,
    }),
    offer({
      id: "bare",
      title: "Office coordinator",
      country: "TN",
      source: "",
    }),
  ];

  it("builds facets from every populated field plus empty domain", () => {
    const facets = offerFacets(rows, ["AI"]);
    expect(facets.countries).toEqual(["AE", "FR", "TN"]);
    expect(facets.domains).toEqual(["Python", "React", NONE_FACET]);
    expect(facets.companies).toContain("Acme");
    expect(facets.companies).toContain(NONE_FACET);
    expect(facets.sources).toEqual(["remotive", "web-job-search", NONE_FACET]);
    expect(facets.stacks).toEqual(["Python", "React", "Spark"]);
    expect(offerDomain({ ...rows[2], title: "AI decision support" }, ["AI"])).toBe("AI");
    expect(offerDomain(rows[2], ["AI"])).toBe("");
    expect(offerIsoDay(rows[0].posted_at)).toBe("2026-08-10");
  });

  it("filters country, domain, dates, score and pay together", () => {
    const filter = emptyOfferFilter();
    filter.countries = ["FR"];
    filter.domains = ["Python"];
    filter.postedFrom = "2026-08-01";
    filter.postedTo = "2026-08-15";
    filter.minScore = "70";
    filter.minPay = "500";
    filter.remotes = ["remote"];
    const hits = applyOfferFilter(rows, filter, ["AI"]);
    expect(hits.map((row) => row.id)).toEqual(["fr-data"]);
    expect(offerFilterActive(filter)).toBe(true);
    expect(offerFilterCount(filter)).toBeGreaterThan(3);
  });

  it("matches query across title, company, stack and country", () => {
    const filter = emptyOfferFilter();
    filter.query = "spark";
    expect(applyOfferFilter(rows, filter).map((row) => row.id)).toEqual(["fr-data"]);
    filter.query = "gulf";
    expect(applyOfferFilter(rows, filter).map((row) => row.id)).toEqual(["ae-front"]);
  });

  it("matches an empty domain when the none token is picked", () => {
    const filter = emptyOfferFilter();
    filter.domains = [NONE_FACET];
    expect(applyOfferFilter(rows, filter, ["AI"]).map((row) => row.id)).toEqual(["bare"]);
    expect(applyOfferFilter(rows, filter, []).map((row) => row.id)).toEqual(["bare"]);
  });
});
