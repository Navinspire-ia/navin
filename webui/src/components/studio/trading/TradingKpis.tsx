// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { formatMoney, formatPct } from "@/lib/trading-format";

export const KPI_GRID_CLASS = "grid min-w-0 grid-cols-2 gap-3 lg:grid-cols-4";
export const KPI_CELL_CLASS = "min-w-0";
export const KPI_CARD_CLASS =
  "relative isolate min-w-0 overflow-hidden rounded-2xl px-3 py-2.5 shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10";
export const KPI_VALUE_CLASS =
  "mt-1 block w-full min-w-0 text-center text-sm font-semibold leading-tight tabular-nums sm:text-base";

export type TradingKpiItem = {
  id: "equity" | "cash" | "return" | "drawdown";
  label: string;
  value: string;
};

export function buildTradingKpis(input: {
  equity: number;
  cash: number;
  returnPct: number;
  drawdownPct: number;
  currency: string;
  labels: { equity: string; cash: string; return: string; drawdown: string };
}): TradingKpiItem[] {
  return [
    { id: "equity", label: input.labels.equity, value: formatMoney(input.equity, input.currency) },
    { id: "cash", label: input.labels.cash, value: formatMoney(input.cash, input.currency) },
    { id: "return", label: input.labels.return, value: formatPct(input.returnPct) },
    { id: "drawdown", label: input.labels.drawdown, value: formatPct(input.drawdownPct) },
  ];
}

export function TradingKpiGrid({ items }: { items: TradingKpiItem[] }) {
  return (
    <div className={KPI_GRID_CLASS} data-testid="trading-kpi-grid">
      {items.map((item) => (
        <div key={item.id} className={KPI_CELL_CLASS} data-testid={`trading-kpi-${item.id}`}>
          <div className={KPI_CARD_CLASS}>
            <div className="text-center text-[11px] uppercase tracking-wide text-muted-foreground">{item.label}</div>
            <div className={KPI_VALUE_CLASS} title={item.value}>
              {item.value}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
