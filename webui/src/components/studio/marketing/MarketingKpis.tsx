// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export const KPI_GRID_CLASS = "grid min-w-0 grid-cols-2 gap-3 lg:grid-cols-4";
export const KPI_CELL_CLASS = "min-w-0";
export const KPI_CARD_CLASS =
  "relative isolate min-w-0 overflow-hidden rounded-2xl px-3 py-2.5 shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10";
export const KPI_VALUE_CLASS =
  "mt-1 block w-full min-w-0 text-center text-sm font-semibold leading-tight tabular-nums sm:text-base";

export type MarketingKpiId = "signups" | "content" | "winners" | "campaigns" | "published" | "scheduled" | "views" | "clicks";

export type MarketingKpiItem = {
  id: MarketingKpiId;
  label: string;
  value: string;
  hint?: string;
};

function compact(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (Math.abs(value) >= 10_000) return `${Math.round(value / 1000)}k`;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(Math.round(value));
}

export function buildMarketingKpis(input: {
  signups: number;
  content: number;
  winners: number;
  campaigns: number;
  published?: number;
  scheduled?: number;
  views?: number;
  clicks?: number;
  labels: { signups: string; content: string; winners: string; campaigns: string; published?: string; scheduled?: string; views?: string; clicks?: string };
}): MarketingKpiItem[] {
  const items: MarketingKpiItem[] = [
    { id: "signups", label: input.labels.signups, value: compact(input.signups) },
    { id: "content", label: input.labels.content, value: String(input.content) },
    { id: "winners", label: input.labels.winners, value: String(input.winners) },
    { id: "campaigns", label: input.labels.campaigns, value: String(input.campaigns) },
  ];
  // The publish row only shows once the desk has posting history or a queue: a fresh desk keeps four tiles.
  if (input.published !== undefined && input.labels.published) {
    items.push(
      { id: "published", label: input.labels.published, value: String(input.published) },
      { id: "scheduled", label: input.labels.scheduled || "scheduled", value: String(input.scheduled || 0) },
      { id: "views", label: input.labels.views || "views", value: compact(input.views || 0) },
      { id: "clicks", label: input.labels.clicks || "clicks", value: compact(input.clicks || 0) },
    );
  }
  return items;
}

export function MarketingKpiGrid({ items }: { items: MarketingKpiItem[] }) {
  return (
    <div className={KPI_GRID_CLASS} data-testid="marketing-kpi-grid">
      {items.map((item) => (
        <div key={item.id} className={KPI_CELL_CLASS} data-testid={`marketing-kpi-${item.id}`}>
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
