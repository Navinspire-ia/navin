// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Callout,
  DefaultButton,
  Dialog,
  DialogFooter,
  DialogType,
  DirectionalHint,
  Icon,
  IconButton,
  PrimaryButton,
  TextField,
} from "@fluentui/react";
import "@/lib/fluent-icons";

import { NoticeFilters } from "@/components/studio/tenders/NoticeFilters";
import { NoticeGoButtons } from "@/components/studio/tenders/NoticeGoButtons";
import { TenderFactsRow } from "@/components/studio/tenders/TenderFactsRow";
import { TenderNoticeDialog } from "@/components/studio/tenders/TenderNoticeDialog";
import {
  applyFacetFilter,
  emptyFacetFilter,
} from "@/components/studio/tenders/notice-filters";
import {
  TENDERS_PAGE_SIZE,
  activeNotices,
  archivedNotices,
  favoriteNotices,
  filterNotices,
  isFavorite,
  isUrgent,
  noticeGoMark,
  noticePageCount,
  paginateNotices,
  pipelineCounts,
  sortNotices,
  type NoticeListView,
  type NoticeRowAction,
  type PipelineFilter,
} from "@/components/studio/tenders/pipeline";
import { DossierPreview } from "@/components/studio/DossierPreview";
import { DocumentGenerationNotice } from "@/components/studio/DocumentGenerationNotice";
import { retentionDays } from "@/components/studio/tenders/retention";
import {
  BUTTON_STYLES,
  HEADER_BUTTON_STYLES,
  ICON_BUTTON_STYLES,
  CHANNELS_HASH,
  MODELS_HASH,
  NOTICE_ROW_MENU,
  OfficialLink,
  Surface,
  asLines,
  chapterTitle,
  lastSentMail,
  lastUnsentMail,
  openInIdeHash,
  scoreTone,
  type Tx,
} from "@/components/studio/tenders/tenders-ui";
import { openInOsBrowser } from "@/lib/api";
import type {
  TenderDesk,
  TenderExportKind,
  TenderFollowUpEvent,
  TenderNotice,
  TenderProfile,
} from "@/lib/tenders-api";
import { countryDisplayName } from "@/lib/country-options";
import { domainLabel } from "@/components/studio/tenders/domain-label";
import { noticeDomain, noticeIsoDay } from "@/components/studio/tenders/notice-filters";
import { parseTenderFacts, sourceNameMap, factDisplay, noticeStatusCode, type TenderFact } from "@/lib/tender-facts";
import { officialTenderHref, postTenders, triggerTenderDownload } from "@/lib/tenders-api";
import {
  downloadTendersExport,
  noticeHasOfferPack,
  readyOfferNotices,
} from "@/lib/tenders-export";
import {
  describeTradingSchedule,
  formatNextDue,
  normalizeTradingSchedule,
} from "@/lib/trading-loop-schedule";
import { cn } from "@/lib/utils";

const FILTERS: { id: PipelineFilter; label: string; fallback: string }[] = [
  { id: "all", label: "filterAll", fallback: "All" },
  { id: "go", label: "filterGo", fallback: "GO" },
  { id: "draft", label: "filterDraft", fallback: "Drafts" },
  { id: "nogo", label: "filterNogo", fallback: "No-go" },
];

function companyLine(profile: TenderProfile): string {
  const bits = [
    profile.name,
    profile.specialty,
    (profile.countries || []).slice(0, 6).join(", "),
  ].filter(Boolean);
  return bits.join(" · ");
}

function stageLabel(stage: string, tx: Tx): string {
  return tx(`stage.${stage}`, stage);
}

/**
 * Header button for the follow-up digest: a count on the pill, a compact panel
 * with what needs the user (open a notice in one click) and the single send action.
 */
export function FollowUpButton({
  desk,
  tx,
  busy,
  onFollow,
  onOpen,
}: {
  desk: TenderDesk;
  tx: Tx;
  busy: string;
  onFollow: () => void;
  onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const events = desk.follow_up?.events || [];
  const pending = desk.follow_up?.pending || 0;
  const label = tx("followKicker", "Follow-up");
  return (
    <>
      <div ref={anchor} className="shrink-0">
        <DefaultButton
          text={pending ? `${label} · ${pending}` : label}
          title={label}
          iconProps={{ iconName: "Send" }}
          onClick={() => setOpen((value) => !value)}
          checked={open}
          styles={HEADER_BUTTON_STYLES}
          aria-haspopup="dialog"
          aria-expanded={open}
          data-testid="tenders-follow-up"
        />
      </div>
      {open ? (
        <Callout
          target={anchor.current}
          isBeakVisible={false}
          gapSpace={6}
          directionalHint={DirectionalHint.bottomLeftEdge}
          preventDismissOnScroll
          preventDismissOnResize
          setInitialFocus
          role="dialog"
          ariaLabel={label}
          onDismiss={() => setOpen(false)}
          styles={{ calloutMain: { borderRadius: 12 } }}
        >
          <div className="grid w-[24rem] gap-3 p-4" data-testid="tenders-follow-up-panel">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-800 dark:text-amber-200">
                {label}
              </p>
              <h2 className="mt-1 text-balance text-base font-semibold">
                {pending
                  ? tx("followTitle", "{{count}} notice(s) need you", { count: pending })
                  : tx("followEmpty", "Nothing needs you right now.")}
              </h2>
              <p className="mt-1 text-pretty text-[12.5px] leading-snug text-muted-foreground">
                {tx(
                  "followBody",
                  "One digest goes to every channel you switched on. Nothing is sent to a buyer without your approval.",
                )}
              </p>
            </div>
            {events.length ? (
              <ul className="-mx-1 grid max-h-64 gap-0.5 overflow-y-auto overscroll-contain px-1 [scrollbar-gutter:stable]" data-menu-scroll="">
                {events.slice(0, 8).map((event) => (
                  <li key={`${event.id}-${event.key}`}>
                    <button
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        onOpen(event.id);
                      }}
                      className="grid w-full cursor-pointer gap-0.5 rounded-lg px-2 py-1.5 text-left transition-[background-color,transform] duration-150 hover:bg-black/5 active:scale-[0.99] dark:hover:bg-white/5"
                    >
                      <span className="text-[10.5px] font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-200">
                        {followLine(event, tx)}
                      </span>
                      <span className="line-clamp-2 text-[13px] leading-snug">{event.title || event.id}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="flex justify-end border-t border-black/10 pt-3 dark:border-white/10">
              <PrimaryButton
                text={tx("followSend", "Send the digest now")}
                iconProps={{ iconName: "Send" }}
                disabled={Boolean(busy) || !pending}
                onClick={() => {
                  setOpen(false);
                  onFollow();
                }}
                styles={BUTTON_STYLES}
                data-testid="tenders-follow-send"
              />
            </div>
          </div>
        </Callout>
      ) : null}
    </>
  );
}

export function followLine(event: TenderFollowUpEvent, tx: Tx): string {
  if (event.kind === "go") return tx("followGo", "GO to confirm");
  if (event.kind === "deadline") {
    return tx("followDeadline", "Due in {{days}} day(s)", { days: event.days ?? 0 });
  }
  return tx("followRelance", "Silent for {{days}} day(s) since submission", { days: event.days ?? 0 });
}

const TASK_LABELS: Record<string, { key: string; fallback: string }> = {
  qualify: { key: "modelTaskQualify", fallback: "Score and GO / NO-GO" },
  write: { key: "modelTaskWrite", fallback: "Write the dossier" },
  mail: { key: "modelTaskMail", fallback: "Buyer letters" },
};

export function ModelRouting({ desk, tx }: { desk: TenderDesk; tx: Tx }) {
  const tasks = desk.models?.tasks || [];
  if (!tasks.length) return null;
  const routed = desk.models?.routed || 0;
  const names = tasks
    .map((row) => row.model || row.preset)
    .filter(Boolean)
    .slice(0, 2)
    .join(" · ");
  return (
    <details className="rounded-2xl px-1 py-2 text-sm text-muted-foreground">
      <summary className="min-h-11 cursor-pointer list-none rounded-xl px-3 py-2 transition-[background-color] duration-150 hover:bg-muted/40">
        {routed
          ? tx("modelTitle", "Each task runs on the model you routed")
          : tx("modelTitleOff", "No model routed - the desk stays on templates")}
        {names ? <span className="ml-2 tabular-nums text-foreground">{names}</span> : null}
      </summary>
      <div className="grid gap-2 px-3 pb-3 pt-1">
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-emerald-800 dark:text-emerald-200">
          {tx("modelKicker", "Models")}
        </p>
        <p className="max-w-xl text-pretty text-[13px]">
          {tx(
            "modelBody",
            "Set these in Settings > Models > Task routing. The decision runs on your capable model, the prose on the cheap one.",
          )}
        </p>
        <DefaultButton
          text={tx("openModels", "Open Settings > Models")}
          onClick={() => openInIdeHash(MODELS_HASH)}
          styles={BUTTON_STYLES}
        />
        <ul className="grid gap-2">
          {tasks.map((row) => (
            <li key={row.task} className="flex flex-wrap items-baseline justify-between gap-2">
              <span>
                {tx(
                  TASK_LABELS[row.task]?.key || `modelTask.${row.task}`,
                  TASK_LABELS[row.task]?.fallback || row.task,
                )}
              </span>
              <span className="font-medium tabular-nums text-foreground">
                {row.model || row.preset || tx("modelUnrouted", "not routed")}
                <span className="ml-2 text-[12px] font-normal uppercase tracking-wide">
                  {row.role}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

function CollectReport({ desk, tx }: { desk: TenderDesk; tx: Tx }) {
  const rows = desk.collect || [];
  if (!rows.length) return null;
  return (
    <div className="grid gap-2">
      <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {tx("collectReport", "Last collect")}
      </p>
      <ul className="grid gap-1 text-[13px]">
        {rows.map((row) => (
          <li key={`${row.source_id}-${row.kind || "row"}`} className="flex flex-wrap justify-between gap-2">
            <span className="font-medium">{row.source_id}</span>
            <span className={row.ok ? "text-teal-700 dark:text-teal-300" : "text-amber-800 dark:text-amber-200"}>
              {row.ok
                ? tx("collectOk", "{{count}} notice(s)", { count: row.count || 0 })
                : row.detail || tx("collectFail", "no notices")}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DiscoveriesPane({
  desk,
  token,
  tx,
  busy,
  onAccept,
}: {
  desk: TenderDesk;
  token: string;
  tx: Tx;
  busy: string;
  onAccept: (host: string) => void;
}) {
  const rows = (desk.discoveries || []).filter((row) => row.host && !row.added);
  if (!rows.length) return null;
  return (
    <Surface className="grid gap-4 p-6 sm:p-8" data-testid="tenders-new-hosts">
      <div>
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-emerald-800 dark:text-emerald-200">
          {tx("discoverKicker", "New official hosts")}
        </p>
        <h3 className="mt-2 text-balance text-lg font-semibold">
          {tx("discoverTitle", "These are websites, not tenders")}
        </h3>
        <p className="mt-1 max-w-xl text-pretty text-sm text-muted-foreground">
          {tx(
            "discoverBody",
            "Collect saw these official portals. They are not notices. Keep a host to add it to your sources for the next collect. The URL opens in the OS browser.",
          )}
        </p>
      </div>
      <ul className="grid gap-3">
        {rows.map((row) => (
          <li key={row.host} className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium">{row.host}</p>
              {row.url ? (
                <OfficialLink
                  href={row.url}
                  token={token}
                  className="break-all text-[13px] text-teal-700 underline underline-offset-2 dark:text-teal-300"
                >
                  {row.url.replace(/^https:\/\/(www\.)?/, "")}
                </OfficialLink>
              ) : null}
            </div>
            {row.added ? (
              <p className="text-[13px] text-muted-foreground">{tx("discoverAdded", "Added")}</p>
            ) : (
              <DefaultButton
                text={tx("discoverAccept", "Keep this host")}
                disabled={Boolean(busy)}
                onClick={() => onAccept(row.host)}
                styles={BUTTON_STYLES}
              />
            )}
          </li>
        ))}
      </ul>
    </Surface>
  );
}

/** Loop status, CRM push, last collect, discovered hosts, model routing and MCP options: what the old dashboard kept. */
export function ActivityPane({
  desk,
  tx,
  busy,
  token,
  onCrmSync,
  onDiscoverAccept,
  locale,
}: {
  desk: TenderDesk;
  tx: Tx;
  busy: string;
  token: string;
  onCrmSync: () => void;
  onDiscoverAccept: (host: string) => void;
  locale?: string;
}) {
  const notices = desk.tenders || [];
  const live = useMemo(() => activeNotices(notices), [notices]);
  const counts = useMemo(() => pipelineCounts(live), [live]);
  return (
    <details className="group min-w-0" data-testid="tenders-activity">
      <summary className="flex cursor-pointer select-none items-center gap-2 text-sm font-medium text-muted-foreground">
        <Icon iconName="ChevronRight" className="text-[10px] transition-transform duration-150 group-open:rotate-90" aria-hidden />
        {tx("activityTitle", "Loop, sources and CRM")}
        <span
          className={cn(
            "rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
            desk.loop?.enabled
              ? "bg-emerald-500/16 text-emerald-800 dark:text-emerald-200"
              : "bg-muted text-muted-foreground",
          )}
          data-testid="tenders-autopilot-badge"
        >
          {desk.loop?.enabled
            ? desk.loop?.phase === "hunt"
              ? tx("autopilotHunt", "Autopilot collecting")
              : tx("autopilotOn", "Autopilot on")
            : tx("autopilotOff", "Autopilot paused")}
        </span>
      </summary>
      <div className="mt-3 grid min-w-0 gap-6">
        <Surface className="grid min-w-0 gap-3 p-5" data-testid="tenders-loop-card">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-emerald-800 dark:text-emerald-200">
            {tx("loop", "Loop")}
          </p>
          <p className="break-words text-sm font-medium tabular-nums">
            {desk.loop?.enabled
              ? desk.loop?.phase === "hunt"
                ? tx("hunting", "Collecting")
                : tx("running", "Running")
              : tx("paused", "Paused")}
            {" · "}
            {desk.loop?.phase || tx("idle", "idle")}
            {" · "}
            {tx("cycleN", "cycle {{n}}", { n: desk.loop?.cycle || 0 })}
          </p>
          <p className="break-words text-pretty text-sm text-muted-foreground">
            {describeTradingSchedule(normalizeTradingSchedule(desk.loop?.schedule), tx, locale || "en")}
            {desk.loop?.enabled && formatNextDue(desk.loop.next_due, locale || "en")
              ? ` · ${tx("scheduleNext", "Next run {{time}}", {
                  time: formatNextDue(desk.loop.next_due, locale || "en"),
                })}`
              : ""}
          </p>
          <p className="break-words text-pretty text-sm text-muted-foreground">
            {desk.loop?.last_result ||
              tx(
                "loopHint",
                "Start the loop and the desk hunts official notices on your clock. Heartbeat watches GO and deadlines between hunts. It never posts a bid. Pause or change the schedule whenever you want.",
              )}
          </p>
          <p className="break-words text-pretty text-[13px] text-emerald-800 dark:text-emerald-200" data-testid="tenders-heartbeat-lane">
            {tx(
              "watchLane",
              "Heartbeat stays on between hunts: new GO and deadlines reach your channels. The loop never sends a buyer mail.",
            )}
          </p>
          {desk.loop?.enabled || desk.loop?.cycle ? (
            <p className="break-words text-pretty text-sm tabular-nums text-emerald-800 dark:text-emerald-200">
              {tx("loopStats", "{{added}} new notices · {{alerts}} alerts", {
                added: desk.loop?.added || 0,
                alerts: desk.loop?.alerts || 0,
              })}
            </p>
          ) : null}
          {(desk.journal || []).some((row) => row.kind === "loop") ? (
            <ol className="grid gap-1.5" data-testid="tenders-loop-journal">
              {(desk.journal || [])
                .filter((row) => row.kind === "loop")
                .slice(-4)
                .reverse()
                .map((row, index) => (
                  <li key={`${row.t || 0}-${index}`} className="break-words text-pretty text-[13px] text-muted-foreground">
                    {row.text || ""}
                  </li>
                ))}
            </ol>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {counts.play ? (
              <DefaultButton
                text={tx("crmSyncAll", "Push deals to CRM")}
                iconProps={{ iconName: "ContactCard" }}
                disabled={Boolean(busy)}
                onClick={onCrmSync}
                styles={BUTTON_STYLES}
              />
            ) : null}
          </div>
          <CollectReport desk={desk} tx={tx} />
        </Surface>
        <DiscoveriesPane desk={desk} token={token} tx={tx} busy={busy} onAccept={onDiscoverAccept} />
        <ModelRouting desk={desk} tx={tx} />
        <TendersMcpOptions desk={desk} tx={tx} />
      </div>
    </details>
  );
}

export function NoticesPane({
  desk,
  tx,
  busy,
  listView,
  picked,
  token = "",
  locale = "fr-FR",
  onListView,
  onPicked,
  onOpen,
  onRowAction,
}: {
  desk: TenderDesk;
  tx: Tx;
  busy: string;
  listView: NoticeListView;
  picked: PipelineFilter | null;
  token?: string;
  locale?: string;
  onListView: (view: NoticeListView) => void;
  onPicked: (filter: PipelineFilter | null) => void;
  onOpen: (id: string) => void;
  onRowAction: (action: NoticeRowAction, id: string) => void;
}) {
  const notices = desk.tenders || [];
  const live = useMemo(() => activeNotices(notices), [notices]);
  const favorites = useMemo(() => favoriteNotices(notices), [notices]);
  const archived = useMemo(() => archivedNotices(notices), [notices]);
  const crafts = desk.profile.crafts || [];
  const [facet, setFacet] = useState(emptyFacetFilter);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pendingDelete, setPendingDelete] = useState<TenderNotice | null>(null);
  const [previewId, setPreviewId] = useState("");
  const [downloading, setDownloading] = useState("");
  const [downloadProgress, setDownloadProgress] = useState("");
  const [listNote, setListNote] = useState("");
  const filter =
    picked && picked !== "play" && picked !== "urgent" ? picked : "all";
  const { archive_after_days: archiveAfter, delete_after_days: deleteAfter } = retentionDays({
    archive_after_days: desk.profile.archive_after_days ?? desk.retention?.archive_after_days,
    delete_after_days: desk.profile.delete_after_days ?? desk.retention?.delete_after_days,
  });
  const pool = listView === "favorites" ? favorites : listView === "archive" ? archived : live;
  const faceted = useMemo(() => applyFacetFilter(pool, facet, crafts), [crafts, facet, pool]);
  const counts = useMemo(
    () => pipelineCounts(listView === "pipeline" ? faceted : live),
    [faceted, listView, live],
  );
  const visible = useMemo(
    () =>
      sortNotices(
        listView === "pipeline" ? filterNotices(faceted, filter, query) : filterNotices(faceted, "all", query),
      ),
    [faceted, filter, listView, query],
  );
  const pages = noticePageCount(visible.length);
  const safePage = Math.min(page, pages);
  const pageRows = useMemo(() => paginateNotices(visible, safePage), [safePage, visible]);
  const sourceNames = useMemo(
    () => sourceNameMap(desk.sources || [], desk.catalog || []),
    [desk.catalog, desk.sources],
  );
  const preview = useMemo(
    () => notices.find((row) => row.id === previewId) || null,
    [notices, previewId],
  );
  useEffect(() => {
    setPage(1);
  }, [facet, filter, listView, query]);

  const openList = (nextView: NoticeListView) => {
    onListView(nextView);
    setQuery("");
    setPage(1);
  };

  const offerRows = useMemo(() => readyOfferNotices(visible), [visible]);

  const fetchPack = async (id: string, kind: TenderExportKind): Promise<boolean> => {
    if (!token) return false;
    try {
      const result = await postTenders(token, "download", { id, kind });
      if (result.download?.data) {
        triggerTenderDownload(result.download);
        return true;
      }
    } catch {
      return false;
    }
    return false;
  };

  const downloadPack = async (id: string, kind: TenderExportKind) => {
    if (!token || downloading) return;
    setListNote("");
    setDownloading(`${id}:${kind}`);
    try {
      const ok = await fetchPack(id, kind);
      if (!ok) setListNote(tx("downloadOfferFailed", "Download failed. Write the reply first."));
    } finally {
      setDownloading("");
    }
  };

  const downloadAllOffers = async () => {
    if (!token || downloading || !offerRows.length) {
      if (!offerRows.length) {
        setListNote(
          tx("downloadOffersEmpty", "No written offer in this list. Open a notice and write the reply."),
        );
      }
      return;
    }
    setListNote("");
    setDownloading("all");
    let done = 0;
    let failed = 0;
    try {
      for (const row of offerRows) {
        done += 1;
        setDownloadProgress(
          tx("downloadOffersBusy", "Downloading {{done}} / {{total}}...", {
            done,
            total: offerRows.length,
          }),
        );
        const okDoc = await fetchPack(row.id, "docx");
        const okPpt = await fetchPack(row.id, "pptx");
        if (!okDoc && !okPpt) failed += 1;
        if (done < offerRows.length) {
          await new Promise((resolve) => window.setTimeout(resolve, 280));
        }
      }
      if (failed) {
        setListNote(
          tx("downloadOffersPartial", "{{failed}} offer(s) could not be downloaded.", { failed }),
        );
      }
    } finally {
      setDownloading("");
      setDownloadProgress("");
    }
  };

  const openOfficial = (id: string) => {
    const row = notices.find((item) => item.id === id);
    const href = officialTenderHref(row?.source_url || "");
    if (!href || !token) return;
    void openInOsBrowser(token, href);
  };

  const runExport = (format: "csv" | "xlsx") => {
    const ok = downloadTendersExport(visible, format, { locale, tx });
    setListNote(ok ? "" : tx("exportEmpty", "Nothing to export. Change the filters."));
  };

  const requestAction = (action: NoticeRowAction, id: string) => {
    if (action === "view") {
      setPreviewId(id);
      return;
    }
    if (action === "delete") {
      const row = notices.find((item) => item.id === id) || null;
      setPendingDelete(row);
      return;
    }
    onRowAction(action, id);
  };

  const closePreview = () => setPreviewId("");
  const workFromPreview = (action: "qualify" | "write" | "view" | "go" | "nogo") => {
    const id = previewId;
    closePreview();
    if (!id) return;
    if (action === "view") {
      onOpen(id);
      return;
    }
    onRowAction(action, id);
  };

  const chipClass = (active: boolean) =>
    cn(
      "min-h-8 shrink-0 cursor-pointer whitespace-nowrap rounded-full px-3 text-xs font-medium outline outline-1 transition-[background-color,color] duration-150",
      active
        ? "bg-emerald-800 text-white outline-emerald-800 dark:bg-emerald-300 dark:text-slate-950 dark:outline-emerald-300"
        : "outline-black/10 hover:bg-muted/50 dark:outline-white/10",
    );
  const pickFilter = (id: PipelineFilter) => {
    openList("pipeline");
    onPicked(id);
  };
  const listTabs = (
    <div
      className="flex shrink-0 flex-nowrap items-center gap-1.5"
      role="tablist"
      aria-label={tx("listViews", "Notice lists")}
      data-testid="tenders-notice-chips"
    >
      {FILTERS.map((row) => {
        const active = listView === "pipeline" && filter === row.id;
        return (
          <button
            key={row.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => pickFilter(row.id)}
            className={chipClass(active)}
          >
            {tx(row.label, row.fallback)} · {counts[row.id]}
          </button>
        );
      })}
      {(
        [
          { id: "favorites" as const, label: "openFavorites", fallback: "Favorites", count: favorites.length },
          { id: "archive" as const, label: "openArchive", fallback: "Archive", count: archived.length },
        ]
      ).map((row) => {
        const active = listView === row.id;
        return (
          <button
            key={row.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => openList(row.id)}
            className={chipClass(active)}
          >
            {tx(row.label, row.fallback)} · {row.count}
          </button>
        );
      })}
    </div>
  );

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4" data-testid="tenders-book">
      <NoticeFilters
        rows={pool}
        crafts={crafts}
        filter={facet}
        query={query}
        tx={tx}
        locale={locale}
        onChange={setFacet}
        onQuery={setQuery}
        leading={listTabs}
        extra={
          <>
            <IconButton
              iconProps={{ iconName: "Download" }}
              disabled={!visible.length}
              title={
                visible.length
                  ? tx("exportHint", "Exports every filtered notice, not only this page.")
                  : tx("exportEmpty", "Nothing to export. Change the filters.")
              }
              ariaLabel={tx("export", "Export")}
              menuProps={{
                ...NOTICE_ROW_MENU,
                items: [
                  {
                    key: "csv",
                    text: tx("exportCsv", "CSV"),
                    iconProps: { iconName: "TextDocument" },
                    disabled: !visible.length,
                    onClick: () => runExport("csv"),
                  },
                  {
                    key: "xlsx",
                    text: tx("exportExcel", "Excel"),
                    iconProps: { iconName: "ExcelDocument" },
                    disabled: !visible.length,
                    onClick: () => runExport("xlsx"),
                  },
                ],
              }}
              data-testid="tenders-export"
              styles={ICON_BUTTON_STYLES}
            />
            <IconButton
              iconProps={{ iconName: "WordDocument" }}
              disabled={!offerRows.length || Boolean(downloading) || !token}
              title={
                downloadProgress ||
                (offerRows.length
                  ? tx("downloadOffersHint", "Downloads Word packs for filtered notices that already have a reply.")
                  : tx("downloadOffersEmpty", "No written offer in this list. Open a notice and write the reply."))
              }
              ariaLabel={downloadProgress || tx("downloadOffers", "Download offers")}
              onClick={() => void downloadAllOffers()}
              data-testid="tenders-download-offers"
              styles={ICON_BUTTON_STYLES}
            />
          </>
        }
      />
      {listView !== "pipeline" ? (
        <p className="max-w-xl text-pretty text-sm text-muted-foreground">
          {listView === "favorites"
            ? tx(
                "favoritesHint",
                "Starred notices stay here until you archive or delete them. Auto-archive still runs after {{days}} days.",
                { days: archiveAfter },
              )
            : tx(
                "archiveHint",
                "Notices move here after {{archive}} days, or when you archive them. They are deleted after {{purge}} days.",
                { archive: archiveAfter, purge: deleteAfter },
              )}
        </p>
      ) : null}
      {listNote ? (
        <p className="text-pretty text-sm text-amber-800 dark:text-amber-200" role="status">
          {listNote}
        </p>
      ) : null}
      <p className="text-sm text-muted-foreground" data-testid="tenders-result-count">
        {tx("resultCount", "Your search returns {{count}} results.", { count: visible.length })}
      </p>
      {visible.length ? (
        <>
          <PipelineList
            rows={pageRows}
            view={listView}
            tx={tx}
            busy={Boolean(busy) || Boolean(downloading)}
            token={token}
            downloading={downloading}
            locale={locale}
            sourceNames={sourceNames}
            crafts={crafts}
            onAction={requestAction}
            onDownload={(id, kind) => void downloadPack(id, kind)}
            onOfficial={openOfficial}
          />
          <NoticePager
            page={safePage}
            pages={pages}
            count={visible.length}
            pageSize={TENDERS_PAGE_SIZE}
            tx={tx}
            onPage={setPage}
          />
        </>
      ) : (
        <Surface className="grid gap-4 p-6 sm:p-8">
          <h3 className="text-balance text-lg font-semibold">
            {listView === "favorites"
              ? tx("favoritesEmptyTitle", "No favorites yet")
              : listView === "archive"
                ? tx("archiveEmptyTitle", "Archive is empty")
                : tx("filterEmptyTitle", "Nothing in this view")}
          </h3>
          <p className="max-w-xl text-pretty text-sm text-muted-foreground">
            {listView === "favorites"
              ? tx("favoritesEmptyBody", "Open a notice menu and choose Favorite to build this list.")
              : listView === "archive"
                ? tx(
                    "archiveEmptyBody",
                    "Notices are archived after {{archive}} days and deleted after {{purge}} days.",
                    { archive: archiveAfter, purge: deleteAfter },
                  )
                : tx("filterEmptyBody", "No notice matches this filter or search.")}
          </p>
          {listView !== "pipeline" ? (
            <DefaultButton
              text={tx("backNotices", "Back to notices")}
              onClick={() => openList("pipeline")}
              styles={BUTTON_STYLES}
            />
          ) : null}
        </Surface>
      )}

      <TenderNoticeDialog
        notice={preview}
        tx={tx}
        token={token}
        busy={Boolean(busy)}
        locale={locale}
        sourceNames={sourceNames}
        onDismiss={closePreview}
        onOpenDossier={() => workFromPreview("view")}
        onQualify={() => workFromPreview("qualify")}
        onWrite={() => workFromPreview("write")}
        onGo={() => workFromPreview("go")}
        onNogo={() => workFromPreview("nogo")}
      />
      <Dialog
        hidden={!pendingDelete}
        onDismiss={() => setPendingDelete(null)}
        modalProps={{
          isBlocking: true,
          dragOptions: undefined,
        }}
        dialogContentProps={{
          type: DialogType.normal,
          title: tx("deleteConfirmTitle", "Delete this notice?"),
          subText: pendingDelete
            ? tx("deleteConfirmBody", "{{title}} leaves the book. This cannot be undone.", {
                title: pendingDelete.title,
              })
            : "",
        }}
      >
        <DialogFooter>
          <PrimaryButton
            text={tx("rowDelete", "Delete")}
            disabled={Boolean(busy)}
            onClick={() => {
              if (pendingDelete) onRowAction("delete", pendingDelete.id);
              setPendingDelete(null);
            }}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("deleteConfirmNo", "Cancel")}
            onClick={() => setPendingDelete(null)}
            styles={BUTTON_STYLES}
          />
        </DialogFooter>
      </Dialog>
    </div>
  );
}

const MCP_GROUPS: { id: string; key: string; fallback: string }[] = [
  { id: "company", key: "mcpGroupCompany", fallback: "Company" },
  { id: "people", key: "mcpGroupPeople", fallback: "People" },
  { id: "posts", key: "mcpGroupPosts", fallback: "Posts" },
  { id: "inbox", key: "mcpGroupInbox", fallback: "Messages" },
  { id: "jobs", key: "mcpGroupJobs", fallback: "Jobs (never a notice)" },
  { id: "session", key: "mcpGroupSession", fallback: "Session" },
];

export function TendersMcpOptions({ desk, tx }: { desk: TenderDesk; tx: Tx }) {
  const rows = desk.stack?.mcp || [];
  if (!rows.length) return null;
  const linkedin = rows.find((row) => (row.id || "").toLowerCase() === "linkedin");
  const others = rows.filter((row) => row !== linkedin);
  const groups = linkedin?.groups || {};
  const confirm = linkedin?.confirm || [];
  return (
    <section className="grid gap-3 px-1 py-2 text-sm text-muted-foreground" data-testid="tenders-mcp-options">
      <div className="grid gap-2 px-3">
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-emerald-800 dark:text-emerald-200">
          {tx("mcpKicker", "Buyer research")}
        </p>
        <h3 className="text-pretty font-medium text-foreground">
          {tx("mcpTitle", "Optional MCP for the buyer, never for the notice")}
        </h3>
        <p className="max-w-xl text-pretty text-[13px]">
          {tx(
            "mcpBody",
            "LinkedIn is the recommended option: company pages, contacts and posts after you enable it and sign in. Never invent a notice from a post. Connection requests and messages need your confirmation.",
          )}
        </p>
        <DefaultButton
          text={tx("openTendersMcp", "Open Settings > Tools")}
          onClick={() => openInIdeHash(CHANNELS_HASH)}
          styles={BUTTON_STYLES}
        />
      </div>
      {linkedin ? (
        <Surface className="grid gap-3 p-6">
          <div className="grid gap-3" data-testid="tenders-linkedin-mcp">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{linkedin.name || "LinkedIn"}</span>
            <span className="rounded-full bg-emerald-700/10 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:text-emerald-200">
              {tx("mcpRecommended", "Recommended")}
            </span>
          </div>
          <p className="max-w-xl text-pretty text-[13px]">{linkedin.role}</p>
          <p className="text-[12px]">
            {tx(
              "mcpLoginHint",
              "First use opens a LinkedIn login, or run uvx mcp-server-linkedin@latest --login / --import-from-browser.",
            )}
          </p>
          <ul className="grid gap-2">
            {MCP_GROUPS.map((group) => {
              const names = groups[group.id] || [];
              if (!names.length) return null;
              return (
                <li key={group.id} className="grid gap-1">
                  <span className="text-[12px] font-medium text-foreground">
                    {tx(group.key, group.fallback)}
                  </span>
                  <span className="font-mono text-[12px]">{names.join(" · ")}</span>
                </li>
              );
            })}
          </ul>
          {confirm.length ? (
            <p className="text-[12px]">
              {tx("mcpConfirmNote", "Needs your confirmation")}: {confirm.join(" · ")}
            </p>
          ) : null}
          {linkedin.jobs?.length ? (
            <p className="text-[12px]">
              {tx(
                "mcpJobsNote",
                "Job tools stay hiring context. They never become a public notice.",
              )}
            </p>
          ) : null}
          </div>
        </Surface>
      ) : null}
      {others.length ? (
        <ul className="grid gap-2 px-3">
          {others.map((row) => (
            <li key={row.id || row.name} className="grid gap-1">
              <span className="font-medium text-foreground">{row.name || row.id}</span>
              <span>{row.role}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

const CARD_FACT_ICON: Record<string, string> = {
  deadline: "Calendar",
  budget: "Money",
  duration: "Clock",
  place: "MapPin",
  remote: "Home",
  language: "LocaleLanguage",
  published: "Calendar",
};

function buyerInitials(name: string): string {
  const words = String(name || "").replace(/[^\p{L}\p{N} ]+/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() || "").join("");
}

function factKnown(fact: TenderFact | undefined): boolean {
  return Boolean(fact && (fact.value || fact.valueKey));
}

/** Board-style notice card: pills, title, tags, buyer excerpt on the left, the facts column on the right. */
function NoticeCard({
  row,
  view,
  tx,
  busy,
  token,
  downloading,
  locale,
  sourceNames,
  crafts,
  onAction,
  onDownload,
  onOfficial,
}: {
  row: TenderNotice;
  view: NoticeListView;
  tx: Tx;
  busy: boolean;
  token: string;
  downloading: string;
  locale: string;
  sourceNames?: Record<string, string>;
  crafts: string[];
  onAction: (action: NoticeRowAction, id: string) => void;
  onDownload?: (id: string, kind: TenderExportKind) => void;
  onOfficial?: (id: string) => void;
}) {
  const starred = isFavorite(row);
  const packReady = noticeHasOfferPack(row);
  const official = officialTenderHref(row.source_url || "");
  const facts = parseTenderFacts(row, { locale, sourceNames });
  const byKey = new Map([...facts.card, ...facts.extra].map((item) => [item.key, item]));
  const status = noticeStatusCode(String(row.status || ""));
  const goMark = noticeGoMark(row);
  const urgent = isUrgent(row);
  const side: TenderFact[] = [];
  for (const key of ["deadline", "budget", "duration"] as const) {
    const fact = byKey.get(key);
    if (factKnown(fact) && fact) side.push(fact);
  }
  const city = String(row.city || "").trim();
  const country = countryDisplayName(String(row.country || ""), locale);
  const place = [city, country && country !== "INTL" ? country : ""].filter(Boolean).join(", ");
  if (place) {
    side.push({ key: "place", labelKey: "factPlace", labelFallback: "Country / city", value: place });
  }
  for (const key of ["remote", "language"] as const) {
    const fact = byKey.get(key);
    if (factKnown(fact) && fact) side.push(fact);
  }
  const domain = domainLabel(noticeDomain(row, crafts), locale);
  const source = facts.card.find((item) => item.key === "source")?.value || "";
  const tags = [...new Set([domain, source].map((item) => item.trim()).filter(Boolean))].slice(0, 3);
  const published = byKey.get("published")?.value || noticeIsoDay(row.publication_date);
  const excerpt = (facts.need || facts.description).replace(/\s+/g, " ").trim().slice(0, 240);
  const buyer = String(row.buyer || "").trim() || source;
  const menuItems = rowMenuItems({
    view,
    starred,
    packReady,
    hasOfficial: Boolean(official),
    goMark,
    tx,
    onAction: (action) => onAction(action, row.id),
    onDownload: (kind) => onDownload?.(row.id, kind),
    onOfficial: () => onOfficial?.(row.id),
  });
  const rowBusy = busy || downloading.startsWith(`${row.id}:`);
  return (
    <article
      data-testid="tenders-notice-card"
      className="grid min-w-0 overflow-hidden rounded-2xl bg-background outline outline-1 outline-black/10 transition-shadow duration-150 hover:shadow-[0_8px_24px_rgba(15,23,42,0.08)] dark:outline-white/10 md:grid-cols-[minmax(0,1fr)_15.5rem]"
    >
      <div className="min-w-0 p-4 sm:p-5">
        <div className="flex flex-nowrap items-start gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
            {status ? (
              <span
                className={cn(
                  "rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide text-white",
                  status === "open" ? "bg-emerald-600" : status === "amended" ? "bg-sky-600" : "bg-slate-500",
                )}
              >
                {tx(`noticeStatus.${status}`, status)}
              </span>
            ) : null}
            {goMark === "go" ? (
              <span className="rounded-full bg-teal-600 px-2.5 py-0.5 text-[11px] font-semibold tracking-wide text-white">
                {tx("filterGo", "GO")}
              </span>
            ) : null}
            {goMark === "nogo" ? (
              <span className="rounded-full bg-slate-500 px-2.5 py-0.5 text-[11px] font-semibold tracking-wide text-white">
                {tx("filterNogo", "No-go")}
              </span>
            ) : null}
            {urgent ? (
              <span className="rounded-full bg-orange-500 px-2.5 py-0.5 text-[11px] font-semibold tracking-wide text-white">
                {tx("filterUrgent", "Urgent")}
              </span>
            ) : null}
            {row.archived ? (
              <span className="rounded-full bg-muted px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                {tx("archivedBadge", "Archived")}
              </span>
            ) : null}
          </div>
          <span
            className={cn("shrink-0 tabular-nums text-sm font-semibold", scoreTone(row.score))}
            data-testid="tenders-notice-score"
          >
            {row.score != null ? Math.round(row.score) : "-"}%
          </span>
          <IconButton
            ariaLabel={starred ? tx("rowUnfavorite", "Remove favorite") : tx("rowFavorite", "Favorite")}
            title={starred ? tx("rowUnfavorite", "Remove favorite") : tx("rowFavorite", "Favorite")}
            iconProps={{ iconName: starred ? "HeartFill" : "Heart" }}
            disabled={rowBusy}
            onClick={() => onAction(starred ? "unfavorite" : "favorite", row.id)}
            styles={{ root: { width: 32, height: 32, color: starred ? "rgb(225 29 72)" : undefined }, rootHovered: { color: "rgb(225 29 72)" } }}
            data-testid="tenders-notice-favorite"
          />
          <IconButton
            ariaLabel={tx("rowMenu", "Notice actions")}
            title={tx("rowMenu", "Notice actions")}
            disabled={rowBusy}
            iconProps={{ iconName: "MoreVertical" }}
            menuProps={{ ...NOTICE_ROW_MENU, items: menuItems }}
            styles={{ root: { width: 32, height: 32, flexShrink: 0 }, menuIcon: { display: "none" } }}
          />
        </div>
        <button
          type="button"
          className="mt-2 block w-full text-left"
          onClick={() => onAction("view", row.id)}
          data-testid="tenders-notice-open"
        >
          <h3 className="text-balance text-lg font-semibold leading-snug hover:underline">{facts.title}</h3>
        </button>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {tags.map((tag) => (
            <span key={tag} className="rounded-md bg-amber-300/30 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 dark:text-amber-200">
              {tag}
            </span>
          ))}
          {published ? <span className="ml-auto text-xs tabular-nums text-muted-foreground">{published}</span> : null}
        </div>
        <div className="mt-3 flex gap-3">
          <div
            aria-hidden
            className="grid size-14 shrink-0 place-items-center rounded-xl bg-emerald-500/12 text-base font-bold text-emerald-800 dark:text-emerald-300"
          >
            {buyerInitials(buyer)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{buyer}</p>
            <p className="mt-0.5 line-clamp-3 text-pretty text-sm text-muted-foreground">
              {excerpt || tx("noDescription", "No published description in the store yet.")}
            </p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
          {official && token ? (
            <OfficialLink
              href={official}
              token={token}
              className="text-[13px] text-emerald-700 underline underline-offset-2 dark:text-emerald-300"
            >
              {tx("rowOfficial", "Official notice")}
            </OfficialLink>
          ) : null}
          {packReady ? (
            <DefaultButton
              text={tx("rowDownloadWord", "Download offer")}
              iconProps={{ iconName: "WordDocument" }}
              disabled={rowBusy}
              onClick={() => onDownload?.(row.id, "docx")}
              styles={{ root: { minHeight: 32, height: 32, cursor: "pointer" } }}
            />
          ) : null}
          <button
            type="button"
            className="text-sm font-semibold text-emerald-700 underline-offset-2 hover:underline dark:text-emerald-300"
            onClick={() => onAction("view", row.id)}
          >
            {tx("viewNotice", "View this notice")}
          </button>
        </div>
      </div>
      <aside className="border-t border-black/10 bg-muted/30 p-4 dark:border-white/10 md:border-l md:border-t-0">
        <dl className="grid gap-3" data-testid="tenders-facts-row">
          {side.map((fact) => (
            <div key={fact.key} data-testid={`tenders-fact-${fact.key}`}>
              <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {tx(fact.labelKey, fact.labelFallback)}
              </dt>
              <dd className="mt-0.5 flex items-center gap-1.5 text-sm">
                <Icon iconName={CARD_FACT_ICON[fact.key] || "Info"} className="text-[13px] text-emerald-700 dark:text-emerald-300" aria-hidden />
                <span className="min-w-0 break-words">{factDisplay(fact, tx)}</span>
              </dd>
            </div>
          ))}
          {!side.length ? <p className="text-sm text-muted-foreground">{tx("unknown", "Not specified")}</p> : null}
        </dl>
      </aside>
    </article>
  );
}

export function PipelineList({
  rows,
  view,
  tx,
  busy,
  token = "",
  downloading = "",
  locale = "fr-FR",
  sourceNames,
  crafts = [],
  onAction,
  onDownload,
  onOfficial,
}: {
  rows: TenderNotice[];
  view: NoticeListView;
  tx: Tx;
  busy: boolean;
  token?: string;
  downloading?: string;
  locale?: string;
  sourceNames?: Record<string, string>;
  crafts?: string[];
  onAction: (action: NoticeRowAction, id: string) => void;
  onDownload?: (id: string, kind: TenderExportKind) => void;
  onOfficial?: (id: string) => void;
}) {
  return (
    <div className="grid min-w-0 gap-3" data-testid="tenders-notice-list">
      {rows.map((row) => (
        <NoticeCard
          key={row.id}
          row={row}
          view={view}
          tx={tx}
          busy={busy}
          token={token}
          downloading={downloading}
          locale={locale}
          sourceNames={sourceNames}
          crafts={crafts}
          onAction={onAction}
          onDownload={onDownload}
          onOfficial={onOfficial}
        />
      ))}
    </div>
  );
}

function rowMenuItems({
  view,
  starred,
  packReady,
  hasOfficial,
  goMark,
  tx,
  onAction,
  onDownload,
  onOfficial,
}: {
  view: NoticeListView;
  starred: boolean;
  packReady: boolean;
  hasOfficial: boolean;
  goMark: "go" | "nogo" | null;
  tx: Tx;
  onAction: (action: NoticeRowAction) => void;
  onDownload: (kind: TenderExportKind) => void;
  onOfficial: () => void;
}) {
  const viewItem = {
    key: "view",
    text: tx("rowView", "View"),
    iconProps: { iconName: "View" },
    onClick: () => onAction("view"),
  };
  const extra = [
    ...(hasOfficial
      ? [
          {
            key: "official",
            text: tx("rowOfficial", "Official notice"),
            iconProps: { iconName: "Globe" },
            onClick: onOfficial,
          },
        ]
      : []),
    ...(packReady
      ? [
          {
            key: "docx",
            text: tx("downloadWord", "Download Word"),
            iconProps: { iconName: "WordDocument" },
            onClick: () => onDownload("docx"),
          },
          {
            key: "pptx",
            text: tx("downloadPpt", "Download PowerPoint"),
            iconProps: { iconName: "PowerPointDocument" },
            onClick: () => onDownload("pptx"),
          },
        ]
      : []),
  ];
  const deleteItem = {
    key: "delete",
    text: tx("rowDelete", "Delete"),
    iconProps: { iconName: "Delete" },
    onClick: () => onAction("delete"),
  };
  if (view === "archive") {
    return [
      viewItem,
      ...extra,
      {
        key: "unarchive",
        text: tx("rowUnarchive", "Restore"),
        iconProps: { iconName: "Undo" },
        onClick: () => onAction("unarchive"),
      },
      deleteItem,
    ];
  }
  return [
    viewItem,
    {
      key: "qualify",
      text: tx("qualify", "Score this notice"),
      iconProps: { iconName: "NumberField" },
      onClick: () => onAction("qualify"),
    },
    {
      key: "go",
      text: tx("rowGo", "GO"),
      iconProps: { iconName: "Accept" },
      canCheck: true,
      checked: goMark === "go",
      onClick: () => onAction("go"),
    },
    {
      key: "nogo",
      text: tx("rowNogo", "No-go"),
      iconProps: { iconName: "Cancel" },
      canCheck: true,
      checked: goMark === "nogo",
      onClick: () => onAction("nogo"),
    },
    {
      key: "write",
      text: tx("write", "Write the reply"),
      iconProps: { iconName: "Edit" },
      onClick: () => onAction("write"),
    },
    ...extra,
    {
      key: "favorite",
      text: starred ? tx("rowUnfavorite", "Remove favorite") : tx("rowFavorite", "Favorite"),
      iconProps: { iconName: starred ? "FavoriteStarFill" : "FavoriteStar" },
      onClick: () => onAction(starred ? "unfavorite" : "favorite"),
    },
    {
      key: "archive",
      text: tx("rowArchive", "Archive"),
      iconProps: { iconName: "Archive" },
      onClick: () => onAction("archive"),
    },
    deleteItem,
  ];
}

function NoticePager({
  page,
  pages,
  count,
  pageSize,
  tx,
  onPage,
}: {
  page: number;
  pages: number;
  count: number;
  pageSize: number;
  tx: Tx;
  onPage: (page: number) => void;
}) {
  if (count <= pageSize) {
    return (
      <p className="text-[13px] tabular-nums text-muted-foreground">
        {tx("pageStatus", "Page {{page}} / {{pages}} · {{count}} notice(s) · {{size}} per page", {
          page,
          pages,
          count,
          size: pageSize,
        })}
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-[13px] tabular-nums text-muted-foreground">
        {tx("pageStatus", "Page {{page}} / {{pages}} · {{count}} notice(s) · {{size}} per page", {
          page,
          pages,
          count,
          size: pageSize,
        })}
      </p>
      <div className="flex flex-wrap gap-2">
        <DefaultButton
          text={tx("pagePrev", "Previous")}
          iconProps={{ iconName: "ChevronLeft" }}
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          styles={BUTTON_STYLES}
        />
        <DefaultButton
          text={tx("pageNext", "Next")}
          iconProps={{ iconName: "ChevronRight" }}
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          styles={BUTTON_STYLES}
        />
      </div>
    </div>
  );
}

export function DossierPane({
  desk,
  selected,
  tx,
  busy,
  onBack,
  onQualify,
  onWrite,
  onRevise,
  onStage,
  onMail,
  onSend,
  onCrmPush,
  onSeed,
  token,
  locale = "fr-FR",
}: {
  desk: TenderDesk;
  selected: TenderNotice;
  tx: Tx;
  busy: string;
  onBack: () => void;
  onQualify: (id: string) => void;
  onWrite: (id: string) => void;
  onRevise: (id: string, remarks: string) => void;
  onStage: (id: string, stage: string) => void;
  onMail: (id: string, kind: string) => void;
  onSend: (id: string, to: string, kind?: string) => void;
  onCrmPush: (id: string) => void;
  onSeed: (text: string) => void;
  token: string;
  locale?: string;
}) {
  const [to, setTo] = useState("");
  const [remarks, setRemarks] = useState("");
  const [downloading, setDownloading] = useState("");
  const [downloadNote, setDownloadNote] = useState("");
  const downloadPack = async (kind: TenderExportKind) => {
    if (!token) return;
    setDownloading(kind);
    setDownloadNote("");
    try {
      const result = await postTenders(token, "download", { id: selected.id, kind });
      if (result.download?.data) {
        triggerTenderDownload(result.download);
        return;
      }
      setDownloadNote(tx("downloadOfferFailed", "Download failed. Write the reply first."));
    } catch (err) {
      setDownloadNote((err as Error).message || tx("downloadOfferFailed", "Download failed. Write the reply first."));
    } finally {
      setDownloading("");
    }
  };
  const facts = parseTenderFacts(selected, {
    locale,
    sourceNames: sourceNameMap(desk.sources || [], desk.catalog || []),
  });
  const company = companyLine(desk.profile);
  const stage = String(selected.stage || "");
  const sendMode = String(desk.profile.send_mode || "approval");
  const lastMail = lastUnsentMail(selected.mail) || (selected.mail || []).slice(-1)[0];
  const lastSent = lastSentMail(selected.mail);
  const canSend = Boolean(lastUnsentMail(selected.mail)) && /.+@.+\..+/.test(to.trim());
  const decision =
    selected.go == null
      ? tx("notScoredYet", "Not scored yet")
      : selected.go
        ? tx("goYes", "GO")
        : tx("goNo", "NO-GO");
  const headline =
    stage === "go" || stage === "no-go" ? decision : `${decision} · ${stageLabel(stage, tx)}`;
  return (
    <div className="grid gap-8">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex min-h-11 w-fit cursor-pointer items-center gap-2 text-sm text-muted-foreground transition-colors duration-150 hover:text-foreground"
      >
        <Icon iconName="ChevronLeft" aria-hidden />
        {tx("backNotices", "Back to notices")}
      </button>
      <Surface className="grid gap-4 p-6 sm:p-8">
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-emerald-800 dark:text-emerald-200">
          {tx("readStep", "1. This notice")}
        </p>
        <div className="flex min-w-0 flex-nowrap items-start gap-3">
          <h2 className="min-w-0 flex-1 truncate text-2xl font-semibold" title={facts.title}>
            {facts.title}
          </h2>
          <p className={cn("shrink-0 text-3xl font-semibold tabular-nums", scoreTone(selected.score))}>
            {selected.score != null ? Math.round(selected.score) : "-"}
          </p>
        </div>
        {company ? <p className="text-pretty text-sm text-muted-foreground">{company}</p> : null}
        <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">{headline}</p>
        <TenderFactsRow facts={facts.card} tx={tx} />
        <TenderFactsRow facts={facts.extra} tx={tx} testId="tenders-dossier-facts-extra" className="sm:grid-cols-3" />
        {selected.source_url ? (
          <OfficialLink
            href={selected.source_url}
            token={token}
            className="text-sm text-emerald-700 underline underline-offset-2 dark:text-emerald-300"
          >
            {selected.source_url.replace(/^https?:\/\//, "")}
          </OfficialLink>
        ) : null}
        <p className="max-h-56 overflow-auto whitespace-pre-wrap text-pretty text-sm leading-relaxed text-muted-foreground">
          {facts.description || tx("noDescription", "No published description in the store yet.")}
        </p>
        {selected.go != null ? (
          <>
            <p className="text-2xl font-semibold tabular-nums">
              {decision}{" "}
              <span className={scoreTone(selected.go_pct ?? selected.score)}>
                {selected.go_pct ?? selected.score ?? "-"}%
              </span>
            </p>
            <p className="text-pretty text-sm text-muted-foreground">
              {facts.goReason || tx("qualifyFirst", "Run Score first.")}
            </p>
            {facts.goNote ? (
              <p className="text-pretty text-sm">{facts.goNote}</p>
            ) : null}
          </>
        ) : (
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("scoreHint", "Score compares this notice to your company, countries and crafts.")}
          </p>
        )}
        <Field label={tx("need", "Need")} value={facts.need || tx("unknown", "non renseigne")} />
        <Field
          label={tx("eligibility", "Eligibility")}
          value={facts.eligibility || tx("unknown", "non renseigne")}
        />
        <Field
          label={tx("gaps", "Gaps")}
          value={asLines(selected.analysis?.gaps).join("; ") || tx("noGaps", "none listed")}
        />
        <div className="flex flex-wrap items-center gap-3">
          <PrimaryButton
            text={tx("qualify", "Score this notice")}
            disabled={Boolean(busy)}
            onClick={() => onQualify(selected.id)}
            styles={BUTTON_STYLES}
          />
          <NoticeGoButtons
            row={selected}
            tx={tx}
            busy={Boolean(busy)}
            size="regular"
            onGo={() => onStage(selected.id, "go")}
            onNogo={() => onStage(selected.id, "no-go")}
          />
        </div>
      </Surface>

      <Surface className="grid gap-4 p-6 sm:p-8" data-testid="tenders-response-draft">
        <h3 className="text-lg font-semibold">{tx("writeStep", "2. Your reply")}</h3>
        <p className="text-pretty text-sm text-muted-foreground">
          {tx(
            "writeHint",
            "A response built from the notice and your company file. Review the requirements, evidence and open points before submission.",
          )}
        </p>
        {selected.enriched ? (
          <p className="text-pretty text-sm text-emerald-800 dark:text-emerald-200">
            {tx("enrichedNotice", "The official page was read to thicken this notice before writing.")}
          </p>
        ) : null}
        {selected.response?.letter || selected.response?.executive_summary ? (
          <>
            <DocumentGenerationNotice generation={selected.response.generation} />
            <DossierPreview title={chapterTitle(selected.response.language, "cover", tx("cover", "Cover page"))} body={asText(selected.response.cover)} />
            <DossierPreview title={chapterTitle(selected.response.language, "toc", tx("toc", "Contents"))} body={asText(selected.response.toc)} />
            <DossierPreview title={chapterTitle(selected.response.language, "letter", tx("letter", "Submission letter"))} body={asText(selected.response.letter)} />
            <DossierPreview title={chapterTitle(selected.response.language, "summary", tx("summary", "Executive summary"))} body={asText(selected.response.executive_summary)} />
            <DossierPreview title={chapterTitle(selected.response.language, "company", tx("company", "Company presentation"))} body={asText(selected.response.company)} />
            <DossierPreview title={chapterTitle(selected.response.language, "need", tx("chapterNeed", "Understanding of the need"))} body={asText(selected.response.need)} />
            <DossierPreview title={chapterTitle(selected.response.language, "approach", tx("approach", "Approach"))} body={asText(selected.response.approach)} />
            <DossierPreview title={chapterTitle(selected.response.language, "vision", tx("vision", "Vision"))} body={asText(selected.response.vision)} />
            <DossierPreview title={chapterTitle(selected.response.language, "functional", tx("functional", "Functional response"))} body={asText(selected.response.functional)} />
            <DossierPreview title={chapterTitle(selected.response.language, "architecture", tx("architecture", "Technical response"))} body={asText(selected.response.architecture)} />
            <DossierPreview title={chapterTitle(selected.response.language, "methodology", tx("methodology", "Methodology"))} body={asText(selected.response.methodology)} />
            <DossierPreview title={chapterTitle(selected.response.language, "followupKpi", tx("followupKpi", "Follow-up and KPIs"))} body={asText(selected.response.followup_kpi)} />
            <DossierPreview title={chapterTitle(selected.response.language, "raciRisks", tx("raciRisks", "RACI and risks"))} body={asText(selected.response.raci_risks)} />
            <DossierPreview title={chapterTitle(selected.response.language, "governance", tx("governance", "Project governance"))} body={asText(selected.response.governance)} />
            <DossierPreview title={chapterTitle(selected.response.language, "planning", tx("planning", "Schedule and milestones"))} body={asText(selected.response.planning)} />
            <DossierPreview title={chapterTitle(selected.response.language, "staffing", tx("staffing", "Staffing"))} body={asText(selected.response.staffing)} />
            <DossierPreview title={chapterTitle(selected.response.language, "references", tx("references", "References"))} body={asText(selected.response.references)} />
            <DossierPreview title={chapterTitle(selected.response.language, "matrix", tx("matrix", "Compliance matrix"))} body={asText(selected.response.compliance_matrix)} />
            <DossierPreview title={chapterTitle(selected.response.language, "financial", tx("financial", "Budget and price schedule"))} body={asText(selected.response.financial_schedule)} />
            {selected.response.revision_notes ? (
              <DossierPreview title={tx("revisionNotes", "Your remarks")} body={asText(selected.response.revision_notes)} />
            ) : null}
            {selected.response.used_files?.length ? (
              <p className="text-[12px] text-muted-foreground">
                {tx("usedFiles", "Used files")}: {selected.response.used_files.join(", ")}
              </p>
            ) : null}
            {selected.response.model ? (
              <p className="text-[12px] text-muted-foreground">
                {tx("writtenWith", "Written with {{model}}", { model: String(selected.response.model) })}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-3">
              <DefaultButton
                text={tx("downloadWord", "Download Word")}
                iconProps={{ iconName: "WordDocument" }}
                disabled={Boolean(busy) || downloading === "docx"}
                data-testid="tenders-download-docx"
                onClick={() => void downloadPack("docx")}
                styles={BUTTON_STYLES}
              />
              <DefaultButton
                text={tx("downloadPpt", "Download PowerPoint")}
                iconProps={{ iconName: "PowerPointDocument" }}
                disabled={Boolean(busy) || downloading === "pptx"}
                data-testid="tenders-download-pptx"
                onClick={() => void downloadPack("pptx")}
                styles={BUTTON_STYLES}
              />
              {selected.response.exports?.diagram_html ? (
                <DefaultButton
                  text={tx("downloadDiagram", "Download interactive diagram")}
                  iconProps={{ iconName: "Flow" }}
                  disabled={Boolean(busy) || Boolean(downloading)}
                  data-testid="tenders-download-diagram"
                  onClick={() => void downloadPack("diagram_html")}
                  styles={BUTTON_STYLES}
                />
              ) : null}
            </div>
            {downloadNote ? <p className="text-pretty text-sm text-rose-700 dark:text-rose-300">{downloadNote}</p> : null}
            <TextField
              label={tx("remarksLabel", "Remarks and requested changes")}
              multiline
              rows={4}
              value={remarks}
              placeholder={tx(
                "remarksHint",
                "Study the draft. Write only what must change. Empty = you can approve it as-is.",
              )}
              data-testid="tenders-remarks"
              onChange={(_, value) => setRemarks(value || "")}
            />
            <div className="flex flex-wrap gap-3">
              <DefaultButton
                text={tx("requestChanges", "Apply my remarks")}
                disabled={Boolean(busy) || !remarks.trim()}
                data-testid="tenders-revise"
                onClick={() => {
                  onRevise(selected.id, remarks.trim());
                  setRemarks("");
                }}
                styles={BUTTON_STYLES}
              />
              <DefaultButton
                text={tx("approveDraft", "Approve this draft")}
                disabled={Boolean(busy)}
                onClick={() => onStage(selected.id, "validating")}
                styles={BUTTON_STYLES}
              />
            </div>
          </>
        ) : (
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("noDraft", "No dossier yet. Build a notice-specific response.")}
          </p>
        )}
        <div className="flex flex-wrap gap-3">
          <PrimaryButton
            text={
              selected.response?.letter
                ? tx("rewrite", "Rewrite from the file")
                : tx("write", "Write the reply")
            }
            disabled={Boolean(busy)}
            onClick={() => onWrite(selected.id)}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("askWrite", "Ask the agent")}
            onClick={() =>
              onSeed(
                `/tenders Qualify ${selected.id} then write a ready dossier from the company file and this notice. Do not invent references.\n\n`,
              )
            }
            styles={BUTTON_STYLES}
          />
        </div>
      </Surface>

      <Surface className="grid gap-4 p-6 sm:p-8">
        <h3 className="text-lg font-semibold">{tx("buyerStep", "3. Send")}</h3>
        <p className="text-pretty text-sm text-muted-foreground">
          {tx(
            "approvalHint",
            "Public AO default: Approval before send. The desk never posts the bid by itself.",
          )}
        </p>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-[13px] leading-relaxed">
          {lastUnsentMail(selected.mail)?.body || tx("noMail", "No mail draft yet.")}
        </pre>
        <div className="flex flex-wrap gap-3">
          <DefaultButton
            text={tx("clarif", "Clarification draft")}
            disabled={Boolean(busy)}
            onClick={() => onMail(selected.id, "clarification")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("relance", "Follow-up draft")}
            disabled={Boolean(busy)}
            onClick={() => onMail(selected.id, "relance")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("submit", "Mark submitted")}
            disabled={Boolean(busy)}
            onClick={() => onStage(selected.id, "submitted")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("won", "Won")}
            disabled={Boolean(busy)}
            onClick={() => onStage(selected.id, "won")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("lost", "Lost")}
            disabled={Boolean(busy)}
            onClick={() => onStage(selected.id, "lost")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("shortlisted", "Shortlisted")}
            disabled={Boolean(busy)}
            onClick={() => onStage(selected.id, "shortlisted")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("negotiation", "Negotiation")}
            disabled={Boolean(busy)}
            onClick={() => onStage(selected.id, "negotiation")}
            styles={BUTTON_STYLES}
          />
        </div>
        <p className="text-pretty text-sm text-muted-foreground">
          {sendMode === "draft"
            ? tx("sendDraftMode", "This desk is in draft mode. Switch to Approval in Settings to send.")
            : sendMode === "autonomous"
              ? tx(
                  "sendAutoHint",
                  "Autonomous: the last draft can leave without a second click. The desk still never posts a public bid.",
                )
              : tx("sendApprovalHint", "Nothing leaves the desk until you click send on this exact draft.")}
        </p>
        <TextField
          label={tx("sendTo", "Recipient email")}
          value={to}
          placeholder={tx("sendToHint", "contact@buyer.gov")}
          onChange={(_, value) => setTo(value || "")}
        />
        {lastSent ? (
          <p className="text-[13px] text-teal-700 dark:text-teal-300">
            {tx("sentTo", "Last mail sent to {{to}}", { to: lastSent.to || "" })}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <PrimaryButton
            text={tx("sendNow", "Approve and send")}
            iconProps={{ iconName: "Mail" }}
            disabled={Boolean(busy) || sendMode === "draft" || !canSend}
            onClick={() => onSend(selected.id, to.trim(), String(lastUnsentMail(selected.mail)?.kind || lastMail?.kind || ""))}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={
              selected.crm_opportunity_id
                ? tx("crmUpdate", "Update the CRM deal")
                : tx("crmPush", "Create the CRM deal")
            }
            iconProps={{ iconName: "ContactCard" }}
            disabled={Boolean(busy)}
            onClick={() => onCrmPush(selected.id)}
            styles={BUTTON_STYLES}
          />
        </div>
        {!lastUnsentMail(selected.mail) ? (
          <p className="text-[13px] text-muted-foreground">
            {tx("sendNeedsDraft", "Draft a clarification or follow-up first. The desk never writes on send.")}
          </p>
        ) : null}
      </Surface>
    </div>
  );
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[12px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-pretty text-sm">{value}</p>
    </div>
  );
}

export function DeskSkeleton() {
  return (
    <div className="mx-auto grid w-full max-w-5xl gap-6" aria-hidden>
      <div className="h-28 animate-pulse rounded-2xl bg-muted/50" />
      <div className="h-36 animate-pulse rounded-2xl bg-muted/40" />
    </div>
  );
}
