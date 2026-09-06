import { cn } from "@/lib/utils";

export function DiffPair({
  added,
  deleted,
  hideZero = false,
  className,
}: {
  added: number;
  deleted: number;
  /** Drop a side that is zero, so a pure addition reads "+121", not "+121 -0". */
  hideZero?: boolean;
  className?: string;
}) {
  const showAdded = !hideZero || added > 0;
  const showDeleted = !hideZero || deleted > 0;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-baseline gap-1.5 text-[length:inherit] font-[inherit] leading-[inherit] tabular-nums",
        className,
      )}
      data-testid="activity-diff-pair"
    >
      {showAdded ? (
        <DiffValue
          sign="+"
          value={added}
          className="text-emerald-600/75 dark:text-emerald-300/75"
        />
      ) : null}
      {showDeleted ? (
        <DiffValue
          sign="-"
          value={deleted}
          className="text-rose-600/70 dark:text-rose-300/75"
        />
      ) : null}
    </span>
  );
}

function DiffValue({ sign, value, className }: { sign: string; value: number; className: string }) {
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
  return (
    <span
      className={cn("inline-flex items-baseline leading-[inherit]", className)}
      aria-label={`${sign}${safeValue}`}
    >
      <span className="inline-flex items-baseline leading-none" aria-hidden>
        {sign}
        {safeValue}
      </span>
    </span>
  );
}
