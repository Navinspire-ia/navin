import type { ReactNode } from "react";
import { AlertTriangle, X } from "lucide-react";

import { cn } from "@/lib/utils";

/** Building blocks shared by the SEO and Ads desks. */

export type DeskSeverity = "critical" | "high" | "medium" | "low" | "info";

export const SEVERITY_ORDER: DeskSeverity[] = ["critical", "high", "medium", "low", "info"];

const SEVERITY_TONE: Record<DeskSeverity, string> = {
  critical: "bg-[#c2410c]/12 text-[#9a3412] dark:text-[#fdba74]",
  high: "bg-[#d4892a]/14 text-[#92400e] dark:text-[#fcd34d]",
  medium: "bg-[#2f7de1]/10 text-[#1d4ed8] dark:text-[#93c5fd]",
  low: "bg-foreground/[0.06] text-foreground/75",
  info: "bg-foreground/[0.04] text-muted-foreground",
};

export function SeverityChip({ severity, label }: { severity: DeskSeverity; label: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium", SEVERITY_TONE[severity])}>
      {label}
    </span>
  );
}

function scoreColor(score: number): string {
  if (score >= 90) return "#1f9d74";
  if (score >= 75) return "#4c9a2a";
  if (score >= 60) return "#d4892a";
  if (score >= 40) return "#c2410c";
  return "#b91c1c";
}

/** 0..100 score as a ring with the grade in the middle. */
export function ScoreRing({ score, grade, label, size = 112 }: { score: number; grade?: string; label: string; size?: number }) {
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, score));
  const color = scoreColor(clamped);
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${label}: ${clamped}/100`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="currentColor" strokeOpacity={0.08} strokeWidth={10} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={10}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - clamped / 100)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: "stroke-dashoffset 600ms ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[26px] font-semibold tabular-nums leading-none text-foreground">{clamped}</span>
        {grade ? <span className="mt-1 text-[11px] font-medium text-muted-foreground">{grade}</span> : null}
      </div>
    </div>
  );
}

export function KpiTile({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className="min-w-0 rounded-xl border border-border/60 bg-background/70 px-3.5 py-3">
      <p className="truncate text-[11.5px] font-medium text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-1 truncate text-[20px] font-semibold tabular-nums tracking-[-0.01em] text-foreground",
          tone === "good" && "text-[#1f9d74]",
          tone === "bad" && "text-[#c2410c]",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function DeskSection({ title, actions, children, className }: { title: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-2xl border border-border/60 bg-card/60 p-4", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold text-foreground">{title}</h2>
        {actions ? <div className="flex flex-wrap items-center gap-1.5">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-xl border border-[#c2410c]/25 bg-[#c2410c]/[0.06] px-3 py-2 text-[12.5px] text-[#9a3412] dark:text-[#fdba74]">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onDismiss ? (
        <button type="button" onClick={onDismiss} className="shrink-0 rounded p-0.5 hover:bg-[#c2410c]/10" aria-label="Dismiss">
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ icon, title, body, action }: { icon: ReactNode; title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border/70 px-6 py-10 text-center">
      <div className="grid h-10 w-10 place-items-center rounded-full bg-foreground/[0.05] text-foreground/70">{icon}</div>
      <p className="text-[13.5px] font-medium text-foreground">{title}</p>
      <p className="max-w-md text-[12.5px] leading-relaxed text-muted-foreground">{body}</p>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

/**
 * A small line chart (one or two series) for trends: scores over time, SERP
 * positions. ``invert`` draws lower values higher (rank 1 at the top).
 */
export function TrendChart({
  points,
  label,
  invert = false,
  height = 120,
  max,
  format = (value) => String(value),
}: {
  points: Array<{ x: string; y: number | null }>;
  label: string;
  invert?: boolean;
  height?: number;
  max?: number;
  format?: (value: number) => string;
}) {
  const values = points.map((point) => point.y).filter((value): value is number => value !== null);
  if (values.length === 0) return null;
  const width = 560;
  const pad = 28;
  const hi = max ?? Math.max(...values, invert ? 10 : 1);
  const lo = invert ? 1 : 0;
  const span = Math.max(1, hi - lo);
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const yOf = (value: number) => {
    const ratio = (value - lo) / span;
    return invert ? pad / 2 + ratio * (height - pad) : height - pad / 2 - ratio * (height - pad);
  };
  const coords = points.map((point, index) => ({ x: pad + index * step, y: point.y === null ? null : yOf(point.y), point }));
  const path = coords
    .filter((item) => item.y !== null)
    .map((item, index) => `${index === 0 ? "M" : "L"}${item.x.toFixed(1)},${(item.y as number).toFixed(1)}`)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={label}>
      <line x1={pad} x2={width - pad} y1={height - pad / 2} y2={height - pad / 2} stroke="currentColor" strokeOpacity={0.12} />
      <path d={path} fill="none" stroke="#2f7de1" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {coords.map((item) =>
        item.y === null ? null : (
          <g key={`${item.point.x}-${item.x}`}>
            <circle cx={item.x} cy={item.y} r={3.5} fill="#2f7de1">
              <title>{`${item.point.x}: ${format(item.point.y as number)}`}</title>
            </circle>
          </g>
        ),
      )}
      <text x={pad} y={height - 2} fontSize={10} fill="currentColor" opacity={0.55}>{points[0]?.x}</text>
      <text x={width - pad} y={height - 2} fontSize={10} fill="currentColor" opacity={0.55} textAnchor="end">
        {points[points.length - 1]?.x}
      </text>
    </svg>
  );
}

/** Latin digits in every language (Arabic included), like the rest of the app. */
export function latnLocale(locale: string): string {
  return `${locale || "en"}-u-nu-latn`;
}

export function formatNumber(value: number | null | undefined, locale: string, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat(latnLocale(locale), { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(value);
}

export function formatPercent(value: number | null | undefined, locale: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat(latnLocale(locale), { style: "percent", maximumFractionDigits: 2 }).format(value);
}

export function formatMoney(value: number | null | undefined, locale: string, currency: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (/^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(latnLocale(locale), { style: "currency", currency, maximumFractionDigits: 2 }).format(value);
    } catch {
      // Unknown ISO code: plain number below.
    }
  }
  return formatNumber(value, locale, 2);
}
