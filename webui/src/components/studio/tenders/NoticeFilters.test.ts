import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NoticeFilters } from "@/components/studio/tenders/NoticeFilters";
import { emptyFacetFilter } from "@/components/studio/tenders/notice-filters";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

describe("tender result filters", () => {
  it("keeps the field grid hidden until Show filters", () => {
    const html = renderToStaticMarkup(
      createElement(NoticeFilters, {
        rows: [],
        crafts: [],
        filter: emptyFacetFilter(),
        query: "",
        tx,
        onChange: () => {},
        onQuery: () => {},
      }),
    );
    expect(html).toContain("Show filters");
    expect(html).toContain("Search a notice");
    expect(html).toContain("sm:flex-row sm:items-end");
    expect(html).not.toContain("Result filters");
    expect(html).not.toContain("Country, domain, dates, score, budget, buyer and source.");
    expect(html).not.toContain("Rescore");
    expect(html).toContain("notice-toggle-filters");
    expect(html).not.toContain("notice-filter-fields");
    expect(html).not.toContain("Min budget");
    expect(html).not.toContain("GO / NO-GO");
    expect(html).not.toContain("Deadline from");
  });
});
