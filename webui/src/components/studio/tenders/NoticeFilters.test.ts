// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NoticeFilters, publishedChoice, publishedFloor } from "@/components/studio/tenders/NoticeFilters";
import { emptyFacetFilter } from "@/components/studio/tenders/notice-filters";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

describe("tender result filters", () => {
  it("prints the board chips on one toolbar line and keeps the field grid hidden until Show filters", () => {
    const html = renderToStaticMarkup(
      createElement(NoticeFilters, {
        rows: [
          { id: "a", source_id: "ted", country: "FR", buyer: "DINUM", title: "Cloud", stage: "matched" },
        ],
        crafts: [],
        filter: emptyFacetFilter(),
        query: "",
        tx,
        onChange: () => {},
        onQuery: () => {},
        leading: createElement("span", null, "All · 1"),
      }),
    );
    expect(html).toContain("Show filters");
    expect(html).toContain("Search a notice");
    expect(html).toContain("All · 1");
    expect(html).toContain("notice-filters-toolbar");
    expect(html).toContain("notice-quick-filters");
    for (const chip of ["Country", "Domain", "Buyer", "Budget", "Deadline", "Published", "GO / NO-GO"]) {
      expect(html).toContain(chip);
    }
    expect(html).toContain('data-testid="notice-chip-country"');
    expect(html).toContain('data-testid="notice-chip-buyer"');
    expect(html).not.toContain("Result filters");
    expect(html).not.toContain("Country, domain, dates, score, budget, buyer and source.");
    expect(html).not.toContain("Rescore");
    expect(html).toContain("notice-toggle-filters");
    expect(html).not.toContain("notice-filter-fields");
    expect(html).not.toContain("Min budget");
    expect(html).not.toContain("Deadline from");
  });

  it("maps the Published chip onto publishedFrom", () => {
    const now = new Date("2026-09-10T12:00:00Z");
    expect(publishedFloor("1", now)).toBe("2026-09-10");
    expect(publishedFloor("7", now)).toBe("2026-09-04");
    const filter = emptyFacetFilter();
    expect(publishedChoice(filter, now)).toBe("");
    filter.publishedFrom = "2026-09-04";
    expect(publishedChoice(filter, now)).toBe("7");
    filter.publishedFrom = "2026-01-01";
    expect(publishedChoice(filter, now)).toBe("custom");
  });
});
