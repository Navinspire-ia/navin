// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  NONE_FACET,
  applyLeadFilter,
  bucketCounts,
  emptyLeadFilter,
  leadFacets,
  leadFilterCount,
} from "@/components/studio/leads/lead-filters";
import type { LeadRow } from "@/lib/leads-api";

const NOW = Date.now() / 1000;
const ROWS: LeadRow[] = [
  { id: "a", company: "Acme Data", country: "FR", sector: "SaaS", source: "sirene", score: 84, stage: "qualified", email: "cto@acme.fr", email_status: "verified", why: ["Hiring a head of data"] },
  { id: "b", company: "Bolt Logistics", country: "BE", sector: "logistics", source: "web", score: 40, stage: "new", signals: [{ kind: "hiring", text: "Supply chain manager" }] },
  { id: "c", company: "Cold Corp", country: "", source: "osm", score: 61, stage: "contacted", email: "hello@cold.io", sequence: { steps: [{ n: 1, status: "sent" }, { n: 2, status: "drafted", due_at: NOW - 60 }] } },
  { id: "z", company: "Gone", country: "FR", score: 99, archived: true },
];

describe("lead facets and filters", () => {
  it("lists what is present, missing values last", () => {
    const facets = leadFacets(ROWS.slice(0, 3));
    expect(facets.countries).toEqual(["BE", "FR", NONE_FACET]);
    expect(facets.sources).toEqual(["osm", "sirene", "web"]);
    expect(facets.sectors).toEqual(["logistics", "SaaS", NONE_FACET]);
    expect(facets.stages).toEqual(["contacted", "new", "qualified"]);
  });

  it("counts every bucket without archived rows", () => {
    const counts = bucketCounts(ROWS);
    expect(counts.all).toBe(3);
    expect(counts.strong).toBe(1);
    expect(counts.email).toBe(2);
    expect(counts.verified).toBe(1);
    expect(counts.due).toBe(1);
    expect(counts.signals).toBe(1);
    expect(counts.pipeline).toBe(1);
  });

  it("combines chips and free text", () => {
    const live = ROWS.slice(0, 3);
    expect(applyLeadFilter(live, { ...emptyLeadFilter(), countries: ["FR"] }).map((row) => row.id)).toEqual(["a"]);
    expect(applyLeadFilter(live, { ...emptyLeadFilter(), countries: [NONE_FACET] }).map((row) => row.id)).toEqual(["c"]);
    expect(applyLeadFilter(live, { ...emptyLeadFilter(), minScore: "55" }).map((row) => row.id)).toEqual(["a", "c"]);
    expect(applyLeadFilter(live, { ...emptyLeadFilter(), email: "none" }).map((row) => row.id)).toEqual(["b"]);
    expect(applyLeadFilter(live, { ...emptyLeadFilter(), email: "verified" }).map((row) => row.id)).toEqual(["a"]);
    expect(applyLeadFilter(live, { ...emptyLeadFilter(), stages: ["new", "contacted"] }).map((row) => row.id)).toEqual(["b", "c"]);
    expect(applyLeadFilter(live, emptyLeadFilter(), "supply chain").map((row) => row.id)).toEqual(["b"]);
    expect(applyLeadFilter(live, emptyLeadFilter(), "head of data").map((row) => row.id)).toEqual(["a"]);
    expect(leadFilterCount({ ...emptyLeadFilter(), countries: ["FR"], minScore: "80", email: "has" })).toBe(3);
  });
});

describe("sector labels", () => {
  it("turns NACE section letters and shouting text into words", async () => {
    const { sectorLabel } = await import("@/components/studio/leads/sector-label");
    expect(sectorLabel("N", "en")).toBe("Administrative and support services");
    expect(sectorLabel("J", "fr")).toBe("Information et communication");
    expect(sectorLabel("LOGISTICS", "en")).toBe("Logistics");
    expect(sectorLabel("SaaS", "en")).toBe("SaaS");
    expect(sectorLabel("", "en")).toBe("");
  });
});
