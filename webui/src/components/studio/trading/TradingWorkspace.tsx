import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ComboBox,
  Customizer,
  DefaultButton,
  Dropdown,
  Icon,
  IconButton,
  MessageBar,
  MessageBarType,
  PrimaryButton,
  ProgressIndicator,
  TextField,
  Toggle,
  createTheme,
  type IComboBoxOption,
  type IDropdownOption,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { TradingKpiGrid, buildTradingKpis } from "@/components/studio/trading/TradingKpis";
import { EquityChart, MiniCurve, PriceChart, QuoteBars, type PriceLevel } from "@/components/studio/trading/TradingCharts";
import { TradingExecutionPane } from "@/components/studio/trading/TradingExecutionPane";
import { TradingLoopSchedulePanel } from "@/components/studio/trading/TradingLoopSchedulePanel";
import { TradingOrderTicket } from "@/components/studio/trading/TradingOrderTicket";
import { TradingScene } from "@/components/studio/trading/TradingScene";
import type { TradingLoopSchedule } from "@/lib/trading-api";
import {
  browserTimeZone,
  describeTradingSchedule,
  formatNextDue,
  normalizeTradingSchedule,
} from "@/lib/trading-loop-schedule";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";
import {
  fetchTradingChart,
  fetchTradingDesk,
  postTrading,
  type TradingBacktest,
  type TradingChart,
  type TradingChartRange,
  type TradingDesk,
  type TradingMandate,
  type TradingOrder,
  type TradingPosition,
  type TradingResearch,
} from "@/lib/trading-api";
import {
  bookEquity,
  bookCurrency,
  formatMoney,
  formatPct,
  formatQty,
  openVenueOrders,
  orderStatusTone,
  pendingOrders,
  stopDistancePct,
} from "@/lib/trading-format";
import { cn } from "@/lib/utils";

const SPRING = { type: "spring" as const, duration: 0.3, bounce: 0 };

const DEFAULT_BRIEF =
  "Analyse chaque matin le Nasdaq 100. Croissance CA > 15 %, dette raisonnable, tendance haussiere. Stop 5 %. Jamais plus de 3 % par ligne. Fills paper sous 2000. Validation seulement au-dessus.";

const PANES = [
  { id: "home", icon: "Home" },
  { id: "mandate", icon: "Globe" },
  { id: "portfolio", icon: "Money" },
  { id: "markets", icon: "BarChart4" },
  { id: "trade", icon: "StockUp" },
  { id: "watchlist", icon: "ViewList" },
  { id: "strategies", icon: "Lightbulb" },
  { id: "agents", icon: "Group" },
  { id: "positions", icon: "StackIndicator" },
  { id: "orders", icon: "ActivateOrders" },
  { id: "research", icon: "Search" },
  { id: "journal", icon: "EditNote" },
  { id: "backtests", icon: "TestBeaker" },
  { id: "risk", icon: "Shield" },
  { id: "execution", icon: "PlugConnected" },
] as const;

const CHART_RANGES: TradingChartRange[] = ["1mo", "3mo", "6mo", "1y", "2y"];

/** Actions whose response already carries the changed slices; no full reload needed. */
const LIGHT_ACTIONS = new Set(["mandate", "settings", "activate", "broker", "secret", "connection"]);

type Pane = (typeof PANES)[number]["id"];

const BUTTON_STYLES = {
  root: { minHeight: 40, cursor: "pointer" as const },
};

function useTradingTheme(mode: "light" | "dark") {
  return useMemo(
    () =>
      createTheme({
        palette:
          mode === "dark"
            ? {
                themePrimary: "#F59E0B",
                themeLighterAlt: "#1A1408",
                themeLighter: "#3F2E12",
                themeLight: "#78530F",
                themeTertiary: "#B45309",
                themeSecondary: "#D97706",
                themeDarkAlt: "#FBBF24",
                themeDark: "#FCD34D",
                themeDarker: "#FDE68A",
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
                themePrimary: "#B45309",
                themeLighterAlt: "#FFFBEB",
                themeLighter: "#FEF3C7",
                themeLight: "#FDE68A",
                themeTertiary: "#F59E0B",
                themeSecondary: "#D97706",
                themeDarkAlt: "#B45309",
                themeDark: "#92400E",
                themeDarker: "#78350F",
                neutralLighterAlt: "#F8FAFC",
                neutralLighter: "#F1F5F9",
                neutralLight: "#E2E8F0",
                neutralQuaternaryAlt: "#CBD5E1",
                neutralQuaternary: "#94A3B8",
                neutralTertiaryAlt: "#64748B",
                neutralTertiary: "#64748B",
                neutralSecondary: "#475569",
                neutralPrimaryAlt: "#334155",
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

function scoreTone(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 70) return "text-emerald-600 dark:text-emerald-400";
  if (score <= 40) return "text-red-600 dark:text-red-400";
  return "text-amber-600 dark:text-amber-400";
}

function actionTone(action: string | undefined): string {
  if (action === "BUY") return "bg-emerald-600/15 text-emerald-700 dark:text-emerald-300";
  if (action === "SELL") return "bg-red-600/15 text-red-700 dark:text-red-300";
  if (action === "HOLD") return "bg-sky-600/15 text-sky-700 dark:text-sky-300";
  return "bg-muted text-muted-foreground";
}

export function TradingWorkspace({
  chatOpen,
  onToggleChat,
  onSeed,
}: {
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const theme = useThemeValue();
  const fluentTheme = useTradingTheme(theme);
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  const [desk, setDesk] = useState<TradingDesk | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [pane, setPane] = useState<Pane>("home");
  const [brief, setBrief] = useState("");
  const [watchInput, setWatchInput] = useState("");
  const [focusSymbol, setFocusSymbol] = useState("");
  const [tradeSymbol, setTradeSymbol] = useState("");
  const [chartRange, setChartRange] = useState<TradingChartRange>("6mo");
  const [charts, setCharts] = useState<Record<string, TradingChart>>({});
  const [chartBusy, setChartBusy] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"start" | "edit">("start");

  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, string | number>) =>
      t(`studio.trading.${key}`, { defaultValue: fallback, ...values }),
    [t],
  );

  const inFlightRef = useRef(false);
  const load = useCallback(async () => {
    if (!token || inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const next = await fetchTradingDesk(token);
      setDesk(next);
      setError("");
    } catch (err) {
      setError((err as Error).message || tx("loadError", "Could not load the trading desk."));
    } finally {
      inFlightRef.current = false;
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const fromDesk = String(desk?.strategy?.brief || "").trim();
    if (!fromDesk) return;
    setBrief((current) => current.trim() || fromDesk);
  }, [desk?.strategy?.brief]);

  useEffect(() => {
    const phase = desk?.loop.phase || "";
    const live =
      Boolean(desk?.loop.enabled) ||
      ["scan", "screen", "analyze", "debate", "risk", "execute", "journal", "busy", "hunt"].includes(
        phase,
      );
    if (!token || !live) return undefined;
    const id = window.setInterval(() => {
      void load();
    }, 8_000);
    return () => window.clearInterval(id);
  }, [token, desk?.loop.enabled, desk?.loop.phase, load]);

  const call = useCallback(
    async (action: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown> | null> => {
      if (!token) return null;
      const light = LIGHT_ACTIONS.has(action);
      if (!light) setBusy(action);
      setError("");
      try {
        const result = await postTrading(token, action, body);
        if (light) {
          setDesk((current) => {
            if (!current) return current;
            return {
              ...current,
              mandate: (result.mandate as TradingMandate | undefined) || current.mandate,
              settings: (result.settings as TradingDesk["settings"] | undefined) || current.settings,
              strategy: (result.strategy as Record<string, unknown> | undefined) || current.strategy,
              strategies: (result.strategies as Record<string, unknown>[] | undefined) || current.strategies,
              catalog: (result.catalog as TradingDesk["catalog"] | undefined) || current.catalog,
              channels: (result.channels as TradingDesk["channels"] | undefined) || current.channels,
              broker: (result.broker as TradingDesk["broker"] | undefined) || current.broker,
            };
          });
        } else {
          await load();
        }
        return result;
      } catch (err) {
        setError((err as Error).message || tx("actionFailed", "Trading action failed."));
        return null;
      } finally {
        setBusy("");
      }
    },
    [token, load, tx],
  );

  const run = useCallback(
    async (action: string, body: Record<string, unknown> = {}) => {
      const result = await call(action, body);
      return result != null;
    },
    [call],
  );

  const loadChart = useCallback(
    async (symbol: string, range: TradingChartRange) => {
      const key = `${symbol}:${range}`;
      if (!token || !symbol || charts[key]) return;
      setChartBusy(key);
      try {
        const chart = await fetchTradingChart(token, symbol, range);
        setCharts((current) => ({ ...current, [key]: chart }));
      } catch (err) {
        setError((err as Error).message || tx("chartFailed", "Could not load the chart."));
      } finally {
        setChartBusy("");
      }
    },
    [token, charts, tx],
  );

  const openTrade = useCallback((symbol: string) => {
    setTradeSymbol(symbol.toUpperCase());
    setPane("trade");
  }, []);

  const equity = useMemo(
    () => bookEquity(desk?.portfolio.cash, desk?.portfolio.positions, desk?.quotes),
    [desk],
  );
  const currency =
    desk?.mandate?.currency ||
    bookCurrency(desk?.mandate?.active_countries || desk?.mandate?.countries) ||
    desk?.portfolio.currency ||
    desk?.settings.currency ||
    "USD";
  const researchList = useMemo(() => {
    if (!desk) return [];
    return Object.values(desk.research || {}).sort((a, b) => (b.t || 0) - (a.t || 0));
  }, [desk]);
  const focused: TradingResearch | null = focusSymbol
    ? desk?.research[focusSymbol] || null
    : researchList[0] || null;
  const waiting = pendingOrders(desk?.orders);
  const working = openVenueOrders(desk?.orders);

  useEffect(() => {
    if (tradeSymbol || !desk) return;
    const first = desk.portfolio.positions?.[0]?.symbol || desk.watchlist[0] || researchList[0]?.symbol || "";
    if (first) setTradeSymbol(first);
  }, [desk, tradeSymbol, researchList]);

  useEffect(() => {
    if (pane !== "trade" || !tradeSymbol) return;
    void loadChart(tradeSymbol, chartRange);
  }, [pane, tradeSymbol, chartRange, loadChart]);

  const seedChat = () => {
    onSeed?.(
      `/trading ${brief.trim() || String(desk?.strategy?.brief || "").trim() || tx("seedFallback", "Status the paper book, then run one honest cycle. Do not invent fills.")}\n\n`,
    );
  };

  const submitSchedule = async (schedule: TradingLoopSchedule, runNow: boolean) => {
    const tz = browserTimeZone();
    const action = scheduleMode === "start" ? "start" : "schedule";
    const ok = await run(action, { schedule: { ...schedule, tz: tz || schedule.tz || null }, tz, run_now: runNow });
    if (ok) setScheduleOpen(false);
  };

  return (
    <Customizer settings={{ theme: fluentTheme }}>
      <div
        className={cn("flex h-full min-h-0 flex-col bg-background", chatOpen ? "pr-0" : "")}
        style={{
          paddingTop: NOTIFICATION_GUTTER ? 0 : undefined,
          WebkitFontSmoothing: "antialiased",
        }}
      >
        <header className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.06)]">
          <div className="flex min-w-0 items-baseline gap-2.5">
            <p className="shrink-0 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
              {tx("kicker", "Studio")}
            </p>
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {tx("title", "Trading Agent OS")}
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <DefaultButton
              text={tx("refresh", "Refresh")}
              iconProps={{ iconName: "Refresh" }}
              disabled={Boolean(busy)}
              onClick={() => void load()}
              styles={BUTTON_STYLES}
            />
            {onToggleChat ? (
              <DefaultButton
                text={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                iconProps={{ iconName: "Chat" }}
                onClick={onToggleChat}
                aria-pressed={Boolean(chatOpen)}
                styles={BUTTON_STYLES}
              />
            ) : null}
            {desk?.loop.enabled ? (
              <PrimaryButton
                text={tx("pause", "Pause loop")}
                iconProps={{ iconName: "Pause" }}
                onClick={() => void run("stop")}
                disabled={Boolean(busy)}
                styles={BUTTON_STYLES}
              />
            ) : (
              <PrimaryButton
                text={tx("start", "Start loop")}
                iconProps={{ iconName: "Play" }}
                onClick={() => {
                  setScheduleMode("start");
                  setScheduleOpen(true);
                }}
                disabled={Boolean(busy)}
                styles={BUTTON_STYLES}
              />
            )}
            <DefaultButton
              text={tx("schedule", "Schedule")}
              iconProps={{ iconName: "Clock" }}
              onClick={() => {
                setScheduleMode("edit");
                setScheduleOpen(true);
              }}
              disabled={Boolean(busy)}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("cycle", "Run cycle")}
              iconProps={{ iconName: "Sync" }}
              onClick={() => void run("tick", { force: true })}
              disabled={Boolean(busy)}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("trade", "Trade")}
              iconProps={{ iconName: "StockUp" }}
              onClick={() => openTrade(tradeSymbol || "")}
              disabled={Boolean(busy)}
              styles={BUTTON_STYLES}
              data-testid="trading-open-trade"
            />
          </div>
        </header>

        <nav
          className="shrink-0 overflow-x-auto px-2 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.08)]"
          aria-label={tx("panesAria", "Trading desk sections")}
        >
          <div className="flex min-w-max items-stretch gap-0.5 py-1">
            {PANES.map((item) => {
              const selected = pane === item.id;
              const badge =
                item.id === "orders"
                  ? waiting.length + working.length
                  : item.id === "positions"
                    ? (desk?.portfolio.positions || []).length
                    : 0;
              return (
                <motion.button
                  key={item.id}
                  type="button"
                  whileTap={reduceMotion ? undefined : { scale: 0.96 }}
                  onClick={() => setPane(item.id)}
                  data-testid={`trading-pane-${item.id}`}
                  className={cn(
                    "relative inline-flex min-h-14 min-w-[4.5rem] shrink-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-xl px-2.5 py-1.5 text-center transition-[background-color,color,box-shadow] duration-150",
                    selected
                      ? "bg-amber-500/16 font-medium text-foreground shadow-[0_6px_16px_rgba(15,23,42,0.08)]"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                >
                  <Icon iconName={item.icon} className="text-[18px] text-amber-500" aria-hidden />
                  <span className="text-[11px] leading-none tracking-wide">
                    {tx(`pane.${item.id}`, item.id === "home" ? "Home" : item.id)}
                  </span>
                  {badge ? (
                    <span className="absolute right-1 top-1 rounded-full bg-amber-500/25 px-1 text-[10px] tabular-nums leading-4">
                      {badge}
                    </span>
                  ) : null}
                </motion.button>
              );
            })}
          </div>
        </nav>

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {error ? (
            <MessageBar
              messageBarType={MessageBarType.warning}
              className="mb-3"
              actions={
                <DefaultButton text={tx("retry", "Retry")} onClick={() => void load()} styles={BUTTON_STYLES} />
              }
            >
              {error}
            </MessageBar>
          ) : null}
          {busy ? (
            <ProgressIndicator className="mb-3" label={`${tx("working", "Working")} - ${busy}`} />
          ) : null}
          {waiting.length ? (
            <button
              type="button"
              onClick={() => setPane("orders")}
              className="mb-3 flex min-h-10 w-full cursor-pointer items-center justify-between gap-3 rounded-2xl bg-amber-500/12 px-4 py-2 text-left shadow-[0_8px_24px_rgba(245,158,11,0.12)] transition-[transform,opacity] duration-150 hover:bg-amber-500/18 active:scale-[0.96]"
            >
              <span className="text-sm font-medium">
                {tx("pendingBanner", "{{count}} orders waiting for approval", { count: waiting.length })}
              </span>
              <span className="text-xs uppercase tracking-wide text-amber-700 dark:text-amber-300">
                {tx("openOrders", "Open orders")}
              </span>
            </button>
          ) : null}

          {!desk && !error ? <DeskSkeleton /> : null}

          {desk ? (
            <>
              {pane === "home" ? (
              <div className="space-y-4">
              <TradingKpiGrid
                items={buildTradingKpis({
                  equity,
                  cash: Number(desk.portfolio.cash || 0),
                  returnPct: Number(desk.metrics.return_pct),
                  drawdownPct: Number(desk.metrics.max_drawdown),
                  currency,
                  labels: {
                    equity: tx("equity", "Equity"),
                    cash: tx("cash", "Cash"),
                    return: tx("return", "Return"),
                    drawdown: tx("drawdown", "Max DD"),
                  },
                })}
              />
              <DeskStatusStrip desk={desk} tx={tx} onOpen={setPane} />
              <div className="grid gap-3 md:grid-cols-3">
                <button
                  type="button"
                  onClick={() => setPane("mandate")}
                  className="cursor-pointer rounded-2xl p-4 text-left shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 transition-[background-color] duration-150 hover:bg-muted/30 dark:outline-white/10"
                >
                  <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{tx("homeDomains", "Domains")}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {(desk.mandate?.active_tapes || []).length
                      ? Array.from(new Set((desk.mandate?.active_tapes || []).map((row) => row.domain))).map((id) => (
                          <span key={id} className="rounded-full bg-amber-500/16 px-2.5 py-1 text-xs font-medium">
                            {(desk.catalog?.domains || []).find((item) => item.id === id)?.name || id}
                          </span>
                        ))
                      : (desk.mandate?.active_domains || desk.mandate?.domains || []).map((id) => (
                          <span key={id} className="rounded-full bg-amber-500/16 px-2.5 py-1 text-xs font-medium">
                            {(desk.catalog?.domains || []).find((item) => item.id === id)?.name || id}
                          </span>
                        ))}
                  </div>
                  {desk.mandate?.domains_mode === "agent" ? (
                    <p className="mt-2 text-xs text-muted-foreground">{tx("homeAgentDomains", "Agent picks domains")}</p>
                  ) : null}
                </button>
                <button
                  type="button"
                  onClick={() => setPane("mandate")}
                  className="cursor-pointer rounded-2xl p-4 text-left shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 transition-[background-color] duration-150 hover:bg-muted/30 dark:outline-white/10"
                >
                  <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{tx("homeCountries", "Countries")}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {((desk.mandate?.active_tapes || []).length
                      ? Array.from(new Set((desk.mandate?.active_tapes || []).map((row) => row.country)))
                      : (desk.mandate?.active_countries || desk.mandate?.countries || []).slice(0, 12)
                    ).map((code) => (
                      <span key={code} className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium tabular-nums">
                        {code}
                        <span className="ml-1 font-normal text-muted-foreground">
                          {(desk.catalog?.countries || []).find((item) => item.id === code)?.name || ""}
                        </span>
                      </span>
                    ))}
                  </div>
                  {desk.mandate?.countries_mode === "agent" ? (
                    <p className="mt-2 text-xs text-muted-foreground">{tx("homeAgentCountries", "Agent walks quoted countries")}</p>
                  ) : null}
                </button>
                <button
                  type="button"
                  onClick={() => setPane("mandate")}
                  className="cursor-pointer rounded-2xl p-4 text-left shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 transition-[background-color] duration-150 hover:bg-muted/30 dark:outline-white/10"
                >
                  <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{tx("homeAlerts", "Alert tools")}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {desk.mandate?.channels?.telegram ? (
                      <span className="rounded-full bg-amber-500/16 px-2.5 py-1 text-xs font-medium">
                        {tx("channelTelegram", "Telegram")}
                        {desk.mandate.channels.telegram_to ? ` · ${desk.mandate.channels.telegram_to}` : ""}
                      </span>
                    ) : null}
                    {desk.mandate?.channels?.whatsapp ? (
                      <span className="rounded-full bg-amber-500/16 px-2.5 py-1 text-xs font-medium">
                        {tx("channelWhatsapp", "WhatsApp")}
                      </span>
                    ) : null}
                    {desk.mandate?.channels?.email ? (
                      <span className="rounded-full bg-amber-500/16 px-2.5 py-1 text-xs font-medium">
                        {tx("channelEmail", "Email")}
                        {desk.mandate.channels.email_to ? ` · ${desk.mandate.channels.email_to}` : ""}
                      </span>
                    ) : null}
                    {!desk.mandate?.channels?.telegram && !desk.mandate?.channels?.whatsapp && !desk.mandate?.channels?.email ? (
                      <span className="text-sm text-muted-foreground">{tx("homeNoAlerts", "No alert channel on the mandate yet.")}</span>
                    ) : null}
                  </div>
                </button>
              </div>
              {(desk.mandate?.active_tapes || []).length ? (
                <div className="flex flex-wrap gap-1.5">
                  {(desk.mandate?.active_tapes || []).slice(0, 16).map((tape) => (
                    <button
                      key={`${tape.domain}:${tape.country}`}
                      type="button"
                      onClick={() => setPane("mandate")}
                      className="cursor-pointer rounded-xl bg-muted/60 px-3 py-1.5 text-xs font-medium"
                    >
                      {(desk.catalog?.domains || []).find((item) => item.id === tape.domain)?.name || tape.domain}
                      {" · "}
                      <span className="tabular-nums">{tape.country}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
                <div className="min-w-0 space-y-4">
                  <Surface className="p-2">
                    <EquityChart
                      points={desk.portfolio.equity_curve}
                      label={tx("equityCurve", "Equity curve")}
                      empty={tx("noCurve", "No marked equity yet.")}
                      up={Number(desk.metrics.return_pct) >= 0}
                      currency={currency}
                    />
                  </Surface>
                  <Surface className="p-4">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{tx("homeTape", "Tape moves")}</p>
                    <div className="mt-3">
                      <QuoteBars
                        rows={Object.values(desk.quotes)}
                        empty={tx("noQuotes", "No quotes yet. Start the loop or add a watchlist name.")}
                      />
                    </div>
                  </Surface>
                  <AnimatePresence initial={false}>
                    <motion.div
                      key={desk.loop.phase || "idle"}
                      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={SPRING}
                    >
                      <Surface className="p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="text-xs uppercase tracking-wide text-muted-foreground">
                              {tx("loop", "Loop")}
                            </p>
                            <p className="text-sm font-medium tabular-nums">
                              {desk.loop.enabled ? tx("running", "Running") : tx("paused", "Paused")}
                              {" · "}
                              {desk.loop.phase || tx("idle", "idle")}
                              {" · "}
                              {tx("cycleN", "cycle {{n}}", { n: desk.loop.cycle || 0 })}
                            </p>
                            <p className="mt-1 text-pretty text-sm text-muted-foreground">
                              {describeTradingSchedule(
                                normalizeTradingSchedule(desk.loop.schedule),
                                tx,
                                i18n.language,
                              )}
                              {desk.loop.enabled && formatNextDue(desk.loop.next_due, i18n.language)
                                ? ` · ${tx("scheduleNext", "Next run {{time}}", {
                                    time: formatNextDue(desk.loop.next_due, i18n.language),
                                  })}`
                                : ""}
                            </p>
                          </div>
                          <span className="inline-flex min-h-8 items-center gap-1.5 rounded-full bg-muted px-2.5 text-xs">
                            <Icon iconName="Shield" className="text-[12px]" aria-hidden />
                            {String(desk.strategy.execution_mode || desk.settings.execution_mode || "autonomous")}
                          </span>
                        </div>
                        <p className="mt-2 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
                          {desk.loop.last_result || tx("loopHint", "Start the loop to scan, debate, and journal real work.")}
                        </p>
                      </Surface>
                    </motion.div>
                  </AnimatePresence>
                  <TextField
                    multiline
                    rows={4}
                    label={tx("mandate", "Program the agent")}
                    placeholder={tx("mandatePlaceholder", DEFAULT_BRIEF)}
                    value={brief}
                    onChange={(_, value) => setBrief(value || "")}
                  />
                  <div className="flex flex-wrap gap-2">
                    <PrimaryButton
                      text={tx("compile", "Compile mandate")}
                      iconProps={{ iconName: "Code" }}
                      disabled={Boolean(busy)}
                      onClick={() =>
                        void run("strategy", {
                          brief: brief.trim() || String(desk.strategy.brief || "").trim() || DEFAULT_BRIEF,
                        })
                      }
                      styles={BUTTON_STYLES}
                    />
                    <DefaultButton
                      text={tx("seed", "Send to chat")}
                      iconProps={{ iconName: "Chat" }}
                      onClick={seedChat}
                      disabled={!onSeed}
                      styles={BUTTON_STYLES}
                    />
                  </div>
                </div>
                <div>
                  <TradingScene
                    returnPct={Number(desk.metrics.return_pct || 0)}
                    active
                    label={tx("sceneAria", "Portfolio risk core")}
                  />
                  {focused ? (
                    <motion.button
                      type="button"
                      whileTap={reduceMotion ? undefined : { scale: 0.96 }}
                      onClick={() => {
                        setFocusSymbol(focused.symbol);
                        setPane("research");
                      }}
                      className="mt-3 w-full cursor-pointer rounded-2xl p-2 text-left shadow-[0_10px_28px_rgba(15,23,42,0.08)] outline outline-1 outline-black/10 transition-[background-color] duration-150 hover:bg-muted/30 dark:outline-white/10"
                    >
                      <div className="rounded-[14px] p-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-medium tabular-nums">
                            {focused.symbol} · {tx("decision", "Decision")}: {focused.action || "WAIT"}
                          </p>
                          <span className={cn("rounded-full px-2 py-0.5 text-xs tabular-nums", actionTone(focused.action))}>
                            {formatPct(focused.confidence)}
                          </span>
                        </div>
                        <p className="mt-2 text-xs tabular-nums text-muted-foreground">
                          {tx("scoreFundamental", "Fundamental")} {Math.round(focused.scores?.fundamental ?? 0)}/100
                          {" · "}
                          {tx("scoreTechnical", "Technical")} {Math.round(focused.scores?.technical ?? 0)}/100
                          {" · "}
                          {tx("scoreSentiment", "Sentiment")} {Math.round(focused.scores?.sentiment ?? 0)}/100
                          {" · "}
                          {tx("scoreMacro", "Macro")} {Math.round(focused.scores?.macro ?? 0)}/100
                          {" · "}
                          {tx("scoreRisk", "Risk")} {Math.round(focused.scores?.risk ?? 0)}/100
                        </p>
                        <p className="mt-2 text-pretty text-sm" style={{ textWrap: "pretty" }}>
                          {focused.thesis}
                        </p>
                      </div>
                    </motion.button>
                  ) : (
                    <Empty text={tx("noResearch", "No research yet. Start the loop or run a cycle.")} />
                  )}
                </div>
              </div>
              </div>
              ) : null}

              <div className={pane === "home" ? "hidden" : "mt-0"}>
                {pane === "mandate" ? (
                  <MandatePane
                    desk={desk}
                    busy={Boolean(busy)}
                    tx={tx}
                    onSave={(mandate) => void run("mandate", { ...mandate })}
                  />
                ) : null}
                {pane === "portfolio" ? <PortfolioPane desk={desk} equity={equity} currency={currency} tx={tx} /> : null}
                {pane === "markets" ? (
                  <MarketsPane
                    desk={desk}
                    busy={Boolean(busy)}
                    tx={tx}
                    onResearch={(symbol) => {
                      setFocusSymbol(symbol);
                      setPane("research");
                      void run("research", { symbol });
                    }}
                    onTrade={openTrade}
                  />
                ) : null}
                {pane === "trade" ? (
                  <TradePane
                    desk={desk}
                    symbol={tradeSymbol}
                    range={chartRange}
                    chart={charts[`${tradeSymbol}:${chartRange}`] || null}
                    chartBusy={chartBusy === `${tradeSymbol}:${chartRange}`}
                    currency={currency}
                    busy={Boolean(busy)}
                    tx={tx}
                    onSymbol={(symbol) => setTradeSymbol(symbol.toUpperCase())}
                    onRange={setChartRange}
                    onOrder={(body) => run("order", body)}
                    onResearch={(symbol) => {
                      setFocusSymbol(symbol);
                      setPane("research");
                      void run("research", { symbol });
                    }}
                  />
                ) : null}
                {pane === "watchlist" ? (
                  <WatchlistPane
                    desk={desk}
                    input={watchInput}
                    tx={tx}
                    onInput={setWatchInput}
                    busy={Boolean(busy)}
                    onAdd={() => {
                      const symbol = watchInput.trim().toUpperCase();
                      if (!symbol) return;
                      void run("watchlist", { symbols: [...desk.watchlist, symbol] });
                      setWatchInput("");
                    }}
                    onRemove={(symbol) =>
                      void run("watchlist", { symbols: desk.watchlist.filter((item) => item !== symbol) })
                    }
                    onOpen={(symbol) => {
                      setFocusSymbol(symbol);
                      setPane("research");
                      void run("research", { symbol });
                    }}
                  />
                ) : null}
                {pane === "strategies" ? (
                  <StrategiesPane
                    desk={desk}
                    busy={Boolean(busy)}
                    tx={tx}
                    onActivate={(id) => void run("activate", { id })}
                    onUseSkill={(skillId) =>
                      onSeed?.(
                        `/trading Use the ${skillId} skill on the current paper mandate. Do not invent prices.\n\n`,
                      )
                    }
                  />
                ) : null}
                {pane === "agents" ? (
                  focused ? (
                    <AgentsPane research={focused} tx={tx} />
                  ) : (
                    <Empty text={tx("agentsEmpty", "Run a cycle to populate the specialists.")} />
                  )
                ) : null}
                {pane === "positions" ? (
                  <PositionsPane
                    desk={desk}
                    currency={currency}
                    busy={Boolean(busy)}
                    tx={tx}
                    onClose={(symbol, qty) => run("close", qty ? { symbol, qty } : { symbol })}
                    onStop={(symbol, stop, take, clearTake) => run("set_stop", { symbol, stop, take, clear_take: clearTake })}
                    onTrade={openTrade}
                  />
                ) : null}
                {pane === "orders" ? (
                  <OrdersPane
                    desk={desk}
                    busy={Boolean(busy)}
                    currency={currency}
                    tx={tx}
                    onApprove={(id) => void run("approve", { id })}
                    onReject={(id) => void run("reject", { id })}
                    onReconcile={() => void run("guard", { force: true })}
                  />
                ) : null}
                {pane === "research" ? (
                  <ResearchPane
                    rows={researchList}
                    selected={focusSymbol}
                    busy={Boolean(busy)}
                    tx={tx}
                    onSelect={setFocusSymbol}
                    onRefresh={(symbol) => void run("research", { symbol })}
                    onTrade={openTrade}
                    onBacktest={(symbol) => {
                      void (async () => {
                        await run("backtest", { symbol });
                        setPane("backtests");
                      })();
                    }}
                  />
                ) : null}
                {pane === "journal" ? <JournalPane desk={desk} tx={tx} /> : null}
                {pane === "backtests" ? (
                  <BacktestsPane
                    desk={desk}
                    busy={Boolean(busy)}
                    currency={currency}
                    defaultSymbol={focusSymbol || tradeSymbol}
                    tx={tx}
                    onRun={(body) => run("backtest", body)}
                  />
                ) : null}
                {pane === "risk" ? (
                  <RiskPane
                    desk={desk}
                    currency={currency}
                    busy={Boolean(busy)}
                    tx={tx}
                    onSave={(payload) => run("settings", payload)}
                  />
                ) : null}
                {pane === "execution" ? <TradingExecutionPane desk={desk} busy={Boolean(busy)} tx={tx} call={call} /> : null}
              </div>
            </>
          ) : null}
        </div>
        <TradingLoopSchedulePanel
          key={`${scheduleMode}-${scheduleOpen ? "open" : "shut"}`}
          open={scheduleOpen}
          mode={scheduleMode}
          schedule={desk?.loop.schedule}
          nextDue={desk?.loop.next_due}
          enabled={Boolean(desk?.loop.enabled)}
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

function Surface({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-2xl shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10",
        className,
      )}
    >
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="mt-3 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>{text}</p>;
}

function matchesFilter(query: string, ...parts: Array<string | number | null | undefined>): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return parts.map((part) => String(part || "")).join(" ").toLowerCase().includes(needle);
}

function PaneFilter({ value, onChange, tx }: { value: string; onChange: (value: string) => void; tx: Tx }) {
  return (
    <div className="max-w-sm" data-testid="trading-pane-filter">
      <TextField
        label={tx("paneFilter", "Filter this list")}
        value={value}
        onChange={(_, next) => onChange(next || "")}
      />
    </div>
  );
}

function DeskSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]" aria-hidden>
      <div className="space-y-3">
        <div className="grid min-w-0 grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="min-w-0">
              <div className="h-16 animate-pulse rounded-2xl bg-muted/50" />
            </div>
          ))}
        </div>
        <div className="h-24 animate-pulse rounded-2xl bg-muted/40" />
        <div className="h-32 animate-pulse rounded-2xl bg-muted/40" />
      </div>
      <div className="h-44 animate-pulse rounded-2xl bg-muted/40" />
    </div>
  );
}

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

function relativeTime(ts: number | undefined, tx: Tx): string {
  if (!ts) return tx("guardNever", "never");
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 90) return tx("guardSecondsAgo", "{{n}} s ago", { n: seconds });
  if (seconds < 5400) return tx("guardMinutesAgo", "{{n}} min ago", { n: Math.round(seconds / 60) });
  return tx("guardHoursAgo", "{{n}} h ago", { n: Math.round(seconds / 3600) });
}

function DeskStatusStrip({ desk, tx, onOpen }: { desk: TradingDesk; tx: Tx; onOpen: (pane: Pane) => void }) {
  const broker = desk.broker;
  const bench = desk.metrics.benchmark;
  const chips: { id: Pane; label: string; value: string; tone: "ok" | "warn" | "muted" }[] = [
    {
      id: "execution",
      label: tx("stripVenue", "Venue"),
      value: broker
        ? `${broker.broker}${broker.broker !== "paper" ? (broker.sandbox ? ` · ${tx("stripSandbox", "sandbox")}` : ` · ${tx("stripLive", "live money")}`) : ""}`
        : "paper",
      tone: broker?.broker !== "paper" && !broker?.sandbox ? "warn" : "ok",
    },
    {
      id: "execution",
      label: tx("stripGuard", "Guard"),
      value: desk.guard?.last_guard ? relativeTime(desk.guard.last_guard, tx) : tx("guardNever", "never"),
      tone: desk.guard?.last_guard ? "ok" : "muted",
    },
    {
      id: "execution",
      label: tx("stripModel", "Model"),
      value: desk.settings.ai_assist === false ? tx("aiOff", "Off") : desk.ai?.routed ? tx("stripRouted", "{{n}} tasks routed", { n: desk.ai.routed }) : tx("aiUnrouted", "keywords only"),
      tone: desk.settings.ai_assist !== false && desk.ai?.routed ? "ok" : "muted",
    },
    {
      id: "portfolio",
      label: tx("stripBenchmark", "vs {{symbol}}", { symbol: String(desk.settings.benchmark || "QQQ") }),
      value:
        bench && bench.alpha != null
          ? `${tx("alpha", "alpha")} ${Number(bench.alpha) >= 0 ? "+" : ""}${Number(bench.alpha).toFixed(1)} pt · ${tx("beta", "beta")} ${bench.beta == null ? "-" : Number(bench.beta).toFixed(2)}`
          : tx("stripNoBenchmark", "needs a few marked days"),
      tone: bench && bench.alpha != null ? (Number(bench.alpha) >= 0 ? "ok" : "warn") : "muted",
    },
  ];
  return (
    <div className="flex flex-wrap gap-2" data-testid="trading-status-strip">
      {chips.map((chip, index) => (
        <button
          key={`${chip.id}-${index}`}
          type="button"
          onClick={() => onOpen(chip.id)}
          className={cn(
            "inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-full px-3 text-xs transition-[background-color] duration-150 hover:bg-muted",
            chip.tone === "ok" ? "bg-emerald-600/10" : chip.tone === "warn" ? "bg-amber-500/16" : "bg-muted/60",
          )}
        >
          <span className="uppercase tracking-wide text-muted-foreground">{chip.label}</span>
          <span className="font-medium tabular-nums">{chip.value}</span>
        </button>
      ))}
    </div>
  );
}

function TradePane({
  desk,
  symbol,
  range,
  chart,
  chartBusy,
  currency,
  busy,
  tx,
  onSymbol,
  onRange,
  onOrder,
  onResearch,
}: {
  desk: TradingDesk;
  symbol: string;
  range: TradingChartRange;
  chart: TradingChart | null;
  chartBusy: boolean;
  currency: string;
  busy: boolean;
  tx: Tx;
  onSymbol: (symbol: string) => void;
  onRange: (range: TradingChartRange) => void;
  onOrder: (body: Record<string, unknown>) => Promise<boolean>;
  onResearch: (symbol: string) => void;
}) {
  const positions = desk.portfolio.positions || [];
  const held = positions.find((row) => row.symbol === symbol);
  const research = desk.research[symbol];
  const quick = Array.from(new Set([...positions.map((row) => row.symbol), ...desk.watchlist, ...Object.keys(desk.quotes)])).slice(0, 14);
  const levels: PriceLevel[] = [];
  if (held) {
    levels.push({ price: Number(held.avg), label: tx("avg", "Avg"), tone: "avg" });
    if (held.stop) levels.push({ price: Number(held.stop), label: tx("stop", "Stop"), tone: "stop" });
    if (held.take) levels.push({ price: Number(held.take), label: tx("take", "Take"), tone: "take" });
  }
  if (chart?.technical?.support) levels.push({ price: Number(chart.technical.support), label: tx("support", "Support"), tone: "support" });
  if (chart?.technical?.resistance) levels.push({ price: Number(chart.technical.resistance), label: tx("resistance", "Resistance"), tone: "resistance" });
  for (const level of desk.settings.alerts?.price_levels || []) {
    if (level.symbol === symbol) levels.push({ price: Number(level.price), label: tx("alertLevel", "Alert"), tone: "alert" });
  }
  const chartCcy = chart?.currency || desk.quotes[symbol]?.currency || currency;
  const tech = chart?.technical;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" data-testid="trading-trade-quick">
        {quick.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => onSymbol(item)}
            className={cn(
              "min-h-8 cursor-pointer rounded-full px-3 text-xs font-medium tabular-nums transition-[background-color] duration-150",
              item === symbol ? "bg-amber-500/20 text-foreground" : "bg-muted/60 text-muted-foreground hover:bg-muted",
            )}
          >
            {item}
            {positions.some((row) => row.symbol === item) ? <span className="ml-1 text-amber-600">●</span> : null}
          </button>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
        <Surface className="p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <div className="min-w-0">
              <p className="truncate text-base font-semibold tabular-nums">
                {symbol || tx("tradePick", "Pick a symbol")}
                {chart?.name && chart.name !== symbol ? <span className="ml-2 text-sm font-normal text-muted-foreground">{chart.name}</span> : null}
              </p>
              {tech ? (
                <p className="text-xs tabular-nums text-muted-foreground">
                  {tech.uptrend ? tx("uptrend", "Uptrend") : tx("noUptrend", "No uptrend")}
                  {tech.sma20 != null ? ` · SMA20 ${Number(tech.sma20).toFixed(2)}` : ""}
                  {tech.sma50 != null ? ` · SMA50 ${Number(tech.sma50).toFixed(2)}` : ""}
                  {tech.sma200 != null ? ` · SMA200 ${Number(tech.sma200).toFixed(2)}` : ""}
                  {tech.macd_hist != null ? ` · MACD ${Number(tech.macd_hist) >= 0 ? "+" : ""}${Number(tech.macd_hist).toFixed(2)}` : ""}
                </p>
              ) : null}
            </div>
            <div className="inline-flex rounded-lg bg-muted/60 p-0.5 text-xs" role="tablist" aria-label={tx("chartRange", "Chart range")}>
              {CHART_RANGES.map((item) => (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  aria-selected={range === item}
                  onClick={() => onRange(item)}
                  className={cn(
                    "min-h-7 cursor-pointer rounded-md px-2 font-medium uppercase transition-[background-color] duration-150",
                    range === item ? "bg-background shadow-sm" : "text-muted-foreground",
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
          <div className="mt-2">
            {chartBusy && !chart ? (
              <div className="h-64 animate-pulse rounded-xl bg-muted/40" aria-hidden />
            ) : chart ? (
              <PriceChart
                bars={chart.bars}
                technical={chart.technical}
                currency={chartCcy}
                levels={levels}
                labels={{
                  empty: tx("chartEmpty", "No bars for this symbol."),
                  volume: tx("volume", "Vol"),
                  sma20: "SMA20",
                  sma50: "SMA50",
                  rsi: "RSI",
                  atr: "ATR",
                }}
              />
            ) : (
              <Empty text={symbol ? tx("chartEmpty", "No bars for this symbol.") : tx("tradePick", "Pick a symbol")} />
            )}
          </div>
          {research ? (
            <button
              type="button"
              onClick={() => onResearch(symbol)}
              className="mt-3 flex w-full cursor-pointer items-start justify-between gap-3 rounded-xl bg-muted/40 px-3 py-2 text-left transition-[background-color] duration-150 hover:bg-muted"
            >
              <span className="min-w-0">
                <span className={cn("rounded-full px-2 py-0.5 text-xs", actionTone(research.action))}>{research.action || "WAIT"}</span>
                <span className="ml-2 text-xs tabular-nums text-muted-foreground">{formatPct(research.confidence)}</span>
                <p className="mt-1 line-clamp-2 text-pretty text-sm" style={{ textWrap: "pretty" }}>
                  {research.note || research.thesis}
                </p>
              </span>
              <Icon iconName="ChevronRight" className="mt-1 shrink-0 text-muted-foreground" aria-hidden />
            </button>
          ) : symbol ? (
            <DefaultButton
              className="mt-3"
              text={tx("researchAction", "Research")}
              iconProps={{ iconName: "Search" }}
              disabled={busy}
              onClick={() => onResearch(symbol)}
              styles={BUTTON_STYLES}
            />
          ) : null}
        </Surface>
        <Surface className="p-4">
          <p className="text-xs uppercase text-muted-foreground">{tx("ticketTitle", "Order ticket")}</p>
          <div className="mt-2">
            <TradingOrderTicket
              symbol={symbol}
              quotes={desk.quotes}
              positions={positions}
              bookCurrency={currency}
              fx={desk.fx}
              busy={busy}
              tx={tx}
              onSubmit={onOrder}
              onSymbolChange={onSymbol}
              compact
            />
          </div>
          {held ? (
            <div className="mt-4 rounded-xl bg-muted/40 px-3 py-2 text-sm tabular-nums">
              <p className="text-xs uppercase text-muted-foreground">{tx("heldLine", "Held")}</p>
              <p className="mt-1">
                {formatQty(held.qty)} @ {formatMoney(held.avg, held.currency || currency)}
                {" · "}
                {tx("pnl", "P&L")} {formatMoney(held.unrealized, currency)}
              </p>
              <p className="text-xs text-muted-foreground">
                {tx("stop", "Stop")} {held.stop ? Number(held.stop).toFixed(2) : "-"}
                {" · "}
                {tx("take", "Take")} {held.take ? Number(held.take).toFixed(2) : "-"}
                {held.broker ? ` · ${held.broker}` : ""}
              </p>
            </div>
          ) : null}
        </Surface>
      </div>
    </div>
  );
}

type MandateTape = { domain: string; country: string };
type AlertId = "telegram" | "whatsapp" | "email";

function tapeKey(tape: MandateTape) {
  return `${tape.domain}:${tape.country}`;
}

function tapesFromMandate(source?: TradingMandate | null): MandateTape[] {
  if (source?.tapes?.length) return source.tapes.map((row) => ({ domain: row.domain, country: row.country }));
  const domains = source?.domains?.length ? source.domains : ["equities"];
  const countries = source?.countries?.length ? source.countries : ["US"];
  const out: MandateTape[] = [];
  for (const domain of domains) {
    for (const country of countries) {
      out.push({ domain, country });
    }
  }
  return out;
}

function listsFromTapes(tapes: MandateTape[]) {
  const domains: string[] = [];
  const countries: string[] = [];
  for (const tape of tapes) {
    if (!domains.includes(tape.domain)) domains.push(tape.domain);
    if (!countries.includes(tape.country)) countries.push(tape.country);
  }
  return {
    domains: domains.length ? domains : ["equities"],
    countries: countries.length ? countries : ["US"],
  };
}

function MandatePane({
  desk,
  busy,
  tx,
  onSave,
}: {
  desk: TradingDesk;
  busy: boolean;
  tx: Tx;
  onSave: (mandate: TradingMandate) => void;
}) {
  const source = desk.mandate || desk.settings.mandate;
  const sourceKey = JSON.stringify(source || {});
  const catalog = desk.catalog || { domains: [], countries: [] };
  const [tapes, setTapes] = useState<MandateTape[]>(() => tapesFromMandate(source));
  const [draftDomain, setDraftDomain] = useState(source?.domains?.[0] || "equities");
  const [draftCountry, setDraftCountry] = useState(source?.countries?.[0] || "US");
  const [domainsMode, setDomainsMode] = useState<"user" | "agent">(source?.domains_mode || "user");
  const [countriesMode, setCountriesMode] = useState<"user" | "agent">(source?.countries_mode || "user");
  const [riskMode, setRiskMode] = useState<"user" | "agent">(source?.risk_mode || "user");
  const [targetMode, setTargetMode] = useState<"user" | "agent">(source?.target_mode || "user");
  const [target, setTarget] = useState(String(source?.target_return_pct ?? 12));
  const [telegram, setTelegram] = useState(Boolean(source?.channels?.telegram));
  const [whatsapp, setWhatsapp] = useState(Boolean(source?.channels?.whatsapp));
  const [email, setEmail] = useState(Boolean(source?.channels?.email));
  const [telegramTo, setTelegramTo] = useState(source?.channels?.telegram_to || "");
  const [whatsappTo, setWhatsappTo] = useState(source?.channels?.whatsapp_to || "");
  const [emailTo, setEmailTo] = useState(source?.channels?.email_to || "");
  const [savedFlash, setSavedFlash] = useState(false);
  const [addedKey, setAddedKey] = useState("");
  const [addedAlert, setAddedAlert] = useState("");
  const addedRef = useRef<HTMLLIElement | null>(null);
  const addedAlertRef = useRef<HTMLLIElement | null>(null);

  useEffect(() => {
    if (!source) return;
    setTapes(tapesFromMandate(source));
    setDraftDomain(source.domains?.[0] || "equities");
    setDraftCountry(source.countries?.[0] || "US");
    setDomainsMode(source.domains_mode || "user");
    setCountriesMode(source.countries_mode || "user");
    setRiskMode(source.risk_mode || "user");
    setTargetMode(source.target_mode || "user");
    setTarget(String(source.target_return_pct ?? 12));
    setTelegram(Boolean(source.channels?.telegram));
    setWhatsapp(Boolean(source.channels?.whatsapp));
    setEmail(Boolean(source.channels?.email));
    setTelegramTo(source.channels?.telegram_to || "");
    setWhatsappTo(source.channels?.whatsapp_to || "");
    setEmailTo(source.channels?.email_to || "");
  }, [source, sourceKey]);

  useEffect(() => {
    if (!savedFlash) return undefined;
    const id = window.setTimeout(() => setSavedFlash(false), 3500);
    return () => window.clearTimeout(id);
  }, [savedFlash]);

  useEffect(() => {
    if (!addedKey) return undefined;
    addedRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const id = window.setTimeout(() => setAddedKey(""), 2400);
    return () => window.clearTimeout(id);
  }, [addedKey]);

  useEffect(() => {
    if (!addedAlert) return undefined;
    addedAlertRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const id = window.setTimeout(() => setAddedAlert(""), 2400);
    return () => window.clearTimeout(id);
  }, [addedAlert]);

  const persist = (overrides: Partial<TradingMandate> = {}) => {
    const nextTapes = overrides.tapes ?? tapes;
    const lists = listsFromTapes(nextTapes);
    const next: TradingMandate = {
      domains: lists.domains,
      domains_mode: overrides.domains_mode ?? domainsMode,
      countries: lists.countries,
      countries_mode: overrides.countries_mode ?? countriesMode,
      tapes: nextTapes,
      risk_mode: overrides.risk_mode ?? riskMode,
      target_mode: overrides.target_mode ?? targetMode,
      target_return_pct: overrides.target_return_pct ?? (Number(target) || 12),
      channels: overrides.channels ?? {
        telegram,
        whatsapp,
        email,
        telegram_to: telegramTo,
        whatsapp_to: whatsappTo,
        email_to: emailTo,
      },
    };
    setSavedFlash(true);
    onSave(next);
  };

  const addTape = (domain: string, country: string) => {
    const nextTape = { domain, country };
    const key = tapeKey(nextTape);
    const exists = tapes.some((row) => tapeKey(row) === key);
    const next = exists ? tapes : [...tapes, nextTape];
    setTapes(next);
    setAddedKey(key);
    persist({ tapes: next });
  };

  const removeTape = (key: string) => {
    if (tapes.length <= 1) return;
    const next = tapes.filter((row) => tapeKey(row) !== key);
    setTapes(next);
    persist({ tapes: next });
  };

  const channelState = (patch: Partial<NonNullable<TradingMandate["channels"]>>) => ({
    telegram,
    whatsapp,
    email,
    telegram_to: telegramTo,
    whatsapp_to: whatsappTo,
    email_to: emailTo,
    ...patch,
  });

  const addAlert = (id: AlertId) => {
    if (id === "telegram") setTelegram(true);
    if (id === "whatsapp") setWhatsapp(true);
    if (id === "email") setEmail(true);
    setAddedAlert(id);
    persist({ channels: channelState({ [id]: true }) });
  };

  const removeAlert = (id: AlertId) => {
    if (id === "telegram") setTelegram(false);
    if (id === "whatsapp") setWhatsapp(false);
    if (id === "email") setEmail(false);
    persist({ channels: channelState({ [id]: false }) });
  };

  const countryRows = catalog.countries || [];
  const domainOptions: IDropdownOption[] = (catalog.domains || []).map((item) => ({
    key: item.id,
    text: item.name,
  }));
  const countryOptions: IComboBoxOption[] = countryRows.map((item) => ({
    key: item.id,
    text: `${item.id}  ${item.name}`,
  }));
  const domainName = (id: string) => (catalog.domains || []).find((item) => item.id === id)?.name || id;
  const countryName = (id: string) => countryRows.find((item) => item.id === id)?.name || id;
  const locked = domainsMode === "agent" || countriesMode === "agent";
  const channelHint = (id: string) => desk.channels?.[id]?.hint || "";
  const channelReady = (id: string) => Boolean(desk.channels?.[id]?.ready);
  const lastAdded = tapes.find((row) => tapeKey(row) === addedKey);
  const alertCatalog: {
    id: AlertId;
    name: string;
    destLabel: string;
    destValue: string;
    setDest: (value: string) => void;
    destKey: "telegram_to" | "whatsapp_to" | "email_to";
    placeholder: string;
    on: boolean;
  }[] = [
    {
      id: "telegram",
      name: tx("channelTelegram", "Telegram"),
      destLabel: tx("telegramTo", "Telegram chat id"),
      destValue: telegramTo,
      setDest: setTelegramTo,
      destKey: "telegram_to",
      placeholder: "123456789",
      on: telegram,
    },
    {
      id: "whatsapp",
      name: tx("channelWhatsapp", "WhatsApp"),
      destLabel: tx("whatsappTo", "WhatsApp destination"),
      destValue: whatsappTo,
      setDest: setWhatsappTo,
      destKey: "whatsapp_to",
      placeholder: "+33612345678",
      on: whatsapp,
    },
    {
      id: "email",
      name: tx("channelEmail", "Email"),
      destLabel: tx("emailTo", "Email address"),
      destValue: emailTo,
      setDest: setEmailTo,
      destKey: "email_to",
      placeholder: "you@example.com",
      on: email,
    },
  ];
  const alertOptions = alertCatalog.filter((item) => !item.on);
  const alertConfigs = alertCatalog.filter((item) => item.on);

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {savedFlash ? (
        <div className="lg:col-span-2">
          <MessageBar messageBarType={MessageBarType.success}>
            {lastAdded
              ? tx(
                  "tapeAdded",
                  "Added {{country}} · {{domain}} to the mandate. It is in the list on the right.",
                  { country: countryName(lastAdded.country), domain: domainName(lastAdded.domain) },
                )
              : addedAlert
                ? tx("alertAdded", "Alert added. It is in Configs on the right.")
                : tx("mandateSaved", "Mandate saved. The next cycle walks this tape.")}
          </MessageBar>
        </div>
      ) : null}

      <Surface className="p-4">
        <Dropdown
          label={tx("mandateDomains", "Domains")}
          selectedKey={draftDomain}
          disabled={locked || busy}
          options={domainOptions}
          onChange={(_, option?: IDropdownOption) => {
            if (!option) return;
            setDraftDomain(String(option.key));
          }}
        />
        <ComboBox
          className="mt-3"
          label={tx("mandateCountries", "Countries")}
          selectedKey={draftCountry}
          disabled={locked || busy}
          allowFreeform={false}
          autoComplete="on"
          options={countryOptions}
          useComboBoxAsMenuWidth
          calloutProps={{ calloutMaxHeight: 280 }}
          onChange={(_, option?: IComboBoxOption) => {
            if (!option) return;
            setDraftCountry(String(option.key));
          }}
        />
        <PrimaryButton
          className="mt-3"
          text={tx("addTape", "Add this config")}
          disabled={locked || busy}
          onClick={() => addTape(draftDomain, draftCountry)}
          styles={BUTTON_STYLES}
        />
        <Toggle
          className="mt-4"
          checked={domainsMode === "agent"}
          onChange={(_, checked) => {
            const mode = checked ? "agent" : "user";
            setDomainsMode(mode);
            persist({ domains_mode: mode });
          }}
          label={tx("domainsAgent", "Let the agent pick domains")}
        />
        <Toggle
          className="mt-2"
          checked={countriesMode === "agent"}
          onChange={(_, checked) => {
            const mode = checked ? "agent" : "user";
            setCountriesMode(mode);
            persist({ countries_mode: mode });
          }}
          label={tx("countriesAgent", "Let the agent pick countries")}
        />
      </Surface>

      <Surface className="p-4">
        <p className="text-xs uppercase text-muted-foreground">{tx("mandateConfigs", "Configs")}</p>
        <p className="mt-1 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
          {locked
            ? tx(
                "mandateConfigsAgent",
                "Agent mode walks every quoted tape. Your saved configs stay here as the starting list.",
              )
            : tx(
                "mandateConfigsHint",
                "Each row is one domain in one country. New picks land at the bottom of this list.",
              )}
        </p>
        <ul className="mt-3 max-h-[22rem] space-y-2 overflow-y-auto pr-1">
          {tapes.map((tape) => {
            const key = tapeKey(tape);
            const fresh = key === addedKey;
            return (
              <motion.li
                key={key}
                ref={fresh ? addedRef : undefined}
                layout
                className={cn(
                  "flex items-center justify-between gap-2 rounded-xl border px-3 py-2 text-sm",
                  fresh
                    ? "border-amber-400/50 bg-amber-500/18"
                    : "border-black/10 bg-muted/40 dark:border-white/10",
                )}
              >
                <div className="min-w-0">
                  <p className="font-medium">
                    {domainName(tape.domain)}
                    {" · "}
                    <span className="tabular-nums">{tape.country}</span>
                  </p>
                  <p className="truncate text-xs text-muted-foreground">{countryName(tape.country)}</p>
                </div>
                <IconButton
                  iconProps={{ iconName: "Cancel" }}
                  title={tx("removeTape", "Remove this config")}
                  ariaLabel={tx("removeTape", "Remove this config")}
                  disabled={locked || tapes.length <= 1}
                  onClick={() => removeTape(key)}
                />
              </motion.li>
            );
          })}
        </ul>
      </Surface>

      <Surface className="p-4">
        <p className="text-xs uppercase text-muted-foreground">{tx("mandateRiskGain", "Risk and gain")}</p>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <Toggle
            checked={riskMode === "agent"}
            onChange={(_, checked) => {
              const mode = checked ? "agent" : "user";
              setRiskMode(mode);
              persist({ risk_mode: mode });
            }}
            label={tx("riskAgent", "Let the agent set risk thresholds")}
          />
          <Toggle
            checked={targetMode === "agent"}
            onChange={(_, checked) => {
              const mode = checked ? "agent" : "user";
              setTargetMode(mode);
              persist({ target_mode: mode });
            }}
            label={tx("targetAgent", "Let the agent set the gain target")}
          />
        </div>
        <TextField
          className="mt-3"
          label={tx("targetReturn", "Target return %")}
          value={target}
          disabled={targetMode === "agent"}
          onChange={(_, value) => setTarget(value || "")}
          onBlur={() => persist({ target_return_pct: Number(target) || 12 })}
        />
        <p className="mt-2 text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
          {tx(
            "riskAgentHint",
            "Agent risk stays conservative: tighter size on crypto and NFT, listed REITs only for real-estate. The risk engine can still only block.",
          )}
        </p>
      </Surface>

      <Surface className="p-4">
        <p className="text-xs uppercase text-muted-foreground">{tx("mandateChannels", "Alerts")}</p>
        <p className="mt-1 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
          {tx(
            "mandateChannelsHint",
            "Click a channel to put it on the mandate. Pending, blocked and filled paper orders fan out here. A closed channel never stops the loop.",
          )}
        </p>
        <ul className="mt-3 grid gap-2 sm:grid-cols-3">
          {alertOptions.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                disabled={busy}
                onClick={() => addAlert(item.id)}
                className="flex min-h-12 w-full cursor-pointer items-center justify-between gap-2 rounded-xl bg-muted/50 px-3 text-left text-sm transition-[transform,background-color] duration-150 hover:bg-muted active:scale-[0.98]"
              >
                <span className="font-medium">{item.name}</span>
                <span className="text-xs text-muted-foreground">
                  {channelReady(item.id)
                    ? tx("channelReady", "ready")
                    : tx("channelNotReady", "not ready")}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {!alertOptions.length ? (
          <p className="mt-3 text-sm text-muted-foreground">
            {tx("alertsAllOn", "Telegram, WhatsApp and email are already on the mandate.")}
          </p>
        ) : null}
      </Surface>

      <div className="lg:col-span-2">
        <Surface className="p-4">
          <p className="text-xs uppercase text-muted-foreground">{tx("alertConfigs", "Alert configs")}</p>
          <p className="mt-1 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
            {tx(
              "alertConfigsHint",
              "Each row is one outbound channel. Fill the destination. Not ready still means Settings > Channels first.",
            )}
          </p>
          {alertConfigs.length ? (
            <ul className="mt-3 space-y-2">
              {alertConfigs.map((item) => {
                const fresh = item.id === addedAlert;
                return (
                  <motion.li
                    key={item.id}
                    ref={fresh ? addedAlertRef : undefined}
                    layout
                    className={cn(
                      "rounded-xl border px-3 py-2",
                      fresh
                        ? "border-amber-400/50 bg-amber-500/18"
                        : "border-black/10 bg-muted/40 dark:border-white/10",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-medium">{item.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {channelReady(item.id)
                            ? tx("alertConfiguredReady", "On the mandate · channel ready")
                            : tx("alertConfiguredWait", "On the mandate · channel not ready yet")}
                        </p>
                      </div>
                      <IconButton
                        iconProps={{ iconName: "Cancel" }}
                        title={tx("removeAlert", "Remove this alert")}
                        ariaLabel={tx("removeAlert", "Remove this alert")}
                        disabled={busy}
                        onClick={() => removeAlert(item.id)}
                      />
                    </div>
                    <TextField
                      className="mt-2"
                      label={item.destLabel}
                      value={item.destValue}
                      placeholder={item.placeholder}
                      onChange={(_, value) => item.setDest(value || "")}
                      onBlur={() => persist({ channels: channelState({ [item.destKey]: item.destValue }) })}
                      description={channelHint(item.id)}
                    />
                  </motion.li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-muted-foreground">
              {tx("alertConfigsEmpty", "Nothing here yet. Click Telegram, WhatsApp or email on the left.")}
            </p>
          )}
        </Surface>
      </div>

      <div className="lg:col-span-2">
        <PrimaryButton
          text={tx("saveMandate", "Save mandate")}
          iconProps={{ iconName: "Save" }}
          disabled={busy || tapes.length < 1}
          onClick={() => persist()}
          styles={BUTTON_STYLES}
        />
      </div>
    </div>
  );
}

function PortfolioPane({
  desk,
  equity,
  currency,
  tx,
}: {
  desk: TradingDesk;
  equity: number;
  currency: string;
  tx: Tx;
}) {
  const bench = desk.metrics.benchmark;
  const exposure = (desk.portfolio.positions || []).reduce((sum, row) => sum + Number(row.qty || 0) * Number(desk.prices?.[row.symbol] ?? row.last ?? row.avg ?? 0), 0);
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Surface className="p-4">
        <p className="text-xs uppercase text-muted-foreground">{tx("book", "Book")}</p>
        <ul className="mt-2 space-y-1 text-sm tabular-nums">
          <li>{tx("equity", "Equity")} {formatMoney(equity, currency)}</li>
          <li>{tx("cash", "Cash")} {formatMoney(Number(desk.portfolio.cash || 0), currency)}</li>
          <li>
            {tx("exposure", "Invested")} {formatMoney(exposure, currency)}
            {equity > 0 ? ` (${formatPct((exposure / equity) * 100, 0)})` : ""}
          </li>
          <li>{tx("realized", "Realized")} {formatMoney(Number(desk.portfolio.realized_pnl || 0), currency)}</li>
          <li>{tx("feesPaid", "Fees paid")} {formatMoney(Number(desk.metrics.fees || 0), currency)}</li>
          <li>{tx("sharpe", "Sharpe")} {desk.metrics.sharpe == null ? "-" : Number(desk.metrics.sharpe).toFixed(2)}</li>
          <li>{tx("sortino", "Sortino")} {desk.metrics.sortino == null ? "-" : Number(desk.metrics.sortino).toFixed(2)}</li>
          <li>{tx("winRate", "Win rate")} {formatPct(Number(desk.metrics.win_rate))}</li>
          <li>{tx("closedTrades", "Closed trades")} {String(desk.metrics.trades ?? 0)}</li>
        </ul>
        <p className="mt-3 text-xs uppercase text-muted-foreground">{tx("stripBenchmark", "vs {{symbol}}", { symbol: String(desk.settings.benchmark || "QQQ") })}</p>
        {bench && bench.alpha != null ? (
          <ul className="mt-1 space-y-1 text-sm tabular-nums" data-testid="trading-benchmark">
            <li>
              {tx("alpha", "alpha")} {Number(bench.alpha) >= 0 ? "+" : ""}
              {Number(bench.alpha).toFixed(2)} pt
            </li>
            <li>{tx("beta", "beta")} {bench.beta == null ? "-" : Number(bench.beta).toFixed(2)}</li>
            <li>
              {tx("benchmarkReturn", "Benchmark return")} {formatPct(bench.benchmark_return_pct)}
              {" · "}
              {tx("portfolioReturn", "Book")} {formatPct(bench.portfolio_return_pct)}
              {" · "}
              {tx("daysN", "{{n}} days", { n: bench.days || 0 })}
            </li>
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">{tx("stripNoBenchmark", "needs a few marked days")}</p>
        )}
        {desk.fx && Object.keys(desk.fx).length > 1 ? (
          <p className="mt-3 text-xs tabular-nums text-muted-foreground">
            {tx("fxLine", "FX to {{currency}}", { currency })}:{" "}
            {Object.entries(desk.fx)
              .filter(([code]) => code !== currency)
              .map(([code, rate]) => `${code} ${Number(rate).toFixed(4)}`)
              .join(" · ")}
          </p>
        ) : null}
      </Surface>
      <Surface className="p-4">
        <p className="text-xs uppercase text-muted-foreground">{tx("activeMandate", "Active mandate")}</p>
        <p className="mt-2 text-sm font-medium">{String(desk.strategy.name || tx("defaultMandate", "Nasdaq growth swing"))}</p>
        <p className="mt-1 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
          {String(desk.strategy.brief || "")}
        </p>
        {desk.mandate ? (
          <div className="mt-3 space-y-1">
            {(desk.mandate.active_tapes || []).slice(0, 8).map((tape) => (
              <p key={`${tape.domain}:${tape.country}`} className="text-xs text-muted-foreground">
                {tape.domain} · {tape.country}
              </p>
            ))}
            {!(desk.mandate.active_tapes || []).length ? (
              <p className="text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
                {(desk.mandate.active_domains || []).join(", ")}
                {" · "}
                {(desk.mandate.active_countries || []).slice(0, 12).join(" ")}
              </p>
            ) : null}
          </div>
        ) : null}
      </Surface>
    </div>
  );
}

function MarketsPane({
  desk,
  onResearch,
  onTrade,
  busy,
  tx,
}: {
  desk: TradingDesk;
  onResearch: (symbol: string) => void;
  onTrade: (symbol: string) => void;
  busy: boolean;
  tx: Tx;
}) {
  const [query, setQuery] = useState("");
  const rows = Object.values(desk.quotes).filter((row) =>
    matchesFilter(query, row.symbol, row.name, row.currency, row.source, desk.research[row.symbol]?.action),
  );
  const bookCcy = desk.portfolio.currency || desk.settings.currency || "USD";
  if (!Object.values(desk.quotes).length) {
    return <Empty text={tx("noQuotes", "No quotes yet. Start the loop or add a watchlist name.")} />;
  }
  return (
    <div className="space-y-3">
      <PaneFilter value={query} onChange={setQuery} tx={tx} />
      {!rows.length ? <Empty text={tx("filterEmpty", "Nothing matches this filter.")} /> : null}
    <Surface className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">{tx("symbol", "Symbol")}</th>
            <th className="px-3 py-2">{tx("price", "Price")}</th>
            <th className="px-3 py-2">{tx("inBook", "In {{currency}}", { currency: bookCcy })}</th>
            <th className="px-3 py-2">{tx("change", "Change")}</th>
            <th className="px-3 py-2">{tx("source", "Source")}</th>
            <th className="px-3 py-2">{tx("decision", "Decision")}</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const research = desk.research[row.symbol];
            const inBook = desk.prices?.[row.symbol];
            return (
              <tr key={row.symbol} className="border-t border-black/5 dark:border-white/5">
                <td className="px-3 py-2">
                  <p className="font-medium">{row.symbol}</p>
                  {row.name && row.name !== row.symbol ? <p className="truncate text-xs text-muted-foreground">{row.name}</p> : null}
                </td>
                <td className="px-3 py-2 tabular-nums">{formatMoney(row.price, row.currency || "USD")}</td>
                <td className="px-3 py-2 tabular-nums text-muted-foreground">
                  {inBook != null && (row.currency || "USD").toUpperCase() !== bookCcy.toUpperCase() ? formatMoney(inBook, bookCcy) : "-"}
                </td>
                <td className={cn("px-3 py-2 tabular-nums", (row.change_pct || 0) >= 0 ? "text-emerald-600" : "text-red-600")}>
                  {formatPct(row.change_pct)}
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground">
                  {row.source || "-"}
                  {row.market_state ? ` · ${row.market_state}` : ""}
                </td>
                <td className="px-3 py-2">
                  <span className={cn("rounded-full px-2 py-0.5 text-xs", actionTone(research?.action))}>
                    {research?.action || "-"}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  <div className="inline-flex gap-1">
                    <DefaultButton
                      text={tx("researchAction", "Research")}
                      disabled={busy}
                      onClick={() => onResearch(row.symbol)}
                      styles={BUTTON_STYLES}
                    />
                    <PrimaryButton
                      text={tx("trade", "Trade")}
                      disabled={busy}
                      onClick={() => onTrade(row.symbol)}
                      styles={BUTTON_STYLES}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Surface>
    </div>
  );
}

function WatchlistPane({
  desk,
  input,
  onInput,
  onAdd,
  onRemove,
  onOpen,
  busy,
  tx,
}: {
  desk: TradingDesk;
  input: string;
  onInput: (value: string) => void;
  onAdd: () => void;
  onRemove: (symbol: string) => void;
  onOpen: (symbol: string) => void;
  busy: boolean;
  tx: Tx;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <TextField
          label={tx("watchSymbol", "Symbol")}
          value={input}
          onChange={(_, value) => onInput((value || "").toUpperCase())}
          placeholder="NVDA"
          className="min-w-[160px]"
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onAdd();
            }
          }}
        />
        <PrimaryButton
          text={tx("add", "Add")}
          iconProps={{ iconName: "Add" }}
          onClick={onAdd}
          disabled={!input.trim() || busy}
          styles={BUTTON_STYLES}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {desk.watchlist.map((symbol) => (
          <span
            key={symbol}
            className="inline-flex min-h-10 items-center gap-1 rounded-full bg-muted/60 px-2.5 text-sm shadow-[0_4px_12px_rgba(15,23,42,0.06)]"
          >
            <button
              type="button"
              className="cursor-pointer px-1 font-medium hover:underline"
              onClick={() => onOpen(symbol)}
            >
              {symbol}
            </button>
            <IconButton
              iconProps={{ iconName: "Cancel" }}
              title={tx("removeSymbol", "Remove {{symbol}}", { symbol })}
              ariaLabel={tx("removeSymbol", "Remove {{symbol}}", { symbol })}
              onClick={() => onRemove(symbol)}
              disabled={busy}
              styles={{ root: { width: 32, height: 32, cursor: "pointer" } }}
            />
          </span>
        ))}
        {!desk.watchlist.length ? <Empty text={tx("watchlistEmpty", "Watchlist is empty.")} /> : null}
      </div>
    </div>
  );
}

function StrategiesPane({
  desk,
  busy,
  onActivate,
  onUseSkill,
  tx,
}: {
  desk: TradingDesk;
  busy: boolean;
  onActivate: (id: string) => void;
  onUseSkill?: (skillId: string) => void;
  tx: Tx;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {desk.strategies.map((row) => (
        <Surface key={String(row.id)} className="p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium">{String(row.name)}</p>
            {row.active ? (
              <span className="rounded-full bg-emerald-600/15 px-2 py-0.5 text-xs">{tx("active", "Active")}</span>
            ) : (
              <DefaultButton
                text={tx("activateStrategy", "Activate")}
                disabled={busy}
                onClick={() => onActivate(String(row.id))}
                styles={BUTTON_STYLES}
              />
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{String(row.skill)}</p>
          <p className="mt-2 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
            {String(row.brief)}
          </p>
        </Surface>
      ))}
      <Surface className="p-4">
        <p className="text-sm font-medium">{tx("installableSkills", "Skills on this desk")}</p>
        <ul className="mt-2 space-y-1 text-sm">
          {desk.skills.map((skill) => (
            <li key={skill.id}>
              <button
                type="button"
                className="min-h-10 w-full cursor-pointer rounded-xl px-2 text-left hover:bg-muted/60"
                onClick={() => onUseSkill?.(skill.id)}
              >
                {skill.name}
              </button>
            </li>
          ))}
        </ul>
      </Surface>
    </div>
  );
}

function AgentsPane({ research, tx }: { research: TradingResearch; tx: Tx }) {
  const scores = research.scores || {};
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(scores).map(([name, score]) => (
          <Surface key={name} className="px-3 py-2">
            <p className="text-xs capitalize text-muted-foreground">{name}</p>
            <p className={cn("text-lg font-semibold tabular-nums", scoreTone(score ?? null))}>
              {score == null ? "-" : Math.round(Number(score))}
            </p>
          </Surface>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Surface className="p-4">
          <p className="text-xs uppercase text-muted-foreground">{tx("bull", "Bull")}</p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
            {(research.debate?.bull?.reasons || []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Surface>
        <Surface className="p-4">
          <p className="text-xs uppercase text-muted-foreground">{tx("bear", "Bear")}</p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
            {(research.debate?.bear?.reasons || []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Surface>
      </div>
      <p className="text-pretty text-sm" style={{ textWrap: "pretty" }}>
        <span className="font-medium">{tx("judge", "Judge")}</span> {research.debate?.judge?.consensus} -{" "}
        {research.debate?.judge?.why}
      </p>
    </div>
  );
}

function StopEditor({
  position,
  busy,
  tx,
  onSave,
  onCancel,
}: {
  position: TradingPosition;
  busy: boolean;
  tx: Tx;
  onSave: (stop: number | null, take: number | null, clearTake: boolean) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [stop, setStop] = useState(position.stop ? String(position.stop) : "");
  const [take, setTake] = useState(position.take ? String(position.take) : "");
  const [error, setError] = useState("");
  const last = Number(position.last ?? position.avg);
  const num = (raw: string) => {
    const trimmed = raw.trim().replace(",", ".");
    if (!trimmed) return null;
    const value = Number(trimmed);
    return Number.isFinite(value) && value > 0 ? value : Number.NaN;
  };
  return (
    <div className="mt-2 grid gap-2 rounded-xl bg-muted/40 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]" data-testid="trading-stop-editor">
      <TextField
        label={tx("stop", "Stop")}
        value={stop}
        inputMode="decimal"
        onChange={(_, value) => setStop(value || "")}
        description={last ? `${tx("last", "Last")} ${last.toFixed(2)}` : ""}
      />
      <TextField
        label={tx("take", "Take")}
        value={take}
        inputMode="decimal"
        placeholder="-"
        onChange={(_, value) => setTake(value || "")}
        description={tx("takeHint", "Empty removes the take-profit")}
      />
      <div className="flex items-end gap-1">
        <PrimaryButton
          text={tx("saveStop", "Save")}
          disabled={busy}
          styles={BUTTON_STYLES}
          onClick={() => {
            const nextStop = num(stop);
            const nextTake = num(take);
            if (Number.isNaN(nextStop) || Number.isNaN(nextTake)) {
              setError(tx("stopInvalid", "Levels must be positive numbers."));
              return;
            }
            if (nextStop != null && last && nextStop >= last) {
              setError(tx("stopAboveLast", "A stop must sit below the last price."));
              return;
            }
            if (nextTake != null && last && nextTake <= last) {
              setError(tx("takeBelowLast", "A take-profit must sit above the last price."));
              return;
            }
            setError("");
            void onSave(nextStop, nextTake, nextTake == null && Boolean(position.take));
          }}
        />
        <DefaultButton text={tx("cancel", "Cancel")} disabled={busy} onClick={onCancel} styles={BUTTON_STYLES} />
      </div>
      {error ? (
        <p className="text-xs text-red-600 dark:text-red-400 sm:col-span-3">{error}</p>
      ) : null}
    </div>
  );
}

function PositionsPane({
  desk,
  currency,
  busy,
  tx,
  onClose,
  onStop,
  onTrade,
}: {
  desk: TradingDesk;
  currency: string;
  busy: boolean;
  tx: Tx;
  onClose: (symbol: string, qty?: number) => Promise<boolean>;
  onStop: (symbol: string, stop: number | null, take: number | null, clearTake: boolean) => Promise<boolean>;
  onTrade: (symbol: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState("");
  const [confirming, setConfirming] = useState("");
  const all = desk.portfolio.positions || [];
  const rows = all.filter((row) => matchesFilter(query, row.symbol, row.id, row.broker, row.sector));
  const approach = Number(desk.settings.alerts?.stop_approach_pct ?? 1.5);
  if (!all.length) return <Empty text={tx("noPositions", "No open paper positions.")} />;
  return (
    <div className="space-y-3">
      <PaneFilter value={query} onChange={setQuery} tx={tx} />
      {!rows.length ? <Empty text={tx("filterEmpty", "Nothing matches this filter.")} /> : null}
      <ul className="space-y-2">
        {rows.map((row) => {
          const last = desk.prices?.[row.symbol] ?? row.last ?? desk.quotes[row.symbol]?.price;
          const pnlPct = row.avg ? ((Number(last ?? row.avg) - Number(row.avg)) / Number(row.avg)) * 100 : null;
          const distance = stopDistancePct(last, row.stop);
          const near = distance != null && distance <= approach;
          const value = Number(row.qty || 0) * Number(last ?? row.avg ?? 0);
          return (
            <li key={row.id}>
              <Surface className={cn("px-4 py-3", near ? "outline-red-500/50" : "")}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium tabular-nums">
                      {row.symbol}
                      <span className="ml-2 text-xs font-normal text-muted-foreground">
                        {formatQty(row.qty)} · {tx("avg", "Avg")} {formatMoney(row.avg, row.currency || currency)}
                        {row.broker && row.broker !== "paper" ? ` · ${row.broker}` : ""}
                        {row.sector ? ` · ${row.sector}` : ""}
                      </span>
                    </p>
                    <p className="mt-1 text-sm tabular-nums">
                      {tx("last", "Last")} {formatMoney(last, currency)}
                      {" · "}
                      {tx("value", "Value")} {formatMoney(value, currency)}
                      {" · "}
                      <span className={cn((row.unrealized || 0) >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                        {tx("pnl", "P&L")} {formatMoney(row.unrealized, currency)}
                        {pnlPct != null ? ` (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%)` : ""}
                      </span>
                    </p>
                    <p className="mt-1 text-xs tabular-nums text-muted-foreground">
                      {tx("stop", "Stop")} {row.stop ? Number(row.stop).toFixed(2) : "-"}
                      {distance != null ? (
                        <span className={cn("ml-1", near ? "font-medium text-red-600 dark:text-red-400" : "")}>
                          ({tx("stopDistance", "{{pct}}% above", { pct: distance.toFixed(1) })})
                        </span>
                      ) : null}
                      {" · "}
                      {tx("take", "Take")} {row.take ? Number(row.take).toFixed(2) : "-"}
                    </p>
                    {row.thesis ? (
                      <p className="mt-1 line-clamp-1 text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
                        {row.thesis}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <DefaultButton
                      text={tx("editStop", "Stop / take")}
                      iconProps={{ iconName: "Edit" }}
                      disabled={busy}
                      onClick={() => setEditing(editing === row.id ? "" : row.id)}
                      styles={BUTTON_STYLES}
                    />
                    <DefaultButton
                      text={tx("trade", "Trade")}
                      iconProps={{ iconName: "StockUp" }}
                      disabled={busy}
                      onClick={() => onTrade(row.symbol)}
                      styles={BUTTON_STYLES}
                    />
                    {confirming === row.id ? (
                      <>
                        <PrimaryButton
                          text={tx("closeConfirm", "Confirm sell all")}
                          iconProps={{ iconName: "Accept" }}
                          disabled={busy}
                          data-testid={`trading-close-confirm-${row.symbol}`}
                          onClick={() => {
                            void onClose(row.symbol).then(() => setConfirming(""));
                          }}
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton text={tx("cancel", "Cancel")} disabled={busy} onClick={() => setConfirming("")} styles={BUTTON_STYLES} />
                      </>
                    ) : (
                      <DefaultButton
                        text={tx("closePosition", "Close")}
                        iconProps={{ iconName: "Blocked2" }}
                        disabled={busy}
                        data-testid={`trading-close-${row.symbol}`}
                        onClick={() => setConfirming(row.id)}
                        styles={BUTTON_STYLES}
                      />
                    )}
                  </div>
                </div>
                <AnimatePresence initial={false}>
                  {editing === row.id ? (
                    <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={SPRING}>
                      <StopEditor
                        position={{ ...row, last: Number(last ?? row.avg) }}
                        busy={busy}
                        tx={tx}
                        onCancel={() => setEditing("")}
                        onSave={async (stop, take, clearTake) => {
                          const ok = await onStop(row.symbol, stop, take, clearTake);
                          if (ok) setEditing("");
                          return ok;
                        }}
                      />
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </Surface>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const ORDER_FILTERS = ["all", "pending", "open", "filled", "blocked"] as const;
type OrderFilter = (typeof ORDER_FILTERS)[number];

function orderMatchesFilter(row: TradingOrder, filter: OrderFilter): boolean {
  if (filter === "all") return true;
  if (filter === "open") return row.status === "submitted" || row.status === "partial";
  if (filter === "blocked") return ["blocked", "rejected", "failed", "cancelled"].includes(row.status);
  return row.status === filter;
}

const ORDER_TONE_CLASS: Record<ReturnType<typeof orderStatusTone>, string> = {
  wait: "bg-amber-500/18 text-amber-800 dark:text-amber-200",
  live: "bg-sky-600/15 text-sky-700 dark:text-sky-300",
  done: "bg-emerald-600/15 text-emerald-700 dark:text-emerald-300",
  stopped: "bg-red-600/15 text-red-700 dark:text-red-300",
  muted: "bg-muted text-muted-foreground",
};

function OrdersPane({
  desk,
  onApprove,
  onReject,
  onReconcile,
  busy,
  currency,
  tx,
}: {
  desk: TradingDesk;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onReconcile: () => void;
  busy: boolean;
  currency: string;
  tx: Tx;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<OrderFilter>("all");
  const all = [...(desk.orders || [])].reverse();
  const rows = all.filter(
    (row) =>
      orderMatchesFilter(row, filter) &&
      matchesFilter(query, row.symbol, row.side, row.status, row.reason, row.origin, row.broker, ...(row.blocks || [])),
  );
  const openCount = openVenueOrders(all).length;
  if (!all.length) return <Empty text={tx("noOrders", "No paper orders yet.")} />;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <PaneFilter value={query} onChange={setQuery} tx={tx} />
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg bg-muted/60 p-0.5 text-xs" role="tablist">
            {ORDER_FILTERS.map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={filter === item}
                onClick={() => setFilter(item)}
                className={cn(
                  "min-h-8 cursor-pointer rounded-md px-2.5 font-medium transition-[background-color] duration-150",
                  filter === item ? "bg-background shadow-sm" : "text-muted-foreground",
                )}
              >
                {tx(`orderFilter.${item}`, item)}
              </button>
            ))}
          </div>
          {openCount ? (
            <DefaultButton
              text={tx("reconcile", "Check venue fills ({{n}})", { n: openCount })}
              iconProps={{ iconName: "Sync" }}
              disabled={busy}
              onClick={onReconcile}
              styles={BUTTON_STYLES}
            />
          ) : null}
        </div>
      </div>
      {!rows.length ? <Empty text={tx("filterEmpty", "Nothing matches this filter.")} /> : null}
      {rows.map((row) => {
        const tone = orderStatusTone(row.status);
        const when = row.t ? new Date(Number(row.t) * 1000).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
        return (
          <Surface key={row.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
            <div className="min-w-0">
              <p className="text-sm font-medium tabular-nums">
                <span className={cn("mr-2 rounded-full px-2 py-0.5 text-[11px] uppercase", row.side === "buy" ? "bg-emerald-600/15 text-emerald-700 dark:text-emerald-300" : "bg-red-600/15 text-red-700 dark:text-red-300")}>
                  {row.side}
                </span>
                {formatQty(row.qty)} {row.symbol} @ {formatMoney(row.price, currency)}
                <span className={cn("ml-2 rounded-full px-2 py-0.5 text-[11px]", ORDER_TONE_CLASS[tone])}>{tx(`orderStatus.${row.status}`, row.status)}</span>
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {when ? `${when} · ` : ""}
                {row.origin ? `${tx(`orderOrigin.${row.origin}`, row.origin)} · ` : ""}
                {row.broker && row.broker !== "paper" ? `${row.broker}${row.venue_symbol ? ` ${row.venue_symbol}` : ""} · ` : ""}
                {row.fee ? `${tx("fee", "fee")} ${formatMoney(row.fee, currency)} · ` : ""}
                {row.filled_qty != null && row.status === "partial" ? `${tx("filledQty", "filled {{qty}}", { qty: formatQty(row.filled_qty) })} · ` : ""}
                {row.pnl != null && row.side === "sell" ? `${tx("pnl", "P&L")} ${formatMoney(row.pnl, currency)} · ` : ""}
                {row.confidence != null ? `${tx("confidence", "confidence")} ${formatPct(row.confidence, 0)} · ` : ""}
                {row.reason || ""}
              </p>
              {row.blocks?.length ? (
                <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">{row.blocks.join("; ")}</p>
              ) : null}
              {row.warnings?.length ? (
                <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-300">{row.warnings.join("; ")}</p>
              ) : null}
              {row.error ? <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">{row.error}</p> : null}
            </div>
            {row.status === "pending" ? (
              <div className="flex gap-2">
                <PrimaryButton
                  text={tx("approve", "Approve")}
                  iconProps={{ iconName: "CheckMark" }}
                  disabled={busy}
                  onClick={() => onApprove(row.id)}
                  styles={BUTTON_STYLES}
                />
                <DefaultButton
                  text={tx("reject", "Reject")}
                  iconProps={{ iconName: "Cancel" }}
                  disabled={busy}
                  onClick={() => onReject(row.id)}
                  styles={BUTTON_STYLES}
                />
              </div>
            ) : null}
          </Surface>
        );
      })}
    </div>
  );
}

function ResearchPane({
  rows,
  selected,
  onSelect,
  onBacktest,
  onRefresh,
  onTrade,
  busy,
  tx,
}: {
  rows: TradingResearch[];
  selected: string;
  onSelect: (symbol: string) => void;
  onBacktest: (symbol: string) => void;
  onRefresh: (symbol: string) => void;
  onTrade: (symbol: string) => void;
  busy?: boolean;
  tx: Tx;
}) {
  const [query, setQuery] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const visible = rows.filter((row) => matchesFilter(query, row.symbol, row.action, row.thesis, row.note));
  const current = visible.find((row) => row.symbol === selected) || visible[0];
  const ask = () => {
    const symbol = symbolInput.trim().toUpperCase();
    if (!symbol) return;
    onSelect(symbol);
    onRefresh(symbol);
    setSymbolInput("");
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <TextField
          label={tx("researchAny", "Research any symbol")}
          value={symbolInput}
          placeholder="ASML.AS"
          className="min-w-[180px]"
          onChange={(_, value) => setSymbolInput((value || "").toUpperCase())}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              ask();
            }
          }}
        />
        <PrimaryButton text={tx("researchAction", "Research")} iconProps={{ iconName: "Search" }} disabled={busy || !symbolInput.trim()} onClick={ask} styles={BUTTON_STYLES} />
        {rows.length ? <PaneFilter value={query} onChange={setQuery} tx={tx} /> : null}
      </div>
      {!rows.length ? <Empty text={tx("noResearchList", "No research notes. Run a cycle.")} /> : null}
      {rows.length && !visible.length ? <Empty text={tx("filterEmpty", "Nothing matches this filter.")} /> : null}
    <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
      <div className="space-y-1">
        {visible.map((row) => (
          <button
            key={row.symbol}
            type="button"
            onClick={() => onSelect(row.symbol)}
            className={cn(
              "flex min-h-10 w-full cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-left text-sm transition-[background-color,transform] duration-150 active:scale-[0.96]",
              row.symbol === current?.symbol ? "bg-muted font-medium" : "hover:bg-muted/50",
            )}
          >
            <span>{row.symbol}</span>
            <span className={cn("rounded-full px-2 py-0.5 text-[11px]", actionTone(row.action))}>{row.action}</span>
          </button>
        ))}
      </div>
      {current ? (
      <Surface className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-medium tabular-nums">
            {current.symbol} · {current.action} · {formatPct(current.confidence)}
            {current.t ? (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {new Date(Number(current.t) * 1000).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
              </span>
            ) : null}
          </p>
          <div className="flex flex-wrap gap-1.5">
            <DefaultButton
              text={tx("refreshResearch", "Re-run")}
              iconProps={{ iconName: "Refresh" }}
              disabled={Boolean(busy)}
              onClick={() => onRefresh(current.symbol)}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("backtest", "Backtest")}
              iconProps={{ iconName: "TestBeaker" }}
              disabled={Boolean(busy)}
              onClick={() => onBacktest(current.symbol)}
              styles={BUTTON_STYLES}
            />
            <PrimaryButton
              text={tx("trade", "Trade")}
              iconProps={{ iconName: "StockUp" }}
              disabled={Boolean(busy)}
              onClick={() => onTrade(current.symbol)}
              styles={BUTTON_STYLES}
            />
          </div>
        </div>
        {current.scores ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-5">
            {Object.entries(current.scores).map(([name, score]) => (
              <div key={name} className="rounded-xl bg-muted/40 px-3 py-2">
                <p className="text-[11px] capitalize text-muted-foreground">{name}</p>
                <p className={cn("text-base font-semibold tabular-nums", scoreTone(score ?? null))}>{score == null ? "-" : Math.round(Number(score))}</p>
              </div>
            ))}
          </div>
        ) : null}
        <p className="mt-3 text-pretty text-sm" style={{ textWrap: "pretty" }}>
          {current.thesis}
        </p>
        {current.note ? (
          <div className="mt-3 rounded-xl bg-amber-500/10 px-3 py-2">
            <p className="text-[11px] uppercase tracking-wide text-amber-800 dark:text-amber-200">{tx("modelNote", "Model note")}</p>
            <p className="mt-1 whitespace-pre-wrap text-pretty text-sm" style={{ textWrap: "pretty" }}>
              {current.note}
            </p>
          </div>
        ) : null}
        {current.debate?.judge ? (
          <p className="mt-3 text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
            <span className="font-medium">{tx("judge", "Judge")}</span> {current.debate.judge.consensus} - {current.debate.judge.why}
          </p>
        ) : null}
      </Surface>
      ) : null}
    </div>
    </div>
  );
}

function JournalPane({ desk, tx }: { desk: TradingDesk; tx: Tx }) {
  const [query, setQuery] = useState("");
  const rows = desk.journal.filter((row) => matchesFilter(query, row.kind, row.symbol, row.text));
  if (!desk.journal.length) {
    return <Empty text={tx("noJournal", "Journal is empty. The loop writes only when it does real work or an honest skip.")} />;
  }
  return (
    <div className="space-y-2">
      <PaneFilter value={query} onChange={setQuery} tx={tx} />
      {!rows.length ? <Empty text={tx("filterEmpty", "Nothing matches this filter.")} /> : null}
    <ol className="space-y-2">
      {rows.map((row) => (
        <li key={row.id || `${row.t}-${row.text}`}>
          <Surface className="px-3 py-2 text-sm">
            <p className="text-xs uppercase text-muted-foreground">
              {row.kind}
              {row.symbol ? ` · ${row.symbol}` : ""}
            </p>
            <p className="text-pretty" style={{ textWrap: "pretty" }}>
              {row.text}
            </p>
          </Surface>
        </li>
      ))}
    </ol>
    </div>
  );
}

const BACKTEST_RANGES = ["6mo", "1y", "2y", "5y"] as const;

function BacktestsPane({
  desk,
  busy,
  currency,
  defaultSymbol,
  tx,
  onRun,
}: {
  desk: TradingDesk;
  busy: boolean;
  currency: string;
  defaultSymbol: string;
  tx: Tx;
  onRun: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [range, setRange] = useState<(typeof BACKTEST_RANGES)[number]>("1y");
  const [benchmark, setBenchmark] = useState(String(desk.settings.benchmark || "QQQ"));
  const [fee, setFee] = useState(String(desk.settings.execution?.fee_bps ?? 5));
  const [slip, setSlip] = useState(String(desk.settings.execution?.slippage_bps ?? 5));
  const [open, setOpen] = useState("");
  useEffect(() => {
    if (defaultSymbol) setSymbol(defaultSymbol);
  }, [defaultSymbol]);
  const rows = [...desk.backtests].reverse().filter((row) => matchesFilter(query, row.symbol, row.range, row.benchmark, row.strategy_name));
  const launch = () => {
    const target = symbol.trim().toUpperCase();
    if (!target) return;
    void onRun({ symbol: target, range, benchmark: benchmark.trim().toUpperCase() || undefined, fee_bps: Number(fee) || 0, slippage_bps: Number(slip) || 0 });
  };
  return (
    <div className="space-y-3">
      <Surface className="p-4">
        <p className="text-xs uppercase text-muted-foreground">{tx("backtestRun", "Run a backtest")}</p>
        <p className="mt-1 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
          {tx(
            "backtestHint",
            "Same specialists, same risk engine, same sizing as the live loop, walked bar by bar with your costs. Fundamentals fall back to a price proxy when no filing is cached.",
          )}
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <TextField
            label={tx("symbol", "Symbol")}
            value={symbol}
            placeholder="NVDA"
            onChange={(_, value) => setSymbol((value || "").toUpperCase())}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                launch();
              }
            }}
          />
          <Dropdown
            label={tx("backtestRange", "History")}
            selectedKey={range}
            options={BACKTEST_RANGES.map((item) => ({ key: item, text: item }))}
            onChange={(_, option) => option && setRange(String(option.key) as (typeof BACKTEST_RANGES)[number])}
          />
          <TextField label={tx("benchmark", "Benchmark")} value={benchmark} onChange={(_, value) => setBenchmark((value || "").toUpperCase())} />
          <TextField label={tx("feeBps", "Fee (bps)")} value={fee} inputMode="decimal" onChange={(_, value) => setFee(value || "")} />
          <TextField label={tx("slippageBps", "Slippage (bps)")} value={slip} inputMode="decimal" onChange={(_, value) => setSlip(value || "")} />
        </div>
        <PrimaryButton
          className="mt-3"
          text={tx("backtest", "Backtest")}
          iconProps={{ iconName: "TestBeaker" }}
          disabled={busy || !symbol.trim()}
          onClick={launch}
          styles={BUTTON_STYLES}
          data-testid="trading-backtest-run"
        />
      </Surface>
      {!desk.backtests.length ? (
        <Empty text={tx("noBacktests", "No backtests yet. Open Research and run Backtest on a name.")} />
      ) : (
        <>
          <PaneFilter value={query} onChange={setQuery} tx={tx} />
          {!rows.length ? <Empty text={tx("filterEmpty", "Nothing matches this filter.")} /> : null}
          <Surface className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">{tx("symbol", "Symbol")}</th>
                  <th className="px-3 py-2">{tx("backtestRange", "History")}</th>
                  <th className="px-3 py-2">{tx("return", "Return")}</th>
                  <th className="px-3 py-2">{tx("benchmark", "Benchmark")}</th>
                  <th className="px-3 py-2">{tx("drawdown", "Max DD")}</th>
                  <th className="px-3 py-2">{tx("sharpe", "Sharpe")}</th>
                  <th className="px-3 py-2">{tx("trades", "Trades")}</th>
                  <th className="px-3 py-2">{tx("winRate", "Win rate")}</th>
                  <th className="px-3 py-2">{tx("curve", "Curve")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const id = String(row.id);
                  const expanded = open === id;
                  const ret = Number(row.return_pct ?? 0);
                  return (
                    <BacktestRow key={id} row={row} expanded={expanded} currency={currency} tx={tx} onToggle={() => setOpen(expanded ? "" : id)} up={ret >= 0} />
                  );
                })}
              </tbody>
            </table>
          </Surface>
        </>
      )}
    </div>
  );
}

function BacktestRow({
  row,
  expanded,
  up,
  currency,
  tx,
  onToggle,
}: {
  row: TradingBacktest;
  expanded: boolean;
  up: boolean;
  currency: string;
  tx: Tx;
  onToggle: () => void;
}) {
  const alpha = row.bench_alpha;
  return (
    <>
      <tr
        className="cursor-pointer border-t border-black/5 transition-[background-color] duration-150 hover:bg-muted/30 dark:border-white/5"
        onClick={onToggle}
        aria-expanded={expanded}
        data-testid={`trading-backtest-row-${row.symbol}`}
      >
        <td className="px-3 py-2">
          <p className="font-medium">{row.symbol}</p>
          <p className="text-[11px] text-muted-foreground">
            {row.t ? new Date(Number(row.t) * 1000).toLocaleDateString() : ""}
            {row.fundamentals ? ` · ${row.fundamentals}` : ""}
          </p>
        </td>
        <td className="px-3 py-2 tabular-nums">{row.range || "-"}</td>
        <td className={cn("px-3 py-2 tabular-nums", up ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>{formatPct(row.return_pct)}</td>
        <td className="px-3 py-2 tabular-nums">
          {row.bench_benchmark_return_pct != null ? (
            <>
              {row.benchmark} {formatPct(row.bench_benchmark_return_pct)}
              <span className={cn("ml-1 text-xs", (alpha ?? 0) >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                ({(alpha ?? 0) >= 0 ? "+" : ""}
                {Number(alpha ?? 0).toFixed(1)})
              </span>
            </>
          ) : (
            "-"
          )}
        </td>
        <td className="px-3 py-2 tabular-nums">{formatPct(row.max_drawdown)}</td>
        <td className="px-3 py-2 tabular-nums">{row.sharpe == null ? "-" : Number(row.sharpe).toFixed(2)}</td>
        <td className="px-3 py-2 tabular-nums">{String(row.trades ?? 0)}</td>
        <td className="px-3 py-2 tabular-nums">{row.win_rate == null ? "-" : formatPct(row.win_rate, 0)}</td>
        <td className="px-3 py-2">
          <MiniCurve points={row.curve} up={up} />
        </td>
      </tr>
      {expanded ? (
        <tr className="border-t border-black/5 bg-muted/20 dark:border-white/5">
          <td colSpan={9} className="px-3 py-3">
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div>
                <EquityChart
                  points={row.curve}
                  label={tx("equityCurve", "Equity curve")}
                  empty={tx("noCurve", "No marked equity yet.")}
                  up={up}
                  currency={currency}
                />
                <p className="mt-2 text-xs tabular-nums text-muted-foreground">
                  {tx("backtestCosts", "Costs")}: {row.fee_bps ?? 0} + {row.slippage_bps ?? 0} bps · {tx("feesPaid", "Fees paid")} {formatMoney(row.fees_paid, currency)}
                  {" · "}
                  {tx("bars", "{{n}} bars", { n: row.bars ?? 0 })}
                  {row.bench_beta != null ? ` · ${tx("beta", "beta")} ${Number(row.bench_beta).toFixed(2)}` : ""}
                  {row.strategy_name ? ` · ${row.strategy_name}` : ""}
                </p>
              </div>
              <div className="max-h-56 overflow-auto">
                <p className="text-xs uppercase text-muted-foreground">{tx("tradeLog", "Trades")}</p>
                {(row.trade_log || []).length ? (
                  <table className="mt-1 w-full text-left text-xs tabular-nums">
                    <thead className="text-muted-foreground">
                      <tr>
                        <th className="py-1 pr-2">{tx("entry", "Entry")}</th>
                        <th className="py-1 pr-2">{tx("exit", "Exit")}</th>
                        <th className="py-1 pr-2">{tx("pnl", "P&L")}</th>
                        <th className="py-1 pr-2">{tx("reasonCol", "Reason")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(row.trade_log || []).map((trade, index) => (
                        <tr key={index} className="border-t border-black/5 dark:border-white/5">
                          <td className="py-1 pr-2">{trade.entry != null ? Number(trade.entry).toFixed(2) : "-"}</td>
                          <td className="py-1 pr-2">{trade.exit != null ? Number(trade.exit).toFixed(2) : "-"}</td>
                          <td className={cn("py-1 pr-2", Number(trade.pnl || 0) >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                            {formatMoney(trade.pnl, currency)}
                          </td>
                          <td className="py-1 pr-2 text-muted-foreground">{trade.reason || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="mt-1 text-sm text-muted-foreground">{tx("noTrades", "No trade fired on this window. The screen or the judge stayed on WAIT.")}</p>
                )}
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function RiskPane({
  desk,
  currency,
  onSave,
  busy,
  tx,
}: {
  desk: TradingDesk;
  currency: string;
  onSave: (payload: Record<string, unknown>) => Promise<boolean>;
  busy: boolean;
  tx: Tx;
}) {
  const risk = desk.settings.risk || {};
  const alerts = desk.settings.alerts || {};
  const riskKey = JSON.stringify(risk);
  const alertKey = JSON.stringify(alerts);
  const [maxPos, setMaxPos] = useState(String(risk.max_position_pct ?? 3));
  const [maxSector, setMaxSector] = useState(String(risk.max_sector_pct ?? 20));
  const [maxDd, setMaxDd] = useState(String(risk.max_drawdown_pct ?? 10));
  const [stop, setStop] = useState(String(risk.stop_loss_pct ?? 5));
  const [take, setTake] = useState(risk.take_profit_pct == null ? "" : String(risk.take_profit_pct));
  const [daily, setDaily] = useState(String(risk.max_daily_loss_pct ?? 2));
  const [maxOrders, setMaxOrders] = useState(String(risk.max_daily_orders ?? 8));
  const [minCash, setMinCash] = useState(String(risk.min_cash_pct ?? 5));
  const [approval, setApproval] = useState(String(risk.approval_notional ?? 2000));
  const [allowed, setAllowed] = useState((risk.allowed_assets || []).join(", "));
  const [blocked, setBlocked] = useState((risk.blocked_assets || []).join(", "));
  const [stopApproach, setStopApproach] = useState(String(alerts.stop_approach_pct ?? 1.5));
  const [ddWarn, setDdWarn] = useState(String(alerts.drawdown_warn_pct ?? 6));
  const [dailyWarn, setDailyWarn] = useState(String(alerts.daily_loss_warn_pct ?? 1.2));
  const [levels, setLevels] = useState(alerts.price_levels || []);
  const [levelSymbol, setLevelSymbol] = useState("");
  const [levelPrice, setLevelPrice] = useState("");
  const [levelWhen, setLevelWhen] = useState<"above" | "below">("above");
  const [autonomous, setAutonomous] = useState(
    String(desk.settings.execution_mode || desk.strategy.execution_mode || "autonomous") !== "approval",
  );
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setMaxPos(String(risk.max_position_pct ?? 3));
    setMaxSector(String(risk.max_sector_pct ?? 20));
    setMaxDd(String(risk.max_drawdown_pct ?? 10));
    setStop(String(risk.stop_loss_pct ?? 5));
    setTake(risk.take_profit_pct == null ? "" : String(risk.take_profit_pct));
    setDaily(String(risk.max_daily_loss_pct ?? 2));
    setMaxOrders(String(risk.max_daily_orders ?? 8));
    setMinCash(String(risk.min_cash_pct ?? 5));
    setApproval(String(risk.approval_notional ?? 2000));
    setAllowed((risk.allowed_assets || []).join(", "));
    setBlocked((risk.blocked_assets || []).join(", "));
    setAutonomous(String(desk.settings.execution_mode || desk.strategy.execution_mode || "autonomous") !== "approval");
  }, [riskKey, desk.settings.execution_mode, desk.strategy.execution_mode]);
  useEffect(() => {
    setStopApproach(String(alerts.stop_approach_pct ?? 1.5));
    setDdWarn(String(alerts.drawdown_warn_pct ?? 6));
    setDailyWarn(String(alerts.daily_loss_warn_pct ?? 1.2));
    setLevels(alerts.price_levels || []);
  }, [alertKey]);
  useEffect(() => {
    if (!saved) return undefined;
    const id = window.setTimeout(() => setSaved(false), 3500);
    return () => window.clearTimeout(id);
  }, [saved]);

  const num = (raw: string, fallback: number) => {
    const value = Number(String(raw).replace(",", "."));
    return Number.isFinite(value) ? value : fallback;
  };
  const save = async () => {
    const ok = await onSave({
      execution_mode: autonomous ? "autonomous" : "approval",
      risk: {
        max_position_pct: num(maxPos, 3),
        max_sector_pct: num(maxSector, 20),
        max_drawdown_pct: num(maxDd, 10),
        stop_loss_pct: num(stop, 5),
        take_profit_pct: take.trim() ? num(take, 0) : null,
        max_daily_loss_pct: num(daily, 2),
        max_daily_orders: Math.max(0, Math.round(num(maxOrders, 8))),
        min_cash_pct: num(minCash, 5),
        approval_notional: num(approval, 2000),
        allowed_assets: allowed,
        blocked_assets: blocked,
        leverage: false,
      },
      alerts: {
        stop_approach_pct: num(stopApproach, 1.5),
        drawdown_warn_pct: num(ddWarn, 6),
        daily_loss_warn_pct: num(dailyWarn, 1.2),
        price_levels: levels,
      },
    });
    if (ok) setSaved(true);
  };
  const addLevel = () => {
    const symbol = levelSymbol.trim().toUpperCase();
    const price = num(levelPrice, 0);
    if (!symbol || price <= 0) return;
    setLevels((current) => [...current.filter((row) => !(row.symbol === symbol && row.price === price)), { symbol, price, when: levelWhen }]);
    setLevelSymbol("");
    setLevelPrice("");
  };
  const field = (label: string, value: string, set: (value: string) => void, description?: string) => (
    <TextField label={label} value={value} inputMode="decimal" onChange={(_, next) => set(next || "")} description={description} />
  );

  return (
    <div className="space-y-3">
      <MessageBar messageBarType={MessageBarType.info}>
        {tx("riskInfo", "These limits are code, not a model. The LLM can propose a trade; this engine can only refuse it.")}
      </MessageBar>
      {saved ? <MessageBar messageBarType={MessageBarType.success}>{tx("riskSaved", "Limits saved. They apply to the next order, manual or loop.")}</MessageBar> : null}
      <div className="grid gap-3 lg:grid-cols-2">
        <Surface className="p-4">
          <p className="text-xs uppercase text-muted-foreground">{tx("riskLimits", "Hard limits")}</p>
          <Toggle
            className="mt-2"
            label={tx("paperFills", "Paper fills under the approval cap")}
            checked={autonomous}
            onChange={(_, checked) => setAutonomous(Boolean(checked))}
            onText={tx("paperFillsOn", "Autonomous")}
            offText={tx("paperFillsOff", "Ask me each buy")}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {field(tx("maxPosition", "Max position %"), maxPos, setMaxPos)}
            {field(tx("maxSector", "Max sector %"), maxSector, setMaxSector)}
            {field(tx("maxDrawdown", "Max drawdown %"), maxDd, setMaxDd)}
            {field(tx("maxDailyLoss", "Max daily loss %"), daily, setDaily)}
            {field(tx("stopLoss", "Stop loss %"), stop, setStop)}
            {field(tx("takeProfit", "Take profit %"), take, setTake, tx("takeProfitHint", "Empty: no automatic take"))}
            {field(tx("maxDailyOrders", "Max orders per day"), maxOrders, setMaxOrders)}
            {field(tx("minCash", "Cash floor %"), minCash, setMinCash)}
            {field(tx("approvalAbove", "Approval above ({{currency}})", { currency }), approval, setApproval)}
          </div>
          <Toggle
            className="mt-3"
            label={tx("leverage", "Leverage")}
            checked={Boolean(risk.leverage)}
            disabled
            onText={tx("leverageOn", "On")}
            offText={tx("leverageOff", "Off (V1)")}
          />
        </Surface>
        <div className="space-y-3">
          <Surface className="p-4">
            <p className="text-xs uppercase text-muted-foreground">{tx("assetLists", "Asset lists")}</p>
            <TextField
              className="mt-2"
              label={tx("allowedAssets", "Only these symbols (optional)")}
              value={allowed}
              placeholder="AAPL, MSFT, BTC-USD"
              onChange={(_, value) => setAllowed(value || "")}
            />
            <TextField
              className="mt-2"
              label={tx("blockedAssets", "Never these symbols")}
              value={blocked}
              placeholder="TSLA, DOGE-USD"
              onChange={(_, value) => setBlocked(value || "")}
            />
          </Surface>
          <Surface className="p-4">
            <p className="text-xs uppercase text-muted-foreground">{tx("alertsTitle", "Guard alerts")}</p>
            <p className="mt-1 text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
              {tx("alertsHint", "The intraday guard warns on the mandate channels before the hard limits bite.")}
            </p>
            <div className="mt-2 grid gap-3 sm:grid-cols-3">
              {field(tx("stopApproach", "Stop within %"), stopApproach, setStopApproach)}
              {field(tx("drawdownWarn", "Drawdown warn %"), ddWarn, setDdWarn)}
              {field(tx("dailyLossWarn", "Daily loss warn %"), dailyWarn, setDailyWarn)}
            </div>
            <p className="mt-3 text-xs uppercase text-muted-foreground">{tx("priceLevels", "Price levels")}</p>
            <div className="mt-1 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] sm:items-end">
              <TextField label={tx("symbol", "Symbol")} value={levelSymbol} onChange={(_, value) => setLevelSymbol((value || "").toUpperCase())} />
              <TextField label={tx("price", "Price")} value={levelPrice} inputMode="decimal" onChange={(_, value) => setLevelPrice(value || "")} />
              <Dropdown
                label={tx("levelWhen", "When")}
                selectedKey={levelWhen}
                options={[
                  { key: "above", text: tx("levelAbove", "crosses above") },
                  { key: "below", text: tx("levelBelow", "falls below") },
                ]}
                onChange={(_, option) => option && setLevelWhen(String(option.key) as "above" | "below")}
              />
              <DefaultButton text={tx("add", "Add")} iconProps={{ iconName: "Add" }} disabled={!levelSymbol.trim() || !levelPrice.trim()} onClick={addLevel} styles={BUTTON_STYLES} />
            </div>
            {levels.length ? (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {levels.map((level) => (
                  <li key={`${level.symbol}-${level.price}-${level.when}`} className="inline-flex min-h-8 items-center gap-1 rounded-full bg-muted/60 pl-3 text-xs tabular-nums">
                    {level.symbol} {level.when === "below" ? "<" : ">"} {Number(level.price).toFixed(2)}
                    <IconButton
                      iconProps={{ iconName: "Cancel" }}
                      ariaLabel={tx("removeLevel", "Remove level")}
                      title={tx("removeLevel", "Remove level")}
                      onClick={() => setLevels((current) => current.filter((row) => row !== level))}
                      styles={{ root: { width: 28, height: 28, cursor: "pointer" } }}
                    />
                  </li>
                ))}
              </ul>
            ) : null}
          </Surface>
        </div>
      </div>
      <PrimaryButton text={tx("saveRisk", "Save risk limits")} iconProps={{ iconName: "Save" }} disabled={busy} styles={BUTTON_STYLES} onClick={() => void save()} data-testid="trading-save-risk" />
    </div>
  );
}
