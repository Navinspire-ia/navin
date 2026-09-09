// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { apiBodyHeaders, apiRequest } from "@/lib/api";

/** Trading desk hash. Stay in the WebView on Linux, Windows and macOS. */
export const TRADING_DESK_HASH = "#/trading";

export type TradingAction =
  | "BUY"
  | "SELL"
  | "HOLD"
  | "WAIT";

export type TradingLoopKind = "daily" | "weekdays" | "weekend" | "weekly" | "monthly";

export interface TradingLoopSchedule {
  kind: TradingLoopKind;
  hour: number;
  minute: number;
  weekday?: number;
  day?: number | "last";
  tz?: string | null;
  expr?: string;
}

export interface TradingQuote {
  symbol: string;
  name?: string;
  price: number;
  change?: number;
  change_pct?: number;
  volume?: number;
  currency?: string;
  source?: string;
  market_state?: string;
  exchange?: string;
}

export interface TradingPosition {
  id: string;
  symbol: string;
  qty: number;
  avg: number;
  last?: number;
  value?: number;
  unrealized?: number;
  stop?: number | null;
  take?: number | null;
  sector?: string;
  thesis?: string;
  currency?: string;
  broker?: string;
  opened_at?: number;
}

export type TradingOrderStatus =
  | "pending"
  | "submitted"
  | "partial"
  | "filled"
  | "blocked"
  | "rejected"
  | "failed"
  | "cancelled"
  | string;

export interface TradingOrder {
  id: string;
  side: string;
  symbol: string;
  qty: number;
  price: number;
  status: TradingOrderStatus;
  reason?: string;
  confidence?: number;
  thesis?: string;
  blocks?: string[];
  warnings?: string[];
  t?: number;
  origin?: string;
  broker?: string;
  venue_symbol?: string;
  venue_id?: string;
  fee?: number;
  filled_qty?: number;
  fill_price?: number | null;
  pnl?: number;
  currency?: string;
  quote_currency?: string;
  fx_rate?: number;
  error?: string;
}

export interface TradingResearch {
  symbol: string;
  action?: TradingAction | string;
  confidence?: number;
  thesis?: string;
  note?: string;
  scores?: Record<string, number | null | undefined>;
  debate?: {
    judge?: { action?: string; why?: string; consensus?: string };
    bull?: { reasons?: string[] };
    bear?: { reasons?: string[] };
  };
  name?: string;
  price?: number;
  t?: number;
}

export interface TradingBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v?: number;
}

export interface TradingTechnical {
  last?: number | null;
  sma20?: number | null;
  sma50?: number | null;
  sma200?: number | null;
  rsi?: number | null;
  macd?: number | null;
  macd_signal?: number | null;
  macd_hist?: number | null;
  atr?: number | null;
  support?: number | null;
  resistance?: number | null;
  volume?: number | null;
  volume_avg?: number | null;
  uptrend?: boolean;
  bars?: number;
}

export interface TradingChart {
  symbol: string;
  range: string;
  currency: string;
  name: string;
  bars: TradingBar[];
  technical: TradingTechnical;
}

export type TradingChartRange = "1mo" | "3mo" | "6mo" | "1y" | "2y";

export interface TradingBrokerRow {
  id: string;
  active: boolean;
  configured: boolean;
  secrets: { name: string; set: boolean }[];
  sandbox: boolean;
  assets: string;
}

export interface TradingBrokerStatus {
  broker: string;
  live: boolean;
  sandbox: boolean;
  fee_bps: number;
  slippage_bps: number;
  guard_interval_s: number;
  brokers: TradingBrokerRow[];
}

export interface TradingAiRouting {
  enabled: boolean;
  routed: number;
  tasks: { task: string; role: string; preset: string; model: string }[];
}

export interface TradingGuard {
  last_guard?: number;
  last_guard_result?: string;
  interval_s?: number;
}

export interface TradingBenchmark {
  alpha?: number | null;
  beta?: number | null;
  benchmark_return_pct?: number | null;
  portfolio_return_pct?: number | null;
  days?: number;
}

export interface TradingBacktest {
  id: string;
  symbol: string;
  t?: number;
  strategy_name?: string;
  bars?: number;
  range?: string;
  starting_cash?: number;
  fee_bps?: number;
  slippage_bps?: number;
  fees_paid?: number;
  benchmark?: string;
  engine?: string;
  fundamentals?: string;
  entries?: number;
  return_pct?: number;
  max_drawdown?: number;
  sharpe?: number | null;
  sortino?: number | null;
  trades?: number;
  win_rate?: number;
  bench_alpha?: number | null;
  bench_beta?: number | null;
  bench_benchmark_return_pct?: number | null;
  curve?: { t: number; equity: number }[];
  signals?: { t: number; side: string; price: number; qty: number; confidence?: number }[];
  trade_log?: { entry?: number; exit?: number; pnl?: number; reason?: string; t?: number }[];
}

export interface TradingAlerts {
  stop_approach_pct?: number;
  drawdown_warn_pct?: number;
  daily_loss_warn_pct?: number;
  price_levels?: { symbol: string; price: number; when: "above" | "below" }[];
}

export interface TradingExecution {
  broker?: string;
  alpaca_paper?: boolean;
  binance_testnet?: boolean;
  fee_bps?: number;
  slippage_bps?: number;
  guard_interval_s?: number;
}

export interface TradingMandate {
  domains: string[];
  domains_mode: "user" | "agent";
  countries: string[];
  countries_mode: "user" | "agent";
  tapes?: { domain: string; country: string }[];
  active_tapes?: { domain: string; country: string }[];
  currency?: "EUR" | "USD" | string;
  risk_mode: "user" | "agent";
  target_mode: "user" | "agent";
  target_return_pct: number;
  active_domains?: string[];
  active_countries?: string[];
  channels?: {
    telegram?: boolean;
    whatsapp?: boolean;
    email?: boolean;
    telegram_to?: string;
    whatsapp_to?: string;
    email_to?: string;
  };
}

export interface TradingRiskLimits {
  max_position_pct?: number;
  max_sector_pct?: number;
  max_daily_loss_pct?: number;
  max_drawdown_pct?: number;
  stop_loss_pct?: number;
  take_profit_pct?: number | null;
  leverage?: boolean;
  max_daily_orders?: number;
  min_cash_pct?: number;
  approval_notional?: number;
  allowed_assets?: string[];
  blocked_assets?: string[];
}

export interface TradingMetrics {
  return_pct?: number | null;
  max_drawdown?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  win_rate?: number | null;
  trades?: number | null;
  fees?: number | null;
  positions?: number | null;
  realized_pnl?: number | null;
  days?: number | null;
  benchmark?: TradingBenchmark;
}

export interface TradingDesk {
  settings: {
    currency?: string;
    starting_cash?: number;
    benchmark?: string;
    risk?: TradingRiskLimits;
    mandate?: TradingMandate;
    execution_mode?: string;
    execution?: TradingExecution;
    alerts?: TradingAlerts;
    ai_assist?: boolean;
  };
  mandate?: TradingMandate;
  catalog?: {
    domains?: { id: string; name: string }[];
    countries?: { id: string; name: string; region?: string }[];
  };
  channels?: Record<string, { ready?: boolean; enabled?: boolean; hint?: string }>;
  strategy: Record<string, unknown>;
  strategies: Record<string, unknown>[];
  portfolio: {
    cash?: number;
    currency?: string;
    positions?: TradingPosition[];
    equity_curve?: { t: number; equity: number }[];
    realized_pnl?: number;
  };
  watchlist: string[];
  quotes: Record<string, TradingQuote>;
  /** Live prices already converted to the book currency (symbol -> price). */
  prices?: Record<string, number>;
  /** Quote currency -> book currency rate. */
  fx?: Record<string, number>;
  orders: TradingOrder[];
  research: Record<string, TradingResearch>;
  journal: { id?: string; t?: number; kind?: string; symbol?: string; text?: string }[];
  loop: {
    enabled?: boolean;
    phase?: string;
    next_due?: number;
    last_result?: string;
    cycle?: number;
    analyzed?: string[];
    schedule?: TradingLoopSchedule | null;
  };
  guard?: TradingGuard;
  broker?: TradingBrokerStatus;
  ai?: TradingAiRouting;
  backtests: TradingBacktest[];
  metrics: TradingMetrics;
  skills: { id: string; name: string }[];
}

export interface TradingConnectionResult {
  ok: boolean;
  broker?: string;
  error?: string;
  cash?: number;
  currency?: string;
  equity?: number;
  buying_power?: number;
  status?: string;
  [key: string]: unknown;
}

function tradingUrl(action: string): string {
  return `/api/trading?action=${encodeURIComponent(action)}`;
}

export async function fetchTradingDesk(token: string): Promise<TradingDesk> {
  return apiRequest<TradingDesk>(tradingUrl("snapshot"), token, {
    headers: apiBodyHeaders("{}"),
  });
}

export async function postTrading(
  token: string,
  action: string,
  body: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(tradingUrl(action), token, {
    headers: apiBodyHeaders(JSON.stringify(body)),
  });
}

export async function fetchTradingChart(
  token: string,
  symbol: string,
  range: TradingChartRange = "6mo",
): Promise<TradingChart> {
  const out = await postTrading(token, "chart", { symbol, range });
  return out.chart as TradingChart;
}
