// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { SettingsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

type TokenUsagePayload = NonNullable<SettingsPayload["usage"]>;
type ModelUsageRowData = {
  model: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  total_tokens: number;
};
type MonthTotal = {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  total_tokens: number;
};

const PAGE_SIZE = 10;

function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(tokens >= 10_000_000 ? 0 : 1)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(tokens >= 10_000 ? 0 : 1)}K`;
  }
  return String(tokens);
}

function formatRequests(count: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(count);
}

function monthLabel(month: string | undefined, language: string): string {
  if (!month || !/^\d{4}-\d{2}$/.test(month)) return month || "";
  const [year, mon] = month.split("-").map(Number);
  try {
    return new Intl.DateTimeFormat(language, {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(year, mon - 1, 1)));
  } catch {
    return month;
  }
}

function currentMonthKey(usageMonth?: string): string {
  if (usageMonth && /^\d{4}-\d{2}$/.test(usageMonth)) return usageMonth;
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function emptyTotal(): MonthTotal {
  return {
    requests: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_tokens: 0,
    total_tokens: 0,
  };
}

/** Tokens actually billed at full price: total minus cache hits. */
function billedTokens(row: { total_tokens: number; cached_tokens: number }): number {
  return Math.max(0, (row.total_tokens || 0) - (row.cached_tokens || 0));
}

function cachedShare(row: { prompt_tokens: number; cached_tokens: number }): string {
  const prompt = row.prompt_tokens || 0;
  const cached = row.cached_tokens || 0;
  if (prompt <= 0 || cached <= 0) return "";
  return ` (${Math.round((cached / prompt) * 100)}%)`;
}

function aggregateMonth(
  usage: TokenUsagePayload | undefined,
  monthKey: string,
): { models: ModelUsageRowData[]; total: MonthTotal } {
  if (!usage) return { models: [], total: emptyTotal() };

  // Prefer server month rollup when viewing the current tracked month.
  if (usage.month === monthKey && usage.month_total) {
    return {
      models: (usage.models_month ?? []).map((row) => ({
        ...row,
        cached_tokens: row.cached_tokens ?? 0,
      })),
      total: {
        requests: usage.month_total.requests ?? 0,
        prompt_tokens: usage.month_total.prompt_tokens ?? 0,
        completion_tokens: usage.month_total.completion_tokens ?? 0,
        cached_tokens: usage.month_total.cached_tokens ?? 0,
        total_tokens: usage.month_total.total_tokens ?? 0,
      },
    };
  }

  const byModel = new Map<string, ModelUsageRowData>();
  const total = emptyTotal();
  for (const day of usage.days ?? []) {
    if (!day.date?.startsWith(monthKey)) continue;
    total.requests += day.requests || 0;
    total.prompt_tokens += day.prompt_tokens || 0;
    total.completion_tokens += day.completion_tokens || 0;
    total.cached_tokens += day.cached_tokens || 0;
    total.total_tokens += day.total_tokens || 0;
    const models = day.models;
    if (!models) continue;
    for (const [model, row] of Object.entries(models)) {
      const key = model.trim();
      if (!key || key === "unknown") continue;
      const current = byModel.get(key) ?? {
        model: key,
        requests: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
      };
      current.requests += row.requests || 0;
      current.prompt_tokens += row.prompt_tokens || 0;
      current.completion_tokens += row.completion_tokens || 0;
      current.cached_tokens += row.cached_tokens || 0;
      current.total_tokens += row.total_tokens || 0;
      byModel.set(key, current);
    }
  }

  const models = [...byModel.values()].sort(
    (a, b) =>
      b.total_tokens - a.total_tokens ||
      b.requests - a.requests ||
      a.model.localeCompare(b.model),
  );
  return { models, total };
}

function availableYears(usage: TokenUsagePayload | undefined, fallbackMonth: string): number[] {
  const years = new Set<number>();
  const [fallbackYear] = fallbackMonth.split("-").map(Number);
  if (fallbackYear) years.add(fallbackYear);
  for (const day of usage?.days ?? []) {
    const year = Number(day.date?.slice(0, 4));
    if (Number.isFinite(year)) years.add(year);
  }
  if (usage?.month) {
    const year = Number(usage.month.slice(0, 4));
    if (Number.isFinite(year)) years.add(year);
  }
  return [...years].sort((a, b) => b - a);
}

const selectClassName =
  "h-8 rounded-full border border-border/60 bg-background px-3 text-[12px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40";

export function ModelTokenUsageTable({ usage }: { usage?: TokenUsagePayload }) {
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });

  const defaultMonth = currentMonthKey(usage?.month);
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState(() => Number(defaultMonth.slice(0, 4)));
  const [month, setMonth] = useState(() => Number(defaultMonth.slice(5, 7)));
  const [page, setPage] = useState(1);
  const [syncedMonth, setSyncedMonth] = useState<string | null>(null);

  // Default to the agent calendar month once when usage arrives (do not clobber filters).
  useEffect(() => {
    if (!usage?.month || syncedMonth === usage.month) return;
    if (syncedMonth === null) {
      setYear(Number(usage.month.slice(0, 4)));
      setMonth(Number(usage.month.slice(5, 7)));
      setPage(1);
    }
    setSyncedMonth(usage.month);
  }, [usage?.month, syncedMonth]);

  const monthKey = `${year}-${String(month).padStart(2, "0")}`;
  const years = useMemo(() => availableYears(usage, defaultMonth), [usage, defaultMonth]);
  const { models, total } = useMemo(
    () => aggregateMonth(usage, monthKey),
    [usage, monthKey],
  );
  const label = useMemo(
    () => monthLabel(monthKey, i18n.language),
    [i18n.language, monthKey],
  );

  const hasMonthUsage = total.total_tokens > 0 || total.requests > 0;
  const trackedTokens = models.reduce((sum, row) => sum + (row.total_tokens || 0), 0);
  const trackedRequests = models.reduce((sum, row) => sum + (row.requests || 0), 0);
  const untrackedTokens = Math.max(0, total.total_tokens - trackedTokens);
  const untrackedRequests = Math.max(0, total.requests - trackedRequests);

  const pageCount = Math.max(1, Math.ceil(models.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageRows = models.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [monthKey]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold text-foreground">
            {tx("settings.usage.models.title", "Token quota by model")}
          </h3>
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">
            {tx(
              "settings.usage.models.subtitle",
              "Requests and tokens this calendar month{{month}}.",
              { month: label ? ` · ${label}` : "" },
            )}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 shrink-0 rounded-full"
          onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronRight className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          )}
          {open
            ? tx("settings.usage.models.hide", "Hide")
            : tx("settings.usage.models.show", "Show details")}
        </Button>
      </div>

      {open ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
              <span>{tx("settings.usage.models.filterYear", "Year")}</span>
              <select
                className={selectClassName}
                value={year}
                onChange={(event) => setYear(Number(event.target.value))}
              >
                {years.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
              <span>{tx("settings.usage.models.filterMonth", "Month")}</span>
              <select
                className={selectClassName}
                value={month}
                onChange={(event) => setMonth(Number(event.target.value))}
              >
                {Array.from({ length: 12 }, (_, index) => index + 1).map((value) => {
                  const key = `${year}-${String(value).padStart(2, "0")}`;
                  return (
                    <option key={value} value={value}>
                      {monthLabel(key, i18n.language).replace(/\s+\d{4}$/, "") || value}
                    </option>
                  );
                })}
              </select>
            </label>
          </div>

          {!hasMonthUsage ? (
            <p className="rounded-xl border border-dashed border-border/60 px-3 py-6 text-center text-[12px] text-muted-foreground">
              {tx(
                "settings.usage.models.empty",
                "Model usage will appear here after new model replies.",
              )}
            </p>
          ) : (
            <div className="space-y-2">
              <div className="overflow-x-auto rounded-xl border border-border/55">
                <table className="w-full min-w-[680px] border-collapse text-left text-[12px]">
                  <thead>
                    <tr className="border-b border-border/55 bg-muted/25 text-[11px] uppercase tracking-wide text-muted-foreground">
                      <th className="px-3 py-2 font-medium">
                        {tx("settings.usage.models.colModel", "Model")}
                      </th>
                      <th className="px-3 py-2 font-medium tabular-nums">
                        {tx("settings.usage.models.colRequests", "Requests")}
                      </th>
                      <th className="px-3 py-2 font-medium tabular-nums">
                        {tx("settings.usage.models.colInput", "Input")}
                      </th>
                      <th className="px-3 py-2 font-medium tabular-nums">
                        {tx("settings.usage.models.colCached", "Cached")}
                      </th>
                      <th className="px-3 py-2 font-medium tabular-nums">
                        {tx("settings.usage.models.colOutput", "Output")}
                      </th>
                      <th className="px-3 py-2 font-medium tabular-nums">
                        {tx("settings.usage.models.colTotal", "Total")}
                      </th>
                      <th
                        className="px-3 py-2 font-medium tabular-nums"
                        title={tx(
                          "settings.usage.models.colBilledHint",
                          "Tokens billed at full price (total minus cache hits).",
                        )}
                      >
                        {tx("settings.usage.models.colBilled", "Billed")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.length === 0 ? (
                      <tr>
                        <td
                          colSpan={7}
                          className="px-3 py-4 text-[12px] text-muted-foreground"
                        >
                          {tx(
                            "settings.usage.models.waitingModels",
                            "No per-model rows yet this month. Send a chat message - the next replies will appear here by model.",
                          )}
                        </td>
                      </tr>
                    ) : (
                      pageRows.map((row) => <ModelUsageRow key={row.model} row={row} />)
                    )}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-border/55 bg-muted/20 font-medium">
                      <td className="px-3 py-2.5">
                        {tx("settings.usage.models.allModels", "All models")}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {formatRequests(total.requests)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {formatTokens(total.prompt_tokens)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {formatTokens(total.cached_tokens)}
                        {cachedShare(total)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {formatTokens(total.completion_tokens)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {formatTokens(total.total_tokens)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {formatTokens(billedTokens(total))}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>

              {models.length > PAGE_SIZE ? (
                <div className="flex flex-wrap items-center justify-between gap-2 px-0.5">
                  <p className="text-[11.5px] text-muted-foreground">
                    {tx(
                      "settings.usage.models.pagination",
                      "Showing {{from}}-{{to}} of {{total}}",
                      {
                        from: (safePage - 1) * PAGE_SIZE + 1,
                        to: Math.min(safePage * PAGE_SIZE, models.length),
                        total: models.length,
                      },
                    )}
                  </p>
                  <div className="flex items-center gap-1.5">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 rounded-full px-3 text-[11.5px]"
                      disabled={safePage <= 1}
                      onClick={() => setPage((current) => Math.max(1, current - 1))}
                    >
                      {tx("settings.usage.models.prev", "Previous")}
                    </Button>
                    <span className="min-w-[4.5rem] text-center text-[11.5px] tabular-nums text-muted-foreground">
                      {safePage} / {pageCount}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 rounded-full px-3 text-[11.5px]"
                      disabled={safePage >= pageCount}
                      onClick={() =>
                        setPage((current) => Math.min(pageCount, current + 1))
                      }
                    >
                      {tx("settings.usage.models.next", "Next")}
                    </Button>
                  </div>
                </div>
              ) : null}

              {untrackedTokens > 0 || untrackedRequests > 0 ? (
                <p className="px-0.5 text-[11.5px] text-muted-foreground">
                  {tx(
                    "settings.usage.models.legacyNote",
                    "{{tokens}} tokens ({{count}} req) have no model label (recorded before per-model tracking).",
                    {
                      tokens: formatTokens(untrackedTokens),
                      count: formatRequests(untrackedRequests),
                    },
                  )}
                </p>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ModelUsageRow({ row }: { row: ModelUsageRowData }) {
  return (
    <tr className="border-b border-border/40 last:border-b-0">
      <td
        className="max-w-[240px] truncate px-3 py-2.5 font-medium text-foreground"
        title={row.model}
      >
        {row.model}
      </td>
      <td className={cn("px-3 py-2.5 tabular-nums text-muted-foreground")}>
        {formatRequests(row.requests)}
      </td>
      <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
        {formatTokens(row.prompt_tokens)}
      </td>
      <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
        {formatTokens(row.cached_tokens)}
        {cachedShare(row)}
      </td>
      <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
        {formatTokens(row.completion_tokens)}
      </td>
      <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
        {formatTokens(row.total_tokens)}
      </td>
      <td className="px-3 py-2.5 tabular-nums text-foreground">
        {formatTokens(billedTokens(row))}
      </td>
    </tr>
  );
}
