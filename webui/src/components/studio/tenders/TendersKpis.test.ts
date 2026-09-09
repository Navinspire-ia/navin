// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  buildTenderKpis,
  KPI_CELL_CLASS,
  KPI_GRID_CLASS,
  KPI_VALUE_CLASS,
  TenderKpiGrid,
  type TenderKpiItem,
} from "@/components/studio/tenders/TendersKpis";

const LABELS = {
  fresh: "New",
  open: "Open",
  qualified: "Qualified",
  deadline: "Deadline 7d",
  weighted: "Weighted pipeline",
};

function sampleItems(): TenderKpiItem[] {
  return buildTenderKpis({
    fresh: 3,
    open: 34,
    qualified: 7,
    deadline7d: 2,
    weighted: 3_100_000,
    labels: LABELS,
  });
}

function cellHtml(html: string, id: TenderKpiItem["id"]): string {
  const marker = `data-testid="tenders-kpi-${id}"`;
  const at = html.indexOf(marker);
  expect(at).toBeGreaterThan(-1);
  const start = html.lastIndexOf("<div", at);
  let depth = 0;
  for (let i = start; i < html.length; i += 1) {
    if (html.startsWith("<div", i)) {
      depth += 1;
      i += 3;
      continue;
    }
    if (html.startsWith("</div>", i)) {
      depth -= 1;
      if (depth === 0) return html.slice(start, i + "</div>".length);
      i += 5;
    }
  }
  throw new Error(`unclosed KPI cell ${id}`);
}

describe("tenders KPIs", () => {
  it("builds five distinct formatted values", () => {
    const items = sampleItems();
    expect(items).toHaveLength(5);
    expect(items.map((item) => item.id)).toEqual([
      "new",
      "open",
      "qualified",
      "deadline",
      "weighted",
    ]);
    expect(items[0].value).toBe("3");
    expect(items[1].value).toBe("34");
    expect(items[2].value).toBe("7");
    expect(items[3].value).toBe("2");
    expect(items[4].value).toBe("3.1 M");
  });

  it("defaults missing new notices to zero", () => {
    const items = buildTenderKpis({
      open: 1,
      qualified: 0,
      deadline7d: 0,
      weighted: 0,
      labels: LABELS,
    });
    expect(items.find((item) => item.id === "new")?.value).toBe("0");
  });

  it("renders each number in its own cell", () => {
    const items = sampleItems();
    const html = renderToStaticMarkup(createElement(TenderKpiGrid, { items }));
    expect(html).toContain('data-testid="tenders-kpi-grid"');
    expect(html).not.toContain("position:absolute");
    const glued = items.map((item) => item.value).join("");
    expect(html).not.toContain(glued);
    for (const item of items) {
      const cell = cellHtml(html, item.id);
      expect(cell).toContain(item.value);
      expect(cell).toContain(item.label);
    }
  });

  it("locks a responsive grid that keeps numbers inside their cell", () => {
    expect(KPI_GRID_CLASS).toContain("grid");
    expect(KPI_GRID_CLASS).toContain("min-w-0");
    expect(KPI_GRID_CLASS).toContain("grid-cols-2");
    expect(KPI_GRID_CLASS).toContain("xl:grid-cols-4");
    expect(KPI_GRID_CLASS).not.toContain("flex-wrap");
    expect(KPI_GRID_CLASS).not.toContain("gap-x-10");
    expect(KPI_GRID_CLASS).not.toContain("xl:grid-cols-5");
    expect(KPI_CELL_CLASS).toContain("min-w-0");
    expect(KPI_VALUE_CLASS).toContain("text-left");
    expect(KPI_VALUE_CLASS).not.toContain("truncate");
  });
});
