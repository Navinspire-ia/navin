import { describe, expect, it } from "vitest";

import {
  bookCurrency,
  bookEquity,
  candleGeometry,
  chartScale,
  formatMoney,
  formatPct,
  formatQty,
  linePath,
  movingAverage,
  openVenueOrders,
  orderStatusTone,
  orderTicketBody,
  pendingOrders,
  sparkPath,
  stopDistancePct,
} from "./trading-format";

describe("trading-format", () => {
  it("keeps crypto and NFT units visible instead of pretending they are dollars", () => {
    expect(formatMoney(7.5, "ETH")).toMatch(/ETH/);
    expect(formatMoney(0.0421, "BTC")).toMatch(/0\.0421/);
    expect(formatMoney(12, "USDT")).toMatch(/USDT/);
    expect(formatMoney(26.5, "GBP")).toMatch(/26\.5/);
  });

  it("prints fractional crypto quantities without trailing zeros", () => {
    expect(formatQty(3)).toBe("3");
    expect(formatQty(0.5)).toBe("0.5");
    expect(formatQty(0.000123)).toBe("0.000123");
    expect(formatQty(null)).toBe("-");
  });

  it("builds an order body only from a complete ticket", () => {
    expect(orderTicketBody({ side: "buy", symbol: "", mode: "qty", amount: "1" })).toEqual({ ok: false, error: "symbol" });
    expect(orderTicketBody({ side: "buy", symbol: "aapl", mode: "qty", amount: "0" })).toEqual({ ok: false, error: "amount" });
    expect(orderTicketBody({ side: "sell", symbol: " nvda ", mode: "notional", amount: "1 500".replace(" ", ""), thesis: " trim " })).toEqual({
      ok: true,
      body: { side: "sell", symbol: "NVDA", notional: 1500, thesis: "trim" },
    });
    expect(orderTicketBody({ side: "buy", symbol: "BTC-USD", mode: "qty", amount: "0,25" })).toEqual({
      ok: true,
      body: { side: "buy", symbol: "BTC-USD", qty: 0.25 },
    });
  });

  it("groups order states into visual families", () => {
    expect(orderStatusTone("pending")).toBe("wait");
    expect(orderStatusTone("submitted")).toBe("live");
    expect(orderStatusTone("partial")).toBe("live");
    expect(orderStatusTone("filled")).toBe("done");
    expect(orderStatusTone("blocked")).toBe("stopped");
    expect(orderStatusTone(undefined)).toBe("muted");
    expect(openVenueOrders([{ status: "submitted" }, { status: "filled" }, { status: "partial" }])).toHaveLength(2);
  });

  it("scales candles into the chart box with wicks outside bodies", () => {
    const bars = [
      { o: 10, h: 12, l: 9, c: 11 },
      { o: 11, h: 11.5, l: 8, c: 8.5 },
    ];
    const scale = chartScale(bars, 200, 100);
    expect(scale).not.toBeNull();
    const candles = candleGeometry(bars, scale!);
    expect(candles).toHaveLength(2);
    expect(candles[0].up).toBe(true);
    expect(candles[1].up).toBe(false);
    expect(candles[0].wickTop).toBeLessThan(candles[0].bodyY);
    expect(candles[0].wickBottom).toBeGreaterThan(candles[0].bodyY + candles[0].bodyH);
    expect(candles[1].x).toBeGreaterThan(candles[0].x);
    expect(chartScale([], 200, 100)).toBeNull();
  });

  it("draws overlays only where the average exists", () => {
    const avg = movingAverage([1, 2, 3, 4], 2);
    expect(avg).toEqual([null, 1.5, 2.5, 3.5]);
    const scale = chartScale([{ h: 4, l: 1 }, { h: 4, l: 1 }, { h: 4, l: 1 }, { h: 4, l: 1 }], 100, 50)!;
    const path = linePath(avg, scale);
    expect(path.startsWith("M")).toBe(true);
    expect(path.split("L")).toHaveLength(3);
    expect(linePath([null, null], scale)).toBe("");
  });

  it("measures how far the tape sits above a stop", () => {
    expect(stopDistancePct(100, 95)).toBeCloseTo(5);
    expect(stopDistancePct(100, null)).toBeNull();
    expect(stopDistancePct(0, 1)).toBeNull();
  });

  it("uses euro only when every country is in the euro area", () => {
    expect(bookCurrency(["FR"])).toBe("EUR");
    expect(bookCurrency(["FR", "DE"])).toBe("EUR");
    expect(bookCurrency(["AE"])).toBe("USD");
    expect(bookCurrency(["FR", "AE"])).toBe("USD");
    expect(bookCurrency([])).toBe("USD");
  });

  it("formats money and percent without inventing zeros", () => {
    expect(formatMoney(null)).toBe("-");
    expect(formatPct(undefined)).toBe("-");
    expect(formatPct(12.34)).toBe("12.3%");
    expect(formatMoney(20000, "EUR")).toMatch(/20/);
    expect(formatMoney(227.9, "USD")).toMatch(/227/);
  });

  it("marks the book from last quotes, not stale cash alone", () => {
    expect(
      bookEquity(10_000, [{ symbol: "AAPL", qty: 2, avg: 100 }], { AAPL: { price: 150 } }),
    ).toBe(10_300);
  });

  it("builds a sparkline only when there is a real curve", () => {
    expect(sparkPath([{ equity: 1 }], 80, 28)).toBe("");
    expect(sparkPath([{ equity: 1 }, { equity: 2 }], 80, 28)).toMatch(/^M/);
  });

  it("keeps only orders waiting for a human", () => {
    expect(
      pendingOrders([
        { status: "pending" },
        { status: "filled" },
        { status: "blocked" },
      ]),
    ).toHaveLength(1);
  });
});
