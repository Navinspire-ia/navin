// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Customizer,
  DefaultButton,
  Dialog,
  DialogFooter,
  DialogType,
  Icon,
  IconButton,
  MessageBar,
  MessageBarType,
  PrimaryButton,
  ProgressIndicator,
  TextField,
  Toggle,
  createTheme,
  type IContextualMenuItem,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { LeadsDashboard } from "@/components/studio/leads/LeadsDashboard";
import { LeadsStart } from "@/components/studio/leads/LeadsStart";
import {
  BUTTON_STYLES,
  ICON_BUTTON_STYLES,
  LEAD_ROW_MENU,
  LEADS_PAGE_SIZE,
  ROW_ICON_BUTTON_STYLES,
  SPRING,
  SURFACE,
  openOfficialLeadUrl,
  scoreTone,
  type Tx,
} from "@/components/studio/leads/leads-ui";
import { usePagedRows } from "@/lib/crm-list";
import { TradingLoopSchedulePanel } from "@/components/studio/trading/TradingLoopSchedulePanel";
import { browserTimeZone } from "@/lib/trading-loop-schedule";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";
import {
  crmProjectLabel,
  downloadTextFile,
  emptyLeadsDesk,
  fetchLeadsDesk,
  fetchLeadsExport,
  leadHasBuyingSignal,
  leadHasDueStep,
  leadHasSequence,
  leadInOutreach,
  leadNextStep,
  leadOutreachDest,
  leadOutreachWhy,
  leadSourceLabel,
  officialLeadHref,
  postLeads,
  resolveLeadsCrmProject,
  type LeadLoopSchedule,
  type LeadBant,
  type LeadChannelReady,
  type LeadProvider,
  type LeadRow,
  type LeadSequenceStep,
  type LeadsChannels,
  type LeadsDesk,
} from "@/lib/leads-api";
import { readLastDevContext } from "@/lib/last-dev-context";
import { cn } from "@/lib/utils";

type View = "work" | "setup";
type DeskPane = "home" | "book";
type BookFilter = "all" | "strong" | "email" | "verified" | "pipeline" | "due" | "signals";

function useDeskWidth(ref: RefObject<HTMLElement | null>, fallback = 640): number {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const apply = () => setWidth(el.clientWidth);
    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

function DeskTab({
  icon,
  label,
  selected,
  disabled,
  onClick,
  testId,
  title,
  ariaLabel,
}: {
  icon: string;
  label: string;
  selected?: boolean;
  disabled?: boolean;
  onClick: () => void;
  testId?: string;
  title?: string;
  ariaLabel?: string;
}) {
  const reduceMotion = useReducedMotion();
  const name = ariaLabel || title || label;
  return (
    <motion.button
      type="button"
      whileTap={reduceMotion || disabled ? undefined : { scale: 0.96 }}
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      aria-label={name}
      aria-pressed={Boolean(selected)}
      data-testid={testId}
      className={cn(
        "relative inline-flex min-h-12 min-w-[4.25rem] shrink-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-xl px-2.5 py-1.5 text-center transition-[background-color,color,box-shadow,transform] duration-150",
        selected
          ? "bg-indigo-500/16 font-medium text-foreground shadow-[0_6px_16px_rgba(15,23,42,0.08)]"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        disabled && "cursor-not-allowed opacity-40 hover:bg-transparent hover:text-muted-foreground",
      )}
    >
      <Icon iconName={icon} className="text-[18px] text-indigo-500" aria-hidden />
      <span className="max-w-[5.5rem] truncate text-[11px] leading-none tracking-wide">{label}</span>
    </motion.button>
  );
}

function useLeadsTheme(mode: "light" | "dark") {
  return useMemo(
    () =>
      createTheme({
        palette:
          mode === "dark"
            ? {
                themePrimary: "#818CF8",
                themeLighterAlt: "#111827",
                themeLighter: "#312E81",
                themeLight: "#3730A3",
                themeTertiary: "#4F46E5",
                themeSecondary: "#818CF8",
                themeDarkAlt: "#A5B4FC",
                themeDark: "#C7D2FE",
                themeDarker: "#E0E7FF",
                neutralLighterAlt: "#0F172A",
                neutralLighter: "#172033",
                neutralLight: "#1E293B",
                neutralQuaternaryAlt: "#273449",
                neutralQuaternary: "#334155",
                neutralTertiaryAlt: "#475569",
                neutralTertiary: "#94A3B8",
                neutralSecondary: "#CBD5E1",
                neutralPrimaryAlt: "#E2E8F0",
                neutralPrimary: "#F8FAFC",
                neutralDark: "#F8FAFC",
                black: "#F8FAFC",
                white: "#0F172A",
              }
            : {
                themePrimary: "#4F46E5",
                themeLighterAlt: "#F5F3FF",
                themeLighter: "#E0E7FF",
                themeLight: "#C7D2FE",
                themeTertiary: "#818CF8",
                themeSecondary: "#6366F1",
                themeDarkAlt: "#4338CA",
                themeDark: "#3730A3",
                themeDarker: "#312E81",
                neutralLighterAlt: "#F8FAFC",
                neutralLighter: "#F1F5F9",
                neutralLight: "#E2E8F0",
                neutralQuaternaryAlt: "#CBD5E1",
                neutralQuaternary: "#94A3B8",
                neutralTertiaryAlt: "#64748B",
                neutralTertiary: "#475569",
                neutralSecondary: "#334155",
                neutralPrimaryAlt: "#1E293B",
                neutralPrimary: "#0F172A",
                neutralDark: "#020617",
                black: "#020617",
                white: "#FFFFFF",
              },
        defaultFontStyle: { fontFamily: '"DM Sans", Inter, sans-serif' },
      }),
    [mode],
  );
}

function isLiveLead(row: LeadRow): boolean {
  return !row.archived && !row.extra?.archived;
}

function filterRows(rows: LeadRow[], filter: BookFilter): LeadRow[] {
  const live = rows.filter(isLiveLead);
  if (filter === "strong") return live.filter((row) => (row.score || 0) >= 80);
  if (filter === "email") return live.filter((row) => Boolean(row.email));
  if (filter === "verified") return live.filter((row) => row.email_status === "verified");
  if (filter === "due") return live.filter((row) => leadHasDueStep(row));
  if (filter === "signals") return live.filter((row) => leadHasBuyingSignal(row));
  if (filter === "pipeline") return live.filter((row) => leadInOutreach(row));
  return live;
}

export function LeadsWorkspace({
  chatOpen,
  onToggleChat,
  onSeed,
  deskPane: deskPaneProp,
  leadId,
  projectPath,
  recentProjects,
  onDeskPane,
}: {
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
  deskPane?: DeskPane;
  leadId?: string;
  projectPath?: string | null;
  recentProjects?: Array<{ path?: string | null }>;
  onDeskPane?: (pane: DeskPane, lead?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const theme = useThemeValue();
  const fluentTheme = useLeadsTheme(theme);
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  const [desk, setDesk] = useState<LeadsDesk | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState("");
  const [view, setView] = useState<View>("work");
  const [deskPane, setDeskPane] = useState<DeskPane>(() =>
    deskPaneProp === "book" || leadId ? "book" : "home",
  );
  const [bookFilter, setBookFilter] = useState<BookFilter>("all");
  const [openId, setOpenId] = useState(() => leadId || "");
  const [confirmSequence, setConfirmSequence] = useState(false);
  const [confirmOptout, setConfirmOptout] = useState(false);
  const [keyDraft, setKeyDraft] = useState<Record<string, string>>({});
  const [channelDraft, setChannelDraft] = useState<LeadsChannels>({});
  const [providersOpen, setProvidersOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"start" | "edit">("start");
  const shellRef = useRef<HTMLDivElement>(null);
  const deskWidth = useDeskWidth(shellRef);
  const compact = deskWidth < 760;

  const tx: Tx = useCallback(
    (key, fallback, values) => t(`studio.leads.${key}`, { defaultValue: fallback, ...values }),
    [t],
  );

  const inFlightRef = useRef(false);
  const load = useCallback(async () => {
    if (!token || inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const next = await fetchLeadsDesk(token);
      setDesk(next);
      setError("");
    } catch (err) {
      setError((err as Error).message || tx("error.load", "Could not load the leads desk."));
    } finally {
      inFlightRef.current = false;
    }
  }, [token, tx]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const hunting =
      desk?.loop?.enabled
      || desk?.loop?.phase === "hunt"
      || desk?.loop?.phase === "sequence"
      || desk?.loop?.phase === "busy";
    if (!token || !hunting) return undefined;
    const id = window.setInterval(() => {
      void load();
    }, 8_000);
    return () => window.clearInterval(id);
  }, [token, desk?.loop?.enabled, desk?.loop?.phase, load]);

  const goPane = useCallback(
    (pane: DeskPane, lead = "") => {
      setOpenId(lead);
      setDeskPane(lead ? "book" : pane);
      onDeskPane?.(lead ? "book" : pane, lead || undefined);
    },
    [onDeskPane],
  );

  const run = useCallback(
    async (action: string, body: Record<string, unknown> = {}, ok = "") => {
      if (!token) return false;
      setBusy(action);
      setError("");
      setSaved("");
      try {
        const next = await postLeads(token, action, body);
        setDesk(next);
        if (action === "hunt") {
          setProvidersOpen(false);
          setView("work");
          goPane("book");
        }
        if (action === "lookalike") {
          const added = Number(next.lookalike?.added || 0);
          if (added <= 0) {
            setError(tx("error.lookalike", "No lookalikes yet. Enrich the seed or add a provider key."));
          } else if (ok) {
            setSaved(ok);
          }
        } else if (action === "sequences") {
          const result = next.sequences || {};
          const parts = [
            tx("saved.sequencesDrafted", "{{count}} drafted", { count: Number(result.drafted || 0) }),
            tx("saved.sequencesSent", "{{count}} sent", { count: Number(result.sent || 0) }),
            tx("saved.sequencesReady", "{{count}} ready for you", { count: Number(result.ready || 0) }),
          ];
          if (result.enrolled) {
            parts.push(tx("saved.sequencesEnrolled", "{{count}} enrolled", { count: Number(result.enrolled || 0) }));
          }
          const blocked = String(result.blocked || "");
          setSaved(parts.join(" · ") + (blocked ? ` · ${blocked}` : ""));
        } else if (ok) {
          setSaved(ok);
        }
        return true;
      } catch (err) {
        setError((err as Error).message || tx("error.action", "The leads action failed."));
        return false;
      } finally {
        setBusy("");
      }
    },
    [token, tx, goPane],
  );

  const submitSchedule = async (schedule: LeadLoopSchedule, runNow: boolean) => {
    const tz = browserTimeZone();
    const action = scheduleMode === "start" ? "start" : "schedule";
    const ok = await run(
      action,
      { schedule: { ...schedule, tz: tz || schedule.tz || null }, tz, run_now: runNow },
      action === "start"
        ? tx("saved.loop", "Loop armed. It hunts on this schedule while Navin is up.")
        : tx("saved.schedule", "Schedule saved."),
    );
    if (ok) setScheduleOpen(false);
  };

  useEffect(() => {
    const lead = (leadId || "").trim();
    if (lead && desk) {
      const exists = desk.leads.some((item) => item.id === lead && isLiveLead(item));
      if (exists) {
        setOpenId(lead);
        setDeskPane("book");
        return;
      }
      setOpenId("");
      setDeskPane("book");
      return;
    }
    if (lead && !desk) {
      setOpenId(lead);
      setDeskPane("book");
      return;
    }
    setOpenId("");
    setDeskPane(deskPaneProp === "book" ? "book" : "home");
  }, [desk, deskPaneProp, leadId]);

  useEffect(() => {
    setConfirmSequence(false);
    setConfirmOptout(false);
  }, [openId]);

  const exportCsv = useCallback(async () => {
    if (!token) return;
    setBusy("export");
    setError("");
    try {
      const filter = bookFilter === "strong" ? { tier: "A" } : {};
      const result = await fetchLeadsExport(token, filter);
      downloadTextFile(result.csv, result.filename || "navin-leads.csv");
      setSaved(tx("saved.export", "{{count}} leads exported as CSV.", { count: Number(result.rows || 0) }));
    } catch (err) {
      setError((err as Error).message || tx("error.action", "The leads action failed."));
    } finally {
      setBusy("");
    }
  }, [token, bookFilter, tx]);

  const live = desk || emptyLeadsDesk();
  const crmProject = useMemo(
    () =>
      resolveLeadsCrmProject({
        projectPath,
        lastDevPath: readLastDevContext()?.projectPath,
        recentProjects,
      }),
    [projectPath, recentProjects],
  );
  const crmName = crmProjectLabel(crmProject);
  const pushLeadToCrm = useCallback(
    (id: string) => {
      if (!crmProject) {
        setError(
          tx(
            "error.crmProject",
            "Open a project folder (not NavinProjects) to push this lead into the CRM.",
          ),
        );
        return;
      }
      void run("crm", { id, project: crmProject }, tx("saved.crm", "Pushed to the CRM."));
    },
    [crmProject, run, tx],
  );
  const openProviders = () => {
    setChannelDraft(live.channels?.configured || live.profile.channels || {});
    setProvidersOpen(true);
    setView("setup");
    void run("probe");
  };
  const connected = Boolean(desk);
  const ready = Boolean(live.wizard_ready || live.profile.wizard_ready);
  const selected = live.leads.find((row) => row.id === openId && isLiveLead(row)) || null;
  const missingLead = Boolean((leadId || "").trim()) && connected && !selected;
  const book = filterRows(live.leads, bookFilter);

  useEffect(() => {
    if (connected && !ready && !busy && view === "work" && !openId && !providersOpen) {
      setView("setup");
    }
  }, [connected, ready, busy, view, openId, providersOpen]);

  const showDesk = connected && view === "work";
  const showWizard = (connected && view === "setup") || (!connected && Boolean(error));
  const activePane: DeskPane = selected ? "book" : deskPane;

  const launchHunt = (body: Record<string, unknown>) => {
    setProvidersOpen(false);
    setView("work");
    goPane("book");
    void run("hunt", body, tx("saved.hunt", "Hunt finished. Review the book."));
  };

  const providersSelected = showWizard;
  const homeSelected = showDesk && activePane === "home";
  const bookSelected = showDesk && activePane === "book";
  const autonomous = live.profile.execution_mode === "autonomous";
  const sequencesHint = autonomous
    ? tx("sequencesSendHint", "Autonomous mode: due emails go out now, within the daily cap and the opt-out list.")
    : tx("sequencesDraftHint", "Approval mode: every due step gets a draft in the prospect's language. Nothing is sent.");
  const loopLabel = live.loop?.enabled ? tx("pause", "Pause loop") : tx("startLoop", "Start loop");

  return (
    <Customizer settings={{ theme: fluentTheme }}>
      <div
        ref={shellRef}
        className={cn(
          "flex h-full min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden bg-background",
          chatOpen ? "pr-0" : "",
        )}
        style={{
          paddingRight: chatOpen ? undefined : NOTIFICATION_GUTTER,
          WebkitFontSmoothing: "antialiased",
        }}
        data-compact={compact ? "1" : "0"}
      >
        <header className="flex min-w-0 shrink-0 items-center gap-3 px-4 py-2.5 sm:px-5 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.06)]">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
              {tx("kicker", "Studio")}
            </p>
            <div className={cn("flex min-w-0", compact ? "flex-col" : "items-baseline gap-2")}>
              <h1 className="shrink-0 whitespace-nowrap text-xl font-semibold tracking-tight">
                {tx("title", "Leads")}
              </h1>
              {live.profile.icp_name ? (
                <p className="min-w-0 truncate text-sm text-muted-foreground" title={live.profile.icp_name}>
                  {live.profile.icp_name}
                </p>
              ) : null}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {ready ? (
              live.loop?.enabled ? (
                <PrimaryButton
                  text={loopLabel}
                  iconProps={{ iconName: "Pause" }}
                  title={loopLabel}
                  ariaLabel={loopLabel}
                  onClick={() => void run("stop", {}, tx("saved.pause", "Loop paused."))}
                  disabled={Boolean(busy)}
                  styles={BUTTON_STYLES}
                  data-testid="leads-pause-loop"
                />
              ) : (
                <PrimaryButton
                  text={loopLabel}
                  iconProps={{ iconName: "Play" }}
                  title={loopLabel}
                  ariaLabel={loopLabel}
                  onClick={() => {
                    setScheduleMode("start");
                    setScheduleOpen(true);
                  }}
                  disabled={Boolean(busy)}
                  styles={BUTTON_STYLES}
                  data-testid="leads-start-loop"
                />
              )
            ) : null}
            {onToggleChat ? (
              <DefaultButton
                text={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                iconProps={{ iconName: "Chat" }}
                title={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                ariaLabel={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                aria-pressed={Boolean(chatOpen)}
                onClick={onToggleChat}
                styles={{
                  root: {
                    ...BUTTON_STYLES.root,
                    background: chatOpen ? "rgba(99, 102, 241, 0.16)" : undefined,
                  },
                }}
                data-testid="leads-chat"
              />
            ) : null}
            <IconButton
              iconProps={{ iconName: "Refresh" }}
              title={tx("refresh", "Refresh")}
              ariaLabel={tx("refresh", "Refresh")}
              disabled={Boolean(busy)}
              onClick={() => void load()}
              styles={ICON_BUTTON_STYLES}
              data-testid="leads-refresh"
            />
          </div>
        </header>
        <nav
          className="shrink-0 overflow-x-auto overscroll-x-contain px-2 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.08)]"
          aria-label={tx("panesAria", "Leads desk sections")}
        >
          <div className="flex min-w-max items-stretch gap-0.5 py-1">
            {ready ? (
              <>
                <DeskTab
                  icon="Home"
                  label={tx("pane.home", "Home")}
                  title={tx("homeDashboard", "Home")}
                  selected={homeSelected}
                  onClick={() => {
                    setView("work");
                    goPane("home");
                  }}
                />
                <DeskTab
                  icon="PageList"
                  label={tx("pane.book", "Book")}
                  title={tx("book", "Book")}
                  selected={bookSelected}
                  onClick={() => {
                    setView("work");
                    goPane("book");
                  }}
                />
              </>
            ) : null}
            <DeskTab
              icon="Settings"
              label={tx("pane.providers", "Providers")}
              title={tx("settings", "Providers")}
              selected={providersSelected}
              onClick={openProviders}
            />
            {ready ? (
              <>
                <span className="mx-1 w-px self-stretch bg-black/10 dark:bg-white/10" aria-hidden />
                <DeskTab
                  icon="Sync"
                  label={tx("pane.cycle", "Cycle")}
                  title={tx("cycle", "Run cycle")}
                  disabled={Boolean(busy)}
                  onClick={() => void run("tick", { force: true }, tx("saved.cycle", "Cycle finished."))}
                  testId="leads-run-cycle"
                />
                <DeskTab
                  icon={autonomous ? "Send" : "Edit"}
                  label={autonomous ? tx("pane.send", "Send") : tx("pane.draft", "Draft")}
                  title={sequencesHint}
                  ariaLabel={
                    autonomous
                      ? tx("sequencesSend", "Send due steps")
                      : tx("sequencesDraft", "Draft due steps")
                  }
                  disabled={Boolean(busy)}
                  onClick={() => void run("sequences")}
                  testId="leads-run-sequences"
                />
                <DeskTab
                  icon="Download"
                  label={tx("pane.export", "CSV")}
                  title={tx("exportCsv", "Export CSV")}
                  disabled={Boolean(busy) || !(live.kpis.total || 0)}
                  onClick={() => void exportCsv()}
                  testId="leads-export"
                />
              </>
            ) : null}
          </div>
        </nav>
        {busy ? <ProgressIndicator barHeight={3} /> : null}
        <div className="min-h-0 min-w-0 flex-1 overflow-auto overflow-x-hidden px-4 py-4 sm:px-5 sm:py-5">
          {error ? (
            <MessageBar
              messageBarType={MessageBarType.error}
              onDismiss={() => setError("")}
              className="mb-4"
              actions={
                !desk ? (
                  <DefaultButton text={tx("error.retry", "Retry")} onClick={() => void load()} styles={BUTTON_STYLES} />
                ) : undefined
              }
            >
              {error}
            </MessageBar>
          ) : null}
          {missingLead ? (
            <MessageBar messageBarType={MessageBarType.info} className="mb-4">
              {tx("error.missingLead", "This lead is not in the book. It may be archived or the link is stale.")}
            </MessageBar>
          ) : null}
          {saved ? (
            <MessageBar messageBarType={MessageBarType.success} onDismiss={() => setSaved("")} className="mb-4">
              {saved}
            </MessageBar>
          ) : null}
          {!token ? (
            <MessageBar messageBarType={MessageBarType.warning} className="mb-4">
              {tx("error.token", "Sign in to load the leads desk.")}
            </MessageBar>
          ) : null}
          {token && !desk && !error ? (
            <p className="mb-4 text-sm text-muted-foreground">{tx("loading", "Loading the leads desk.")}</p>
          ) : null}
          <AnimatePresence initial={false} mode="wait">
            {showWizard ? (
              <motion.div key="setup" initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
                {providersOpen ? (
                  <ProvidersPane
                    tx={tx}
                    token={token || ""}
                    providers={live.providers}
                    draft={keyDraft}
                    onDraft={setKeyDraft}
                    channels={channelDraft}
                    onChannels={setChannelDraft}
                    channelReady={live.channels?.ready || {}}
                    busy={Boolean(busy)}
                    onSaveKeys={() => void run("keys", keyDraft, tx("saved.keys", "Keys stored on this machine."))}
                    onSaveChannels={() =>
                      void run("profile", { channels: channelDraft }, tx("saved.channels", "Alert channels saved."))
                    }
                    onBack={() => {
                      setProvidersOpen(false);
                      setView(ready ? "work" : "setup");
                    }}
                  />
                ) : (
                  <LeadsStart
                    tx={tx}
                    profile={live.profile}
                    busy={Boolean(busy)}
                    onLaunch={launchHunt}
                    onDetails={() => setProvidersOpen(true)}
                    onSave={
                      ready
                        ? (body) => {
                            void run("profile", body, tx("saved.profile", "ICP saved. The next hunt uses it.")).then((ok) => {
                              if (ok) {
                                setView("work");
                                goPane("home");
                              }
                            });
                          }
                        : undefined
                    }
                    onBack={
                      ready
                        ? () => {
                            setView("work");
                            goPane("home");
                          }
                        : undefined
                    }
                  />
                )}
              </motion.div>
            ) : null}
            {showDesk && activePane === "home" ? (
              <motion.div key="home" initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
                <LeadsDashboard
                  tx={tx}
                  compact={compact}
                  headline={live.kpis.headline}
                  rows={live.leads}
                  providers={live.providers}
                  busy={Boolean(busy)}
                  onOpenBook={(filter) => {
                    setBookFilter((filter as BookFilter) || "all");
                    goPane("book");
                  }}
                  onHunt={() => void run("hunt", {}, tx("saved.hunt", "Hunt finished. Review the book."))}
                  onRescore={() => void run("rescore", {}, tx("saved.rescore", "Book rescored with BANT-F."))}
                  onOpenLead={(id) => goPane("book", id)}
                  onProviders={openProviders}
                  loop={live.loop}
                  locale={i18n.language}
                  profile={live.profile}
                  onEditProfile={() => {
                    setProvidersOpen(false);
                    setView("setup");
                  }}
                />
              </motion.div>
            ) : null}
            {showDesk && activePane === "book" ? (
              <motion.div key="book" initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
                {selected ? (
                  <LeadDossier
                    key={selected.id}
                    tx={tx}
                    token={token || ""}
                    row={selected}
                    busy={Boolean(busy)}
                    channelReady={live.channels?.ready || {}}
                    onBack={() => goPane("book")}
                    onStage={(stage) => void run("stage", { id: selected.id, stage })}
                    onEnrich={() => void run("enrich", { id: selected.id }, tx("saved.enrich", "Enrichment applied."))}
                    crmProjectName={crmName}
                    onCrm={() => pushLeadToCrm(selected.id)}
                    onOutreach={(channel, extra) =>
                      void run(
                        "outreach",
                        { id: selected.id, channel, send: true, ...extra },
                        tx("saved.outreach", "Message sent."),
                      )
                    }
                    confirmSequence={confirmSequence}
                    onAskSequence={() =>
                      selected.sequence?.steps?.length
                        ? setConfirmSequence(true)
                        : void run("sequence", { id: selected.id }, tx("saved.sequence", "Cold sequence started (j0 / j3 / j7)."))
                    }
                    onConfirmSequence={() => {
                      setConfirmSequence(false);
                      void run("sequence", { id: selected.id }, tx("saved.sequence", "Cold sequence started (j0 / j3 / j7)."));
                    }}
                    onCancelSequence={() => setConfirmSequence(false)}
                    onLookalike={() =>
                      void run("lookalike", { id: selected.id }, tx("saved.lookalike", "Lookalikes added to the book."))
                    }
                    onRedraft={(step) =>
                      void run(
                        "redraft",
                        { id: selected.id, step },
                        tx("saved.redraft", "Draft rewritten in the prospect's language."),
                      )
                    }
                    confirmOptout={confirmOptout}
                    onAskOptout={() => setConfirmOptout(true)}
                    onCancelOptout={() => setConfirmOptout(false)}
                    onConfirmOptout={(value) => {
                      setConfirmOptout(false);
                      void run("optout", { value }, tx("saved.optout", "Contact opted out. Sequences stopped, never re-added."));
                    }}
                    onSeed={onSeed}
                  />
                ) : (
                  <LeadBook
                    tx={tx}
                    rows={book}
                    filter={bookFilter}
                    busy={Boolean(busy)}
                    crmProjectName={crmName}
                    onFilter={setBookFilter}
                    onOpen={(id) => goPane("book", id)}
                    onCrm={(id) => pushLeadToCrm(id)}
                    onEnrich={(id) => void run("enrich", { id }, tx("saved.enrich", "Enrichment applied."))}
                    onSequence={(id) =>
                      void run("sequence", { id }, tx("saved.sequence", "Cold sequence started (j0 / j3 / j7)."))
                    }
                    onLookalike={(id) => void run("lookalike", { id }, tx("saved.lookalike", "Lookalikes added to the book."))}
                    onOptout={(value) =>
                      void run("optout", { value }, tx("saved.optout", "Contact opted out. Sequences stopped, never re-added."))
                    }
                    onDelete={(id) => {
                      void run("delete", { id }, tx("saved.delete", "Lead removed from the book.")).then((ok) => {
                        if (ok && openId === id) goPane("book");
                      });
                    }}
                  />
                )}
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
        <TradingLoopSchedulePanel
          key={`${scheduleMode}-${scheduleOpen ? "open" : "shut"}`}
          open={scheduleOpen}
          mode={scheduleMode}
          schedule={live.loop?.schedule}
          nextDue={live.loop?.next_due}
          enabled={Boolean(live.loop?.enabled)}
          locale={i18n.language}
          busy={Boolean(busy)}
          tx={tx}
          onDismiss={() => setScheduleOpen(false)}
          onSubmit={(schedule, runNow) => void submitSchedule(schedule, runNow)}
        />
      </div>
    </Customizer>
  );
}

const BOOK_COLUMNS: { key: string; label: string; fallback: string }[] = [
  { key: "company", label: "col.company", fallback: "Company" },
  { key: "contact", label: "col.contact", fallback: "Contact" },
  { key: "email", label: "col.email", fallback: "Email" },
  { key: "country", label: "col.country", fallback: "Country" },
  { key: "score", label: "col.score", fallback: "Score" },
  { key: "stage", label: "col.stage", fallback: "Stage" },
  { key: "source", label: "col.source", fallback: "Source" },
  { key: "next", label: "col.next", fallback: "Next action" },
];

type LeadRowKind = "view" | "crm" | "enrich" | "sequence" | "lookalike" | "optout" | "delete";

function leadBlockValue(row: LeadRow): string {
  return String(row.email || row.domain || "").trim();
}

function leadMenuItems(
  row: LeadRow,
  tx: Tx,
  onAction: (kind: LeadRowKind) => void,
): IContextualMenuItem[] {
  const blocked = leadBlockValue(row);
  return [
    {
      key: "view",
      text: tx("rowOpen", "Open"),
      iconProps: { iconName: "OpenFile" },
      onClick: () => onAction("view"),
    },
    {
      key: "crm",
      text: tx("rowCrm", "Add to CRM"),
      iconProps: { iconName: "AddFriend" },
      onClick: () => onAction("crm"),
    },
    {
      key: "enrich",
      text: tx("dossier.enrich", "Enrich missing fields"),
      iconProps: { iconName: "SearchData" },
      onClick: () => onAction("enrich"),
    },
    {
      key: "sequence",
      text: leadHasSequence(row)
        ? tx("dossier.sequenceRestart", "Restart sequence")
        : tx("dossier.sequenceStart", "Start j0/j3/j7"),
      iconProps: { iconName: "Mail" },
      onClick: () => onAction("sequence"),
    },
    {
      key: "lookalike",
      text: tx("dossier.lookalike", "Find lookalikes"),
      iconProps: { iconName: "People" },
      onClick: () => onAction("lookalike"),
    },
    {
      key: "optout",
      text: tx("dossier.optout", "Opt out"),
      iconProps: { iconName: "Blocked" },
      disabled: !blocked,
      onClick: () => onAction("optout"),
    },
    {
      key: "delete",
      text: tx("rowDelete", "Delete"),
      iconProps: { iconName: "Delete" },
      onClick: () => onAction("delete"),
    },
  ];
}

function LeadBook({
  tx,
  rows,
  filter,
  busy,
  crmProjectName,
  onFilter,
  onOpen,
  onCrm,
  onEnrich,
  onSequence,
  onLookalike,
  onOptout,
  onDelete,
}: {
  tx: Tx;
  rows: LeadRow[];
  filter: BookFilter;
  busy: boolean;
  crmProjectName?: string;
  onFilter: (value: BookFilter) => void;
  onOpen: (id: string) => void;
  onCrm: (id: string) => void;
  onEnrich: (id: string) => void;
  onSequence: (id: string) => void;
  onLookalike: (id: string) => void;
  onOptout: (value: string) => void;
  onDelete: (id: string) => void;
}) {
  const { page, setPage, slice, totalPages, pageSize } = usePagedRows(rows, LEADS_PAGE_SIZE);
  const [pending, setPending] = useState<{ kind: "delete" | "optout"; row: LeadRow } | null>(null);
  useEffect(() => {
    setPage(1);
  }, [filter, setPage]);

  const crmLabel = crmProjectName
    ? tx("dossier.crmNamed", "Add to CRM · {{project}}", { project: crmProjectName })
    : tx("rowCrm", "Add to CRM");

  const runRow = (kind: LeadRowKind, row: LeadRow) => {
    if (kind === "view") onOpen(row.id);
    else if (kind === "crm") onCrm(row.id);
    else if (kind === "enrich") onEnrich(row.id);
    else if (kind === "sequence") onSequence(row.id);
    else if (kind === "lookalike") onLookalike(row.id);
    else if (kind === "optout") setPending({ kind: "optout", row });
    else if (kind === "delete") setPending({ kind: "delete", row });
  };

  const filters: { id: BookFilter; label: string }[] = [
    { id: "all", label: tx("filter.all", "All") },
    { id: "strong", label: tx("filter.strong", "80+") },
    { id: "email", label: tx("filter.email", "Email") },
    { id: "verified", label: tx("filter.verified", "Verified") },
    { id: "due", label: tx("filter.due", "Due") },
    { id: "signals", label: tx("filter.signals", "Signals") },
    { id: "pipeline", label: tx("filter.pipeline", "Outreach") },
  ];
  return (
    <div className="grid gap-4" data-testid="leads-book">
      <div className="flex flex-wrap gap-2">
        {filters.map((item) => (
          <button
            key={item.id}
            type="button"
            data-testid={`leads-filter-${item.id}`}
            onClick={() => onFilter(item.id)}
            className={cn(
              "min-h-10 cursor-pointer rounded-full px-4 text-sm font-medium outline outline-1",
              filter === item.id
                ? "bg-indigo-600 text-white outline-indigo-600"
                : "bg-background outline-black/10 dark:outline-white/10",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <p className={cn(SURFACE, "p-6 text-sm text-muted-foreground")}>
          {tx("bookEmpty", "No leads in this view. Hunt companies or change the filter.")}
        </p>
      ) : (
        <>
          <div className={cn(SURFACE, "overflow-hidden")}>
            <div className="min-w-0 overflow-x-auto" data-testid="leads-book-table">
              <table className="w-full min-w-[64rem] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-black/10 text-left dark:border-white/10">
                    {BOOK_COLUMNS.map((col) => (
                      <th
                        key={col.key}
                        className="whitespace-nowrap px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
                      >
                        {tx(col.label, col.fallback)}
                      </th>
                    ))}
                    <th className="px-2 py-2">
                      <span className="sr-only">{tx("rowMenu", "Lead actions")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {slice.map((row) => {
                    const contact = [row.person, row.role].filter(Boolean).join(" · ");
                    return (
                      <tr
                        key={row.id}
                        className="border-b border-black/5 dark:border-white/10"
                        data-testid="leads-book-row"
                      >
                        <td className="max-w-[16rem] px-3 py-2">
                          <button
                            type="button"
                            onClick={() => onOpen(row.id)}
                            className="flex min-h-11 min-w-0 max-w-full cursor-pointer items-center rounded-lg px-1 text-left hover:bg-muted/30"
                            title={row.company}
                            data-testid="leads-book-open"
                          >
                            <span className="min-w-0 truncate font-medium">{row.company}</span>
                          </button>
                        </td>
                        <td className="max-w-[12rem] truncate px-3 py-2" title={contact}>
                          {contact || "-"}
                        </td>
                        <td className="max-w-[14rem] truncate px-3 py-2" title={row.email || ""}>
                          {row.email || tx("bookNoEmail", "no email yet")}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">{row.country || "-"}</td>
                        <td className={cn("whitespace-nowrap px-3 py-2 tabular-nums font-semibold", scoreTone(row.score))}>
                          {row.score ?? 0}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 uppercase text-muted-foreground">
                          {row.stage || "new"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          {row.source ? <SourceBadge source={row.source} /> : "-"}
                        </td>
                        <td className="max-w-[14rem] truncate px-3 py-2 text-muted-foreground" title={row.next_action || ""}>
                          {row.next_action || "-"}
                        </td>
                        <td className="whitespace-nowrap px-2 py-1">
                          <div className="flex flex-nowrap items-center justify-end gap-1">
                            <DefaultButton
                              text={tx("rowCrm", "Add to CRM")}
                              title={crmLabel}
                              ariaLabel={crmLabel}
                              iconProps={{ iconName: "AddFriend" }}
                              disabled={busy}
                              onClick={() => onCrm(row.id)}
                              styles={{ root: { minHeight: 40, minWidth: 0, cursor: "pointer" } }}
                              data-testid="leads-row-crm"
                            />
                            <IconButton
                              iconProps={{ iconName: "Delete" }}
                              title={tx("rowDelete", "Delete")}
                              ariaLabel={tx("rowDelete", "Delete")}
                              disabled={busy}
                              onClick={() => setPending({ kind: "delete", row })}
                              styles={ROW_ICON_BUTTON_STYLES}
                              data-testid="leads-row-delete"
                            />
                            <IconButton
                              iconProps={{ iconName: "MoreVertical" }}
                              title={tx("rowMenu", "Lead actions")}
                              ariaLabel={tx("rowMenu", "Lead actions")}
                              disabled={busy}
                              menuProps={{ ...LEAD_ROW_MENU, items: leadMenuItems(row, tx, (kind) => runRow(kind, row)) }}
                              styles={ROW_ICON_BUTTON_STYLES}
                              data-testid="leads-row-menu"
                            />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          <LeadPager
            page={page}
            pages={totalPages}
            count={rows.length}
            pageSize={pageSize}
            tx={tx}
            onPage={setPage}
          />
        </>
      )}
      <Dialog
        hidden={!pending}
        onDismiss={() => setPending(null)}
        dialogContentProps={
          pending?.kind === "optout"
            ? {
                type: DialogType.normal,
                title: tx("dossier.optoutConfirmTitle", "Opt this contact out?"),
                subText: tx(
                  "dossier.optoutConfirm",
                  "{{value}} goes on the do-not-contact list. Running sequences stop and the hunt never adds it back.",
                  { value: leadBlockValue(pending.row) },
                ),
              }
            : {
                type: DialogType.normal,
                title: tx("deleteConfirmTitle", "Delete this lead?"),
                subText: tx(
                  "deleteConfirmBody",
                  "{{company}} leaves the book. This cannot be undone.",
                  { company: pending?.row.company || "" },
                ),
              }
        }
      >
        <DialogFooter>
          <PrimaryButton
            text={
              pending?.kind === "optout" ? tx("dossier.optout", "Opt out") : tx("deleteConfirmYes", "Delete")
            }
            onClick={() => {
              if (!pending) return;
              if (pending.kind === "optout") onOptout(leadBlockValue(pending.row));
              else onDelete(pending.row.id);
              setPending(null);
            }}
          />
          <DefaultButton text={tx("dossier.cancel", "Cancel")} onClick={() => setPending(null)} />
        </DialogFooter>
      </Dialog>
    </div>
  );
}

function LeadPager({
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
  const status = (
    <p className="text-[13px] tabular-nums text-muted-foreground" data-testid="leads-page-status">
      {tx("pageStatus", "Page {{page}} / {{pages}} · {{count}} lead(s) · {{size}} per page", {
        page,
        pages,
        count,
        size: pageSize,
      })}
    </p>
  );
  if (count <= pageSize) return status;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      {status}
      <div className="flex flex-wrap gap-2">
        <DefaultButton
          text={tx("pagePrev", "Previous")}
          iconProps={{ iconName: "ChevronLeft" }}
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          styles={BUTTON_STYLES}
          data-testid="leads-page-prev"
        />
        <DefaultButton
          text={tx("pageNext", "Next")}
          iconProps={{ iconName: "ChevronRight" }}
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          styles={BUTTON_STYLES}
          data-testid="leads-page-next"
        />
      </div>
    </div>
  );
}

function LeadDossier({
  tx,
  token,
  row,
  busy,
  channelReady,
  onBack,
  onStage,
  onEnrich,
  onCrm,
  onOutreach,
  confirmSequence,
  onAskSequence,
  onConfirmSequence,
  onCancelSequence,
  onLookalike,
  onRedraft,
  confirmOptout,
  onAskOptout,
  onCancelOptout,
  onConfirmOptout,
  onSeed,
  crmProjectName,
}: {
  tx: Tx;
  token: string;
  row: LeadRow;
  busy: boolean;
  channelReady: Record<string, LeadChannelReady>;
  crmProjectName?: string;
  onBack: () => void;
  onStage: (stage: string) => void;
  onEnrich: () => void;
  onCrm: () => void;
  onOutreach: (channel: string, extra: { subject?: string; body?: string; to?: string }) => void;
  confirmSequence: boolean;
  onAskSequence: () => void;
  onConfirmSequence: () => void;
  onCancelSequence: () => void;
  onLookalike: () => void;
  onRedraft: (step: number) => void;
  confirmOptout: boolean;
  onAskOptout: () => void;
  onCancelOptout: () => void;
  onConfirmOptout: (value: string) => void;
  onSeed?: (text: string) => void;
}) {
  const stages = ["new", "qualified", "contacted", "replied", "meeting", "opportunity", "won"];
  const nextStep = leadNextStep(row);
  const nextDraft = nextStep?.draft;
  const [subject, setSubject] = useState(String(nextDraft?.subject || row.company || ""));
  const [body, setBody] = useState(String(nextDraft?.body || ""));
  const [loadedDraft, setLoadedDraft] = useState(`${nextStep?.n ?? ""}:${nextDraft?.body ?? ""}`);
  // A fresh draft from the desk (redraft, loop) replaces the compose box unless the user typed.
  const draftKey = `${nextStep?.n ?? ""}:${nextDraft?.body ?? ""}`;
  if (draftKey !== loadedDraft) {
    setLoadedDraft(draftKey);
    if (nextDraft?.body) {
      setSubject(String(nextDraft.subject || row.company || ""));
      setBody(String(nextDraft.body || ""));
    }
  }
  const optoutValue = String(row.email || row.domain || "").trim();
  const sequenceStopped = String(row.sequence?.stopped || "");
  const outreach = [
    row.company,
    row.person,
    row.role,
    row.email,
    row.phone,
    row.website,
    row.linkedin_url,
    `score ${row.score ?? 0}`,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <div className="mx-auto grid w-full max-w-3xl gap-4" data-testid="leads-dossier">
      <DefaultButton text={tx("dossier.back", "Back to the book")} iconProps={{ iconName: "Back" }} onClick={onBack} styles={BUTTON_STYLES} />
      <div className={cn(SURFACE, "grid gap-5 p-6")}>
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-balance text-2xl font-semibold">{row.company}</h2>
            {row.source ? <SourceBadge source={row.source} /> : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {[row.country, row.sector, row.size].filter(Boolean).join(" · ")}
          </p>
        </div>
        <dl className="grid gap-3 sm:grid-cols-2">
          <Fact label={tx("dossier.person", "Contact")} value={[row.person, row.role].filter(Boolean).join(" · ")} />
          <Fact label={tx("dossier.email", "Email")} value={row.email ? `${row.email} (${row.email_status || "unknown"})` : "-"} />
          <Fact label={tx("dossier.phone", "Phone")} value={row.phone || "-"} />
          <Fact label={tx("dossier.score", "AI score")} value={`${row.score ?? 0} / 100 · ${row.tier || "C"}`} />
          <Fact label={tx("dossier.next", "Next action")} value={row.next_action || "-"} />
          <Fact
            label={tx("dossier.signals", "Signals")}
            value={(row.signals || []).map((item) => item.kind).filter(Boolean).join(" · ") || "-"}
          />
        </dl>
        {row.next_action ? (
          <p className="rounded-xl bg-indigo-50 px-4 py-3 text-sm font-medium text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100">
            {row.next_action}
          </p>
        ) : null}
        <BantBars tx={tx} bant={row.bant} />
        {(row.why || []).length > 0 ? (
          <div>
            <p className="text-[12px] text-muted-foreground">{tx("dossier.why", "Why this score")}</p>
            <ul className="mt-2 grid gap-1 text-sm">
              {(row.why || []).map((item) => (
                <li key={item}>- {item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {(row.disqualify || []).length > 0 ? (
          <p className="text-sm text-rose-700 dark:text-rose-300">
            {tx("dossier.disqualify", "Watch-outs")}: {(row.disqualify || []).join(" · ")}
          </p>
        ) : null}
        {row.sequence?.steps?.length ? (
          <div data-testid="leads-sequence">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[12px] text-muted-foreground">{tx("dossier.sequence", "Sequence")}</p>
              {sequenceStopped ? (
                <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-medium text-rose-800 dark:bg-rose-950 dark:text-rose-200">
                  {tx("dossier.sequenceStopped", "Stopped: {{why}}", { why: sequenceStopped })}
                </span>
              ) : null}
            </div>
            <ul className="mt-2 grid gap-2 text-sm">
              {row.sequence.steps.map((step) => (
                <SequenceStepRow
                  key={String(step.n)}
                  tx={tx}
                  step={step}
                  isNext={Boolean(nextStep && step.n === nextStep.n && !sequenceStopped)}
                  busy={busy}
                  onUse={() => {
                    setSubject(String(step.draft?.subject || row.company || ""));
                    setBody(String(step.draft?.body || ""));
                  }}
                  onRedraft={() => onRedraft(Number(step.n) || 1)}
                />
              ))}
            </ul>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {officialLeadHref(row.website || row.domain || "") ? (
            <DefaultButton
              text={tx("dossier.site", "Website")}
              onClick={() => openOfficialLeadUrl(token, row.website || row.domain || "")}
              styles={BUTTON_STYLES}
            />
          ) : null}
          {officialLeadHref(row.linkedin_url || "") ? (
            <DefaultButton
              text="LinkedIn"
              onClick={() => openOfficialLeadUrl(token, row.linkedin_url || "")}
              styles={BUTTON_STYLES}
            />
          ) : null}
        </div>
        {row.signal ? <p className="text-pretty text-sm text-muted-foreground">{row.signal}</p> : null}
        <div className="flex flex-wrap gap-2">
          {stages.map((stage) => (
            <button
              key={stage}
              type="button"
              onClick={() => onStage(stage)}
              className={cn(
                "min-h-9 cursor-pointer rounded-full px-3 text-[12px] font-medium outline outline-1",
                row.stage === stage ? "bg-indigo-600 text-white outline-indigo-600" : "outline-black/10 dark:outline-white/10",
              )}
            >
              {stage}
            </button>
          ))}
        </div>
        <TextField
          label={tx("dossier.subject", "Subject")}
          value={subject}
          onChange={(_, value) => setSubject(value || "")}
        />
        <TextField
          label={tx("dossier.compose", "Message")}
          multiline
          rows={4}
          value={body}
          onChange={(_, value) => setBody(value || "")}
        />
        <div className="flex flex-wrap gap-2">
          <PrimaryButton text={tx("dossier.enrich", "Enrich missing fields")} disabled={busy} onClick={onEnrich} styles={BUTTON_STYLES} />
          <DefaultButton
            text={row.sequence?.steps?.length ? tx("dossier.sequenceRestart", "Restart sequence") : tx("dossier.sequenceStart", "Start j0/j3/j7")}
            disabled={busy}
            onClick={onAskSequence}
            styles={BUTTON_STYLES}
          />
          <DefaultButton text={tx("dossier.lookalike", "Find lookalikes")} disabled={busy} onClick={onLookalike} styles={BUTTON_STYLES} />
          <DefaultButton
            text={
              crmProjectName
                ? tx("dossier.crmNamed", "Add to CRM · {{project}}", { project: crmProjectName })
                : tx("dossier.crm", "Add to CRM")
            }
            disabled={busy}
            onClick={onCrm}
            data-testid="leads-crm"
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("dossier.sendEmail", "Send email")}
            title={outreachTitle(tx, row, "email", channelReady)}
            disabled={busy || Boolean(leadOutreachWhy(row, "email", channelReady))}
            onClick={() => onOutreach("email", { subject, body })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("dossier.sendWhatsapp", "Send WhatsApp")}
            title={outreachTitle(tx, row, "whatsapp", channelReady)}
            disabled={busy || Boolean(leadOutreachWhy(row, "whatsapp", channelReady))}
            onClick={() => onOutreach("whatsapp", { subject, body, to: leadOutreachDest(row, "whatsapp") })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("dossier.sendTelegram", "Send Telegram")}
            title={outreachTitle(tx, row, "telegram", channelReady)}
            disabled={busy || Boolean(leadOutreachWhy(row, "telegram", channelReady))}
            onClick={() => onOutreach("telegram", { subject, body, to: leadOutreachDest(row, "telegram") })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("dossier.sendTeams", "Send Teams")}
            title={outreachTitle(tx, row, "teams", channelReady)}
            disabled={busy || Boolean(leadOutreachWhy(row, "teams", channelReady))}
            onClick={() => onOutreach("teams", { subject, body, to: leadOutreachDest(row, "teams") })}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("dossier.emailDraft", "Draft in chat")}
            onClick={() =>
              onSeed?.(
                `/leads Write a short cold email for this lead. Facts only, no invented emails.\n\n${outreach}\n`,
              )
            }
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("dossier.optout", "Opt out")}
            title={
              optoutValue
                ? tx("dossier.optoutHint", "Stop every sequence for {{value}} and never re-add it.", { value: optoutValue })
                : tx("dossier.optoutNeed", "This lead has no email or domain to block.")
            }
            iconProps={{ iconName: "Blocked" }}
            disabled={busy || !optoutValue}
            onClick={onAskOptout}
            styles={BUTTON_STYLES}
            data-testid="leads-optout"
          />
        </div>
        <OutreachHints tx={tx} row={row} channelReady={channelReady} />
      </div>
      <Dialog
        hidden={!confirmOptout}
        onDismiss={onCancelOptout}
        dialogContentProps={{
          type: DialogType.normal,
          title: tx("dossier.optoutConfirmTitle", "Opt this contact out?"),
          subText: tx(
            "dossier.optoutConfirm",
            "{{value}} goes on the do-not-contact list. Running sequences stop and the hunt never adds it back.",
            { value: optoutValue },
          ),
        }}
      >
        <DialogFooter>
          <PrimaryButton text={tx("dossier.optout", "Opt out")} onClick={() => onConfirmOptout(optoutValue)} />
          <DefaultButton text={tx("dossier.cancel", "Cancel")} onClick={onCancelOptout} />
        </DialogFooter>
      </Dialog>
      <Dialog
        hidden={!confirmSequence}
        onDismiss={onCancelSequence}
        dialogContentProps={{
          type: DialogType.normal,
          title: tx("dossier.sequenceConfirmTitle", "Restart the sequence?"),
          subText: tx(
            "dossier.sequenceConfirm",
            "This replaces the current j0 / j3 / j7 cadence. Steps already sent stay in the journal.",
          ),
        }}
      >
        <DialogFooter>
          <PrimaryButton text={tx("dossier.sequenceRestart", "Restart sequence")} onClick={onConfirmSequence} />
          <DefaultButton text={tx("dossier.cancel", "Cancel")} onClick={onCancelSequence} />
        </DialogFooter>
      </Dialog>
    </div>
  );
}

function outreachTitle(
  tx: Tx,
  row: LeadRow,
  channel: string,
  ready: Record<string, LeadChannelReady>,
): string {
  const why = leadOutreachWhy(row, channel, ready);
  if (why === "need-email") return tx("dossier.needEmail", "Add an email on this lead first.");
  if (why === "need-phone") return tx("dossier.needPhone", "Add a phone number on this lead first.");
  if (why === "need-dest") return tx("dossier.needDest", "Add a destination on this lead first.");
  if (why === "need-channel") {
    return ready[channel]?.hint || tx("dossier.needChannel", "Connect this channel in Settings > Channels.");
  }
  return "";
}

function OutreachHints({
  tx,
  row,
  channelReady,
}: {
  tx: Tx;
  row: LeadRow;
  channelReady: Record<string, LeadChannelReady>;
}) {
  const hints = (["email", "whatsapp", "telegram", "teams"] as const)
    .map((channel) => outreachTitle(tx, row, channel, channelReady))
    .filter(Boolean);
  const unique = [...new Set(hints)];
  if (!unique.length) return null;
  return (
    <p className="text-pretty text-[12px] text-muted-foreground" data-testid="leads-outreach-hints">
      {unique.join(" ")}
    </p>
  );
}

function BantBars({ tx, bant }: { tx: Tx; bant?: LeadBant }) {
  const rows: { key: keyof LeadBant; label: string; max: number }[] = [
    { key: "fit", label: tx("dossier.fit", "Fit"), max: 30 },
    { key: "need", label: tx("dossier.need", "Need"), max: 25 },
    { key: "timing", label: tx("dossier.timing", "Timing"), max: 20 },
    { key: "authority", label: tx("dossier.authority", "Authority"), max: 15 },
    { key: "budget", label: tx("dossier.budget", "Budget"), max: 10 },
  ];
  return (
    <div className="grid gap-2">
      <p className="text-[12px] text-muted-foreground">{tx("dossier.bant", "BANT-F")}</p>
      {rows.map((item) => {
        const value = Number(bant?.[item.key] || 0);
        const pct = Math.max(0, Math.min(100, (value / item.max) * 100));
        return (
          <div key={item.key} className="grid gap-1">
            <div className="flex justify-between text-[12px]">
              <span>{item.label}</span>
              <span className="tabular-nums">
                {value}/{item.max}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-indigo-600" style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm font-medium">{value || "-"}</dd>
    </div>
  );
}

const SOURCE_TONE: Record<string, string> = {
  hiring: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  web: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  osm: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  sirene: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  companies_house: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  opencorporates: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
};

export function SourceBadge({ source }: { source: string }) {
  const key = String(source || "").toLowerCase();
  const label = leadSourceLabel(key);
  if (!label) return null;
  return (
    <span
      className={cn(
        "inline-block rounded-full px-2 py-0.5 text-[11px] font-medium",
        SOURCE_TONE[key] || "bg-muted text-muted-foreground",
      )}
      data-testid="leads-source-badge"
    >
      {label}
    </span>
  );
}

function stepStatusTone(status: string, isNext: boolean): string {
  if (status === "sent") return "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200";
  if (isNext && status === "due") return "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200";
  return "bg-muted text-muted-foreground";
}

function SequenceStepRow({
  tx,
  step,
  isNext,
  busy,
  onUse,
  onRedraft,
}: {
  tx: Tx;
  step: LeadSequenceStep;
  isNext: boolean;
  busy: boolean;
  onUse: () => void;
  onRedraft: () => void;
}) {
  const [open, setOpen] = useState(isNext);
  const status = String(step.status || "pending");
  const draft = step.draft;
  const statusLabel =
    status === "sent"
      ? tx("dossier.stepSent", "sent")
      : status === "due"
        ? tx("dossier.stepDue", "due")
        : tx("dossier.stepPending", "pending");
  return (
    <li className={cn("rounded-xl outline outline-1 outline-black/10 dark:outline-white/10", isNext && "outline-indigo-400/60")}>
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left"
          data-testid={`leads-step-${step.n}`}
        >
          <span className="font-medium">{step.label || `j${step.day}`}</span>
          <span className="text-muted-foreground">· {step.sent_channel || step.channel}</span>
          <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", stepStatusTone(status, isNext))}>{statusLabel}</span>
          {draft?.model ? (
            <span className="text-[11px] text-muted-foreground">{tx("dossier.stepAi", "AI · {{model}}", { model: draft.model })}</span>
          ) : draft?.body ? (
            <span className="text-[11px] text-muted-foreground">{tx("dossier.stepTemplate", "template · {{lang}}", { lang: draft.lang || "" })}</span>
          ) : null}
          {draft?.subject ? <span className="min-w-0 truncate text-muted-foreground">{draft.subject}</span> : null}
        </button>
        {status !== "sent" ? (
          <span className="flex shrink-0 gap-1">
            {draft?.body ? (
              <DefaultButton text={tx("dossier.stepUse", "Use this draft")} onClick={onUse} disabled={busy} styles={BUTTON_STYLES} />
            ) : null}
            <DefaultButton
              text={draft?.body ? tx("dossier.stepRedraft", "Rewrite") : tx("dossier.stepDraft", "Draft")}
              iconProps={{ iconName: "Edit" }}
              onClick={onRedraft}
              disabled={busy}
              styles={BUTTON_STYLES}
            />
          </span>
        ) : null}
      </div>
      {open && draft?.body ? (
        <pre className="whitespace-pre-wrap border-t border-black/10 px-3 py-3 font-sans text-[13px] leading-relaxed text-foreground dark:border-white/10">
          {draft.body}
        </pre>
      ) : null}
    </li>
  );
}

function ProvidersPane({
  tx,
  token,
  providers,
  draft,
  onDraft,
  channels,
  onChannels,
  channelReady,
  busy,
  onSaveKeys,
  onSaveChannels,
  onBack,
}: {
  tx: Tx;
  token: string;
  providers: LeadProvider[];
  draft: Record<string, string>;
  onDraft: (next: Record<string, string>) => void;
  channels: LeadsChannels;
  onChannels: (next: LeadsChannels) => void;
  channelReady: Record<string, LeadChannelReady>;
  busy: boolean;
  onSaveKeys: () => void;
  onSaveChannels: () => void;
  onBack: () => void;
}) {
  return (
    <div className="mx-auto grid w-full max-w-3xl gap-4">
      <DefaultButton text={tx("providers.back", "Back")} iconProps={{ iconName: "Back" }} onClick={onBack} styles={BUTTON_STYLES} />
      <div className={cn(SURFACE, "grid gap-5 p-6")}>
        <div>
          <h2 className="text-2xl font-semibold">{tx("providers.title", "Lead providers")}</h2>
          <p className="mt-2 text-pretty text-sm text-muted-foreground">
            {tx(
              "providers.sub",
              "Keys stay on this machine. Free sources (SIRENE, web) run without a key. LinkedIn is a reference link only.",
            )}
          </p>
        </div>
        {providers.map((row) => {
          const docs = officialLeadHref(row.docs || "");
          let badge = tx("providers.missing", "No key");
          if (row.kind === "reference") badge = tx("providers.reference", "Reference link");
          else if (row.kind === "free" && row.live === true) badge = tx("providers.live", "Live");
          else if (row.kind === "free" && row.live === false) badge = tx("providers.down", "Down");
          else if (row.kind === "free") badge = tx("providers.free", "No key needed");
          else if (row.configured) badge = tx("providers.set", "Configured {{hint}}", { hint: row.hint || "" });
          return (
            <div key={row.id} className="grid gap-2 rounded-xl bg-muted/30 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="font-medium">{row.name}</p>
                <p className="text-[12px] text-muted-foreground">{badge}</p>
              </div>
              <p className="text-[12px] text-muted-foreground">{row.notes}</p>
              {row.error ? <p className="text-[12px] text-rose-700 dark:text-rose-300">{row.error}</p> : null}
              {row.kind === "byok" ? (
                <TextField
                  type="password"
                  value={draft[row.id] || ""}
                  onChange={(_, value) => onDraft({ ...draft, [row.id]: value || "" })}
                  placeholder={row.configured ? "••••" : tx("providers.paste", "Paste API key")}
                />
              ) : null}
              {docs ? (
                <DefaultButton
                  text={tx("providers.docs", "Open docs")}
                  onClick={() => openOfficialLeadUrl(token, docs)}
                  styles={BUTTON_STYLES}
                />
              ) : null}
            </div>
          );
        })}
        <PrimaryButton text={tx("providers.save", "Save keys")} disabled={busy} onClick={onSaveKeys} styles={BUTTON_STYLES} />
      </div>
      <div className={cn(SURFACE, "grid gap-5 p-6")}>
        <div>
          <h2 className="text-2xl font-semibold">{tx("channels.title", "Alert channels")}</h2>
          <p className="mt-2 text-pretty text-sm text-muted-foreground">
            {tx(
              "channels.sub",
              "Heartbeat watch pings you here when a lead scores 80+. Connect the apps in Settings > Channels, then turn them on.",
            )}
          </p>
        </div>
        {(
          [
            ["email", "email_to", tx("channels.email", "Email")],
            ["whatsapp", "whatsapp_to", tx("channels.whatsapp", "WhatsApp")],
            ["telegram", "telegram_to", tx("channels.telegram", "Telegram")],
            ["teams", "teams_to", tx("channels.teams", "Microsoft Teams")],
          ] as const
        ).map(([flag, dest, label]) => {
          const ready = channelReady[flag];
          return (
            <div key={flag} className="grid gap-2 rounded-xl bg-muted/30 p-4">
              <Toggle
                label={label}
                checked={Boolean(channels[flag])}
                onChange={(_, checked) => onChannels({ ...channels, [flag]: Boolean(checked) })}
              />
              <p className="text-[12px] text-muted-foreground">
                {ready?.ready ? tx("channels.ready", "Connected") : tx("channels.off", "Not connected")}
                {ready?.hint ? ` - ${ready.hint}` : ""}
              </p>
              {channels[flag] ? (
                <TextField
                  label={tx("channels.to", "Destination")}
                  value={String(channels[dest] || "")}
                  onChange={(_, value) => onChannels({ ...channels, [dest]: value || "" })}
                />
              ) : null}
            </div>
          );
        })}
        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            text={tx("channels.save", "Save channels")}
            disabled={busy}
            onClick={onSaveChannels}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("channels.open", "Open Settings > Channels")}
            iconProps={{ iconName: "NavigateExternalInline" }}
            onClick={() => {
              window.location.hash = "#/tools";
            }}
            styles={BUTTON_STYLES}
          />
        </div>
      </div>
    </div>
  );
}
