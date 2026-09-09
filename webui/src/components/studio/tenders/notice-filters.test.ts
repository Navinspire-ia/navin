// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { TenderNotice } from "@/lib/tenders-api";
import {
  NONE_FACET,
  applyFacetFilter,
  emptyFacetFilter,
  facetFilterActive,
  facetFilterCount,
  noticeDomain,
  noticeFacets,
  noticeIsoDay,
} from "@/components/studio/tenders/notice-filters";

function notice(partial: Partial<TenderNotice>): TenderNotice {
  return {
    id: partial.id || "tn-1",
    source_id: partial.source_id || "ted",
    country: partial.country || "FR",
    title: partial.title || "Cloud platform",
    stage: partial.stage || "analysed",
    ...partial,
  };
}

describe("tenders result filters", () => {
  const rows = [
    notice({
      id: "fr-cloud",
      country: "FR",
      sector: "Cloud",
      buyer: "DINUM",
      source_id: "ted",
      currency: "EUR",
      deadline: "2026-09-15",
      publication_date: "2026-08-01",
      fetched_at: Date.parse("2026-08-10T00:00:00Z") / 1000,
      score: 82,
      budget: 120000,
      go: true,
      stage: "go",
    }),
    notice({
      id: "ae-works",
      country: "AE",
      sector: "Works",
      buyer: "Dubai",
      source_id: "esupply",
      currency: "AED",
      deadline: "2026-10-01",
      publication_date: "2026-08-20",
      fetched_at: Date.parse("2026-08-21T00:00:00Z") / 1000,
      score: 40,
      budget: 8000,
      go: false,
      stage: "no-go",
    }),
    notice({
      id: "bare",
      country: "TN",
      title: "AI decision support",
      stage: "discovered",
    }),
  ];

  it("builds facets from every populated field plus empty domain", () => {
    const facets = noticeFacets(rows, ["AI"]);
    expect(facets.countries).toEqual(["AE", "FR", "TN"]);
    expect(facets.sectors).toEqual(["AI", "Cloud", "Works"]);
    expect(facets.buyers).toContain("DINUM");
    expect(facets.buyers).toContain(NONE_FACET);
    expect(facets.sources).toEqual(["esupply", "ted"]);
    expect(noticeDomain(rows[2], ["AI"])).toBe("AI");
    expect(noticeIsoDay(rows[0].deadline)).toBe("2026-09-15");
  });

  it("filters country, domain, dates, score and budget together", () => {
    const filter = emptyFacetFilter();
    filter.countries = ["FR"];
    filter.sectors = ["Cloud"];
    filter.deadlineFrom = "2026-09-01";
    filter.deadlineTo = "2026-09-30";
    filter.publishedFrom = "2026-08-01";
    filter.minScore = "70";
    filter.minBudget = "10000";
    filter.go = "go";
    const hits = applyFacetFilter(rows, filter, ["AI"]);
    expect(hits.map((row) => row.id)).toEqual(["fr-cloud"]);
    expect(facetFilterActive(filter)).toBe(true);
    expect(facetFilterCount(filter)).toBeGreaterThan(3);
  });

  it("keeps notices with no deadline when that option is on", () => {
    const filter = emptyFacetFilter();
    filter.deadlineState = "none";
    expect(applyFacetFilter(rows, filter).map((row) => row.id)).toEqual(["bare"]);
  });

  it("matches an empty domain when the none token is picked", () => {
    const filter = emptyFacetFilter();
    filter.sectors = [NONE_FACET];
    expect(applyFacetFilter(rows, filter, ["AI"]).map((row) => row.id)).toEqual([]);
    expect(applyFacetFilter(rows, filter, []).map((row) => row.id)).toEqual(["bare"]);
  });
});
