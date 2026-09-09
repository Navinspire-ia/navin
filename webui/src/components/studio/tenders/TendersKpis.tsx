// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DASH_TILE_CLASS, DASH_TILE_GRID_CLASS } from "@/components/studio/tenders/tenders-ui";

export const KPI_GRID_CLASS = DASH_TILE_GRID_CLASS;
export const KPI_CELL_CLASS = "min-w-0";
export const KPI_CARD_CLASS = "min-w-0";
export const KPI_VALUE_CLASS =
  "mt-1 block w-full min-w-0 text-left text-xl font-semibold leading-tight tabular-nums sm:text-2xl";

export type TenderKpiItem = {
  id: "new" | "open" | "qualified" | "deadline" | "weighted";
  label: string;
  value: string;
};

export function buildTenderKpis(input: {
  open: number;
  qualified: number;
  fresh?: number;
  deadline7d: number;
  weighted: number;
  labels: { open: string; qualified: string; fresh?: string; deadline: string; weighted: string };
}): TenderKpiItem[] {
  const weighted =
    input.weighted > 0
      ? input.weighted >= 1_000_000
        ? `${(input.weighted / 1_000_000).toFixed(1)} M`
        : input.weighted.toLocaleString("fr-FR")
      : "n/a";
  return [
    { id: "new", label: input.labels.fresh || "New", value: String(input.fresh || 0) },
    { id: "open", label: input.labels.open, value: String(input.open) },
    { id: "qualified", label: input.labels.qualified, value: String(input.qualified) },
    { id: "deadline", label: input.labels.deadline, value: String(input.deadline7d) },
    { id: "weighted", label: input.labels.weighted, value: weighted },
  ];
}

function KpiCard({
  item,
  onPick,
}: {
  item: TenderKpiItem;
  onPick?: (id: TenderKpiItem["id"]) => void;
}) {
  const inner = (
    <div className={KPI_CARD_CLASS}>
      <div className="text-pretty text-[12px] text-muted-foreground">{item.label}</div>
      <div className={KPI_VALUE_CLASS} title={item.value}>
        {item.value}
      </div>
    </div>
  );
  if (onPick) {
    return (
      <button type="button" onClick={() => onPick(item.id)} className={DASH_TILE_CLASS}>
        {inner}
      </button>
    );
  }
  return (
    <div className="flex min-h-20 min-w-0 flex-col justify-center rounded-2xl px-4 py-3 outline outline-1 outline-black/10 dark:outline-white/10">
      {inner}
    </div>
  );
}

export function TenderKpiGrid({
  items,
  onPick,
}: {
  items: TenderKpiItem[];
  onPick?: (id: TenderKpiItem["id"]) => void;
}) {
  const strip = items.filter((item) => item.id !== "weighted");
  const weighted = items.find((item) => item.id === "weighted");
  return (
    <div className="grid min-w-0 gap-3" data-testid="tenders-kpi-grid">
      <div className={KPI_GRID_CLASS}>
        {strip.map((item) => (
          <div key={item.id} className={KPI_CELL_CLASS} data-testid={`tenders-kpi-${item.id}`}>
            <KpiCard item={item} onPick={onPick} />
          </div>
        ))}
      </div>
      {weighted ? (
        <div className={KPI_CELL_CLASS} data-testid={`tenders-kpi-${weighted.id}`}>
          <KpiCard item={weighted} onPick={onPick} />
        </div>
      ) : null}
    </div>
  );
}
