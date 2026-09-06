import type { CrmInsights } from "@/lib/api";

type Props = {
  insight: CrmInsights | null;
  tx: (key: string, fallback: string) => string;
};

export function CrmInsightCard({ insight, tx }: Props) {
  if (!insight) return null;
  const tone =
    insight.tone === "good"
      ? "text-emerald-600"
      : insight.tone === "risk"
        ? "text-amber-600"
        : "text-muted-foreground";
  return (
    <div className="rounded-xl border border-border/60 bg-muted/15 p-3 text-[12px] shadow-[0_1px_0_rgba(0,0,0,0.04)]">
      <p className="font-semibold">{tx("crm.insights", "Navin Insights")}</p>
      <p className={`mt-1 tabular-nums ${tone}`}>
        {tx("crm.health", "Sante")} {insight.health}/100
      </p>
      {insight.nextAction ? (
        <p className="text-wrap text-muted-foreground" style={{ textWrap: "pretty" } as never}>
          {insight.nextAction}
        </p>
      ) : null}
      {insight.risk ? <p className="text-amber-600">{insight.risk}</p> : null}
      {insight.suggestion ? <p>{insight.suggestion}</p> : null}
    </div>
  );
}
