import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Customizer,
  DefaultButton,
  Icon,
  MessageBar,
  MessageBarType,
  PrimaryButton,
  ProgressIndicator,
  TextField,
  createTheme,
  Dropdown,
  type IDropdownOption,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { MarketingQA } from "@/components/studio/MarketingQA";
import { BrandVisuals, CreativeCard, MediaThumb } from "@/components/studio/marketing/MarketingMedia";
import { MarketingKpiGrid, buildMarketingKpis } from "@/components/studio/marketing/MarketingKpis";
import { ConnectorsPane, ContentQueue, Pill } from "@/components/studio/marketing/MarketingPublish";
import { MarketingScene } from "@/components/studio/marketing/MarketingScene";
import { TradingLoopSchedulePanel } from "@/components/studio/trading/TradingLoopSchedulePanel";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";
import {
  fetchMarketingDesk,
  postMarketing,
  type MarketingDesk,
  type MarketingLoopSchedule,
} from "@/lib/marketing-api";
import { channelLabel, formatWhen, summarizeQueue } from "@/lib/marketing-publish";
import {
  browserTimeZone,
  describeTradingSchedule,
  formatNextDue,
  normalizeTradingSchedule,
} from "@/lib/trading-loop-schedule";
import { cn } from "@/lib/utils";

const SPRING = { type: "spring" as const, duration: 0.3, bounce: 0 };

const PANES = [
  { id: "home", icon: "Home" },
  { id: "brand", icon: "Color" },
  { id: "product", icon: "Product" },
  { id: "campaigns", icon: "Calendar" },
  { id: "content", icon: "EditNote" },
  { id: "publish", icon: "Send" },
  { id: "creative", icon: "Photo2" },
  { id: "qa", icon: "CheckList" },
  { id: "social", icon: "Share" },
  { id: "seo", icon: "Search" },
  { id: "ads", icon: "Money" },
  { id: "analytics", icon: "BarChart4" },
  { id: "competitors", icon: "Org" },
  { id: "launch", icon: "Rocket" },
  { id: "settings", icon: "Settings" },
  { id: "journal", icon: "TextDocument" },
] as const;

type Pane = (typeof PANES)[number]["id"];
type SourceKind = "workspace" | "recent" | "url" | "product";

const SOCIAL_CHANNELS = ["linkedin", "facebook", "instagram", "tiktok"] as const;
const STUDIO_PLACEMENTS = new Set([
  "linkedin post",
  "facebook post",
  "instagram post",
  "tiktok cover",
  "linkedin clip",
  "facebook clip",
  "instagram reel",
  "tiktok clip",
  "brand lockup",
  "og / blog header",
  "site logo",
  "site og",
  "site image",
  "15s voice over",
]);

const AUDIENCE_CHIPS = [
  "Founders / indie hackers",
  "SMB operators",
  "Product teams",
  "Marketing / growth",
  "Agencies",
  "Developers",
  "Finance / ops",
  "HR / people ops",
];
const TONE_CHIPS = ["Clear and confident", "Warm and human", "Expert / technical", "Playful", "Premium / quiet"];
const COUNTRY_CHIPS = [
  "France",
  "Belgique",
  "Suisse",
  "Canada",
  "USA",
  "UK",
  "Allemagne",
  "Espagne",
  "Maroc",
  "Tunisie",
  "Senegal",
  "Cote d'Ivoire",
  "World",
];
const LANGUAGE_CHIPS = ["Francais", "English", "Arabe", "Espanol", "Deutsch"];
const INDUSTRY_CHIPS = [
  "B2B SaaS",
  "Marketplace",
  "Devtools",
  "Fintech",
  "Health",
  "Education",
  "Agency",
  "Ecommerce",
  "Local services",
];

function folderName(path?: string | null): string {
  const clean = (path || "").replace(/[\\/]+$/, "");
  if (!clean) return "";
  const parts = clean.split(/[\\/]/);
  return parts[parts.length - 1] || clean;
}

function isProjectsRootName(name?: string | null, path?: string | null): boolean {
  const label = (name || folderName(path) || "").trim().toLowerCase().replace(/\s+/g, "");
  return label === "navinprojects";
}

function productDisplayName(name?: string | null, path?: string | null): string {
  const raw = (name || "").trim();
  if (!raw || isProjectsRootName(raw, path)) return "";
  return raw;
}

function withoutProjectsRoot(text?: string | null, fallback = ""): string {
  const raw = text || "";
  if (!/navinprojects/i.test(raw)) return raw;
  const name = fallback.trim() || "the product";
  return raw.replace(/NavinProjects/gi, name);
}

function toggleChip(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

const BUTTON_STYLES = {
  root: { minHeight: 40, cursor: "pointer" as const },
};

function useMarketingTheme(mode: "light" | "dark") {
  return useMemo(
    () =>
      createTheme({
        palette:
          mode === "dark"
            ? {
                themePrimary: "#A855F7",
                themeLighterAlt: "#14081A",
                themeLighter: "#3B1764",
                themeLight: "#6B21A8",
                themeTertiary: "#7C3AED",
                themeSecondary: "#8B5CF6",
                themeDarkAlt: "#C084FC",
                themeDark: "#D8B4FE",
                themeDarker: "#E9D5FF",
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
                themePrimary: "#7C3AED",
                themeLighterAlt: "#F5F3FF",
                themeLighter: "#EDE9FE",
                themeLight: "#DDD6FE",
                themeTertiary: "#A78BFA",
                themeSecondary: "#8B5CF6",
                themeDarkAlt: "#7C3AED",
                themeDark: "#6D28D9",
                themeDarker: "#5B21B6",
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

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-border/70 bg-background/80 p-4 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
      <h3 className="mb-3 text-balance text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function ChipRow({
  items,
  selected,
  onToggle,
  single,
}: {
  items: string[];
  selected: string[];
  onToggle: (value: string) => void;
  single?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => {
        const on = single ? selected[0] === item : selected.includes(item);
        return (
          <DefaultButton
            key={item}
            text={item}
            primary={on}
            onClick={() => onToggle(item)}
            styles={{
              root: {
                minHeight: 32,
                borderRadius: 999,
                padding: "0 12px",
                cursor: "pointer",
              },
            }}
          />
        );
      })}
    </div>
  );
}

export function MarketingWorkspace({
  chatOpen,
  onToggleChat,
  onSeed,
  projectPath,
  projectName,
  recentProjects = [],
}: {
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
  projectPath?: string | null;
  projectName?: string | null;
  recentProjects?: { path: string; name: string }[];
}) {
  const { t, i18n } = useTranslation();
  const theme = useThemeValue();
  const fluentTheme = useMarketingTheme(theme);
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  const [desk, setDesk] = useState<MarketingDesk | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [pane, setPane] = useState<Pane>("home");
  const [company, setCompany] = useState("");
  const [tone, setTone] = useState("");
  const [audience, setAudience] = useState("");
  const [goal, setGoal] = useState("1000 inscriptions");
  const [traffic, setTraffic] = useState("");
  const [leads, setLeads] = useState("");
  const [signups, setSignups] = useState("");
  const [revenue, setRevenue] = useState("");
  const [rankKeyword, setRankKeyword] = useState("");
  const [rankUrl, setRankUrl] = useState("");
  const [rankPosition, setRankPosition] = useState("");
  const [contentHits, setContentHits] = useState<Record<string, { views: string; clicks: string; conversions: string }>>({});
  const [competitorName, setCompetitorName] = useState("");
  const [competitorNote, setCompetitorNote] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"start" | "edit">("start");
  const [notice, setNotice] = useState("");
  const [hint, setHint] = useState("");
  const [sourceKind, setSourceKind] = useState<SourceKind>("recent");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [sourceOneLiner, setSourceOneLiner] = useState("");
  const [pickedPath, setPickedPath] = useState("");
  const [countries, setCountries] = useState<string[]>([]);
  const [languages, setLanguages] = useState<string[]>([]);
  const [industries, setIndustries] = useState<string[]>([]);

  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, string | number>) =>
      t(`studio.marketing.${key}`, { defaultValue: fallback, ...values }),
    [t],
  );

  const copyPost = useCallback(
    (text: string) => {
      const body = text.trim();
      if (!body) return;
      const ok = () => setNotice(tx("copied", "Copied. Paste it on LinkedIn, Facebook, Instagram or TikTok."));
      const fail = () => setError(tx("copyFailed", "Could not copy that draft."));
      if (navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(body).then(ok, fail);
        return;
      }
      try {
        const area = document.createElement("textarea");
        area.value = body;
        area.setAttribute("readonly", "true");
        area.style.position = "fixed";
        area.style.left = "-9999px";
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand("copy");
        area.remove();
        if (copied) ok();
        else fail();
      } catch {
        fail();
      }
    },
    [tx],
  );

  const inFlightRef = useRef(false);
  const load = useCallback(async () => {
    if (!token || inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const next = await fetchMarketingDesk(token);
      setDesk(next);
      const productName = productDisplayName(next.product.name, next.product.workspace);
      const brandCompany = productDisplayName(next.brand.company);
      setCompany((current) => productDisplayName(current) || brandCompany || productName);
      setTone((current) => current || next.brand.tone || "");
      setAudience((current) => current || next.brand.audience || next.positioning.icp || "");
      setCountries((current) => (current.length ? current : next.brand.countries || []));
      setLanguages((current) => (current.length ? current : next.brand.languages || []));
      setIndustries((current) => (current.length ? current : next.brand.industries || []));
      setSourceUrl((current) => current || next.product.site || next.brand.site || "");
      setSourceName((current) => productDisplayName(current) || productName);
      setSourceOneLiner((current) => current || next.product.one_liner || "");
      const kind = (next.product.source_kind || "") as SourceKind;
      const boundWorkspace = next.product.workspace || "";
      const boundIsRoot = isProjectsRootName(next.product.name, boundWorkspace);
      if (kind === "url" || kind === "product") {
        setSourceKind(kind);
      } else if (kind === "workspace" && boundWorkspace && !boundIsRoot) {
        setSourceKind(boundWorkspace !== (projectPath || "") ? "recent" : "workspace");
        setPickedPath(boundWorkspace);
      }
      setTraffic((current) => current || (next.analytics.traffic ? String(next.analytics.traffic) : ""));
      setLeads((current) => current || (next.analytics.leads ? String(next.analytics.leads) : ""));
      setSignups((current) => current || (next.analytics.signups ? String(next.analytics.signups) : ""));
      setRevenue((current) => current || (next.analytics.revenue ? String(next.analytics.revenue) : ""));
      setContentHits((current) => {
        if (Object.keys(current).length) return current;
        const nextHits: Record<string, { views: string; clicks: string; conversions: string }> = {};
        for (const [id, raw] of Object.entries(next.analytics.by_content || {})) {
          nextHits[id] = {
            views: String(raw.views || ""),
            clicks: String(raw.clicks || ""),
            conversions: String(raw.conversions || ""),
          };
        }
        return nextHits;
      });
      setError("");
    } catch (err) {
      setError((err as Error).message || tx("loadError", "Could not load the marketing desk."));
    } finally {
      inFlightRef.current = false;
      setReady(true);
    }
  }, [token, tx, projectPath]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!token || !desk?.loop.enabled) return undefined;
    const id = window.setInterval(() => {
      void load();
    }, 12_000);
    return () => window.clearInterval(id);
  }, [token, desk?.loop.enabled, load]);

  const run = useCallback(
    async (action: string, body: Record<string, unknown> = {}, after?: (next: MarketingDesk) => void) => {
      if (!token) return false;
      setBusy(action);
      setError("");
      setHint("");
      setNotice("");
      try {
        const next = await postMarketing(token, action, body);
        setDesk(next);
        if (next.product?.site) setSourceUrl(next.product.site);
        if (productDisplayName(next.product?.name, next.product?.workspace)) {
          setSourceName(productDisplayName(next.product.name, next.product.workspace));
        }
        if (next.product?.one_liner) setSourceOneLiner(next.product.one_liner);
        if (next.product?.source_kind === "url") setSourceKind("url");
        if (productDisplayName(next.brand?.company)) {
          setCompany((current) => productDisplayName(current) || productDisplayName(next.brand.company));
        }
        after?.(next);
        return true;
      } catch (err) {
        setError((err as Error).message || tx("actionFailed", "Marketing action failed."));
        return false;
      } finally {
        setBusy("");
      }
    },
    [token, tx],
  );

  const shipSocial = useCallback(() => {
    void run("ship", {}, (next) => {
      setNotice(
        tx("shipDone", "Pack ready: {{n}} drafts for LinkedIn, Facebook, Instagram, TikTok. {{skip}}", {
          n: next.content.length,
          skip: (next.produce?.skipped || []).join(" ") || "",
        }),
      );
      setPane("social");
    });
  }, [run, tx]);

  const currentLabel = isProjectsRootName(projectName, projectPath)
    ? ""
    : projectName || folderName(projectPath) || "";
  const boundLabel =
    productDisplayName(desk?.product.name, desk?.product.workspace) || tx("noProduct", "No product yet.");
  const workspaceChoices = recentProjects.filter((row) => !isProjectsRootName(row.name, row.path));
  const boundHint = desk?.product.site
    ? desk.product.site
    : desk?.product.workspace && !isProjectsRootName(desk.product.name, desk.product.workspace)
      ? desk.product.workspace
      : tx("notBound", "Nothing bound yet. Pick a source below.");

  const sourcePayload = useCallback(() => {
    if (sourceKind === "url") {
      return {
        workspace: "",
        source_kind: "url",
        site: sourceUrl.trim(),
        name: sourceName.trim(),
        one_liner: sourceOneLiner.trim(),
      };
    }
    if (sourceKind === "product") {
      return {
        workspace: "",
        source_kind: "product",
        site: sourceUrl.trim(),
        name: sourceName.trim(),
        one_liner: sourceOneLiner.trim(),
      };
    }
    const workspace = sourceKind === "recent" ? pickedPath : projectPath || "";
    return {
      workspace,
      source_kind: "workspace",
      name: sourceName.trim(),
      one_liner: sourceOneLiner.trim(),
    };
  }, [pickedPath, projectPath, sourceKind, sourceName, sourceOneLiner, sourceUrl]);

  const boundPayload = () => {
    const product = desk?.product;
    if (!product?.name && !product?.site && !product?.workspace) return null;
    const kind = product.source_kind || (product.site ? "url" : "workspace");
    return {
      workspace: product.workspace || "",
      source_kind: kind,
      site: product.site || "",
      name: product.name || "",
      one_liner: product.one_liner || "",
    };
  };

  const bindProduct = (andPipeline: boolean, preferBound = false) => {
    setHint("");
    let payload = sourcePayload();
    const incomplete =
      (sourceKind === "url" && !payload.site) ||
      (sourceKind === "product" && !payload.name) ||
      ((sourceKind === "workspace" || sourceKind === "recent") && !payload.workspace);
    if (incomplete && preferBound) {
      const fallback = boundPayload();
      if (fallback) payload = fallback;
    }
    if (payload.source_kind === "url" && !payload.site) {
      setHint(tx("needUrl", "Paste the live site URL first."));
      return;
    }
    if (payload.source_kind === "product" && !payload.name) {
      setHint(tx("needName", "Name the product first."));
      return;
    }
    if (payload.source_kind === "workspace" && !payload.workspace) {
      setHint(tx("needWorkspace", "Open a project in Navin, or pick one from the list."));
      return;
    }
    const action = andPipeline ? "pipeline" : "understand";
    void run(action, { ...payload, goal, days: 30, signups: 1000 }, (next) => {
      setNotice(
        andPipeline
          ? tx("pipelineDone", "Product bound. Campaign, content and launch kit are ready.")
          : tx("understandDone", "Product bound: {{name}}", { name: next.product.name || payload.name || "" }),
      );
      setPane(andPipeline ? "campaigns" : "product");
    });
  };

  const hasPublishHistory = Boolean(
    (desk?.kpis.published || 0) + (desk?.kpis.scheduled || 0) + (desk?.kpis.approved || 0) + (desk?.kpis.failed || 0) + (desk?.kpis.measured || 0),
  );
  const kpis = useMemo(
    () =>
      buildMarketingKpis({
        signups: desk?.kpis.signups || 0,
        content: desk?.kpis.content || 0,
        winners: desk?.kpis.winners || 0,
        campaigns: desk?.kpis.campaigns || 0,
        published: hasPublishHistory ? desk?.kpis.published || 0 : undefined,
        scheduled: desk?.kpis.scheduled || 0,
        views: desk?.kpis.views || 0,
        clicks: desk?.kpis.clicks || 0,
        labels: {
          signups: tx("kpiSignups", "Signups"),
          content: tx("kpiContent", "Content"),
          winners: tx("kpiWinners", "Winners"),
          campaigns: tx("kpiCampaigns", "Campaigns"),
          published: tx("kpiPublished", "Published"),
          scheduled: tx("kpiScheduled", "Scheduled"),
          views: tx("kpiViews", "Views"),
          clicks: tx("kpiClicks", "Clicks"),
        },
      }),
    [desk, hasPublishHistory, tx],
  );
  const queueSummary = summarizeQueue(desk?.queue);
  const readyConnectors = (desk?.connectors || []).filter((row) => row.ready && row.mode !== "manual");

  const seedChat = (text?: string) => {
    onSeed?.(
      text ||
        `/marketing ${tx("seedFallback", "Lance le marketing de mon produit. Comprends le projet courant, pose le positionnement, puis propose la campagne 30 jours.")}\n\n`,
    );
  };

  const submitSchedule = async (schedule: MarketingLoopSchedule, runNow: boolean) => {
    const tz = browserTimeZone();
    const action = scheduleMode === "start" ? "start" : "schedule";
    const ok = await run(action, { schedule: { ...schedule, tz: tz || schedule.tz || null }, tz, run_now: runNow });
    if (ok) setScheduleOpen(false);
  };

  const loopSchedule = normalizeTradingSchedule(desk?.loop.schedule);
  const loopLabel = describeTradingSchedule(loopSchedule, tx, i18n.language);
  const dueLabel = formatNextDue(desk?.loop.next_due, i18n.language);

  return (
    <Customizer settings={{ theme: fluentTheme }}>
      <div
        className={cn("flex h-full min-h-0 flex-col bg-background", chatOpen ? "pr-0" : "")}
        style={{ paddingTop: NOTIFICATION_GUTTER ? 0 : undefined, WebkitFontSmoothing: "antialiased" }}
      >
        <header className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.06)]">
          <div className="flex min-w-0 items-baseline gap-2.5">
            <p className="shrink-0 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
              {tx("kicker", "Studio")}
            </p>
            <h1 className="truncate text-xl font-semibold tracking-tight">{tx("deskTitle", "Marketing Agent OS")}</h1>
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
                data-testid="marketing-pause-loop"
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
                disabled={Boolean(busy) || !desk?.armed}
                data-testid="marketing-start-loop"
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
              data-testid="marketing-schedule-loop"
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("cycle", "Run cycle")}
              iconProps={{ iconName: "Sync" }}
              onClick={() => void run("tick", { force: true })}
              disabled={Boolean(busy)}
              data-testid="marketing-tick-loop"
              styles={BUTTON_STYLES}
            />
          </div>
        </header>

        <nav
          className="shrink-0 overflow-x-auto px-2 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.08)]"
          aria-label={tx("panesAria", "Marketing desk sections")}
        >
          <div className="flex min-w-max items-stretch gap-0.5 py-1">
            {PANES.map((item) => {
              const selected = pane === item.id;
              return (
                <motion.button
                  key={item.id}
                  type="button"
                  whileTap={reduceMotion ? undefined : { scale: 0.96 }}
                  onClick={() => setPane(item.id)}
                  className={cn(
                    "relative inline-flex min-h-14 min-w-[4.5rem] shrink-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-xl px-2.5 py-1.5 text-center transition-[background-color,color,box-shadow] duration-150",
                    selected
                      ? "bg-violet-500/16 font-medium text-foreground shadow-[0_6px_16px_rgba(15,23,42,0.08)]"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                >
                  <Icon iconName={item.icon} className="text-base" />
                  <span className="text-[11px]">{tx(`pane.${item.id}`, item.id)}</span>
                </motion.button>
              );
            })}
          </div>
        </nav>

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {!ready && !error ? (
            <ProgressIndicator
              label={tx("loading", "Loading the marketing desk")}
              className="mb-4"
            />
          ) : null}
          {error ? (
            <MessageBar messageBarType={MessageBarType.error} onDismiss={() => setError("")} className="mb-4">
              {error}
              <DefaultButton
                className="ml-3"
                text={tx("retry", "Try again")}
                onClick={() => {
                  setReady(false);
                  void load();
                }}
                styles={BUTTON_STYLES}
              />
            </MessageBar>
          ) : null}
          {hint ? (
            <MessageBar messageBarType={MessageBarType.warning} onDismiss={() => setHint("")} className="mb-4">
              {hint}
            </MessageBar>
          ) : null}
          {notice ? (
            <MessageBar messageBarType={MessageBarType.success} onDismiss={() => setNotice("")} className="mb-4">
              {notice}
            </MessageBar>
          ) : null}
          {busy ? (
            <ProgressIndicator
              label={tx("busyLabel", "Working: {{action}}", { action: busy })}
              className="mb-4"
            />
          ) : null}

          <AnimatePresence mode="wait">
            <motion.div
              key={pane}
              initial={reduceMotion ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: -6 }}
              transition={SPRING}
              className={
                pane === "qa"
                  ? "grid min-h-0 gap-4"
                  : "grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]"
              }
            >
              {pane === "qa" ? (
                <div className="grid gap-4">
                  <Card title={tx("deskCreatives", "Desk creatives")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx(
                        "deskCreativesHint",
                        "Harvested and generated assets from this marketing book. PASS / WARN / BLOCK writes a human verdict. The workspace QA below still scans marketing/creatives/ in the Code project.",
                      )}
                    </p>
                    {(desk?.creatives || []).some((item) => item.preview) ? (
                      <ul className="grid gap-3 sm:grid-cols-2">
                        {(desk?.creatives || [])
                          .filter((item) => item.preview)
                          .map((item) => (
                            <CreativeCard
                              key={item.id}
                              item={item}
                              busy={Boolean(busy)}
                              generateLabel={tx("generateThisOne", "Generate this")}
                              onGenerate={() => void run("produce", { id: item.id, kinds: [item.kind || "image"] })}
                              onVision={(verdict) => void run("vision", { id: item.id, verdict })}
                            />
                          ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {tx("noDeskCreatives", "No desk previews yet. Harvest the live site or generate a brand kit.")}
                      </p>
                    )}
                  </Card>
                  <MarketingQA projectPath={projectPath} />
                </div>
              ) : null}
              {pane !== "qa" ? (
              <div className="grid min-w-0 gap-4">
                {pane === "home" ? (
                  <>
                    <MarketingKpiGrid items={kpis} />
                    <Card title={tx("sourceTitle", "What are we marketing?")}>
                      <p className="text-pretty text-sm text-muted-foreground">
                        {tx(
                          "sourceHint",
                          "Bind a workspace, a live site, or a product already in production. The default projects folder is not a product.",
                        )}
                      </p>
                      <div className="mt-3 rounded-xl border border-border/60 bg-muted/30 px-3 py-2">
                        <p className="text-sm font-medium">{boundLabel}</p>
                        <p className="text-pretty text-xs text-muted-foreground">{boundHint}</p>
                        {currentLabel ? (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {tx("openInNavin", "Open in Navin: {{name}}", { name: currentLabel })}
                          </p>
                        ) : null}
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        {(
                          [
                            ["recent", tx("srcRecent", "Workspace")],
                            ["url", tx("srcUrl", "Live site")],
                            ["product", tx("srcProduct", "Existing product")],
                          ] as const
                        ).map(([id, label]) => (
                          <DefaultButton
                            key={id}
                            text={label}
                            primary={sourceKind === id}
                            onClick={() => {
                              setHint("");
                              setSourceKind(id);
                            }}
                            styles={BUTTON_STYLES}
                          />
                        ))}
                      </div>
                      {sourceKind === "workspace" ? (
                        <p className="mt-3 text-sm">
                          {tx("willScan", "Will scan: {{path}}", { path: projectPath || tx("none", "none") })}
                        </p>
                      ) : null}
                      {sourceKind === "recent" ? (
                        workspaceChoices.length ? (
                          <Dropdown
                            className="mt-3"
                            label={tx("pickProject", "Pick a workspace")}
                            selectedKey={pickedPath || undefined}
                            options={workspaceChoices.map((row) => ({ key: row.path, text: `${row.name} - ${row.path}` })) as IDropdownOption[]}
                            onChange={(_, option) => setPickedPath(String(option?.key || ""))}
                          />
                        ) : (
                          <p className="mt-3 text-sm text-muted-foreground">
                            {tx("noRecent", "No workspace yet. Open one in Code, or bind a live site / existing product.")}
                          </p>
                        )
                      ) : null}
                      {sourceKind === "url" || sourceKind === "product" ? (
                        <div className="mt-3 grid gap-3">
                          {sourceKind === "url" ? (
                            <TextField
                              label={tx("liveUrl", "Live site URL")}
                              placeholder="https://"
                              value={sourceUrl}
                              onChange={(_, value) => setSourceUrl(value || "")}
                            />
                          ) : null}
                          <TextField
                            label={tx("productName", "Product name")}
                            value={sourceName}
                            onChange={(_, value) => setSourceName(value || "")}
                          />
                          <TextField
                            label={tx("oneLiner", "What it does")}
                            value={sourceOneLiner}
                            onChange={(_, value) => setSourceOneLiner(value || "")}
                            multiline
                            rows={2}
                          />
                          {sourceKind === "product" ? (
                            <TextField
                              label={tx("liveUrlOptional", "Site URL (optional)")}
                              placeholder="https://"
                              value={sourceUrl}
                              onChange={(_, value) => setSourceUrl(value || "")}
                            />
                          ) : null}
                        </div>
                      ) : null}
                      <div className="mt-4 flex flex-wrap gap-2">
                        <PrimaryButton
                          text={tx("bindUnderstand", "Bind and understand")}
                          disabled={Boolean(busy)}
                          onClick={() => bindProduct(false)}
                          data-testid="marketing-bind-source"
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("useProject", "Use current project")}
                          disabled={Boolean(busy) || !projectPath || isProjectsRootName(projectName, projectPath)}
                          onClick={() => {
                            setSourceKind("workspace");
                            void run("pipeline", { workspace: projectPath || "", source_kind: "workspace", goal, days: 30, signups: 1000 },
                              () => {
                                setNotice(tx("pipelineDone", "Product bound. Campaign, content and launch kit are ready."));
                                setPane("campaigns");
                              },
                            );
                          }}
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("runPipeline", "Plan 30 days from this source")}
                          disabled={Boolean(busy)}
                          onClick={() => bindProduct(true)}
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("launchProduct", "Launch product")}
                          disabled={Boolean(busy) || !desk?.armed}
                          onClick={() =>
                            void run("launch", {}, (next) => {
                              const n = next.launch.items?.length || 0;
                              setNotice(tx("launchDone", "Launch kit ready: {{n}} assets. Open the Launch pane.", { n }));
                              setPane("launch");
                            })
                          }
                          data-testid="marketing-launch-product"
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("harvestSite", "Harvest the live site")}
                          disabled={Boolean(busy) || (sourceKind !== "url" && !sourceUrl && !desk?.product.site)}
                          onClick={() =>
                            void run(
                              "harvest",
                              { site: sourceUrl || desk?.product.site || desk?.brand.site },
                              (next) => {
                                setNotice(
                                  tx("harvestDone", "Site harvested: {{n}} images, brand filled.", {
                                    n: next.harvest?.images?.length || 0,
                                  }),
                                );
                                setPane("brand");
                              },
                            )
                          }
                          data-testid="marketing-harvest-site"
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("askAgent", "Ask the team")}
                          onClick={() => seedChat()}
                          styles={BUTTON_STYLES}
                        />
                      </div>
                    </Card>
                    <Card title={tx("homeNow", "What the agents are doing")}>
                      <p className="text-pretty text-sm text-muted-foreground">
                        {desk?.loop.last_result || tx("loopIdle", "Loop is paused. Understand the product, then start.")}
                      </p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {tx("loopMeta", "{{phase}} - {{schedule}} - next {{due}}", {
                          phase: desk?.loop.phase || "idle",
                          schedule: loopLabel,
                          due: dueLabel,
                        })}
                      </p>
                      <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="marketing-home-queue">
                        <Pill tone={queueSummary.due || queueSummary.approved ? "info" : "neutral"}>
                          {tx("homeQueue", "{{n}} posts ready to go", { n: queueSummary.due + queueSummary.approved + queueSummary.auto })}
                        </Pill>
                        <Pill tone={queueSummary.waiting ? "info" : "neutral"}>{tx("queueWaiting", "{{n}} scheduled", { n: queueSummary.waiting })}</Pill>
                        <Pill tone={readyConnectors.length ? "success" : "warning"}>
                          {readyConnectors.length
                            ? tx("homeConnected", "{{list}} connected", { list: readyConnectors.map((row) => channelLabel(row.channel)).join(", ") })
                            : tx("homeNotConnected", "no channel connected")}
                        </Pill>
                        <Pill tone={desk?.ai?.enabled && desk.ai.routed ? "success" : "neutral"}>
                          {desk?.ai?.enabled && desk.ai.routed ? tx("homeModel", "model writes copy") : tx("homeTemplates", "template copy")}
                        </Pill>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <DefaultButton
                          text={tx("openPublish", "Open the queue")}
                          iconProps={{ iconName: "Send" }}
                          onClick={() => setPane("publish")}
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("openSettings", "Connect channels")}
                          iconProps={{ iconName: "Settings" }}
                          onClick={() => setPane("settings")}
                          data-testid="marketing-open-settings"
                          styles={BUTTON_STYLES}
                        />
                      </div>
                    </Card>
                    <Card title={tx("positioning", "Positioning")}>
                      <p className="text-sm">
                        {withoutProjectsRoot(
                          desk?.positioning.statement,
                          productDisplayName(desk?.product.name, desk?.product.workspace),
                        ) || tx("noPositioning", "No positioning yet.")}
                      </p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        ICP {desk?.positioning.icp || "-"} · {(desk?.positioning.personas || []).join(" / ") || "-"}
                      </p>
                    </Card>
                    {desk?.harvest?.site ? (
                      <Card title={tx("harvestSnapshot", "From the live site")}>
                        <p className="text-sm">{desk.harvest.one_liner || desk.harvest.name}</p>
                        {(desk.harvest.headings || []).length ? (
                          <p className="mt-2 text-pretty text-xs text-muted-foreground">
                            {(desk.harvest.headings || []).slice(0, 4).join(" · ")}
                          </p>
                        ) : null}
                        {(desk.harvest.ctas || []).length ? (
                          <p className="mt-1 text-xs">{(desk.harvest.ctas || []).slice(0, 4).join(" · ")}</p>
                        ) : null}
                        {desk.harvest.social && Object.keys(desk.harvest.social).length ? (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {Object.entries(desk.harvest.social)
                              .map(([channel, href]) => `${channel}: ${href}`)
                              .join(" · ")}
                          </p>
                        ) : null}
                      </Card>
                    ) : null}
                  </>
                ) : null}

                {pane === "brand" ? (
                  <Card title={tx("brandMemory", "Brand Memory")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx(
                        "brandHint",
                        "Pick who you talk to, where, and in which language - same idea as a Meta ads set. Then save. Empty fields stay empty; nothing is invented.",
                      )}
                    </p>
                    <div className="grid gap-4">
                      <TextField label={tx("company", "Company")} value={company} onChange={(_, v) => setCompany(v || "")} />
                      <div>
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {tx("tone", "Tone")}
                        </p>
                        <ChipRow
                          items={TONE_CHIPS}
                          selected={tone ? [tone] : []}
                          single
                          onToggle={(value) => setTone(value)}
                        />
                      </div>
                      <div>
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {tx("audience", "Audience")}
                        </p>
                        <ChipRow
                          items={AUDIENCE_CHIPS}
                          selected={audience ? audience.split(" · ").filter(Boolean) : []}
                          onToggle={(value) => {
                            const current = audience ? audience.split(" · ").filter(Boolean) : [];
                            setAudience(toggleChip(current, value).join(" · "));
                          }}
                        />
                      </div>
                      <div>
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {tx("countries", "Countries")}
                        </p>
                        <ChipRow items={COUNTRY_CHIPS} selected={countries} onToggle={(value) => setCountries((c) => toggleChip(c, value))} />
                      </div>
                      <div>
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {tx("languages", "Languages")}
                        </p>
                        <ChipRow items={LANGUAGE_CHIPS} selected={languages} onToggle={(value) => setLanguages((c) => toggleChip(c, value))} />
                      </div>
                      <div>
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {tx("industries", "Industry")}
                        </p>
                        <ChipRow items={INDUSTRY_CHIPS} selected={industries} onToggle={(value) => setIndustries((c) => toggleChip(c, value))} />
                      </div>
                      {desk ? <BrandVisuals desk={desk} /> : null}
                      <div className="mt-3 flex flex-wrap gap-2">
                        <PrimaryButton
                          text={tx("saveBrand", "Save brand")}
                          disabled={Boolean(busy)}
                          onClick={() =>
                            void run(
                              "brand",
                              {
                                company,
                                tone,
                                audience,
                                countries,
                                languages,
                                industries,
                                product: desk?.product.name || company,
                                site: desk?.product.site || sourceUrl,
                              },
                              () => setNotice(tx("brandSaved", "Brand memory saved.")),
                            )
                          }
                          styles={BUTTON_STYLES}
                        />
                        <DefaultButton
                          text={tx("harvestSite", "Harvest the live site")}
                          disabled={Boolean(busy) || !(sourceUrl || desk?.product.site || desk?.brand.site)}
                          onClick={() =>
                            void run(
                              "harvest",
                              { site: sourceUrl || desk?.product.site || desk?.brand.site },
                              (next) =>
                                setNotice(
                                  tx("harvestDone", "Site harvested: {{n}} images, brand filled.", {
                                    n: next.harvest?.images?.length || 0,
                                  }),
                                ),
                            )
                          }
                          styles={BUTTON_STYLES}
                        />
                      </div>
                    </div>
                  </Card>
                ) : null}

                {pane === "product" ? (
                  <Card title={tx("product", "Product understanding")}>
                    <p className="text-sm font-medium">{boundLabel}</p>
                    <p className="mt-1 text-pretty text-sm text-muted-foreground">{desk?.product.one_liner}</p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {desk?.product.source_kind ||
                        (desk?.product.name ? tx("bound", "bound") : tx("unbound", "unbound"))}{" "}
                      · {boundHint}
                    </p>
                    <p className="mt-2 text-pretty text-xs text-muted-foreground">
                      {desk?.product.pain} · {desk?.product.value_prop}
                    </p>
                    {(desk?.product.screenshots || []).length ? (
                      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {(desk?.product.screenshots || []).slice(0, 6).map((shot) => (
                          <MediaThumb key={shot} src={shot} label={tx("fromSite", "From the live site")} />
                        ))}
                      </div>
                    ) : null}
                    <p className="mt-3 text-xs text-muted-foreground">
                      {tx(
                        "productHint",
                        "This is the product bound to the marketing book, not automatically the Code workspace. Use Overview to bind another project, a live site, or a product already shipping.",
                      )}
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("understand", "Understand project")}
                        disabled={Boolean(busy)}
                        onClick={() => bindProduct(false, true)}
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("position", "Build positioning")}
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void run("position", {}, () => {
                            setNotice(tx("positionDone", "Positioning written."));
                          })
                        }
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("research", "Research market")}
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void run("research", {}, () => {
                            setNotice(tx("researchDone", "Market research written."));
                          })
                        }
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    {desk?.research?.market || (desk?.research?.trends || []).length ? (
                      <div className="mt-4 rounded-xl border border-border/60 px-3 py-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">{tx("research", "Research")}</p>
                        <p className="mt-1 text-sm">{desk?.research.market}</p>
                        <p className="mt-1 text-pretty text-xs text-muted-foreground">
                          {(desk?.research.trends || []).join(" · ")}
                        </p>
                        <p className="mt-1 text-pretty text-xs">
                          {(desk?.research.keywords || []).slice(0, 8).join(" · ")}
                        </p>
                      </div>
                    ) : null}
                  </Card>
                ) : null}

                {pane === "campaigns" ? (
                  <Card title={tx("campaigns", "Campaigns")}>
                    <TextField label={tx("goal", "Goal")} value={goal} onChange={(_, v) => setGoal(v || "")} />
                    <div className="mt-3 flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("plan30", "Plan 30 days")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("plan", { goal, days: 30, signups: 1000 })}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    <ul className="mt-4 grid gap-2">
                      {(desk?.campaigns || []).map((campaign) => (
                        <li key={campaign.id} className="rounded-xl border border-border/60 px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <div>
                              <p className="text-sm font-medium">{campaign.label}</p>
                              <p className="text-xs text-muted-foreground">
                                {campaign.goal} · {campaign.status} · {(campaign.channels || []).join(", ")}
                              </p>
                              {campaign.objectives?.length ? (
                                <p className="mt-1 text-pretty text-xs text-muted-foreground">
                                  {campaign.objectives.join(" · ")}
                                </p>
                              ) : null}
                            </div>
                            {campaign.status !== "approved" ? (
                              <DefaultButton
                                text={tx("approve", "Approve")}
                                disabled={Boolean(busy)}
                                onClick={() => void run("approve", { id: campaign.id })}
                                styles={BUTTON_STYLES}
                              />
                            ) : null}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null}

                {pane === "content" ? (
                  <Card title={tx("content", "Channel content")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx(
                        "contentHintLive",
                        "Drafts for every channel, written from the bound product (a routed model when one is set, honest templates otherwise). Approve, schedule or publish each one; the loop posts what is due and measures it.",
                      )}
                    </p>
                    <div className="mb-4 flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("shipSocial", "Ready for LinkedIn, Facebook, Instagram, TikTok")}
                        disabled={Boolean(busy) || !desk?.armed}
                        onClick={() => shipSocial()}
                        data-testid="marketing-ship-social"
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("writeContent", "Write variants")}
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void run(
                            "content",
                            { channels: [...SOCIAL_CHANNELS] },
                            (next) => {
                              const n = (next.content || []).filter((row) =>
                                SOCIAL_CHANNELS.includes(row.channel as (typeof SOCIAL_CHANNELS)[number]),
                              ).length;
                              setNotice(tx("contentDone", "Wrote {{n}} channel drafts.", { n }));
                            },
                          )
                        }
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    {desk ? (
                      <ContentQueue
                        desk={desk}
                        busy={busy}
                        locale={i18n.language}
                        tx={tx}
                        run={run}
                        onCopy={copyPost}
                        onNotice={setNotice}
                      />
                    ) : null}
                  </Card>
                ) : null}

                {pane === "publish" ? (
                  <Card title={tx("publishQueue", "Publishing queue")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx(
                        "publishHint",
                        "What goes out and when. Scheduled posts leave at their time, approved posts on the next loop cycle (within the per-cycle budget), and in autonomous mode ready posts follow. Every post gets a tracked link.",
                      )}
                    </p>
                    {desk ? (
                      <ContentQueue
                        desk={desk}
                        busy={busy}
                        locale={i18n.language}
                        tx={tx}
                        run={run}
                        onCopy={copyPost}
                        onNotice={setNotice}
                      />
                    ) : null}
                  </Card>
                ) : null}

                {pane === "settings" && desk ? (
                  <ConnectorsPane desk={desk} busy={busy} locale={i18n.language} tx={tx} run={run} onNotice={setNotice} />
                ) : null}

                {pane === "social" ? (
                  <Card title={tx("socialCalendar", "Social calendar")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx("socialHintLive", "Drafts plus the matching still or clip. Copy them here, or send them from the Publish pane once a channel is connected.")}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("shipSocial", "Ready for LinkedIn, Facebook, Instagram, TikTok")}
                        disabled={Boolean(busy) || !desk?.armed}
                        onClick={() => shipSocial()}
                        data-testid="marketing-ship-social"
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("buildSocial", "Build the 14-day calendar")}
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void run("social", {}, (next) =>
                            setNotice(tx("socialDone", "{{n}} draft posts ready.", { n: next.social?.posts?.length || 0 })),
                          )
                        }
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    <ul className="mt-4 grid gap-3">
                      {(desk?.social?.posts || [])
                        .filter((post) => SOCIAL_CHANNELS.includes(post.channel as (typeof SOCIAL_CHANNELS)[number]))
                        .map((post) => (
                        <li key={post.id} className="rounded-xl border border-border/60 px-3 py-3">
                          <div className="flex items-center justify-between gap-2">
                            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                              Day {post.day} · {post.channel} · {post.status}
                            </p>
                            <DefaultButton
                              text={tx("copyDraft", "Copy")}
                              disabled={!post.body}
                              onClick={() => copyPost(String(post.body || ""))}
                              styles={BUTTON_STYLES}
                            />
                          </div>
                          {post.hook && !String(post.body || "").includes(post.hook) ? (
                            <p className="mt-1 text-sm font-medium">{post.hook}</p>
                          ) : null}
                          <p className="mt-1 text-pretty text-sm">{post.body}</p>
                          <MediaThumb src={post.preview} kind="image" label={`${post.channel} still`} />
                          <MediaThumb src={post.clip} kind="video" label={`${post.channel} clip`} />
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null}

                {pane === "creative" ? (
                  <Card title={tx("creativeStudio", "Creative studio")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx(
                        "creativeHint",
                        "Brand stills, then LinkedIn / Facebook / Instagram / TikTok posts and clips from the bound product. Harvested site images are used as references.",
                      )}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("shipSocial", "Ready for LinkedIn, Facebook, Instagram, TikTok")}
                        disabled={Boolean(busy) || !desk?.armed}
                        onClick={() => shipSocial()}
                        data-testid="marketing-ship-social"
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("produceImages", "Generate brand kit")}
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void run("produce", { kinds: ["image"], pack: "brand" }, (next) =>
                            setNotice(
                              tx("produceDone", "Produced {{n}} assets. {{skip}}", {
                                n: next.produce?.produced ?? next.creatives.filter((row) => row.preview).length,
                                skip: (next.produce?.skipped || []).join(" ") || "",
                              }),
                            ),
                          )
                        }
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("producePosts", "Generate post images")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("produce", { kinds: ["image"], pack: "posts" })}
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("produceClip", "Generate a clip")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("produce", { kinds: ["video"], pack: "clips" })}
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("produceVoice", "Generate voice over")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("produce", { kinds: ["audio"] })}
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("briefCreatives", "Brief only")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("creative")}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    <ul className="mt-4 grid gap-3 sm:grid-cols-2">
                      {(desk?.creatives || [])
                        .filter((item) => STUDIO_PLACEMENTS.has(String(item.placement || "")) || Boolean(item.preview))
                        .map((item) => (
                        <CreativeCard
                          key={item.id}
                          item={item}
                          busy={Boolean(busy)}
                          generateLabel={tx("generateThisOne", "Generate this")}
                          onGenerate={() => void run("produce", { id: item.id, kinds: [item.kind || "image"] })}
                          onVision={(verdict) => void run("vision", { id: item.id, verdict })}
                        />
                      ))}
                    </ul>
                  </Card>
                ) : null}

                {pane === "seo" ? (
                  <Card title={tx("seo", "SEO")}>
                    <p className="text-pretty text-sm text-muted-foreground">
                      {tx("seoHint", "Pages and keywords from the live site harvest plus research. Rankings stay empty until you ingest them.")}
                    </p>
                    <PrimaryButton
                      className="mt-3"
                      text={tx("buildSeo", "Build SEO from the site")}
                      disabled={Boolean(busy)}
                      onClick={() => void run("seo")}
                      styles={BUTTON_STYLES}
                    />
                    <p className="mt-3 text-sm">
                      {(desk?.seo.keywords || desk?.research.keywords || []).join(" · ") ||
                        tx("noKeywords", "No keywords yet. Harvest the site or run research.")}
                    </p>
                    <ul className="mt-3 grid gap-2">
                      {(desk?.seo.pages || []).map((page) => (
                        <li key={page.url || page.title} className="rounded-xl border border-border/60 px-3 py-2">
                          <p className="text-sm font-medium">{page.title || page.url}</p>
                          <p className="text-xs text-muted-foreground">{page.url}</p>
                          <p className="text-pretty text-xs">{page.description}</p>
                        </li>
                      ))}
                    </ul>
                    <div className="mt-4 grid gap-3 sm:grid-cols-3">
                      <TextField
                        label={tx("rankKeyword", "Keyword")}
                        value={rankKeyword}
                        onChange={(_, value) => setRankKeyword(value || "")}
                      />
                      <TextField
                        label={tx("rankUrl", "URL")}
                        value={rankUrl}
                        onChange={(_, value) => setRankUrl(value || "")}
                      />
                      <TextField
                        label={tx("rankPosition", "Position")}
                        value={rankPosition}
                        onChange={(_, value) => setRankPosition(value || "")}
                      />
                    </div>
                    <DefaultButton
                      className="mt-3"
                      text={tx("ingestRanking", "Ingest measured ranking")}
                      disabled={Boolean(busy) || !rankKeyword.trim() || !rankUrl.trim() || !rankPosition.trim()}
                      onClick={() =>
                        void run("seo", { keyword: rankKeyword.trim(), url: rankUrl.trim(), position: rankPosition.trim() }, () => {
                          setRankKeyword("");
                          setRankUrl("");
                          setRankPosition("");
                          setNotice(tx("rankingSaved", "Ranking saved. Nothing was invented."));
                        })
                      }
                      styles={BUTTON_STYLES}
                    />
                    {(desk?.seo.rankings || []).length ? (
                      <ul className="mt-3 grid gap-2">
                        {(desk?.seo.rankings || []).map((row) => (
                          <li key={`${row.keyword}-${row.url}`} className="text-sm">
                            #{row.position} · {row.keyword} · {row.url}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </Card>
                ) : null}

                {pane === "ads" ? (
                  <Card title={tx("ads", "Ads")}>
                    <p className="text-pretty text-sm text-muted-foreground">
                      {tx(
                        "adsHint",
                        "Proposed ad sets from brand audience, countries and languages. Spend stays 0. The desk never buys traffic.",
                      )}
                    </p>
                    <p className="mt-2 text-xs">{tx("spendLabel", "Spend {{n}}", { n: desk?.ads.spend ?? 0 })}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("proposeAds", "Propose ad sets")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("ads")}
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("produceImages", "Generate brand kit")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("produce", { kinds: ["image"], pack: "posts" })}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    <ul className="mt-4 grid gap-3">
                      {(desk?.ads.campaigns || []).map((campaign) => (
                        <li key={campaign.id || campaign.name} className="rounded-xl border border-border/60 px-3 py-3">
                          <p className="text-sm font-medium">{campaign.name}</p>
                          <p className="text-xs text-muted-foreground">
                            {campaign.objective} · {campaign.audience} · {(campaign.countries || []).join(", ")} ·{" "}
                            {(campaign.languages || []).join(", ")}
                          </p>
                          <p className="mt-1 text-pretty text-sm">{campaign.copy}</p>
                          <p className="mt-1 text-xs uppercase text-muted-foreground">
                            {campaign.status} · spend {campaign.spend ?? 0}
                          </p>
                          <div className="mt-2 grid grid-cols-3 gap-2">
                            {(campaign.creative_ids || [])
                              .map((id) => desk?.creatives.find((row) => row.id === id))
                              .filter((row): row is NonNullable<typeof row> => Boolean(row?.preview))
                              .map((row) => (
                                <MediaThumb key={row.id} src={row.preview} kind={row.kind} label={row.placement} />
                              ))}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null}

                {pane === "analytics" ? (
                  <Card title={tx("analytics", "Analytics")}>
                    <p className="text-sm">
                      {tx("funnel", "Traffic {{t}} → leads {{l}} → signups {{s}} → revenue {{r}}", {
                        t: desk?.analytics.traffic || 0,
                        l: desk?.analytics.leads || 0,
                        s: desk?.analytics.signups || 0,
                        r: desk?.analytics.revenue || 0,
                      })}
                    </p>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <PrimaryButton
                        text={tx("syncMetrics", "Sync metrics")}
                        iconProps={{ iconName: "BarChart4" }}
                        disabled={Boolean(busy)}
                        data-testid="marketing-sync-metrics-analytics"
                        onClick={() =>
                          void run("measure", {}, (next) => {
                            const report = next.measure || {};
                            setNotice(
                              report.error
                                ? tx("measureError", "Metrics: {{error}}", { error: report.error })
                                : tx("measureDone", "{{engagement}} posts measured, {{traffic}} tracked links with visits.", {
                                    engagement: report.engagement || 0,
                                    traffic: report.traffic || 0,
                                  }),
                            );
                          })
                        }
                        styles={BUTTON_STYLES}
                      />
                      <span className="text-xs text-muted-foreground">
                        {desk?.settings.analytics?.provider
                          ? tx("analyticsProviderLine", "{{provider}} on {{site}}{{when}}", {
                              provider: desk.settings.analytics.provider,
                              site: desk.settings.analytics.site_id || "",
                              when: desk.settings.analytics.measured_at
                                ? ` · ${tx("measuredAt", "last measured {{when}}", { when: formatWhen(desk.settings.analytics.measured_at, i18n.language) })}`
                                : "",
                            })
                          : tx("analyticsNoProvider", "Channel counters only (X, Facebook, LinkedIn). Add Plausible or Matomo in Settings for visits and signups per post.")}
                      </span>
                    </div>
                    <ul className="mt-3 grid gap-2">
                      {(desk?.scoreboard || []).map((row) => {
                        const measured = desk?.analytics.by_content?.[row.id];
                        return (
                          <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 text-sm" data-testid={`marketing-score-${row.id}`}>
                            <span className="flex items-center gap-2">
                              <span className="font-medium">{channelLabel(row.channel)}</span>
                              <span className="text-muted-foreground">{row.title}</span>
                              {measured?.measured_at ? (
                                <Pill tone="success">{measured.traffic_source || measured.source || tx("measured", "measured")}</Pill>
                              ) : (
                                <Pill tone="neutral">{tx("typed", "typed")}</Pill>
                              )}
                            </span>
                            <span className="tabular-nums text-xs">
                              {tx("scoreLine", "{{views}} views · {{clicks}} clicks · {{conv}} conv · CTR {{ctr}}%", {
                                views: Math.round(row.views || 0),
                                clicks: Math.round(row.clicks || 0),
                                conv: Math.round(row.conversions || 0),
                                ctr: Math.round((row.ctr || 0) * 1000) / 10,
                              })}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                    <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {tx("manualMetrics", "Type numbers the desk cannot fetch")}
                    </p>
                    <div className="mt-3 grid gap-3 sm:grid-cols-4">
                      <TextField
                        label={tx("traffic", "Traffic")}
                        value={traffic}
                        onChange={(_, value) => setTraffic(value || "")}
                      />
                      <TextField
                        label={tx("leads", "Leads")}
                        value={leads}
                        onChange={(_, value) => setLeads(value || "")}
                      />
                      <TextField
                        label={tx("signups", "Signups")}
                        value={signups}
                        onChange={(_, value) => setSignups(value || "")}
                      />
                      <TextField
                        label={tx("revenue", "Revenue")}
                        value={revenue}
                        onChange={(_, value) => setRevenue(value || "")}
                      />
                    </div>
                    {(desk?.content || []).length ? (
                      <ul className="mt-4 grid gap-3">
                        {(desk?.content || []).slice(0, 8).map((item) => {
                          const hit = contentHits[item.id] || { views: "", clicks: "", conversions: "" };
                          return (
                            <li key={item.id} className="rounded-xl border border-border/60 px-3 py-2">
                              <p className="text-xs uppercase text-muted-foreground">
                                {item.channel} · {item.title || item.hook}
                              </p>
                              <div className="mt-2 grid gap-2 sm:grid-cols-3">
                                <TextField
                                  label={tx("views", "Views")}
                                  value={hit.views}
                                  onChange={(_, value) =>
                                    setContentHits((current) => ({
                                      ...current,
                                      [item.id]: { ...hit, views: value || "" },
                                    }))
                                  }
                                />
                                <TextField
                                  label={tx("clicks", "Clicks")}
                                  value={hit.clicks}
                                  onChange={(_, value) =>
                                    setContentHits((current) => ({
                                      ...current,
                                      [item.id]: { ...hit, clicks: value || "" },
                                    }))
                                  }
                                />
                                <TextField
                                  label={tx("conversions", "Conversions")}
                                  value={hit.conversions}
                                  onChange={(_, value) =>
                                    setContentHits((current) => ({
                                      ...current,
                                      [item.id]: { ...hit, conversions: value || "" },
                                    }))
                                  }
                                />
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    ) : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <PrimaryButton
                        text={tx("saveMetrics", "Save metrics")}
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void run("metrics", {
                            traffic: traffic || 0,
                            leads: leads || 0,
                            signups: signups || 0,
                            revenue: revenue || 0,
                            by_content: Object.fromEntries(
                              Object.entries(contentHits)
                                .filter(([, hit]) => hit.views || hit.clicks || hit.conversions)
                                .map(([id, hit]) => [
                                  id,
                                  {
                                    views: Number(hit.views || 0),
                                    clicks: Number(hit.clicks || 0),
                                    conversions: Number(hit.conversions || 0),
                                  },
                                ]),
                            ),
                          })
                        }
                        styles={BUTTON_STYLES}
                      />
                      <DefaultButton
                        text={tx("improve", "Improve winners")}
                        disabled={Boolean(busy)}
                        onClick={() => void run("improve")}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                  </Card>
                ) : null}

                {pane === "competitors" ? (
                  <Card title={tx("competitors", "Competitors")}>
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <PrimaryButton
                        text={tx("researchWeb", "Research the market (web)")}
                        iconProps={{ iconName: "Search" }}
                        disabled={Boolean(busy) || !desk?.armed}
                        data-testid="marketing-research-web"
                        onClick={() =>
                          void run("research", {}, (next) => {
                            setNotice(
                              tx("researchWebDone", "{{n}} competitors from {{hits}} web results ({{mode}}).", {
                                n: next.research.competitors?.length || 0,
                                hits: next.research.hits || 0,
                                mode: next.research.mode || "web",
                              }),
                            );
                          })
                        }
                        styles={BUTTON_STYLES}
                      />
                      {desk?.research.mode ? (
                        <Pill tone={desk.research.mode === "empty" ? "neutral" : desk.research.mode.startsWith("web") ? "success" : "info"}>
                          {tx(`researchMode.${desk.research.mode.replace("+", "_")}`, desk.research.mode)}
                        </Pill>
                      ) : null}
                      {desk?.research.fetched_at ? (
                        <span className="text-xs text-muted-foreground">{formatWhen(desk.research.fetched_at, i18n.language)}</span>
                      ) : null}
                    </div>
                    {(desk?.research.queries || []).length ? (
                      <p className="mb-3 text-pretty text-xs text-muted-foreground">
                        {tx("researchQueries", "Searched: {{list}}", { list: (desk?.research.queries || []).join(" · ") })}
                      </p>
                    ) : null}
                    {(desk?.research.trends || []).length ? (
                      <p className="mb-3 text-pretty text-xs">
                        {tx("trends", "Trends")}: {(desk?.research.trends || []).join(" · ")}
                      </p>
                    ) : null}
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto]">
                      <TextField
                        label={tx("competitorName", "Name")}
                        value={competitorName}
                        onChange={(_, value) => setCompetitorName(value || "")}
                      />
                      <TextField
                        label={tx("competitorNote", "Note")}
                        value={competitorNote}
                        onChange={(_, value) => setCompetitorNote(value || "")}
                      />
                      <PrimaryButton
                        className="self-end"
                        text={tx("addCompetitor", "Add competitor")}
                        disabled={Boolean(busy) || !competitorName.trim()}
                        onClick={() => {
                          const name = competitorName.trim();
                          if (!name) return;
                          void run("competitor", { name, note: competitorNote }).then((ok) => {
                            if (ok) {
                              setCompetitorName("");
                              setCompetitorNote("");
                            }
                          });
                        }}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                    <ul className="mt-4 grid gap-2">
                      {(desk?.competitors || []).map((row) => {
                        const detail = row as { url?: string; angle?: string; source?: string };
                        return (
                          <li key={row.id} className="rounded-xl border border-border/60 px-3 py-2 text-sm">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="font-medium">{row.name}</p>
                              {row.pricing ? <Pill tone="info">{row.pricing}</Pill> : null}
                              {detail.angle ? <span className="text-xs text-muted-foreground">{detail.angle}</span> : null}
                            </div>
                            {row.note ? <p className="text-pretty text-xs text-muted-foreground">{row.note}</p> : null}
                            {detail.url ? (
                              <a className="break-all text-xs text-violet-700 hover:underline dark:text-violet-300" href={detail.url} target="_blank" rel="noreferrer">
                                {detail.url}
                              </a>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                    {!(desk?.competitors || []).length ? (
                      <p className="mt-3 text-sm text-muted-foreground">
                        {tx("noCompetitors", "No competitor on file. Run the web research or add one you know. Nothing is invented.")}
                      </p>
                    ) : null}
                  </Card>
                ) : null}

                {pane === "launch" ? (
                  <Card title={tx("launchKit", "Launch kit")}>
                    <p className="mb-3 text-pretty text-sm text-muted-foreground">
                      {tx(
                        "launchHint",
                        "Launch product writes markdown files under the marketing launch folder: positioning, landing, SEO, social, email, Product Hunt, press. It does not publish and does not spend.",
                      )}
                    </p>
                    <PrimaryButton
                      text={tx("buildLaunch", "Build launch kit")}
                      disabled={Boolean(busy)}
                      onClick={() =>
                        void run("launch", {}, (next) => {
                          setNotice(tx("launchDone", "Launch kit ready: {{n}} assets. Open the Launch pane.", { n: next.launch.items?.length || 0 }));
                        })
                      }
                      styles={BUTTON_STYLES}
                    />
                    {(desk?.launch.items || []).length ? (
                      <ul className="mt-4 grid gap-2">
                        {(desk?.launch.items || []).map((item) => (
                          <li key={item.id} className="rounded-xl border border-border/60 px-3 py-2">
                            <p className="text-sm font-medium">{item.label}</p>
                            {item.file ? <p className="text-xs text-muted-foreground">{item.file}</p> : null}
                            <p className="text-pretty text-xs text-muted-foreground">{item.body}</p>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-4 text-sm text-muted-foreground">
                        {tx("noLaunch", "No kit yet. Bind a product, then click Launch product.")}
                      </p>
                    )}
                  </Card>
                ) : null}

                {pane === "journal" ? (
                  <Card title={tx("journal", "Journal")}>
                    {(desk?.journal || []).length ? (
                      <ul className="grid gap-2">
                        {(desk?.journal || []).map((row) => (
                          <li key={row.id} className="text-sm">
                            <span className="text-xs uppercase text-muted-foreground">{row.kind}</span> {row.text}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-muted-foreground">{tx("noJournal", "No journal entries yet. Run a cycle to start the book.")}</p>
                    )}
                  </Card>
                ) : null}
              </div>
              ) : null}

              {pane !== "qa" ? (
              <div className="grid min-w-0 content-start gap-4">
                <MarketingScene
                  heat={(desk?.kpis.winners || 0) > 0 ? 0.8 : desk?.armed ? 0.35 : 0}
                  active={Boolean(desk?.loop.enabled)}
                  label={tx("sceneAria", "Three dimensional growth loop for the marketing desk")}
                />
                <Card title={tx("next", "Next move")}>
                  <p className="text-sm text-muted-foreground">
                    {!desk?.armed
                      ? tx("nextUnarmed", "Bind a workspace, a live site, or a product already shipping.")
                      : !readyConnectors.length
                        ? tx("nextConnect", "Connect one channel (LinkedIn token, X app, Telegram bot...) so the desk can post for you.")
                        : queueSummary.due + queueSummary.approved
                          ? tx("nextPublish", "{{n}} posts are approved or due. Publish them now or let the loop send them.", { n: queueSummary.due + queueSummary.approved })
                          : tx("nextArmed", "Write the social pack, approve a campaign, or start the growth loop.")}
                  </p>
                  {desk?.armed && !readyConnectors.length ? (
                    <PrimaryButton
                      className="mt-3"
                      text={tx("openSettings", "Connect channels")}
                      iconProps={{ iconName: "Settings" }}
                      onClick={() => setPane("settings")}
                      styles={BUTTON_STYLES}
                    />
                  ) : desk?.armed && queueSummary.due + queueSummary.approved ? (
                    <PrimaryButton
                      className="mt-3"
                      text={tx("openPublish", "Open the queue")}
                      iconProps={{ iconName: "Send" }}
                      onClick={() => setPane("publish")}
                      styles={BUTTON_STYLES}
                    />
                  ) : (
                    <PrimaryButton
                      className="mt-3"
                      text={tx("shipSocial", "Ready for LinkedIn, Facebook, Instagram, TikTok")}
                      disabled={Boolean(busy) || !desk?.armed}
                      onClick={() => shipSocial()}
                      styles={BUTTON_STYLES}
                    />
                  )}
                </Card>
              </div>
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>

        <TradingLoopSchedulePanel
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
