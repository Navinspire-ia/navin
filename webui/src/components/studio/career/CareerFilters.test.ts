// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CareerFilters } from "@/components/studio/career/CareerFilters";
import { emptyOfferFilter } from "@/lib/career-filters";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

describe("career result filters", () => {
  it("keeps the 19 fields off the list until Show filters", () => {
    const html = renderToStaticMarkup(
      createElement(CareerFilters, {
        rows: [],
        filter: emptyOfferFilter(),
        tx,
        onChange: () => {},
      }),
    );
    expect(html).toContain("Show filters");
    expect(html).toContain("Search the list");
    expect(html).toContain("Clear filters");
    expect(html).not.toContain("Result filters");
    expect(html).not.toContain("Search stays here");
    expect(html).not.toContain("Min pay");
    expect(html).not.toContain("Posted from");
    expect(html).not.toContain("Work mode");
  });
});
