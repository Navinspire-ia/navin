import { DefaultButton, PrimaryButton } from "@fluentui/react";
import type { ReactNode } from "react";

import { CareerMoneyBar, type CareerMoneyBarProps } from "@/components/studio/career/CareerKpis";
import { CareerScene } from "@/components/studio/career/CareerScene";
import { BUTTON_STYLES, SURFACE } from "@/components/studio/career/career-ui";
import type { CareerJournalRow, CareerLoop, CareerOpportunity } from "@/lib/career-api";
import { archivedOffers, favoriteOffers, type OfferListView } from "@/lib/career-list";
import {
  describeTradingSchedule,
  formatNextDue,
  normalizeTradingSchedule,
} from "@/lib/trading-loop-schedule";
import { cn } from "@/lib/utils";

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

export type OfferDeskOpen = {
  view?: OfferListView;
  bucket?: "all" | "perfect" | "good" | "skip";
  id?: string;
};

function scoreTone(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 80) return "text-emerald-700 dark:text-emerald-300";
  if (score >= 55) return "text-amber-700 dark:text-amber-300";
  return "text-rose-700 dark:text-rose-300";
}

export function CareerDashboard({
  tx,
  money,
  matchRatio,
  busy,
  headline,
  live,
  allRows,
  applications,
  interviews,
  searchNote,
  sources,
  onOpenOffers,
  onOpenPane,
  onSearch,
  onFind,
  onOpenProfile,
  loop,
  journal,
  locale,
}: {
  tx: Tx;
  money: CareerMoneyBarProps;
  matchRatio: number;
  busy: boolean;
  headline?: string;
  live: CareerOpportunity[];
  allRows: CareerOpportunity[];
  applications: number;
  interviews: number;
  searchNote?: string;
  sources?: ReactNode;
  onOpenOffers: (next?: OfferDeskOpen) => void;
  onOpenPane: (pane: "applications" | "interviews" | "profile") => void;
  onSearch: () => void;
  onFind: () => void;
  onOpenProfile: () => void;
  loop?: CareerLoop;
  journal?: CareerJournalRow[];
  locale?: string;
}) {
  const favorites = favoriteOffers(allRows);
  const archived = archivedOffers(allRows);
  const perfect = live.filter((row) => row.bucket === "perfect").length;
  const good = live.filter((row) => row.bucket === "good").length;
  const skip = live.filter((row) => row.bucket === "skip").length;
  const preview = live.slice(0, 5);
  const tiles: {
    key: string;
    label: string;
    fallback: string;
    count: number;
    open?: OfferDeskOpen;
    pane?: "applications" | "interviews";
  }[] = [
    { key: "all", label: "bucket.all", fallback: "All", count: live.length, open: { view: "inbox", bucket: "all" } },
    { key: "perfect", label: "bucket.perfect", fallback: "Strong", count: perfect, open: { view: "inbox", bucket: "perfect" } },
    { key: "good", label: "bucket.good", fallback: "OK", count: good, open: { view: "inbox", bucket: "good" } },
    { key: "skip", label: "bucket.skip", fallback: "Weak", count: skip, open: { view: "inbox", bucket: "skip" } },
    { key: "fav", label: "openFavorites", fallback: "Favorites", count: favorites.length, open: { view: "favorites" } },
    { key: "arch", label: "openArchive", fallback: "Archive", count: archived.length, open: { view: "archive" } },
    { key: "apps", label: "pane.applications", fallback: "Applications", count: applications, pane: "applications" },
    { key: "talks", label: "pane.interviews", fallback: "Interviews", count: interviews, pane: "interviews" },
  ];

  return (
    <div className="grid gap-8" data-testid="career-home-dashboard">
      <CareerMoneyBar {...money} />

      <div className={cn(SURFACE, "grid gap-6 p-6 sm:p-8")}>
        <div className="grid gap-6 sm:grid-cols-[1fr_auto] sm:items-center">
          <div>
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
              {tx("homeDashboard", "Dashboard")}
            </p>
            <h2 className="mt-2 text-balance text-2xl font-semibold sm:text-3xl">
              {headline || tx("emptyHeadline", "No offers yet. Save the profile, then search authorized sources.")}
            </h2>
            <p className="mt-2 max-w-xl text-pretty text-sm text-muted-foreground">
              {tx(
                "dashLead",
                "Money, match counts and the next moves live here. The full book stays on Offers.",
              )}
            </p>
          </div>
          <div className="w-full sm:w-48">
            <CareerScene
              matchRatio={matchRatio}
              active={!busy}
              label={tx("sceneAria", "Three dimensional briefcase for the career pipeline")}
            />
          </div>
        </div>
        <ol className="grid gap-3 sm:grid-cols-3">
          {(
            [
              ["find", tx("start.find", "Find"), tx("how1", "Navin lists Remotive, published ATS boards, and web snippets.")],
              ["prepare", tx("start.prepare", "Prepare"), tx("how3", "Navin tailors the CV from your facts.")],
              ["apply", tx("start.apply", "Apply"), tx("how2", "You open the official offer and send it.")],
            ] as const
          ).map(([id, title, hint], index) => (
            <li key={id}>
              <p className="text-[12px] font-medium tabular-nums text-indigo-700 dark:text-indigo-300">{index + 1}</p>
              <p className="mt-1 text-sm font-semibold">{title}</p>
              <p className="mt-1 text-pretty text-[13px] leading-relaxed text-muted-foreground">{hint}</p>
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-3">
          <PrimaryButton
            text={tx("openOfferBook", "Open all offers")}
            iconProps={{ iconName: "PageList" }}
            onClick={() => onOpenOffers({ view: "inbox", bucket: "all" })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("search", "Search offers")}
            iconProps={{ iconName: "Search" }}
            disabled={busy}
            onClick={onSearch}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("findMission", "Find me a mission")}
            iconProps={{ iconName: "Work" }}
            disabled={busy}
            onClick={onFind}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("openFavorites", "Favorites")}
            iconProps={{ iconName: "FavoriteStar" }}
            onClick={() => onOpenOffers({ view: "favorites" })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("openArchive", "Archive")}
            iconProps={{ iconName: "Archive" }}
            onClick={() => onOpenOffers({ view: "archive" })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("tileProfile", "Edit profile")}
            iconProps={{ iconName: "Contact" }}
            onClick={onOpenProfile}
            styles={BUTTON_STYLES}
          />
        </div>
        {searchNote ? <p className="text-pretty text-sm text-muted-foreground">{searchNote}</p> : null}
      </div>

      <div className={cn(SURFACE, "grid gap-3 p-6 sm:p-8")} data-testid="career-loop-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
              {tx("loop", "Loop")}
            </p>
            <p className="mt-1 text-sm font-medium tabular-nums">
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
                  "Pick a time, start the loop, and the desk hunts authorized sources on its own. Heartbeat stays silent. Pause or change the schedule whenever you want.",
                )}
            </p>
            {loop?.enabled || loop?.cycle ? (
              <p className="mt-2 text-pretty text-sm tabular-nums text-indigo-700 dark:text-indigo-300">
                {tx("loopStats", "{{added}} new offers · {{alerts}} alerts", {
                  added: loop?.added || 0,
                  alerts: loop?.alerts || 0,
                })}
              </p>
            ) : null}
          </div>
        </div>
        {(loop?.alerts || 0) > 0 ? (
          <p
            className="rounded-xl bg-indigo-50 px-3 py-2 text-pretty text-sm text-indigo-900 dark:bg-indigo-950/50 dark:text-indigo-100"
            data-testid="career-loop-banner"
          >
            {tx("loopBanner", "{{count}} strong matches or follow-ups are ready. Open Offers.", {
              count: loop?.alerts || 0,
            })}
          </p>
        ) : null}
        {journal && journal.length ? (
          <ol className="grid gap-1.5" data-testid="career-loop-journal">
            {journal
              .filter((row) => row.kind === "loop" || row.kind === "watch")
              .slice(-4)
              .reverse()
              .map((row, index) => (
                <li key={`${row.at || 0}-${index}`} className="text-pretty text-[13px] text-muted-foreground">
                  {row.text || ""}
                </li>
              ))}
          </ol>
        ) : null}
      </div>

      <div className={cn(SURFACE, "grid gap-5 p-6 sm:p-8")}>
        <div>
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
            {tx("dashSnapshot", "Pipeline")}
          </p>
          <p className="mt-1 text-pretty text-sm text-muted-foreground">
            {tx("dashSnapshotBody", "Open a tile to see that list. Filters stay on the Offers page.")}
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {tiles.map((tile) => (
            <button
              key={tile.key}
              type="button"
              data-testid={`career-dash-${tile.key}`}
              onClick={() => {
                if (tile.open) onOpenOffers(tile.open);
                else if (tile.pane) onOpenPane(tile.pane);
              }}
              className="min-h-20 cursor-pointer rounded-2xl px-4 py-3 text-left shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 transition-[transform,background-color] duration-150 hover:bg-muted/30 active:scale-[0.96] dark:outline-white/10"
            >
              <span className="block text-2xl font-semibold tabular-nums text-indigo-700 dark:text-indigo-300">
                {tile.count}
              </span>
              <span className="mt-1 block text-[13px] text-muted-foreground">
                {tx(tile.label, tile.fallback)}
              </span>
            </button>
          ))}
        </div>
      </div>

      {preview.length ? (
        <div className={cn(SURFACE, "grid gap-4 p-6 sm:p-8")}>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
                {tx("dashNextTitle", "Next offers")}
              </p>
              <p className="mt-1 text-pretty text-sm text-muted-foreground">
                {tx("dashNextBody", "A short preview. The full book, filters and pages live under Offers.")}
              </p>
            </div>
            <DefaultButton
              text={tx("offerBook", "Offers")}
              iconProps={{ iconName: "PageList" }}
              onClick={() => onOpenOffers({ view: "inbox", bucket: "all" })}
              styles={BUTTON_STYLES}
            />
          </div>
          <ul className="grid gap-2">
            {preview.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => onOpenOffers({ view: "inbox", bucket: "all", id: row.id })}
                  className="flex min-h-14 w-full cursor-pointer items-center gap-4 rounded-xl px-3 text-left transition-[transform,background-color] duration-150 hover:bg-muted/40 active:scale-[0.96]"
                >
                  <span className={cn("w-10 shrink-0 text-lg font-semibold tabular-nums", scoreTone(row.match_score))}>
                    {row.match_score != null ? Math.round(row.match_score) : "-"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{row.title}</span>
                    <span className="block truncate text-[13px] text-muted-foreground">
                      {[row.company, row.location || row.country, row.source].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {sources}
    </div>
  );
}
