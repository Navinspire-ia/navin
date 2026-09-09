// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Customizer, DefaultButton, IconButton, MessageBar, MessageBarType, PrimaryButton, ProgressIndicator } from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { DeskSkeleton, DossierPane, HomePane, NoticesPane } from "@/components/studio/tenders/TendersDesk";
import { buildTenderKpis } from "@/components/studio/tenders/TendersKpis";
import type { NoticeListView, PipelineFilter } from "@/components/studio/tenders/pipeline";
import { TendersWizard } from "@/components/studio/tenders/TendersWizard";
import { TenderAlertDeliveries } from "@/components/studio/tenders/TenderAlertDeliveries";
import { TradingLoopSchedulePanel } from "@/components/studio/trading/TradingLoopSchedulePanel";
import {
  BUTTON_STYLES,
  ICON_BUTTON_STYLES,
  SPRING,
  Surface,
  friendlyError,
  useTendersTheme,
  type Tx,
} from "@/components/studio/tenders/tenders-ui";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";
import { browserTimeZone } from "@/lib/trading-loop-schedule";
import { fetchTendersDesk, postTenders, type TenderDesk, type TenderLoopSchedule } from "@/lib/tenders-api";
import { cn } from "@/lib/utils";

type View = "work" | "setup";
type DeskPane = "home" | "tenders";

function emptyDesk(): TenderDesk {
  return {
    profile: {
      send_mode: "approval",
      countries: [],
      crafts: [],
      channels: {},
      references: [],
    },
    wizard_ready: false,
    tenders: [],
    kpis: { open: 0, new: 0, qualified: 0, deadline_7d: 0, weighted_value: 0 },
    sources: [],
    catalog: [],
    catalog_by_zone: [],
    queries: [],
    discoveries: [],
    journal: [],
    stages: [],
    send_modes: ["draft", "approval", "autonomous"],
    channels: {},
    loop: { enabled: false, phase: "idle", cycle: 0 },
  };
}

export function TendersWorkspace({
  chatOpen,
  onToggleChat,
  onSeed,
  noticeId,
  pane,
  onPane,
  onNotice,
}: {
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
  noticeId?: string;
  pane?: "home" | "tenders";
  onPane?: (pane: "home" | "tenders") => void;
  onNotice?: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const theme = useThemeValue();
  const fluentTheme = useTendersTheme(theme);
  const customizerSettings = useMemo(() => ({ theme: fluentTheme }), [fluentTheme]);
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  const [desk, setDesk] = useState<TenderDesk | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [savedType, setSavedType] = useState(MessageBarType.success);
  const [busy, setBusy] = useState("");
  const [view, setView] = useState<View>("work");
  const [deskPane, setDeskPane] = useState<DeskPane>(
    noticeId || pane === "tenders" ? "tenders" : "home",
  );
  const [bookView, setBookView] = useState<NoticeListView>("pipeline");
  const [bookFilter, setBookFilter] = useState<PipelineFilter | null>("all");
  const [openId, setOpenId] = useState(noticeId || "");
  const stayOnSetup = useRef(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"start" | "edit">("start");

  const openHome = useCallback(() => {
    stayOnSetup.current = false;
    setOpenId("");
    setView("work");
    setDeskPane("home");
    onPane?.("home");
  }, [onPane]);

  const openBook = useCallback(
    (nextView?: NoticeListView, nextFilter?: PipelineFilter | null) => {
      stayOnSetup.current = false;
      setOpenId("");
      setView("work");
      setDeskPane("tenders");
      if (nextView) setBookView(nextView);
      if (nextFilter !== undefined) setBookFilter(nextFilter);
      onPane?.("tenders");
    },
    [onPane],
  );

  const tx: Tx = useCallback(
    (key, fallback, values) => t(`studio.tenders.${key}`, { defaultValue: fallback, ...values }),
    [t],
  );
  const txRef = useRef(tx);
  txRef.current = tx;

  const inFlightRef = useRef(false);
  const load = useCallback(async () => {
    if (!token || inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const next = await fetchTendersDesk(token);
      setDesk(next);
      setError("");
    } catch (err) {
      setError(friendlyError((err as Error).message || "", txRef.current));
    } finally {
      inFlightRef.current = false;
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const hunting = desk?.loop?.phase === "hunt" || desk?.loop?.phase === "busy";
    const awaitingReceipts = Boolean(desk?.alert_deliveries?.pending);
    if (!token || (!hunting && !awaitingReceipts)) return undefined;
    const id = window.setInterval(() => {
      void load();
    }, awaitingReceipts ? 3_000 : 8_000);
    return () => window.clearInterval(id);
  }, [token, desk?.loop?.phase, desk?.alert_deliveries?.pending, load]);

  useEffect(() => {
    const next = (noticeId || "").trim();
    setOpenId(next);
    if (next && !stayOnSetup.current) {
      setView("work");
      setDeskPane("tenders");
    }
  }, [noticeId]);

  useEffect(() => {
    if ((noticeId || "").trim() || stayOnSetup.current) return;
    if (pane === "tenders") {
      setView("work");
      setDeskPane("tenders");
    }
  }, [noticeId, pane]);

  const showNotice = useCallback(
    (id: string) => {
      if (id) stayOnSetup.current = false;
      setOpenId(id);
      if (id) {
        setView("work");
        setDeskPane("tenders");
      }
      onNotice?.(id);
    },
    [onNotice],
  );

  const run = useCallback(
    async (action: string, body: Record<string, unknown> = {}) => {
      if (!token) return false;
      setBusy(action);
      setError("");
      setSaved("");
      setSavedType(MessageBarType.success);
      try {
        const result = await postTenders(token, action, body);
        setDesk(result);
        if (
          action === "profile" ||
          action === "knowledge" ||
          action === "upload" ||
          action === "remove-file" ||
          action === "add-reference" ||
          action === "custom-source"
        ) {
          setSaved(tx("saved", "Saved."));
        } else if (action === "follow") {
          const confirmed = result.watch?.delivered === true;
          if (result.watch?.count && !confirmed) setSavedType(MessageBarType.info);
          setSaved(
            result.watch?.count
              ? confirmed
                ? (i18n.language.startsWith("fr") ? "Les canaux activés ont confirmé le récapitulatif." : "Enabled channels confirmed the digest.")
                : (i18n.language.startsWith("fr") ? "Les alertes restent en attente de confirmation. Consultez le suivi par canal." : "Alerts are awaiting confirmation. Check delivery tracking by channel.")
              : tx("followNothing", "Nothing new to report."),
          );
        } else if (action === "notify" || action === "retry-alerts") {
          setSavedType(MessageBarType.info);
          setSaved(i18n.language.startsWith("fr") ? "Demande enregistrée. Les confirmations apparaissent dans le suivi des alertes." : "Request recorded. Confirmations appear in alert delivery tracking.");
        } else if (action === "crm-sync") {
          setSaved(
            tx("crmDone", "CRM updated: {{created}} created, {{updated}} updated.", {
              created: result.crm?.created || 0,
              updated: result.crm?.updated || 0,
            }),
          );
        } else if (action === "send") {
          setSaved(tx("sendDone", "Mail sent to {{to}}.", { to: result.send?.to || "" }));
        } else if (action === "favorite") {
          setSaved(tx("favoriteSaved", "Added to favorites."));
        } else if (action === "unfavorite") {
          setSaved(tx("unfavoriteSaved", "Removed from favorites."));
        } else if (action === "archive") {
          setSaved(tx("archiveSaved", "Notice archived."));
        } else if (action === "unarchive") {
          setSaved(tx("unarchiveSaved", "Notice restored."));
        } else if (action === "delete") {
          setSaved(tx("deleteSaved", "Notice deleted."));
        } else if (action === "start") {
          setSaved(result.loop_tick?.reason || tx("loopStarted", "Loop started."));
        } else if (action === "stop") {
          setSaved(result.loop?.last_result || tx("loopPaused", "Loop paused."));
        } else if (action === "schedule") {
          setSaved(result.loop?.last_result || tx("loopScheduleSaved", "Schedule saved."));
        } else if (action === "tick") {
          setSaved(result.loop_tick?.reason || tx("cycleDone", "Cycle done."));
        } else if (action === "write") {
          setSaved(tx("writeDone", "Ready draft on file. Study it, then remark only if needed."));
        } else if (action === "revise") {
          setSaved(tx("reviseDone", "Remarks applied. Review the updated draft."));
        } else if (action === "stage") {
          const stage = String(body.stage || "");
          if (stage === "go") setSaved(tx("goSaved", "Marked GO."));
          else if (stage === "no-go") setSaved(tx("nogoSaved", "Marked No-go."));
        }
        return true;
      } catch (err) {
        setError(friendlyError((err as Error).message || "", tx));
        return false;
      } finally {
        setBusy("");
      }
    },
    [token, tx, i18n.language],
  );

  const live = desk || emptyDesk();
  const connected = Boolean(desk);
  const ready = Boolean(live.wizard_ready);
  const selected = useMemo(
    () => live.tenders.find((row) => row.id === openId) || null,
    [live, openId],
  );
  const kpis = useMemo(
    () =>
      buildTenderKpis({
        fresh: live.kpis.new || 0,
        open: live.kpis.open || 0,
        qualified: live.kpis.qualified || 0,
        deadline7d: live.kpis.deadline_7d || 0,
        weighted: live.kpis.weighted_value || 0,
        labels: {
          fresh: tx("kpiNew", "New"),
          open: tx("kpiOpen", "Still in play"),
          qualified: tx("kpiQualified", "Qualified"),
          deadline: tx("kpiDeadline", "Due this week"),
          weighted: tx("kpiWeighted", "Weighted value"),
        },
      }),
    [live, tx],
  );

  useEffect(() => {
    if (connected && !ready && view === "work" && !openId) setView("setup");
  }, [connected, ready, view, openId]);

  const showDesk = connected && ready && view === "work";
  const showWizard = connected && (view === "setup" || !ready);

  const submitSchedule = async (schedule: TenderLoopSchedule, runNow: boolean) => {
    const tz = browserTimeZone();
    const action = scheduleMode === "start" ? "start" : "schedule";
    const ok = await run(action, {
      schedule: { ...schedule, tz: tz || schedule.tz || null },
      tz,
      run_now: runNow,
    });
    if (ok) setScheduleOpen(false);
  };

  return (
    <Customizer settings={customizerSettings}>
      <div
        className={cn(
          "flex h-full min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden bg-background",
          chatOpen ? "pr-0" : "",
        )}
        style={{
          paddingRight: NOTIFICATION_GUTTER,
          WebkitFontSmoothing: "antialiased",
        }}
      >
        <header className="flex min-w-0 w-full shrink-0 flex-wrap items-end justify-between gap-3 px-4 py-4 sm:gap-4 sm:px-6 sm:py-5 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.06)]">
          <div className="min-w-0 max-w-2xl">
            <h1 className="text-balance text-2xl font-semibold tracking-tight">
              {tx("title", "Tenders")}
            </h1>
            {!showWizard && live.profile.name ? (
              <p className="mt-1 break-words text-pretty text-sm font-medium">{live.profile.name}</p>
            ) : null}
          </div>
          <div className="flex min-w-0 w-full flex-1 items-center gap-2">
            <nav
              className="flex min-w-0 flex-1 flex-wrap items-center gap-2 sm:justify-end"
              aria-label={tx("deskPanesAria", "Desk panes")}
            >
              {ready ? (
                <>
                  {deskPane === "home" && view === "work" && !openId ? (
                    <PrimaryButton
                      text={tx("homeDashboard", "Home dashboard")}
                      iconProps={{ iconName: "Home" }}
                      onClick={openHome}
                      styles={BUTTON_STYLES}
                    />
                  ) : (
                    <DefaultButton
                      text={tx("homeDashboard", "Home dashboard")}
                      iconProps={{ iconName: "Home" }}
                      onClick={openHome}
                      styles={BUTTON_STYLES}
                    />
                  )}
                  {deskPane === "tenders" && view === "work" ? (
                    <PrimaryButton
                      text={tx("tenderBook", "Tender")}
                      iconProps={{ iconName: "PageList" }}
                      onClick={() => openBook("pipeline", "all")}
                      styles={BUTTON_STYLES}
                    />
                  ) : (
                    <DefaultButton
                      text={tx("tenderBook", "Tender")}
                      iconProps={{ iconName: "PageList" }}
                      onClick={() => openBook("pipeline", "all")}
                      styles={BUTTON_STYLES}
                    />
                  )}
                </>
              ) : null}
              <DefaultButton
                text={showWizard ? tx("wizardKicker", "Company setup") : tx("settings", "Settings")}
                iconProps={{ iconName: "Settings" }}
                onClick={() => {
                  stayOnSetup.current = true;
                  showNotice("");
                  setView("setup");
                }}
                styles={BUTTON_STYLES}
              />
              {showDesk ? (
                <>
                  {live.loop?.enabled ? (
                    <PrimaryButton
                      text={tx("pause", "Pause loop")}
                      iconProps={{ iconName: "Pause" }}
                      onClick={() => void run("stop")}
                      disabled={Boolean(busy)}
                      styles={BUTTON_STYLES}
                      data-testid="tenders-pause-loop"
                    />
                  ) : (
                    <PrimaryButton
                      text={tx("startLoop", "Start loop")}
                      iconProps={{ iconName: "Play" }}
                      onClick={() => {
                        setScheduleMode("start");
                        setScheduleOpen(true);
                      }}
                      disabled={Boolean(busy)}
                      styles={BUTTON_STYLES}
                      data-testid="tenders-start-loop"
                    />
                  )}
                  <DefaultButton
                    text={tx("cycle", "Run cycle")}
                    iconProps={{ iconName: "Sync" }}
                    onClick={() => void run("tick", { force: true })}
                    disabled={Boolean(busy)}
                    styles={BUTTON_STYLES}
                    data-testid="tenders-run-cycle"
                  />
                </>
              ) : null}
            </nav>
            <div className="flex shrink-0 items-center gap-2">
              {onToggleChat ? (
                <DefaultButton
                  text={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                  iconProps={{ iconName: "Chat" }}
                  aria-pressed={Boolean(chatOpen)}
                  onClick={onToggleChat}
                  styles={BUTTON_STYLES}
                  data-testid="tenders-chat"
                />
              ) : null}
              <IconButton
                iconProps={{ iconName: "Refresh" }}
                title={tx("refresh", "Refresh")}
                ariaLabel={tx("refresh", "Refresh")}
                disabled={Boolean(busy)}
                onClick={() => void load()}
                styles={ICON_BUTTON_STYLES}
                data-testid="tenders-refresh"
              />
            </div>
          </div>
        </header>

        <div
          data-testid="tenders-scroll"
          className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain scrollbar-thin scrollbar-track-transparent [overflow-anchor:none]"
        >
        <div className="min-w-0 overflow-x-hidden px-4 py-5 sm:px-6 sm:py-6">
          {error ? (
            <MessageBar messageBarType={MessageBarType.error} className="mb-4" onDismiss={() => setError("")}>
              {error}
            </MessageBar>
          ) : null}
          {saved ? (
            <MessageBar messageBarType={savedType} className="mb-4" onDismiss={() => setSaved("")}>
              {saved}
            </MessageBar>
          ) : null}
          {busy ? (
            <ProgressIndicator
              className="mb-4"
              label={
                busy === "collect" || busy === "tick" || busy === "start"
                  ? tx("collecting", "Reading official portals. This can take a minute.")
                  : `${tx("working", "Working")} - ${busy}`
              }
            />
          ) : null}
          {!token ? (
            <Surface className="mx-auto max-w-xl p-8">
              <h2 className="text-balance text-xl font-semibold">{tx("noTokenTitle", "Sign in")}</h2>
              <p className="mt-2 text-pretty text-sm text-muted-foreground">
                {tx("noToken", "Sign in to Navin to open this desk.")}
              </p>
            </Surface>
          ) : null}
          {token && !desk && !error ? <DeskSkeleton /> : null}
          {token && !desk && error ? (
            <Surface className="mx-auto max-w-xl p-8">
              <h2 className="text-balance text-xl font-semibold">
                {tx("offlineTitle", "The Tenders desk is not connected")}
              </h2>
              <p className="mt-2 text-pretty text-sm text-muted-foreground">{error}</p>
              <PrimaryButton
                className="mt-5"
                text={tx("retry", "Retry")}
                onClick={() => void load()}
                styles={BUTTON_STYLES}
              />
            </Surface>
          ) : null}

          <AnimatePresence mode="wait" initial={false}>
            {connected ? (
              <motion.div
                key={
                  showWizard
                    ? "setup"
                    : view === "work" && openId
                      ? `notice-${openId}`
                      : "desk"
                }
                initial={reduceMotion ? false : { opacity: 0, y: 10, filter: "blur(4px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={reduceMotion ? undefined : { opacity: 0, y: -8, filter: "blur(4px)" }}
                transition={SPRING}
                className="mx-auto flex w-full min-w-0 max-w-5xl flex-col gap-8"
              >
                {showDesk && !openId ? (
                  <TenderAlertDeliveries desk={live} busy={Boolean(busy)}
                    onRetry={() => void run("retry-alerts")}
                    onConfigure={() => { stayOnSetup.current = true; setView("setup"); }} />
                ) : null}
                {showWizard ? (
                  <TendersWizard
                    desk={live}
                    tx={tx}
                    busy={busy}
                    token={token}
                    onSave={async (body) => {
                      const ok = await run("profile", body);
                      if (ok && body.wizard_complete) setView("work");
                      return ok;
                    }}
                    onUpload={(body) => run("upload", body)}
                    onRemoveFile={(body) => run("remove-file", body)}
                    onAddReference={(body) => run("add-reference", body)}
                    onCustomSource={(body) => run("custom-source", body)}
                    onSecret={(name, value) => run("secret", { name, value })}
                    onPreviewFile={async (fileId) => {
                      if (!token || !fileId) return "";
                      try {
                        const result = await postTenders(token, "file", { id: fileId });
                        return String(result.file?.text || result.file?.excerpt || "");
                      } catch {
                        return "";
                      }
                    }}
                    onTest={() =>
                      run("notify", {
                        title: "Navin Tenders",
                        detail: i18n.language.startsWith("fr") ? "Test des canaux activés depuis la configuration Tenders." : "Test of enabled channels from Tenders configuration.",
                      })
                    }
                  />
                ) : null}
                {showDesk && !openId && deskPane === "home" ? (
                  <HomePane
                    desk={live}
                    kpis={kpis}
                    tx={tx}
                    busy={busy}
                    token={token}
                    onCollect={() => void run("collect")}
                    onOpen={(id) => showNotice(id)}
                    onSetup={() => {
                      stayOnSetup.current = true;
                      setView("setup");
                    }}
                    onFollow={() => void run("follow")}
                    onCrmSync={() => void run("crm-sync")}
                    onDiscoverAccept={(host) => void run("discover-accept", { host })}
                    onOpenBook={openBook}
                    locale={i18n.language}
                  />
                ) : null}
                {showDesk && !openId && deskPane === "tenders" ? (
                  <NoticesPane
                    desk={live}
                    tx={tx}
                    busy={busy}
                    listView={bookView}
                    picked={bookFilter}
                    token={token}
                    locale={i18n.language}
                    onListView={setBookView}
                    onPicked={setBookFilter}
                    onOpen={(id) => showNotice(id)}
                    onRowAction={(action, id) => {
                      if (action === "view") {
                        showNotice(id);
                        return;
                      }
                      if (action === "qualify" || action === "write") {
                        showNotice(id);
                        void run(action, { id });
                        return;
                      }
                      if (action === "go") {
                        void run("stage", { id, stage: "go" });
                        return;
                      }
                      if (action === "nogo") {
                        void run("stage", { id, stage: "no-go" });
                        return;
                      }
                      void run(action, { id });
                    }}
                  />
                ) : null}
                {showDesk && selected ? (
                  <DossierPane
                    desk={live}
                    selected={selected}
                    tx={tx}
                    busy={busy}
                    onBack={() => showNotice("")}
                    onQualify={(id) => void run("qualify", { id })}
                    onWrite={(id) => void run("write", { id })}
                    onRevise={(id, remarks) => void run("revise", { id, remarks })}
                    onStage={(id, stage) => void run("stage", { id, stage })}
                    onMail={(id, kind) => void run("mail", { id, kind })}
                    onSend={(id, to, kind) => void run("send", { id, to, approved: true, kind: kind || "" })}
                    onCrmPush={(id) => void run("crm-sync", { id })}
                    onSeed={(text) => onSeed?.(text)}
                    token={token}
                    locale={i18n.language}
                  />
                ) : null}
                {showDesk && openId && !selected ? (
                  <Surface className="p-8">
                    <p className="text-pretty text-sm text-muted-foreground">
                      {tx("missingNotice", "This notice is no longer in the book.")}
                    </p>
                    <DefaultButton
                      className="mt-4"
                      text={tx("backNotices", "Back to notices")}
                      onClick={() => showNotice("")}
                      styles={BUTTON_STYLES}
                    />
                  </Surface>
                ) : null}
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
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
