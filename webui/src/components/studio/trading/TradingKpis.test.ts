import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  buildTradingKpis,
  KPI_CARD_CLASS,
  KPI_CELL_CLASS,
  KPI_GRID_CLASS,
  KPI_VALUE_CLASS,
  TradingKpiGrid,
  type TradingKpiItem,
} from "@/components/studio/trading/TradingKpis";

const LABELS = { equity: "Equity", cash: "Cash", return: "Return", drawdown: "Max DD" };

function sampleItems(): TradingKpiItem[] {
  return buildTradingKpis({
    equity: 20_000,
    cash: 1_234.56,
    returnPct: 1.5,
    drawdownPct: 3.2,
    currency: "EUR",
    labels: LABELS,
  });
}

function cellHtml(html: string, id: TradingKpiItem["id"]): string {
  const marker = `data-testid="trading-kpi-${id}"`;
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

describe("trading KPIs", () => {
  it("builds four distinct formatted values (the overlap bug glued them into one string)", () => {
    const items = sampleItems();
    expect(items).toHaveLength(4);
    expect(items.map((item) => item.id)).toEqual(["equity", "cash", "return", "drawdown"]);
    const values = items.map((item) => item.value);
    expect(new Set(values).size).toBe(4);
    expect(values[0]).toMatch(/20/);
    expect(values[1]).toMatch(/1.?234/);
    expect(values[2]).toBe("1.5%");
    expect(values[3]).toBe("3.2%");
    const glued = values.join("");
    expect(glued).toMatch(/20/);
    expect(values.some((value) => value === glued)).toBe(false);
  });

  it("renders each number in its own cell so DOM text is not one concatenated line", () => {
    const items = sampleItems();
    const html = renderToStaticMarkup(createElement(TradingKpiGrid, { items }));
    expect(html).toContain('data-testid="trading-kpi-grid"');
    expect(html).not.toContain("<p");
    expect(html).not.toContain("position:absolute");
    expect(html).not.toContain("position: absolute");

    const glued = items.map((item) => item.value).join("");
    expect(html).not.toContain(glued);

    for (const item of items) {
      const cell = cellHtml(html, item.id);
      expect(cell).toContain(item.value);
      expect(cell).toContain(item.label);
      for (const other of items) {
        if (other.id === item.id) continue;
        expect(cell).not.toContain(other.value);
        expect(cell).not.toContain(other.label);
      }
    }
  });

  it("keeps the same 20k equity/cash pair in two nodes instead of €20,000.00€20,000.00", () => {
    const items = buildTradingKpis({
      equity: 20_000,
      cash: 20_000,
      returnPct: 0,
      drawdownPct: 0,
      currency: "EUR",
      labels: LABELS,
    });
    const html = renderToStaticMarkup(createElement(TradingKpiGrid, { items }));
    const equity = cellHtml(html, "equity");
    const cash = cellHtml(html, "cash");
    const ret = cellHtml(html, "return");
    expect(equity).toContain(items[0].value);
    expect(cash).toContain(items[1].value);
    expect(ret).toContain("0.0%");
    expect(equity.includes(cash) || cash.includes(equity)).toBe(false);
    expect(html).not.toContain(`${items[0].value}${items[1].value}${items[2].value}`);
  });

  it("locks grid CSS that keeps numbers inside their cell", () => {
    expect(KPI_GRID_CLASS).toContain("grid");
    expect(KPI_GRID_CLASS).toContain("min-w-0");
    expect(KPI_GRID_CLASS).toContain("lg:grid-cols-4");
    expect(KPI_GRID_CLASS).not.toContain("absolute");
    expect(KPI_CELL_CLASS).toContain("min-w-0");
    expect(KPI_CARD_CLASS).toContain("overflow-hidden");
    expect(KPI_CARD_CLASS).toContain("min-w-0");
    expect(KPI_CARD_CLASS).toContain("isolate");
    expect(KPI_VALUE_CLASS).toContain("text-center");
    expect(KPI_VALUE_CLASS).toContain("block");
    expect(KPI_VALUE_CLASS).not.toContain("truncate");
    expect(KPI_VALUE_CLASS).toContain("w-full");
    expect(KPI_VALUE_CLASS).toContain("min-w-0");
  });
});
