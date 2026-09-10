// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  CareerFilters,
  DAY_RATE_FLOORS,
  DURATION_CHOICES,
  SALARY_FLOORS,
  durationChoice,
} from "@/components/studio/career/CareerFilters";
import { emptyOfferFilter } from "@/lib/career-filters";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

describe("career result filters", () => {
  it("prints the board chips on the list and keeps the 19 fields behind Show filters", () => {
    const html = renderToStaticMarkup(
      createElement(CareerFilters, {
        rows: [],
        filter: emptyOfferFilter(),
        tx,
        onChange: () => {},
        leading: createElement("span", null, "Offers · 3"),
      }),
    );
    expect(html).toContain("Show filters");
    expect(html).toContain("Search the list");
    expect(html).toContain("Clear filters");
    expect(html).toContain("Offers · 3");
    // Free-Work style quick filters: one chip per board facet.
    for (const chip of ["Country", "Contract", "Duration", "Pay", "Remote", "Experience", "Posted"]) {
      expect(html).toContain(chip);
    }
    expect(html).toContain("career-quick-filters");
    expect(html).not.toContain("Result filters");
    expect(html).not.toContain("Search stays here");
    expect(html).not.toContain("Min pay");
    expect(html).not.toContain("Posted from");
    expect(html).not.toContain("Work mode");
  });

  it("maps the duration and pay chips onto the shared filter fields", () => {
    const filter = emptyOfferFilter();
    expect(durationChoice(filter)).toBe("");
    filter.minDuration = "3";
    filter.maxDuration = "6";
    expect(durationChoice(filter)).toBe("3to6");
    filter.maxDuration = "";
    expect(durationChoice(filter)).toBe("custom");
    expect(DURATION_CHOICES.find((choice) => choice.key === "gte12")?.min).toBe("12");
    expect(DAY_RATE_FLOORS).toContain("500");
    expect(SALARY_FLOORS).toContain("40000");
  });
});
