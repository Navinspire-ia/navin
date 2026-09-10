// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import {
  NONE_FACET,
  applyOfferFilter,
  emptyOfferFilter,
  offerDomain,
  offerDurationMonths,
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

  describe("board filters: contract, experience, freshness, duration, pay floors", () => {
    const today = new Date().toISOString().slice(0, 10);
    const lastMonth = new Date(Date.now() - 40 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const board = [
      offer({
        id: "mission",
        title: "AI Architect",
        country: "FR",
        currency: "EUR",
        contracts: ["contractor"],
        experience_level: "senior",
        duration: "1 year",
        duration_months: 12,
        daily_rate_min: 400,
        daily_rate_max: 600,
        posted_at: today,
      }),
      offer({
        id: "cdi",
        title: "Data Engineer",
        country: "FR",
        currency: "EUR",
        contracts: ["permanent"],
        experience_level: "mid",
        salary_min: 40000,
        salary_max: 45000,
        posted_at: lastMonth,
      }),
      offer({
        id: "legacy",
        title: "ML Engineer",
        country: "BE",
        employment_type: "CDD 4 mois",
        duration: "4 mois",
        seniority: "Junior",
        posted_at: today,
      }),
    ];

    it("lists contract and experience facets in board order", () => {
      const facets = offerFacets(board);
      expect(facets.contracts).toEqual(["contractor", "permanent", "fixed-term"]);
      expect(facets.experiences).toEqual(["junior", "mid", "senior"]);
    });

    it("keeps only picked contracts and experience bands", () => {
      const filter = emptyOfferFilter();
      filter.contracts = ["contractor", "fixed-term"];
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["mission", "legacy"]);
      filter.contracts = [];
      filter.experiences = ["mid"];
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["cdi"]);
      expect(offerFilterCount(filter)).toBe(1);
    });

    it("drops stale offers when a freshness window is picked", () => {
      const filter = emptyOfferFilter();
      filter.postedWithin = "7";
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["mission", "legacy"]);
      filter.postedWithin = "";
      expect(applyOfferFilter(board, filter)).toHaveLength(3);
    });

    it("reads mission length from the structured field or the posted label", () => {
      expect(offerDurationMonths(board[0])).toBe(12);
      expect(offerDurationMonths(board[2])).toBe(4);
      expect(offerDurationMonths(board[1])).toBe(0);
      const filter = emptyOfferFilter();
      filter.minDuration = "6";
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["mission"]);
      filter.minDuration = "";
      filter.maxDuration = "6";
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["legacy"]);
    });

    it("applies day rate and yearly salary floors on the posted ranges", () => {
      const filter = emptyOfferFilter();
      filter.minDayRate = "500";
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["mission"]);
      filter.minDayRate = "700";
      expect(applyOfferFilter(board, filter)).toEqual([]);
      filter.minDayRate = "";
      filter.minSalary = "42000";
      expect(applyOfferFilter(board, filter).map((row) => row.id)).toEqual(["cdi"]);
      expect(offerFilterCount(filter)).toBe(1);
    });
  });
});
