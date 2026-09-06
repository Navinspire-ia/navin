import { useMemo, useState } from "react";
import { motion } from "framer-motion";

import type { TradingBar, TradingTechnical } from "@/lib/trading-api";
import {
  candleGeometry,
  chartScale,
  formatCompact,
  formatMoney,
  linePath,
  movingAverage,
  sparkPath,
} from "@/lib/trading-format";
import { cn } from "@/lib/utils";

const CHART_W = 640;
const CHART_H = 220;
const VOL_H = 36;

export interface PriceLevel {
  price: number;
  label: string;
  tone: "stop" | "take" | "avg" | "support" | "resistance" | "alert";
}

const LEVEL_COLORS: Record<PriceLevel["tone"], string> = {
  stop: "#dc2626",
  take: "#059669",
  avg: "#64748b",
  support: "#0ea5e9",
  resistance: "#a855f7",
  alert: "#f59e0b",
};

/**
 * Candles + SMA20/50 + volume + horizontal levels, drawn in SVG so it works
 * offline and in the WebView. Hovering reads one bar out loud (price/date).
 */
export function PriceChart({
  bars,
  technical,
  currency,
  levels = [],
  labels,
  className,
}: {
  bars: TradingBar[];
  technical?: TradingTechnical | null;
  currency: string;
  levels?: PriceLevel[];
  labels: { empty: string; volume: string; sma20: string; sma50: string; rsi: string; atr: string };
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const slice = useMemo(() => bars.slice(-160), [bars]);
  const closes = useMemo(() => slice.map((bar) => Number(bar.c)), [slice]);
  const sma20 = useMemo(() => movingAverage(closes, 20), [closes]);
  const sma50 = useMemo(() => movingAverage(closes, 50), [closes]);
  const scale = useMemo(
    () => chartScale(slice, CHART_W, CHART_H, levels.map((level) => level.price)),
    [slice, levels],
  );
  const candles = useMemo(() => (scale ? candleGeometry(slice, scale) : []), [slice, scale]);
  const volumes = slice.map((bar) => Number(bar.v || 0));
  const volMax = Math.max(1, ...volumes);

  if (!scale || !slice.length) {
    return <p className="p-4 text-sm text-muted-foreground">{labels.empty}</p>;
  }

  const active = hover != null ? slice[hover] : slice[slice.length - 1];
  const activeIndex = hover ?? slice.length - 1;
  const first = Number(slice[0].c);
  const move = first ? ((Number(active.c) - first) / first) * 100 : 0;
  const bodyW = Math.max(1.5, scale.step * 0.62);
  const dateOf = (bar: TradingBar) =>
    new Date(Number(bar.t) * 1000).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "2-digit" });

  return (
    <div className={cn("select-none", className)} data-testid="trading-price-chart">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-1 text-xs tabular-nums">
        <p className="font-medium">
          {formatMoney(Number(active.c), currency)}
          <span className={cn("ml-2", move >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
            {move >= 0 ? "+" : ""}
            {move.toFixed(2)}%
          </span>
          <span className="ml-2 text-muted-foreground">{dateOf(active)}</span>
        </p>
        <p className="text-muted-foreground">
          O {Number(active.o).toFixed(2)} · H {Number(active.h).toFixed(2)} · L {Number(active.l).toFixed(2)} · C{" "}
          {Number(active.c).toFixed(2)}
          {active.v ? ` · ${labels.volume} ${formatCompact(active.v)}` : ""}
        </p>
      </div>
      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H + VOL_H + 8}`}
        className="mt-1 h-auto w-full"
        role="img"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - box.left) / box.width) * CHART_W;
          setHover(Math.max(0, Math.min(slice.length - 1, Math.floor(x / scale.step))));
        }}
      >
        {[0.2, 0.4, 0.6, 0.8].map((frac) => (
          <line
            key={frac}
            x1="0"
            x2={CHART_W}
            y1={CHART_H * frac}
            y2={CHART_H * frac}
            className="stroke-black/10 dark:stroke-white/10"
            strokeWidth="1"
          />
        ))}
        {[0.2, 0.4, 0.6, 0.8].map((frac) => (
          <text
            key={`p-${frac}`}
            x={CHART_W - 4}
            y={CHART_H * frac - 3}
            textAnchor="end"
            className="fill-current text-[10px] text-muted-foreground"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {(scale.max - (scale.max - scale.min) * frac).toFixed(2)}
          </text>
        ))}
        {volumes.map((volume, index) => (
          <rect
            key={`v-${index}`}
            x={scale.x(index) - bodyW / 2}
            y={CHART_H + 8 + VOL_H - (volume / volMax) * VOL_H}
            width={bodyW}
            height={(volume / volMax) * VOL_H}
            className={cn(candles[index]?.up ? "fill-amber-500/35" : "fill-red-500/30")}
          />
        ))}
        <path d={linePath(sma20, scale)} fill="none" stroke="#0ea5e9" strokeWidth="1.4" opacity="0.9" />
        <path d={linePath(sma50, scale)} fill="none" stroke="#a855f7" strokeWidth="1.4" opacity="0.9" />
        {candles.map((candle, index) => (
          <g key={index}>
            <line
              x1={candle.x}
              x2={candle.x}
              y1={candle.wickTop}
              y2={candle.wickBottom}
              stroke={candle.up ? "#F59E0B" : "#dc2626"}
              strokeWidth="1"
            />
            <rect
              x={candle.x - bodyW / 2}
              y={candle.bodyY}
              width={bodyW}
              height={candle.bodyH}
              fill={candle.up ? "#F59E0B" : "#dc2626"}
              rx={bodyW > 4 ? 1 : 0}
            />
          </g>
        ))}
        {levels.map((level) => {
          const y = scale.y(level.price);
          if (y < 0 || y > CHART_H) return null;
          return (
            <g key={`${level.tone}-${level.label}-${level.price}`}>
              <line x1="0" x2={CHART_W} y1={y} y2={y} stroke={LEVEL_COLORS[level.tone]} strokeDasharray="4 4" strokeWidth="1" />
              <text x="4" y={y - 3} className="text-[10px]" fill={LEVEL_COLORS[level.tone]}>
                {level.label} {level.price.toFixed(2)}
              </text>
            </g>
          );
        })}
        {hover != null ? (
          <motion.line
            x1={scale.x(activeIndex)}
            x2={scale.x(activeIndex)}
            y1="0"
            y2={CHART_H + VOL_H + 8}
            className="stroke-black/30 dark:stroke-white/40"
            strokeWidth="1"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          />
        ) : null}
      </svg>
      <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-muted-foreground">
        <span className="tabular-nums">
          {dateOf(slice[0])} - {dateOf(slice[slice.length - 1])}
        </span>
        <span className="flex flex-wrap items-center gap-3 tabular-nums">
          <span className="inline-flex items-center gap-1">
            <i className="inline-block h-0.5 w-3 bg-sky-500" aria-hidden /> {labels.sma20}
          </span>
          <span className="inline-flex items-center gap-1">
            <i className="inline-block h-0.5 w-3 bg-purple-500" aria-hidden /> {labels.sma50}
          </span>
          {technical?.rsi != null ? (
            <span>
              {labels.rsi} {Number(technical.rsi).toFixed(0)}
            </span>
          ) : null}
          {technical?.atr != null ? (
            <span>
              {labels.atr} {Number(technical.atr).toFixed(2)}
            </span>
          ) : null}
        </span>
      </div>
    </div>
  );
}

/** Tiny equity strip for backtest rows and position cards. */
export function MiniCurve({ points, up }: { points: { equity?: number }[] | null | undefined; up: boolean }) {
  const path = sparkPath(points, 120, 32);
  if (!path) return <span className="text-xs text-muted-foreground">-</span>;
  return (
    <svg viewBox="0 0 120 32" className="h-8 w-28" aria-hidden>
      <path d={path} fill="none" stroke={up ? "#F59E0B" : "#dc2626"} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

export function EquityChart({
  points,
  label,
  empty,
  up,
  currency = "USD",
}: {
  points: { equity?: number }[] | null | undefined;
  label: string;
  empty: string;
  up?: boolean;
  currency?: string;
}) {
  const width = 320;
  const height = 110;
  const path = sparkPath(points, width, height);
  const area = path ? `${path} L${width} ${height} L0 ${height} Z` : "";
  const last = (points || []).map((row) => Number(row.equity)).filter((value) => Number.isFinite(value)).at(-1);
  const tone = up === false ? "#dc2626" : "#F59E0B";

  return (
    <div className="rounded-[14px] bg-muted/40 px-3 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
        {last != null ? (
          <p className="text-xs font-medium tabular-nums text-muted-foreground">{formatMoney(last, currency)}</p>
        ) : null}
      </div>
      {path ? (
        <svg viewBox={`0 0 ${width} ${height}`} className="mt-2 h-28 w-full" aria-hidden>
          {[0.25, 0.5, 0.75].map((frac) => (
            <line
              key={frac}
              x1="0"
              x2={width}
              y1={height * frac}
              y2={height * frac}
              className="stroke-black/10 dark:stroke-white/10"
              strokeWidth="1"
            />
          ))}
          <path d={area} fill={tone} fillOpacity="0.16" />
          <path d={path} fill="none" stroke={tone} strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
      ) : (
        <p className="mt-6 text-sm text-muted-foreground">{empty}</p>
      )}
    </div>
  );
}

export function QuoteBars({
  rows,
  empty,
}: {
  rows: { symbol: string; change_pct?: number }[];
  empty: string;
}) {
  const slice = rows.slice(0, 8);
  if (!slice.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="space-y-2">
      {slice.map((row) => {
        const pct = Number(row.change_pct || 0);
        const width = Math.min(100, Math.abs(pct) * 8 + 8);
        return (
          <li key={row.symbol} className="flex items-center gap-2 text-xs">
            <span className="w-16 shrink-0 font-medium tabular-nums">{row.symbol}</span>
            <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full rounded-full", pct >= 0 ? "bg-amber-500" : "bg-red-500")}
                style={{ width: `${width}%` }}
              />
            </div>
            <span className={cn("w-12 shrink-0 text-right tabular-nums", pct >= 0 ? "text-amber-600" : "text-red-600")}>
              {pct.toFixed(1)}%
            </span>
          </li>
        );
      })}
    </ul>
  );
}
