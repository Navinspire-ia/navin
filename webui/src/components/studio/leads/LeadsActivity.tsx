// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DefaultButton } from "@fluentui/react";

import { LeadsScene } from "@/components/studio/leads/LeadsScene";
import { isLiveLead } from "@/components/studio/leads/lead-filters";
import { BUTTON_STYLES, SURFACE, type Tx } from "@/components/studio/leads/leads-ui";
import { leadSourceLabel, type LeadLoop, type LeadProvider, type LeadRow, type LeadsProfile } from "@/lib/leads-api";
import { describeTradingSchedule, formatNextDue, normalizeTradingSchedule } from "@/lib/trading-loop-schedule";
import { cn } from "@/lib/utils";

/**
 * What used to be the Home dashboard, folded under the book: loop status and
 * schedule, autonomy, where the leads come from, and the maintenance actions.
 */
export function LeadsActivity({
  tx,
  rows,
  providers,
  busy,
  loop,
  locale,
  profile,
  onRescore,
  onProviders,
  onEditProfile,
}: {
  tx: Tx;
  rows: LeadRow[];
  providers: LeadProvider[];
  busy: boolean;
  loop?: LeadLoop | null;
  locale?: string;
  profile?: LeadsProfile;
  onRescore: () => void;
  onProviders: () => void;
  onEditProfile: () => void;
}) {
  const live = rows.filter(isLiveLead);
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
  const readyProviders = providers.filter((row) => row.configured && row.kind !== "reference").length;
  const heat = live.length ? strong / live.length : 0.2;
  const status = loop?.enabled
    ? loop?.phase === "hunt"
      ? tx("hunting", "Hunting")
      : tx("running", "Running")
    : tx("paused", "Paused");

  return (
    <details className="group min-w-0" data-testid="leads-activity">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-xl px-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
        <span className="inline-block text-[10px] transition-transform group-open:rotate-90" aria-hidden>
          &#9654;
        </span>
        <span>{tx("activityTitle", "Loop, sources and autonomy")}</span>
        <span className="tabular-nums" data-testid="leads-loop-status">
          · {status} · {tx("cycleN", "cycle {{n}}", { n: loop?.cycle || 0 })}
        </span>
        <span className="ml-auto tabular-nums">{tx("home.providersReady", "{{n}} live sources", { n: readyProviders })}</span>
      </summary>
      <div className="grid gap-4 pt-3">
        <div className={cn(SURFACE, "grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_10rem] lg:items-start")} data-testid="leads-loop-card">
          <div className="min-w-0">
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
              {tx("loop", "Loop")}
            </p>
            <p className="mt-1 text-sm font-medium tabular-nums">
              {status}
              {" · "}
              {loop?.phase || tx("idle", "idle")}
              {" · "}
              {tx("cycleN", "cycle {{n}}", { n: loop?.cycle || 0 })}
            </p>
            <p className="mt-1 text-pretty text-sm text-muted-foreground">
              {describeTradingSchedule(normalizeTradingSchedule(loop?.schedule), tx, locale || "en")}
              {loop?.enabled && formatNextDue(loop.next_due, locale || "en")
                ? ` · ${tx("scheduleNext", "Next run {{time}}", { time: formatNextDue(loop.next_due, locale || "en") })}`
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
          <div className="grid min-w-0 gap-2 text-sm" data-testid="leads-autonomy-card">
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
            <div className="flex flex-wrap gap-2 pt-1">
              <DefaultButton
                text={tx("autonomyEdit", "Edit ICP and autonomy")}
                iconProps={{ iconName: "Edit" }}
                onClick={onEditProfile}
                styles={BUTTON_STYLES}
                data-testid="leads-edit-profile"
              />
              <DefaultButton
                text={tx("home.rescore", "Rescore BANT-F")}
                iconProps={{ iconName: "Chart" }}
                disabled={busy}
                onClick={onRescore}
                styles={BUTTON_STYLES}
                data-testid="leads-rescore"
              />
              <DefaultButton
                text={tx("home.keys", "BYOK providers")}
                iconProps={{ iconName: "Permissions" }}
                onClick={onProviders}
                styles={BUTTON_STYLES}
              />
            </div>
          </div>
          <LeadsScene
            matchRatio={heat}
            active={!busy && loop?.phase !== "hunt"}
            label={tx("sceneAria", "Three dimensional lead funnel")}
            className="h-28 max-w-[10rem] justify-self-end"
          />
        </div>
        {sourceRows.length > 0 ? (
          <div className={cn(SURFACE, "grid gap-3 p-5")} data-testid="leads-sources-card">
            <h3 className="text-base font-semibold">{tx("home.sources", "Where the leads come from")}</h3>
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
      </div>
    </details>
  );
}
