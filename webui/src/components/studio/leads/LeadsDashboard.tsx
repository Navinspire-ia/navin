import { DefaultButton, PrimaryButton } from "@fluentui/react";

import { LeadsScene } from "@/components/studio/leads/LeadsScene";
import { BUTTON_STYLES, SURFACE, scoreTone, type Tx } from "@/components/studio/leads/leads-ui";
import {
  leadHasBuyingSignal,
  leadHasDueStep,
  leadInOutreach,
  leadSourceLabel,
  type LeadLoop,
  type LeadProvider,
  type LeadRow,
  type LeadsProfile,
} from "@/lib/leads-api";
import {
  describeTradingSchedule,
  formatNextDue,
  normalizeTradingSchedule,
} from "@/lib/trading-loop-schedule";
import { cn } from "@/lib/utils";

export function LeadsDashboard({
  tx,
  compact = false,
  headline,
  rows,
  providers,
  busy,
  onOpenBook,
  onHunt,
  onRescore,
  onOpenLead,
  onProviders,
  loop,
  locale,
  profile,
  onEditProfile,
}: {
  tx: Tx;
  compact?: boolean;
  headline?: string;
  rows: LeadRow[];
  providers: LeadProvider[];
  busy: boolean;
  onOpenBook: (filter?: string) => void;
  onHunt: () => void;
  onRescore: () => void;
  onOpenLead: (id: string) => void;
  onProviders: () => void;
  loop?: LeadLoop | null;
  locale?: string;
  profile?: LeadsProfile;
  onEditProfile?: () => void;
}) {
  const live = rows.filter((row) => !row.archived && !row.extra?.archived);
  const bySource = live.reduce<Record<string, number>>((acc, row) => {
    const key = String(row.source || "").toLowerCase();
    if (key) acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const sourceRows = Object.entries(bySource).sort((a, b) => b[1] - a[1]);
  const sentSteps = live.reduce(
    (sum, row) => sum + (row.sequence?.steps || []).filter((step) => step.status === "sent").length,
    0,
  );
  const autonomous = profile?.execution_mode === "autonomous";
  const strong = live.filter((row) => (row.score || 0) >= 80).length;
  const withEmail = live.filter((row) => Boolean(row.email)).length;
  const verified = live.filter((row) => row.email_status === "verified").length;
  const pipeline = live.filter((row) => leadInOutreach(row)).length;
  const due = live.filter((row) => leadHasDueStep(row)).length;
  const signals = live.filter((row) => leadHasBuyingSignal(row)).length;
  const readyProviders = providers.filter((row) => row.configured && row.kind !== "reference").length;
  const preview = live.slice(0, 6);
  const queue = live
    .filter((row) => row.next_action === "contact now" || leadHasDueStep(row))
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 6);
  const heat = live.length ? strong / live.length : 0.2;
  const tiles = [
    { key: "all", label: tx("kpi.total", "Leads"), count: live.length, filter: "all" },
    { key: "strong", label: tx("kpi.strong", "Tier A"), count: strong, filter: "strong" },
    { key: "email", label: tx("kpi.email", "With email"), count: withEmail, filter: "email" },
    { key: "due", label: tx("kpi.due", "Follow-ups due"), count: due, filter: "due" },
    { key: "signals", label: tx("kpi.signals", "Buying signals"), count: signals, filter: "signals" },
    { key: "verified", label: tx("kpi.verified", "Verified"), count: verified, filter: "verified" },
    { key: "pipe", label: tx("kpi.pipeline", "In outreach"), count: pipeline, filter: "pipeline" },
  ];

  return (
    <div className="grid gap-8" data-testid="leads-home-dashboard">
      <div className={cn(SURFACE, "grid gap-6 p-5 sm:p-6", compact ? "grid-cols-1" : "lg:grid-cols-[minmax(0,1fr)_14rem] lg:items-center")}>
        <div className="min-w-0">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
            {tx("home.kicker", "Pipeline")}
          </p>
          <h2 className="mt-2 text-balance text-2xl font-semibold tracking-tight">
            {headline || tx("home.emptyHeadline", "No hunt yet. Set an ICP and run discovery.")}
          </h2>
          <p className="mt-3 text-pretty text-sm leading-relaxed text-muted-foreground">
            {tx(
              "home.flow",
              "Registries, hiring signals, web lists and OpenStreetMap first, then BANT-F with evidence, then a j0/j3/j7 sequence drafted in the prospect's language. Apollo is a last-resort fill. Heartbeat alerts, never sends.",
            )}
          </p>
          <div className="mt-5 flex flex-wrap gap-2 [&>*]:min-w-0">
            <PrimaryButton
              text={tx("home.hunt", "Hunt companies")}
              iconProps={{ iconName: "Search" }}
              disabled={busy}
              onClick={onHunt}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("home.book", "Open the book")}
              iconProps={{ iconName: "PageList" }}
              onClick={() => onOpenBook("all")}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("home.rescore", "Rescore BANT-F")}
              iconProps={{ iconName: "Chart" }}
              disabled={busy}
              onClick={onRescore}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("home.keys", "BYOK providers")}
              iconProps={{ iconName: "Permissions" }}
              onClick={onProviders}
              styles={BUTTON_STYLES}
            />
          </div>
          <p className="mt-3 text-[12px] text-muted-foreground">
            {tx("home.providersReady", "{{n}} live sources", { n: readyProviders })}
          </p>
        </div>
        <LeadsScene
          matchRatio={heat}
          active={!busy && loop?.phase !== "hunt"}
          label={tx("sceneAria", "Three dimensional lead funnel")}
          className={compact ? "h-28 max-w-[11rem]" : undefined}
        />
      </div>
      <div className={cn(SURFACE, "grid gap-3 p-6")} data-testid="leads-loop-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
              {tx("loop", "Loop")}
            </p>
            <p className="mt-1 text-sm font-medium tabular-nums" data-testid="leads-loop-status">
              {loop?.enabled
                ? loop?.phase === "hunt"
                  ? tx("hunting", "Hunting")
                  : tx("running", "Running")
                : tx("paused", "Paused")}
              {" · "}
              {loop?.phase || tx("idle", "idle")}
              {" · "}
              {tx("cycleN", "cycle {{n}}", { n: loop?.cycle || 0 })}
            </p>
            <p className="mt-1 text-pretty text-sm text-muted-foreground">
              {describeTradingSchedule(normalizeTradingSchedule(loop?.schedule), tx, locale || "en")}
              {loop?.enabled && formatNextDue(loop.next_due, locale || "en")
                ? ` · ${tx("scheduleNext", "Next run {{time}}", {
                    time: formatNextDue(loop.next_due, locale || "en"),
                  })}`
                : ""}
            </p>
            <p className="mt-2 text-pretty text-sm text-muted-foreground">
              {loop?.last_result ||
                tx(
                  "loopHint",
                  "Pick a time, start the loop, and the desk hunts on its own. Heartbeat stays silent. Pause or change the schedule whenever you want.",
                )}
            </p>
            {loop?.enabled || loop?.cycle ? (
              <p className="mt-2 text-pretty text-sm tabular-nums text-indigo-700 dark:text-indigo-300">
                {tx("loopStats", "{{added}} new leads · {{alerts}} alerts", {
                  added: loop?.added || 0,
                  alerts: loop?.alerts || 0,
                })}
                {loop?.sent || loop?.drafts_ready
                  ? ` · ${tx("loopSequenceStats", "{{sent}} sent · {{ready}} drafts ready", {
                      sent: loop?.sent || 0,
                      ready: loop?.drafts_ready || 0,
                    })}`
                  : ""}
              </p>
            ) : null}
          </div>
          <div className="grid min-w-0 w-full gap-2 text-sm sm:max-w-xs" data-testid="leads-autonomy-card">
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              {tx("autonomy", "Autonomy")}
            </p>
            <p className="font-medium">
              {autonomous
                ? tx("autonomyOn", "Autonomous · sends up to {{cap}} emails a day", { cap: profile?.daily_send_cap ?? 0 })
                : tx("autonomyOff", "Approval · drafts wait for you")}
            </p>
            <p className="text-pretty text-muted-foreground">
              {tx("autonomySources", "Sources: {{list}}", {
                list:
                  (profile?.sources || []).map((item) => leadSourceLabel(String(item))).filter(Boolean).join(", ")
                  || tx("autonomyRegistriesOnly", "registries and keys only"),
              })}
              {sentSteps ? ` · ${tx("autonomySent", "{{n}} steps sent", { n: sentSteps })}` : ""}
            </p>
            {onEditProfile ? (
              <DefaultButton
                text={tx("autonomyEdit", "Edit ICP and autonomy")}
                iconProps={{ iconName: "Edit" }}
                onClick={onEditProfile}
                styles={BUTTON_STYLES}
                data-testid="leads-edit-profile"
              />
            ) : null}
          </div>
        </div>
      </div>
      {sourceRows.length > 0 ? (
        <div className={cn(SURFACE, "grid gap-3 p-5")} data-testid="leads-sources-card">
          <h3 className="text-lg font-semibold">{tx("home.sources", "Where the leads come from")}</h3>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {sourceRows.map(([source, count]) => {
              const pct = live.length ? Math.round((count / live.length) * 100) : 0;
              return (
                <li key={source} className="grid gap-1">
                  <div className="flex justify-between text-sm">
                    <span className="font-medium">{leadSourceLabel(source)}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {count} · {pct}%
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-indigo-600" style={{ width: `${pct}%` }} />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      <div className={cn("grid gap-3", compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-3 xl:grid-cols-7")}>
        {tiles.map((tile) => (
          <button
            key={tile.key}
            type="button"
            data-testid={`leads-dash-${tile.key}`}
            onClick={() => onOpenBook(tile.filter)}
            className={cn(SURFACE, "min-h-[5.5rem] min-w-0 cursor-pointer p-4 text-left")}
          >
            <p className="text-[12px] text-muted-foreground">{tile.label}</p>
            <p className="mt-2 font-semibold tabular-nums text-2xl">{tile.count}</p>
          </button>
        ))}
      </div>
      {queue.length > 0 ? (
        <div className={cn(SURFACE, "grid gap-3 p-5")}>
          <h3 className="text-lg font-semibold">{tx("home.queue", "Do this next")}</h3>
          <ul className="grid gap-2">
            {queue.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => onOpenLead(row.id)}
                  className="flex w-full min-h-12 cursor-pointer items-center justify-between gap-3 rounded-xl px-3 text-left hover:bg-muted/50"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{row.company}</span>
                    <span className="block truncate text-[12px] text-muted-foreground">
                      {leadHasDueStep(row)
                        ? tx("home.queueDue", "Follow-up is due")
                        : row.next_action || tx("home.queueNow", "Contact now")}
                    </span>
                  </span>
                  <span className={cn("tabular-nums text-sm font-semibold", scoreTone(row.score))}>
                    {row.score ?? 0}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className={cn(SURFACE, "grid gap-3 p-5")}>
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <h3 className="min-w-0 text-lg font-semibold">{tx("home.preview", "Latest leads")}</h3>
          <DefaultButton text={tx("home.seeAll", "See all")} onClick={() => onOpenBook("all")} styles={BUTTON_STYLES} />
        </div>
        {preview.length === 0 ? (
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("home.noRows", "Run a hunt. France uses SIRENE for free. Other markets need a provider key.")}
          </p>
        ) : (
          <ul className="grid gap-2">
            {preview.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => onOpenLead(row.id)}
                  className="flex w-full min-h-12 cursor-pointer items-center justify-between gap-3 rounded-xl px-3 text-left hover:bg-muted/50"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{row.company}</span>
                    <span className="block truncate text-[12px] text-muted-foreground">
                      {[row.person, row.role, row.country].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  <span className={cn("tabular-nums text-sm font-semibold", scoreTone(row.score))}>
                    {row.score ?? 0}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
