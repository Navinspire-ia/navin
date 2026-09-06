/** Display helpers for the Trading desk. Keep currency math out of JSX. */

const MONEY_CODES = new Set(["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "HKD", "SGD"]);

const EURO_COUNTRIES = new Set([
  "AT",
  "BE",
  "CY",
  "EE",
  "FI",
  "FR",
  "DE",
  "GR",
  "IE",
  "IT",
  "LV",
  "LT",
  "LU",
  "MT",
  "NL",
  "PT",
  "SK",
  "SI",
  "ES",
]);

export function bookCurrency(countries: string[] | null | undefined): "EUR" | "USD" {
  const codes = (countries || []).map((code) => String(code || "").trim().toUpperCase()).filter(Boolean);
  if (codes.length && codes.every((code) => EURO_COUNTRIES.has(code))) return "EUR";
  return "USD";
}

export function formatMoney(value: number | null | undefined, currency = "USD"): string {
  if (value == null || Number.isNaN(Number(value))) return "-";
  const raw = (currency || "USD").toUpperCase();
  const amount = Number(value);
  const digits = Math.abs(amount) > 0 && Math.abs(amount) < 1 ? 4 : 2;
  if (MONEY_CODES.has(raw)) {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: raw,
      maximumFractionDigits: digits,
    }).format(amount);
  }
  // Crypto tickers and NFT floors (ETH, SOL, USDT) are not ISO codes: keep the unit visible.
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: raw, maximumFractionDigits: digits }).format(amount);
  } catch {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(amount)} ${raw}`;
  }
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toFixed(digits)}%`;
}

export function bookEquity(
  cash: number | null | undefined,
  positions: { symbol: string; qty?: number; last?: number; avg?: number }[] | null | undefined,
  quotes: Record<string, { price?: number }> | null | undefined,
): number {
  const pos = (positions || []).reduce((sum, row) => {
    const px = quotes?.[row.symbol]?.price ?? row.last ?? row.avg ?? 0;
    return sum + Number(row.qty || 0) * Number(px || 0);
  }, 0);
  return Number(cash || 0) + pos;
}

export function sparkPath(
  points: { equity?: number }[] | null | undefined,
  width: number,
  height: number,
): string {
  const values = (points || [])
    .map((row) => Number(row.equity))
    .filter((value) => Number.isFinite(value));
  if (values.length < 2 || width <= 0 || height <= 0) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - ((value - min) / span) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

export function pendingOrders<T extends { status?: string }>(orders: T[] | null | undefined): T[] {
  return (orders || []).filter((row) => row.status === "pending");
}

/** Orders that left the desk but have not been booked yet (venue still working them). */
export function openVenueOrders<T extends { status?: string }>(orders: T[] | null | undefined): T[] {
  return (orders || []).filter((row) => row.status === "submitted" || row.status === "partial");
}

export type OrderTone = "wait" | "live" | "done" | "stopped" | "muted";

/** One visual family per order state so the list reads at a glance. */
export function orderStatusTone(status: string | null | undefined): OrderTone {
  switch (String(status || "").toLowerCase()) {
    case "pending":
      return "wait";
    case "submitted":
    case "partial":
      return "live";
    case "filled":
      return "done";
    case "blocked":
    case "rejected":
    case "failed":
      return "stopped";
    default:
      return "muted";
  }
}

export interface OrderTicketInput {
  side: "buy" | "sell";
  symbol: string;
  mode: "qty" | "notional";
  amount: string;
  thesis?: string;
}

/** Body for the ``order`` action, or an error key when the ticket cannot be sent. */
export function orderTicketBody(
  input: OrderTicketInput,
): { ok: true; body: Record<string, unknown> } | { ok: false; error: "symbol" | "amount" } {
  const symbol = input.symbol.trim().toUpperCase();
  if (!symbol) return { ok: false, error: "symbol" };
  const amount = Number(String(input.amount).replace(",", "."));
  if (!Number.isFinite(amount) || amount <= 0) return { ok: false, error: "amount" };
  const body: Record<string, unknown> = { side: input.side, symbol };
  body[input.mode === "qty" ? "qty" : "notional"] = amount;
  const thesis = (input.thesis || "").trim();
  if (thesis) body.thesis = thesis;
  return { ok: true, body };
}

export interface CandleGeometry {
  x: number;
  bodyY: number;
  bodyH: number;
  wickTop: number;
  wickBottom: number;
  up: boolean;
}

export interface ChartScale {
  min: number;
  max: number;
  y: (price: number) => number;
  x: (index: number) => number;
  step: number;
}

/** Price to pixel mapping shared by candles, overlays and level lines. */
export function chartScale(
  bars: { h: number; l: number }[],
  width: number,
  height: number,
  extra: Array<number | null | undefined> = [],
  pad = 0.04,
): ChartScale | null {
  if (!bars.length || width <= 0 || height <= 0) return null;
  const highs = bars.map((bar) => Number(bar.h)).filter((value) => Number.isFinite(value));
  const lows = bars.map((bar) => Number(bar.l)).filter((value) => Number.isFinite(value));
  const extras = extra.map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0);
  if (!highs.length || !lows.length) return null;
  let max = Math.max(...highs, ...extras);
  let min = Math.min(...lows, ...extras);
  const span = max - min || max * 0.02 || 1;
  max += span * pad;
  min -= span * pad;
  const range = max - min || 1;
  const step = width / bars.length;
  return {
    min,
    max,
    step,
    y: (price: number) => height - ((price - min) / range) * height,
    x: (index: number) => index * step + step / 2,
  };
}

export function candleGeometry(
  bars: { o: number; h: number; l: number; c: number }[],
  scale: ChartScale,
): CandleGeometry[] {
  return bars.map((bar, index) => {
    const open = Number(bar.o);
    const close = Number(bar.c);
    const top = scale.y(Math.max(open, close));
    const bottom = scale.y(Math.min(open, close));
    return {
      x: scale.x(index),
      bodyY: top,
      bodyH: Math.max(1, bottom - top),
      wickTop: scale.y(Number(bar.h)),
      wickBottom: scale.y(Number(bar.l)),
      up: close >= open,
    };
  });
}

/** Simple moving average aligned to *bars* (null until the window fills). */
export function movingAverage(values: number[], window: number): Array<number | null> {
  const out: Array<number | null> = [];
  let sum = 0;
  for (let index = 0; index < values.length; index += 1) {
    sum += values[index];
    if (index >= window) sum -= values[index - window];
    out.push(index >= window - 1 ? sum / window : null);
  }
  return out;
}

export function linePath(values: Array<number | null>, scale: ChartScale): string {
  let path = "";
  let pen = false;
  values.forEach((value, index) => {
    if (value == null || !Number.isFinite(value)) {
      pen = false;
      return;
    }
    path += `${pen ? " L" : (path ? " M" : "M")}${scale.x(index).toFixed(2)} ${scale.y(value).toFixed(2)}`;
    pen = true;
  });
  return path;
}

/** Fraction of the way from stop to entry the last price sits; 0 = at the stop. */
export function stopDistancePct(last: number | null | undefined, stop: number | null | undefined): number | null {
  if (last == null || stop == null || !Number.isFinite(Number(last)) || !Number.isFinite(Number(stop))) return null;
  if (Number(last) <= 0) return null;
  return ((Number(last) - Number(stop)) / Number(last)) * 100;
}

export function formatCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(Number(value));
}

export function formatQty(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  const qty = Number(value);
  if (Number.isInteger(qty)) return String(qty);
  return qty.toFixed(Math.abs(qty) < 1 ? 6 : 4).replace(/\.?0+$/, "");
}
