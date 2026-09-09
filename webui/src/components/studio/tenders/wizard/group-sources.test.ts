// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { filterSourcesByQuery, groupSourcesByZone } from "@/components/studio/tenders/wizard/group-sources";
import type { TenderSource } from "@/lib/tenders-api";

function src(id: string, zone: string): TenderSource {
  return {
    id,
    name: id,
    country: "FR",
    zone,
    priority: "P1",
    ingest: "api",
    url: "https://example.gov",
  };
}

describe("tenders source grouping", () => {
  it("groups catalog rows by zone in a stable order", () => {
    const grouped = groupSourcesByZone([
      src("boamp", "europe"),
      src("sam-gov", "americas"),
      src("afdb", "africa"),
      src("ted", "europe"),
    ]);
    expect(grouped.map((row) => row.zone)).toEqual(["europe", "africa", "americas"]);
    expect(grouped[0].sources.map((row) => row.id)).toEqual(["boamp", "ted"]);
  });

  it("filters catalog rows by name, country or id", () => {
    const rows = [src("ted", "europe"), src("afdb", "africa")];
    rows[0].name = "TED Europa";
    expect(filterSourcesByQuery(rows, "ted").map((row) => row.id)).toEqual(["ted"]);
    expect(filterSourcesByQuery(rows, "africa").map((row) => row.id)).toEqual(["afdb"]);
    expect(filterSourcesByQuery(rows, "")).toHaveLength(2);
  });

  it("filters catalog rows by official URL or access", () => {
    const rows = [src("ted", "europe"), src("afdb", "africa")];
    rows[0].url = "https://ted.europa.eu/";
    rows[0].access = "api";
    rows[1].url = "https://www.afdb.org/en/projects-and-operations/procurement";
    rows[1].access = "scrape";
    expect(filterSourcesByQuery(rows, "ted.europa.eu").map((row) => row.id)).toEqual(["ted"]);
    expect(filterSourcesByQuery(rows, "scrape").map((row) => row.id)).toEqual(["afdb"]);
  });
});
